# v1.1 2026-10-12  Brief 12 step 5 (PLAN 6 row 5, section 7.2 frozen): C2 automatic-linking bake-off,
#                  POST_EXTRACTION against DURING_EXTRACTION against DISABLED, 3 modes x 3 runs on the frozen 12
#                  threads, through the SDK exactly as 7.2 says; scoring with the frozen matcher and statistics
#                  through py/lib/c2run.py.
#                  v1.1: (Codex 33 r1, ISSUES 2, both accepted) the export reads metadata with json_serialize(...
#                  returning clob) (the default VARCHAR2 is bounded); each run is scored from the export exactly as
#                  written (after redaction), and whether that equals the unredacted scoring is recorded.
#                  v1.0: first version (phase 2).
"""Step 5. Run on the demo VM from ~/agentmem-poc/code with the POC venv, only through run_all.sh S5 (which verifies
the freeze and regenerates the C2 set with --check first):
    .venv/bin/python py/50_autolink_bakeoff.py

Schedule (PLAN 7.2 "Runs"): R1 POST, DUR, OFF; R2 DUR, OFF, POST; R3 OFF, POST, DUR. One run of one mode:
  1. a new pool; store AL_POST / AL_DUR / AL_OFF (VECTOR, OracleDBEmbedder ASKORACLE.ALL_MINILM_L12_V2) recreated with
     SchemaPolicy.RECREATE inside AGENTMEM by lib.stores (purge jobs disabled, objects recorded in POC_MANIFEST); the
     client has the LLM of PLAN 10.5 (state/llm_choice.json, temperature 0, supports_vision False, 2 concurrent
     requests) and MemoryExtractionConfig(extraction_mode=INLINE, memory_extraction_frequency=2,
     enable_context_summary=False, memory_link_extraction_mode=<mode>); window and token limits are NOT passed (SDK
     defaults -1, 100,000, 100,000); the thread's resolved config is read back and must be exactly that, or the step
     stops before its first message (exit 1);
  2. for each of the 12 threads in thread-id order: create_thread(thread_id, user_id, agent_id='c2_agent',
     memory_extraction_config=<the same>), then the five exchanges in the scripted order, each one
     add_messages([user, assistant]) with the frozen text, timestamp and a fixed message id; after each exchange the
     user's memory and link counts are read back on a separate connection before the next exchange (7.2 sanity);
  3. the store's MEMORY and MEMORY_LINK rows are exported to results/s5/<mode>_r<n>.jsonl (before the store is
     recreated again: each store is recreated only at the start of its next run), then scored per thread with
     lib.matcher.score_thread (lib.c2run.score_run).
Calls and retries: the SDK makes every LLM call inside add_messages. lib.llmretry retries each failed call at the
  litellm entry point at most twice (5 s, 20 s); BudgetExceeded is never retried and stops the step. If an exchange
  still raises after a failed call, the exchange is recorded as failed (its raw messages are stored by the SDK first,
  its derived memories are lost) and the thread goes on. Any other exception inside a run is a technical abort.
Void and repeat (PLAN 7.0, 7.2): a run is void when more than 5% of its LLM calls failed after the retries, when
  DISABLED made a link, when a read-back did not happen, or on a technical abort; a void run is repeated once at
  once (label _repeat), both are reported, and only the repeat is counted. A second void attempt of the same run
  stops the step (exit 2): the plan has no third attempt. No completed run is dropped or re-scored by judgement.
Counting: the allow-listed listener (meter.Meter), the litellm counter (budget pre-check per attempt) and
  llmretry's logical-call counter; the SDK's swallowed failures (link persistence, invalid link or extraction
  responses) are counted by message template only, from the 'oracleagentmemory' logger at DEBUG.
Results (staging/s5/results/): <mode>_r<n>[_repeat].jsonl (exports), c2_runs.json (every attempt, per exchange and
  per thread), c2_results.json (confirmatory test, verdict, descriptive tables, costs), s5_run_summary.json;
  transcript staging/s5/transcripts/s5_transcript.md.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import runlog  # noqa: E402  (first: redaction before anything else)

RUN = runlog.start("s5", need_oci=True)

from lib import amconf, c2run, llmretry, meter, redact, stores  # noqa: E402

LITELLM = amconf.configure_litellm()

from oracleagentmemory.apis.message import Message  # noqa: E402
from oracleagentmemory.core import (MemoryExtractionConfig, MemoryExtractionMode,  # noqa: E402
                                    MemoryLinkExtractionMode, SchemaPolicy, SearchStrategy)
from oracleagentmemory.core.extractors._oneshotextractor import _ResolvedMemoryExtractionConfig  # noqa: E402
from oracleagentmemory.observability import register_event_listeners  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "frozen"
DATA = c2run.load(DATA_DIR)
AGENT_ID = "c2_agent"
SWALLOWED = {                                    # SDK messages for failures it does not raise (wheel string table)
    "Automatic memory-link persistence failed.": "link_persistence_failed",            # core/thread.pyc:778
    "Memory link-resolution response validation failed.": "link_response_invalid",     # _oneshotextractor:1930
    "Memory extraction response validation failed; no memories extracted.": "extraction_response_invalid",  # :1988
    "Thread extraction safe-summary preparation deferred.": "summary_prep_deferred",   # thread.pyc:937
    "Thread extraction summary refresh deferred.": "summary_refresh_deferred",         # thread.pyc:958
}


class StepStop(RuntimeError):
    """The resolved configuration is not PLAN 7.2's: nothing more is run, the step exits 1."""


