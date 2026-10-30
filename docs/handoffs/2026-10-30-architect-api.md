HAND-OFF NOTE

Role: Solution architect (API specification)

Date: 30 Oct 2026

What I completed (with links to files or pull requests):
- `docs/api/client.yaml`: client API, 37 operations. Portal, document
  intake (two-step upload to UK storage), simple case tracker, case events,
  deadlines, approved letters, exports, special-category endpoints.
- `docs/api/operations.yaml`: operations API, 78 operations (80 after the alert endpoints were added). Work queue with
  claim-next, side-by-side document viewer as page images, extraction
  review, cases, deadline cancellation, letter drafting, editing, approval
  and sending, quality sampling and scoring, administration, configuration
  (rule packs, deadline rules with approval and dry run, calendars,
  templates, codes, event types, hazard phases), audit search and chain
  verification, job monitoring.
- `docs/api/common.yaml`: shared schemas.
- `docs/api/README.md`: conventions (auth, permissions, audit,
  special-category data, idempotency, concurrency, errors, pagination,
  times, background work, human in the loop, versioning).

Tests added and their status:
- `backend/tests/test_api_spec.py`, 47 tests, all passing. 113 tests in the
  suite in total. They check:
  - each spec is valid OpenAPI 3.1 and every `$ref` resolves;
  - every operation declares roles, audit and provider access;
  - PATCH needs If-Match, and creating POSTs are idempotent;
  - client viewers are read-only;
  - special-category reads need the reason header;
  - the client API never references draft AI output;
  - the operations API never serves files;
  - 31 enums match the database CHECK constraints.

Decisions made (and decision record links):
- Two APIs with separate credentials and no file downloads for staff
  (`docs/decisions/0001-architecture-and-stack.md`; detail in
  `docs/api/README.md`).

Open questions for the founder:
1. Do client admins need to manage their own users, or do Caseward admins
   do it all? Currently Caseward admins only.
2. Should exports prepared by staff (evidence packs) go to all client
   admins, or to a named person?
3. Are the proposed rate limits (600 requests a minute per user, 30 uploads
   a minute per client) acceptable for the pilot?

Risks or problems found:
- The ETag for optimistic concurrency needs a row version. Use Postgres
  `xmin`, or add an `updated_at`. The backend should decide when
  implementing.
- Extraction result schemas are open objects until the compliance expert's
  field lists exist. The 98% accuracy gate depends on them.

What the next role needs to do next:
- Product lead: map backlog stories to `operationId`s.
- Compliance expert: field lists per certificate type, which feed the
  extraction schemas.
- Backend: FastAPI skeleton. Generate route stubs and permission checks from
  `x-roles` and `x-audit`, and test responses against these schemas.
- Frontend: build against `operations.yaml` (console) and `client.yaml`
  (portal), using mock servers generated from the specs.
- Security lead: review the two-API boundary, special-category endpoints,
  the no-download design, and token and MFA handling.
