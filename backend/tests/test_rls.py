import uuid

import psycopg
import pytest

from caseward.db import RequestContext, scoped_transaction
from conftest import insert


def ids(conn, ctx, query, params=()):
    with scoped_transaction(conn, ctx) as c:
        return {row[0] for row in c.execute(query, params).fetchall()}


def test_landlord_sees_only_its_own_properties(app_conn, world):
    assert ids(app_conn, world.a, "select client_id from caseward.property") == {world.landlord_a}
    assert ids(app_conn, world.b, "select client_id from caseward.property") == {world.landlord_b}


def test_no_client_context_sees_nothing(app_conn, world):
    ctx = RequestContext(actor_kind="user", user_id=world.user_a)
    assert ids(app_conn, ctx, "select id from caseward.property") == set()
    assert ids(app_conn, ctx, "select id from caseward.client") == set()


def test_contractor_sees_engaged_landlord_only(app_conn, world):
    seen = ids(app_conn, world.c, "select id from caseward.property")
    assert world.property_a in seen
    assert world.property_b not in seen


def test_contractor_cannot_see_tenant_data(app_conn, world):
    assert ids(app_conn, world.a, "select id from caseward.tenant_contact") == {world.contact_a}
    assert ids(app_conn, world.c, "select id from caseward.tenant_contact") == set()
    assert ids(app_conn, world.c, "select id from caseward.tenancy") == set()


def test_ended_engagement_revokes_contractor_access(app_conn, staff_conn, world):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        conn.execute(
            "update caseward.engagement set starts_on = current_date - 10, ends_on = current_date - 1"
            " where provider_client_id = %s",
            [world.contractor_c],
        )
    assert world.property_a not in ids(app_conn, world.c, "select id from caseward.property")


def test_cannot_insert_rows_for_another_client(app_conn, world):
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        with scoped_transaction(app_conn, world.a) as conn:
            insert(conn, "property", client_id=world.landlord_b, address_line_1="x", postcode="x",
                   jurisdiction="england")


def test_cannot_move_a_row_to_another_client(app_conn, world):
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        with scoped_transaction(app_conn, world.a) as conn:
            conn.execute("update caseward.property set client_id = %s where id = %s",
                         [world.landlord_b, world.property_a])


def test_cross_client_links_are_rejected_even_for_staff(staff_conn, world):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with scoped_transaction(staff_conn, world.staff_ctx) as conn:
            insert(conn, "defect", client_id=world.landlord_a, property_id=world.property_b, code_scheme="eicr", code="C2",
                   description="Damaged socket", identified_on="2026-10-01")


def test_client_connection_cannot_become_staff(app_conn, world):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with scoped_transaction(app_conn, world.a) as conn:
            conn.execute("set local role caseward_staff")


def test_staff_see_every_client(staff_conn, world):
    seen = ids(staff_conn, world.staff_ctx, "select id from caseward.property")
    assert {world.property_a, world.property_b} <= seen


def test_users_see_only_colleagues(app_conn, world):
    seen = ids(app_conn, world.a, "select id from caseward.app_user")
    assert seen == {world.user_a, world.user_a2}


def test_worker_sees_all_jobs_and_clients_see_their_own(app_conn, worker_conn, world):
    keys = {}
    for ctx in (world.a, world.b):
        keys[ctx.client_id] = f"extract-{uuid.uuid4()}"
        with scoped_transaction(app_conn, ctx) as conn:
            conn.execute(
                "insert into caseward.job (client_id, kind, idempotency_key) values (%s, 'extract', %s)",
                [ctx.client_id, keys[ctx.client_id]],
            )
    query = "select client_id from caseward.job where idempotency_key = any(%s)"
    assert ids(app_conn, world.a, query, [list(keys.values())]) == {world.landlord_a}
    worker_ctx = RequestContext(actor_kind="worker")
    assert ids(worker_conn, worker_ctx, query, [list(keys.values())]) == {world.landlord_a, world.landlord_b}


def test_landlords_see_the_providers_they_engage_and_no_others(app_conn, world):
    assert ids(app_conn, world.a, "select id from caseward.client") == {world.landlord_a, world.contractor_c}
    assert ids(app_conn, world.b, "select id from caseward.client") == {world.landlord_b}
    assert ids(app_conn, world.c, "select id from caseward.client") == {world.contractor_c, world.landlord_a}
