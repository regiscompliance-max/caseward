HAND-OFF NOTE

Role: Backend engineer (letters)

Date: 30 Oct 2026

What I completed (with links to files or pull requests):
- `backend/src/caseward/letters.py`: merge fields, template checking,
  rendering, and `[[...]]` gap markers.
- `backend/src/caseward/ai/prompts/letter_summary.v1.txt`: one
  plain-English paragraph from the facts only, with no added dates or
  promises.
- `backend/src/caseward/worker/letters.py`: the `draft_letter` job. It
  merges facts, adds the AI summary when approved and requested (never
  sending tenant names or contacts), stores version 1 with its origin and
  model versions, and queues an `approve_letter` task.
- Operations API:
  - letters: create, get, new version (with conflict detection), approve
    (four eyes, latest version only, no gaps left), mark sent (records the
    template's case event), void;
  - templates: list, create (validated, auto-versioned), approve (another
    compliance lead).
- Client API: `listLetters` and `getLetter`, approved or sent only.
- `backend/db/migrations/0006_letters.sql`:
  - template author, case event and freeze guard;
  - `template` as a version origin;
  - landlords can see the providers they engage.
- Spec: template `case_event_kind` and `created_by`, a `template` origin,
  409 and 422 where they now occur, and a nullable template `rule_pack`
  (the response check caught this).

Tests added and their status:
- `backend/tests/test_letters.py`, 14 tests. They cover:
  - templates: unknown fields refused, a second compliance lead required,
    approved templates frozen, versions numbered automatically, drafts not
    usable;
  - drafting: facts merged, the AI summary added, no tenant name sent to
    the model, a template-only draft leaving a gap that blocks approval, an
    approval task queued;
  - editing and approval: version conflicts, no self-approval, latest
    version only, no edits after approval, the task completed;
  - sending: the written-summary deadline met, sent letters can't be voided,
    only approved letters can be sent;
  - recipients and visibility: only engaged contractors, clients seeing
    approved letters only, providers seeing none.
- `backend/tests/test_rls.py`: landlords see only the providers they
  engage.
- 214 tests in total, all passing, including from a clean install.

Decisions made (and decision record links):
- `docs/decisions/0009-letters.md`

Open questions for the founder:
1. Should Caseward send letters itself (post through a print-and-post
   service, email), or do your team send them and record it here, as now?
2. Who should be allowed to approve letters: any case handler other than
   the author (current), or only senior handlers or compliance leads?
3. Which languages do you need first for translations?

Risks or problems found:
- Letter wording, including the summary prompt, needs the compliance
  expert's review. The brief asks for plain English and accessible formats.
- Case notes are sent to the AI provider when drafting a summary. Staff
  should not put tenant names in notes. Consider a check for this.

What the next role needs to do next:
- Compliance expert: letter templates for each Awaab's Law step (written
  summary, updates, completion), with the case event each one records.
- Frontend: the letter editor (version history, [[...]] gaps highlighted,
  approve and send).
- Backend: the rest of the work queue (`claimNextTask`, `updateTask`,
  `completeTask`), quality sampling and scoring, and the dashboard.
