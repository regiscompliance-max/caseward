"""SQL and row shapes shared by the API and the worker."""

DEADLINE_SELECT = """
  select d.id, d.case_id, d.defect_id, d.inspection_id, d.rule_key, d.rule_version, r.title as rule_title,
         r.rule_pack, d.clock_started_at, d.due_at,
         (d.due_at at time zone 'Europe/London')::date as due_local_date,
         d.status, d.completed_at, p.alert_level, p.elapsed_fraction
  from caseward.deadline d
  join caseward.deadline_rule r on (r.rule_key, r.version) = (d.rule_key, d.rule_version)
  left join caseward.deadline_progress p on p.id = d.id
"""


def deadline_out(row: dict) -> dict:
    for kind in ("case", "defect", "inspection"):
        if row[f"{kind}_id"] is not None:
            subject = {"kind": kind, "id": row[f"{kind}_id"]}
    return {
        "id": row["id"],
        "subject": subject,
        "rule": {"key": row["rule_key"], "version": row["rule_version"], "title": row["rule_title"],
                 "rule_pack": row["rule_pack"]},
        "clock_started_at": row["clock_started_at"],
        "due_at": row["due_at"],
        "due_local_date": row["due_local_date"],
        "status": row["status"],
        "completed_at": row["completed_at"],
        "alert_level": row["alert_level"],
        "elapsed_fraction": row["elapsed_fraction"],
    }


CASE_EVENT_SELECT = """
  select e.id, e.case_id, e.kind, t.name as kind_name, t.is_excuse_evidence, e.occurred_at, e.channel, e.notes,
         e.evidence_document_id, e.contractor_client_id, e.corrects_event_id,
         (select c.id from caseward.case_event c where c.corrects_event_id = e.id limit 1) as corrected_by_event_id,
         e.recorded_by, e.recorded_at
  from caseward.case_event e
  join caseward.event_type t on (t.code, t.subject_kind) = (e.kind, e.subject_kind)
"""
