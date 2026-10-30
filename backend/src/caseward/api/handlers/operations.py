"""Operations API handlers. Operations not listed here return 501."""

from caseward.api.framework import Call, Result
from caseward.api.handlers import documents, extractions, letters, shared


def create_case(call: Call) -> Result:
    return shared.create_case(call, call.query["client_id"])


HANDLERS = {
    "getMe": shared.get_me,
    "listProperties": shared.list_properties,
    "listCases": shared.list_cases,
    "getCase": shared.get_case,
    "createCase": create_case,
    "createCaseEvent": shared.create_case_event,
    "listDeadlines": shared.list_deadlines,
    "listDeadlineAlerts": shared.list_deadline_alerts,
    "acknowledgeDeadlineAlert": shared.acknowledge_deadline_alert,
    "listHazardCategories": lambda call: shared.list_hazard_categories(call, enabled_only=False),
    "listEventTypes": lambda call: shared.list_event_types(call, enabled_only=False),
    "createUpload": documents.create_upload,
    "createDocument": lambda call: documents.create_document(call, default_source="email"),
    "listDocuments": documents.list_documents,
    "getDocument": documents.get_document_with_pages,
    "getDocumentPage": documents.get_document_page,
    "getExtraction": extractions.get_extraction,
    "reviewExtraction": extractions.review_extraction,
    "createLetter": letters.create_letter,
    "getLetter": letters.get_letter,
    "createLetterVersion": letters.create_letter_version,
    "approveLetter": letters.approve_letter,
    "markLetterSent": letters.mark_letter_sent,
    "voidLetter": letters.void_letter,
    "listLetterTemplates": letters.list_letter_templates,
    "createLetterTemplate": letters.create_letter_template,
    "approveLetterTemplate": letters.approve_letter_template,
}
