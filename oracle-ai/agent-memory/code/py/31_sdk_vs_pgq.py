# v1.1 2026-10-10  Brief 12 step 3 (PLAN 6 row 3, B3): the SDK's run-time relational graph-expansion SQL against its
#                  unused SQL/PGQ builder, both rendered by the installed wheel, executed on store DEMO as AGENTMEM.
#                  v1.1: (Codex review r2, findings 2 and 3) the label must match run_all.sh's [a-z0-9_]{1,24}; both
#                  rendered statements must be a single query (WITH or SELECT first, no semicolon, no DML/DDL/PL/SQL
#                  keyword) or nothing is executed and the script exits 1 (fail closed).
#                  v1.0: (Codex review r1, finding 2; day 1 restart) equality is claimed only over pairs that ran on both
#                  sides: comparable_pairs is reported, and all_*_equal is None when no pair is comparable and False
#                  unless every intended pair ran on both sides and matched.
#                  v0.1: first draft (day 1).
"""Step 3, part 2. Run on the demo VM from ~/agentmem-poc/code with the POC venv:
    .venv/bin/python py/31_sdk_vs_pgq.py [--label LABEL]
For roots M10, M3 and M8 and 1, 2 and 3 hops, the wheel renders (core/_db/sql.pyc):
  relational: build_query_memory_graph_expansion_sql_and_binds("DEMO_MEMORY", "DEMO_MEMORY_LINK", ...)  (used at run time)
  SQL/PGQ:    build_memory_graph_expansion_sql_and_binds("DEMO_MEMORY", "DEMO_MEMORY_GRAPH", ...)     (kept for future use)
with the arguments a client search passes for user CUST_1001 (exact user match, no thread or agent filter, no record
type or metadata filter) and max_linked_results=100 (the SDK default). Both are executed; the result rows are compared
as ordered lists and as multisets on every returned column (LOBs read as text, JSON as its text form). Any difference
is listed row by row. Elapsed time: median of 20 executions of each, reported as illustrative only (ten nodes).
Whether the PGQ builder's SQL runs on 23.26.1 at all is itself a result (error kept verbatim).
Results: staging/s3/results/sdk_vs_pgq[_LABEL].json and the rendered SQL of each pair; transcript staging/s3/transcripts/.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import runlog  # noqa: E402  (first: redaction before anything else)

ap = argparse.ArgumentParser()
ap.add_argument("--label", default="")
ARGS = ap.parse_args()
if ARGS.label and not re.fullmatch(r"[a-z0-9_]{1,24}", ARGS.label):      # the runner's rule (run_all.sh)
    sys.exit("bad label: [a-z0-9_]{1,24}")
SUFFIX = f"_{ARGS.label}" if ARGS.label else ""
RUN = runlog.start("s3", tag=f"s3_sdk_vs_pgq{SUFFIX}")

from lib import amconf, hero_seed  # noqa: E402

LITELLM = amconf.configure_litellm()          # the SDK import pulls litellm; switches asserted anyway

from oracleagentmemory.core._db import sql as amsql  # noqa: E402

KEY_OF = {m.memory_id: m.key for m in hero_seed.MEMORIES}
MEM = hero_seed.by_key()
ROOTS = ("M10", "M3", "M8")
REPS = 20


def norm(v):
    if hasattr(v, "read"):
        v = v.read()
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True)
    if isinstance(v, bytes):
        return v.hex()
    if v is not None and not isinstance(v, (int, float, str)):
        return str(v)
    return v


_WRITE_WORDS = re.compile(r"\b(insert|update|delete|merge|create|drop|alter|grant|revoke|truncate|begin|declare|"
                          r"call|execute|lock|commit|rollback)\b", re.I)


def read_only_query(sql: str) -> bool:
    """A single query: starts with WITH or SELECT, no semicolon, no DML, DDL or PL/SQL keyword (comments included,
    so a false refusal is possible and safe)."""
    return bool(re.match(r"(?is)\s*(with|select)\b", sql)) and ";" not in sql and not _WRITE_WORDS.search(sql)


def execute(cur, sql: str, binds: dict) -> dict:
    try:
        cur.execute(sql, binds)
        cols = [d[0].lower() for d in cur.description]
        rows = [tuple(norm(x) for x in r) for r in cur.fetchall()]
        return {"ok": True, "columns": cols, "rows": rows}
    except Exception as exc:                     # a refusal of the PGQ form is a result
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:2000]}


def timed(cur, sql: str, binds: dict) -> float:
    t = time.perf_counter()
    cur.execute(sql, binds)
    cur.fetchall()
    return (time.perf_counter() - t) * 1000


def keyed(row, cols):
    d = dict(zip(cols, row))
    return {c: (KEY_OF.get(v, v) if isinstance(v, str) else v) for c, v in d.items()}


common = dict(max_linked_results=100, thread_id=None, user_id=hero_seed.USER_ID, agent_id=None,
              exact_thread_match=False, exact_user_match=True, exact_agent_match=False, record_types=None,
              metadata_filter=None)
pool = amconf.make_pool("AGENTMEM", 1, 1)
pairs = []
with pool.acquire() as conn, conn.cursor() as cur:
    for root in ROOTS:
        for hops in (1, 2, 3):
            rid = MEM[root].memory_id
            rq, rb = amsql.build_query_memory_graph_expansion_sql_and_binds(
                "DEMO_MEMORY", "DEMO_MEMORY_LINK", num_hops=hops, root_ids=[rid], **common)
            pq, pb = amsql.build_memory_graph_expansion_sql_and_binds(
                "DEMO_MEMORY", "DEMO_MEMORY_GRAPH", num_hops=hops, root_ids=[rid], **common)
            tag = f"{root}_h{hops}"
            RUN.write_text("results", f"rendered_relational_{tag}{SUFFIX}.sql", rq + "\n")
            RUN.write_text("results", f"rendered_pgq_{tag}{SUFFIX}.sql", pq + "\n")
            bad = [n for n, q in (("relational", rq), ("pgq", pq)) if not read_only_query(q)]
            if bad:                                   # fail closed: nothing of this run is executed
                RUN.log.error("rendered SQL is not a single read-only query: %s (%s); nothing executed", bad, tag)
                sys.exit(1)
            r = execute(cur, rq, rb)
            p = execute(cur, pq, pb)
            rec = {"root": root, "hops": hops, "relational_ok": r["ok"], "pgq_ok": p["ok"],
                   "binds_equal": rb == pb}
            if not r["ok"]:
                rec["relational_error"] = {"type": r["error_type"], "text": r["error"]}
            if not p["ok"]:
                rec["pgq_error"] = {"type": p["error_type"], "text": p["error"]}
            if r["ok"] and p["ok"]:
                rec["columns_equal"] = r["columns"] == p["columns"]
                rec["rows_relational"] = len(r["rows"])
                rec["rows_pgq"] = len(p["rows"])
                rec["ordered_equal"] = r["rows"] == p["rows"]
                rec["multiset_equal"] = Counter(r["rows"]) == Counter(p["rows"])
                only_r = list((Counter(r["rows"]) - Counter(p["rows"])).elements())
                only_p = list((Counter(p["rows"]) - Counter(r["rows"])).elements())
                rec["only_relational"] = [keyed(x, r["columns"]) for x in only_r]
                rec["only_pgq"] = [keyed(x, p["columns"]) for x in only_p]
                rec["linked_records"] = [keyed(x, r["columns"]).get("record_id") for x in r["rows"]]
                tr = [timed(cur, rq, rb) for _ in range(REPS)]
                tp = [timed(cur, pq, pb) for _ in range(REPS)]
                rec["median_ms_relational"] = round(statistics.median(tr), 2)
                rec["median_ms_pgq"] = round(statistics.median(tp), 2)
            pairs.append(rec)
            RUN.transcript(f"- {tag}: relational {'ok' if r['ok'] else 'ERROR'}, pgq {'ok' if p['ok'] else 'ERROR'}"
                           + (f", rows {rec['rows_relational']}/{rec['rows_pgq']}, multiset equal "
                              f"{rec['multiset_equal']}, ordered equal {rec['ordered_equal']}"
                              if r["ok"] and p["ok"] else ""))
pool.close()
comparable = [x for x in pairs if x["relational_ok"] and x["pgq_ok"]]
every_pair_ran = len(comparable) == len(pairs) and len(pairs) > 0


def all_equal(field: str):
    """None when nothing could be compared; True only if every intended pair ran on both sides and matched."""
    if not comparable:
        return None
    return every_pair_ran and all(x.get(field) is True for x in comparable)


summary = {
    "pairs": len(pairs),
    "comparable_pairs": len(comparable),
    "pgq_runs_on_23_26_1": all(x["pgq_ok"] for x in pairs) if pairs else None,
    "all_multiset_equal": all_equal("multiset_equal"),
    "all_ordered_equal": all_equal("ordered_equal"),
    "pairs_not_comparable": [f"{x['root']}_h{x['hops']}" for x in pairs if x not in comparable],
    "timing_note": "median of 20 executions each; illustrative only (ten nodes), PLAN 9",
}
RUN.write_json("results", f"sdk_vs_pgq{SUFFIX}.json", {"summary": summary, "pairs": pairs, "label": ARGS.label or None,
                                                       "common_arguments": common})
RUN.finish(summary)
sys.exit(0 if all(x["relational_ok"] for x in pairs) else 1)
