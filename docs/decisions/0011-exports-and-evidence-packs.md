# 0011: Exports and evidence packs

Date: 30 Oct 2026. Status: proposed.

## What

- **Requested in the API, built by the worker.** An `export` job runs under
  the requester's acting client, so row-level security limits it exactly as
  it limits the requester. A contractor's export covers only the landlords
  that engage it and never includes letters.
- **Reports** (CSV or PDF): deadline performance, certificate register,
  open defects, and the audit log (a landlord's client admins only).
- **Case evidence pack** for the Housing Ombudsman, the regulator or a
  court, as a PDF:
  - a case summary;
  - the full timeline, including access refusals and other evidence for a
    reasonable excuse;
  - every approved letter in full, with who approved it and when it was
    sent;
  - an index of evidence documents with each one's SHA-256;
  - the document pages themselves (clean files only, up to 200 pages).
  The CSV form is the timeline only.
- **Files** are stored in UK storage with their SHA-256 recorded, and
  expire after 7 days. Downloads use a 60-second link and are audited.
- **Who downloads:** the requester only, or for exports staff prepare, the
  client's admins. Staff can't download (decision 0007).
- **CSV safety:** cells that a spreadsheet would run as formulas (`=`,
  `+`, `-`, `@`) are prefixed with `'`. Files start with a UTF-8 BOM so
  Excel shows accented names correctly.
- **No special-category data** appears in any export.

## Why

The brief asks for exportable evidence for complaints and claims, and for
deadline performance reporting by hazard and contractor. Building under the
requester's own access avoids a second set of permission rules for exports.

## Fix found while building this

Decision 0006 let the worker login see and update every deadline, so the
sweep could run. That access also applied while the worker ran a single
client's job, so **a landlord's export could include another landlord's
deadlines** (rule names and dates, not personal data). The export tests
caught it before any real data existed. Migration 0012 moves that access to
a `caseward_sweeper` role that only `sweep_deadlines()` runs as. A
regression test checks that a client job sees no other client's deadlines.

## Consequences

- Large evidence packs are built in memory. Fine for hundreds of pages;
  revisit for thousands.
- Exports are not yet emailed. Requesters poll and download.
