"""Builds a FastAPI app whose routes come from the OpenAPI specification.

Every request goes through the same steps:

1. authenticate the token (multi-factor required) and load the user;
2. authorise against the operation's x-roles and x-provider-access;
3. validate parameters and body against the spec;
4. run the handler in one transaction scoped to the acting client
   (caseward.db.scoped_transaction), with idempotency and view auditing in
   the same transaction;
5. optionally validate the response against the spec.

Operations with no handler yet return 501 after steps 1-3, so the full
contract (auth, permissions, validation) holds from day one.
"""

import hashlib
import ipaddress
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from jsonschema import FormatChecker
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from caseward.api import problems
from caseward.api.auth import Membership, Principal, TokenVerifier, authenticate
from caseward.api.encoding import to_json
from caseward.api.problems import Problem
from caseward.api.settings import Settings
from caseward.api.spec import Operation, load_spec
from caseward.db import RequestContext, record_access, scoped_transaction
from caseward.services import Services

PROBLEM_JSON = "application/problem+json"

formats = FormatChecker()


@formats.checks("date-time", raises=ValueError)
def _aware_datetime(value: object) -> bool:
    # A naive timestamp would be read in the server's time zone; require an offset.
    return not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is not None


@formats.checks("date", raises=ValueError)
def _date(value: object) -> bool:
    return not isinstance(value, str) or bool(date.fromisoformat(value))


@formats.checks("uuid", raises=ValueError)
def _uuid(value: object) -> bool:
    return not isinstance(value, str) or bool(UUID(value))


@dataclass
class Result:
    status: int
    # JSON-serialisable, or bytes with media_type for binary responses.
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    media_type: str | None = None
    # For view auditing when the entity is not simply the {id} path parameter,
    # or belongs to a client other than the acting one.
    audit_entity_id: str | None = None
    audit_client_id: UUID | None = None


@dataclass
class Call:
    conn: psycopg.Connection
    op: Operation
    principal: Principal
    membership: Membership | None
    ctx: RequestContext
    path: dict[str, Any]
    query: dict[str, Any]
    headers: dict[str, Any]
    body: Any
    services: Services | None = None

    @property
    def is_provider(self) -> bool:
        return self.membership is not None and self.membership.client_kind != "landlord"

    def rows(self, query: str, params: Any = None) -> list[dict[str, Any]]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            return cur.execute(query, params).fetchall()

    def row(self, query: str, params: Any = None) -> dict[str, Any] | None:
        with self.conn.cursor(row_factory=dict_row) as cur:
            return cur.execute(query, params).fetchone()


Handler = Callable[[Call], Result]


def build_app(kind: str, settings: Settings, pool: ConnectionPool, verifier: TokenVerifier,
              handlers: dict[str, Handler], services: Services | None = None) -> FastAPI:
    spec = load_spec(kind, settings.spec_dir)
    unknown = set(handlers) - {op.operation_id for op in spec.operations}
    if unknown:
        raise ValueError(f"handlers for operations not in the spec: {sorted(unknown)}")

    app = FastAPI(title=spec.document["info"]["title"], version=spec.document["info"]["version"],
                  openapi_url=None, docs_url=None, redoc_url=None)
    app.state.spec = spec

    for op in spec.operations:
        app.add_api_route(op.path, _endpoint(kind, op, handlers.get(op.operation_id), settings, pool, verifier,
                                             services),
                          methods=[op.method.upper()], name=op.operation_id)

    @app.exception_handler(StarletteHTTPException)
    async def _unrouted(request: Request, exc: StarletteHTTPException):
        code = "method_not_allowed" if exc.status_code == 405 else "not_found"
        return _problem_response(Problem(exc.status_code, code, "No such operation"), None)

    return app


def _endpoint(kind, op, handler, settings, pool, verifier, services):
    async def endpoint(request: Request) -> Response:
        body = await request.body()
        return await run_in_threadpool(_handle, kind, op, handler, settings, pool, verifier, services, request,
                                       body)
    endpoint.__name__ = op.operation_id
    return endpoint


