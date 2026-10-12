# v1.1 2026-10-10  Brief 14: the optional Generative AI project for the Responses API step (PLAN.md v1.1 S9).
#   v1.1: (Codex code review r1, finding 3) the project, once ACTIVE, must pass manifest.ownership_problems() (tags,
#         compartment, name, Phoenix OCID); otherwise it is deleted at once and the step stops (OwnershipError).
#   v1.0: first version.
"""S9 runs only if our user may create a Generative AI project. The create is written ahead to the manifest like
every other create; a refusal ends S9 and is recorded verbatim (redacted). CreateGenerativeAiProject answers 201
with a work request; the project is polled until ACTIVE (10-minute cap)."""
import time

from . import phoenix
from .manifest import Manifest, ownership_problems
from .ociclient import OciClient
from .ocisign import redact
from .profiles import OwnershipError

PROJECT_TYPE = "generativeaiproject"
DISPLAY_NAME = "b14-resp"
WAIT_S = 600
POLL_S = 5


def create_project(client: OciClient, manifest: Manifest, run_id: str, *, sleep=time.sleep,
                   clock=time.monotonic) -> dict:
    body = {"compartmentId": client.sec.compartment, "displayName": DISPLAY_NAME,
            "description": "Brief 14 lab, Responses API step; deleted after the post",
            "freeformTags": {"brief": "b14", "run": run_id, "kind": "project"}}
    e = manifest.intent("PROJ", PROJECT_TYPE, DISPLAY_NAME)
    res = client.request("POST", f"{phoenix.MGMT_BASE}/generativeAiProjects", json_body=body,
                         extra_headers={"opc-retry-token": e["retry_token"]}, repeatable=True)
    rec = {"alias": "PROJ", "http_status": res.status, "latency_ms": res.latency_ms}
    if res.status is None or res.status >= 500:
        manifest.unknown(e["seq"], res.error or f"HTTP {res.status}")
        return dict(rec, outcome="UNKNOWN")
    if not res.ok:
        msg = res.error_message(client.sec)
        manifest.refused(e["seq"], res.status, res.error_code(), msg)
        return dict(rec, outcome="REFUSED", code=res.error_code(), message=msg)
    ocid = (res.body or {}).get("id", "")
    manifest.created(e["seq"], ocid, res.status, (res.body or {}).get("timeCreated"))
    t0 = clock()
    while True:
        g = client.request("GET", f"{phoenix.MGMT_BASE}/generativeAiProjects/{ocid}", repeatable=True)
        state = (g.body or {}).get("lifecycleState") if g.ok else f"HTTP {g.status}"
        if state in ("ACTIVE", "FAILED") or clock() - t0 >= WAIT_S:
            break
        sleep(POLL_S)
    out = dict(rec, outcome="CREATED", state=state, seconds_to_state=round(clock() - t0, 1))
    entry = next(x for x in manifest.entries() if x["seq"] == e["seq"])
    problems = ownership_problems(g.body or {}, entry, client.sec.compartment) if g.ok else ["GET failed"]
    if problems:
        out["deleted_at_once"] = delete_project(client, manifest, e["seq"], ocid, sleep=sleep, clock=clock)
        raise OwnershipError(f"PROJ: the stored project fails the ownership check: {problems}")
    return {k: (redact(v, client.sec) if isinstance(v, str) else v) for k, v in out.items()}


def delete_project(client: OciClient, manifest: Manifest, seq: int, ocid: str, *, sleep=time.sleep,
                   clock=time.monotonic) -> dict:
    """Delete a project this process just created (its OCID came from our own create answer), then wait for it."""
    url = f"{phoenix.MGMT_BASE}/generativeAiProjects/{ocid}"
    res = client.request("DELETE", url, repeatable=True)
    if res.status == 404:
        manifest.deleted(seq, 404)
        return {"delete_status": 404, "gone": True}
    if not res.ok:
        manifest.delete_failed(seq, res.status, res.error_message(client.sec))
        return {"delete_status": res.status, "gone": False}
    t0 = clock()
    while clock() - t0 < WAIT_S:
        g = client.request("GET", url, repeatable=True)
        if g.status == 404 or (g.ok and (g.body or {}).get("lifecycleState") == "DELETED"):
            manifest.deleted(seq, res.status)
            return {"delete_status": res.status, "gone": True}
        sleep(POLL_S)
    manifest.delete_failed(seq, res.status, f"not gone after {WAIT_S} s")
    return {"delete_status": res.status, "gone": False}
