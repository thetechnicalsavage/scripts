#!/usr/bin/env python3
# v1.2 - NL2SQL accuracy lab: score a Select AI profile against a fixed question set.
#        v1.2: any '@' left after removing string literals is rejected (database links).
#        v1.1 (after adversarial review): generated SQL runs in a READ ONLY transaction and
#              FOR UPDATE / database links are rejected; numbers returned as text are
#              compared as numbers; questions marked "ordered" must also return the rows
#              in the gold order.
#
# Usage  : LAB_USER=NL2SQL_LAB LAB_PASSWORD=... LAB_DSN=host:1521/pdb \
#            python3 eval_nl2sql.py --profile NL2SQL_LAB_AI --stage S0_baseline --runs 3
#          [--set question|paraphrase] [--only Q03,Q20] [--questions questions.json] [--out ../../results]
# Needs  : python-oracledb (thin mode is enough).
#
# What it does, per question and run:
#   1. asks Select AI for SQL:  DBMS_CLOUD_AI.GENERATE(prompt, profile_name, action => 'showsql')
#   2. runs that SQL, but only if it is ONE statement starting with SELECT or WITH
#   3. compares the rows with the rows of every gold SQL for the question
#
# The comparison ("execution accuracy"): the answer is correct when, for some gold, the
# generated result has the same number of rows AND some choice of its columns reproduces
# the gold rows exactly (as a multiset). So column order, column names and EXTRA columns
# do not matter; missing columns, extra rows or different values do. Before comparing,
# numbers are rounded to 2 decimals, dates become YYYY-MM-DD, strings are trimmed and
# case-folded; a string that is a plain number is compared as that number. Row order is
# ignored unless the question is marked "ordered" (then the rows must also come back in the
# gold order; a top-N question without the flag is still checked by WHICH rows).
#
# Safety: run this only against a lab schema. Generated SQL is executed as the connected
# user inside SET TRANSACTION READ ONLY, with FOR UPDATE and @dblink rejected and a call
# timeout; for anything more sensitive, connect as a user that has SELECT only.
#
# Output (appended, never overwritten): <out>/runs.jsonl (every generated SQL and verdict)
# and <out>/summary.csv (one line per stage/set/run). Nothing secret is written.
import argparse
import collections
import csv
import datetime as dt
import decimal
import itertools
import json
import logging
import os
import re
import statistics
import sys
import time

import oracledb

log = logging.getLogger("eval_nl2sql")
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_ROWS = 5000
RETRIES = 3            # transient LLM/HTTP failures only; backoff 5 s, 10 s, 20 s


NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


def norm(v):
    if v is None:
        return None
    if isinstance(v, (int, float, decimal.Decimal)):
        r = round(float(v), 2)
        return 0.0 if r == 0 else r
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time(0) else v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if hasattr(v, "read"):                       # LOB
        v = v.read()
    t = str(v).strip()
    if NUMERIC.match(t):                         # e.g. TO_CHAR(COUNT(*)) -> '600'
        r = round(float(t), 2)
        return 0.0 if r == 0 else r
    return t.casefold()


def fetch(cur, sql):
    cur.execute(sql)
    rows = cur.fetchmany(MAX_ROWS + 1)
    if len(rows) > MAX_ROWS:
        raise RuntimeError(f"more than {MAX_ROWS} rows")
    return [tuple(norm(v) for v in r) for r in rows]


def matches(gold, got, ordered=False):
    """True when some injective choice of `got` columns reproduces `gold` as a multiset.
    With ordered=True the chosen columns must also list the rows in the gold order."""
    if len(gold) != len(got):
        return False
    if not gold:
        return True
    k, m = len(gold[0]), len(got[0])
    if m < k:
        return False
    gcols = [collections.Counter(r[j] for r in gold) for j in range(k)]
    rcols = [collections.Counter(r[c] for r in got) for c in range(m)]
    cand = [[c for c in range(m) if rcols[c] == gcols[j]] for j in range(k)]
    if any(not c for c in cand):
        return False
    target = collections.Counter(gold)
    for pick in itertools.product(*cand):
        if len(set(pick)) == k and collections.Counter(tuple(r[c] for c in pick) for r in got) == target:
            if not ordered or [tuple(r[c] for c in pick) for r in got] == list(gold):
                return True
    return False


SAFE = re.compile(r"^\s*(select|with)\b", re.I)


def clean_sql(text):
    s = (text or "").strip()
    s = re.sub(r"^```(sql)?|```$", "", s, flags=re.I).strip()
    s = s.rstrip().rstrip(";").rstrip()
    if not SAFE.match(s):
        raise ValueError("not a SELECT/WITH statement")
    bare = re.sub(r"'[^']*'", "", s)
    if ";" in bare:
        raise ValueError("more than one statement")
    if re.search(r"\bfor\s+update\b", bare, re.I):
        raise ValueError("FOR UPDATE is not allowed")
    if "@" in bare:
        raise ValueError("database links are not allowed")
    return s


