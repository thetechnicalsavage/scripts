#!/usr/bin/env python3
# v1.1 - app 900 (builder M, phase 4): the Isolation Forest reference detector (PLAN.md D5), outside the database.
#        Reads the same training rows as the in-database detectors (PKG_ANOM_TRAIN.window_signals and
#        training_query: FEATURE_MINUTE minus the chaos windows, minus rows with a null in a used signal, constant
#        signals dropped), fits scikit-learn's IsolationForest(random_state=20261004), scores the complete minutes of
#        the scoring range and writes one SCORE_EVAL row per FEATURE_MINUTE minute of that range under the run tag
#        (flag null where a used signal is null), model_name IFOREST_<contamination>, detector IFOREST. Registers
#        the model in MODEL_REGISTRY (CANDIDATE; never ACTIVE: D5 is a reference, not part of the live app).
#        v1.0: first version, 01-Oct-2026. v1.1: public copy: host name and local-time references removed; DEFAULT_SECRETS is a placeholder (pass --secrets FILE).
#
# Run as : the demo user on the demo VM, with the separate venv ~/anomaly/venv-ml (tools/requirements-ml.txt):
#            ~/anomaly/venv-ml/bin/python tools/iforest.py --train-from 2026-10-01T12:35Z --train-to 2026-10-04T12:35Z \
#                --score-from 2026-10-04T12:35Z --score-to 2026-10-05T12:35Z --run-tag DEV_D1 [--contamination 0.01]
#          --dry-run fits and scores but writes nothing. Never uses the load driver's venv.
# Secrets: ANOMOPS_PWD is read from the secrets file (--secrets FILE; the default <secrets-file> is a placeholder) the way the load
#          driver reads it; it is passed to python-oracledb only, never printed or logged (the log formatter masks
#          every value of the file as a last guard).
# Output : JSON log lines on stderr; one JSON summary line on stdout. Exit 0 ok, 1 usage/secrets, 2 database error,
#          3 not enough rows.
# Score  : the anomaly score s(x) = -score_samples(x) of the original paper (higher = more anomalous, in (0, 1]);
#          flag = 1 where predict(x) = -1, i.e. s(x) above the contamination threshold (-offset_).
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import pickle
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

RANDOM_STATE = 20261004
DEFAULT_SECRETS = "<secrets-file>"
DEFAULT_DSN = "localhost:1521/ORCLPDB1"
TAG_RE = re.compile(r"^[A-Z0-9_]{1,40}$")
TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})Z$")
MIN_ROWS = 30
LOG = logging.getLogger("iforest")


class UsageError(Exception):
    pass


class SecretsError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ arguments
def parse_utc(text: str) -> datetime:
    """'YYYY-MM-DDTHH:MMZ' -> naive datetime in UTC (the database's DATE values are UTC)."""
    m = TS_RE.match(text or "")
    if not m:
        raise UsageError(f"not a UTC minute (YYYY-MM-DDTHH:MMZ): {text!r}")
    try:
        return datetime(*(int(g) for g in m.groups()))
    except ValueError as exc:
        raise UsageError(f"not a valid time: {text!r}") from exc


def model_name(contamination: float) -> str:
    """IFOREST_<contamination>, e.g. 0.01 -> IFOREST_0.01 (the contract's naming)."""
    return "IFOREST_" + format(contamination, "g")


def variant(contamination: float) -> str:
    """MODEL_REGISTRY.variant (A-Z, 0-9, _): 0.01 -> C0_01."""
    return "C" + format(contamination, "g").replace(".", "_").replace("-", "M").upper()


@dataclass(frozen=True)
class Args:
    train_from: datetime
    train_to: datetime
    score_from: datetime
    score_to: datetime
    run_tag: str
    contamination: float
    n_estimators: int
    secrets: str
    dsn: str
    dry_run: bool


def parse_args(argv: Sequence[str]) -> Args:
    p = argparse.ArgumentParser(prog="iforest.py", description="App 900 Isolation Forest reference detector (D5)")
    p.add_argument("--train-from", required=True)
    p.add_argument("--train-to", required=True)
    p.add_argument("--score-from", required=True)
    p.add_argument("--score-to", required=True)
    p.add_argument("--run-tag", required=True)
    p.add_argument("--contamination", type=float, default=0.01)
    p.add_argument("--n-estimators", type=int, default=200)
    p.add_argument("--secrets", default=DEFAULT_SECRETS)
    p.add_argument("--dsn", default=DEFAULT_DSN)
    p.add_argument("--dry-run", action="store_true")
    try:
        ns = p.parse_args(list(argv))
    except SystemExit as exc:          # argparse exits on --help or a usage error
        raise UsageError("bad arguments (see --help)") from exc
    a = Args(parse_utc(ns.train_from), parse_utc(ns.train_to), parse_utc(ns.score_from), parse_utc(ns.score_to),
             ns.run_tag, ns.contamination, ns.n_estimators, os.path.expanduser(ns.secrets), ns.dsn, ns.dry_run)
    if a.train_from >= a.train_to:
        raise UsageError("--train-from must be before --train-to")
    if a.score_from >= a.score_to:
        raise UsageError("--score-from must be before --score-to")
    if not TAG_RE.match(a.run_tag):
        raise UsageError("--run-tag must be 1-40 of A-Z, 0-9, _")
    if not (math.isfinite(a.contamination) and 0 < a.contamination <= 0.5):
        raise UsageError("--contamination must be in (0, 0.5]")
    if not 10 <= a.n_estimators <= 2000:
        raise UsageError("--n-estimators must be 10-2000")
    if not re.match(r"^[A-Za-z0-9_.:/-]+$", a.dsn):
        raise UsageError("--dsn not acceptable")
    return a


