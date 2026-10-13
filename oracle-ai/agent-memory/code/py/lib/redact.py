# v1.0 2026-10-10  Brief 12 PLAN 10.3: the run's only logging handlers, with a redacting Formatter, plus redacting
#                  sys/threading exception hooks. Imported before anything else by every step script.
#                  v1.0: first version (day 1).
"""Redaction at the formatter, the exception hooks and the warnings capture.

Why a Formatter and not a Filter: a logging.Filter runs before the traceback is formatted, so
logger.exception(...) inside litellm or the SDK would pass a filter unredacted. RedactingFormatter.format() builds
the complete line first (message, exc_info traceback text, stack_info) and then replaces:
  1. the exact secret values registered with add_secrets() (longest first);
  2. patterns: OCIDs, key fingerprints, PEM blocks, private-key paths (*.pem, anything under a 'secrets' directory),
     IPv4 addresses, password assignments, OCI region names (replaced by <region>, which the leak scan allows) and
     home-directory paths.
install() removes every handler of the root logger and of every logger that exists at that moment, and attaches
the two handlers of the run (console and a staging file) to the root logger only; harden() repeats the removal
after third-party imports (litellm adds its own handlers when imported) and makes every logger propagate to root.
sys.excepthook and threading.excepthook print a redacted standard traceback (no local variables).
"""
from __future__ import annotations

import logging
import re
import sys
import threading
import traceback
from pathlib import Path

_SECRETS: list[str] = []
REPLACEMENT = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN[^-]*-----.*?(?:-----END[^-]*-----|\Z)", re.S), "[REDACTED-PEM]"),
    (re.compile(r"ocid1\.[A-Za-z0-9._-]+", re.I), "[REDACTED-OCID]"),
    (re.compile(r"(?:[0-9a-f]{2}:){15}[0-9a-f]{2}", re.I), "[REDACTED-FINGERPRINT]"),
    (re.compile(r"(?:/[^\s'\"]*)?/secrets/[^\s'\",)]*"), "[REDACTED-PATH]"),
    (re.compile(r"[^\s'\"]*\.pem\b"), "[REDACTED-PATH]"),
    (re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), "[REDACTED-IP]"),
    (re.compile(r"(?i)([a-z_]*(?:password|passwd|pwd|secret|api_key)[a-z_]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"),
     r"\1\2[REDACTED]"),
    (re.compile(r"/home/[A-Za-z0-9_.-]+"), "~"),
    (re.compile(r"\b[a-z]{2}-[a-z]+-\d\b"), "<region>"),
)


def add_secrets(values) -> None:
    """Register exact values to redact (empty or very short values are ignored: they would match everything)."""
    for v in values:
        if isinstance(v, str) and len(v) >= 4 and v not in _SECRETS:
            _SECRETS.append(v)
    _SECRETS.sort(key=len, reverse=True)


def redact(text: str) -> str:
    if not text:
        return text
    for v in _SECRETS:
        if v in text:
            text = text.replace(v, REPLACEMENT)
    for rx, rep in _PATTERNS:
        text = rx.sub(rep, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # super().format() already appends exc_text and stack_info; redact the finished string.
        return redact(super().format(record))

    def formatException(self, ei) -> str:   # noqa: N802 (logging API name)
        return redact(super().formatException(ei))


_FMT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_installed: dict[str, logging.Handler] = {}


def _strip_handlers() -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        if h not in _installed.values():
            root.removeHandler(h)
    for name, lg in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(lg, logging.Logger):
            for h in list(lg.handlers):
                lg.removeHandler(h)
            lg.propagate = True


def install(log_file: Path | None, level: int = logging.INFO) -> None:
    """Make the console and (optionally) a staging file the run's only handlers, both redacting."""
    _strip_handlers()
    root = logging.getLogger()
    root.setLevel(level)
    fmt = RedactingFormatter(_FMT)
    if "console" not in _installed:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(fmt)
        root.addHandler(ch)
        _installed["console"] = ch
    if log_file is not None and "file" not in _installed:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
        _installed["file"] = fh
    logging.captureWarnings(True)
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


def harden() -> None:
    """Call after third-party imports: removes any handler they added and routes everything to root."""
    _strip_handlers()


def _emit(text: str) -> None:
    text = redact(text)
    try:
        sys.stderr.write(text if text.endswith("\n") else text + "\n")
    except Exception:            # stderr may be closed at interpreter shutdown; nothing else can be done
        pass
    if "file" in _installed:
        try:
            h = _installed["file"]
            h.acquire()
            try:
                h.stream.write(text if text.endswith("\n") else text + "\n")
                h.flush()
            finally:
                h.release()
        except Exception:
            pass


def _excepthook(exc_type, exc, tb) -> None:
    _emit("UNCAUGHT " + "".join(traceback.format_exception(exc_type, exc, tb)))


def _thread_excepthook(args) -> None:
    name = args.thread.name if args.thread is not None else "?"
    _emit(f"UNCAUGHT in thread {name} " +
          "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
