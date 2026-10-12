# v1.0 2026-10-10  Brief 14 tests: database time windows (b14lib/windows.py). v1.0: first version.
from datetime import datetime, timezone

import pytest

from b14lib import windows


def t(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


@pytest.mark.parametrize("now,margin,ok", [
    (t(2026, 10, 10, 2, 0), 0, False),          # inside the nightly window
    (t(2026, 10, 10, 1, 49), 0, True),
    (t(2026, 10, 10, 1, 40), 20, False),        # would run into 01:50
    (t(2026, 10, 10, 4, 0), 20, True),
    (t(2026, 10, 10, 23, 45), 20, True),        # the next day's window opens later
    (t(2026, 10, 11, 1, 35), 20, False),
    (t(2026, 10, 12, 10, 0), 0, False),         # Monday brief 11 live shots
    (t(2026, 10, 12, 8, 50), 20, False),        # would run into 09:00
    (t(2026, 10, 12, 16, 0), 20, True),
    (t(2026, 10, 13, 10, 0), 20, True),
])
def test_windows(now, margin, ok):
    assert windows.db_window_open(now, margin)[0] is ok


def test_require_raises_and_naive_time_is_refused():
    with pytest.raises(windows.WindowClosed):
        windows.require_db_window(t(2026, 10, 10, 3, 0))
    with pytest.raises(ValueError):
        windows.db_window_open(datetime(2026, 10, 10, 12, 0))
