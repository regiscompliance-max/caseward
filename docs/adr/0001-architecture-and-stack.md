# ADR 0001: Architecture and stack

Status: Proposed
Date: 2026-10-30

## Context

Caseward handles compliance evidence for landlords and contractors: safety
certificates, defect reports and hazard cases with legal deadlines, and
letters to tenants. The data includes personal data about tenants, so it must
stay in the UK, be isolated per client, and leave an audit trail that can be
shown to a regulator or a court.

## Decision

We adopt the recommended stack with the changes below.

| Area | Decision |
|---|---|
| Frontend | Next.js + TypeScript with an accessible component library (WCAG 2.2 AA). |
| API | Python + FastAPI. |
| Database | PostgreSQL 16+ in a UK region, row-level security per client. |
| Files | UK-region object storage, encrypted, private buckets, short-lived signed URLs only. |
| Jobs | **Changed:** a Postgres-backed queue (`caseward.job`) rather than Redis/Celery. |
| OCR | Runs before extraction. **Added:** must be hosted in the UK, or run on our own servers. |
| AI | One internal service for all model calls; model and prompt versions are recorded on every output. |
| Auth | Managed identity provider (SAML + OIDC) with MFA for everyone. **Changed:** roles and permissions live in our database. |
| Hosting | UK cloud region, infrastructure as code, separate dev, test and production accounts. |

### Why the changes

**Postgres-backed queue.** A document and its first job are written in one
transaction, so an upload can never be saved without its extraction being
queued (or the reverse). It removes a separate service that would need its own
UK hosting, encryption and access controls. Workers claim jobs with
`FOR UPDATE SKIP LOCKED` (`caseward.claim_job`). Payloads carry ids only, never
personal data. Revisit if throughput exceeds a few hundred jobs per second.

**Roles in our database.** Client SSO varies by landlord (Entra ID, Google and
others). The identity provider only proves who someone is; what they can do
comes from `caseward.membership` and `app_user.staff_role`, which are
auditable and covered by tests.

**UK processing, not just UK endpoints.** Some managed AI and OCR services
route requests between regions. Before go-live, confirm in writing that
inference and OCR for our account stay in the UK, and that the provider does
not train on our data.

## How isolation works

1. The API never connects as the table owner. Migrations run as a separate
   NOLOGIN owner role.
2. Each request runs in one transaction that sets `app.client_id`,
   `app.user_id`, `app.actor_kind`, `app.request_id` and `app.source_ip` as
   transaction-local settings (`caseward.db.scoped_transaction`). Nothing
   leaks into the next request on a pooled connection.
3. Row-level security policies read those settings. Client tables use
   `FORCE ROW LEVEL SECURITY`.
4. Staff cross-client access comes from membership of the `caseward_staff`
   database role, which only the internal operations API's credentials have.
   A client-facing connection cannot grant itself staff access by changing a
   setting.
5. Composite foreign keys (`(id, client_id)`) stop rows of one client
   referencing another client's rows, even when written by staff.

## Integrity rules enforced in the database

These rules are enforced in the database, not only in the API, so a bug or a
direct query cannot get around them:

- Deadline rules need approval by a compliance lead who is not the author.
  Approved versions are frozen, and only one version is effective at a time.
- Deadlines store every input needed to recalculate them. Those inputs cannot
  change after creation. Met and missed are worked out from the completion
  time, not set by hand.
- Letter versions are append-only. The approver cannot be the author of the
  approved version, and an approval cannot be changed.
- Quality reviewers cannot review their own work.
- Every change to a business table is written to `audit_event`. That table is
  append-only and hash-chained (`caseward.verify_audit_chain()`), and personal
  data is redacted from it.

## Consequences

- Business rules live partly in SQL triggers, so they need integration tests
  against real Postgres (see `backend/tests`). CI runs them on every push.
- Audit inserts are serialised by the hash chain. This is fine at expected
  volumes. If it becomes a bottleneck, chain per client instead.
- `can_access_client` runs per row. Check query plans once data volumes are
  realistic, and add a cached list of accessible clients per request if needed.
- The API has no DELETE grant. Retention deletes will run under a separate,
  audited role (not built yet).

## Open questions

- Which cloud provider? The design is portable: Postgres, S3-compatible
  storage and containers.
- Which identity provider?
- The compliance lead needs to confirm the conventions in
  `backend/src/caseward/deadlines.py`: end of day, whether the start day
  counts, and month arithmetic.
- Should contractor access be scoped to individual properties rather than to
  the whole landlord?
