#!/usr/bin/env python3
# v1.1 - brief 09 (Select AI RAG tuning, EN + AR): flatten the per-index result files into the three
#        chart inputs of make_charts.py (its header "Input contract"): retrieval.jsonl,
#        chunk_coverage.jsonl and answers.jsonl under results/charts_input/.
#        v1.1 (Codex review 1): an answer run must be complete before it is charted: every run file
#              ends its last session with a summary line, holds a record for every (config, question)
#              eval_rag.py schedules, and runs 1..--expect-runs (default 3) are all present; a config
#              whose run summary is not publishable (infra_error above 2%) is logged; a retrieval
#              direction must start with the question's language. (Codex re-review) VERSION follows
#              the header; a malformed run summary (configs not a mapping, infra_rate missing, not a
#              number, not finite or outside 0..1, or not one entry per config of the run) refuses
#              (exit 2) instead of a traceback or a 0; a summary line must name the file's run.
#        v1.0: first version.
#
# Run as : the local authoring workstation; no database, no network. Reads the top level of
#          results/ (never supp_v24/ or another subfolder) and writes results/charts_input/ only.
# Usage  : python3 flatten_results.py [--results ../../results] [--out ../../results/charts_input]
#            [--questions ../eval/questions.json] [--rag-stamp 20260930T023755Z]
#            [--decisions ../../results/dev_decisions_<UTC>.json] [--stage CONFIG=STAGE ...]
#            [--audit FILE | --no-audit] [--expect-runs 3]
#          then: python3 make_charts.py --results ../../results/charts_input
# Re-run : safe. Every output is replaced atomically; an output whose input is absent is removed,
#          so make_charts.py never draws a stale file. Nothing is written when a guard fails.
# Exit   : 0 written | 1 no input of any kind | 2 refused (mixed questions seals, an --only or
#          single-split run, two files for one index, an incomplete or malformed file, a field
#          that disagrees with questions.json or the index name)
#
# Inputs (file names as the eval tools write them):
#   retrieval_<INDEX>_<UTC>.jsonl  eval_retrieval.py: one file per index, split "all", no --only,
#                                  complete (a summary line and every question of questions.json),
#                                  sealed with the same digest as the current questions.json
#   coverage_<INDEX>_<UTC>.jsonl   chunk_coverage.py: one file per index, complete (summary line,
#                                  as many chunk lines as chunks_measured)
#   rag_<UTC>_r<run>.jsonl         eval_rag.py: one stamp (--rag-stamp when several are present),
#                                  split "all" or "test", no --only, the same seal, complete (runs
#                                  1..--expect-runs, each with a summary line after its last header
#                                  and a record for every scheduled config x question). Absent:
#                                  skipped with a warning.
#
# Field mapping, retrieval.jsonl (one row per index x question x run_label):
#   config_id        the index name, e.g. RAG_M0_C1024_O128_COS (make_charts.py labels, picks and
#                    --hitk-config use it); experiments_config keeps the experiments.csv config id
#   model_key, chunk_size, chunk_overlap, metric   parsed from the index name (eval_retrieval.INDEX_NAME)
#   split, bucket, answerable   the record, checked against questions.json
#   qid, q_lang      questions.json id and q_lang, checked against the record and the -EN/-AR suffix
#   masked           run_label is mask_ar or mask_en (not "unmasked")
#   doc_lang         masked: the twin left in the candidates (mask_ar -> en, mask_en -> ar, checked
#                    against the direction); unmasked: questions.json doc_lang (en | ar, "both" for a
#                    twin pair, "none" for an unanswerable question)
#   containable      the record's flag; an unanswerable question has no span, so false
#   evidence_rank    first_evidence_rank: 1-based rank of the first evidence chunk within the top 20, else null
#   best_gold_score  best_gold.similarity;  best_wrong_score  best_non_gold.similarity (1 - cosine distance)
#   Bucket D (retrieval-only dialect paraphrases of T facts) is left out, as in every pooled figure
#   (eval_retrieval.RETRIEVAL_ONLY_BUCKETS); run_label, direction and fact_id are kept for tracing.
# chunk_coverage.jsonl: one row per measured chunk whose stored embedding equals its content's
#   (stored_matches_content); model_key and chunk_size from the index name; lang, chunk_chars
#   (= chars) and embedded_chars from the chunk line.
# answers.jsonl: one row per config x question x scored run (verdicts via stats.load_answers, with the
#   human audit applied when the audit file exists; an infra_error-only run is skipped).
#   config_id = the index; stage = --stage CONFIG=STAGE, else "S10" for the dev decisions' S10 index
#   when the answer run used its calibrated knobs (results/go3_configs.md), else the experiments
#   row's stage from that index's retrieval header.
from __future__ import annotations

