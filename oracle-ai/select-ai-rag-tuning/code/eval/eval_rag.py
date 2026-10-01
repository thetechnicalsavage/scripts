#!/usr/bin/env python3
# v1.5 - (v1.5, GO-3: a "declined" call is a scored refusal, done on resume) RAG tuning lab: answer layer. DBMS_CLOUD_AI.GENERATE(question, RAG_P_x, 'narrate'),
#        scored by eval_norm.py v1.3 (PLAN.md 5.3).
#        v1.4 (verifier follow-up): on resume, bytes after a run file's last newline (a line cut off
#              by a kill) are moved to <file>.torn and cut from the file under the stamp lock before
#              anything is appended, as 04_probes.py p17 does; before, the new header was appended
#              onto that partial line and the next resume stopped on a corrupt line. A complete line
#              that does not parse (invalid UTF-8 included) stops the run (exit 2) before any call.
#        v1.3 (Codex adversarial review): an exclusive flock on <out>/rag_<stamp>.lock is held from
#              before the done-set is read until the run ends; a second run on the same stamp exits
#              2 before any call.
#        v1.2 (review 1, 29-Sep): bucket D / the retrieval_only tag is never scheduled for narrate
#              unless --include-retrieval-only (exploratory; the header records it); the echo and
#              unscorable-fact checks read the question after number-word conversion.
#        v1.1: after Codex review: index content fingerprint in the stats guard; a fact whose
#              every alternative is echoed by its question stops the run (unscorable); run
#              summaries include rows written before a resume; a torn last JSONL line is skipped.
#        v1.0: first version: seeded interleaved order, pairing guard, identical answer
#              parameters across configs, infra_error backoff/re-queue (never scored), stop
#              conditions, resumable per UTC stamp.
#
# Run as : RAG_LAB (python-oracledb thin), on the host that reaches the PDB.
# Usage  : RAG_LAB_DSN=localhost:1521/orclpdb1 python3 eval_rag.py --configs <id>,<id> [--runs 3]
#            [--answer-layer] [--indexes RAG_M0_C1024_O128_COS,...] [--split dev|test|all]
#            [--only ID,ID] [--stamp 20260929T140000Z] [--plan-only] [--out ../../results]
#            [--include-retrieval-only]   (D dialect paraphrases: exploratory, off by default)
#          The password comes from RAG_LAB_PWD_FILE, else RAG_LAB_PWD, else a hidden prompt
#          (eval_retrieval.connect_from_env); it is never logged or written.
# Re-run : safe and resumable: pass the same --stamp and every (run, config, question) that
#          already has a scored record is skipped; infra errors are retried. Output is appended
#          to results/rag_<stamp>_r<run>.jsonl (one file per run); a line a kill cut off is set
#          aside in rag_<stamp>_r<run>.jsonl.torn on resume. One run per stamp at a time:
#          the run holds an exclusive flock on results/rag_<stamp>.lock (a second one exits 2 at
#          once). PAUSE and the infra window stop between calls and repeat none; a hard kill
#          (SIGKILL, power loss) between a GENERATE call's return and its JSONL line repeats at
#          most that one in-flight paid call on resume.
# Exit   : 0 ok | 2 precondition/guard failed | 3 stopped (PAUSE file / infra window, resumable)
#          4 a lab index's chunk count or content length changed during the run
#
# Order (PLAN.md 5.3): for run r (seed = r, recorded), questions in a seeded shuffle (sort by
# sha256("r:id"), version-independent); configs rotated per question, so time and model drift
# cannot line up with a config. Calls are serial and >= --min-interval seconds apart.
# Errors: ORA-20000 "No matching results found from vector search" is the no-match refusal and
# is scored; ORA-20000 "Sorry, unfortunately ..." (Select AI declining an answer it cannot ground
# in the sources, v1.5) is the declined refusal and is scored too. Every other ORA/HTTP/timeout is infra_error: retried after 5/15/45 s (cap 3), then
# re-queued once at the end of the run; still failing -> recorded, never scored. Stop when more
# than 5% of the last 50 attempt-cycles ended in infra_error, or when the PAUSE file appears.
from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eval_norm as en                    # noqa: E402
import eval_retrieval as er              # noqa: E402

log = logging.getLogger("eval_rag")
VERSION = "eval_rag.py v1.3"
DEFAULT_CORPUS_SRC = os.path.join(HERE, "..", "..", "corpus", "src")
ANSWER_PARAMS = ("provider", "model", "temperature", "max_tokens", "conversation", "seed", "enable_sources")
EXPECTED = {"temperature": 0.0, "max_tokens": 1024}        # contract: temperature 0, max_tokens 1024


