"""Onboarding through the API: clients, engagements, invited users,
memberships, tenants and special-category data."""

import time
import uuid
from datetime import datetime, timedelta

import jwt
import pytest

from caseward.api.handlers import client as client_handlers
from caseward.api.handlers import operations as ops_handlers
from caseward.db import scoped_transaction
from caseward.deadlines import UK
from conftest import insert
from test_api import ISSUER, KEY, As, make_client, problem

TODAY = datetime.now(UK).date()


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
def admin(ops_api, staff_conn, world):
    name = f"admin{uuid.uuid4().hex[:4]}"
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        user = insert(conn, "app_user", idp_subject=world.subject(name), email=f"{name}-{world.tag}@example.test",
                      display_name=name, staff_role="admin")
    who = As(ops_api, world, name, "operations")
    who.user_id = user
    return who


def first_sign_in(client_api, email, subject=None, verified=True):
    now = int(time.time())
    claims = {"sub": subject or f"idp|{uuid.uuid4()}", "iss": ISSUER, "aud": "client", "iat": now, "exp": now + 300,
              "amr": ["pwd", "mfa"], "email": email, "email_verified": verified}
    token = jwt.encode(claims, KEY, algorithm="RS256")
    return client_api.get("/me", headers={"Authorization": f"Bearer {token}"})


def test_onboarding_a_landlord_end_to_end(admin, client_api, world):
    landlord = admin.post("/clients", json={"name": f"Riverside Homes {world.tag}", "kind": "landlord"})
    assert landlord.status_code == 201, landlord.text
    landlord = landlord.json()
    email = f"housing.officer.{uuid.uuid4().hex[:6]}@riverside.example"
    invited = admin.post("/users", json={"email": email, "display_name": "Housing Officer"}).json()
    member = admin.api.put(f"/clients/{landlord['id']}/members/{invited['id']}",
                           json={"role": "client_admin"}, headers=admin.headers)
    assert member.status_code == 200, member.text
    assert member.json()["client_name"] == f"Riverside Homes {world.tag}"

    me = first_sign_in(client_api, email)
    assert me.status_code == 200, me.text
    assert me.json()["acting_client_id"] == landlord["id"]
    assert me.json()["user"]["id"] == invited["id"]


def test_invitations_need_a_verified_email(admin, client_api, world):
    email = f"unverified.{uuid.uuid4().hex[:6]}@example.test"
    admin.post("/users", json={"email": email, "display_name": "Someone"})
    problem(first_sign_in(client_api, email, verified=False), 401, "unknown_user")


def test_an_invitation_is_claimed_once(admin, client_api, world):
    email = f"once.{uuid.uuid4().hex[:6]}@example.test"
    invited = admin.post("/users", json={"email": email, "display_name": "Once"}).json()
    admin.api.put(f"/clients/{world.landlord_a}/members/{invited['id']}", json={"role": "client_viewer"},
                  headers=admin.headers)
    assert first_sign_in(client_api, email, subject="idp|first").status_code == 200
    problem(first_sign_in(client_api, email, subject="idp|someone-else"), 401, "unknown_user")
    assert first_sign_in(client_api, email, subject="idp|first").status_code == 200


def test_only_admins_manage_users_and_clients(ops_api, world):
    lead = As(ops_api, world, "lead", "operations")
    problem(lead.post("/clients", json={"name": "x", "kind": "landlord"}), 403, "forbidden")
    problem(lead.post("/users", json={"email": "x@example.test", "display_name": "x"}), 403, "forbidden")
    assert lead.get("/clients", params={"kind": "contractor"}).status_code == 200


def test_duplicate_users_are_refused(admin, world):
    problem(admin.post("/users", json={"email": f"USER_A-{world.tag}@example.test", "display_name": "dup"}), 409,
            "conflict")


def test_users_change_with_the_current_version(admin, world):
    users = admin.get("/users", params={"client_id": str(world.landlord_a), "limit": 100}).json()["items"]
    target = next(u for u in users if u["id"] == str(world.user_a2))
    url = f"/users/{target['id']}"
    changed = admin.api.patch(url, json={"display_name": "Renamed"},
                              headers={**admin.headers, "If-Match": target["row_version"]})
    assert changed.status_code == 200 and changed.json()["display_name"] == "Renamed"
    problem(admin.api.patch(url, json={"is_active": False}, headers={**admin.headers, "If-Match": target["row_version"]}),
            412, "stale")


def find_user(admin, user_id):
    body = admin.get("/users", params={"limit": 100}).json()
    while True:
        found = [u for u in body["items"] if u["id"] == str(user_id)]
        if found or not body["next_cursor"]:
            return found[0]
        body = admin.get("/users", params={"limit": 100, "cursor": body["next_cursor"]}).json()


def test_admins_cannot_lock_themselves_out(admin):
    me = find_user(admin, admin.user_id)
    problem(admin.api.patch(f"/users/{admin.user_id}", json={"is_active": False},
                            headers={**admin.headers, "If-Match": me["row_version"]}), 422, "self_lockout")


def test_staff_are_not_client_members(admin, world):
    problem(admin.api.put(f"/clients/{world.landlord_a}/members/{world.caseworker}", json={"role": "client_admin"},
                          headers=admin.headers), 422, "staff_not_members")


