# Data model

The schema lives in `backend/db/migrations/0001_foundation.sql`. This page
explains how it maps to the brief, and what was added and why.

## Mapping to the brief

| Brief | Table(s) | Notes |
|---|---|---|
| Client (landlord or contractor) | `client`, `engagement` | `engagement` gives a contractor access to a landlord's data while it is current. |
| — | `app_user`, `membership` | Added: users and their role per client. Staff have `app_user.staff_role`. |
| Property (address, UPRN) | `property` | Adds `jurisdiction`, which decides the rules and bank holidays that apply. |
| Tenant contact (minimal) | `tenancy`, `tenant_contact` | Contact belongs to a tenancy with start and end dates. Has retention and legal-hold fields. Hidden from contractors. |
| Document | `document`, `ocr_output` | Adds a SHA-256 hash (evidence integrity, dedupe per client), a malware scan status and retention fields. Raw OCR text is stored separately. |
| Extraction | `extraction`, `extraction_review` | Records provider, model version, prompt id and version, and output schema version. People's accepted or corrected values live in `extraction_review`. |
| — | `inspection` | Added: EICR and gas safety checks, with inspected and next-due dates. Defects usually come from one. |
| Defect | `defect` | Codes C1/C2/C3/FI (EICR), ID/AR/NCS (gas), Other. |
| Case | `hazard_case`, `case_defect`, `case_status_change`, `hazard_category` | `aware_at` (when the landlord became aware) is kept separate from `received_at`. One case can cover several defects. Status history is written automatically. Seeded with the 29 HHSRS hazards. |
| Deadline | `deadline` | Exactly one of case, defect or inspection. References `(rule_key, rule_version)`. Stores clock start, holiday calendar and calculator version. |
| DeadlineRule | `deadline_rule` | Versioned, needs approval by a compliance lead who is not the author, frozen once approved. Has jurisdiction, subject kind and what it applies to. |
| — | `holiday_calendar`, `bank_holiday` | Added: versioned bank holidays per region, loaded from the GOV.UK feed. |
| Task | `task` | One optional subject, assignee, SLA due time. |
| Letter | `letter`, `letter_version`, `letter_template` | Versions are append-only. The approved text is `letter_version` at `letter.approved_version`. Sending records delivery evidence. |
| QualityReview | `quality_review` | Only on finished tasks, and never by the task's own assignee. |
| AuditEvent | `audit_event` | Who (`actor_user_id`, `actor_kind`), what (`action`, entity, changed fields, before/after), when, and where from (`request_id`, `source_ip`, `user_agent`). Append-only and hash-chained. |
| — | `job` | Added: the background job queue. |

## Who can see what

| Data | Owning landlord | Engaged contractor | Staff |
|---|---|---|---|
| Properties, documents, extractions, inspections, defects, cases, deadlines, tasks | read/write | read/write | all clients |
| Tenancies, tenant contacts, letters | read/write | none | all clients |
| Audit events | read (own) | none | all |
| Deadline rules, templates, calendars | read | read | write |

Fine-grained permissions (for example, who may approve a letter) are checked
in the API on top of this. Row-level security is the backstop.

## Deadline lifecycle

1. A trigger event happens, for example a case is created with `aware_at`.
2. The API picks the approved rule version that is effective on that date for
   the property's jurisdiction and the subject's severity or code, and loads
   the matching holiday calendar.
3. `caseward.deadlines.compute_due_at` works out `due_at`. The deadline row
   stores every input.
4. Recording `completed_at` sets the status to `met` or `missed`. A sweeper
   job marks overdue open deadlines as `missed`.
5. If a rule or the facts change, cancel the deadline and create a new one.
   History is never rewritten.
