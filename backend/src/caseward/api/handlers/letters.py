"""Letters: tenant letters and contractor instructions.

Drafted from an approved template (and, where configured, an AI summary
paragraph), edited by case handlers, approved by someone other than the
version's author, then recorded as sent. Clients only ever see approved or
sent letters.
"""

from psycopg.types.json import Jsonb

from caseward import letters as templates
from caseward.api.framework import Call, Result
from caseward.api.handlers.shared import decode_cursor, limit_plus_one, page
from caseward.api.problems import Problem, not_found

LETTER_COLUMNS = """l.id, l.client_id, l.case_id, l.template_id, l.recipient_kind, l.recipient_contact_id,
  l.recipient_client_id, l.channel, l.status, l.approved_version, l.approved_by, l.approved_at, l.sent_at,
  l.delivery_evidence_document_id"""
CHANNEL_TO_EVENT = {"post": "letter", "email": "email", "portal": "portal"}


def _letter(call: Call, letter_id, lock: bool = False) -> dict:
    row = call.row(f"select {LETTER_COLUMNS} from caseward.letter l where l.id = %s" + (" for update" if lock else ""),
                   [letter_id])
    if row is None:
        raise not_found()
    return row


def _versions(call: Call, letter_id) -> list[dict]:
    return call.rows("select version, body, origin, model_provider, model_version, prompt_id, prompt_version,"
                     " created_by, created_at from caseward.letter_version where letter_id = %s order by version",
                     [letter_id])


def _full(call: Call, letter_id) -> Result:
    row = _letter(call, letter_id)
    row["versions"] = _versions(call, letter_id)
    return Result(200, row, audit_client_id=row["client_id"])


def create_letter(call: Call) -> Result:
    b, client_id = call.body, call.query["client_id"]
    template = call.row("select id, client_id, status from caseward.letter_template where id = %s", [b["template_id"]])
    if template is None or template["client_id"] not in (None, client_id):
        raise not_found()
    if template["status"] != "approved":
        raise Problem(422, "template_not_approved", "Only approved templates can be used")
    if b["recipient_kind"] == "tenant":
        if not b.get("recipient_contact_id") or b.get("recipient_client_id"):
            raise Problem(422, "invalid_recipient", "A tenant letter needs recipient_contact_id only")
    else:
        if not b.get("recipient_client_id") or b.get("recipient_contact_id"):
            raise Problem(422, "invalid_recipient", "A contractor instruction needs recipient_client_id only")
        engaged = call.row("select 1 from caseward.engagement where landlord_client_id = %s and provider_client_id = %s"
                           " and starts_on <= current_date and (ends_on is null or ends_on >= current_date)",
                           [client_id, b["recipient_client_id"]])
        if engaged is None:
            raise Problem(422, "recipient_not_engaged", "That contractor is not currently engaged by this client")
    letter_id = call.row(
        "insert into caseward.letter (client_id, case_id, template_id, recipient_kind, recipient_contact_id,"
        " recipient_client_id, channel) values (%s, %s, %s, %s, %s, %s, %s) returning id",
        [client_id, b.get("case_id"), b["template_id"], b["recipient_kind"], b.get("recipient_contact_id"),
         b.get("recipient_client_id"), b["channel"]])["id"]
    call.conn.execute(
        "insert into caseward.job (client_id, kind, payload, idempotency_key) values (%s, 'draft_letter', %s, %s)",
        [client_id, Jsonb({"letter_id": str(letter_id), "ai_draft": b.get("ai_draft", True)}),
         f"draft_letter:{letter_id}"])
    result = _full(call, letter_id)
    result.status = 202
    return result


def get_letter(call: Call) -> Result:
    return _full(call, call.path["id"])


def create_letter_version(call: Call) -> Result:
    letter = _letter(call, call.path["id"], lock=True)
    if letter["status"] != "draft_ready":
        raise Problem(409, "not_editable", f"The letter is {letter['status']}")
    latest = call.row("select max(version) as v from caseward.letter_version where letter_id = %s", [letter["id"]])["v"]
    if call.body["based_on_version"] != latest:
        raise Problem(409, "version_conflict", f"Version {latest} was saved since; reload and try again")
    row = call.row(
        "insert into caseward.letter_version (letter_id, client_id, version, body, origin, created_by)"
        " values (%s, %s, %s, %s, 'human_edit', %s)"
        " returning version, body, origin, model_provider, model_version, prompt_id, prompt_version, created_by,"
        " created_at",
        [letter["id"], letter["client_id"], latest + 1, call.body["body"], call.principal.user_id])
    return Result(201, row, audit_client_id=letter["client_id"])


def approve_letter(call: Call) -> Result:
    letter = _letter(call, call.path["id"], lock=True)
    if letter["status"] != "draft_ready":
        raise Problem(409, "not_approvable", f"The letter is {letter['status']}")
    version = call.row("select version, body from caseward.letter_version where letter_id = %s"
                       " order by version desc limit 1", [letter["id"]])
    if call.body["version"] != version["version"]:
        raise Problem(422, "not_latest", f"Only the latest version ({version['version']}) can be approved")
    gaps = templates.unresolved(version["body"])
    if gaps:
        raise Problem(422, "unresolved_placeholders", "Fill in every [[...]] before approving", detail=", ".join(gaps))
    call.conn.execute("update caseward.letter set status = 'approved', approved_version = %s, approved_by = %s,"
                      " approved_at = now() where id = %s", [version["version"], call.principal.user_id, letter["id"]])
    call.conn.execute("update caseward.task set status = 'done', completed_at = now(),"
                      " started_at = coalesce(started_at, now()), assignee_id = coalesce(assignee_id, %s)"
                      " where letter_id = %s and kind = 'approve_letter' and status in ('queued', 'in_progress', 'blocked')",
                      [call.principal.user_id, letter["id"]])
    return _full(call, letter["id"])


