# v1.1 2026-10-12  Brief 12 step 10 (PLAN v0.3.8 6 row 10, 9): the announcement's snippets live on store SNIP: the
#                  corrected ones end to end, S7 as written against memories written by the minimally fixed S2, the
#                  as-written failures of S1-S4 confirmed live, and the final N of 10 by the counting rule.
#                  v1.1: (after the first run, 18:22Z) lib/opsrun.py v1.2 applies PLAN 6 row 10's counting rule as
#                  written; a control is added: the same link_records with the STORED type ('memory') on the minimally
#                  fixed S2's memories (API_NOTES section 4's corrected S7: pass the stored type). v1.0: first version.
"""Step 10. Run on the demo VM from ~/agentmem-poc/code with the POC venv (run_all.sh S10):
    .venv/bin/python py/100_snippets_live.py
No LLM is attached anywhere (memory_extraction_config=MemoryExtractionConfig(extract_memories=False), llm omitted),
so add_memory does no autonomous linking (B16) and the step makes 0 LLM calls (checked by the meter and the litellm
counter). The snippet forms are those of research/API_NOTES.md section 4 and code/offline/check_blog_snippets.py
(verified against the announcement on 09-Oct); our data is synthetic (two customers, two preferences each).
  S8  oracledb.defaults.program = "agentmem-poc-s10" before the pool exists (as written); read back.
  S1  as written: import oracleagentmemory.stores.oracledb (expected ModuleNotFoundError); corrected:
      from oracleagentmemory.core import OracleDBMemoryStore, SchemaPolicy.
  --  store SNIP recreated by id (lib.stores.open_store, RECREATE: purge jobs disabled, manifest recorded), so the step
      is re-runnable; then
  S9  as written after S1's fix: OracleDBMemoryStore(**store_kwargs, schema_policy=SchemaPolicy.CREATE_IF_NECESSARY)
      on SNIP, and OracleAgentMemory(store=that store, embedder=the same OracleDBEmbedder, extraction off).
      Objects created in its window are merged into
      the manifest; SNIP's purge jobs must still be disabled.
  S6  MemoryExtractionConfig(memory_extraction_frequency=1, memory_link_extraction_mode=POST_EXTRACTION) constructed
      (not attached: with no LLM, extraction would refuse; nothing is extracted in this step).
  S2  as written: add_memory(user_id=..., agent_id=..., text=..., metadata=...) (expected TypeError); corrected
      (customer SNIP_CUST_01): add_memory(item["text"], user_id, agent_id, metadata, memory_type="preference");
      minimally fixed (customer SNIP_CUST_02): add_memory(user_id, agent_id, content=item["text"], metadata), so the
      stored type is the default.
  S7  as written (types "preference", relation "supersedes", metadata) on the corrected S2's two memories (end to end)
      and on the minimally fixed S2's two memories (the counting test; B17's error was predicted); then, as a
      control, the same call with the stored type 'memory' on those memories.
  S3  as written: await memory.search(query=..., num_hops=0) (expected ValueError, no user scope); with only user_id
      added (expected TypeError on the await); corrected: memory.search(query, user_id, num_hops=0) and num_hops=1, and
      await memory.search_async(...) (same ids as the sync call).
  S4  as written: the post's helper linked_results(result) is not exported (NameError) and its label expression on a
      real (RecordRelation, SearchResult) element; corrected: for rel, child in result.linked_results.
  S5  format_content() of the top num_hops=1 result.
Results: staging/s10/results/s10_snippets_live.json; transcript staging/s10/transcripts/s10_transcript.md.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import runlog  # noqa: E402  (first: redaction before anything else)

RUN = runlog.start("s10")

import oracledb  # noqa: E402

PROGRAM = "agentmem-poc-s10"
oracledb.defaults.program = PROGRAM                     # S8, as written in the post, before any connection exists

from lib import amconf, meter, opsrun, redact, stores  # noqa: E402

LITELLM = amconf.configure_litellm()

from oracleagentmemory.core import (MemoryExtractionConfig, MemoryLinkExtractionMode,  # noqa: E402
                                    OracleAgentMemory, SchemaPolicy, SearchStrategy)
from oracleagentmemory.observability import register_event_listeners  # noqa: E402

STORE = "SNIP"
AGENT = "snip_agent"
CUST_FIXED = "SNIP_CUST_01"          # corrected S2
CUST_MIN = "SNIP_CUST_02"            # minimally fixed S2 (only text= changed to content=)
QUERY = "How does the customer want to hear about billing?"
ITEMS = [
    {"text": "Prefers e-mail for billing updates.", "metadata": {"source": "snippet_check", "order": 1}},
    {"text": "Now prefers WhatsApp for billing updates, never e-mail.", "metadata": {"source": "snippet_check",
                                                                                   "order": 2}},
]
LINK_META = {"reason": "the customer changed the billing channel"}


class StepStop(RuntimeError):
    pass


def memories(pool) -> list[dict]:
    with pool.acquire() as c, c.cursor() as cur:
        cur.execute("""select record_id, user_id, memory_type, status, dbms_lob.substr(content, 200, 1)
                         from SNIP_MEMORY order by user_id, created_at, record_id""")
        return [{"record_id": r[0], "user_id": r[1], "memory_type": r[2], "status": r[3], "content": r[4]}
                for r in cur.fetchall()]


def links(pool) -> list[dict]:
    with pool.acquire() as c, c.cursor() as cur:
        cur.execute("""select relation_id, source_memory_id, target_memory_id, relation_type,
                              json_serialize(metadata returning varchar2(4000)) from SNIP_MEMORY_LINK
                        order by created_at, relation_id""")
        return [{"relation_id": r[0], "source": r[1], "target": r[2], "relation_type": r[3], "metadata": r[4]}
                for r in cur.fetchall()]


def result_rows(results) -> list[dict]:
    rows = []
    for rank, r in enumerate(results, 1):
        linked = [{"relation_type": getattr(rel, "relation_type", None), "child": str(getattr(child, "id", "")),
                   "child_status": str(getattr(child, "status", ""))}
                  for rel, child in (getattr(r, "linked_results", None) or [])]
        rows.append({"rank": rank, "id": str(r.id), "status": str(getattr(r, "status", "")),
                     "distance": getattr(r, "distance", None), "content": str(r.content)[:160], "linked": linked})
    return rows


m = meter.Meter()
ledger = meter.Ledger(amconf.STATE / "token_ledger.json", amconf.TOKEN_BUDGET)
lc = meter.LitellmCounter(meter=m, ledger=ledger)
lc.install()
FATAL = (meter.BudgetExceeded,)
R = redact.redact
out: dict = {"store": STORE, "query": QUERY, "items": ITEMS, "program_set": PROGRAM, "checks": {}}
ck = out["checks"]
rc = 0
RUN.transcript("## Step 10: the announcement's snippets live on store SNIP (oracleagentmemory 26.8.0, no LLM)")
try:
    try:
        with register_event_listeners([m]):
            # S8 read-back (the assignment ran at import time, before any connection)
            ck["S8_as_written"] = {"ok": oracledb.defaults.program == PROGRAM,
                                   "defaults_program": oracledb.defaults.program}
            # S1
            ck["S1_as_written"] = opsrun.run_op("import oracleagentmemory.stores.oracledb",
                                                lambda: importlib.import_module("oracleagentmemory.stores.oracledb"),
                                                summarize=lambda mod: getattr(mod, "__name__", None), redact=R)
            from oracleagentmemory.core import OracleDBMemoryStore, SchemaPolicy as SP  # noqa: N814  (S1 corrected)
            ck["S1_corrected"] = {"ok": OracleDBMemoryStore is not None and SP is SchemaPolicy,
                                  "import": "from oracleagentmemory.core import OracleDBMemoryStore, SchemaPolicy"}
            pool = amconf.make_pool("AGENTMEM", 1, 4)
            memory = base = None
            try:
                with pool.acquire() as c, c.cursor() as cur:
                    try:
                        cur.execute("select sys_context('USERENV', 'CLIENT_PROGRAM_NAME') from dual")
                        ck["S8_as_written"]["db_client_program_name"] = cur.fetchone()[0]
                    except oracledb.DatabaseError as exc:      # recorded; the post's line itself already ran
                        ck["S8_as_written"]["db_client_program_name_error"] = opsrun.error_chain(exc, R)
                emb = amconf.make_embedder(pool)
                # a clean SNIP for every run (guarded, manifest recorded, purge jobs disabled)
                base, info = stores.open_store(pool, STORE, policy=SchemaPolicy.RECREATE, embedder=emb, llm=None,
                                               strategy=SearchStrategy.VECTOR,
                                               extraction_config=MemoryExtractionConfig(extract_memories=False))
                out["snip_recreate"] = info
                base.close()
                base = None
                # S9 as written (after S1's fix), in its own manifest window
                with pool.acquire() as c:
                    t_s9 = stores.db_t0(c)
                store_kwargs = {"embedder": emb, "pool": pool, "memory_store_id": STORE}
                s9 = {}
                try:
                    store = OracleDBMemoryStore(**store_kwargs, schema_policy=SchemaPolicy.CREATE_IF_NECESSARY)
                    s9 = {"ok": True}
                except FATAL:
                    raise
                except Exception as exc:
                    s9 = {"ok": False, "error_chain": opsrun.error_chain(exc, R)}
                with pool.acquire() as c:
                    s9["manifest_window"] = stores.record_manifest(c, t_s9, create=False)
                    s9["purge_jobs_after"] = stores.purge_jobs(c, STORE)
                ck["S9_as_written"] = s9
                if not s9["ok"]:
                    raise StepStop("S9 failed live: " + json.dumps(s9["error_chain"])[:400])
                if s9["manifest_window"]["unlisted"] or s9["manifest_window"]["conflicts"]:
                    raise StepStop(f"S9's open left unlisted or conflicting objects: {s9['manifest_window']}")
                if any(j["enabled"] == "TRUE" for j in s9["purge_jobs_after"]):
                    raise StepStop("a SNIP purge job is enabled after S9's open (PLAN 5.3): stop, ask the operator")
                # the client gets the same in-database embedder: with an explicit store, a client without one
                # cannot embed a query for VECTOR search (OracleAgentMemory docstring, "embedder")
                memory = OracleAgentMemory(store=store, embedder=emb,
                                           memory_extraction_config=MemoryExtractionConfig(extract_memories=False))
                # S6
                ck["S6_as_written"] = opsrun.run_op(
                    "MemoryExtractionConfig(memory_extraction_frequency=1, POST_EXTRACTION)",
                    lambda: MemoryExtractionConfig(memory_extraction_frequency=1,
                                                   memory_link_extraction_mode=MemoryLinkExtractionMode.POST_EXTRACTION),
                    summarize=lambda c: {"memory_extraction_frequency": c.memory_extraction_frequency,
                                         "memory_link_extraction_mode": str(c.memory_link_extraction_mode)},
                    redact=R)
                ck["S6_as_written"]["note"] = "constructed only: no LLM is attached, so nothing is extracted here"
                # S2 as written
                ck["S2_as_written"] = opsrun.run_op(
                    "add_memory(user_id=, agent_id=, text=, metadata=)",
                    lambda: memory.add_memory(user_id=CUST_FIXED, agent_id=AGENT, text=ITEMS[0]["text"],
                                              metadata=ITEMS[0]["metadata"]), fatal=FATAL, redact=R)
                # S2 corrected (end to end) and minimally fixed (the S7 counting test)
                ids_fixed, ids_min = [], []
                for it in ITEMS:
                    ids_fixed.append(memory.add_memory(it["text"], user_id=CUST_FIXED, agent_id=AGENT,
                                                       metadata=it["metadata"], memory_type="preference"))
                for it in ITEMS:
                    ids_min.append(memory.add_memory(user_id=CUST_MIN, agent_id=AGENT, content=it["text"],
                                                     metadata=it["metadata"]))
                stored = memories(pool)
                typ = {r["record_id"]: r["memory_type"] for r in stored}
                ck["S2_corrected"] = {"ok": all(typ.get(i) == "preference" for i in ids_fixed), "ids": ids_fixed,
                                      "stored_types": [typ.get(i) for i in ids_fixed]}
                ck["S2_minimally_fixed"] = {"ok": len(ids_min) == 2, "ids": ids_min,
                                            "stored_types": [typ.get(i) for i in ids_min],
                                            "form": "add_memory(user_id=, agent_id=, content=item['text'], metadata=)"}
                # S7 as written, end to end on the corrected memories, then against the minimally fixed ones
                s7_kw = dict(source_record_type="preference", target_record_type="preference",
                             relation_type="supersedes", metadata=LINK_META)
                ck["S7_as_written_on_corrected_S2"] = opsrun.run_op(
                    "link_records(new, 'preference', old, 'preference', 'supersedes', metadata)",
                    lambda: memory.link_records(source_record_id=ids_fixed[1], target_record_id=ids_fixed[0],
                                                **s7_kw), summarize=str, fatal=FATAL, redact=R)
                s7min = opsrun.run_op(
                    "link_records(new, 'preference', old, 'preference', 'supersedes', metadata)",
                    lambda: memory.link_records(source_record_id=ids_min[1], target_record_id=ids_min[0], **s7_kw),
                    summarize=str, fatal=FATAL, redact=R)
                s7min["s2_form"] = "minimally fixed"
                s7min["stored_types"] = ck["S2_minimally_fixed"]["stored_types"]
                ck["S7_as_written_on_minimally_fixed_S2"] = s7min
                # control: the same call with the type those memories were stored with (API_NOTES 4, corrected S7)
                ck["S7_stored_type_on_minimally_fixed_S2"] = opsrun.run_op(
                    "link_records(new, 'memory', old, 'memory', 'supersedes', metadata)",
                    lambda: memory.link_records(source_record_id=ids_min[1], target_record_id=ids_min[0],
                                                source_record_type="memory", target_record_type="memory",
                                                relation_type="supersedes", metadata=LINK_META),
                    summarize=str, fatal=FATAL, redact=R)
                out["memories_after_s7"] = memories(pool)
                out["links_after_s7"] = links(pool)

                # S3: as written, with only user_id added, corrected (sync, num_hops 0 and 1; async)
                async def s3_as_written():
                    return await memory.search(query=QUERY, num_hops=0)

                async def s3_user_only():
                    return await memory.search(query=QUERY, user_id=CUST_FIXED, num_hops=0)

                async def s3_async_corrected():
                    return await memory.search_async(query=QUERY, user_id=CUST_FIXED, num_hops=0)

                ck["S3_as_written"] = opsrun.run_op("await memory.search(query=, num_hops=0)",
                                                    lambda: asyncio.run(s3_as_written()), fatal=FATAL, redact=R)
                ck["S3_user_id_added_still_awaited"] = opsrun.run_op(
                    "await memory.search(query=, user_id=, num_hops=0)", lambda: asyncio.run(s3_user_only()),
                    fatal=FATAL, redact=R)
                direct = memory.search(query=QUERY, user_id=CUST_FIXED, num_hops=0)
                graph = memory.search(query=QUERY, user_id=CUST_FIXED, num_hops=1)
                asyn = asyncio.run(s3_async_corrected())
                ck["S3_corrected"] = {"ok": len(direct) > 0, "direct_num_hops_0": result_rows(direct),
                                      "graph_num_hops_1": result_rows(graph),
                                      "search_async_same_ids": [str(r.id) for r in asyn] == [str(r.id) for r in direct]}
                # S4: the post's helper and label expression, then the corrected loop
                top = graph[0] if graph else None
                ck["S4_as_written_helper"] = opsrun.run_op(
                    "linked_results(result)", lambda: linked_results(top),   # noqa: F821  (the post's unshown helper)
                    fatal=FATAL, redact=R)
                labels = [getattr(linked, "link_type", getattr(linked, "relationship", "linked"))
                          for linked in (getattr(top, "linked_results", None) or [])]
                corrected = [{"relation_type": rel.relation_type, "child": str(child.id),
                              "child_content": str(child.content)[:160]}
                             for rel, child in (getattr(top, "linked_results", None) or [])]
                ck["S4_as_written_label"] = {"ok": bool(labels) and all(x != "linked" for x in labels),
                                             "labels": labels,
                                             "expression": 'getattr(linked, "link_type", getattr(linked, '
                                                           '"relationship", "linked"))'}
                ck["S4_corrected"] = {"ok": bool(corrected) and all(c["relation_type"] for c in corrected),
                                      "loop": "for rel, child in result.linked_results", "linked": corrected}
                # S5
                fc = top.format_content() if top is not None else None
                ck["S5_as_written"] = {"ok": bool(fc), "top_id": str(top.id) if top is not None else None,
                                       "format_content": fc,
                                       "includes_linked_text": bool(fc) and all(c["child_content"][:40] in fc
                                                                               for c in corrected)}
                with pool.acquire() as c:
                    out["unlisted_objects_now"] = stores.unlisted_now(c)
                    out["purge_jobs_end"] = stores.purge_jobs(c, STORE)
            finally:
                for cl in (memory, base):
                    if cl is not None:
                        cl.close()
                pool.close()
        out["count"] = opsrun.snippet_count(ck["S7_as_written_on_minimally_fixed_S2"])
        llm_used = m.summary()["llm_calls"] + lc.summary()["calls"].get("completion", 0) \
            + lc.summary()["calls"].get("acompletion", 0)
        if llm_used:
            raise StepStop(f"step 10 made {llm_used} LLM call(s); it must make none")
        for k, v in ck.items():
            RUN.transcript(f"- {k}: {'ok' if v.get('ok') else 'FAILS'}"
                           + ("" if v.get("ok") else f" ({(v.get('error_chain') or [{}])[0].get('type')}: "
                                                    f"{(v.get('error_chain') or [{}])[0].get('message')})"))
        RUN.transcript(f"- N of 10 = {out['count']['n_fail']} ({', '.join(out['count']['failing'])}); "
                       f"{out['count']['reason']}")
    except StepStop as exc:
        out["stopped"] = str(exc)
        RUN.log.error("step 10 stopped: %s", exc)
        RUN.transcript(f"- STOPPED: {exc}")
        rc = 1
finally:
    tok, ncalls = meter.step_tokens(m, lc)
    ledger.commit("s10", tok, ncalls)

out["meter"] = m.summary()
out["litellm_counter"] = lc.summary()
out["ledger_total_after"] = ledger.total
RUN.write_json("results", "s10_snippets_live.json", out)
RUN.finish({"exit": rc, "n_of_10": out.get("count", {}).get("n_fail"), "failing": out.get("count", {}).get("failing"),
            "checks": {k: bool(v.get("ok")) for k, v in ck.items()}, "llm_calls": out["meter"]["llm_calls"],
            "stopped": out.get("stopped"), "unlisted_objects": len(out.get("unlisted_objects_now", []))})
sys.exit(rc)
