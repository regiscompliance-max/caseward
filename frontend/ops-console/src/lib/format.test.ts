import { timeLeft, ukDate, ukDateTime } from "./format";

const now = new Date("2026-10-30T12:00:00Z");

describe("timeLeft", () => {
  it.each([
    ["2026-11-01T15:00:00Z", "due in 2 days 3 hours"],
    ["2026-10-30T13:30:00Z", "due in 1 hour 30 minutes"],
    ["2026-10-30T12:20:00Z", "due in 20 minutes"],
    ["2026-10-30T11:15:00Z", "overdue by 45 minutes"],
    ["2026-10-29T12:00:00Z", "overdue by 1 day"],
  ])("%s is %s", (due, expected) => {
    expect(timeLeft(due, now)).toBe(expected);
  });
});

describe("UK formatting", () => {
  it("shows deadlines in UK time and says so, including across the clock change", () => {
    expect(ukDateTime("2027-01-11T23:59:59Z")).toBe("11 Jan 2027, 23:59 UK time");
    expect(ukDateTime("2026-07-01T22:30:00Z")).toBe("1 Jul 2026, 23:30 UK time");
  });

  it("formats date-only values without shifting the day", () => {
    expect(ukDate("2031-09-14")).toBe("14 September 2031");
  });
});
