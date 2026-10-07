#!/usr/bin/env python3
# v1.2 - app 900 load driver: the SHOP application's client on the demo VM.
#        Runs the five contract transactions (contract section 2) against SHOP_APP@oradb1/FREEPDB1 through a
#        python-oracledb thin pool, with a seeded UTC daily load shape (section 3); obeys SHOP.DRIVER_CONTROL
#        (pause, load level, connection leak, logon storm, error share); MERGEs one row of client-side metrics
#        per UTC minute into ANOMOPS.APP_MINUTE as ANOM_FEED, keeping up to 120 minutes in memory while the
#        observer is unreachable. JSON-lines logs with rotation; every secret value is masked in every line.
#        v1.0: first version, 01-Oct-2026.
#        v1.1: unknown_product_id capped at 9 digits (PKG_SHOP's pid:qty token), so a routed order fails with
#              -20001 (unknown product), never -20002 (malformed); repeated DRIVER_CONTROL warnings throttled. v1.2: public copy: host name and local-time references removed (comments only; VERSION stays "1.1").
"""App 900 load driver.

Usage:
    anomaly_driver.py --config ~/anomaly/driver.ini              run until SIGTERM / SIGINT
    anomaly_driver.py --config ~/anomaly/driver.ini --check-config validate config + secrets keys, no DB

Exit codes: 0 clean stop, 1 unexpected fatal error, 2 configuration or secrets error (systemd does not restart).

Percentiles: app_p50_ms and app_p95_ms are exact nearest-rank percentiles over every transaction that completed
in the UTC minute (successful and failed: the latency the application's users saw). For n latencies sorted
ascending, the p-th percentile is the value at 1-based rank ceil(p * n / 100), computed in integer arithmetic.
No interpolation, no sampling, no histogram buckets.
"""
from __future__ import annotations

import argparse
import collections
import configparser
import dataclasses
import functools
import itertools
import json
import logging
import logging.handlers
import math
import os
import random
import re
import signal
import stat
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

import oracledb

VERSION = "1.1"
PROGRAM = "anomaly-driver"          # v$session.program / module of every target session the driver opens
LOGGER_NAME = "anomaly_driver"

EXIT_OK, EXIT_FATAL, EXIT_CONFIG = 0, 1, 2

HARD_MAX_WORKERS = 16               # PLAN section 4: driver concurrency is capped
HARD_MAX_POOL = 16
HARD_MAX_LEAK = 40                  # the conn_leak scenario's ceiling
LOAD_PCT_MAX = 300.0
BOUNDARY_GRACE_S = 0.25             # the minute thread wakes this long after each UTC minute boundary
MAX_GAP_ROWS = 1440                 # never synthesise more than a day of empty minutes after a clock jump
WORKER_JOIN_S = 10.0                # shutdown: how long workers together get to finish their transaction
ERR_EXPECTED = "ORA-20001"          # PKG_SHOP's "unknown product / insufficient stock": counted, not warned

# Contract section 2: the mix (percent) and the exact SQL. Pre-registered; not configurable on purpose.
MIX: tuple[tuple[str, int], ...] = (
    ("browse", 45), ("search", 10), ("place_order", 20), ("pay", 15), ("order_status", 10))
_MIX_NAMES = tuple(name for name, _ in MIX)
_MIX_EDGES = tuple(itertools.accumulate(weight for _, weight in MIX))
_MIX_TOTAL = _MIX_EDGES[-1]

SQL_BROWSE = ("select p.product_id, p.name, p.price, i.qty_on_hand from SHOP.PRODUCTS p join SHOP.INVENTORY i "
              "on i.product_id = p.product_id where p.category_id = :cat and p.active = 'Y' "
              "order by p.popularity desc fetch first 20 rows only")
SQL_SEARCH = ("select product_id, name, price from SHOP.PRODUCTS where category_id = :cat "
              "and upper(name) like :pat fetch first 20 rows only")
SQL_PLACE_ORDER = "begin SHOP.PKG_SHOP.place_order(:cust, :items, :oid); end;"
SQL_PAY = "begin SHOP.PKG_SHOP.pay(:oid, :amt, :method); end;"
SQL_ORDER_STATUS = ("select o.order_id, o.status, o.total, count(l.line_no) from SHOP.ORDERS o join SHOP.ORDER_LINES l "
                    "on l.order_id = o.order_id where o.customer_id = :cust group by o.order_id, o.status, o.total, "
                    "o.order_ts order by o.order_ts desc fetch first 5 rows only")
SQL_CONTROL = ("select enabled, load_pct, conn_leak_target, logon_storm, error_pct "
               "from SHOP.DRIVER_CONTROL where id = 1")
SQL_QUERY = {"browse": SQL_BROWSE, "search": SQL_SEARCH, "order_status": SQL_ORDER_STATUS}

# One row per UTC minute; re-sending a minute is harmless (MERGE on the key).
SQL_MERGE_APP_MINUTE = (
    "merge into ANOMOPS.APP_MINUTE t "
    "using (select :ts_minute as ts_minute, :n_ok as n_ok, :n_err as n_err, :app_tps as app_tps, "
    ":app_p50_ms as app_p50_ms, :app_p95_ms as app_p95_ms, :app_err_pct as app_err_pct from dual) s "
    "on (t.ts_minute = s.ts_minute) "
    "when matched then update set t.n_ok = s.n_ok, t.n_err = s.n_err, t.app_tps = s.app_tps, "
    "t.app_p50_ms = s.app_p50_ms, t.app_p95_ms = s.app_p95_ms, t.app_err_pct = s.app_err_pct, "
    "t.loaded_ts = sys_extract_utc(systimestamp) "
    "when not matched then insert (ts_minute, n_ok, n_err, app_tps, app_p50_ms, app_p95_ms, app_err_pct, loaded_ts) "
    "values (s.ts_minute, s.n_ok, s.n_err, s.app_tps, s.app_p50_ms, s.app_p95_ms, s.app_err_pct, "
    "sys_extract_utc(systimestamp))")
MERGE_BIND_NAMES = ("ts_minute", "n_ok", "n_err", "app_tps", "app_p50_ms", "app_p95_ms", "app_err_pct")


# ----------------------------------------------------------------------------------------------------- errors
class ConfigError(Exception):
    """driver.ini is missing, unreadable or holds an invalid value."""


class SecretsError(Exception):
    """The secrets file is missing, unreadable or lacks a required key. Never carries a secret value."""


# ----------------------------------------------------------------------------------------------------- config
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
_HOST = re.compile(r"^[A-Za-z0-9.\-]{1,253}$")
_TOKEN = re.compile(r"^[A-Z0-9 ]{1,30}$")
_METHOD = re.compile(r"^[A-Z_]{1,20}$")


