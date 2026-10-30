"""The work queue, quality sampling, the client dashboard and the case timeline."""

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from caseward.api.handlers import client as client_handlers
from caseward.api.handlers import operations as ops_handlers
from caseward.db import scoped_transaction
from conftest import insert, new_case, new_deadline
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


@pytest.fixture
def handler(ops_api, world):
    return As(ops_api, world, "case_handler", "operations")


@pytest.fixture
def lead(ops_api, world):
    return As(ops_api, world, "lead", "operations")


@pytest.fixture
def other_handler(ops_api, staff_conn, world):
    name = f"handler{uuid.uuid4().hex[:4]}"
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        insert(conn, "app_user", idp_subject=world.idp_subject(name), email=f"{name}-{world.tag}@example.test",
               display_name=name, staff_role="case_handler")
    return As(ops_api, world, name, "operations")


def task(staff_conn, world, **values):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        return str(insert(conn, "task", client_id=world.landlord_a, kind=values.pop("kind", "other"), **values))


def claim(who, world):
    return who.post("/tasks/claim", json={"client_id": str(world.landlord_a)})


def patch(who, url, body, etag):
    return who.api.patch(url, json=body, headers={**who.headers, "If-Match": etag})


def complete(who, task_id, etag):
    return who.api.post(f"/tasks/{task_id}/complete", headers={**who.headers, "If-Match": etag})


# Queue -------------------------------------------------------------------------------

def test_claim_order_is_priority_then_deadline_then_sla(handler, staff_conn, app_conn, world, rule, calendar_ew):
    now = datetime.now(UTC)
    with scoped_transaction(app_conn, world.a) as conn:
        soon_case = new_case(conn, world)
        start = now - timedelta(days=1)
        new_deadline(conn, world, rule, calendar_ew, soon_case, clock_started_at=start, due_at=now + timedelta(days=1))
    later_sla = task(staff_conn, world, priority=2, sla_due_at=now + timedelta(hours=1))
    with_deadline = task(staff_conn, world, priority=2, case_id=soon_case, sla_due_at=now + timedelta(days=5))
    urgent = task(staff_conn, world, priority=1, sla_due_at=now + timedelta(days=9))
    routine = task(staff_conn, world, priority=3)
    order = [claim(handler, world).json()["id"] for _ in range(4)]
    assert order == [urgent, with_deadline, later_sla, routine]
    assert claim(handler, world).status_code == 204


def test_claimed_tasks_are_mine_and_in_progress(handler, other_handler, staff_conn, world):
    task_id = task(staff_conn, world)
    claimed = claim(handler, world)
    assert claimed.json()["id"] == task_id
    assert claimed.json()["status"] == "in_progress" and claimed.json()["started_at"]
    assert claim(other_handler, world).status_code == 204
    mine = handler.get("/tasks", params={"assignee": "me", "client_id": str(world.landlord_a)}).json()["items"]
    assert [t["id"] for t in mine] == [task_id]


