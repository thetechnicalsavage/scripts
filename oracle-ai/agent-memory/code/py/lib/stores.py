# v1.10 2026-10-12 Brief 12 PLAN 5.3, 11.4: open or recreate a store by id inside AGENTMEM, disable its two purge
#                  jobs right after, report the job runs the creation caused, and record every object the store owns
#                  in AGENTMEM.POC_MANIFEST (the rollback guard's list).
#                  v1.10: (phase 2, PLAN v0.3.6 11.4) a fourth name prefix, VECTOR$DR$<ID>_: the IVF centroid tables
#                  of a hybrid vector index's vector part (S4 second attempt, 10-Oct 19:17Z: RV_HYB's index created
#                  VECTOR$DR$RV_HYB_RECORD_CHUNKS_CONTENT_HVX_I$VI$..._IVF_FLAT_CENTROIDS and _CENTROID_PARTITIONS,
#                  which no rule owned, so they and their system-named indexes and LOBs were unlisted). Nothing else.
#                  v1.9: (Codex adversarial r1 of the APEX group, finding 2) a sweep never MERGES a 'POC' row: it only
#                  accepts POC_RESULT and its key when sql/84 already recorded them under 'POC' (84 v0.5 writes those
#                  rows before it creates the table); otherwise they are reported unlisted, so no POC_RESULT made by
#                  anything else can be adopted.
#                  v1.8: (day 1, APEX stage, PLAN v0.3.4 section 8) the results table POC_RESULT, which
#                  sql/84_results_table.sql creates and records under store_id 'POC', is owned by 'POC' (its primary-key
#                  index through the table closure), so the end-of-stage sweep of stage APEXG records it under the same
#                  id instead of reporting it unlisted, and manifest_conflicts() checks its rows like any other.
#                  v1.7: (day 1, S2's first attempt failed with ORA-27478 at 09:19 UTC: DEMO_AUTO's expired-records job
#                  was still RUNNING its creation-time run when DBMS_SCHEDULER.DISABLE was called) disable_purge_jobs()
#                  waits, bounded (120 s, polling every 2 s), while the job is RUNNING and retries the disable on
#                  ORA-27478 within the same bound; it never stops or kills the run; the seconds waited are reported.
#                  v1.6: (Codex r4 of step 1C, findings 1 and 2) a manifest row proves ownership only under the SAME
#                  store id: preopen_check() compares store_id, record_manifest() returns 'conflicts' (objects of the
#                  window already listed under another store id; open_store raises on any), and the new
#                  manifest_conflicts() lists every existing object whose 11.4 owner differs from its manifest row.
#                  v1.5: (Codex r2 of step 3, finding 1) sweep() never creates the manifest: it requires it
#                  (require_manifest) and record_manifest(create=False) issues no DDL; it only merges rows for objects
#                  that appeared in the window and are owned by a store (none in a read-only stage).
#                  v1.4: (Codex adversarial review r1 of step 1C, finding 1) store_objects() is read-only: it calls the
#                  new require_manifest() (raises when POC_MANIFEST or POC_MANIFEST_PK is missing) instead of
#                  ensure_manifest(), so the destructive step's snapshot can never create anything.
#                  v1.3: (day 1, after step 1's ORA-01031) store_objects(): every existing object a store id owns under
#                  the 11.4 closure, with its manifest entry, for py/11_drop_failed_store.py. No other change.
#                  v1.2: (Codex r2) preopen_check applies the full ownership closure (table-owned indexes, LOBs, LOB
#                  indexes, identity sequences), not only the name prefixes.
#                  v1.1: (Codex review r1 of the step 1 group, findings 2 and 3) only the two exact SDK job names are
#                  touched, and any other <ID>_PURGE_* job stops the open; before a creating open, every existing
#                  object a store id would claim must already be in the manifest (a POC leftover), or the open is
#                  refused before the SDK can drop or replace it. v1.0: first version (day 1).
"""Store lifecycle and the creation manifest.

open_store(): t0 = the database's sysdate minus one second (DATE granularity of USER_OBJECTS.created; never the client
clock), then the SDK client is built (RECREATE drops and recreates only this store's exact-named objects, PLAN 2),
then the store's own purge jobs <ID>_PURGE_*_J are disabled (5.3; the SDK creates them enabled with start_date =
systimestamp, so each runs once first: those runs are read from USER_SCHEDULER_JOB_RUN_DETAILS and reported), then
the objects created since t0 that belong to the store are merged into POC_MANIFEST.

Ownership (11.4): an object belongs to store S if its name starts with S_, DR$S_, VECTOR$S_ or (v1.10) VECTOR$DR$S_
(the vector part of a hybrid index on an S_ table: Oracle names its IVF centroid tables VECTOR$ followed by the Text
index's DR$ internal name); the longest known store id wins, so DEMO never claims DEMO_AUTO_*; or it is an index, LOB
segment or LOB index of an owned table
(USER_INDEXES, USER_LOBS: system-named primary-key indexes and LOBs), or the identity sequence of an owned table
(USER_TAB_IDENTITY_COLS), applied until nothing changes. The SDK's shared objects (AGENT_MEMORY_STORES and the two
packages with their bodies) are recorded under store_id 'SDK', with the registry's own indexes and LOBs; the results
table POC_RESULT (sql/84) under 'POC', with its key; a sweep only accepts those two when 84 already recorded them
and never records them itself. POC_MANIFEST and POC_MANIFEST_PK are the manifest's own two
objects. Anything else created in the window is NOT recorded: it is
returned as 'unlisted' so the step reports it, and the rollback guard would stop on it (a safe failure).
sweep() applies the same rules to every known store over a whole stage window (objects Oracle creates after an open,
such as HNSW checkpoint or journal tables).
"""
from __future__ import annotations

