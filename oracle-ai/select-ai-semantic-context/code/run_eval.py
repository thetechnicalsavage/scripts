#!/usr/bin/env python3
# v1.4 2026-11-11 - published copy: imports lab_conn.py from this folder instead of the lab host's own helper; no default path.
# v1.3 2026-11-11 - Codex r4: DATE columns fingerprinted to the second; gold results stored and compared as sorted
#                    (unordered) row lists; a resume must use the same mode (generate, or rescore and its source run).
# v1.2 2026-11-11 - Codex r3: numbers agree at 2 decimals; the 1-decimal allowance applies only to the percentage
#                    question and only against a gold value rounded to 1 decimal, after putting a fraction in percent
#                    units; values and rows are paired by complete bipartite matching (order no longer matters); the
#                    run fingerprint hashes every row of the four tables and keeps the evaluation date and the gold
#                    results, and a resume is refused if any of them changed.
# v1.1 2026-11-11 - (also: export logs a path relative to the brief, no home path in logs.)
#                    Codex r2: the x100 percentage allowance applies only to a percentage question (it let a count of
#                    1 match 100 before); column mapping is injective and searched completely; the JSONL is exported
#                    from SEM_EVAL_RESULTS after the run (an interruption between commit and append lost a line);
#                    each run writes results/<run>.meta.json (question-set and data fingerprints, evaluation date);
#                    --rescore <run> re-runs a stored run's SQL with this matcher into a new run id (no LLM calls).
# v1.0 2026-11-11 - brief 15 evaluation: for each arm (AI profile) x question x repetition, ask Select AI for SQL with
#   DBMS_CLOUD_AI.GENERATE(action => 'showsql') (not cached, unlike SELECT AI), run that SQL and compare its result with
#   the question's gold SQL result. Resumable: a (run, arm, question, repetition) already in SEM_EVAL_RESULTS is skipped,
#   so a re-run after an interruption continues where it stopped. Results also go to results/<run>.jsonl.
#   Match rule (results, not SQL text): same row count, and every gold column equals some generated column as a
#   multiset of values with rows aligned (extra generated columns are allowed). Numbers compare at 2 decimals, or at
#   1 decimal when either side has at most 1; a fraction equal to a gold percentage / 100 also matches.
# Usage: run_eval.py --login <lab login env> --run R1 [--reps 3] [--arms ...] [--gold-only]
#        run_eval.py --login <env> --run R1b --rescore R1        (re-score stored SQL, no LLM)
#        run_eval.py --login <env> --run R1 --export-only
import argparse
import datetime
import decimal
import hashlib
import itertools
import json
import logging
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # lab_conn.py
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(os.path.dirname(HERE), 'results')
ARM_LABEL = {'SALES_BASE': 'A', 'SALES_SEM': 'B', 'SALES_ANNOT': 'C'}
RUN_RE = re.compile(r'^[A-Za-z0-9_-]{1,30}$')
log = logging.getLogger('run_eval')


def norm_value(v):
    if v is None:
        return None
    if isinstance(v, (int, float, decimal.Decimal)):
        return float(v)
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    return str(v).strip().upper()


def num_equal(g, x):
    """Numbers agree at 2 decimals."""
    return abs(g - x) < 0.005 or round(g, 2) == round(x, 2)


def val_equal(g, x, pct=False):
    if isinstance(g, float) and isinstance(x, float):
        if num_equal(g, x):
            return True
        if pct:
            # a percentage question: a fraction is first put in percent units; the gold value is rounded to 1 decimal
            # (Q12's gold SQL), so a generated value that rounds to it at 1 decimal also matches
            for c in (x, x * 100):
                if num_equal(g, c) or (round(g, 1) == g and round(c, 1) == g):
                    return True
        return False
    return g == x


def perfect_matching(left, right, eq):
    """True when every item of left pairs with a different item of right under eq (Kuhn's augmenting paths), so the
    result never depends on the order of rows or values."""
    if len(left) != len(right):
        return False
    adj = [[j for j, r in enumerate(right) if eq(l, r)] for l in left]
    match_r = [-1] * len(right)

    def augment(i, seen):
        for j in adj[i]:
            if not seen[j]:
                seen[j] = True
                if match_r[j] == -1 or augment(match_r[j], seen):
                    match_r[j] = i
                    return True
        return False

    return all(augment(i, [False] * len(right)) for i in range(len(left)))


def column_matches(gcol, xcol, pct=False):
    """True when two columns hold the same multiset of values."""
    return perfect_matching(list(gcol), list(xcol), lambda g, x: val_equal(g, x, pct))


