"""Certificate extraction: versioned prompt and schema in, draft result and
review flags out.

The model's own confidence is only one input. Our checks below decide what
a case handler is shown and why. Every result is a draft until a person
approves it (robustness rules 1 and 2).
"""

import json
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from caseward.ai.provider import ModelProvider

HERE = Path(__file__).parent
PROMPT_ID = "certificate_extraction"
PROMPT_VERSION = "1"
# Certificate type -> output schema version. Only types listed here are extracted.
SCHEMAS = {"eicr": "0", "gas_safety": "0"}
CRITICAL_FIELDS = ("address_line_1", "postcode", "inspection_date", "next_inspection_due", "outcome", "defects")
CONFIDENCE_THRESHOLD = 0.9
# Codes that mean an unsatisfactory result.
UNSATISFACTORY_CODES = {"eicr": {"C1", "C2", "FI"}, "gas_safety": {"ID", "AR"}}
MAX_TEXT_CHARS = 200_000


@dataclass
class Extracted:
    result: dict[str, Any]
    field_confidence: dict[str, float]
    flags: list[dict[str, str]] = field(default_factory=list)
    provider: str = ""
    model_version: str = ""
    prompt_id: str = PROMPT_ID
    prompt_version: str = PROMPT_VERSION
    output_schema_version: str = ""


@cache
def prompt() -> str:
    return (HERE / "prompts" / f"{PROMPT_ID}.v{PROMPT_VERSION}.txt").read_text()


@cache
def output_schema(certificate_type: str) -> dict[str, Any]:
    schema = json.loads((HERE / "schemas" / f"{certificate_type}.v{SCHEMAS[certificate_type]}.json").read_text())
    schema.pop("$comment", None)
    return schema


def _for_provider(node: Any) -> Any:
    """Strict-mode schemas on some providers reject validation keywords. We check
    those ourselves (validate_result), so leave them out of what we send."""
    if isinstance(node, dict):
        return {k: _for_provider(v) for k, v in node.items() if k not in {"format", "pattern", "minimum", "maximum"}}
    if isinstance(node, list):
        return [_for_provider(v) for v in node]
    return node


def response_schema(certificate_type: str) -> dict[str, Any]:
    """What the model must return: the fields, and a confidence for each top-level field."""
    fields = _for_provider(output_schema(certificate_type))
    names = list(fields["properties"])
    return {
        "type": "object", "additionalProperties": False, "required": ["fields", "confidence"],
        "properties": {
            "fields": fields,
            "confidence": {"type": "object", "additionalProperties": False, "required": names,
                           "properties": {n: {"type": "number"} for n in names}},
        },
    }


def validate_result(certificate_type: str, result: Any) -> list[str]:
    validator = Draft202012Validator(output_schema(certificate_type), format_checker=FormatChecker())
    return [f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in validator.iter_errors(result)]


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def review_flags(certificate_type: str, result: dict[str, Any], confidence: dict[str, float],
                 today: date, property_postcode: str | None = None) -> list[dict[str, str]]:
    flags = []

    def flag(field_name, reason, detail):
        flags.append({"field": f"/{field_name}", "reason": reason, "detail": detail})

    for name in CRITICAL_FIELDS:
        if result.get(name) is None:
            flag(name, "missing_required", "Not found on the certificate.")
    for name, value in confidence.items():
        if value < CONFIDENCE_THRESHOLD:
            flag(name, "low_confidence", f"Model confidence {value:.2f} is below {CONFIDENCE_THRESHOLD}.")

    inspected, next_due = _date(result.get("inspection_date")), _date(result.get("next_inspection_due"))
    for name in ("inspection_date", "next_inspection_due"):
        if result.get(name) and _date(result[name]) is None:
            flag(name, "invalid_value", f"{result[name]!r} is not a real date.")
    if inspected and inspected > today:
        flag("inspection_date", "date_out_of_range", "The inspection date is in the future.")
    if inspected and (today - inspected).days > 10 * 366:
        flag("inspection_date", "date_out_of_range", "The inspection is more than 10 years old.")
    if inspected and next_due and next_due <= inspected:
        flag("next_inspection_due", "date_out_of_range", "The next inspection is due on or before this one.")

    codes = {d["code"] for d in result.get("defects") or []}
    bad = codes & UNSATISFACTORY_CODES.get(certificate_type, set())
    if result.get("outcome") == "satisfactory" and bad:
        flag("outcome", "contradiction", f"Outcome \"Satisfactory\" with {', '.join(sorted(bad))} present.")
    if result.get("outcome") == "unsatisfactory" and not bad:
        flag("outcome", "contradiction", "Outcome \"Unsatisfactory\" but no code that makes it so.")

    def norm(postcode):
        return "".join(postcode.split()).upper() if postcode else None
    if property_postcode and result.get("postcode") and norm(result["postcode"]) != norm(property_postcode):
        flag("postcode", "contradiction",
             f"Certificate postcode {result['postcode']} does not match the property ({property_postcode}).")
    return flags


def extract(provider: ModelProvider, certificate_type: str, certificate_type_name: str, page_texts: list[str],
            today: date, property_postcode: str | None = None) -> Extracted:
    text = "\n\n".join(f"--- Page {n} ---\n{t}" for n, t in enumerate(page_texts, start=1))[:MAX_TEXT_CHARS]
    completion = provider.complete_json(
        system=prompt().format(certificate_type_name=certificate_type_name),
        user=text,
        schema_name=f"{certificate_type}_v{SCHEMAS[certificate_type]}",
        schema=response_schema(certificate_type),
    )
    result = completion.content.get("fields")
    confidence = completion.content.get("confidence") or {}
    errors = validate_result(certificate_type, result) if isinstance(result, dict) else ["/: not an object"]
    if errors:
        # Keep nothing that fails the schema: the reviewer works from the page instead.
        flags = [{"field": "/", "reason": "invalid_value", "detail": f"Model output failed validation: {errors[0]}"}]
        result, confidence = {}, {}
    else:
        flags = review_flags(certificate_type, result, confidence, today, property_postcode)
    return Extracted(result=result, field_confidence=confidence, flags=flags, provider=completion.provider,
                     model_version=completion.model_version, output_schema_version=SCHEMAS[certificate_type])
