"""Integration test harness.

Creates a throwaway database, applies the migrations as the admin user, and
connects as non-superuser login roles (superusers bypass row-level security).
Set CASEWARD_TEST_ADMIN_DSN to point at a Postgres 16+ server.
"""

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from caseward.bank_holidays import load_calendar, publish_calendar
from caseward.db import RequestContext, scoped_transaction
from caseward.deadlines import CALCULATOR_VERSION, DeadlineRule, compute_due_at

ADMIN_DSN = os.environ.get("CASEWARD_TEST_ADMIN_DSN", "postgresql://postgres:postgres@localhost:5432/postgres")
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
PASSWORD = "caseward-test"
LOGIN_ROLES = {
    "cw_test_app": ["caseward_app"],
    "cw_test_staff": ["caseward_app", "caseward_staff"],
    "cw_test_worker": ["caseward_app", "caseward_worker"],
}


@pytest.fixture(scope="session")
def database():
    name = f"caseward_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    db_dsn = psycopg.conninfo.make_conninfo(ADMIN_DSN, dbname=name)
    try:
        with psycopg.connect(db_dsn, autocommit=True) as conn:
            for migration in sorted(MIGRATIONS.glob("*.sql")):
                conn.execute(migration.read_text())
            for role, groups in LOGIN_ROLES.items():
                exists = conn.execute("select 1 from pg_roles where rolname = %s", [role]).fetchone()
                if not exists:
                    conn.execute(sql.SQL("create role {} login password {}").format(
                        sql.Identifier(role), sql.Literal(PASSWORD)))
                for group in groups:
                    conn.execute(sql.SQL("grant {} to {}").format(sql.Identifier(group), sql.Identifier(role)))
        yield db_dsn
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def admin_dsn(database):
    return database


def _connect(dsn, role):
    return psycopg.connect(dsn, user=role, password=PASSWORD, autocommit=True)


@pytest.fixture(scope="session")
def app_conn(database):
    with _connect(database, "cw_test_app") as conn:
        yield conn


@pytest.fixture(scope="session")
def staff_conn(database):
    with _connect(database, "cw_test_staff") as conn:
        yield conn


@pytest.fixture(scope="session")
def worker_conn(database):
    with _connect(database, "cw_test_worker") as conn:
        yield conn


@dataclass
class World:
    landlord_a: uuid.UUID
    landlord_b: uuid.UUID
    contractor_c: uuid.UUID
    user_a: uuid.UUID
    user_a2: uuid.UUID
    user_b: uuid.UUID
    user_c: uuid.UUID
    caseworker: uuid.UUID
    reviewer: uuid.UUID
    compliance_lead: uuid.UUID
    property_a: uuid.UUID
    property_b: uuid.UUID
    tenancy_a: uuid.UUID
    contact_a: uuid.UUID
    staff_ctx: RequestContext

    def ctx(self, client_id, user_id):
        return RequestContext(actor_kind="user", client_id=client_id, user_id=user_id, request_id="req-test")

    @property
    def a(self):
        return self.ctx(self.landlord_a, self.user_a)

    @property
    def b(self):
        return self.ctx(self.landlord_b, self.user_b)

    @property
    def c(self):
        return self.ctx(self.contractor_c, self.user_c)


def insert(conn, table, **values):
    query = sql.SQL("insert into caseward.{} ({}) values ({}) returning id").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, values)),
        sql.SQL(", ").join(sql.Placeholder() * len(values)),
    )
    return conn.execute(query, list(values.values())).fetchone()[0]


@pytest.fixture
def world(staff_conn):
    """Two landlords, a contractor engaged by landlord A, users and properties."""
    tag = uuid.uuid4().hex[:8]

    def user(name, staff_role=None):
        return insert(staff_conn, "app_user", idp_subject=f"{name}-{tag}", email=f"{name}-{tag}@example.test",
                      display_name=name, staff_role=staff_role)

    seed_ctx = RequestContext(actor_kind="system", request_id="seed")
    with scoped_transaction(staff_conn, seed_ctx) as conn:
        a = insert(conn, "client", name=f"Landlord A {tag}", kind="landlord")
        b = insert(conn, "client", name=f"Landlord B {tag}", kind="landlord")
        c = insert(conn, "client", name=f"Contractor C {tag}", kind="contractor")
        insert(conn, "engagement", landlord_client_id=a, provider_client_id=c)
        ids = {n: user(n) for n in ("user_a", "user_a2", "user_b", "user_c")}
        caseworker = user("case_handler", "case_handler")
        reviewer = user("analyst", "quality_analyst")
        lead = user("lead", "compliance_lead")
        for u, cl, role in [(ids["user_a"], a, "client_admin"), (ids["user_a2"], a, "client_member"),
                            (ids["user_b"], b, "client_admin"), (ids["user_c"], c, "client_member")]:
            conn.execute("insert into caseward.membership (user_id, client_id, role) values (%s, %s, %s)", [u, cl, role])
        prop_a = insert(conn, "property", client_id=a, uprn=100000000001, address_line_1="1 High St",
                        postcode="SW1A 1AA", jurisdiction="england")
        prop_b = insert(conn, "property", client_id=b, uprn=100000000002, address_line_1="2 Low Rd",
                        postcode="M1 1AA", jurisdiction="england")
        tenancy = insert(conn, "tenancy", client_id=a, property_id=prop_a, starts_on="2025-01-01")
        contact = insert(conn, "tenant_contact", client_id=a, tenancy_id=tenancy, full_name="Pat Tenant",
                         email="pat@example.test")
        conn.execute("update caseward.rule_pack set status = 'live', approved_by = %s, approved_at = now()"
                     " where code = 'awaab_social'", [lead])
        conn.execute("insert into caseward.client_rule_pack (client_id, rule_pack, enabled_from)"
                     " values (%s, 'awaab_social', '2025-01-01')", [a])

    return World(
        landlord_a=a, landlord_b=b, contractor_c=c, caseworker=caseworker, reviewer=reviewer,
        compliance_lead=lead, property_a=prop_a, property_b=prop_b, tenancy_a=tenancy, contact_a=contact,
        staff_ctx=RequestContext(actor_kind="user", user_id=caseworker, request_id="req-staff"),
        **ids,
    )


# Deadline helpers ----------------------------------------------------------

UK_CLOCK_START = datetime.fromisoformat("2026-12-23T15:00:00+00:00")


def new_rule(conn, world, *, approved=True, **overrides):
    values = dict(
        rule_pack="awaab_social", rule_key=f"test.case.{uuid.uuid4().hex[:8]}", version=1, title="Test rule",
        description="For tests", jurisdiction="england", subject_kind="case", applies_to="significant",
        trigger_event="case_aware", completion_event="investigation_completed",
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
