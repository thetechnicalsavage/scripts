# v1.0 2026-10-10  Brief 14: database and APEX time windows (PLAN.md v1.0 section 5 rule 6). v1.0: first version.
"""No database or APEX work between 01:50 and 04:00 UTC (every day), nor on Monday 12-Oct-2026 09:00-16:00 UTC
(brief 11's live screenshots on the same database). OCI-only steps are not limited. The SQL scripts repeat the check.
"""
from datetime import datetime, time, timedelta, timezone

NIGHTLY = (time(1, 50), time(4, 0))
BLACKOUTS = ((datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc), datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)),)
MARGIN_MIN = 20     # a database step must have this many minutes before the next window opens


class WindowClosed(RuntimeError):
    pass


def db_window_open(now: datetime | None = None, margin_min: int = 0) -> tuple[bool, str]:
    """(True, '') when database work may start now and run for margin_min minutes; else (False, reason)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("a timezone-aware UTC time is required")
    now = now.astimezone(timezone.utc)
    end = now + timedelta(minutes=margin_min)
    for t in (now, end):
        if NIGHTLY[0] <= t.time() < NIGHTLY[1]:
            return False, "01:50-04:00 UTC nightly window"
    # the interval [now, end] must not touch a nightly window that starts in between (crossing 01:50)
    start_today = now.replace(hour=NIGHTLY[0].hour, minute=NIGHTLY[0].minute, second=0, microsecond=0)
    for s in (start_today, start_today + timedelta(days=1)):
        if now < s <= end:
            return False, "the 01:50 UTC window opens within the step's margin"
    for a, b in BLACKOUTS:
        if now < b and end > a:
            return False, "Monday 12-Oct 09:00-16:00 UTC (brief 11 live screenshots)"
    return True, ""


def require_db_window(now: datetime | None = None, margin_min: int = MARGIN_MIN) -> None:
    ok, why = db_window_open(now, margin_min)
    if not ok:
        raise WindowClosed(f"database step refused: {why}")
