import uuid
from datetime import datetime, timedelta

import psycopg
import pytest

from caseward.db import record_access, scoped_transaction
from conftest import insert, new_case, new_deadline, new_rule


def test_pack_goes_live_only_with_compliance_lead(staff_conn, world):
    with pytest.raises(psycopg.errors.RaiseException, match="compliance lead"):
        with scoped_transaction(staff_conn, world.staff_ctx) as conn:
            conn.execute("update caseward.rule_pack set status = 'live', approved_by = %s, approved_at = now()"
                         " where code = 'awaab_private_rented'", [world.caseworker])


def test_deadline_needs_pack_enabled_for_client(app_conn, staff_conn, world, rule, calendar_ew):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        case_b = insert(conn, "hazard_case", client_id=world.landlord_b, property_id=world.property_b,
                        reference="HC-B", hazard_category="damp_and_mould_growth", severity="significant",
                        received_at=datetime.now().astimezone(), aware_at=datetime.now().astimezone(),
                        awareness_source="tenant_report")
    with pytest.raises(psycopg.errors.RaiseException, match="not enabled for this client"):
        with scoped_transaction(app_conn, world.b) as conn:
            new_deadline(conn, world, rule, calendar_ew, case_b, client_id=world.landlord_b)


def test_deadline_rejects_rule_from_pack_that_is_off(app_conn, staff_conn, world, calendar_ew):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        off_rule = new_rule(conn, world, rule_pack="awaab_private_rented")
    with pytest.raises(psycopg.errors.RaiseException, match="is not live"):
        with scoped_transaction(app_conn, world.a) as conn:
            new_deadline(conn, world, off_rule, calendar_ew, new_case(conn, world))


def test_defect_codes_come_from_configuration(app_conn, world):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with scoped_transaction(app_conn, world.a) as conn:
            # C1 is an EICR code, not a gas code.
            insert(conn, "defect", client_id=world.landlord_a, property_id=world.property_a,
                   code_scheme="gas_safety", code="C1", description="x", identified_on="2026-10-01")


def test_rule_trigger_and_completion_events_must_suit_the_subject(staff_conn, world):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with scoped_transaction(staff_conn, world.staff_ctx) as conn:
            new_rule(conn, world, completion_event="defect_remediated")


def test_completion_event_meets_the_deadline(app_conn, world, rule, calendar_ew):
    with scoped_transaction(app_conn, world.a) as conn:
        case_id = new_case(conn, world)
        deadline = new_deadline(conn, world, rule, calendar_ew, case_id)
        insert(conn, "case_event", client_id=world.landlord_a, case_id=case_id, kind="access_refused",
               occurred_at="2026-12-29T10:00:00+00:00", channel="in_person", recorded_by=world.user_a)
        assert conn.execute("select status from caseward.deadline where id = %s", [deadline]).fetchone() == ("open",)
        insert(conn, "case_event", client_id=world.landlord_a, case_id=case_id, kind="investigation_completed",
               occurred_at="2027-01-05T10:00:00+00:00", recorded_by=world.user_a)
        status, completed = conn.execute("select status, completed_at from caseward.deadline where id = %s",
                                         [deadline]).fetchone()
    assert status == "met"
    assert completed == datetime.fromisoformat("2027-01-05T10:00:00+00:00")


def test_case_events_are_append_only_evidence(app_conn, world):
    with scoped_transaction(app_conn, world.a) as conn:
        case_id = new_case(conn, world)
        event = insert(conn, "case_event", client_id=world.landlord_a, case_id=case_id, kind="access_attempt",
                       occurred_at=datetime.now().astimezone(), recorded_by=world.user_a)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with scoped_transaction(app_conn, world.a) as conn:
            conn.execute("update caseward.case_event set notes = 'changed' where id = %s", [event])
    with scoped_transaction(app_conn, world.a) as conn:
        insert(conn, "case_event", client_id=world.landlord_a, case_id=case_id, kind="access_attempt",
               occurred_at=datetime.now().astimezone(), corrects_event_id=event, notes="Wrong time recorded",
               recorded_by=world.user_a)


def test_contractor_can_log_access_attempts_on_engaged_cases(app_conn, world):
    with scoped_transaction(app_conn, world.a) as conn:
        case_id = new_case(conn, world)
    with scoped_transaction(app_conn, world.c) as conn:
        insert(conn, "case_event", client_id=world.landlord_a, case_id=case_id, kind="appointment_missed",
               occurred_at=datetime.now().astimezone(), contractor_client_id=world.contractor_c,
               recorded_by=world.user_c)


def test_deadline_progress_alert_levels(app_conn, world, rule, calendar_ew):
    now = datetime.now().astimezone()
    expected = {}
    with scoped_transaction(app_conn, world.a) as conn:
        for started_days_ago, level in [(1, "on_track"), (6, "alert_50"), (9, "alert_80")]:
            start = now - timedelta(days=started_days_ago)
            deadline = new_deadline(conn, world, rule, calendar_ew, new_case(conn, world), clock_started_at=start,
                                    due_at=start + timedelta(days=10))
            expected[deadline] = level
        levels = dict(conn.execute("select id, alert_level from caseward.deadline_progress where id = any(%s)",
                                   [list(expected)]).fetchall())
    assert levels == expected


def test_views_and_exports_are_audited(app_conn, world):
    report = str(uuid.uuid4())
    with scoped_transaction(app_conn, world.a) as conn:
        record_access(conn, world.a, "export", "deadline_report", report, {"format": "csv"})
        row = conn.execute("select actor_user_id, action, after from caseward.audit_event where entity_id = %s",
                           [report]).fetchone()
    assert row == (world.user_a, "export", {"format": "csv"})
