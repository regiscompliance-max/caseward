-- 0006_letters.sql
--
-- Template authorship and approval, the case event a sent letter records,
-- and drafts made from a template alone (no AI).

begin;

alter table caseward.letter_template
  add column created_by uuid references caseward.app_user (id),
  -- Recording a letter from this template as sent logs this case event,
  -- e.g. written_summary_sent, which can meet a deadline.
  add column case_event_kind text,
  add column case_event_subject text not null default 'case' check (case_event_subject = 'case'),
  add foreign key (case_event_kind, case_event_subject) references caseward.event_type (code, subject_kind),
  add check (approved_by is null or approved_by is distinct from created_by);

alter table caseward.letter_version drop constraint letter_version_origin_check;
alter table caseward.letter_version
  add constraint letter_version_origin_check check (origin in ('template', 'ai_draft', 'human_edit')),
  add check (origin <> 'template' or created_by is null);

-- Approved templates are frozen apart from retiring them, and only an active
-- compliance lead other than the author approves.
create function caseward.letter_template_guard() returns trigger
language plpgsql as $$
begin
  if tg_op = 'UPDATE' and old.status <> 'draft' then
    if old.status = 'retired' or new.status not in ('approved', 'retired')
       or (to_jsonb(new) - 'status') <> (to_jsonb(old) - 'status') then
      raise exception 'letter template %.v% is %; create a new version instead', old.template_key, old.version, old.status;
    end if;
  end if;
  if new.status = 'approved' and (tg_op = 'INSERT' or old.status = 'draft') and not exists (
       select 1 from caseward.app_user u
       where u.id = new.approved_by and u.staff_role = 'compliance_lead' and u.is_active) then
    raise exception 'letter templates must be approved by an active compliance lead';
  end if;
  return new;
end $$;

create trigger guard before insert or update on caseward.letter_template
  for each row execute function caseward.letter_template_guard();

-- Landlords can see the contractors, agents and legal teams they engage (to
-- address instructions to them, or show who a case is assigned to). Before
-- this only the provider could see the landlord.
drop policy client_access on caseward.client;
create policy client_access on caseward.client using (
  caseward.can_access_client(id)
  or exists (select 1 from caseward.engagement e
             where e.landlord_client_id = caseward.current_client_id() and e.provider_client_id = client.id));

commit;
