-- 0009_administration.sql
--
-- Invitations: a user is created before they first sign in, so their
-- identity provider subject is not known yet. On first sign-in with a
-- verified email address, the invitation is bound to that subject.

begin;

-- Sign-in has to find the user and their memberships before any client
-- context exists, which row-level security forbids by design. Rather than
-- weaken the policies, these lookups run as caseward_auth: a role that cannot
-- log in, that no login role is a member of, and that has narrow policies of
-- its own. (Running them as the table owner only works if the owner bypasses
-- row-level security, which a production owner must not.)
do $$
begin
  if not exists (select from pg_roles where rolname = 'caseward_auth') then
    create role caseward_auth nologin;
  end if;
end $$;
grant caseward_auth to current_user;
-- CREATE is needed only to take ownership of the functions below; revoked at the end.
grant usage, create on schema caseward to caseward_auth;
grant select on caseward.app_user, caseward.membership, caseward.client to caseward_auth;
grant update (idp_subject) on caseward.app_user to caseward_auth;
grant insert on caseward.audit_event to caseward_auth;

create policy auth_lookup on caseward.app_user for select to caseward_auth using (true);
create policy auth_claim on caseward.app_user for update to caseward_auth
  using (idp_subject like 'invited:%') with check (idp_subject not like 'invited:%');
create policy auth_lookup on caseward.membership for select to caseward_auth using (true);
create policy auth_lookup on caseward.client for select to caseward_auth using (true);

alter function caseward.resolve_user(text) owner to caseward_auth;
alter function caseward.user_memberships(uuid) owner to caseward_auth;

create function caseward.claim_invitation(p_idp_subject text, p_email text)
returns uuid
language sql security definer
set search_path = caseward, pg_catalog
as $$
  update caseward.app_user
  set idp_subject = p_idp_subject
  where lower(email) = lower(p_email) and idp_subject like 'invited:%' and is_active
  returning id
$$;

alter function caseward.claim_invitation(text, text) owner to caseward_auth;
revoke execute on function caseward.claim_invitation(text, text) from public;
grant execute on function caseward.claim_invitation(text, text) to caseward_app;

revoke create on schema caseward from caseward_auth;

commit;
