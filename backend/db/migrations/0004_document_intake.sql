-- 0004_document_intake.sql
--
-- Two-step uploads (presigned URL, then register) and rendered pages for the
-- side-by-side viewer.

begin;

-- An upload URL that was issued. Only the user it was issued to can turn it
-- into a document, once, before it expires.
create table caseward.upload (
  id                 uuid primary key,
  client_id          uuid not null references caseward.client (id),
  created_by         uuid not null references caseward.app_user (id),
  storage_key        text not null unique,
  original_filename  text,
  mime_type          text not null,
  size_bytes         bigint not null check (size_bytes > 0),
  sha256             text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  expires_at         timestamptz not null,
  document_id        uuid references caseward.document (id),
  created_at         timestamptz not null default now()
);

alter table caseward.upload enable row level security;
alter table caseward.upload force row level security;
create policy own_uploads on caseward.upload
  using (caseward.can_access_client(client_id) and created_by = caseward.current_user_id())
  with check (caseward.can_access_client(client_id) and created_by = caseward.current_user_id());

create table caseward.document_page (
  document_id        uuid not null,
  client_id          uuid not null,
  page_number        integer not null check (page_number > 0),
  image_storage_key  text not null,
  width              integer not null check (width > 0),
  height             integer not null check (height > 0),
  has_text_layer     boolean not null,
  primary key (document_id, page_number),
  foreign key (document_id, client_id) references caseward.document (id, client_id)
);

alter table caseward.document_page enable row level security;
alter table caseward.document_page force row level security;
create policy client_access on caseward.document_page
  using (caseward.can_access_client(client_id))
  with check (caseward.can_access_client(client_id));

-- What the portal and console show as a document's progress.
create function caseward.document_processing(d caseward.document) returns text
language sql stable as $$
  select case
    when d.malware_scan = 'pending' then 'scanning'
    when d.malware_scan = 'infected' then 'rejected'
    when d.malware_scan = 'error' then 'failed'
    when exists (select 1 from caseward.job j where j.client_id = d.client_id and j.status = 'dead'
                   and j.payload ->> 'document_id' = d.id::text) then 'failed'
    when not exists (select 1 from caseward.ocr_output o where o.document_id = d.id)
         and d.mime_type in ('application/pdf', 'image/jpeg', 'image/png', 'image/tiff') then 'reading'
    when d.doc_type not in ('certificate', 'hazard_report') then 'complete'
    when exists (select 1 from caseward.extraction_review r join caseward.extraction e on e.id = r.extraction_id
                 where e.document_id = d.id and r.decision = 'rejected')
         and not exists (select 1 from caseward.extraction_review r join caseward.extraction e on e.id = r.extraction_id
                         where e.document_id = d.id and r.decision <> 'rejected') then 'rejected'
    when exists (select 1 from caseward.extraction_review r join caseward.extraction e on e.id = r.extraction_id
                 where e.document_id = d.id) then 'complete'
    when exists (select 1 from caseward.extraction e where e.document_id = d.id and e.status = 'succeeded')
      then 'awaiting_review'
    else 'reading'
  end
$$;

create trigger audit after insert or update or delete on caseward.upload
  for each row execute function caseward.audit_row();
create trigger audit after insert or update or delete on caseward.document_page
  for each row execute function caseward.audit_row();

grant select, insert, update on caseward.upload to caseward_app;
grant select, insert on caseward.document_page to caseward_app;

commit;
