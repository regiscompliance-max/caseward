-- 0010_updates.sql
--
-- Why a defect's status changed, and who cancelled a deadline and why.

begin;

alter table caseward.defect add column status_note text check (length(status_note) <= 2000);

alter table caseward.deadline
  add column cancelled_by uuid references caseward.app_user (id),
  add column cancelled_at timestamptz,
  add column cancel_reason text check (length(cancel_reason) <= 2000),
  add check (status <> 'cancelled' or (cancel_reason is not null and cancelled_at is not null));

-- Admins requeue dead jobs from the operations API.
grant update on caseward.job to caseward_staff;

commit;