import argparse
import collections
import contextlib
import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BRIEF = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "eval"))
import eval_retrieval as er  # noqa: E402
import stats as st  # noqa: E402
import eval_rag as rag  # noqa: E402

log = logging.getLogger("flatten_results")
VERSION = "flatten_results.py v1.1"

DEFAULT_RESULTS = os.path.join(BRIEF, "results")
DEFAULT_OUT = os.path.join(DEFAULT_RESULTS, "charts_input")
OUT_FILES = {"retrieval": "retrieval.jsonl", "coverage": "chunk_coverage.jsonl", "answers": "answers.jsonl"}
MANIFEST = "manifest.json"

STAMP = r"(\d{8}T\d{6}Z)"
RETRIEVAL_FILE = re.compile(rf"^retrieval_(RAG_[A-Z0-9_]+)_{STAMP}\.jsonl$")
COVERAGE_FILE = re.compile(rf"^coverage_(RAG_[A-Z0-9_]+)_{STAMP}\.jsonl$")
RAG_FILE = re.compile(rf"^rag_{STAMP}_r([0-9]+)\.jsonl$")
DECISIONS_FILE = re.compile(rf"^dev_decisions_{STAMP}\.json$")
ID_SUFFIX = re.compile(r"-(EN|AR)$")

LEFT_BY_MASK = {"mask_ar": "en", "mask_en": "ar"}      # the twin that stays in the candidate set
RUN_ORDER = {"unmasked": 0, "mask_ar": 1, "mask_en": 2}
TWIN_BUCKETS = ("T", "D")
MAX_ERRORS = 10


class Refused(Exception):
    """An input failed a guard: nothing is written (exit 2)."""


def _stats_call(fn, *args, **kw):
    """stats.py stops with SystemExit(message) on a bad input; here that is a refusal."""
    try:
        return fn(*args, **kw)
    except SystemExit as e:
        raise Refused(str(e)) from None


def read_lines(path):
    """Every JSON object of a JSONL file (stats.read_jsonl: a line that does not parse refuses)."""
    return _stats_call(lambda: [r for _, r in st.read_jsonl([path])])


def list_inputs(results, pattern):
    """Files directly under results/ whose name matches pattern, sorted by name."""
    try:
        names = sorted(os.listdir(results))
    except OSError as e:
        raise Refused(f"cannot list {os.path.basename(results) or results}: {e.strerror}") from None
    return [os.path.join(results, n) for n in names
            if pattern.match(n) and os.path.isfile(os.path.join(results, n))]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_index(index):
    """(model_key, chunk_size, chunk_overlap, metric suffix) from RAG_<M>_C<size>_O<overlap>_<METRIC>."""
    m = er.INDEX_NAME.match(index or "")
    if not m:
        raise Refused(f"{index!r} is not a RAG_<model>_C<chunk>_O<overlap>_<metric> index name")
    return m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)


def _errors(errs):
    more = f" (+{len(errs) - MAX_ERRORS} more)" if len(errs) > MAX_ERRORS else ""
    return "; ".join(errs[:MAX_ERRORS]) + more


def _score(best):
    """similarity of a best_gold / best_non_gold block, or None; a non-finite value refuses."""
    if not best:
        return None
    v = best.get("similarity")
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"similarity {v!r} is not a finite number")
    return float(v)


def expected_runs(q):
    """The run labels eval_retrieval.plan_runs gives a question (without the corpus)."""
    if q["answerable"] and q["bucket"] in TWIN_BUCKETS:
        return {"unmasked", "mask_ar", "mask_en"}
    return {"unmasked"}


