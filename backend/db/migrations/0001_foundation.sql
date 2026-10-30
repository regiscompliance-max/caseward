-- 0001_foundation.sql
--
-- Core schema for Caseward. See docs/data-model.md for the reasoning behind
-- each table and docs/adr/0001-architecture-and-stack.md for the stack.
--
-- Security model
--   * Run this migration as a dedicated NOLOGIN owner role. The API never
--     connects as the owner.
--   * Every request runs in a transaction that sets app.client_id,
--     app.user_id, app.actor_kind, app.request_id and app.source_ip with
--     set_config(..., true) (see backend/src/caseward/db.py).
--   * caseward_app      - client-facing API. Sees only its own client's rows,
--                         plus landlords it is engaged by (contractors).
--   * caseward_staff    - internal operations staff. Cross-client access,
--                         granted by role membership rather than a setting
--                         so a client-facing connection cannot escalate.
--   * caseward_worker   - background workers. Can claim any job; processes
--                         it under the job's client context.

begin;

create extension if not exists btree_gist;

do $$
begin
  if not exists (select from pg_roles where rolname = 'caseward_app') then
    create role caseward_app nologin;
  end if;
  if not exists (select from pg_roles where rolname = 'caseward_staff') then
    create role caseward_staff nologin;
  end if;
  if not exists (select from pg_roles where rolname = 'caseward_worker') then
    create role caseward_worker nologin;
  end if;
end $$;

create schema caseward;
revoke all on schema caseward from public;
grant usage on schema caseward to caseward_app, caseward_staff, caseward_worker;

-- ---------------------------------------------------------------------------
-- Context helpers (no table dependencies)
-- ---------------------------------------------------------------------------

create function caseward.current_client_id() returns uuid
language sql stable as $$
  select nullif(current_setting('app.client_id', true), '')::uuid
$$;

create function caseward.current_user_id() returns uuid
language sql stable as $$
  select nullif(current_setting('app.user_id', true), '')::uuid
$$;

create function caseward.is_staff() returns boolean
language sql stable as $$
  select pg_has_role('caseward_staff', 'MEMBER')
$$;

-- ---------------------------------------------------------------------------
-- Organisations, users and access
-- ---------------------------------------------------------------------------

create table caseward.client (
  id          uuid primary key default gen_random_uuid(),
  name        text not null check (length(trim(name)) > 0),
  kind        text not null check (kind in ('landlord', 'contractor')),
  created_at  timestamptz not null default now()
);

-- A contractor working for a landlord. Grants the contractor access to the
-- landlord's properties, documents, defects and cases (not tenant contacts
-- or letters) while the engagement is current.
create table caseward.engagement (
  id                    uuid primary key default gen_random_uuid(),
  landlord_client_id    uuid not null references caseward.client (id),
  contractor_client_id  uuid not null references caseward.client (id),
  starts_on             date not null default current_date,
  ends_on               date,
  created_at            timestamptz not null default now(),
  check (landlord_client_id <> contractor_client_id),
  check (ends_on is null or ends_on >= starts_on)
);
create index on caseward.engagement (contractor_client_id, landlord_client_id);

create table caseward.app_user (
  id            uuid primary key default gen_random_uuid(),
  idp_subject   text not null unique,
  email         text not null,
  display_name  text not null,
  staff_role    text check (staff_role in ('caseworker', 'quality_reviewer', 'compliance_lead', 'admin')),
  is_active     boolean not null default true,
  created_at    timestamptz not null default now()
);
create unique index app_user_email_key on caseward.app_user (lower(email));

create table caseward.membership (
  user_id     uuid not null references caseward.app_user (id),
  client_id   uuid not null references caseward.client (id),
  role        text not null check (role in ('client_admin', 'client_member', 'client_viewer')),
  created_at  timestamptz not null default now(),
  primary key (user_id, client_id)
);

-- ---------------------------------------------------------------------------
-- Properties and tenants
-- ---------------------------------------------------------------------------

create table caseward.property (
  id              uuid primary key default gen_random_uuid(),
  client_id       uuid not null references caseward.client (id),
  uprn            bigint check (uprn > 0 and uprn < 1000000000000),
  address_line_1  text not null,
  address_line_2  text,
  town            text,
  postcode        text not null,
  jurisdiction    text not null check (jurisdiction in ('england', 'wales', 'scotland', 'northern_ireland')),
  created_at      timestamptz not null default now(),
  unique (client_id, uprn),
  unique (id, client_id)
);

create table caseward.tenancy (
  id           uuid primary key default gen_random_uuid(),
  client_id    uuid not null,
  property_id  uuid not null,
  starts_on    date not null,
  ends_on      date,
  created_at   timestamptz not null default now(),
  unique (id, client_id),
  foreign key (property_id, client_id) references caseward.property (id, client_id),
  check (ends_on is null or ends_on >= starts_on)
);

-- Minimal fields only. Anything more needs a documented lawful basis.
create table caseward.tenant_contact (
  id                 uuid primary key default gen_random_uuid(),
  client_id          uuid not null,
  tenancy_id         uuid not null,
  full_name          text not null,
  phone              text,
  email              text,
  preferred_channel  text check (preferred_channel in ('post', 'email', 'phone', 'portal')),
  retain_until       date,
  legal_hold         boolean not null default false,
  created_at         timestamptz not null default now(),
  unique (id, client_id),
  foreign key (tenancy_id, client_id) references caseward.tenancy (id, client_id)
);

-- ---------------------------------------------------------------------------
-- Documents, OCR and extraction
-- ---------------------------------------------------------------------------

