# v1.2 2026-10-10  Brief 12 day 1 (PLAN 6, 11.4; plan owner's decision of 10-Oct): remove what a FAILED store
#                  creation left, through the SDK's own drop, after proving from POC_MANIFEST that every object it
#                  would remove is the POC's.
#                  v1.2: (Codex adversarial review r2, finding 1) DEMO's own vectorizer preference must be absent at
#                  the end (after-check store_preference_absent); "already clean" also requires it absent, and an
#                  orphaned one without a registry row is refused (the SDK drop needs the row; ask the operator).
#                  v1.1: (Codex adversarial review r1, BLOCKING 3) the manifest is required, never created (stores v1.4);
#                  only store DEMO is accepted (the plan owner authorised DEMO's leftovers, nothing else); the Text /
#                  vectorizer preference check is exact: the only preference that may disappear is DEMO's own
#                  vectorizer name (pkb vectorizer_name: DEMO_RECORD_CHUNKS_VECZ) and none may appear.
#                  An error raised by the SDK's drop is recorded (type and text) and the after-checks still run.
#                  v1.0: first version (after step 1's ORA-01031 left store DEMO FAILED).
"""Run on the demo VM from ~/agentmem-poc/code with the POC venv, only through run_all.sh S1C:
    .venv/bin/python py/11_drop_failed_store.py DEMO

Why a separate step: step 1's first run failed inside the SDK's store creation (ORA-01031, no CREATE SEQUENCE), which
left the registry row DEMO in lifecycle FAILED and part of DEMO's tables. RECREATE would also drop them, but this step
removes them first with the evidence the rollback guard needs: what was there, that it was ours, that only it went.

Order (AGENTMEM's own schema only; nothing outside it is read or changed):
  1. Before: the registry row (AGENT_MEMORY_STORES, owner_id = CURRENT_USERID as the package writes it), every
     existing object the store id owns
     under the 11.4 closure (lib.stores.store_objects) with its manifest entry, every other AGENTMEM object with its
     object_id and last DDL time, the store's purge jobs (exact SDK names; a foreign <ID>_PURGE_* job stops here), its
     property graph, AGENTMEM's Oracle Text / vectorizer preferences (CTX_USER_PREFERENCES), the recycle bin, and every
     object the manifest does not list (lib.stores.unlisted_now). POC_MANIFEST and its index must exist
     (lib.stores.require_manifest): nothing here creates anything.
  2. Decision. Nothing registered and nothing owned: already clean, exit 0, no call. Otherwise every condition must
     hold, or nothing is dropped and the step exits 1 (ask the operator): the registry row exists and its lifecycle is
     FAILED (a healthy store is never dropped here); every owned object is listed in POC_MANIFEST under this store id;
     AGENTMEM has no object the manifest does not list; the recycle bin is empty.
  3. The SDK's own statement, PLSQL_DROP_MEMORY_STORE_SQL from the installed wheel
     (dbms_agent_memory_store.drop_memory_store): exact-named jobs, graph, tables with PURGE, the store's vectorizer
     preference, then the registry row (B14; pkb drop_managed_schema 4144-4161). The package commits.
  4. After, every check must hold or the step exits 1: no registry row; no object owned by the store id; every object
     that disappeared was one of the owned objects listed in step 1; every other object has the same object_id and
     last DDL time; no purge job or graph of the store; recycle bin empty; no Text/vectorizer preference appeared
     and the only one that may have gone is DEMO's own vectorizer preference; the SDK's two packages VALID; nothing
     unlisted. Only store DEMO is accepted (PLAN v0.3.3 step 1C).
POC_MANIFEST rows of the dropped objects are kept: the manifest lists what the POC created, and the rollback guard of
11.4 only asks whether each EXISTING object is listed, so a row without an object cannot widen it.
Results: staging/s1c/results/s1c_drop_failed_<ID>.json; transcript staging/s1c/transcripts/.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import runlog  # noqa: E402  (first: redaction before anything else)

RUN = runlog.start("s1c", tag="s1c_drop_failed")

from lib import amconf, stores  # noqa: E402

LITELLM = amconf.configure_litellm()          # the SDK import pulls litellm; switches asserted anyway

from oracleagentmemory.core._oraclememorystoreadmin import PLSQL_DROP_MEMORY_STORE_SQL  # noqa: E402

ALLOWED = ("DEMO",)        # PLAN v0.3.3 step 1C: the plan owner authorised DEMO's leftovers only
if len(sys.argv) != 2 or sys.argv[1] not in ALLOWED:
    RUN.log.error("usage: 11_drop_failed_store.py DEMO (no other store id is accepted)")
    sys.exit(2)
STORE = sys.argv[1]
SDK_PACKAGES = ("DBMS_AGENT_MEMORY_STORE", "DBMS_AGENT_MEMORY_STORE_INT")
# The SDK's vectorizer preference name for this store (pkb vectorizer_name: prefix || 'RECORD_CHUNKS_VECZ' when it
# fits in 30 bytes, which DEMO's does); drop_managed_schema removes it if it exists.
STORE_PREFERENCE = f"{STORE}_RECORD_CHUNKS_VECZ"
assert len(STORE_PREFERENCE.encode()) <= 30


def registry(cur) -> dict | None:
    cur.execute("select memory_store_name, lifecycle_state, schema_version, "
                "to_char(sys_extract_utc(updated_at), 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') "
                "from agent_memory_stores where owner_id = sys_context('userenv', 'current_userid') "
                "and memory_store_name = :s", s=STORE)
    rows = cur.fetchall()
    if len(rows) > 1:
        raise RuntimeError(f"{len(rows)} registry rows for {STORE}: ask the operator")
    return ({"memory_store_name": rows[0][0], "lifecycle_state": rows[0][1], "schema_version": rows[0][2],
             "updated_at_utc": rows[0][3]} if rows else None)


def snapshot(conn) -> dict:
    stores.require_manifest(conn)                 # raises before any read if the manifest is incomplete
    with conn.cursor() as cur:
        reg = registry(cur)
        cur.execute("select object_name, object_type, object_id, status, "
                    "to_char(last_ddl_time, 'YYYY-MM-DD HH24:MI:SS') from user_objects order by 1, 2")
        objects = {f"{r[0]}|{r[1]}": {"object_id": r[2], "status": r[3], "last_ddl_time": r[4]}
                   for r in cur.fetchall()}
        cur.execute("select graph_name from user_property_graphs where graph_name = :g", g=f"{STORE}_MEMORY_GRAPH")
        graphs = [r[0] for r in cur.fetchall()]
        cur.execute("select count(*) from user_recyclebin")
        recyclebin = cur.fetchone()[0]
        cur.execute("select pre_name, pre_class from ctx_user_preferences order by 1")
        prefs = [f"{r[0]}|{r[1]}" for r in cur.fetchall()]
        cur.execute("select object_name, object_type, status from user_objects where object_name in (:a, :b) "
                    "order by 1, 2", a=SDK_PACKAGES[0], b=SDK_PACKAGES[1])
        packages = [list(r) for r in cur.fetchall()]
    return {"registry": reg, "owned": stores.store_objects(conn, STORE), "objects": objects,
            "purge_jobs": stores.purge_jobs(conn, STORE), "graphs": graphs, "recyclebin": recyclebin,
            "text_and_vectorizer_preferences": prefs, "sdk_packages": packages,
            "unlisted_now": stores.unlisted_now(conn)}


pool = amconf.make_pool("AGENTMEM", 1, 1)
out: dict = {"store": STORE, "sdk_statement": " ".join(PLSQL_DROP_MEMORY_STORE_SQL.split())}
rc = 0
try:
    with pool.acquire() as conn:
        before = snapshot(conn)
        out["before"] = before
        owned_keys = {f"{o['object_name']}|{o['object_type']}" for o in before["owned"]}
        RUN.transcript(f"## Step 1C: failed store {STORE}")
        RUN.transcript(f"- registry before: {before['registry']}")
        RUN.transcript(f"- owned objects before: {len(before['owned'])}; AGENTMEM objects: {len(before['objects'])}")
        refusals = []
        store_pref_before = any(x.split("|", 1)[0] == STORE_PREFERENCE
                                for x in before["text_and_vectorizer_preferences"])
        if before["registry"] is None and not before["owned"] and not store_pref_before:
            out["decision"] = "already clean: no registry row, no object and no preference of the store; nothing called"
        else:
            if before["registry"] is None:
                refusals.append("objects or the vectorizer preference of the store exist but no registry row: the SDK "
                                "drop cannot run")
            elif before["registry"]["lifecycle_state"] != "FAILED":
                refusals.append(f"registry lifecycle is {before['registry']['lifecycle_state']}, not FAILED: "
                                "a store that is not FAILED is never dropped here")
            not_ours = [o for o in before["owned"] if o["manifest_store_id"] != STORE]
            if not_ours:
                refusals.append(f"owned objects not listed in POC_MANIFEST under {STORE}: {not_ours}")
            if before["unlisted_now"]:
                refusals.append(f"AGENTMEM objects the manifest does not list: {before['unlisted_now']}")
            if before["recyclebin"]:
                refusals.append(f"recycle bin holds {before['recyclebin']} object(s)")
            if refusals:
                out["decision"] = "refused: nothing dropped"
                out["refusals"] = refusals
                rc = 1
            else:
                try:
                    with conn.cursor() as cur:
                        cur.execute(PLSQL_DROP_MEMORY_STORE_SQL, memory_store_id=STORE)
                    conn.commit()                   # the package commits itself; this ends our transaction
                    out["decision"] = "dropped through the SDK's drop_memory_store"
                    RUN.transcript(f"- called the SDK's drop_memory_store for {STORE}")
                except Exception as exc:            # the package sets FAILED and re-raises; recorded, then checked
                    conn.rollback()
                    out["decision"] = "the SDK's drop_memory_store raised: see drop_error"
                    out["drop_error"] = {"type": type(exc).__name__, "text": str(exc)[:1000]}
                    RUN.transcript(f"- drop_memory_store raised {type(exc).__name__}")
                    rc = 1
        after = snapshot(conn)
        out["after"] = after
        gone = sorted(set(before["objects"]) - set(after["objects"]))
        appeared = sorted(set(after["objects"]) - set(before["objects"]))
        changed = sorted(k for k in set(before["objects"]) & set(after["objects"])
                         if k not in owned_keys
                         and (before["objects"][k]["object_id"], before["objects"][k]["last_ddl_time"])
                         != (after["objects"][k]["object_id"], after["objects"][k]["last_ddl_time"]))
        checks = {
            "registry_row_gone": after["registry"] is None,
            "no_object_of_the_store_left": not after["owned"],
            "only_owned_objects_disappeared": set(gone) <= owned_keys,
            "nothing_appeared": not appeared,
            "other_objects_unchanged": not changed,
            "no_purge_job_or_graph_left": not after["purge_jobs"] and not after["graphs"],
            "recyclebin_empty": after["recyclebin"] == 0,
            "no_preference_appeared": not (set(after["text_and_vectorizer_preferences"])
                                           - set(before["text_and_vectorizer_preferences"])),
            "store_preference_absent": all(x.split("|", 1)[0] != STORE_PREFERENCE
                                           for x in after["text_and_vectorizer_preferences"]),
            "only_the_store_preference_may_go": all(
                x.split("|", 1)[0] == STORE_PREFERENCE
                for x in set(before["text_and_vectorizer_preferences"])
                - set(after["text_and_vectorizer_preferences"])),
            "sdk_packages_valid": len(after["sdk_packages"]) == 4
                                  and all(p[2] == "VALID" for p in after["sdk_packages"]),
            "nothing_unlisted": not after["unlisted_now"],
        }
        out["dropped"] = gone
        out["appeared"] = appeared
        out["changed_other_objects"] = changed
        out["checks"] = checks
        if rc == 0 and not all(checks.values()):
            rc = 1
        RUN.transcript(f"- decision: {out['decision']}")
        for kk, v in checks.items():
            RUN.transcript(f"- check {kk}: {v}")
        RUN.transcript(f"- dropped ({len(gone)}): {gone}")
finally:
    pool.close()

out["manifest_rows_kept"] = ("POC_MANIFEST rows of the dropped objects are kept (what the POC created); the 11.4 "
                             "guard only asks whether each existing object is listed")
RUN.write_json("results", f"s1c_drop_failed_{STORE}.json", out)
RUN.finish({"store": STORE, "decision": out.get("decision"), "refusals": out.get("refusals", []),
            "dropped": len(out.get("dropped", [])), "checks_all_true": all(out.get("checks", {}).values()) if
            out.get("checks") else None, "exit": rc})
sys.exit(rc)
