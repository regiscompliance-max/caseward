import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from caseward.db import RequestContext, scoped_transaction
from caseward.worker.runner import Worker
from conftest import PASSWORD, insert, new_case, new_deadline, new_rule


@pytest.fixture(scope="module")
def worker(database):
    pool = ConnectionPool(psycopg.conninfo.make_conninfo(database, user="cw_test_worker", password=PASSWORD),
                          kwargs={"autocommit": True}, min_size=1, max_size=2, open=True)
    yield Worker(pool, worker_id="test-worker")
    pool.close()


def deadlines(conn, ctx, case_id, rule_key):
    with scoped_transaction(conn, ctx) as c:
        return c.execute("select status, clock_started_at, due_at, completed_at from caseward.deadline"
                         " where case_id = %s and rule_key = %s order by clock_started_at",
                         [case_id, rule_key]).fetchall()


def job(admin_dsn, idempotency_key):
    with psycopg.connect(admin_dsn) as admin:
        return admin.execute("select status, attempts, last_error, run_after > now() from caseward.job"
                             " where idempotency_key = %s", [idempotency_key]).fetchone()


def test_new_case_gets_its_deadline(app_conn, worker, world, rule, calendar_ew):
    with scoped_transaction(app_conn, world.a) as conn:
        case_id = new_case(conn, world)
        insert(conn, "case_event", client_id=world.landlord_a, case_id=case_id, kind="case_aware",
               occurred_at="2026-12-23T15:00:00+00:00", recorded_by=world.user_a)
    worker.drain()
    [(status, clock, due, _)] = deadlines(app_conn, world.a, case_id, rule[0])
    assert status == "open"
    assert clock == datetime.fromisoformat("2026-12-23T15:00:00+00:00")
    # 10 working days, skipping Christmas Day, the Boxing Day substitute and New Year's Day.
    assert due.astimezone(UTC).date().isoformat() == "2027-01-11"


def test_calculation_is_idempotent(app_conn, worker, admin_dsn, world, rule, calendar_ew):
    with scoped_transaction(app_conn, world.a) as conn:
        case_id = new_case(conn, world)
        event = insert(conn, "case_event", client_id=world.landlord_a, case_id=case_id, kind="case_aware",
                       occurred_at="2026-12-23T15:00:00+00:00", recorded_by=world.user_a)
    worker.drain()
    with psycopg.connect(admin_dsn) as admin:
        admin.execute(
            "insert into caseward.job (client_id, kind, payload, idempotency_key) values (%s, 'compute_deadlines', %s, %s)",
            [world.landlord_a, Jsonb({"subject": {"kind": "case", "id": str(case_id)}, "event": "case_aware",
                                      "event_id": str(event)}), f"again-{uuid.uuid4()}"])
    worker.drain()
    assert len(deadlines(app_conn, world.a, case_id, rule[0])) == 1


def test_follow_on_deadline_starts_from_the_event(app_conn, staff_conn, worker, world, calendar_ew):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        summary = new_rule(conn, world, trigger_event="investigation_completed",
                           completion_event="written_summary_sent", duration=3)
    with scoped_transaction(app_conn, world.a) as conn:
        case_id = new_case(conn, world)
        insert(conn, "case_event", client_id=world.landlord_a, case_id=case_id, kind="investigation_completed",
               occurred_at="2026-12-24T11:00:00+00:00", recorded_by=world.user_a)
    worker.drain()
    [(status, clock, due, _)] = deadlines(app_conn, world.a, case_id, summary[0])
    assert clock == datetime.fromisoformat("2026-12-24T11:00:00+00:00")
    assert due.astimezone(UTC).date().isoformat() == "2026-12-31"


def test_completion_recorded_before_calculation_still_counts(app_conn, worker, world, rule, calendar_ew):
    with scoped_transaction(app_conn, world.a) as conn:
        case_id = new_case(conn, world)
        for kind, at in (("case_aware", "2026-12-23T15:00:00+00:00"), ("investigation_completed", "2027-01-05T09:00:00+00:00")):
            insert(conn, "case_event", client_id=world.landlord_a, case_id=case_id, kind=kind, occurred_at=at,
                   recorded_by=world.user_a)
    worker.drain()
    [(status, _, _, completed)] = deadlines(app_conn, world.a, case_id, rule[0])
    assert (status, completed) == ("met", datetime.fromisoformat("2027-01-05T09:00:00+00:00"))


