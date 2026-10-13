# v1.1 2026-10-12  Brief 12 step 8 (PLAN v0.3.8 6 row 8, 11.2, 11.3, 5.3): operations on store OPS: a durable thread
#                  summary written by client A and read by client B in a new process, the runtime user AGENTMEM_RT
#                  under the docs' grant list A and then with list B added, the registry and `oam check-plsql`, and an
#                  already-expired memory hidden from search, then removed by OPS's own purge job (enabled, run once,
#                  disabled).
#                  v1.1: (Codex 35 r1, BLOCKING 4, all accepted) (1) the expired-records job is enabled only when its
#                  next scheduled run is more than 10 minutes away, so the scheduler cannot fire it beside the manual
#                  run; otherwise it is run while disabled (recorded); its runs in the window are counted (expected 1);
#                  (2) the runtime user's operations no longer depend on each other: setup writes, as AGENTMEM, one
#                  fixture memory and one fixture link per list, so link_records and delete_record_link are tried
#                  whatever add_memory did (get_summary still needs get_thread's thread object); (3) error messages are
#                  kept verbatim (lib/opsrun.py v1.1); (4) the purge cleanup always ends with a direct disable of both
#                  exact OPS jobs and a final read-back, and fails the phase if either is still enabled.
#                  v1.0: first version (phase 2).
"""Step 8, one phase per process (run_all.sh stage S8 runs them in this order, with the SQL steps between):
    .venv/bin/python py/80_operations.py setup      # AGENTMEM, LLM attached: OPS recreated; thread of 12 messages;
                                                    #   client A's get_summary(); two seed memories and one link;
                                                    #   the expired memory; search must not return it
    (sql/81_registry_jobs.sql as AGENTMEM: the "before" render)
    .venv/bin/python py/80_operations.py reader     # AGENTMEM, a NEW process (client B), LLM attached: get_thread +
                                                    #   get_summary; same text as A's? LLM calls in B (prediction 0)
    (sql/82a_runtime_grants_docs.sql as AGENTMEM: list A)
    .venv/bin/python py/80_operations.py rt A       # AGENTMEM_RT, schema_owner='AGENTMEM', REQUIRE_EXISTING: search,
                                                    #   search num_hops=1, add_memory, link_records (from setup's
                                                    #   fixture memory), delete_record_link (setup's fixture link),
                                                    #   add_image (with a description), list_images, get_thread,
                                                    #   get_summary: each tried on its own; ok or the error chain
                                                    #   verbatim (Oracle codes extracted)
    (sql/82b_runtime_grants_extra.sql as AGENTMEM: list B added)
    .venv/bin/python py/80_operations.py rt B       # the same operations, new ids
    .venv/bin/python py/80_operations.py ops        # AGENTMEM, no LLM: registry rows; oam check-plsql (password on
                                                    #   stdin); the runtime user's rows; the expired memory before,
                                                    #   OPS_PURGE_EXPIRED_RECORDS_J enabled (only if its next run is
                                                    #   over 10 min away), run once in this session, disabled; the
                                                    #   memory and its chunks after; runs of the job in the window
    (sql/81_registry_jobs.sql again: the "after" render)
Nothing outside store OPS is touched. OPS is recreated by id (lib.stores.open_store: guarded, purge jobs disabled,
manifest recorded); only the ops phase enables OPS's expired-records job, for one run, and disables it again (5.3).
Every operation's outcome is reported as found; nothing is retried to change an outcome. A refused runtime-user
operation is a finding, not a failure of the step; the step fails only on a precondition (wrong privilege set, a
missing summary or store) or a database error outside the measured operations.
Results: staging/s8/results/s8_<phase>.json; transcripts staging/s8/transcripts/s8_<phase>_transcript.md.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import runlog  # noqa: E402  (first: redaction before anything else)

USAGE = "usage: 80_operations.py setup | reader | rt A | rt B | ops\n"
ARGS = sys.argv[1:]
if not ARGS or ARGS[0] not in ("setup", "reader", "rt", "ops") or (ARGS[0] == "rt") != (len(ARGS) == 2) \
        or (ARGS[0] == "rt" and ARGS[1] not in ("A", "B")) or len(ARGS) > 2:
    sys.stderr.write(USAGE)
    sys.exit(2)
PHASE = ARGS[0]
LIST = ARGS[1] if PHASE == "rt" else ""
TAG = f"s8_{PHASE}{'_' + LIST if LIST else ''}"
RUN = runlog.start("s8", tag=TAG, need_oci=PHASE in ("setup", "reader", "rt"))

from lib import amconf, meter, opsrun, redact, stores  # noqa: E402

LITELLM = amconf.configure_litellm()

from oracleagentmemory.apis.ttl import TimeToLiveAnchor  # noqa: E402
from oracleagentmemory.core import (MemoryExtractionConfig, OracleAgentMemory, SchemaPolicy,  # noqa: E402
                                    SearchStrategy)
from oracleagentmemory.observability import register_event_listeners  # noqa: E402

CODE = Path(__file__).resolve().parents[1]
STORE = "OPS"
USER = "OPS_CUST_01"
AGENT = "ops_agent"
THREAD = "ops_thread_01"
SETUP_JSON = amconf.STAGING / "s8" / "results" / "s8_setup.json"
IMAGE = CODE / "data" / "frozen" / "images" / "vision_check_64.png"      # a synthetic 64x64 PNG (frozen, day 0)
IMAGE_DESC = "Synthetic 64x64 test card used only for the runtime-user check of step 8."
EXPIRED_ID = "ops_expired_m1"
EXPIRED_TEXT = "Temporary promo code OPS-TMP-7 gave one free day of roaming; it has lapsed."
EXPIRED_Q = "promo code OPS-TMP-7 free roaming day"
SEARCH_Q = "Which router does the customer have now?"
EXPIRED_JOB, ORPHAN_JOB = stores.sdk_job_names(STORE)
SEEDS = [("ops_seed_m1", "The customer's router was replaced with model RT-9 on 2 October 2026."),
         ("ops_seed_m2", "The customer confirmed on 4 October 2026 that the RT-9 router works.")]
SEED_LINK = ("ops_seed_l1", "ops_seed_m2", "ops_seed_m1", "supports")
# one fixture memory and one fixture link per runtime-user list, written by AGENTMEM in setup (Codex 35 r1, 2):
# rt L links FROM ops_fix_L_m1 TO ops_seed_m2 (its own new link) and deletes the fixture link ops_fix_L_l1
FIXTURES = {lst: (f"ops_fix_{lst.lower()}_m1", f"ops_fix_{lst.lower()}_l1") for lst in ("A", "B")}
ENABLE_MARGIN_MIN = 10        # enable the purge job only if its next scheduled run is further away than this
MESSAGES = [  # synthetic, one thread of 12 messages (PLAN 3, store OPS)
    {"role": "user", "content": "Hi, my home internet keeps dropping every evening since last week."},
    {"role": "assistant", "content": "Sorry about that. Is the router showing a red light when it drops?"},
    {"role": "user", "content": "Yes, the light turns red for a minute and then goes green again."},
    {"role": "assistant", "content": "That points to the router. I can book a replacement RT-9 router for you."},
    {"role": "user", "content": "Please do. Can it arrive before Friday?"},
    {"role": "assistant", "content": "It is booked for delivery on 2 October 2026, before Friday."},
    {"role": "user", "content": "Great. Also, will I be charged for the replacement?"},
    {"role": "assistant", "content": "No charge: the old router is still under warranty."},
    {"role": "user", "content": "Good. I will be roaming in the GCC next month, anything I should know?"},
    {"role": "assistant", "content": "Your plan includes the GCC roaming add-on RMG-GCC-7D at QAR 35 for 7 days."},
    {"role": "user", "content": "OK, please send me the details on WhatsApp, not by call."},
    {"role": "assistant", "content": "Done: details sent on WhatsApp, and I noted you prefer WhatsApp to calls."},
]
FATAL = (meter.BudgetExceeded,)
R = redact.redact


class StepStop(RuntimeError):
    """A precondition failed: the phase exits 1 (the stage stops)."""


def no_extraction() -> MemoryExtractionConfig:
    return MemoryExtractionConfig(extract_memories=False)


def llm():
    return amconf.make_llm(structured_reminder=True if amconf.structured_reminder_needed() else None)


def summary_rows(pool) -> list[dict]:
    with pool.acquire() as c, c.cursor() as cur:
        cur.execute("""select record_id, thread_id, status,
                              json_serialize(content returning varchar2(32000) truncate error on error),
                              json_serialize(metadata returning varchar2(4000) truncate error on error),
                              to_char(sys_extract_utc(created_at), 'YYYY-MM-DD"T"HH24:MI:SS.FF3"Z"')
                         from OPS_THREAD_SUMMARY where thread_id = :t order by created_at, record_id""", t=THREAD)
        return [{"record_id": r[0], "thread_id": r[1], "status": r[2], "content_json": r[3], "metadata_json": r[4],
                 "created_at_utc": r[5]} for r in cur.fetchall()]


def memory_rows(pool, like: str = "%") -> list[dict]:
    with pool.acquire() as c, c.cursor() as cur:
        cur.execute("""select m.record_id, m.memory_type, m.status,
                              to_char(sys_extract_utc(m.expires_at), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                              case when m.expires_at is not null and m.expires_at <= systimestamp then 'yes' else 'no'
                              end,
                              (select count(*) from OPS_RECORD_CHUNKS c where c.source_id = m.record_id)
                         from OPS_MEMORY m where m.record_id like :p order by m.record_id""", p=like)
        return [{"record_id": r[0], "memory_type": r[1], "status": r[2], "expires_at_utc": r[3], "expired": r[4],
                 "chunks": r[5]} for r in cur.fetchall()]


def chunk_count(pool, source_id: str) -> int:
    with pool.acquire() as c, c.cursor() as cur:
        cur.execute("select count(*) from OPS_RECORD_CHUNKS where source_id = :s", s=source_id)
        return int(cur.fetchone()[0])


def ids_of(results) -> list[str]:
    return [str(r.id) for r in results]


def hops_summary(results) -> dict:
    return {"ids": ids_of(results),
            "linked": {str(r.id): [getattr(rel, "relation_type", None)
                                   for rel, _ in (getattr(r, "linked_results", None) or [])] for r in results}}


m = meter.Meter()
ledger = meter.Ledger(amconf.STATE / "token_ledger.json", amconf.TOKEN_BUDGET)
lc = meter.LitellmCounter(meter=m, ledger=ledger)
lc.install()


def calls() -> int:
    c = lc.summary()["calls"]
    return int(c.get("completion", 0)) + int(c.get("acompletion", 0))


# ---------------------------------------------------------------------------------------------------- phases
def phase_setup(out: dict) -> None:
    pool = amconf.make_pool("AGENTMEM", 1, 4)
    client = None
    try:
        emb = amconf.make_embedder(pool)
        client, info = stores.open_store(pool, STORE, policy=SchemaPolicy.RECREATE, embedder=emb, llm=llm(),
                                         strategy=SearchStrategy.VECTOR, extraction_config=no_extraction())
        out["ops_recreate"] = info
        c0 = calls()
        thread = client.create_thread(thread_id=THREAD, user_id=USER, agent_id=AGENT)
        out["messages_added"] = len(thread.add_messages(MESSAGES))
        out["llm_calls_create_and_add_messages"] = calls() - c0
        out["summary_rows_before_get_summary"] = summary_rows(pool)
        holder: dict = {}
        rec = opsrun.run_op("client A get_summary()", lambda: holder.setdefault("s", thread.get_summary()),
                            summarize=lambda s: {"content_chars": len(s.content or "")}, calls=calls, fatal=FATAL,
                            redact=R)
        out["summary_a"] = {**rec, "content": holder["s"].content if "s" in holder else None}
        out["summary_rows_after_a"] = summary_rows(pool)
        RUN.transcript(f"- client A: {out['messages_added']} messages added ({out['llm_calls_create_and_add_messages']}"
                       f" LLM calls), get_summary {'ok' if rec['ok'] else 'FAILED'} with {rec.get('llm_calls')} LLM "
                       f"call(s), {len(out['summary_rows_after_a'])} THREAD_SUMMARY row(s)")
        for mid, text in SEEDS:
            client.add_memory(text, memory_type="fact", user_id=USER, agent_id=AGENT, memory_id=mid,
                              autonomous_linking=False)
        lid, src, dst, typ = SEED_LINK
        client.link_records(source_record_id=src, source_record_type="fact", target_record_id=dst,
                            target_record_type="fact", relation_type=typ, relation_id=lid)
        for lst, (fmid, flid) in FIXTURES.items():
            client.add_memory(f"Fixture for runtime-user list {lst}: the RT-9 router sits in the hallway.",
                              memory_type="fact", user_id=USER, agent_id=AGENT, memory_id=fmid,
                              autonomous_linking=False)
            client.link_records(source_record_id=fmid, source_record_type="fact", target_record_id="ops_seed_m1",
                                target_record_type="fact", relation_type="supports", relation_id=flid)
        out["fixtures"] = {lst: {"memory_id": fmid, "link_id": flid} for lst, (fmid, flid) in FIXTURES.items()}
        ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
        out["expired_add"] = opsrun.run_op(
            "add_memory(expired: timestamp 3 days ago, ttl_days=1, ttl_anchor=TIMESTAMP)",
            lambda: client.add_memory(EXPIRED_TEXT, memory_type="fact", user_id=USER, agent_id=AGENT,
                                      memory_id=EXPIRED_ID, timestamp=ts, ttl_days=1,
                                      ttl_anchor=TimeToLiveAnchor.TIMESTAMP, autonomous_linking=False),
            summarize=str, calls=calls, fatal=FATAL, redact=R)
        out["expired_add"]["timestamp_passed"] = ts
        out["memories"] = memory_rows(pool)
        res = client.search(EXPIRED_Q, user_id=USER, max_results=10)
        out["expired_search"] = {"query": EXPIRED_Q, "returned_ids": ids_of(res),
                                 "expired_returned": EXPIRED_ID in ids_of(res)}
        res2 = client.search(SEARCH_Q, user_id=USER, max_results=10)
        out["seed_search"] = {"query": SEARCH_Q, "returned_ids": ids_of(res2)}
        RUN.transcript(f"- expired memory: add {'ok' if out['expired_add']['ok'] else 'REFUSED'}; rows "
                       f"{[(r['record_id'], r['expired'], r['chunks']) for r in out['memories']]}; returned by "
                       f"search: {out['expired_search']['expired_returned']}")
        if not out["summary_a"]["ok"] or not out["summary_a"]["content"]:
            raise StepStop("client A wrote no summary: the reader phase has nothing to compare")
    finally:
        if client is not None:
            client.close()
        pool.close()


def phase_reader(out: dict) -> None:
    try:
        setup = json.loads(SETUP_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise StepStop("staging/s8/results/s8_setup.json missing or unreadable: run the setup phase first") from None
    text_a = (setup.get("summary_a") or {}).get("content")
    if not text_a:
        raise StepStop("the setup phase recorded no summary text")
    pool = amconf.make_pool("AGENTMEM", 1, 4)
    client = None
    try:
        client, info = stores.open_store(pool, STORE, policy=SchemaPolicy.REQUIRE_EXISTING,
                                         embedder=amconf.make_embedder(pool), llm=llm(),
                                         strategy=SearchStrategy.VECTOR, extraction_config=no_extraction())
        out["ops_open"] = info
        before = summary_rows(pool)
        holder: dict = {}
        rec_t = opsrun.run_op("client B get_thread()", lambda: holder.setdefault("t", client.get_thread(THREAD)),
                              summarize=lambda t: "loaded", calls=calls, fatal=FATAL, redact=R)
        rec_s = {"op": "client B get_summary()", "ok": False, "skipped": "get_thread failed"}
        if rec_t["ok"]:
            rec_s = opsrun.run_op("client B get_summary()", lambda: holder.setdefault("s", holder["t"].get_summary()),
                                  summarize=lambda s: {"content_chars": len(s.content or "")}, calls=calls,
                                  fatal=FATAL, redact=R)
        text_b = holder["s"].content if "s" in holder else None
        after = summary_rows(pool)
        out["get_thread"] = rec_t
        out["get_summary"] = {**rec_s, "content": text_b}
        out["same_text_as_a"] = text_b == text_a
        out["llm_calls_in_b"] = calls()
        out["prediction_zero_llm_calls"] = {"predicted": 0, "found": out["llm_calls_in_b"],
                                            "confirmed": out["llm_calls_in_b"] == 0}
        out["summary_rows_unchanged"] = before == after
        out["summary_rows"] = after
        RUN.transcript(f"- client B (new process): get_summary {'ok' if rec_s.get('ok') else 'FAILED'}; same text as "
                       f"A: {out['same_text_as_a']}; LLM calls in B: {out['llm_calls_in_b']} (prediction 0); "
                       f"THREAD_SUMMARY rows unchanged: {out['summary_rows_unchanged']}")
    finally:
        if client is not None:
            client.close()
        pool.close()


def rt_privileges(rt_pool) -> list[tuple[str, str]]:
    with rt_pool.acquire() as c, c.cursor() as cur:
        cur.execute("select privilege, table_name from user_tab_privs where grantee = 'AGENTMEM_RT' "
                    "and owner = 'AGENTMEM' order by table_name, privilege")
        return [(r[0], r[1]) for r in cur.fetchall()]


def phase_rt(out: dict) -> None:
    lo = LIST.lower()
    expected = opsrun.list_a() if LIST == "A" else (opsrun.list_a() | opsrun.list_b())
    try:
        setup = json.loads(SETUP_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise StepStop("staging/s8/results/s8_setup.json missing: run the setup phase first") from None
    text_a = (setup.get("summary_a") or {}).get("content")
    rt_pool = amconf.make_pool("AGENTMEM_RT", 1, 2)
    client = None
    try:
        found = rt_privileges(rt_pool)
        diff = opsrun.privilege_diff(found, expected)
        out["privileges"] = {"list": LIST, "held_from_agentmem": [list(x) for x in found], "expected_rows":
                             len(expected), "missing": [list(x) for x in diff["missing"]],
                             "extra": [list(x) for x in diff["extra"]]}
        if diff["missing"] or diff["extra"]:
            raise StepStop(f"AGENTMEM_RT does not hold exactly list {LIST}: {diff}")
        holder: dict = {}
        opened = opsrun.run_op(
            "OracleAgentMemory(connection=AGENTMEM_RT pool, schema_owner='AGENTMEM', memory_store_id='OPS', "
            "REQUIRE_EXISTING)",
            lambda: holder.setdefault("c", OracleAgentMemory(
                connection=rt_pool, embedder=amconf.make_embedder(rt_pool), llm=llm(),
                schema_policy=SchemaPolicy.REQUIRE_EXISTING, memory_store_id=STORE, schema_owner="AGENTMEM",
                search_strategy=SearchStrategy.VECTOR, memory_extraction_config=no_extraction())),
            summarize=lambda c: "opened", calls=calls, fatal=FATAL, redact=R)
        ops = [opened]
        client = holder.get("c")
        mem_id, link_id, img_id = f"ops_rt_{lo}_m1", f"ops_rt_{lo}_l1", f"ops_rt_{lo}_img1"
        fix_mem, fix_link = FIXTURES[LIST]
        png = IMAGE.read_bytes()

        def op(name, fn, summarize=None, needs=()):
            if client is None:
                ops.append({"op": name, "ok": False, "skipped": "the client did not open"})
                return
            failed = [n for n in needs if not next((o for o in ops if o["op"] == n), {}).get("ok")]
            if failed:
                ops.append({"op": name, "ok": False, "skipped": f"depends on {failed}"})
                return
            ops.append(opsrun.run_op(name, fn, summarize=summarize, calls=calls, fatal=FATAL, redact=R))

        op("search", lambda: client.search(SEARCH_Q, user_id=USER, max_results=10), ids_of)
        op("search num_hops=1", lambda: client.search(SEARCH_Q, user_id=USER, max_results=10, num_hops=1),
           hops_summary)
        op("add_memory", lambda: client.add_memory(f"Runtime user {LIST} note: the RT-9 router was set to "
                                                   "the 5 GHz band.", memory_type="fact", user_id=USER,
                                                   agent_id=AGENT, memory_id=mem_id, autonomous_linking=False), str)
        op("link_records", lambda: client.link_records(source_record_id=fix_mem, source_record_type="fact",
                                                       target_record_id="ops_seed_m2", target_record_type="fact",
                                                       relation_type="supports", relation_id=link_id), str)
        op("delete_record_link", lambda: client.delete_record_link(relation_id=fix_link), str)
        op("add_image", lambda: client.add_image(png, description=IMAGE_DESC, image_id=img_id, user_id=USER), str)
        op("list_images", lambda: client.list_images(user_id=USER, include_bytes=False),
           lambda lst: [str(i.id) for i in lst])
        op("get_thread", lambda: holder.setdefault("t", client.get_thread(THREAD)), lambda t: "loaded")
        op("get_summary", lambda: holder.setdefault("s", holder["t"].get_summary()),
           lambda s: {"content_chars": len(s.content or ""), "same_text_as_a": s.content == text_a},
           needs=("get_thread",))
        out["operations"] = ops
        out["llm_calls_total"] = calls()
        failed = [o["op"] for o in ops if not o.get("ok")]
        out["failed_operations"] = failed
        for o in ops:
            chain = o.get("error_chain") or []
            RUN.transcript(f"- list {LIST} {o['op']}: " + ("ok" if o.get("ok") else
                           f"skipped ({o['skipped']})" if o.get("skipped") else
                           f"FAILED {o.get('oracle_codes')} {chain[0]['type'] if chain else ''}: "
                           f"{chain[0]['message'][:300] if chain else ''}")
                           + (f" [LLM calls {o['llm_calls']}]" if o.get("llm_calls") else ""))
    finally:
        if client is not None:
            client.close()
        rt_pool.close()


def run_oam_check() -> dict:
    exe = Path(sys.executable).parent / "oam"
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORACLE_MEMORY_DB_")}
    pw = amconf.db_password("AGENTMEM")
    t = time.perf_counter()
    try:
        p = subprocess.run([str(exe), "--user", "AGENTMEM", "--dsn", amconf.DSN, "--password-stdin", "check-plsql"],
                           input=pw + "\n", capture_output=True, text=True, timeout=180, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {R(str(exc))[:500]}"}
    finally:
        pw = ""
    return {"ok": p.returncode == 0, "returncode": p.returncode, "stdout": R(p.stdout)[-3000:],
            "stderr": R(p.stderr)[-3000:], "seconds": round(time.perf_counter() - t, 2),
            "command": "oam --user AGENTMEM --dsn <alias> --password-stdin check-plsql"}


def registry(pool) -> list[dict]:
    with pool.acquire() as c, c.cursor() as cur:
        cur.execute("""select memory_store_name, lifecycle_state, schema_version, owner_id,
                              to_char(sys_extract_utc(created_at), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                              to_char(sys_extract_utc(updated_at), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                              dbms_lob.substr(store_config, 3000, 1)
                         from AGENT_MEMORY_STORES order by memory_store_name""")
        return [{"memory_store_name": r[0], "lifecycle_state": r[1], "schema_version": r[2], "owner_id": r[3],
                 "created_at_utc": r[4], "updated_at_utc": r[5], "store_config": r[6]} for r in cur.fetchall()]


def phase_ops(out: dict) -> None:
    pool = amconf.make_pool("AGENTMEM", 1, 4)
    client = None
    try:
        out["registry"] = registry(pool)
        out["oam_check_plsql"] = run_oam_check()
        client, info = stores.open_store(pool, STORE, policy=SchemaPolicy.REQUIRE_EXISTING,
                                         embedder=amconf.make_embedder(pool), llm=None,
                                         strategy=SearchStrategy.VECTOR, extraction_config=no_extraction())
        out["ops_open"] = info
        with pool.acquire() as c, c.cursor() as cur:
            cur.execute("select record_id, memory_type, status from OPS_MEMORY where record_id like 'ops\\_rt\\_%' "
                        "escape '\\' order by record_id")
            out["rt_memories"] = [list(r) for r in cur.fetchall()]
            cur.execute("select relation_id, relation_type from OPS_MEMORY_LINK order by relation_id")
            out["links_now"] = [list(r) for r in cur.fetchall()]
            cur.execute("select record_id, document_type, dbms_lob.getlength(blob) from OPS_DOCUMENT "
                        "order by record_id")
            out["documents_now"] = [list(r) for r in cur.fetchall()]
        before = memory_rows(pool)
        exp = [r for r in before if r["record_id"] == EXPIRED_ID]
        out["expired_before"] = exp[0] if exp else None
        if not exp:
            out["purge"] = {"skipped": "no expired memory was stored in the setup phase"}
            return
        res = client.search(EXPIRED_Q, user_id=USER, max_results=10)
        out["expired_search_before"] = {"returned_ids": ids_of(res), "expired_returned": EXPIRED_ID in ids_of(res)}
        with pool.acquire() as c:
            t0 = stores.db_t0(c)
            jobs_before = stores.purge_jobs(c, STORE)
            if any(j["enabled"] == "TRUE" for j in jobs_before):
                raise StepStop(f"an OPS purge job is enabled before the manual run: {jobs_before}")
            with c.cursor() as cur:
                cur.execute("""select to_char(sys_extract_utc(next_run_date), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                                      case when next_run_date > systimestamp + numtodsinterval(:m, 'MINUTE')
                                           then 1 else 0 end
                                 from user_scheduler_jobs where job_name = :j""", m=ENABLE_MARGIN_MIN, j=EXPIRED_JOB)
                nxt = cur.fetchone()
            run_rec: dict = {"job": EXPIRED_JOB, "jobs_before": jobs_before,
                             "next_run_date_utc": nxt[0] if nxt else None,
                             "enable_safe": bool(nxt and nxt[1] == 1)}
            try:
                with c.cursor() as cur:
                    if run_rec["enable_safe"]:
                        cur.execute("begin dbms_scheduler.enable(:j); end;", j=EXPIRED_JOB)
                        run_rec["enabled_for_the_run"] = True
                    else:          # the scheduler could fire it beside our run: run it while disabled instead
                        run_rec["enabled_for_the_run"] = False
                        run_rec["enable_skipped"] = (f"next scheduled run {run_rec['next_run_date_utc']} is within "
                                                     f"{ENABLE_MARGIN_MIN} minutes or unknown")
                    t = time.perf_counter()
                    cur.execute("begin dbms_scheduler.run_job(job_name => :j, use_current_session => true); end;",
                                j=EXPIRED_JOB)
                    run_rec["run_seconds"] = round(time.perf_counter() - t, 3)
                    run_rec["ok"] = True
            except FATAL:
                raise
            except Exception as exc:          # reported as found; the job is disabled below in every case
                run_rec["ok"] = False
                run_rec["error_chain"] = opsrun.error_chain(exc, R)
            finally:
                try:
                    run_rec["disable"] = stores.disable_purge_jobs(c, STORE)
                except Exception as exc:      # recorded, then a direct disable of both exact jobs is tried
                    run_rec["disable_error"] = opsrun.error_chain(exc, R)
                    for jn in (EXPIRED_JOB, ORPHAN_JOB):
                        try:
                            with c.cursor() as cur:
                                cur.execute("begin dbms_scheduler.disable(:j); end;", j=jn)
                        except Exception as exc2:      # noqa: BLE001  (recorded; the read-back below decides)
                            run_rec.setdefault("direct_disable_errors", []).append(
                                {"job": jn, "error": opsrun.error_chain(exc2, R)})
                run_rec["jobs_after"] = stores.purge_jobs(c, STORE)
            run_rec["runs_since"] = stores.job_runs_since(c, STORE, t0)
            for key, jn in (("expired_job_runs_in_window", EXPIRED_JOB), ("orphan_job_runs_in_window", ORPHAN_JOB)):
                run_rec[key] = sum(1 for r in run_rec["runs_since"] if r["job_name"] == jn)
            if any(j["enabled"] == "TRUE" for j in run_rec["jobs_after"]):
                out["purge"] = run_rec
                raise StepStop(f"an OPS purge job is still enabled after the manual run (PLAN 5.3): "
                               f"{run_rec['jobs_after']}")
        out["purge"] = run_rec
        after = memory_rows(pool)
        out["memories_before"] = before
        out["memories_after"] = after
        out["expired_after"] = next((r for r in after if r["record_id"] == EXPIRED_ID), None)
        out["expired_chunks_after"] = chunk_count(pool, EXPIRED_ID)
        out["other_memories_unchanged"] = ([r for r in before if r["record_id"] != EXPIRED_ID]
                                           == [r for r in after if r["record_id"] != EXPIRED_ID])
        res = client.search(EXPIRED_Q, user_id=USER, max_results=10)
        out["expired_search_after"] = {"returned_ids": ids_of(res), "expired_returned": EXPIRED_ID in ids_of(res)}
        with pool.acquire() as c:
            out["unlisted_objects_now"] = stores.unlisted_now(c)
        RUN.transcript(f"- registry rows {len(out['registry'])}; oam check-plsql rc "
                       f"{out['oam_check_plsql'].get('returncode')}: {out['oam_check_plsql'].get('stdout', '')[-200:]}")
        RUN.transcript(f"- purge: enabled for the run {run_rec.get('enabled_for_the_run')} (next run "
                       f"{run_rec.get('next_run_date_utc')}); runs of {EXPIRED_JOB} in the window "
                       f"{run_rec['expired_job_runs_in_window']}")
        RUN.transcript(f"- purge: run {'ok' if run_rec.get('ok') else 'FAILED'}; expired memory after: "
                       f"{out['expired_after']}; its chunks {out['expired_chunks_after']}; other memories unchanged: "
                       f"{out['other_memories_unchanged']}; jobs after {run_rec['jobs_after']}")
    finally:
        if client is not None:
            client.close()
        pool.close()


out: dict = {"phase": PHASE, "list": LIST or None, "store": STORE, "user_id": USER, "thread_id": THREAD}
rc = 0
RUN.transcript(f"## Step 8 phase {PHASE}{' ' + LIST if LIST else ''} (store OPS)")
try:
    try:
        with register_event_listeners([m]):
            {"setup": phase_setup, "reader": phase_reader, "rt": phase_rt, "ops": phase_ops}[PHASE](out)
    except StepStop as exc:
        out["stopped"] = str(exc)
        RUN.log.error("step 8 %s stopped: %s", TAG, exc)
        RUN.transcript(f"- STOPPED: {exc}")
        rc = 1
finally:
    tok, ncalls = meter.step_tokens(m, lc)
    ledger.commit(TAG, tok, ncalls)

out["meter"] = m.summary()
out["litellm_counter"] = lc.summary()
out["ledger_total_after"] = ledger.total
RUN.write_json("results", f"{TAG}.json", out)
RUN.finish({"exit": rc, "phase": PHASE, "list": LIST or None, "llm_calls": calls(), "tokens": tok,
            "stopped": out.get("stopped"), "failed_operations": out.get("failed_operations")})
sys.exit(rc)
