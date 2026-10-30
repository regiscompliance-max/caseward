"""Changing cases, defects and deadlines; audit search; the job queue."""

import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from caseward.api.handlers import client as client_handlers
from caseward.api.handlers import operations as ops_handlers
from caseward.db import scoped_transaction
from caseward.worker.runner import Worker
from conftest import PASSWORD, insert, new_case, new_rule
from test_api import As, make_client, problem


@pytest.fixture(scope="module")
def ops_api(database):
    pool, api = make_client(database, "operations", "cw_test_staff", ops_handlers.HANDLERS)
    yield api
    pool.close()


@pytest.fixture(scope="module")
def client_api(database):
    pool, api = make_client(database, "client", "cw_test_app", client_handlers.HANDLERS)
    yield api
    pool.close()


@pytest.fixture(scope="module")
def worker(database):
    pool = ConnectionPool(psycopg.conninfo.make_conninfo(database, user="cw_test_worker", password=PASSWORD),
                          kwargs={"autocommit": True}, min_size=1, max_size=1, open=True)
    yield Worker(pool)
    pool.close()


@pytest.fixture
def landlord(client_api, world):
    return As(client_api, world, "user_a", "client")


@pytest.fixture
def lead(ops_api, world):
    return As(ops_api, world, "lead", "operations")


@pytest.fixture
def handler(ops_api, world):
    return As(ops_api, world, "case_handler", "operations")


def patch(who, url, body, etag):
    return who.api.patch(url, json=body, headers={**who.headers, "If-Match": etag})


def aware_case(app_conn, world, at="2026-12-23T15:00:00+00:00"):
    with scoped_transaction(app_conn, world.a) as conn:
        case = new_case(conn, world)
        event = insert(conn, "case_event", client_id=world.landlord_a, case_id=case, kind="case_aware",
                       occurred_at=at, recorded_by=world.user_a)
    return case, event


def deadlines(admin_dsn, case_id, rule_key):
    with psycopg.connect(admin_dsn) as admin:
        return admin.execute("select status, clock_started_at, cancel_reason from caseward.deadline"
                             " where case_id = %s and rule_key = %s order by created_at", [case_id, rule_key]).fetchall()


# Cases ---------------------------------------------------------------------------------

def test_closing_a_case_needs_the_current_version(landlord, client_api, app_conn, world):
    case, _ = aware_case(app_conn, world)
    etag = landlord.get(f"/cases/{case}").headers["ETag"]
    closed = patch(landlord, f"/cases/{case}", {"status": "resolved"}, etag)
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "resolved" and closed.json()["closed_at"]
    problem(patch(landlord, f"/cases/{case}", {"status": "open"}, etag), 412, "stale")
    reopened = patch(landlord, f"/cases/{case}", {"status": "open"}, closed.headers["ETag"]).json()
    assert reopened["closed_at"] is None
    contractor = As(client_api, world, "user_c", "client")
    problem(patch(contractor, f"/cases/{case}", {"status": "closed"}, closed.headers["ETag"]), 404, "not_found")


def test_severity_changes_replace_the_deadlines(landlord, staff_conn, app_conn, worker, admin_dsn, world, rule,
                                                calendar_ew):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        emergency = new_rule(conn, world, applies_to="emergency", duration=24, duration_unit="hours", day_type=None,
                             completion_event="made_safe")
    case, _ = aware_case(app_conn, world)
    worker.drain()
    assert [d[0] for d in deadlines(admin_dsn, case, rule[0])] == ["open"]

    etag = landlord.get(f"/cases/{case}").headers["ETag"]
    problem(patch(landlord, f"/cases/{case}", {"severity": "emergency"}, etag), 422, "reason_required")
    changed = patch(landlord, f"/cases/{case}", {"severity": "emergency", "reason": "Child with asthma at home"}, etag)
    assert changed.status_code == 200 and changed.json()["severity"] == "emergency"
    worker.drain()
    [(status, _, reason)] = deadlines(admin_dsn, case, rule[0])
    assert status == "cancelled" and "Child with asthma at home" in reason
    assert [d[0] for d in deadlines(admin_dsn, case, emergency[0])] == ["open"]
    timeline = landlord.get(f"/cases/{case}/timeline", params={"limit": 100}).json()["items"]
    assert any(e["entry_type"] == "event" and "Child with asthma" in (e["event"]["notes"] or "") for e in timeline)


