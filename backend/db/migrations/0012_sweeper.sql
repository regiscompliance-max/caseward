-- 0012_sweeper.sql
--
-- Fix: in 0003 the worker login could select and update every client's
-- deadlines, so the sweep could run across clients. That access also applied
-- while the worker ran a job for one client, so an export for one landlord
-- could include another landlord's deadlines. The cross-client access now
-- belongs to caseward_sweeper, which only sweep_deadlines() runs as.

begin;

drop policy worker_sweep on caseward.deadline;
drop policy worker_sweep_update on caseward.deadline;
drop policy worker_sweep on caseward.deadline_alert;

do $$
begin
  if not exists (select from pg_roles where rolname = 'caseward_sweeper') then
    create role caseward_sweeper nologin;
  end if;
end $$;
grant caseward_sweeper to current_user;
-- CREATE is needed only to take ownership of the function below; revoked at the end.
grant usage, create on schema caseward to caseward_sweeper;
grant select, update on caseward.deadline to caseward_sweeper;
grant select, insert on caseward.deadline_alert to caseward_sweeper;
grant select on caseward.deadline_progress to caseward_sweeper;

create policy sweeper on caseward.deadline for select to caseward_sweeper using (true);
create policy sweeper_update on caseward.deadline for update to caseward_sweeper using (true) with check (true);
create policy sweeper on caseward.deadline_alert for all to caseward_sweeper using (true) with check (true);

alter function caseward.sweep_deadlines() security definer;
alter function caseward.sweep_deadlines() set search_path = caseward, pg_catalog;
alter function caseward.sweep_deadlines() owner to caseward_sweeper;
revoke create on schema caseward from caseward_sweeper;

commit;
