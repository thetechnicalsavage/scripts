# v1.1 2026-10-10  Brief 14: profile plan from discovery, invalid attempts, create/verify/delete-on-accept
#   (PLAN.md v1.1 section 6 S2 and S4).
#   v1.1: (Codex plan review r1, findings 2-3; code review r1, finding 2) every delete is preceded by a GET and
#         manifest.ownership_problems(); the stored resource must pass the same check (tags included) right after its
#         create, otherwise it is deleted at once and the run stops (OwnershipError).
#   v1.0: first version.
"""Routing profiles, Phoenix only.

build_plan() is offline: it reads the S1 discovery snapshot and returns the five profiles and five invalid attempts,
each body already checked by the Phoenix lock (the compartment is added at S4). create() writes the manifest intent
first, sends the create with that entry's retry token, records the outcome, waits for ACTIVE and checks the stored
regions; an accepted invalid attempt is deleted at once. At most MAX_PROFILES routing profiles are ever created by
the run, transient ones included.
"""
import re
import time
import urllib.parse
from datetime import datetime, timedelta

from . import phoenix
from .budget import PRICES
from .manifest import Manifest, ownership_problems
from .ociclient import OciClient
from .ocisign import redact

RP_TYPE = "generativeairoutingprofile"
MAX_PROFILES = 5
ACTIVE_WAIT_S = 120
POLL_S = 2
SLATE = (  # alias, model, fallback   (PLAN.md section 2)
    ("P1", "xai.grok-4.3", None),
    ("P2", "xai.grok-4.20-0309-reasoning", None),
    ("P3", "google.gemini-2.5-flash", None),
    ("P4", "google.gemini-2.5-pro", None),
    ("P5", "xai.grok-4.7", "xai.grok-4.6"),
)
ALLOWED_VENDORS = ("xai", "google")
N1_ALIAS_MODEL = "xai.grok-4.20-non-reasoning"
N3_PREFERRED = "openai.gpt-oss-120b"
N4_MODEL = "meta.llama-3.3-70b-instruct"
ORDER = ("P1", "P2", "P3", "N1", "N2", "N3", "N4", "N5", "P4", "P5")   # PLAN.md S4 order


class CapReached(RuntimeError):
    pass


class OwnershipError(RuntimeError):
    """A resource we just created does not carry what clean-up will require; it was deleted and the run stops."""


def _avail(item: dict, region: str = phoenix.REGION) -> dict | None:
    return next((a for a in item.get("availability") or [] if a.get("region") == region), None)


def _on_demand_somewhere(item: dict) -> bool:
    return any("ON_DEMAND" in (a.get("servingModes") or []) for a in item.get("availability") or [])


def _slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")[:40]


def candidate_ok(item: dict | None, snapshot_utc: str) -> tuple[bool, str]:
    """PLAN.md S2 rules for a profile model, judged on the S1 snapshot."""
    if item is None:
        return False, "not in discovery"
    if item.get("modelId") not in PRICES:
        return False, "no price entry"
    if item.get("vendor") not in ALLOWED_VENDORS:
        return False, "vendor not xAI or Google"
    if "CHAT" not in (item.get("capabilities") or []):
        return False, "no CHAT capability"
    a = _avail(item)
    if a is None or "ON_DEMAND" not in (a.get("servingModes") or []):
        return False, "not on demand in Phoenix"
    t0 = datetime.strptime(snapshot_utc[:19], "%Y-%m-%dT%H:%M:%S")
    dep = a.get("timeDeprecated")
    if dep and datetime.strptime(dep[:19], "%Y-%m-%dT%H:%M:%S") <= t0:
        return False, "deprecated in Phoenix"
    ret = a.get("timeOnDemandRetired")
    if ret and datetime.strptime(ret[:19], "%Y-%m-%dT%H:%M:%S") <= t0 + timedelta(days=30):
        return False, "on-demand retirement within 30 days"
    return True, ""


def profile_body(alias: str, model_list: list | None, run_id: str, kind: str) -> dict:
    body = {
        "compartmentId": None,                      # filled from the environment at S4
        "displayName": f"b14-{alias.lower()}-" + (_slug(model_list[0]) if model_list else "no-model-policy"),
        "description": f"Brief 14 lab, Phoenix only ({kind}); deleted after the post",
        "freeformTags": {"brief": "b14", "run": run_id, "kind": kind},
        "regionRoutingPolicy": {"allowedRegions": [phoenix.REGION]},
    }
    if model_list is not None:
        body["modelRoutingPolicy"] = {"allowedModels": list(model_list)}
    phoenix.check_profile_body(body)
    return body


