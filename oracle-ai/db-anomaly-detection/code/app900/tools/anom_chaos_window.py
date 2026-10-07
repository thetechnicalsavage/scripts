# v1.2 - app 900 (builder C, phase 4): what a chaos run did to the signals. For each CHAOS_RUN row picked, prints
#        the observer's FEATURE_MINUTE intervals around the run (the scenario's expected signals from PLAN.md
#        section 6, plus AAS and the driver's APP_TPS and APP_P95_MS), a baseline (the median of the clean
#        intervals near it), the in-window mean and peak and their change, and the load driver's per-minute log
#        lines and control events for the same window. For the blog transcript 05-chaos-scenarios.txt.
#        v1.2: public copy: host name and local-time references removed. v1.1: the baseline is the median of every clean interval within --baseline minutes on either side (an
#              interval is clean when it ends 30 s before and begins 60 s after every chaos run), optionally
#              bounded by --pool-from/--pool-to (one load regime only): with runs 3 minutes apart, the 5 minutes
#              before a run were never clean.
#              Notes are flattened to one CSV field (an ORA- message holds line breaks, commas and quotes).
#        v1.0: first version, 01-Oct-2026.
#
# Run as : the demo user on the demo VM (python3 3.12, standard library only), with oradb1 and ora26ai running.
# Usage  : python3 tools/anom_chaos_window.py --runs 3,4,5            (or --since 2026-10-01T13:00:00Z [--source TEST])
#          [--baseline 30] [--pool-from <UTC>] [--pool-to <UTC>] [--after 3] [--driver-log ~/anomaly/logs/driver.log]
# Access : SYSDBA by OS authentication inside the containers (docker exec), SELECT statements only: no password is
#          read, nothing is written to either database. The driver log is read, never written.
# Exit   : 0 ok; 1 usage, environment or a failed query.
import argparse
import csv
import glob
import io
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

LOG = logging.getLogger("anom_chaos_window")
TGT = os.environ.get("ANOM_TGT_CONTAINER", "oradb1")
OBS = os.environ.get("ANOM_OBS_CONTAINER", "ora26ai")
TIMEOUT_S = 120

# PLAN.md section 6: the attribution key of each scenario (pre-registered)
EXPECTED = {
    "blocking_chain": ["W_APPL", "ENQ_WAITS", "RT_TXN", "APP_P95_MS", "COMMITS"],
    "plan_regression": ["LIO_TXN", "LIO", "CPU_TXN", "RT_TXN", "APP_P95_MS"],
    "slow_drift": ["LIO_TXN", "CPU_TXN", "RT_TXN", "APP_P95_MS"],
    "batch_wrong_time": ["REDO", "BLKCHG", "W_COMMIT", "PWRITES", "LIO"],
    "hard_parse_storm": ["HARDPARSE", "PARSE", "W_CONCUR", "CPU"],
    "commit_storm": ["COMMITS", "REDO", "W_COMMIT", "W_CONFIG"],
    "io_storm": ["PIO", "PIO_BYTES", "W_USERIO", "LONGSCANS"],
    "cpu_hog": ["CPU", "CPU_TXN", "AAS", "DBTIME"],
    "temp_spill": ["TEMP", "W_USERIO", "PWRITES"],
    "conn_leak": ["SESSIONS", "LOGONS"],
    "logon_storm": ["LOGONS", "CPU", "W_OTHER"],
    "app_error_burst": ["APP_ERR_PCT", "TXN", "COMMITS"],
}
ALWAYS = ["AAS", "APP_TPS", "APP_P95_MS"]
ALL_SIGNALS = ["AAS", "DBTIME", "CPU", "CPU_TXN", "WAIT_RATIO", "LIO_TXN", "LIO", "PIO", "PIO_BYTES", "PWRITES",
               "REDO", "BLKCHG", "COMMITS", "TXN", "CALLS", "EXECS", "HARDPARSE", "PARSE", "LOGONS", "SESSIONS",
               "RT_TXN", "SQL_RT", "ENQ_WAITS", "TEMP", "LONGSCANS", "W_USERIO", "W_COMMIT", "W_CONCUR", "W_APPL",
               "W_CONFIG", "W_OTHER", "APP_TPS", "APP_P50_MS", "APP_P95_MS", "APP_ERR_PCT"]
