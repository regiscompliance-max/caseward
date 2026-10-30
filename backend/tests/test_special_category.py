import psycopg
import pytest

from caseward.db import RequestContext, scoped_transaction


@pytest.fixture
def vulnerability(staff_conn, world):
    """A recorded vulnerability factor on landlord A's tenancy, recorded by a caseworker."""
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        conn.execute("insert into caseward.vulnerability_factor_type (code, name, rule_pack)"
                     " values ('respiratory_condition', 'Respiratory condition', 'awaab_social')"
                     " on conflict do nothing")
        conn.execute("insert into caseward.household_vulnerability"
                     " (client_id, tenancy_id, factor, source, recorded_by)"
                     " values (%s, %s, 'respiratory_condition', 'tenant', %s)",
                     [world.landlord_a, world.tenancy_a, world.caseworker])


def read(conn, ctx, tenancy_id, reason="Assessing risk for case HC-1"):
    with scoped_transaction(conn, ctx) as c:
        return c.execute("select factor from caseward.read_household_vulnerabilities(%s, %s)",
                         [tenancy_id, reason]).fetchall()


def test_api_cannot_select_the_table_directly(app_conn, world, vulnerability):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with scoped_transaction(app_conn, world.a) as conn:
            conn.execute("select * from caseward.household_vulnerability")


def test_caseworker_can_read_and_the_read_is_audited(staff_conn, world, vulnerability):
    assert read(staff_conn, world.staff_ctx, world.tenancy_a) == [("respiratory_condition",)]
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        event = conn.execute(
            "select actor_user_id, after ->> 'reason' from caseward.audit_event"
            " where action = 'view' and entity_type = 'household_vulnerability' and entity_id = %s"
            " order by id desc limit 1", [str(world.tenancy_a)]).fetchone()
    assert event == (world.caseworker, "Assessing risk for case HC-1")


def test_reading_needs_a_reason(staff_conn, world, vulnerability):
    with pytest.raises(psycopg.errors.RaiseException, match="reason"):
        read(staff_conn, world.staff_ctx, world.tenancy_a, reason=" ")


def test_quality_analysts_cannot_read(staff_conn, world, vulnerability):
    ctx = RequestContext(actor_kind="user", user_id=world.reviewer)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        read(staff_conn, ctx, world.tenancy_a)


def test_client_users_need_explicit_access(app_conn, staff_conn, world, vulnerability):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        read(app_conn, world.a, world.tenancy_a)
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        conn.execute("update caseward.membership set special_category_access = true"
                     " where user_id = %s and client_id = %s", [world.user_a, world.landlord_a])
    assert read(app_conn, world.a, world.tenancy_a) == [("respiratory_condition",)]


def test_contractors_cannot_read(app_conn, world, vulnerability):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        read(app_conn, world.c, world.tenancy_a)


def test_vulnerability_values_are_redacted_from_audit(staff_conn, world, vulnerability):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        row = conn.execute(
            "select before, after, changed_fields @> array['factor'] from caseward.audit_event"
            " where entity_type = 'household_vulnerability' and action = 'insert' and client_id = %s",
            [world.landlord_a]).fetchone()
    assert row == (None, None, True)
