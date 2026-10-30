"""Deadline calculation.

Rules are data (``caseward.deadline_rule``); this module only applies them.
Each stored deadline records the rule version, clock start, holiday calendar
and ``CALCULATOR_VERSION`` so it can be recalculated exactly later. Change
``CALCULATOR_VERSION`` whenever the conventions below change.

Conventions (to be confirmed by the compliance lead):
  * Hour durations are elapsed time from the clock start.
  * Day and month durations end at 23:59:59 UK time on the final day.
  * Working days exclude weekends and the bank holidays of the property's
    jurisdiction. The day the clock starts is never counted, so a clock that
    starts on a weekend or bank holiday effectively starts the next working day.
  * Month durations land on the same day of the month, or the last day of a
    shorter month.
"""

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

CALCULATOR_VERSION = "1"

UK = ZoneInfo("Europe/London")
END_OF_DAY = time(23, 59, 59)


@dataclass(frozen=True)
class DeadlineRule:
    rule_key: str
    version: int
    duration: int
    duration_unit: Literal["hours", "days", "months"]
    day_type: Literal["working", "calendar"] | None = None

    def __post_init__(self) -> None:
        if self.duration <= 0:
            raise ValueError("duration must be positive")
        if (self.duration_unit == "days") != (self.day_type is not None):
            raise ValueError("day_type is required for day durations and only for them")


def compute_due_at(
    rule: DeadlineRule,
    clock_started_at: datetime,
    bank_holidays: frozenset[date] = frozenset(),
) -> datetime:
    if clock_started_at.tzinfo is None:
        raise ValueError("clock_started_at must be timezone-aware")

    if rule.duration_unit == "hours":
        # Arithmetic on an aware datetime in a ZoneInfo zone is wall-clock, so
        # go via UTC to get elapsed hours across clock changes.
        return clock_started_at.astimezone(UTC) + timedelta(hours=rule.duration)

    start_day = clock_started_at.astimezone(UK).date()
    if rule.duration_unit == "months":
        due_day = add_months(start_day, rule.duration)
    elif rule.day_type == "calendar":
        due_day = start_day + timedelta(days=rule.duration)
    else:
        due_day = add_working_days(start_day, rule.duration, bank_holidays)
    return datetime.combine(due_day, END_OF_DAY, tzinfo=UK)


def is_working_day(day: date, bank_holidays: frozenset[date]) -> bool:
    return day.weekday() < 5 and day not in bank_holidays


def add_working_days(start: date, days: int, bank_holidays: frozenset[date]) -> date:
    current = start
    counted = 0
    while counted < days:
        current += timedelta(days=1)
        if is_working_day(current, bank_holidays):
            counted += 1
    return current


def add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
