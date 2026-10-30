import uuid
from datetime import datetime, timedelta

import psycopg
import pytest

from caseward.bank_holidays import load_calendar, publish_calendar
from caseward.db import RequestContext, scoped_transaction
from caseward.deadlines import CALCULATOR_VERSION, DeadlineRule, compute_due_at
from conftest import insert

UK_CLOCK_START = datetime.fromisoformat("2026-12-23T15:00:00+00:00")


def new_rule(conn, world, *, approved=True, **overrides):
    values = dict(
        rule_key=f"test.case.{uuid.uuid4().hex[:8]}", version=1, title="Test rule", description="For tests",
        jurisdiction="england", subject_kind="case", applies_to="significant", trigger_event="case_aware",
        duration=10, duration_unit="days", day_type="working", authored_by=world.caseworker,
    )
    if approved:
        values.update(status="approved", approved_by=world.compliance_lead, approved_at=datetime.now().astimezone(),
                      effective_from="2025-01-01")
    values.update(overrides)
    insert(conn, "deadline_rule", **values)
    return values["rule_key"], values["version"]


def new_case(conn, world, severity="significant"):
    return insert(conn, "hazard_case", client_id=world.landlord_a, property_id=world.property_a,
                  reference=f"HC-{uuid.uuid4().hex[:6]}", hazard_category="damp_and_mould_growth",
                  severity=severity, received_at=UK_CLOCK_START, aware_at=UK_CLOCK_START,
                  awareness_source="tenant_report")


@pytest.fixture
def calendar_ew(staff_conn, world):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        return publish_calendar(conn, "england-and-wales", [
            (datetime(2026, 12, 25).date(), "Christmas Day"),
            (datetime(2026, 12, 28).date(), "Boxing Day (substitute day)"),
            (datetime(2027, 1, 1).date(), "New Year's Day"),
        ], source="test")


@pytest.fixture
def rule(staff_conn, world):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        return new_rule(conn, world)


def new_deadline(conn, world, rule, calendar_id, case_id, **overrides):
    holidays = load_calendar(conn, calendar_id)
    due = compute_due_at(DeadlineRule(rule[0], rule[1], 10, "days", "working"), UK_CLOCK_START, holidays)
    values = dict(client_id=world.landlord_a, case_id=case_id, rule_key=rule[0], rule_version=rule[1],
                  clock_started_at=UK_CLOCK_START, holiday_calendar_id=calendar_id, due_at=due,
                  calculator_version=CALCULATOR_VERSION)
    values.update(overrides)
    return insert(conn, "deadline", **values)


# Deadline rules ------------------------------------------------------------

def test_rule_cannot_be_approved_by_its_author(staff_conn, world):
    with pytest.raises(psycopg.errors.CheckViolation):
        with scoped_transaction(staff_conn, world.staff_ctx) as conn:
            new_rule(conn, world, authored_by=world.compliance_lead)


def test_rule_approver_must_be_compliance_lead(staff_conn, world):
    with pytest.raises(psycopg.errors.RaiseException, match="compliance lead"):
        with scoped_transaction(staff_conn, world.staff_ctx) as conn:
            new_rule(conn, world, approved_by=world.reviewer)


def test_approved_rule_is_frozen_but_can_be_retired(staff_conn, world, rule):
    with pytest.raises(psycopg.errors.RaiseException, match="create a new version"):
        with scoped_transaction(staff_conn, world.staff_ctx) as conn:
            conn.execute("update caseward.deadline_rule set duration = 5 where rule_key = %s", [rule[0]])
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        conn.execute("update caseward.deadline_rule set status = 'retired', effective_to = '2026-01-01'"
                     " where rule_key = %s", [rule[0]])


def test_only_one_version_is_effective_at_a_time(staff_conn, world, rule):
    with pytest.raises(psycopg.errors.ExclusionViolation):
        with scoped_transaction(staff_conn, world.staff_ctx) as conn:
            new_rule(conn, world, rule_key=rule[0], version=2, effective_from="2026-06-01")
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        conn.execute("update caseward.deadline_rule set effective_to = '2026-06-01' where rule_key = %s",
                     [rule[0]])
        new_rule(conn, world, rule_key=rule[0], version=2, effective_from="2026-06-01", duration=7)


# Deadlines -----------------------------------------------------------------

def test_deadline_needs_exactly_one_subject(app_conn, world, rule, calendar_ew):
    with pytest.raises(psycopg.errors.CheckViolation):
        with scoped_transaction(app_conn, world.a) as conn:
            new_deadline(conn, world, rule, calendar_ew, case_id=None)


