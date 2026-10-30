"""Operations API handlers. Operations not listed here return 501."""

from caseward.api.framework import Call, Result
from caseward.api.handlers import shared


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
}
