# v1.4 2026-10-12  Brief 12 step 4 (PLAN 6 row 4, section 7.1 frozen): C1 retrieval bake-off, HYBRID against VECTOR
#                  against KEYWORD on the frozen BIZ_0001 corpus and its 120 labelled queries, called through the SDK
#                  exactly as 7.1 says; statistics from the frozen py/lib/stats.py through py/lib/c1run.py.
#                  v1.4: (phase 2, PLAN v0.3.6; the second attempt, 10-Oct 19:17Z, stopped before any search on
#                  its own precondition "RV_HYB: None of 400 chunks have an embedding") a HYBRID store has no
#                  chunk-table EMBEDDING column: its read-back now reads the hybrid index's <index>$VECTORS view
#                  (lib.c1db.hybrid_vectors) and the precondition (moved to lib.c1run.check_loaded, tested) needs
#                  every memory chunk to have an embedding there; (Codex 31 r1, finding 1) every store's read-back
#                  also checks identity (lib.c1db.coverage: valid memories without a chunk, chunks without a valid
#                  memory, both must be 0). Loading, searching and scoring unchanged.
#                  v1.3: (Codex 26 r3) a close failure with no other failure in flight stops the run (technical
#                  abort, exit 2) before the next store is created; while another failure is in flight it is only
#                  recorded, so the original failure is kept.
#                  v1.2: (Codex 26 r2) pool.close() guarded like the clients (a cleanup failure is recorded and
#                  never masks the original failure); the topN note cites PLAN 8.1 v0.3.4 as the record of the
#                  deviation from 7.1's description, 7.1 itself unchanged.
#                  v1.1: (Codex 26 r1) clients and pools are closed each in its own guard, the original failure kept
#                  and a close failure recorded; the per-query arm rotation is recorded as the reading of 7.0's "per
#                  run" for a one-run comparison; the summary's Hit@1 counts are cross-checked against c1_family's.
#                  v1.0: first version (day 1, phase 2).
"""Step 4. Run on the demo VM from ~/agentmem-poc/code with the POC venv (run_all.sh S4 verifies the freeze first):
    .venv/bin/python py/40_retrieval_bakeoff.py
Load (one store at a time, each with its own pool, closed before the next store is created, so no open session ever
sees the package re-installation a RECREATE performs, PLAN 5.3):
  RV_HYB  SearchStrategy.HYBRID,  OracleDBEmbedder(ASKORACLE.ALL_MINILM_L12_V2, 384, 256), search_index_sync ON_COMMIT
  RV_VEC  SearchStrategy.VECTOR,  the same embedder
  RV_KEY  SearchStrategy.KEYWORD, no embedder, search_index_sync ON_COMMIT (written out although it is the default)
  each: SchemaPolicy.RECREATE inside AGENTMEM by store id (lib.stores: purge jobs disabled, objects recorded in
  POC_MANIFEST), client WITHOUT an LLM and with extraction off, then the 400 frozen memories in memory-id order with
  add_memory(content, memory_type, user_id, memory_id, timestamp, autonomous_linking=False) and nothing else (no
  metadata). Read-back: 400 valid memories of BIZ_0001 and nothing else, one id each, chunks, embeddings (VECTOR: the
  chunk table's EMBEDDING column; HYBRID: the hybrid index's <index>$VECTORS view, v1.4), the Text index's pending
  queue empty (HYBRID, KEYWORD), so every memory is searchable before the first query.
Search (all three stores reopened with REQUIRE_EXISTING on one fresh pool, same strategy and settings): for each of the
  120 queries in qid order, the three strategies in the order lib.c1run.arm_order(i) (rotated per query, PLAN 7.0),
  search(query, user_id='BIZ_0001', max_results=10, num_hops=0, include_invalid_results=False): the default top-K
  configuration, no reranker, no pruning. The returned order is the ranking; latency is the wall time of the call.
Scoring: Hit@1 (confirmatory: the nine exact McNemar tests with Holm, stats.c1_family), Hit@5, MRR@10, zero-result
  count, latency median and p90 (exploratory); P1 and P2; the wording rule. A query with no result is a miss.
Technical abort (PLAN 7.0): any exception in a load or a search stops the script (exit 2) with what was done so far
  written to s4_failure.json; the run is then void and repeated once, both reported. A failed read-back precondition
  stops with exit 1 before the first search. No result is ever re-run, dropped or re-scored.
No LLM is attached anywhere (C1 makes no LLM call); the meter must show 0 LLM calls.
Results: staging/s4/results/{s4_store_load.json, c1_per_query.json, c1_results.json, s4_run_summary.json};
transcript staging/s4/transcripts/s4_transcript.md.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import runlog  # noqa: E402  (first: redaction before anything else)

RUN = runlog.start("s4")

from lib import amconf, c1db, c1run, meter, stores  # noqa: E402

LITELLM = amconf.configure_litellm()

from oracleagentmemory.core import (MemoryExtractionConfig, SchemaPolicy, SearchIndexSyncMode,  # noqa: E402
                                    SearchStrategy)
from oracleagentmemory.observability import register_event_listeners  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data" / "frozen"
CORPUS = c1run.load_corpus(DATA / "c1_corpus.jsonl")
QUERIES = c1run.load_queries(DATA / "c1_queries.jsonl", [m["memory_id"] for m in CORPUS])
SEARCH_CALL = {"user_id": c1run.USER_ID, "max_results": 10, "num_hops": 0, "include_invalid_results": False}
HYBRID_TOPN_NOTE = ("Recorded deviation from 7.1's description (section 7 frozen and unchanged): 7.1's wording rule "
                    "says HYBRID passes topN 10, which describes build_hybrid_search_request alone. For this call "
                    "(max_results=10) the store asks DBMS_HYBRID_VECTOR.SEARCH for topN 50 (candidate limit "
                    "max(k, ceil(k x chunks per record)) = 10, over-fetched to max(10 x 5, 20) because the status "
                    "filter also runs in SQL; _compute_hybrid_candidate_limit, rendered offline on 10-Oct, "
                    "research/rendered_sql/search_sql_render_10oct.out). The measurement is the SDK's call as it "
                    "is; PLAN 8.1 (v0.3.4) records that the post reports 50 as this deviation.")


class StepStop(RuntimeError):
    """A read-back precondition failed: nothing is searched, the step exits 1."""


def _settings(strategy: str) -> dict:
    kw = {"strategy": SearchStrategy[strategy], "llm": None,
          "extraction_config": MemoryExtractionConfig(extract_memories=False)}
    if strategy in ("HYBRID", "KEYWORD"):
        kw["index_sync"] = SearchIndexSyncMode.ON_COMMIT
    return kw


def readback(pool, store_id: str, strategy: str) -> dict:
    """Read-only state of one loaded store; table names come from the fixed store ids only."""
    if store_id not in c1run.STORE_OF.values():
        raise ValueError(store_id)
    mem_t, chk_t = f"{store_id}_MEMORY", f"{store_id}_RECORD_CHUNKS"
    out: dict = {}
    with pool.acquire() as conn, conn.cursor() as cur:
        cur.execute(f"select count(*), count(distinct record_id), "
                    f"sum(case when status = 'valid' then 1 else 0 end) from {mem_t} where user_id = :u",
                    u=c1run.USER_ID)
        n, nd, nv = cur.fetchone()
        cur.execute(f"select count(*) from {mem_t} where user_id is null or user_id <> :u", u=c1run.USER_ID)
        other = cur.fetchone()[0]
        cur.execute(f"select count(*) from {mem_t} where expires_at is not null")
        ttl = cur.fetchone()[0]
        out["memories"] = {"rows": n, "distinct_ids": nd, "valid": nv or 0, "other_users": other, "with_ttl": ttl}
        cur.execute(f"select count(*), count(distinct source_id) from {chk_t} "
                    f"where source_record_type in ('fact', 'guideline', 'memory', 'preference')")
        cn, cd = cur.fetchone()
        out["chunks"] = {"rows": cn, "distinct_sources": cd}
        out["coverage"] = c1db.coverage(conn, store_id, c1run.USER_ID)
        cur.execute("select count(*) from user_tab_columns where table_name = :t and column_name = 'EMBEDDING'",
                    t=chk_t)
        if cur.fetchone()[0] == 1:
            cur.execute(f"select count(*) from {chk_t} where embedding is not null")
            out["chunks"]["with_embedding"] = cur.fetchone()[0]
        else:
            out["chunks"]["with_embedding"] = None
        cur.execute("select index_name, index_type, ityp_name, status, domidx_opstatus from user_indexes "
                    "where table_name = :t order by index_name", t=chk_t)
        out["chunk_indexes"] = [{"index_name": r[0], "index_type": r[1], "ityp_name": r[2], "status": r[3],
                                 "domidx_opstatus": r[4]} for r in cur.fetchall()]
        if strategy in ("HYBRID", "KEYWORD"):
            cur.execute(r"select idx_name, idx_sync_type, idx_status from ctx_user_indexes "
                        r"where idx_name like :p escape '\' order by idx_name",
                        p=store_id.replace("_", r"\_") + r"\_%")
            out["text_indexes"] = [{"idx_name": r[0], "sync_type": r[1], "status": r[2]} for r in cur.fetchall()]
            cur.execute(r"select count(*) from ctx_user_pending where pnd_index_name like :p escape '\'",
                        p=store_id.replace("_", r"\_") + r"\_%")
            out["text_pending_rows"] = cur.fetchone()[0]
        if strategy == "HYBRID":           # the vectors live in the hybrid index, not in the chunk table (v1.4)
            out["hybrid_vectors"] = c1db.hybrid_vectors(conn, store_id)
    return out


CLOSE_ERRORS: list = []


def _close_pool(pool, label: str) -> None:
    """Close a pool; a failure is recorded and logged so it never replaces an exception already in flight."""
    try:
        pool.close()
    except Exception as exc:                  # recorded in s4_store_load.json and logged
        CLOSE_ERRORS.append({"pool": label, "error_type": type(exc).__name__, "error": str(exc)[:500]})
        RUN.log.warning("closing the %s pool failed: %s", label, type(exc).__name__)


def _close(client, store_id: str) -> None:
    """Close one SDK client; a failure here is recorded (never replaces an exception already in flight)."""
    try:
        client.close()
    except Exception as exc:                  # recorded in s4_store_load.json and logged; the pool still closes
        CLOSE_ERRORS.append({"store_id": store_id, "error_type": type(exc).__name__, "error": str(exc)[:500]})
        RUN.log.warning("closing the %s client failed: %s", store_id, type(exc).__name__)


class CleanupFailed(RuntimeError):
    """A client or pool did not close although nothing else failed: the run stops (technical abort, PLAN 7.0)."""


def _release(clients, pool, label: str, *, failing: bool) -> None:
    """Close the clients, then the pool, each guarded. With failing=True (another exception is in flight) close
    failures are only recorded; otherwise any close failure raises CleanupFailed, so nothing runs after it."""
    n0 = len(CLOSE_ERRORS)
    for c, sid in clients:
        if c is not None:
            _close(c, sid)
    _close_pool(pool, label)
    if not failing and len(CLOSE_ERRORS) > n0:
        raise CleanupFailed(f"closing {label} failed: {CLOSE_ERRORS[n0:]}")


def load_store(strategy: str, m: meter.Meter) -> dict:
    sid = c1run.STORE_OF[strategy]
    rec: dict = {"store_id": sid, "strategy": strategy}
    pool = amconf.make_pool("AGENTMEM", 1, 4)
    client = None
    try:
        emb = amconf.make_embedder(pool) if strategy != "KEYWORD" else None
        t = time.perf_counter()
        client, info = stores.open_store(pool, sid, policy=SchemaPolicy.RECREATE, embedder=emb, **_settings(strategy))
        rec["open_seconds"] = round(time.perf_counter() - t, 3)
        rec["store_open"] = info
        RUN.transcript(f"- {sid} ({strategy}) recreated in {rec['open_seconds']} s; purge jobs "
                       f"{[(j['job_name'], j['enabled_after']) for j in info.get('purge_jobs', [])]}; "
                       f"manifest recorded {info.get('manifest', {}).get('recorded')}, "
                       f"unlisted {info.get('manifest', {}).get('unlisted')}")
        mismatched = []
        t = time.perf_counter()
        for mem in CORPUS:
            rid = client.add_memory(mem["content"], memory_type=mem["memory_type"], user_id=c1run.USER_ID,
                                    memory_id=mem["memory_id"], timestamp=mem["timestamp"], autonomous_linking=False)
            if rid != mem["memory_id"]:
                mismatched.append([mem["memory_id"], rid])
        rec["load_seconds"] = round(time.perf_counter() - t, 3)
        rec["add_memory_calls"] = len(CORPUS)
        rec["ids_returned_as_ours"] = not mismatched
        rec["readback"] = readback(pool, sid, strategy)
        rec["precondition_failures"] = c1run.check_loaded(sid, strategy, rec["readback"], mismatched)
        rec["llm_calls_so_far"] = m.summary()["llm_calls"]
        RUN.transcript(f"  - 400 add_memory in {rec['load_seconds']} s; read-back {rec['readback']['memories']}, "
                       f"chunks {rec['readback']['chunks']}, Text pending {rec['readback'].get('text_pending_rows')}; "
                       f"preconditions {'OK' if not rec['precondition_failures'] else rec['precondition_failures']}")
    except BaseException:
        _release([(client, sid)], pool, f"load {sid}", failing=True)
        raise
    _release([(client, sid)], pool, f"load {sid}", failing=False)
    return rec


def search_all(m: meter.Meter, records: list, failure: dict) -> dict:
    pool = amconf.make_pool("AGENTMEM", 1, 4)
    clients: dict = {}
    opened: dict = {}
    try:
        for s in c1run.STRATEGIES:
            emb = amconf.make_embedder(pool) if s != "KEYWORD" else None
            clients[s], opened[s] = stores.open_store(pool, c1run.STORE_OF[s], policy=SchemaPolicy.REQUIRE_EXISTING,
                                                      embedder=emb, **_settings(s))
        RUN.transcript("## Search: 120 queries x 3 strategies, arm order rotated per query")
        for i, q in enumerate(QUERIES):
            for pos, s in enumerate(c1run.arm_order(i), 1):
                failure.update({"qid": q["qid"], "strategy": s})
                t = time.perf_counter()
                res = clients[s].search(q["text"], **SEARCH_CALL)
                ms = (time.perf_counter() - t) * 1000.0
                ranked = [str(r.id) for r in res]
                rec = {"qid": q["qid"], "qclass": q["qclass"], "strategy": s, "store_id": c1run.STORE_OF[s],
                       "position_in_query": pos, "query_text": q["text"], "gold_memory_id": q["gold_memory_id"],
                       "near_collision": q["near_collision"], "sibling_memory_id": q["sibling_memory_id"],
                       "ranked_ids": ranked, "latency_ms": round(ms, 3),
                       "returned_statuses": sorted({str(getattr(r, "status", "")) for r in res}),
                       "distance_or_score": [getattr(r, "distance", None) for r in res]}
                if q["qclass"] == "B":
                    rec["overlap_count"] = q["overlap_count"]
                    rec["overlap_terms"] = q["overlap_terms"]
                rec.update(c1run.score(ranked, q["gold_memory_id"]))
                records.append(rec)
        failure.clear()
    except BaseException:
        _release([(c, c1run.STORE_OF[s]) for s, c in clients.items()], pool, "search", failing=True)
        raise
    _release([(c, c1run.STORE_OF[s]) for s, c in clients.items()], pool, "search", failing=False)
    return {s: {"purge_jobs": v.get("purge_jobs"), "policy": v.get("policy")} for s, v in opened.items()}


m = meter.Meter()
ledger = meter.Ledger(amconf.STATE / "token_ledger.json", amconf.TOKEN_BUDGET)
lc = meter.LitellmCounter(meter=m, ledger=ledger)
lc.install()
out: dict = {"design": {"plan": "PLAN v0.3.6 section 7.1 (frozen, unchanged)", "user_id": c1run.USER_ID,
                        "stores": c1run.STORE_OF, "search_call": SEARCH_CALL, "memories": len(CORPUS),
                        "queries": len(QUERIES),
                        "interleaving": ("PLAN 7.0 says the arm order is rotated per run; C1 has one run per "
                                         "strategy, so the order is rotated by one position per query in qid order "
                                         "(lib.c1run.arm_order: each strategy first, second and third 40 times). "
                                         "Recorded as this step's reading of 7.0, decided before the run; Hit@1, "
                                         "Hit@5 and MRR do not depend on the order, only the exploratory latency "
                                         "can."),
                        "hybrid_topN_as_sent": HYBRID_TOPN_NOTE}}
records: list = []
failure: dict = {}
rc = 0
RUN.transcript("## Step 4 (C1): HYBRID vs VECTOR vs KEYWORD on BIZ_0001 (400 memories, 120 queries)")
try:
    try:
        with register_event_listeners([m]):
            loads = []
            for s in c1run.STRATEGIES:
                failure.update({"phase": "load", "store_id": c1run.STORE_OF[s]})
                loads.append(load_store(s, m))
            failure.clear()
            out["loads"] = loads
            bad = [b for ld in loads for b in ld["precondition_failures"]]
            if bad:
                raise StepStop("; ".join(bad))
            failure.update({"phase": "search"})
            out["search_open"] = search_all(m, records, failure)
        pool = amconf.make_pool("AGENTMEM", 1, 1)
        try:
            with pool.acquire() as conn:
                out["unlisted_objects_now"] = stores.unlisted_now(conn)
        except BaseException:
            _release([], pool, "final", failing=True)
            raise
        _release([], pool, "final", failing=False)
    except StepStop as exc:
        out["stopped"] = str(exc)
        RUN.log.error("step 4 stopped before searching: %s", exc)
        RUN.transcript(f"- STOPPED: {exc}")
        rc = 1
    except Exception as exc:                 # technical abort (7.0): recorded, then the run is void
        failure["error_type"] = type(exc).__name__
        failure["error"] = str(exc)[:2000]
        failure["searches_completed"] = len(records)
        out["failure"] = failure
        RUN.log.error("step 4 technical abort in %s: %s", failure.get("phase"), type(exc).__name__)
        RUN.transcript(f"- TECHNICAL ABORT ({failure.get('phase')}, {failure.get('store_id') or failure.get('qid')}): "
                       f"{type(exc).__name__}: {str(exc)[:300]}")
        rc = 2
finally:
    tok, ncalls = meter.step_tokens(m, lc)
    ledger.commit("s4", tok, ncalls)

out["meter"] = m.summary()
out["litellm_counter"] = lc.summary()
out["no_llm_call"] = out["meter"]["llm_calls"] == 0 and sum(lc.summary()["calls"].values()) == 0
out["client_close_errors"] = CLOSE_ERRORS
RUN.write_json("results", "s4_store_load.json", {k: out.get(k) for k in ("design", "loads", "search_open",
                                                                          "unlisted_objects_now", "stopped",
                                                                          "failure", "meter", "litellm_counter",
                                                                          "no_llm_call", "client_close_errors")})
if rc == 2:
    RUN.write_json("results", "s4_failure.json", {"failure": failure, "partial_records": records})
if rc == 0:
    tests = c1run.confirmatory(records, QUERIES)
    summ = c1run.summary(records, QUERIES)
    c1run.crosscheck(summ, tests)
    preds = c1run.predictions(records, QUERIES)
    nearc = c1run.near_collision(records, QUERIES)
    words = c1run.wording(tests)
    RUN.write_json("results", "c1_per_query.json", records)
    RUN.write_json("results", "c1_results.json", {
        "design": out["design"], "confirmatory_tests": tests, "summary": summ, "predictions": preds,
        "near_collision_exploratory": nearc, "wording": words, "result_rows": c1run.result_rows(summ, tests),
        "no_llm_call": out["no_llm_call"]})
    RUN.transcript("## Hit@1 per class (confirmatory) and exploratory measures")
    RUN.transcript("| strategy | slice | Hit@1 | Hit@5 | MRR@10 | zero | latency ms median / p90 |")
    RUN.transcript("|---|---|---|---|---|---|---|")
    for r in summ:
        RUN.transcript(f"| {r['strategy']} | {r['slice']} | {r['hit_at_1']}/{r['n']} | {r['hit_at_5']}/{r['n']} | "
                       f"{r['mrr_at_10']:.3f} | {r['zero_results']} | {r['latency_ms_median']:.1f} / "
                       f"{r['latency_ms_p90']:.1f} |")
    RUN.transcript("## The nine confirmatory tests (exact McNemar, Holm over nine)")
    for t in tests:
        RUN.transcript(f"- class {t['query_class']} {t['x']} vs {t['y']}: {t['hits_x']} vs {t['hits_y']}, b={t['b']} "
                       f"c={t['c']}, p={t['p']:.6f}, Holm p={t['p_holm']:.6f} -> {t['verdict']}")
    RUN.transcript(f"- P1 {'confirmed' if preds['P1']['confirmed'] else 'refuted'}: KEYWORD zero results on class B "
                   f"{preds['P1']['keyword_zero_result_B']}/40; P2 "
                   f"{'confirmed' if preds['P2']['confirmed'] else 'refuted'}: class C "
                   f"{preds['P2']['keyword_zero_result_C']}/40")
    RUN.transcript(f"- VECTOR beats HYBRID anywhere (headline rule): {words['headline_vector_beats_hybrid']}")
    RUN.finish({"exit": rc, "searches": len(records), "llm_calls": out["meter"]["llm_calls"],
                "verdicts": {f"{t['query_class']}:{t['x']}_vs_{t['y']}": t["verdict"] for t in tests},
                "hit_at_1": {f"{r['strategy']}:{r['slice']}": r["hit_at_1"] for r in summ},
                "P1": preds["P1"]["confirmed"], "P2": preds["P2"]["confirmed"],
                "unlisted_objects": len(out.get("unlisted_objects_now", []))})
else:
    RUN.finish({"exit": rc, "searches": len(records), "stopped": out.get("stopped"),
                "failure": {k: failure.get(k) for k in ("phase", "store_id", "qid", "strategy", "error_type")},
                "llm_calls": out["meter"]["llm_calls"]})
sys.exit(rc)
