"""Handlers used by both APIs. Row-level security decides what each caller
sees, so the same query serves a landlord, a provider or staff."""

import base64
import json
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from caseward.api.encoding import to_json
from caseward.api.framework import Call, Result
from caseward.api.problems import Problem, not_found

# -- Pagination -------------------------------------------------------------


def decode_cursor(call: Call) -> list | None:
    cursor = call.query.get("cursor")
    if cursor is None:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except ValueError:
        raise Problem(400, "invalid_cursor", "The cursor is not valid") from None


def encode_cursor(values: list) -> str:
    return base64.urlsafe_b64encode(json.dumps(to_json(values)).encode()).decode()


def page(call: Call, rows: list[dict], key: list[str]) -> dict:
    limit = call.query.get("limit", 50)
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor([last[k] for k in key])
    for row in rows:
        for k in key:
            if k.startswith("_"):
                row.pop(k, None)
    return {"items": rows, "next_cursor": next_cursor}


def limit_plus_one(call: Call) -> int:
    return call.query.get("limit", 50) + 1


# -- Account ----------------------------------------------------------------


def get_me(call: Call) -> Result:
    user = call.row("select id, email, display_name, staff_role, is_active from caseward.app_user where id = %s",
                    [call.principal.user_id])
    memberships = [
        {"user_id": call.principal.user_id, "client_id": m.client_id, "client_name": m.client_name,
         "client_kind": m.client_kind, "role": m.role, "special_category_access": m.special_category_access}
        for m in call.principal.memberships
    ]
    acting = call.membership.client_id if call.membership else None
    return Result(200, {"user": user, "memberships": memberships, "acting_client_id": acting})


# -- Properties -------------------------------------------------------------

PROPERTY_COLUMNS = "id, client_id, uprn, address_line_1, address_line_2, town, postcode, jurisdiction"


def list_properties(call: Call) -> Result:
    where, params = ["true"], []
    if "client_id" in call.query:
        where.append("client_id = %s")
        params.append(call.query["client_id"])
    if "q" in call.query:
        where.append("(address_line_1 ilike %s or postcode ilike %s or uprn::text = %s)")
        like = f"%{call.query['q']}%"
        params += [like, like, call.query["q"]]
    cursor = decode_cursor(call)
    if cursor:
        where.append("(postcode, address_line_1, id) > (%s, %s, %s::uuid)")
        params += cursor
    rows = call.rows(
        f"select {PROPERTY_COLUMNS}, postcode as _postcode, address_line_1 as _address"
        f" from caseward.property where {' and '.join(where)}"
        " order by postcode, address_line_1, id limit %s",
        params + [limit_plus_one(call)],
    )
    return Result(200, page(call, rows, ["_postcode", "_address", "id"]))


def get_property(call: Call) -> Result:
    row = call.row(f"select {PROPERTY_COLUMNS}, xmin::text as etag from caseward.property where id = %s",
                   [call.path["id"]])
    if row is None:
        raise not_found()
    etag = row.pop("etag")
    return Result(200, row, headers={"ETag": f'"{etag}"'}, audit_client_id=row["client_id"])


def create_property(call: Call) -> Result:
    b = call.body
    row = call.row(
        f"insert into caseward.property (client_id, uprn, address_line_1, address_line_2, town, postcode, jurisdiction)"
        f" values (%s, %s, %s, %s, %s, upper(%s), %s) returning {PROPERTY_COLUMNS}",
        [call.ctx.client_id, b.get("uprn"), b["address_line_1"], b.get("address_line_2"), b.get("town"),
         b["postcode"], b["jurisdiction"]],
    )
    return Result(201, row, headers={"Location": f"/properties/{row['id']}"})


# -- Deadlines --------------------------------------------------------------

DEADLINE_SELECT = """
  select d.id, d.case_id, d.defect_id, d.inspection_id, d.rule_key, d.rule_version, r.title as rule_title,
         r.rule_pack, d.clock_started_at, d.due_at,
         (d.due_at at time zone 'Europe/London')::date as due_local_date,
         d.status, d.completed_at, p.alert_level, p.elapsed_fraction
  from caseward.deadline d
  join caseward.deadline_rule r on (r.rule_key, r.version) = (d.rule_key, d.rule_version)
  left join caseward.deadline_progress p on p.id = d.id
"""


