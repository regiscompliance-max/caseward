"""Document intake and viewing.

Upload is two steps. The caller gets a presigned URL and sends the bytes
straight to UK storage. It then registers the upload as a document, which
queues malware scanning. Clients may download originals once they are clean;
staff only ever see watermarked page images.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from caseward import pages
from caseward.api.framework import Call, Result
from caseward.api.handlers.shared import decode_cursor, limit_plus_one, page
from caseward.api.problems import Problem, not_found

UPLOAD_SECONDS = 15 * 60
DOWNLOAD_SECONDS = 60

DOCUMENT_SELECT = """
  select d.id, d.client_id, d.property_id, d.doc_type, d.certificate_type, d.source, d.received_at,
         d.original_filename, d.sha256, d.mime_type, d.size_bytes, d.malware_scan,
         caseward.document_processing(d) as processing, d.created_at, d.storage_key as _storage_key
  from caseward.document d
"""


def _out(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _upload_client(call: Call) -> UUID:
    """The client a new document belongs to. Providers upload for a landlord that engages them."""
    if call.membership is None:  # operations API: staff name the client
        return call.query["client_id"]
    landlord = call.query.get("landlord_client_id")
    if not call.is_provider:
        if landlord is not None and landlord != call.ctx.client_id:
            raise not_found()
        return call.ctx.client_id
    if landlord is None:
        raise Problem(400, "invalid_parameter", "landlord_client_id is required when uploading for a landlord")
    if call.row("select 1 from caseward.client where id = %s and kind = 'landlord'", [landlord]) is None:
        raise not_found()
    return landlord


def create_upload(call: Call) -> Result:
    b = call.body
    client_id = _upload_client(call)
    existing = call.row("select id from caseward.document where client_id = %s and sha256 = %s",
                        [client_id, b["sha256"]])
    if existing:
        raise Problem(409, "duplicate_document", "This file has already been uploaded",
                      extra={"existing_document_id": str(existing["id"])})
    upload_id = uuid4()
    key = f"clients/{client_id}/uploads/{upload_id}"
    expires_at = datetime.now(UTC) + timedelta(seconds=UPLOAD_SECONDS)
    call.conn.execute(
        "insert into caseward.upload (id, client_id, created_by, storage_key, original_filename, mime_type,"
        " size_bytes, sha256, expires_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [upload_id, client_id, call.principal.user_id, key, b["filename"], b["mime_type"], b["size_bytes"],
         b["sha256"], expires_at])
    url, headers = call.services.storage.presign_put(key, b["mime_type"], b["size_bytes"], b["sha256"],
                                                     UPLOAD_SECONDS)
    return Result(201, {"upload_id": upload_id, "put_url": url, "required_headers": headers,
                        "expires_at": expires_at})


def create_document(call: Call, default_source: str) -> Result:
    b = call.body
    upload = call.row("select * from caseward.upload where id = %s for update", [b["upload_id"]])
    if upload is None:
        raise not_found()
    if "client_id" in call.query and upload["client_id"] != call.query["client_id"]:
        raise not_found()
    if upload["document_id"] is not None:
        raise Problem(409, "upload_already_used", "This upload has already been registered",
                      extra={"existing_document_id": str(upload["document_id"])})
    if upload["expires_at"] < datetime.now(UTC):
        raise Problem(422, "upload_expired", "The upload URL expired; request a new one")
    info = call.services.storage.stat(upload["storage_key"])
    if info is None:
        raise Problem(422, "upload_missing", "The file has not been uploaded yet")
    if info.size != upload["size_bytes"] or (info.sha256 and info.sha256 != upload["sha256"]):
        raise Problem(422, "upload_mismatch", "The uploaded file does not match its declared size or SHA-256")
    if b.get("certificate_type") and b["doc_type"] != "certificate":
        raise Problem(422, "rule_violation", "certificate_type is only for doc_type 'certificate'")

    document_id = call.row(
        """insert into caseward.document (client_id, property_id, doc_type, certificate_type, source, received_at,
             storage_key, sha256, mime_type, size_bytes, original_filename, uploaded_by)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        [upload["client_id"], b.get("property_id"), b["doc_type"], b.get("certificate_type"),
         b.get("source", default_source), b["received_at"], upload["storage_key"], upload["sha256"],
         upload["mime_type"], upload["size_bytes"], upload["original_filename"], call.principal.user_id],
    )["id"]
    call.conn.execute("update caseward.upload set document_id = %s where id = %s", [document_id, upload["id"]])
    call.conn.execute(
        "insert into caseward.job (client_id, kind, payload, idempotency_key) values (%s, 'scan_malware', %s, %s)",
        [upload["client_id"], Jsonb({"document_id": str(document_id)}), f"scan_malware:{document_id}"])
    row = call.row(DOCUMENT_SELECT + " where d.id = %s", [document_id])
    return Result(202, _out(row), headers={"Location": f"/documents/{document_id}"},
                  audit_client_id=upload["client_id"])