def test_deadline_rejects_draft_rule(app_conn, staff_conn, world, calendar_ew):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        draft = new_rule(conn, world, approved=False)
    with pytest.raises(psycopg.errors.RaiseException, match="draft"):
        with scoped_transaction(app_conn, world.a) as conn:
            new_deadline(conn, world, draft, calendar_ew, new_case(conn, world))


def test_deadline_rejects_rule_for_other_jurisdiction_or_severity(app_conn, staff_conn, world, calendar_ew):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        welsh = new_rule(conn, world, jurisdiction="wales")
        emergency = new_rule(conn, world, applies_to="emergency")
    for wrong_rule, message in [(welsh, "is for wales"), (emergency, "applies to case emergency")]:
        with pytest.raises(psycopg.errors.RaiseException, match=message):
            with scoped_transaction(app_conn, world.a) as conn:
                new_deadline(conn, world, wrong_rule, calendar_ew, new_case(conn, world))


def test_working_day_rule_needs_the_right_calendar(app_conn, staff_conn, world, rule):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        scottish = publish_calendar(conn, "scotland", [(datetime(2026, 11, 30).date(), "St Andrew's Day")], "test")
    with pytest.raises(psycopg.errors.RaiseException, match="holiday calendar"):
        with scoped_transaction(app_conn, world.a) as conn:
            new_deadline(conn, world, rule, scottish, new_case(conn, world))


def test_met_and_missed_are_derived_from_completion(app_conn, world, rule, calendar_ew):
    with scoped_transaction(app_conn, world.a) as conn:
        on_time = new_deadline(conn, world, rule, calendar_ew, new_case(conn, world))
        late = new_deadline(conn, world, rule, calendar_ew, new_case(conn, world))
        conn.execute("update caseward.deadline set completed_at = due_at - interval '1 hour' where id = %s", [on_time])
        conn.execute("update caseward.deadline set completed_at = due_at + interval '1 second' where id = %s", [late])
        statuses = dict(conn.execute("select id, status from caseward.deadline where id in (%s, %s)",
                                     [on_time, late]).fetchall())
    assert statuses == {on_time: "met", late: "missed"}


def test_deadline_cannot_be_marked_met_by_hand(app_conn, world, rule, calendar_ew):
    with pytest.raises(psycopg.errors.RaiseException, match="completed_at"):
        with scoped_transaction(app_conn, world.a) as conn:
            deadline = new_deadline(conn, world, rule, calendar_ew, new_case(conn, world))
            conn.execute("update caseward.deadline set status = 'met' where id = %s", [deadline])


def test_deadline_cannot_be_marked_missed_before_due(app_conn, world, rule, calendar_ew):
    with pytest.raises(psycopg.errors.RaiseException, match="not yet due"):
        with scoped_transaction(app_conn, world.a) as conn:
            case_id = new_case(conn, world)
            start = datetime.now().astimezone()
            deadline = new_deadline(conn, world, rule, calendar_ew, case_id, clock_started_at=start,
                                    due_at=start + timedelta(days=14))
            conn.execute("update caseward.deadline set status = 'missed' where id = %s", [deadline])


def test_deadline_inputs_are_immutable(app_conn, world, rule, calendar_ew):
    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        with scoped_transaction(app_conn, world.a) as conn:
            deadline = new_deadline(conn, world, rule, calendar_ew, new_case(conn, world))
            conn.execute("update caseward.deadline set due_at = due_at + interval '1 day' where id = %s", [deadline])


def test_stored_deadline_recalculates_exactly(app_conn, world, rule, calendar_ew):
    with scoped_transaction(app_conn, world.a) as conn:
        deadline = new_deadline(conn, world, rule, calendar_ew, new_case(conn, world))
        row = conn.execute(
            """
            select d.clock_started_at, d.holiday_calendar_id, d.due_at, r.duration, r.duration_unit, r.day_type
            from caseward.deadline d
            join caseward.deadline_rule r on (r.rule_key, r.version) = (d.rule_key, d.rule_version)
            where d.id = %s
            """,
            [deadline],
        ).fetchone()
        holidays = load_calendar(conn, row[1])
    recomputed = compute_due_at(DeadlineRule(rule[0], rule[1], row[3], row[4], row[5]), row[0], holidays)
    assert recomputed == row[2]
    # 23 Dec 2026 + 10 working days, skipping 25 Dec, 28 Dec and 1 Jan.
    assert recomputed.date().isoformat() == "2027-01-11"


# Letters -------------------------------------------------------------------

