# Caseward API specification

| File | What it is |
|---|---|
| [client.yaml](client.yaml) | **Client API** for landlords and their providers (contractors, managing agents, legal teams): the portal, document intake, the simple case tracker, exports. Internet-facing. |
| [operations.yaml](operations.yaml) | **Operations API** for Caseward staff: the work queue, document and extraction review, letters, quality review, administration, configuration, audit. Private network only. |
| [common.yaml](common.yaml) | Shared schemas, parameters and responses. |

All three are OpenAPI 3.1. `backend/tests/test_api_spec.py` checks that they
are valid and that every `$ref` resolves. It also enforces the conventions
below and checks that each enum matches the database's CHECK constraint, so
the spec and the schema cannot drift apart.

To browse them, open them in any OpenAPI viewer, for example
`npx @redocly/cli preview-docs docs/api/operations.yaml`.

## Why two APIs

They are separate deployments with separate database credentials (decision
0001). The client API's credentials cannot read across clients, so a bug in
the internet-facing service cannot leak another landlord's data. The
operations API is reachable only from the UK-hosted virtual desktop the
Nairobi team uses. It has **no endpoint that hands over a file**: documents
are viewed as rendered, watermarked page images, which keeps "no downloads
to local devices" true by design.

## Conventions

### Authentication

- Every request carries a bearer token from the identity provider. Tokens
  without multi-factor authentication are rejected (401, `mfa_required`).
- Client users who belong to more than one client choose one with
  `X-Caseward-Client`. Providers act for a landlord by passing
  `landlord_client_id` where an operation needs it.
- The API sets the database context (`caseward.db.scoped_transaction`) from
  the verified token and nothing else.

### Permissions: `x-roles`, `x-provider-access`

Each operation lists the roles that may call it in `x-roles`. In the client
API, `x-provider-access` says what an engaged provider may do: `none`,
`read` or `write`. Where it is `none`, providers get 404, as if the resource
does not exist. `client_viewer` is read-only everywhere apart from requesting
an export. The database's row-level security is the backstop if the API
gets a check wrong.

### Audit: `x-audit`

| Value | Meaning |
|---|---|
| `write` | Recorded automatically by database triggers. |
| `view` | The API calls `caseward.db.record_access(..., "view", ...)` before returning client data. |
| `export` | As for view, with `"export"` or `"download"`. |
| `none` | Reference data or the user's own status only. |

This is how MVP item 8 (audit every view, edit, approval and export) is met.

### Special-category data: `x-special-category`

Household vulnerability endpoints need the `X-Access-Reason` header. It is a
header so the reason never lands in URLs or access logs, and it is stored
with the audit event. Responses are `Cache-Control: no-store`. Creating a
record returns no body, so the data is not echoed back.

### Idempotency

Every POST that creates something takes an `Idempotency-Key` header, or has
a natural key that returns 409 on a repeat. The server keeps keys for 24
hours and replays the original response. Uploading the same file twice is
refused with 409 `duplicate_document` and the existing document's id
(robustness rule 6).

### Concurrency

Reads that can be followed by an edit return an `ETag`. Every PATCH, and
other edits of shared state, require `If-Match`, and a stale value gets 412.
Letter edits instead use `based_on_version` and get 409 if someone else saved
first. Two case handlers can never silently overwrite each other.

### Errors

Errors use `application/problem+json` (RFC 9457) with a stable `code`, for
example `duplicate_document`, `self_approval`, `rule_not_approved`,
`pack_not_enabled`, `mfa_required`. Database rule violations map to 422 with
the matching code. Ids belonging to other clients return 404, never 403.

### Lists

Lists use cursor pagination: `limit` (up to 100) and an opaque `cursor`. The
response is `{items, next_cursor}`, and `next_cursor` is null on the last
page.

### Dates and times

Timestamps are RFC 3339. Responses are in UTC (`Z`). Deadlines also carry
`due_local_date`, the UK calendar date, so screens in Nairobi and the UK
show the same day. Date-only fields are UK dates.

### Background work

Anything that takes time (document processing, AI drafting, deadline
calculation, exports) returns 202 and continues in the database job queue.
If the AI service is down, jobs wait and retry, and nothing is skipped
(robustness rule 5).

### Human in the loop

- The client API cannot reach AI drafts at all. Clients see approved and
  sent letters only, and inspections and defects created from
  human-approved extractions.
- `POST /extractions/{id}/review` and `POST /letters/{id}/approve` are the
  only ways AI output becomes a record or a letter. The approver cannot be
  the author.
- Deadline rules and rule packs go live only through the compliance lead
  endpoints, with the qualified person's confirmation recorded.

### Versioning

The version is in the path (`/v1`). Within a version, changes are additive
only. Breaking changes mean `/v2`, with both run in parallel for a
transition period.

## Not yet specified

- **Extraction result schemas per certificate type** (`Extraction.result`,
  `ExtractionReviewCreate.approved_result`). These depend on the compliance
  expert's field lists. Until then they are open JSON objects.
- **Rate limits.** Proposed: 600 requests a minute per user, and 30 uploads
  a minute per client.
- **Webhooks** for clients with their own systems. Phase 2.