def _handle(kind, op, handler, settings, pool, verifier, services, request: Request, raw_body: bytes) -> Response:
    request_id = request.headers.get("x-request-id") or str(uuid4())
    try:
        with pool.connection() as conn:
            principal = authenticate(verifier, request.headers.get("authorization"), conn)
            membership = _authorise(kind, op, principal, request)
            path, query, headers = _parameters(op, request)
            body = _body(op, raw_body, request)
            if handler is None:
                raise Problem(501, "not_implemented", "Not implemented yet", f"{op.operation_id} is specified but not built.")
            ctx = RequestContext(
                actor_kind="user",
                client_id=membership.client_id if membership else None,
                user_id=principal.user_id,
                request_id=request_id,
                source_ip=_source_ip(request),
            )
            call = Call(conn, op, principal, membership, ctx, path, query, headers, body, services)
            with scoped_transaction(conn, ctx):
                result = _idempotent_replay(call) if op.idempotent else None
                if result is None:
                    result = handler(call)
                    _audit(call, result)
                    if op.idempotent:
                        _store_idempotent(call, result)
    except Problem as problem:
        return _problem_response(problem, request_id)
    except psycopg.Error as exc:
        return _problem_response(problems.from_database(exc), request_id)

    headers = {"X-Request-Id": request_id, **result.headers}
    if isinstance(result.body, bytes):
        return Response(result.body, status_code=result.status, headers=headers, media_type=result.media_type)
    payload = to_json(result.body)
    if settings.validate_responses:
        _validate_response(op, result.status, payload)
    if result.body is None:
        return Response(status_code=result.status, headers=headers)
    return JSONResponse(payload, status_code=result.status, headers=headers)


def _source_ip(request: Request) -> str | None:
    # Behind the load balancer, run uvicorn with --proxy-headers so this is the caller's address.
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def _problem_response(problem: Problem, request_id: str | None) -> JSONResponse:
    headers = dict(problem.headers)
    if request_id:
        headers["X-Request-Id"] = request_id
    return JSONResponse(problem.body(request_id), status_code=problem.status, headers=headers, media_type=PROBLEM_JSON)


def _authorise(kind: str, op: Operation, principal: Principal, request: Request) -> Membership | None:
    if kind == "operations":
        if principal.staff_role not in op.roles:
            raise problems.forbidden("Your role cannot do this.")
        return None

    if principal.staff_role:
        raise problems.forbidden("Staff use the operations API.")
    acting = request.headers.get("x-caseward-client")
    if acting:
        try:
            membership = principal.membership(UUID(acting))
        except ValueError:
            raise Problem(400, "invalid_parameter", "X-Caseward-Client is not a valid id") from None
        if membership is None:
            raise problems.forbidden("You are not a member of that client.")
    elif len(principal.memberships) == 1:
        membership = principal.memberships[0]
    elif not principal.memberships:
        raise problems.forbidden("Your account is not linked to a client.")
    else:
        raise Problem(400, "acting_client_required", "Choose a client",
                      "You belong to more than one client: send X-Caseward-Client.")
    if membership.role not in op.roles:
        raise problems.forbidden("Your role cannot do this.")
    if membership.client_kind != "landlord" and op.provider_access == "none":
        raise problems.not_found()
    return membership


def _parameters(op: Operation, request: Request) -> tuple[dict, dict, dict]:
    values: dict[str, dict[str, Any]] = {"path": {}, "query": {}, "header": {}}
    errors = []
    known_query = {p.name for p in op.parameters if p.location == "query"}
    for name in request.query_params:
        if name not in known_query:
            errors.append({"field": name, "message": "Unknown query parameter."})
    for param in op.parameters:
        source = {"path": request.path_params, "query": request.query_params, "header": request.headers}[param.location]
        raw = source.get(param.name)
        if raw is None:
            if param.required:
                errors.append({"field": param.name, "message": "Required."})
            continue
        try:
            values[param.location][param.name] = _coerce(op, param.name, param.schema, raw)
        except ValueError as exc:
            errors.append({"field": param.name, "message": str(exc)})
    if errors:
        raise Problem(400, "invalid_parameter", "Some parameters are not valid", errors=errors)
    return values["path"], values["query"], values["header"]


