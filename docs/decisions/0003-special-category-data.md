# 0003: Household vulnerability as special-category data

Date: 30 Oct 2026. Status: proposed; needs security lead review.

## What

`household_vulnerability` has no SELECT grant for the API. Reads go through
`caseward.read_household_vulnerabilities(tenancy_id, reason)`, which:

- allows only active case handlers and compliance leads, or client users
  given `membership.special_category_access` for their own client;
- requires a reason;
- writes a `view` audit event with that reason before returning rows.

Quality analysts and engaged providers never qualify. Values are redacted
from the change audit, which records only which columns changed. Records
carry `retain_until` and `legal_hold`.

## Why

The brief classes vulnerability factors as special-category data under UK
GDPR, visible only to roles that need them. Putting the access check and the
read logging in one database function means neither can be skipped.

## Alternatives considered

- **Row-level security alone.** It can restrict rows, but can't require a
  reason or log the read.
- **Application-level checks.** Easy to get around, and the read logging
  could be forgotten.
- **Field-level encryption with a separate key.** Worth adding later for
  defence in depth. It doesn't replace the access check.

## Open

- Which vulnerability factors to record (compliance expert).
- Lawful basis and the condition for processing special-category data
  (DPIA, security lead).