@dataclasses.dataclass(frozen=True)
class TargetConfig:
    host: str
    port: int
    service: str
    user: str
    password_key: str
    pool_min: int
    pool_max: int
    pool_wait_timeout_ms: int
    call_timeout_ms: int
    tcp_connect_timeout_s: float

    @property
    def dsn(self) -> str:
        return f"{self.host}:{self.port}/{self.service}"


@dataclasses.dataclass(frozen=True)
class ObserverConfig:
    host: str
    port: int
    service: str
    user: str
    password_key: str
    call_timeout_ms: int
    tcp_connect_timeout_s: float
    buffer_minutes: int

    @property
    def dsn(self) -> str:
        return f"{self.host}:{self.port}/{self.service}"


@dataclasses.dataclass(frozen=True)
class LoadConfig:
    workers: int
    seed: int
    peak_tps: float
    jitter: float
    control_interval_s: float


@dataclasses.dataclass(frozen=True)
class DataConfig:
    n_categories: int
    n_products: int
    n_customers: int
    hot_products: int
    hot_order_pct: float
    max_items: int
    max_qty: int
    unknown_product_id: int
    search_terms: tuple[str, ...]
    pay_methods: tuple[str, ...]
    recent_orders: int


@dataclasses.dataclass(frozen=True)
class LogConfig:
    file: str
    level: str
    max_bytes: int
    backups: int


@dataclasses.dataclass(frozen=True)
class Config:
    target: TargetConfig
    observer: ObserverConfig
    load: LoadConfig
    data: DataConfig
    log: LogConfig
    secrets_file: str
    source: str

    def summary(self) -> dict[str, Any]:
        """Everything worth recording at start; holds no secret (only the key names)."""
        return {"config": self.source, "target": f"{self.target.user}@{self.target.dsn}",
                "observer": f"{self.observer.user}@{self.observer.dsn}",
                "pool": [self.target.pool_min, self.target.pool_max], "workers": self.load.workers,
                "seed": self.load.seed, "peak_tps": self.load.peak_tps, "jitter": self.load.jitter,
                "buffer_minutes": self.observer.buffer_minutes, "secrets_file": self.secrets_file,
                "mix": dict(MIX)}


def _get(cp: configparser.ConfigParser, section: str, key: str) -> str:
    try:
        value = cp.get(section, key).strip()
    except (configparser.NoSectionError, configparser.NoOptionError) as exc:
        raise ConfigError(f"[{section}] {key} is missing") from exc
    if not value:
        raise ConfigError(f"[{section}] {key} is empty")
    return value


def _int(cp, section, key, lo, hi) -> int:
    raw = _get(cp, section, key)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"[{section}] {key} must be an integer") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"[{section}] {key}={value} outside [{lo}, {hi}]")
    return value


def _float(cp, section, key, lo, hi, lo_open=False) -> float:
    raw = _get(cp, section, key)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"[{section}] {key} must be a number") from exc
    if math.isnan(value) or value > hi or value < lo or (lo_open and value == lo):
        raise ConfigError(f"[{section}] {key}={raw} outside {'(' if lo_open else '['}{lo}, {hi}]")
    return value


def _match(cp, section, key, pattern: re.Pattern) -> str:
    value = _get(cp, section, key)
    if not pattern.match(value):
        raise ConfigError(f"[{section}] {key} has characters that are not allowed")
    return value


def _list(cp, section, key, pattern: re.Pattern) -> tuple[str, ...]:
    items = tuple(part.strip() for part in _get(cp, section, key).split(",") if part.strip())
    if not items or any(not pattern.match(item) for item in items):
        raise ConfigError(f"[{section}] {key} must be a comma list matching {pattern.pattern}")
    return items


def load_config(path: str) -> Config:
    """Read and validate driver.ini. Raises ConfigError with a message that never holds a secret."""
    path = os.path.abspath(os.path.expanduser(path))
    cp = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="utf-8") as handle:
            cp.read_file(handle)
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc.strerror}") from exc
    except configparser.Error as exc:
        raise ConfigError(f"cannot parse config {path}: {type(exc).__name__}") from exc

    target = TargetConfig(
        host=_match(cp, "target", "host", _HOST), port=_int(cp, "target", "port", 1, 65535),
        service=_match(cp, "target", "service", _IDENT), user=_match(cp, "target", "user", _IDENT),
        password_key=_match(cp, "target", "password_key", _IDENT),
        pool_min=_int(cp, "target", "pool_min", 1, HARD_MAX_POOL),
        pool_max=_int(cp, "target", "pool_max", 1, HARD_MAX_POOL),
        pool_wait_timeout_ms=_int(cp, "target", "pool_wait_timeout_ms", 100, 60000),
        call_timeout_ms=_int(cp, "target", "call_timeout_ms", 1000, 600000),
        tcp_connect_timeout_s=_float(cp, "target", "tcp_connect_timeout_s", 0.5, 60))
    observer = ObserverConfig(
        host=_match(cp, "observer", "host", _HOST), port=_int(cp, "observer", "port", 1, 65535),
        service=_match(cp, "observer", "service", _IDENT), user=_match(cp, "observer", "user", _IDENT),
        password_key=_match(cp, "observer", "password_key", _IDENT),
        call_timeout_ms=_int(cp, "observer", "call_timeout_ms", 1000, 600000),
        tcp_connect_timeout_s=_float(cp, "observer", "tcp_connect_timeout_s", 0.5, 60),
        buffer_minutes=_int(cp, "observer", "buffer_minutes", 1, 1440))
    load = LoadConfig(
        workers=_int(cp, "load", "workers", 1, HARD_MAX_WORKERS), seed=_int(cp, "load", "seed", 0, 2**63 - 1),
        peak_tps=_float(cp, "load", "peak_tps", 0, 200, lo_open=True), jitter=_float(cp, "load", "jitter", 0, 0.2),
        control_interval_s=_float(cp, "load", "control_interval_s", 1, 300))
    data = DataConfig(
        n_categories=_int(cp, "data", "n_categories", 1, 10**6), n_products=_int(cp, "data", "n_products", 2, 10**8),
        n_customers=_int(cp, "data", "n_customers", 1, 10**9), hot_products=_int(cp, "data", "hot_products", 1, 10**6),
        hot_order_pct=_float(cp, "data", "hot_order_pct", 0, 100), max_items=_int(cp, "data", "max_items", 1, 10),
        max_qty=_int(cp, "data", "max_qty", 1, 10),
        # at most 9 digits: PKG_SHOP refuses a longer pid as malformed (-20002) instead of unknown (-20001)
        unknown_product_id=_int(cp, "data", "unknown_product_id", 1, 999_999_999),
        search_terms=_list(cp, "data", "search_terms", _TOKEN), pay_methods=_list(cp, "data", "pay_methods", _METHOD),
        recent_orders=_int(cp, "data", "recent_orders", 1, 1000))
    log = LogConfig(
        file=os.path.abspath(os.path.expanduser(_get(cp, "logging", "file"))),
        level=_get(cp, "logging", "level").upper(), max_bytes=_int(cp, "logging", "max_bytes", 65536, 1 << 30),
        backups=_int(cp, "logging", "backups", 1, 50))

    # cross-field rules
    if target.pool_min > target.pool_max:
        raise ConfigError("[target] pool_min is larger than pool_max")
    if load.workers + 1 > target.pool_max:
        raise ConfigError("[target] pool_max must be at least [load] workers + 1 (the control reader shares it)")
    if data.hot_products >= data.n_products:
        raise ConfigError("[data] hot_products must be smaller than n_products")
    if data.max_items > data.n_products - data.hot_products:
        raise ConfigError("[data] max_items is larger than the number of non-hot products")
    if data.unknown_product_id <= data.n_products:
        raise ConfigError("[data] unknown_product_id must be above n_products (it must not exist)")
    if log.level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ConfigError("[logging] level must be DEBUG, INFO, WARNING or ERROR")
    secrets_file = os.path.abspath(os.path.expanduser(_get(cp, "secrets", "file")))
    return Config(target=target, observer=observer, load=load, data=data, log=log,
                  secrets_file=secrets_file, source=path)


