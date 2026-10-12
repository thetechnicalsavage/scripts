#!/usr/bin/env python3
# v1.4 2026-10-10  Brief 14 POC runner, OCI steps (PLAN.md v1.1 section 6: S0-S5, S9, S10 OCI part).
#   v1.4: (S10, before any deletion) `export`: GET-only definitions of this run's resources with named
#         placeholders for every identifier (b14lib/export.py), written to results/s10/definitions/.
#   v1.3: (S9 re-run) `responses` reuses this run's CREATED project from the manifest (GET, ACTIVE, ownership check
#         against S0) instead of creating a second one; --use-reserve sends the calls from the reserve bucket with a
#         reason; a re-run writes results/s9/responses_r<n>.json and never overwrites an earlier run's file.
#   v1.2: (POC restart after the 04:40Z session stop) `verify`: GET-only check of every manifest resource and of the
#         profile and project lists against S0 (cleanup.verify_state); writes results/state/verify_<utc>.json.
#   v1.1: OwnershipError (a just-created resource without our marks: deleted, run stopped) is a guard refusal; status
#         output goes to stdout without print().
#   v1.0: first version. Written in the plan stage; NOT run yet. Runs on the demo VM from ~/b14-poc with the venv of
#         code/requirements.txt. Every OCI call goes through b14lib (Phoenix lock, budget, manifest, redaction).
"""Usage: b14_run.py <step> [options]

Steps:  preflight   S0  auth check, baselines of routing profiles and projects (no inference)
        snapshot    S1  discovery and ListModels snapshot, Phoenix only (no inference)
        plan        S2  profile plan from the snapshot (offline)
        smoke       S3  one direct call per planned model            (budget bucket s3_smoke)
        create      S4  profiles and invalid attempts, manifest first (no inference)
        route       S5  direct against profile, model held constant  (budget bucket s5_routing)
        responses   S9  optional Responses API step                  (budget bucket s9_responses)
        cleanup     S10 delete the manifest's OCIDs and verify against the S0 baseline
        verify          GET only: every manifest resource and the lists against S0 (no create, no delete)
        export      S10 GET only: definitions of this run's resources, identifiers as named placeholders
        status          budget summary and the redacted manifest

Secrets: --env (default ~/oracle26ai/secrets/oracle.env) and --key (default ~/oracle26ai/secrets/oci_key.pem) are
read, never printed. Results (redacted) go under <home>/results/<step>/; the budget ledger is <home>/state/budget.json.
Exit codes: 0 done, 1 refused by a guard (lock, budget, cap) or a failed prerequisite, 2 usage.
"""
import argparse
import json
import logging
import os
import statistics
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from b14lib import chat, cleanup, export, phoenix, profiles, project  # noqa: E402
from b14lib.budget import BudgetExceeded, Ledger  # noqa: E402
from b14lib.manifest import Manifest, ownership_problems  # noqa: E402
from b14lib.ociclient import OciClient  # noqa: E402
from b14lib.ocisign import Secrets, redact  # noqa: E402

LOG = logging.getLogger("b14.run")
SECRETS_DIR = Path("~/oracle26ai/secrets").expanduser()

