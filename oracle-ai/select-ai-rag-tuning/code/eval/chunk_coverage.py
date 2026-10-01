#!/usr/bin/env python3
# v1.1 - RAG tuning lab: how much of each stored chunk the embedding model actually reads.
#        v1.1: prefixes are cut in SQL (SUBSTR of the stored CLOB) instead of bound as text, so an
#              Arabic chunk over 4,000 bytes cannot hit a bind limit; index content fingerprint.
#        v1.0: first version (PLAN.md 5.2, story 3): binary search over word-boundary prefixes for
#              the shortest prefix whose embedding equals the stored one.
#
# Run as : RAG_LAB (python-oracledb thin), on the host that reaches the PDB. Depends on probe P4b
#          (stored $VECTAB embedding = VECTOR_EMBEDDING(model USING content)); a chunk where that
#          does not hold is reported as stored_matches_content=false and not searched.
# Usage  : RAG_LAB_DSN=localhost:1521/orclpdb1 python3 chunk_coverage.py --config <config_id>
#            [--index RAG_M0_C1024_O128_COS] [--sample N] [--eps 1e-6] [--out ../../results]
#          Default sample: every chunk for ALL_MINILM_L12_V2, 100 chunks for other models
#          (deterministic: lowest sha256(object_name, content)).
# Re-run : safe; read-only; writes a new results/coverage_<INDEX>_<UTC>.jsonl.
# Exit   : 0 ok | 2 precondition/guard failed | 3 stopped (PAUSE file)
#
# Why a binary search is valid: a model that truncates at N tokens gives every prefix that
# reaches past token N the same embedding as the whole chunk, and every shorter prefix a
# different one, so "prefix embedding == stored embedding" is monotone in prefix length.
# Prefix lengths are Python code points; Oracle SUBSTR on a CLOB counts UCS-2 units. They are the
# same for this corpus (Latin and Arabic are in the BMP).
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eval_retrieval as er              # noqa: E402

log = logging.getLogger("chunk_coverage")
VERSION = "chunk_coverage.py v1.1"
FULL_SAMPLE_MODELS = ("ALL_MINILM_L12_V2",)


def word_ends(text: str):
    return [m.end() for m in re.finditer(r"\S+", text or "")]


def prefix_ends(text: str):
    """Candidate prefix lengths: the end of every word, plus the full length."""
    return sorted(set(word_ends(text) + [len(text)])) if text else []


def shortest_equal_prefix(text: str, is_equal):
    """Smallest candidate prefix for which is_equal(prefix) holds; the full text must hold
    (the caller checks it). Returns (prefix_length, predicate_calls)."""
    cands = prefix_ends(text)
    lo, hi, calls = 0, len(cands) - 1, 0
    while lo < hi:
        mid = (lo + hi) // 2
        calls += 1
        if is_equal(text[:cands[mid]]):
            hi = mid
        else:
            lo = mid + 1
    return cands[lo], calls


def sample_chunks(chunks, n):
    if not n or n >= len(chunks):
        return list(chunks)
    key = lambda c: hashlib.sha256((c["obj"] or "").encode() + b"\0" + (c["content"] or "").encode()).hexdigest()
    return sorted(chunks, key=key)[:n]


def coverage_for_chunk(db, p, chunk, eps=1e-6):
    content = chunk["content"] or ""
    if not content.strip():
        return {"skipped": "empty"}
    d_full = db.embedding_distance(p.index, p.owner, p.model, chunk["rid"], len(content))
    if d_full > eps:
        return {"stored_matches_content": False, "d_full": d_full, "chars": len(content)}

    def equal(prefix):
        return db.embedding_distance(p.index, p.owner, p.model, chunk["rid"], len(prefix)) <= eps
    end, calls = shortest_equal_prefix(content, equal)
    last_word_end = word_ends(content)[-1]        # trailing whitespace never counts as unread
    return {"stored_matches_content": True, "d_full": d_full, "chars": len(content),
            "embedded_chars": end, "fraction": round(end / len(content), 4),
            "truncated": end < last_word_end, "calls": calls + 1}


