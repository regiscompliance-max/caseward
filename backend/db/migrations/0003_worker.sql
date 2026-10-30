-- 0003_worker.sql
--
-- Background work: queueing deadline calculation from the database itself,
-- the missed-deadline sweep, deadline alerts and stale-job recovery.

begin;

-- ---------------------------------------------------------------------------
-- Every trigger event queues deadline calculation
-- ---------------------------------------------------------------------------

-- Queued by trigger rather than by the API, so events recorded by any route
-- (API, worker, import) are never skipped. The worker decides whether any
-- rule applies; a job that finds none does nothing.
create function caseward.queue_deadline_calculation() returns trigger
language plpgsql as $$
declare
  -- One function serves three tables, so read fields through jsonb rather
  -- than NEW.field, which fails for fields the table does not have.
  row_data jsonb := to_jsonb(new);
  is_event boolean := tg_table_name = 'case_event';
  subject_kind text := case tg_table_name when 'case_event' then 'case' else tg_table_name end;
  subject_id text := case when is_event then row_data ->> 'case_id' else row_data ->> 'id' end;
  event text := case tg_table_name
                  when 'case_event' then row_data ->> 'kind'
                  when 'defect' then 'defect_identified'
                  else 'inspection_completed' end;
  payload jsonb := jsonb_build_object('subject', jsonb_build_object('kind', subject_kind, 'id', subject_id),
                                      'event', event);
begin
  if is_event then
    if row_data ->> 'corrects_event_id' is not null then
      return null;
    end if;
    payload := payload || jsonb_build_object('event_id', row_data ->> 'id');
  end if;
  insert into caseward.job (client_id, kind, payload, idempotency_key)
  values (new.client_id, 'compute_deadlines', payload,
          format('compute_deadlines:%s:%s:%s', subject_kind, subject_id,
                 case when is_event then row_data ->> 'id' else event end))
  on conflict (idempotency_key) do nothing;
  return null;
end $$;

create trigger queue_deadlines after insert on caseward.case_event
  for each row execute function caseward.queue_deadline_calculation();
create trigger queue_deadlines after insert on caseward.defect
  for each row execute function caseward.queue_deadline_calculation();
create trigger queue_deadlines after insert on caseward.inspection
  for each row execute function caseward.queue_deadline_calculation();

-- ---------------------------------------------------------------------------
-- Deadline alerts
-- ---------------------------------------------------------------------------

-- One row per alert raised. Delivery (console, email) reads from here.
create table caseward.deadline_alert (
  id               uuid primary key default gen_random_uuid(),
  client_id        uuid not null,
  deadline_id      uuid not null references caseward.deadline (id),
  level            text not null check (level in ('alert_50', 'alert_80', 'overdue')),
  raised_at        timestamptz not null default now(),
  acknowledged_by  uuid references caseward.app_user (id),
  acknowledged_at  timestamptz,
  unique (deadline_id, level),
  check ((acknowledged_by is null) = (acknowledged_at is null))
);
create index on caseward.deadline_alert (client_id, raised_at) where acknowledged_at is null;

alter table caseward.deadline_alert enable row level security;
alter table caseward.deadline_alert force row level security;
create policy client_access on caseward.deadline_alert
  using (caseward.can_access_client(client_id))
  with check (caseward.can_access_client(client_id));

create trigger audit after insert or update or delete on caseward.deadline_alert
  for each row execute function caseward.audit_row();

create function caseward.alert_rank(level text) returns integer
language sql immutable as $$
  select case level when 'alert_50' then 1 when 'alert_80' then 2 when 'overdue' then 3 else 0 end
$$;

-- The sweep runs across every client. Deadlines and alerts hold no personal
-- data, so the worker may see and update them all; everything else it does
-- under the job's own client context.
create policy worker_sweep on caseward.deadline for select to caseward_worker using (true);
create policy worker_sweep_update on caseward.deadline for update to caseward_worker using (true) with check (true);
create policy worker_sweep on caseward.deadline_alert for all to caseward_worker using (true) with check (true);

-- Marks overdue open deadlines as missed, then raises the highest alert each
-- deadline has reached, unless that level (or a higher one) was raised
-- before. A late sweep therefore raises one alert, not a burst.
create function caseward.sweep_deadlines()
returns table (missed integer, alerts integer)
language plpgsql as $$
declare
  missed_count integer;
  alert_count integer;
begin
  update caseward.deadline set status = 'missed'
  where status = 'open' and completed_at is null and due_at < now();
  get diagnostics missed_count = row_count;

  insert into caseward.deadline_alert (client_id, deadline_id, level)
  select p.client_id, p.id, p.alert_level
  from caseward.deadline_progress p
  where p.alert_level <> 'on_track'
    and not exists (
      select 1 from caseward.deadline_alert a
      where a.deadline_id = p.id and caseward.alert_rank(a.level) >= caseward.alert_rank(p.alert_level))
  on conflict (deadline_id, level) do nothing;
  get diagnostics alert_count = row_count;

  return query select missed_count, alert_count;
end $$;

-- A worker that dies mid-job leaves it 'running'. Put such jobs back, or
-- mark them dead if they have used up their attempts.
create function caseward.requeue_stale_jobs(p_older_than interval)
returns integer
language plpgsql as $$
declare
  n integer;
begin
  update caseward.job
  set status = case when attempts >= max_attempts then 'dead' else 'queued' end,
      locked_by = null,
      locked_at = null,
      run_after = now(),
      last_error = coalesce(last_error, 'worker stopped before finishing'),
      finished_at = case when attempts >= max_attempts then now() end
  where status = 'running' and locked_at < now() - p_older_than;
  get diagnostics n = row_count;
  return n;
end $$;

grant select, update on caseward.deadline_alert to caseward_app;
grant insert on caseward.deadline_alert to caseward_worker;
revoke execute on function caseward.sweep_deadlines(), caseward.requeue_stale_jobs(interval) from public;
grant execute on function caseward.sweep_deadlines(), caseward.requeue_stale_jobs(interval) to caseward_worker;

commit;