# S1 probe set: the study's Phoenix probes (code/probes/oci_readonly_probe.py v1.0) without any other region,
# Identity or Limits call, plus the dedicated-mode list and one modelId filter per S2 candidate.
SNAPSHOT_PROBES = [
    ("md_all", "/modelDiscovery", [("limit", "100")]),
    ("md_region_chat_ondemand", "/modelDiscovery",
     [("region", phoenix.REGION), ("capability", "CHAT"), ("servingMode", "ON_DEMAND")]),
    ("md_region_dedicated", "/modelDiscovery", [("region", phoenix.REGION), ("servingMode", "DEDICATED")]),
    ("md_grok420_nonreasoning", "/modelDiscovery", [("modelId", "xai.grok-4.20-non-reasoning")]),
    ("md_grok420_0309_nonreasoning", "/modelDiscovery", [("modelId", "xai.grok-4.20-0309-non-reasoning")]),
    ("md_proxy", "/modelDiscovery", [("modelAccess", "PROXY")]),
    ("md_api_responses", "/modelDiscovery", [("apiCapability", "OPENAI_V1_RESPONSES")]),
    ("md_realm_oc1", "/modelDiscovery", [("realm", "oc1")]),
    ("md_region_od_not_retired", "/modelDiscovery",
     [("region", phoenix.REGION), ("servingMode", "ON_DEMAND"), ("isOnDemandRetired", "false")]),
    ("md_deprecated", "/modelDiscovery", [("isDeprecated", "true")]),
    ("lm_chat", "/models", [("capability", "CHAT")]),
    ("rp_list_home", "/routingProfiles", []),
] + [(f"md_model_{m.replace('.', '_')}", "/modelDiscovery", [("modelId", m)])
     for m in sorted({x for _, m, fb in profiles.SLATE for x in (m, fb) if x})]


def utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path: Path, obj, sec) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = redact(json.dumps(obj, indent=1, default=str, sort_keys=False), sec)
    path.write_text(text + "\n", encoding="utf-8")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_probe(client: OciClient, name: str, path: str, params: list, max_pages: int = 20) -> dict:
    items, statuses, latencies, err, page = [], [], [], None, None
    pages = 0
    while pages < max_pages:
        q = [("compartmentId", client.sec.compartment)] + list(params) + ([("page", page)] if page else [])
        res = client.request("GET", phoenix.MGMT_BASE + path + "?" + urllib.parse.urlencode(q), repeatable=True)
        pages += 1
        statuses.append(res.status)
        latencies.append(res.latency_ms)
        if not res.ok:
            err = {"code": res.error_code(), "message": res.error_message(client.sec), "transport": res.error}
            break
        items += (res.body or {}).get("items", []) if isinstance(res.body, dict) else []
        page = res.headers.get("opc-next-page")
        if not page:
            break
    return {"probe": name, "request": f"GET {path} params={params}", "http_status": statuses, "pages": pages,
            "latency_ms": latencies, "item_count": len(items), "error": err, "items": None if err else items,
            "probed_at_utc": utc()}


# ---------------------------------------------------------------------------------------------------- steps
def step_preflight(a, sec, client) -> int:
    probe = run_probe(client, "md_limit1", "/modelDiscovery", [("limit", "1")], max_pages=1)
    if probe["error"]:
        LOG.error("discovery did not answer 200 in %s: %s", phoenix.REGION, probe["error"])
        write_json(a.home / "results/s0/preflight.json", {"discovery": probe, "verdict": "STOP"}, sec)
        return 1
    base = cleanup.baseline(client)
    write_json(a.home / "state/baseline.json", base, sec)
    out = {"region": phoenix.REGION, "lock": "on", "discovery_status": probe["http_status"],
           "routing_profiles_before": len(base["routing_profiles"]), "projects_before": len(base["projects"]),
           "taken_utc": base["taken_utc"], "verdict": "GO"}
    write_json(a.home / "results/s0/preflight.json", out, sec)
    LOG.info("preflight GO: %s", out)
    return 0


def step_snapshot(a, sec, client) -> int:
    outdir = a.home / "results/s1"
    summary = {}
    for name, path, params in SNAPSHOT_PROBES:
        r = run_probe(client, name, path, params)
        write_json(outdir / f"{name}.json", r, sec)
        summary[name] = {k: r[k] for k in ("http_status", "pages", "item_count", "latency_ms", "error")}
    write_json(outdir / "summary.json", summary, sec)
    bad = [n for n, s in summary.items() if s["error"]]
    LOG.info("snapshot: %d probes, %d with errors %s", len(summary), len(bad), bad)
    return 0 if "md_all" not in bad else 1


