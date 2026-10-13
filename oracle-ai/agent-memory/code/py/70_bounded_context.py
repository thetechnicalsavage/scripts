# v1.2 2026-10-12  Brief 12 step 7 (PLAN 6 row 7, section 7.3 frozen): C3 bounded context, top-K against LLM pruning
#                  on RV_VEC (step 4's corpus, read-only) with the 40 frozen set queries, one client per arm, through
#                  the SDK exactly as 7.3 says; statistics from the frozen py/lib/stats.py through py/lib/c3run.py.
#                  v1.2: (Codex 34 r2) the determinism stop also sets the run summary's exit to 1.
#                  v1.1: (Codex 34 r1, ISSUES 3, all accepted) pruner Llms honour state/llm_choice.json's
#                  structured-output reminder like every other step; the top-K determinism check is enforced (two
#                  differing top-K runs stop the step before any decision, exit 1); RV_VEC's read-back also checks
#                  identity (lib.c1db.coverage 0/0), distinct chunk sources and no TTL, as step 4 did.
#                  v1.0: first version (phase 2).
"""Step 7. Run on the demo VM from ~/agentmem-poc/code with the POC venv, only through run_all.sh S7 (which verifies
the freeze and regenerates the C1/C3 files with --check first):
    .venv/bin/python py/70_bounded_context.py

Store: RV_VEC as step 4 left it, opened with SchemaPolicy.REQUIRE_EXISTING (never recreated, nothing written); its
  read-back must show 400 valid memories of BIZ_0001, nothing else, and 400 chunks with an embedding, or the step
  stops before the first search (exit 1).
Arms (PLAN 7.3), one client per arm on one pool, the search configuration set on the client:
  TOPK10      TopKMemorySearchConfig(max_results=10), calls with max_results=10
  TOPK5       TopKMemorySearchConfig(max_results=5),  calls with max_results=5
  PRUNE_FAST  PruningMemorySearchConfig(pruner=<own Llm>, evaluation_mode=FAST, protected_fraction=0.1)
  PRUNE_EXT   ... EXTENDED, protected_fraction=0.1, num_probe_points=3
  PRUNE_EXH   ... EXHAUSTIVE, protected_fraction=0.1
  pruning arms are called with max_results=10 (the candidate pool). Every call: search(text, user_id='BIZ_0001',
  max_results=..., token_budget=0, soft_token_budget=0, num_hops=0); no other argument. Each pruning arm has its
  own Llm (PLAN 10.5 model, temperature 0, supports_vision False, max_concurrent_requests=2); clients without an LLM
  and with extraction off.
Runs: pruning arms 3, top-K arms 2 (must return identical lists; the first is used). Arm order rotated per run
  (lib.c3run.schedule); within a run the 40 queries in qid order, one search at a time.
Per query: the returned ids in the SDK's order; estimated tokens = sum of max(1, round(len(formatted_content)/3.5))
  (stats.estimated_tokens, the SDK's own budget count); gold recall and precision (stats.recall_precision); pruner
  calls and tokens (listener and litellm counter deltas around the call); latency; EXTENDED's "regions overlap"
  warnings (counted, not suppressed; every occurrence is emitted by a warnings filter for that one message).
Retries and void (PLAN 10.5, 7.0): lib.llmretry retries each failed pruner call at the litellm entry point at most
  twice (5 s, 20 s); BudgetExceeded is never retried and stops the step. A search that still raises stops its run
  (technical abort): the run is void and repeated once at once (label _repeat), both reported, the repeat used; a
  run with more than 5% of its LLM calls failed after the retries is void the same way; a second void attempt stops
  the step (exit 2).
Results (staging/s7/results/): c3_per_query.json (every attempt's per-query records), c3_results.json (decisions,
  determinism, descriptive tables, wording), s7_run_summary.json; transcript staging/s7/transcripts/s7_transcript.md.
"""
from __future__ import annotations

import logging
import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import runlog  # noqa: E402  (first: redaction before anything else)

RUN = runlog.start("s7", need_oci=True)

from lib import amconf, c1db, c3run, llmretry, meter, stores  # noqa: E402

