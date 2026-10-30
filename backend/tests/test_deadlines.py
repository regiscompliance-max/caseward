from datetime import date, datetime

import pytest

from caseward.bank_holidays import parse_govuk
from caseward.deadlines import UK, DeadlineRule, add_months, compute_due_at

HOLIDAYS_2026 = frozenset({date(2026, 12, 25), date(2026, 12, 28)})


def rule(duration, unit, day_type=None):
    return DeadlineRule("test", 1, duration, unit, day_type)


def uk(*args):
    return datetime(*args, tzinfo=UK)


def test_working_days_skip_weekends_and_bank_holidays():
    # Wed 23 Dec 2026: 24th, 29th, 30th, 31st are the first four working days.
    due = compute_due_at(rule(4, "days", "working"), uk(2026, 12, 23, 9), HOLIDAYS_2026)
    assert due == uk(2026, 12, 31, 23, 59, 59)


def test_clock_starting_on_a_weekend_counts_from_next_working_day():
    due = compute_due_at(rule(1, "days", "working"), uk(2026, 10, 31, 22), frozenset())
    assert due.date() == date(2026, 11, 2)


def test_working_days_use_uk_date_not_utc():
    # 00:30 BST on Monday 1 June is still Sunday 31 May in UTC.
    start = datetime.fromisoformat("2026-05-31T23:30:00+00:00")
    due = compute_due_at(rule(1, "days", "working"), start, frozenset())
    assert due.date() == date(2026, 6, 2)


def test_calendar_days_ignore_weekends():
    due = compute_due_at(rule(7, "days", "calendar"), uk(2026, 10, 30, 12), HOLIDAYS_2026)
    assert due == uk(2026, 11, 6, 23, 59, 59)


def test_hours_are_elapsed_time_across_clock_change():
    # Clocks go back at 02:00 BST on Sunday 25 October 2026.
    due = compute_due_at(rule(24, "hours"), uk(2026, 10, 24, 12), frozenset())
    assert due.astimezone(UK) == uk(2026, 10, 25, 11)


def test_months_clamp_to_end_of_shorter_month():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2027, 11, 30), 3) == date(2028, 2, 29)
    due = compute_due_at(rule(12, "months"), uk(2026, 3, 15, 10), frozenset())
    assert due == uk(2027, 3, 15, 23, 59, 59)


def test_naive_clock_start_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        compute_due_at(rule(1, "hours"), datetime(2026, 1, 1), frozenset())


@pytest.mark.parametrize("unit, day_type", [("days", None), ("hours", "working")])
def test_rule_day_type_must_match_unit(unit, day_type):
    with pytest.raises(ValueError):
        rule(1, unit, day_type)


def test_parse_govuk_feed():
    payload = {
        "scotland": {"division": "scotland", "events": [
            {"title": "St Andrew's Day", "date": "2026-11-30", "notes": "", "bunting": True},
            {"title": "2nd January", "date": "2026-01-02", "notes": "", "bunting": True},
        ]},
    }
    assert parse_govuk(payload, "scotland") == [
        (date(2026, 1, 2), "2nd January"), (date(2026, 11, 30), "St Andrew's Day")]
    with pytest.raises(ValueError):
        parse_govuk(payload, "wales")