create table caseward.document (
  id                 uuid primary key default gen_random_uuid(),
  client_id          uuid not null references caseward.client (id),
  property_id        uuid,
  doc_type           text not null check (doc_type in (
                       'eicr', 'gas_safety_record', 'damp_mould_report', 'tenant_report',
                       'contractor_report', 'photo', 'correspondence', 'other')),
  source             text not null check (source in ('upload', 'email', 'portal', 'api')),
  received_at        timestamptz not null,
  storage_key        text not null unique,
  sha256             text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  mime_type          text not null,
  size_bytes         bigint not null check (size_bytes > 0),
  original_filename  text,
  malware_scan       text not null default 'pending' check (malware_scan in ('pending', 'clean', 'infected', 'error')),
  uploaded_by        uuid references caseward.app_user (id),
  retain_until       date,
  legal_hold         boolean not null default false,
  created_at         timestamptz not null default now(),
  unique (id, client_id),
  unique (client_id, sha256),
  foreign key (property_id, client_id) references caseward.property (id, client_id)
);

create table caseward.ocr_output (
  id                uuid primary key default gen_random_uuid(),
  client_id         uuid not null,
  document_id       uuid not null,
  engine            text not null,
  engine_version    text not null,
  text_storage_key  text not null,
  page_count        integer check (page_count > 0),
  mean_confidence   numeric(5, 4) check (mean_confidence between 0 and 1),
  created_at        timestamptz not null default now(),
  unique (id, client_id),
  foreign key (document_id, client_id) references caseward.document (id, client_id)
);

create table caseward.extraction (
  id                     uuid primary key default gen_random_uuid(),
  client_id              uuid not null,
  document_id            uuid not null,
  ocr_output_id          uuid,
  status                 text not null default 'pending' check (status in ('pending', 'running', 'succeeded', 'failed')),
  result                 jsonb,
  field_confidence       jsonb check (field_confidence is null or jsonb_typeof(field_confidence) = 'object'),
  model_provider         text not null,
  model_version          text not null,
  prompt_id              text not null,
  prompt_version         text not null,
  output_schema_version  text not null,
  error                  text,
  created_at             timestamptz not null default now(),
  completed_at           timestamptz,
  unique (id, client_id),
  foreign key (document_id, client_id) references caseward.document (id, client_id),
  foreign key (ocr_output_id, client_id) references caseward.ocr_output (id, client_id),
  check (status <> 'succeeded' or (result is not null and field_confidence is not null and completed_at is not null))
);

-- What a person accepted or corrected. Downstream records cite this, not the
-- raw model output.
create table caseward.extraction_review (
  id               uuid primary key default gen_random_uuid(),
  client_id        uuid not null,
  extraction_id    uuid not null,
  reviewer_id      uuid not null references caseward.app_user (id),
  decision         text not null check (decision in ('approved', 'corrected', 'rejected')),
  approved_result  jsonb,
  notes            text,
  reviewed_at      timestamptz not null default now(),
  unique (id, client_id),
  foreign key (extraction_id, client_id) references caseward.extraction (id, client_id),
  check (decision = 'rejected' or approved_result is not null)
);

-- ---------------------------------------------------------------------------
-- Inspections, defects and cases
-- ---------------------------------------------------------------------------

create table caseward.inspection (
  id                     uuid primary key default gen_random_uuid(),
  client_id              uuid not null,
  property_id            uuid not null,
  document_id            uuid,
  extraction_review_id   uuid,
  kind                   text not null check (kind in ('eicr', 'gas_safety')),
  inspected_on           date not null,
  next_due_on            date,
  engineer_name          text,
  engineer_registration  text,
  outcome                text not null default 'unknown' check (outcome in ('satisfactory', 'unsatisfactory', 'unknown')),
  created_at             timestamptz not null default now(),
  unique (id, client_id),
  foreign key (property_id, client_id) references caseward.property (id, client_id),
  foreign key (document_id, client_id) references caseward.document (id, client_id),
  foreign key (extraction_review_id, client_id) references caseward.extraction_review (id, client_id),
  check (next_due_on is null or next_due_on > inspected_on)
);

create table caseward.defect (
  id             uuid primary key default gen_random_uuid(),
  client_id      uuid not null,
  property_id    uuid not null,
  inspection_id  uuid,
  document_id    uuid,
  code           text not null check (code in ('C1', 'C2', 'C3', 'FI', 'ID', 'AR', 'NCS', 'Other')),
  description    text not null,
  location       text,
  status         text not null default 'open' check (status in (
                   'open', 'in_progress', 'remediated', 'verified', 'closed', 'not_required')),
  identified_on  date not null,
  closed_at      timestamptz,
  created_at     timestamptz not null default now(),
  unique (id, client_id),
  foreign key (property_id, client_id) references caseward.property (id, client_id),
  foreign key (inspection_id, client_id) references caseward.inspection (id, client_id),
  foreign key (document_id, client_id) references caseward.document (id, client_id)
);
create index on caseward.defect (client_id, status);

create table caseward.hazard_category (
  code    text primary key,
  name    text not null,
  scheme  text not null default 'hhsrs'
);

