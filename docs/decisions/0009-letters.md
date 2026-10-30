# 0009: Letters

Date: 30 Oct 2026. Status: proposed.

## What

- **Templates** are plain text with a fixed list of merge fields
  (`caseward.letters.MERGE_FIELDS`). Unknown fields are refused when a
  template is saved. Templates are versioned per key, language and variant
  (standard, large print, easy read). A compliance lead other than the
  author approves them, and approved templates are frozen.
- **Facts come from data, never from AI.** Names, the address, the case
  reference, the hazard, dates and the next deadline are merged from the
  database. AI writes only `{{ ai_summary }}`: one plain-English paragraph
  (prompt `letter_summary` v1). It is given the case facts and notes but
  **never the tenant's name or contact details**. Without an approved AI
  provider, or with `ai_draft: false`, the draft is template only.
- **Gaps are visible.** Anything that can't be filled becomes a `[[...]]`
  marker, and a letter cannot be approved while any remain.
- **Four eyes.**
  - Every version is kept (append-only). Edits must start from the latest
    version, so two people can't overwrite each other.
  - Only the latest version can be approved, and never by its author
    (checked in the database).
  - After approval the text can't change. To change it, void the letter and
    start again.
- **Sending is recorded, not done by Caseward yet.** `POST /letters/{id}/sent`
  records when and how it was sent, with delivery evidence. If the template
  names a case event (for example `written_summary_sent`), that event is
  logged, which can meet a deadline.
- **Contractor instructions** go only to contractors the landlord currently
  engages. To support this, landlords can now see the client records of
  their engaged providers (migration 0006).
- **Clients see approved and sent letters only.** Providers see no letters.

## Why

- Robustness rule 1 (AI output is a draft until a person approves it), plus
  the brief's "always human-approved before sending".
- Keeping legal dates out of AI output means a model can't invent a
  timescale.
- Linking "sent" to the case event makes the written-summary deadline and
  the letter one fact, not two that can disagree.

## Alternatives considered

- **AI writes the whole letter from the template.** It reads better, but
  facts and dates could drift, and every letter would need a line-by-line
  check against the record.
- **A full template language (Jinja).** More flexible, but templates are
  approved legal wording, and conditionals make them harder to approve. Add
  later if needed.

## Consequences

- Posting, email delivery and PDF rendering are not built. Staff send
  through existing channels and record it here.
- Translations are separate template variants per language. The text needs
  a qualified translator.
