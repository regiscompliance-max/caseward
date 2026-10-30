-- 0011_exports.sql
--
-- Exports: CSV and PDF reports, and case evidence packs, built in the
-- background under the requester's own access.

begin;

alter table caseward.job drop constraint job_kind_check;
alter table caseward.job add constraint job_kind_check check (kind in (
  'scan_malware', 'ocr', 'extract', 'draft_letter', 'compute_deadlines', 'export'));

create table caseward.export (
  id                 uuid primary key default gen_random_uuid(),
  -- The client the export runs as: the requester's acting client, or for an
  -- export staff prepare, the client it is for.
  client_id          uuid not null references caseward.client (id),
  requested_by       uuid not null references caseward.app_user (id),
  -- Prepared by staff for the client's admins to download.
  for_client_admins  boolean not null default false,
  kind               text not null check (kind in (
                       'deadline_performance', 'case_evidence_pack', 'certificate_register', 'open_defects', 'audit_log')),
  format             text not null check (format in ('csv', 'pdf')),
  filters            jsonb not null default '{}',
  status             text not null default 'queued' check (status in ('queued', 'running', 'ready', 'failed')),
  storage_key        text,
  sha256             text check (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes         bigint,
  error              text,
  requested_at       timestamptz not null default now(),
  ready_at           timestamptz,
  expires_at         timestamptz,
  check (status <> 'ready' or (storage_key is not null and sha256 is not null and expires_at is not null))
);
create index on caseward.export (client_id, requested_at);

alter table caseward.export enable row level security;
alter table caseward.export force row level security;
create policy client_access on caseward.export
  using (caseward.can_access_client(client_id))
  with check (caseward.can_access_client(client_id));

create trigger audit after insert or update or delete on caseward.export
  for each row execute function caseward.audit_row();

grant select, insert, update on caseward.export to caseward_app;

commit;
