"""Configuration a compliance lead maintains, end to end through the API."""

import uuid
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from caseward.api.handlers import client as client_handlers
from caseward.api.handlers import operations as ops_handlers
from caseward.db import scoped_transaction
from caseward.deadlines import UK
from caseward.services import Services
from caseward.storage import LocalStorage
from caseward.worker.runner import Worker
from conftest import PASSWORD, insert, new_case
from test_api import As, make_client, problem

TODAY = datetime.now(UK).date()


class Feed:
    """Stands in for the GOV.UK bank holidays feed."""

    def __init__(self):
        self.extra = []

    def __call__(self):
        def region(name, events):
            return {"division": name, "events": [{"title": t, "date": d, "notes": "", "bunting": True}
                                                 for d, t in events + self.extra]}
        return {"england-and-wales": region("england-and-wales", [("2026-12-25", "Christmas Day"),
                                                                  ("2027-12-27", "Christmas Day (substitute)")]),
                "scotland": region("scotland", [("2026-11-30", "St Andrew's Day"), ("2027-12-27", "Christmas")]),
                "northern-ireland": region("northern-ireland", [("2027-03-17", "St Patrick's Day"),
                                                                ("2027-12-27", "Christmas")])}


@pytest.fixture(scope="module")
def feed():
    return Feed()


@pytest.fixture(scope="module")
def ops_api(database, tmp_path_factory, feed):
    services = Services(storage=LocalStorage(tmp_path_factory.mktemp("s")), bank_holidays=feed)
    pool, api = make_client(database, "operations", "cw_test_staff", ops_handlers.HANDLERS, services)
    yield api
    pool.close()


@pytest.fixture(scope="module")
def client_api(database):
    pool, api = make_client(database, "client", "cw_test_app", client_handlers.HANDLERS)
    yield api
    pool.close()


@pytest.fixture
def lead(ops_api, world):
    return As(ops_api, world, "lead", "operations")


@pytest.fixture
def second_lead(ops_api, staff_conn, world):
    name = f"lead2{uuid.uuid4().hex[:4]}"
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        insert(conn, "app_user", idp_subject=world.subject(name), email=f"{name}-{world.tag}@example.test",
               display_name=name, staff_role="compliance_lead")
    return As(ops_api, world, name, "operations")


@pytest.fixture
def handler(ops_api, world):
    return As(ops_api, world, "case_handler", "operations")


def patch(who, url, body, etag):
    return who.api.patch(url, json=body, headers={**who.headers, "If-Match": etag,
                                                   "Content-Type": "application/merge-patch+json"})


def draft(who, pack="awaab_social", key=None, **overrides):
    body = {"rule_pack": pack, "rule_key": key or f"cfg.{uuid.uuid4().hex[:8]}", "title": "Investigate significant hazard",
            "description": "UNVERIFIED test rule", "jurisdiction": "england", "subject_kind": "case",
            "applies_to": "significant", "trigger_event": "case_aware", "completion_event": "investigation_completed",
            "duration": 10, "duration_unit": "days", "day_type": "working", **overrides}
    response = who.post("/deadline-rules", json=body)
    assert response.status_code == 201, response.text
    return response


def approve(who, rule_id, effective_from=None, **extra):
    return who.post(f"/deadline-rules/{rule_id}/approve", json={
        "effective_from": (effective_from or TODAY).isoformat(), "confirmation": "Signed off by J Smith, solicitor", **extra})


# Deadline rules ----------------------------------------------------------------------

def test_a_rule_goes_from_draft_to_approved(lead, second_lead):
    created = draft(lead)
    rule = created.json()
    assert (rule["status"], rule["verified"], rule["version"]) == ("draft", False, 1)
    edited = patch(lead, f"/deadline-rules/{rule['id']}", {"duration": 12}, created.headers["ETag"])
    assert edited.status_code == 200 and edited.json()["duration"] == 12
    problem(patch(lead, f"/deadline-rules/{rule['id']}", {"duration": 9}, created.headers["ETag"]), 412, "stale")

    problem(approve(lead, rule["id"]), 422, "self_approval")
    problem(approve(second_lead, rule["id"], TODAY - timedelta(days=1)), 422, "effective_date_in_past")
    approved = approve(second_lead, rule["id"])
    assert approved.status_code == 200, approved.text
    assert (approved.json()["status"], approved.json()["verified"], approved.json()["approval_reference"]) == (
        "approved", True, "Signed off by J Smith, solicitor")
    problem(patch(second_lead, f"/deadline-rules/{rule['id']}", {"duration": 5}, approved.headers["ETag"]), 422,
            "immutable")
    problem(approve(second_lead, rule["id"]), 409, "not_draft")