@dataclasses.dataclass
class Config:
    config_id: str
    index: str
    rag_profile: str
    model_name: str | None = None
    pairing: er.Pairing | None = None


@dataclasses.dataclass
class Item:
    run: int
    order: int
    q: dict
    cfg: Config


# ---------------------------------------------------------------------------------------------
# configs and guards
# ---------------------------------------------------------------------------------------------
def configs_from_args(a) -> list:
    out = []
    if a.indexes:
        for x in a.indexes.split(","):
            idx = er.lab_index_name(x)
            out.append(Config(idx, idx, "RAG_P_" + idx[4:]))
    if a.configs or a.answer_layer:
        import csv
        with open(a.experiments, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        want = [x.strip() for x in a.configs.split(",") if x.strip()] if a.configs else None
        for want_id in (want or [r["config_id"] for r in rows if er.truthy(r.get("answer_layer"))]):
            r = er.experiment_row(a.experiments, want_id)
            idx = er.lab_index_name(r["index_name"])
            out.append(Config(r["config_id"], idx, er.simple_name(r.get("rag_profile") or "RAG_P_" + idx[4:]),
                              (r.get("model_name") or "").strip() or None))
    seen, uniq = set(), []
    for c in out:                            # identical indexes are de-duplicated (PLAN.md 5.3)
        if c.index in seen:
            log.info("event=dedup config=%s index=%s", c.config_id, c.index)
            continue
        seen.add(c.index)
        uniq.append(c)
    if not uniq:
        raise er.GuardError("no configs: pass --configs, --answer-layer or --indexes")
    return uniq


def check_answer_params(configs):
    """PLAN.md 2.4: conversation false, temperature 0, max_tokens 1024, and identical answer
    parameters in every config, so only retrieval differs between configs."""
    ref = None
    for c in configs:
        ra = c.pairing.rag_attributes
        if ra.get("conversation", "false").lower() not in ("false", "0", "no"):
            raise er.GuardError(f"{c.rag_profile}: conversation must be false (got {ra.get('conversation')!r})")
        for k, v in EXPECTED.items():
            got = er._num(ra.get(k))
            if got is None or float(got) != v:
                raise er.GuardError(f"{c.rag_profile}: {k} must be {v:g} (got {ra.get(k)!r})")
        cur = {k: ra.get(k) for k in ANSWER_PARAMS}
        if ref is None:
            ref = (c.rag_profile, cur)
        elif cur != ref[1]:
            raise er.GuardError(f"answer parameters differ: {ref[0]} {ref[1]} vs {c.rag_profile} {cur}")
    return ref[1] if ref else {}


def unscorable_facts(questions):
    """Fact groups whose every alternative already appears in the question: no answer can ever
    prove them, so the run refuses to start (fix the gold set through P17). v1.2: the question is
    read as the scorer reads it (number words as digits: 'two days' holds 2)."""
    bad = []
    for q in questions:
        qn = en.question_text(q["question"])
        for alt in q.get("facts") or []:
            if alt and all(en._pattern(x, unit=False).search(qn) for x in alt):
                bad.append((q["id"], alt))
    return bad


def echo_violations(questions):
    """Authoring rule (PLAN.md 5.1): no fact string in its own question. The scorer drops echoed
    alternatives anyway; this lists them at start so they can be fixed before GO-3."""
    bad = []
    for q in questions:
        qn = en.question_text(q["question"])
        for alt in q.get("facts") or []:
            for x in alt:
                if en._pattern(x, unit=False).search(qn):
                    bad.append((q["id"], x))
    return bad


RETRIEVAL_ONLY_BUCKETS = ("D",)
RETRIEVAL_ONLY_TAG = "retrieval_only"


def retrieval_only(q) -> bool:
    """Bucket D (Gulf-dialect paraphrases of T questions) is retrieval-layer only (PLAN.md 5.1):
    each paraphrases a T fact already in the set, so answering it would count that fact twice."""
    return q.get("bucket") in RETRIEVAL_ONLY_BUCKETS or RETRIEVAL_ONLY_TAG in (q.get("tags") or [])


def answer_layer_questions(questions, include_retrieval_only=False):
    """The questions the answer layer schedules: every retrieval_only question is left out unless
    include_retrieval_only (an exploratory run, never part of a headline)."""
    return [q for q in questions if include_retrieval_only or not retrieval_only(q)]


# ---------------------------------------------------------------------------------------------
# schedule and bookkeeping (pure; unit-tested)
# ---------------------------------------------------------------------------------------------
def shuffled(questions, seed: int):
    return sorted(questions, key=lambda q: hashlib.sha256(f"{seed}:{q['id']}".encode()).hexdigest())


def build_schedule(questions, configs, runs, first_run=1):
    out = []
    n = len(configs)
    for run in range(first_run, first_run + runs):
        for i, q in enumerate(shuffled(questions, run)):
            for j in range(n):
                out.append(Item(run, i, q, configs[(i + j) % n]))
    return out


class InfraWindow:
    """Stop condition: infra_error above 5% of any 50-call window (PLAN.md 9). One entry per
    attempt-cycle (a call and its retries), so a single retried call is counted once."""

    def __init__(self, size=50, max_rate=0.05):
        self.buf = collections.deque(maxlen=size)
        self.size, self.max_rate = size, max_rate

    def add(self, infra: bool):
        self.buf.append(bool(infra))

    def exceeded(self) -> bool:
        return sum(self.buf) > self.max_rate * self.size


def lock_stamp(out_dir: str, stamp: str):
    """One eval_rag.py per stamp: an exclusive flock on <out_dir>/rag_<stamp>.lock, taken without
    waiting and held until the returned file is closed (or the process ends). Two runs on one
    stamp would both see a (run, config, question) as open and pay for it twice."""
    import fcntl
    path = os.path.join(out_dir, f"rag_{stamp}.lock")
    f = open(path, "a", encoding="utf-8")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise er.GuardError(f"another eval_rag.py run holds stamp {stamp} ({os.path.basename(path)}); "
                            "wait for it to finish (or stop it) and resume") from None
    except OSError as e:
        f.close()
        raise er.GuardError(f"cannot lock {os.path.basename(path)} ({e.strerror})") from None
    return f


def read_jsonl_resume(path: str, repair: bool = False) -> list:
    """Records of a run file this harness appends to (04_probes.py p17 reads its JSONL the same way).
    Bytes after the last newline are a line cut off by a hard kill, not a record: with repair (the
    caller holds the stamp lock) they are appended to <path>.torn as evidence and cut from the file,
    so the next record starts on a line of its own; without repair they are skipped. A complete line
    that does not parse is corruption and stops the run."""
    with open(path, "rb") as f:
        data = f.read()
    cut = data.rfind(b"\n") + 1
    whole, torn = data[:cut], data[cut:]
    if torn:
        name = os.path.basename(path)
        if repair:
            with open(path + ".torn", "ab") as t:
                t.write(torn + b"\n")
            with open(path, "r+b") as f:
                f.truncate(cut)
            log.warning("event=torn_last_line file=%s moved_to=%s.torn (its call is made again)", name, name)
        else:
            log.warning("event=torn_last_line file=%s skipped", name)
    out = []
    # split on newline bytes only: an answer written with ensure_ascii=False may hold U+2028, which
    # str.splitlines() would treat as a line end; each line is decoded on its own, inside the try
    for n, raw in enumerate(whole.split(b"\n"), 1):
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw.decode("utf-8")))
        except ValueError:                                  # UnicodeDecodeError is a ValueError too
            raise er.GuardError(f"{os.path.basename(path)}:{n}: corrupt JSONL line (not a cut-off last line); "
                                "look at it before resuming") from None
    return out


