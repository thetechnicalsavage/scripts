#!/usr/bin/env python3
# v1.0 - NL2SQL accuracy lab: how much do the scoring choices move the numbers?
#
# Usage : LAB_USER=... LAB_PASSWORD=... LAB_DSN=... python3 sensitivity.py [--out ../../results]
# Re-executes every stored answer in runs.jsonl (read-only transaction, no model calls) and
# scores it under five rules, from the published one to the strictest:
#   published     numbers to 2 dp, extra columns allowed, any gold, ordered questions in order
#   exact_numbers numbers compared to 6 dp
#   exact_columns the answer must have exactly the gold's number of columns
#   first_gold    only the first gold per question (no code/name or active-only alternatives)
#   strict        all three together
# Writes <out>/sensitivity.csv (stage, set, rule, mean accuracy %) and prints the table.
import argparse
import collections
import csv
import json
import logging
import os
import sys

import oracledb

import eval_nl2sql as E

log = logging.getLogger("sensitivity")
RULES = ("published", "exact_numbers", "exact_columns", "first_gold", "strict")


def raw_fetch(cur, sql):
    cur.execute(sql)
    rows = cur.fetchmany(E.MAX_ROWS + 1)
    if len(rows) > E.MAX_ROWS:
        raise RuntimeError("too many rows")
    return [tuple(v.read() if hasattr(v, "read") else v for v in r) for r in rows]


def normalise(rows, dp):
    def n(v):
        if isinstance(v, (int, float)) or type(v).__name__ == "Decimal":
            r = round(float(v), dp)
            return 0.0 if r == 0 else r
        if isinstance(v, str) and E.NUMERIC.match(v.strip()):
            r = round(float(v.strip()), dp)
            return 0.0 if r == 0 else r
        return E.norm(v)
    return [tuple(n(v) for v in r) for r in rows]


def verdict(gold_raw, got_raw, ordered, rule):
    dp = 6 if rule in ("exact_numbers", "strict") else 2
    golds = gold_raw[:1] if rule in ("first_gold", "strict") else gold_raw
    got = normalise(got_raw, dp)
    for g in golds:
        gn = normalise(g, dp)
        if rule in ("exact_columns", "strict") and gn and got and len(gn[0]) != len(got[0]):
            continue
        if E.matches(gn, got, ordered):
            return True
    return False


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=os.path.join(E.HERE, "questions.json"))
    ap.add_argument("--out", default=os.path.join(E.HERE, "..", "..", "results"))
    a = ap.parse_args()
    for v in ("LAB_USER", "LAB_PASSWORD", "LAB_DSN"):
        if not os.environ.get(v):
            raise SystemExit(f"environment variable {v} is not set")
    qs = {q["id"]: q for q in json.load(open(a.questions))["questions"]}
    con = oracledb.connect(user=os.environ["LAB_USER"], password=os.environ["LAB_PASSWORD"],
                           dsn=os.environ["LAB_DSN"])
    con.call_timeout = 120_000
    cur = con.cursor()
    gold = {qid: [raw_fetch(cur, g) for g in q["gold"]] for qid, q in qs.items()}

    score = collections.OrderedDict()
    for line in open(os.path.join(a.out, "runs.jsonl")):
        r = json.loads(line)
        got = None
        try:
            sql = E.clean_sql(r["sql"])
            con.rollback()
            cur.execute("set transaction read only")
            got = raw_fetch(cur, sql)
        except (oracledb.Error, ValueError, RuntimeError):
            pass
        con.rollback()
        key = (r["stage"], r["set"])
        s = score.setdefault(key, {rule: [0, 0] for rule in RULES})
        for rule in RULES:
            ok = got is not None and verdict(gold[r["id"]], got, qs[r["id"]].get("ordered", False), rule)
            s[rule][0] += ok
            s[rule][1] += 1
    with open(os.path.join(a.out, "sensitivity.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stage", "set"] + [f"{r}_pct" for r in RULES])
        print(f"{'stage':24} {'set':10} " + " ".join(f"{r:>13}" for r in RULES))
        for (stage, qset), s in score.items():
            vals = [round(100 * s[r][0] / s[r][1], 1) for r in RULES]
            w.writerow([stage, qset] + vals)
            print(f"{stage:24} {qset:10} " + " ".join(f"{v:13.1f}" for v in vals))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