import datetime as dt
import logging
import time

LOG = logging.getLogger("poc.stores")

KNOWN_STORE_IDS = ("DEMO", "DEMO_AUTO", "RV_VEC", "RV_HYB", "RV_KEY", "AL_POST", "AL_DUR", "AL_OFF", "OPS", "SNIP")
POC_OWNED = frozenset({("POC_RESULT", "TABLE")})          # sql/84; its key POC_RESULT_PK follows the table
SDK_SHARED = frozenset({("AGENT_MEMORY_STORES", "TABLE"), ("DBMS_AGENT_MEMORY_STORE", "PACKAGE"),
                        ("DBMS_AGENT_MEMORY_STORE", "PACKAGE BODY"), ("DBMS_AGENT_MEMORY_STORE_INT", "PACKAGE"),
                        ("DBMS_AGENT_MEMORY_STORE_INT", "PACKAGE BODY")})
SELF = frozenset({("POC_MANIFEST", "TABLE"), ("POC_MANIFEST_PK", "INDEX")})

MANIFEST_DDL = """create table if not exists POC_MANIFEST (
  object_name  varchar2(128) not null,
  object_type  varchar2(30)  not null,
  store_id     varchar2(16)  not null,
  recorded_at  timestamp     not null,
  constraint POC_MANIFEST_PK primary key (object_name, object_type))"""

MERGE_SQL = """merge into POC_MANIFEST m
using (select :1 as object_name, :2 as object_type, :3 as store_id from dual) s
   on (m.object_name = s.object_name and m.object_type = s.object_type)
 when not matched then insert (object_name, object_type, store_id, recorded_at)
      values (s.object_name, s.object_type, s.store_id, systimestamp)"""


def owner_store(name: str, store_ids=KNOWN_STORE_IDS) -> str | None:
    for sid in sorted(store_ids, key=len, reverse=True):
        for prefix in (f"{sid}_", f"DR${sid}_", f"VECTOR${sid}_", f"VECTOR$DR${sid}_"):
            if name.startswith(prefix):
                return sid
    return None