create table caseward.hazard_case (
  id                              uuid primary key default gen_random_uuid(),
  client_id                       uuid not null,
  property_id                     uuid not null,
  reference                       text not null,
  hazard_category                 text not null references caseward.hazard_category (code),
  severity                        text not null check (severity in ('emergency', 'significant', 'other')),
  received_at                     timestamptz not null,
  -- When the landlord became aware. This, not received_at, usually starts the clock.
  aware_at                        timestamptz not null,
  awareness_source                text not null check (awareness_source in (
                                    'tenant_report', 'inspection', 'contractor', 'third_party', 'other')),
  awareness_evidence_document_id  uuid,
  status                          text not null default 'open' check (status in (
                                    'open', 'investigating', 'awaiting_works', 'works_in_progress',
                                    'resolved', 'closed', 'withdrawn')),
  closed_at                       timestamptz,
  created_at                      timestamptz not null default now(),
  unique (client_id, reference),
  unique (id, client_id),
  foreign key (property_id, client_id) references caseward.property (id, client_id),
  foreign key (awareness_evidence_document_id, client_id) references caseward.document (id, client_id)
);
create index on caseward.hazard_case (client_id, status);

create table caseward.case_defect (
  client_id  uuid not null,
  case_id    uuid not null,
  defect_id  uuid not null,
  primary key (case_id, defect_id),
  foreign key (case_id, client_id) references caseward.hazard_case (id, client_id),
  foreign key (defect_id, client_id) references caseward.defect (id, client_id)
);

-- Written only by trigger.
create table caseward.case_status_change (
  id           bigint generated always as identity primary key,
  client_id    uuid not null,
  case_id      uuid not null,
  from_status  text,
  to_status    text not null,
  changed_by   uuid,
  changed_at   timestamptz not null default now(),
  foreign key (case_id, client_id) references caseward.hazard_case (id, client_id)
);

-- ---------------------------------------------------------------------------
-- Deadlines
-- ---------------------------------------------------------------------------

-- A new version replaces the calendar; old versions stay so past deadlines
-- can be recalculated exactly.
create table caseward.holiday_calendar (
  id            uuid primary key default gen_random_uuid(),
  region        text not null check (region in ('england-and-wales', 'scotland', 'northern-ireland')),
  version       integer not null check (version > 0),
  source        text not null,
  published_at  timestamptz not null default now(),
  unique (region, version)
);

create table caseward.bank_holiday (
  calendar_id   uuid not null references caseward.holiday_calendar (id),
  holiday_date  date not null,
  title         text not null,
  primary key (calendar_id, holiday_date)
);

-- Rule values are configuration approved by a compliance lead. Nothing here
-- hard-codes a legal timescale.
create table caseward.deadline_rule (
  id              uuid primary key default gen_random_uuid(),
  rule_key        text not null check (rule_key ~ '^[a-z0-9_.-]+$'),
  version         integer not null check (version > 0),
  title           text not null,
  description     text not null,
  legal_reference text,
  jurisdiction    text not null check (jurisdiction in ('england', 'wales', 'scotland', 'northern_ireland')),
  subject_kind    text not null check (subject_kind in ('case', 'defect', 'inspection')),
  -- Case severity, defect code or inspection kind this rule applies to.
  applies_to      text not null,
  trigger_event   text not null check (trigger_event in (
                    'case_aware', 'investigation_completed', 'defect_identified', 'inspection_completed')),
  duration        integer not null check (duration > 0),
  duration_unit   text not null check (duration_unit in ('hours', 'days', 'months')),
  day_type        text check (day_type in ('working', 'calendar')),
  status          text not null default 'draft' check (status in ('draft', 'approved', 'retired')),
  authored_by     uuid not null references caseward.app_user (id),
  approved_by     uuid references caseward.app_user (id),
  approved_at     timestamptz,
  effective_from  date,
  effective_to    date,
  created_at      timestamptz not null default now(),
  unique (rule_key, version),
  check ((duration_unit = 'days') = (day_type is not null)),
  check (status = 'draft' or (approved_by is not null and approved_at is not null and effective_from is not null)),
  check (approved_by is null or approved_by <> authored_by),
  check (effective_to is null or effective_to > effective_from),
  exclude using gist (
    rule_key with =,
    daterange(effective_from, effective_to) with &&
  ) where (status = 'approved')
);

create table caseward.deadline (
  id                   uuid primary key default gen_random_uuid(),
  client_id            uuid not null,
  case_id              uuid,
  defect_id            uuid,
  inspection_id        uuid,
  rule_key             text not null,
  rule_version         integer not null,
  clock_started_at     timestamptz not null,
  holiday_calendar_id  uuid references caseward.holiday_calendar (id),
  due_at               timestamptz not null,
  calculator_version   text not null,
  status               text not null default 'open' check (status in ('open', 'met', 'missed', 'cancelled')),
  completed_at         timestamptz,
  created_at           timestamptz not null default now(),
  foreign key (rule_key, rule_version) references caseward.deadline_rule (rule_key, version),
  foreign key (case_id, client_id) references caseward.hazard_case (id, client_id),
  foreign key (defect_id, client_id) references caseward.defect (id, client_id),
  foreign key (inspection_id, client_id) references caseward.inspection (id, client_id),
  check (num_nonnulls(case_id, defect_id, inspection_id) = 1),
  check (due_at > clock_started_at)
);
create index on caseward.deadline (client_id, status, due_at);

-- ---------------------------------------------------------------------------
-- Letters
-- ---------------------------------------------------------------------------

-- client_id null means a template available to every client.
create table caseward.letter_template (
  id            uuid primary key default gen_random_uuid(),
  client_id     uuid references caseward.client (id),
  template_key  text not null,
  version       integer not null check (version > 0),
  title         text not null,
  body          text not null,
  status        text not null default 'draft' check (status in ('draft', 'approved', 'retired')),
  approved_by   uuid references caseward.app_user (id),
  approved_at   timestamptz,
  created_at    timestamptz not null default now(),
  unique nulls not distinct (client_id, template_key, version),
  check (status = 'draft' or (approved_by is not null and approved_at is not null))
);

