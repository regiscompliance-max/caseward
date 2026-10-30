# 0004: Case events as the timeline and evidence log

Date: 30 Oct 2026. Status: proposed.

## What

`case_event` records everything that happens on a case, using event types
from configuration:

- awareness;
- making safe, investigation, written summary, works started and completed;
- access attempts, refusals, missed appointments, supply delays;
- alternative accommodation offers, acceptances and refusals, and moves out
  and back;
- notes.

Each event has an occurrence time, channel, evidence document, the
contractor involved and who recorded it. Events are append-only: a mistake
is corrected by a new event that names the one it corrects. Event types that
can support a reasonable excuse defence are flagged.

Deadline rules name a trigger event and a completion event. Recording a
completion event meets the case's open deadlines under that rule
automatically.

## Why

The brief needs a full case timeline that can be exported for the Housing
Ombudsman, the regulator or a court, plus evidence for a reasonable excuse
defence, plus alternative accommodation tracking. One append-only log covers
all three, and also drives deadlines, so the timeline and deadline status
cannot disagree.

## Alternatives considered

- **Separate tables for access attempts, accommodation offers and so on.**
  More structure per type, but every new duty would need a new table, which
  goes against decision 0002. Structured details can be added per event type
  in a JSON column with a schema if needed.
- **Milestone timestamp columns on the case.** Simple, but loses repeats
  (several access attempts) and the history of who recorded what.
