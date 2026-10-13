# v1.0 2026-10-10  Brief 12 PLAN 10.3: the redacting formatter and hooks, with fake values only: logger.exception,
#                  a child logger, an uncaught exception, a thread, and the pattern set. v1.0: first version (day 1).
import logging
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PY = Path(__file__).resolve().parents[1] / "py"
sys.path.insert(0, str(PY))
from lib import redact  # noqa: E402

FAKE_PW = "Qx7#fakePasswordValue_123456"
FAKE_OCID = "ocid1.user.oc1..aaaaaaaafakefakefakefake"
FAKE_FP = "aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99"
FAKE_KEY = "/home/someone/oracle26ai/secrets/oci_key_fixed.pem"


def _script(body: str, tmp_path: Path) -> subprocess.CompletedProcess:
    src = tmp_path / "s.py"
    src.write_text(textwrap.dedent(f"""
        import logging, sys, threading
        sys.path.insert(0, {str(PY)!r})
        from lib import redact
        redact.add_secrets([{FAKE_PW!r}])
        redact.install(__import__('pathlib').Path({str(tmp_path / 'run.log')!r}))
        log = logging.getLogger('poc.test')
    """) + textwrap.dedent(body))
    return subprocess.run([sys.executable, str(src)], capture_output=True, text=True)


def _assert_clean(text: str) -> None:
    for v in (FAKE_PW, FAKE_OCID, FAKE_FP, "oci_key_fixed.pem", "/home/someone"):
        assert v not in text


def test_logger_exception_traceback_is_redacted(tmp_path):
    r = _script(f"""
        try:
            raise RuntimeError("login failed for password={FAKE_PW!s} user {FAKE_OCID} key {FAKE_KEY}")
        except RuntimeError:
            log.exception("boom fp %s", {FAKE_FP!r})
    """, tmp_path)
    out = r.stderr + (tmp_path / "run.log").read_text()
    assert "boom" in out and "RuntimeError" in out
    _assert_clean(out)
    assert "[REDACTED" in out


def test_child_logger_and_third_party_handler_removed(tmp_path):
    r = _script(f"""
        lib_log = logging.getLogger('litellm.child')
        h = logging.StreamHandler(sys.stderr)
        lib_log.addHandler(h)          # a third-party handler added after install()
        redact.harden()
        lib_log.warning("token %s", {FAKE_PW!r})
    """, tmp_path)
    out = r.stderr + (tmp_path / "run.log").read_text()
    assert "token" in out
    _assert_clean(out)


def test_uncaught_exception_is_redacted(tmp_path):
    r = _script(f"""
        raise ValueError("secret {FAKE_PW} in {FAKE_KEY}")
    """, tmp_path)
    assert r.returncode != 0
    out = r.stderr + (tmp_path / "run.log").read_text()
    assert "UNCAUGHT" in out and "ValueError" in out
    _assert_clean(out)


def test_thread_exception_is_redacted(tmp_path):
    r = _script(f"""
        def work():
            raise KeyError("{FAKE_OCID} {FAKE_FP}")
        t = threading.Thread(target=work, name='bg'); t.start(); t.join()
    """, tmp_path)
    out = r.stderr + (tmp_path / "run.log").read_text()
    assert "UNCAUGHT in thread bg" in out
    _assert_clean(out)


@pytest.mark.parametrize("text,gone", [
    ("ip 10.1.2.3 here", "10.1.2.3"),
    ("region us-phoenix-1 used", "us-phoenix-1"),
    ("-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----", "AAAA"),
    ("AGENTMEM_PWD=abc123xyz", "abc123xyz"),
    ("path /home/user/x", "/home/user"),
])
def test_patterns(text, gone):
    assert gone not in redact.redact(text)


def test_version_numbers_are_not_ips():
    assert "23.26.1.0.0" in redact.redact("EE 23.26.1.0.0 gold")


def test_short_values_are_not_registered():
    redact.add_secrets(["ab", ""])
    assert redact.redact("ab cd") == "ab cd"