create table caseward.letter (
  id                             uuid primary key default gen_random_uuid(),
  client_id                      uuid not null,
  case_id                        uuid,
  template_id                    uuid not null references caseward.letter_template (id),
  recipient_contact_id           uuid,
  channel                        text not null check (channel in ('post', 'email', 'portal')),
  status                         text not null default 'drafting' check (status in (
                                   'drafting', 'draft_ready', 'approved', 'sent', 'void')),
  approved_version               integer,
  approved_by                    uuid references caseward.app_user (id),
  approved_at                    timestamptz,
  sent_at                        timestamptz,
  delivery_evidence_document_id  uuid,
  created_at                     timestamptz not null default now(),
  unique (id, client_id),
  foreign key (case_id, client_id) references caseward.hazard_case (id, client_id),
  foreign key (recipient_contact_id, client_id) references caseward.tenant_contact (id, client_id),
  foreign key (delivery_evidence_document_id, client_id) references caseward.document (id, client_id),
  check (status not in ('approved', 'sent') or (approved_version is not null and approved_by is not null and approved_at is not null)),
  check (status <> 'sent' or sent_at is not null)
);

-- Append-only. The approved text is the body of letter.approved_version.
create table caseward.letter_version (
  letter_id       uuid not null,
  client_id       uuid not null,
  version         integer not null check (version > 0),
  body            text not null,
  origin          text not null check (origin in ('ai_draft', 'human_edit')),
  model_provider  text,
  model_version   text,
  prompt_id       text,
  prompt_version  text,
  created_by      uuid references caseward.app_user (id),
  created_at      timestamptz not null default now(),
  primary key (letter_id, version),
  foreign key (letter_id, client_id) references caseward.letter (id, client_id),
  check (origin <> 'ai_draft' or (model_version is not null and prompt_version is not null)),
  check (origin <> 'human_edit' or created_by is not null)
);

alter table caseward.letter
  add foreign key (id, approved_version) references caseward.letter_version (letter_id, version);

-- ---------------------------------------------------------------------------
-- Work queue and quality
-- ---------------------------------------------------------------------------

create table caseward.task (
  id             uuid primary key default gen_random_uuid(),
  client_id      uuid not null,
  kind           text not null check (kind in (
                   'review_extraction', 'triage_case', 'draft_letter', 'approve_letter',
                   'chase_works', 'other')),
  extraction_id  uuid,
  case_id        uuid,
  defect_id      uuid,
  letter_id      uuid,
  document_id    uuid,
  assignee_id    uuid references caseward.app_user (id),
  status         text not null default 'queued' check (status in ('queued', 'in_progress', 'blocked', 'done', 'cancelled')),
  priority       smallint not null default 3 check (priority between 1 and 5),
  sla_due_at     timestamptz,
  created_at     timestamptz not null default now(),
  started_at     timestamptz,
  completed_at   timestamptz,
  unique (id, client_id),
  foreign key (extraction_id, client_id) references caseward.extraction (id, client_id),
  foreign key (case_id, client_id) references caseward.hazard_case (id, client_id),
  foreign key (defect_id, client_id) references caseward.defect (id, client_id),
  foreign key (letter_id, client_id) references caseward.letter (id, client_id),
  foreign key (document_id, client_id) references caseward.document (id, client_id),
  check (num_nonnulls(extraction_id, case_id, defect_id, letter_id, document_id) <= 1),
  check (status <> 'done' or completed_at is not null)
);
create index on caseward.task (status, sla_due_at);
create index on caseward.task (assignee_id) where status in ('queued', 'in_progress', 'blocked');

create table caseward.quality_review (
  id           uuid primary key default gen_random_uuid(),
  client_id    uuid not null,
  task_id      uuid not null,
  reviewer_id  uuid not null references caseward.app_user (id),
  score        smallint not null check (score between 1 and 5),
  notes        text,
  created_at   timestamptz not null default now(),
  unique (task_id, reviewer_id),
  foreign key (task_id, client_id) references caseward.task (id, client_id)
);

-- Background jobs. Payloads carry ids only, never personal data.
create table caseward.job (
  id               bigint generated always as identity primary key,
  client_id        uuid not null references caseward.client (id),
  kind             text not null check (kind in ('scan_malware', 'ocr', 'extract', 'draft_letter', 'compute_deadlines')),
  payload          jsonb not null default '{}',
  idempotency_key  text not null unique,
  status           text not null default 'queued' check (status in ('queued', 'running', 'succeeded', 'failed', 'dead')),
  run_after        timestamptz not null default now(),
  attempts         integer not null default 0,
  max_attempts     integer not null default 5 check (max_attempts > 0),
  locked_by        text,
  locked_at        timestamptz,
  last_error       text,
  created_at       timestamptz not null default now(),
  finished_at      timestamptz
);
create index job_ready on caseward.job (run_after, id) where status = 'queued';

-- ---------------------------------------------------------------------------
-- Audit
-- ---------------------------------------------------------------------------

-- Append-only and hash-chained. id is assigned by the chain trigger, so ids
-- are gapless and in chain order.
create table caseward.audit_event (
  id              bigint primary key,
  occurred_at     timestamptz not null,
  actor_user_id   uuid,
  actor_kind      text not null check (actor_kind in ('user', 'worker', 'system')),
  client_id       uuid,
  action          text not null,
  entity_type     text not null,
  entity_id       text,
  request_id      text,
  source_ip       inet,
  user_agent      text,
  changed_fields  text[],
  before          jsonb,
  after           jsonb,
  prev_hash       bytea not null,
  hash            bytea not null
);
create index on caseward.audit_event (client_id, occurred_at);
create index on caseward.audit_event (entity_type, entity_id);