def step_plan(a, sec, client) -> int:
    md = load_json(a.home / "results/s1/md_all.json")
    if md.get("error") or not md.get("items"):
        LOG.error("no usable md_all snapshot")
        return 1
    plan = profiles.build_plan(md["items"], a.run_id, md["probed_at_utc"])
    write_json(a.home / "results/s2/profile_plan.json", plan, sec)
    LOG.info("plan: %d profiles %s, dropped %s", len(plan["profiles"]), [p["model"] for p in plan["profiles"]],
             plan["dropped"])
    return 0


def _plan(a) -> dict:
    plan = load_json(a.home / "results/s2/profile_plan.json")
    if plan.get("run_id") != a.run_id:
        raise SystemExit("the plan belongs to another run id")
    return plan


def step_smoke(a, sec, client) -> int:
    ledger = Ledger(a.home / "state/budget.json")
    out = []
    for p in _plan(a)["profiles"]:
        t = chat.Target(alias=f"M:{p['model']}", kind="direct", model=p["model"], serving_id=p["model"])
        out.append(chat.call(client, ledger, "s3_smoke", t, 0))
        time.sleep(1)
    write_json(a.home / "results/s3/smoke.json", out, sec)
    return 0


def step_create(a, sec, client) -> int:
    plan = _plan(a)
    smoke = {r["model"]: (r["final"].get("status") == 200 and r["final"].get("answer_ok"))
             for r in load_json(a.home / "results/s3/smoke.json")}
    specs = {s["alias"]: s for s in plan["profiles"] + plan["invalid"]}
    man = Manifest(a.manifest, a.run_id)
    records, auth_refusals = [], 0
    for alias in plan["order"]:
        s = specs.get(alias)
        if s is None:
            records.append({"alias": alias, "outcome": "NOT_IN_PLAN"})
            continue
        if s["kind"] == "profile" and not smoke.get(s["model"]):
            records.append({"alias": alias, "outcome": "SKIPPED", "reason": "the model failed the S3 direct call"})
            continue
        if auth_refusals >= 2:
            records.append({"alias": alias, "outcome": "SKIPPED", "reason": "create not authorized for our user"})
            continue
        try:
            rec = profiles.create(client, man, s)
        except profiles.CapReached as exc:
            records.append({"alias": alias, "outcome": "SKIPPED", "reason": str(exc)})
            continue
        records.append(rec)
        if rec.get("outcome") == "REFUSED" and rec.get("http_status") in (401, 403, 404) \
                and "NotAuthorized" in str(rec.get("code")) and s["kind"] == "profile":
            auth_refusals += 1
        write_json(a.home / "results/s4/records.json", records, sec)
        write_json(a.home / "results/manifest_redacted.json", man.redacted(), sec)
    write_json(a.home / "results/s4/records.json", records, sec)
    write_json(a.home / "results/manifest_redacted.json", man.redacted(), sec)
    return 0


def _profile_targets(a) -> list:
    plan = _plan(a)
    models = {p["alias"]: p["model"] for p in plan["profiles"]}
    man = Manifest(a.manifest, a.run_id)
    out = []
    for e in man.entries():
        if e["run_id"] == a.run_id and e["state"] == "CREATED" and e["alias"] in models:
            out.append(chat.Target(alias=e["alias"], kind="profile", model=models[e["alias"]], serving_id=e["ocid"]))
    return out


def step_route(a, sec, client) -> int:
    ledger = Ledger(a.home / "state/budget.json")
    calls_path = a.home / "results/s5/calls.jsonl"
    calls_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    try:
        for prof in _profile_targets(a):
            direct = chat.Target(alias=f"M:{prof.model}", kind="direct", model=prof.model, serving_id=prof.model)
            for n in range(len(chat.PROMPTS)):
                pair = (direct, prof) if n % 2 == 0 else (prof, direct)
                for t in pair:
                    r = chat.call(client, ledger, "s5_routing", t, n)
                    r["profile_alias"] = prof.alias
                    rows.append(r)
                    with calls_path.open("a", encoding="utf-8") as fh:
                        fh.write(redact(json.dumps(r, default=str), sec) + "\n")
                    time.sleep(1)
    except BudgetExceeded as exc:
        LOG.error("budget stop: %s", exc)
    write_json(a.home / "results/s5/summary.json", summarize(rows), sec)
    return 0


