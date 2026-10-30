"""The export job: build the file under the requester's access, store it in
UK storage with its SHA-256, and make it available for 7 days."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from caseward import exports
from caseward.services import Services

KEEP_FOR = timedelta(days=7)
MIME = {"csv": "text/csv", "pdf": "application/pdf"}


def build(rows, export: dict, page_image) -> bytes:
    kind, fmt, filters = export["kind"], export["format"], export["filters"]
    if kind == "case_evidence_pack":
        if fmt == "pdf":
            return exports.evidence_pack(rows, filters["case_id"], page_image)
        return exports.evidence_pack_csv(rows, filters["case_id"])
    if kind == "audit_log":
        header, data = exports.audit_log(rows, filters, export["client_id"])
    else:
        header, data = getattr(exports, kind)(rows, filters)
    if fmt == "csv":
        return exports.to_csv(header, data)
    return exports.table_pdf(exports.TABLES[kind], header, data, filters)


def export(services: Services):
    def handler(conn: psycopg.Connection, job: dict[str, Any]):
        cur = conn.cursor(row_factory=dict_row)
        row = cur.execute("select * from caseward.export where id = %s for update",
                          [job["payload"]["export_id"]]).fetchone()
        if row is None:
            raise LookupError(f"export {job['payload']['export_id']} not found")
        if row["status"] in ("ready", "failed"):
            return row["status"]

        def rows(query, params=None):
            with conn.cursor(row_factory=dict_row) as c:
                return c.execute(query, params).fetchall()

        try:
            data = build(rows, row, services.storage.get)
        except exports.ExportError as exc:
            cur.execute("update caseward.export set status = 'failed', error = %s where id = %s", [str(exc), row["id"]])
            return "failed"
        key = f"clients/{row['client_id']}/exports/{row['id']}.{row['format']}"
        services.storage.put(key, data, MIME[row["format"]])
        now = datetime.now(UTC)
        cur.execute("update caseward.export set status = 'ready', storage_key = %s, sha256 = %s, size_bytes = %s,"
                    " ready_at = %s, expires_at = %s where id = %s",
                    [key, hashlib.sha256(data).hexdigest(), len(data), now, now + KEEP_FOR, row["id"]])
        return "ready"
    return handler