def check_seal(header, digest, name):
    seal = header.get("seal") or {}
    if seal.get("digest") != digest or not seal.get("ok"):
        raise Refused(f"{name}: questions seal {str(seal.get('digest'))[:12]}... (ok={seal.get('ok')}) "
                      f"is not the current questions.json ({digest[:12]}...)")


# ---------------------------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------------------------
def load_retrieval(paths, questions, digest):
    """{index: {"header", "records", "path"}} after the per-file guards; stats.load_retrieval refuses
    two files for one index and a query record without its own header."""
    names = collections.Counter(RETRIEVAL_FILE.match(os.path.basename(p)).group(1) for p in paths)
    dup = sorted(i for i, n in names.items() if n > 1)
    if dup:
        raise Refused(f"two or more retrieval files for {', '.join(dup)}: keep one per index in results/")
    for p in paths:
        name = os.path.basename(p)
        lines = read_lines(p)
        kinds = collections.Counter(r.get("type") for r in lines)
        if kinds["header"] != 1:
            raise Refused(f"{name}: {kinds['header']} header lines, expected 1")
        if kinds["summary"] != 1:
            raise Refused(f"{name}: no summary line (the run stopped before the end)")
        h = next(r for r in lines if r.get("type") == "header")
        if h.get("index") != RETRIEVAL_FILE.match(name).group(1):
            raise Refused(f"{name}: header index {h.get('index')!r} differs from the file name")
        if h.get("only"):
            raise Refused(f"{name}: an --only run ({h['only']!r}) cannot be charted")
        if h.get("split") != "all":
            raise Refused(f"{name}: split {h.get('split')!r}; the charts need a split 'all' run")
        check_seal(h, digest, name)
    data = _stats_call(st.load_retrieval, paths)
    for p in paths:
        idx = RETRIEVAL_FILE.match(os.path.basename(p)).group(1)
        data[idx]["path"] = p
        got = set(data[idx]["records"])
        want = {(qid, lab) for qid, q in questions.items() for lab in expected_runs(q)}
        if got != want:
            missing, extra = sorted(want - got), sorted(got - want)
            raise Refused(f"{os.path.basename(p)}: records do not match questions.json "
                          f"({len(missing)} missing, first {missing[:2]}; {len(extra)} unexpected, first {extra[:2]})")
    return data


