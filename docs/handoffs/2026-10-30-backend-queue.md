HAND-OFF NOTE

Role: Backend engineer (work queue, quality, dashboard, timeline)

Date: 30 Oct 2026

What I completed (with links to files or pull requests):
- `backend/src/caseward/api/handlers/queue.py`:
  - `listTasks` (filters, including assignee me or unassigned, with cursor
    paging in queue order);
  - `claimNextTask` (SKIP LOCKED, so two handlers never get the same task);
  - `getTask`, `updateTask` (If-Match; block or cancel only with a reason;
    release back to the queue; reassign only to active case handlers or
    leads; only the assignee or a lead may change a task);
  - `completeTask` (refuses until the work is recorded, for example an
    extraction review must exist);
  - `sampleTasksForReview` (random, finished, not mine, not yet reviewed by
    me);
  - `createQualityReview`.
- `backend/src/caseward/api/handlers/reports.py`:
  - client `getDashboard`: deadlines by alert level, met and missed in the
    last 30 days, open and emergency cases, expired and due certificates
    (latest per property and type); providers can narrow it to one
    landlord;
  - `getCaseTimeline`, in both APIs: events (including corrections), status
    changes, deadlines, sent letters (hidden from providers by row-level
    security) and evidence documents, in time order with paging.
- `backend/db/migrations/0007_queue.sql`: task note and queue indexes.
- Spec: the queue order is now **priority first**, then nearest legal
  deadline, then SLA. An urgent C1 review has no deadline until it is
  approved, so it must not sit behind routine work. Tasks gained a `note`.

Tests added and their status:
- `backend/tests/test_queue.py`, 13 tests. They cover:
  - claim order, claims that never overlap, and paging in queue order;
  - stale ETags, reasons required, releasing and reassigning, and who may
    change a task;
  - completion only once the work is done, and closed tasks staying closed;
  - quality sampling rules, one review per reviewer, and no scoring your
    own work;
  - dashboard counts and isolation, and a provider narrowing to one
    landlord;
  - the timeline: order, paging, and letters hidden from providers.
- 227 tests in total, all passing, including from a clean install.

Built so far: client API 24 of 37 operations, operations API 35 of 80. The
rest return 501 after checking the token, role and request.

Not built yet:
- Client: tenancies and tenant contacts, vulnerability endpoints (the
  database side is built and tested), `updateCase`, `updateDefect`,
  exports, certificate type reference.
- Operations: administration (clients, engagements, users, memberships),
  configuration (rule packs, deadline rules including approve and dry run,
  holiday calendar import, codes, event types, hazard phases), audit search
  and verification, job monitoring and retry, case and defect updates,
  deadline cancel, rerunning extraction, exports.

Open questions for the founder:
- None new.

Risks or problems found:
- The dashboard counts all deadlines the caller can see. For a provider
  working for many landlords, use `landlord_client_id` to see one.

What the next role needs to do next:
- Backend:
  1. Configuration endpoints. The compliance lead needs them to load and
     approve rules, because nothing creates real deadlines until rules are
     approved.
  2. Administration, so clients and users can be onboarded without SQL.
  3. Tenants and vulnerability endpoints.
  4. Exports, including the Ombudsman evidence pack.
  5. Audit search and job monitoring.
- Frontend: the operations console (queue, "next task", review screen,
  letters) now has every endpoint it needs.
