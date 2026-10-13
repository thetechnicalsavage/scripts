# v1.0 2026-10-12  Brief 12 step 5 (PLAN 7.2, frozen): pure helpers for the C2 automatic-linking bake-off. They
#                  validate the frozen conversation set, fix the run schedule, turn a store export into per-thread
#                  counts with the frozen matcher, apply 7.0/7.2's void rules and assemble the confirmatory test and
#                  the descriptive tables. No database, no network; every measure is a lib.matcher / lib.stats function.
#                  v1.0: first version (phase 2, before step 5's first run).
"""C2 assembly. Nothing here defines a measure: mapping, TP/FP/FN, harmful invalidations, distractor links, pair
hits and slot coverage are lib.matcher.score_thread; precision/recall/F1 are lib.matcher.prf; micro-F1, the exact
permutation test and the verdict are lib.stats (PLAN 7.0 freeze). This module only feeds them the rows step 5
exported and says, in the plan's words, what the numbers allow the post to say.

Export row (one per MEMORY or MEMORY_LINK row of a store after a run), as py/50_autolink_bakeoff.py writes it:
    {"table": "MEMORY", "record_id", "user_id", "thread_id", "memory_type", "status", "content", ...}
    {"table": "MEMORY_LINK", "relation_id", "source_memory_id", "source_memory_user_id", "target_memory_id",
     "target_memory_user_id", "relation_type", "opposite_relation_type", ...}
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

from lib import matcher, stats

MODES = ("POST_EXTRACTION", "DURING_EXTRACTION", "DISABLED")
STORE_OF = {"POST_EXTRACTION": "AL_POST", "DURING_EXTRACTION": "AL_DUR", "DISABLED": "AL_OFF"}
# PLAN 7.2 "Runs": R1 POST, DUR, OFF; R2 DUR, OFF, POST; R3 OFF, POST, DUR.
RUN_ORDER = ((1, ("POST_EXTRACTION", "DURING_EXTRACTION", "DISABLED")),
             (2, ("DURING_EXTRACTION", "DISABLED", "POST_EXTRACTION")),
             (3, ("DISABLED", "POST_EXTRACTION", "DURING_EXTRACTION")))
N_RUNS = 3
N_THREADS = stats.C2_N_THREADS                 # 12
MESSAGES_PER_THREAD = 10
EXCHANGES_PER_THREAD = 5
N_GOLD = 36
N_DISTRACTORS = 9
TYPE_COUNTS = {"supersedes": 12, "refines": 8, "supports": 6, "contradicts": 5, "duplicates": 5}
EXTRACTION_FREQUENCY = 2                       # PLAN 7.2 config: memory_extraction_frequency=2
SDK_DEFAULTS = {"memory_extraction_window": -1, "memory_extraction_token_limit": 100_000,
                "memory_link_extraction_token_limit": 100_000}      # PLAN 7.2: left at the SDK defaults


class DataError(ValueError):
    """The frozen data is not what PLAN 7.2 describes: the step stops before any database work."""


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                raise DataError(f"{Path(path).name}: empty line {n}")
            rows.append(json.loads(line))
    return rows


def schedule() -> list[tuple[int, str]]:
    """The nine (run, mode) slots in execution order."""
    return [(r, m) for r, modes in RUN_ORDER for m in modes]


def load(frozen_dir: Path) -> dict:
    """The frozen C2 set, validated against PLAN 3 and 7.2. Returns threads in thread-id order and, per thread,
    the matcher's Slot / GoldLink objects and the distractor slot pairs."""
    d = Path(frozen_dir)
    threads = _read_jsonl(d / "c2_threads.jsonl")
    slots = _read_jsonl(d / "c2_slots.jsonl")
    links = _read_jsonl(d / "c2_links.jsonl")
    dx = _read_jsonl(d / "c2_distractors.jsonl")
    if len(threads) != N_THREADS:
        raise DataError(f"{len(threads)} threads, PLAN 7.2 needs {N_THREADS}")
    tids = [t["thread_id"] for t in threads]
    if tids != sorted(tids) or len(set(tids)) != N_THREADS:
        raise DataError("thread ids are not unique and in order")
    users = [t["user_id"] for t in threads]
    if users != [f"CUST_{2000 + i}" for i in range(1, N_THREADS + 1)]:
        raise DataError("thread users are not CUST_2001..CUST_2012 in order")
    if len(links) != N_GOLD or Counter(lk["relation_type"] for lk in links) != Counter(TYPE_COUNTS):
        raise DataError("gold links are not the 36 of PLAN 3 (12/8/6/5/5)")
    if len(dx) != N_DISTRACTORS:
        raise DataError(f"{len(dx)} distractor pairs, PLAN 3 has {N_DISTRACTORS}")
    out = {"threads": [], "by_thread": {}}
    for t in threads:
        tid, uid = t["thread_id"], t["user_id"]
        msgs = t["messages"]
        if len(msgs) != MESSAGES_PER_THREAD:
            raise DataError(f"{tid}: {len(msgs)} messages")
        if [m["seq"] for m in msgs] != list(range(1, MESSAGES_PER_THREAD + 1)):
            raise DataError(f"{tid}: message seq is not 1..10")
        for i, m in enumerate(msgs):
            want_role = "user" if i % 2 == 0 else "assistant"
            if m["role"] != want_role or m["exchange"] != i // 2 + 1:
                raise DataError(f"{tid}: message {m['seq']} is not the scripted user/assistant exchange order")
            if not isinstance(m["content"], str) or not m["content"].strip():
                raise DataError(f"{tid}: empty message {m['seq']}")
        t_slots = [s for s in slots if s["thread_id"] == tid]
        if any(s["user_id"] != uid for s in t_slots):
            raise DataError(f"{tid}: a slot names another user")
        slot_objs = [matcher.Slot(s["slot_id"], tuple(tuple(g) for g in s["key_terms"]), s["end_status"])
                     for s in t_slots]
        gold = [matcher.GoldLink(lk["source_slot"], lk["target_slot"], lk["relation_type"])
                for lk in links if lk["thread_id"] == tid]
        pairs = [(p["slot_a"], p["slot_b"]) for p in dx if p["thread_id"] == tid]
        # the frozen end statuses must be exactly the matcher's rule (7.2 precisions)
        want = matcher.end_statuses([s.slot_id for s in slot_objs], gold)
        if {s.slot_id: s.end_status for s in slot_objs} != want:
            raise DataError(f"{tid}: frozen end statuses differ from matcher.end_statuses")
        out["threads"].append({"thread_id": tid, "user_id": uid, "messages": msgs})
        out["by_thread"][tid] = {"user_id": uid, "slots": slot_objs, "gold": gold, "distractors": pairs}
    if sum(len(v["gold"]) for v in out["by_thread"].values()) != N_GOLD:
        raise DataError("gold links do not all belong to the 12 threads")
    if sum(len(v["slots"]) for v in out["by_thread"].values()) != len(slots):
        raise DataError("slots do not all belong to the 12 threads")
    return out