def load_prior(paths, repair=False):
    """Answer records already written under this stamp (resume). With repair (under the stamp lock,
    right before the writers open the files) a line cut off by a kill is cut from its file."""
    out = []
    for p in paths:
        if os.path.exists(p):
            out.extend(r for r in read_jsonl_resume(p, repair=repair) if r.get("type") == "answer")
    return out


def done_keys(records):
    return {(r["run"], r["config_id"], r["id"]) for r in records if r.get("status") in en.SCORED_STATUSES}


def load_done(paths):
    return done_keys(load_prior(paths))


def answer_record(item: Item, res: er.CallResult, cycle: int, corpus_nums) -> dict:
    q, c = item.q, item.cfg
    rec = {"type": "answer", "utc": er.utc_now().isoformat(), "run": item.run, "seed": item.run,
           "order": item.order, "cycle": cycle, "config_id": c.config_id, "index": c.index,
           "rag_profile": c.rag_profile,
           "profile_model": (c.pairing.rag_attributes.get("model") if c.pairing else None),
           "id": q["id"], "fact_id": q["fact_id"], "q_lang": q["q_lang"], "bucket": q["bucket"],
           "split": q["split"], "answerable": q["answerable"], "status": res.status,
           "attempts": res.attempts, "latency_ms": res.latency_ms, "error": res.error,
           "answer": res.text, "verdict": None}
    if res.status in en.SCORED_STATUSES:
        rec["declined"] = res.status == "declined"      # v1.5: Select AI refused an ungrounded answer
        rec.update(en.score_answer(res.text, q, no_match=res.status in ("no_match", "declined"),
                                   corpus_nums=corpus_nums))
    return rec


