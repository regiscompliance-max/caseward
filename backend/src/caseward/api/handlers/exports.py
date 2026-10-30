"""Exports: requested here, built by the worker, downloaded once ready.

Clients download only exports they asked for, plus exports staff prepared
for their client's admins. Staff never download (decision 0007). Ready files
expire after 7 days.
"""

from datetime import UTC, date, datetime

from psycopg.types.json import Jsonb

from caseward.api.framework import Call, Result
from caseward.api.problems import Problem, forbidden, not_found

EXPORT_COLUMNS = "id, client_id, requested_by, for_client_admins, kind, format, status, requested_at, ready_at," \
                 " expires_at, error, storage_key"
DOWNLOAD_SECONDS = 60


def _out(row: dict) -> dict:
    status = row["status"]
    if status == "ready" and row["expires_at"] < datetime.now(UTC):
        status = "expired"
    return {"id": row["id"], "kind": row["kind"], "format": row["format"], "status": status,
            "requested_at": row["requested_at"], "ready_at": row["ready_at"], "expires_at": row["expires_at"],
            "error": row["error"]}


def _create(call: Call, client_id, for_client_admins: bool) -> Result:
    b = call.body
    filters = {k: v for k, v in (b.get("filters") or {}).items() if v is not None}
    if b["kind"] == "case_evidence_pack":
        if not filters.get("case_id"):
            raise Problem(422, "case_required", "An evidence pack is for one case: give filters.case_id")
        if call.row("select 1 from caseward.hazard_case where id = %s and client_id = %s",
                    [filters["case_id"], client_id]) is None and not call.is_provider:
            raise not_found()
        if call.is_provider and call.row("select 1 from caseward.hazard_case where id = %s",
                                         [filters["case_id"]]) is None:
            raise not_found()
    if b["kind"] == "audit_log":
        if for_client_admins:
            raise Problem(422, "rule_violation", "Staff do not export clients' audit logs")
        if call.membership.role != "client_admin" or call.is_provider:
            raise forbidden("Only a landlord's client admins can export the audit log.")
    row = call.row(f"insert into caseward.export (client_id, requested_by, for_client_admins, kind, format, filters)"
                   f" values (%s, %s, %s, %s, %s, %s) returning {EXPORT_COLUMNS}",
                   [client_id, call.principal.user_id, for_client_admins, b["kind"], b["format"], Jsonb(filters)])
    call.conn.execute(
        "insert into caseward.job (client_id, kind, payload, idempotency_key) values (%s, 'export', %s, %s)",
        [client_id, Jsonb({"export_id": str(row["id"])}), f"export:{row['id']}"])
    return Result(202, _out(row), headers={"Location": f"/exports/{row['id']}"}, audit_entity_id=str(row["id"]),
                  audit_client_id=client_id)


def create_export(call: Call) -> Result:
    return _create(call, call.ctx.client_id, for_client_admins=False)


def prepare_export_for_client(call: Call) -> Result:
    if call.row("select 1 from caseward.client where id = %s and kind = 'landlord'", [call.query["client_id"]]) is None:
        raise not_found()
    return _create(call, call.query["client_id"], for_client_admins=True)


def _mine(call: Call) -> dict:
    row = call.row(f"select {EXPORT_COLUMNS} from caseward.export where id = %s", [call.path["id"]])
    if row is None:
        raise not_found()
    mine = row["requested_by"] == call.principal.user_id
    for_my_admins = (row["for_client_admins"] and row["client_id"] == call.ctx.client_id
                     and call.membership.role == "client_admin")
    if not (mine or for_my_admins):
        raise not_found()
    return row


def get_export(call: Call) -> Result:
    return Result(200, _out(_mine(call)))


def download_export(call: Call) -> Result:
    row = _mine(call)
    status = _out(row)["status"]
    if status != "ready":
        raise Problem(409, "not_ready" if status in ("queued", "running") else status,
                      f"The export is {status}")
    filename = f"caseward-{row['kind'].replace('_', '-')}-{date.today():%Y-%m-%d}.{row['format']}"
    url = call.services.storage.presign_get(row["storage_key"], DOWNLOAD_SECONDS, filename)
    return Result(303, headers={"Location": url, "Cache-Control": "no-store"}, audit_client_id=row["client_id"])