def mark_letter_sent(call: Call) -> Result:
    letter = _letter(call, call.path["id"], lock=True)
    if letter["status"] != "approved":
        raise Problem(422, "not_approved", "Only an approved letter can be sent")
    call.conn.execute("update caseward.letter set status = 'sent', sent_at = %s, delivery_evidence_document_id = %s"
                      " where id = %s", [call.body["sent_at"], call.body.get("delivery_evidence_document_id"),
                                         letter["id"]])
    kind = call.row("select case_event_kind from caseward.letter_template where id = %s",
                    [letter["template_id"]])["case_event_kind"]
    if kind and letter["case_id"]:
        # May meet a deadline, e.g. the written summary to the tenant.
        call.conn.execute(
            "insert into caseward.case_event (client_id, case_id, kind, occurred_at, channel, notes,"
            " evidence_document_id, recorded_by) values (%s, %s, %s, %s, %s, %s, %s, %s)",
            [letter["client_id"], letter["case_id"], kind, call.body["sent_at"], CHANNEL_TO_EVENT[letter["channel"]],
             f"Letter {letter['id']} sent", call.body.get("delivery_evidence_document_id"), call.principal.user_id])
    return _full(call, letter["id"])


def void_letter(call: Call) -> Result:
    letter = _letter(call, call.path["id"], lock=True)
    if letter["status"] in ("sent", "void"):
        raise Problem(422, "not_voidable", f"The letter is already {letter['status']}")
    call.conn.execute("update caseward.letter set status = 'void' where id = %s", [letter["id"]])
    return _full(call, letter["id"])


# -- Client view: approved and sent letters only ------------------------------

APPROVED_SELECT = """
  select l.id, l.case_id, l.status, l.recipient_kind, l.channel, t.language, t.variant, v.body as approved_text,
         l.approved_at, l.sent_at, l.client_id as _client_id
  from caseward.letter l
  join caseward.letter_template t on t.id = l.template_id
  join caseward.letter_version v on (v.letter_id, v.version) = (l.id, l.approved_version)
  where l.status in ('approved', 'sent')
"""


def list_approved_letters(call: Call) -> Result:
    where, params = [], []
    if "case_id" in call.query:
        where.append("l.case_id = %s")
        params.append(call.query["case_id"])
    cursor = decode_cursor(call)
    if cursor:
        where.append("(l.approved_at, l.id) < (%s::timestamptz, %s::uuid)")
        params += cursor
    rows = call.rows(APPROVED_SELECT + "".join(f" and {w}" for w in where)
                     + " order by l.approved_at desc, l.id desc limit %s", params + [limit_plus_one(call)])
    result = page(call, rows, ["approved_at", "id"])
    result["items"] = [{k: v for k, v in r.items() if not k.startswith("_")} for r in result["items"]]
    return Result(200, result)


def get_approved_letter(call: Call) -> Result:
    row = call.row(APPROVED_SELECT + " and l.id = %s", [call.path["id"]])
    if row is None:
        raise not_found()
    client_id = row.pop("_client_id")
    return Result(200, row, audit_client_id=client_id)


# -- Templates ------------------------------------------------------------------

TEMPLATE_COLUMNS = """id, client_id, rule_pack, template_key, language, variant, version, title, body, status,
  approved_by, approved_at, created_by, case_event_kind"""


def list_letter_templates(call: Call) -> Result:
    where, params = ["true"], []
    for name in ("client_id", "status"):
        if name in call.query:
            where.append(f"{name} = %s")
            params.append(call.query[name])
    rows = call.rows(f"select {TEMPLATE_COLUMNS} from caseward.letter_template where {' and '.join(where)}"
                     " order by template_key, language, variant, version", params)
    return Result(200, {"items": rows})


def create_letter_template(call: Call) -> Result:
    b = call.body
    try:
        templates.check_template(b["body"])
    except templates.TemplateError as exc:
        raise Problem(422, "invalid_template", "The template is not valid", detail=str(exc)) from None
    language, variant = b.get("language", "en-GB"), b.get("variant", "standard")
    row = call.row(
        f"""insert into caseward.letter_template (client_id, rule_pack, template_key, language, variant, version,
              title, body, created_by, case_event_kind)
            select %s, %s, %s, %s, %s, coalesce(max(version), 0) + 1, %s, %s, %s, %s
            from caseward.letter_template
            where client_id is not distinct from %s and template_key = %s and language = %s and variant = %s
            returning {TEMPLATE_COLUMNS}""",
        [b.get("client_id"), b.get("rule_pack"), b["template_key"], language, variant, b["title"], b["body"],
         call.principal.user_id, b.get("case_event_kind"), b.get("client_id"), b["template_key"], language, variant])
    return Result(201, row)


def approve_letter_template(call: Call) -> Result:
    template = call.row("select status, created_by from caseward.letter_template where id = %s for update",
                        [call.path["id"]])
    if template is None:
        raise not_found()
    if template["status"] != "draft":
        raise Problem(422, "immutable", f"The template is already {template['status']}")
    if template["created_by"] == call.principal.user_id:
        raise Problem(422, "self_approval", "A template must be approved by someone other than its author")
    row = call.row(f"update caseward.letter_template set status = 'approved', approved_by = %s, approved_at = now()"
                   f" where id = %s returning {TEMPLATE_COLUMNS}", [call.principal.user_id, call.path["id"]])
    return Result(200, row)
