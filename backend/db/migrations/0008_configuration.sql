-- 0008_configuration.sql
--
-- Record the qualified person's confirmation against every rule approval and
-- every rule pack switched on.

begin;

alter table caseward.deadline_rule
  add column approval_reference text,
  add check (status = 'draft' or approval_reference is not null);

alter table caseward.rule_pack
  add column approval_reference text,
  add check (status = 'off' or approval_reference is not null);

commit;