def summarize(recs):
    ok = [r for r in recs if r.get("stored_matches_content")]
    groups = {}
    for r in ok:
        groups.setdefault(r.get("lang") or "?", []).append(r)
        groups.setdefault("ALL", []).append(r)
    out = {"chunks": len(recs), "stored_mismatch": sum(1 for r in recs if r.get("stored_matches_content") is False),
           "by_lang": {}}
    for k, g in sorted(groups.items()):
        tr = [r["embedded_chars"] for r in g if r["truncated"]]
        out["by_lang"][k] = {"n": len(g), "fully_embedded": round(sum(not r["truncated"] for r in g) / len(g), 4),
                             "mean_fraction": round(sum(r["fraction"] for r in g) / len(g), 4),
                             "effective_window_chars_median": statistics.median(tr) if tr else None}
    return out


def main(argv=None) -> int:
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="RAG lab: embedded fraction of each chunk")
    ap.add_argument("--config")
    ap.add_argument("--index")
    ap.add_argument("--rag-profile")
    ap.add_argument("--experiments", default=er.DEFAULT_EXPERIMENTS)
    ap.add_argument("--manifest", default=er.DEFAULT_MANIFEST)
    ap.add_argument("--out", default=er.DEFAULT_OUT)
    ap.add_argument("--sample", type=int, default=-1, help="-1 model default | 0 all | N")
    ap.add_argument("--eps", type=float, default=1e-6, help="max cosine distance counted as equal")
    ap.add_argument("--call-timeout", type=int, default=180)
    ap.add_argument("--pause-file", default=er.DEFAULT_PAUSE)
    ap.add_argument("--stamp", default="")
    a = ap.parse_args(argv)

    db, w = None, None
    try:
        row = er.experiment_row(a.experiments, a.config) if a.config else None
        index = er.lab_index_name((row or {}).get("index_name") or a.index or "")
        rag = a.rag_profile or (row or {}).get("rag_profile") or ("RAG_P_" + index[4:])
        corpus = er.Corpus.from_csv(a.manifest)
        db = er.OracleLabDB(er.connect_from_env(), call_timeout_ms=a.call_timeout * 1000)
        p = er.resolve_pairing(db, index, rag, expected_model=(row or {}).get("model_name"))
        er.check_columns(db.vectab_columns(index))
        stats_now, all_chunks = er.snapshot_index(db, index)
        er.check_stats(stats_now, er.previous_stats(a.out, index), index)
        n = a.sample if a.sample >= 0 else (0 if p.model in FULL_SAMPLE_MODELS else 100)
        chunks = sample_chunks(all_chunks, n)
        started = er.utc_now()
        stamp = a.stamp or er.stamp_of(started)
        os.makedirs(a.out, exist_ok=True)
        path = os.path.join(a.out, f"coverage_{index}_{stamp}.jsonl")
        w = er.JsonlWriter(path)
        w.write({"type": "header", "tool": VERSION, "utc": started.isoformat(), "index": index,
                 "config_id": a.config, "pairing": er.dataclasses.asdict(p),
                 "index_stats": {index: stats_now}, "sample": n, "chunks_measured": len(chunks),
                 "eps": a.eps})
        recs = []
        for i, c in enumerate(chunks, 1):
            if er.pause_requested(a.pause_file):
                raise er.StopRun(f"PAUSE file present: {a.pause_file}")
            doc = corpus.doc_of(c["obj"])
            r = {"type": "chunk", "object_name": c["obj"], "doc_id": doc, "lang": corpus.lang.get(doc),
                 "content_sha256": er.sha(c["content"]), **coverage_for_chunk(db, p, c, a.eps)}
            recs.append(r)
            w.write(r)
            if i % 50 == 0:
                log.info("event=progress index=%s done=%d of=%d", index, i, len(chunks))
        s = summarize(recs)
        w.write({"type": "summary", "utc": er.utc_now().isoformat(), **s})
        sys.stdout.write(er.json.dumps(s, indent=1) + "\n")
        log.info("event=done index=%s out=%s", index, path)
        return 0
    except er.GuardError as e:
        log.error("event=guard_failed %s", e)
        return 2
    except er.StopRun as e:
        log.warning("event=stopped %s", e)
        return 3
    except er.DBCallError as e:
        log.error("event=db_error %s", er.redact(e))
        return 2
    finally:
        if w is not None:
            w.close()
        if db is not None:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