# ------------------------------------------------------------------------------------------------ secrets
def read_secrets(path: str) -> dict[str, str]:
    """KEY=VALUE lines, parsed exactly as the load driver's load_secrets: blank and '#' lines skipped, an optional
    'export ' prefix, key and value stripped, one pair of matching quotes removed, the last line for a key wins."""
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError as exc:
        raise SecretsError(f"secrets file not found: {path}") from exc
    except OSError as exc:
        raise SecretsError(f"cannot read secrets file {path}: {exc.strerror}") from exc
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


class MaskingFormatter(logging.Formatter):
    """One JSON object per line; every secret value is replaced before the line leaves the process."""

    def __init__(self) -> None:
        super().__init__()
        self.secrets: list[str] = []

    def add(self, values: Iterable[str]) -> None:
        self.secrets.extend(v for v in values if v and len(v) >= 4)
        self.secrets.sort(key=len, reverse=True)

    def format(self, record: logging.LogRecord) -> str:
        doc = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
               "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        doc.update(getattr(record, "fields", {}))
        line = json.dumps(doc, default=str)
        for s in self.secrets:
            line = line.replace(s, "***")
        return line


def _f(**fields: Any) -> dict[str, Any]:
    return {"fields": fields}


# ------------------------------------------------------------------------------------------------ the model
def fit(train_x, contamination: float, n_estimators: int):
    """IsolationForest(random_state=20261004) fitted on train_x."""
    from sklearn.ensemble import IsolationForest   # imported here: the pure helpers need no scikit-learn

    model = IsolationForest(n_estimators=n_estimators, contamination=contamination, max_samples="auto",
                            random_state=RANDOM_STATE)
    model.fit(train_x)
    return model


def score(model, x) -> tuple[list[int], list[float]]:
    """(flags, scores) for the rows of x: flag 1 where predict = -1; score = -score_samples (paper's s(x))."""
    if len(x) == 0:
        return [], []
    return [1 if p == -1 else 0 for p in model.predict(x)], [float(-v) for v in model.score_samples(x)]


def eval_rows(all_ts: Sequence[datetime], scored_ts: Sequence[datetime], flags: Sequence[int],
              scores: Sequence[float]) -> list[tuple[datetime, Optional[int], Optional[float]]]:
    """One row per minute of the range: the scored minutes with their flag and score, the others (a null in a used
    signal) with nulls, in time order."""
    by_ts = {t: (f, s) for t, f, s in zip(scored_ts, flags, scores)}
    if len(by_ts) != len(scored_ts):
        raise ValueError("duplicate minute among the scored rows")
    missing = set(by_ts) - set(all_ts)
    if missing:
        raise ValueError(f"{len(missing)} scored minute(s) are not in the range")
    return [(t, *by_ts.get(t, (None, None))) for t in sorted(all_ts)]


# ------------------------------------------------------------------------------------------------ database
def fetch_matrix(cur, sql: str, n_signals: int):
    cur.execute(f"select * from ({sql}) order by ts")      # the package's SQL; ordered so the fit is repeatable
    rows = cur.fetchall()
    ts = [r[0] for r in rows]
    x = [[float(v) for v in r[1:]] for r in rows]
    if any(len(r) != n_signals for r in x):
        raise ValueError("the query returned a different number of signals")
    return ts, x


