# 0001: Architecture and stack

Date: 30 Oct 2026. Status: proposed.

## What

We adopt the recommended stack in the brief, with four changes:

1. **Background jobs use a queue stored in Postgres** (`caseward.job`, claimed
   with `FOR UPDATE SKIP LOCKED`) instead of Redis/Celery.
2. **Two API deployments with different database credentials**: the client
   API and the operations API. Staff cross-client access comes from database
   role membership, not a request setting.
3. **Roles and permissions live in our database.** The identity provider
   only handles SSO and MFA.
4. **OCR and AI must process data in the UK**, not just expose a UK endpoint.

## Why

1. A document and its first job are saved in one transaction, so nothing is
   lost between them and robustness rule 5 (documents queue and wait) holds.
   It also removes a service that would need its own UK hosting and controls.
   Payloads carry ids only.
2. The client API is exposed to the internet. If it has a bug, it still
   cannot reach other clients' data.
3. Clients bring different identity providers. Keeping permissions in our
   database makes them auditable and testable.
4. Some managed AI and OCR services route requests between regions. This
   must be confirmed in writing before the security gate.

## Alternatives considered

- **Redis + Celery or a managed cloud queue.** More throughput, but another
  service to secure, and jobs can't be queued in the same transaction as the
  data. Revisit above a few hundred jobs per second.
- **A single API with a staff flag in the session.** Simpler, but a bug or
  injection in the client API could switch the flag on.
- **Permissions in the identity provider.** Varies per client and is hard
  to test.

## Consequences

- Business rules live partly in SQL triggers. Integration tests run against
  real Postgres in CI.
- Audit inserts are serialised by the hash chain. If that becomes a
  bottleneck, chain per client instead.
- The API has no DELETE grant. Retention deletes will run as a separate,
  audited role (not built yet).
