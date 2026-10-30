HAND-OFF NOTE

Role: Backend engineer (background worker)

Date: 30 Oct 2026

What I completed (with links to files or pull requests):
- `backend/db/migrations/0003_worker.sql`:
  - triggers that queue deadline calculation for every case event, defect
    and inspection;
  - the `deadline_alert` table;
  - `sweep_deadlines()`, which marks missed deadlines and raises alerts;
  - `requeue_stale_jobs()`.
- `backend/src/caseward/worker/`:
  - `runner.py`: claim, run under the job's client, commit the work and
    success together, back off on failure, sweep, requeue stale jobs;
  - `deadlines.py`: the `compute_deadlines` job, which matches approved
    rules in enabled live packs for the jurisdiction and severity, code or
    certificate type, uses the latest holiday calendar with a coverage
    check, is safe to repeat, and meets a deadline straight away if its
    completion event is already recorded;
  - `__main__.py`: `python -m caseward.worker`, which stops cleanly on
    SIGTERM.
- Operations API: `GET /deadline-alerts` and
  `POST /deadline-alerts/{id}/acknowledge`, added to the spec and built.
- The API no longer queues deadline jobs itself: the trigger does it.

Tests added and their status:
- `backend/tests/test_worker.py`, 13 tests. They cover:
  - a deadline created from a new case, with bank holidays skipped;
  - calculation that is safe to repeat;
  - a follow-on deadline started from an investigation event;
  - a completion recorded before the calculation ran;
  - no deadline when the rule pack is not enabled;
  - a calendar that does not cover the due date, retried with backoff;
  - failing jobs moving to dead;
  - job kinds without a handler waiting in the queue;
  - stale jobs being requeued;
  - worker changes audited as the worker under the job's client;
  - the sweep marking missed deadlines, raising alerts once each,
    escalating 50% then 80%, and keeping alerts per client.
- `backend/tests/test_api.py`: listing and acknowledging alerts, with role
  checks.
- 158 tests in total, all passing. The worker process was also tried
  against a real database.

Decisions made (and decision record links):
- `docs/decisions/0006-background-worker.md`

Open questions for the founder:
1. Who should receive alerts outside the console (email, SMS), and should
   clients get them too?
2. For emergency cases, do you want an alert the moment the case is opened,
   on top of the deadline alerts? Not built yet.

Risks or problems found:
- Correcting a `case_aware` event does not move deadlines that already
  exist. Staff must cancel and recompute them.
- The worker can read and update every deadline and alert (no personal
  data), for the sweep. The security lead should review this (decision
  0006).
- Without real approved rules, the worker creates no deadlines. The
  compliance expert's rule specs are still the critical path.

What the next role needs to do next:
- Backend:
  - document intake: uploads, malware scan job, OCR job, page rendering;
  - the extraction job and the review endpoint;
  - letter drafting.
  Each new job kind is a function plus one line in `worker/runner.py`.
- DevOps: run at least two worker processes, and alert when jobs go `dead`
  or the queue backs up (`GET /jobs/summary`, still to build).
- Compliance expert: rules, so that deadlines appear for real.
