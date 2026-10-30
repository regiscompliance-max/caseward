HAND-OFF NOTE

Role: Backend engineer (updates, audit, job monitoring)

Date: 30 Oct 2026

What I completed (with links to files or pull requests):
- `backend/src/caseward/api/handlers/updates.py`:
  - **updateCase** (both APIs, If-Match):
    - closing sets `closed_at` and reopening clears it;
    - the assigned contractor must be engaged;
    - **a severity change needs a reason**. It cancels the open deadlines
      started under the old severity's rules, records a timeline note, and
      recalculates from the case's events.
  - **updateDefect** (both APIs, If-Match, with a note):
    - providers may mark a defect remediated but not verify or close it;
    - **remediating meets the defect's deadlines**. Nothing completed
      defect deadlines before.
  - Link and unlink defects to cases, same landlord only.
  - **cancelDeadline** (compliance lead): records who, when and why, then
    recalculates using each event's latest correction. **A corrected
    awareness time now moves the deadline**, which closes the gap noted in
    decision 0006.
  - **rerunExtraction**: only once the document has been read, and never
    after an approved extraction already created records.
- `backend/src/caseward/api/handlers/monitoring.py`:
  - audit search (filters, newest first);
  - audit chain verification;
  - job summary by kind;
  - retrying dead jobs (admin).
- `backend/db/migrations/0010_updates.sql`: defect status note, deadline
  cancellation fields, and staff update on jobs.
- **Error handling fix:** a missing database grant used to show up as 404,
  looking like a deliberate refusal. That hid a real bug: job retry could
  not have worked. Missing grants now fail as server errors. No other
  hidden cases were found.
- Spec: `rerunExtraction` returns 202 with no body, and defects carry
  `row_version`.

Tests added and their status:
- `backend/tests/test_updates.py`, 9 tests. They cover:
  - case close and reopen with version checks, and providers refused;
  - a severity change replacing deadlines, with the reason on the
    timeline;
  - contractor assignment only to engaged providers;
  - defect links within one landlord only;
  - remediation meeting defect deadlines, with provider limits;
  - a corrected awareness time moving the deadline;
  - extraction re-run rules;
  - audit search and chain check, with role limits;
  - dead jobs seen and retried.
- `backend/tests/test_api.py`: missing grants fail loudly.
- 261 tests in total, all passing, twice in a row and from a clean install.

Built so far: client API 34 of 37, operations API 79 of 80. Only exports
remain.

Open questions for the founder:
- None new.

Risks or problems found:
- The operations API has no defect list or inspection view of its own. Case
  handlers see defects through cases and extraction reviews. Add operations
  reads if the console needs them.

What the next role needs to do next:
- Backend: exports (CSV and PDF) and the Ombudsman, regulator or court
  evidence pack: the timeline plus documents, photos and approved letters.
