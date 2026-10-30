HAND-OFF NOTE

Role: Backend engineer (exports)

Date: 30 Oct 2026

What I completed (with links to files or pull requests):
- `backend/src/caseward/exports.py`: deadline performance, certificate
  register, open defects, audit log, CSV (safe against formula injection)
  and PDF (ReportLab), and the **case evidence pack**:
  - summary and full timeline;
  - approved letters with approver and sending;
  - a document index with SHA-256;
  - the page images.
- `backend/src/caseward/timeline.py`, `backend/src/caseward/queries.py`:
  the timeline and shared SQL, used by both the API and the worker.
- `backend/src/caseward/worker/exports.py`: builds under the requester's
  access, stores the file with its SHA-256, keeps it 7 days, and fails
  cleanly if the request can't be met.
- API, client: create, status, download. Only the requester can download,
  or a client admin for staff-prepared exports. Expired exports show as
  such. Operations: staff prepare exports for a client's admins (never the
  audit log, and staff can't download).
- `backend/db/migrations/0011_exports.sql`: exports table and the `export`
  job kind.
- **`backend/db/migrations/0012_sweeper.sql`: fixes a leak between
  clients.** The worker's cross-client deadline access (for the sweep) also
  applied during single-client jobs, so one landlord's export could include
  another's deadlines. Now only `sweep_deadlines()` crosses clients, as a
  dedicated role. Decision 0006 is amended and a regression test added.
- **Every operation in both specs is now built:** 37 of 37 client and 80 of
  80 operations. A test checks that every spec operation has a handler.

Tests added and their status:
- `backend/tests/test_exports.py`, 11 tests. They cover:
  - deadline CSV contents, formula escaping and isolation;
  - certificate register and defects as PDF;
  - contractors exporting only engaged landlords' data;
  - the evidence pack containing the whole story, with SHA-256s, and
    without letters for providers;
  - the pack as CSV;
  - one visible case only, and a missing case failing cleanly;
  - who can download, and staff-prepared exports;
  - the audit log for client admins only;
  - expiry, and exports and downloads audited.
- `backend/tests/test_worker.py`: the cross-client regression.
- `backend/tests/test_api.py`: every spec operation has a handler; the 501
  path is still covered.
- 275 tests in total, all passing, twice in a row and from a clean install.

Decisions made (and decision record links):
- `docs/decisions/0011-exports-and-evidence-packs.md`; 0006 amended.

Open questions for the founder:
1. Should evidence packs carry a statement of truth or a cover note signed
   by a named person, for the Ombudsman or a court?
2. How long should exports be kept? 7 days is a placeholder.

Risks or problems found:
- The leak above was found by tests before any real data existed. The
  security lead should review every cross-client role: the sweep,
  `caseward_auth` and staff access.

What the next role needs to do next:
- Code reviewer and security lead: review `feat/foundation-schema`. The
  backend MVP is feature-complete against the spec.
- DevOps: environments, roles (decisions 0010 and 0012), the worker image
  with Tesseract and ClamAV, a UK S3 bucket, and CI deploys.
- Frontend: console and portal against the specs.
