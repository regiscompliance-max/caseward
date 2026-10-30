HAND-OFF NOTE

Role: Backend engineer (configuration)

Date: 30 Oct 2026

What I completed (with links to files or pull requests):
- `backend/src/caseward/api/handlers/configuration.py`, operations API:
  - **Deadline rules:** list (all versions), draft (versions number
    themselves), get (ETag), edit drafts (If-Match), approve, retire, dry
    run.
  - **Approving a rule:**
    - only by a compliance lead who is not the author;
    - effective from today or later, never backdated;
    - records the qualified person's confirmation;
    - must name the version it replaces, which ends on the same day.
    Approved rules are frozen.
  - **Dry run** (`POST /deadline-rules/{id}/test`): works on drafts, so the
    compliance expert can check rules against their test cases before
    approval.
  - **Rule packs:** list, and switch on (a compliance lead, with
    confirmation recorded; refused while any rule in the pack has no
    approved version). Enabled per landlord from a date.
  - **Bank holidays:** list calendars, and import from GOV.UK, which
    creates a new version only for regions whose dates changed.
  - **Code lists:** hazard phase and in-scope date (If-Match on
    `row_version`); add certificate types, defect codes, event types and
    vulnerability factor types.
- Client API: `listCertificateTypes` (with defect codes).
- `backend/db/migrations/0008_configuration.sql`: `approval_reference` on
  rules and packs, required once approved or live.
- Spec: `approval_reference` on rules and packs, `row_version` on hazard
  categories, and nullable `legal_reference` and `day_type` (the response
  check caught these).

Tests added and their status:
- `backend/tests/test_configuration.py`, 12 tests. They cover:
  - the draft-to-approved lifecycle: edit conflicts, no self-approval, no
    backdating, frozen once approved;
  - only compliance leads approve;
  - versioned replacement from a date, and retiring;
  - dry runs on drafts, for working days and hours;
  - inconsistent rules refused;
  - **an approved rule creating a real deadline through the worker**;
  - packs refusing to go live with unapproved rules, and enabled per
    landlord;
  - holiday import only where dates changed;
  - hazard phases with a version check;
  - code lists growing by configuration and appearing in the client
    reference.
- 239 tests in total, all passing, three runs in a row and from a clean
  install.

Decisions made (and decision record links):
- None new. This implements decisions 0002 (rule packs as configuration)
  and 0006 (worker).

Open questions for the founder:
1. Who is the qualified person who confirms deadline rules, and what should
   the recorded confirmation look like (for example a solicitor's written
   sign-off reference)?
2. Should switching on a rule pack need a second compliance lead, as rule
   approval does? Today one is enough.

Risks or problems found:
- Phase 2 hazards begin on 30 November 2026, according to the brief. For
  those deadlines to exist from that date, rules and hazard phases need to
  be loaded and approved before then.

What the next role needs to do next:
- **Compliance expert and founder:** load the Awaab's Law rules through
  these endpoints, check each with a dry run against your test cases, then
  approve. Set hazard phases, import bank holidays, switch on
  `awaab_social` and enable it for each landlord.
- Backend: administration (clients, engagements, users, memberships),
  tenant and vulnerability endpoints, case and defect updates, deadline
  cancel, exports, audit search and job monitoring.