# ---------------------------------------------------------------------------------------------------- secrets
class SecretMasker:
    """Replaces every known secret value, raw and in its repr/JSON-escaped forms, with ***."""
    MASK = "***"
    MIN_LEN = 4      # shorter values are not masked (they would mangle ordinary text); load_secrets warns

    def __init__(self) -> None:
        self._variants: tuple[str, ...] = ()

    def add(self, values: Iterable[str]) -> None:
        variants = set(self._variants)
        for value in values:
            if not value or len(value) < self.MIN_LEN:
                continue
            inner_repr = repr(value)[1:-1]
            for form in (value, inner_repr, json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1],
                         json.dumps(inner_repr)[1:-1], json.dumps(inner_repr, ensure_ascii=False)[1:-1]):
                variants.add(form)
        # longest first so a secret that contains another is masked whole
        self._variants = tuple(sorted(variants, key=len, reverse=True))

    def mask(self, text: str) -> str:
        for variant in self._variants:
            if variant in text:
                text = text.replace(variant, self.MASK)
        return text

    def __repr__(self) -> str:
        return f"SecretMasker({len(self._variants)} forms)"


class Secrets:
    """The passwords the driver needs. repr/str never show a value."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = dict(values)

    def get(self, key: str) -> str:
        return self._values[key]

    def keys(self) -> list[str]:
        return sorted(self._values)

    def __repr__(self) -> str:
        return f"Secrets(keys={self.keys()})"

    __str__ = __repr__


def load_secrets(path: str, required: Iterable[str], masker: SecretMasker,
                 log: Optional[logging.Logger] = None) -> Secrets:
    """Parse KEY=VALUE lines (optional 'export ', optional matching quotes). Every value in the file is added to
    the masker before anything else happens, so even unused keys can never reach a log."""
    try:
        info = os.stat(path)
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
    masker.add(values.values())

    if log is not None:
        if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            log.warning("secrets file is readable by group or other; expected mode 600",
                        extra=_f(path=path, mode=oct(stat.S_IMODE(info.st_mode))))
        short = sorted(k for k, v in values.items() if v and len(v) < SecretMasker.MIN_LEN)
        if short:
            log.warning("secret values too short to mask reliably", extra=_f(keys=short))
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise SecretsError(f"secrets file {path} lacks key(s): {', '.join(missing)}")
    return Secrets({key: values[key] for key in required})


# ---------------------------------------------------------------------------------------------------- logging
def _f(**fields: Any) -> dict[str, Any]:
    """logging `extra=` helper: structured fields for the JSON line."""
    return {"fields": fields}


def iso_utc(epoch: float) -> str:
    stamp = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z"


def minute_iso(epoch_minute: int) -> str:
    return datetime.fromtimestamp(epoch_minute * 60, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


class JsonFormatter(logging.Formatter):
    """One JSON object per line; the finished line is passed through the secret masker (the last word)."""

    def __init__(self, masker: SecretMasker) -> None:
        super().__init__()
        self._masker = masker

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {"ts": iso_utc(record.created), "level": record.levelname,
                               "logger": record.name, "thread": record.threadName,
                               "msg": self._masker.mask(record.getMessage())}
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                doc.setdefault(key, value)
        if record.exc_info:
            doc["exc"] = self._masker.mask(self.formatException(record.exc_info))
        line = json.dumps(doc, default=str, ensure_ascii=False)
        return self._masker.mask(line)


def setup_logging(cfg: Optional[LogConfig], masker: SecretMasker, to_file: bool = True) -> logging.Logger:
    """JSON lines to the rotating file (all levels from cfg.level) and WARNING+ to stderr (the journal)."""
    log = logging.getLogger(LOGGER_NAME)
    log.handlers.clear()
    log.propagate = False
    log.setLevel(cfg.level if cfg else "INFO")
    formatter = JsonFormatter(masker)
    if cfg is not None and to_file:
        os.makedirs(os.path.dirname(cfg.file), mode=0o700, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            cfg.file, maxBytes=cfg.max_bytes, backupCount=cfg.backups, encoding="utf-8")
        file_handler.setFormatter(formatter)
        log.addHandler(file_handler)
    err_handler = logging.StreamHandler(sys.stderr)
    err_handler.setFormatter(formatter)
    err_handler.setLevel(logging.WARNING if (cfg is not None and to_file) else logging.INFO)
    log.addHandler(err_handler)
    return log


def install_excepthooks(log: logging.Logger) -> None:
    """Unhandled exceptions go through the masking formatter, never straight to stderr."""
    def _main_hook(exc_type, exc, tb):
        log.critical("unhandled exception", exc_info=(exc_type, exc, tb))

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        log.critical("unhandled exception in thread", exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
                     extra=_f(thread_name=getattr(args.thread, "name", None)))

    sys.excepthook = _main_hook
    threading.excepthook = _thread_hook


class Throttle:
    """At most one True per key per interval (keeps repeated warnings out of the log)."""

    def __init__(self, interval_s: float, mono: Callable[[], float] = time.monotonic) -> None:
        self._interval = interval_s
        self._mono = mono
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._mono()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self._interval:
                return False
            self._last[key] = now
            return True


# ------------------------------------------------------------------------------------------------ load shape
def shape_factor(hour: float) -> float:
    """Fraction of the peak rate at a UTC hour of day (fractional hours).
    23:00-04:00 0.25; 04:00-06:00 linear ramp 0.25 -> 1.0; 06:00-09:00 1.0; 09:00-15:00 0.6; 15:00-18:00 1.0;
    18:00-23:00 linear ramp 1.0 -> 0.25."""
    h = hour % 24.0
    if h >= 23.0 or h < 4.0:
        return 0.25
    if h < 6.0:
        return 0.25 + 0.75 * (h - 4.0) / 2.0
    if h < 9.0:
        return 1.0
    if h < 15.0:
        return 0.6
    if h < 18.0:
        return 1.0
    return 1.0 - 0.75 * (h - 18.0) / 5.0


@functools.lru_cache(maxsize=16)
def minute_jitter(seed: int, epoch_minute: int, amplitude: float) -> float:
    """The seeded jitter for one UTC minute, the same for every worker (a str seed is hashed with SHA-512 by
    random.Random, so it is stable across runs and independent of PYTHONHASHSEED)."""
    if amplitude <= 0:
        return 0.0
    return random.Random(f"{seed}:jitter:{epoch_minute}").uniform(-amplitude, amplitude)


def target_tps(now_epoch: float, peak_tps: float, load_pct: float, seed: int, jitter: float) -> float:
    """Total transactions per second wanted now: peak x shape(UTC hour) x (1 + minute jitter) x load_pct/100."""
    stamp = datetime.fromtimestamp(now_epoch, tz=timezone.utc)
    hour = stamp.hour + stamp.minute / 60.0 + stamp.second / 3600.0
    factor = shape_factor(hour) * (1.0 + minute_jitter(seed, int(now_epoch // 60), jitter))
    return max(0.0, peak_tps * factor * max(0.0, load_pct) / 100.0)


def pick_transaction(rng: random.Random) -> str:
    """One draw from the contract mix."""
    r = rng.random() * _MIX_TOTAL
    for name, edge in zip(_MIX_NAMES, _MIX_EDGES):
        if r < edge:
            return name
    return _MIX_NAMES[-1]


def build_items(rng: random.Random, data: DataConfig, force_error: bool) -> str:
    """'pid:qty,pid:qty' with 1..max_items distinct products in ascending id order (a fixed lock order, so two
    orders never deadlock on INVENTORY). hot_order_pct of orders include exactly one hot product (1..hot_products);
    the others come from the rest of the catalogue. force_error replaces the last item with a product that does not
    exist, so PKG_SHOP.place_order does its work and then fails with -20001 and the caller rolls back."""
    n_items = rng.randint(1, data.max_items)
    chosen: set[int] = set()
    if rng.random() * 100.0 < data.hot_order_pct:
        chosen.add(rng.randint(1, data.hot_products))
    while len(chosen) < n_items:
        chosen.add(rng.randint(data.hot_products + 1, data.n_products))
    pids = sorted(chosen)
    if force_error:
        pids[-1] = data.unknown_product_id          # above n_products, so the list stays ascending
    return ",".join(f"{pid}:{rng.randint(1, data.max_qty)}" for pid in pids)


def nearest_rank(sorted_values: list[float], pct: int) -> Optional[float]:
    """Exact nearest-rank percentile of an ascending list: the value at 1-based rank ceil(pct * n / 100),
    i.e. the smallest value with at least pct% of the values at or below it. None for an empty list."""
    if isinstance(pct, bool) or not isinstance(pct, int) or not 0 < pct <= 100:
        raise ValueError("pct must be an integer in 1..100")
    n = len(sorted_values)
    if n == 0:
        return None
    rank = (pct * n + 99) // 100        # integer ceil: no float rounding at exact ranks
    return sorted_values[rank - 1]


def error_code(exc: BaseException) -> str:
    """'ORA-20001', 'DPY-6005', ... from a python-oracledb error; the class name otherwise."""
    err = exc.args[0] if exc.args else None
    code = getattr(err, "full_code", None)
    return str(code) if code else type(exc).__name__


# ---------------------------------------------------------------------------------------------- DRIVER_CONTROL
@dataclasses.dataclass(frozen=True)
class ControlState:
    """SHOP.DRIVER_CONTROL as the driver applies it. The defaults are the table's column defaults."""
    enabled: bool = True
    load_pct: float = 100.0
    conn_leak_target: int = 0
    logon_storm: bool = False
    error_pct: float = 0.0


