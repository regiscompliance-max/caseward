"""The client dashboard and the case timeline. Row-level security scopes
both: a landlord sees its own, a provider sees the landlords it works for,
and staff see everything."""

from datetime import UTC, datetime
from caseward.api.framework import Call, Result
from caseward import timeline
from caseward.api.handlers.shared import decode_cursor, encode_cursor
from caseward.api.problems import not_found
from caseward.deadlines import UK

CLOSED_CASE = ("resolved", "closed", "withdrawn")


def get_dashboard(call: Call) -> Result:
    landlord = call.query.get("landlord_client_id")
    scope, params = ("and client_id = %s", [landlord]) if landlord else ("", [])

    alerts = {r["alert_level"]: r["count"] for r in call.rows(
        f"select alert_level, count(*) from caseward.deadline_progress where true {scope} group by alert_level",
        params)}
    recent = call.row(
        f"""select count(*) filter (where status = 'met' and completed_at >= now() - interval '30 days') as met,
                   count(*) filter (where status = 'missed' and due_at >= now() - interval '30 days') as missed
            from caseward.deadline where true {scope}""", params)
    by_status = {r["status"]: r["count"] for r in call.rows(
        f"select status, count(*) from caseward.hazard_case where true {scope} group by status", params)}
    emergency = call.row(
        f"select count(*) as n from caseward.hazard_case where severity = 'emergency'"
        f" and status <> all(%s) {scope}", [list(CLOSED_CASE)] + params)["n"]
    # Each property's latest certificate of each type.
    certificates = call.rows(
        f"""select certificate_type, next_due_on from (
              select distinct on (property_id, certificate_type) certificate_type, next_due_on
              from caseward.inspection where true {scope}
              order by property_id, certificate_type, inspected_on desc) latest""", params)
    today = datetime.now(UK).date()
    by_type: dict[str, int] = {}
    for c in certificates:
        by_type[c["certificate_type"]] = by_type.get(c["certificate_type"], 0) + 1

    return Result(200, {
        "as_of": datetime.now(UTC),
        "deadlines": {"on_track": alerts.get("on_track", 0), "alert_50": alerts.get("alert_50", 0),
                      "alert_80": alerts.get("alert_80", 0), "overdue": alerts.get("overdue", 0),
                      "met_last_30_days": recent["met"], "missed_last_30_days": recent["missed"]},
        "cases": {"open": sum(n for s, n in by_status.items() if s not in CLOSED_CASE), "emergency_open": emergency,
                  "by_status": by_status},
        "certificates": {
            "expired": sum(1 for c in certificates if c["next_due_on"] and c["next_due_on"] < today),
            "due_within_30_days": sum(1 for c in certificates
                                      if c["next_due_on"] and 0 <= (c["next_due_on"] - today).days <= 30),
            "by_type": by_type},
    })


def get_case_timeline(call: Call) -> Result:
    case = call.row("select id, client_id from caseward.hazard_case where id = %s", [call.path["id"]])
    if case is None:
        raise not_found()
    entries = timeline.build(call.rows, case["id"])
    cursor = decode_cursor(call)
    if cursor:
        after = (datetime.fromisoformat(cursor[0]), cursor[1], cursor[2])
        entries = [e for e in entries if (e["at"], e["entry_type"], str(e["_id"])) > after]
    limit = call.query.get("limit", 50)
    more = len(entries) > limit
    entries = entries[:limit]
    next_cursor = None
    if more:
        last = entries[-1]
        next_cursor = encode_cursor([last["at"].isoformat(), last["entry_type"], str(last["_id"])])
    for e in entries:
        e.pop("_id")
    return Result(200, {"items": entries, "next_cursor": next_cursor}, audit_client_id=case["client_id"])
