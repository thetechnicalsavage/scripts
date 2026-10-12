# v1.0 2026-10-10  Brief 14: signed HTTP through the Phoenix lock (PLAN.md v1.0 sections 5, 6). v1.0: first version.
"""One place where the POC talks to OCI.

Every request passes phoenix.check_url (and phoenix.check_body when it has a JSON body) BEFORE it is signed.
Retries: only for 429 and 5xx, only when the caller says the request is safe to repeat (GET, DELETE, or a create
carrying a retry token), at most `max_retries` times with exponential back-off (2 s, 4 s, 8 s, capped at 30 s).
Inference calls pass max_retries=0 and are retried by chat.py, which counts every attempt in the budget.
"""
import json
import logging
import time
from dataclasses import dataclass, field

import requests

from . import phoenix
from .ocisign import Secrets, redact, sign_headers

LOG = logging.getLogger("b14.http")
TIMEOUT_S = 90
BACKOFF_CAP_S = 30


@dataclass
class Result:
    method: str
    status: int | None            # None = transport error (no HTTP answer)
    body: object = None           # parsed JSON (NOT redacted: callers redact before writing anything)
    text: str = ""                # redacted text of the body, first 4000 characters
    header_names: list = field(default_factory=list)   # names only; identifier headers ('*-id') dropped
    headers: dict = field(default_factory=dict)        # selected values: etag, content-type, opc-next-page
    latency_ms: int = 0
    attempts: int = 1
    error: str | None = None      # transport error class name

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    def error_code(self):
        return self.body.get("code") if isinstance(self.body, dict) else None

    def error_message(self, sec=None):
        msg = self.body.get("message") if isinstance(self.body, dict) else None
        return redact(str(msg), sec) if msg is not None else None


KEEP_HEADER_VALUES = ("etag", "content-type", "opc-next-page", "retry-after")


def _header_names(headers) -> list:
    """Response header names for the record. A name ending in '-id' carries an identifier: name and value dropped."""
    return sorted({k.lower() for k in headers.keys() if not k.lower().endswith("-id")})


class OciClient:
    def __init__(self, sec: Secrets, *, session=None, sleep=time.sleep, timeout: int = TIMEOUT_S):
        phoenix.check_env_region(sec.region)
        self.sec = sec
        self.session = session or requests.Session()
        self.sleep = sleep
        self.timeout = timeout

    def request(self, method: str, url: str, *, json_body=None, extra_headers: dict | None = None,
                repeatable: bool = False, max_retries: int = 3) -> Result:
        phoenix.check_url(url)
        body = None
        if json_body is not None:
            phoenix.check_body(json_body)
            body = json.dumps(json_body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        retries = max_retries if repeatable else 0
        attempt = 0
        while True:
            attempt += 1
            headers = sign_headers(self.sec, method, url, body)
            for k, v in (extra_headers or {}).items():
                headers[k] = v          # unsigned extras (retry token, if-match, project header)
            t0 = time.monotonic()
            try:
                r = self.session.request(method.upper(), url, headers=headers, data=body, timeout=self.timeout)
            except requests.RequestException as exc:
                ms = round((time.monotonic() - t0) * 1000)
                LOG.warning("%s transport error %s after %d ms", method.upper(), type(exc).__name__, ms)
                if attempt <= retries:
                    self._backoff(attempt)
                    continue
                return Result(method.upper(), None, latency_ms=ms, attempts=attempt, error=type(exc).__name__)
            ms = round((time.monotonic() - t0) * 1000)
            if (r.status_code == 429 or r.status_code >= 500) and attempt <= retries:
                LOG.warning("%s HTTP %s, retry %d", method.upper(), r.status_code, attempt)
                self._backoff(attempt)
                continue
            try:
                parsed = r.json() if r.content else None
            except ValueError:
                parsed = None
            res = Result(method.upper(), r.status_code, body=parsed,
                         text=redact(r.text or "", self.sec)[:4000],
                         header_names=_header_names(r.headers),
                         headers={k: r.headers.get(k) for k in KEEP_HEADER_VALUES if r.headers.get(k) is not None},
                         latency_ms=ms, attempts=attempt)
            LOG.info("%s %s -> %s in %d ms (attempt %d)", method.upper(), redact(url, self.sec)[:120],
                     r.status_code, ms, attempt)
            return res

    def _backoff(self, attempt: int) -> None:
        self.sleep(min(2 ** attempt, BACKOFF_CAP_S))