def _ctl_num(value: Any, default: float, lo: float, hi: float, name: str, issues: list[str]) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        issues.append(f"{name} is not a number; using {default:g}")
        return default
    if math.isnan(number) or math.isinf(number):
        issues.append(f"{name} is not finite; using {default:g}")
        return default
    if number < lo or number > hi:
        issues.append(f"{name}={number:g} outside [{lo:g}, {hi:g}]; clamped")
        number = min(max(number, lo), hi)
    return number


def _ctl_flag(value: Any, default: bool, name: str, issues: list[str]) -> bool:
    if value is None:
        return default
    text = str(value).strip().upper()
    if text in ("Y", "N"):
        return text == "Y"
    issues.append(f"{name} is neither Y nor N; using {'Y' if default else 'N'}")
    return default


def parse_control(row: Optional[tuple]) -> tuple[ControlState, list[str]]:
    """(enabled, load_pct, conn_leak_target, logon_storm, error_pct) -> validated state + what was adjusted.
    NULLs take the defaults; out-of-range numbers are clamped; nothing raises."""
    default = ControlState()
    if row is None:
        return default, ["DRIVER_CONTROL row id=1 not found; using defaults"]
    if len(row) != 5:
        return default, [f"DRIVER_CONTROL row has {len(row)} columns, expected 5; using defaults"]
    enabled, load_pct, leak, storm, err_pct = row
    issues: list[str] = []
    state = ControlState(
        enabled=_ctl_flag(enabled, default.enabled, "enabled", issues),
        load_pct=_ctl_num(load_pct, default.load_pct, 0.0, LOAD_PCT_MAX, "load_pct", issues),
        conn_leak_target=int(round(_ctl_num(leak, default.conn_leak_target, 0, HARD_MAX_LEAK,
                                            "conn_leak_target", issues))),
        logon_storm=_ctl_flag(storm, default.logon_storm, "logon_storm", issues),
        error_pct=_ctl_num(err_pct, default.error_pct, 0.0, 100.0, "error_pct", issues))
    return state, issues


