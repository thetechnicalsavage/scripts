# v1.2 2026-10-10  Brief 12 step 2 (PLAN 6 row 2, section 3; API_NOTES B4, B16, B17, B21, B22, B15): the five link
#                  types on store DEMO as step 1 left it, then the same seeds in DEMO_AUTO with an LLM attached.
#                  v1.2: (Codex review r2, finding 1) the memory just added is attributed too when its stored text
#                  differs from the seed (before = None).
#                  v1.1: (Codex review r1, BLOCKING 3) DEMO's start state must equal section 3 (statuses and the six
#                  links) or nothing is changed; after Part A, DEMO must be restored exactly (statuses, links with
#                  reverse labels, invalidation reasons; an accepted A1 undone) or Part B is not run; either stop
#                  writes the results and exits 1. Per-add tokens take the larger of the listener's and the litellm
#                  counter's deltas; content rewrites are attributed to the add that made them (previous vs now).
#                  v1.0: first version (day 1; drafted in the first continuation run, completed and first reviewed on
#                  the restart: the swallowed-failure logger and message checked in the wheel, per-add litellm counts).
"""Step 2. Run on the demo VM from ~/agentmem-poc/code with the POC venv, only through run_all.sh S2:
    .venv/bin/python py/20_link_types.py

Part A, store DEMO (opened with REQUIRE_EXISTING: never recreated here; client WITHOUT an LLM, extraction off, so
Part A makes no LLM call). Before and after every call, every hero memory's status and invalidation_reason and every
link are read from DEMO_MEMORY / DEMO_MEMORY_LINK. Calls, in this order:
  A1. link_records(M1 -> M2, "supersedes"): the reverse of the stored M2 -> M1 link on the same pair.
      Prediction (B22): rejected ("A conflicting memory relation already exists."); the error is kept verbatim. If it
      is accepted instead, that is reported as refuted and the new link is deleted by its relation id at once.
  A2. update_record_link(<M8 -> M7>, relation_type="supports"). Prediction (B21): 1 row; M7 becomes valid and its
      invalidation_reason is gone; the reverse label becomes is_supported_by.
  A3. delete_record_link(relation_id=<M2 -> M1>). Prediction (B4): 1 row; M1 becomes valid (the AFTER DELETE trigger).
  R1. update_record_link(<M8 -> M7>, relation_type="refines") and R2. link_records(M2 -> M1, "supersedes"): the
      original links restored. Then the statuses and the link set (source, target, type, reverse label) are compared
      with the state read at the start of step 2 (= DEMO as step 1 left it) and with section 3's expectation.
      Relation ids and created_at of the two restored links differ by construction and are listed, not compared.
  A1 runs while M2 -> M1 still exists (PLAN 6 names it "on the same pair"); A3 deletes that link only afterwards.
Part B, store DEMO_AUTO (RECREATE; a NEW pool, so the package reinstall of RECREATE cannot invalidate Part A's
sessions; Part A's client and pool are closed first). The same ten seeds, added in time order
(hero_seed.memories_in_time_order) with add_memory(content, memory_type, user_id, memory_id, timestamp) and
autonomous_linking NOT passed (B16: an LLM is attached, so it defaults to on), the LLM of PLAN 10.5 (state/
llm_choice.json, temperature 0, supports_vision False). After each add: the new links, every status, any content the
resolver rewrote (B15), LLM calls and tokens of that add. At the end: every link the LLM added, classified against the
hero's six (same direction and type, same pair with another type or direction, or not in the hero), and whether an
invalidating link (supersedes, refines, duplicates) points at a memory the hero timeline marks current (valid in
section 3's final state): a harmful invalidation. One demonstration run, not a measurement (PLAN 6 row 2); no retry,
because a repeated add_memory would duplicate the memory (PLAN 10.5's two retries apply to calls the step script
makes itself; here the LLM call is the SDK's own, inside add_memory). Automatic-link failures the SDK swallows are
counted by message only: OracleAgentMemory._resolve_new_memory_links catches Exception and calls
logger.debug('Automatic memory-link resolution failed.') on getLogger('oracleagentmemory.core.oracleagentmemory'),
without exc_info (disassembled from the installed wheel); add_memory runs that resolver synchronously.
Stops (exit 1, results still written): DEMO's start state differs from section 3 (nothing is changed); DEMO is not
restored exactly after Part A (Part B is not run). A refuted prediction is a result, not a stop.
Results: staging/s2/results/s2_link_types.json; transcript staging/s2/transcripts/.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import runlog  # noqa: E402  (first: redaction before anything else)

RUN = runlog.start("s2", need_oci=True)

from lib import amconf, hero_seed, meter, stores  # noqa: E402

LITELLM = amconf.configure_litellm()

from oracleagentmemory.core import MemoryExtractionConfig, SchemaPolicy, SearchStrategy  # noqa: E402
from oracleagentmemory.observability import register_event_listeners  # noqa: E402

hero_seed.validate()
MEM = hero_seed.by_key()
KEY_OF = {m.memory_id: m.key for m in hero_seed.MEMORIES}
CURRENT = {k for k, s in hero_seed.expected_statuses().items() if s == "valid"}     # section 3's final state
GOLD = {(lk.source, lk.target): lk.relation_type for lk in hero_seed.LINKS}
SDK_LINK_FAILURE = "Automatic memory-link resolution failed."


class StepStop(RuntimeError):
    """A precondition or the restore failed: the results are written and the step exits 1."""


def key(rid) -> str:
    return KEY_OF.get(str(rid), str(rid))


class SwallowedFailureCounter(logging.Handler):
    """Counts the SDK's swallowed automatic-link failures by message template only (no arguments, no traceback);
    records at INFO or above are passed on to the run's redacting root handlers."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.msg == SDK_LINK_FAILURE:
            self.count += 1
        if record.levelno >= logging.INFO:
            logging.getLogger().handle(record)