create table caseward.audit_chain_head (
  singleton  boolean primary key default true check (singleton),
  last_id    bigint not null,
  last_hash  bytea not null
);
insert into caseward.audit_chain_head (last_id, last_hash)
values (0, sha256(convert_to('caseward-audit-genesis', 'UTF8')));

-- ---------------------------------------------------------------------------
-- Access functions (depend on tables)
-- ---------------------------------------------------------------------------

create function caseward.can_access_client(target uuid) returns boolean
language sql stable as $$
  select caseward.is_staff()
      or target = caseward.current_client_id()
      or exists (
           select 1
           from caseward.engagement e
           where e.contractor_client_id = caseward.current_client_id()
             and e.landlord_client_id = target
             and e.starts_on <= current_date
             and (e.ends_on is null or e.ends_on >= current_date))
$$;

-- For data contractors must not see: tenant contacts and letters.
create function caseward.is_own_client(target uuid) returns boolean
language sql stable as $$
  select caseward.is_staff() or target = caseward.current_client_id()
$$;

-- ---------------------------------------------------------------------------
-- Trigger functions
-- ---------------------------------------------------------------------------

create function caseward.forbid_mutation() returns trigger
language plpgsql as $$
begin
  raise exception '% on caseward.% is not allowed: table is append-only', tg_op, tg_table_name
    using errcode = 'insufficient_privilege';
end $$;

create function caseward.audit_chain() returns trigger
language plpgsql security definer
set search_path = caseward, pg_catalog
set timezone = 'UTC'
as $$
declare
  head caseward.audit_chain_head;
begin
  select * into head from caseward.audit_chain_head for update;
  new.id := head.last_id + 1;
  new.occurred_at := clock_timestamp();
  new.prev_hash := head.last_hash;
  new.hash := caseward.audit_event_hash(new);
  update caseward.audit_chain_head set last_id = new.id, last_hash = new.hash;
  return new;
end $$;

create function caseward.audit_event_hash(e caseward.audit_event) returns bytea
language sql immutable
set timezone = 'UTC'
as $$
  select sha256(e.prev_hash || convert_to((to_jsonb(e) - 'hash' - 'prev_hash')::text, 'UTF8'))
$$;

-- Returns the id of the first event whose hash does not verify, or null if
-- the whole chain is intact.
create function caseward.verify_audit_chain() returns bigint
language plpgsql stable security definer
set search_path = caseward, pg_catalog
set timezone = 'UTC'
as $$
declare
  e caseward.audit_event;
  expected_prev bytea := sha256(convert_to('caseward-audit-genesis', 'UTF8'));
  expected_id bigint := 1;
begin
  for e in select * from caseward.audit_event order by id loop
    if e.id <> expected_id or e.prev_hash <> expected_prev or e.hash <> caseward.audit_event_hash(e) then
      return e.id;
    end if;
    expected_prev := e.hash;
    expected_id := e.id + 1;
  end loop;
  if (select last_id from caseward.audit_chain_head) <> expected_id - 1 then
    return expected_id;
  end if;
  return null;
end $$;

-- Row-level audit for every business table. Pass 'redact' to record which
-- columns changed without their values (personal data).
create function caseward.audit_row() returns trigger
language plpgsql security definer
set search_path = caseward, pg_catalog
as $$
declare
  redact boolean := coalesce(tg_argv[0], '') = 'redact';
  old_j jsonb;
  new_j jsonb;
  changed text[];
begin
  if tg_op in ('UPDATE', 'DELETE') then old_j := to_jsonb(old); end if;
  if tg_op in ('INSERT', 'UPDATE') then new_j := to_jsonb(new); end if;

  select array_agg(k order by k) into changed
  from (select jsonb_object_keys(coalesce(new_j, '{}'))
        union
        select jsonb_object_keys(coalesce(old_j, '{}'))) as keys (k)
  where new_j -> k is distinct from old_j -> k;

  if tg_op = 'UPDATE' and changed is null then
    return null;
  end if;

  insert into caseward.audit_event (
    actor_user_id, actor_kind, client_id, action, entity_type, entity_id,
    request_id, source_ip, changed_fields, before, after)
  values (
    caseward.current_user_id(),
    coalesce(nullif(current_setting('app.actor_kind', true), ''), 'system'),
    coalesce(new_j ->> 'client_id', old_j ->> 'client_id')::uuid,
    lower(tg_op),
    tg_table_name,
    coalesce(new_j ->> 'id', old_j ->> 'id'),
    nullif(current_setting('app.request_id', true), ''),
    nullif(current_setting('app.source_ip', true), '')::inet,
    changed,
    case when redact then null else old_j end,
    case when redact then null else new_j end);
  return null;
end $$;

create function caseward.log_case_status() returns trigger
language plpgsql security definer
set search_path = caseward, pg_catalog
as $$
begin
  if tg_op = 'INSERT' or new.status is distinct from old.status then
    insert into caseward.case_status_change (client_id, case_id, from_status, to_status, changed_by)
    values (new.client_id, new.id, case when tg_op = 'UPDATE' then old.status end, new.status,
            caseward.current_user_id());
  end if;
  return null;
end $$;

