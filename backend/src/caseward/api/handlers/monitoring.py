"""Audit search, the audit chain check, and the job queue."""

from datetime import UTC, datetime

from caseward.api.framework import Call, Result
from caseward.api.handlers.shared import decode_cursor, limit_plus_one, page
from caseward.api.problems import Problem, not_found

AUDIT_COLUMNS = """id, occurred_at, actor_user_id, actor_kind, client_id, action, entity_type, entity_id, request_id,
  host(source_ip) as source_ip, changed_fields, before, after"""


def list_audit_events(call: Call) -> Result:
    q = call.query
    where, params = ["true"], []
    for name in ("client_id", "actor_user_id", "entity_type", "entity_id", "action"):
        if name in q:
            where.append(f"{name} = %s")
            params.append(str(q[name]) if name == "entity_id" else q[name])
    if "from" in q:
        where.append("occurred_at >= %s")
        params.append(q["from"])
    if "to" in q:
        where.append("occurred_at < %s")
        params.append(q["to"])
    cursor = decode_cursor(call)
    if cursor:
        where.append("id < %s")
        params.append(cursor[0])
    rows = call.rows(f"select {AUDIT_COLUMNS} from caseward.audit_event where {' and '.join(where)}"
                     " order by id desc limit %s", params + [limit_plus_one(call)])
    return Result(200, page(call, rows, ["id"]))


def verify_audit_chain(call: Call) -> Result:
    broken = call.row("select caseward.verify_audit_chain() as id")["id"]
    last = call.row("select coalesce(max(id), 0) as id from caseward.audit_event")["id"]
    return Result(200, {"intact": broken is None, "first_broken_event_id": broken, "last_event_id": last,
                        "checked_at": datetime.now(UTC)})


def get_job_summary(call: Call) -> Result:
    rows = call.rows("""select kind, count(*) filter (where status = 'queued') as queued,
                               count(*) filter (where status = 'running') as running,
                               count(*) filter (where status = 'dead') as dead,
                               min(created_at) filter (where status = 'queued') as oldest_queued_at
                        from caseward.job group by kind order by kind""")
    return Result(200, {"as_of": datetime.now(UTC), "by_kind": rows})


def retry_job(call: Call) -> Result:
    job = call.row("select id, status from caseward.job where id = %s for update", [call.path["job_id"]])
    if job is None:
        raise not_found()
    if job["status"] != "dead":
        raise Problem(409, "not_dead", f"Only dead jobs can be retried; this one is {job['status']}")
    call.conn.execute("update caseward.job set status = 'queued', attempts = 0, run_after = now(), finished_at = null"
                      " where id = %s", [job["id"]])
    return Result(202)
