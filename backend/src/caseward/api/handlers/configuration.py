"""Configuration a compliance lead maintains: rule packs, deadline rules,
bank holiday calendars and the code lists rule packs are made of.

Legal rules go live only by a compliance lead's approval, with the qualified
person's confirmation recorded, from a date no earlier than today. Running
deadlines keep the rule version they were created under.
"""

from datetime import datetime

from caseward.api.framework import Call, Result
from caseward.api.problems import Problem, not_found
from caseward.bank_holidays import REGION_FOR_JURISDICTION, REGIONS, latest_calendar, load_calendar, parse_govuk, \
    publish_calendar
from caseward.deadlines import CALCULATOR_VERSION, UK, DeadlineRule, compute_due_at

RULE_COLUMNS = """id, rule_pack, rule_key, version, title, description, legal_reference, jurisdiction, subject_kind,
  applies_to, trigger_event, completion_event, duration, duration_unit, day_type, status, authored_by, approved_by,
  approved_at, effective_from, effective_to, approval_reference, status = 'approved' as verified"""
EDITABLE = ("title", "description", "legal_reference", "applies_to", "trigger_event", "completion_event", "duration",
            "duration_unit", "day_type")


def _if_match(call: Call, current: str) -> None:
    if call.headers["If-Match"].strip().strip('"') != current:
        raise Problem(412, "stale", "This changed since you loaded it; reload and try again")


def _today():
    return datetime.now(UK).date()


# -- Rule packs -------------------------------------------------------------------

def list_rule_packs(call: Call) -> Result:
    return Result(200, {"items": call.rows("select code, name, status, approved_by, approved_at, approval_reference"
                                           " from caseward.rule_pack order by name")})


def activate_rule_pack(call: Call) -> Result:
    pack = call.row("select code, status from caseward.rule_pack where code = %s for update", [call.path["code"]])
    if pack is None:
        raise not_found()
    unapproved = [r["rule_key"] for r in call.rows(
        """select rule_key from caseward.deadline_rule where rule_pack = %s group by rule_key
           having bool_and(status = 'draft')""", [pack["code"]])]
    if unapproved:
        raise Problem(422, "rules_not_approved", "Every deadline rule in the pack needs an approved version first",
                      detail=", ".join(sorted(unapproved)))
    row = call.row("update caseward.rule_pack set status = 'live', approved_by = %s, approved_at = now(),"
                   " approval_reference = %s where code = %s"
                   " returning code, name, status, approved_by, approved_at, approval_reference",
                   [call.principal.user_id, call.body["confirmation"], pack["code"]])
    return Result(200, row)


def list_client_rule_packs(call: Call) -> Result:
    if call.row("select 1 from caseward.client where id = %s", [call.path["id"]]) is None:
        raise not_found()
    return Result(200, {"items": call.rows("select rule_pack, enabled_from, enabled_to from caseward.client_rule_pack"
                                           " where client_id = %s order by rule_pack, enabled_from",
                                           [call.path["id"]])})


def enable_client_rule_pack(call: Call) -> Result:
    b = call.body
    if call.row("select 1 from caseward.client where id = %s and kind = 'landlord'", [call.path["id"]]) is None:
        raise not_found()
    row = call.row("insert into caseward.client_rule_pack (client_id, rule_pack, enabled_from, enabled_to)"
                   " values (%s, %s, %s, %s) returning rule_pack, enabled_from, enabled_to",
                   [call.path["id"], b["rule_pack"], b["enabled_from"], b.get("enabled_to")])
    return Result(201, row)


# -- Deadline rules ------------------------------------------------------------------

def _rule(call: Call, rule_id, lock: bool = False) -> dict:
    row = call.row(f"select {RULE_COLUMNS}, xmin::text as _etag from caseward.deadline_rule where id = %s"
                   + (" for update" if lock else ""), [rule_id])
    if row is None:
        raise not_found()
    return row


def _rule_result(row: dict, status: int = 200) -> Result:
    etag = row.pop("_etag", None)
    return Result(status, row, headers={"ETag": f'"{etag}"'} if etag else {})


def list_deadline_rules(call: Call) -> Result:
    where, params = ["true"], []
    for name in ("rule_pack", "status", "rule_key"):
        if name in call.query:
            where.append(f"{name} = %s")
            params.append(call.query[name])
    return Result(200, {"items": call.rows(f"select {RULE_COLUMNS} from caseward.deadline_rule"
                                           f" where {' and '.join(where)} order by rule_key, version", params)})


