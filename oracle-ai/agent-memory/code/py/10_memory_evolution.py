# v1.1 2026-10-10  Brief 12 step 1 (PLAN 6 row 1, section 3): memory evolution on store DEMO with a client WITHOUT an
#                  LLM. v1.1: (Codex review r1, finding 5) the no-LLM prediction also checks the litellm counter's
#                  completion calls. v1.0: first version (day 1).
"""Step 1. Run on the demo VM from ~/agentmem-poc/code with the POC venv:
    .venv/bin/python py/10_memory_evolution.py
What it does (store DEMO is recreated by id with SchemaPolicy.RECREATE inside AGENTMEM; nothing else is touched):
  1. client: OracleAgentMemory(connection=pool, embedder=OracleDBEmbedder(ASKORACLE.ALL_MINILM_L12_V2, 384, 256),
     llm=None, memory_extraction_config=MemoryExtractionConfig(extract_memories=False), VECTOR) -- no LLM at all;
     the purge jobs are disabled right after the open and the store's objects recorded in POC_MANIFEST (lib.stores).
  2. hero M1-M10 (py/lib/hero_seed.py, frozen) with add_memory(content, memory_type, user_id, memory_id, timestamp,
     autonomous_linking=False), in key order; the six links with link_records (stored types, B17).
  3. statuses read from DEMO_MEMORY (status, invalidation_reason) against section 3's expectation (M1, M2, M3, M7
     invalid; B4, B21).
  4. "Which plan does the customer want now?": num_hops 0; 1 with max_linked_results=3; 2 with max_linked_results=3;
     include_invalid_results=False, max_results=10: direct results (rank, key, status, distance), the rank of M10, M2
     and M1, linked_results (relation type per child), format_content() of each direct result and whether it contains
     its linked children's text.
  5. "Did the customer ever want unlimited data?": num_hops 0 with include_invalid_results True and False.
  6. the meter: embedder events by concrete class (prediction: only OracleDBEmbedder) and LiteLLM embedding calls
     (prediction: 0); LLM calls (must be 0).
Every prediction is reported as confirmed or refuted; nothing is retried to make one hold.
Results: staging/s1/results/*.json and format_content_*.txt; transcript staging/s1/transcripts/.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import runlog  # noqa: E402  (first: redaction before anything else)

RUN = runlog.start("s1")

from lib import amconf, hero_seed, meter, stores  # noqa: E402

LITELLM = amconf.configure_litellm()

from oracleagentmemory.core import MemoryExtractionConfig, SchemaPolicy, SearchStrategy  # noqa: E402
from oracleagentmemory.observability import register_event_listeners  # noqa: E402

hero_seed.validate()
KEY_OF = {m.memory_id: m.key for m in hero_seed.MEMORIES}
MEM = hero_seed.by_key()


def key(rid) -> str:
    return KEY_OF.get(str(rid), str(rid))


def statuses(pool) -> dict:
    with pool.acquire() as c, c.cursor() as cur:
        cur.execute("""select record_id, memory_type, status,
                              json_value(metadata, '$."$agent_memory"."invalidation_reason"')
                         from DEMO_MEMORY where user_id = :u order by record_id""", u=hero_seed.USER_ID)
        return {key(r[0]): {"memory_type": r[1], "status": r[2], "invalidation_reason": r[3]} for r in cur.fetchall()}


def links(pool) -> list[dict]:
    with pool.acquire() as c, c.cursor() as cur:
        cur.execute("""select source_memory_id, target_memory_id, relation_type, opposite_relation_type
                         from DEMO_MEMORY_LINK order by created_at, relation_id""")
        return [{"source": key(r[0]), "target": key(r[1]), "relation_type": r[2], "opposite": r[3]}
                for r in cur.fetchall()]


def result_rows(results) -> list[dict]:
    rows = []
    for rank, r in enumerate(results, 1):
        linked = []
        for rel, child in (getattr(r, "linked_results", None) or []):
            linked.append({"relation_type": getattr(rel, "relation_type", None),
                           "source": key(getattr(rel, "source_record_id", "")),
                           "target": key(getattr(rel, "target_record_id", "")),
                           "child": key(getattr(child, "id", "")),
                           "child_status": str(getattr(child, "status", "")),
                           "child_content": str(getattr(child, "content", ""))[:160]})
        fc = r.format_content()
        rows.append({"rank": rank, "key": key(r.id), "status": str(getattr(r, "status", "")),
                     "distance": getattr(r, "distance", None), "content": str(r.content)[:160],
                     "linked": linked,
                     "format_content_includes_linked_text": (all(lk["child_content"][:40] in fc for lk in linked)
                                                             if linked else None),
                     "format_content": fc})
    return rows


def rank_of(rows, k) -> int | None:
    return next((r["rank"] for r in rows if r["key"] == k), None)


m = meter.Meter()
ledger = meter.Ledger(amconf.STATE / "token_ledger.json", amconf.TOKEN_BUDGET)
lc = meter.LitellmCounter(meter=m, ledger=ledger)
lc.install()
pool = amconf.make_pool("AGENTMEM", 1, 4)
out: dict = {"store": hero_seed.STORE_ID, "user_id": hero_seed.USER_ID}
try:
    with register_event_listeners([m]):
        emb = amconf.make_embedder(pool)
        client, info = stores.open_store(pool, hero_seed.STORE_ID, policy=SchemaPolicy.RECREATE, embedder=emb,
                                         llm=None, strategy=SearchStrategy.VECTOR,
                                         extraction_config=MemoryExtractionConfig(extract_memories=False))
        out["store_open"] = info
        RUN.transcript(f"## Step 1: store {hero_seed.STORE_ID} recreated, client without an LLM")
        ids = {}
        for mem in hero_seed.MEMORIES:
            rid = client.add_memory(mem.content, memory_type=mem.memory_type, user_id=hero_seed.USER_ID,
                                    memory_id=mem.memory_id, timestamp=mem.timestamp, autonomous_linking=False)
            ids[mem.key] = rid
            RUN.transcript(f"- add_memory {mem.key} ({mem.memory_type}, {mem.timestamp}) -> {rid}")
        out["memory_ids_as_returned"] = ids
        out["memory_ids_match_ours"] = all(ids[k] == MEM[k].memory_id for k in ids)
        rel_ids = {}
        for lk in hero_seed.LINKS:
            s, t = MEM[lk.source], MEM[lk.target]
            rid = client.link_records(s.memory_id, s.memory_type, t.memory_id, t.memory_type, lk.relation_type)
            rel_ids[f"{lk.source}->{lk.target}"] = rid
            RUN.transcript(f"- link_records {lk.source} {lk.relation_type} {lk.target}")
        out["relation_ids"] = rel_ids
        st = statuses(pool)
        exp = hero_seed.expected_statuses()
        mism = {k: {"expected": exp[k], "found": st.get(k, {}).get("status")} for k in exp
                if st.get(k, {}).get("status") != exp[k]}
        out["statuses_after_seeding"] = st
        out["links_after_seeding"] = links(pool)
        out["prediction_statuses"] = {"expected_invalid": sorted(hero_seed.EXPECTED_INVALID),
                                      "confirmed": not mism, "mismatches": mism}
        RUN.transcript(f"- statuses vs section 3: {'MATCH' if not mism else 'MISMATCH ' + str(mism)}")

        searches = {}
        for label, hops, maxl in (("hops0", 0, None), ("hops1_max3", 1, 3), ("hops2_max3", 2, 3)):
            kw = dict(user_id=hero_seed.USER_ID, max_results=10, num_hops=hops, include_invalid_results=False)
            if maxl is not None:
                kw["max_linked_results"] = maxl
            t = time.perf_counter()
            res = client.search(hero_seed.Q_PLAN_NOW, **kw)
            ms = round((time.perf_counter() - t) * 1000, 1)
            rows = result_rows(res)
            searches[label] = {"call": {k: v for k, v in kw.items()}, "latency_ms": ms, "results": rows,
                               "rank_M10": rank_of(rows, "M10"), "rank_M2": rank_of(rows, "M2"),
                               "rank_M1": rank_of(rows, "M1")}
            RUN.transcript(f"- search '{hero_seed.Q_PLAN_NOW}' {label}: ranks M10={searches[label]['rank_M10']} "
                           f"M2={searches[label]['rank_M2']} M1={searches[label]['rank_M1']}; "
                           f"linked on top result: {[(x['relation_type'], x['child']) for x in rows[0]['linked']] if rows else []}")
            if rows:
                RUN.write_text("results", f"format_content_plan_now_{label}_top.txt", rows[0]["format_content"] + "\n")
        out["search_plan_now"] = searches
        top1 = searches["hops1_max3"]["results"][0] if searches["hops1_max3"]["results"] else None
        out["prediction_plan_now"] = {
            "M10_ranks_first_hops0": searches["hops0"]["rank_M10"] == 1,
            "old_preferences_as_linked_context_hops1": (sorted({x["child"] for x in top1["linked"]}) if top1 else []),
            "format_content_includes_linked_hops1": top1["format_content_includes_linked_text"] if top1 else None,
        }
        ever = {}
        for inc in (True, False):
            res = client.search(hero_seed.Q_EVER_UNLIMITED, user_id=hero_seed.USER_ID, max_results=10, num_hops=0,
                                include_invalid_results=inc)
            rows = result_rows(res)
            ever[f"include_invalid_{inc}"] = {"results": [{k: r[k] for k in ("rank", "key", "status", "distance",
                                                                             "content")} for r in rows],
                                              "rank_M1": rank_of(rows, "M1")}
            RUN.transcript(f"- search '{hero_seed.Q_EVER_UNLIMITED}' include_invalid_results={inc}: "
                           f"M1 rank {ever[f'include_invalid_{inc}']['rank_M1']}, "
                           f"{len(rows)} result(s)")
        out["search_ever_unlimited"] = ever
    with pool.acquire() as conn:
        out["unlisted_objects_now"] = stores.unlisted_now(conn)
finally:
    tok, calls = meter.step_tokens(m, lc)
    ledger.commit("s1", tok, calls)
    pool.close()

ms_ = m.summary()
emb_started = {k.split(":", 1)[1] for k in ms_["embedder_events_by_class"] if k.startswith("EmbedderStartedEvent:")}
out["meter"] = ms_
out["litellm_counter"] = lc.summary()
out["prediction_in_db_embedding"] = {
    "embedder_classes_started": sorted(emb_started),
    "only_OracleDBEmbedder": emb_started == {"OracleDBEmbedder"},
    "litellm_embedding_calls": lc.summary()["calls"].get("embedding", 0) + lc.summary()["calls"].get("aembedding", 0),
    "llm_calls": ms_["llm_calls"],
    "litellm_completion_calls": lc.summary()["calls"].get("completion", 0) + lc.summary()["calls"].get("acompletion", 0),
}
out["prediction_no_llm"] = {"confirmed": ms_["llm_calls"] == 0
                            and out["prediction_in_db_embedding"]["litellm_completion_calls"] == 0}
RUN.write_json("results", "s1_memory_evolution.json", out)
RUN.transcript(f"- meter: embedder classes {sorted(emb_started)}, litellm embedding calls "
               f"{out['prediction_in_db_embedding']['litellm_embedding_calls']}, LLM calls {ms_['llm_calls']}")
RUN.finish({"statuses_match": out["prediction_statuses"]["confirmed"],
            "M10_first_hops0": out["prediction_plan_now"]["M10_ranks_first_hops0"],
            "llm_calls": ms_["llm_calls"], "no_llm_confirmed": out["prediction_no_llm"]["confirmed"],
            "unlisted_objects": len(out["unlisted_objects_now"])})