def test_cases_are_assigned_only_to_engaged_contractors(landlord, app_conn, world):
    case, _ = aware_case(app_conn, world)
    etag = landlord.get(f"/cases/{case}").headers["ETag"]
    problem(patch(landlord, f"/cases/{case}", {"assigned_contractor_id": str(world.landlord_b)}, etag), 422,
            "recipient_not_engaged")
    assigned = patch(landlord, f"/cases/{case}", {"assigned_contractor_id": str(world.contractor_c)}, etag)
    assert assigned.json()["assigned_contractor_id"] == str(world.contractor_c)


def test_defects_link_to_cases_of_the_same_landlord(handler, staff_conn, app_conn, world):
    case, _ = aware_case(app_conn, world)
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        mine = insert(conn, "defect", client_id=world.landlord_a, property_id=world.property_a, code_scheme="eicr",
                      code="C2", description="Cracked socket", identified_on="2026-10-01")
        theirs = insert(conn, "defect", client_id=world.landlord_b, property_id=world.property_b, code_scheme="eicr",
                        code="C2", description="Other", identified_on="2026-10-01")
    assert handler.api.put(f"/cases/{case}/defects/{mine}", headers=handler.headers).status_code == 204
    assert handler.get(f"/cases/{case}").json()["defect_ids"] == [str(mine)]
    problem(handler.api.put(f"/cases/{case}/defects/{theirs}", headers=handler.headers), 404, "not_found")
    assert handler.api.delete(f"/cases/{case}/defects/{mine}", headers=handler.headers).status_code == 204
    problem(handler.api.delete(f"/cases/{case}/defects/{mine}", headers=handler.headers), 404, "not_found")


# Defects ---------------------------------------------------------------------------------

def test_remediating_a_defect_meets_its_deadline(landlord, client_api, staff_conn, app_conn, worker, admin_dsn, world,
                                                 calendar_ew):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        repair = new_rule(conn, world, subject_kind="defect", applies_to="eicr:C1", trigger_event="defect_identified",
                          completion_event="defect_remediated", duration=28, day_type="calendar")
    with scoped_transaction(app_conn, world.a) as conn:
        defect = insert(conn, "defect", client_id=world.landlord_a, property_id=world.property_a, code_scheme="eicr",
                        code="C1", description="Exposed conductors", identified_on="2026-12-01")
    worker.drain()
    contractor = As(client_api, world, "user_c", "client")
    etag = contractor.get(f"/defects/{defect}").headers["ETag"]
    problem(patch(contractor, f"/defects/{defect}", {"status": "closed"}, etag), 403, "forbidden")
    fixed = patch(contractor, f"/defects/{defect}", {"status": "remediated", "note": "Replaced consumer unit"}, etag)
    assert fixed.status_code == 200, fixed.text
    with psycopg.connect(admin_dsn) as admin:
        status, note = admin.execute("select d.status, f.status_note from caseward.deadline d"
                                     " join caseward.defect f on f.id = d.defect_id"
                                     " where d.defect_id = %s and d.rule_key = %s", [defect, repair[0]]).fetchone()
    assert (status, note) == ("met", "Replaced consumer unit")
    closed = patch(landlord, f"/defects/{defect}", {"status": "closed"}, fixed.json()["row_version"])
    assert closed.json()["closed_at"] is not None


# Deadlines --------------------------------------------------------------------------------

