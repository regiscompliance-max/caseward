/**
 * Dates and times. Legal deadlines are UK time, and the team works from
 * Nairobi, so every deadline is shown in UK time with the zone named, plus
 * how long is left.
 */

const UK = "Europe/London";

export function ukDateTime(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: UK, day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(new Date(iso)) + " UK time";
}

export function ukDate(isoDate: string): string {
  // Date-only values are already UK dates; format without shifting time zones.
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "numeric", month: "long", year: "numeric" })
    .format(new Date(Date.UTC(y, m - 1, d)));
}

/** "due in 2 days 3 hours", "overdue by 45 minutes". */
export function timeLeft(iso: string, now: Date = new Date()): string {
  const ms = new Date(iso).getTime() - now.getTime();
  const minutes = Math.round(Math.abs(ms) / 60_000);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  const parts = days > 0 ? [plural(days, "day"), hours ? plural(hours, "hour") : ""]
    : hours > 0 ? [plural(hours, "hour"), mins ? plural(mins, "minute") : ""]
    : [plural(Math.max(mins, 1), "minute")];
  const text = parts.filter(Boolean).join(" ");
  return ms >= 0 ? `due in ${text}` : `overdue by ${text}`;
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}