def new_letter(conn, staff_conn, world):
    with scoped_transaction(staff_conn, world.staff_ctx) as sconn:
        template = insert(sconn, "letter_template", template_key=f"t-{uuid.uuid4().hex[:6]}", version=1,
                          title="Investigation notice", body="Dear {{name}}", status="approved",
                          approved_by=world.compliance_lead, approved_at=datetime.now().astimezone())
    letter = insert(conn, "letter", client_id=world.landlord_a, template_id=template,
                    recipient_contact_id=world.contact_a, channel="post")
    conn.execute(
        "insert into caseward.letter_version (letter_id, client_id, version, body, origin, model_provider,"
        " model_version, prompt_id, prompt_version) values (%s, %s, 1, 'AI draft', 'ai_draft', 'p', 'm1',"
        " 'letter.investigation', '3')",
        [letter, world.landlord_a],
    )
    conn.execute(
        "insert into caseward.letter_version (letter_id, client_id, version, body, origin, created_by)"
        " values (%s, %s, 2, 'Edited', 'human_edit', %s)",
        [letter, world.landlord_a, world.user_a2],
    )
    return letter


def approve(conn, letter, version, approver):
    conn.execute(
        "update caseward.letter set status = 'approved', approved_version = %s, approved_by = %s,"
        " approved_at = now() where id = %s",
        [version, approver, letter],
    )


def test_letter_versions_are_append_only(app_conn, staff_conn, world):
    with scoped_transaction(app_conn, world.a) as conn:
        letter = new_letter(conn, staff_conn, world)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with scoped_transaction(app_conn, world.a) as conn:
            conn.execute("update caseward.letter_version set body = 'changed' where letter_id = %s", [letter])


def test_ai_draft_must_record_model_and_prompt(app_conn, staff_conn, world):
    with pytest.raises(psycopg.errors.CheckViolation):
        with scoped_transaction(app_conn, world.a) as conn:
            letter = new_letter(conn, staff_conn, world)
            conn.execute("insert into caseward.letter_version (letter_id, client_id, version, body, origin)"
                         " values (%s, %s, 3, 'x', 'ai_draft')", [letter, world.landlord_a])


def test_author_cannot_approve_their_own_version(app_conn, staff_conn, world):
    with pytest.raises(psycopg.errors.RaiseException, match="they wrote"):
        with scoped_transaction(app_conn, world.a) as conn:
            approve(conn, new_letter(conn, staff_conn, world), 2, world.user_a2)


def test_letter_approval_is_immutable(app_conn, staff_conn, world):
    with scoped_transaction(app_conn, world.a) as conn:
        letter = new_letter(conn, staff_conn, world)
        approve(conn, letter, 2, world.user_a)
    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        with scoped_transaction(app_conn, world.a) as conn:
            conn.execute("update caseward.letter set approved_version = 1 where id = %s", [letter])


# Quality review ------------------------------------------------------------

def test_reviewer_cannot_review_own_or_unfinished_task(staff_conn, world):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        open_task = insert(conn, "task", client_id=world.landlord_a, kind="other", assignee_id=world.caseworker)
        done_task = insert(conn, "task", client_id=world.landlord_a, kind="other", assignee_id=world.caseworker,
                           status="done", completed_at=datetime.now().astimezone())
    for task, reviewer, message in [(open_task, world.reviewer, "cannot be quality reviewed yet"),
                                    (done_task, world.caseworker, "their own task")]:
        with pytest.raises(psycopg.errors.RaiseException, match=message):
            with scoped_transaction(staff_conn, world.staff_ctx) as conn:
                insert(conn, "quality_review", client_id=world.landlord_a, task_id=task, reviewer_id=reviewer,
                       score=4)
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        insert(conn, "quality_review", client_id=world.landlord_a, task_id=done_task, reviewer_id=world.reviewer,
               score=4)


# Cases ---------------------------------------------------------------------

def test_case_status_changes_are_recorded(app_conn, world):
    with scoped_transaction(app_conn, world.a) as conn:
        case_id = new_case(conn, world)
        conn.execute("update caseward.hazard_case set status = 'investigating' where id = %s", [case_id])
        history = conn.execute("select from_status, to_status, changed_by from caseward.case_status_change"
                               " where case_id = %s order by id", [case_id]).fetchall()
    assert history == [(None, "open", world.user_a), ("open", "investigating", world.user_a)]


# Audit ---------------------------------------------------------------------

def test_changes_are_audited_with_actor_and_request(app_conn, world):
    ctx = RequestContext(actor_kind="user", client_id=world.landlord_a, user_id=world.user_a,
                         request_id="req-123", source_ip="203.0.113.7")
    with scoped_transaction(app_conn, ctx) as conn:
        defect = insert(conn, "defect", client_id=world.landlord_a, property_id=world.property_a, code="C1",
                        description="Exposed live conductors", identified_on="2026-10-01")
        event = conn.execute(
            "select actor_user_id, actor_kind, action, request_id, host(source_ip), after ->> 'code'"
            " from caseward.audit_event where entity_type = 'defect' and entity_id = %s",
            [str(defect)],
        ).fetchone()
    assert event == (world.user_a, "user", "insert", "req-123", "203.0.113.7", "C1")