def retrieval_rows(data, questions):
    """(rows, counts) in the make_charts.py retrieval contract; refuses on any field mismatch."""
    order = {qid: i for i, qid in enumerate(questions)}
    rows, errs = [], []
    counts = collections.Counter()
    for idx in sorted(data):
        h = data[idx]["header"]
        model, size, overlap, metric = parse_index(idx)
        exp = h.get("experiments_row") or {}
        for key, want in (("model_key", model), ("chunk_size", str(size)), ("chunk_overlap", str(overlap)),
                          ("metric", metric), ("index_name", idx)):
            if exp.get(key) not in (None, "", "TBD") and str(exp[key]) != want:
                errs.append(f"{idx}: experiments row {key} = {exp[key]!r}, the index name says {want!r}")
        recs = sorted(data[idx]["records"].values(), key=lambda r: (order[r["id"]], RUN_ORDER.get(r["run_label"], 9)))
        for r in recs:
            q = questions[r["id"]]
            where = f"{idx} {r['id']} {r['run_label']}"
            if q["bucket"] in er.RETRIEVAL_ONLY_BUCKETS:
                counts["retrieval_only_left_out"] += 1
                continue
            for key in ("q_lang", "bucket", "split", "answerable", "fact_id"):
                if r.get(key) != q[key]:
                    errs.append(f"{where}: {key} {r.get(key)!r}, questions.json has {q[key]!r}")
            sfx = ID_SUFFIX.search(r["id"])
            if sfx and sfx.group(1).lower() != q["q_lang"]:
                errs.append(f"{where}: id suffix says {sfx.group(1)}, q_lang is {q['q_lang']!r}")
            label = r["run_label"]
            left, _, right = (r.get("direction") or "").lower().partition("->")
            if left != q["q_lang"]:
                errs.append(f"{where}: direction {r.get('direction')!r} does not start with q_lang {q['q_lang']!r}")
            if label in LEFT_BY_MASK:
                doc_lang = LEFT_BY_MASK[label]
                if right != doc_lang or not r.get("masked_docs"):
                    errs.append(f"{where}: direction {r.get('direction')!r} / masked_docs "
                                f"{r.get('masked_docs')!r} do not match {label}")
            else:
                doc_lang = q["doc_lang"]
                if right != doc_lang or r.get("masked_docs"):
                    errs.append(f"{where}: direction {r.get('direction')!r} does not match doc_lang {doc_lang!r}")
            cont = r.get("containable")
            if not q["answerable"]:
                if cont not in (None, False):
                    errs.append(f"{where}: an unanswerable question marked containable {cont!r}")
                cont = False
            elif not isinstance(cont, bool):
                errs.append(f"{where}: containable {cont!r} is not a bool")
            rank = r.get("first_evidence_rank")
            top = h.get("top") or er.TOP
            if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int) or not 1 <= rank <= top):
                errs.append(f"{where}: first_evidence_rank {rank!r} outside 1..{top}")
            try:
                gold, wrong = _score(r.get("best_gold")), _score(r.get("best_non_gold"))
            except ValueError as e:
                errs.append(f"{where}: {e}")
                continue
            rows.append({"config_id": idx, "model_key": model, "chunk_size": size, "chunk_overlap": overlap,
                         "metric": metric, "split": q["split"], "qid": r["id"], "q_lang": q["q_lang"],
                         "doc_lang": doc_lang, "bucket": q["bucket"], "answerable": q["answerable"],
                         "masked": label in LEFT_BY_MASK, "containable": cont, "evidence_rank": rank,
                         "best_gold_score": gold, "best_wrong_score": wrong,
                         "run_label": label, "direction": r.get("direction"), "fact_id": q["fact_id"],
                         "experiments_config": h.get("config_id")})
            counts["rows"] += 1
    if errs:
        raise Refused("retrieval: " + _errors(errs))
    return rows, counts


# ---------------------------------------------------------------------------------------------
# chunk coverage
# ---------------------------------------------------------------------------------------------
def coverage_rows(paths):
    """(rows, counts, indexes) in the make_charts.py chunk_coverage contract."""
    seen, rows, errs = {}, [], []
    counts = collections.Counter()
    for p in paths:
        name = os.path.basename(p)
        lines = read_lines(p)
        heads = [r for r in lines if r.get("type") == "header"]
        chunks = [r for r in lines if r.get("type") == "chunk"]
        if len(heads) != 1:
            raise Refused(f"{name}: {len(heads)} header lines, expected 1")
        if not any(r.get("type") == "summary" for r in lines):
            raise Refused(f"{name}: no summary line (the run stopped before the end)")
        h = heads[0]
        idx = h.get("index")
        if idx != COVERAGE_FILE.match(name).group(1):
            raise Refused(f"{name}: header index {idx!r} differs from the file name")
        if idx in seen:
            raise Refused(f"two coverage files for {idx} ({seen[idx]}, {name}): keep one per index")
        seen[idx] = name
        if h.get("chunks_measured") != len(chunks):
            raise Refused(f"{name}: {len(chunks)} chunk lines, header says {h.get('chunks_measured')}")
        model, size, _, _ = parse_index(idx)
        for n, c in enumerate(chunks, 1):
            if c.get("skipped"):
                counts["skipped_empty"] += 1
                continue
            if c.get("stored_matches_content") is not True:
                counts["stored_mismatch"] += 1
                continue
            if c.get("lang") not in ("en", "ar"):
                counts["lang_unknown"] += 1
                continue
            chars, emb = c.get("chars"), c.get("embedded_chars")
            if not all(isinstance(v, int) and not isinstance(v, bool) for v in (chars, emb)) or not 0 <= emb <= chars:
                errs.append(f"{name} chunk {n}: chars {chars!r}, embedded_chars {emb!r}")
                continue
            rows.append({"model_key": model, "chunk_size": size, "lang": c["lang"], "chunk_chars": chars,
                         "embedded_chars": emb, "config_id": idx})
            counts["rows"] += 1
    if errs:
        raise Refused("coverage: " + _errors(errs))
    return rows, counts, sorted(seen)


