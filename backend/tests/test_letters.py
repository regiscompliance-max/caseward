"""Letters from template to sent, with every step a person controls."""

import uuid

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from caseward.ai.provider import Completion
from caseward.api.handlers import client as client_handlers
from caseward.api.handlers import operations as ops_handlers
from caseward.db import scoped_transaction
from caseward.services import Services
from caseward.storage import LocalStorage
from caseward.worker.runner import Worker, build_handlers
from conftest import PASSWORD, insert, new_case, new_rule
from test_api import As, make_client, problem

SUMMARY_TEMPLATE = """{{ today }}

Dear {{ recipient_name }},

{{ property_address }} (case {{ case_reference }}): {{ hazard_category }}

We inspected your home on {{ investigation_date }}.

{{ ai_summary }}

Yours sincerely,
{{ landlord_name }}"""


class SummaryModel:
    name = "fake-model"

    def __init__(self):
        self.calls = []

    def complete_json(self, system, user, schema_name, schema):
        self.calls.append(user)
        return Completion({"summary": "We found mould in your bedroom. We will start repairs soon."},
                          "fake", "fake-model-2026-10")


@pytest.fixture(scope="module")
def model():
    return SummaryModel()


@pytest.fixture(scope="module")
def services(tmp_path_factory, model):
    return Services(storage=LocalStorage(tmp_path_factory.mktemp("storage")), ai=model)


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
def handler(ops_api, world):
    return As(ops_api, world, "case_handler", "operations")


@pytest.fixture
def lead(ops_api, world):
    return As(ops_api, world, "lead", "operations")


@pytest.fixture
def second_lead(ops_api, staff_conn, world):
    name = f"lead2{uuid.uuid4().hex[:4]}"
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        insert(conn, "app_user", idp_subject=world.idp_subject(name), email=f"{name}-{world.tag}@example.test",
               display_name=name, staff_role="compliance_lead")
    return As(ops_api, world, name, "operations")


@pytest.fixture
def template(lead, second_lead):
    created = lead.post("/letter-templates", json={
        "template_key": f"written_summary_{uuid.uuid4().hex[:6]}", "title": "Written summary",
        "body": SUMMARY_TEMPLATE, "rule_pack": "awaab_social", "case_event_kind": "written_summary_sent"})
    assert created.status_code == 201, created.text
    approved = second_lead.post(f"/letter-templates/{created.json()['id']}/approve")
    assert approved.status_code == 200, approved.text
    return approved.json()


@pytest.fixture
def case_id(app_conn, world):
    with scoped_transaction(app_conn, world.a) as conn:
        case = new_case(conn, world)
        insert(conn, "case_event", client_id=world.landlord_a, case_id=case, kind="investigation_completed",
               occurred_at="2026-12-24T11:00:00+00:00", notes="Black mould behind the wardrobe", recorded_by=world.user_a)
    return case


def start_letter(handler, world, template, case_id, **overrides):
    body = {"case_id": str(case_id), "template_id": template["id"], "recipient_kind": "tenant",
            "recipient_contact_id": str(world.contact_a), "channel": "post", **overrides}
    body = {k: v for k, v in body.items() if v is not None}
    return handler.post("/letters", json=body, params={"client_id": str(world.landlord_a)})


def drafted(handler, worker, world, template, case_id, **overrides):
    created = start_letter(handler, world, template, case_id, **overrides)
    assert created.status_code == 202, created.text
    assert created.json()["status"] == "drafting"
    worker.drain()
    return handler.get(f"/letters/{created.json()['id']}").json()


# Templates ----------------------------------------------------------------------

def test_templates_refuse_unknown_merge_fields(lead):
    response = lead.post("/letter-templates", json={"template_key": "bad", "title": "Bad",
                                                   "body": "Dear {{ tenant_phone }}"})
    assert "tenant_phone" in problem(response, 422, "invalid_template")["detail"]


