# 0006: Background worker and deadline sweep

Date: 30 Oct 2026. Status: proposed; needs security lead review.

## What

- **Deadline calculation is queued by the database.** Triggers on
  `case_event`, `defect` and `inspection` insert a `compute_deadlines` job
  for every new record (except corrections). The worker finds the approved
  rules that match, then creates deadlines. Running a job twice is safe.
- **Each job runs under its own client's context.** The worker claims a job,
  then does the work in a transaction with `app.client_id` set to the job's
  client, so row-level security applies as it would for that client. The
  work and the job's success commit together.
- **Failures back off and then stop.** Retries use exponential backoff up to
  `max_attempts`, after which the job is marked `dead`. The stored error is
  the first line only, so no row values are kept. Jobs left `running` by a
  stopped worker go back on the queue after 15 minutes.
- **Kinds without a handler wait.** A worker only claims job kinds it can
  run. OCR, extraction and drafting jobs stay queued until those handlers
  exist (robustness rule 5).
- **A periodic sweep runs every minute, across all clients.** It marks
  overdue open deadlines as `missed` and raises one `deadline_alert` per
  level reached (50%, 80%, overdue). A late sweep raises only the highest
  level reached.
- ~~**The worker can see every deadline.** To sweep, it may select and update
  every `deadline` and `deadline_alert` row (policies for `caseward_worker`).~~
  **Amended (migration 0012, decision 0011):** that access leaked across
  clients during ordinary jobs. Only `sweep_deadlines()` now crosses clients,
  running as the `caseward_sweeper` role.
- **No holiday calendar, no deadline.** If the bank holiday calendar does
  not reach the due date, the job fails with `CalendarMissing` rather than
  guess. A GOV.UK calendar covers to 31 December of the last year it lists.

## Why

- Queueing from triggers means no route into the database can skip deadline
  calculation, and nothing depends on the API remembering to.
- Committing the work and the job's success together avoids half-finished
  jobs.
- Rule 7 needs alerts that are reliable and never hidden, and the alerts
  table is an audited, permanent record of each warning.

## Alternatives considered

- **Let the API calculate deadlines in the request.** It's quicker to show
  results, but it couples requests to rule evaluation, and events created by
  other routes would be missed.
- **A sweep per client under that client's context.** Narrower access, but
  the worker would need to list clients, which needs the same kind of
  cross-client read. We may revisit this with a dedicated sweeper role.
- **Raise every level a deadline has passed.** On a late sweep that sends a
  burst of out-of-date warnings.

## Consequences

- Every case event creates a job, most of which find no rule. They're cheap,
  but monitor queue size.
- Correcting a `case_aware` event does not yet move existing deadlines. A
  compliance lead cancels and recomputes them (`POST /deadlines/{id}/cancel`).
- Alerts are recorded but not yet delivered by email or SMS.
