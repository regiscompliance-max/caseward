"""Load UK bank holidays as a new, immutable holiday calendar version.

The source is the GOV.UK feed (https://www.gov.uk/bank-holidays.json), which
has one division per region: england-and-wales, scotland, northern-ireland.
"""

from datetime import date
from typing import Any
from uuid import UUID

import psycopg

REGIONS = ("england-and-wales", "scotland", "northern-ireland")
GOVUK_URL = "https://www.gov.uk/bank-holidays.json"


def parse_govuk(payload: dict[str, Any], region: str) -> list[tuple[date, str]]:
    if region not in REGIONS:
        raise ValueError(f"unknown region {region!r}")
    events = payload[region]["events"]
    holidays = sorted({(date.fromisoformat(e["date"]), e["title"]) for e in events})
    if not holidays:
        raise ValueError(f"no bank holidays for {region}")
    return holidays


def publish_calendar(
    conn: psycopg.Connection, region: str, holidays: list[tuple[date, str]], source: str
) -> UUID:
    """Insert the next calendar version for ``region``. Run inside a staff transaction."""
    row = conn.execute(
        """
        insert into caseward.holiday_calendar (region, version, source)
        select %s, coalesce(max(version), 0) + 1, %s
        from caseward.holiday_calendar where region = %s
        returning id
        """,
        [region, source, region],
    ).fetchone()
    calendar_id = row[0]
    with conn.cursor() as cur:
        cur.executemany(
            "insert into caseward.bank_holiday (calendar_id, holiday_date, title) values (%s, %s, %s)",
            [(calendar_id, day, title) for day, title in holidays],
        )
    return calendar_id


def load_calendar(conn: psycopg.Connection, calendar_id: UUID) -> frozenset[date]:
    rows = conn.execute(
        "select holiday_date from caseward.bank_holiday where calendar_id = %s", [calendar_id]
    ).fetchall()
    return frozenset(r[0] for r in rows)


# Which GOV.UK bank holiday region applies in each jurisdiction.
REGION_FOR_JURISDICTION = {
    "england": "england-and-wales",
    "wales": "england-and-wales",
    "scotland": "scotland",
    "northern_ireland": "northern-ireland",
}


def latest_calendar(conn: psycopg.Connection, region: str) -> tuple[UUID, date] | None:
    """The newest calendar for a region, and the last date it covers (31 December
    of the year of its last listed holiday, since GOV.UK publishes whole years)."""
    row = conn.execute(
        """
        select c.id, make_date(extract(year from max(h.holiday_date))::int, 12, 31)
        from caseward.holiday_calendar c join caseward.bank_holiday h on h.calendar_id = c.id
        where c.region = %s
        group by c.id, c.version order by c.version desc limit 1
        """,
        [region],
    ).fetchone()
    return (row[0], row[1]) if row else None
