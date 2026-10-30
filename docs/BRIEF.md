# Caseward master brief

Every role reads this first. Source: *Caseward — AI Build Team Instructions* (30 Oct 2026). The full pack, including the role prompts and build sequence, is in [TEAM-INSTRUCTIONS.md](TEAM-INSTRUCTIONS.md).

## Master brief

You are part of an AI build team creating Caseward, a compliance operations platform that covers every duty under Awaab's Law for UK social landlords, their contractors and managing agents, with rule packs for private rented, block safety and NHS estates compliance.  
  
WHAT CASEWARD DOES  
Landlords and contractors send us compliance certificates (EICRs, gas safety records, fire risk assessments, water and lift reports) and tenant hazard reports. AI reads each document, extracts defects, codes and dates, sets legal deadlines and drafts letters. A trained human team in Nairobi checks every AI output, handles exceptions, chases contractors and calls tenants. Results go back to the client with a full audit trail.  
  
WHO USES IT  
1. Case handlers (Nairobi): work a queue, check AI drafts, edit and approve.  
2. Quality analysts: sample completed work and score it.  
3. Client users (UK landlords and contractors): see cases, deadlines, certificates and evidence; download reports.  
4. Admins: manage clients, users, deadline rules and templates.  
  
MVP SCOPE (build only this first)  
1. Document intake: upload PDF, image or email attachment; store securely.  
2. Certificate reader: AI extraction to a fixed JSON schema with a confidence score per field.  
3. Operations console: work queue sorted by deadline; side-by-side view of the document and extracted fields; approve, edit or reject.  
4. Deadline engine: rules stored as configuration, never hard-coded; working-day calendar including UK bank holidays.  
5. Letter drafting: tenant letters and contractor instructions from templates plus AI, always human-approved before sending.  
6. Full Awaab's Law case management for every hazard phase (see the coverage section), plus a simple tracker for clients without their own system..  
7. Client portal: read-only dashboard and CSV/PDF export.  
8. Audit log of every view, edit, approval and export.  
9. Sector rule packs: all certificate types, codes, deadline rules, letters and reports are loaded per sector pack, so new sectors are added as configuration, not new code.  
  
NON-NEGOTIABLES  
- All client data stored and processed in the UK (UK cloud region). Nairobi staff access only through secure remote access; no downloads to local devices.  
- UK GDPR: data minimisation, role-based access, encryption in transit and at rest, retention rules, full audit trail.  
- No AI output reaches a tenant or client without human approval.  
- Every AI extraction stores the model version, prompt version, confidence and the human decision.  
- Deadline rules are legal requirements: they live in versioned configuration and must be confirmed by a qualified person before use.  
- Use only synthetic or anonymised test documents until the security gate is passed.  
- Plain, accessible UI (WCAG 2.2 AA). British English throughout.  
  
WORKING RULES FOR ALL ROLES  
- Read /docs/BRIEF.md and the latest notes in /docs/handoffs/ before starting.  
- Never guess requirements: list open questions in your hand-off note.  
- Write tests with your code. Nothing is done until tests pass.  
- Keep decisions in /docs/decisions/ as short records (what, why, alternatives).  
- Finish every session with a hand-off note using the template in this pack.

## Architecture and data model

A suggested starting point for the architect role to confirm or change. It is kept as text so it can be pasted straight into the master brief.

RECOMMENDED STACK (architect may propose changes with reasons)  
- Frontend: Next.js + TypeScript, accessible component library.  
- Backend API: Python + FastAPI.  
- Database: PostgreSQL (UK region), with row-level security per client.  
- File storage: UK-region object storage, encrypted, private buckets only.  
- Background jobs: queue + workers for extraction and letter drafting.  
- OCR: for scanned documents before AI extraction.  
- AI models: called through one internal service so models and prompts can be swapped and versioned; the provider must offer UK GDPR-compliant terms with no training on our data.  
- Auth: single sign-on for client users, multi-factor authentication for everyone, role-based permissions.  
- Hosting: UK cloud region; infrastructure as code; separate dev, test and production.  
  
CORE DATA MODEL  
- Client (landlord or contractor), Property (address, UPRN), Tenant contact (minimal fields only)  
- Document (file, type, source, received date, client, property)  
- Extraction (document, JSON result, per-field confidence, model version, prompt version)  
- Defect (code C1/C2/C3/FI/ID/AR/NCS/Other, description, location, status)  
- Case (hazard category, severity, received date, status)  
- Deadline (case or defect, rule id + rule version, due date, met/missed)  
- Task (queue item, assignee, status, SLA)  
- Letter (template, draft, approved text, approver, sent date)  
- QualityReview (task, reviewer, score, notes)  
- AuditEvent (who, what, when, from where)  
- DeadlineRule (versioned config: trigger, working or calendar days, duration, approved by, effective from)