def state(pool, store: str) -> dict:
    with pool.acquire() as c, c.cursor() as cur:
        cur.execute(f"""select record_id, memory_type, status,
                               json_value(metadata, '$."$agent_memory"."invalidation_reason"'),
                               dbms_lob.substr(content, 4000, 1)
                          from {store}_MEMORY where user_id = :u order by record_id""", u=hero_seed.USER_ID)
        mems = {key(r[0]): {"memory_type": r[1], "status": r[2], "invalidation_reason": r[3], "content": r[4]}
                for r in cur.fetchall()}
        cur.execute(f"""select relation_id, source_memory_id, target_memory_id, relation_type, opposite_relation_type,
                               to_char(sys_extract_utc(created_at), 'YYYY-MM-DD"T"HH24:MI:SS.FF3"Z"')
                          from {store}_MEMORY_LINK order by created_at, relation_id""")
        links = [{"relation_id": r[0], "source": key(r[1]), "target": key(r[2]), "relation_type": r[3],
                  "opposite": r[4], "created_at": r[5]} for r in cur.fetchall()]
    return {"memories": mems, "links": links}


def statuses(st: dict) -> dict:
    return {k: v["status"] for k, v in st["memories"].items()}


def link_set(st: dict) -> set[tuple]:
    return {(x["source"], x["target"], x["relation_type"], x["opposite"]) for x in st["links"]}


def rel_id(st: dict, src: str, dst: str) -> str:
    ids = [x["relation_id"] for x in st["links"] if x["source"] == src and x["target"] == dst]
    if len(ids) != 1:
        raise RuntimeError(f"expected exactly one link {src}->{dst}, found {len(ids)}")
    return ids[0]


def changed(before: dict, after: dict) -> dict:
    out = {}
    for k in sorted(set(before["memories"]) | set(after["memories"]), key=lambda x: (len(x), x)):
        b, a = before["memories"].get(k, {}), after["memories"].get(k, {})
        if (b.get("status"), b.get("invalidation_reason")) != (a.get("status"), a.get("invalidation_reason")):
            out[k] = {"status": [b.get("status"), a.get("status")],
                      "invalidation_reason": [b.get("invalidation_reason"), a.get("invalidation_reason")]}
    return out


def call(label: str, fn, pool, store: str) -> dict:
    """Run one SDK call between two state reads; a raised error is a result (kept verbatim, redacted on write)."""
    before = state(pool, store)
    rec: dict = {"call": label}
    t = time.perf_counter()
    try:
        rec["returned"] = fn()
        rec["ok"] = True
    except Exception as exc:                      # the refusal itself is what A1 tests
        rec["ok"] = False
        rec["error_type"] = type(exc).__name__
        rec["error"] = str(exc)[:1000]
    rec["ms"] = round((time.perf_counter() - t) * 1000, 1)
    after = state(pool, store)
    rec["status_changes"] = changed(before, after)
    rec["links_added"] = sorted(link_set(after) - link_set(before))
    rec["links_removed"] = sorted(link_set(before) - link_set(after))
    rec["statuses_after"] = statuses(after)
    RUN.transcript(f"- {label}: {'ok, returned ' + repr(rec.get('returned')) if rec['ok'] else rec['error_type'] + ': ' + rec['error']}"
                   f"; status changes {rec['status_changes'] or 'none'}")
    return rec


