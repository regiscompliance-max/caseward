HAND-OFF NOTE

Role: AI extraction engineer and backend engineer (extraction and review)

Date: 30 Oct 2026

What I completed (with links to files or pull requests):
- `backend/src/caseward/ai/provider.py`: the single model gateway, a Grok
  (xAI) adapter in chat-completions format with strict JSON schema output,
  and the **residency gate** (no approval reference or host allowlist means
  no provider).
- `backend/src/caseward/ai/prompts/certificate_extraction.v1.txt`: treats
  certificate text as data, not instructions; UK date order; never guess.
- `backend/src/caseward/ai/schemas/eicr.v0.json` and `gas_safety.v0.json`:
  **UNVERIFIED** drafts.
- `backend/src/caseward/ai/extraction.py`: extraction plus our own checks
  (missing, low confidence, invalid or impossible dates, contradictions,
  postcode mismatch).
- `backend/src/caseward/ai/evaluate.py`: harness that scores critical fields
  against a labelled set, with a 98% gate.
- `backend/src/caseward/worker/extraction.py`: the `extract` job, which
  drafts, flags and queues a review task (urgent for C1 or ID).
- Operations API: `getExtraction` and `reviewExtraction`. Approving creates
  the inspection and defects, links the property if needed, and completes
  the task.
- Client API: `listInspections`, `getInspection`, `listDefects`,
  `getDefect`.
- `backend/db/migrations/0005_extraction.sql`: flags on extractions, one
  review per extraction.

Tests added and their status:
- `backend/tests/test_extraction.py`, 20 tests. They cover:
  - the residency gate, request shape, model version recording, and
    provider errors that never include the response body;
  - the schema sent to the model;
  - each check;
  - output that breaks the schema being discarded;
  - prompt injection framing;
  - the evaluation gate;
  - end to end: draft and urgent task, correction then client view, review
    once by the right roles, schema-checked approvals, a property required,
    flags raising priority, unsupported types going to a person, and an AI
    outage leaving documents waiting.
- 199 tests in total, all passing, twice from a clean install.

Decisions made (and decision record links):
- `docs/decisions/0008-ai-extraction-and-provider.md`

Open questions for the founder:
1. **xAI and UK processing.** I found no documented UK region for xAI's API.
   xAI says regional endpoints alone don't guarantee where processing
   happens, and residency is arranged through its sales team. Will you ask
   xAI for a written UK processing and no-training commitment, or check
   whether Grok on Amazon Bedrock in London covers it? Until then
   extraction stays off.
2. Review SLAs: 1 hour when a C1 or ID is present, otherwise 24 hours.
   These are placeholders. Please confirm them.

Risks or problems found:
- The schemas and prompt are drafts. The compliance expert's field lists and
  the labelled test set are needed before measuring the 98% gate.
- Only the text is sent to the model, not page images. Accuracy on poor
  scans depends on OCR quality.

What the next role needs to do next:
- Compliance expert: EICR and gas field lists, and at least 30 labelled
  synthetic documents per pack, in the format in `caseward/ai/evaluate.py`.
- Founder: the provider residency decision above.
- Frontend: the review screen, with page images beside the editable draft,
  flags shown with their reasons, and approve, correct or reject.
- Backend: letters (drafting, editing, approval, sending), then the rest of
  the work queue (`claimNextTask`, `completeTask`).
