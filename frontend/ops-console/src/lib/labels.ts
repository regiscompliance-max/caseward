import type { Schemas } from "@/api/client";

export const TASK_KINDS: Record<Schemas["TaskKind"], string> = {
  review_extraction: "Review certificate",
  triage_case: "Triage case",
  draft_letter: "Draft letter",
  approve_letter: "Approve letter",
  chase_works: "Chase works",
  other: "Manual handling",
};

export const TASK_STATUSES: Record<Schemas["TaskStatus"], string> = {
  queued: "Waiting",
  in_progress: "In progress",
  blocked: "Blocked",
  done: "Done",
  cancelled: "Cancelled",
};

export const ALERT_LEVELS: Record<string, string> = {
  on_track: "On track",
  alert_50: "Half the time used",
  alert_80: "80% of the time used",
  overdue: "Overdue",
};

export const FLAG_REASONS: Record<Schemas["ExtractionFlag"]["reason"], string> = {
  low_confidence: "Low confidence",
  contradiction: "Contradiction",
  missing_required: "Missing",
  invalid_value: "Not valid",
  date_out_of_range: "Date looks wrong",
};

export const PRIORITIES: Record<number, string> = { 1: "Urgent", 2: "High", 3: "Normal", 4: "Low", 5: "Lowest" };