def deadline_out(row: dict) -> dict:
    for kind in ("case", "defect", "inspection"):
        if row[f"{kind}_id"] is not None:
            subject = {"kind": kind, "id": row[f"{kind}_id"]}
    return {
        "id": row["id"],
        "subject": subject,
        "rule": {"key": row["rule_key"], "version": row["rule_version"], "title": row["rule_title"],
                 "rule_pack": row["rule_pack"]},
        "clock_started_at": row["clock_started_at"],
        "due_at": row["due_at"],
        "due_local_date": row["due_local_date"],
        "status": row["status"],
        "completed_at": row["completed_at"],
        "alert_level": row["alert_level"],
        "elapsed_fraction": row["elapsed_fraction"],
    }


def deadlines_by_id(call: Call, ids: list[UUID]) -> dict[UUID, dict]:
    if not ids:
        return {}
    rows = call.rows(DEADLINE_SELECT + " where d.id = any(%s)", [ids])
    return {r["id"]: deadline_out(r) for r in rows}


def list_deadlines(call: Call) -> Result:
    q = call.query
    where, params = ["true"], []
    for name, column in (("case_id", "d.case_id"), ("client_id", "d.client_id"), ("status", "d.status"),
                         ("alert_level", "p.alert_level")):
        if name in q:
            where.append(f"{column} = %s")
            params.append(q[name])
    if "due_before" in q:
        where.append("d.due_at < %s")
        params.append(q["due_before"])
    if "property_id" in q:
        where.append("""exists (
            select 1 from caseward.hazard_case c where c.id = d.case_id and c.property_id = %(p)s
            union all select 1 from caseward.defect f where f.id = d.defect_id and f.property_id = %(p)s
            union all select 1 from caseward.inspection i where i.id = d.inspection_id and i.property_id = %(p)s)"""
                     .replace("%(p)s", "%s"))
        params += [q["property_id"]] * 3
    cursor = decode_cursor(call)
    if cursor:
        where.append("(d.due_at, d.id) > (%s::timestamptz, %s::uuid)")
        params += cursor
    rows = call.rows(DEADLINE_SELECT + f" where {' and '.join(where)} order by d.due_at, d.id limit %s",
                     params + [limit_plus_one(call)])
    result = page(call, rows, ["due_at", "id"])
    result["items"] = [deadline_out(r) for r in result["items"]]
    return Result(200, result)


# -- Deadline alerts ----------------------------------------------------------

ALERT_SELECT = """
  select a.id, a.client_id, c.name as client_name, a.level, a.raised_at, a.acknowledged_by, a.acknowledged_at,
         a.deadline_id
  from caseward.deadline_alert a
  join caseward.client c on c.id = a.client_id
"""


def alerts_out(call: Call, rows: list[dict]) -> list[dict]:
    deadlines = deadlines_by_id(call, [r["deadline_id"] for r in rows])
    return [{**{k: v for k, v in r.items() if k != "deadline_id"}, "deadline": deadlines[r["deadline_id"]]}
            for r in rows]


def list_deadline_alerts(call: Call) -> Result:
    q = call.query
    where, params = ["true"], []
    state = q.get("state", "open")
    if state != "all":
        where.append("a.acknowledged_at is null" if state == "open" else "a.acknowledged_at is not null")
    for name in ("client_id", "level"):
        if name in q:
            where.append(f"a.{name} = %s")
            params.append(q[name])
    cursor = decode_cursor(call)
    if cursor:
        where.append("(a.raised_at, a.id) < (%s::timestamptz, %s::uuid)")
        params += cursor
    rows = call.rows(ALERT_SELECT + f" where {' and '.join(where)} order by a.raised_at desc, a.id desc limit %s",
                     params + [limit_plus_one(call)])
    result = page(call, rows, ["raised_at", "id"])
    result["items"] = alerts_out(call, result["items"])
    return Result(200, result)


def acknowledge_deadline_alert(call: Call) -> Result:
    alert = call.row("select acknowledged_at from caseward.deadline_alert where id = %s", [call.path["id"]])
    if alert is None:
        raise not_found()
    if alert["acknowledged_at"] is not None:
        raise Problem(409, "already_acknowledged", "This alert has already been acknowledged")
    call.conn.execute("update caseward.deadline_alert set acknowledged_by = %s, acknowledged_at = now() where id = %s",
                      [call.principal.user_id, call.path["id"]])
    row = call.row(ALERT_SELECT + " where a.id = %s", [call.path["id"]])
    return Result(200, alerts_out(call, [row])[0])