def get_deadline_rule(call: Call) -> Result:
    return _rule_result(_rule(call, call.path["id"]))


def create_deadline_rule(call: Call) -> Result:
    b = call.body
    if (b["duration_unit"] == "days") != (b.get("day_type") is not None):
        raise Problem(422, "rule_violation", "day_type is required for day durations, and only for them")
    row = call.row(
        f"""insert into caseward.deadline_rule (rule_pack, rule_key, version, title, description, legal_reference,
              jurisdiction, subject_kind, applies_to, trigger_event, completion_event, duration, duration_unit,
              day_type, authored_by)
            select %s, %s, coalesce(max(version), 0) + 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            from caseward.deadline_rule where rule_key = %s
            returning {RULE_COLUMNS}, xmin::text as _etag""",
        [b["rule_pack"], b["rule_key"], b["title"], b["description"], b.get("legal_reference"), b["jurisdiction"],
         b["subject_kind"], b["applies_to"], b["trigger_event"], b["completion_event"], b["duration"],
         b["duration_unit"], b.get("day_type"), call.principal.user_id, b["rule_key"]])
    return _rule_result(row, 201)


def update_deadline_rule(call: Call) -> Result:
    rule = _rule(call, call.path["id"], lock=True)
    _if_match(call, rule["_etag"])
    if rule["status"] != "draft":
        raise Problem(422, "immutable", "Approved rules are frozen; draft a new version instead")
    changes = {k: v for k, v in call.body.items() if k in EDITABLE}
    if changes.get("duration_unit", rule["duration_unit"]) != "days":
        changes.setdefault("day_type", None)
    sets = ", ".join(f"{k} = %s" for k in changes)
    call.conn.execute(f"update caseward.deadline_rule set {sets} where id = %s", [*changes.values(), rule["id"]])
    return _rule_result(_rule(call, rule["id"]))


def approve_deadline_rule(call: Call) -> Result:
    b = call.body
    rule = _rule(call, call.path["id"], lock=True)
    if rule["status"] != "draft":
        raise Problem(409, "not_draft", f"Version {rule['version']} is already {rule['status']}")
    if rule["authored_by"] == call.principal.user_id:
        raise Problem(422, "self_approval", "A rule must be approved by someone other than its author")
    effective_from = datetime.fromisoformat(b["effective_from"]).date() if isinstance(b["effective_from"], str) \
        else b["effective_from"]
    if effective_from < _today():
        raise Problem(422, "effective_date_in_past", "Rules take effect from today or later, never backdated")

    current = call.row("select id, version, effective_from from caseward.deadline_rule where rule_key = %s"
                       " and status = 'approved' and effective_to is null for update", [rule["rule_key"]])
    if current and b.get("supersedes_version") != current["version"]:
        raise Problem(422, "supersedes_required",
                      f"Version {current['version']} is in force: name it in supersedes_version to replace it")
    if current:
        if effective_from <= current["effective_from"]:
            raise Problem(422, "rule_violation", "The new version must start after the one it replaces")
        call.conn.execute("update caseward.deadline_rule set effective_to = %s where id = %s",
                          [effective_from, current["id"]])
    call.conn.execute("update caseward.deadline_rule set status = 'approved', approved_by = %s, approved_at = now(),"
                      " effective_from = %s, approval_reference = %s where id = %s",
                      [call.principal.user_id, effective_from, b["confirmation"], rule["id"]])
    return _rule_result(_rule(call, rule["id"]))


def retire_deadline_rule(call: Call) -> Result:
    rule = _rule(call, call.path["id"], lock=True)
    if rule["status"] != "approved":
        raise Problem(422, "not_approved", f"Version {rule['version']} is {rule['status']}")
    effective_to = datetime.fromisoformat(call.body["effective_to"]).date()
    if effective_to < _today() or effective_to <= rule["effective_from"]:
        raise Problem(422, "rule_violation", "Retire from today or later, and after the rule started")
    call.conn.execute("update caseward.deadline_rule set status = 'retired', effective_to = %s where id = %s",
                      [effective_to, rule["id"]])
    return _rule_result(_rule(call, rule["id"]))


