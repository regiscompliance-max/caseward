"""Reports and evidence packs, as CSV or PDF.

Builders take a `rows(query, params)` function so they run under whatever
access the caller has. Nothing here includes special-category data.
"""

import csv
import io
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from caseward import timeline
from caseward.deadlines import UK
from caseward.letters import uk_date

Rows = Callable[[str, Any], list[dict]]


class ExportError(Exception):
    """The request cannot be met (for example the case does not exist). Not retried."""


# -- Queries ---------------------------------------------------------------------

def deadline_performance(rows: Rows, filters: dict) -> tuple[list[str], list[list]]:
    where, params = ["d.status <> 'cancelled'"], []
    if filters.get("from"):
        where.append("d.due_at >= (%s::date::timestamp at time zone 'Europe/London')")
        params.append(filters["from"])
    if filters.get("to"):
        where.append("d.due_at < ((%s::date + 1)::timestamp at time zone 'Europe/London')")
        params.append(filters["to"])
    if filters.get("hazard_category"):
        where.append("c.hazard_category = %s")
        params.append(filters["hazard_category"])
    if filters.get("contractor_client_id"):
        where.append("c.assigned_contractor_id = %s")
        params.append(filters["contractor_client_id"])
    if filters.get("property_id"):
        where.append("p.id = %s")
        params.append(filters["property_id"])
    data = rows(f"""
        select c.reference, h.name as hazard, c.severity, p.postcode, r.title as rule, d.rule_key, d.rule_version,
               d.clock_started_at, d.due_at, d.completed_at, d.status, con.name as contractor
        from caseward.deadline d
        join caseward.deadline_rule r on (r.rule_key, r.version) = (d.rule_key, d.rule_version)
        left join caseward.hazard_case c on c.id = d.case_id
        left join caseward.hazard_category h on h.code = c.hazard_category
        left join caseward.defect df on df.id = d.defect_id
        left join caseward.inspection i on i.id = d.inspection_id
        left join caseward.property p on p.id = coalesce(c.property_id, df.property_id, i.property_id)
        left join caseward.client con on con.id = c.assigned_contractor_id
        where {' and '.join(where)} order by d.due_at""", params)
    header = ["Case", "Hazard", "Severity", "Postcode", "Rule", "Rule version", "Clock started", "Due", "Completed",
              "Result", "Contractor"]
    return header, [[r["reference"], r["hazard"], r["severity"], r["postcode"], r["rule"],
                     f"{r['rule_key']} v{r['rule_version']}", _when(r["clock_started_at"]), _when(r["due_at"]),
                     _when(r["completed_at"]), r["status"], r["contractor"]] for r in data]


def certificate_register(rows: Rows, filters: dict) -> tuple[list[str], list[list]]:
    params = [filters["property_id"]] if filters.get("property_id") else []
    data = rows(f"""
        select distinct on (i.property_id, i.certificate_type)
               concat_ws(', ', p.address_line_1, p.town, p.postcode) as address, p.uprn, t.name as certificate,
               i.inspected_on, i.next_due_on, i.outcome,
               (select count(*) from caseward.defect f where f.inspection_id = i.id
                and f.status not in ('closed', 'not_required', 'verified')) as open_defects
        from caseward.inspection i
        join caseward.property p on p.id = i.property_id
        join caseward.certificate_type t on t.code = i.certificate_type
        {"where i.property_id = %s" if params else ""}
        order by i.property_id, i.certificate_type, i.inspected_on desc""", params)
    today = datetime.now(UK).date()

    def state(due):
        if due is None:
            return "No due date"
        if due < today:
            return "Expired"
        return "Due within 30 days" if (due - today).days <= 30 else "In date"

    header = ["Address", "UPRN", "Certificate", "Inspected", "Next due", "Status", "Outcome", "Open defects"]
    return header, sorted([[r["address"], r["uprn"], r["certificate"], uk_date(r["inspected_on"]),
                            uk_date(r["next_due_on"]), state(r["next_due_on"]), r["outcome"], r["open_defects"]]
                           for r in data], key=lambda row: (row[5] != "Expired", row[0] or ""))


def open_defects(rows: Rows, filters: dict) -> tuple[list[str], list[list]]:
    params = [filters["property_id"]] if filters.get("property_id") else []
    data = rows(f"""
        select concat_ws(', ', p.address_line_1, p.postcode) as address, f.code_scheme, f.code, f.description,
               f.location, f.status, f.identified_on
        from caseward.defect f join caseward.property p on p.id = f.property_id
        where f.status not in ('closed', 'not_required', 'verified') {"and f.property_id = %s" if params else ""}
        order by f.code, f.identified_on""", params)
    header = ["Address", "Code", "Description", "Location", "Status", "Identified"]
    return header, [[r["address"], f"{r['code_scheme']}:{r['code']}", r["description"], r["location"], r["status"],
                     uk_date(r["identified_on"])] for r in data]