def exchanges(messages: Sequence[dict]) -> list[list[dict]]:
    """The five exchanges of a thread, two messages each, in the scripted order (PLAN 7.2: one exchange at a time)."""
    ex = [list(messages[i:i + 2]) for i in range(0, len(messages), 2)]
    if len(ex) != EXCHANGES_PER_THREAD or any(len(e) != 2 for e in ex):
        raise DataError("a thread does not split into five two-message exchanges")
    return ex


def check_resolved_config(resolved: Mapping, mode: str) -> list[str]:
    """The thread's resolved extraction settings must be PLAN 7.2's config with the SDK defaults left alone."""
    want = {"extraction_mode": "INLINE", "memory_extraction_frequency": EXTRACTION_FREQUENCY,
            "enable_context_summary": False, "memory_link_extraction_mode": mode, "extract_memories": True,
            **SDK_DEFAULTS}
    bad = []
    for k, v in want.items():
        got = resolved.get(k)
        if isinstance(got, str) and isinstance(v, str):
            ok = got.upper() == v.upper()
        else:
            ok = got == v
        if not ok:
            bad.append(f"{k}: resolved {got!r}, PLAN 7.2 needs {v!r}")
    return bad


def _memories_for(rows: Sequence[dict], user_id: str) -> dict[str, str]:
    return {r["record_id"]: r["content"] for r in rows if r["table"] == "MEMORY" and r["user_id"] == user_id}


