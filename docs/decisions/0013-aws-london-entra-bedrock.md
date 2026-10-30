# 0013: AWS London hosting, Microsoft 365 sign-in, Bedrock for AI

Date: 30 Oct 2026. Status: accepted by the founder (hosting on AWS, sign-in
with Microsoft 365, AI on Amazon Bedrock).

## What

- **Hosting: AWS London (eu-west-2).**
  - ECS Fargate for the client API, operations API, console and worker. The
    worker image includes Tesseract, with ClamAV as a sidecar container.
  - RDS for PostgreSQL 16: Multi-AZ, KMS-encrypted, point-in-time recovery.
    The schema owner is not a superuser (decision 0010).
  - S3 for documents: private, KMS, versioned.
  - Amazon WorkSpaces in London for the Nairobi team, with clipboard,
    printing and drive sharing off. The console and operations API are
    reachable only from that network.
  - An Application Load Balancer and AWS WAF in front of the client portal
    and client API only.
  - Infrastructure in Terraform, with separate AWS accounts for dev, test
    and production. Deploys from GitHub Actions use OIDC (no stored keys).
- **Staff sign-in: Microsoft 365 (Entra ID)**, through the several-provider
  sign-in (decision 0010). The API registration issues v1 access tokens,
  which carry `amr`, and Conditional Access requires MFA. Setup steps are in
  `docs/deployment/entra-id.md`.
- **AI: Amazon Bedrock, in-region in London only.**
  `caseward.ai.provider.BedrockProvider` refuses:
  - any region but eu-west-2;
  - cross-region and global profiles (`eu.`, `global.` and similar);
  - every inference profile ARN, because even an application profile in
    eu-west-2 can wrap a cross-region profile.
  Structured output uses the Converse API with one forced tool whose input
  schema is ours. The intended model is **Claude Sonnet 5**. AWS announced
  in-region London inference for Claude Opus 5.5 and Claude Sonnet 5 on 30
  Sep 2026, saying requests are processed within the Region called. The
  model ID is configuration (`CASEWARD_AI_MODEL`).
- **Cloudflare** hosts only the public preview (`site/`).

## Why

- The code already targets S3 with KMS in eu-west-2.
- AWS has every piece the brief needs in the UK: the database, storage,
  remote desktops for Nairobi, and an AI service with in-country
  processing.
- The founder chose Microsoft 365 and Bedrock.

## Before real data

- In the Bedrock console in eu-west-2, confirm the exact model ID and that
  it is listed as in-region, not just through a profile. Check against AWS's
  "Regional availability by models" page.
- Record the AWS agreement reference (customer agreement and data processing
  addendum) in `CASEWARD_AI_RESIDENCY_APPROVAL`.
- One real Microsoft 365 sign-in, checking `iss`, `aud` and `amr` (see the
  setup guide).
- Run the evaluation harness (98% gate) with the chosen model against the
  compliance expert's labelled set.

## Consequences

- Grok is no longer the planned provider. Its adapter stays, behind the same
  residency gate, but is not configured.
- If a staff member ever needs to use the console outside WorkSpaces, that is
  a separate decision.