def test_only_compliance_leads_approve(handler, lead):
    rule = draft(lead).json()
    problem(approve(handler, rule["id"]), 403, "forbidden")


def test_new_versions_replace_the_old_from_a_date(lead, second_lead):
    v1 = draft(lead).json()
    approve(second_lead, v1["id"])
    v2 = draft(lead, key=v1["rule_key"], duration=7).json()
    assert v2["version"] == 2
    problem(approve(second_lead, v2["id"], TODAY + timedelta(days=30)), 422, "supersedes_required")
    replaced = approve(second_lead, v2["id"], TODAY + timedelta(days=30), supersedes_version=1)
    assert replaced.status_code == 200, replaced.text
    old = lead.get(f"/deadline-rules/{v1['id']}").json()
    assert (old["status"], old["effective_to"]) == ("approved", (TODAY + timedelta(days=30)).isoformat())
    versions = lead.get("/deadline-rules", params={"rule_key": v1["rule_key"]}).json()["items"]
    assert [v["version"] for v in versions] == [1, 2]


def test_rules_can_be_retired(lead, second_lead):
    rule = draft(lead).json()
    approve(second_lead, rule["id"])
    retired = second_lead.post(f"/deadline-rules/{rule['id']}/retire", json={
        "effective_to": (TODAY + timedelta(days=1)).isoformat(), "reason": "Regulations changed"})
    assert retired.status_code == 200 and retired.json()["status"] == "retired"


def test_dry_run_works_on_drafts(lead, ops_api):
    lead.post("/holiday-calendars/import")
    rule = draft(lead, duration=3).json()
    result = lead.post(f"/deadline-rules/{rule['id']}/test", json={"clock_started_at": "2026-12-24T11:00:00Z"})
    assert result.status_code == 200, result.text
    # 24 Dec + 3 working days, skipping Christmas Day and the weekend.
    assert result.json()["due_local_date"] == "2026-12-30"
    hours = draft(lead, duration=24, duration_unit="hours", day_type=None).json()
    assert hours["day_type"] is None
    assert lead.post(f"/deadline-rules/{hours['id']}/test",
                     json={"clock_started_at": "2026-10-24T12:00:00Z"}).json()["due_at"] == "2026-10-25T12:00:00Z"


def test_rules_must_be_consistent(lead):
    response = lead.post("/deadline-rules", json={
        "rule_pack": "awaab_social", "rule_key": "bad", "title": "t", "description": "d", "jurisdiction": "england",
        "subject_kind": "case", "applies_to": "*", "trigger_event": "case_aware", "completion_event": "made_safe",
        "duration": 24, "duration_unit": "hours", "day_type": "working"})
    problem(response, 422, "rule_violation")
    response = lead.post("/deadline-rules", json={
        "rule_pack": "awaab_social", "rule_key": "bad", "title": "t", "description": "d", "jurisdiction": "england",
        "subject_kind": "case", "applies_to": "*", "trigger_event": "defect_identified", "completion_event": "made_safe",
        "duration": 24, "duration_unit": "hours"})
    problem(response, 422, "invalid_reference")


def test_an_approved_rule_creates_real_deadlines(lead, second_lead, ops_api, app_conn, database, world):
    lead.post("/holiday-calendars/import")
    rule = draft(lead, duration_unit="hours", day_type=None, duration=24, applies_to="emergency",
                 title="Make safe (emergency)", completion_event="made_safe").json()
    approve(second_lead, rule["id"])
    with scoped_transaction(app_conn, world.a) as conn:
        case = new_case(conn, world, severity="emergency")
        insert(conn, "case_event", client_id=world.landlord_a, case_id=case, kind="case_aware",
               occurred_at=datetime.now(UTC), recorded_by=world.user_a)
    pool = ConnectionPool(psycopg.conninfo.make_conninfo(database, user="cw_test_worker", password=PASSWORD),
                          kwargs={"autocommit": True}, min_size=1, max_size=1, open=True)
    try:
        Worker(pool).drain()
    finally:
        pool.close()
    with scoped_transaction(app_conn, world.a) as conn:
        rows = conn.execute("select rule_version, status from caseward.deadline where case_id = %s and rule_key = %s",
                            [case, rule["rule_key"]]).fetchall()
    assert rows == [(1, "open")]


# Rule packs -----------------------------------------------------------------------------

