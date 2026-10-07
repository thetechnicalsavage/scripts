#!/usr/bin/env python3
# v1.2 - app 900 (integrator): summarise the load driver's per-minute log lines for the soak check.
#        Reads ~/anomaly/logs/driver.log (and its first rotated file), keeps the INFO "minute" lines of the last
#        N UTC minutes, and prints one row per minute: the counts, the client-side latency, the feed status and
#        the measured TPS against the load shape's target (peak_tps x shape x load_pct/100; the driver adds a
#        seeded +/-5% jitter per minute on top, which the 25% tolerance absorbs). Then a summary: error rate,
#        largest TPS deviation, missing minutes, restarts, the newest minute's age, WARNING/ERROR/CRITICAL lines.
#        v1.2: public copy: host name and local-time references removed. v1.1: also fails when the newest minute line ended more than 2 minutes ago (phase 4 tune): a stopped
#              driver left a clean window behind it and passed every v1.0 check (a trailing gap has no next minute).
#        v1.0: first version, 01-Oct-2026.
#        Read only: opens the log files for reading, connects to nothing, writes nothing.
#
# Run as : the demo user on the demo VM (tools/anom_soak_check.sh calls it), or anywhere with a copy of the log.
# Usage  : anom_soak_minutes.py [--log FILE] [--minutes N] [--peak-tps X] [--tolerance PCT] [--max-err-pct PCT]
# Exit   : 0 every check passed; 1 usage or the log cannot be read; 3 a check failed (the summary says which).
# Pinned : Python 3.12 standard library only.
"""Per-minute table and health summary of the app 900 load driver log (JSON lines)."""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

EXIT_OK, EXIT_USAGE, EXIT_CHECK = 0, 1, 3
DEFAULT_LOG = "~/anomaly/logs/driver.log"
TS_MINUTE_FMT = "%Y-%m-%dT%H:%MZ"
TS_LINE_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
# the newest minute line is written about 0.3 s after its minute ends; two missed lines make it this old
MAX_TAIL_AGE_S = 120


@dataclass(frozen=True)
class Minute:
    ts: datetime                 # the minute's start, UTC
    n_ok: int
    n_err: int
    app_tps: float
    p50: Optional[float]
    p95: Optional[float]
    err_pct: float
    feed: str
    buffered: int
    shape: float
    load_pct: float
    enabled: bool
    errors: dict

    def expected_tps(self, peak_tps: float) -> float:
        return peak_tps * self.shape * self.load_pct / 100.0 if self.enabled else 0.0


@dataclass
class Parsed:
    minutes: list[Minute] = field(default_factory=list)
    starts: list[datetime] = field(default_factory=list)       # "starting" lines (one per process start)
    peak_tps: Optional[float] = None                            # from the newest "starting" line
    problems: collections.Counter = field(default_factory=collections.Counter)   # (level, msg) -> count
    bad_lines: int = 0


def _utc(text: str, fmt: str) -> datetime:
    return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)


def parse_lines(lines: Iterable[str], since: datetime) -> Parsed:
    """Keep what happened at or after `since` (UTC). Lines that are not JSON objects are counted, not fatal."""
    out = Parsed()
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            doc = json.loads(raw)
            stamp = _utc(doc["ts"], TS_LINE_FMT)
        except (ValueError, KeyError, TypeError):
            out.bad_lines += 1
            continue
        if not isinstance(doc, dict):
            out.bad_lines += 1
            continue
        msg, level = doc.get("msg"), doc.get("level")
        if msg == "starting":
            # peak_tps is a start-time setting: take the newest one even when that start is before the window
            if isinstance(doc.get("peak_tps"), (int, float)):
                out.peak_tps = float(doc["peak_tps"])
            if stamp >= since:
                out.starts.append(stamp)
        if stamp < since:
            continue
        if level in ("WARNING", "ERROR", "CRITICAL"):
            out.problems[(level, str(msg))] += 1
        if msg != "minute":
            continue
        try:
            minute = Minute(
                ts=_utc(doc["ts_minute"], TS_MINUTE_FMT), n_ok=int(doc["n_ok"]), n_err=int(doc["n_err"]),
                app_tps=float(doc["app_tps"]), p50=doc.get("app_p50_ms"), p95=doc.get("app_p95_ms"),
                err_pct=float(doc["app_err_pct"]), feed=str(doc.get("feed")), buffered=int(doc.get("buffered", 0)),
                shape=float(doc["shape"]), load_pct=float(doc["load_pct"]), enabled=bool(doc["enabled"]),
                errors=dict(doc.get("errors") or {}))
        except (KeyError, TypeError, ValueError):
            out.bad_lines += 1
            continue
        if minute.ts >= since:
            out.minutes.append(minute)
    out.minutes.sort(key=lambda m: m.ts)
    return out


def read_log(path: str) -> list[str]:
    """The rotated file first (older lines), then the live one. A missing rotated file is normal."""
    lines: list[str] = []
    for name in (path + ".1", path):
        try:
            with open(name, encoding="utf-8") as handle:
                lines.extend(handle.read().splitlines())
        except FileNotFoundError:
            if name == path:
                raise
    return lines


def _fmt(value: Optional[float], width: int, places: int = 1) -> str:
    return f"{'-':>{width}}" if value is None else f"{value:>{width}.{places}f}"


