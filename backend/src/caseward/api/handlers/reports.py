"""The client dashboard and the case timeline. Row-level security scopes
both: a landlord sees its own, a provider sees the landlords it works for,
and staff see everything."""

from datetime import UTC, datetime
from caseward.api.framework import Call, Result
from caseward.api.handlers.shared import (CASE_EVENT_SELECT, DEADLINE_SELECT, deadline_out, decode_cursor,
                                          encode_cursor)
from caseward.api.problems import not_found
from caseward.deadlines import UK
from caseward.letters import uk_date

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
    case = call.row("select id, client_id, awareness_evidence_document_id from caseward.hazard_case where id = %s",
                    [call.path["id"]])
    if case is None:
        raise not_found()
    entries = []

    events = call.rows(CASE_EVENT_SELECT + " where e.case_id = %s", [case["id"]])
    for e in events:
        summary = e["kind_name"] + (" (correction)" if e["corrects_event_id"] else "")
        entries.append({"entry_type": "event", "at": e["occurred_at"], "summary": summary, "event": e, "_id": e["id"]})

    for s in call.rows("select id, from_status, to_status, changed_by, changed_at from caseward.case_status_change"
                       " where case_id = %s", [case["id"]]):
        summary = f"Status: {s['to_status']}" if s["from_status"] is None else \
            f"Status: {s['from_status']} to {s['to_status']}"
        entries.append({"entry_type": "status_change", "at": s["changed_at"], "summary": summary, "_id": s["id"],
                        "status_change": {k: s[k] for k in ("from_status", "to_status", "changed_by")}})

    for d in call.rows(DEADLINE_SELECT + " where d.case_id = %s", [case["id"]]):
        entries.append({"entry_type": "deadline", "at": d["clock_started_at"], "_id": d["id"],
                        "summary": f"{d['rule_title']}: due {uk_date(d['due_local_date'])}", "deadline": deadline_out(d)})

    # Letters are hidden from providers by row-level security.
    for l in call.rows("select id, channel, sent_at from caseward.letter where case_id = %s and sent_at is not null",
                       [case["id"]]):
        entries.append({"entry_type": "letter", "at": l["sent_at"], "summary": f"Letter sent ({l['channel']})",
                        "letter_id": l["id"], "_id": l["id"]})

    document_ids = {e["evidence_document_id"] for e in events if e["evidence_document_id"]}
    if case["awareness_evidence_document_id"]:
        document_ids.add(case["awareness_evidence_document_id"])
    for doc in call.rows("select id, doc_type, received_at from caseward.document where id = any(%s)",
                         [list(document_ids)]):
        entries.append({"entry_type": "document", "at": doc["received_at"], "document_id": doc["id"], "_id": doc["id"],
                        "summary": f"Document received ({doc['doc_type'].replace('_', ' ')})"})

    entries.sort(key=lambda e: (e["at"], e["entry_type"], str(e["_id"])))
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
