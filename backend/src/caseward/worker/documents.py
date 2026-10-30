"""Document jobs: scan_malware, then ocr (render pages and read text), then
extract. Each step queues the next, so a document moves through the pipeline
one committed step at a time."""

import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from caseward import pages
from caseward.services import Services
from caseward.storage import sha256_hex

log = logging.getLogger("caseward.worker.documents")

DOCUMENT = """select id, client_id, storage_key, sha256, size_bytes, mime_type, doc_type, malware_scan
              from caseward.document where id = %s"""
EXTRACTABLE = ("certificate", "hazard_report")


def queue(conn: psycopg.Connection, client_id, kind: str, document_id) -> None:
    conn.execute(
        "insert into caseward.job (client_id, kind, payload, idempotency_key) values (%s, %s, %s, %s)"
        " on conflict (idempotency_key) do nothing",
        [client_id, kind, Jsonb({"document_id": str(document_id)}), f"{kind}:{document_id}"])


def load(conn: psycopg.Connection, job: dict[str, Any]) -> dict[str, Any]:
    doc = conn.cursor(row_factory=dict_row).execute(DOCUMENT, [job["payload"]["document_id"]]).fetchone()
    if doc is None:
        raise LookupError(f"document {job['payload']['document_id']} not found")
    return doc


def scan_malware(services: Services):
    def handler(conn: psycopg.Connection, job: dict[str, Any]) -> str:
        doc = load(conn, job)
        if doc["malware_scan"] != "pending":
            return doc["malware_scan"]
        data = services.storage.get(doc["storage_key"])
        # The authoritative integrity check: the stored bytes must be the ones declared.
        if len(data) != doc["size_bytes"] or sha256_hex(data) != doc["sha256"]:
            log.warning("document %s does not match its declared size or SHA-256", doc["id"])
            conn.execute("update caseward.document set malware_scan = 'error' where id = %s", [doc["id"]])
            return "error"
        verdict = services.scanner.scan(data)
        conn.execute("update caseward.document set malware_scan = %s where id = %s", [verdict.result, doc["id"]])
        if verdict.result == "infected":
            log.warning("document %s infected: %s", doc["id"], verdict.signature)
        elif doc["mime_type"] in pages.RENDERABLE:
            queue(conn, doc["client_id"], "ocr", doc["id"])
        return verdict.result
    return handler


def ocr(services: Services):
    def handler(conn: psycopg.Connection, job: dict[str, Any]) -> int:
        doc = load(conn, job)
        if doc["malware_scan"] != "clean":
            raise RuntimeError(f"document {doc['id']} is {doc['malware_scan']}, not clean")
        if conn.execute("select 1 from caseward.ocr_output where document_id = %s", [doc["id"]]).fetchone():
            return 0
        rendered = pages.render(services.storage.get(doc["storage_key"]), doc["mime_type"])
        base = f"clients/{doc['client_id']}/documents/{doc['id']}"
        texts, confidences, used_ocr = [], [], False
        for number, page in enumerate(rendered, start=1):
            if page.text is not None:
                text, confidence = page.text, 1.0
            else:
                text, confidence = services.ocr.read(page.image)
                used_ocr = True
            key = f"{base}/pages/{number}.webp"
            services.storage.put(key, pages.to_webp(page.image), "image/webp")
            conn.execute(
                "insert into caseward.document_page (document_id, client_id, page_number, image_storage_key, width,"
                " height, has_text_layer) values (%s, %s, %s, %s, %s, %s, %s)",
                [doc["id"], doc["client_id"], number, key, page.image.width, page.image.height, page.text is not None])
            texts.append(text)
            confidences.append(confidence)

        text_key = f"{base}/text.json"
        services.storage.put(text_key, json.dumps({"pages": texts}).encode(), "application/json")
        engine = services.ocr.name if used_ocr else "pdf-text-layer"
        version = services.ocr.version if used_ocr else "pypdfium2"
        conn.execute(
            "insert into caseward.ocr_output (client_id, document_id, engine, engine_version, text_storage_key,"
            " page_count, mean_confidence) values (%s, %s, %s, %s, %s, %s, %s)",
            [doc["client_id"], doc["id"], engine, version, text_key, len(rendered),
             round(sum(confidences) / len(confidences), 4) if confidences else None])
        if doc["doc_type"] in EXTRACTABLE:
            queue(conn, doc["client_id"], "extract", doc["id"])
        return len(rendered)
    return handler
