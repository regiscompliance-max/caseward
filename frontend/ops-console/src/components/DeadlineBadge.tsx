import { ALERT_LEVELS } from "@/lib/labels";
import { timeLeft, ukDateTime } from "@/lib/format";

const SHAPES: Record<string, string> = { overdue: "■", alert_80: "▲", alert_50: "●", on_track: "○" };

/** A deadline's state as words and a shape, never colour alone. */
export function DeadlineBadge({ dueAt, level, now }: { dueAt?: string | null; level?: string | null; now?: Date }) {
  if (!dueAt) {
    return <span className="badge" data-level="none">No legal deadline</span>;
  }
  const state = level ?? (new Date(dueAt) < (now ?? new Date()) ? "overdue" : "on_track");
  return (
    <span className="badge" data-level={state} title={ukDateTime(dueAt)}>
      <span aria-hidden="true">{SHAPES[state] ?? "○"}</span>
      <span>{ALERT_LEVELS[state] ?? state}: {timeLeft(dueAt, now)}</span>
    </span>
  );
}