def test_task_list_pages_in_queue_order(handler, staff_conn, world):
    ids = [task(staff_conn, world, priority=p) for p in (3, 1, 2)]
    seen, cursor = [], None
    while True:
        body = handler.get("/tasks", params={"client_id": str(world.landlord_a), "limit": 1,
                                             **({"cursor": cursor} if cursor else {})}).json()
        seen += [t["id"] for t in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert seen == [ids[1], ids[2], ids[0]]


def test_updates_need_the_current_version(handler, staff_conn, world):
    task_id = task(staff_conn, world)
    etag = claim(handler, world).headers["ETag"]
    blocked = patch(handler, f"/tasks/{task_id}", {"status": "blocked", "note": "Waiting for the tenant"}, etag)
    assert blocked.status_code == 200, blocked.text
    assert (blocked.json()["status"], blocked.json()["note"]) == ("blocked", "Waiting for the tenant")
    problem(patch(handler, f"/tasks/{task_id}", {"priority": 1}, etag), 412, "stale")


def test_blocking_and_cancelling_need_a_reason(handler, staff_conn, world):
    task_id = task(staff_conn, world)
    etag = claim(handler, world).headers["ETag"]
    problem(patch(handler, f"/tasks/{task_id}", {"status": "cancelled"}, etag), 422, "note_required")


def test_releasing_puts_a_task_back_for_anyone(handler, other_handler, staff_conn, world):
    task_id = task(staff_conn, world)
    etag = claim(handler, world).headers["ETag"]
    released = patch(handler, f"/tasks/{task_id}", {"status": "queued"}, etag).json()
    assert (released["status"], released["assignee_id"]) == ("queued", None)
    assert claim(other_handler, world).json()["id"] == task_id


def test_only_the_assignee_or_a_lead_changes_a_task(handler, other_handler, lead, staff_conn, world):
    task_id = task(staff_conn, world)
    etag = claim(handler, world).headers["ETag"]
    problem(patch(other_handler, f"/tasks/{task_id}", {"priority": 1}, etag), 403, "forbidden")
    moved = patch(lead, f"/tasks/{task_id}", {"assignee_id": str(world.compliance_lead)}, etag)
    assert moved.status_code == 200 and moved.json()["assignee_id"] == str(world.compliance_lead)


def test_tasks_go_only_to_case_handlers_and_leads(handler, staff_conn, world):
    task_id = task(staff_conn, world)
    etag = claim(handler, world).headers["ETag"]
    problem(patch(handler, f"/tasks/{task_id}", {"assignee_id": str(world.reviewer)}, etag), 422, "invalid_assignee")


def test_completion_needs_the_work_done(handler, staff_conn, app_conn, world):
    with scoped_transaction(app_conn, world.a) as conn:
        document = insert(conn, "document", client_id=world.landlord_a, doc_type="certificate", source="upload",
                          received_at=datetime.now(UTC), storage_key=f"k/{uuid.uuid4()}", sha256=uuid.uuid4().hex * 2,
                          mime_type="application/pdf", size_bytes=10)
        extraction = insert(conn, "extraction", client_id=world.landlord_a, document_id=document, status="succeeded",
                            result=psycopg.types.json.Jsonb({}), field_confidence=psycopg.types.json.Jsonb({}),
                            completed_at=datetime.now(UTC), model_provider="p", model_version="m", prompt_id="x",
                            prompt_version="1", output_schema_version="0")
    review = task(staff_conn, world, kind="review_extraction", extraction_id=extraction)
    etag = claim(handler, world).headers["ETag"]
    problem(complete(handler, review, etag), 422, "work_not_finished")

    other = task(staff_conn, world, priority=5)
    etag = handler.get(f"/tasks/{other}").headers["ETag"]
    etag = patch(handler, f"/tasks/{other}", {"status": "in_progress"}, etag).headers["ETag"]
    done = complete(handler, other, etag)
    assert done.status_code == 200 and done.json()["status"] == "done"
    problem(patch(handler, f"/tasks/{other}", {"priority": 1}, done.headers["ETag"]), 409, "task_closed")


# Quality ------------------------------------------------------------------------------

def finished_task(staff_conn, world, assignee):
    return task(staff_conn, world, status="done", assignee_id=assignee, completed_at=datetime.now(UTC))


def test_sampling_skips_my_own_and_unfinished_work(ops_api, staff_conn, world):
    analyst = As(ops_api, world, "analyst", "operations")
    theirs = finished_task(staff_conn, world, world.caseworker)
    task(staff_conn, world)  # not finished
    sample = analyst.get("/quality/sample", params={"client_id": str(world.landlord_a), "size": 50}).json()["items"]
    assert [t["id"] for t in sample] == [theirs]
    reviewed = analyst.post(f"/tasks/{theirs}/quality-reviews", json={"score": 4, "notes": "Good"})
    assert reviewed.status_code == 201, reviewed.text
    problem(analyst.post(f"/tasks/{theirs}/quality-reviews", json={"score": 5}), 409, "already_reviewed")
    assert analyst.get("/quality/sample", params={"client_id": str(world.landlord_a)}).json()["items"] == []


def test_nobody_scores_their_own_work(lead, staff_conn, world):
    mine = finished_task(staff_conn, world, world.compliance_lead)
    problem(lead.post(f"/tasks/{mine}/quality-reviews", json={"score": 5}), 422, "self_review")


# Dashboard and timeline ---------------------------------------------------------------------

def test_dashboard_counts_the_clients_own_work(client_api, app_conn, world, rule, calendar_ew):
    now = datetime.now(UTC)
    with scoped_transaction(app_conn, world.a) as conn:
        for fraction in (0.1, 0.9):
            start = now - timedelta(days=10 * fraction)
            new_deadline(conn, world, rule, calendar_ew, new_case(conn, world), clock_started_at=start,
                         due_at=start + timedelta(days=10))
        insert(conn, "hazard_case", client_id=world.landlord_a, property_id=world.property_a, reference="E-1",
               hazard_category="fire", severity="emergency", received_at=now, aware_at=now,
               awareness_source="tenant_report")
        insert(conn, "inspection", client_id=world.landlord_a, property_id=world.property_a, certificate_type="eicr",
               inspected_on="2021-01-10", next_due_on="2026-01-10", outcome="satisfactory")
        insert(conn, "inspection", client_id=world.landlord_a, property_id=world.property_a,
               certificate_type="gas_safety", inspected_on="2025-11-20", next_due_on=(now + timedelta(days=10)).date())
    body = As(client_api, world, "user_a", "client").get("/dashboard").json()
    assert (body["deadlines"]["on_track"], body["deadlines"]["alert_80"]) == (1, 1)
    assert (body["cases"]["open"], body["cases"]["emergency_open"]) == (3, 1)
    assert body["certificates"] == {"expired": 1, "due_within_30_days": 1, "by_type": {"eicr": 1, "gas_safety": 1}}
    other = As(client_api, world, "user_b", "client").get("/dashboard").json()
    assert other["cases"]["open"] == 0 and other["certificates"]["by_type"] == {}
    provider = As(client_api, world, "user_c", "client")
    narrowed = provider.get("/dashboard", params={"landlord_client_id": str(world.landlord_a)}).json()
    assert narrowed["cases"]["emergency_open"] == 1


def test_timeline_tells_the_story_in_order(client_api, handler, staff_conn, app_conn, world):
    with scoped_transaction(app_conn, world.a) as conn:
        case = new_case(conn, world)
        for kind, at in (("case_aware", "2026-12-23T15:00:00+00:00"), ("access_refused", "2026-12-28T10:00:00+00:00"),
                         ("investigation_completed", "2027-01-04T10:00:00+00:00")):
            insert(conn, "case_event", client_id=world.landlord_a, case_id=case, kind=kind, occurred_at=at,
                   recorded_by=world.user_a)
        conn.execute("update caseward.hazard_case set status = 'investigating' where id = %s", [case])
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        template = insert(conn, "letter_template", template_key=f"t{uuid.uuid4().hex[:6]}", version=1, title="T",
                          body="x", status="approved", approved_by=world.compliance_lead, approved_at=datetime.now(UTC))
        letter = insert(conn, "letter", client_id=world.landlord_a, case_id=case, template_id=template,
                        recipient_kind="tenant", recipient_contact_id=world.contact_a, channel="post")
        conn.execute("insert into caseward.letter_version (letter_id, client_id, version, body, origin, created_by)"
                     " values (%s, %s, 1, 'Final', 'human_edit', %s)", [letter, world.landlord_a, world.caseworker])
        conn.execute("update caseward.letter set status = 'approved', approved_version = 1, approved_by = %s,"
                     " approved_at = now() where id = %s", [world.compliance_lead, letter])
        conn.execute("update caseward.letter set status = 'sent', sent_at = '2027-01-06T09:00:00Z' where id = %s", [letter])

    landlord = As(client_api, world, "user_a", "client")
    pages, cursor = [], None
    while True:
        body = landlord.get(f"/cases/{case}/timeline", params={"limit": 2, **({"cursor": cursor} if cursor else {})}).json()
        pages += body["items"]
        cursor = body["next_cursor"]
        if not cursor:
            break
    events = [e["summary"] for e in pages if e["entry_type"] in ("event", "letter")]
    assert events == ["Landlord became aware", "Access refused by tenant", "Investigation completed",
                      "Letter sent (post)"]
    assert [e["at"] for e in pages] == sorted(e["at"] for e in pages)
    assert any(e["entry_type"] == "status_change" for e in pages)

    contractor = As(client_api, world, "user_c", "client")
    seen = contractor.get(f"/cases/{case}/timeline", params={"limit": 100}).json()["items"]
    assert "letter" not in {e["entry_type"] for e in seen}
    assert len(handler.get(f"/cases/{case}/timeline", params={"limit": 100}).json()["items"]) == len(pages)