def summarize_run(records):
    by = collections.defaultdict(lambda: collections.Counter())
    for r in records:
        key = r["config_id"]
        if r["status"] == "infra_error" and r["cycle"] == 2:
            by[key]["infra_final"] += 1
        elif r["status"] != "infra_error":
            by[key][r["verdict"]] += 1
            by[key]["scored"] += 1
    out = {}
    for k, c in by.items():
        total = c["scored"] + c["infra_final"]
        rate = c["infra_final"] / total if total else 0.0
        base = {"scored": 0, "infra_final": 0, **{v: 0 for v in en.VERDICTS}}
        base.update(c)
        out[k] = dict(base, infra_rate=round(rate, 4), publishable=rate <= 0.02)
    return out


# ---------------------------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------------------------
def execute(schedule, caller, writers, corpus_nums, done=frozenset(), pause_file=None,
            window=None, prior=()) -> list:
    """Runs the schedule serially. Returns every record written. Raises StopRun. `prior` holds
    the records of an earlier session under the same stamp, so run summaries stay complete."""
    window = window or InfraWindow()
    written = []
    by_run = collections.OrderedDict()
    for it in schedule:
        by_run.setdefault(it.run, []).append(it)

    def one(it, cycle):
        if er.pause_requested(pause_file):
            raise er.StopRun(f"PAUSE file present: {pause_file}")
        res = caller.call(it.q["question"], it.cfg.rag_profile, "narrate")
        window.add(res.status == "infra_error")
        rec = answer_record(it, res, cycle, corpus_nums)
        writers[it.run].write(rec)
        written.append(rec)
        log.info("event=answer run=%d cfg=%s id=%s status=%s verdict=%s attempts=%d",
                 it.run, it.cfg.config_id, it.q["id"], res.status, rec["verdict"], res.attempts)
        if window.exceeded():
            raise er.StopRun(f"infra_error above 5% of the last {window.size} calls")
        return res.status

    for run, items in by_run.items():
        requeue = []
        for it in items:
            if (it.run, it.cfg.config_id, it.q["id"]) in done:
                continue
            if one(it, 1) == "infra_error":
                requeue.append(it)
        for it in requeue:                    # re-queued at the end of the run, once
            one(it, 2)
        writers[run].write({"type": "summary", "utc": er.utc_now().isoformat(), "run": run,
                            "configs": summarize_run([r for r in list(prior) + written
                                                      if r["run"] == run])})
    return written