def test_templates_need_a_second_compliance_lead(lead, handler):
    created = lead.post("/letter-templates", json={"template_key": f"k{uuid.uuid4().hex[:6]}", "title": "T",
                                                  "body": "Dear {{ recipient_name }}"}).json()
    problem(lead.post(f"/letter-templates/{created['id']}/approve"), 422, "self_approval")
    problem(handler.post(f"/letter-templates/{created['id']}/approve"), 403, "forbidden")


def test_approved_templates_are_frozen(staff_conn, world, template):
    with pytest.raises(psycopg.errors.RaiseException, match="create a new version"):
        with scoped_transaction(staff_conn, world.staff_ctx) as conn:
            conn.execute("update caseward.letter_template set body = 'changed' where id = %s", [template["id"]])


def test_new_versions_number_themselves(lead, template):
    again = lead.post("/letter-templates", json={"template_key": template["template_key"], "title": "v2",
                                                "body": SUMMARY_TEMPLATE})
    assert again.json()["version"] == template["version"] + 1


def test_unapproved_templates_cannot_be_used(handler, lead, world, case_id):
    draft = lead.post("/letter-templates", json={"template_key": f"k{uuid.uuid4().hex[:6]}", "title": "T",
                                                "body": "Dear {{ recipient_name }}"}).json()
    problem(start_letter(handler, world, draft, case_id), 422, "template_not_approved")


# Drafting -----------------------------------------------------------------------

def test_draft_merges_facts_and_an_ai_summary(handler, worker, model, world, template, case_id):
    letter = drafted(handler, worker, world, template, case_id)
    assert letter["status"] == "draft_ready"
    [version] = letter["versions"]
    assert (version["origin"], version["model_version"], version["prompt_id"]) == (
        "ai_draft", "fake-model-2026-10", "letter_summary")
    assert "Dear Pat Tenant," in version["body"]
    assert "1 High St, SW1A 1AA" in version["body"]
    assert "We inspected your home on 24 December 2026." in version["body"]
    assert "We found mould in your bedroom." in version["body"]
    # The model saw the case facts, never the tenant's name or contact details.
    sent_to_model = model.calls[-1]
    assert "Black mould behind the wardrobe" in sent_to_model
    assert "Pat" not in sent_to_model and "pat@example.test" not in sent_to_model


def test_without_ai_the_gap_is_left_for_a_person(handler, worker, world, template, case_id):
    letter = drafted(handler, worker, world, template, case_id, ai_draft=False)
    [version] = letter["versions"]
    assert version["origin"] == "template" and version["model_version"] is None
    assert "[[ai_summary:" in version["body"]
    body = problem(handler.post(f"/letters/{letter['id']}/approve", json={"version": 1}), 422,
                   "unresolved_placeholders")
    assert "ai_summary" in body["detail"]


def test_drafting_queues_an_approval_task(handler, worker, admin_dsn, world, template, case_id):
    letter = drafted(handler, worker, world, template, case_id)
    with psycopg.connect(admin_dsn) as admin:
        assert admin.execute("select kind, status from caseward.task where letter_id = %s",
                             [letter["id"]]).fetchall() == [("approve_letter", "queued")]


# Editing and approval --------------------------------------------------------------

def test_edits_are_versioned_and_conflicts_caught(handler, lead, worker, world, template, case_id):
    letter = drafted(handler, worker, world, template, case_id, ai_draft=False)
    body = "Edited by the lead"
    edited = handler.post(f"/letters/{letter['id']}/versions", json={"body": "Edited by handler", "based_on_version": 1})
    assert edited.status_code == 201 and edited.json()["version"] == 2
    problem(lead.post(f"/letters/{letter['id']}/versions", json={"body": body, "based_on_version": 1}), 409,
            "version_conflict")


