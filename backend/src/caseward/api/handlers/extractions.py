"""Extraction review: the only way an AI draft becomes a record.

A case handler compares the draft with the page images, then approves,
corrects or rejects it. Approving or correcting creates the inspection and
its defects in the same transaction. Their database triggers then queue
deadline calculation.
"""

from psycopg.types.json import Jsonb

from caseward.ai import extraction as ai_extraction
from caseward.api.framework import Call, Result
from caseward.api.problems import Problem, not_found

EXTRACTION_COLUMNS = """e.id, e.document_id, e.status, e.result, e.field_confidence, e.flags, e.model_provider,
  e.model_version, e.prompt_id, e.prompt_version, e.output_schema_version, e.error, e.created_at, e.completed_at"""


def _review(call: Call, extraction_id) -> dict | None:
    review = call.row("select id, extraction_id, reviewer_id, decision, approved_result, notes, reviewed_at"
                      " from caseward.extraction_review where extraction_id = %s", [extraction_id])
    if review is None:
        return None
    inspection = call.row("select id from caseward.inspection where extraction_review_id = %s", [review["id"]])
    defects = call.rows("select id from caseward.defect where inspection_id = %s order by created_at, id",
                        [inspection["id"]]) if inspection else []
    review["created"] = {"inspection_id": inspection["id"] if inspection else None,
                         "defect_ids": [d["id"] for d in defects]}
    return review


def get_extraction(call: Call) -> Result:
    row = call.row(f"select {EXTRACTION_COLUMNS}, e.client_id from caseward.extraction e where e.id = %s",
                   [call.path["id"]])
    if row is None:
        raise not_found()
    client_id = row.pop("client_id")
    row["review"] = _review(call, row["id"])
    return Result(200, row, audit_client_id=client_id)


def review_extraction(call: Call) -> Result:
    b = call.body
    ex = call.row(
        """select e.id, e.client_id, e.status, e.document_id, d.property_id, d.certificate_type
           from caseward.extraction e join caseward.document d on d.id = e.document_id
           where e.id = %s for update of e""", [call.path["id"]])
    if ex is None:
        raise not_found()
    if ex["status"] != "succeeded":
        raise Problem(409, "not_reviewable", f"The extraction is {ex['status']}")
    if call.row("select 1 from caseward.extraction_review where extraction_id = %s", [ex["id"]]):
        raise Problem(409, "already_reviewed", "This extraction has already been reviewed; run extraction again to redo it")

    approving = b["decision"] != "rejected"
    result = b.get("approved_result")
    if approving:
        errors = ai_extraction.validate_result(ex["certificate_type"], result) \
            if ex["certificate_type"] in ai_extraction.SCHEMAS else ["/: no output schema for this certificate type"]
        if errors:
            raise Problem(422, "invalid_result", "The approved result does not match the certificate schema",
                          errors=[{"field": e.split(":")[0], "message": e.split(": ", 1)[-1]} for e in errors])
        if not result.get("inspection_date"):
            raise Problem(422, "invalid_result", "An approved certificate needs its inspection date")

    property_id = b.get("property_id") or ex["property_id"]
    if approving and property_id is None:
        raise Problem(422, "property_required", "Link the document to a property before approving")
    if b.get("property_id") and b["property_id"] != ex["property_id"]:
        call.conn.execute("update caseward.document set property_id = %s where id = %s",
                          [b["property_id"], ex["document_id"]])

    review_id = call.row(
        "insert into caseward.extraction_review (client_id, extraction_id, reviewer_id, decision, approved_result,"
        " notes) values (%s, %s, %s, %s, %s, %s) returning id",
        [ex["client_id"], ex["id"], call.principal.user_id, b["decision"], Jsonb(result) if approving else None,
         b.get("notes")])["id"]

    if approving:
        inspection_id = call.row(
            """insert into caseward.inspection (client_id, property_id, document_id, extraction_review_id,
                 certificate_type, inspected_on, next_due_on, engineer_name, engineer_registration, outcome)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            [ex["client_id"], property_id, ex["document_id"], review_id, ex["certificate_type"],
             result["inspection_date"], result.get("next_inspection_due"), result.get("engineer_name"),
             result.get("engineer_registration"), result.get("outcome") or "unknown"])["id"]
        for defect in result.get("defects", []):
            call.conn.execute(
                """insert into caseward.defect (client_id, property_id, inspection_id, document_id, code_scheme,
                     code, description, location, identified_on)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [ex["client_id"], property_id, inspection_id, ex["document_id"], ex["certificate_type"],
                 defect["code"], defect["description"], defect.get("location"), result["inspection_date"]])

    call.conn.execute("update caseward.task set status = 'done', completed_at = now(),"
                      " started_at = coalesce(started_at, now()), assignee_id = coalesce(assignee_id, %s)"
                      " where extraction_id = %s and status in ('queued', 'in_progress', 'blocked')",
                      [call.principal.user_id, ex["id"]])
    return Result(201, _review(call, ex["id"]), audit_client_id=ex["client_id"])