ISO = "%Y-%m-%dT%H:%M:%SZ"


class QueryError(Exception):
    pass


def sqlplus(container: str, connect: list[str], sql: str) -> list[dict]:
    """Run one SELECT through SQL*Plus inside the container as SYSDBA; return the CSV rows as dicts."""
    script = "\n".join(connect + [
        "set markup csv on quote off", "set feedback off", "set pagesize 50000", "set heading on", "set numwidth 40",
        "whenever sqlerror exit failure", sql.rstrip().rstrip(";") + ";", "exit"]) + "\n"
    try:
        res = subprocess.run(["docker", "exec", "-i", container, "bash", "-lc", "sqlplus -s -L /nolog"],
                             input=script, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QueryError(f"{container}: {exc}") from exc
    out = res.stdout
    if res.returncode != 0 or re.search(r"^(ORA|SP2)-\d+", out, re.M):
        raise QueryError(f"{container}: query failed (rc={res.returncode}): {out.strip()[:400]}")
    lines = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith("Session altered")]
    if not lines:
        return []
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def target_rows(sql: str) -> list[dict]:
    return sqlplus(TGT, ["connect / as sysdba", "alter session set container=FREEPDB1;"], sql)


def observer_rows(sql: str) -> list[dict]:
    return sqlplus(OBS, ["connect / as sysdba", "alter session set container=ORCLPDB1;"], sql)


def ts_of(text: str) -> datetime | None:
    if not text:
        return None
    return datetime.strptime(text, ISO).replace(tzinfo=timezone.utc)


def lit(t: datetime) -> str:
    return f"to_date('{t.strftime('%Y-%m-%d %H:%M:%S')}', 'YYYY-MM-DD HH24:MI:SS')"


def fmt(v: float | None) -> str:
    if v is None:
        return "-"
    a = abs(v)
    for div, suf in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if a >= div * 10:
            return f"{v / div:.1f}{suf}"
    if a >= 100:
        return f"{v:.0f}"
    if a >= 1:
        return f"{v:.2f}"
    return f"{v:.3f}"


def num(text: str | None) -> float | None:
    if text is None or text.strip() == "":
        return None
    return float(text)


def runs(args) -> list[dict]:
    cols = ("run_id, scenario, intensity, source, status, restored, "
            "to_char(start_ts, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') start_ts, "
            "to_char(planned_end_ts, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') planned_end_ts, "
            "to_char(end_ts, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') end_ts, "
            # one CSV field: no line break, comma or double quote (an ORA- message can hold all three)
            "translate(note, chr(10)||chr(13)||',\"', '  ;''') note")
    if args.runs:
        ids = ",".join(str(int(x)) for x in args.runs.split(","))
        where = f"run_id in ({ids})"
    else:
        since = ts_of(args.since)
        where = f"start_ts >= cast({lit(since)} as timestamp)"
        if args.source:
            if args.source not in ("UI", "SCHEDULE", "TEST"):
                raise SystemExit("--source must be UI, SCHEDULE or TEST")
            where += f" and source = '{args.source}'"
    return target_rows(f"select {cols} from SHOP.CHAOS_RUN where {where} order by run_id")


def all_windows() -> list[tuple[int, datetime, datetime]]:
    rows = target_rows("select run_id, to_char(start_ts, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') s, "
                       "to_char(nvl(end_ts, planned_end_ts), 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') e from SHOP.CHAOS_RUN")
    return [(int(r["RUN_ID"]), ts_of(r["S"]), ts_of(r["E"])) for r in rows]


def features(t0: datetime, t1: datetime) -> list[dict]:
    cols = ", ".join(ALL_SIGNALS)
    return observer_rows(f"select to_char(ts, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') ts, {cols} from ANOMOPS.FEATURE_MINUTE "
                         f"where ts >= {lit(t0)} and ts < {lit(t1)} order by ts")