def test_no_deadline_without_an_enabled_rule_pack(app_conn, staff_conn, worker, world, rule, calendar_ew):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        case_b = insert(conn, "hazard_case", client_id=world.landlord_b, property_id=world.property_b,
                        reference=f"B-{uuid.uuid4().hex[:6]}", hazard_category="damp_and_mould_growth",
                        severity="significant", received_at="2026-12-23T15:00:00+00:00",
                        aware_at="2026-12-23T15:00:00+00:00", awareness_source="tenant_report")
        insert(conn, "case_event", client_id=world.landlord_b, case_id=case_b, kind="case_aware",
               occurred_at="2026-12-23T15:00:00+00:00", recorded_by=world.caseworker)
    worker.drain()
    assert deadlines(staff_conn, world.staff_ctx, case_b, rule[0]) == []


def test_calendar_must_cover_the_deadline(app_conn, admin_dsn, worker, world, rule, calendar_ew):
    with scoped_transaction(app_conn, world.a) as conn:
        case_id = new_case(conn, world)
        event = insert(conn, "case_event", client_id=world.landlord_a, case_id=case_id, kind="case_aware",
                       occurred_at="2029-06-01T09:00:00+00:00", recorded_by=world.user_a)
    worker.drain()
    status, attempts, error, backed_off = job(admin_dsn, f"compute_deadlines:case:{case_id}:{event}")
    assert (status, attempts, backed_off) == ("queued", 1, True)
    assert error.startswith("CalendarMissing: england-and-wales calendar ends 2027-12-31")


def test_failing_jobs_back_off_then_stop(admin_dsn, worker, world):
    key = f"broken-{uuid.uuid4()}"
    with psycopg.connect(admin_dsn) as admin:
        admin.execute("insert into caseward.job (client_id, kind, payload, idempotency_key, max_attempts)"
                      " values (%s, 'compute_deadlines', %s, %s, 2)",
                      [world.landlord_a, Jsonb({"subject": {"kind": "case", "id": str(uuid.uuid4())}, "event": "case_aware"}), key])
    worker.drain()
    assert job(admin_dsn, key)[:2] == ("queued", 1)
    with psycopg.connect(admin_dsn) as admin:
        admin.execute("update caseward.job set run_after = now() where idempotency_key = %s", [key])
    worker.drain()
    status, attempts, error, _ = job(admin_dsn, key)
    assert (status, attempts) == ("dead", 2)
    assert error.startswith("LookupError")


def test_kinds_without_a_handler_wait_in_the_queue(admin_dsn, worker, world):
    key = f"ocr-{uuid.uuid4()}"
    with psycopg.connect(admin_dsn) as admin:
        admin.execute("insert into caseward.job (client_id, kind, idempotency_key) values (%s, 'ocr', %s)",
                      [world.landlord_a, key])
    worker.drain()
    assert job(admin_dsn, key)[:2] == ("queued", 0)


def test_jobs_left_running_by_a_dead_worker_are_requeued(admin_dsn, worker, world):
    key = f"stale-{uuid.uuid4()}"
    with psycopg.connect(admin_dsn) as admin:
        admin.execute("insert into caseward.job (client_id, kind, idempotency_key, status, attempts, locked_by, locked_at)"
                      " values (%s, 'ocr', %s, 'running', 1, 'gone', now() - interval '1 hour')",
                      [world.landlord_a, key])
    assert worker.requeue_stale() >= 1
    assert job(admin_dsn, key)[:3] == ("queued", 1, "worker stopped before finishing")


def test_worker_changes_are_audited_as_the_worker(app_conn, admin_dsn, worker, world, rule, calendar_ew):
    with scoped_transaction(app_conn, world.a) as conn:
        case_id = new_case(conn, world)
        insert(conn, "case_event", client_id=world.landlord_a, case_id=case_id, kind="case_aware",
               occurred_at="2026-12-23T15:00:00+00:00", recorded_by=world.user_a)
    worker.drain()
    with psycopg.connect(admin_dsn) as admin:
        actors = admin.execute(
            "select distinct a.actor_kind, a.actor_user_id, a.client_id, a.request_id like 'job-%%'"
            " from caseward.audit_event a join caseward.deadline d on a.entity_id = d.id::text"
            " where a.entity_type = 'deadline' and a.action = 'insert' and d.case_id = %s", [case_id]).fetchall()
    assert actors == [("worker", None, world.landlord_a, True)]


