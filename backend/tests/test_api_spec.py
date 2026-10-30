"""Checks on the OpenAPI specifications in docs/api.

Beyond validity, these enforce the conventions in docs/api/README.md and
keep enumerations in step with the database's CHECK constraints.
"""

import copy
import re
from functools import cache
from pathlib import Path

import psycopg
import pytest
import yaml
from openapi_spec_validator import validate

API_DIR = Path(__file__).resolve().parents[2] / "docs" / "api"
SPECS = ["common.yaml", "client.yaml", "operations.yaml"]
METHODS = {"get", "post", "put", "patch", "delete"}
CLIENT_ROLES = {"client_admin", "client_member", "client_viewer"}
STAFF_ROLES = {"case_handler", "quality_analyst", "compliance_lead", "admin"}
AUDIT_KINDS = {"none", "view", "write", "export"}


@cache
def _parsed(name):
    return yaml.safe_load((API_DIR / name).read_text())


def load(name):
    return copy.deepcopy(_parsed(name))


def operations(spec):
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method in METHODS:
                yield path, method, op


def refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                yield value
            else:
                yield from refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from refs(value)


def resolve(ref, current_file):
    file_part, _, pointer = ref.partition("#")
    doc = _parsed(file_part or current_file)
    for part in pointer.strip("/").split("/"):
        doc = doc[part]
    return doc


def param_names(op, spec_name):
    names = set()
    for p in op.get("parameters", []):
        if "$ref" in p:
            p = resolve(p["$ref"], spec_name)
        names.add(p["name"])
    return names


@pytest.mark.parametrize("name", SPECS)
def test_spec_is_valid_openapi(name):
    validate(load(name), base_uri=(API_DIR / name).as_uri())


@pytest.mark.parametrize("name", SPECS)
def test_every_ref_resolves(name):
    for ref in refs(load(name)):
        resolve(ref, name)


@pytest.mark.parametrize("name", SPECS)
def test_enum_values_are_strings(name):
    # YAML turns unquoted off/on/no/yes into booleans.
    def walk(node):
        if isinstance(node, dict):
            for value in node.get("enum", []):
                assert isinstance(value, str), f"non-string enum value {value!r}"
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(load(name))


@pytest.mark.parametrize("name, roles", [("client.yaml", CLIENT_ROLES), ("operations.yaml", STAFF_ROLES)])
def test_operation_conventions(name, roles):
    spec = load(name)
    ids = set()
    for path, method, op in operations(spec):
        where = f"{method.upper()} {path}"
        assert op["operationId"] not in ids, where
        ids.add(op["operationId"])
        assert op.get("x-roles") and set(op["x-roles"]) <= roles, where
        assert op.get("x-audit") in AUDIT_KINDS, where
        if name == "client.yaml":
            assert op.get("x-provider-access") in {"none", "read", "write"}, where
        responses = op["responses"]
        assert "401" in responses, where
        if "{id}" in path:
            assert "404" in responses, where
        names = param_names(op, name)
        if method == "patch":
            assert "If-Match" in names, where
        if method == "post" and ({"201", "202"} & set(responses)):
            # Retries must be safe: an idempotency key, or a natural key that conflicts.
            assert "Idempotency-Key" in names or "409" in responses, where
        if method in {"post", "put", "patch", "delete"} and name == "client.yaml":
            assert set(op["x-roles"]) != {"client_viewer"} and "client_viewer" not in op["x-roles"] \
                or op["operationId"] == "createExport", f"viewers are read-only: {where}"


@pytest.mark.parametrize("name", ["client.yaml", "operations.yaml"])
def test_special_category_reads_need_a_reason_header(name):
    for path, method, op in operations(load(name)):
        if op.get("x-special-category") and method == "get":
            assert "X-Access-Reason" in param_names(op, name), path
        for p in op.get("parameters", []):
            if "$ref" not in p:
                assert not (p["in"] == "query" and "reason" in p["name"]), path