-- Approved rules are frozen apart from retiring them. Approver must be an
-- active compliance lead (and, by check constraint, not the author).
create function caseward.deadline_rule_guard() returns trigger
language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    if old.status <> 'draft' then
      raise exception 'deadline rule %.v% is % and cannot be deleted', old.rule_key, old.version, old.status;
    end if;
    return old;
  end if;

  if tg_op = 'UPDATE' and old.status <> 'draft' then
    if old.status = 'retired' then
      raise exception 'deadline rule %.v% is retired and immutable', old.rule_key, old.version;
    end if;
    if new.status not in ('approved', 'retired')
       or (to_jsonb(new) - 'status' - 'effective_to') <> (to_jsonb(old) - 'status' - 'effective_to') then
      raise exception 'deadline rule %.v% is approved; create a new version instead', old.rule_key, old.version;
    end if;
  end if;

  if new.status <> 'draft' and not exists (
       select 1 from caseward.app_user u
       where u.id = new.approved_by and u.staff_role = 'compliance_lead' and u.is_active) then
    raise exception 'deadline rules must be approved by an active compliance lead';
  end if;
  return new;
end $$;

-- Deadlines store every input needed to recalculate them. Those inputs are
-- frozen: to change a deadline, cancel it and create a new one. Status is
-- derived from completed_at so met/missed cannot be set by hand.
create function caseward.deadline_guard() returns trigger
language plpgsql as $$
declare
  r caseward.deadline_rule;
  subject_kind text;
  subject_applies_to text;
  property_jurisdiction text;
  calendar_region text;
begin
  if tg_op = 'UPDATE' then
    if (new.client_id, new.case_id, new.defect_id, new.inspection_id, new.rule_key, new.rule_version,
        new.clock_started_at, new.holiday_calendar_id, new.due_at, new.calculator_version)
       is distinct from
       (old.client_id, old.case_id, old.defect_id, old.inspection_id, old.rule_key, old.rule_version,
        old.clock_started_at, old.holiday_calendar_id, old.due_at, old.calculator_version) then
      raise exception 'deadline inputs are immutable; cancel deadline % and create a new one', old.id;
    end if;
    if old.status in ('met', 'cancelled') and new is distinct from old then
      raise exception 'deadline % is % and final', old.id, old.status;
    end if;
    if old.completed_at is not null and new.completed_at is distinct from old.completed_at then
      raise exception 'deadline % completion time is already recorded', old.id;
    end if;
  else
    -- Before triggers run ahead of check constraints; fail with the constraint's own error.
    if num_nonnulls(new.case_id, new.defect_id, new.inspection_id) <> 1 then
      raise exception 'deadline needs exactly one of case, defect or inspection'
        using errcode = 'check_violation';
    end if;

    select * into r from caseward.deadline_rule
    where rule_key = new.rule_key and version = new.rule_version;

    if r.status <> 'approved' then
      raise exception 'deadline rule %.v% is % and cannot create deadlines', r.rule_key, r.version, r.status;
    end if;
    if (new.clock_started_at at time zone 'Europe/London')::date
       not between r.effective_from and coalesce(r.effective_to - 1, 'infinity'::date) then
      raise exception 'deadline rule %.v% is not effective at %', r.rule_key, r.version, new.clock_started_at;
    end if;

    if new.case_id is not null then
      select 'case', c.severity, p.jurisdiction into subject_kind, subject_applies_to, property_jurisdiction
      from caseward.hazard_case c join caseward.property p on p.id = c.property_id
      where c.id = new.case_id;
    elsif new.defect_id is not null then
      select 'defect', d.code, p.jurisdiction into subject_kind, subject_applies_to, property_jurisdiction
      from caseward.defect d join caseward.property p on p.id = d.property_id
      where d.id = new.defect_id;
    else
      select 'inspection', i.kind, p.jurisdiction into subject_kind, subject_applies_to, property_jurisdiction
      from caseward.inspection i join caseward.property p on p.id = i.property_id
      where i.id = new.inspection_id;
    end if;

    if subject_kind is distinct from r.subject_kind
       or (r.applies_to <> '*' and subject_applies_to is distinct from r.applies_to) then
      raise exception 'deadline rule %.v% applies to % %, not % %',
        r.rule_key, r.version, r.subject_kind, r.applies_to, subject_kind, subject_applies_to;
    end if;
    if property_jurisdiction is distinct from r.jurisdiction then
      raise exception 'deadline rule %.v% is for %, property is in %',
        r.rule_key, r.version, r.jurisdiction, property_jurisdiction;
    end if;

    if r.day_type = 'working' then
      select region into calendar_region from caseward.holiday_calendar where id = new.holiday_calendar_id;
      if calendar_region is distinct from (case r.jurisdiction
           when 'scotland' then 'scotland'
           when 'northern_ireland' then 'northern-ireland'
           else 'england-and-wales' end) then
        raise exception 'working-day rule %.v% needs a % holiday calendar', r.rule_key, r.version, r.jurisdiction;
      end if;
    end if;
  end if;

  if new.completed_at is not null then
    if new.status = 'cancelled' then
      raise exception 'a cancelled deadline cannot be completed';
    end if;
    new.status := case when new.completed_at <= new.due_at then 'met' else 'missed' end;
  elsif new.status = 'met' then
    raise exception 'deadline can only be met by recording completed_at';
  elsif new.status = 'missed' and now() <= new.due_at then
    raise exception 'deadline % is not yet due', new.id;
  end if;
  return new;
end $$;