LITELLM = amconf.configure_litellm()

from oracleagentmemory.core import (MemoryExtractionConfig, PruningEvaluationMode,  # noqa: E402
                                    PruningMemorySearchConfig, SchemaPolicy, SearchStrategy,
                                    TopKMemorySearchConfig)
from oracleagentmemory.observability import register_event_listeners  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data" / "frozen"
QUERIES = c3run.load_queries(DATA / "c3_queries.jsonl")
OVERLAP_MSG = "Extended pruning evaluation regions overlap"      # core/_postsearch/_pruning.pyc:295 (warnings.warn)
warnings.filterwarnings("always", message=OVERLAP_MSG)            # count every occurrence (default shows it once)


class StepStop(RuntimeError):
    """RV_VEC is not as step 4 left it: nothing is searched, the step exits 1."""


class BothVoid(RuntimeError):
    """A run and its one repeat were both void: the plan has no third attempt; the step exits 2."""


class OverlapCounter(logging.Handler):
    """Counts EXTENDED's overlap warning (captured by logging into 'py.warnings'); keeps nothing else."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if OVERLAP_MSG in record.getMessage():
                self.count += 1
        except Exception:                 # a malformed record is not ours to fix; it is still logged by root
            pass


def search_config(arm: str, llm):
    if arm in c3run.TOPK_ARMS:
        return TopKMemorySearchConfig(max_results=c3run.MAX_RESULTS[arm])
    mode = {"PRUNE_FAST": PruningEvaluationMode.FAST, "PRUNE_EXT": PruningEvaluationMode.EXTENDED,
            "PRUNE_EXH": PruningEvaluationMode.EXHAUSTIVE}[arm]
    kw = {"pruner": llm, "evaluation_mode": mode, "protected_fraction": 0.1}
    if arm == "PRUNE_EXT":
        kw["num_probe_points"] = 3
    return PruningMemorySearchConfig(**kw)


def readback(pool) -> dict:
    """Read-only state of RV_VEC (fixed table names)."""
    with pool.acquire() as conn, conn.cursor() as cur:
        cur.execute("select count(*), count(distinct record_id), sum(case when status = 'valid' then 1 else 0 end) "
                    "from RV_VEC_MEMORY where user_id = :u", u=c3run.USER_ID)
        n, nd, nv = cur.fetchone()
        cur.execute("select count(*) from RV_VEC_MEMORY where user_id is null or user_id <> :u", u=c3run.USER_ID)
        other = cur.fetchone()[0]
        cur.execute("select count(*) from RV_VEC_MEMORY where expires_at is not null")
        ttl = cur.fetchone()[0]
        cur.execute("select count(*), count(distinct source_id), sum(case when embedding is not null then 1 else 0 end) "
                    "from RV_VEC_RECORD_CHUNKS where source_record_type in ('fact', 'guideline', 'memory', 'preference')")
        cn, cd, ce = cur.fetchone()
        cov = c1db.coverage(conn, c3run.STORE_ID, c3run.USER_ID)       # identity, not only counts (as step 4)
    return {"memories": {"rows": n, "distinct_ids": nd, "valid": nv or 0, "other_users": other, "with_ttl": ttl},
            "chunks": {"rows": cn, "distinct_sources": cd, "with_embedding": ce or 0}, "coverage": cov}


def snap(m, lc, rs) -> dict:
    ms, cs = m.summary(), lc.summary()
    return {"llm_calls": ms["llm_calls"], "tok_in": ms["tokens"].get("input", 0),
            "tok_out": ms["tokens"].get("output", 0), "tok_total": ms["tokens"].get("total", 0),
            "lc_in": cs["tokens"].get("input", 0), "lc_out": cs["tokens"].get("output", 0),
            "lc_total": cs["tokens"].get("total", 0), "retry": rs.snapshot()}


def delta(a: dict, b: dict) -> dict:
    ra, rb = a["retry"], b["retry"]
    return {"calls": max(b["llm_calls"] - a["llm_calls"], rb["calls"] - ra["calls"]),
            "attempts": rb["attempts"] - ra["attempts"],
            "failed_after_retries": rb["failed_after_retries"] - ra["failed_after_retries"],
            "input": max(b["tok_in"] - a["tok_in"], b["lc_in"] - a["lc_in"]),
            "output": max(b["tok_out"] - a["tok_out"], b["lc_out"] - a["lc_out"]),
            "total": max(b["tok_total"] - a["tok_total"], b["lc_total"] - a["lc_total"])}


def one_run(arm: str, rnd: int, suffix: str, client, ctr, oc: OverlapCounter) -> tuple[dict, list]:
    """One run of one arm over the 40 queries; returns (attempt record, per-query records)."""
    m, lc, rs = ctr
    label = f"{arm.lower()}_r{rnd}{suffix}"
    att = {"arm": arm, "run": rnd, "label": label, "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    recs: list = []
    s0 = snap(*ctr)
    aborted = None
    for q in QUERIES:
        a = snap(*ctr)
        o0 = oc.count
        t = time.perf_counter()
        try:
            res = client.search(q["text"], user_id=c3run.USER_ID, max_results=c3run.MAX_RESULTS[arm], token_budget=0,
                                soft_token_budget=0, num_hops=0)
        except meter.BudgetExceeded:
            raise
        except Exception as exc:              # after the call-level retries: this run is void (technical abort)
            aborted = {"qid": q["qid"], "error_type": type(exc).__name__, "error": str(exc)[:500]}
            RUN.log.error("%s %s: search raised %s", label, q["qid"], type(exc).__name__)
            break
        ms = (time.perf_counter() - t) * 1000.0
        d = delta(a, snap(*ctr))
        ids = [str(r.id) for r in res]
        rec = {"qid": q["qid"], "arm": arm, "run": rnd, "label": label, "returned_ids": ids,
               "gold_memory_ids": q["gold_memory_ids"], "latency_ms": round(ms, 3),
               "pruner_calls": d["calls"], "pruner_attempts": d["attempts"],
               "pruner_failed_after_retries": d["failed_after_retries"], "pruner_input_tokens": d["input"],
               "pruner_output_tokens": d["output"], "pruner_total_tokens": d["total"],
               "overlap_warnings": oc.count - o0}
        rec.update(c3run.score(ids, [r.formatted_content for r in res], q["gold_memory_ids"]))
        recs.append(rec)
    dd = delta(s0, snap(*ctr))
    att.update({"queries_done": len(recs), "llm_calls": dd["calls"], "failed_after_retries": dd["failed_after_retries"],
                "tokens": dd["total"], "aborted": aborted,
                "void": c3run.void_reasons(dd["failed_after_retries"], dd["calls"], aborted is not None)})
    if not att["void"] and len(recs) != len(QUERIES):
        att["void"] = [f"only {len(recs)} of {len(QUERIES)} queries"]
    RUN.transcript(f"- {label}: {len(recs)} queries, LLM calls {dd['calls']}, tokens {dd['total']}, void "
                   f"{att['void'] or 'no'}")
    return att, recs


CLOSE_ERRORS: list = []
m = meter.Meter()
ledger = meter.Ledger(amconf.STATE / "token_ledger.json", amconf.TOKEN_BUDGET)
lc = meter.LitellmCounter(meter=m, ledger=ledger)
lc.install()
rs = llmretry.RetryStats()
llmretry.install(rs, no_retry=(meter.BudgetExceeded,))
oc = OverlapCounter()
logging.getLogger("py.warnings").addHandler(oc)
CTR = (m, lc, rs)
design = {"plan": "PLAN v0.3.6 section 7.3 (frozen, unchanged)", "store": c3run.STORE_ID, "user_id": c3run.USER_ID,
          "arms": {a: {"max_results": c3run.MAX_RESULTS[a], "runs": c3run.RUNS[a]} for a in c3run.ARMS},
          "pruning_config": {"protected_fraction": 0.1, "num_probe_points": "3 for PRUNE_EXT only"},
          "call": "search(text, user_id='BIZ_0001', max_results=<arm>, token_budget=0, soft_token_budget=0, "
                  "num_hops=0)",
          "schedule": [[r, a] for r, a in c3run.schedule()],
          "llm": {"model": amconf.chosen_model(), "temperature": 0, "supports_vision": False,
                  "max_concurrent_requests": amconf.MAX_CONCURRENT, "one Llm per pruning arm": True},
          "retries": {"delays_s": list(llmretry.RETRY_DELAYS), "attempt_timeout_s": llmretry.ATTEMPT_TIMEOUT_S,
                      "where": "litellm entry point (lib/llmretry.py), under the SDK's Llm"},
          "void_rule": "PLAN 7.0: a search that raises after the call-level retries stops its run, or > 5% of the "
                       "run's LLM calls failed after the retries; repeated once, both reported, the repeat used"}
attempts: list = []
records: list = []
used: list = []
rc = 0
stop: dict = {}
RUN.transcript("## Step 7 (C3): top-K vs LLM pruning on RV_VEC, 40 set queries")
try:
    try:
        with register_event_listeners([m]):
            pool = amconf.make_pool("AGENTMEM", 1, 4)
            clients: dict = {}
            try:
                rb = readback(pool)
                design["rv_vec_readback"] = rb
                bad = c3run.check_store(rb)
                if bad:
                    raise StepStop("; ".join(bad))
                for arm in c3run.ARMS:
                    llm = (amconf.make_llm(structured_reminder=True if amconf.structured_reminder_needed() else None)
                           if arm in c3run.PRUNING_ARMS else None)
                    clients[arm], info = stores.open_store(
                        pool, c3run.STORE_ID, policy=SchemaPolicy.REQUIRE_EXISTING, embedder=amconf.make_embedder(pool),
                        llm=None, extraction_config=MemoryExtractionConfig(extract_memories=False),
                        strategy=SearchStrategy.VECTOR, search_config=search_config(arm, llm))
                    design.setdefault("open", {})[arm] = {"policy": info.get("policy"),
                                                          "purge_jobs": info.get("purge_jobs")}
                for rnd, arm in c3run.schedule():
                    att, recs = one_run(arm, rnd, "", clients[arm], CTR, oc)
                    attempts.append(att)
                    records.extend(recs)
                    if att["void"]:
                        RUN.transcript(f"- run {att['label']} VOID ({att['void']}): repeated once")
                        att2, recs2 = one_run(arm, rnd, "_repeat", clients[arm], CTR, oc)
                        attempts.append(att2)
                        records.extend(recs2)
                        if att2["void"]:
                            raise BothVoid(f"{att['label']} and its repeat are both void: {att['void']} / "
                                           f"{att2['void']}")
                        recs = recs2
                    used.extend(recs)
                    RUN.write_json("results", "c3_per_query.json", {"attempts": attempts, "records": records})
                with pool.acquire() as conn:
                    design["unlisted_objects_now"] = stores.unlisted_now(conn)
            finally:
                for arm, c in clients.items():
                    try:
                        c.close()
                    except Exception as exc:
                        CLOSE_ERRORS.append({"what": f"client {arm}", "error_type": type(exc).__name__})
                try:
                    pool.close()
                except Exception as exc:
                    CLOSE_ERRORS.append({"what": "pool", "error_type": type(exc).__name__})
    except StepStop as exc:
        stop = {"kind": "stop", "error": str(exc)[:2000]}
        RUN.log.error("step 7 stopped: %s", exc)
        RUN.transcript(f"- STOPPED: {exc}")
        rc = 1
    except BothVoid as exc:
        stop = {"kind": "both_void", "error": str(exc)[:2000]}
        RUN.log.error("step 7: %s", exc)
        RUN.transcript(f"- STOPPED: {exc}")
        rc = 2
    except Exception as exc:                      # budget refusal or a failure outside a run: technical abort
        stop = {"kind": "abort", "error_type": type(exc).__name__, "error": str(exc)[:2000]}
        RUN.log.error("step 7 technical abort: %s", type(exc).__name__)
        RUN.transcript(f"- TECHNICAL ABORT: {type(exc).__name__}: {str(exc)[:300]}")
        rc = 2
finally:
    tok, ncalls = meter.step_tokens(m, lc)
    ledger.commit("s7", tok, ncalls)
    logging.getLogger("py.warnings").removeHandler(oc)

if rc == 0 and CLOSE_ERRORS:
    stop = {"kind": "close_failed", "errors": CLOSE_ERRORS}
    rc = 2
overall = {"meter": m.summary(), "litellm_counter": lc.summary(), "retries": rs.snapshot(),
           "overlap_warnings": oc.count, "ledger_total_after": ledger.total, "client_close_errors": CLOSE_ERRORS,
           "stop": stop}
RUN.write_json("results", "c3_per_query.json", {"design": design, "attempts": attempts, "records": records,
                                                 "overall": overall})
summary = {"exit": rc, "attempts": len(attempts), "void_attempts": [a["label"] for a in attempts if a["void"]],
           "llm_calls": overall["meter"]["llm_calls"], "tokens": tok,
           "failed_after_retries": overall["retries"]["failed_after_retries"],
           "unlisted_objects": len(design.get("unlisted_objects_now", [])), "stop": stop}
det = c3run.determinism(used) if rc == 0 else {}
if rc == 0 and any(det.values()):                 # (Codex 34 r1, finding 2) PLAN 7.3: the two top-K runs MUST match
    stop = {"kind": "topk_not_deterministic", "differing_queries": det}
    RUN.log.error("top-K runs differ: %s", det)
    RUN.transcript(f"- STOPPED: the two top-K runs differ ({det}); no decision is computed")
    RUN.write_json("results", "c3_results.json", {"design": design, "determinism_topk": det, "stop": stop,
                                                  "void_attempts": summary["void_attempts"]})
    summary["stop"] = stop
    rc = 1
    summary["exit"] = rc                          # (Codex 34 r2) the run summary records the real exit
if rc == 0:
    tokens, recall, prec = c3run.per_query_values(used, QUERIES)
    dec = c3run.decisions(tokens, recall)
    words = c3run.wording(dec)
    desc = c3run.describe(used, QUERIES, tokens, recall, prec)
    RUN.write_json("results", "c3_results.json", {"design": design, "decisions": dec, "wording": words,
                                                  "determinism_topk": det, "descriptive": desc,
                                                  "per_query_used": {"tokens": tokens, "recall": recall,
                                                                     "precision": prec},
                                                  "void_attempts": summary["void_attempts"]})
    RUN.transcript("## Per arm (descriptive; pruning arms averaged over 3 runs, top-K run 1)")
    RUN.transcript("| arm | returned tokens | recall | precision | pruner calls/query | pruner tokens/run | "
                   "latency ms median / p90 |")
    RUN.transcript("|---|---|---|---|---|---|---|")
    for d in desc:
        RUN.transcript(f"| {d['arm']} | {d['tokens_sum_used']:.1f} | {d['recall_mean_used']:.3f} | "
                       f"{d['precision_mean_used']:.3f} | {d['pruner_calls_per_query_mean']:.2f} | "
                       f"{d['pruner_total_tokens_per_run_mean']:.0f} | {d['latency_ms_median']:.0f} / "
                       f"{d['latency_ms_p90']:.0f} |")
    RUN.transcript("## Confirmatory bootstrap (10,000 resamples, bounds at 0.05/6)")
    for d in dec:
        RUN.transcript(f"- {d['arm']}: R10 upper {d['r10_upper']:.4f}, D10 lower {d['d10_lower']:.4f} -> (a) "
                       f"{d['decision_a_smaller_without_material_loss']}; D5 lower {d['d5_lower']:.4f}, R5 upper "
                       f"{d['r5_upper']:.4f} -> (b) {d['decision_b_better_than_top5']}")
    RUN.transcript(f"- top-K determinism: differing queries {det}")
    RUN.transcript(f"- wording: {words['sentence']}")
    summary.update({"decisions": {d["arm"]: [d["decision_a_smaller_without_material_loss"],
                                             d["decision_b_better_than_top5"]] for d in dec},
                    "determinism_differences": {k: len(v) for k, v in det.items()}, "wording": words["sentence"]})
RUN.finish(summary)
sys.exit(rc)