def audit_log(rows: Rows, filters: dict, client_id) -> tuple[list[str], list[list]]:
    where, params = ["client_id = %s"], [client_id]
    if filters.get("from"):
        where.append("occurred_at >= (%s::date::timestamp at time zone 'Europe/London')")
        params.append(filters["from"])
    if filters.get("to"):
        where.append("occurred_at < ((%s::date + 1)::timestamp at time zone 'Europe/London')")
        params.append(filters["to"])
    data = rows(f"""select id, occurred_at, actor_kind, actor_user_id, action, entity_type, entity_id, request_id,
                           host(source_ip) as source_ip, array_to_string(changed_fields, ' ') as changed_fields
                    from caseward.audit_event where {' and '.join(where)} order by id""", params)
    header = ["Event", "When (UTC)", "Actor kind", "Actor", "Action", "Entity", "Entity id", "Request", "Source IP",
              "Changed fields"]
    return header, [[r["id"], r["occurred_at"].astimezone(UTC).isoformat(), r["actor_kind"], r["actor_user_id"],
                     r["action"], r["entity_type"], r["entity_id"], r["request_id"], r["source_ip"],
                     r["changed_fields"]] for r in data]


TABLES = {"deadline_performance": "Deadline performance", "certificate_register": "Certificate register",
          "open_defects": "Open defects", "audit_log": "Audit log"}


def _when(value) -> str | None:
    return value.astimezone(UK).strftime("%d/%m/%Y %H:%M") if value else None


# -- CSV -------------------------------------------------------------------------------

