"""The compute_deadlines job: create the deadlines an event starts.

Queued by database trigger for every case event, new defect and new
inspection (migration 0003). Safe to run more than once for the same event.
"""

from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from caseward.bank_holidays import REGION_FOR_JURISDICTION, latest_calendar, load_calendar
from caseward.deadlines import CALCULATOR_VERSION, UK, DeadlineRule, compute_due_at

# For each subject kind: how to find its client, what rules match on
# (applies_to), its property's jurisdiction, and when the clock starts when
# the trigger is not a case event. Date-only facts start at midnight UK time.
SUBJECTS = {
    "case": """
        select c.client_id, c.severity as applies_to, p.jurisdiction, c.aware_at as clock
        from caseward.hazard_case c join caseward.property p on p.id = c.property_id where c.id = %s""",
    "defect": """
        select d.client_id, d.code_scheme || ':' || d.code as applies_to, p.jurisdiction,
               (d.identified_on::timestamp at time zone 'Europe/London') as clock
        from caseward.defect d join caseward.property p on p.id = d.property_id where d.id = %s""",
    "inspection": """
        select i.client_id, i.certificate_type as applies_to, p.jurisdiction,
               (i.inspected_on::timestamp at time zone 'Europe/London') as clock
        from caseward.inspection i join caseward.property p on p.id = i.property_id where i.id = %s""",
}

MATCHING_RULES = """
    select r.* from caseward.deadline_rule r
    join caseward.rule_pack rp on rp.code = r.rule_pack and rp.status = 'live'
    join caseward.client_rule_pack cp on cp.rule_pack = r.rule_pack and cp.client_id = %(client_id)s
         and cp.enabled_from <= %(day)s and (cp.enabled_to is null or cp.enabled_to > %(day)s)
    where r.status = 'approved'
      and r.subject_kind = %(kind)s
      and r.trigger_event = %(event)s
      and r.jurisdiction = %(jurisdiction)s
      and r.applies_to in (%(applies_to)s, '*')
      and r.effective_from <= %(day)s and (r.effective_to is null or r.effective_to > %(day)s)
    order by r.rule_key
"""


class CalendarMissing(Exception):
    """No holiday calendar covers the due date. Retried, then dead-lettered for staff."""


def compute_deadlines(conn: psycopg.Connection, job: dict[str, Any]) -> list:
    payload = job["payload"]
    kind = payload["subject"]["kind"]
    subject_id = payload["subject"]["id"]
    event = payload["event"]
    cur = conn.cursor(row_factory=dict_row)

    subject = cur.execute(SUBJECTS[kind], [subject_id]).fetchone()
    if subject is None:
        raise LookupError(f"{kind} {subject_id} not found")
    clock: datetime = subject["clock"]
    if payload.get("event_id"):
        clock = cur.execute("select occurred_at from caseward.case_event where id = %s",
                            [payload["event_id"]]).fetchone()["occurred_at"]
    day = clock.astimezone(UK).date()

    rules = cur.execute(MATCHING_RULES, {"client_id": subject["client_id"], "day": day, "kind": kind,
                                         "event": event, "jurisdiction": subject["jurisdiction"],
                                         "applies_to": subject["applies_to"]}).fetchall()
    created = []
    for rule in rules:
        exists = cur.execute(
            f"select 1 from caseward.deadline where {kind}_id = %s and rule_key = %s and clock_started_at = %s"
            " and status <> 'cancelled'", [subject_id, rule["rule_key"], clock]).fetchone()
        if exists:
            continue

        calendar_id, holidays = None, frozenset()
        if rule["day_type"] == "working":
            region = REGION_FOR_JURISDICTION[subject["jurisdiction"]]
            calendar = latest_calendar(conn, region)
            if calendar is None:
                raise CalendarMissing(f"no {region} bank holiday calendar")
            calendar_id, covered_to = calendar
            holidays = load_calendar(conn, calendar_id)

        due = compute_due_at(DeadlineRule(rule["rule_key"], rule["version"], rule["duration"],
                                          rule["duration_unit"], rule["day_type"]), clock, holidays)
        if calendar_id and due.astimezone(UK).date() > covered_to:
            raise CalendarMissing(f"{region} calendar ends {covered_to}, deadline falls on {due.date()}")

        deadline_id = cur.execute(
            f"""insert into caseward.deadline (client_id, {kind}_id, rule_key, rule_version, clock_started_at,
                  holiday_calendar_id, due_at, calculator_version)
                values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            [subject["client_id"], subject_id, rule["rule_key"], rule["version"], clock, calendar_id, due,
             CALCULATOR_VERSION]).fetchone()["id"]

        # The completion event may already have been recorded (for example the
        # investigation was logged before this job ran).
        if kind == "case":
            done = cur.execute(
                "select min(occurred_at) as at from caseward.case_event where case_id = %s and kind = %s"
                " and corrects_event_id is null and occurred_at >= %s",
                [subject_id, rule["completion_event"], clock]).fetchone()["at"]
            if done:
                cur.execute("update caseward.deadline set completed_at = %s where id = %s", [done, deadline_id])
        created.append(deadline_id)
    return created