def generate(cur, profile, prompt):
    for attempt in range(1, RETRIES + 1):
        try:
            cur.execute("select dbms_cloud_ai.generate(prompt => :p, profile_name => :pr, "
                        "action => 'showsql') from dual", p=prompt, pr=profile)
            v = cur.fetchone()[0]
            return v.read() if hasattr(v, "read") else (v or "")
        except oracledb.Error as e:
            msg = str(e)
            transient = any(t in msg for t in ("429", "500", "502", "503", "504", "timed out", "ORA-29273"))
            if not transient or attempt == RETRIES:
                raise
            wait = 5 * 2 ** (attempt - 1)
            log.warning("transient generate failure (%s), retry %d in %ds", msg.splitlines()[0], attempt, wait)
            time.sleep(wait)
    return ""


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--stage", required=True, help="label recorded with every result, e.g. S0_baseline")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--set", choices=("question", "paraphrase"), default="question")
    ap.add_argument("--only", default="", help="comma-separated question ids")
    ap.add_argument("--questions", default=os.path.join(HERE, "questions.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "..", "results"))
    a = ap.parse_args()

    for v in ("LAB_USER", "LAB_PASSWORD", "LAB_DSN"):
        if not os.environ.get(v):
            raise SystemExit(f"environment variable {v} is not set")
    qs = json.load(open(a.questions))["questions"]
    if a.only:
        keep = {x.strip().upper() for x in a.only.split(",")}
        qs = [q for q in qs if q["id"] in keep]
    os.makedirs(a.out, exist_ok=True)

    con = oracledb.connect(user=os.environ["LAB_USER"], password=os.environ["LAB_PASSWORD"],
                           dsn=os.environ["LAB_DSN"])
    con.call_timeout = 180_000
    cur = con.cursor()

    gold_rows = {}
    for q in qs:
        gold_rows[q["id"]] = [fetch(cur, g) for g in q["gold"]]

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    grid = collections.defaultdict(dict)
    sqls = collections.defaultdict(list)
    with open(os.path.join(a.out, "runs.jsonl"), "a") as jf, \
         open(os.path.join(a.out, "summary.csv"), "a", newline="") as sf:
        sw = csv.writer(sf)
        if sf.tell() == 0:
            sw.writerow(["utc", "profile", "stage", "set", "run", "questions", "executed", "correct",
                         "accuracy_pct", "median_generate_ms"])
        for run in range(1, a.runs + 1):
            ok_exec = ok = 0
            times = []
            for q in qs:
                prompt = q[a.set]
                rec = {"utc": stamp, "profile": a.profile, "stage": a.stage, "set": a.set, "run": run,
                       "id": q["id"], "prompt": prompt, "sql": None, "generate_ms": None,
                       "executed": False, "error": None, "rows": None, "correct": False, "gold_index": None}
                t0 = time.time()
                try:
                    raw = generate(cur, a.profile, prompt)
                    rec["generate_ms"] = int((time.time() - t0) * 1000)
                    times.append(rec["generate_ms"])
                    rec["sql"] = raw
                    sql = clean_sql(raw)
                    con.rollback()                               # start a clean transaction
                    cur.execute("set transaction read only")
                    got = fetch(cur, sql)
                    con.rollback()
                    rec["executed"], rec["rows"] = True, len(got)
                    ok_exec += 1
                    for i, g in enumerate(gold_rows[q["id"]]):
                        if matches(g, got, q.get("ordered", False)):
                            rec["correct"], rec["gold_index"] = True, i
                            ok += 1
                            break
                except (oracledb.Error, ValueError, RuntimeError) as e:
                    rec["error"] = str(e).splitlines()[0][:300]
                    con.rollback()
                grid[q["id"]][run] = "OK" if rec["correct"] else ("ERR" if not rec["executed"] else "WRONG")
                sqls[q["id"]].append(re.sub(r"\s+", " ", rec["sql"] or "").strip())
                jf.write(json.dumps(rec) + "\n")
                jf.flush()
                log.info("%s run %d %s %-5s %s", a.stage, run, q["id"], grid[q["id"]][run],
                         rec["error"] or "")
            acc = round(100 * ok / len(qs), 1) if qs else 0.0
            sw.writerow([stamp, a.profile, a.stage, a.set, run, len(qs), ok_exec, ok, acc,
                         int(statistics.median(times)) if times else ""])
            sf.flush()
            log.info("%s run %d: %d/%d correct (%.1f%%), %d executed", a.stage, run, ok, len(qs), acc, ok_exec)

    stable = sum(1 for v in sqls.values() if len(set(v)) == 1)
    print(f"\n{a.stage} [{a.set}] profile={a.profile}")
    print("id    " + "  ".join(f"run{r}" for r in range(1, a.runs + 1)))
    for q in qs:
        print(f"{q['id']:5} " + "  ".join(f"{grid[q['id']].get(r, '-'):5}" for r in range(1, a.runs + 1)))
    per_run = [sum(1 for q in qs if grid[q["id"]].get(r) == "OK") for r in range(1, a.runs + 1)]
    print("correct per run:", per_run, f"of {len(qs)}")
    print(f"identical SQL in every run: {stable}/{len(qs)} questions")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