def test_nobody_approves_their_own_words(handler, lead, admin_dsn, client_api, worker, world, template, case_id):
    letter = drafted(handler, worker, world, template, case_id)
    handler.post(f"/letters/{letter['id']}/versions", json={"body": "Final wording", "based_on_version": 1})
    problem(handler.post(f"/letters/{letter['id']}/approve", json={"version": 2}), 422, "self_approval")
    problem(lead.post(f"/letters/{letter['id']}/approve", json={"version": 1}), 422, "not_latest")
    approved = lead.post(f"/letters/{letter['id']}/approve", json={"version": 2})
    assert approved.status_code == 200, approved.text
    assert (approved.json()["status"], approved.json()["approved_version"]) == ("approved", 2)
    problem(handler.post(f"/letters/{letter['id']}/versions", json={"body": "late", "based_on_version": 2}), 409,
            "not_editable")
    with psycopg.connect(admin_dsn) as admin:
        assert admin.execute("select status from caseward.task where letter_id = %s",
                             [letter["id"]]).fetchone() == ("done",)


# Sending ------------------------------------------------------------------------------

def test_sending_the_summary_meets_its_deadline(handler, lead, staff_conn, app_conn, worker, world, calendar_ew,
                                                template, admin_dsn):
    with scoped_transaction(staff_conn, world.staff_ctx) as conn:
        rule = new_rule(conn, world, trigger_event="investigation_completed", completion_event="written_summary_sent",
                        duration=3)
    with scoped_transaction(app_conn, world.a) as conn:
        case = new_case(conn, world)
        insert(conn, "case_event", client_id=world.landlord_a, case_id=case, kind="investigation_completed",
               occurred_at="2026-12-24T11:00:00+00:00", recorded_by=world.user_a)
    worker.drain()
    letter = drafted(handler, worker, world, template, case)
    assert "31 December 2026" not in letter["versions"][0]["body"]  # dates come from merge fields only
    lead.post(f"/letters/{letter['id']}/approve", json={"version": 1})
    sent = handler.post(f"/letters/{letter['id']}/sent", json={"sent_at": "2026-12-29T09:00:00Z"})
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "sent"
    with psycopg.connect(admin_dsn) as admin:
        status, completed = admin.execute("select status, completed_at from caseward.deadline"
                                          " where case_id = %s and rule_key = %s", [case, rule[0]]).fetchone()
        event = admin.execute("select channel from caseward.case_event where case_id = %s"
                              " and kind = 'written_summary_sent'", [case]).fetchone()
    assert (status, completed.isoformat()) == ("met", "2026-12-29T09:00:00+00:00")
    assert event == ("letter",)
    problem(handler.post(f"/letters/{letter['id']}/void"), 422, "not_voidable")


def test_only_approved_letters_can_be_sent(handler, worker, world, template, case_id):
    letter = drafted(handler, worker, world, template, case_id)
    problem(handler.post(f"/letters/{letter['id']}/sent", json={"sent_at": "2026-12-29T09:00:00Z"}), 422,
            "not_approved")
    voided = handler.post(f"/letters/{letter['id']}/void")
    assert voided.json()["status"] == "void"


# Recipients and visibility ---------------------------------------------------------------

def test_contractor_instructions_go_only_to_engaged_contractors(handler, worker, world, template, case_id):
    problem(start_letter(handler, world, template, case_id, recipient_kind="contractor", recipient_contact_id=None,
                         recipient_client_id=str(world.landlord_b)), 422, "recipient_not_engaged")
    letter = drafted(handler, worker, world, template, case_id, recipient_kind="contractor",
                     recipient_contact_id=None, recipient_client_id=str(world.contractor_c))
    assert f"Dear Contractor C {world.tag}," in letter["versions"][0]["body"]


def test_clients_see_approved_letters_only(handler, lead, client_api, worker, world, template, case_id):
    landlord = As(client_api, world, "user_a", "client")
    letter = drafted(handler, worker, world, template, case_id)
    problem(landlord.get(f"/letters/{letter['id']}"), 404, "not_found")
    assert letter["id"] not in [l["id"] for l in landlord.get("/letters").json()["items"]]
    lead.post(f"/letters/{letter['id']}/approve", json={"version": 1})
    seen = landlord.get(f"/letters/{letter['id']}").json()
    assert seen["approved_text"] == letter["versions"][0]["body"]
    contractor = As(client_api, world, "user_c", "client")
    problem(contractor.get(f"/letters/{letter['id']}"), 404, "not_found")
