"""Date helpers shared by the leave rules and the screens."""

from datetime import date, datetime, timedelta

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def today() -> date:
    return date.today()


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def to_datetime(value: date) -> datetime:
    return datetime(value.year, value.month, value.day)


def to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError(f"Expected a date, got {type(value).__name__}")


def format_date(value: date) -> str:
    return f"{value.day:02d} {MONTHS[value.month - 1]} {value.year}"


def format_when(value) -> str:
    if not isinstance(value, datetime):
        return ""
    return f"{format_date(value.date())}, {value.hour:02d}:{value.minute:02d}"


def clean_spaces(value: str) -> str:
    return " ".join(value.split())


def leave_days(start: date, end: date, holidays: set[date]) -> int:
    """Count inclusive dates, skipping the holiday list.

    Weekly offs count as leave unless an admin adds those dates as holidays.
    """
    if end < start:
        return 0
    count = 0
    current = start
    while current <= end:
        if current not in holidays:
            count += 1
        current += timedelta(days=1)
    return count


def mondays_in_year(year: int) -> list[date]:
    current = date(year, 1, 1)
    current += timedelta(days=(0 - current.weekday()) % 7)
    found = []
    while current.year == year:
        found.append(current)
        current += timedelta(days=7)
    return found