def _safe_cell(value) -> str:
    """Stop spreadsheets running a cell as a formula (CSV injection)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(header: list[str], data: list[list]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows([[_safe_cell(v) for v in row] for row in data])
    return ("﻿" + out.getvalue()).encode("utf-8")  # BOM so Excel reads UTF-8


# -- PDF -------------------------------------------------------------------------------

def _styles():
    from reportlab.lib.styles import getSampleStyleSheet
    styles = getSampleStyleSheet()
    styles["BodyText"].fontSize = 9
    styles["BodyText"].leading = 12
    return styles


def _table(header, data, styles):
    from reportlab.lib import colors
    from reportlab.platypus import LongTable, Paragraph, TableStyle
    cell = styles["BodyText"]
    body = [[Paragraph(f"<b>{_escape(h)}</b>", cell) for h in header]] + \
           [[Paragraph(_escape("" if v is None else str(v)), cell) for v in row] for row in data]
    table = LongTable(body, repeatRows=1)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return table


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render(story, landscape_page: bool, title: str) -> bytes:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate
    out = io.BytesIO()
    generated = datetime.now(UK).strftime("%d %B %Y %H:%M")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(36, 20, f"{title} · Caseward · generated {generated} UK time · page {doc.page}")
        canvas.restoreState()

    size = landscape(A4) if landscape_page else A4
    SimpleDocTemplate(out, pagesize=size, title=title, author="Caseward", leftMargin=36, rightMargin=36,
                      topMargin=36, bottomMargin=36).build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


def table_pdf(title: str, header, data, filters: dict) -> bytes:
    from reportlab.platypus import Paragraph, Spacer
    styles = _styles()
    applied = ", ".join(f"{k}: {v}" for k, v in filters.items()) or "none"
    story = [Paragraph(_escape(title), styles["Title"]),
             Paragraph(_escape(f"Filters: {applied}. {len(data)} rows."), styles["BodyText"]), Spacer(1, 8),
             _table(header, data, styles) if data else Paragraph("No records.", styles["BodyText"])]
    return _render(story, landscape_page=True, title=title)


# -- Evidence pack ---------------------------------------------------------------------------

MAX_PACK_PAGES = 200


def evidence_pack(rows: Rows, case_id, page_image: Callable[[str], bytes]) -> bytes:
    """The case for the Housing Ombudsman, the regulator or a court: summary,
    full timeline, approved letters, and every evidence document with its
    SHA-256 so its integrity can be checked against the original."""
    from PIL import Image
    from reportlab.lib.units import mm
    from reportlab.platypus import Image as PdfImage
    from reportlab.platypus import PageBreak, Paragraph, Spacer

    case = rows("""select c.reference, h.name as hazard, c.severity, c.status, c.aware_at, c.received_at,
                          c.awareness_source, concat_ws(', ', p.address_line_1, p.address_line_2, p.town, p.postcode)
                          as address, l.name as landlord
                   from caseward.hazard_case c
                   join caseward.hazard_category h on h.code = c.hazard_category
                   join caseward.property p on p.id = c.property_id
                   join caseward.client l on l.id = c.client_id where c.id = %s""", [case_id])
    if not case:
        raise ExportError("case not found")
    case = case[0]
    styles = _styles()
    title = f"Case evidence pack: {case['reference']}"
    story = [Paragraph(_escape(title), styles["Title"]), _table(["", ""], [
        ["Landlord", case["landlord"]], ["Property", case["address"]], ["Hazard", case["hazard"]],
        ["Severity", case["severity"]], ["Status", case["status"]],
        ["Landlord became aware", _when(case["aware_at"])], ["Report received", _when(case["received_at"])],
        ["Source", case["awareness_source"].replace("_", " ")]], styles), Spacer(1, 10)]

    entries = timeline.build(rows, case_id)
    story += [Paragraph("Timeline", styles["Heading2"]), _table(["When (UK)", "What", "Detail"], [
        [_when(e["at"]), e["summary"], _entry_detail(e)] for e in entries], styles)]

    letters = rows("""select l.id, l.status, l.channel, l.sent_at, l.approved_at, v.body, u.display_name as approver
                      from caseward.letter l
                      join caseward.letter_version v on (v.letter_id, v.version) = (l.id, l.approved_version)
                      left join caseward.app_user u on u.id = l.approved_by
                      where l.case_id = %s and l.status in ('approved', 'sent') order by l.approved_at""", [case_id])
    if letters:
        story += [PageBreak(), Paragraph("Letters", styles["Heading2"])]
        for letter in letters:
            sent = f"sent {_when(letter['sent_at'])} by {letter['channel']}" if letter["sent_at"] else "not yet sent"
            story += [Paragraph(_escape(f"Approved {_when(letter['approved_at'])} by {letter['approver'] or 'unknown'};"
                                        f" {sent}."), styles["Italic"]),
                      Paragraph(_escape(letter["body"]).replace("\n", "<br/>"), styles["BodyText"]), Spacer(1, 12)]

    document_ids = [e["document_id"] for e in entries if e["entry_type"] == "document"]
    document_ids += [e["event"]["evidence_document_id"] for e in entries
                     if e["entry_type"] == "event" and e["event"]["evidence_document_id"]]
    documents = rows("""select d.id, d.doc_type, d.original_filename, d.received_at, d.sha256, d.malware_scan
                        from caseward.document d where d.id = any(%s) order by d.received_at""",
                     [list(dict.fromkeys(document_ids))])
    if documents:
        story += [PageBreak(), Paragraph("Evidence documents", styles["Heading2"]),
                  _table(["Received", "Type", "File", "SHA-256"], [
                      [_when(d["received_at"]), d["doc_type"].replace("_", " "), d["original_filename"], d["sha256"]]
                      for d in documents], styles)]
        included = 0
        for doc in documents:
            if doc["malware_scan"] != "clean":
                continue
            for page in rows("select page_number, image_storage_key from caseward.document_page"
                             " where document_id = %s order by page_number", [doc["id"]]):
                if included >= MAX_PACK_PAGES:
                    break
                image = Image.open(io.BytesIO(page_image(page["image_storage_key"]))).convert("RGB")
                png = io.BytesIO()
                image.save(png, format="PNG")
                png.seek(0)
                width, height = 180 * mm, 180 * mm * image.height / image.width
                if height > 240 * mm:
                    width, height = width * 240 * mm / height, 240 * mm
                story += [PageBreak(), Paragraph(_escape(
                    f"{doc['original_filename'] or doc['doc_type']}, page {page['page_number']}"), styles["Italic"]),
                    PdfImage(png, width=width, height=height)]
                included += 1
        if included >= MAX_PACK_PAGES:
            story += [Paragraph(f"Only the first {MAX_PACK_PAGES} pages are included. The originals are held by "
                                "Caseward and can be supplied in full.", styles["BodyText"])]
    return _render(story, landscape_page=False, title=title)


def _entry_detail(entry: dict) -> str:
    if entry["entry_type"] == "event":
        e = entry["event"]
        parts = [e["channel"] and f"by {e['channel'].replace('_', ' ')}", e["notes"]]
        return "; ".join(p for p in parts if p)
    if entry["entry_type"] == "deadline":
        d = entry["deadline"]
        return f"{d['status']}" + (f", completed {_when(d['completed_at'])}" if d["completed_at"] else "")
    return ""


def evidence_pack_csv(rows: Rows, case_id) -> bytes:
    entries = timeline.build(rows, case_id)
    if not entries:
        raise ExportError("case not found")
    return to_csv(["When (UK)", "What", "Detail"], [[_when(e["at"]), e["summary"], _entry_detail(e)] for e in entries])