def rows_match(gold, gen, pct=False):
    """(ok, detail): same row count; each gold column maps to a different generated column (extra generated columns
    allowed) holding the same values; the rows pair up under that mapping. pct: the question asks for a percentage."""
    if len(gold) != len(gen):
        return False, f'rows {len(gen)} vs gold {len(gold)}'
    if not gold:
        return True, 'both empty'
    gcols = list(zip(*gold))
    xcols = list(zip(*gen))
    candidates = [[j for j, xc in enumerate(xcols) if column_matches(gc, xc, pct)] for gc in gcols]
    if any(not c for c in candidates):
        missing = [i for i, c in enumerate(candidates) if not c]
        return False, f'gold column(s) {missing} not found in the result'
    for mapping in itertools.product(*candidates):
        if len(set(mapping)) != len(mapping):
            continue                                    # one generated column cannot stand for two gold columns
        projected = [tuple(r[j] for j in mapping) for r in gen]
        if perfect_matching(gold, projected, lambda g, x: all(val_equal(a, b, pct) for a, b in zip(g, x))):
            return True, 'match'
    return False, 'columns match but rows do not line up'


def fetch(cur, sql, limit=500):
    cur.execute(sql)
    rows = cur.fetchmany(limit + 1)
    if len(rows) > limit:
        raise RuntimeError(f'more than {limit} rows')
    return [tuple(norm_value(v) for v in r) for r in rows]


def clean_sql(text):
    s = (text or '').strip()
    s = re.sub(r'^```(?:sql)?\s*|\s*```$', '', s, flags=re.I).strip()
    return s[:-1].rstrip() if s.endswith(';') else s


def is_pct(question):
    return 'percent' in question.lower()


def score(cur, gold_rows, gen_sql, question):
    """(outcome, detail) for one generated statement."""
    if not re.match(r'^\s*(select|with)\b', gen_sql or '', re.I):
        return 'no_sql', (gen_sql or '')[:300]
    try:
        ok, detail = rows_match(gold_rows, fetch(cur, gen_sql), is_pct(question))
        return ('correct' if ok else 'wrong'), detail
    except Exception as e:  # oracledb.Error or the row limit: the statement did not give a usable result
        return 'error', 'run: ' + str(e).splitlines()[0][:300]


def fingerprints(cur, questions):
    """Question set, every row of the four tables (count and sum of ORA_HASH per table), and the evaluation date."""
    qhash = hashlib.sha256(json.dumps(questions).encode()).hexdigest()[:16]
    tables = {
        'regions': "region_id || '|' || region_name",
        'customers': "cust_id || '|' || cust_name || '|' || region_id || '|' || segment || '|' || acct_status || '|' || "
                     "to_char(signup_date, 'YYYY-MM-DD HH24:MI:SS')",
        'orders': "order_id || '|' || cust_id || '|' || to_char(order_date, 'YYYY-MM-DD HH24:MI:SS') || '|' || status || '|' || "
                  "gross_amt || '|' || disc_amt || '|' || to_char(promised_date, 'YYYY-MM-DD HH24:MI:SS') || '|' || "
                  "to_char(shipped_date, 'YYYY-MM-DD HH24:MI:SS')",
        'tickets': "ticket_id || '|' || cust_id || '|' || to_char(opened_date, 'YYYY-MM-DD HH24:MI:SS') || '|' || "
                   "to_char(closed_date, 'YYYY-MM-DD HH24:MI:SS') || '|' || sev || '|' || escl_flag || '|' || status",
    }
    fp = {'questions_sha256_16': qhash}
    for t, expr in tables.items():
        cur.execute(f'select count(*), sum(ora_hash({expr})) from {t}')
        n, h = cur.fetchone()
        fp[f'{t}_rows'] = n
        fp[f'{t}_hash'] = int(h or 0)
    cur.execute("select to_char(sysdate, 'YYYY-MM-DD') from dual")
    fp['evaluation_date_db'] = cur.fetchone()[0]
    return fp


