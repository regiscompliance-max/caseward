"""RFC 9457 problem responses, and how database errors map to them."""

import re

import psycopg
from psycopg import errors


class Problem(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str | None = None,
                 errors: list[dict] | None = None, headers: dict[str, str] | None = None,
                 extra: dict | None = None):
        super().__init__(detail or title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.errors = errors
        self.headers = headers or {}
        self.extra = extra or {}

    def body(self, request_id: str | None) -> dict:
        body = {"type": f"https://docs.caseward.example/problems/{self.code}", "title": self.title,
                "status": self.status, "code": self.code}
        if self.detail:
            body["detail"] = self.detail
        if self.errors:
            body["errors"] = self.errors
        if request_id:
            body["request_id"] = request_id
        return {**body, **self.extra}


def not_found() -> Problem:
    return Problem(404, "not_found", "Not found")


def forbidden(detail: str | None = None) -> Problem:
    return Problem(403, "forbidden", "Not allowed", detail)


# Messages raised by the database's rule triggers, mapped to stable codes.
RULE_CODES = [
    (r"cannot approve a letter version they wrote|approved by its author", "self_approval"),
    (r"rule pack .* is not live", "pack_not_live"),
    (r"not enabled for this client", "pack_not_enabled"),
    (r"deadline rule .* is (draft|retired)", "rule_not_approved"),
    (r"compliance lead", "approver_not_compliance_lead"),
    (r"immutable|append-only|final|frozen|create a new version", "immutable"),
    (r"cannot quality review their own task", "self_review"),
]


def from_database(exc: psycopg.Error) -> Problem:
    detail = exc.diag.message_primary if exc.diag else str(exc)
    if isinstance(exc, errors.InsufficientPrivilege):
        if (detail or "").startswith("permission denied for"):
            # A missing grant is a bug in our code or migrations, not a refusal: fail loudly.
            raise exc
        # A row-level security refusal, or one of our access checks: say nothing
        # about whether the target exists.
        return not_found()
    if isinstance(exc, errors.ForeignKeyViolation):
        return Problem(422, "invalid_reference", "A referenced record does not exist or is not available")
    if isinstance(exc, (errors.UniqueViolation, errors.ExclusionViolation)):
        return Problem(409, "conflict", "Conflicts with an existing record", detail)
    if isinstance(exc, (errors.CheckViolation, errors.NotNullViolation)):
        return Problem(422, "rule_violation", "Breaks a data rule", detail)
    if isinstance(exc, errors.RaiseException):
        code = next((c for pattern, c in RULE_CODES if re.search(pattern, detail or "")), "rule_violation")
        return Problem(422, code, "Breaks a rule", detail)
    if isinstance(exc, (errors.InvalidTextRepresentation, errors.DatetimeFieldOverflow)):
        return Problem(400, "invalid_value", "A value is not valid", detail)
    raise exc