def summarize(rows: list) -> dict:
    out = {}
    for r in rows:
        key = r["profile_alias"]
        s = out.setdefault(key, {"model": r["model"], "direct": [], "profile": []})
        s[r["kind"]].append(r["final"])
    res = {}
    for key, s in out.items():
        res[key] = {"model": s["model"]}
        for kind in ("direct", "profile"):
            f = s[kind]
            ok = [x for x in f if x.get("status") == 200]
            lat = [x["latency_ms"] for x in ok if x.get("latency_ms") is not None]
            res[key][kind] = {
                "n": len(f), "http_200": len(ok), "answer_ok": sum(1 for x in f if x.get("answer_ok")),
                "statuses": sorted({str(x.get("status")) for x in f}),
                "error_codes": sorted({str(x.get("code")) for x in f if x.get("code")}),
                "latency_p50_ms": statistics.median(lat) if lat else None,
                "model_id_shapes": sorted({str(x.get("model_id_shape")) for x in ok}),
                "model_versions": sorted({str(x.get("model_version")) for x in ok}),
                "mean_completion_tokens": round(statistics.mean([x["completion_tokens"] for x in ok
                                                                 if x.get("completion_tokens") is not None]), 1)
                if any(x.get("completion_tokens") is not None for x in ok) else None,
                "header_names": sorted({h for x in ok for h in (x.get("header_names") or [])}),
            }
    return res


def _existing_project(a, client, man) -> tuple:
    """This run's CREATED project, re-checked (GET, ACTIVE, ownership against S0), or (None, None) if there is none."""
    proj = next((e for e in man.entries() if e["alias"] == "PROJ" and e["run_id"] == a.run_id
                 and e["state"] == "CREATED"), None)
    if proj is None:
        return None, None
    g = client.request("GET", f"{phoenix.MGMT_BASE}/generativeAiProjects/{proj['ocid']}", repeatable=True)
    base = load_json(a.home / "state/baseline.json")
    problems = (ownership_problems(g.body or {}, proj, client.sec.compartment, base["projects"]) if g.ok
                else [f"GET answered HTTP {g.status}"])
    state = (g.body or {}).get("lifecycleState") if g.ok else None
    if problems or state != "ACTIVE":
        raise profiles.OwnershipError(f"PROJ: the existing project is not usable: state {state}, {problems}")
    return proj, {"alias": "PROJ", "outcome": "REUSED", "state": state, "http_status": g.status}


def step_responses(a, sec, client) -> int:
    man = Manifest(a.manifest, a.run_id)
    ledger = Ledger(a.home / "state/budget.json")
    proj, rec = _existing_project(a, client, man)
    if proj is None:
        rec = project.create_project(client, man, a.run_id)
    out = {"project": rec, "use_reserve": a.use_reserve, "calls": []}
    if rec.get("state") == "ACTIVE" and rec.get("outcome") in ("CREATED", "REUSED"):
        proj = proj or next(e for e in man.entries() if e["alias"] == "PROJ" and e["run_id"] == a.run_id
                            and e["state"] == "CREATED")
        targets = [t for t in _profile_targets(a) if t.alias == "P1"]
        if targets:
            prof = targets[0]
            direct = chat.Target(alias=f"M:{prof.model}", kind="direct", model=prof.model, serving_id=prof.model)
            for n in range(3):
                for t in ((direct, prof) if n % 2 == 0 else (prof, direct)):
                    out["calls"].append(chat.call_responses(client, ledger, "s9_responses", t, n, proj["ocid"],
                                                            use_reserve=a.use_reserve))
                    time.sleep(1)
    outdir = a.home / "results/s9"
    path, n = outdir / "responses.json", 1
    while path.exists():                     # a re-run never overwrites an earlier run's record
        n += 1
        path = outdir / f"responses_r{n}.json"
    write_json(path, out, sec)
    write_json(a.home / "results/manifest_redacted.json", man.redacted(), sec)
    LOG.info("responses: project %s, %d calls, written to %s", rec.get("outcome"), len(out["calls"]), path.name)
    return 0


