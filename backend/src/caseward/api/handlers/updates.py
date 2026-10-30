"""Changing cases, defects and deadlines after the fact.

Deadlines are never edited. When the facts behind one change (severity, or a
corrected event time), it is cancelled with a reason and recalculated from
the case's events, using each event's latest correction.
"""

from uuid import uuid4

from psycopg.types.json import Jsonb

from caseward.api.framework import Call, Result
from caseward.api.handlers.shared import CASE_SELECT, cases_out
from caseward.api.problems import Problem, forbidden, not_found

CLOSED = ("resolved", "closed", "withdrawn")
LANDLORD_ONLY_DEFECT_STATUSES = ("verified", "closed", "not_required")


def _if_match(call: Call, current: str) -> None:
    if call.headers["If-Match"].strip().strip('"') != current:
        raise Problem(412, "stale", "This changed since you loaded it; reload and try again")


def queue_recalculation(call: Call, subject_kind: str, subject_id, client_id) -> int:
    """Queue deadline calculation for every trigger the subject has had."""
    if subject_kind == "case":
        triggers = call.rows(
            """select e.kind, coalesce(
                 (select c.id from caseward.case_event c where c.corrects_event_id = e.id
                  order by c.recorded_at desc limit 1), e.id) as event_id
               from caseward.case_event e where e.case_id = %s and e.corrects_event_id is null""", [subject_id])
    else:
        triggers = [{"kind": "defect_identified" if subject_kind == "defect" else "inspection_completed",
                     "event_id": None}]
    for t in triggers:
        payload = {"subject": {"kind": subject_kind, "id": str(subject_id)}, "event": t["kind"]}
        if t["event_id"]:
            payload["event_id"] = str(t["event_id"])
        call.conn.execute(
            "insert into caseward.job (client_id, kind, payload, idempotency_key) values (%s, 'compute_deadlines', %s, %s)",
            [client_id, Jsonb(payload), f"recompute:{subject_kind}:{subject_id}:{uuid4()}"])
    return len(triggers)


def _cancel(call: Call, where: str, params: list, reason: str) -> list:
    return [r["id"] for r in call.rows(
        f"update caseward.deadline set status = 'cancelled', cancelled_by = %s, cancelled_at = now(),"
        f" cancel_reason = %s where status in ('open', 'missed') and completed_at is null and {where} returning id",
        [call.principal.user_id, reason, *params])]


def update_case(call: Call) -> Result:
    b = call.body
    case = call.row("select id, client_id, severity, status, xmin::text as v from caseward.hazard_case"
                    " where id = %s for update", [call.path["id"]])
    if case is None:
        raise not_found()
    _if_match(call, case["v"])
    sets, params = [], []

    if "status" in b and b["status"] != case["status"]:
        sets += ["status = %s", "closed_at = %s"]
        params += [b["status"], None]
        if b["status"] in CLOSED:
            sets[-1] = "closed_at = now()"
            params.pop()
    if "assigned_contractor_id" in b:
        contractor = b["assigned_contractor_id"]
        if contractor and call.row(
                "select 1 from caseward.engagement where landlord_client_id = %s and provider_client_id = %s"
                " and starts_on <= current_date and (ends_on is null or ends_on >= current_date)",
                [case["client_id"], contractor]) is None:
            raise Problem(422, "recipient_not_engaged", "That contractor is not currently engaged by this landlord")
        sets.append("assigned_contractor_id = %s")
        params.append(contractor)
    severity_changed = "severity" in b and b["severity"] != case["severity"]
    if severity_changed:
        if not b.get("reason"):
            raise Problem(422, "reason_required", "Say why the severity changed")
        sets.append("severity = %s")
        params.append(b["severity"])
    if sets:
        call.conn.execute(f"update caseward.hazard_case set {', '.join(sets)} where id = %s", [*params, case["id"]])

    if severity_changed:
        reason = f"Severity changed from {case['severity']} to {b['severity']}: {b['reason']}"
        _cancel(call, """case_id = %s and (rule_key, rule_version) in (
                           select rule_key, version from caseward.deadline_rule
                           where subject_kind = 'case' and applies_to = %s)""", [case["id"], case["severity"]], reason)
        call.conn.execute(
            "insert into caseward.case_event (client_id, case_id, kind, occurred_at, notes, recorded_by)"
            " values (%s, %s, 'note', now(), %s, %s)", [case["client_id"], case["id"], reason, call.principal.user_id])
        queue_recalculation(call, "case", case["id"], case["client_id"])

    row = call.row(CASE_SELECT.format(sort="null") + " where c.id = %s", [case["id"]])
    return Result(200, cases_out(call, [row])[0], headers={"ETag": f'"{row["etag"]}"'},
                  audit_client_id=case["client_id"])