# ---------------------------------------------------------------------------------------------
# answers
# ---------------------------------------------------------------------------------------------
def pick_rag_files(results, stamp=None):
    """(stamp, [paths]) of one eval_rag.py run, or (None, []) when there is none."""
    files = list_inputs(results, RAG_FILE)
    stamps = sorted({RAG_FILE.match(os.path.basename(p)).group(1) for p in files})
    if stamp:
        if stamp not in stamps:
            raise Refused(f"--rag-stamp {stamp}: no rag_{stamp}_r<run>.jsonl (stamps present: {stamps or 'none'})")
    elif len(stamps) > 1:
        raise Refused(f"answer files from {len(stamps)} runs ({', '.join(stamps)}): pass --rag-stamp")
    elif stamps:
        stamp = stamps[0]
    else:
        return None, []
    return stamp, [p for p in files if RAG_FILE.match(os.path.basename(p)).group(1) == stamp]


def check_rag_complete(paths, configs, questions, expect_runs):
    """Refuses an answer run that is still going, was stopped, or was copied in part: runs
    1..expect_runs must all be there; in each file the last header (a resume appends one) is
    followed by a summary line (eval_rag.execute writes it when the run ends), and every
    (config, question) that eval_rag.py schedules for the header's split has a record (scored,
    or infra_error after its re-queue). Returns {config_id: worst infra_rate in any run summary}."""
    runs, infra = {}, collections.defaultdict(float)
    qlist = list(questions.values())
    for p in paths:
        name = os.path.basename(p)
        run_no = int(RAG_FILE.match(name).group(2))
        lines = read_lines(p)
        heads = [i for i, r in enumerate(lines) if r.get("type") == "header"]
        if not heads:
            raise Refused(f"{name}: no header line")
        if any(lines[i].get("run") != run_no for i in heads):
            raise Refused(f"{name}: a header names another run than r{run_no}")
        tail = [r for r in lines[heads[-1] + 1:] if r.get("type") == "summary"]
        if not tail:
            raise Refused(f"{name}: no summary line after its last header (the run is still going, "
                          "was stopped, or was copied before it ended)")
        if any(r.get("run") != run_no for r in tail):
            raise Refused(f"{name}: a summary line of another run than r{run_no}")
        h = lines[heads[-1]]
        qs = rag.answer_layer_questions(er.select_questions(qlist, h.get("split") or "all", ""),
                                        bool(h.get("include_retrieval_only")))
        want = {(cid, q["id"]) for cid in configs for q in qs}
        answers = [r for r in lines if r.get("type") == "answer"]
        if any(r.get("run") != run_no for r in answers):
            raise Refused(f"{name}: an answer record of another run")
        got = {(str(r.get("config_id")), r.get("id")) for r in answers}
        if got != want:
            missing, extra = sorted(want - got), sorted(got - want)
            raise Refused(f"{name}: answer records do not match the schedule ({len(missing)} missing, first "
                          f"{missing[:2]}; {len(extra)} unexpected, first {extra[:2]})")
        summ = tail[-1].get("configs")
        rates = {str(cid): c.get("infra_rate") for cid, c in summ.items() if isinstance(c, dict)} \
            if isinstance(summ, dict) else None
        # eval_rag.summarize_run writes a finite fraction in 0..1 for every config of the run
        if rates is None or len(rates) != len(summ) or set(rates) != set(configs) or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1
                for v in rates.values()):
            raise Refused(f"{name}: the run summary's configs block is malformed")
        for cid, rate in rates.items():
            infra[cid] = max(infra[cid], rate)
        runs[run_no] = name
    if sorted(runs) != list(range(1, expect_runs + 1)):
        raise Refused(f"answer runs {sorted(runs)} present, expected 1..{expect_runs} (pass --expect-runs "
                      "for a run with another count)")
    for cid, rate in sorted(infra.items()):
        if rate > 0.02:                                  # PLAN.md 5.3: such a config is not published
            log.warning("event=config_not_publishable config=%s infra_rate=%.4f (above 2%%)", cid, rate)
    return dict(infra)