def list_documents(call: Call) -> Result:
    q = call.query
    where, params = ["true"], []
    for name in ("client_id", "property_id", "doc_type"):
        if name in q:
            where.append(f"d.{name} = %s")
            params.append(q[name])
    if "received_from" in q:
        where.append("d.received_at >= (%s::date::timestamp at time zone 'Europe/London')")
        params.append(q["received_from"])
    if "received_to" in q:
        where.append("d.received_at < ((%s::date + 1)::timestamp at time zone 'Europe/London')")
        params.append(q["received_to"])
    if "processing" in q:
        where.append("caseward.document_processing(d) = %s")
        params.append(q["processing"])
    cursor = decode_cursor(call)
    if cursor:
        where.append("(d.received_at, d.id) < (%s::timestamptz, %s::uuid)")
        params += cursor
    rows = call.rows(DOCUMENT_SELECT + f" where {' and '.join(where)} order by d.received_at desc, d.id desc limit %s",
                     params + [limit_plus_one(call)])
    result = page(call, rows, ["received_at", "id"])
    result["items"] = [_out(r) for r in result["items"]]
    return Result(200, result)


def _document(call: Call) -> dict:
    row = call.row(DOCUMENT_SELECT + " where d.id = %s", [call.path["id"]])
    if row is None:
        raise not_found()
    return row


def get_document(call: Call) -> Result:
    row = _document(call)
    return Result(200, _out(row), audit_client_id=row["client_id"])


def get_document_with_pages(call: Call) -> Result:
    row = _document(call)
    body = _out(row)
    ocr = call.row("select page_count from caseward.ocr_output where document_id = %s"
                   " order by created_at desc limit 1", [row["id"]])
    body["page_count"] = ocr["page_count"] if ocr else None
    body["extraction_ids"] = [r["id"] for r in call.rows(
        "select id from caseward.extraction where document_id = %s order by created_at", [row["id"]])]
    return Result(200, body, audit_client_id=row["client_id"])


def download_document(call: Call) -> Result:
    row = _document(call)
    if row["malware_scan"] != "clean":
        raise Problem(409, "not_clean", "This file has not passed malware scanning")
    url = call.services.storage.presign_get(row["_storage_key"], DOWNLOAD_SECONDS, row["original_filename"])
    return Result(303, headers={"Location": url, "Cache-Control": "no-store"}, audit_client_id=row["client_id"])


def get_document_page(call: Call) -> Result:
    row = _document(call)
    if row["malware_scan"] != "clean":
        raise Problem(409, "not_clean", "This file has not passed malware scanning")
    page_row = call.row("select image_storage_key from caseward.document_page where document_id = %s"
                        " and page_number = %s", [row["id"], call.path["page"]])
    if page_row is None:
        if row["processing"] in ("scanning", "reading"):
            raise Problem(409, "not_ready", "Pages are still being prepared")
        raise not_found()
    image = call.services.storage.get(page_row["image_storage_key"])
    label = f"{call.principal.display_name} · {datetime.now(UTC):%Y-%m-%d %H:%M} UTC · Caseward · do not copy"
    return Result(200, pages.watermark(image, label), media_type="image/webp",
                  headers={"Cache-Control": "no-store"}, audit_client_id=row["client_id"],
                  audit_entity_id=f"{row['id']}/pages/{call.path['page']}")
