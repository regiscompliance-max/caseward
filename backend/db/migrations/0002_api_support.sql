-- 0002_api_support.sql
--
-- What the API layer needs that the domain schema does not: resolving the
-- signed-in user before any client context exists, and idempotency records.

begin;

-- Row-level security hides app_user and membership until app.user_id and
-- app.client_id are set, but the API needs both to set them. These two
-- functions answer exactly that and nothing more.
create function caseward.resolve_user(p_idp_subject text)
returns table (id uuid, staff_role text, is_active boolean, display_name text)
language sql stable security definer
set search_path = caseward, pg_catalog
as $$
  select u.id, u.staff_role, u.is_active, u.display_name
  from caseward.app_user u
  where u.idp_subject = p_idp_subject
$$;

create function caseward.user_memberships(p_user_id uuid)
returns table (client_id uuid, client_name text, client_kind text, role text, special_category_access boolean)
language sql stable security definer
set search_path = caseward, pg_catalog
as $$
  select m.client_id, c.name, c.kind, m.role, m.special_category_access
  from caseward.membership m
  join caseward.client c on c.id = m.client_id
  where m.user_id = p_user_id
  order by c.name
$$;

revoke execute on function caseward.resolve_user(text), caseward.user_memberships(uuid) from public;
grant execute on function caseward.resolve_user(text), caseward.user_memberships(uuid) to caseward_app;

-- Written in the same transaction as the operation it protects, so a failed
-- request leaves no record and can be retried with the same key.
create table caseward.idempotency_record (
  user_id       uuid not null,
  key           text not null,
  operation_id  text not null,
  request_hash  text not null,
  status        smallint,
  body          jsonb,
  created_at    timestamptz not null default now(),
  primary key (user_id, key)
);
create index on caseward.idempotency_record (created_at);

alter table caseward.idempotency_record enable row level security;
alter table caseward.idempotency_record force row level security;
create policy own_records on caseward.idempotency_record
  using (user_id = caseward.current_user_id())
  with check (user_id = caseward.current_user_id());

grant select, insert, update on caseward.idempotency_record to caseward_app;

commit;
