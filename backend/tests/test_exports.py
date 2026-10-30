"""Exports: what goes in, who can see it, and the evidence pack."""

import csv
import hashlib
import io
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from PIL import Image
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pypdf import PdfReader

from caseward import pages
from caseward.api.handlers import client as client_handlers
from caseward.api.handlers import operations as ops_handlers
from caseward.db import scoped_transaction
from caseward.services import Services
from caseward.storage import LocalStorage
from caseward.worker.runner import Worker, build_handlers
from conftest import PASSWORD, insert, new_case, new_deadline
from test_api import As, make_client, problem


@pytest.fixture(scope="module")
def services(tmp_path_factory):
    return Services(storage=LocalStorage(tmp_path_factory.mktemp("storage")))


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


def request(who, kind, fmt="csv", **filters):
    return who.post("/exports", json={"kind": kind, "format": fmt, "filters": filters})


def produced(who, worker, services, admin_dsn, kind, fmt="csv", **filters) -> bytes:
    created = request(who, kind, fmt, **filters)
    assert created.status_code == 202, created.text
    assert created.json()["status"] == "queued"
    worker.drain()
    export = who.get(f"/exports/{created.json()['id']}").json()
    assert export["status"] == "ready", export
    download = who.get(f"/exports/{export['id']}/download", follow_redirects=False)
    assert download.status_code == 303
    with psycopg.connect(admin_dsn) as admin:
        key, digest = admin.execute("select storage_key, sha256 from caseward.export where id = %s",
                                    [export["id"]]).fetchone()
    data = services.storage.get(key)
    assert hashlib.sha256(data).hexdigest() == digest
    return data