def update_defect(call: Call) -> Result:
    defect = call.row("select id, client_id, status, xmin::text as v from caseward.defect where id = %s for update",
                      [call.path["id"]])
    if defect is None:
        raise not_found()
    _if_match(call, defect["v"])
    status = call.body["status"]
    if call.is_provider and status in LANDLORD_ONLY_DEFECT_STATUSES:
        raise forbidden("Only the landlord can verify or close a defect.")
    closing = status in ("closed", "not_required")
    call.conn.execute(
        "update caseward.defect set status = %s, status_note = %s,"
        " closed_at = case when %s then coalesce(closed_at, now()) else null end where id = %s",
        [status, call.body.get("note"), closing, defect["id"]])
    if status in ("remediated", "verified", "closed") and defect["status"] not in ("remediated", "verified", "closed"):
        # Defect deadlines end on remediation; there is no defect event table, so complete them here.
        call.conn.execute(
            """update caseward.deadline d set completed_at = now()
               from caseward.deadline_rule r
               where d.defect_id = %s and d.completed_at is null and d.status in ('open', 'missed')
                 and (r.rule_key, r.version) = (d.rule_key, d.rule_version) and r.completion_event = 'defect_remediated'""",
            [defect["id"]])
    row = call.row("select id, property_id, inspection_id, code_scheme, code, description, location, status,"
                   " identified_on, closed_at, xmin::text as row_version,"
                   " coalesce((select array_agg(case_id) from caseward.case_defect where defect_id = defect.id), '{}')"
                   " as case_ids from caseward.defect where id = %s", [defect["id"]])
    return Result(200, row, headers={"ETag": f'"{row["row_version"]}"'}, audit_client_id=defect["client_id"])


def link_case_defect(call: Call) -> Result:
    case = call.row("select id, client_id from caseward.hazard_case where id = %s", [call.path["id"]])
    defect = call.row("select id, client_id from caseward.defect where id = %s", [call.path["defect_id"]])
    if case is None or defect is None or case["client_id"] != defect["client_id"]:
        raise not_found()
    call.conn.execute("insert into caseward.case_defect (client_id, case_id, defect_id) values (%s, %s, %s)"
                      " on conflict do nothing", [case["client_id"], case["id"], defect["id"]])
    return Result(204)


def unlink_case_defect(call: Call) -> Result:
    deleted = call.row("delete from caseward.case_defect where case_id = %s and defect_id = %s returning case_id",
                       [call.path["id"], call.path["defect_id"]])
    if deleted is None:
        raise not_found()
    return Result(204)


def cancel_deadline(call: Call) -> Result:
    from caseward.api.handlers.shared import DEADLINE_SELECT, deadline_out
    deadline = call.row("select id, client_id, status, case_id, defect_id, inspection_id from caseward.deadline"
                        " where id = %s for update", [call.path["id"]])
    if deadline is None:
        raise not_found()
    if deadline["status"] in ("met", "cancelled"):
        raise Problem(422, "not_cancellable", f"The deadline is already {deadline['status']}")
    _cancel(call, "id = %s", [deadline["id"]], call.body["reason"])
    if call.body.get("recompute", True):
        kind = next(k for k in ("case", "defect", "inspection") if deadline[f"{k}_id"])
        queue_recalculation(call, kind, deadline[f"{kind}_id"], deadline["client_id"])
    row = call.row(DEADLINE_SELECT + " where d.id = %s", [deadline["id"]])
    return Result(200, deadline_out(row), audit_client_id=deadline["client_id"])


def rerun_extraction(call: Call) -> Result:
    doc = call.row("select id, client_id, malware_scan, doc_type from caseward.document where id = %s",
                   [call.path["id"]])
    if doc is None:
        raise not_found()
    if doc["malware_scan"] != "clean" or call.row(
            "select 1 from caseward.ocr_output where document_id = %s", [doc["id"]]) is None:
        raise Problem(409, "not_ready", "The document has not been read yet")
    if call.row("""select 1 from caseward.extraction_review r join caseward.extraction e on e.id = r.extraction_id
                   where e.document_id = %s and r.decision <> 'rejected'""", [doc["id"]]):
        raise Problem(409, "already_approved", "An approved extraction already created this document's records")
    call.conn.execute(
        "insert into caseward.job (client_id, kind, payload, idempotency_key) values (%s, 'extract', %s, %s)",
        [doc["client_id"], Jsonb({"document_id": str(doc["id"])}), f"extract:{doc['id']}:{uuid4()}"])
    return Result(202, headers={"Location": f"/documents/{doc['id']}"}, audit_client_id=doc["client_id"])