def main(argv=None) -> int:
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Select AI RAG lab: answer layer (narrate)")
    ap.add_argument("--configs", default="", help="config_ids from experiments.csv")
    ap.add_argument("--answer-layer", action="store_true", help="every row with answer_layer set")
    ap.add_argument("--indexes", default="", help="lab indexes (RAG profile RAG_P_<index without RAG_>)")
    ap.add_argument("--experiments", default=er.DEFAULT_EXPERIMENTS)
    ap.add_argument("--questions", default=er.DEFAULT_QUESTIONS)
    ap.add_argument("--manifest", default=er.DEFAULT_MANIFEST)
    ap.add_argument("--corpus-src", default=DEFAULT_CORPUS_SRC)
    ap.add_argument("--out", default=er.DEFAULT_OUT)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--first-run", type=int, default=1)
    ap.add_argument("--split", choices=("all", "dev", "test"), default="all")
    ap.add_argument("--only", default="")
    ap.add_argument("--stamp", default="", help="resume the run files of this UTC stamp")
    ap.add_argument("--min-interval", type=float, default=1.0)
    ap.add_argument("--call-timeout", type=int, default=180, help="seconds")
    ap.add_argument("--pause-file", default=er.DEFAULT_PAUSE)
    ap.add_argument("--allow-unsealed", action="store_true")
    ap.add_argument("--plan-only", action="store_true", help="print the call order; no database")
    ap.add_argument("--include-retrieval-only", action="store_true",
                    help="also answer bucket D / retrieval_only questions (exploratory, never a headline)")
    a = ap.parse_args(argv)

    db, writers, lock = None, {}, None
    try:
        if a.runs < 1 or a.first_run < 1:
            raise er.GuardError("--runs and --first-run must be >= 1")
        configs = configs_from_args(a)
        corpus = er.Corpus.from_csv(a.manifest)
        meta, qs, seal = er.load_questions(a.questions, corpus)
        if not seal["ok"]:
            if not (a.allow_unsealed or a.plan_only):
                raise er.GuardError(f"questions.json seal mismatch (digest {seal['digest']}); "
                                    "--allow-unsealed is for dry runs only")
            log.warning("event=unsealed_questions digest=%s", seal["digest"])
        qs = er.select_questions(qs, a.split, a.only)
        n_selected = len(qs)
        qs = answer_layer_questions(qs, a.include_retrieval_only)
        if len(qs) < n_selected:
            log.info("event=retrieval_only_excluded count=%d (pass --include-retrieval-only to answer them)",
                     n_selected - len(qs))
        elif a.include_retrieval_only:
            log.warning("event=retrieval_only_included exploratory=true")
        if not qs:
            raise er.GuardError("no questions left to answer after the split/--only/retrieval_only filters")
        for qid, term in echo_violations(qs):
            log.warning("event=question_echo id=%s term=%r (ignored by the scorer)", qid, term)
        dead = unscorable_facts(qs)
        if dead:
            raise er.GuardError(f"facts fully echoed by their question (unscorable): {dead[:5]}")
        schedule = build_schedule(qs, configs, a.runs, a.first_run)
        if a.plan_only:
            for it in schedule:
                sys.stdout.write(f"{it.run}\t{it.order}\t{it.q['id']}\t{it.cfg.config_id}\n")
            return 0
        corpus_nums = en.corpus_numbers(a.corpus_src)

        db = er.OracleLabDB(er.connect_from_env(), call_timeout_ms=a.call_timeout * 1000)
        stats_before = {}
        for c in configs:
            c.pairing = er.resolve_pairing(db, c.index, c.rag_profile, expected_model=c.model_name)
            s, _ = er.snapshot_index(db, c.index)
            er.check_stats(s, er.previous_stats(a.out, c.index), c.index)
            stats_before[c.index] = s
        params = check_answer_params(configs)

        stamp = a.stamp or er.stamp_of(er.utc_now())
        os.makedirs(a.out, exist_ok=True)
        lock = lock_stamp(a.out, stamp)          # held until the finally below, before the done-set is read
        paths = {r: os.path.join(a.out, f"rag_{stamp}_r{r}.jsonl")
                 for r in range(a.first_run, a.first_run + a.runs)}
        prior = load_prior(paths.values(), repair=True)   # under the lock, before any append
        done = done_keys(prior)
        for r, p in paths.items():
            resumed = os.path.exists(p)
            writers[r] = er.JsonlWriter(p)
            writers[r].write({"type": "header", "tool": VERSION, "utc": er.utc_now().isoformat(),
                              "stamp": stamp, "run": r, "seed": r, "resumed": resumed,
                              "configs": [{"config_id": c.config_id, "index": c.index,
                                           "rag_profile": c.rag_profile,
                                           "pairing": dataclasses.asdict(c.pairing)} for c in configs],
                              "answer_params": params, "index_stats": stats_before,
                              "questions_file": os.path.basename(a.questions), "seal": seal,
                              "questions_meta_version": meta.get("version"), "split": a.split,
                              "only": a.only, "n_questions": len(qs), "already_done": len(done),
                              "include_retrieval_only": a.include_retrieval_only,
                              "retrieval_only_excluded": n_selected - len(qs),
                              "min_interval_s": a.min_interval, "backoff_s": [5, 15, 45]})
        log.info("event=start stamp=%s configs=%d questions=%d runs=%d calls=%d done=%d",
                 stamp, len(configs), len(qs), a.runs, len(schedule), len(done))
        caller = er.Caller(db, min_interval=a.min_interval)
        execute(schedule, caller, writers, corpus_nums, done, a.pause_file, prior=prior)
        rc = 0
        for c in configs:
            s, _ = er.snapshot_index(db, c.index)
            if not er.stats_equal(s, stats_before[c.index]):
                log.error("event=index_changed_during_run index=%s", c.index)
                rc = 4
        log.info("event=done stamp=%s rc=%d", stamp, rc)
        return rc
    except er.GuardError as e:
        log.error("event=guard_failed %s", e)
        return 2
    except er.StopRun as e:
        log.warning("event=stopped %s (resume with --stamp)", e)
        return 3
    except er.DBCallError as e:
        log.error("event=db_error %s", er.redact(e))
        return 2
    finally:
        for w in writers.values():
            w.close()
        if db is not None:
            db.close()
        if lock is not None:
            lock.close()                         # after the writers: the last line is on disk first


if __name__ == "__main__":
    sys.exit(main())