def _coerce(op: Operation, name: str, schema: dict, raw: str) -> Any:
    types = schema.get("type")
    value: Any = raw
    if types == "integer" or (isinstance(types, list) and "integer" in types):
        try:
            value = int(raw)
        except ValueError:
            raise ValueError("Must be a whole number.") from None
    error = next(iter(op.validator(f"param:{name}", schema, formats).iter_errors(value)), None)
    if error is not None:
        raise ValueError(error.message)
    fmt = schema.get("format")
    if fmt == "uuid":
        return UUID(value)
    if fmt == "date":
        return date.fromisoformat(value)
    if fmt == "date-time":
        return datetime.fromisoformat(value)
    return value


def _body(op: Operation, raw: bytes, request: Request) -> Any:
    if op.body_schema is None:
        return None
    if not raw:
        if op.body_required:
            raise Problem(400, "body_required", "A request body is required")
        return None
    content_type = request.headers.get("content-type", "").split(";")[0].strip()
    if content_type not in {"application/json", "application/merge-patch+json"}:
        raise Problem(415, "unsupported_media_type", "Send JSON")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        raise Problem(400, "invalid_json", "The body is not valid JSON") from None
    validator = op.validator("body", op.body_schema, formats)
    errors = [{"field": "/" + "/".join(str(p) for p in e.absolute_path), "message": e.message}
              for e in validator.iter_errors(body)]
    if errors:
        raise Problem(400, "invalid_body", "The request body is not valid", errors=errors)
    return body


def _request_hash(call: Call) -> str:
    payload = json.dumps({"op": call.op.operation_id, "path": to_json(call.path), "query": to_json(call.query),
                          "body": call.body}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _idempotent_replay(call: Call) -> Result | None:
    key = call.headers["Idempotency-Key"]
    digest = _request_hash(call)
    # Blocks until a concurrent request with the same key commits or rolls back.
    inserted = call.row(
        "insert into caseward.idempotency_record (user_id, key, operation_id, request_hash)"
        " values (%s, %s, %s, %s) on conflict do nothing returning 1",
        [call.principal.user_id, key, call.op.operation_id, digest],
    )
    if inserted:
        return None
    previous = call.row(
        "select operation_id, request_hash, status, body from caseward.idempotency_record"
        " where user_id = %s and key = %s", [call.principal.user_id, key])
    if previous["operation_id"] != call.op.operation_id or previous["request_hash"] != digest:
        raise Problem(422, "idempotency_key_reused", "This Idempotency-Key was used for a different request")
    return Result(previous["status"], previous["body"], headers={"Idempotent-Replayed": "true"})


def _store_idempotent(call: Call, result: Result) -> None:
    call.conn.execute(
        "update caseward.idempotency_record set status = %s, body = %s where user_id = %s and key = %s",
        [result.status, Jsonb(to_json(result.body)), call.principal.user_id, call.headers["Idempotency-Key"]],
    )


def _audit(call: Call, result: Result) -> None:
    if result.status >= 400 or call.op.audit not in {"view", "export"}:
        return
    if call.op.audit == "export":
        action = "download" if call.op.path.endswith("/download") else "export"
    else:
        action = "view"
    ctx = replace(call.ctx, client_id=result.audit_client_id) if result.audit_client_id else call.ctx
    entity_id = result.audit_entity_id or call.path.get("id") or "list"
    record_access(call.conn, ctx, action, call.op.resource, str(entity_id),
                  {"operation": call.op.operation_id, "query": to_json(call.query)})


def _validate_response(op: Operation, status: int, payload: Any) -> None:
    if status not in op.response_schemas:
        raise AssertionError(f"{op.operation_id} returned {status}, which the spec does not list")
    schema = op.response_schemas[status]
    if schema is None:
        return
    errors = list(op.validator(f"response:{status}", schema, formats).iter_errors(payload))
    if errors:
        details = "; ".join(f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors[:5])
        raise AssertionError(f"{op.operation_id} {status} response does not match the spec: {details}")
