"""
Shared type-coercion helpers used by multiple integration mappers.
"""

from datetime import date, datetime, timezone


def safe_date(value):
    """Parse a date string (YYYY-MM-DD or ISO datetime) or return None."""
    if value is None or value == "" or value == "0000-00-00":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(value)[:19], fmt).date()
            except ValueError:
                continue
    except (TypeError, ValueError):
        pass
    return None


def safe_datetime(value):
    """Parse a datetime string or return None. Always returns timezone-aware."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        pass
    try:
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(str(value)[:19], fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    except (TypeError, ValueError):
        pass
    return None