# ---------------------------------------------------------------------------------------- minute aggregation
@dataclasses.dataclass
class _Bucket:
    latencies: list[float] = dataclasses.field(default_factory=list)
    n_ok: int = 0
    n_err: int = 0
    errors: collections.Counter = dataclasses.field(default_factory=collections.Counter)


@dataclasses.dataclass(frozen=True)
class MinuteRow:
    """One finished UTC minute of client-side metrics (one APP_MINUTE row)."""
    epoch_minute: int
    n_ok: int
    n_err: int
    app_p50_ms: Optional[float]
    app_p95_ms: Optional[float]
    errors: dict[str, int]

    @classmethod
    def from_bucket(cls, epoch_minute: int, bucket: Optional[_Bucket]) -> "MinuteRow":
        if bucket is None:
            return cls(epoch_minute, 0, 0, None, None, {})
        lat = sorted(bucket.latencies)
        p50, p95 = nearest_rank(lat, 50), nearest_rank(lat, 95)
        return cls(epoch_minute, bucket.n_ok, bucket.n_err,
                   None if p50 is None else round(p50, 3), None if p95 is None else round(p95, 3),
                   dict(sorted(bucket.errors.items())))

    @property
    def ts_minute(self) -> datetime:
        """The minute's start, UTC, naive (bound as an Oracle DATE)."""
        return datetime.fromtimestamp(self.epoch_minute * 60, tz=timezone.utc).replace(tzinfo=None)

    @property
    def app_tps(self) -> float:
        return round((self.n_ok + self.n_err) / 60.0, 4)

    @property
    def app_err_pct(self) -> float:
        """Failed share of the minute's transactions, percent; 0 when nothing ran."""
        total = self.n_ok + self.n_err
        return 0.0 if total == 0 else round(100.0 * self.n_err / total, 3)

    def binds(self) -> dict[str, Any]:
        return {"ts_minute": self.ts_minute, "n_ok": self.n_ok, "n_err": self.n_err, "app_tps": self.app_tps,
                "app_p50_ms": self.app_p50_ms, "app_p95_ms": self.app_p95_ms, "app_err_pct": self.app_err_pct}

    def log_fields(self) -> dict[str, Any]:
        return {"ts_minute": minute_iso(self.epoch_minute), "n_ok": self.n_ok, "n_err": self.n_err,
                "app_tps": self.app_tps, "app_p50_ms": self.app_p50_ms, "app_p95_ms": self.app_p95_ms,
                "app_err_pct": self.app_err_pct, "errors": self.errors}


