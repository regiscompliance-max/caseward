"""Client API handlers. Operations not listed here return 501."""

from caseward.api.framework import Call, Result
from caseward.api.handlers import certificates, documents, letters, shared


def create_case(call: Call) -> Result:
    return shared.create_case(call, call.ctx.client_id)


HANDLERS = {
    "getMe": shared.get_me,
    "listProperties": shared.list_properties,
    "getProperty": shared.get_property,
    "createProperty": shared.create_property,
    "listCases": shared.list_cases,
    "getCase": shared.get_case,
    "createCase": create_case,
    "createCaseEvent": shared.create_case_event,
    "listDeadlines": shared.list_deadlines,
    "listHazardCategories": lambda call: shared.list_hazard_categories(call, enabled_only=True),
    "listEventTypes": lambda call: shared.list_event_types(call, enabled_only=True, subject_kind="case"),
    "createUpload": documents.create_upload,
    "createDocument": lambda call: documents.create_document(call, default_source="upload"),
    "listDocuments": documents.list_documents,
    "getDocument": documents.get_document,
    "downloadDocument": documents.download_document,
    "listInspections": certificates.list_inspections,
    "getInspection": certificates.get_inspection,
    "listDefects": certificates.list_defects,
    "getDefect": certificates.get_defect,
    "listLetters": letters.list_approved_letters,
    "getLetter": letters.get_approved_letter,
}