def build_plan(md_all: list, run_id: str, snapshot_utc: str) -> dict:
    """S2: profiles P1-P5 and invalid attempts N1-N5 from the discovery snapshot (no network)."""
    by_id = {i.get("modelId"): i for i in md_all}
    profiles, dropped = [], []
    for alias, model, fallback in SLATE:
        ok, why = candidate_ok(by_id.get(model), snapshot_utc)
        chosen = model if ok else None
        if not ok:
            dropped.append({"alias": alias, "model": model, "reason": why})
            if fallback:
                ok2, why2 = candidate_ok(by_id.get(fallback), snapshot_utc)
                if ok2:
                    chosen = fallback
                else:
                    dropped.append({"alias": alias, "model": fallback, "reason": why2})
        if chosen:
            profiles.append({"alias": alias, "kind": "profile", "model": chosen,
                             "body": profile_body(alias, [chosen], run_id, "profile")})
    chosen_models = [p["model"] for p in profiles]
    invalid = []

    def add(alias, models, premise_ok, premise, expected):
        e = {"alias": alias, "kind": "invalid", "models": models, "premise": premise, "premise_ok": premise_ok,
             "expected": expected}
        if premise_ok:
            e["body"] = profile_body(alias, models, run_id, "invalid")
        invalid.append(e)

    add("N1", [N1_ALIAS_MODEL], N1_ALIAS_MODEL not in by_id,
        "the alias is absent from discovery", "open question")
    two = [m for m in ("xai.grok-4.3", "google.gemini-2.5-flash") if m in chosen_models]
    add("N2", two, len(two) == 2, "both models passed the S2 rules", "refused (docs: one model)")
    ded = [i["modelId"] for i in md_all
           if (a := _avail(i)) and "DEDICATED" in (a.get("servingModes") or [])
           and "ON_DEMAND" not in (a.get("servingModes") or [])]
    n3 = N3_PREFERRED if N3_PREFERRED in ded else (sorted(ded)[0] if ded else None)
    add("N3", [n3] if n3 else [], n3 is not None, "a model discovery shows DEDICATED-only in Phoenix",
        "refused (docs: on-demand models only)")
    n4 = by_id.get(N4_MODEL)
    n4_ok = bool(n4) and _on_demand_somewhere(n4) and not (
        (a := _avail(n4)) and "ON_DEMAND" in (a.get("servingModes") or []))
    add("N4", [N4_MODEL], n4_ok, "on demand elsewhere, not on demand in Phoenix",
        "refused (docs: regions where the model is available)")
    add("N5", None, True, "no modelRoutingPolicy (not required by the schema)", "open question")
    return {"run_id": run_id, "snapshot_utc": snapshot_utc, "region": phoenix.REGION, "order": list(ORDER),
            "profiles": profiles, "invalid": invalid, "dropped": dropped}


# ---------------------------------------------------------------------------------------------- OCI operations
def _rp_url(ocid: str | None = None) -> str:
    return f"{phoenix.MGMT_BASE}/routingProfiles" + (f"/{ocid}" if ocid else "")


def list_profiles(client: OciClient, max_pages: int = 20) -> list:
    """All routing profiles in the compartment that are not DELETED (raw items: callers hash or redact ids)."""
    items, page = [], None
    for _ in range(max_pages):
        q = [("compartmentId", client.sec.compartment), ("limit", "100")] + ([("page", page)] if page else [])
        res = client.request("GET", _rp_url() + "?" + urllib.parse.urlencode(q), repeatable=True)
        if not res.ok:
            raise RuntimeError(f"ListRoutingProfiles failed: HTTP {res.status} {res.error_code()}")
        items += [i for i in (res.body or {}).get("items", []) if i.get("lifecycleState") != "DELETED"]
        page = res.headers.get("opc-next-page")
        if not page:
            return items
    raise RuntimeError("ListRoutingProfiles: page cap reached")


def wait_state(client: OciClient, ocid: str, want: tuple, *, timeout_s: int = ACTIVE_WAIT_S,
               sleep=time.sleep, clock=time.monotonic) -> tuple[str | None, float, dict | None]:
    """Poll GetRoutingProfile until lifecycleState is in `want` (or 404 when '404' is in want)."""
    t0 = clock()
    while True:
        res = client.request("GET", _rp_url(ocid), repeatable=True)
        if res.status == 404 and "404" in want:
            return "404", clock() - t0, None
        if res.ok:
            stt = (res.body or {}).get("lifecycleState")
            if stt in want or stt == "FAILED":
                return stt, clock() - t0, res.body
        if clock() - t0 >= timeout_s:
            return ((res.body or {}).get("lifecycleState") if res.ok else f"HTTP {res.status}"), clock() - t0, \
                (res.body if res.ok else None)
        sleep(POLL_S)


