#!/usr/bin/env python3
# v1.2 - NL2SQL accuracy lab: re-grade stored runs against the CURRENT questions.json.
#
#        v1.2: rescored.jsonl records the error of THIS re-execution, never a stale one.
#        v1.1: same read-only execution and ordered-question rule as eval_nl2sql.py v1.1.
# Usage : LAB_USER=... LAB_PASSWORD=... LAB_DSN=... python3 rescore.py [--out ../../results]
# Why   : if a gold answer is widened (e.g. names accepted as well as codes), every stage
#         must be re-graded by the same rules. This re-executes the SQL that each run
#         already stored in runs.jsonl - no model calls - and writes rescored.csv
#         (one line per stage/set/run) and rescored.jsonl (per question).
# The lab data is deterministic, so re-executing old SQL gives the rows it gave then.
import argparse
import collections
import csv
import json
import logging
import os
import sys

import oracledb

from eval_nl2sql import HERE, clean_sql, fetch, matches

log = logging.getLogger("rescore")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=os.path.join(HERE, "questions.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "..", "results"))
    a = ap.parse_args()
    for v in ("LAB_USER", "LAB_PASSWORD", "LAB_DSN"):
        if not os.environ.get(v):
            raise SystemExit(f"environment variable {v} is not set")

    qs = {q["id"]: q for q in json.load(open(a.questions))["questions"]}
    con = oracledb.connect(user=os.environ["LAB_USER"], password=os.environ["LAB_PASSWORD"],
                           dsn=os.environ["LAB_DSN"])
    con.call_timeout = 120_000
    cur = con.cursor()
    gold = {qid: [fetch(cur, g) for g in q["gold"]] for qid, q in qs.items()}

    runs = [json.loads(l) for l in open(os.path.join(a.out, "runs.jsonl"))]
    tally = collections.OrderedDict()
    changed = 0
    with open(os.path.join(a.out, "rescored.jsonl"), "w") as jf:
        for r in runs:
            ok, executed, err = False, False, None
            try:
                sql = clean_sql(r["sql"])
                con.rollback()
                cur.execute("set transaction read only")
                got = fetch(cur, sql)
                executed = True
                ok = any(matches(g, got, qs[r["id"]].get("ordered", False)) for g in gold[r["id"]])
            except (oracledb.Error, ValueError, RuntimeError) as e:
                err = str(e).splitlines()[0][:200]
            con.rollback()
            if ok != r["correct"]:
                changed += 1
                log.info("%s run %s %s: %s -> %s", r["stage"], r["run"], r["id"], r["correct"], ok)
            key = (r["stage"], r["set"], r["run"])
            t = tally.setdefault(key, {"n": 0, "exec": 0, "ok": 0})
            t["n"] += 1
            t["exec"] += executed
            t["ok"] += ok
            jf.write(json.dumps({**r, "correct": ok, "executed": executed, "error": err}) + "\n")
    with open(os.path.join(a.out, "rescored.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stage", "set", "run", "questions", "executed", "correct", "accuracy_pct"])
        for (stage, qset, run), t in tally.items():
            w.writerow([stage, qset, run, t["n"], t["exec"], t["ok"], round(100 * t["ok"] / t["n"], 1)])
    log.info("re-graded %d stored answers; %d verdicts changed", len(runs), changed)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
