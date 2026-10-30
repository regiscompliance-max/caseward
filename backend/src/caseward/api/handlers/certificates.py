"""Inspections (certificates) and defects. These only ever come from
human-approved extractions or manual entry, never straight from the AI."""

from caseward.api.framework import Call, Result
from caseward.api.handlers.shared import decode_cursor, limit_plus_one, page
from caseward.api.problems import not_found

INSPECTION_SELECT = """
  select i.id, i.property_id, i.document_id, i.certificate_type, i.inspected_on, i.next_due_on, i.engineer_name,
         i.engineer_registration, i.outcome, i.client_id as _client_id
  from caseward.inspection i
"""
DEFECT_SELECT = """
  select f.id, f.property_id, f.inspection_id, f.code_scheme, f.code, f.description, f.location, f.status,
         f.identified_on, f.closed_at, f.created_at as _created_at, f.client_id as _client_id,
         coalesce((select array_agg(cd.case_id) from caseward.case_defect cd where cd.defect_id = f.id), '{}') as case_ids
  from caseward.defect f
"""


def _clean(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _with_defects(call: Call, inspections: list[dict]) -> list[dict]:
    defects: dict = {}
    if inspections:
        for d in call.rows(DEFECT_SELECT + " where f.inspection_id = any(%s) order by f.created_at, f.id",
                           [[i["id"] for i in inspections]]):
            defects.setdefault(d["inspection_id"], []).append(_clean(d))
    return [{**_clean(i), "defects": defects.get(i["id"], [])} for i in inspections]


def list_inspections(call: Call) -> Result:
    q = call.query
    where, params = ["true"], []
    for name in ("property_id", "certificate_type"):
        if name in q:
            where.append(f"i.{name} = %s")
            params.append(q[name])
    if "due_before" in q:
        where.append("i.next_due_on < %s")
        params.append(q["due_before"])
    cursor = decode_cursor(call)
    if cursor:
        where.append("(i.inspected_on, i.id) < (%s::date, %s::uuid)")
        params += cursor
    rows = call.rows(INSPECTION_SELECT + f" where {' and '.join(where)} order by i.inspected_on desc, i.id desc"
                     " limit %s", params + [limit_plus_one(call)])
    result = page(call, rows, ["inspected_on", "id"])
    result["items"] = _with_defects(call, result["items"])
    return Result(200, result)


def get_inspection(call: Call) -> Result:
    row = call.row(INSPECTION_SELECT + " where i.id = %s", [call.path["id"]])
    if row is None:
        raise not_found()
    return Result(200, _with_defects(call, [row])[0], audit_client_id=row["_client_id"])


def list_defects(call: Call) -> Result:
    q = call.query
    where, params = ["true"], []
    for name in ("property_id", "status"):
        if name in q:
            where.append(f"f.{name} = %s")
            params.append(q[name])
    if "code" in q:
        scheme, _, code = q["code"].partition(":")
        where.append("(f.code_scheme, f.code) = (%s, %s)")
        params += [scheme, code]
    cursor = decode_cursor(call)
    if cursor:
        where.append("(f.created_at, f.id) < (%s::timestamptz, %s::uuid)")
        params += cursor
    rows = call.rows(DEFECT_SELECT + f" where {' and '.join(where)} order by f.created_at desc, f.id desc limit %s",
                     params + [limit_plus_one(call)])
    result = page(call, rows, ["_created_at", "id"])
    result["items"] = [_clean(r) for r in result["items"]]
    return Result(200, result)


def get_defect(call: Call) -> Result:
    row = call.row(DEFECT_SELECT.replace("from caseward.defect f", ", f.xmin::text as _etag from caseward.defect f")
                   + " where f.id = %s", [call.path["id"]])
    if row is None:
        raise not_found()
    return Result(200, _clean(row), headers={"ETag": f'"{row["_etag"]}"'}, audit_client_id=row["_client_id"])
