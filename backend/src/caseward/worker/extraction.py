"""The extract job: AI draft of a certificate, then a review task for a person.

Nothing here reaches a client or creates a defect. Only the review endpoint
does that, after a case handler approves (robustness rule 1).
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from caseward.ai import extraction
from caseward.deadlines import UK
from caseward.services import Services
from caseward.worker.documents import load

# Review SLAs. UNVERIFIED: to be set by the product lead.
URGENT_CODES = {"C1", "ID"}
URGENT_SLA = timedelta(hours=1)
STANDARD_SLA = timedelta(hours=24)


def extract(services: Services):
    def handler(conn: psycopg.Connection, job: dict[str, Any]):
        doc = load(conn, job)
        cur = conn.cursor(row_factory=dict_row)
        meta = cur.execute(
            """select d.certificate_type, ct.name as certificate_type_name, p.postcode
               from caseward.document d
               left join caseward.certificate_type ct on ct.code = d.certificate_type
               left join caseward.property p on p.id = d.property_id
               where d.id = %s""", [doc["id"]]).fetchone()
        certificate_type = meta["certificate_type"]

        if certificate_type not in extraction.SCHEMAS:
            # No reviewed schema for this type yet: a person reads it instead.
            cur.execute(
                "insert into caseward.task (client_id, kind, document_id, priority, sla_due_at)"
                " values (%s, 'other', %s, 3, %s)",
                [doc["client_id"], doc["id"], datetime.now(UTC) + STANDARD_SLA])
            return None

        ocr = cur.execute("select id, text_storage_key from caseward.ocr_output where document_id = %s"
                          " order by created_at desc limit 1", [doc["id"]]).fetchone()
        if ocr is None:
            raise LookupError(f"document {doc['id']} has no text yet")
        page_texts = json.loads(services.storage.get(ocr["text_storage_key"]))["pages"]

        result = extraction.extract(services.ai, certificate_type, meta["certificate_type_name"], page_texts,
                                    today=datetime.now(UK).date(), property_postcode=meta["postcode"])
        extraction_id = cur.execute(
            """insert into caseward.extraction (client_id, document_id, ocr_output_id, status, result,
                 field_confidence, flags, model_provider, model_version, prompt_id, prompt_version,
                 output_schema_version, completed_at)
               values (%s, %s, %s, 'succeeded', %s, %s, %s, %s, %s, %s, %s, %s, now()) returning id""",
            [doc["client_id"], doc["id"], ocr["id"], Jsonb(result.result), Jsonb(result.field_confidence),
             Jsonb(result.flags), result.provider, result.model_version, result.prompt_id, result.prompt_version,
             result.output_schema_version]).fetchone()["id"]

        codes = {d["code"] for d in result.result.get("defects", [])}
        urgent = bool(codes & URGENT_CODES)
        priority = 1 if urgent else 2 if result.flags else 3
        cur.execute(
            "insert into caseward.task (client_id, kind, extraction_id, priority, sla_due_at)"
            " values (%s, 'review_extraction', %s, %s, %s)",
            [doc["client_id"], extraction_id, priority,
             datetime.now(UTC) + (URGENT_SLA if urgent else STANDARD_SLA)])
        return extraction_id
    return handler