def run(a: Args, fmt: MaskingFormatter) -> dict[str, Any]:
    import oracledb   # thin mode; imported here so --help and the tests need no driver
    import sklearn.ensemble  # noqa: F401 - loaded before the timed fit, so train_seconds is the fit alone

    secrets = read_secrets(a.secrets)
    fmt.add(secrets.values())
    pwd = secrets.get("ANOMOPS_PWD")
    if not pwd:
        raise SecretsError("ANOMOPS_PWD missing from the secrets file")
    name = model_name(a.contamination)
    with oracledb.connect(user="ANOMOPS", password=pwd, dsn=a.dsn) as conn:
        pwd = None
        cur = conn.cursor()
        used_v = cur.var(str, 4000)
        dropped_v = cur.var(str, 32767)
        cur.callproc("PKG_ANOM_TRAIN.window_signals", [a.train_from, a.train_to, used_v, dropped_v])
        used = (used_v.getvalue() or "").split(",") if used_v.getvalue() else []
        dropped = json.loads(dropped_v.getvalue() or "[]")
        if not used:
            raise ValueError("no usable signal in the training window")
        train_sql = cur.callfunc("PKG_ANOM_TRAIN.training_query", str, [a.train_from, a.train_to, ",".join(used)])
        _, train_x = fetch_matrix(cur, train_sql, len(used))
        LOG.info("training rows read", extra=_f(rows=len(train_x), signals=len(used), dropped=len(dropped)))
        if len(train_x) < MIN_ROWS:
            raise RuntimeError(f"only {len(train_x)} training rows ({MIN_ROWS} needed)")
        score_sql = cur.callfunc("PKG_ANOM_TRAIN.rows_query", str, [a.score_from, a.score_to, ",".join(used)])
        scored_ts, score_x = fetch_matrix(cur, score_sql, len(used))
        cur.execute("select ts from FEATURE_MINUTE where ts >= :a and ts < :b order by ts", a=a.score_from,
                    b=a.score_to)
        all_ts = [r[0] for r in cur.fetchall()]

        t0 = time.monotonic()
        model = fit(train_x, a.contamination, a.n_estimators)
        train_secs = time.monotonic() - t0
        t1 = time.monotonic()
        flags, scores = score(model, score_x)
        score_secs = time.monotonic() - t1
        rows = eval_rows(all_ts, scored_ts, flags, scores)
        size_bytes = len(pickle.dumps(model))
        summary = {"model": name, "run_tag": a.run_tag, "train_rows": len(train_x), "signals": len(used),
                   "dropped": [d.get("signal") for d in dropped], "score_minutes": len(rows),
                   "scored": len(scored_ts), "flagged": sum(flags), "train_seconds": round(train_secs, 3),
                   "score_ms_per_min": round(score_secs * 1000 / max(1, len(scored_ts)), 3),
                   "size_bytes": size_bytes, "dry_run": a.dry_run}
        if a.dry_run:
            return summary

        import sklearn
        settings = {"contamination": a.contamination, "n_estimators": a.n_estimators, "max_samples": "auto",
                    "random_state": RANDOM_STATE, "threshold": float(-model.offset_),
                    "sklearn": sklearn.__version__}
        signals = {"used": used, "dropped": dropped, "rows_trained": len(train_x)}
        cur.execute("delete from SCORE_EVAL_DETAIL where run_tag = :t and model_name = :m and ts >= :a and ts < :b",
                    t=a.run_tag, m=name, a=a.score_from, b=a.score_to)
        cur.execute("delete from SCORE_EVAL where run_tag = :t and model_name = :m and ts >= :a and ts < :b",
                    t=a.run_tag, m=name, a=a.score_from, b=a.score_to)
        cur.executemany("insert into SCORE_EVAL (run_tag, ts, model_name, detector, flag, score, scored_ts) "
                        "values (:1, :2, :3, 'IFOREST', :4, :5, sys_extract_utc(systimestamp))",
                        [(a.run_tag, t, name, f, s) for t, f, s in rows])
        cur.execute("""
            merge into MODEL_REGISTRY r
            using (select :name model_name from dual) s on (r.model_name = s.model_name)
             when matched then update set r.train_from = :tf, r.train_to = :tt, r.n_rows = :n,
                  r.signals_json = :sig, r.settings_json = :st, r.train_seconds = :secs, r.size_bytes = :sz,
                  r.score_ms_per_min = :ms, r.note = :note
             when not matched then insert (model_name, detector, variant, train_from, train_to, n_rows, signals_json,
                  settings_json, status, created_ts, status_ts, train_seconds, size_bytes, score_ms_per_min, note)
                  values (:name, 'IFOREST', :var, :tf, :tt, :n, :sig, :st, 'CANDIDATE', sys_extract_utc(systimestamp),
                  sys_extract_utc(systimestamp), :secs, :sz, :ms, :note)""",
                    name=name, var=variant(a.contamination), tf=a.train_from, tt=a.train_to, n=len(train_x),
                    sig=json.dumps(signals), st=json.dumps(settings), secs=round(train_secs, 3), sz=size_bytes,
                    ms=summary["score_ms_per_min"],
                    note=f"tools/iforest.py: {len(train_x)} training rows, scored {a.run_tag}")
        conn.commit()
        LOG.info("scores written", extra=_f(**summary))
        return summary


def main(argv: Sequence[str]) -> int:
    fmt = MaskingFormatter()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    try:
        a = parse_args(argv)
    except UsageError as exc:
        LOG.error("usage", extra=_f(error=str(exc)))
        return 1
    try:
        summary = run(a, fmt)
    except SecretsError as exc:
        LOG.error("secrets", extra=_f(error=str(exc)))
        return 1
    except RuntimeError as exc:
        LOG.error("not enough rows", extra=_f(error=str(exc)))
        return 3
    except ValueError as exc:
        LOG.error("data", extra=_f(error=str(exc)))
        return 2
    except Exception as exc:  # noqa: BLE001 - oracledb.Error and anything unexpected: logged with its type, exit 2
        LOG.error("database or runtime error", extra=_f(error=str(exc), type=type(exc).__name__))
        return 2
    sys.stdout.write(json.dumps(summary, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