def dry_run_deadline_rule(call: Call) -> Result:
    rule = _rule(call, call.path["id"])
    clock = datetime.fromisoformat(call.body["clock_started_at"])
    jurisdiction = call.body.get("jurisdiction", rule["jurisdiction"])
    calendar_id, holidays = None, frozenset()
    if rule["day_type"] == "working":
        calendar = latest_calendar(call.conn, REGION_FOR_JURISDICTION[jurisdiction])
        if calendar is None:
            raise Problem(422, "calendar_missing", "No bank holiday calendar for this jurisdiction")
        calendar_id, covered_to = calendar
        holidays = load_calendar(call.conn, calendar_id)
    due = compute_due_at(DeadlineRule(rule["rule_key"], rule["version"], rule["duration"], rule["duration_unit"],
                                      rule["day_type"]), clock, holidays)
    if calendar_id and due.astimezone(UK).date() > covered_to:
        raise Problem(422, "calendar_missing", f"The bank holiday calendar ends {covered_to}")
    return Result(200, {"due_at": due, "due_local_date": due.astimezone(UK).date(), "holiday_calendar_id": calendar_id,
                        "calculator_version": CALCULATOR_VERSION})


# -- Bank holidays -------------------------------------------------------------------

CALENDAR_SELECT = """
  select c.id, c.region, c.version, c.source, c.published_at, count(h.*) as holiday_count,
         min(h.holiday_date) as first_date, max(h.holiday_date) as last_date
  from caseward.holiday_calendar c left join caseward.bank_holiday h on h.calendar_id = c.id
"""


def list_holiday_calendars(call: Call) -> Result:
    return Result(200, {"items": call.rows(CALENDAR_SELECT + " group by c.id order by c.region, c.version")})


def import_holiday_calendars(call: Call) -> Result:
    payload = call.services.bank_holidays()
    created = []
    for region in REGIONS:
        holidays = parse_govuk(payload, region)
        latest = latest_calendar(call.conn, region)
        if latest:
            existing = sorted(call.conn.execute("select holiday_date, title from caseward.bank_holiday"
                                                " where calendar_id = %s", [latest[0]]).fetchall())
            if existing == holidays:
                continue
        created.append(publish_calendar(call.conn, region, holidays, "https://www.gov.uk/bank-holidays.json"))
    rows = call.rows(CALENDAR_SELECT + " where c.id = any(%s) group by c.id order by c.region", [created]) if created \
        else []
    return Result(200, {"items": rows})


# -- Code lists ---------------------------------------------------------------------

def update_hazard_category(call: Call) -> Result:
    current = call.row("select xmin::text as v from caseward.hazard_category where code = %s for update",
                       [call.path["code"]])
    if current is None:
        raise not_found()
    _if_match(call, current["v"])
    changes = {k: call.body[k] for k in ("phase", "in_scope_from") if k in call.body}
    call.conn.execute(f"update caseward.hazard_category set {', '.join(f'{k} = %s' for k in changes)} where code = %s",
                      [*changes.values(), call.path["code"]])
    return Result(200, call.row("select code, name, rule_pack, phase, in_scope_from, xmin::text as row_version"
                                " from caseward.hazard_category where code = %s", [call.path["code"]]))


def _list(table: str, columns: str, order: str):
    def handler(call: Call) -> Result:
        return Result(200, {"items": call.rows(f"select {columns} from caseward.{table} order by {order}")})
    return handler


def _create(table: str, columns: tuple[str, ...]):
    def handler(call: Call) -> Result:
        values = [call.body.get(c) for c in columns]
        row = call.row(f"insert into caseward.{table} ({', '.join(columns)}) values ({', '.join(['%s'] * len(columns))})"
                       f" returning {', '.join(columns)}", values)
        return Result(201, row)
    return handler


list_certificate_types = _list("certificate_type", "code, rule_pack, name", "name")
create_certificate_type = _create("certificate_type", ("code", "rule_pack", "name"))
list_defect_codes = _list("defect_code", "scheme, code, name, rule_pack", "scheme, code")
create_defect_code = _create("defect_code", ("scheme", "code", "name", "rule_pack"))
create_event_type = _create("event_type", ("code", "subject_kind", "name", "is_excuse_evidence", "rule_pack"))
list_vulnerability_factor_types = _list("vulnerability_factor_type", "code, name, rule_pack", "name")
create_vulnerability_factor_type = _create("vulnerability_factor_type", ("code", "name", "rule_pack"))


def client_certificate_types(call: Call) -> Result:
    return Result(200, {"items": call.rows("select code, rule_pack, name from caseward.certificate_type order by name"),
                        "defect_codes": call.rows("select scheme, code, name, rule_pack from caseward.defect_code"
                                                  " order by scheme, code")})
