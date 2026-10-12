# v1.0 2026-10-10  Brief 14: secrets loader, OCI request signing and redaction (PLAN.md v1.0 sections 4, 5 rule 9).
#   v1.0: first version. Signing reused from code/probes/oci_readonly_probe.py v1.0 (the study's read-only probe, which
#         signed GET only) and extended to POST/PUT/DELETE as OCI's HTTP signature scheme requires: for a body-carrying
#         method the signed headers add content-length, content-type and x-content-sha256. The loader keeps only the
#         five OCI keys of the env file and never stores the other values in it.
"""OCI API-key signing for the brief 14 POC.

Nothing here prints, logs or returns a secret. Secrets.__repr__ is masked; redact() scrubs exact secret values,
then OCIDs (kept as type and region key only), then IPv4 addresses.
"""
import base64
import email.utils
import hashlib
import re
import urllib.parse
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ENV_KEYS = ("OCI_USER_OCID", "OCI_TENANCY_OCID", "OCI_FINGERPRINT", "OCI_COMPARTMENT_ID", "OCI_REGION")
BODY_METHODS = ("post", "put", "patch")

# an OCID: the prefix "ocid1", then <type>.<realm>.<region key, may be empty>.<unique part>, dot-separated
OCID_RE = re.compile(r"ocid1\.([a-z0-9_-]+)\.([a-z0-9_-]*)\.([a-z0-9_-]*)\.[A-Za-z0-9_.-]+")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


class SecretsError(RuntimeError):
    pass


def read_env_keys(env_file: Path, keys=ENV_KEYS) -> dict:
    """KEY=VALUE lines -> only the requested keys. Other lines are parsed and dropped at once."""
    vals = {}
    try:
        lines = Path(env_file).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SecretsError(f"cannot read the env file ({type(exc).__name__})") from None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[len("export "):].strip()
        if k in keys:
            vals[k] = v.strip().strip('"').strip("'")
    missing = [k for k in keys if not vals.get(k)]
    if missing:
        raise SecretsError(f"env file lacks: {', '.join(missing)}")
    return vals


class Secrets:
    """Signing material. Never printed: __repr__ is masked and no attribute is logged by this package."""

    def __init__(self, user: str, tenancy: str, fingerprint: str, compartment: str, region: str, private_key):
        self.user = user
        self.tenancy = tenancy
        self.fingerprint = fingerprint
        self.compartment = compartment
        self.region = region
        self.key = private_key
        self.scrub = [v for v in (user, tenancy, fingerprint, compartment) if v]

    @classmethod
    def from_files(cls, env_file: Path, key_file: Path) -> "Secrets":
        v = read_env_keys(env_file)
        try:
            key = serialization.load_pem_private_key(Path(key_file).read_bytes(), password=None)
        except (OSError, ValueError, TypeError) as exc:
            raise SecretsError(f"cannot load the private key ({type(exc).__name__})") from None
        return cls(v["OCI_USER_OCID"], v["OCI_TENANCY_OCID"], v["OCI_FINGERPRINT"], v["OCI_COMPARTMENT_ID"],
                   v["OCI_REGION"], key)

    def __repr__(self) -> str:
        return "<Secrets redacted>"

    __str__ = __repr__


def ocid_shape(ocid: str) -> str:
    """An OCID of type generativeaimodel with region key phx -> '<generativeaimodel:phx>' ('-' if no region key)."""
    m = OCID_RE.fullmatch(ocid or "")
    if not m:
        return "<not-an-ocid>"
    return f"<{m.group(1)}:{m.group(3) or '-'}>"


def sha16(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def redact(text: str, sec: "Secrets | None" = None) -> str:
    if sec is not None:
        for v in sec.scrub:
            text = text.replace(v, "<redacted>")
    text = OCID_RE.sub(lambda m: f"<{m.group(1)}:{m.group(3) or '-'}>", text)
    return IP_RE.sub("<ip>", text)


def sign_headers(sec: Secrets, method: str, url: str, body: bytes | None = None,
                 content_type: str = "application/json", date: str | None = None) -> dict:
    """Headers for one request, OCI HTTP signature (draft-cavage-http-signatures, rsa-sha256).

    GET/DELETE/HEAD sign (request-target), date and host. POST/PUT/PATCH also sign content-length, content-type and
    x-content-sha256 (the base64 SHA-256 of the exact body bytes sent; an empty body is signed as b"").
    """
    m = method.lower()
    p = urllib.parse.urlsplit(url)
    target = p.path + (("?" + p.query) if p.query else "")
    date = date or email.utils.formatdate(usegmt=True)
    hdrs = {"date": date, "host": p.netloc}
    names = ["(request-target)", "date", "host"]
    if m in BODY_METHODS:
        body = body if body is not None else b""
        hdrs["content-length"] = str(len(body))
        hdrs["content-type"] = content_type
        hdrs["x-content-sha256"] = base64.b64encode(hashlib.sha256(body).digest()).decode()
        names += ["content-length", "content-type", "x-content-sha256"]
    lines = []
    for n in names:
        lines.append(f"(request-target): {m} {target}" if n == "(request-target)" else f"{n}: {hdrs[n]}")
    signing = "\n".join(lines)
    sig = sec.key.sign(signing.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    key_id = f"{sec.tenancy}/{sec.user}/{sec.fingerprint}"
    hdrs["authorization"] = (f'Signature version="1",keyId="{key_id}",algorithm="rsa-sha256",'
                             f'headers="{" ".join(names)}",signature="{base64.b64encode(sig).decode()}"')
    hdrs["accept"] = "application/json"
    return hdrs


def signing_string(method: str, url: str, headers: dict) -> str:
    """Rebuild the signing string from signed headers (used by the tests to verify a signature)."""
    p = urllib.parse.urlsplit(url)
    target = p.path + (("?" + p.query) if p.query else "")
    names = re.search(r'headers="([^"]+)"', headers["authorization"]).group(1).split()
    return "\n".join(f"(request-target): {method.lower()} {target}" if n == "(request-target)"
                     else f"{n}: {headers[n]}" for n in names)
