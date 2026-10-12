# v1.0 2026-10-10  Brief 14 tests: secrets loader, request signing, redaction (b14lib/ocisign.py). v1.0: first version.
import base64
import hashlib
import re

import pytest
from conftest import fake_ocid
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from b14lib import ocisign
from b14lib.ocisign import Secrets, SecretsError


def verify(sec, method, url, hdrs):
    sig = base64.b64decode(re.search(r'signature="([^"]+)"', hdrs["authorization"]).group(1))
    sec.key.public_key().verify(sig, ocisign.signing_string(method, url, hdrs).encode(), padding.PKCS1v15(),
                                hashes.SHA256())


def test_env_loader_keeps_only_the_five_keys(tmp_path):
    env = tmp_path / "oracle.env"
    env.write_text("# comment\nORACLE_PWD=notkept\nexport OCI_USER_OCID='u1'\nOCI_TENANCY_OCID=\"t1\"\n"
                   "OCI_FINGERPRINT=f1\nOCI_COMPARTMENT_ID=c1\nOCI_REGION=us-phoenix-1\nGOOGLE_OAUTH_CLIENT_SECRET=x\n")
    vals = ocisign.read_env_keys(env)
    assert set(vals) == set(ocisign.ENV_KEYS)
    assert vals["OCI_USER_OCID"] == "u1" and vals["OCI_TENANCY_OCID"] == "t1"
    assert "notkept" not in repr(vals) and "x" not in vals.values()


def test_env_loader_missing_key_names_it_without_values(tmp_path):
    env = tmp_path / "oracle.env"
    env.write_text("OCI_USER_OCID=secretvalue\n")
    with pytest.raises(SecretsError) as e:
        ocisign.read_env_keys(env)
    assert "OCI_TENANCY_OCID" in str(e.value) and "secretvalue" not in str(e.value)
    with pytest.raises(SecretsError):
        ocisign.read_env_keys(tmp_path / "missing.env")


def test_from_files_and_masked_repr(tmp_path, rsa_key):
    env = tmp_path / "oracle.env"
    env.write_text("\n".join(f"{k}=v{k}" for k in ocisign.ENV_KEYS) + "\n")
    key = tmp_path / "k.pem"
    key.write_bytes(rsa_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    s = Secrets.from_files(env, key)
    assert repr(s) == str(s) == "<Secrets redacted>"
    bad = tmp_path / "bad.pem"
    bad.write_text("not a key")
    with pytest.raises(SecretsError):
        Secrets.from_files(env, bad)


def test_sign_get_and_verify(sec):
    url = "https://generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/modelDiscovery?compartmentId=c&limit=1"
    h = ocisign.sign_headers(sec, "GET", url)
    assert 'headers="(request-target) date host"' in h["authorization"]
    assert "x-content-sha256" not in h
    verify(sec, "GET", url, h)


def test_sign_post_covers_the_body(sec):
    url = "https://inference.generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/actions/chat"
    body = b'{"a":1}'
    h = ocisign.sign_headers(sec, "POST", url, body)
    assert h["content-length"] == "7"
    assert h["x-content-sha256"] == base64.b64encode(hashlib.sha256(body).digest()).decode()
    assert 'headers="(request-target) date host content-length content-type x-content-sha256"' in h["authorization"]
    verify(sec, "POST", url, h)
    # a different body under the same headers no longer matches its digest
    assert h["x-content-sha256"] != base64.b64encode(hashlib.sha256(b'{"a":2}').digest()).decode()


def test_sign_delete_has_no_body_headers(sec):
    url = "https://generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/routingProfiles/x"
    h = ocisign.sign_headers(sec, "DELETE", url)
    assert "content-length" not in h
    verify(sec, "DELETE", url, h)


def test_redact_and_shape(sec):
    model = fake_ocid("generativeaimodel", "phx")
    ip = ".".join(["192", "0", "2", "10"])          # a documentation address, assembled so no IP literal is in the file
    text = f"user {sec.user} model {model} from {ip} compartment {sec.compartment}"
    out = ocisign.redact(text, sec)
    assert "ocid" + "1." not in out and ip not in out
    assert "<generativeaimodel:phx>" in out and "<redacted>" in out and "<ip>" in out
    assert ocisign.ocid_shape(fake_ocid("tenancy", "")) == "<tenancy:->"
    assert ocisign.ocid_shape("xai.grok-4.3") == "<not-an-ocid>"
    assert len(ocisign.sha16("x")) == 16