# -- Cases ------------------------------------------------------------------

CASE_SORTS = {
    # name: (sort expression, direction, cast for the cursor value)
    "next_deadline": ("coalesce(nd.due_at, '9999-12-31T00:00:00Z'::timestamptz)", "asc", "timestamptz"),
    "aware_at": ("c.aware_at", "desc", "timestamptz"),
    "reference": ("c.reference", "asc", "text"),
}

CASE_SELECT = """
  select c.id, c.client_id, c.property_id, c.reference, c.hazard_category, h.name as hazard_category_name,
         c.severity, c.received_at, c.aware_at, c.awareness_source, c.assigned_contractor_id, c.status,
         c.closed_at, nd.id as next_deadline_id, c.xmin::text as etag, {sort} as _sort
  from caseward.hazard_case c
  join caseward.hazard_category h on h.code = c.hazard_category
  left join lateral (
    select d.id, d.due_at from caseward.deadline d
    where d.case_id = c.id and d.status in ('open', 'missed') and d.completed_at is null
    order by d.due_at limit 1) nd on true
"""


def cases_out(call: Call, rows: list[dict]) -> list[dict]:
    deadlines = deadlines_by_id(call, [r["next_deadline_id"] for r in rows if r["next_deadline_id"]])
    defects: dict[UUID, list] = {}
    if rows:
        for link in call.rows("select case_id, defect_id from caseward.case_defect where case_id = any(%s)",
                              [[r["id"] for r in rows]]):
            defects.setdefault(link["case_id"], []).append(link["defect_id"])
    out = []
    for r in rows:
        r = dict(r)
        r.pop("etag", None)
        r.pop("_sort", None)
        r["next_deadline"] = deadlines.get(r.pop("next_deadline_id"))
        r["defect_ids"] = defects.get(r["id"], [])
        out.append(r)
    return out


def list_cases(call: Call) -> Result:
    q = call.query
    expr, direction, cast = CASE_SORTS[q.get("sort", "next_deadline")]
    where, params = ["true"], []
    for name in ("client_id", "property_id", "status", "severity", "hazard_category"):
        if name in q:
            where.append(f"c.{name} = %s")
            params.append(q[name])
    if "alert_level" in q:
        where.append("exists (select 1 from caseward.deadline_progress p where p.case_id = c.id and p.alert_level = %s)")
        params.append(q["alert_level"])
    cursor = decode_cursor(call)
    if cursor:
        op = ">" if direction == "asc" else "<"
        where.append(f"({expr}, c.id) {op} (%s::{cast}, %s::uuid)")
        params += cursor
    rows = call.rows(
        CASE_SELECT.format(sort=expr) + f" where {' and '.join(where)} order by {expr} {direction}, c.id {direction}"
        " limit %s",
        params + [limit_plus_one(call)],
    )
    result = page(call, rows, ["_sort", "id"])
    result["items"] = cases_out(call, result["items"])
    return Result(200, result)


def get_case(call: Call) -> Result:
    row = call.row(CASE_SELECT.format(sort="null") + " where c.id = %s", [call.path["id"]])
    if row is None:
        raise not_found()
    etag = row["etag"]
    return Result(200, cases_out(call, [row])[0], headers={"ETag": f'"{etag}"'}, audit_client_id=row["client_id"])