def test_a_pack_goes_live_only_when_its_rules_are_approved(lead, second_lead, handler, world):
    rule = draft(lead, pack="block_safety").json()
    problem(handler.post("/rule-packs/block_safety/activate", json={"confirmation": "x" * 10}), 403, "forbidden")
    body = problem(second_lead.post("/rule-packs/block_safety/activate", json={"confirmation": "Board sign-off"}), 422,
                   "rules_not_approved")
    assert rule["rule_key"] in body["detail"]
    approve(second_lead, rule["id"])
    live = second_lead.post("/rule-packs/block_safety/activate", json={"confirmation": "Board sign-off 2026-10-30"})
    assert live.status_code == 200, live.text
    assert (live.json()["status"], live.json()["approval_reference"]) == ("live", "Board sign-off 2026-10-30")
    packs = {p["code"]: p["status"] for p in lead.get("/rule-packs").json()["items"]}
    assert packs["block_safety"] == "live" and packs["awaab_private_rented"] == "off"


def test_packs_are_enabled_per_landlord(lead, world):
    url = f"/clients/{world.landlord_b}/rule-packs"
    enabled = lead.post(url, json={"rule_pack": "awaab_social", "enabled_from": "2026-11-30"})
    assert enabled.status_code == 201, enabled.text
    problem(lead.post(url, json={"rule_pack": "awaab_social", "enabled_from": "2026-11-30"}), 409, "conflict")
    assert lead.get(url).json()["items"] == [{"rule_pack": "awaab_social", "enabled_from": "2026-11-30",
                                              "enabled_to": None}]
    problem(lead.post(f"/clients/{world.contractor_c}/rule-packs",
                      json={"rule_pack": "awaab_social", "enabled_from": "2026-11-30"}), 404, "not_found")


# Bank holidays ---------------------------------------------------------------------------

def test_holidays_import_only_what_changed(lead, feed):
    lead.post("/holiday-calendars/import")
    assert lead.post("/holiday-calendars/import").json()["items"] == []
    feed.extra = [("2027-05-03", "Early May bank holiday")]
    try:
        changed = lead.post("/holiday-calendars/import").json()["items"]
    finally:
        feed.extra = []
    assert {c["region"] for c in changed} == {"england-and-wales", "scotland", "northern-ireland"}
    assert all(c["holiday_count"] == 3 for c in changed)


# Code lists ------------------------------------------------------------------------------

def test_hazard_phases_are_set_with_the_current_version(lead, handler):
    categories = {c["code"]: c for c in lead.get("/config/hazard-categories").json()["items"]}
    excess_cold = categories["excess_cold"]
    url = "/config/hazard-categories/excess_cold"
    problem(patch(handler, url, {"phase": 2}, excess_cold["row_version"]), 403, "forbidden")
    updated = lead.api.patch(url, json={"phase": 2, "in_scope_from": "2026-11-30"},
                             headers={**lead.headers, "If-Match": excess_cold["row_version"]})
    assert updated.status_code == 200, updated.text
    assert (updated.json()["phase"], updated.json()["in_scope_from"]) == (2, "2026-11-30")
    problem(lead.api.patch(url, json={"phase": 3}, headers={**lead.headers, "If-Match": excess_cold["row_version"]}),
            412, "stale")


def test_code_lists_grow_by_configuration(lead, handler, client_api, world):
    code = f"fire_door_{uuid.uuid4().hex[:4]}"
    assert lead.post("/config/certificate-types", json={"code": code, "rule_pack": "block_safety",
                                                        "name": "Fire door inspection"}).status_code == 201
    problem(lead.post("/config/certificate-types", json={"code": code, "rule_pack": "block_safety", "name": "x"}),
            409, "conflict")
    assert lead.post("/config/defect-codes", json={"scheme": code, "code": "FAIL", "name": "Door fails to close",
                                                   "rule_pack": "block_safety"}).status_code == 201
    assert lead.post("/config/event-types", json={"code": f"door_checked_{uuid.uuid4().hex[:4]}", "subject_kind": "case",
                                                  "name": "Fire door checked", "is_excuse_evidence": False,
                                                  "rule_pack": "block_safety"}).status_code == 201
    assert lead.post("/config/vulnerability-factor-types", json={
        "code": f"resp_{uuid.uuid4().hex[:4]}", "name": "Respiratory condition",
        "rule_pack": "awaab_social"}).status_code == 201
    problem(handler.post("/config/defect-codes", json={"scheme": code, "code": "X", "name": "x",
                                                       "rule_pack": "block_safety"}), 403, "forbidden")
    reference = As(client_api, world, "user_a", "client").get("/reference/certificate-types").json()
    assert code in {c["code"] for c in reference["items"]}
    assert (code, "FAIL") in {(d["scheme"], d["code"]) for d in reference["defect_codes"]}