def report(parsed: Parsed, peak_tps: float, tolerance_pct: float, max_err_pct: float, *,
           now: datetime) -> tuple[list[str], bool]:
    """The table and the summary lines, and whether every check passed. `now` is UTC (aware)."""
    lines = ["minute_utc         n_ok n_err    tps  target   dev%   p50_ms   p95_ms  err%  feed      buf  shape",
             "----------------- ----- ----- ------ ------- ------ -------- -------- ----- --------- --- ------"]
    worst_dev = 0.0
    for m in parsed.minutes:
        target = m.expected_tps(peak_tps)
        dev = None if target <= 0 else 100.0 * (m.app_tps - target) / target
        if dev is not None:
            worst_dev = max(worst_dev, abs(dev))
        lines.append(f"{m.ts.strftime('%Y-%m-%d %H:%M')} {m.n_ok:>5} {m.n_err:>5} {m.app_tps:>6.2f} {target:>7.2f} "
                     f"{_fmt(dev, 6)} {_fmt(m.p50, 8)} {_fmt(m.p95, 8)} {m.err_pct:>5.2f} {m.feed:<9} "
                     f"{m.buffered:>3} {m.shape:>6.3f}")
    ok_total = sum(m.n_ok for m in parsed.minutes)
    err_total = sum(m.n_err for m in parsed.minutes)
    err_pct = 0.0 if ok_total + err_total == 0 else 100.0 * err_total / (ok_total + err_total)
    codes: collections.Counter = collections.Counter()
    for m in parsed.minutes:
        codes.update(m.errors)
    missing = []
    for prev, cur in zip(parsed.minutes, parsed.minutes[1:]):
        step = int((cur.ts - prev.ts).total_seconds() // 60)
        missing.extend(prev.ts + timedelta(minutes=k) for k in range(1, step))
    not_fed = [m for m in parsed.minutes if m.feed != "ok"]
    # a start before the first full minute of the window is the start being watched; one after it is a restart
    restarts = [t for t in parsed.starts if parsed.minutes and t > parsed.minutes[0].ts]
    # trailing gap: seconds since the newest minute in the log ended (None when there is no minute at all)
    newest = parsed.minutes[-1] if parsed.minutes else None
    tail_age = None if newest is None else (now - (newest.ts + timedelta(minutes=1))).total_seconds()

    checks = {
        "minutes present": bool(parsed.minutes),
        f"error rate below {max_err_pct:g}%": err_pct < max_err_pct,
        f"TPS within {tolerance_pct:g}% of the shape every minute": worst_dev <= tolerance_pct,
        "no missing minute in the driver log": not missing,
        "every minute reached APP_MINUTE (feed ok)": not not_fed,
        "no ERROR or CRITICAL line": not any(level in ("ERROR", "CRITICAL") for level, _ in parsed.problems),
        "no driver restart after the first minute of the window": not restarts,
        "newest minute line ended at most 2 minutes ago": tail_age is not None and tail_age <= MAX_TAIL_AGE_S,
    }
    lines += ["", f"minutes {len(parsed.minutes)}; transactions ok {ok_total}, failed {err_total} "
              f"({err_pct:.3f}%); peak_tps {peak_tps:g}; largest |TPS deviation| {worst_dev:.1f}%",
              f"error codes: {dict(sorted(codes.items())) or 'none'}",
              f"missing minutes: {[t.strftime('%H:%M') for t in missing] or 'none'}; "
              f"minutes not fed: {[m.ts.strftime('%H:%M') for m in not_fed] or 'none'}",
              f"driver starts in the window: {[t.strftime('%H:%M:%SZ') for t in parsed.starts] or 'none'}; "
              f"unparsable lines: {parsed.bad_lines}",
              "newest minute line: none" if newest is None else
              f"newest minute line: {newest.ts.strftime('%H:%M')} (ended {tail_age:.0f} s ago)"]
    if parsed.problems:
        for (level, msg), count in sorted(parsed.problems.items()):
            lines.append(f"  {level:<8} x{count:<5} {msg}")
    else:
        lines.append("warnings/errors: none")
    for name, passed in checks.items():
        lines.append(f"CHECK {'PASS' if passed else 'FAIL'}  {name}")
    return lines, all(checks.values())


def main(argv: Optional[list[str]] = None, now: Optional[datetime] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default=DEFAULT_LOG)
    parser.add_argument("--minutes", type=int, default=30, help="window, whole UTC minutes back from now (1-1440)")
    parser.add_argument("--peak-tps", type=float, default=None, help="default: from the newest 'starting' line")
    parser.add_argument("--tolerance", type=float, default=25.0, help="allowed TPS deviation, percent")
    parser.add_argument("--max-err-pct", type=float, default=1.0, help="allowed transaction error rate, percent")
    args = parser.parse_args(argv)
    if not 1 <= args.minutes <= 1440:
        print("anom_soak_minutes: --minutes must be 1-1440", file=sys.stderr)
        return EXIT_USAGE
    now = now or datetime.now(timezone.utc)
    since = now.replace(second=0, microsecond=0) - timedelta(minutes=args.minutes)
    path = os.path.expanduser(args.log)
    try:
        parsed = parse_lines(read_log(path), since)
    except OSError as exc:
        print(f"anom_soak_minutes: cannot read {path}: {exc.strerror}", file=sys.stderr)
        return EXIT_USAGE
    peak = args.peak_tps if args.peak_tps is not None else parsed.peak_tps
    if peak is None or peak <= 0:
        print("anom_soak_minutes: no peak_tps in the log; pass --peak-tps", file=sys.stderr)
        return EXIT_USAGE
    lines, passed = report(parsed, peak, args.tolerance, args.max_err_pct, now=now)
    print("\n".join(lines))
    return EXIT_OK if passed else EXIT_CHECK


if __name__ == "__main__":
    sys.exit(main())