def create_case(call: Call, client_id: UUID) -> Result:
    b = call.body
    reference = b.get("reference") or f"CW-{datetime.now(UTC):%y%m%d}-{secrets.token_hex(3).upper()}"
    case_id = call.row(
        """insert into caseward.hazard_case (client_id, property_id, reference, hazard_category, severity,
             received_at, aware_at, awareness_source, awareness_evidence_document_id)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        [client_id, b["property_id"], reference, b["hazard_category"], b["severity"], b["received_at"],
         b["aware_at"], b["awareness_source"], b.get("awareness_evidence_document_id")],
    )["id"]
    for defect_id in b.get("defect_ids", []):
        call.conn.execute("insert into caseward.case_defect (client_id, case_id, defect_id) values (%s, %s, %s)",
                          [client_id, case_id, defect_id])
    call.conn.execute(
        "insert into caseward.case_event (client_id, case_id, kind, occurred_at, evidence_document_id, recorded_by)"
        " values (%s, %s, 'case_aware', %s, %s, %s)",
        [client_id, case_id, b["aware_at"], b.get("awareness_evidence_document_id"), call.principal.user_id],
    )
    if b.get("description"):
        call.conn.execute(
            "insert into caseward.case_event (client_id, case_id, kind, occurred_at, notes, recorded_by)"
            " values (%s, %s, 'note', %s, %s, %s)",
            [client_id, case_id, b["received_at"], b["description"], call.principal.user_id],
        )
    # Recording case_aware queues deadline calculation (database trigger, 0003).
    row = call.row(CASE_SELECT.format(sort="null") + " where c.id = %s", [case_id])
    return Result(201, cases_out(call, [row])[0], headers={"Location": f"/cases/{case_id}"})


CASE_EVENT_SELECT = """
  select e.id, e.case_id, e.kind, t.name as kind_name, t.is_excuse_evidence, e.occurred_at, e.channel, e.notes,
         e.evidence_document_id, e.contractor_client_id, e.corrects_event_id,
         (select c.id from caseward.case_event c where c.corrects_event_id = e.id limit 1) as corrected_by_event_id,
         e.recorded_by, e.recorded_at
  from caseward.case_event e
  join caseward.event_type t on (t.code, t.subject_kind) = (e.kind, e.subject_kind)
"""


def create_case_event(call: Call) -> Result:
    b = call.body
    case = call.row("select id, client_id from caseward.hazard_case where id = %s", [call.path["id"]])
    if case is None:
        raise not_found()
    if b.get("corrects_event_id"):
        corrected = call.row("select case_id from caseward.case_event where id = %s", [b["corrects_event_id"]])
        if corrected is None or corrected["case_id"] != case["id"]:
            raise Problem(422, "invalid_reference", "corrects_event_id must be an event on this case")
    open_before = [r["id"] for r in call.rows(
        "select id from caseward.deadline where case_id = %s and completed_at is null and status in ('open', 'missed')",
        [case["id"]])]
    contractor = b.get("contractor_client_id") or (call.membership.client_id if call.is_provider else None)
    event_id = call.row(
        """insert into caseward.case_event (client_id, case_id, kind, occurred_at, channel, notes,
             evidence_document_id, contractor_client_id, corrects_event_id, recorded_by)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        [case["client_id"], case["id"], b["kind"], b["occurred_at"], b.get("channel"), b.get("notes"),
         b.get("evidence_document_id"), contractor, b.get("corrects_event_id"), call.principal.user_id],
    )["id"]
    event = call.row(CASE_EVENT_SELECT + " where e.id = %s", [event_id])
    met = call.rows("select id from caseward.deadline where id = any(%s) and completed_at is not null", [open_before])
    event["deadlines_met"] = [r["id"] for r in met]
    return Result(201, event)


# -- Reference data ---------------------------------------------------------

ENABLED_PACKS = """
  select cp.rule_pack from caseward.client_rule_pack cp
  join caseward.rule_pack rp on rp.code = cp.rule_pack and rp.status = 'live'
  where cp.enabled_from <= current_date and (cp.enabled_to is null or cp.enabled_to > current_date)
"""


def list_hazard_categories(call: Call, enabled_only: bool) -> Result:
    where = f"where rule_pack in ({ENABLED_PACKS})" if enabled_only else ""
    rows = call.rows(f"select code, name, rule_pack, phase, in_scope_from, xmin::text as row_version"
                     f" from caseward.hazard_category {where}"
                     " order by name")
    return Result(200, {"items": rows})


def list_event_types(call: Call, enabled_only: bool, subject_kind: str | None = None) -> Result:
    where, params = ["true"], []
    if enabled_only:
        where.append(f"(rule_pack is null or rule_pack in ({ENABLED_PACKS}))")
    if subject_kind:
        where.append("subject_kind = %s")
        params.append(subject_kind)
    rows = call.rows("select code, subject_kind, name, is_excuse_evidence, rule_pack from caseward.event_type"
                     f" where {' and '.join(where)} order by subject_kind, name", params)
    return Result(200, {"items": rows})


def require(value: Any, message: str) -> Any:
    if value is None:
        raise Problem(400, "invalid_parameter", message)
    return value