def step_cleanup(a, sec, client) -> int:
    man = Manifest(a.manifest, a.run_id)
    base = load_json(a.home / "state/baseline.json")
    rep = cleanup.run(client, man, base)
    write_json(a.home / "results/cleanup_report.json", rep, sec)
    write_json(a.home / "results/manifest_redacted.json", man.redacted(), sec)
    LOG.info("clean-up: clean=%s unresolved=%d strangers=%d", rep["clean"], len(rep["unresolved"]),
             len(rep["strangers"]))
    return 0 if rep["clean"] else 1


def step_verify(a, sec, client) -> int:
    rep = cleanup.verify_state(client, Manifest(a.manifest, a.run_id), load_json(a.home / "state/baseline.json"))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    write_json(a.home / f"results/state/verify_{stamp}.json", rep, sec)
    LOG.info("verify: consistent=%s profiles now %d (baseline %d + live %d), projects now %d, strangers %d",
             rep["consistent"], rep["routing_profiles"]["now"], rep["routing_profiles"]["baseline"],
             rep["routing_profiles"]["manifest_live"], rep["projects"]["now"], len(rep["strangers"]))
    return 0 if rep["consistent"] else 1


def step_export(a, sec, client) -> int:
    """S10, before any deletion: GET-only definitions of this run's resources (b14lib/export.py)."""
    out = export.export_all(client, Manifest(a.manifest, a.run_id), load_json(a.home / "state/baseline.json"),
                            _plan(a), a.run_id)
    outdir = a.home / "results/s10/definitions"
    for alias, doc in out["definitions"].items():
        write_json(outdir / f"{alias}.json", doc, sec)
    write_json(outdir / "index.json", {"format": export.FORMAT, "run_id": a.run_id, "taken_utc": utc(),
                                       "ok": out["ok"], "placeholders": out["placeholders"], "rows": out["rows"]}, sec)
    LOG.info("export: ok=%s written %s", out["ok"], sorted(out["definitions"]))
    return 0 if out["ok"] else 1


def step_status(a, sec, client) -> int:
    sys.stdout.write(json.dumps({"budget": Ledger(a.home / "state/budget.json").summary(),
                                 "manifest": Manifest(a.manifest, a.run_id).redacted()}, indent=1) + "\n")
    return 0


STEPS = {"preflight": step_preflight, "snapshot": step_snapshot, "plan": step_plan, "smoke": step_smoke,
         "create": step_create, "route": step_route, "responses": step_responses, "cleanup": step_cleanup,
         "verify": step_verify, "export": step_export, "status": step_status}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=sorted(STEPS))
    ap.add_argument("--run-id", required=True, help="e.g. b14-20261010a; fixed for the whole POC")
    ap.add_argument("--home", type=Path, default=Path("~/b14-poc").expanduser())
    ap.add_argument("--env", type=Path, default=SECRETS_DIR / "oracle.env")
    ap.add_argument("--key", type=Path, default=SECRETS_DIR / "oci_key.pem")
    ap.add_argument("--manifest", type=Path, default=SECRETS_DIR / "b14_manifest.json")
    ap.add_argument("--use-reserve", default=None,
                    help="responses only: draw from the reserve bucket, with this reason")
    a = ap.parse_args(argv)
    if a.use_reserve is not None and (a.step != "responses" or not a.use_reserve.strip()):
        ap.error("--use-reserve takes a non-empty reason and applies to the responses step only")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.Formatter.converter = time.gmtime
    os.umask(0o077)
    try:
        sec = Secrets.from_files(a.env, a.key)
        client = OciClient(sec)
        return STEPS[a.step](a, sec, client)
    except (phoenix.RegionLockError, BudgetExceeded, profiles.OwnershipError, export.ExportError) as exc:
        LOG.error("guard refused: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