def classify(objects, indexes, lobs, idents, store_ids=KNOWN_STORE_IDS):
    """Pure function (tested offline).
    objects: [(name, type)] created in the window; indexes: [(index_name, table_name)];
    lobs: [(segment_name, index_name, table_name)]; idents: [(table_name, sequence_name)].
    Returns (records [(name, type, store_id)], unlisted [(name, type)])."""
    owner: dict[str, str] = {}
    for name, typ in objects:
        sid = owner_store(name, store_ids)
        if sid:
            owner[name] = sid
        elif (name, typ) in SDK_SHARED:
            owner[name] = "SDK"
        elif (name, typ) in POC_OWNED:
            owner[name] = "POC"
    changed = True
    while changed:
        changed = False
        for idx, tab in indexes:
            if tab in owner and idx not in owner:
                owner[idx] = owner[tab]
                changed = True
        for seg, lidx, tab in lobs:
            if tab in owner:
                for n in (seg, lidx):
                    if n and n not in owner:
                        owner[n] = owner[tab]
                        changed = True
        for tab, seq in idents:
            if tab in owner and seq and seq not in owner:
                owner[seq] = owner[tab]
                changed = True
    records, unlisted = [], []
    for name, typ in objects:
        if (name, typ) in SELF:
            continue
        if name in owner:
            records.append((name, typ, owner[name]))
        else:
            unlisted.append((name, typ))
    return records, unlisted