-- Approval is a separate human step: once recorded it cannot change, and the
-- approver cannot be the person who wrote the approved version.
create function caseward.letter_guard() returns trigger
language plpgsql as $$
begin
  if tg_op = 'UPDATE' then
    if old.status in ('sent', 'void')
       and (to_jsonb(new) - 'delivery_evidence_document_id') <> (to_jsonb(old) - 'delivery_evidence_document_id') then
      raise exception 'letter % is % and cannot change', old.id, old.status;
    end if;
    if old.approved_version is not null
       and (new.approved_version, new.approved_by, new.approved_at)
           is distinct from (old.approved_version, old.approved_by, old.approved_at) then
      raise exception 'approval of letter % is immutable', old.id;
    end if;
  end if;

  if new.approved_by is not null and exists (
       select 1 from caseward.letter_version v
       where v.letter_id = new.id and v.version = new.approved_version and v.created_by = new.approved_by) then
    raise exception 'approver cannot approve a letter version they wrote';
  end if;
  return new;
end $$;

create function caseward.quality_review_guard() returns trigger
language plpgsql as $$
declare
  t caseward.task;
begin
  select * into t from caseward.task where id = new.task_id;
  if t.status <> 'done' then
    raise exception 'task % is % and cannot be quality reviewed yet', t.id, t.status;
  end if;
  if t.assignee_id = new.reviewer_id then
    raise exception 'reviewer cannot quality review their own task';
  end if;
  return new;
end $$;

-- ---------------------------------------------------------------------------
-- Job queue functions
-- ---------------------------------------------------------------------------

create function caseward.claim_job(p_worker text, p_kinds text[] default null)
returns setof caseward.job
language sql as $$
  update caseward.job j
  set status = 'running', attempts = j.attempts + 1, locked_by = p_worker, locked_at = now()
  where j.id = (
    select id from caseward.job
    where status = 'queued' and run_after <= now() and (p_kinds is null or kind = any (p_kinds))
    order by run_after, id
    for update skip locked
    limit 1)
  returning j.*
$$;

-- On failure, retry with exponential backoff until max_attempts, then 'dead'.
create function caseward.finish_job(p_id bigint, p_error text default null)
returns caseward.job
language sql as $$
  update caseward.job
  set status = case
                 when p_error is null then 'succeeded'
                 when attempts >= max_attempts then 'dead'
                 else 'queued'
               end,
      run_after = case when p_error is null then run_after
                       else now() + make_interval(secs => 30 * power(2, attempts - 1)) end,
      last_error = p_error,
      locked_by = null,
      locked_at = null,
      finished_at = case when p_error is null or attempts >= max_attempts then now() end
  where id = p_id and status = 'running'
  returning *
$$;

-- ---------------------------------------------------------------------------
-- Triggers
-- ---------------------------------------------------------------------------

create trigger audit_chain before insert on caseward.audit_event
  for each row execute function caseward.audit_chain();
create trigger append_only before update or delete on caseward.audit_event
  for each row execute function caseward.forbid_mutation();
create trigger append_only_truncate before truncate on caseward.audit_event
  for each statement execute function caseward.forbid_mutation();
create trigger append_only before update or delete on caseward.audit_chain_head
  for each row when (pg_trigger_depth() = 0) execute function caseward.forbid_mutation();

create trigger append_only before update or delete on caseward.letter_version
  for each row execute function caseward.forbid_mutation();
create trigger append_only before update or delete on caseward.holiday_calendar
  for each row execute function caseward.forbid_mutation();
create trigger append_only before update or delete on caseward.bank_holiday
  for each row execute function caseward.forbid_mutation();
create trigger append_only before update or delete on caseward.case_status_change
  for each row execute function caseward.forbid_mutation();

create trigger guard before insert or update or delete on caseward.deadline_rule
  for each row execute function caseward.deadline_rule_guard();
create trigger guard before insert or update on caseward.deadline
  for each row execute function caseward.deadline_guard();
create trigger guard before insert or update on caseward.letter
  for each row execute function caseward.letter_guard();
create trigger guard before insert or update on caseward.quality_review
  for each row execute function caseward.quality_review_guard();
create trigger log_status after insert or update of status on caseward.hazard_case
  for each row execute function caseward.log_case_status();

do $$
declare
  t text;