m = meter.Meter()
ledger = meter.Ledger(amconf.STATE / "token_ledger.json", amconf.TOKEN_BUDGET)
lc = meter.LitellmCounter(meter=m, ledger=ledger)
lc.install()
swallowed = SwallowedFailureCounter()
out: dict = {"user_id": hero_seed.USER_ID}
rc = 0
try:
    try:
        with register_event_listeners([m]):
            # ---------------- Part A: DEMO as step 1 left it ----------------
            pool_a = amconf.make_pool("AGENTMEM", 1, 4)
            client = None
            try:
                emb = amconf.make_embedder(pool_a)
                client, info = stores.open_store(pool_a, hero_seed.STORE_ID, policy=SchemaPolicy.REQUIRE_EXISTING,
                                                 embedder=emb, llm=None, strategy=SearchStrategy.VECTOR,
                                                 extraction_config=MemoryExtractionConfig(extract_memories=False))
                out["demo_open"] = info
                RUN.transcript(f"## Step 2, part A: store {hero_seed.STORE_ID} as step 1 left it (REQUIRE_EXISTING, no LLM)")
                s0 = state(pool_a, hero_seed.STORE_ID)
                out["demo_state_start"] = s0
                exp = hero_seed.expected_statuses()
                out["demo_start_matches_section3"] = {
                    "statuses": statuses(s0) == exp,
                    "links": {(x[0], x[1], x[2]) for x in link_set(s0)} == {(lk.source, lk.target, lk.relation_type)
                                                                           for lk in hero_seed.LINKS},
                }
                if not all(out["demo_start_matches_section3"].values()):
                    raise StepStop("DEMO is not as step 1 left it (section 3): nothing changed; ask the operator")
                m1, m2, m7, m8 = MEM["M1"], MEM["M2"], MEM["M7"], MEM["M8"]
                calls = []
                a1 = call("A1 link_records M1 -> M2 supersedes (reverse of M2 -> M1)",
                          lambda: client.link_records(m1.memory_id, m1.memory_type, m2.memory_id, m2.memory_type,
                                                      "supersedes"), pool_a, hero_seed.STORE_ID)
                calls.append(a1)
                if a1["ok"]:                          # refuted: remove the accepted reverse link before going on
                    calls.append(call("A1-undo delete_record_link of the accepted reverse link",
                                      lambda: client.delete_record_link(relation_id=a1["returned"]), pool_a,
                                      hero_seed.STORE_ID))
                r87 = rel_id(state(pool_a, hero_seed.STORE_ID), "M8", "M7")
                a2 = call("A2 update_record_link M8 -> M7 refines to supports",
                          lambda: client.update_record_link(r87, relation_type="supports"), pool_a, hero_seed.STORE_ID)
                calls.append(a2)
                r21 = rel_id(state(pool_a, hero_seed.STORE_ID), "M2", "M1")
                a3 = call("A3 delete_record_link M2 -> M1",
                          lambda: client.delete_record_link(relation_id=r21), pool_a, hero_seed.STORE_ID)
                calls.append(a3)
                rr1 = call("R1 update_record_link M8 -> M7 back to refines",
                           lambda: client.update_record_link(r87, relation_type="refines"), pool_a, hero_seed.STORE_ID)
                calls.append(rr1)
                rr2 = call("R2 link_records M2 -> M1 supersedes (restore)",
                           lambda: client.link_records(m2.memory_id, m2.memory_type, m1.memory_id, m1.memory_type,
                                                       "supersedes"), pool_a, hero_seed.STORE_ID)
                calls.append(rr2)
                s_end = state(pool_a, hero_seed.STORE_ID)
                out["demo_calls"] = calls
                out["demo_state_end"] = s_end
                l7 = [x for x in a2["links_added"] if x[0] == "M8" and x[1] == "M7"]
                out["predictions_demo"] = {
                    "A1_reverse_link_rejected_B22": {"confirmed": not a1["ok"], "error_type": a1.get("error_type"),
                                                     "error": a1.get("error")},
                    "A2_M7_valid_after_refines_to_supports_B21": {
                        "confirmed": a2["ok"] and a2.get("returned") == 1 and a2["statuses_after"].get("M7") == "valid",
                        "returned": a2.get("returned"), "M7_after": a2["statuses_after"].get("M7"),
                        "reverse_label_after": l7[0][3] if l7 else None},
                    "A3_M1_valid_after_delete_B4": {
                        "confirmed": a3["ok"] and a3.get("returned") == 1 and a3["statuses_after"].get("M1") == "valid",
                        "returned": a3.get("returned"), "M1_after": a3["statuses_after"].get("M1")},
                    "restored_equals_step1_state": {
                        "statuses_equal": statuses(s_end) == statuses(s0),
                        "links_equal": link_set(s_end) == link_set(s0),
                        "invalidation_reasons_equal": {k: v["invalidation_reason"] for k, v in s_end["memories"].items()}
                                                      == {k: v["invalidation_reason"] for k, v in s0["memories"].items()},
                        "relation_ids_changed": sorted({x["relation_id"] for x in s_end["links"]}
                                                       ^ {x["relation_id"] for x in s0["links"]}),
                    },
                }
                rp = out["predictions_demo"]["restored_equals_step1_state"]
                undo = [c for c in calls if c["call"].startswith("A1-undo")]
                a1_clean = (not a1["ok"]) or (len(undo) == 1 and undo[0]["ok"] and undo[0].get("returned") == 1)
                out["demo_restored"] = {
                    "statuses_equal": rp["statuses_equal"], "links_equal": rp["links_equal"],
                    "invalidation_reasons_equal": rp["invalidation_reasons_equal"], "a1_rejected_or_undone": a1_clean,
                    "r2_relation_id_returned": bool(rr2["ok"] and isinstance(rr2.get("returned"), str)),
                }
                if not all(out["demo_restored"].values()):
                    raise StepStop("DEMO was not restored to step 1's state: Part B not run; ask the operator")
            finally:
                if client is not None:
                    client.close()
                pool_a.close()

            # ---------------- Part B: DEMO_AUTO with an LLM ----------------
            sdk_logger = logging.getLogger("oracleagentmemory.core.oracleagentmemory")
            sdk_logger.addHandler(swallowed)
            sdk_logger.setLevel(logging.DEBUG)
            sdk_logger.propagate = False              # DEBUG lines stay out of the run log; INFO+ forwarded by the counter
            pool_b = amconf.make_pool("AGENTMEM", 1, 4)
            try:
                emb_b = amconf.make_embedder(pool_b)
                llm = amconf.make_llm(structured_reminder=True if amconf.structured_reminder_needed() else None)
                auto, info_b = stores.open_store(pool_b, hero_seed.STORE_ID_AUTO, policy=SchemaPolicy.RECREATE,
                                                 embedder=emb_b, llm=llm, strategy=SearchStrategy.VECTOR)
                out["demo_auto_open"] = info_b
                out["demo_auto_model"] = amconf.chosen_model()
                RUN.transcript(f"## Step 2, part B: store {hero_seed.STORE_ID_AUTO} recreated, LLM {out['demo_auto_model']}, "
                               "autonomous_linking at its default")
                adds = []
                prev = state(pool_b, hero_seed.STORE_ID_AUTO)
                for mem in hero_seed.memories_in_time_order():
                    calls_before = m.summary()["llm_calls"]
                    tok_before = m.summary()["tokens"].get("total", 0)
                    lct_before = lc.summary()["tokens"].get("total", 0)
                    lc_before = lc.summary()["calls"].get("completion", 0) + lc.summary()["calls"].get("acompletion", 0)
                    fails_before = swallowed.count
                    t = time.perf_counter()
                    rid = auto.add_memory(mem.content, memory_type=mem.memory_type, user_id=hero_seed.USER_ID,
                                          memory_id=mem.memory_id, timestamp=mem.timestamp)
                    ms = round((time.perf_counter() - t) * 1000, 1)
                    now = state(pool_b, hero_seed.STORE_ID_AUTO)
                    stored = now["memories"].get(mem.key, {}).get("content")
                    rec = {"key": mem.key, "returned_id_matches": rid == mem.memory_id, "ms": ms,
                           "llm_calls": m.summary()["llm_calls"] - calls_before,
                           "litellm_completion_calls": (lc.summary()["calls"].get("completion", 0)
                                                        + lc.summary()["calls"].get("acompletion", 0) - lc_before),
                           "tokens_listener": m.summary()["tokens"].get("total", 0) - tok_before,
                           "tokens_litellm": lc.summary()["tokens"].get("total", 0) - lct_before,
                           "swallowed_link_failures": swallowed.count - fails_before,
                           "links_added": sorted(link_set(now) - link_set(prev)),
                           "status_changes": changed(prev, now),
                           "content_rewritten": {k: {"seed": MEM[k].content,
                                                     "before": prev["memories"].get(k, {}).get("content"),
                                                     "stored": v["content"]}
                                                 for k, v in now["memories"].items()
                                                 if (k in prev["memories"]
                                                     and v["content"] != prev["memories"][k]["content"])
                                                 or (k not in prev["memories"] and v["content"] != MEM[k].content)},
                           "stored_equals_seed": stored == mem.content}
                    rec["tokens"] = max(rec["tokens_listener"], rec["tokens_litellm"])
                    adds.append(rec)
                    RUN.transcript(f"- add {mem.key}: links added {[(x[0], x[2], x[1]) for x in rec['links_added']]}, "
                                   f"status changes {rec['status_changes'] or 'none'}, LLM calls {rec['llm_calls']}")
                    prev = now
                final = state(pool_b, hero_seed.STORE_ID_AUTO)
                auto_links = []
                for x in final["links"]:
                    pair = (x["source"], x["target"])
                    if GOLD.get(pair) == x["relation_type"]:
                        vs = "hero link (same direction and type)"
                    elif pair in GOLD:
                        vs = f"hero pair, other type (hero: {GOLD[pair]})"
                    elif (pair[1], pair[0]) in GOLD:
                        vs = f"hero pair reversed (hero: {pair[1]} {GOLD[(pair[1], pair[0])]} {pair[0]})"
                    else:
                        vs = "not a hero pair"
                    inval = x["relation_type"] in hero_seed.INVALIDATING
                    auto_links.append({"source": x["source"], "relation_type": x["relation_type"], "target": x["target"],
                                       "opposite": x["opposite"], "vs_hero": vs, "invalidating": inval,
                                       "target_current_in_hero": x["target"] in CURRENT,
                                       "harmful_invalidation": inval and x["target"] in CURRENT})
                out["demo_auto_adds"] = adds
                out["demo_auto_final"] = final
                out["demo_auto_links"] = auto_links
                out["demo_auto_summary"] = {
                    "links_total": len(auto_links),
                    "hero_links_found": sum(1 for x in auto_links if x["vs_hero"].startswith("hero link")),
                    "hero_links_missed": sorted(f"{s} {t} {d}" for (s, d), t in GOLD.items()
                                                if not any(x["source"] == s and x["target"] == d
                                                           and x["relation_type"] == t for x in auto_links)),
                    "harmful_invalidations": sum(1 for x in auto_links if x["harmful_invalidation"]),
                    "statuses_final": statuses(final),
                    "statuses_vs_section3": {k: {"auto": v, "hero": hero_seed.expected_statuses()[k]}
                                             for k, v in statuses(final).items()
                                             if v != hero_seed.expected_statuses()[k]},
                    "memories_rewritten": sorted({k for a in adds for k in a["content_rewritten"]}),
                    "memories_stored_differently_from_seed": sorted(k for k, v in final["memories"].items()
                                                                    if v["content"] != MEM[k].content),
                    "swallowed_link_failures": swallowed.count,
                    "note": "one demonstration run, not a measurement (PLAN 6 row 2)",
                }
                auto.close()
            finally:
                pool_b.close()
                sdk_logger.removeHandler(swallowed)
                sdk_logger.propagate = True
                sdk_logger.setLevel(logging.NOTSET)
        pool_c = amconf.make_pool("AGENTMEM", 1, 1)
        try:
            with pool_c.acquire() as conn:
                out["unlisted_objects_now"] = stores.unlisted_now(conn)
        finally:
            pool_c.close()
    except StepStop as exc:
        out["stopped"] = str(exc)
        RUN.log.error("step 2 stopped: %s", exc)
        RUN.transcript(f"- STOPPED: {exc}")
        rc = 1
finally:
    tok, ncalls = meter.step_tokens(m, lc)
    ledger.commit("s2", tok, ncalls)

out["meter"] = m.summary()
out["litellm_counter"] = lc.summary()
out["ledger_total_after"] = ledger.total
RUN.write_json("results", "s2_link_types.json", out)
pd = out.get("predictions_demo", {})
RUN.finish({
    "A1_rejected": pd.get("A1_reverse_link_rejected_B22", {}).get("confirmed"),
    "A2_M7_valid": pd.get("A2_M7_valid_after_refines_to_supports_B21", {}).get("confirmed"),
    "A3_M1_valid": pd.get("A3_M1_valid_after_delete_B4", {}).get("confirmed"),
    "restored": pd.get("restored_equals_step1_state"),
    "auto": {k: v for k, v in out.get("demo_auto_summary", {}).items() if k in
             ("links_total", "hero_links_found", "harmful_invalidations", "swallowed_link_failures")},
    "llm_calls": out["meter"]["llm_calls"], "tokens": tok,
    "unlisted_objects": len(out.get("unlisted_objects_now", [])),
    "demo_restored": out.get("demo_restored"), "stopped": out.get("stopped"), "exit": rc,
})
sys.exit(rc)