def _links_for(rows: Sequence[dict], user_id: str) -> list[matcher.PredictedLink]:
    return [matcher.PredictedLink(r["source_memory_id"], r["target_memory_id"], r["relation_type"])
            for r in rows if r["table"] == "MEMORY_LINK" and r["source_memory_user_id"] == user_id]


def cross_user_links(rows: Sequence[dict]) -> int:
    """Links whose two endpoints belong to different users (never expected: extraction is scoped to one user)."""
    return sum(1 for r in rows if r["table"] == "MEMORY_LINK"
               and r["source_memory_user_id"] != r["target_memory_user_id"])


def score_run(rows: Sequence[dict], data: dict) -> dict[str, dict]:
    """Per-thread scores of one run from its export (lib.matcher.score_thread); thread-id keyed."""
    out = {}
    for t in data["threads"]:
        tid, uid = t["thread_id"], t["user_id"]
        bt = data["by_thread"][tid]
        sc = matcher.score_thread(_memories_for(rows, uid), _links_for(rows, uid), bt["slots"], bt["gold"],
                                  bt["distractors"])
        d = {k: getattr(sc, k) for k in ("tp", "fp", "fn", "harmful_invalidations", "invalidations_unmapped_target",
                                          "distractor_links", "pair_hits", "gold_links", "predicted_links",
                                          "slots_covered", "slots_total", "memories_matched",
                                          "memories_unmatched", "memories_ambiguous")}
        d["credited"] = [[g.source_slot, g.target_slot, g.relation_type] for g in sc.credited]
        out[tid] = d
    return out


def run_totals(per_thread: Mapping[str, Mapping]) -> dict:
    """One run's descriptive totals (PLAN 7.2 "Descriptive, no test")."""
    keys = ("tp", "fp", "fn", "harmful_invalidations", "invalidations_unmapped_target", "distractor_links",
            "pair_hits", "gold_links", "predicted_links", "slots_covered", "slots_total", "memories_matched",
            "memories_unmatched", "memories_ambiguous")
    tot = {k: sum(int(v[k]) for v in per_thread.values()) for k in keys}
    p, r, f = matcher.prf(tot["tp"], tot["fp"], tot["fn"])
    tot.update({"precision": p, "recall": r, "f1": f,
                "pair_recall": tot["pair_hits"] / tot["gold_links"] if tot["gold_links"] else 0.0,
                "slot_coverage": tot["slots_covered"] / tot["slots_total"] if tot["slots_total"] else 0.0})
    return tot


def void_reasons(mode: str, rows: Sequence[dict], failed_calls: int, calls: int, exchange_failures: int,
                 readback_ok: bool) -> list[str]:
    """PLAN 7.0 / 7.2: a run is void when more than 5% of its LLM calls fail after the retries, when DISABLED made
    a link, or when the per-exchange memory-count read-back did not happen (a database error stops the script and
    is handled by the caller as a technical abort)."""
    from lib import llmretry
    reasons = []
    if llmretry.void_by_failures(failed_calls, calls):
        reasons.append(f"{failed_calls} of {calls} LLM calls failed after the retries (> 5%)")
    if mode == "DISABLED":
        n = sum(1 for r in rows if r["table"] == "MEMORY_LINK")
        if n:
            reasons.append(f"DISABLED created {n} link(s)")
    if not readback_ok:
        reasons.append("the memory count was not read back after every exchange")
    if exchange_failures and not failed_calls:
        reasons.append(f"{exchange_failures} exchange(s) raised without a failed LLM call")
    return reasons


