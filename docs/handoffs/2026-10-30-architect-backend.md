HAND-OFF NOTE

Role: Solution architect and backend engineer (foundations)

Date: 30 Oct 2026

What I completed (with links to files or pull requests):
- Brief saved as `docs/BRIEF.md`; full pack in `docs/TEAM-INSTRUCTIONS.md`.
- `docs/ARCHITECTURE.md`: components, security boundaries, data model,
  access matrix, deadline lifecycle.
- `backend/db/migrations/0001_foundation.sql`:
  - full schema with row-level security per client;
  - provider engagements;
  - rule packs and sector configuration;
  - case timeline;
  - special-category data access;
  - deadline engine checks;
  - letter approval;
  - quality review rules;
  - hash-chained audit log;
  - job queue.
- `backend/src/caseward/`: request-scoped database context, audit of views
  and exports, deadline calculator, GOV.UK bank holiday loader.
- Branch `feat/foundation-schema`; no pull request yet.

Tests added and their status:
- 66 tests, all passing against Postgres 16 as non-superuser roles: row-level
  security, integrity rules, audit chain and tampering detection, job queue,
  deadline calculation, rule packs, case events, special-category access.
- CI: `.github/workflows/ci.yml`.

Decisions made (and decision record links):
- `docs/decisions/0001-architecture-and-stack.md`
- `docs/decisions/0002-rule-packs-as-configuration.md`
- `docs/decisions/0003-special-category-data.md`
- `docs/decisions/0004-case-events-as-evidence.md`

Open questions for the founder:
1. Deadline counting conventions in `backend/src/caseward/deadlines.py` (end
   of day 23:59:59 UK time; the start day is not counted; month arithmetic).
   These need a qualified person's confirmation alongside the timescales.
2. Should contractor and managing agent access cover a landlord's whole
   portfolio (current) or only named properties?
3. Region for regulator reporting: local authority from postcode, or the
   client's own regions?
4. Which cloud provider and identity provider? The design is portable.
5. Supported and temporary accommodation providers: which client kind and
   rule pack? (The brief lists their scope as "to confirm".)
6. Can client users ever see vulnerability data, or only Caseward staff?
   Currently it is per-user opt-in by an admin.

Risks or problems found:
- No legal timescales or hazard phases are loaded. Phase 2 starts on 30 Nov
  2026 according to the brief, so the compliance expert's rule specs are on
  the critical path.
- The hash chain serialises audit writes. This is fine at pilot volumes;
  monitor it.
- `can_access_client` runs per row. Check query plans with realistic data.
- Vulnerability data is protected by access control only. Field-level
  encryption is recommended before real data arrives.

What the next role needs to do next:
- Product lead: turn the MVP into stories in `docs/BACKLOG.md`, using the
  schema as the shared vocabulary.
- Compliance expert: write rule pack specs in `docs/domain/<pack>/` as rows
  for `deadline_rule`, `event_type`, `hazard_category` (phase,
  `in_scope_from`), `defect_code`, `certificate_type` and
  `vulnerability_factor_type`, all marked UNVERIFIED.
- Architect (next session): API specification (OpenAPI) and extraction JSON
  schemas per certificate type.
- Security lead: review decisions 0001 and 0003, the access matrix in
  `ARCHITECTURE.md`, and the DPIA inputs.
- Backend: FastAPI skeleton on top of `scoped_transaction`, then the
  `compute_deadlines` job and the missed-deadline sweeper.
