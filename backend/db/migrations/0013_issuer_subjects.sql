-- 0013_issuer_subjects.sql
--
-- Several identity providers are trusted (our staff directory and each
-- landlord's own single sign-on). A subject is only unique within one
-- provider, so users are stored as "issuer|subject"; invitations keep their
-- "invited:" placeholder until first sign-in.

begin;

alter table caseward.app_user
  add constraint idp_subject_is_qualified check (idp_subject like 'invited:%' or idp_subject like '%|%');

commit;
