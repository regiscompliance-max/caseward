# 0008: AI extraction, and xAI Grok behind a residency gate

Date: 30 Oct 2026. Status: proposed. **The provider stays off until UK
processing is confirmed in writing.**

## What

- **One place calls a model:** `caseward.ai.provider`. The founder chose
  xAI's Grok, which uses the common chat-completions format, so the adapter
  also works with other providers that use it.
- **Residency gate.** A provider cannot be created without:
  - `CASEWARD_AI_RESIDENCY_APPROVAL`: the reference to the signed agreement
    that confirms UK processing and no training on our data;
  - `CASEWARD_AI_ALLOWED_HOSTS`: the endpoint hosts that agreement covers.
  There is no default endpoint. Without both, extraction jobs wait in the
  queue and nothing is sent anywhere.
- **Versioned prompt and schema.** The prompt (`certificate_extraction` v1)
  and output schemas (`eicr` v0, `gas_safety` v0, both **UNVERIFIED
  drafts**) are files in the repository. Every extraction records the
  provider, the exact model version, the prompt id and version, and the
  schema version.
- **Our own checks decide the flags**, not just the model's confidence:
  - missing critical fields;
  - confidence below 0.9;
  - dates that don't exist or don't make sense;
  - "Satisfactory" alongside C1/C2/FI or ID/AR, and the reverse;
  - a certificate postcode that differs from the property's.
  Output that fails the schema is discarded, and the reviewer works from
  the page images instead.
- **Every result is a draft.** A `review_extraction` task is queued, with
  priority 1 and a 1-hour SLA if a C1 or ID is present. Only
  `POST /extractions/{id}/review` creates inspections and defects, which
  then queue their own deadline calculation.
- **Certificate types without a reviewed schema** (fire, water, lift,
  asbestos for now) create a manual task instead of an AI draft.
- **Evaluation harness:** `python -m caseward.ai.evaluate <labelled set>`
  scores critical fields and fails below 98% (robustness rule 3).

## Why

Research on 30 Oct 2026 found the following. It needs confirming with xAI
directly, because `docs.x.ai` was not reachable from the build environment.

- xAI documents a global endpoint and regional endpoints (a US one is
  documented, and EU names appear in examples). It says requests may be
  routed between regions, so a regional endpoint alone does not guarantee
  where data is processed.
- **No UK region was found.** Residency commitments are arranged through
  xAI sales, under contract.
- Third-party sources disagree on whether zero data retention is on by
  default. xAI's enterprise terms are the authority.
- Grok models are also listed on Amazon Bedrock. If a Bedrock offering is
  available in eu-west-2 (London), that may be the simplest route to UK
  processing under existing AWS terms. Check this.

So the brief's requirement ("All client data stored and processed in the
UK", "UK GDPR-compliant terms with no training on our data") is not yet
shown to be met for Grok. The gate makes that a configuration fact signed
off by a person, not an assumption in code.

## Alternatives considered

- **Hard-code xAI's global endpoint.** That breaks the UK-processing rule
  as far as we can tell today.
- **Send page images to a multimodal model.** It might be more accurate on
  poor scans, but it sends more data. Revisit with the evaluation harness
  once the labelled set exists.

## Consequences

- Before real data: a written UK processing and no-training commitment from
  xAI, or from whichever route is chosen (direct or Bedrock). Record its
  reference in `CASEWARD_AI_RESIDENCY_APPROVAL`.
- The 98% gate cannot be measured until the compliance expert's labelled set
  exists (at least 30 documents per pack).

## Amendment (30 Oct 2026)

The founder chose **Amazon Bedrock**. In-region inference in London is
documented for Claude models, and the `BedrockProvider` allows only
in-region model IDs in eu-west-2 (decision 0013). The Grok adapter stays but
is not configured.