# Sweep -------------------------------------------------------------------------

def overdue_fraction_deadline(conn, world, rule, calendar, fraction):
    start = datetime.now(UTC) - timedelta(days=10 * fraction)
    return new_deadline(conn, world, rule, calendar, new_case(conn, world), clock_started_at=start,
                        due_at=start + timedelta(days=10))


def alerts(conn, ctx, deadline_id):
    with scoped_transaction(conn, ctx) as c:
        return [r[0] for r in c.execute("select level from caseward.deadline_alert where deadline_id = %s"
                                        " order by raised_at, level", [deadline_id]).fetchall()]


def test_sweep_marks_missed_and_raises_alerts(app_conn, worker, world, rule, calendar_ew):
    with scoped_transaction(app_conn, world.a) as conn:
        late = overdue_fraction_deadline(conn, world, rule, calendar_ew, 1.1)
        close = overdue_fraction_deadline(conn, world, rule, calendar_ew, 0.85)
        half = overdue_fraction_deadline(conn, world, rule, calendar_ew, 0.55)
        fine = overdue_fraction_deadline(conn, world, rule, calendar_ew, 0.1)
    worker.sweep()
    with scoped_transaction(app_conn, world.a) as conn:
        status = conn.execute("select status from caseward.deadline where id = %s", [late]).fetchone()[0]
    assert status == "missed"
    assert [alerts(app_conn, world.a, d) for d in (late, close, half, fine)] == [
        ["overdue"], ["alert_80"], ["alert_50"], []]
    worker.sweep()
    assert alerts(app_conn, world.a, close) == ["alert_80"]


def test_alerts_escalate_as_time_passes(app_conn, admin_dsn, worker, world, rule, calendar_ew):
    with scoped_transaction(app_conn, world.a) as conn:
        deadline = overdue_fraction_deadline(conn, world, rule, calendar_ew, 0.55)
    worker.sweep()
    assert alerts(app_conn, world.a, deadline) == ["alert_50"]
    # Simulate four more days passing by moving the deadline back in time.
    with psycopg.connect(admin_dsn, autocommit=True) as admin, admin.transaction():
        admin.execute("alter table caseward.deadline disable trigger guard")
        admin.execute("update caseward.deadline set clock_started_at = clock_started_at - interval '4 days',"
                      " due_at = due_at - interval '4 days' where id = %s", [deadline])
        admin.execute("alter table caseward.deadline enable trigger guard")
    worker.sweep()
    assert alerts(app_conn, world.a, deadline) == ["alert_50", "alert_80"]


def test_alerts_are_isolated_per_client(app_conn, worker, world, rule, calendar_ew):
    with scoped_transaction(app_conn, world.a) as conn:
        deadline = overdue_fraction_deadline(conn, world, rule, calendar_ew, 0.9)
    worker.sweep()
    assert alerts(app_conn, world.a, deadline) == ["alert_80"]
    assert alerts(app_conn, world.b, deadline) == []


def test_jobs_for_one_client_cannot_see_another_clients_deadlines(app_conn, admin_dsn, worker, world, rule,
                                                                  calendar_ew):
    """Regression: the worker login once saw every deadline (for the sweep), even
    while running one client's job. Only sweep_deadlines() may cross clients."""
    with scoped_transaction(app_conn, world.a) as conn:
        new_deadline(conn, world, rule, calendar_ew, new_case(conn, world))
    seen = []

    def peek(conn, job):
        seen.append(conn.execute("select count(*) from caseward.deadline where client_id = %s",
                                 [world.landlord_a]).fetchone()[0])

    key = f"peek-{uuid.uuid4()}"
    with psycopg.connect(admin_dsn) as admin:
        admin.execute("insert into caseward.job (client_id, kind, idempotency_key) values (%s, 'ocr', %s)",
                      [world.landlord_b, key])
    Worker(worker.pool, handlers={"ocr": peek}).drain()
    assert seen and set(seen) == {0}
    assert worker.sweep() is not None  # the sweep itself still works across clients
