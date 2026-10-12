# v1.3 2026-10-10  Brief 14: OCI clean-up by manifest OCID and its verification (PLAN.md v1.1 section 12 step 1).
#   v1.3: (Codex adversarial review 31, finding 1) run() and verify_state() act only on this run's entries
#         (run_id == manifest.run_id); another run's live entry is reported under other_runs, never deleted, and
#         blocks a clean verdict. (The 10-Oct manifest holds one run only.)
#   v1.2: (POC restart after the 04:40Z session stop) verify_state(): a GET-only check that OCI holds exactly what the
#         manifest says (each entry's resource, the profile and project lists against S0 + the live entries); it
#         creates, changes and deletes nothing.
#   v1.1: (Codex plan review r1, finding 2) before each DELETE a GET must pass manifest.ownership_problems() against the
#         S0 baseline; a resource that fails it is not deleted and is reported.
#   v1.0: first version.
"""Delete exactly what the manifest lists, by OCID, newest first; then prove it.

Nothing is ever deleted by name or by listing: a `b14-*` routing profile that is not in the manifest is reported and
left alone. An UNKNOWN entry (a create whose answer was lost) must be resolved first by re-running its create with the
same retry token (profiles.create), because there is no OCID to delete; clean-up reports it and stops short of a
"clean" verdict.
"""
import time
import urllib.parse

from . import phoenix
from .manifest import LIVE_STATES, Manifest, ownership_problems
from .ociclient import OciClient
from .ocisign import sha16
from .profiles import RP_TYPE, list_profiles

PROJECT_TYPE = "generativeaiproject"
PROJECT_WAIT_S = 600
POLL_S = 5


def _url(rtype: str, ocid: str | None = None) -> str:
    base = phoenix.MGMT_BASE + ("/routingProfiles" if rtype == RP_TYPE else "/generativeAiProjects")
    return base + (f"/{ocid}" if ocid else "")


def list_projects(client: OciClient, max_pages: int = 20) -> list:
    items, page = [], None
    for _ in range(max_pages):
        q = [("compartmentId", client.sec.compartment), ("limit", "100")] + ([("page", page)] if page else [])
        res = client.request("GET", _url(PROJECT_TYPE) + "?" + urllib.parse.urlencode(q), repeatable=True)
        if not res.ok:
            raise RuntimeError(f"ListGenerativeAiProjects failed: HTTP {res.status} {res.error_code()}")
        items += [i for i in (res.body or {}).get("items", []) if i.get("lifecycleState") != "DELETED"]
        page = res.headers.get("opc-next-page")
        if not page:
            return items
    raise RuntimeError("ListGenerativeAiProjects: page cap reached")


