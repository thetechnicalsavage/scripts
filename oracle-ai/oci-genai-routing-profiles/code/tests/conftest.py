# v1.0 2026-10-10  Brief 14 tests: shared fixtures (fake OCI server, signing key, fake identifiers).
#   v1.0: first version. No network, no database, no OCI: every HTTP call goes to FakeSession.
#   Fake OCIDs are assembled from parts so that no OCID-shaped literal sits in a file (estate convention, app 800).
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "poc"))

from b14lib import phoenix  # noqa: E402
from b14lib.ocisign import Secrets  # noqa: E402

PREFIX = "ocid" + "1."


def fake_ocid(rtype: str, region_key: str = "phx", tail: str = "aaaaexample") -> str:
    return f"{PREFIX}{rtype}.oc1.{region_key}.{tail}"


@pytest.fixture(scope="session")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def sec(rsa_key):
    return Secrets(user=fake_ocid("user", "", "useruniq"), tenancy=fake_ocid("tenancy", "", "tenuniq"),
                   fingerprint=":".join(["0f"] * 4) + "-test-fp", compartment=fake_ocid("compartment", "", "compuniq"),
                   region=phoenix.REGION, private_key=rsa_key)


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = "" if body is None else json.dumps(body)
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeSession:
    """Routes (METHOD, path-prefix) to a handler(method, url, body_dict) -> FakeResponse, and records every call."""

    def __init__(self):
        self.calls = []
        self.routes = []

    def on(self, method, path_part, handler):
        self.routes.append((method.upper(), path_part, handler))

    def request(self, method, url, headers=None, data=None, timeout=None):
        body = json.loads(data) if data else None
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        for m, part, h in reversed(self.routes):
            if m == method and part in url:
                r = h(method, url, body)
                if isinstance(r, Exception):
                    raise r
                return r
        return FakeResponse(404, {"code": "NotFound", "message": "no fake route"})


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def client(sec, session):
    from b14lib.ociclient import OciClient
    return OciClient(sec, session=session, sleep=lambda s: None)


@pytest.fixture
def secrets_dir(tmp_path):
    d = tmp_path / "secrets"
    d.mkdir(mode=0o700)
    return d
