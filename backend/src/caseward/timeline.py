"""A case's history in time order: events, status changes, deadlines, sent
letters and evidence documents. Used by the timeline endpoint and the
evidence pack. Row-level security decides what the caller sees (providers
see no letters)."""

from collections.abc import Callable
from typing import Any

from caseward.letters import uk_date
from caseward.queries import CASE_EVENT_SELECT, DEADLINE_SELECT, deadline_out

Rows = Callable[[str, Any], list[dict]]


def build(rows: Rows, case_id) -> list[dict]:
    """Entries sorted oldest first. Each has a private _id for stable ordering."""
    case = rows("select id, awareness_evidence_document_id from caseward.hazard_case where id = %s", [case_id])
    if not case:
        return []
    entries = []

    events = rows(CASE_EVENT_SELECT + " where e.case_id = %s", [case_id])
    for e in events:
        summary = e["kind_name"] + (" (correction)" if e["corrects_event_id"] else "")
        entries.append({"entry_type": "event", "at": e["occurred_at"], "summary": summary, "event": e, "_id": e["id"]})

    for s in rows("select id, from_status, to_status, changed_by, changed_at from caseward.case_status_change"
                  " where case_id = %s", [case_id]):
        summary = f"Status: {s['to_status']}" if s["from_status"] is None else \
            f"Status: {s['from_status']} to {s['to_status']}"
        entries.append({"entry_type": "status_change", "at": s["changed_at"], "summary": summary, "_id": s["id"],
                        "status_change": {k: s[k] for k in ("from_status", "to_status", "changed_by")}})

    for d in rows(DEADLINE_SELECT + " where d.case_id = %s", [case_id]):
        entries.append({"entry_type": "deadline", "at": d["clock_started_at"], "_id": d["id"],
                        "summary": f"{d['rule_title']}: due {uk_date(d['due_local_date'])}", "deadline": deadline_out(d)})

    for letter in rows("select id, channel, sent_at from caseward.letter where case_id = %s and sent_at is not null",
                       [case_id]):
        entries.append({"entry_type": "letter", "at": letter["sent_at"], "summary": f"Letter sent ({letter['channel']})",
                        "letter_id": letter["id"], "_id": letter["id"]})

    document_ids = {e["evidence_document_id"] for e in events if e["evidence_document_id"]}
    if case[0]["awareness_evidence_document_id"]:
        document_ids.add(case[0]["awareness_evidence_document_id"])
    for doc in rows("select id, doc_type, received_at from caseward.document where id = any(%s)", [list(document_ids)]):
        entries.append({"entry_type": "document", "at": doc["received_at"], "document_id": doc["id"], "_id": doc["id"],
                        "summary": f"Document received ({doc['doc_type'].replace('_', ' ')})"})

    entries.sort(key=lambda e: (e["at"], e["entry_type"], str(e["_id"])))
    return entries