class MinuteAggregator:
    """Buckets completed transactions by the UTC minute in which they finished. Thread-safe.
    The minute the driver started in is partial and is never emitted; minutes with no transactions are emitted
    as zero rows (n_ok = n_err = 0, percentiles NULL), so the feed has no gaps while the process runs."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        start = int(clock() // 60)
        self._first_full = start + 1
        self._last_closed = start - 1
        self._buckets: dict[int, _Bucket] = {}
        self.total_ok = 0
        self.total_err = 0

    def record(self, latency_ms: float, ok: bool, err_code: Optional[str] = None) -> None:
        with self._lock:
            minute = int(self._clock() // 60)
            if minute <= self._last_closed:
                # the wall clock stepped back across a closed boundary: count it in the open minute
                minute = self._last_closed + 1
            bucket = self._buckets.get(minute)
            if bucket is None:
                bucket = self._buckets[minute] = _Bucket()
            bucket.latencies.append(float(latency_ms))
            if ok:
                bucket.n_ok += 1
                self.total_ok += 1
            else:
                bucket.n_err += 1
                bucket.errors[err_code or "unknown"] += 1
                self.total_err += 1

    def close_until(self, boundary_minute: int) -> tuple[list[MinuteRow], list[int]]:
        """Close every minute before boundary_minute. Returns (rows for full minutes, partial minutes skipped)."""
        with self._lock:
            if boundary_minute - 1 <= self._last_closed:
                return [], []
            closing = list(range(self._last_closed + 1, boundary_minute))
            taken = {minute: self._buckets.pop(minute, None) for minute in closing}
            self._last_closed = boundary_minute - 1
        rows: list[MinuteRow] = []
        skipped: list[int] = []
        for minute in closing[-MAX_GAP_ROWS:]:
            if minute < self._first_full:
                skipped.append(minute)
                continue
            # sorting happens here, outside the lock, so workers never wait on it
            rows.append(MinuteRow.from_bucket(minute, taken[minute]))
        return rows, skipped

    def open_minutes(self) -> dict[int, int]:
        """Minutes still open and how many transactions each holds (for the shutdown log line)."""
        with self._lock:
            return {m: b.n_ok + b.n_err for m, b in self._buckets.items()}


# -------------------------------------------------------------------------------------------- observer feed
class MinuteFeed:
    """MERGEs finished minutes into ANOMOPS.APP_MINUTE as ANOM_FEED. While the observer is unreachable the rows
    stay in memory, oldest first, up to cap; beyond that the oldest is dropped with a warning. Every attempt sends
    the whole buffer in time order in one transaction, so a later success fills the gap in order.
    Used by one thread only (the minute thread)."""

    def __init__(self, cfg: ObserverConfig, password: str, ora: Any, log: logging.Logger) -> None:
        self._cfg = cfg
        self._password = password
        self._ora = ora
        self._log = log
        self._buffer: collections.deque[MinuteRow] = collections.deque()
        self._conn: Any = None
        self.dropped_total = 0

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def buffered_minutes(self) -> list[int]:
        return [row.epoch_minute for row in self._buffer]

    def publish(self, rows: Iterable[MinuteRow]) -> str:
        """Add rows, then try to flush everything buffered. Returns 'ok', 'buffered' or 'idle'."""
        for row in rows:
            self._add(row)
        if not self._buffer:
            return "idle"
        return "ok" if self._flush() else "buffered"

    def _add(self, row: MinuteRow) -> None:
        while len(self._buffer) >= self._cfg.buffer_minutes:
            dropped = self._buffer.popleft()
            self.dropped_total += 1
            self._log.warning("offline buffer full; oldest minute dropped",
                              extra=_f(dropped_minute=minute_iso(dropped.epoch_minute),
                                       cap=self._cfg.buffer_minutes, dropped_total=self.dropped_total))
        self._buffer.append(row)

    def _connect(self) -> Any:
        if self._conn is not None:
            return self._conn
        conn = self._ora.connect(user=self._cfg.user, password=self._password, dsn=self._cfg.dsn,
                                 tcp_connect_timeout=self._cfg.tcp_connect_timeout_s, program=PROGRAM)
        conn.call_timeout = self._cfg.call_timeout_ms
        self._conn = conn
        return conn

    def _flush(self) -> bool:
        rows = sorted(self._buffer, key=lambda r: r.epoch_minute)
        conn = None
        try:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.setinputsizes(ts_minute=self._ora.DB_TYPE_DATE, n_ok=self._ora.DB_TYPE_NUMBER,
                                  n_err=self._ora.DB_TYPE_NUMBER, app_tps=self._ora.DB_TYPE_NUMBER,
                                  app_p50_ms=self._ora.DB_TYPE_NUMBER, app_p95_ms=self._ora.DB_TYPE_NUMBER,
                                  app_err_pct=self._ora.DB_TYPE_NUMBER)
                cur.executemany(SQL_MERGE_APP_MINUTE, [row.binds() for row in rows])
            conn.commit()
        except self._ora.Error as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except self._ora.Error as rb_exc:
                    self._log.debug("rollback after failed MERGE failed", extra=_f(error=error_code(rb_exc)))
            self._discard()
            self._log.warning("APP_MINUTE not written; minutes kept in memory",
                              extra=_f(error=error_code(exc), detail=str(exc)[:300], buffered=len(self._buffer),
                                       oldest=minute_iso(rows[0].epoch_minute)))
            return False
        if len(rows) > 1:
            self._log.info("buffered minutes flushed", extra=_f(rows=len(rows),
                                                                 first=minute_iso(rows[0].epoch_minute),
                                                                 last=minute_iso(rows[-1].epoch_minute)))
        self._buffer.clear()
        return True

    def _discard(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.close()
        except self._ora.Error as exc:
            self._log.debug("closing the observer connection failed", extra=_f(error=error_code(exc)))

    def close(self) -> None:
        self._discard()


# --------------------------------------------------------------------------------------- target connections
class TargetConnections:
    """The SHOP_APP pool (min 2, max 16) and standalone connections for logon storms and leaks."""

    def __init__(self, cfg: TargetConfig, password: str, ora: Any, log: logging.Logger) -> None:
        self._cfg = cfg
        self._password = password
        self._ora = ora
        self._log = log
        self._pool: Any = None

    def open_pool(self) -> None:
        # thin mode returns at once even when the target is down; acquire() then fails fast (DPY-6005) and the
        # transaction counts as an error for its minute, which is what the application really experiences
        self._pool = self._ora.create_pool(
            user=self._cfg.user, password=self._password, dsn=self._cfg.dsn,
            min=self._cfg.pool_min, max=self._cfg.pool_max, increment=1,
            getmode=self._ora.POOL_GETMODE_TIMEDWAIT, wait_timeout=self._cfg.pool_wait_timeout_ms,
            ping_interval=60, tcp_connect_timeout=self._cfg.tcp_connect_timeout_s, program=PROGRAM)

    def connect_standalone(self) -> Any:
        conn = self._ora.connect(user=self._cfg.user, password=self._password, dsn=self._cfg.dsn,
                                 tcp_connect_timeout=self._cfg.tcp_connect_timeout_s, program=PROGRAM)
        conn.call_timeout = self._cfg.call_timeout_ms
        return conn

    def acquire(self, standalone: bool) -> Any:
        if standalone:
            return self.connect_standalone()
        conn = self._pool.acquire()
        conn.call_timeout = self._cfg.call_timeout_ms
        return conn

    def release(self, conn: Any, standalone: bool, healthy: bool) -> None:
        """Standalone: close. Pooled: back to the pool when healthy, otherwise dropped so the pool replaces it."""
        try:
            if standalone:
                conn.close()
            elif healthy and conn.is_healthy():
                self._pool.release(conn)
            else:
                self._pool.drop(conn)
        except self._ora.Error as exc:
            self._log.debug("releasing a target connection failed", extra=_f(error=error_code(exc)))

    def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is None:
            return
        try:
            pool.close(force=True)
        except self._ora.Error as exc:
            self._log.warning("closing the pool failed", extra=_f(error=error_code(exc)))


class LeakManager:
    """conn_leak: holds conn_leak_target extra standalone sessions open and idle (0 closes them)."""

    def __init__(self, conns: TargetConnections, ora: Any, log: logging.Logger) -> None:
        self._conns = conns
        self._ora = ora
        self._log = log
        self._held: list[Any] = []

    @property
    def held(self) -> int:
        return len(self._held)

    def reconcile(self, target: int) -> None:
        target = max(0, min(HARD_MAX_LEAK, int(target)))
        opened = closed = 0
        failure: Optional[str] = None
        while len(self._held) < target:
            try:
                self._held.append(self._conns.connect_standalone())
                opened += 1
            except self._ora.Error as exc:
                failure = error_code(exc)          # try again on the next control cycle
                break
        while len(self._held) > target:
            conn = self._held.pop()
            closed += 1
            try:
                conn.close()
            except self._ora.Error as exc:
                self._log.debug("closing a leaked session failed", extra=_f(error=error_code(exc)))
        if opened or closed or failure:
            level = logging.WARNING if failure else logging.INFO
            self._log.log(level, "conn_leak reconciled", extra=_f(target=target, held=len(self._held),
                                                                 opened=opened, closed=closed, error=failure))

    def close_all(self) -> None:
        self.reconcile(0)


# ------------------------------------------------------------------------------------------------ controller
class Controller:
    """Reads SHOP.DRIVER_CONTROL every control_interval_s through the pool and applies it. When the read fails the
    last known state stays in force."""

    def __init__(self, conns: TargetConnections, leak: LeakManager, ora: Any, log: logging.Logger) -> None:
        self._conns = conns
        self._leak = leak
        self._ora = ora
        self._log = log
        self._state = ControlState()
        self._throttle = Throttle(300.0)
        self.read_failures = 0

    @property
    def state(self) -> ControlState:
        return self._state                   # a reference swap: atomic for readers

    def _read(self) -> Optional[tuple]:
        conn = self._conns.acquire(standalone=False)
        healthy = False
        try:
            conn.module, conn.action = PROGRAM, "driver_control"
            with conn.cursor() as cur:
                cur.execute(SQL_CONTROL)
                row = cur.fetchone()
            healthy = True
            return row
        finally:
            self._conns.release(conn, standalone=False, healthy=healthy)

    def poll_once(self) -> None:
        try:
            row = self._read()
        except self._ora.Error as exc:
            self.read_failures += 1
            if self._throttle.allow("read"):
                self._log.warning("DRIVER_CONTROL read failed; last state kept",
                                  extra=_f(error=error_code(exc), detail=str(exc)[:300],
                                           failures=self.read_failures))
            return
        state, issues = parse_control(row)
        # the same bad value is read every cycle: warn when it first appears, then at most every 5 minutes
        if issues and self._throttle.allow("issues:" + "|".join(issues)):
            self._log.warning("DRIVER_CONTROL value(s) adjusted", extra=_f(issues=issues))
        if state != self._state:
            self._log.info("control changed", extra=_f(old=dataclasses.asdict(self._state),
                                                       new=dataclasses.asdict(state)))
            self._state = state
        self._leak.reconcile(state.conn_leak_target)


# ------------------------------------------------------------------------------------------------- workers
@dataclasses.dataclass
class Context:
    cfg: Config
    ora: Any
    log: logging.Logger
    stop: threading.Event
    conns: TargetConnections
    agg: MinuteAggregator
    controller: Controller
    clock: Callable[[], float] = time.time
    mono: Callable[[], float] = time.monotonic
    warn_throttle: Throttle = dataclasses.field(default_factory=lambda: Throttle(60.0))


class Worker(threading.Thread):
    """Loop: pick a transaction by the mix, run it, record latency (ms, monotonic clock) and outcome, then wait
    for the next start. Starts follow a seeded Poisson process at this worker's share of the target rate; when a
    transaction outlasts the gap, the next one starts at once (no backlog builds), so a slow database lowers
    throughput as it would for a real application."""

    def __init__(self, idx: int, ctx: Context) -> None:
        super().__init__(name=f"worker-{idx}", daemon=True)
        self.idx = idx
        self.ctx = ctx
        self.rng = random.Random(f"{ctx.cfg.load.seed}:worker:{idx}")
        self.recent: collections.deque[int] = collections.deque(maxlen=ctx.cfg.data.recent_orders)

    def run(self) -> None:
        next_start = self.ctx.mono()
        while not self.ctx.stop.is_set():
            try:
                next_start = self.step(next_start)
            except Exception:                    # a defect must not end the worker silently: log, back off, go on
                self.ctx.log.exception("worker step failed", extra=_f(worker=self.idx))
                self.ctx.stop.wait(1.0)
                next_start = self.ctx.mono()

    def step(self, next_start: float) -> float:
        ctx = self.ctx
        state = ctx.controller.state
        if not state.enabled:
            ctx.stop.wait(1.0)
            return ctx.mono()
        load = ctx.cfg.load
        rate = target_tps(ctx.clock(), load.peak_tps, state.load_pct, load.seed, load.jitter) / load.workers
        if rate <= 0:
            ctx.stop.wait(1.0)
            return ctx.mono()
        delay = next_start - ctx.mono()
        if delay > 0:
            ctx.stop.wait(min(delay, 1.0))     # re-check stop and control at least once a second
            return next_start
        t0 = ctx.mono()
        kind = pick_transaction(self.rng)
        ok, code = self.run_transaction(kind, state)
        ctx.agg.record((ctx.mono() - t0) * 1000.0, ok, code)
        if not ok and code != ERR_EXPECTED and ctx.warn_throttle.allow(code or "unknown"):
            ctx.log.warning("transaction failed", extra=_f(txn=kind, error=code, worker=self.idx))
        return t0 + self.rng.expovariate(rate)

    def params(self, kind: str, state: ControlState) -> tuple[str, dict[str, Any]]:
        """Draw the binds before touching the database, so random draws do not depend on outcomes."""
        data = self.ctx.cfg.data
        if kind == "pay" and not self.recent:
            kind = "place_order"                 # nothing of ours to pay yet (the first seconds after a start)
        if kind in ("browse", "search"):
            binds: dict[str, Any] = {"cat": self.rng.randint(1, data.n_categories)}
            if kind == "search":
                binds["pat"] = f"%{self.rng.choice(data.search_terms)}%"
            return kind, binds
        if kind == "order_status":
            return kind, {"cust": self.rng.randint(1, data.n_customers)}
        if kind == "place_order":
            force_error = self.rng.random() * 100.0 < state.error_pct
            return kind, {"cust": self.rng.randint(1, data.n_customers),
                          "items": build_items(self.rng, data, force_error)}
        if kind == "pay":
            return kind, {"oid": self.recent.popleft(), "amt": round(self.rng.uniform(5.0, 500.0), 2),
                          "method": self.rng.choice(data.pay_methods)}
        raise ValueError(f"unknown transaction {kind!r}")

    def run_transaction(self, kind: str, state: ControlState) -> tuple[bool, Optional[str]]:
        """Run one transaction; commit the writes; on any database error roll back and report the error code."""
        ctx = self.ctx
        kind, binds = self.params(kind, state)
        standalone = state.logon_storm          # logon_storm: connect and disconnect for every transaction
        conn = None
        healthy = False
        try:
            conn = ctx.conns.acquire(standalone)
            conn.module, conn.action = PROGRAM, kind
            with conn.cursor() as cur:
                if kind in SQL_QUERY:
                    cur.prefetchrows = 21        # the whole result (<= 20 rows) in one round trip
                    cur.arraysize = 21
                    cur.execute(SQL_QUERY[kind], binds)
                    cur.fetchall()
                elif kind == "place_order":
                    oid = cur.var(int)
                    cur.execute(SQL_PLACE_ORDER, dict(binds, oid=oid))
                    conn.commit()
                    value = oid.getvalue()
                    if value is not None:
                        self.recent.append(int(value))
                else:
                    cur.execute(SQL_PAY, binds)
                    conn.commit()
            healthy = True
            return True, None
        except ctx.ora.Error as exc:
            code = error_code(exc)
            if conn is not None:
                try:
                    conn.rollback()
                    healthy = True
                except ctx.ora.Error as rb_exc:
                    ctx.log.debug("rollback failed; connection dropped", extra=_f(error=error_code(rb_exc)))
            return False, code
        finally:
            if conn is not None:
                ctx.conns.release(conn, standalone, healthy)


class ControlThread(threading.Thread):
    def __init__(self, ctx: Context) -> None:
        super().__init__(name="control", daemon=True)
        self.ctx = ctx

    def run(self) -> None:
        while not self.ctx.stop.wait(self.ctx.cfg.load.control_interval_s):
            try:
                self.ctx.controller.poll_once()
            except Exception:
                self.ctx.log.exception("control cycle failed")


class MinuteThread(threading.Thread):
    """Wakes just after every UTC minute boundary, closes the finished minute(s), logs one INFO line per minute
    and hands the rows to the feed. On stop it publishes the minutes that are complete; the open one is partial
    and is not sent."""

    def __init__(self, ctx: Context, feed: MinuteFeed, leak: LeakManager) -> None:
        super().__init__(name="minute", daemon=True)
        self.ctx = ctx
        self.feed = feed
        self.leak = leak

    def run(self) -> None:
        while True:
            now = self.ctx.clock()
            wait = (int(now // 60) + 1) * 60 - now + BOUNDARY_GRACE_S
            if self.ctx.stop.wait(wait):
                break
            self.publish_closed()
        self.publish_closed()
        for minute, count in sorted(self.ctx.agg.open_minutes().items()):
            self.ctx.log.info("partial minute not sent", extra=_f(ts_minute=minute_iso(minute), transactions=count))

    def publish_closed(self) -> str:
        try:
            rows, skipped = self.ctx.agg.close_until(int(self.ctx.clock() // 60))
            for minute in skipped:
                self.ctx.log.info("partial minute not sent (driver started inside it)",
                                  extra=_f(ts_minute=minute_iso(minute)))
            status = self.feed.publish(rows)
            state = self.ctx.controller.state
            load = self.ctx.cfg.load
            for row in rows:
                hour = (row.epoch_minute % 1440 + 0.5) / 60.0
                self.ctx.log.info("minute", extra=_f(
                    **row.log_fields(), feed=status, buffered=self.feed.buffered,
                    shape=round(shape_factor(hour), 4), load_pct=state.load_pct, enabled=state.enabled,
                    logon_storm=state.logon_storm, error_pct=state.error_pct, leak_held=self.leak.held,
                    seed=load.seed))
            return status
        except Exception:
            self.ctx.log.exception("minute cycle failed")
            return "error"


# ---------------------------------------------------------------------------------------------------- driver
class Driver:
    def __init__(self, cfg: Config, secrets: Secrets, log: logging.Logger, ora: Any = oracledb,
                 clock: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.ora = ora
        self.log = log
        self.stop = threading.Event()
        self.conns = TargetConnections(cfg.target, secrets.get(cfg.target.password_key), ora, log)
        self.leak = LeakManager(self.conns, ora, log)
        self.controller = Controller(self.conns, self.leak, ora, log)
        self.agg = MinuteAggregator(clock)
        self.feed = MinuteFeed(cfg.observer, secrets.get(cfg.observer.password_key), ora, log)
        self.ctx = Context(cfg=cfg, ora=ora, log=log, stop=self.stop, conns=self.conns, agg=self.agg,
                           controller=self.controller, clock=clock, mono=mono)
        self.workers = [Worker(i + 1, self.ctx) for i in range(cfg.load.workers)]
        self.control_thread = ControlThread(self.ctx)
        self.minute_thread = MinuteThread(self.ctx, self.feed, self.leak)
        self._mono = mono

    def request_stop(self, signum: Optional[int] = None) -> None:
        if not self.stop.is_set():
            self.log.info("stop requested", extra=_f(signal=signal.Signals(signum).name if signum else None))
        self.stop.set()

    def run(self) -> int:
        thin = getattr(self.ora, "is_thin_mode", lambda: None)()
        self.log.info("starting", extra=_f(version=VERSION, oracledb=getattr(self.ora, "__version__", "?"),
                                           thin_mode=thin, pid=os.getpid(), **self.cfg.summary()))
        self.conns.open_pool()
        self.controller.poll_once()             # the first state is known before any load is sent
        self.minute_thread.start()
        self.control_thread.start()
        for worker in self.workers:
            worker.start()
        self.log.info("running", extra=_f(workers=len(self.workers), control=dataclasses.asdict(self.controller.state)))
        while not self.stop.wait(1.0):
            pass
        return self.shutdown()

    @staticmethod
    def _join(thread: threading.Thread, timeout: float) -> None:
        if thread.ident is not None:            # never started (fatal error during start): nothing to join
            thread.join(max(0.0, timeout))

    def shutdown(self) -> int:
        """Bounded: workers get WORKER_JOIN_S together, the minute thread its final MERGE. Worst case stays well
        inside the unit's TimeoutStopSec=60."""
        deadline = self._mono() + WORKER_JOIN_S
        for worker in self.workers:
            self._join(worker, deadline - self._mono())
        busy = [w.name for w in self.workers if w.is_alive()]
        self._join(self.control_thread, max(1.0, deadline - self._mono()))
        self._join(self.minute_thread, self.cfg.observer.call_timeout_ms / 1000.0
                   + self.cfg.observer.tcp_connect_timeout_s + 5.0)
        self.leak.close_all()
        if busy:
            # closing a pool under a thread that is inside a call can block; process exit closes the sockets
            self.log.warning("workers still inside a database call at shutdown; pool left to process exit",
                             extra=_f(workers=busy))
        else:
            self.conns.close()
        self.feed.close()
        self.log.info("stopped", extra=_f(transactions_ok=self.agg.total_ok, transactions_err=self.agg.total_err,
                                          unsent_buffered=self.feed.buffered,
                                          dropped_total=self.feed.dropped_total))
        return EXIT_OK