def driver_lines(path_glob: str, t0: datetime, t1: datetime) -> list[dict]:
    out = []
    for path in sorted(glob.glob(os.path.expanduser(path_glob) + "*")):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        LOG.warning("skipped a line that is not JSON in %s", os.path.basename(path))
                        continue
                    stamp = d.get("ts_minute") if d.get("msg") == "minute" else d.get("ts")
                    if not stamp:
                        continue
                    t = datetime.strptime(stamp[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
                    if t0 <= t < t1 and (d.get("msg") == "minute" or d.get("thread") == "control"
                                         or d.get("level") in ("WARNING", "ERROR", "CRITICAL")
                                         or d.get("msg") in ("starting", "stopped", "stop requested")):
                        out.append(d)
        except OSError as exc:
            LOG.warning("cannot read %s: %s", path, exc)
    out.sort(key=lambda d: d.get("ts", ""))
    return out


def report(run: dict, windows, args) -> None:
    rid = int(run["RUN_ID"])
    start, end = ts_of(run["START_TS"]), ts_of(run["END_TS"] or run["PLANNED_END_TS"])
    signals = list(dict.fromkeys(EXPECTED[run["SCENARIO"]] + ALWAYS))
    t0 = start - timedelta(minutes=3)
    t1 = end + timedelta(minutes=args.after)
    shown = features(t0, t1)
    p0 = max(start - timedelta(minutes=args.baseline), ts_of(args.pool_from) or start - timedelta(days=1))
    p1 = min(end + timedelta(minutes=args.baseline), ts_of(args.pool_to) or end + timedelta(days=1))
    pool = features(p0, p1) if p0 < p1 else []

    def clean(r) -> bool:
        b = ts_of(r["TS"])
        e = b + timedelta(seconds=60)
        return not any(b < oe + timedelta(seconds=60) and e > os_ - timedelta(seconds=30) for _, os_, oe in windows)

    base = [r for r in pool if clean(r)]
    print(f"== run {rid} {run['SCENARIO']} {run['INTENSITY']} ({run['SOURCE']}): "
          f"{start.strftime('%H:%M:%S')} - {end.strftime('%H:%M:%S')} UTC, {run['STATUS']}, restored {run['RESTORED']}")
    print(f"   note: {run['NOTE'].replace(';', ',')}")
    print(f"   expected signals (PLAN section 6): {', '.join(EXPECTED[run['SCENARIO']])}")
    print("   METRIC_MINUTE / FEATURE_MINUTE, one row per 60-s interval (begin, UTC); * = at least 30 s inside the run,")
    print("   + = less than 30 s inside it (its APP_* columns are the driver minute before the run, so they are left out)")
    print("   " + f"{'interval':10}" + "".join(f"{s:>11}" for s in signals))
    inside, partial = [], []
    for r in shown:
        b = ts_of(r["TS"])
        e = b + timedelta(seconds=60)
        # FEATURE_MINUTE joins the driver minute nearest the interval, so an interval with less than 30 s inside
        # the run carries the driver minute before (or after) it: its DB columns count, its APP_* columns do not
        overlap = (min(e, end) - max(b, start)).total_seconds()
        mark = "*" if overlap >= 30 else ("+" if overlap > 0 else (" " if clean(r) else "x"))
        print("   " + f"{b.strftime('%H:%M:%S')} {mark}" + "".join(f"{fmt(num(r[s])):>11}" for s in signals))
        if overlap >= 30:
            inside.append(r)
        elif overlap > 0:
            partial.append(r)

    def rows_for(s):
        return inside if s.startswith("APP_") else inside + partial

    def median(rs, s):
        vals = sorted(num(r[s]) for r in rs if num(r[s]) is not None)
        if not vals:
            return None
        m = len(vals) // 2
        return vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2

    def mean(rs, s):
        vals = [num(r[s]) for r in rs if num(r[s]) is not None]
        return sum(vals) / len(vals) if vals else None

    def peak(rs, s):
        vals = [num(r[s]) for r in rs if num(r[s]) is not None]
        return max(vals) if vals else None

    print("   " + f"{'baseline':10}" + "".join(f"{fmt(median(base, s)):>11}" for s in signals)
          + f"   (median of {len(base)} clean interval(s), {p0.strftime('%H:%M')}-{p1.strftime('%H:%M')})")
    print("   " + f"{'run mean':10}" + "".join(f"{fmt(mean(rows_for(s), s)):>11}" for s in signals))
    print("   " + f"{'run peak':10}" + "".join(f"{fmt(peak(rows_for(s), s)):>11}" for s in signals))
    ratio = []
    for s in signals:
        b, p = median(base, s), peak(rows_for(s), s)
        if b is None or p is None:
            ratio.append("-")
        elif b == 0:
            ratio.append("from 0" if p else "0")
        else:
            ratio.append("x" + fmt(p / b))
    print("   " + f"{'peak/base':10}" + "".join(f"{x:>11}" for x in ratio))
    print("   x = less than 30 s before or 60 s after a chaos run: never part of a baseline")
    print("   driver (~/anomaly/logs/driver.log): per-minute lines and control events in the window")
    for d in driver_lines(args.driver_log, start - timedelta(minutes=2), t1):
        if d.get("msg") == "minute":
            errs = ",".join(f"{k}={v}" for k, v in (d.get("errors") or {}).items()) or "-"
            print(f"     {d['ts_minute'][11:16]}  ok={d.get('n_ok')} err={d.get('n_err')} tps={d.get('app_tps')} "
                  f"p50={d.get('app_p50_ms')}ms p95={d.get('app_p95_ms')}ms leak_held={d.get('leak_held')} "
                  f"logon_storm={d.get('logon_storm')} error_pct={d.get('error_pct')} errors={errs}")
        else:
            # (the "starting" line's target and observer are connect strings: never printed)
            extra = {k: v for k, v in d.items() if k in ("new", "target", "held", "opened", "closed", "error",
                                                          "signal", "detail") and not isinstance(v, str)
                     or k in ("signal", "error") and isinstance(v, str)}
            print(f"     {d['ts'][11:19]}  {d.get('level')} {d.get('msg')} {json.dumps(extra, sort_keys=True)}")
    print()


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s anom_chaos_window %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--runs", help="comma-separated CHAOS_RUN ids")
    g.add_argument("--since", help="runs that started at or after this UTC time, e.g. 2026-10-01T13:00:00Z")
    ap.add_argument("--source", help="with --since: UI, SCHEDULE or TEST")
    ap.add_argument("--baseline", type=int, default=30,
                    help="clean intervals within this many minutes of the run form the baseline (default 30)")
    ap.add_argument("--pool-from", help="no baseline interval before this UTC time (e.g. a load change)")
    ap.add_argument("--pool-to", help="no baseline interval at or after this UTC time")
    ap.add_argument("--after", type=int, default=3, help="minutes shown after the run (default 3)")
    ap.add_argument("--driver-log", default="~/anomaly/logs/driver.log")
    args = ap.parse_args()
    if args.runs and not re.fullmatch(r"\d+(,\d+)*", args.runs):
        ap.error("--runs takes ids like 3,4,5")
    if args.since and not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", args.since):
        ap.error("--since takes a UTC time like 2026-10-01T13:00:00Z")
    for opt in (args.pool_from, args.pool_to):
        if opt and not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", opt):
            ap.error("--pool-from/--pool-to take a UTC time like 2026-10-01T13:11:00Z")
    if not 1 <= args.baseline <= 180 or not 0 <= args.after <= 60:
        ap.error("--baseline 1-180, --after 0-60")
    try:
        picked = runs(args)
        windows = all_windows()
        for run in picked:
            report(run, windows, args)
    except QueryError as exc:
        LOG.error("%s", exc)
        return 1
    if not picked:
        LOG.warning("no CHAOS_RUN row matched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