The architect's decisions on this starting point are in [ARCHITECTURE.md](ARCHITECTURE.md) and [decisions/](decisions/).

## Coverage: everything Awaab's Law, plus other sectors

Caseward must support every duty Awaab's Law places on landlords, for every type of organisation it affects. Add this section to the master brief. All timescales below are **unverified**: the compliance expert drafts them from the regulations and a qualified person confirms them before use.

### What the system must handle

| Awaab's Law requirement                          | What Caseward must do                                                                                                                                                   |
|--------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| The clock starts when the landlord becomes aware | Record the awareness date from any channel: tenant call, email, repairs operative, contractor, surveyor or inspection                                                   |
| Emergency hazards                                | Flag immediately; track investigation and making safe within 24 hours                                                                                                   |
| Significant hazards                              | Track investigation within 10 working days, the written summary to the tenant within 3 working days of the investigation, and the start and completion of safety works  |
| Hazards widen by phase                           | Hazard categories held as configuration: Phase 1 (damp, mould, emergencies), Phase 2 from 30 November 2026, Phase 3 in 2027 (all remaining hazards except overcrowding) |
| Household circumstances affect risk              | Record vulnerability factors as special-category data, visible only to roles that need them                                                                             |
| Home cannot be made safe in time                 | Track offers of alternative accommodation, acceptance or refusal, and moves back                                                                                        |
| Defence of reasonable excuse                     | Log every access attempt, refusal, missed appointment and supply delay with evidence                                                                                    |
| Written communication                            | Templates for summaries, updates and completion letters, in plain English with accessible formats and translations                                                      |
| Evidence for complaints and claims               | Full case timeline with photos, reports and letters, exportable for the Housing Ombudsman, regulator or court                                                           |
| Board and regulator reporting                    | Deadline performance by hazard, region and contractor                                                                                                                   |

### Who it serves

- Housing associations and councils (including arm's-length management organisations)

- Supported and temporary accommodation providers, where the regulations apply to them (scope to confirm)

- Repairs, maintenance and compliance contractors working for social landlords

- Managing agents and block managers in mixed-tenure buildings with social tenants

- Legal teams handling disrepair claims

- Private landlords and letting agents: built now, switched off until regulations extend Awaab's Law to the private rented sector

### Rule packs

| Pack                        | Covers                                                                                                      | Status                                |
|-----------------------------|-------------------------------------------------------------------------------------------------------------|---------------------------------------|
| Awaab's Law: social housing | All three phases, timescales, letters, evidence                                                             | Build first                           |
| Social housing certificates | EICRs, gas, fire risk assessments, water, lifts, asbestos                                                   | Build first                           |
| Awaab's Law: private rented | Expected to mirror the social housing rules                                                                 | Draft; off until regulations are made |
| Block safety                | Building Safety Act duties for higher-risk buildings, fire door and lift checks, leaseholder communications | Phase 2                               |
| Private rented compliance   | Five-yearly electrical checks, gas, energy certificates, Renters' Rights Act duties                         | Phase 2                               |
| NHS estates                 | Health Technical Memoranda compliance; no patient data                                                      | Phase 3                               |

**Regulatory change process.** When rules change, the regulatory watch role drafts a new rule-pack version, the compliance expert updates the test set, a qualified person approves it, and it goes live from a set date without changing cases already running under the old rules.

## Robustness rules

Add these to the master brief once the build starts. They are what make the system dependable rather than just working.

ROBUSTNESS RULES  
1. Human in the loop: every AI result is a draft until a person approves it.  
2. Confidence routing: any field below the confidence threshold, or any contradiction (e.g. "Satisfactory" with a C2), goes to a human with the reason shown.  
3. Evaluation before release: no prompt or model change ships unless it scores at least as well as the current version on the labelled test set.  
4. Versioning: store model, prompt and deadline-rule versions on every record so any decision can be explained later.  
5. Fail safe: if the AI service is down, documents queue and wait; nothing is skipped and staff are alerted.  
6. Idempotency: uploading the same document twice never creates two cases.  
7. Deadline safety: alerts at 50%, 80% and 100% of every deadline; missed deadlines are reported, never hidden.  
8. Least privilege: users see only their client's data and only what their role needs.  
9. Observability: dashboards for queue size, processing time, AI accuracy, deadline performance and errors.  
10. Recovery: daily backups, tested restores, and a written plan for outages and data incidents.

## Hand-off note template

Every role ends each session by saving this in /docs/handoffs/:

HAND-OFF NOTE  
Role:  
Date:  
What I completed (with links to files or pull requests):  
Tests added and their status:  
Decisions made (and decision record links):  
Open questions for the founder:  
Risks or problems found:  
What the next role needs to do next:
