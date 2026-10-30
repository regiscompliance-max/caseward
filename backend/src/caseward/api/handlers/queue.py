"""The operations work queue and quality sampling.

Order: priority, then the nearest open legal deadline linked to the task's
subject, then SLA, then age. Claiming uses SKIP LOCKED, so two case
handlers pressing "next" at once never get the same task.
"""

from uuid import UUID

from caseward.api.framework import Call, Result
from caseward.api.handlers.shared import decode_cursor, limit_plus_one, page
from caseward.api.problems import Problem, forbidden, not_found

FAR_FUTURE = "'9999-12-31T00:00:00Z'::timestamptz"
EARLIEST_DEADLINE = """(select min(d.due_at) from caseward.deadline d
   where d.completed_at is null and d.status in ('open', 'missed')
     and (d.case_id = t.case_id or d.defect_id = t.defect_id
          or d.case_id = (select l.case_id from caseward.letter l where l.id = t.letter_id)))"""
QUEUE_ORDER = f"t.priority, coalesce({EARLIEST_DEADLINE}, {FAR_FUTURE}), coalesce(t.sla_due_at, {FAR_FUTURE}), t.created_at, t.id"
TASK_SELECT = f"""
  select t.id, t.client_id, c.name as client_name, t.kind, t.extraction_id, t.case_id, t.defect_id, t.letter_id,
         t.document_id, t.assignee_id, t.status, t.priority, t.sla_due_at, t.created_at, t.started_at,
         t.completed_at, t.note, t.xmin::text as _etag, {EARLIEST_DEADLINE} as earliest_deadline_due_at
  from caseward.task t join caseward.client c on c.id = t.client_id
"""
SUBJECTS = ("extraction", "case", "defect", "letter", "document")
LEADS = {"compliance_lead", "admin"}


def task_out(row: dict) -> dict:
    subject_columns = {f"{s}_id" for s in SUBJECTS}
    out = {k: v for k, v in row.items() if not k.startswith("_") and k not in subject_columns}
    subject = next(({"kind": s, "id": row[f"{s}_id"]} for s in SUBJECTS if row[f"{s}_id"]), None)
    return {**out, "subject": subject}


def _task(call: Call, task_id, lock: bool = False) -> dict:
    row = call.row(TASK_SELECT + " where t.id = %s" + (" for update of t" if lock else ""), [task_id])
    if row is None:
        raise not_found()
    return row


def _check_etag(call: Call, task: dict) -> None:
    if call.headers["If-Match"].strip().strip('"') != task["_etag"]:
        raise Problem(412, "stale", "The task changed since you loaded it; reload and try again")


def _may_change(call: Call, task: dict) -> None:
    if task["assignee_id"] not in (None, call.principal.user_id) and call.principal.staff_role not in LEADS:
        raise forbidden("This task is assigned to someone else.")


def _result(call: Call, task_id, status: int = 200) -> Result:
    row = _task(call, task_id)
    return Result(status, task_out(row), headers={"ETag": f'"{row["_etag"]}"'}, audit_client_id=row["client_id"])


def list_tasks(call: Call) -> Result:
    q = call.query
    where, params = ["true"], []
    for name in ("client_id", "status", "kind"):
        if name in q:
            where.append(f"t.{name} = %s")
            params.append(q[name])
    assignee = q.get("assignee")
    if assignee == "me":
        where.append("t.assignee_id = %s")
        params.append(call.principal.user_id)
    elif assignee == "unassigned":
        where.append("t.assignee_id is null")
    elif assignee is not None:
        try:
            params.append(UUID(assignee))
        except ValueError:
            raise Problem(400, "invalid_parameter", "assignee must be a user id, 'me' or 'unassigned'") from None
        where.append("t.assignee_id = %s")
    cursor = decode_cursor(call)
    if cursor:
        where.append(f"(t.priority, coalesce({EARLIEST_DEADLINE}, {FAR_FUTURE}), coalesce(t.sla_due_at, {FAR_FUTURE}),"
                     " t.created_at, t.id) > (%s::smallint, %s::timestamptz, %s::timestamptz, %s::timestamptz, %s::uuid)")
        params += cursor
    sort_keys = (f", t.priority as _k1, coalesce({EARLIEST_DEADLINE}, {FAR_FUTURE}) as _k2,"
                 f" coalesce(t.sla_due_at, {FAR_FUTURE}) as _k3, t.created_at as _k4")
    rows = call.rows(TASK_SELECT.replace(" as earliest_deadline_due_at", " as earliest_deadline_due_at" + sort_keys)
                     + f" where {' and '.join(where)} order by {QUEUE_ORDER} limit %s", params + [limit_plus_one(call)])
    result = page(call, rows, ["_k1", "_k2", "_k3", "_k4", "id"])
    result["items"] = [task_out(r) for r in result["items"]]
    return Result(200, result)