def counts_for_test(runs: Mapping[tuple[int, str], Mapping], data: dict, mode: str) -> list[tuple[int, int, int]]:
    """(TP, FP, FN) per thread in thread-id order, summed over the mode's 3 counted runs (PLAN 7.2)."""
    mine = [v for (rn, m), v in runs.items() if m == mode]
    if len(mine) != N_RUNS:
        raise ValueError(f"{mode}: {len(mine)} counted runs, PLAN 7.2 needs {N_RUNS}")
    out = []
    for t in data["threads"]:
        tid = t["thread_id"]
        out.append(tuple(sum(int(v["per_thread"][tid][k]) for v in mine) for k in ("tp", "fp", "fn")))
    return out


def confirmatory(runs: Mapping[tuple[int, str], Mapping], data: dict) -> dict:
    """The one confirmatory test (stats.c2_permutation_test) and the verdict with the harmful-invalidation gate
    (stats.c2_verdict). runs[(run_no, mode)] = {"per_thread": ..., "totals": ...} of the COUNTED run of that slot."""
    post = counts_for_test(runs, data, "POST_EXTRACTION")
    dur = counts_for_test(runs, data, "DURING_EXTRACTION")
    perm = stats.c2_permutation_test(post, dur)
    hp = [int(runs[(rn, "POST_EXTRACTION")]["totals"]["harmful_invalidations"]) for rn in (1, 2, 3)]
    hd = [int(runs[(rn, "DURING_EXTRACTION")]["totals"]["harmful_invalidations"]) for rn in (1, 2, 3)]
    verdict = stats.c2_verdict(perm, hp, hd)
    return {"post_counts_per_thread": post, "during_counts_per_thread": dur,
            "micro_f1_post": perm.f1_post, "micro_f1_during": perm.f1_during, "t_observed": perm.t_observed,
            "n_extreme": perm.n_extreme, "n_assignments": perm.n_assignments, "p": perm.p,
            "harmful_post_runs": hp, "harmful_during_runs": hd, "verdict": verdict,
            "disabled_note": "DISABLED creates no links by design: the cost baseline and the harmful-invalidation "
                             "floor (0), not a contender (PLAN 7.2)."}


def _mean_range(xs: Sequence[float]) -> dict:
    return {"mean": statistics.fmean(xs), "min": min(xs), "max": max(xs), "runs": list(xs)}


def costs(runs: Mapping[tuple[int, str], Mapping]) -> dict:
    """LLM calls, input/output/total tokens and wall time per mode as means with the range over the counted runs,
    and "linking costs X" = POST or DURING minus DISABLED (difference of the means; PLAN 7.2)."""
    metrics = ("llm_calls", "input_tokens", "output_tokens", "total_tokens", "wall_seconds",
               "exchange_seconds_mean")
    out: dict = {"per_mode": {}, "linking_cost_vs_disabled": {}}
    for mode in MODES:
        mine = [runs[(rn, mode)]["cost"] for rn in (1, 2, 3) if (rn, mode) in runs]
        if not mine:
            continue
        out["per_mode"][mode] = {k: _mean_range([float(c[k]) for c in mine]) for k in metrics}
    base = out["per_mode"].get("DISABLED")
    if base:
        for mode in ("POST_EXTRACTION", "DURING_EXTRACTION"):
            if mode in out["per_mode"]:
                out["linking_cost_vs_disabled"][mode] = {
                    k: out["per_mode"][mode][k]["mean"] - base[k]["mean"] for k in metrics}
    return out


def per_type(runs: Mapping[tuple[int, str], Mapping], data: dict) -> dict:
    """Credited gold links per relation type and mode, summed over the counted runs (described, never ranked)."""
    gold_types = Counter(g.relation_type for v in data["by_thread"].values() for g in v["gold"])
    out = {}
    for mode in MODES:
        c: Counter = Counter()
        for (rn, m), v in runs.items():
            if m != mode:
                continue
            for tid, d in v["per_thread"].items():
                c.update(x[2] for x in d["credited"])
        out[mode] = {t: {"credited_over_runs": c.get(t, 0), "gold_per_run": gold_types[t]}
                     for t in matcher.RELATION_TYPES}
    return out
