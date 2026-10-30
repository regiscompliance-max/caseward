"""Document intake end to end: upload, register, scan, render, read, view."""

import hashlib
import io
import json
import socket
import struct
import threading
import uuid
from datetime import UTC, datetime

import boto3
import psycopg
import pytest
from moto import mock_aws
from PIL import Image
from psycopg_pool import ConnectionPool

from caseward import pages
from caseward.api.handlers import client as client_handlers
from caseward.api.handlers import operations as ops_handlers
from caseward.scanning import ClamdScanner, ScannerError, Verdict
from caseward.services import Services
from caseward.storage import LocalStorage, S3Storage
from caseward.worker.runner import Worker, build_handlers
from conftest import PASSWORD
from test_api import As, make_client, problem

EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class FakeScanner:
    name = "fake"

    def __init__(self):
        self.down = False

    def scan(self, data):
        if self.down:
            raise ScannerError("clamd unavailable")
        return Verdict("infected", "Eicar-Test-Signature") if EICAR in data else Verdict("clean")


class FakeOcr:
    name = "fake-ocr"
    version = "1.0"

    def read(self, image):
        return "Scanned certificate text", 0.87


def text_pdf(*lines: str) -> bytes:
    """A minimal one-page PDF with a real text layer."""
    content = "BT /F1 14 Tf 72 720 Td 18 TL " + " ".join(f"({line}) '" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R"
        " /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def scanned_pdf() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (600, 800), "white").save(out, format="PDF")
    return out.getvalue()


def png(colour="white") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (400, 300), colour).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture(scope="module")
def services(tmp_path_factory):
    return Services(storage=LocalStorage(tmp_path_factory.mktemp("storage")), scanner=FakeScanner(), ocr=FakeOcr())


@pytest.fixture(scope="module")
def client_api(database, services):
    pool, api = make_client(database, "client", "cw_test_app", client_handlers.HANDLERS, services)
    yield api
    pool.close()


@pytest.fixture(scope="module")
def ops_api(database, services):
    pool, api = make_client(database, "operations", "cw_test_staff", ops_handlers.HANDLERS, services)
    yield api
    pool.close()


@pytest.fixture(scope="module")
def worker(database, services):
    pool = ConnectionPool(psycopg.conninfo.make_conninfo(database, user="cw_test_worker", password=PASSWORD),
                          kwargs={"autocommit": True}, min_size=1, max_size=2, open=True)
    yield Worker(pool, handlers=build_handlers(services))
    pool.close()


@pytest.fixture
def landlord(client_api, world):
    return As(client_api, world, "user_a", "client")


@pytest.fixture
def handler(ops_api, world):
    return As(ops_api, world, "case_handler", "operations")


def unique(data: bytes) -> bytes:
    """Make each test's file distinct, so duplicate detection does not interfere."""
    if data.startswith(b"%PDF"):
        return data + f"\n% {uuid.uuid4()}\n".encode()
    return data + uuid.uuid4().bytes


