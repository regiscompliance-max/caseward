"""The draft_letter job: fill a template from case data, with an AI summary
paragraph when an approved AI provider is configured. The draft then waits
for a person to edit and approve it (robustness rule 1)."""

from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from caseward import letters
from caseward.deadlines import UK
from caseward.services import Services

PROMPT_ID = "letter_summary"
PROMPT_VERSION = "1"
SUMMARY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["summary"],
                  "properties": {"summary": {"type": "string"}}}
APPROVAL_SLA = timedelta(hours=24)  # UNVERIFIED: to be set by the product lead.


@cache
def prompt() -> str:
    return (Path(__file__).parents[1] / "ai" / "prompts" / f"{PROMPT_ID}.v{PROMPT_VERSION}.txt").read_text()


def _facts(cur, letter: dict) -> tuple[dict[str, str | None], str | None]:
    """Merge values, and the facts for the AI summary (no names or contact details)."""
    values = {"today": letters.uk_date(datetime.now(UK).date())}
    landlord = cur.execute("select name from caseward.client where id = %s", [letter["client_id"]]).fetchone()
    values["landlord_name"] = landlord["name"]
    if letter["recipient_kind"] == "tenant":
        values["recipient_name"] = cur.execute("select full_name from caseward.tenant_contact where id = %s",
                                               [letter["recipient_contact_id"]]).fetchone()["full_name"]
    else:
        values["recipient_name"] = cur.execute("select name from caseward.client where id = %s",
                                               [letter["recipient_client_id"]]).fetchone()["name"]
    if letter["case_id"] is None:
        return values, None

    case = cur.execute(
        """select c.reference, h.name as hazard, c.severity, c.aware_at, c.status,
                  concat_ws(', ', p.address_line_1, p.address_line_2, p.town, p.postcode) as address
           from caseward.hazard_case c
           join caseward.hazard_category h on h.code = c.hazard_category
           join caseward.property p on p.id = c.property_id where c.id = %s""", [letter["case_id"]]).fetchone()
    investigated = cur.execute(
        "select min(occurred_at) as at from caseward.case_event where case_id = %s"
        " and kind = 'investigation_completed' and corrects_event_id is null", [letter["case_id"]]).fetchone()["at"]
    deadline = cur.execute(
        """select r.title, d.due_at from caseward.deadline d
           join caseward.deadline_rule r on (r.rule_key, r.version) = (d.rule_key, d.rule_version)
           where d.case_id = %s and d.completed_at is null and d.status in ('open', 'missed')
           order by d.due_at limit 1""", [letter["case_id"]]).fetchone()
    values.update(
        property_address=case["address"], case_reference=case["reference"], hazard_category=case["hazard"],
        aware_date=letters.uk_date(case["aware_at"].astimezone(UK).date()),
        investigation_date=letters.uk_date(investigated.astimezone(UK).date()) if investigated else None,
        next_deadline_title=deadline["title"] if deadline else None,
        next_deadline_date=letters.uk_date(deadline["due_at"].astimezone(UK).date()) if deadline else None,
    )

    events = cur.execute(
        """select t.name, e.occurred_at, e.notes from caseward.case_event e
           join caseward.event_type t on (t.code, t.subject_kind) = (e.kind, e.subject_kind)
           where e.case_id = %s and e.corrects_event_id is null order by e.occurred_at""",
        [letter["case_id"]]).fetchall()
    lines = [f"Hazard: {case['hazard']} ({case['severity']})", f"Case status: {case['status']}",
             "Events so far:"]
    lines += [f"- {letters.uk_date(e['occurred_at'].astimezone(UK).date())}: {e['name']}"
              + (f". Notes: {e['notes']}" if e["notes"] else "") for e in events]
    if deadline:
        lines.append(f"Next step: {deadline['title']}, due by {values['next_deadline_date']}")
    return values, "\n".join(lines)


def draft_letter(services: Services):
    def handler(conn: psycopg.Connection, job: dict[str, Any]):
        cur = conn.cursor(row_factory=dict_row)
        letter = cur.execute(
            """select l.*, t.body as template_body from caseward.letter l
               join caseward.letter_template t on t.id = l.template_id where l.id = %s""",
            [job["payload"]["letter_id"]]).fetchone()
        if letter is None:
            raise LookupError(f"letter {job['payload']['letter_id']} not found")
        if letter["status"] != "drafting":
            return None

        values, facts = _facts(cur, letter)
        origin, model = "template", {"model_provider": None, "model_version": None, "prompt_id": None,
                                     "prompt_version": None}
        wants_summary = "ai_summary" in letters.fields_in(letter["template_body"])
        if wants_summary and facts and job["payload"].get("ai_draft") and services.ai is not None:
            completion = services.ai.complete_json(prompt(), facts, "letter_summary_v1", SUMMARY_SCHEMA)
            summary = completion.content.get("summary")
            if isinstance(summary, str) and summary.strip():
                values["ai_summary"] = summary.strip()
                origin = "ai_draft"
                model = {"model_provider": completion.provider, "model_version": completion.model_version,
                         "prompt_id": PROMPT_ID, "prompt_version": PROMPT_VERSION}

        cur.execute(
            """insert into caseward.letter_version (letter_id, client_id, version, body, origin, model_provider,
                 model_version, prompt_id, prompt_version)
               values (%s, %s, 1, %s, %s, %s, %s, %s, %s)""",
            [letter["id"], letter["client_id"], letters.render(letter["template_body"], values), origin,
             model["model_provider"], model["model_version"], model["prompt_id"], model["prompt_version"]])
        cur.execute("update caseward.letter set status = 'draft_ready' where id = %s", [letter["id"]])
        cur.execute("insert into caseward.task (client_id, kind, letter_id, priority, sla_due_at)"
                    " values (%s, 'approve_letter', %s, 2, %s)",
                    [letter["client_id"], letter["id"], datetime.now(UTC) + APPROVAL_SLA])
        return letter["id"]
    return handler
