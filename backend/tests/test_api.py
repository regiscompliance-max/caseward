"""End-to-end tests of the client and operations APIs against a real database.

Every response is checked against the OpenAPI spec (validate_responses),
so these tests also prove the implementation matches the contract.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from caseward.api.auth import JwtVerifier
from caseward.api.framework import build_app
from caseward.api.handlers import client as client_handlers
from caseward.api.handlers import operations as ops_handlers
from caseward.api.settings import Settings
from caseward.api.spec import load_spec
from caseward.db import scoped_transaction
from conftest import PASSWORD, insert, new_case, new_deadline

ISSUER = "https://idp.caseward.test/"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(subject, audience, mfa=True, expires_in=300, key=KEY):
    now = int(time.time())
    claims = {"sub": subject, "iss": ISSUER, "aud": audience, "iat": now, "exp": now + expires_in,
              "amr": ["pwd", "mfa"] if mfa else ["pwd"]}
    return jwt.encode(claims, key, algorithm="RS256")


def make_client(database, kind, role, handlers, services=None):
    pool = ConnectionPool(psycopg.conninfo.make_conninfo(database, user=role, password=PASSWORD),
                          kwargs={"autocommit": True}, min_size=1, max_size=4, open=True)
    settings = Settings(database_url="", oidc_issuer=ISSUER, oidc_audience=kind, validate_responses=True)
    app = build_app(kind, settings, pool, JwtVerifier(ISSUER, kind, key=KEY.public_key()), handlers, services)
    return pool, TestClient(app)


@pytest.fixture(scope="module")
def client_api(database):
    pool, client = make_client(database, "client", "cw_test_app", client_handlers.HANDLERS)
    yield client
    pool.close()


@pytest.fixture(scope="module")
def ops_api(database):
    pool, client = make_client(database, "operations", "cw_test_staff", ops_handlers.HANDLERS)
    yield client
    pool.close()


class As:
    """Requests as a seeded user."""

    def __init__(self, api, world, name, audience):
        self.api = api
        self.headers = {"Authorization": f"Bearer {token(world.subject(name), audience)}"}

    def get(self, url, **kw):
        return self.api.get(url, headers={**self.headers, **kw.pop("headers", {})}, **kw)

    def post(self, url, json=None, key=None, **kw):
        headers = {**self.headers, "Idempotency-Key": key or str(uuid.uuid4()), **kw.pop("headers", {})}
        return self.api.post(url, json=json, headers=headers, **kw)


@pytest.fixture
def landlord(client_api, world):
    return As(client_api, world, "user_a", "client")


@pytest.fixture
def contractor(client_api, world):
    return As(client_api, world, "user_c", "client")


@pytest.fixture
def handler(ops_api, world):
    return As(ops_api, world, "case_handler", "operations")


def case_body(world, **overrides):
    now = datetime.now(UTC).isoformat()
    body = {"property_id": str(world.property_a), "hazard_category": "damp_and_mould_growth",
            "severity": "significant", "received_at": now, "aware_at": now, "awareness_source": "tenant_report"}
    body.update(overrides)
    return body


def problem(response, status, code):
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == code
    return response.json()


# Routing ---------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["client", "operations"])
def test_every_specified_operation_is_routed(client_api, ops_api, kind):
    app = (client_api if kind == "client" else ops_api).app
    routed = {route.name for route in app.routes}
    assert {op.operation_id for op in load_spec(kind).operations} <= routed


def test_handlers_must_match_spec_operations(database):
    with pytest.raises(ValueError, match="not in the spec"):
        make_client(database, "client", "cw_test_app", {"noSuchOperation": lambda call: None})


def test_unbuilt_operation_returns_501_after_checking_access(landlord, client_api, staff_conn, world):
    problem(landlord.get("/inspections"), 501, "not_implemented")
    viewer = make_viewer(staff_conn, world)
    problem(As(client_api, world, viewer, "client").post("/uploads", json={}), 403, "forbidden")


# Authentication --------------------------------------------------------------

def test_requires_a_token(client_api):
    body = problem(client_api.get("/me"), 401, "missing_token")
    assert body["request_id"]


@pytest.mark.parametrize("kwargs, code", [
    ({"mfa": False}, "mfa_required"),
    ({"expires_in": -10}, "token_expired"),
    ({"audience": "operations"}, "invalid_token"),
    ({"key": rsa.generate_private_key(public_exponent=65537, key_size=2048)}, "invalid_token"),
])
def test_rejects_bad_tokens(client_api, world, kwargs, code):
    audience = kwargs.pop("audience", "client")
    bad = token(world.subject("user_a"), audience, **kwargs)
    problem(client_api.get("/me", headers={"Authorization": f"Bearer {bad}"}), 401, code)


def test_rejects_unknown_and_deactivated_users(client_api, staff_conn, world):
    unknown = token("nobody", "client")
    problem(client_api.get("/me", headers={"Authorization": f"Bearer {unknown}"}), 401, "unknown_user")
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        conn.execute("update caseward.app_user set is_active = false where id = %s", [world.user_a2])
    deactivated = token(world.subject("user_a2"), "client")
    problem(client_api.get("/me", headers={"Authorization": f"Bearer {deactivated}"}), 401, "unknown_user")


def test_staff_and_client_users_use_their_own_api(client_api, ops_api, world):
    problem(As(client_api, world, "case_handler", "client").get("/me"), 403, "forbidden")
    problem(As(ops_api, world, "user_a", "operations").get("/me"), 403, "forbidden")


# Client API ------------------------------------------------------------------

def make_viewer(staff_conn, world):
    name = f"viewer{uuid.uuid4().hex[:4]}"
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        user = insert(conn, "app_user", idp_subject=world.subject(name), email=f"{name}-{world.tag}@example.test",
                      display_name=name)
        conn.execute("insert into caseward.membership (user_id, client_id, role) values (%s, %s, 'client_viewer')",
                     [user, world.landlord_a])
    return name


def test_me(landlord, world):
    body = landlord.get("/me").json()
    assert body["acting_client_id"] == str(world.landlord_a)
    assert [m["role"] for m in body["memberships"]] == ["client_admin"]


def test_acting_client_must_be_a_membership(landlord, world):
    problem(landlord.get("/me", headers={"X-Caseward-Client": str(world.landlord_b)}), 403, "forbidden")


def test_properties_are_isolated_per_client(landlord, contractor, world):
    response = landlord.get("/properties")
    assert response.status_code == 200, response.text
    assert [p["id"] for p in response.json()["items"]] == [str(world.property_a)]
    assert str(world.property_a) in [p["id"] for p in contractor.get("/properties").json()["items"]]
    assert landlord.get(f"/properties/{world.property_b}").status_code == 404


def test_viewers_are_read_only(client_api, staff_conn, world):
    viewer = As(client_api, world, make_viewer(staff_conn, world), "client")
    assert viewer.get("/properties").status_code == 200
    body = {"address_line_1": "3 New St", "postcode": "SW1A 2AA", "jurisdiction": "england"}
    problem(viewer.post("/properties", json=body), 403, "forbidden")


def test_create_is_idempotent(landlord, admin_dsn, world):
    body = {"address_line_1": "4 Mill Lane", "postcode": "le1 1aa", "jurisdiction": "england"}
    key = str(uuid.uuid4())
    first = landlord.post("/properties", json=body, key=key)
    assert first.status_code == 201
    assert first.json()["postcode"] == "LE1 1AA"
    again = landlord.post("/properties", json=body, key=key)
    assert again.status_code == 201
    assert again.json() == first.json()
    assert again.headers["Idempotent-Replayed"] == "true"
    problem(landlord.post("/properties", json={**body, "town": "Leicester"}, key=key), 422, "idempotency_key_reused")
    with psycopg.connect(admin_dsn) as admin:
        count = admin.execute("select count(*) from caseward.property where address_line_1 = '4 Mill Lane'"
                              " and client_id = %s", [world.landlord_a]).fetchone()[0]
        audited = admin.execute("select count(*) from caseward.audit_event where entity_type = 'property'"
                                " and entity_id = %s and action = 'insert'", [first.json()["id"]]).fetchone()[0]
    assert (count, audited) == (1, 1)


def test_invalid_requests_are_rejected_with_details(landlord, world):
    body = problem(landlord.post("/properties", json={"postcode": "X", "jurisdiction": "mars"}), 400, "invalid_body")
    assert {e["field"] for e in body["errors"]} >= {"/", "/postcode", "/jurisdiction"}
    problem(landlord.get("/properties", params={"colour": "blue"}), 400, "invalid_parameter")
    problem(landlord.get("/cases", params={"limit": 0}), 400, "invalid_parameter")
    naive = case_body(world, aware_at="2026-10-30T09:00:00")
    problem(landlord.post("/cases", json=naive), 400, "invalid_body")


def test_create_and_view_a_case(landlord, admin_dsn, world):
    created = landlord.post("/cases", json=case_body(world, description="Black mould in bedroom"))
    assert created.status_code == 201, created.text
    case = created.json()
    assert created.headers["Location"] == f"/cases/{case['id']}"
    fetched = landlord.get(f"/cases/{case['id']}")
    assert fetched.status_code == 200
    assert fetched.headers["ETag"]
    assert fetched.json()["hazard_category_name"] == "Damp and mould growth"
    with psycopg.connect(admin_dsn) as admin:
        events = admin.execute("select kind from caseward.case_event where case_id = %s order by kind",
                               [case["id"]]).fetchall()
        jobs = admin.execute("select payload ->> 'event' from caseward.job where kind = 'compute_deadlines'"
                             " and payload -> 'subject' ->> 'id' = %s order by 1", [case["id"]]).fetchall()
        views = admin.execute("select actor_user_id, client_id from caseward.audit_event"
                              " where action = 'view' and entity_type = 'cases' and entity_id = %s",
                              [case["id"]]).fetchall()
    assert events == [("case_aware",), ("note",)]
    assert jobs == [("case_aware",), ("note",)]
    assert views == [(world.user_a, world.landlord_a)]


def test_other_clients_cannot_see_a_case(landlord, client_api, world):
    case = landlord.post("/cases", json=case_body(world)).json()
    problem(As(client_api, world, "user_b", "client").get(f"/cases/{case['id']}"), 404, "not_found")


def test_case_must_be_for_own_property(landlord, world):
    problem(landlord.post("/cases", json=case_body(world, property_id=str(world.property_b))), 422,
            "invalid_reference")


def test_contractor_logs_events_but_cannot_open_cases(landlord, contractor, admin_dsn, world):
    problem(contractor.post("/cases", json=case_body(world)), 404, "not_found")
    case = landlord.post("/cases", json=case_body(world)).json()
    event = contractor.post(f"/cases/{case['id']}/events", json={
        "kind": "access_refused", "occurred_at": datetime.now(UTC).isoformat(), "channel": "in_person"})
    assert event.status_code == 201, event.text
    assert event.json()["is_excuse_evidence"] is True
    assert event.json()["contractor_client_id"] == str(world.contractor_c)
    with psycopg.connect(admin_dsn) as admin:
        viewed = admin.execute("select client_id from caseward.audit_event where action = 'view'"
                               " and entity_id = %s and actor_user_id = %s", [case["id"], world.user_c]).fetchall()
    assert viewed == []
    contractor.get(f"/cases/{case['id']}")
    with psycopg.connect(admin_dsn) as admin:
        viewed = admin.execute("select client_id from caseward.audit_event where action = 'view'"
                               " and entity_id = %s and actor_user_id = %s", [case["id"], world.user_c]).fetchall()
    # Recorded against the landlord, so the landlord's audit log shows the contractor's views.
    assert viewed == [(world.landlord_a,)]


def test_completion_event_meets_deadline(landlord, app_conn, world, rule, calendar_ew):
    with scoped_transaction(app_conn, world.a) as conn:
        case_id = new_case(conn, world)
        deadline = new_deadline(conn, world, rule, calendar_ew, case_id)
    response = landlord.post(f"/cases/{case_id}/events", json={
        "kind": "investigation_completed", "occurred_at": "2027-01-04T10:00:00Z"})
    assert response.status_code == 201, response.text
    assert response.json()["deadlines_met"] == [str(deadline)]
    deadlines = landlord.get("/deadlines", params={"case_id": str(case_id)}).json()["items"]
    assert [(d["status"], d["due_local_date"]) for d in deadlines] == [("met", "2027-01-11")]


def test_unknown_event_kind_is_rejected(landlord, world):
    case = landlord.post("/cases", json=case_body(world)).json()
    response = landlord.post(f"/cases/{case['id']}/events", json={"kind": "teleported", "occurred_at": "2026-10-30T10:00:00Z"})
    problem(response, 422, "invalid_reference")


def test_case_list_pages_with_a_cursor(landlord, world):
    tag = uuid.uuid4().hex[:6]
    for n in range(3):
        assert landlord.post("/cases", json=case_body(world, reference=f"P-{tag}-{n}")).status_code == 201
    seen, cursor = [], None
    while True:
        params = {"sort": "reference", "limit": 2, **({"cursor": cursor} if cursor else {})}
        body = landlord.get("/cases", params=params).json()
        seen += [c["reference"] for c in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    ours = [r for r in seen if r.startswith(f"P-{tag}")]
    assert ours == [f"P-{tag}-{n}" for n in range(3)]
    assert len(seen) == len(set(seen))


def test_reference_data_follows_enabled_rule_packs(landlord, client_api, world):
    assert len(landlord.get("/reference/hazard-categories").json()["items"]) == 29
    other = As(client_api, world, "user_b", "client")
    assert other.get("/reference/hazard-categories").json()["items"] == []
    kinds = {e["code"] for e in other.get("/reference/event-types").json()["items"]}
    assert "access_refused" in kinds


# Operations API -------------------------------------------------------------

def test_staff_work_across_clients(handler, landlord, world):
    case = landlord.post("/cases", json=case_body(world)).json()
    listed = handler.get("/cases", params={"client_id": str(world.landlord_a), "limit": 100}).json()["items"]
    assert case["id"] in [c["id"] for c in listed]
    created = handler.post("/cases", json=case_body(world), params={"client_id": str(world.landlord_a)})
    assert created.status_code == 201, created.text
    assert created.json()["client_id"] == str(world.landlord_a)


def test_quality_analysts_cannot_open_cases(ops_api, world):
    analyst = As(ops_api, world, "analyst", "operations")
    assert analyst.get("/cases").status_code == 200
    problem(analyst.post("/cases", json=case_body(world), params={"client_id": str(world.landlord_a)}), 403,
            "forbidden")


def test_staff_views_are_audited_against_the_client(handler, landlord, admin_dsn, world):
    case = landlord.post("/cases", json=case_body(world)).json()
    handler.get(f"/cases/{case['id']}")
    with psycopg.connect(admin_dsn) as admin:
        rows = admin.execute("select client_id from caseward.audit_event where action = 'view'"
                             " and entity_id = %s and actor_user_id = %s", [case["id"], world.caseworker]).fetchall()
    assert rows == [(world.landlord_a,)]


def test_deadline_alert_filter(handler, app_conn, world, rule, calendar_ew):
    start = datetime.now(UTC) - timedelta(days=9)
    with scoped_transaction(app_conn, world.a) as conn:
        deadline = new_deadline(conn, world, rule, calendar_ew, new_case(conn, world), clock_started_at=start,
                                due_at=start + timedelta(days=10))
    items = handler.get("/deadlines", params={"alert_level": "alert_80", "limit": 100}).json()["items"]
    assert str(deadline) in [d["id"] for d in items]
    assert {d["alert_level"] for d in items} == {"alert_80"}


def test_responses_that_break_the_spec_are_caught(database, world):
    from caseward.api.framework import Result
    pool, api = make_client(database, "client", "cw_test_app", {"getMe": lambda call: Result(200, {"user": "x"})})
    try:
        with pytest.raises(AssertionError, match="does not match the spec"):
            As(api, world, "user_a", "client").get("/me")
    finally:
        pool.close()


def test_staff_see_and_acknowledge_deadline_alerts(handler, ops_api, app_conn, database, world, rule, calendar_ew):
    from caseward.worker.runner import Worker
    start = datetime.now(UTC) - timedelta(days=9)
    with scoped_transaction(app_conn, world.a) as conn:
        deadline = new_deadline(conn, world, rule, calendar_ew, new_case(conn, world), clock_started_at=start,
                                due_at=start + timedelta(days=10))
    pool = ConnectionPool(psycopg.conninfo.make_conninfo(database, user="cw_test_worker", password=PASSWORD),
                          kwargs={"autocommit": True}, min_size=1, max_size=1, open=True)
    try:
        Worker(pool).sweep()
    finally:
        pool.close()

    listed = handler.get("/deadline-alerts", params={"client_id": str(world.landlord_a), "limit": 100}).json()["items"]
    [alert] = [a for a in listed if a["deadline"]["id"] == str(deadline)]
    assert (alert["level"], alert["client_name"]) == ("alert_80", f"Landlord A {world.tag}")

    problem(As(ops_api, world, "analyst", "operations").post(f"/deadline-alerts/{alert['id']}/acknowledge"),
            403, "forbidden")
    acknowledged = handler.post(f"/deadline-alerts/{alert['id']}/acknowledge")
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["acknowledged_by"] == str(world.caseworker)
    problem(handler.post(f"/deadline-alerts/{alert['id']}/acknowledge"), 409, "already_acknowledged")
    still_open = handler.get("/deadline-alerts", params={"client_id": str(world.landlord_a), "limit": 100}).json()
    assert alert["id"] not in [a["id"] for a in still_open["items"]]
