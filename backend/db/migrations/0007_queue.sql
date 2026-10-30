-- 0007_queue.sql
--
-- The work queue: a note on why a task was blocked, released or cancelled,
-- and indexes for claiming the next task.

begin;

alter table caseward.task add column note text check (length(note) <= 2000);

create index task_queue on caseward.task (priority, sla_due_at, created_at)
  where status = 'queued';
create index task_done on caseward.task (completed_at) where status = 'done';

commit;