def read_csv(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def pdf_text(data: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(p.extract_text() for p in reader.pages), len(reader.pages)


# Contents -------------------------------------------------------------------------------

def test_deadline_performance_csv(landlord, client_api, app_conn, worker, services, admin_dsn, world, rule,
                                  calendar_ew):
    with scoped_transaction(app_conn, world.a) as conn:
        case = insert(conn, "hazard_case", client_id=world.landlord_a, property_id=world.property_a,
                      reference=f"=HYPERLINK(\"http://evil\")-{uuid.uuid4().hex[:4]}",
                      hazard_category="damp_and_mould_growth", severity="significant",
                      received_at="2026-12-23T15:00:00+00:00", aware_at="2026-12-23T15:00:00+00:00",
                      awareness_source="tenant_report")
        new_deadline(conn, world, rule, calendar_ew, case)
    rows = read_csv(produced(landlord, worker, services, admin_dsn, "deadline_performance", **{"from": "2027-01-01"}))
    assert rows[0][:3] == ["Case", "Hazard", "Severity"]
    ours = [r for r in rows[1:] if "HYPERLINK" in r[0]]
    assert len(ours) == 1
    assert ours[0][0].startswith("'=")  # spreadsheets will not run it
    assert ours[0][1] == "Damp and mould growth" and ours[0][7] == "11/01/2027 23:59"

    other = read_csv(produced(As(client_api, world, "user_b", "client"), worker, services, admin_dsn,
                              "deadline_performance"))
    assert other[1:] == []


def test_certificate_register_and_defects_as_pdf(landlord, app_conn, worker, services, admin_dsn, world):
    with scoped_transaction(app_conn, world.a) as conn:
        inspection = insert(conn, "inspection", client_id=world.landlord_a, property_id=world.property_a,
                            certificate_type="eicr", inspected_on="2020-06-01", next_due_on="2025-06-01",
                            outcome="unsatisfactory")
        insert(conn, "defect", client_id=world.landlord_a, property_id=world.property_a, inspection_id=inspection,
               code_scheme="eicr", code="C2", description="No RCD protection", identified_on="2020-06-01")
    register, _ = pdf_text(produced(landlord, worker, services, admin_dsn, "certificate_register", "pdf"))
    assert "Certificate register" in register and "Expired" in register and "1 High St" in register
    defects, _ = pdf_text(produced(landlord, worker, services, admin_dsn, "open_defects", "pdf"))
    assert "No RCD protection" in defects and "eicr:C2" in defects


def test_contractors_export_only_what_they_can_see(client_api, app_conn, staff_conn, worker, services, admin_dsn,
                                                   world):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        insert(conn, "defect", client_id=world.landlord_b, property_id=world.property_b, code_scheme="eicr",
               code="C1", description=f"Landlord B only {world.tag}", identified_on="2026-10-01")
        insert(conn, "defect", client_id=world.landlord_a, property_id=world.property_a, code_scheme="eicr",
               code="C1", description=f"Landlord A {world.tag}", identified_on="2026-10-01")
    contractor = As(client_api, world, "user_c", "client")
    rows = read_csv(produced(contractor, worker, services, admin_dsn, "open_defects"))
    descriptions = {r[2] for r in rows[1:]}
    assert f"Landlord A {world.tag}" in descriptions and f"Landlord B only {world.tag}" not in descriptions


# Evidence pack ---------------------------------------------------------------------------------

def evidence_case(app_conn, staff_conn, services, world):
    with scoped_transaction(app_conn, world.a) as conn:
        doc = insert(conn, "document", client_id=world.landlord_a, property_id=world.property_a, doc_type="photo",
                     source="upload", received_at="2026-12-22T10:00:00+00:00", storage_key=f"k/{uuid.uuid4()}",
                     sha256=uuid.uuid4().hex * 2, mime_type="image/png", size_bytes=10,
                     original_filename="bedroom-wall.png", malware_scan="clean")
        key = f"clients/{world.landlord_a}/documents/{doc}/pages/1.webp"
        services.storage.put(key, pages.to_webp(Image.new("RGB", (400, 300), "darkgreen")), "image/webp")
        conn.execute("insert into caseward.document_page (document_id, client_id, page_number, image_storage_key,"
                     " width, height, has_text_layer) values (%s, %s, 1, %s, 400, 300, false)",
                     [doc, world.landlord_a, key])
        case = new_case(conn, world)
        for kind, at, notes in (("case_aware", "2026-12-23T15:00:00+00:00", "Tenant phoned about black mould"),
                                ("access_refused", "2026-12-28T10:00:00+00:00", "Tenant unwell, asked to rebook")):
            insert(conn, "case_event", client_id=world.landlord_a, case_id=case, kind=kind, occurred_at=at,
                   notes=notes, evidence_document_id=doc if kind == "case_aware" else None, recorded_by=world.user_a)
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        template = insert(conn, "letter_template", template_key=f"t{uuid.uuid4().hex[:6]}", version=1, title="T",
                          body="x", status="approved", approved_by=world.compliance_lead, approved_at=datetime.now(UTC))
        letter = insert(conn, "letter", client_id=world.landlord_a, case_id=case, template_id=template,
                        recipient_kind="tenant", recipient_contact_id=world.contact_a, channel="post")
        conn.execute("insert into caseward.letter_version (letter_id, client_id, version, body, origin, created_by)"
                     " values (%s, %s, 1, %s, 'human_edit', %s)",
                     [letter, world.landlord_a, "Dear Pat Tenant,\nWe will visit on 4 January.", world.caseworker])
        conn.execute("update caseward.letter set status = 'approved', approved_version = 1, approved_by = %s,"
                     " approved_at = now() where id = %s", [world.compliance_lead, letter])
    with scoped_transaction(app_conn, world.a) as conn:
        sha = conn.execute("select sha256 from caseward.document where id = %s", [doc]).fetchone()[0]
    return case, sha


def test_evidence_pack_has_the_whole_story(landlord, client_api, app_conn, staff_conn, worker, services, admin_dsn,
                                           world):
    case, sha = evidence_case(app_conn, staff_conn, services, world)
    text, page_count = pdf_text(produced(landlord, worker, services, admin_dsn, "case_evidence_pack", "pdf",
                                         case_id=str(case)))
    for expected in ("Case evidence pack", "Timeline", "Landlord became aware", "Tenant unwell, asked to rebook",
                     "Access refused by tenant", "We will visit on 4 January", "bedroom-wall.png", sha[:32]):
        assert expected in text.replace("\n", " "), expected
    assert page_count >= 4  # summary and timeline, letters, document index, the photo (scanned clean)

    contractor = As(client_api, world, "user_c", "client")
    seen, _ = pdf_text(produced(contractor, worker, services, admin_dsn, "case_evidence_pack", "pdf",
                                case_id=str(case)))
    assert "Tenant unwell" in seen and "We will visit on 4 January" not in seen  # providers see no letters

    timeline = read_csv(produced(landlord, worker, services, admin_dsn, "case_evidence_pack", "csv", case_id=str(case)))
    what = [r[1] for r in timeline[1:]]
    assert what.index("Document received (photo)") < what.index("Landlord became aware") < what.index(
        "Access refused by tenant")


def test_evidence_packs_are_for_one_visible_case(landlord, client_api, app_conn, world):
    problem(request(landlord, "case_evidence_pack", "pdf"), 422, "case_required")
    with scoped_transaction(app_conn, world.a) as conn:
        case = new_case(conn, world)
    problem(request(As(client_api, world, "user_b", "client"), "case_evidence_pack", "pdf", case_id=str(case)), 404,
            "not_found")


def test_missing_case_fails_the_export_cleanly(worker, admin_dsn, world):
    with psycopg.connect(admin_dsn) as admin:
        export_id = admin.execute(
            "insert into caseward.export (client_id, requested_by, kind, format, filters)"
            " values (%s, %s, 'case_evidence_pack', 'pdf', %s) returning id",
            [world.landlord_a, world.user_a, Jsonb({"case_id": str(uuid.uuid4())})]).fetchone()[0]
        admin.execute("insert into caseward.job (client_id, kind, payload, idempotency_key) values (%s, 'export', %s, %s)",
                      [world.landlord_a, Jsonb({"export_id": str(export_id)}), f"export:{export_id}"])
    worker.drain()
    with psycopg.connect(admin_dsn) as admin:
        assert admin.execute("select status, error from caseward.export where id = %s",
                             [export_id]).fetchone() == ("failed", "case not found")


# Who can download -------------------------------------------------------------------------------

def test_exports_belong_to_whoever_asked(landlord, client_api, worker, world):
    created = request(landlord, "open_defects").json()
    colleague = As(client_api, world, "user_a2", "client")
    problem(colleague.get(f"/exports/{created['id']}"), 404, "not_found")
    problem(colleague.get(f"/exports/{created['id']}/download", follow_redirects=False), 404, "not_found")


def test_staff_prepare_exports_for_client_admins(ops_api, landlord, client_api, worker, services, admin_dsn, world):
    handler = As(ops_api, world, "case_handler", "operations")
    prepared = handler.post("/exports", json={"kind": "certificate_register", "format": "pdf"},
                            params={"client_id": str(world.landlord_a)})
    assert prepared.status_code == 202, prepared.text
    problem(handler.post("/exports", json={"kind": "audit_log", "format": "csv"},
                         params={"client_id": str(world.landlord_a)}), 422, "rule_violation")
    worker.drain()
    assert landlord.get(f"/exports/{prepared.json()['id']}").json()["status"] == "ready"
    assert landlord.get(f"/exports/{prepared.json()['id']}/download", follow_redirects=False).status_code == 303
    member = As(client_api, world, "user_a2", "client")
    problem(member.get(f"/exports/{prepared.json()['id']}"), 404, "not_found")


def test_only_client_admins_export_the_audit_log(landlord, client_api, worker, services, admin_dsn, world):
    problem(request(As(client_api, world, "user_a2", "client"), "audit_log"), 403, "forbidden")
    problem(request(As(client_api, world, "user_c", "client"), "audit_log"), 403, "forbidden")
    rows = read_csv(produced(landlord, worker, services, admin_dsn, "audit_log"))
    assert rows[0][:3] == ["Event", "When (UTC)", "Actor kind"]
    assert any(r[4] == "insert" and r[5] == "property" for r in rows[1:])


def test_exports_expire(landlord, worker, admin_dsn, world):
    created = request(landlord, "open_defects").json()
    worker.drain()
    with psycopg.connect(admin_dsn) as admin:
        admin.execute("update caseward.export set expires_at = now() - interval '1 minute' where id = %s",
                      [created["id"]])
    assert landlord.get(f"/exports/{created['id']}").json()["status"] == "expired"
    problem(landlord.get(f"/exports/{created['id']}/download", follow_redirects=False), 409, "expired")


def test_exports_and_downloads_are_audited(landlord, worker, admin_dsn, world):
    created = request(landlord, "open_defects").json()
    worker.drain()
    landlord.get(f"/exports/{created['id']}/download", follow_redirects=False)
    with psycopg.connect(admin_dsn) as admin:
        actions = {r[0] for r in admin.execute("select action from caseward.audit_event where entity_type = 'exports'"
                                                " and entity_id = %s", [created["id"]]).fetchall()}
    assert actions == {"export", "download"}
