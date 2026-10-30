# 0005: The API is driven by the OpenAPI specification

Date: 30 Oct 2026. Status: proposed.

## What

The FastAPI apps build their routes from `docs/api/*.yaml` at start-up
(`caseward.api.framework.build_app`). One generic endpoint per operation:

1. checks the token (multi-factor required) and loads the user;
2. enforces `x-roles` and `x-provider-access`;
3. validates parameters and body against the spec's JSON Schemas;
4. runs the handler in one transaction scoped to the acting client, with
   idempotency records and view or export audit events written in the same
   transaction.

Handlers are plain functions that take a `Call` and return a `Result`. An
operation without a handler returns 501, but only after steps 1-3, so
access rules apply from the start. In development and tests, every response
is also validated against the spec.

## Why

- The brief's backend definition of done is "the API matches the
  specification". This approach makes that structural rather than a review
  task: the spec is the only place a route, role or schema is declared.
- Permissions and auditing are declared once per operation and enforced in
  one place, so a new handler cannot forget them.
- Frontend and backend can work in parallel against the same contract.

## Alternatives considered

- **Code-first FastAPI with Pydantic models, generating the spec.** This is
  idiomatic, but the spec would follow the code, and the role and audit
  rules would be spread across decorators. Pydantic models can still be
  generated from the spec later if handlers need typed bodies.
- **openapi-core or connexion.** Both do similar validation, but they bring
  their own request lifecycle. We need the auth, row-level security and
  idempotency steps to share one database transaction, which is simpler
  with our own small wrapper.

## Consequences

- Handlers receive JSON as dicts, not typed objects.
- Changing a route means editing the YAML first. The spec tests and the
  response validation catch drift.
