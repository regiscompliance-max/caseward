HAND-OFF NOTE

Role: Backend engineer (administration, tenants)

Date: 30 Oct 2026

What I completed (with links to files or pull requests):
- **Found and fixed a sign-in bug that only production would have shown.**
  - The tests ran migrations as a superuser, which hid that the sign-in
    lookups could not see users under forced row-level security. Nobody
    could have signed in to a real deployment.
  - The tests now run migrations as a non-superuser owner.
  - The sign-in lookups now run as a dedicated `caseward_auth` role
    (decision 0010).
- `backend/db/migrations/0009_administration.sql`: the `caseward_auth`
  role, its policies, and `claim_invitation`.
- `backend/src/caseward/api/handlers/admin.py`, operations API:
  - clients (list, create);
  - engagements (list, create, end), refusing overlaps and non-providers;
  - users (list, invite, update with `row_version`), with no self-lockout
    and no staff with client memberships;
  - memberships (put, delete).
- `backend/src/caseward/api/handlers/tenants.py`: tenancies and contacts
  (landlords and staff only), and vulnerability list, record and end
  through the special-category functions. 403 without access, a reason
  header required, and audited once, with the reason.
- Sign-in: first sign-in with a verified email claims the invitation, once.
- Spec: `row_version` on users and tenant contacts.

Tests added and their status:
- `backend/tests/test_admin.py`, 12 tests. They cover:
  - onboarding a landlord end to end (client, invite, membership, first
    sign-in);
  - verified email required, and an invitation claimed once;
  - admin-only actions, duplicates refused, version checks, no
    self-lockout, staff never members;
  - engagements giving and removing contractor access;
  - a removed membership removing access;
  - tenant contacts, with providers refused;
  - special-category access, reason, roles, grant and ending.
- **The whole suite now runs as a non-superuser owner.** 251 tests, all
  passing, twice in a row and from a clean install.

Built so far: client API 31 of 37, operations API 63 of 80.

Not built yet:
- Client: `updateCase`, `updateDefect`, exports (create, status, download).
- Operations: `updateCase`, link and unlink defects, `updateDefect`,
  `cancelDeadline`, `rerunExtraction`, `createExport`, audit search and
  verification, job summary and retry.

Decisions made (and decision record links):
- `docs/decisions/0010-database-owner-and-sign-in.md`

Open questions for the founder:
1. Should client admins manage their own users (invite colleagues, set
   roles), or only Caseward admins, as now?
2. When someone is given special-category access, the spec says their
   client admin is notified. Notifications aren't built. Who should be
   told, and how?

Risks or problems found:
- Client identity providers must send `email` and `email_verified`.
  Confirm this for each SSO integration.

What the next role needs to do next:
- DevOps: create the production owner role as in decision 0010 (no
  superuser, no BYPASSRLS).
- Backend: the remaining case, defect and deadline updates, then exports
  (Ombudsman evidence pack), audit search and job monitoring.