def test_personal_data_is_redacted_from_audit(app_conn, world):
    with scoped_transaction(app_conn, world.a) as conn:
        conn.execute("update caseward.tenant_contact set phone = '07700900123' where id = %s", [world.contact_a])
        before, after, changed = conn.execute(
            "select before, after, changed_fields from caseward.audit_event"
            " where entity_type = 'tenant_contact' and entity_id = %s and action = 'update'",
            [str(world.contact_a)],
        ).fetchone()
    assert (before, after, changed) == (None, None, ["phone"])


def test_contractors_cannot_read_landlord_audit(app_conn, world):
    with scoped_transaction(app_conn, world.c) as conn:
        rows = conn.execute("select 1 from caseward.audit_event where client_id = %s", [world.landlord_a]).fetchall()
    assert rows == []


def test_audit_actor_cannot_be_forged(app_conn, world):
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        with scoped_transaction(app_conn, world.a) as conn:
            conn.execute("insert into caseward.audit_event (actor_user_id, actor_kind, client_id, action, entity_type)"
                         " values (%s, 'user', %s, 'export', 'report')", [world.user_b, world.landlord_a])


def test_audit_is_append_only(app_conn, admin_dsn, world):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with scoped_transaction(app_conn, world.a) as conn:
            conn.execute("delete from caseward.audit_event")
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        for statement in ("update caseward.audit_event set action = 'x'", "delete from caseward.audit_event",
                          "truncate caseward.audit_event"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
                admin.execute(statement)


def test_audit_chain_detects_tampering(staff_conn, admin_dsn, world):
    def verify():
        with scoped_transaction(staff_conn, world.staff_ctx) as conn:
            return conn.execute("select caseward.verify_audit_chain()").fetchone()[0]

    assert verify() is None
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        target, original = admin.execute(
            "select id, action from caseward.audit_event order by id desc offset 1 limit 1").fetchone()

        def set_action(action):
            with admin.transaction():
                admin.execute("alter table caseward.audit_event disable trigger append_only")
                admin.execute("update caseward.audit_event set action = %s where id = %s", [action, target])
                admin.execute("alter table caseward.audit_event enable trigger append_only")

        set_action("tampered")
        try:
            assert verify() == target
        finally:
            set_action(original)
    assert verify() is None


# Job queue -----------------------------------------------------------------

def test_failed_jobs_back_off_then_die(app_conn, worker_conn, world):
    key = f"ocr-{uuid.uuid4()}"
    with scoped_transaction(app_conn, world.a) as conn:
        conn.execute("insert into caseward.job (client_id, kind, idempotency_key, max_attempts)"
                     " values (%s, 'ocr', %s, 2)", [world.landlord_a, key])
    worker_ctx = RequestContext(actor_kind="worker")
    with scoped_transaction(worker_conn, worker_ctx) as conn:
        job_id = conn.execute("select id from caseward.job where idempotency_key = %s", [key]).fetchone()[0]
        conn.execute("update caseward.job set status = 'running', attempts = 1 where id = %s", [job_id])
        first = conn.execute("select status, run_after > now() from caseward.finish_job(%s, 'boom')",
                             [job_id]).fetchone()
        conn.execute("update caseward.job set status = 'running', attempts = 2 where id = %s", [job_id])
        second = conn.execute("select status, finished_at is not null from caseward.finish_job(%s, 'boom')",
                              [job_id]).fetchone()
    assert first == ("queued", True)
    assert second == ("dead", True)


def test_claim_job_skips_rows_locked_by_another_worker(app_conn, worker_conn, database, world):
    with scoped_transaction(app_conn, world.a) as conn:
        for _ in range(2):
            conn.execute("insert into caseward.job (client_id, kind, idempotency_key) values (%s, 'draft_letter', %s)",
                         [world.landlord_a, f"draft-{uuid.uuid4()}"])
    worker_ctx = RequestContext(actor_kind="worker")
    with psycopg.connect(database, user="cw_test_worker", password="caseward-test", autocommit=True) as other:
        with scoped_transaction(other, worker_ctx) as c1, scoped_transaction(worker_conn, worker_ctx) as c2:
            first = c1.execute("select id from caseward.claim_job('w1', array['draft_letter'])").fetchone()
            second = c2.execute("select id from caseward.claim_job('w2', array['draft_letter'])").fetchone()
            assert first and second and first != second
            for c, job in ((c1, first), (c2, second)):
                c.execute("select caseward.finish_job(%s)", [job[0]])
