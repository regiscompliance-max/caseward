# 0002: Rule packs as configuration

Date: 30 Oct 2026. Status: proposed.

## What

Everything sector-specific is a row belonging to a `rule_pack`:

- certificate types
- defect codes (stored as scheme plus code, for example `eicr:C2`)
- hazard categories, with their phase and in-scope date
- event types
- vulnerability factor types
- deadline rules
- letter templates

There are no CHECK lists or code paths per sector. Rule packs:

- start `off`;
- go `live` only when an active compliance lead approves;
- apply to a client only from the date in `client_rule_pack`.

Versioning is per item: deadline rules and letter templates have versions
with effective dates. A rule pack "release" is the set of new versions that
share an `effective_from` date.

## Why

The brief requires new sectors to be added as configuration (MVP item 9), and
rule changes to go live from a set date without changing running cases.
Deadlines record the exact rule version they were created under, so a new
version never alters a running case.

## Alternatives considered

- **Whole-pack versions (`rule_pack_version` containing every item).** These
  are cleaner to approve as one unit, but every small correction would need a
  new pack version, and approval would happen at two levels. We can add this
  later as a grouping over item versions if regulators expect it.
- **Rules in YAML files in the repo.** Easy to review, but the approval and
  effective dates would live outside the audit trail. YAML can still be the
  authoring format for the compliance expert, loaded through a reviewed
  import.

## Consequences

- No legal timescales are seeded. Hazard `phase` and `in_scope_from` are left
  empty until the compliance expert drafts them and a qualified person
  confirms them. All timescales stay UNVERIFIED until then.
- The private-rented Awaab's Law pack exists, but stays `off` until
  regulations are made.