begin
  foreach t in array array[
    'client', 'engagement', 'membership', 'property', 'tenancy', 'document', 'ocr_output',
    'extraction', 'extraction_review', 'inspection', 'defect', 'hazard_case', 'case_defect',
    'holiday_calendar', 'bank_holiday', 'deadline_rule', 'deadline', 'letter_template', 'letter',
    'letter_version', 'task', 'quality_review', 'hazard_category']
  loop
    execute format(
      'create trigger audit after insert or update or delete on caseward.%I
         for each row execute function caseward.audit_row()', t);
  end loop;

  foreach t in array array['app_user', 'tenant_contact'] loop
    execute format(
      'create trigger audit after insert or update or delete on caseward.%I
         for each row execute function caseward.audit_row(''redact'')', t);
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------

do $$
declare
  t text;
begin
  -- Visible to the owning client, engaged contractors and staff.
  foreach t in array array[
    'property', 'document', 'ocr_output', 'extraction', 'extraction_review', 'inspection',
    'defect', 'hazard_case', 'case_defect', 'case_status_change', 'deadline', 'task', 'quality_review']
  loop
    execute format('alter table caseward.%I enable row level security', t);
    execute format('alter table caseward.%I force row level security', t);
    execute format(
      'create policy client_access on caseward.%I
         using (caseward.can_access_client(client_id))
         with check (caseward.can_access_client(client_id))', t);
  end loop;

  -- Visible to the owning client and staff only.
  foreach t in array array['tenancy', 'tenant_contact', 'letter', 'letter_version'] loop
    execute format('alter table caseward.%I enable row level security', t);
    execute format('alter table caseward.%I force row level security', t);
    execute format(
      'create policy own_client on caseward.%I
         using (caseward.is_own_client(client_id))
         with check (caseward.is_own_client(client_id))', t);
  end loop;
end $$;

-- case_status_change is written by a security definer trigger running as the
-- owner, so it must not force RLS on the owner.
alter table caseward.case_status_change no force row level security;

alter table caseward.client enable row level security;
alter table caseward.client force row level security;
create policy client_access on caseward.client using (caseward.can_access_client(id));

alter table caseward.engagement enable row level security;
alter table caseward.engagement force row level security;
create policy party on caseward.engagement using (
  caseward.is_staff()
  or landlord_client_id = caseward.current_client_id()
  or contractor_client_id = caseward.current_client_id());

alter table caseward.membership enable row level security;
alter table caseward.membership force row level security;
create policy own_client on caseward.membership using (caseward.is_own_client(client_id));

alter table caseward.app_user enable row level security;
alter table caseward.app_user force row level security;
create policy visible_user on caseward.app_user using (
  caseward.is_staff()
  or id = caseward.current_user_id()
  or exists (select 1 from caseward.membership m
             where m.user_id = app_user.id and m.client_id = caseward.current_client_id()));

alter table caseward.letter_template enable row level security;
alter table caseward.letter_template force row level security;
create policy visible_template on caseward.letter_template
  using (client_id is null or caseward.is_own_client(client_id))
  with check (caseward.is_staff());

alter table caseward.job enable row level security;
alter table caseward.job force row level security;
create policy client_access on caseward.job
  using (caseward.can_access_client(client_id))
  with check (caseward.can_access_client(client_id));
create policy worker_all on caseward.job for all to caseward_worker using (true) with check (true);

-- Not forced: the owner-run chain trigger and audit_row must always write.
alter table caseward.audit_event enable row level security;
create policy read_own on caseward.audit_event for select using (
  caseward.is_staff() or (client_id is not null and client_id = caseward.current_client_id()));
create policy write_own on caseward.audit_event for insert with check (
  actor_user_id is not distinct from caseward.current_user_id()
  and (client_id is null or caseward.can_access_client(client_id)));

-- ---------------------------------------------------------------------------
-- Grants. No DELETE for the API except unlinking a defect from a case;
-- retention deletes run as a separate, audited role.
-- ---------------------------------------------------------------------------

grant select on all tables in schema caseward to caseward_app;
revoke select on caseward.audit_chain_head from caseward_app;

grant insert, update on
  caseward.property, caseward.tenancy, caseward.tenant_contact, caseward.document,
  caseward.ocr_output, caseward.extraction, caseward.extraction_review, caseward.inspection,
  caseward.defect, caseward.hazard_case, caseward.deadline, caseward.task, caseward.letter,
  caseward.quality_review
to caseward_app;
grant insert, delete on caseward.case_defect to caseward_app;
grant insert on caseward.letter_version, caseward.audit_event, caseward.job to caseward_app;

grant insert, update on
  caseward.client, caseward.engagement, caseward.app_user, caseward.membership,
  caseward.deadline_rule, caseward.letter_template, caseward.hazard_category
to caseward_staff;
grant insert on caseward.holiday_calendar, caseward.bank_holiday to caseward_staff;
grant delete on caseward.membership, caseward.deadline_rule to caseward_staff;

grant select, update on caseward.job to caseward_worker;

grant usage on all sequences in schema caseward to caseward_app;

revoke execute on function caseward.claim_job(text, text[]), caseward.finish_job(bigint, text) from public;
grant execute on function caseward.claim_job(text, text[]), caseward.finish_job(bigint, text) to caseward_worker;
revoke execute on function caseward.verify_audit_chain() from public;
grant execute on function caseward.verify_audit_chain() to caseward_staff;

-- ---------------------------------------------------------------------------
-- Reference data: the 29 HHSRS hazards
-- ---------------------------------------------------------------------------

insert into caseward.hazard_category (code, name) values
  ('damp_and_mould_growth', 'Damp and mould growth'),
  ('excess_cold', 'Excess cold'),
  ('excess_heat', 'Excess heat'),
  ('asbestos_and_mmf', 'Asbestos and MMF'),
  ('biocides', 'Biocides'),
  ('carbon_monoxide', 'Carbon monoxide and fuel combustion products'),
  ('lead', 'Lead'),
  ('radiation', 'Radiation'),
  ('uncombusted_fuel_gas', 'Uncombusted fuel gas'),
  ('volatile_organic_compounds', 'Volatile organic compounds'),
  ('crowding_and_space', 'Crowding and space'),
  ('entry_by_intruders', 'Entry by intruders'),
  ('lighting', 'Lighting'),
  ('noise', 'Noise'),
  ('domestic_hygiene_pests_refuse', 'Domestic hygiene, pests and refuse'),
  ('food_safety', 'Food safety'),
  ('personal_hygiene_sanitation_drainage', 'Personal hygiene, sanitation and drainage'),
  ('water_supply', 'Water supply'),
  ('falls_baths', 'Falls associated with baths etc.'),
  ('falls_level_surfaces', 'Falling on level surfaces etc.'),
  ('falls_stairs', 'Falling on stairs etc.'),
  ('falls_between_levels', 'Falling between levels'),
  ('electrical_hazards', 'Electrical hazards'),
  ('fire', 'Fire'),
  ('flames_hot_surfaces', 'Flames, hot surfaces etc.'),
  ('collision_and_entrapment', 'Collision and entrapment'),
  ('explosions', 'Explosions'),
  ('position_operability_amenities', 'Position and operability of amenities etc.'),
  ('structural_collapse', 'Structural collapse and falling elements');

commit;