def export(cur, run):
    """results/<run>.jsonl rebuilt from SEM_EVAL_RESULTS (the database is the record)."""
    cur.execute("""select arm, qid, rep, outcome, detail, elapsed_ms, generated_sql from sem_eval_results
                    where run_id = :r order by rep, arm, qid""", r=run)
    rows = cur.fetchall()
    path = os.path.join(RESULTS, f'{run}.jsonl')
    with open(path + '.tmp', 'w', encoding='utf-8') as f:
        for arm, qid, rep, outcome, detail, ms, sql in rows:
            sql = sql.read() if hasattr(sql, 'read') else sql
            f.write(json.dumps({'run': run, 'arm': arm, 'label': ARM_LABEL.get(arm, arm), 'qid': qid, 'rep': rep,
                                'outcome': outcome, 'detail': detail, 'elapsed_ms': ms, 'sql': sql}) + '\n')
    os.replace(path + '.tmp', path)
    log.info('exported %d rows to %s', len(rows), os.path.relpath(path, os.path.dirname(HERE)))


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    ap = argparse.ArgumentParser(description='brief 15 semantic-context evaluation')
    ap.add_argument('--login', required=True)
    ap.add_argument('--run', required=True)
    ap.add_argument('--reps', type=int, default=3)
    ap.add_argument('--arms', default='SALES_BASE,SALES_SEM,SALES_ANNOT')
    ap.add_argument('--gold-only', action='store_true')
    ap.add_argument('--rescore', metavar='SOURCE_RUN', help='re-score the stored SQL of SOURCE_RUN into --run (no LLM)')
    ap.add_argument('--export-only', action='store_true')
    a = ap.parse_args(argv)
    for r in (a.run, a.rescore):
        if r is not None and not RUN_RE.match(r):
            raise SystemExit('run id: letters, digits, _ and - only')
    arms = [x.strip().upper() for x in a.arms.split(',') if x.strip()]

    import oracledb
    from lab_conn import connect, read_env
    login = read_env(a.login)
    try:
        conn, _ = connect(login)
    except oracledb.Error as e:
        raise SystemExit(f'cannot connect: {str(e).splitlines()[0]}') from e
    conn.call_timeout = 120000
    conn.action = 'brief15_eval'
    cur = conn.cursor()
    os.makedirs(RESULTS, exist_ok=True)
    if a.export_only:
        export(cur, a.run)
        return 0
    cur.execute('select qid, question, gold_sql from sem_eval_questions order by qid')
    questions = [(q, t, g.read() if hasattr(g, 'read') else g) for q, t, g in cur.fetchall()]
    gold = {}
    for qid, _, gsql in questions:
        gold[qid] = fetch(cur, gsql)
        log.info('gold %s: %s', qid, gold[qid][:6])
    if a.gold_only:
        return 0
    qtext = {q: t for q, t, _ in questions}
    meta_path = os.path.join(RESULTS, f'{a.run}.meta.json')
    meta = {'run': a.run, 'mode': ('rescore:' + a.rescore) if a.rescore else 'generate', 'rescored_from': a.rescore,
            **fingerprints(cur, questions),
            'gold': {q: sorted(([list(r) for r in v]), key=lambda r: json.dumps(r, sort_keys=True)) for q, v in gold.items()}}
    if os.path.exists(meta_path):
        old = json.load(open(meta_path))
        changed = [k for k in meta if k != 'run' and old.get(k) != meta[k]]
        if changed:
            raise SystemExit(f'{os.path.relpath(meta_path, os.path.dirname(HERE))}: changed since this run started ({", ".join(changed)}); use a new run id')
    else:
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=1)

    cur.execute('select arm, qid, rep from sem_eval_results where run_id = :r', r=a.run)
    done = set(cur.fetchall())
    if a.rescore:
        cur.execute("""select arm, qid, rep, generated_sql, elapsed_ms from sem_eval_results where run_id = :r
                        order by rep, arm, qid""", r=a.rescore)
        src = [(arm, qid, rep, sql.read() if hasattr(sql, 'read') else sql, ms) for arm, qid, rep, sql, ms in cur.fetchall()]
        for arm, qid, rep, gen_sql, ms in src:
            if (arm, qid, rep) in done:
                continue
            outcome, detail = score(cur, gold[qid], gen_sql, qtext[qid]) if gen_sql else ('error', 'no SQL stored')
            cur.execute("""insert into sem_eval_results (run_id, arm, qid, rep, outcome, detail, generated_sql, elapsed_ms)
                           values (:r, :a, :q, :n, :o, :d, :s, :e)""",
                        r=a.run, a=arm, q=qid, n=rep, o=outcome, d=(detail or '')[:400], s=gen_sql, e=ms)
            conn.commit()
            log.info('rescored %s %s rep %d: %s (%s)', arm, qid, rep, outcome, (detail or '')[:80])
    else:
        for rep in range(1, a.reps + 1):
            for arm in arms:
                for qid, question, _ in questions:
                    if (arm, qid, rep) in done:
                        continue
                    t0 = time.monotonic()
                    gen_sql, outcome, detail = None, None, None
                    try:
                        cur.execute("select dbms_cloud_ai.generate(prompt => :p, profile_name => :pr, action => 'showsql') from dual",
                                    p=question, pr=arm)
                        raw = cur.fetchone()[0]
                        gen_sql = clean_sql(raw.read() if hasattr(raw, 'read') else raw)
                    except oracledb.Error as e:
                        outcome, detail = 'error', 'generate: ' + str(e).splitlines()[0][:300]
                    elapsed = int((time.monotonic() - t0) * 1000)
                    if outcome is None:
                        outcome, detail = score(cur, gold[qid], gen_sql, question)
                    cur.execute("""insert into sem_eval_results (run_id, arm, qid, rep, outcome, detail, generated_sql, elapsed_ms)
                                   values (:r, :a, :q, :n, :o, :d, :s, :e)""",
                                r=a.run, a=arm, q=qid, n=rep, o=outcome, d=(detail or '')[:400], s=gen_sql, e=elapsed)
                    conn.commit()
                    log.info('%s %s rep %d: %s (%s) %d ms', arm, qid, rep, outcome, (detail or '')[:80], elapsed)
    export(cur, a.run)
    cur.execute("""select arm, sum(case when outcome = 'correct' then 1 else 0 end), count(*)
                     from sem_eval_results where run_id = :r group by arm order by arm""", r=a.run)
    for arm, ok, n in cur.fetchall():
        log.info('SUMMARY %s (%s): %d of %d correct', arm, ARM_LABEL.get(arm, arm), ok, n)
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
