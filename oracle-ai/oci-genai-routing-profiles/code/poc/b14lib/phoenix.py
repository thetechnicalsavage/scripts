# v1.1 2026-10-10  Brief 14: the Phoenix lock (PLAN.md v1.1 section 5 rule 1).
#   v1.1: check_url also refuses a region identifier other than Phoenix in the path or the query string (Codex code
#         review r1, finding 4). v1.0: first version.
"""Every OCI call of the POC goes to us-phoenix-1 and every routing profile lists only us-phoenix-1.

Four layers, each raising RegionLockError before anything is signed or sent:
  (a) check_env_region: the environment's OCI_REGION must be the Phoenix region;
  (b) check_url: https, the host is exactly the Phoenix management or inference host, and no path segment or query
      value names another region;
  (c) check_body / check_profile_body: no region identifier other than Phoenix anywhere in a JSON body, and a routing
      profile body carries regionRoutingPolicy.allowedRegions == [Phoenix] exactly;
  (d) check_stored_profile: what the service stored after a create lists only Phoenix (the caller deletes otherwise).
"""
import re
import urllib.parse

REGION = "us-phoenix-1"
REGION_KEY = "phx"
_DOMAIN = "oci.oraclecloud.com"
MGMT_HOST = f"generativeai.{REGION}.{_DOMAIN}"
INFER_HOST = f"inference.generativeai.{REGION}.{_DOMAIN}"
ALLOWED_HOSTS = frozenset({MGMT_HOST, INFER_HOST})
MGMT_BASE = f"https://{MGMT_HOST}/20231130"
INFER_BASE = f"https://{INFER_HOST}/20231130"
OPENAI_BASE = f"https://{INFER_HOST}/openai/v1"

# A whole string that looks like an OCI region identifier: us-phoenix-1, eu-frankfurt-2, us-gov-phoenix-1,
# ap-hyderabad-1. Model ids (xai.grok-4.3, cohere.command-a-03-2025) contain a dot and never match as a whole.
REGION_ID_RE = re.compile(r"^[a-z]{2,}(?:-[a-z]+)+-\d+$")


class RegionLockError(RuntimeError):
    """Raised before any request that would leave the Phoenix region or name another region."""


def check_env_region(region: str) -> None:
    if region != REGION:
        raise RegionLockError(f"OCI_REGION in the environment is not {REGION}; nothing runs")


def check_url(url: str) -> str:
    """Return the URL unchanged when it is an https URL on a Phoenix Generative AI host, else raise."""
    try:
        p = urllib.parse.urlsplit(url)
        port = p.port
    except ValueError as exc:
        raise RegionLockError("unparseable URL") from exc
    if p.scheme != "https":
        raise RegionLockError("only https is allowed")
    if p.username or p.password:
        raise RegionLockError("credentials in a URL are not allowed")
    if port not in (None, 443):
        raise RegionLockError("only the default https port is allowed")
    if (p.hostname or "") not in ALLOWED_HOSTS:
        raise RegionLockError(f"host is not a {REGION} Generative AI host")
    parts = [urllib.parse.unquote(x) for x in p.path.split("/") if x]
    parts += [v for pair in urllib.parse.parse_qsl(p.query, keep_blank_values=True) for v in pair]
    if any(REGION_ID_RE.match(x) and x != REGION for x in parts):
        raise RegionLockError(f"the URL names a region other than {REGION}")
    return url


def find_region_ids(obj) -> list:
    """Every string (key or value, at any depth) in a JSON-like object that is a region identifier."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(k)
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif isinstance(o, str) and REGION_ID_RE.match(o):
            found.append(o)

    walk(obj)
    return found


def check_body(obj) -> None:
    others = sorted({r for r in find_region_ids(obj) if r != REGION})
    if others:
        raise RegionLockError(f"request body names {len(others)} region(s) other than {REGION}")


def check_profile_body(body: dict) -> None:
    """A create or update body for a routing profile: Phoenix only, and stated explicitly."""
    check_body(body)
    rrp = body.get("regionRoutingPolicy")
    if not isinstance(rrp, dict) or rrp.get("allowedRegions") != [REGION]:
        raise RegionLockError(f"regionRoutingPolicy.allowedRegions must be exactly [{REGION!r}]")


def check_stored_profile(obj: dict) -> None:
    """A routing profile as the service returned it must list only Phoenix."""
    rrp = obj.get("regionRoutingPolicy")
    regions = rrp.get("allowedRegions") if isinstance(rrp, dict) else None
    if regions != [REGION]:
        raise RegionLockError("the stored routing profile does not list exactly the Phoenix region")
    check_body(obj)