def test_engagements_give_and_remove_contractor_access(admin, client_api, world):
    contractor = admin.post("/clients", json={"name": f"Sparks Ltd {world.tag}", "kind": "contractor"}).json()
    email = f"sparks.{uuid.uuid4().hex[:6]}@example.test"
    user = admin.post("/users", json={"email": email, "display_name": "Sparky"}).json()
    admin.api.put(f"/clients/{contractor['id']}/members/{user['id']}", json={"role": "client_member"},
                  headers=admin.headers)
    subject = f"idp|{uuid.uuid4()}"
    first_sign_in(client_api, email, subject=subject)
    sparky = As(client_api, world, "x", "client")
    sparky.headers = {"Authorization": f"Bearer {jwt.encode({'sub': subject, 'iss': ISSUER, 'aud': 'client', 'iat': int(time.time()), 'exp': int(time.time()) + 300, 'amr': ['mfa']}, KEY, algorithm='RS256')}"}
    assert sparky.get("/properties").json()["items"] == []

    url = f"/clients/{world.landlord_a}/engagements"
    engaged = admin.post(url, json={"provider_client_id": contractor["id"], "starts_on": TODAY.isoformat()})
    assert engaged.status_code == 201, engaged.text
    problem(admin.post(url, json={"provider_client_id": contractor["id"], "starts_on": TODAY.isoformat()}), 409,
            "conflict")
    problem(admin.post(url, json={"provider_client_id": str(world.landlord_b), "starts_on": TODAY.isoformat()}), 422,
            "not_a_provider")
    assert str(world.property_a) in [p["id"] for p in sparky.get("/properties").json()["items"]]

    problem(admin.post(f"/engagements/{engaged.json()['id']}/end",
                       json={"ends_on": (TODAY - timedelta(days=1)).isoformat()}), 422, "rule_violation")
    ended = admin.post(f"/engagements/{engaged.json()['id']}/end", json={"ends_on": TODAY.isoformat()})
    assert ended.json()["ends_on"] == TODAY.isoformat()
    assert len(admin.get(url).json()["items"]) == 2


def test_removing_a_membership_removes_access(admin, client_api, world):
    email = f"leaver.{uuid.uuid4().hex[:6]}@example.test"
    user = admin.post("/users", json={"email": email, "display_name": "Leaver"}).json()
    admin.api.put(f"/clients/{world.landlord_a}/members/{user['id']}", json={"role": "client_member"},
                  headers=admin.headers)
    subject = f"idp|{uuid.uuid4()}"
    assert first_sign_in(client_api, email, subject=subject).status_code == 200
    deleted = admin.api.delete(f"/clients/{world.landlord_a}/members/{user['id']}", headers=admin.headers)
    assert deleted.status_code == 204
    problem(first_sign_in(client_api, email, subject=subject), 403, "forbidden")


# Tenants and special-category data --------------------------------------------------------

def test_landlords_keep_tenant_contacts(client_api, world):
    landlord = As(client_api, world, "user_a", "client")
    tenancy = landlord.post(f"/properties/{world.property_a}/tenancies", json={"starts_on": "2026-04-01"})
    assert tenancy.status_code == 201, tenancy.text
    contact = landlord.post(f"/tenancies/{tenancy.json()['id']}/contacts",
                            json={"full_name": "Sam Resident", "phone": "07700900456", "preferred_channel": "phone"})
    assert contact.status_code == 201, contact.text
    url = f"/tenant-contacts/{contact.json()['id']}"
    updated = landlord.api.patch(url, json={"email": "sam@example.test"},
                                 headers={**landlord.headers, "If-Match": contact.json()["row_version"],
                                          "Content-Type": "application/merge-patch+json"})
    assert updated.status_code == 200, updated.text
    listed = landlord.get(f"/properties/{world.property_a}/tenancies").json()["items"]
    assert "sam@example.test" in [c["email"] for t in listed for c in t["contacts"]]
    contractor = As(client_api, world, "user_c", "client")
    problem(contractor.get(f"/properties/{world.property_a}/tenancies"), 404, "not_found")


def test_vulnerability_needs_access_and_a_reason(client_api, ops_api, staff_conn, admin, world):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        conn.execute("insert into caseward.vulnerability_factor_type (code, name, rule_pack)"
                     " values ('young_child', 'Child under 5 in the home', 'awaab_social') on conflict do nothing")
    handler = As(ops_api, world, "case_handler", "operations")
    url = f"/tenancies/{world.tenancy_a}/vulnerabilities"
    recorded = handler.post(url, json={"factor": "young_child", "source": "tenant"})
    assert recorded.status_code == 201 and recorded.content == b""
    problem(handler.get(url), 400, "invalid_parameter")
    seen = handler.get(url, headers={"X-Access-Reason": "Prioritising damp case HC-7"})
    assert seen.status_code == 200 and seen.headers["cache-control"] == "no-store"
    assert [v["factor"] for v in seen.json()["items"]] == ["young_child"]

    landlord = As(client_api, world, "user_a", "client")
    problem(landlord.get(url, headers={"X-Access-Reason": "Checking records"}), 403, "forbidden")
    problem(As(ops_api, world, "analyst", "operations").get(url, headers={"X-Access-Reason": "Sampling"}), 403,
            "forbidden")
    problem(As(client_api, world, "user_c", "client").get(url, headers={"X-Access-Reason": "Visit"}), 404, "not_found")

    vulnerability_id = seen.json()["items"][0]["id"]
    problem(landlord.post(f"/vulnerabilities/{vulnerability_id}/end"), 404, "not_found")

    granted = admin.api.put(f"/clients/{world.landlord_a}/members/{world.user_a}",
                            json={"role": "client_admin", "special_category_access": True}, headers=admin.headers)
    assert granted.json()["special_category_access"] is True
    assert landlord.get(url, headers={"X-Access-Reason": "Arranging a visit"}).status_code == 200
    assert landlord.post(f"/vulnerabilities/{vulnerability_id}/end").status_code == 204
    current = landlord.get(url, headers={"X-Access-Reason": "Checking it ended"}).json()["items"]
    assert current[0]["ended_at"] is not None