def rag_configs(headers, stamp):
    """{config_id: {"index", "match_limit", "similarity_threshold"}}, identical in every header
    of the run (a resume writes one more header; the knobs must not have moved in between)."""
    out = None
    for h in headers:
        if h.get("stamp") != stamp:
            raise Refused(f"answer header stamp {h.get('stamp')!r} in a rag_{stamp} file")
        cur = {}
        for c in h.get("configs") or []:
            p = c.get("pairing") or {}
            cur[str(c.get("config_id"))] = {"index": c.get("index"), "match_limit": p.get("match_limit"),
                                            "similarity_threshold": p.get("similarity_threshold")}
        if not cur:
            raise Refused(f"rag_{stamp}: a header lists no configs")
        if out is not None and cur != out:
            raise Refused(f"rag_{stamp}: the headers disagree on the configs or their knobs (resumed "
                          "after the index knobs changed?)")
        out = cur
    return out or {}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_decisions(path, digest):
    """The S10 binding of a decide_dev.py file: {"index", "match_limit", "similarity_threshold"} or None."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        raise Refused(f"{os.path.basename(path)}: cannot read ({e})") from None
    if d.get("questions_digest") != digest:
        raise Refused(f"{os.path.basename(path)}: decided on questions {str(d.get('questions_digest'))[:12]}..., "
                      f"not the current {digest[:12]}...")
    knobs = d.get("s10_knobs") or {}
    if not knobs.get("index") or (d.get("bindings") or {}).get("S10") != knobs.get("index"):
        log.warning("event=no_s10_binding decisions=%s", os.path.basename(path))
        return None
    return knobs


def stage_map(configs, ret_headers, s10, overrides):
    """{config_id: stage} for every answer config; refuses a config without one."""
    by_config = {str(h.get("config_id")): (h.get("experiments_row") or {}).get("stage") for h in ret_headers}
    by_index = {h.get("index"): (h.get("experiments_row") or {}).get("stage") for h in ret_headers}
    unknown = sorted(set(overrides) - set(configs))
    if unknown:
        raise Refused(f"--stage for config(s) {', '.join(unknown)}, which the answer run does not have "
                      f"(it has {', '.join(sorted(configs))})")
    out, missing = {}, []
    for cid, c in sorted(configs.items()):
        stage = overrides.get(cid)
        source = "--stage"
        if not stage and s10 and c["index"] == s10["index"]:
            same = (_num(c["match_limit"]) == _num(s10.get("match_limit"))
                    and _num(c["similarity_threshold"]) is not None
                    and abs(_num(c["similarity_threshold"]) - (_num(s10.get("similarity_threshold")) or -1)) < 1e-9)
            if same:
                stage, source = "S10", "dev decisions S10 knobs"
            else:
                log.warning("event=s10_index_without_s10_knobs config=%s index=%s match_limit=%s threshold=%s",
                            cid, c["index"], c["match_limit"], c["similarity_threshold"])
        if not stage:
            stage, source = by_config.get(cid) or by_index.get(c["index"]), "experiments row"
        if not stage:
            missing.append(cid)
            continue
        out[cid] = stage
        log.info("event=stage config=%s index=%s stage=%s source=%s", cid, c["index"], stage, source)
    if missing:
        raise Refused(f"no stage for answer config(s) {', '.join(missing)}: pass --stage CONFIG=STAGE")
    return out


def answer_rows(ans, configs, stages, questions):
    """(rows, counts) in the make_charts.py answers contract."""
    rows, errs = [], []
    counts = collections.Counter()
    for (idx, qid) in sorted(ans):
        e = ans[(idx, qid)]
        q, cid = e["q"], str(e.get("config_id"))
        if cid not in configs or configs[cid]["index"] != idx:
            errs.append(f"{idx} {qid}: config {cid!r} is not this index in the run header")
            continue
        if qid not in questions:
            errs.append(f"{idx} {qid}: not in questions.json")
            continue
        ref = questions[qid]
        for key in ("split", "answerable", "bucket", "q_lang"):
            if q.get(key) != ref[key]:
                errs.append(f"{idx} {qid}: {key} {q.get(key)!r}, questions.json has {ref[key]!r}")
        if ref["bucket"] in er.RETRIEVAL_ONLY_BUCKETS:
            counts["retrieval_only_left_out"] += 1
            continue
        for run, verdict in sorted(e["runs"].items()):
            if verdict is None:                     # infra_error only: never scored (PLAN.md 5.3)
                counts["infra_error_runs_left_out"] += 1
                continue
            rows.append({"config_id": idx, "stage": stages[cid], "split": ref["split"], "qid": qid,
                         "run": int(run), "answerable": ref["answerable"], "verdict": verdict,
                         "experiments_config": cid})
            counts["rows"] += 1
    if errs:
        raise Refused("answers: " + _errors(errs))
    return rows, counts


# ---------------------------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------------------------
def write_atomic(path, text):
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def jsonl(rows):
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def parse_stage_overrides(items):
    out = {}
    for x in items or []:
        cid, sep, stage = x.partition("=")
        if not sep or not cid.strip() or not re.fullmatch(r"S[0-9]+[A-Za-z0-9_]*", stage.strip()):
            raise Refused(f"--stage {x!r}: expected CONFIG=STAGE, e.g. 12=S10")
        out[cid.strip()] = stage.strip()
    return out


def default_decisions(results):
    files = list_inputs(results, DECISIONS_FILE)
    return files[-1] if files else None            # names sort by their UTC stamp: the latest round


def main(argv=None) -> int:
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Flatten the RAG lab results into make_charts.py inputs")
    ap.add_argument("--results", default=DEFAULT_RESULTS)
    ap.add_argument("--out", default=None, help="default: <results>/charts_input")
    ap.add_argument("--questions", default=er.DEFAULT_QUESTIONS)
    ap.add_argument("--rag-stamp", default="", help="the eval_rag.py run to chart when several are present")
    ap.add_argument("--decisions", default=None, help="decide_dev.py output (default: the latest in results/)")
    ap.add_argument("--stage", action="append", default=[], help="CONFIG=STAGE for an answer config")
    ap.add_argument("--audit", default=None, help=f"human audit verdicts (default: {os.path.basename(st.DEFAULT_AUDIT)} "
                                                   "in results/ if present)")
    ap.add_argument("--no-audit", action="store_true", help="scorer verdicts only")
    ap.add_argument("--expect-runs", type=int, default=3, help="answer runs 1..N that must be present (GO-3: 3)")
    a = ap.parse_args(argv)
    results = a.results
    out = a.out or os.path.join(results, "charts_input")

    try:
        overrides = parse_stage_overrides(a.stage)
        try:
            meta, qs, seal = er.load_questions(a.questions)
        except (OSError, ValueError, er.GuardError) as e:
            raise Refused(f"questions.json: {e}") from None
        if not seal["ok"]:
            raise Refused(f"questions.json is not sealed (digest {seal['digest'][:12]}...)")
        digest = seal["digest"]
        questions = {q["id"]: q for q in qs}

        ret_paths = list_inputs(results, RETRIEVAL_FILE)
        cov_paths = list_inputs(results, COVERAGE_FILE)
        stamp, rag_paths = pick_rag_files(results, a.rag_stamp or None)
        if not (ret_paths or cov_paths or rag_paths):
            log.error("event=no_input results=%s", os.path.basename(os.path.normpath(results)))
            return 1

        ret = load_retrieval(ret_paths, questions, digest) if ret_paths else {}
        ret_headers = [d["header"] for d in ret.values()]
        rag_headers = _stats_call(st.headers, rag_paths) if rag_paths else []
        for h in rag_headers:
            check_seal(h, digest, f"rag_{stamp}")
        # one questions seal across every input, no --only run; answers from split all or test
        _stats_call(st.check_headers, ret_headers + rag_headers)
        if rag_headers:
            _stats_call(st.check_headers, rag_headers, split="test")

        outputs, manifest_inputs = {}, []
        if ret:
            rows, counts = retrieval_rows(ret, questions)
            outputs["retrieval"] = (rows, dict(counts))
            for idx in sorted(ret):
                p = ret[idx]["path"]
                manifest_inputs.append({"kind": "retrieval", "file": os.path.basename(p), "sha256": sha256_file(p),
                                        "index": idx, "config": ret[idx]["header"].get("config_id")})
        else:
            log.warning("event=input_absent kind=retrieval")

        if cov_paths:
            rows, counts, cov_idx = coverage_rows(cov_paths)
            counts = dict(counts, indexes=len(cov_idx))
            outputs["coverage"] = (rows, counts)
            manifest_inputs += [{"kind": "coverage", "file": os.path.basename(p), "sha256": sha256_file(p)}
                                for p in cov_paths]
            if len(cov_idx) < 2:
                log.warning("event=coverage_single_config indexes=%s (chart 35 compares configs)", ",".join(cov_idx))
        else:
            log.warning("event=input_absent kind=coverage")

        if rag_paths:
            configs = rag_configs(rag_headers, stamp)
            if a.expect_runs < 1:
                raise Refused("--expect-runs must be >= 1")
            infra = check_rag_complete(rag_paths, configs, questions, a.expect_runs)
            dec_path = a.decisions or default_decisions(results)
            if not dec_path:
                log.warning("event=no_dev_decisions (no config is labelled S10 unless --stage says so)")
            stages = stage_map(configs, ret_headers, load_decisions(dec_path, digest), overrides)
            audit_path = None if a.no_audit else (a.audit or os.path.join(results, os.path.basename(st.DEFAULT_AUDIT)))
            if audit_path and not os.path.exists(audit_path):
                if a.audit:
                    raise Refused(f"--audit {os.path.basename(a.audit)}: no such file")
                audit_path = None
            audit = _stats_call(st.load_audit, audit_path) if audit_path else None
            ans = _stats_call(st.load_answers, rag_paths, audit)
            rows, counts = answer_rows(ans, configs, stages, questions)
            counts = dict(counts, verdicts="adjudicated" if audit else "scorer", stamp=stamp,
                          infra_rate_max={cid: round(v, 4) for cid, v in sorted(infra.items())})
            outputs["answers"] = (rows, counts)
            manifest_inputs += [{"kind": "answers", "file": os.path.basename(p), "sha256": sha256_file(p)}
                                for p in rag_paths]
            if dec_path:
                manifest_inputs.append({"kind": "decisions", "file": os.path.basename(dec_path),
                                        "sha256": sha256_file(dec_path)})
            if audit_path:
                manifest_inputs.append({"kind": "audit", "file": os.path.basename(audit_path),
                                        "sha256": sha256_file(audit_path)})
        else:
            log.warning("event=input_absent kind=answers (GO-3 not copied yet?): answers.jsonl not written")
    except Refused as e:
        log.error("event=refused %s", e)
        return 2
    except OSError as e:
        log.error("event=io_error file=%s error=%s", os.path.basename(e.filename or ""), e.strerror)
        return 2

    manifest = {"tool": VERSION, "utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "questions_version": meta.get("version"), "questions_digest": digest,
                "inputs": manifest_inputs,
                "outputs": {OUT_FILES[k]: {"rows": len(v[0]), **{c: n for c, n in v[1].items() if c != "rows"}}
                            for k, v in sorted(outputs.items())}}
    try:
        os.makedirs(out, exist_ok=True)
        for kind, name in OUT_FILES.items():
            path = os.path.join(out, name)
            if kind in outputs:
                rows, counts = outputs[kind]
                write_atomic(path, jsonl(rows))
                log.info("event=written file=%s rows=%d %s", name, len(rows),
                         " ".join(f"{k}={v}" for k, v in sorted(counts.items()) if k != "rows"))
            elif os.path.exists(path):
                os.unlink(path)                       # never leave a stale input for make_charts.py
                log.warning("event=stale_removed file=%s", name)
        write_atomic(os.path.join(out, MANIFEST), json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    except OSError as e:
        log.error("event=write_failed file=%s error=%s", os.path.basename(e.filename or ""), e.strerror)
        return 2
    log.info("event=done out=%s files=%d", os.path.basename(os.path.normpath(out)), len(outputs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