# ------------------------------------------------------------------------------------------------------ main
def parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="App 900 load driver (SHOP workload + APP_MINUTE feed)")
    parser.add_argument("--config", default="~/anomaly/driver.ini", help="path of driver.ini")
    parser.add_argument("--check-config", action="store_true",
                        help="validate driver.ini and the secrets file keys, connect to nothing, exit")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None, ora: Any = oracledb) -> int:
    args = parse_args(argv)
    masker = SecretMasker()
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        setup_logging(None, masker).error("configuration error", extra=_f(error=str(exc)))
        return EXIT_CONFIG
    try:
        log = setup_logging(cfg.log, masker, to_file=not args.check_config)
    except OSError as exc:
        setup_logging(None, masker).error("cannot open the log file", extra=_f(error=exc.strerror,
                                                                               file=cfg.log.file))
        return EXIT_CONFIG
    try:
        secrets = load_secrets(cfg.secrets_file, (cfg.target.password_key, cfg.observer.password_key), masker, log)
    except SecretsError as exc:
        log.error("secrets error", extra=_f(error=str(exc)))
        return EXIT_CONFIG
    if args.check_config:
        log.info("config ok", extra=_f(keys_found=secrets.keys(), **cfg.summary()))
        return EXIT_OK

    install_excepthooks(log)
    driver = Driver(cfg, secrets, log, ora=ora)
    signal.signal(signal.SIGTERM, lambda signum, _frame: driver.request_stop(signum))
    signal.signal(signal.SIGINT, lambda signum, _frame: driver.request_stop(signum))
    try:
        return driver.run()
    except Exception:
        log.exception("fatal error")
        driver.stop.set()
        try:
            driver.shutdown()
        except Exception:
            log.exception("shutdown after a fatal error failed")
        return EXIT_FATAL


if __name__ == "__main__":
    sys.exit(main())