def ensure_manifest(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(MANIFEST_DDL)


class ManifestMissing(RuntimeError):
    pass


def require_manifest(conn) -> None:
    """Read-only: POC_MANIFEST (TABLE) and POC_MANIFEST_PK (INDEX) must both exist; nothing is created."""
    with conn.cursor() as cur:
        cur.execute("select object_name, object_type from user_objects "
                    "where (object_name = 'POC_MANIFEST' and object_type = 'TABLE') "
                    "or (object_name = 'POC_MANIFEST_PK' and object_type = 'INDEX')")
        found = {tuple(r) for r in cur.fetchall()}
    if found != set(SELF):
        raise ManifestMissing(f"POC_MANIFEST is incomplete (found {sorted(found)}): nothing done, ask the operator")


def db_t0(conn) -> dt.datetime:
    with conn.cursor() as cur:
        cur.execute("select sysdate - 1/86400 from dual")
        return cur.fetchone()[0]


def _window(conn, t0):
    with conn.cursor() as cur:
        cur.execute("select object_name, object_type from user_objects where created >= :t0", t0=t0)
        objects = [tuple(r) for r in cur.fetchall()]
        cur.execute("select index_name, table_name from user_indexes")
        indexes = [tuple(r) for r in cur.fetchall()]
        cur.execute("select segment_name, index_name, table_name from user_lobs")
        lobs = [tuple(r) for r in cur.fetchall()]
        cur.execute("select table_name, sequence_name from user_tab_identity_cols")
        idents = [tuple(r) for r in cur.fetchall()]
    return objects, indexes, lobs, idents


def record_manifest(conn, t0, store_ids=KNOWN_STORE_IDS, *, create: bool = True) -> dict:
    if create:
        ensure_manifest(conn)                 # open_store: the first store open creates the manifest
    else:
        require_manifest(conn)                # sweeps: no DDL, ever
    objects, indexes, lobs, idents = _window(conn, t0)
    records, unlisted = classify(objects, indexes, lobs, idents, store_ids)
    # 'POC' objects (sql/84's table and key) are never recorded here: only 84 records them, before it creates them,
    # so one that 84 did not record is reported unlisted instead of being adopted (v1.9)
    listed_before = _listed(conn)
    poc = [(n, t, sid) for n, t, sid in records if sid == "POC"]
    to_merge = [r for r in records if r[2] != "POC"]
    unlisted = unlisted + [(n, t) for n, t, _ in poc if (n, t) not in listed_before]
    with conn.cursor() as cur:
        if to_merge:
            cur.executemany(MERGE_SQL, to_merge)
    conn.commit()
    listed = _listed(conn)
    conflicts = [(n, t, sid, listed.get((n, t))) for n, t, sid in to_merge if listed.get((n, t)) != sid]
    conflicts += [(n, t, sid, listed[(n, t)]) for n, t, sid in poc if (n, t) in listed and listed[(n, t)] != sid]
    if unlisted:
        LOG.warning("objects created in the window but not owned by a store: %s", unlisted)
    if conflicts:
        LOG.error("manifest rows under another store id (name, type, owner, listed as): %s", conflicts)
    return {"window_start_db": str(t0), "recorded": len(to_merge), "unlisted": unlisted,
            "by_store": _count(to_merge), "poc_checked": len(poc), "conflicts": conflicts}


def _listed(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("select object_name, object_type, store_id from poc_manifest")
        return {(r[0], r[1]): r[2] for r in cur.fetchall()}


def manifest_conflicts(conn, store_ids=KNOWN_STORE_IDS) -> list[tuple]:
    """Read-only: every existing AGENTMEM object whose 11.4 owner (name prefixes and the table closure) differs from
    the store_id its manifest row names (name, type, owner, listed as). Missing rows are unlisted_now()'s job."""
    require_manifest(conn)
    with conn.cursor() as cur:
        cur.execute("select object_name, object_type from user_objects")
        objects = [tuple(r) for r in cur.fetchall()]
        cur.execute("select index_name, table_name from user_indexes")
        indexes = [tuple(r) for r in cur.fetchall()]
        cur.execute("select segment_name, index_name, table_name from user_lobs")
        lobs = [tuple(r) for r in cur.fetchall()]
        cur.execute("select table_name, sequence_name from user_tab_identity_cols")
        idents = [tuple(r) for r in cur.fetchall()]
    listed = _listed(conn)
    records, _ = classify(objects, indexes, lobs, idents, store_ids)
    return sorted((n, t, sid, listed[(n, t)]) for n, t, sid in records
                  if (n, t) in listed and listed[(n, t)] != sid)


def _count(records) -> dict:
    out: dict[str, int] = {}
    for _, _, sid in records:
        out[sid] = out.get(sid, 0) + 1
    return out


def sdk_job_names(store_id: str) -> tuple[str, str]:
    """The two purge jobs the SDK creates per store (API_NOTES B13)."""
    return (f"{store_id}_PURGE_EXPIRED_RECORDS_J", f"{store_id}_PURGE_ORPHANED_CHUNKS_J")


def purge_jobs(conn, store_id: str) -> list[dict]:
    """The store's SDK purge jobs; any OTHER job named <ID>_PURGE_* is not ours to touch and stops the caller."""
    exact = sdk_job_names(store_id)
    with conn.cursor() as cur:
        cur.execute(r"select job_name, enabled, state, run_count from user_scheduler_jobs "
                    r"where job_name like :p escape '\' order by job_name",
                    p=store_id.replace("_", r"\_") + r"\_PURGE\_%")
        rows = cur.fetchall()
    foreign = [r[0] for r in rows if r[0] not in exact]
    if foreign:
        raise RuntimeError(f"jobs named like {store_id}'s purge jobs but not the SDK's two: {foreign}; "
                           "nothing touched: ask the operator")
    return [{"job_name": r[0], "enabled": r[1], "state": r[2], "run_count": r[3]} for r in rows]


def preopen_check(conn, store_id: str) -> list[tuple[str, str]]:
    """Before a creating open: every existing object the store id would claim (the full 11.4 closure: name prefixes,
    then table-owned indexes, LOBs, LOB indexes and identity sequences) must already be in the manifest (a leftover of
    an earlier POC run) UNDER THIS STORE ID; a row under another store id is not proof (v1.6). Returns the offending
    objects (empty list = safe)."""
    ensure_manifest(conn)
    with conn.cursor() as cur:
        cur.execute("select object_name, object_type from user_objects")
        objects = [tuple(r) for r in cur.fetchall()]
        cur.execute("select object_name, object_type, store_id from poc_manifest")
        listed = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        cur.execute("select index_name, table_name from user_indexes")
        indexes = [tuple(r) for r in cur.fetchall()]
        cur.execute("select segment_name, index_name, table_name from user_lobs")
        lobs = [tuple(r) for r in cur.fetchall()]
        cur.execute("select table_name, sequence_name from user_tab_identity_cols")
        idents = [tuple(r) for r in cur.fetchall()]
    records, _ = classify(objects, indexes, lobs, idents)
    return [(n, t) for n, t, sid in records if sid == store_id and listed.get((n, t)) != store_id]


def store_objects(conn, store_id: str) -> list[dict]:
    """Every EXISTING AGENTMEM object that store_id owns under the full 11.4 closure (name prefixes, then table-owned
    indexes, LOBs, LOB indexes and identity sequences), each with its object_id, status, last DDL time and the
    manifest's store_id for it (None when the manifest does not list it). Read-only: a missing manifest raises
    ManifestMissing (require_manifest), it is never created here."""
    require_manifest(conn)
    with conn.cursor() as cur:
        cur.execute("select object_name, object_type, object_id, status, "
                    "to_char(last_ddl_time, 'YYYY-MM-DD HH24:MI:SS') from user_objects")
        rows = {(r[0], r[1]): (r[2], r[3], r[4]) for r in cur.fetchall()}
        cur.execute("select object_name, object_type, store_id from poc_manifest")
        listed = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        cur.execute("select index_name, table_name from user_indexes")
        indexes = [tuple(r) for r in cur.fetchall()]
        cur.execute("select segment_name, index_name, table_name from user_lobs")
        lobs = [tuple(r) for r in cur.fetchall()]
        cur.execute("select table_name, sequence_name from user_tab_identity_cols")
        idents = [tuple(r) for r in cur.fetchall()]
    records, _ = classify(list(rows), indexes, lobs, idents)
    return [{"object_name": n, "object_type": t, "object_id": rows[(n, t)][0], "status": rows[(n, t)][1],
             "last_ddl_time": rows[(n, t)][2], "manifest_store_id": listed.get((n, t))}
            for n, t, sid in sorted(records) if sid == store_id]


JOB_RUNNING_WAIT_S = 120      # the creation-time run of a purge job on a new store takes seconds (S1: under 1 s)
JOB_POLL_S = 2
ORA_JOB_RUNNING = 27478       # ORA-27478: job "..." is running (DBMS_SCHEDULER.DISABLE without force)


def _job_state(conn, job_name: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("select state from user_scheduler_jobs where job_name = :j", j=job_name)
        row = cur.fetchone()
    return row[0] if row else None


def _is_job_running_error(exc: Exception) -> bool:
    """ORA-27478 from python-oracledb: the error object carries code 27478 and full_code 'ORA-27478'."""
    err = exc.args[0] if exc.args else None
    return getattr(err, "code", None) == ORA_JOB_RUNNING or getattr(err, "full_code", None) == "ORA-27478"


def disable_purge_jobs(conn, store_id: str, *, wait_s: float = JOB_RUNNING_WAIT_S, poll_s: float = JOB_POLL_S,
                       sleep=time.sleep) -> list[dict]:
    """Disable the store's two SDK purge jobs (PLAN 5.3). The SDK creates them enabled with start_date = systimestamp,
    so a job can still be RUNNING its creation-time run here; DISABLE (force FALSE) would then raise ORA-27478. The
    run is never stopped: we wait while the job is RUNNING and retry the disable on ORA-27478, both within wait_s
    seconds in total per job, then raise."""
    before = purge_jobs(conn, store_id)
    waited: dict[str, float] = {}
    for j in before:
        if j["enabled"] != "TRUE":
            continue
        spent = 0.0
        while True:
            if _job_state(conn, j["job_name"]) == "RUNNING":
                if spent >= wait_s:
                    raise RuntimeError(f"{j['job_name']} still RUNNING after {wait_s:.0f} s; not disabled: ask the "
                                       "operator")
                sleep(poll_s)
                spent += poll_s
                continue
            try:
                with conn.cursor() as cur:
                    cur.callproc("dbms_scheduler.disable", [j["job_name"]])
                break
            except Exception as exc:          # only ORA-27478 is retried; anything else propagates unchanged
                if not _is_job_running_error(exc) or spent >= wait_s:
                    raise
                sleep(poll_s)
                spent += poll_s
        waited[j["job_name"]] = spent
    after = purge_jobs(conn, store_id)
    if any(j["enabled"] == "TRUE" for j in after):
        raise RuntimeError(f"a purge job of {store_id} is still enabled")
    return [{"job_name": b["job_name"], "enabled_before": b["enabled"], "enabled_after": a["enabled"],
             "seconds_waited_for_running_job": waited.get(b["job_name"], 0.0)}
            for b, a in zip(before, after)]


def job_runs_since(conn, store_id: str, t0) -> list[dict]:
    with conn.cursor() as cur:
        # t0 is a DATE from sysdate (database host clock); log_date is a TIMESTAMP WITH TIME ZONE. The window is
        # therefore expressed as "the last (sysdate - t0) seconds" against systimestamp, both on the database.
        cur.execute(r"select job_name, status, error#, "
                    r"to_char(sys_extract_utc(actual_start_date), 'YYYY-MM-DD HH24:MI:SS') || ' UTC' "
                    r"from user_scheduler_job_run_details where job_name like :p escape '\' "
                    r"and log_date >= systimestamp - numtodsinterval((sysdate - :t0) * 86400 + 1, 'SECOND') "
                    r"order by log_date",
                    p=store_id.replace("_", r"\_") + r"\_PURGE\_%", t0=t0)
        return [{"job_name": r[0], "status": r[1], "error": r[2], "start": r[3]} for r in cur.fetchall()]


def open_store(pool, store_id: str, *, policy, embedder, llm=None, extraction_config=None, strategy=None,
               index_sync=None, search_config=None):
    """Build the client for store_id; with a creating policy also disable the purge jobs and record the manifest."""
    from oracleagentmemory.core import OracleAgentMemory, SchemaPolicy
    if store_id not in KNOWN_STORE_IDS:
        raise ValueError(f"store id {store_id} is not one of PLAN section 6's ids")
    with pool.acquire() as conn:
        ensure_manifest(conn)
        if policy != SchemaPolicy.REQUIRE_EXISTING:
            foreign = preopen_check(conn, store_id)
            if foreign:
                raise RuntimeError(f"AGENTMEM holds objects named for store {store_id} that the manifest does not "
                                   f"list: {foreign}; the SDK would drop or replace them: nothing done, ask the "
                                   "operator")
            purge_jobs(conn, store_id)          # raises on a foreign <ID>_PURGE_* job before anything changes
        t0 = db_t0(conn)
    kw = dict(connection=pool, embedder=embedder, llm=llm, schema_policy=policy, memory_store_id=store_id)
    if extraction_config is not None:
        kw["memory_extraction_config"] = extraction_config
    if strategy is not None:
        kw["search_strategy"] = strategy
    if index_sync is not None:
        kw["search_index_sync"] = index_sync
    if search_config is not None:
        kw["search_config"] = search_config
    client = OracleAgentMemory(**kw)
    info = {"store_id": store_id, "policy": getattr(policy, "name", str(policy))}
    with pool.acquire() as conn:
        if policy != SchemaPolicy.REQUIRE_EXISTING:
            info["purge_jobs"] = disable_purge_jobs(conn, store_id)
            info["purge_job_runs_since_open"] = job_runs_since(conn, store_id, t0)
            info["manifest"] = record_manifest(conn, t0)
            if info["manifest"]["conflicts"]:
                raise RuntimeError(f"objects of the open are listed in POC_MANIFEST under another store id: "
                                   f"{info['manifest']['conflicts']}; stop and ask the operator")
        else:
            info["purge_jobs"] = purge_jobs(conn, store_id)
    return client, info


def sweep(conn, t0) -> dict:
    """End-of-stage merge over the whole stage window, every known store id (11.4 v0.2). No DDL: the manifest must
    exist; rows are merged only for objects created in the window that a store owns."""
    return record_manifest(conn, t0, create=False)


def unlisted_now(conn) -> list[tuple[str, str]]:
    """Every AGENTMEM object the manifest does not list (what the rollback guard would stop on), self excluded."""
    with conn.cursor() as cur:
        cur.execute("""select o.object_name, o.object_type from user_objects o
                        where not (o.object_name = 'POC_MANIFEST' and o.object_type = 'TABLE')
                          and not (o.object_name = 'POC_MANIFEST_PK' and o.object_type = 'INDEX')
                          and not exists (select 1 from poc_manifest m
                                           where m.object_name = o.object_name and m.object_type = o.object_type)
                        order by 1, 2""")
        return [tuple(r) for r in cur.fetchall()]