def delete_profile(client: OciClient, manifest: Manifest, seq: int, ocid: str, *, sleep=time.sleep,
                   require_tags: bool = True) -> dict:
    """Delete one profile this run created, after a GET proves it is ours (manifest.ownership_problems)."""
    entry = next(e for e in manifest.entries() if e["seq"] == seq)
    got = client.request("GET", _rp_url(ocid), repeatable=True)
    if got.status == 404:
        manifest.deleted(seq, 404)
        return {"delete_status": 404, "gone": True}
    if not got.ok:
        manifest.delete_failed(seq, got.status, "ownership check: GET failed")
        return {"delete_status": None, "gone": False, "refused": f"GET failed: HTTP {got.status}"}
    problems = ownership_problems(got.body or {}, entry, client.sec.compartment, require_tags=require_tags)
    if problems:
        manifest.delete_failed(seq, None, "ownership not verified: " + "; ".join(problems))
        return {"delete_status": None, "gone": False, "refused": problems}
    res = client.request("DELETE", _rp_url(ocid), repeatable=True)
    if res.status == 404:
        manifest.deleted(seq, 404)
        return {"delete_status": 404, "gone": True}
    if not res.ok:
        manifest.delete_failed(seq, res.status, res.error_message(client.sec))
        return {"delete_status": res.status, "gone": False, "code": res.error_code()}
    state, secs, _ = wait_state(client, ocid, ("DELETED", "404"), sleep=sleep)
    if state in ("DELETED", "404"):
        manifest.deleted(seq, res.status)
        return {"delete_status": res.status, "gone": True, "seconds_to_gone": round(secs, 1)}
    manifest.delete_failed(seq, res.status, f"not gone after {ACTIVE_WAIT_S} s (state {state})")
    return {"delete_status": res.status, "gone": False, "state": state}


def create(client: OciClient, manifest: Manifest, spec: dict, *, sleep=time.sleep) -> dict:
    """S4: one create (profile or invalid attempt). Returns a redacted record."""
    if "body" not in spec:
        return {"alias": spec["alias"], "outcome": "SKIPPED", "reason": f"premise not met: {spec.get('premise')}"}
    # an INTENT/UNKNOWN entry for this alias is being resolved (same retry token): it is already counted
    if manifest.pending(spec["alias"]) is None and manifest.count_created(RP_TYPE) >= MAX_PROFILES:
        raise CapReached(f"{MAX_PROFILES} routing profiles already created in this run")
    body = dict(spec["body"], compartmentId=client.sec.compartment)
    phoenix.check_profile_body(body)
    e = manifest.intent(spec["alias"], RP_TYPE, body["displayName"])
    before = len(list_profiles(client)) if spec["kind"] == "invalid" else None
    res = client.request("POST", _rp_url(), json_body=body, extra_headers={"opc-retry-token": e["retry_token"]},
                         repeatable=True)
    rec = {"alias": spec["alias"], "kind": spec["kind"], "models": body.get("modelRoutingPolicy", {}).get(
        "allowedModels"), "http_status": res.status, "latency_ms": res.latency_ms, "attempts": res.attempts}
    if res.status is None or res.status >= 500:
        manifest.unknown(e["seq"], res.error or f"HTTP {res.status}")
        rec.update(outcome="UNKNOWN", error=res.error, code=res.error_code())
        return rec
    if not res.ok:
        msg = res.error_message(client.sec)
        manifest.refused(e["seq"], res.status, res.error_code(), msg)
        rec.update(outcome="REFUSED", code=res.error_code(), message=msg)
        if before is not None:
            rec["profiles_before"], rec["profiles_after"] = before, len(list_profiles(client))
        return rec
    ocid = (res.body or {}).get("id", "")
    manifest.created(e["seq"], ocid, res.status, (res.body or {}).get("timeCreated"))
    rec["outcome"] = "CREATED"
    state, secs, stored = wait_state(client, ocid, ("ACTIVE",), sleep=sleep)
    rec.update(state=state, seconds_to_state=round(secs, 1))
    if stored is not None:
        rec["stored_models"] = (stored.get("modelRoutingPolicy") or {}).get("allowedModels")
        rec["stored_regions"] = (stored.get("regionRoutingPolicy") or {}).get("allowedRegions")
        rec["has_previous_state"] = stored.get("previousState") is not None
        tags = stored.get("freeformTags") or {}
        rec["tags_ok"] = tags.get("brief") == "b14" and tags.get("run") == manifest.run_id
    if stored is None:
        # the stored regions could not be read back: never keep a profile we could not verify
        rec["outcome"] = "CREATED_UNVERIFIED"
        rec["deleted_at_once"] = delete_profile(client, manifest, e["seq"], ocid, sleep=sleep, require_tags=False)
        return {k: (redact(v, client.sec) if isinstance(v, str) else v) for k, v in rec.items()}
    try:
        phoenix.check_stored_profile(stored)
    except phoenix.RegionLockError:
        rec["deleted_at_once"] = delete_profile(client, manifest, e["seq"], ocid, sleep=sleep, require_tags=False)
        raise
    entry = next(x for x in manifest.entries() if x["seq"] == e["seq"])
    problems = ownership_problems(stored, entry, client.sec.compartment)
    if problems:
        # clean-up would refuse to delete it later (it requires these marks): delete it now, while we know it is ours
        rec["deleted_at_once"] = delete_profile(client, manifest, e["seq"], ocid, sleep=sleep, require_tags=False)
        raise OwnershipError(f"{spec['alias']}: the stored profile fails the ownership check: {problems}")
    if spec["kind"] == "invalid":
        rec["deleted_at_once"] = delete_profile(client, manifest, e["seq"], ocid, sleep=sleep, require_tags=False)
        if before is not None:
            rec["profiles_before"], rec["profiles_after"] = before, len(list_profiles(client))
    return {k: (redact(v, client.sec) if isinstance(v, str) else v) for k, v in rec.items()}