def baseline(client: OciClient) -> dict:
    """S0: the ids (as sha256 prefixes) of every routing profile and project that exists before the POC."""
    return {"routing_profiles": sorted(sha16(i["id"]) for i in list_profiles(client)),
            "projects": sorted(sha16(i["id"]) for i in list_projects(client)),
            "taken_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _gone(client: OciClient, rtype: str, ocid: str) -> tuple[bool, str]:
    res = client.request("GET", _url(rtype, ocid), repeatable=True)
    if res.status == 404:
        return True, "404"
    if res.ok and (res.body or {}).get("lifecycleState") == "DELETED":
        return True, "DELETED"
    return False, ((res.body or {}).get("lifecycleState") if res.ok else f"HTTP {res.status}")


def delete_one(client: OciClient, manifest: Manifest, e: dict, base: dict, *, sleep=time.sleep,
               clock=time.monotonic) -> dict:
    rtype, ocid = e["type"], e["ocid"]
    got = client.request("GET", _url(rtype, ocid), repeatable=True)
    if got.status == 404:
        manifest.deleted(e["seq"], 404)
        return {"seq": e["seq"], "alias": e["alias"], "delete_status": 404, "gone": True}
    if not got.ok:
        manifest.delete_failed(e["seq"], got.status, "ownership check: GET failed")
        return {"seq": e["seq"], "alias": e["alias"], "gone": False, "refused": f"GET failed: HTTP {got.status}"}
    if (got.body or {}).get("lifecycleState") == "DELETED":
        manifest.deleted(e["seq"], got.status)
        return {"seq": e["seq"], "alias": e["alias"], "delete_status": None, "gone": True, "how": "DELETED"}
    baseline_ids = base["routing_profiles"] if rtype == RP_TYPE else base["projects"]
    problems = ownership_problems(got.body or {}, e, client.sec.compartment, baseline_ids)
    if problems:
        manifest.delete_failed(e["seq"], None, "ownership not verified: " + "; ".join(problems))
        return {"seq": e["seq"], "alias": e["alias"], "gone": False, "refused": problems}
    res = client.request("DELETE", _url(rtype, ocid), repeatable=True)
    if res.status == 404:
        manifest.deleted(e["seq"], 404)
        return {"seq": e["seq"], "alias": e["alias"], "delete_status": 404, "gone": True}
    if not res.ok:
        manifest.delete_failed(e["seq"], res.status, res.error_message(client.sec))
        return {"seq": e["seq"], "alias": e["alias"], "delete_status": res.status, "gone": False,
                "code": res.error_code()}
    t0 = clock()
    limit = PROJECT_WAIT_S if rtype == PROJECT_TYPE else 120
    while True:
        gone, how = _gone(client, rtype, ocid)
        if gone:
            manifest.deleted(e["seq"], res.status)
            return {"seq": e["seq"], "alias": e["alias"], "delete_status": res.status, "gone": True, "how": how,
                    "seconds": round(clock() - t0, 1)}
        if clock() - t0 >= limit:
            manifest.delete_failed(e["seq"], res.status, f"still {how} after {limit} s")
            return {"seq": e["seq"], "alias": e["alias"], "delete_status": res.status, "gone": False, "state": how}
        sleep(POLL_S)


def run(client: OciClient, manifest: Manifest, base: dict, *, sleep=time.sleep, clock=time.monotonic) -> dict:
    report = {"deleted": [], "unresolved": [], "verify": {}, "strangers": [], "other_runs": []}
    for e in manifest.live():
        if e.get("run_id") != manifest.run_id:
            report["other_runs"].append({"seq": e["seq"], "alias": e["alias"], "state": e["state"]})
            continue
        if e["state"] == "UNKNOWN" or not e["ocid"]:
            report["unresolved"].append({"seq": e["seq"], "alias": e["alias"], "state": e["state"]})
            continue
        report["deleted"].append(delete_one(client, manifest, e, base, sleep=sleep, clock=clock))
    # verification 1: every OCID this run ever held is gone
    per_entry = []
    for e in manifest.entries():
        if e["ocid"] and e.get("run_id") == manifest.run_id:
            gone, how = _gone(client, e["type"], e["ocid"])
            per_entry.append({"seq": e["seq"], "alias": e["alias"], "gone": gone, "how": how})
    # verification 2: what exists now is exactly the S0 baseline (nothing of ours left, nothing else removed)
    rps, projs = list_profiles(client), list_projects(client)
    now_rp, now_pj = sorted(sha16(i["id"]) for i in rps), sorted(sha16(i["id"]) for i in projs)
    ours = {e["ocid_sha16"] for e in manifest.entries() if e["ocid_sha16"]}
    report["strangers"] = [{"display_name": i.get("displayName"), "id_sha16": sha16(i["id"])} for i in rps
                           if str(i.get("displayName", "")).startswith("b14-") and sha16(i["id"]) not in ours]
    report["verify"] = {
        "manifest_ocids_gone": all(x["gone"] for x in per_entry),
        "per_entry": per_entry,
        "routing_profiles_equal_baseline": now_rp == base["routing_profiles"],
        "projects_equal_baseline": now_pj == base["projects"],
        "baseline_missing_routing_profiles": len(set(base["routing_profiles"]) - set(now_rp)),
        "baseline_missing_projects": len(set(base["projects"]) - set(now_pj)),
    }
    report["refused"] = [d for d in report["deleted"] if d.get("refused")]
    report["clean"] = (not report["unresolved"] and not report["refused"] and not report["other_runs"]
                       and report["verify"]["manifest_ocids_gone"]
                       and report["verify"]["routing_profiles_equal_baseline"]
                       and report["verify"]["projects_equal_baseline"])
    return report


def verify_state(client: OciClient, manifest: Manifest, base: dict) -> dict:
    """Read-only (GET and List only): does OCI hold exactly what the manifest says? Nothing is created or deleted.

    Per manifest entry: CREATED must answer 200 and ACTIVE, pass ownership_problems() against the S0 baseline and, for a
    routing profile, store exactly the Phoenix region; DELETED must answer 404 or DELETED; REFUSED has no OCID and
    is not looked up; INTENT, UNKNOWN and DELETE_FAILED are reported as needing attention. Then the lists: what is not
    DELETED now must be exactly the S0 baseline plus this manifest's live OCIDs, and no `b14-*` routing profile may
    exist outside the manifest (reported, never touched).
    """
    rows = []
    for e in manifest.entries():
        row = {"seq": e["seq"], "alias": e["alias"], "type": e["type"], "manifest_state": e["state"]}
        if e.get("run_id") != manifest.run_id:     # another run's entry: a person has to look
            rows.append(dict(row, checked=False, other_run=True, as_expected=False))
            continue
        if not e["ocid"]:
            row.update(checked=False, as_expected=e["state"] == "REFUSED")
            rows.append(row)
            continue
        res = client.request("GET", _url(e["type"], e["ocid"]), repeatable=True)
        got = res.body if res.ok and isinstance(res.body, dict) else {}
        life = got.get("lifecycleState") if res.ok else None
        row.update(checked=True, http_status=res.status, lifecycle=life)
        if e["state"] == "DELETED":
            row["as_expected"] = res.status == 404 or life == "DELETED"
        elif e["state"] == "CREATED":
            base_ids = base["routing_profiles"] if e["type"] == RP_TYPE else base["projects"]
            problems = (ownership_problems(got, e, client.sec.compartment, base_ids) if res.ok
                        else [f"GET answered HTTP {res.status}"])
            if res.ok and e["type"] == RP_TYPE:
                regions = (got.get("regionRoutingPolicy") or {}).get("allowedRegions")
                row["stored_regions"] = regions
                row["stored_models"] = (got.get("modelRoutingPolicy") or {}).get("allowedModels")
                if regions != [phoenix.REGION]:
                    problems.append("stored regions are not exactly the Phoenix region")
            row["problems"] = problems
            row["as_expected"] = bool(res.ok and life == "ACTIVE" and not problems)
        else:                                   # INTENT, UNKNOWN, DELETE_FAILED: a person has to look
            row["as_expected"] = False
        rows.append(row)

    entries = manifest.entries()
    ours = {e["ocid_sha16"] for e in entries if e["ocid_sha16"]}

    def compare(now_items: list, rtype: str, base_ids: list) -> dict:
        now = {sha16(i["id"]) for i in now_items}
        live = {e["ocid_sha16"] for e in entries
                if e["ocid_sha16"] and e["type"] == rtype and e["state"] in LIVE_STATES}
        expected = set(base_ids) | live
        return {"now": len(now), "baseline": len(base_ids), "manifest_live": len(live),
                "equal_baseline_plus_live": now == expected, "baseline_missing": len(set(base_ids) - now),
                "unexplained": len(now - expected), "live_missing": len(live - now)}

    rps, projs = list_profiles(client), list_projects(client)
    out = {"taken_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "entries": rows,
           "routing_profiles": compare(rps, RP_TYPE, base["routing_profiles"]),
           "projects": compare(projs, PROJECT_TYPE, base["projects"]),
           "strangers": [{"display_name": i.get("displayName"), "id_sha16": sha16(i["id"])} for i in rps
                         if str(i.get("displayName", "")).startswith("b14-") and sha16(i["id"]) not in ours]}
    out["consistent"] = (all(r["as_expected"] for r in rows) and not out["strangers"]
                         and out["routing_profiles"]["equal_baseline_plus_live"]
                         and out["projects"]["equal_baseline_plus_live"])
    return out