def upload(who, services, data, mime="application/pdf", put=None, params=None, **document):
    declared = {"filename": "EICR 1 High St.pdf", "mime_type": mime, "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}
    response = who.post("/uploads", json=declared, params=params)
    if response.status_code != 201:
        return response
    issued = response.json()
    key = issued["put_url"].split("/dev-storage/")[1].split("?")[0]
    if put is not False:
        services.storage.put(key, data if put is None else put, mime)
    body = {"upload_id": issued["upload_id"], "doc_type": "certificate", "received_at": datetime.now(UTC).isoformat(),
            **document}
    # The upload records which client the document is for, so registration needs no client parameter.
    return who.post("/documents", json=body)


def test_certificate_goes_from_upload_to_readable_pages(landlord, handler, services, worker, admin_dsn, world):
    created = upload(landlord, services, unique(text_pdf("Electrical Installation Condition Report", "Code C2")),
                     property_id=str(world.property_a), certificate_type="eicr")
    assert created.status_code == 202, created.text
    document = created.json()
    assert (document["processing"], document["malware_scan"]) == ("scanning", "pending")

    worker.drain()
    assert landlord.get(f"/documents/{document['id']}").json()["processing"] == "reading"
    detail = handler.get(f"/documents/{document['id']}").json()
    assert (detail["malware_scan"], detail["page_count"]) == ("clean", 1)

    page = handler.get(f"/documents/{document['id']}/pages/1")
    assert page.status_code == 200
    assert page.headers["content-type"] == "image/webp"
    assert page.headers["cache-control"] == "no-store"
    with psycopg.connect(admin_dsn) as admin:
        stored_key, text_key, engine = admin.execute(
            "select p.image_storage_key, o.text_storage_key, o.engine from caseward.document_page p"
            " join caseward.ocr_output o on o.document_id = p.document_id where p.document_id = %s",
            [document["id"]]).fetchone()
        jobs = dict(admin.execute("select kind, status from caseward.job where payload ->> 'document_id' = %s",
                                  [document["id"]]).fetchall())
        viewed = admin.execute("select client_id from caseward.audit_event where action = 'view'"
                               " and entity_id = %s", [f"{document['id']}/pages/1"]).fetchall()
    assert page.content != services.storage.get(stored_key)  # watermarked for the viewer
    assert "Code C2" in json.loads(services.storage.get(text_key))["pages"][0]
    assert engine == "pdf-text-layer"
    # Extraction is queued and waits until the AI extraction worker exists.
    assert jobs == {"scan_malware": "succeeded", "ocr": "succeeded", "extract": "queued"}
    assert viewed == [(world.landlord_a,)]


def test_scans_are_read_by_ocr(landlord, services, worker, admin_dsn):
    document = upload(landlord, services, unique(scanned_pdf())).json()
    worker.drain()
    with psycopg.connect(admin_dsn) as admin:
        row = admin.execute("select engine, mean_confidence, page_count from caseward.ocr_output"
                            " where document_id = %s", [document["id"]]).fetchone()
    assert (row[0], float(row[1]), row[2]) == ("fake-ocr", 0.87, 1)


def test_photos_are_complete_once_rendered(landlord, services, worker):
    document = upload(landlord, services, unique(png()), mime="image/png", doc_type="photo").json()
    worker.drain()
    assert landlord.get(f"/documents/{document['id']}").json()["processing"] == "complete"


def test_the_same_file_cannot_be_uploaded_twice(landlord, services, worker):
    data = unique(text_pdf("Gas Safety Record"))
    first = upload(landlord, services, data).json()
    again = upload(landlord, services, data)
    body = problem(again, 409, "duplicate_document")
    assert body["existing_document_id"] == first["id"]


def test_registration_checks_the_file_arrived_intact(landlord, services):
    data = unique(text_pdf("Fire Risk Assessment"))
    problem(upload(landlord, services, data, put=False), 422, "upload_missing")
    problem(upload(landlord, services, unique(text_pdf("Fire Risk Assessment")), put=b"short"), 422, "upload_mismatch")


def test_files_changed_after_registration_fail_the_scan(landlord, services, worker, admin_dsn):
    data = unique(text_pdf("Lift Thorough Examination"))
    document = upload(landlord, services, data).json()
    with psycopg.connect(admin_dsn) as admin:
        key = admin.execute("select storage_key from caseward.document where id = %s", [document["id"]]).fetchone()[0]
    services.storage.put(key, bytes(len(data)), "application/pdf")  # same size, different bytes
    worker.drain()
    detail = landlord.get(f"/documents/{document['id']}").json()
    assert (detail["malware_scan"], detail["processing"]) == ("error", "failed")


def test_infected_files_are_rejected_and_never_served(landlord, handler, services, worker):
    document = upload(landlord, services, unique(b"%PDF-1.4\n" + EICAR)).json()
    worker.drain()
    detail = landlord.get(f"/documents/{document['id']}").json()
    assert (detail["malware_scan"], detail["processing"]) == ("infected", "rejected")
    problem(landlord.get(f"/documents/{document['id']}/download", follow_redirects=False), 409, "not_clean")
    problem(handler.get(f"/documents/{document['id']}/pages/1"), 409, "not_clean")


def test_scanner_outage_leaves_documents_waiting(landlord, services, worker, admin_dsn):
    services.scanner.down = True
    try:
        document = upload(landlord, services, unique(text_pdf("Asbestos Survey"))).json()
        worker.drain()
        with psycopg.connect(admin_dsn) as admin:
            status, attempts = admin.execute("select status, attempts from caseward.job where idempotency_key = %s",
                                             [f"scan_malware:{document['id']}"]).fetchone()
        assert (status, attempts) == ("queued", 1)
        assert landlord.get(f"/documents/{document['id']}").json()["processing"] == "scanning"
    finally:
        services.scanner.down = False


def test_unreadable_documents_end_up_failed(landlord, services, worker, admin_dsn):
    document = upload(landlord, services, unique(b"%PDF-1.4 this is not really a PDF")).json()
    with psycopg.connect(admin_dsn) as admin:
        admin.execute("update caseward.job set max_attempts = 1 where idempotency_key = %s",
                      [f"scan_malware:{document['id']}"])
    worker.drain()
    with psycopg.connect(admin_dsn) as admin:
        admin.execute("update caseward.job set max_attempts = 1, run_after = now() where idempotency_key = %s",
                      [f"ocr:{document['id']}"])
    worker.drain()
    assert landlord.get(f"/documents/{document['id']}").json()["processing"] == "failed"


def test_uploads_are_single_use_and_personal(landlord, client_api, services, world):
    data = unique(text_pdf("Water Risk Assessment"))
    declared = {"filename": "w.pdf", "mime_type": "application/pdf", "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}
    issued = landlord.post("/uploads", json=declared).json()
    services.storage.put(issued["put_url"].split("/dev-storage/")[1].split("?")[0], data, "application/pdf")
    body = {"upload_id": issued["upload_id"], "doc_type": "certificate", "received_at": datetime.now(UTC).isoformat()}
    colleague = As(client_api, world, "user_a2", "client")
    problem(colleague.post("/documents", json=body), 404, "not_found")
    assert landlord.post("/documents", json=body).status_code == 202
    problem(landlord.post("/documents", json=body), 409, "upload_already_used")


def test_expired_uploads_cannot_be_registered(landlord, services, admin_dsn):
    data = unique(text_pdf("Expired"))
    declared = {"filename": "x.pdf", "mime_type": "application/pdf", "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}
    issued = landlord.post("/uploads", json=declared).json()
    with psycopg.connect(admin_dsn) as admin:
        admin.execute("update caseward.upload set expires_at = now() - interval '1 minute' where id = %s",
                      [issued["upload_id"]])
    body = {"upload_id": issued["upload_id"], "doc_type": "certificate", "received_at": datetime.now(UTC).isoformat()}
    problem(landlord.post("/documents", json=body), 422, "upload_expired")


def test_contractors_upload_for_the_landlords_they_work_for(client_api, services, world):
    contractor = As(client_api, world, "user_c", "client")
    problem(upload(contractor, services, unique(text_pdf("Contractor report"))), 400, "invalid_parameter")
    problem(upload(contractor, services, unique(text_pdf("Contractor report")),
                   params={"landlord_client_id": str(world.landlord_b)}), 404, "not_found")
    created = upload(contractor, services, unique(text_pdf("Contractor report")), doc_type="contractor_report",
                     params={"landlord_client_id": str(world.landlord_a)})
    assert created.status_code == 202, created.text
    assert created.json()["client_id"] == str(world.landlord_a)


def test_clients_download_clean_originals(landlord, services, worker, admin_dsn):
    document = upload(landlord, services, unique(text_pdf("Download me"))).json()
    worker.drain()
    response = landlord.get(f"/documents/{document['id']}/download", follow_redirects=False)
    assert response.status_code == 303
    assert "signature=" in response.headers["location"]
    with psycopg.connect(admin_dsn) as admin:
        actions = admin.execute("select action from caseward.audit_event where entity_type = 'documents'"
                                " and entity_id = %s", [document["id"]]).fetchall()
    assert ("download",) in actions


def test_staff_filter_documents_by_progress(handler, landlord, services, worker, world):
    document = upload(landlord, services, unique(png("blue")), mime="image/png", doc_type="photo").json()
    worker.drain()
    params = {"client_id": str(world.landlord_a), "processing": "complete", "limit": 100}
    assert document["id"] in [d["id"] for d in handler.get("/documents", params=params).json()["items"]]
    other = handler.get("/documents", params={**params, "processing": "scanning"}).json()["items"]
    assert document["id"] not in [d["id"] for d in other]


# Components ------------------------------------------------------------------

class FakeClamd:
    """Speaks enough of clamd's INSTREAM protocol to test the client."""

    def __init__(self, reply: bytes):
        self.reply = reply
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.received = b""
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.server.accept()
        with conn:
            buffer = b""
            while True:
                buffer += conn.recv(65536)
                if b"zINSTREAM\0" in buffer:
                    break
            buffer = buffer.split(b"zINSTREAM\0", 1)[1]
            while True:
                while len(buffer) < 4:
                    buffer += conn.recv(65536)
                size = struct.unpack("!I", buffer[:4])[0]
                buffer = buffer[4:]
                if size == 0:
                    break
                while len(buffer) < size:
                    buffer += conn.recv(65536)
                self.received += buffer[:size]
                buffer = buffer[size:]
            conn.sendall(self.reply)


@pytest.mark.parametrize("reply, expected", [
    (b"stream: OK\0", Verdict("clean")),
    (b"stream: Eicar-Signature FOUND\0", Verdict("infected", "Eicar-Signature")),
])
def test_clamd_client(reply, expected):
    fake = FakeClamd(reply)
    data = b"x" * 200_000
    assert ClamdScanner("127.0.0.1", fake.port).scan(data) == expected
    assert fake.received == data


def test_clamd_errors_and_outages_raise():
    with pytest.raises(ScannerError, match="clamd error"):
        ClamdScanner("127.0.0.1", FakeClamd(b"INSTREAM size limit exceeded. ERROR\0").port).scan(b"x")
    closed = socket.create_server(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    with pytest.raises(ScannerError, match="unavailable"):
        ClamdScanner("127.0.0.1", port, timeout=1).scan(b"x")


def test_s3_storage_is_uk_only_and_encrypted():
    with pytest.raises(ValueError, match="not in the UK"):
        S3Storage("bucket", "eu-west-1", client=object())
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-west-2")
        s3.create_bucket(Bucket="caseward-test", CreateBucketConfiguration={"LocationConstraint": "eu-west-2"})
        storage = S3Storage("caseward-test", "eu-west-2", kms_key_id="alias/caseward", client=s3)
        data = b"certificate bytes"
        digest = hashlib.sha256(data).hexdigest()
        url, headers = storage.presign_put("clients/x/uploads/y", "application/pdf", len(data), digest, 900)
        assert "x-amz-checksum-sha256" in url.lower() and "x-amz-server-side-encryption" in url.lower()
        assert headers["x-amz-server-side-encryption-aws-kms-key-id"] == "alias/caseward"
        assert storage.stat("clients/x/uploads/y") is None
        storage.put("clients/x/uploads/y", data, "application/pdf")
        assert storage.stat("clients/x/uploads/y").size == len(data)
        assert storage.get("clients/x/uploads/y") == data
        assert s3.head_object(Bucket="caseward-test", Key="clients/x/uploads/y")["ServerSideEncryption"] == "aws:kms"
        assert "response-content-disposition" in storage.presign_get("clients/x/uploads/y", 60, "EICR.pdf").lower()


def test_local_storage_keys_cannot_escape_the_root(tmp_path):
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).put("../outside", b"x", "text/plain")


def test_multi_page_tiffs_render_every_page():
    out = io.BytesIO()
    first, second = Image.new("RGB", (300, 400), "white"), Image.new("RGB", (300, 400), "grey")
    first.save(out, format="TIFF", save_all=True, append_images=[second])
    rendered = pages.render(out.getvalue(), "image/tiff")
    assert len(rendered) == 2 and all(p.text is None for p in rendered)


def test_unsupported_formats_are_refused():
    with pytest.raises(pages.UnsupportedDocument):
        pages.render(b"not a pdf", "application/pdf")
    with pytest.raises(pages.UnsupportedDocument):
        pages.render(b"...", "message/rfc822")