class BothVoid(RuntimeError):
    """A run and its one repeat were both void: the plan has no third attempt; the step exits 2."""


class SwallowedCounter(logging.Handler):
    """Counts the SDK's swallowed failures by message template only (no arguments, no traceback); records at INFO
    or above are passed on to the run's redacting root handlers."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.counts = {v: 0 for v in SWALLOWED.values()}

    def emit(self, record: logging.LogRecord) -> None:
        key = SWALLOWED.get(record.msg) if isinstance(record.msg, str) else None
        if key:
            self.counts[key] += 1
        if record.levelno >= logging.INFO:
            logging.getLogger().handle(record)


def make_cfg(mode: str) -> MemoryExtractionConfig:
    return MemoryExtractionConfig(extraction_mode=MemoryExtractionMode.INLINE, memory_extraction_frequency=2,
                                  enable_context_summary=False,
                                  memory_link_extraction_mode=MemoryLinkExtractionMode[mode])


def _plain(d: dict) -> dict:
    return {k: (v.value if hasattr(v, "value") else v) for k, v in d.items()}


def resolved_config(thread) -> dict:
    """The thread's resolved extraction settings (read-only; the SDK keeps them on the thread's runtime config)."""
    return _plain(vars(thread._thread_runtime_config.memory_extraction_config))


def snap(m: meter.Meter, lc: meter.LitellmCounter, rs: llmretry.RetryStats, sw: SwallowedCounter) -> dict:
    ms, cs = m.summary(), lc.summary()
    return {"llm_calls": ms["llm_calls"], "llm_failures": ms["llm_failures"],
            "tok_in": ms["tokens"].get("input", 0), "tok_out": ms["tokens"].get("output", 0),
            "tok_total": ms["tokens"].get("total", 0),
            "lc_attempts": cs["calls"].get("acompletion", 0) + cs["calls"].get("completion", 0),
            "lc_in": cs["tokens"].get("input", 0), "lc_out": cs["tokens"].get("output", 0),
            "lc_total": cs["tokens"].get("total", 0), "retry": rs.snapshot(), "swallowed": dict(sw.counts)}


def delta(a: dict, b: dict) -> dict:
    """Counter deltas between two snapshots; tokens take the larger of the listener's and the counter's delta."""
    ra, rb = a["retry"], b["retry"]
    return {"llm_calls": max(b["llm_calls"] - a["llm_calls"], rb["calls"] - ra["calls"]),
            "attempts": rb["attempts"] - ra["attempts"],
            "retried_calls": rb["retried_calls"] - ra["retried_calls"],
            "failed_after_retries": rb["failed_after_retries"] - ra["failed_after_retries"],
            "listener_failures": b["llm_failures"] - a["llm_failures"],
            "input_tokens": max(b["tok_in"] - a["tok_in"], b["lc_in"] - a["lc_in"]),
            "output_tokens": max(b["tok_out"] - a["tok_out"], b["lc_out"] - a["lc_out"]),
            "total_tokens": max(b["tok_total"] - a["tok_total"], b["lc_total"] - a["lc_total"]),
            "swallowed": {k: b["swallowed"][k] - a["swallowed"][k] for k in b["swallowed"]}}


def counts_now(pool, sid: str, user_id: str) -> tuple[int, int]:
    if sid not in c2run.STORE_OF.values():
        raise ValueError(sid)
    with pool.acquire() as conn, conn.cursor() as cur:
        cur.execute(f"select count(*) from {sid}_MEMORY where user_id = :u", u=user_id)
        n_mem = cur.fetchone()[0]
        cur.execute(f"select count(*) from {sid}_MEMORY_LINK where source_memory_user_id = :u", u=user_id)
        n_link = cur.fetchone()[0]
    return int(n_mem), int(n_link)


def _text(v):
    return v.read() if hasattr(v, "read") else v


def export(pool, sid: str) -> list[dict]:
    """Every MEMORY and MEMORY_LINK row of the store (read-only; table names from the fixed store ids only)."""
    if sid not in c2run.STORE_OF.values():
        raise ValueError(sid)
    rows: list[dict] = []
    with pool.acquire() as conn, conn.cursor() as cur:
        cur.execute(f"""select record_id, user_id, thread_id, agent_id, memory_type, status, content, timestamp,
                               json_serialize(metadata returning clob),
                               to_char(sys_extract_utc(created_at), 'YYYY-MM-DD"T"HH24:MI:SS.FF3"Z"')
                          from {sid}_MEMORY order by order_seq""")
        for r in cur.fetchall():
            rows.append({"table": "MEMORY", "record_id": r[0], "user_id": r[1], "thread_id": r[2],
                         "agent_id": r[3], "memory_type": r[4], "status": r[5], "content": _text(r[6]),
                         "timestamp": r[7], "metadata": _text(r[8]), "created_at": r[9]})
        cur.execute(f"""select relation_id, source_memory_id, source_memory_user_id, target_memory_id,
                               target_memory_user_id, relation_type, opposite_relation_type,
                               json_serialize(metadata returning clob),
                               to_char(sys_extract_utc(created_at), 'YYYY-MM-DD"T"HH24:MI:SS.FF3"Z"')
                          from {sid}_MEMORY_LINK order by created_at, relation_id""")
        for r in cur.fetchall():
            rows.append({"table": "MEMORY_LINK", "relation_id": r[0], "source_memory_id": r[1],
                         "source_memory_user_id": r[2], "target_memory_id": r[3], "target_memory_user_id": r[4],
                         "relation_type": r[5], "opposite_relation_type": r[6], "metadata": _text(r[7]),
                         "created_at": r[8]})
    return rows


CLOSE_ERRORS: list = []
PROBE = {"pending": True}
STOPPED: list = []


def _release(client, pool, label: str, *, failing: bool) -> None:
    """Close the client, then the pool, each guarded; a close failure is recorded, and raises only when nothing
    else is failing (so it never replaces the original exception)."""
    n0 = len(CLOSE_ERRORS)
    if client is not None:
        try:
            client.close()
        except Exception as exc:
            CLOSE_ERRORS.append({"what": f"client {label}", "error_type": type(exc).__name__})
    try:
        pool.close()
    except Exception as exc:
        CLOSE_ERRORS.append({"what": f"pool {label}", "error_type": type(exc).__name__})
    if not failing and len(CLOSE_ERRORS) > n0:
        raise RuntimeError(f"closing {label} failed: {CLOSE_ERRORS[n0:]}")


def one_run(run_no: int, mode: str, suffix: str, ctr: tuple) -> dict:
    """One attempt of one (run, mode) slot. Returns its record; 'void' lists the reasons (empty = counted)."""
    m, lc, rs, sw = ctr
    sid = c2run.STORE_OF[mode]
    label = f"{mode.lower()}_r{run_no}{suffix}"
    rec: dict = {"run": run_no, "mode": mode, "store_id": sid, "label": label,
                 "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "threads": [], "void": []}
    RUN.transcript(f"## Run {run_no} {mode} ({label}), store {sid}")
    s_run = snap(*ctr)
    pool = amconf.make_pool("AGENTMEM", 1, 4)
    client = None
    exchange_failures = 0
    readback_ok = True
    try:
        emb = amconf.make_embedder(pool)
        llm = amconf.make_llm(structured_reminder=True if amconf.structured_reminder_needed() else None)
        cfg = make_cfg(mode)
        t = time.perf_counter()
        client, info = stores.open_store(pool, sid, policy=SchemaPolicy.RECREATE, embedder=emb, llm=llm,
                                         extraction_config=cfg, strategy=SearchStrategy.VECTOR)
        rec["store_open_seconds"] = round(time.perf_counter() - t, 3)
        rec["store_open"] = info
        RUN.transcript(f"- {sid} recreated in {rec['store_open_seconds']} s; manifest recorded "
                       f"{info.get('manifest', {}).get('recorded')}, unlisted {info.get('manifest', {}).get('unlisted')}; "
                       f"purge jobs {[(j['job_name'], j['enabled_after']) for j in info.get('purge_jobs', [])]}")
        for th_data in DATA["threads"]:
            tid, uid = th_data["thread_id"], th_data["user_id"]
            th = client.create_thread(thread_id=tid, user_id=uid, agent_id=AGENT_ID, memory_extraction_config=cfg)
            try:
                resolved = resolved_config(th)
            except Exception as exc:              # cannot prove the config: stop before any message (no repeat)
                raise StepStop(f"{tid}: resolved extraction config unreadable ({type(exc).__name__})") from None
            bad = c2run.check_resolved_config(resolved, mode)
            if bad:
                rec["resolved_config_bad"] = {"thread_id": tid, "resolved": resolved, "problems": bad}
                raise StepStop(f"{tid}: resolved extraction config is not PLAN 7.2's: {bad}")
            if tid == DATA["threads"][0]["thread_id"]:
                rec["resolved_config"] = resolved
                RUN.transcript(f"- resolved config ({tid}): {json.dumps(resolved, sort_keys=True, default=str)}")
            trec = {"thread_id": tid, "user_id": uid, "exchanges": []}
            prev_mem = 0
            for i, ex in enumerate(c2run.exchanges(th_data["messages"]), 1):
                msgs = [Message(role=x["role"], content=x["content"], timestamp=x["timestamp"],
                                id=f"{tid}_msg{x['seq']:02d}") for x in ex]
                a = snap(*ctr)
                t = time.perf_counter()
                err = None
                try:
                    th.add_messages(msgs)
                except meter.BudgetExceeded:
                    raise
                except Exception as exc:          # an LLM call that failed after the retries, else re-raised
                    if rs.snapshot()["failed_after_retries"] > a["retry"]["failed_after_retries"]:
                        err = {"error_type": type(exc).__name__, "error": str(exc)[:300]}
                        exchange_failures += 1
                        RUN.log.warning("%s exchange %d: extraction failed after the retries (%s)", tid, i,
                                        type(exc).__name__)
                    else:
                        raise
                secs = time.perf_counter() - t
                b = snap(*ctr)
                try:
                    n_mem, n_link = counts_now(pool, sid, uid)      # 7.2 sanity: read back before the next one
                except Exception:
                    readback_ok = False
                    raise
                d = delta(a, b)
                xr = {"exchange": i, "seconds": round(secs, 3), "memories_after": n_mem, "links_after": n_link,
                      "new_memories": n_mem - prev_mem, **d}
                if err:
                    xr["failed"] = err
                prev_mem = n_mem
                trec["exchanges"].append(xr)
            rec["threads"].append(trec)
            if PROBE["pending"]:                  # first thread of the first run: prove the export and scoring work
                try:                              # before more LLM work is spent (a failure stops, no repeat)
                    c2run.score_run(export(pool, sid), DATA)
                except Exception as exc:
                    raise StepStop(f"export/scoring probe after {tid} failed ({type(exc).__name__}: "
                                   f"{str(exc)[:300]})") from None
                PROBE["pending"] = False
                RUN.transcript(f"- export and scoring probe after {tid}: ok")
            last = trec["exchanges"][-1]
            RUN.transcript(f"  - {tid}: memories {last['memories_after']}, links {last['links_after']}, LLM calls "
                           f"{sum(x['llm_calls'] for x in trec['exchanges'])}, failed exchanges "
                           f"{sum(1 for x in trec['exchanges'] if 'failed' in x)}")
        rows = export(pool, sid)
    except (StepStop, meter.BudgetExceeded) as exc:
        _release(client, pool, label, failing=True)
        rec["stopped"] = {"error_type": type(exc).__name__, "error": str(exc)[:1000]}
        rec["cost_so_far"] = delta(s_run, snap(*ctr))
        STOPPED.append(rec)                       # the step's results keep the stopped attempt too
        raise
    except Exception as exc:                      # technical abort of this attempt (7.0): void, repeated once
        _release(client, pool, label, failing=True)
        rec["technical_abort"] = {"error_type": type(exc).__name__, "error": str(exc)[:1000]}
        rec["void"] = [f"technical abort: {type(exc).__name__}"]
        rec["cost_so_far"] = delta(s_run, snap(*ctr))
        RUN.log.error("run %s technical abort: %s", label, type(exc).__name__)
        RUN.transcript(f"- TECHNICAL ABORT: {type(exc).__name__}: {str(exc)[:300]}")
        return rec
    _release(client, pool, label, failing=False)
    raw = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False, default=str) + "\n" for r in rows)
    red = redact.redact(raw)
    path = RUN.write_text("results", f"{label}.jsonl", raw)      # write_text applies the same redact(): red is the file
    try:                                                         # (Codex 33 r1, finding 2) score what is published
        written, scored_from = [json.loads(x) for x in red.splitlines()], "export as written"
    except ValueError:                                           # a redaction pattern broke a JSON line: recorded
        written, scored_from = rows, "unredacted rows (redaction broke the JSON of the export)"
    rec["export"] = {"file": path.name, "rows": len(rows),
                     "memories": sum(1 for r in rows if r["table"] == "MEMORY"),
                     "links": sum(1 for r in rows if r["table"] == "MEMORY_LINK"),
                     "sha256_as_written": hashlib.sha256(red.encode("utf-8")).hexdigest(),
                     "lines_changed_by_redaction": sum(1 for x, y in zip(raw.splitlines(), red.splitlines())
                                                       if x != y)}
    rec["per_thread"] = c2run.score_run(written, DATA)
    rec["totals"] = c2run.run_totals(rec["per_thread"])
    rec["export"]["scores_equal_unredacted"] = c2run.score_run(rows, DATA) == rec["per_thread"]
    rec["export"]["scored_from"] = scored_from
    rec["cross_user_links"] = c2run.cross_user_links(written)
    run_d = delta(s_run, snap(*ctr))
    xs = [x["seconds"] for t_ in rec["threads"] for x in t_["exchanges"]]
    rec["cost"] = {"llm_calls": run_d["llm_calls"], "input_tokens": run_d["input_tokens"],
                   "output_tokens": run_d["output_tokens"], "total_tokens": run_d["total_tokens"],
                   "wall_seconds": round(sum(xs), 3), "exchange_seconds_mean": round(sum(xs) / len(xs), 3),
                   "attempts": run_d["attempts"], "retried_calls": run_d["retried_calls"],
                   "failed_after_retries": run_d["failed_after_retries"], "swallowed": run_d["swallowed"],
                   "failed_exchanges": exchange_failures}
    rec["void"] = c2run.void_reasons(mode, written, run_d["failed_after_retries"], run_d["llm_calls"],
                                     exchange_failures, readback_ok)
    tt = rec["totals"]
    RUN.transcript(f"- export {rec['export']['memories']} memories, {rec['export']['links']} links; TP {tt['tp']} "
                   f"FP {tt['fp']} FN {tt['fn']} (P {tt['precision']:.3f} R {tt['recall']:.3f} F1 {tt['f1']:.3f}); "
                   f"harmful {tt['harmful_invalidations']}, distractor links {tt['distractor_links']}, slot coverage "
                   f"{tt['slots_covered']}/{tt['slots_total']}; LLM calls {rec['cost']['llm_calls']}, tokens "
                   f"{rec['cost']['total_tokens']}, {rec['cost']['wall_seconds']} s; void: {rec['void'] or 'no'}")
    return rec


m = meter.Meter()
ledger = meter.Ledger(amconf.STATE / "token_ledger.json", amconf.TOKEN_BUDGET)
lc = meter.LitellmCounter(meter=m, ledger=ledger)
lc.install()
rs = llmretry.RetryStats()
llmretry.install(rs, no_retry=(meter.BudgetExceeded,))
sw = SwallowedCounter()
sdk_logger = logging.getLogger("oracleagentmemory")
sdk_logger.addHandler(sw)
sdk_logger.setLevel(logging.DEBUG)
sdk_logger.propagate = False                      # DEBUG lines stay out of the run log; INFO+ forwarded by the counter
CTR = (m, lc, rs, sw)

design = {"plan": "PLAN v0.3.6 section 7.2 (frozen, unchanged)", "stores": c2run.STORE_OF,
          "run_order": [[r, list(ms)] for r, ms in c2run.RUN_ORDER], "agent_id": AGENT_ID,
          "config": {"extraction_mode": "INLINE", "memory_extraction_frequency": 2, "enable_context_summary": False,
                     "memory_link_extraction_mode": "<mode>", "not_passed": sorted(c2run.SDK_DEFAULTS)},
          "resolved_offline": {mode: _plain(vars(_ResolvedMemoryExtractionConfig.from_public_config(make_cfg(mode))))
                               for mode in c2run.MODES},
          "llm": {"model": amconf.chosen_model(), "temperature": 0, "supports_vision": False,
                  "max_concurrent_requests": amconf.MAX_CONCURRENT,
                  "structured_reminder": amconf.structured_reminder_needed()},
          "retries": {"delays_s": list(llmretry.RETRY_DELAYS), "attempt_timeout_s": llmretry.ATTEMPT_TIMEOUT_S,
                      "where": "litellm entry point (lib/llmretry.py), under the SDK's Llm; reading of PLAN 10.5 "
                               "for calls the SDK makes inside add_messages, decided before the run"},
          "void_rule": "PLAN 7.0: > 5% of a run's LLM calls failed after the retries, a database error, or a 7.2 "
                       "sanity check (DISABLED made a link; memory count not read back after an exchange); a void "
                       "run is repeated once, both reported, the repeat counted",
          "threads": c2run.N_THREADS, "messages": sum(len(t["messages"]) for t in DATA["threads"]),
          "gold_links": c2run.N_GOLD, "distractor_pairs": c2run.N_DISTRACTORS}
attempts: list = []
counted: dict = {}
rc = 0
stop: dict = {}
RUN.transcript("## Step 5 (C2): POST_EXTRACTION vs DURING_EXTRACTION vs DISABLED, 3 runs each, 12 threads")
RUN.transcript(f"- design: {json.dumps({k: design[k] for k in ('run_order', 'config', 'llm', 'retries')}, default=str)}")
try:
    try:
        with register_event_listeners([m]):
            for run_no, mode in c2run.schedule():
                rec = one_run(run_no, mode, "", CTR)
                attempts.append(rec)
                if rec["void"]:
                    RUN.transcript(f"- run {rec['label']} VOID ({rec['void']}): repeated once")
                    rec2 = one_run(run_no, mode, "_repeat", CTR)
                    attempts.append(rec2)
                    if rec2["void"]:
                        raise BothVoid(f"{rec['label']} and its repeat are both void: {rec['void']} / {rec2['void']}")
                    rec = rec2
                counted[(run_no, mode)] = rec
                RUN.write_json("results", "c2_runs.json", {"design": design, "attempts": attempts})
        pool = amconf.make_pool("AGENTMEM", 1, 1)
        try:
            with pool.acquire() as conn:
                design["unlisted_objects_now"] = stores.unlisted_now(conn)
        finally:
            pool.close()
    except StepStop as exc:
        stop = {"kind": "stop", "error": str(exc)[:2000]}
        RUN.log.error("step 5 stopped: %s", exc)
        RUN.transcript(f"- STOPPED: {exc}")
        rc = 1
    except BothVoid as exc:
        stop = {"kind": "both_void", "error": str(exc)[:2000]}
        RUN.log.error("step 5: %s", exc)
        RUN.transcript(f"- STOPPED: {exc}")
        rc = 2
    except Exception as exc:                      # budget refusal or a failure outside a run: technical abort
        stop = {"kind": "abort", "error_type": type(exc).__name__, "error": str(exc)[:2000]}
        RUN.log.error("step 5 technical abort: %s", type(exc).__name__)
        RUN.transcript(f"- TECHNICAL ABORT: {type(exc).__name__}: {str(exc)[:300]}")
        rc = 2
finally:
    tok, ncalls = meter.step_tokens(m, lc)
    ledger.commit("s5", tok, ncalls)
    sdk_logger.removeHandler(sw)
    sdk_logger.propagate = True
    sdk_logger.setLevel(logging.NOTSET)

overall = {"meter": m.summary(), "litellm_counter": lc.summary(), "retries": rs.snapshot(), "swallowed": sw.counts,
           "ledger_total_after": ledger.total, "client_close_errors": CLOSE_ERRORS, "stop": stop}
RUN.write_json("results", "c2_runs.json", {"design": design, "attempts": attempts + STOPPED, "overall": overall})
summary = {"exit": rc, "attempts": len(attempts), "counted_runs": len(counted),
           "void_attempts": [a["label"] for a in attempts if a["void"]], "llm_calls": overall["meter"]["llm_calls"],
           "tokens": tok, "failed_after_retries": overall["retries"]["failed_after_retries"],
           "unlisted_objects": len(design.get("unlisted_objects_now", [])), "stop": stop}
if rc == 0 and len(counted) == 9:
    conf = c2run.confirmatory(counted, DATA)
    cost = c2run.costs(counted)
    ptype = c2run.per_type(counted, DATA)
    per_run = {f"{mode}_r{rn}": {**counted[(rn, mode)]["totals"], "cost": counted[(rn, mode)]["cost"],
                                 "label": counted[(rn, mode)]["label"]}
               for rn, mode in c2run.schedule()}
    RUN.write_json("results", "c2_results.json", {"design": design, "confirmatory": conf, "per_run": per_run,
                                                  "costs": cost, "per_type_described": ptype,
                                                  "void_attempts": summary["void_attempts"]})
    RUN.transcript("## Per run (descriptive)")
    RUN.transcript("| run | TP | FP | FN | P | R | F1 | harmful | distractor | pair recall | slots | calls | tokens | s |")
    RUN.transcript("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, v in per_run.items():
        RUN.transcript(f"| {k} | {v['tp']} | {v['fp']} | {v['fn']} | {v['precision']:.3f} | {v['recall']:.3f} | "
                       f"{v['f1']:.3f} | {v['harmful_invalidations']} | {v['distractor_links']} | "
                       f"{v['pair_recall']:.3f} | {v['slots_covered']}/{v['slots_total']} | {v['cost']['llm_calls']} | "
                       f"{v['cost']['total_tokens']} | {v['cost']['wall_seconds']} |")
    RUN.transcript(f"## Confirmatory: micro-F1 POST {conf['micro_f1_post']:.4f}, DURING {conf['micro_f1_during']:.4f}, "
                   f"T {conf['t_observed']:.4f}, p {conf['p']:.4f} ({conf['n_extreme']}/{conf['n_assignments']}); "
                   f"harmful POST {conf['harmful_post_runs']}, DURING {conf['harmful_during_runs']}")
    RUN.transcript(f"- verdict: {conf['verdict']}")
    summary.update({"micro_f1_post": conf["micro_f1_post"], "micro_f1_during": conf["micro_f1_during"],
                    "p": conf["p"], "verdict": conf["verdict"]})
RUN.finish(summary)
sys.exit(rc)
