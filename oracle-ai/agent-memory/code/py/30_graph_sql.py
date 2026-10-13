# v1.1 2026-10-10  Brief 12 step 3 (PLAN 6 row 3, section 8): run sql/30_graph_queries.sql as AGENTMEM on store DEMO,
#                  with section 8's fallbacks when 23.26.1 refuses a form.
#                  v1.1: (Codex review r2, finding 2) the label must match run_all.sh's [a-z0-9_]{1,24}.
#                  v1.0: (Codex review r1, finding 1; day 1 restart) Q3 (quantified) and Q3H (fixed hops) are tracked
#                  separately: Q3H must succeed for every root (the page shows it beside Q3), Q3H stands in for Q3 only
#                  when the quantified form fails, and the M10 chain is checked on both forms.
#                  v0.1: first draft (day 1).
"""Step 3, part 1. Run on the demo VM from ~/agentmem-poc/code with the POC venv:
    .venv/bin/python py/30_graph_sql.py [--label LABEL]
Statements are read from sql/30_graph_queries.sql ("-- @<id>" marker lines; SELECT only). Binds: P1_USER_ID =
CUST_1001; P2_RECORD_ID = M10, M3 and M8 in turn (PLAN 6 row 3), each run separately for Q3 and Q3H.
Fallbacks (section 8), applied only when the primary form raises: Q1 -> Q1F, Q2 -> Q2F (bind outside GRAPH_TABLE);
Q3 -> Q3H (fixed hops). Q3H always runs as well and is required for every root (the page shows hop-by-hop rows beside
Q3, and section 8 never relies on LISTAGG's order alone). Every error is kept verbatim (redacted); which form produced
each result is recorded, and the post must name any fallback used.
Checks against the hero (reported, not enforced): Q1 returns the ten memories with section 3's statuses; Q2 the six
links; from M10, both Q3 and Q3H give M2 (1 hop, supersedes) and M1 (2 hops, supersedes > supersedes).
Exit: 0 when Q1 and Q2 (or their fallbacks) ran, Q3H ran for every root, and Q3 ran for every root or Q3H stood in
for it; 1 otherwise.
Results: staging/s3/results/graph_queries[_LABEL].json; transcript staging/s3/transcripts/.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import runlog  # noqa: E402  (first: redaction before anything else)

ap = argparse.ArgumentParser()
ap.add_argument("--label", default="", help="suffix for the results file (e.g. after_s2)")
ARGS = ap.parse_args()
if ARGS.label and not re.fullmatch(r"[a-z0-9_]{1,24}", ARGS.label):      # the runner's rule (run_all.sh)
    sys.exit("bad label: [a-z0-9_]{1,24}")
SUFFIX = f"_{ARGS.label}" if ARGS.label else ""
RUN = runlog.start("s3", tag=f"s3_graph{SUFFIX}")

from lib import amconf, hero_seed  # noqa: E402

SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "30_graph_queries.sql"
KEY_OF = {m.memory_id: m.key for m in hero_seed.MEMORIES}
MEM = hero_seed.by_key()
ROOTS = ("M10", "M3", "M8")


def statements() -> dict[str, str]:
    text = SQL_FILE.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for block in re.split(r"(?m)^-- @", text)[1:]:
        sid, _, body = block.partition(" ")
        body = body.split("\n", 1)[1] if "\n" in body else ""
        stmt = body.strip()
        if not stmt.endswith(";"):
            raise ValueError(f"{sid}: statement does not end with a semicolon")
        stmt = stmt[:-1].strip()
        if not re.match(r"(?is)^select\b", stmt):
            raise ValueError(f"{sid}: only SELECT statements are allowed")
        out[sid] = stmt
    return out


def k(v):
    return KEY_OF.get(v, v) if isinstance(v, str) else v


def run_stmt(cur, sql: str, binds: dict) -> dict:
    t = time.perf_counter()
    try:
        cur.execute(sql, binds)
        cols = [d[0].lower() for d in cur.description]
        rows = [[(x.read() if hasattr(x, "read") else x) for x in r] for r in cur.fetchall()]
        rows = [[k(x) if c in ("mem_id", "src_id", "dst_id", "hop1_id", "hop2_id", "hop3_id") else
                 (str(x) if x is not None and not isinstance(x, (int, float, str)) else x) for c, x in zip(cols, r)]
                for r in rows]
        return {"ok": True, "columns": cols, "rows": rows, "ms": round((time.perf_counter() - t) * 1000, 1)}
    except Exception as exc:                     # the refusal itself is a result (section 8 fallbacks)
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:2000],
                "ms": round((time.perf_counter() - t) * 1000, 1)}


S = statements()
pool = amconf.make_pool("AGENTMEM", 1, 1)
out: dict = {"store": "DEMO", "user": hero_seed.USER_ID, "label": ARGS.label or None, "forms": {}}
with pool.acquire() as conn, conn.cursor() as cur:
    for q, fb in (("Q1", "Q1F"), ("Q2", "Q2F")):
        r = run_stmt(cur, S[q], {"P1_USER_ID": hero_seed.USER_ID})
        out[q] = r
        out["forms"][q] = q
        if not r["ok"]:
            RUN.log.warning("%s refused (%s); running the fallback %s", q, r["error_type"], fb)
            out[fb] = run_stmt(cur, S[fb], {"P1_USER_ID": hero_seed.USER_ID})
            out["forms"][q] = fb if out[fb]["ok"] else "none"
    out["Q3"], out["Q3H"] = {}, {}
    for root in ROOTS:
        b = {"P2_RECORD_ID": MEM[root].memory_id}
        out["Q3"][root] = run_stmt(cur, S["Q3"], b)
        out["Q3H"][root] = run_stmt(cur, S["Q3H"], b)
    q3_ok = all(out["Q3"][r]["ok"] for r in ROOTS)              # the quantified form, every root
    q3h_ok = all(out["Q3H"][r]["ok"] for r in ROOTS)            # the fixed-hop form, every root (required)
    out["Q3_quantified_ok"], out["Q3H_fixed_ok"] = q3_ok, q3h_ok
    out["forms"]["Q3"] = "Q3" if q3_ok else ("Q3H" if q3h_ok else "none")
    out["forms"]["Q3H"] = "Q3H" if q3h_ok else "none"
pool.close()

# checks against the hero (reported either way)
exp_status = hero_seed.expected_statuses()
q1 = out.get(out["forms"]["Q1"]) or {}
q1_status = {r[0]: r[2] for r in q1.get("rows", [])} if q1.get("ok") else {}
q2 = out.get(out["forms"]["Q2"]) or {}
q2_links = sorted((r[0], r[1], r[3]) for r in q2.get("rows", [])) if q2.get("ok") else []
exp_links = sorted((lk.source, lk.relation_type, lk.target) for lk in hero_seed.LINKS)


def chain(res: dict) -> dict:
    got = {}
    if res.get("ok"):
        for r in res["rows"]:
            row = dict(zip(res["columns"], r))
            got[row["dst_id"]] = (row["hops"], row["path_types"])
    return got


def m10_ok(c: dict) -> bool:
    return c.get("M2") == (1, "supersedes") and c.get("M1") == (2, "supersedes > supersedes")


chain_m10_q3, chain_m10_q3h = chain(out["Q3"]["M10"]), chain(out["Q3H"]["M10"])
chain_m10 = chain_m10_q3 if out["forms"]["Q3"] == "Q3" else chain_m10_q3h
out["checks"] = {
    "Q1_ten_memories_with_section3_statuses": q1_status == exp_status,
    "Q1_statuses": q1_status,
    "Q2_six_links_equal_hero": q2_links == exp_links,
    "Q2_links": q2_links,
    "Q3_from_M10": {kk: list(v) for kk, v in chain_m10.items()},
    "Q3_M10_gives_M2_then_M1": m10_ok(chain_m10),
    "Q3_quantified_M10_gives_M2_then_M1": m10_ok(chain_m10_q3) if out["Q3_quantified_ok"] else None,
    "Q3H_fixed_M10_gives_M2_then_M1": m10_ok(chain_m10_q3h) if out["Q3H_fixed_ok"] else None,
    "Q3H_from_M10": {kk: list(v) for kk, v in chain_m10_q3h.items()},
}
RUN.write_json("results", f"graph_queries{SUFFIX}.json", out)
for q in ("Q1", "Q2", "Q3", "Q3H"):
    RUN.transcript(f"- {q}: form used {out['forms'][q]}")
for kk, v in out["checks"].items():
    if not isinstance(v, (dict, list)):
        RUN.transcript(f"- check {kk}: {v}")
RUN.finish({"forms": out["forms"], "Q3_quantified_ok": out["Q3_quantified_ok"], "Q3H_fixed_ok": out["Q3H_fixed_ok"],
            "checks": {kk: v for kk, v in out["checks"].items() if isinstance(v, bool) or v is None}})
sys.exit(0 if all(f != "none" for f in out["forms"].values()) else 1)      # forms["Q3H"] is "none" unless all roots ran
