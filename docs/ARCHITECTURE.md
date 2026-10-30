# Caseward architecture

Owner: solution architect. Status: draft for Phase 1 gate approval.

Read with [BRIEF.md](BRIEF.md). Decisions and alternatives are in
[decisions/](decisions/). The schema is
`backend/db/migrations/0001_foundation.sql`.

## Components

```
 Client users (UK)            Case handlers, quality analysts, admins (Nairobi)
       │                                   │  secure remote desktop only,
       ▼                                   ▼  no local downloads
 ┌──────────────┐                 ┌──────────────────┐
 │ Client portal│                 │ Operations console│   Next.js + TypeScript
 └──────┬───────┘                 └────────┬─────────┘
        │ SSO + MFA                        │ SSO + MFA
        ▼                                  ▼
 ┌──────────────┐                 ┌──────────────────┐
 │ Client API   │                 │ Operations API   │   FastAPI, separate
 │ (app creds)  │                 │ (staff creds)    │   deployments
 └──────┬───────┘                 └────────┬─────────┘
        └──────────────┬───────────────────┘
                       ▼
        ┌─────────────────────────────┐      ┌───────────────────────┐
        │ PostgreSQL (UK region)      │◀────▶│ Workers               │
        │ RLS, audit chain, job queue │      │ malware scan, OCR,    │
        └─────────────────────────────┘      │ extraction, drafting, │
                       ▲                     │ deadlines, alerts     │
                       │                     └──────────┬────────────┘
        ┌──────────────┴──────────────┐                 ▼
        │ Object storage (UK, private)│      ┌───────────────────────┐
        └─────────────────────────────┘      │ AI service (internal) │
                                             │ prompts + models      │
                                             │ versioned; UK only    │
                                             └───────────────────────┘
```

- **Two APIs, two sets of database credentials.** The client API connects as
  a member of `caseward_app` only. The operations API also has
  `caseward_staff`, which gives cross-client access. The internet-facing
  client API therefore cannot reach other clients' data, even if it has a bug.
- **Workers** connect as `caseward_worker`. They can claim any job, then do
  the work under the job's client context.
- **The AI service** is the only component that calls a model. It records
  the provider, model version, prompt id and version, and output schema
  version on every result. It never writes to the database directly: workers
  call it and store the result.
- **Nairobi access** goes through a UK-hosted virtual desktop with clipboard,
  print and download disabled. This is a DevOps and security deliverable.

## Security boundaries

| Boundary | Mechanism | Tested in |
|---|---|---|
| Client from client | Row-level security on `app.client_id`, plus composite `(id, client_id)` foreign keys | `test_rls.py` |
| Contractor, managing agent or legal team from landlord | `engagement` with start and end dates; no access to tenant data or letters | `test_rls.py` |
| Client API from staff powers | Staff access comes from login role membership, not a setting | `test_rls.py` |
| Special-category data | No direct SELECT. A function checks the role, requires a reason and audits every read | `test_special_category.py` |
| AI output to tenant or client | Letter approval is a separate human step; the approver cannot be the author | `test_integrity.py` |
| Legal rules | Rule packs and deadline rules need a compliance lead's approval; approved versions are frozen | `test_integrity.py`, `test_rule_packs.py` |
| Tampering | Append-only, hash-chained `audit_event`; `verify_audit_chain()` | `test_integrity.py` |

## Data model

| Brief entity | Tables | Notes |
|---|---|---|
| Client | `client`, `engagement`, `app_user`, `membership` | Client kinds: landlord, contractor, managing agent, legal. Staff roles: case handler, quality analyst, compliance lead, admin. |
| Property | `property` | UPRN is unique per client. `jurisdiction` selects the rules and bank holidays. |
| Tenant contact | `tenancy`, `tenant_contact`, `household_vulnerability` | Minimal fields, with retention and legal hold. Vulnerability factors are special-category data (decision 0003). |
| Document | `document`, `ocr_output` | SHA-256 per client, so the same file uploaded twice is rejected (robustness rule 6). Malware scan status. |
| Extraction | `extraction`, `extraction_review` | Model, prompt and schema versions; per-field confidence; the human decision. |
| Defect | `inspection`, `defect` | Certificate types and defect codes are configuration (decision 0002). |
| Case | `hazard_case`, `case_defect`, `case_event`, `case_status_change`, `hazard_category` | `aware_at` starts the clock. `case_event` is the timeline and the reasonable-excuse evidence (decision 0004). |
| Deadline | `deadline`, `deadline_progress` view | Stores every input needed to recalculate it. Completion events meet it automatically. The view drives the 50%, 80% and 100% alerts. |
| DeadlineRule | `deadline_rule`, `holiday_calendar`, `bank_holiday` | Belongs to a rule pack. Start and completion events. Versioned, approved, effective-dated. |
| Task | `task` | Queue item with SLA. |
| Letter | `letter_template`, `letter`, `letter_version` | Tenant or contractor recipient. Language and accessible variants (large print, easy read). |
| QualityReview | `quality_review` | Only on finished tasks, never by the task's own assignee. |
| AuditEvent | `audit_event` | Writes are audited by trigger. Views and exports are audited by the API through `caseward.db.record_access`. |
| (added) | `rule_pack`, `client_rule_pack`, `certificate_type`, `defect_code`, `event_type`, `vulnerability_factor_type` | Sector configuration. |
| (added) | `job` | Background queue in Postgres (decision 0001). |

### Who can see what

| Data | Owning landlord | Engaged provider | Staff |
|---|---|---|---|
| Properties, documents, extractions, inspections, defects, cases, case events, deadlines, tasks | read/write | read/write | all clients |
| Tenancies, tenant contacts, letters | read/write | none | all clients |
| Household vulnerability | users with `special_category_access`, via function only | none | case handlers and compliance leads, via function only |
| Audit events | read (own) | none | all |
| Rule packs, rules, templates, codes, calendars | read | read | write (rule packs and rules need a compliance lead) |

The API also checks fine-grained permissions on top (for example, who may
approve a letter). Row-level security is the backstop.

## Deadline lifecycle

1. An event is recorded, for example a case is created with `aware_at`, or a
   `case_event` of kind `investigation_completed` is logged.
2. The `compute_deadlines` job finds the approved rules whose trigger event
   matches. The rule's pack must be live and enabled for the client, and the
   rule must cover the property's jurisdiction and the case severity, defect
   code or certificate type. The job loads the matching holiday calendar.
3. `caseward.deadlines.compute_due_at` works out `due_at`. The database
   checks the rule, pack, subject and calendar before storing the row.
4. A `case_event` whose kind is the rule's completion event sets
   `completed_at`, and the status becomes `met` or `missed`. A sweeper marks
   overdue open deadlines as `missed`. Missed deadlines are never hidden.
5. Rule changes create new versions that take effect from a set date.
   Running deadlines keep the version they were created under.

## API

Specified in [api/](api/): `client.yaml`, `operations.yaml` and shared
`common.yaml`, with conventions in [api/README.md](api/README.md). Tests keep
the spec's enums in step with the database.

## Not yet designed

- Extraction JSON schemas per certificate type. These need the compliance
  expert's specs.
- Reporting queries (deadline performance by hazard, region and contractor).
  Region needs a decision: local authority, from postcode lookup?
