HAND-OFF NOTE

Role: Backend engineer (API skeleton)

Date: 30 Oct 2026

What I completed (with links to files or pull requests):
- `backend/src/caseward/api/`: FastAPI apps built from the OpenAPI spec
  (decision 0005).
  - `framework.py`: routing from the spec, authorisation from `x-roles` and
    `x-provider-access`, validation of parameters and bodies, idempotency,
    view and export auditing, problem responses, optional response
    validation.
  - `auth.py`: JWT verification (JWKS in production) with multi-factor
    required, and user and membership lookup.
  - `problems.py`: RFC 9457 errors. Database rule violations map to 422
    with stable codes, and row-level security refusals map to 404.
  - `handlers/`: first working slice, below.
  - `main.py`: `client_app` and `operations_app` entry points for uvicorn.
- First working slice, in both APIs where specified: me, properties (list,
  get, create), cases (list with cursor paging and three sorts, get with
  ETag, create), case events (with automatic deadline completion),
  deadlines (list with filters, including alert level), and reference data.
  On case creation the system also records the `case_aware` event and
  queues a `compute_deadlines` job.
- The other operations return 501 after checking the token, role and
  request, so the contract already holds for them.
- `backend/db/migrations/0002_api_support.sql`: user lookup functions used
  before a client context exists, and idempotency records stored in the
  same transaction as the request.

Tests added and their status:
- `backend/tests/test_api.py`: 30 end-to-end tests through HTTP against
  Postgres, with every response validated against the spec. They cover:
  - token problems: missing, expired, wrong audience, wrong key, no MFA;
  - unknown and deactivated users;
  - each API rejecting the other API's users;
  - acting-client checks and client isolation;
  - provider access: contractors can log events but cannot open cases;
  - read-only viewers;
  - idempotent replay and reused keys;
  - request validation, including timestamps without a time zone;
  - view audits recorded against the landlord;
  - deadline completion from an event, cursor paging and alert filters.
- 143 tests in total, all passing. A clean install passes too.

Decisions made (and decision record links):
- `docs/decisions/0005-spec-driven-api.md`

Open questions for the founder:
- None new. The questions in the earlier hand-off notes still stand.

Risks or problems found:
- When a provider lists a landlord's records, the view audit is recorded
  against the provider's own client. Views of single records are recorded
  against the landlord. Decide whether list views also need per-landlord
  records.
- Idempotency records are never cleaned up yet. Add a daily job to delete
  records older than 24 hours.
- The source IP comes from the connection. In production, run behind the
  load balancer with `--proxy-headers` and a trusted proxy list.
- The `compute_deadlines` job is queued but no worker runs it yet, so cases
  created through the API have no deadlines until the worker exists.

What the next role needs to do next:
- Backend:
  - the worker process (malware scan, OCR, extraction, `compute_deadlines`,
    the missed-deadline sweeper and alerts);
  - document intake (uploads, documents, page rendering);
  - extraction review;
  - letters.
  Each new handler is a function plus one line in `handlers/*.py`.
- Frontend: start the operations console against the working slice. The
  queue, documents and extractions endpoints follow next.
- Security lead: review `framework.py` and `auth.py`, particularly the
  acting-client logic, the 404-not-403 rule and the idempotency key scope
  (per user).