def test_a_corrected_awareness_time_moves_the_deadline(lead, handler, app_conn, worker, admin_dsn, world, rule,
                                                       calendar_ew):
    case, event = aware_case(app_conn, world, at="2026-12-23T15:00:00+00:00")
    worker.drain()
    with scoped_transaction(app_conn, world.a) as conn:
        insert(conn, "case_event", client_id=world.landlord_a, case_id=case, kind="case_aware",
               occurred_at="2026-12-21T09:00:00+00:00", corrects_event_id=event, notes="Tenant first called on the 21st",
               recorded_by=world.user_a)
        deadline_id = conn.execute("select id from caseward.deadline where case_id = %s and rule_key = %s",
                                   [case, rule[0]]).fetchone()[0]
    problem(handler.post(f"/deadlines/{deadline_id}/cancel", json={"reason": "Wrong start"}), 403, "forbidden")
    cancelled = lead.post(f"/deadlines/{deadline_id}/cancel", json={"reason": "Awareness date corrected to 21 Dec"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    worker.drain()
    rows = deadlines(admin_dsn, case, rule[0])
    assert [(r[0], r[1].isoformat()) for r in rows] == [
        ("cancelled", "2026-12-23T15:00:00+00:00"), ("open", "2026-12-21T09:00:00+00:00")]
    problem(lead.post(f"/deadlines/{deadline_id}/cancel", json={"reason": "again"}), 422, "not_cancellable")


# Extraction re-runs ---------------------------------------------------------------------------

def test_extraction_reruns_only_when_the_document_is_ready(handler, app_conn, admin_dsn, world):
    with scoped_transaction(app_conn, world.a) as conn:
        doc = insert(conn, "document", client_id=world.landlord_a, doc_type="certificate", certificate_type="eicr",
                     source="upload", received_at=datetime.now(UTC), storage_key=f"k/{uuid.uuid4()}",
                     sha256=uuid.uuid4().hex * 2, mime_type="application/pdf", size_bytes=10)
    problem(handler.post(f"/documents/{doc}/extractions"), 409, "not_ready")
    with psycopg.connect(admin_dsn) as admin:
        admin.execute("update caseward.document set malware_scan = 'clean' where id = %s", [doc])
        admin.execute("insert into caseward.ocr_output (client_id, document_id, engine, engine_version,"
                      " text_storage_key) values (%s, %s, 'x', '1', 'k')", [world.landlord_a, doc])
    queued = handler.post(f"/documents/{doc}/extractions")
    assert queued.status_code == 202 and queued.headers["location"] == f"/documents/{doc}"
    with psycopg.connect(admin_dsn) as admin:
        assert admin.execute("select count(*) from caseward.job where kind = 'extract'"
                             " and payload ->> 'document_id' = %s", [str(doc)]).fetchone()[0] == 1


# Audit and jobs ------------------------------------------------------------------------------------

def test_audit_search_and_chain_check(lead, handler, landlord, world):
    created = landlord.post("/properties", json={"address_line_1": "9 Audit Way", "postcode": "AB1 2CD",
                                                 "jurisdiction": "england"}).json()
    events = lead.get("/audit-events", params={"entity_type": "property", "entity_id": created["id"]}).json()["items"]
    assert [(e["action"], e["actor_user_id"]) for e in events] == [("insert", str(world.user_a))]
    assert events[0]["after"]["address_line_1"] == "9 Audit Way"
    problem(handler.get("/audit-events"), 403, "forbidden")
    check = lead.post("/audit/verification").json()
    assert check["intact"] is True and check["last_event_id"] >= events[0]["id"]


def test_dead_jobs_can_be_seen_and_retried(ops_api, staff_conn, admin_dsn, lead, world):
    with psycopg.connect(admin_dsn) as admin:
        job_id = admin.execute(
            "insert into caseward.job (client_id, kind, idempotency_key, status, attempts, last_error)"
            " values (%s, 'ocr', %s, 'dead', 5, 'boom') returning id", [world.landlord_a, f"dead-{uuid.uuid4()}"]).fetchone()[0]
    summary = {k["kind"]: k for k in lead.get("/jobs/summary").json()["by_kind"]}
    assert summary["ocr"]["dead"] >= 1
    name = f"admin{uuid.uuid4().hex[:4]}"
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        insert(conn, "app_user", idp_subject=world.idp_subject(name), email=f"{name}-{world.tag}@example.test",
               display_name=name, staff_role="admin")
    admin_user = As(ops_api, world, name, "operations")
    problem(lead.post(f"/jobs/{job_id}/retry"), 403, "forbidden")
    assert admin_user.post(f"/jobs/{job_id}/retry").status_code == 202
    problem(admin_user.post(f"/jobs/{job_id}/retry"), 409, "not_dead")
    with psycopg.connect(admin_dsn) as admin:
        assert admin.execute("select status, attempts from caseward.job where id = %s", [job_id]).fetchone() == ("queued", 0)
