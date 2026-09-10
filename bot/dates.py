"""Date math for recurring events: figuring out the next occurrence of an
event given its recurrence rule, with sane handling of edge cases like
Feb 29 birthdays on non-leap years, or a "31st of every month" event
landing in February.
"""
from calendar import monthrange
from datetime import date
from typing import Optional

RECURRENCES = ("once", "yearly", "monthly")


def safe_date(year: int, month: int, day: int) -> date:
    """Build a date, clamping the day to the last valid day of that month
    (e.g. Feb 29 in a non-leap year -> Feb 28, day 31 in April -> Apr 30)."""
    last_day = monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return safe_date(year, month, d.day)


def next_occurrence(event_date: date, recurrence: str, today: date) -> Optional[date]:
    """Return the next date (>= today) this event falls on, or None if the
    event has no more occurrences (a one-off event that's already passed)."""
    if recurrence == "once":
        return event_date if event_date >= today else None

    if recurrence == "yearly":
        candidate = safe_date(today.year, event_date.month, event_date.day)
        if candidate < today:
            candidate = safe_date(today.year + 1, event_date.month, event_date.day)
        return candidate

    if recurrence == "monthly":
        candidate = safe_date(today.year, today.month, event_date.day)
        if candidate < today:
            candidate = _add_months(candidate, 1)
        return candidate

    raise ValueError(f"Unknown recurrence: {recurrence!r}")