def test_clients_never_see_draft_ai_output():
    forbidden = {"Extraction", "ExtractionReview", "Letter", "LetterVersion", "AuditEvent"}
    used = {ref.rsplit("/", 1)[-1] for ref in refs(load("client.yaml"))}
    assert not (used & forbidden)
    assert load("common.yaml")["components"]["schemas"]["ApprovedLetter"]["properties"]["status"]["enum"] == [
        "approved", "sent"]


def test_operations_api_never_hands_over_files():
    for path, method, op in operations(load("operations.yaml")):
        assert "download" not in path, path
        for status, response in op["responses"].items():
            assert status != "303", path
            for media in response.get("content", {}):
                assert media in {"application/json", "image/webp"}, f"{path} serves {media}"


# Enumerations must match the database --------------------------------------

ENUM_COLUMNS = {
    "ClientKind": ("client", "kind"),
    "StaffRole": ("app_user", "staff_role"),
    "MembershipRole": ("membership", "role"),
    "Jurisdiction": ("property", "jurisdiction"),
    "DocType": ("document", "doc_type"),
    "DocumentSource": ("document", "source"),
    "MalwareScan": ("document", "malware_scan"),
    "ExtractionStatus": ("extraction", "status"),
    "ReviewDecision": ("extraction_review", "decision"),
    "InspectionOutcome": ("inspection", "outcome"),
    "DefectStatus": ("defect", "status"),
    "Severity": ("hazard_case", "severity"),
    "CaseStatus": ("hazard_case", "status"),
    "AwarenessSource": ("hazard_case", "awareness_source"),
    "EventChannel": ("case_event", "channel"),
    "DeadlineStatus": ("deadline", "status"),
    "LetterStatus": ("letter", "status"),
    "LetterChannel": ("letter", "channel"),
    "RecipientKind": ("letter", "recipient_kind"),
    "TemplateVariant": ("letter_template", "variant"),
    "TaskKind": ("task", "kind"),
    "TaskStatus": ("task", "status"),
    "PreferredChannel": ("tenant_contact", "preferred_channel"),
    "VulnerabilitySource": ("household_vulnerability", "source"),
    "RuleStatus": ("deadline_rule", "status"),
    "DurationUnit": ("deadline_rule", "duration_unit"),
    "DayType": ("deadline_rule", "day_type"),
    "DeadlineAlertLevel": ("deadline_alert", "level"),
}
INLINE_ENUMS = {
    ("RulePack", "status"): ("rule_pack", "status"),
    ("HolidayCalendar", "region"): ("holiday_calendar", "region"),
    ("EventType", "subject_kind"): ("event_type", "subject_kind"),
    ("AuditEvent", "actor_kind"): ("audit_event", "actor_kind"),
}


def db_enum(conn, table, column):
    defs = conn.execute(
        "select pg_get_constraintdef(oid) from pg_constraint where conrelid = %s::regclass and contype = 'c'",
        [f"caseward.{table}"],
    ).fetchall()
    for (definition,) in defs:
        match = re.search(rf"\({column} = ANY \(ARRAY\[(.*?)\]\)\)", definition)
        if match:
            return set(re.findall(r"'([^']*)'::text", match.group(1)))
    raise AssertionError(f"no enum constraint on {table}.{column}")


@pytest.fixture(scope="module")
def admin(admin_dsn):
    with psycopg.connect(admin_dsn) as conn:
        yield conn


@pytest.mark.parametrize("schema", sorted(ENUM_COLUMNS))
def test_enum_matches_database(admin, schema):
    values = load("common.yaml")["components"]["schemas"][schema]["enum"]
    assert set(values) == db_enum(admin, *ENUM_COLUMNS[schema])


@pytest.mark.parametrize("schema, prop", sorted(INLINE_ENUMS))
def test_inline_enum_matches_database(admin, schema, prop):
    values = load("common.yaml")["components"]["schemas"][schema]["properties"][prop]["enum"]
    assert set(values) == db_enum(admin, *INLINE_ENUMS[(schema, prop)])


def test_letter_template_status_matches_rule_status(admin):
    assert db_enum(admin, "letter_template", "status") == db_enum(admin, "deadline_rule", "status")
