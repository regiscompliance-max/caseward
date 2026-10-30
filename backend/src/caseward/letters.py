"""Letter templates: a fixed set of merge fields filled from case data.

Facts, names and dates always come from the database. AI writes only the
`ai_summary` paragraph, and is never sent tenant names or contact details.
Anything that cannot be filled is left as a visible [[...]] marker, and a
letter cannot be approved while any remain.
"""

import re

FIELD = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
MARKER = re.compile(r"\[\[[^\]]+\]\]")

MERGE_FIELDS = {
    "today": "Today's date",
    "recipient_name": "Tenant's name, or the contractor's name",
    "property_address": "The property's address",
    "landlord_name": "The landlord's name",
    "case_reference": "The case reference",
    "hazard_category": "The hazard, e.g. Damp and mould growth",
    "aware_date": "When the landlord became aware",
    "investigation_date": "When the investigation was completed",
    "next_deadline_title": "The next open deadline's rule title",
    "next_deadline_date": "The next open deadline's due date",
    "ai_summary": "A plain-English summary of the case so far, drafted by AI for a person to check",
}


class TemplateError(ValueError):
    pass


def fields_in(body: str) -> set[str]:
    return set(FIELD.findall(body))


def check_template(body: str) -> None:
    unknown = fields_in(body) - set(MERGE_FIELDS)
    if unknown:
        raise TemplateError(f"unknown merge fields: {', '.join(sorted(unknown))}")
    if MARKER.search(body):
        raise TemplateError("templates cannot contain [[...]] markers")


def render(body: str, values: dict[str, str | None]) -> str:
    def fill(match: re.Match) -> str:
        value = values.get(match.group(1))
        return value if value else f"[[{match.group(1)}: {MERGE_FIELDS[match.group(1)]}]]"
    return FIELD.sub(fill, body)


def unresolved(body: str) -> list[str]:
    return MARKER.findall(body)


def uk_date(value) -> str | None:
    """'3 April 2026', the form used in letters."""
    return f"{value.day} {value:%B %Y}" if value else None