def claim_next_task(call: Call) -> Result:
    b = call.body or {}
    where, params = ["t.status = 'queued'", "(t.assignee_id is null or t.assignee_id = %s)"], [call.principal.user_id]
    if b.get("kinds"):
        where.append("t.kind = any(%s)")
        params.append(b["kinds"])
    if b.get("client_id"):
        where.append("t.client_id = %s")
        params.append(b["client_id"])
    claimed = call.row(
        f"""update caseward.task set assignee_id = %s, status = 'in_progress', started_at = coalesce(started_at, now())
            where id = (select t.id from caseward.task t where {' and '.join(where)}
                        order by {QUEUE_ORDER} for update skip locked limit 1)
            returning id""", [call.principal.user_id] + params)
    if claimed is None:
        return Result(204)
    return _result(call, claimed["id"])


def get_task(call: Call) -> Result:
    return _result(call, call.path["id"])


def update_task(call: Call) -> Result:
    b = call.body
    task = _task(call, call.path["id"], lock=True)
    _check_etag(call, task)
    if task["status"] in ("done", "cancelled"):
        raise Problem(409, "task_closed", f"The task is {task['status']}")
    _may_change(call, task)

    assignee = task["assignee_id"]
    if "assignee_id" in b:
        assignee = b["assignee_id"]
        if assignee is not None and call.row(
                "select 1 from caseward.app_user where id = %s and is_active"
                " and staff_role in ('case_handler', 'compliance_lead')", [assignee]) is None:
            raise Problem(422, "invalid_assignee", "Tasks can only be assigned to active case handlers or compliance leads")
    status = b.get("status", task["status"])
    if status in ("blocked", "cancelled") and not b.get("note"):
        raise Problem(422, "note_required", f"Say why the task is {status}")
    if status == "queued" and "assignee_id" not in b:
        assignee = None  # Released back to the queue for anyone.
    if status == "in_progress" and assignee is None:
        assignee = call.principal.user_id

    call.conn.execute(
        """update caseward.task set status = %s, assignee_id = %s, priority = %s, note = coalesce(%s, note),
             started_at = case when %s = 'in_progress' then coalesce(started_at, now()) else started_at end,
             completed_at = case when %s = 'cancelled' then now() else completed_at end
           where id = %s""",
        [status, assignee, b.get("priority", task["priority"]), b.get("note"), status, status, task["id"]])
    return _result(call, task["id"])


def _work_finished(call: Call, task: dict) -> bool:
    if task["kind"] == "review_extraction":
        return call.row("select 1 from caseward.extraction_review where extraction_id = %s",
                        [task["extraction_id"]]) is not None
    if task["kind"] == "approve_letter":
        return call.row("select 1 from caseward.letter where id = %s and status in ('approved', 'sent', 'void')",
                        [task["letter_id"]]) is not None
    if task["kind"] == "draft_letter":
        return call.row("select 1 from caseward.letter_version where letter_id = %s", [task["letter_id"]]) is not None
    return True


def complete_task(call: Call) -> Result:
    task = _task(call, call.path["id"], lock=True)
    _check_etag(call, task)
    if task["status"] in ("done", "cancelled"):
        raise Problem(409, "task_closed", f"The task is {task['status']}")
    if task["assignee_id"] != call.principal.user_id and call.principal.staff_role not in LEADS:
        raise forbidden("Only the assignee can complete this task.")
    if not _work_finished(call, task):
        raise Problem(422, "work_not_finished", "The work for this task has not been recorded yet")
    call.conn.execute("update caseward.task set status = 'done', completed_at = now(),"
                      " assignee_id = coalesce(assignee_id, %s), started_at = coalesce(started_at, now())"
                      " where id = %s", [call.principal.user_id, task["id"]])
    return _result(call, task["id"])


def sample_tasks_for_review(call: Call) -> Result:
    q = call.query
    where = ["t.status = 'done'", "t.assignee_id is distinct from %s",
             "not exists (select 1 from caseward.quality_review r where r.task_id = t.id and r.reviewer_id = %s)"]
    params = [call.principal.user_id, call.principal.user_id]
    for name in ("client_id", "kind"):
        if name in q:
            where.append(f"t.{name} = %s")
            params.append(q[name])
    if "completed_from" in q:
        where.append("t.completed_at >= (%s::date::timestamp at time zone 'Europe/London')")
        params.append(q["completed_from"])
    rows = call.rows(TASK_SELECT + f" where {' and '.join(where)} order by random() limit %s",
                     params + [q.get("size", 10)])
    return Result(200, {"items": [task_out(r) for r in rows]})


def create_quality_review(call: Call) -> Result:
    task = _task(call, call.path["id"])
    if call.row("select 1 from caseward.quality_review where task_id = %s and reviewer_id = %s",
                [task["id"], call.principal.user_id]):
        raise Problem(409, "already_reviewed", "You have already reviewed this task")
    row = call.row(
        "insert into caseward.quality_review (client_id, task_id, reviewer_id, score, notes) values (%s, %s, %s, %s, %s)"
        " returning id, task_id, reviewer_id, score, notes, created_at",
        [task["client_id"], task["id"], call.principal.user_id, call.body["score"], call.body.get("notes")])
    return Result(201, row, audit_client_id=task["client_id"])
