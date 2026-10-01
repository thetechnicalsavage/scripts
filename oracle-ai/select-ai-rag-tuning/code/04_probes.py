#!/usr/bin/env python3
# v1.3 - Brief 09 (Select AI RAG tuning): the probes of PLAN.md 8.1 that need Python or call
#        the LLM, plus the no-database helpers around them.
#        v1.3: public copy: the other app is named the existing HR app; tools/capture.py (lab-internal) not shipped.
#        v1.2: Codex adversarial review: the RAG_LAB password is read from RAG_LAB_PWD_FILE first (a
#              file of this user's, closed to others, as run_all.sh writes it), then RAG_LAB_PWD, then
#              a prompt; p17 holds an exclusive lock (p17_chat.jsonl.lock) for its whole run, so a
#              second p17 at the same time exits 2 before any call; on resume a JSONL line cut off
#              by a kill is moved to p17_chat.jsonl.torn instead of stopping the resume.
#        v1.1: p2 (P2, DB-vs-local parity of one model on the 20 token-id strings, one text per call,
#              against verify_models.py refvec; appends results/model_parity.csv); p0-status (the P0
#              transcript, or the read-only ad-hoc check of 29-Sep, into results/probes/p0.status, which
#              gates MODELS; a PASS needs one P0 run, one STATUS line and every GATE line passing);
#              p8 stops p1's pipeline after every UPDATE_VECTOR_INDEX, its restore included; p8 and p16
#              find the pipeline by attribute, else by the <index>$VECPIPELINE name (looked up again
#              after an update); model_parity.csv rows are appended under a file lock.
#        v1.0: first version: p4 truncation window (word-boundary binary search), p5 extraction
#              recall and the PDF-or-DOCX decision, p7 runsql/showprompt/narrate shape, p8
#              SELECT AI vs GENERATE cache, p11 GENERATE attribute override and seed, p14 SCORE
#              vs distance, p16 which profile embeds the query, p17 chat prior-knowledge control;
#              files, stage-dir, p0-df, make-noto-control.
#
# Run as : DB probes: RAG_LAB through python-oracledb thin, on the database host, with the lab
#          venv. files, stage-dir, p0-df and make-noto-control need no database. Nothing here
#          writes into the database directories: 01_stage_files.sh is the one write path.
# Usage  : python3 04_probes.py [--corpus-dir DIR] <probe> [options]    (-h lists them)
#            p4  [--models M0,M1,...] [--sample-chars 12000]
#            p5  (after 05_extraction_gate.sql; again after P6 to include p1's stored text)
#            p2 --key M1 [--refvec results/models/M1_refvec.json]   (after 03 for that key; no LLM)
#            p7 | p8 | p11 | p14 | p16                    (short; 6-16 GENERATE calls each)
#            p17 [--runs 3] [--profile RAG_EMB_M0] [--limit N]   (~324 chat calls; resumable)
#            files | stage-dir --stage initial|p13 --dir DIR | p0-df --path DIR
#            p0-status --transcript FILE                  (04_probes.sql P0 transcript -> p0.status)
#            p0-status --transcript FILE --adhoc --run-utc YYYY-MM-DDTHH:MM:SSZ   (29-Sep read-only check)
#            make-noto-control [--out FILE]
#          (the lab ran it through tools/capture.py, a lab-internal transcript wrapper not shipped here)
# Env    : RAG_LAB_DSN (default localhost:1521/orclpdb1). The RAG_LAB password: RAG_LAB_PWD_FILE
#          (path of a regular file owned by this user, mode 600 or tighter, one line), else
#          RAG_LAB_PWD, else a hidden prompt. Both variables are removed from the environment once
#          read; the password is never logged or written.
# Re-run : safe. Read-only probes change nothing. p8 and p16 change p1's match_limit or
#          profile_name and restore them in a finally block, and stop p1's pipeline after each
#          change (UPDATE_VECTOR_INDEX can restart it). p2 appends one row per run to
#          results/model_parity.csv (the last row for a key decides); p0-status rewrites p0.status.
#          p11 and p16 create scratch profiles RAG_P_PRB_P1_SEED / RAG_P_PRB_P1_XM1 (names checked,
#          dropped and recreated on re-run).
#          p17 appends to results/probes/p17_chat.jsonl and skips (run, question) pairs already
#          answered, so a stopped run resumes. It holds an exclusive flock on
#          p17_chat.jsonl.lock for the whole run: a second p17 started meanwhile exits 2 at once.
#          A hard kill (SIGKILL, power loss) between a GENERATE call's return and its JSONL line
#          repeats at most that one in-flight paid call on resume (a line the kill cut off is moved
#          to p17_chat.jsonl.torn); PAUSE and the infra window stop between calls and repeat none.
#          stage-dir is idempotent (same bytes = kept).
# Exit   : 0 done (a probe's finding is output, never a failure) | 1 unexpected database error |
#          2 precondition or guard failed | 3 P5 decision STOP (DOCX failed: ask the operator) |
#          4 stopped by the PAUSE file or the infra-error window (p17 resumes) |
#          5 a gate failed and was recorded (p2 fail or error in model_parity.csv, p0-status FAIL)
# Output : transcript-ready text on stdout, and results/probes/<probe>.txt / .json. Every line
#          passes redact() first: OCIDs, OCI URLs and regions, request ids, IPs, e-mail
#          addresses, /home paths, key fingerprints, PEM headers and 12-hex ids are masked.
from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import fcntl
import getpass
import hashlib
import ipaddress
import json
import logging
import math
import os
import random
import re
import shutil
import stat
import sys
import tempfile
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))             # .../code
ROOT = os.path.abspath(os.path.join(HERE, ".."))                # the brief folder
sys.path.insert(0, os.path.join(HERE, "tools"))
import build_corpus as bc  # noqa: E402  the corpus normaliser: P5 must score with exactly this one

log = logging.getLogger("probes")
VERSION = "04_probes.py v1.3"

PROBE_FILES = os.path.join(HERE, "probe_files.txt")
DEFAULT_OUT = os.path.join(ROOT, "results", "probes")
DEFAULT_RESULTS = os.path.join(ROOT, "results")                 # run_all.sh's RESULTS_DIR default
DEFAULT_PAUSE = os.path.join(HERE, "PAUSE")                     # same file eval_retrieval.py honours
DEFAULT_QUESTIONS = os.path.join(HERE, "eval", "questions.json")
DEFAULT_NOTO_OUT = os.path.join(ROOT, "corpus", "probe", "PRB-AR-NOTO-GLF-001-AR.pdf")
CORPUS_DIR = os.path.join(ROOT, "corpus")        # --corpus-dir overrides it (a copy on the database host)
DEFAULT_DSN = "localhost:1521/orclpdb1"

LAB_USER = "RAG_LAB"
MODEL_OWNER = "ASKORACLE"
MODELS = {"M0": "ALL_MINILM_L12_V2", "M1": "MULTILINGUAL_E5_SMALL", "M2": "MULTILINGUAL_E5_BASE",
          "M3": "MULTILINGUAL_E5_LARGE", "M4": "BGE_M3", "M5": "ARCTIC_EMBED_L_V2",
          "M6": "ARABIC_TRIPLET_V2", "M1Q": "MULTILINGUAL_E5_SMALL_Q"}
PROBE_DIR = "RAG_PROBE_DIR"
P1, P2, P4 = "RAG_PRB_P1", "RAG_PRB_P2", "RAG_PRB_P4"
PROBE_INDEX = re.compile(r"^RAG_PRB_(P[1-4]|X1)$")
SCRATCH_PROFILE = re.compile(r"^RAG_P_PRB_P1_(XM1|SEED)$")
SIMPLE = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")
DSN_RX = re.compile(r"^[A-Za-z0-9._-]+(:\d{1,5})?/[A-Za-z0-9._$-]+$")
SELECT_AI_TEXT = re.compile(r"^[A-Za-z0-9 ,.()?-]{1,300}$")      # spliced into SELECT AI: no quotes
MIN_RECALL = 0.95                                                # PLAN.md 8.1 P5
CHUNK_SIZES = (640, 1024, 1536, 2000)

# Fixed probe prompts. They are about the probe files only and are not gold questions, so no
# probe touches the sealed gold set except p17 (whose whole purpose is the gold set).
PROBE_QUESTIONS = (
    {"id": "PQ1", "lang": "en", "doc": "GLF-001-EN",
     "text": "How many days of annual leave can an employee at the Doha branch carry forward into the next leave year?"},
    {"id": "PQ2", "lang": "en", "doc": "HRP-001",
     "text": "What is the carry forward cap for Earned Leave in India?"},
    {"id": "PQ3", "lang": "ar", "doc": "GLF-001-AR",
     "text": "كم يوماً من الإجازة السنوية يمكن لموظف فرع الدوحة ترحيلها إلى السنة التالية؟"},
    {"id": "PQ4", "lang": "ar", "doc": "GLF-001-AR",
     "text": "متى يستطيع الموظف في فرع الرياض استخدام إجازته السنوية المتراكمة؟"},
)


class GuardError(Exception):
    """A precondition or safety check failed (exit 2)."""


class DBError(Exception):
    """A database call failed. The message is the driver's; redact before showing it."""


class StopRun(Exception):
    """PAUSE file or infra-error window (exit 4)."""


# =============================================================================================
# redaction: every printed or saved line passes through redact()
# =============================================================================================
def _extra_literals():
    """Host names and addresses from the git-ignored leak pattern file (literal, case-insensitive)."""
    path = os.environ.get("BLOG_LEAK_PATTERNS") or os.path.expanduser("~/.config/blog-leak-patterns.txt")
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#") and len(s) >= 3:
                    out.append(re.compile(re.escape(s), re.I))
    except FileNotFoundError:
        pass
    return out


_EXTRA = None
_SECRETS = []
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\.?\d)")
_IPV6 = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}(?:[0-9A-Fa-f]{1,4}|\d{1,3}(?:\.\d{1,3}){3})?(?![\w:])")
_RULES = (
    (re.compile(r"(?:https?|file)://\S*oraclecloud\.com\S*", re.I), "<oci-url>"),
    (re.compile(r"\S*\.oci\.oraclecloud\.com\S*", re.I), "<oci-host>"),
    (re.compile(r"ocid1\.[\w.\-]+", re.I), "<ocid>"),
    (re.compile(r"opc[-_]request[-_]id[^\s:=]*(?:\s*[:=]?\s*\S+)?", re.I), "<request-id>"),
    (re.compile(r"-{5}BEGIN[^\n]*"), "<pem>"),                  # written so this file holds no PEM marker
    (re.compile(r"(?:[0-9a-f]{2}:){15}[0-9a-f]{2}", re.I), "<fingerprint>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
                r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"/home/[A-Za-z0-9_.-]+"), "/home/<user>"),
    (re.compile(r"\b[a-z]+-[a-z]+-[0-9]+\b"), "<region>"),
    (re.compile(r"\b(?=[0-9]*[a-f])[0-9a-f]{12}\b"), "<hex12>"),
)
REDACTIONS = collections.Counter()


def _ipv4_sub(m):
    if all(0 <= int(x) <= 255 for x in m.groups()):
        REDACTIONS["ip"] += 1
        return "<ip>"
    return m.group(0)


def _ipv6_sub(m):
    s = m.group(0)
    if s.count(":") >= 2 and ("::" in s or re.search(r"[A-Fa-f]", s)):
        try:
            ipaddress.IPv6Address(s)
            REDACTIONS["ip"] += 1
            return "<ip>"
        except ValueError:
            return s
    return s


def register_secret(value: str, label: str):
    """Mask one more literal (the DSN host, the password) in everything redact() touches."""
    global _EXTRA
    if _EXTRA is None:
        _EXTRA = _extra_literals()
    if value and len(value) >= 3 and value.lower() != "localhost":
        _SECRETS.append((re.compile(re.escape(value), re.I), label))


def redact(text) -> str:
    """Mask what must never reach a transcript. Idempotent: redact(redact(x)) == redact(x)."""
    global _EXTRA
    if _EXTRA is None:
        _EXTRA = _extra_literals()
    s = "" if text is None else str(text)
    for rx, label in _SECRETS:
        s, n = rx.subn(label, s)
        REDACTIONS[label] += n
    for rx in _EXTRA:
        s, n = rx.subn("<host>", s)
        REDACTIONS["host"] += n
    for rx, rep in _RULES:
        s, n = rx.subn(rep, s)
        REDACTIONS[rep] += n
    s = _IPV4.sub(_ipv4_sub, s)
    s = _IPV6.sub(_ipv6_sub, s)
    return s


def redact_data(x):
    if isinstance(x, str):
        return redact(x)
    if isinstance(x, dict):
        return {redact(k) if isinstance(k, str) else k: redact_data(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [redact_data(v) for v in x]
    return x


def short_error(msg) -> str:
    """First line of a driver error, plus a PLS- line when there is one, redacted and capped."""
    lines = [ln.strip() for ln in str(msg or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    keep = [lines[0]] + [ln for ln in lines[1:] if "PLS-" in ln][:1]
    return redact(" | ".join(keep))[:400]


def mark_lost(s: str) -> str:
    """How extracted text is shown: '?', U+FFFD and U+00BF spelled out, so capture.py's
    lost-character gate cannot mistake a finding for a broken NLS_LANG."""
    return (s or "").replace("?", "<?>").replace("�", "<U+FFFD>").replace("¿", "<U+00BF>")


def sha16(text) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Out:
    """Transcript writer: prints redacted lines and keeps them for results/probes/<probe>.txt."""

    def __init__(self, probe: str, out_dir: str | None):
        self.probe, self.out_dir, self.lines = probe, out_dir, []

    def __call__(self, s=""):
        for line in str(s).split("\n"):
            line = redact(line)
            sys.stdout.write(line + "\n")
            self.lines.append(line)
        sys.stdout.flush()

    def block(self, text: str, max_lines: int, indent: str = "   | "):
        lines = (text or "").split("\n")
        for ln in lines[:max_lines]:
            self(indent + ln)
        if len(lines) > max_lines:
            self(f"{indent}... ({len(lines) - max_lines} more lines; full text in results/probes)")

    def finish(self, data: dict):
        if not self.out_dir:
            return
        os.makedirs(self.out_dir, exist_ok=True)
        data = dict(data, probe=self.probe, version=VERSION, utc=utc_stamp(),
                    redactions=dict(REDACTIONS))
        write_atomic(os.path.join(self.out_dir, self.probe + ".json"),
                     json.dumps(redact_data(data), ensure_ascii=False, indent=1))
        write_atomic(os.path.join(self.out_dir, self.probe + ".txt"), "\n".join(self.lines) + "\n")

    def save_text(self, name: str, text: str):
        if not self.out_dir:
            return
        d = os.path.join(self.out_dir, self.probe)
        os.makedirs(d, exist_ok=True)
        write_atomic(os.path.join(d, name), redact(text))


def write_atomic(path: str, text: str):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".probe_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# =============================================================================================
# probe_files.txt
# =============================================================================================
PF_COLUMNS = ("probe_name", "stage", "role", "lang", "format", "origin", "made_by", "source_text",
              "doc_id", "required", "what")
PF_ROLES = ("gate-ar-pdf", "gate-docx", "gate-en-pdf", "reference", "negative-control", "p13-ascii",
            "p13-multibyte")
ASCII_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


@dataclasses.dataclass(frozen=True)
class ProbeFile:
    probe_name: str
    stage: str
    role: str
    lang: str
    format: str
    origin: str
    made_by: str
    source_text: str
    doc_id: str
    required: str
    what: str


def _relpath_ok(p: str) -> bool:
    return bool(p) and not os.path.isabs(p) and ".." not in p.replace("\\", "/").split("/")


def load_probe_files(path: str = PROBE_FILES) -> list:
    rows, seen = [], set()
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            s = line.rstrip("\n")
            if not s.strip() or s.lstrip().startswith("#"):
                continue
            parts = [x.strip() for x in s.split("|")]
            if len(parts) != len(PF_COLUMNS):
                raise GuardError(f"probe_files.txt:{n}: {len(parts)} columns, expected {len(PF_COLUMNS)}")
            r = ProbeFile(*parts)
            name = unicodedata.normalize("NFC", r.probe_name)
            if name != r.probe_name or "/" in name or "\\" in name or name.startswith(".") or "\x00" in name:
                raise GuardError(f"probe_files.txt:{n}: bad file name")
            if r.stage not in ("initial", "p13"):
                raise GuardError(f"probe_files.txt:{n}: stage must be initial or p13")
            if r.stage == "initial" and not ASCII_NAME.match(name):
                raise GuardError(f"probe_files.txt:{n}: staged probe files need ASCII names")
            if r.role not in PF_ROLES or r.lang not in ("en", "ar") or r.format not in ("pdf", "docx"):
                raise GuardError(f"probe_files.txt:{n}: bad role, lang or format")
            if not name.lower().endswith("." + r.format):
                raise GuardError(f"probe_files.txt:{n}: the extension does not match the format")
            if r.required not in ("yes", "no"):
                raise GuardError(f"probe_files.txt:{n}: required must be yes or no")
            if not _relpath_ok(r.source_text):
                raise GuardError(f"probe_files.txt:{n}: source_text must be a relative path")
            if not (r.origin.startswith("probe:") or _relpath_ok(r.origin)):
                raise GuardError(f"probe_files.txt:{n}: origin must be a relative path or probe:<name>")
            if name in seen:
                raise GuardError(f"probe_files.txt:{n}: duplicate {name}")
            seen.add(name)
            rows.append(r)
    initial = {r.probe_name for r in rows if r.stage == "initial"}
    for r in rows:
        if r.origin.startswith("probe:") and r.origin[6:] not in initial:
            raise GuardError(f"probe_files.txt: {r.probe_name} copies unknown probe file {r.origin[6:]}")
    roles = [r.role for r in rows]
    for role in PF_ROLES:
        if roles.count(role) != 1:
            raise GuardError(f"probe_files.txt: role {role} appears {roles.count(role)} times, expected once")
    return rows


def brief_path(rel: str) -> str:
    """A probe_files.txt path: corpus/... resolves under CORPUS_DIR, anything else under the brief."""
    if rel.startswith("corpus/"):
        return os.path.join(CORPUS_DIR, rel[len("corpus/"):])
    return os.path.join(ROOT, rel)


def source_plain_text(rel: str) -> str:
    """The words a reader of the document sees (build_corpus.plain_text of its source)."""
    meta, blocks = bc.parse_source(brief_path(rel))
    return bc.plain_text(meta, blocks)


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# =============================================================================================
# P4: the effective window of a model, by binary search over word-boundary prefixes
# =============================================================================================
def cosine_distance(a, b) -> float:
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)


def prefix(words, k: int) -> str:
    return " ".join(words[:k])


def cap_words_to_bytes(words, max_bytes: int) -> list:
    """Longest word prefix whose ' '-joined UTF-8 size fits max_bytes (VARCHAR2 limits are bytes)."""
    out, total = [], 0
    for i, w in enumerate(words):
        add = len(w.encode("utf-8")) + (1 if i else 0)
        if total + add > max_bytes:
            break
        out.append(w)
        total += add
    return out


def find_window(n: int, same) -> int:
    """Smallest k in 1..n with same(k) true. same(n) is true by definition (the whole text) and
    same is monotone: once the prefix reaches the model's token cap, every longer prefix is cut
    to the same tokens and embeds identically."""
    if n < 1:
        raise ValueError("empty sample")
    lo, hi = 1, n
    while lo < hi:
        mid = (lo + hi) // 2
        if same(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


def measure_window(words, embed, eps: float = 1e-6) -> dict:
    """embed(text) -> vector. Returns the shortest prefix whose embedding equals the whole
    sample's; when that is the whole sample, the model read everything (no truncation seen)."""
    n = len(words)
    if n == 0:
        raise ValueError("empty sample")
    calls = [0]

    def emb(k):
        calls[0] += 1
        return embed(prefix(words, k))

    full = emb(n)
    cache = {n: 0.0}

    def dist(k):
        if k not in cache:
            cache[k] = cosine_distance(emb(k), full)
        return cache[k]

    k = find_window(n, lambda j: dist(j) <= eps)
    checks = sorted({j for j in (k + 1, k + max(1, (n - k) // 2)) if k < j < n})
    monotone_ok = all(dist(j) <= eps for j in checks)
    text_k = prefix(words, k)
    return {"words": n, "k": k, "window_chars": len(text_k), "window_bytes": len(text_k.encode("utf-8")),
            "sample_chars": len(prefix(words, n)), "truncated": k < n,
            "d_below": dist(k - 1) if k > 1 else None, "d_at": dist(k), "monotone_checks": checks,
            "monotone_ok": monotone_ok, "degenerate": k == 1 and n > 1, "calls": calls[0]}


def coverage(result: dict, chunk_sizes=CHUNK_SIZES) -> list:
    """Share of a chunk of each size that the model reads. A lower bound when no truncation was
    seen inside the sample."""
    out = []
    for cs in chunk_sizes:
        if result["truncated"]:
            out.append({"chunk_size": cs, "embedded": min(1.0, result["window_chars"] / cs), "lower_bound": False})
        else:
            out.append({"chunk_size": cs, "embedded": min(1.0, result["sample_chars"] / cs), "lower_bound": True})
    return out


def sample_words(lang: str, min_chars: int = 12000, src_dir: str | None = None) -> list:
    """Deterministic sample: the Gulf sources of one language in file-name order, as a reader
    sees them (build_corpus.plain_text), until min_chars characters."""
    src_dir = src_dir or os.path.join(CORPUS_DIR, "src", "gulf")
    tag = "-AR-" if lang == "ar" else "-EN-"
    words, total = [], 0
    for f in sorted(x for x in os.listdir(src_dir) if x.endswith(".txt") and tag in x):
        meta, blocks = bc.parse_source(os.path.join(src_dir, f))
        for w in bc.plain_text(meta, blocks).split():
            words.append(w)
            total += len(w) + 1
            if total >= min_chars:
                return words
    return words


# =============================================================================================
# P5: extraction metrics with the build_corpus.py normaliser, and the PDF-or-DOCX decision
# =============================================================================================
ARABIC_LETTER = re.compile("[ء-غف-ي]")
PRESENTATION = re.compile("[ﭐ-﷿ﹰ-﻿]")
SUSPICIOUS_Q = re.compile(r"\?{2,}|(?<=\w)\?(?=\w)|(?:(?<=\s)|^)\?(?=\w)", re.M)
TATWEEL = "ـ"


def extraction_metrics(source: str, extracted: str, strict_q: bool = True) -> dict:
    """source: build_corpus.plain_text of the document; extracted: what the database produced.
    Word recall is build_corpus.word_integrity, so it matches the local gate in manifest.csv.
    strict_q: any '?' beyond the source's own counts (UTL_TO_TEXT: no overlap). For index
    chunks, which repeat text across overlaps, only '?' in place of letters counts."""
    extracted = extracted or ""
    src_words = bc.WORD.findall(bc.norm(source))
    ext_set = set(bc.WORD.findall(bc.norm(extracted)))
    distinct = list(dict.fromkeys(src_words))
    missing = [w for w in distinct if w not in ext_set]
    nums = [w for w in distinct if w.isdigit() and len(w) >= 2 and w != w[::-1]]
    rev_nums = [x for x in nums if x not in ext_set and x[::-1] in ext_set]
    ar = [w for w in distinct if ARABIC_LETTER.match(w) and len(w) >= 3 and w != w[::-1]]
    rev_words = [w for w in ar if w not in ext_set and w[::-1] in ext_set]
    tat_src = {t for t in source.split() if TATWEEL in t}
    tat_ext = {t for t in extracted.split() if TATWEEL in t}
    q_raw, q_src = extracted.count("?"), source.count("?")
    q_susp = len(SUSPICIOUS_Q.findall(extracted))
    return {
        "chars": len(extracted),
        "words_source": len(src_words),
        "recall": round(bc.word_integrity(source, extracted), 4),
        "missing_distinct": len(missing),
        "missing_examples": missing[:8],
        "reversed_numbers": rev_nums,
        "reversed_words": len(rev_words),
        "reversed_word_examples": rev_words[:5],
        "q_raw": q_raw,
        "q_source": q_src,
        "q_suspicious": q_susp,
        "q_added": max(0, q_raw - q_src) if strict_q else q_susp,
        "fffd": extracted.count("�"),
        "inverted_q": extracted.count("¿"),
        "tatweel": extracted.count(TATWEEL),
        "tatweel_source": source.count(TATWEEL),
        "tatweel_words_added": len(tat_ext - tat_src),
        "presentation_forms": len(PRESENTATION.findall(extracted)),
        "arabic_letters": len(ARABIC_LETTER.findall(extracted)),
    }


def gate_verdict(m: dict, min_recall: float = MIN_RECALL):
    """PLAN.md 8.1 P5: word recall >= 0.95, 0 reversed numbers, 0 '?', 0 U+0640."""
    reasons = []
    if m["recall"] < min_recall:
        reasons.append(f"word recall {m['recall']:.3f} < {min_recall:.2f}")
    if m["reversed_numbers"]:
        reasons.append(f"{len(m['reversed_numbers'])} reversed number(s), e.g. {', '.join(m['reversed_numbers'][:3])}")
    if m["q_added"] or m["q_suspicious"]:
        reasons.append(f"{max(m['q_added'], m['q_suspicious'])} '?' not in the source")
    if m["fffd"] or m["inverted_q"]:
        reasons.append(f"{m['fffd'] + m['inverted_q']} replacement character(s) U+FFFD/U+00BF")
    if m["tatweel_words_added"]:
        reasons.append(f"{m['tatweel_words_added']} word(s) with an added U+0640")
    return (not reasons), reasons


def decide_format(results: dict) -> dict:
    """results: role -> {"pass": bool | None (not measured), "reasons": [...]}.
    PDF only when the Arabic PDF and the English Chromium PDF both pass; otherwise every Gulf
    document goes in as DOCX; a DOCX that fails stops the run for the operator."""
    reasons, warnings = [], []
    dx, ar, en = results.get("gate-docx"), results.get("gate-ar-pdf"), results.get("gate-en-pdf")
    ref, neg = results.get("reference"), results.get("negative-control")
    if ref is not None and ref.get("pass") is False:
        warnings.append("the India reportlab PDF failed too: India cannot change format (S2 reproduces the existing HR app); report it")
    if neg is not None and neg.get("pass") is True:
        warnings.append("the Noto negative control PASSED: this gate may not tell good from bad extraction here")
    if neg is None or neg.get("pass") is None:
        warnings.append("negative control not measured (optional)")
    if dx is None or dx.get("pass") is not True:
        why = "not measured" if dx is None or dx.get("pass") is None else "; ".join(dx.get("reasons", []))
        return {"decision": "STOP", "stop": True, "corpus_format": None,
                "reasons": [f"the DOCX did not pass ({why}): stop, report, decide with the operator"],
                "warnings": warnings}
    for label, r in (("Arabic Amiri PDF", ar), ("English Chromium PDF", en)):
        if r is None or r.get("pass") is None:
            return {"decision": "STOP", "stop": True, "corpus_format": None,
                    "reasons": [f"the {label} was not measured: stage it and re-run p5"], "warnings": warnings}
        if not r["pass"]:
            reasons.append(f"{label} failed: " + "; ".join(r.get("reasons", [])))
    fmt = "docx" if reasons else "pdf"
    if not reasons:
        reasons.append("the Arabic Amiri PDF and the English Chromium PDF both pass")
    return {"decision": fmt, "stop": False, "corpus_format": fmt, "reasons": reasons, "warnings": warnings}


# =============================================================================================
# runsql rows, chunk matching and SCORE transforms (P14), chunk detection in prompts (P8/P11/P16)
# =============================================================================================
SCORE_KEYS = ("score", "similarity", "similarity_score", "vector_score", "distance", "vector_distance")
CONTENT_KEYS = ("content", "text", "chunk", "chunk_text", "page_content", "document", "data")
SOURCE_KEYS = ("location", "source", "object_name", "file_name", "filename", "file", "url", "document_name")


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _json_candidates(text: str):
    t = (text or "").strip()
    if not t:
        return
    try:
        yield json.loads(t)
        return
    except ValueError:
        pass
    items = []
    for line in t.splitlines():
        s = line.strip().rstrip(",")
        if s.startswith(("{", "[")):
            try:
                items.append(json.loads(s))
            except ValueError:
                pass
    if items:
        yield items


def _dict_rows(obj) -> list:
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            return obj
        out = []
        for x in obj:
            out.extend(_dict_rows(x))
        return out
    if isinstance(obj, dict):
        low = {str(k).lower(): v for k, v in obj.items()}
        for k in ("rows", "data", "results", "items", "documents", "chunks", "hits", "matches"):
            if isinstance(low.get(k), list):
                return _dict_rows(low[k])
        if any(k in low for k in SCORE_KEYS + CONTENT_KEYS):
            return [obj]
        for v in obj.values():
            if isinstance(v, (list, dict)):
                r = _dict_rows(v)
                if r:
                    return r
    return []


def parse_runsql_rows(text: str) -> list:
    """Rows out of a runsql reply, whatever JSON shape it has: score, content, source."""
    for cand in _json_candidates(text):
        out = []
        for r in _dict_rows(cand):
            low = {str(k).lower(): v for k, v in r.items()}
            attrs = low.get("attributes")
            if isinstance(attrs, str):
                try:
                    attrs = json.loads(attrs)
                except ValueError:
                    attrs = None
            skey = next((k for k in SCORE_KEYS if k in low and _num(low[k]) is not None), None)
            ckey = next((k for k in CONTENT_KEYS if isinstance(low.get(k), str)), None)
            src = next((str(low[k]) for k in SOURCE_KEYS if low.get(k)), None)
            if src is None and isinstance(attrs, dict):
                src = attrs.get("object_name")
            out.append({"score": _num(low[skey]) if skey else None, "score_key": skey,
                        "content": low.get(ckey) if ckey else None, "source": src, "keys": sorted(low)})
        if out:
            return out
    return []


def text_key(s: str) -> str:
    """Letters and digits only, after the build_corpus normaliser: line breaks, table pipes and
    punctuation differ between a chunk and the same text inside a prompt."""
    return "".join(ch for ch in bc.norm(s or "").casefold() if unicodedata.category(ch)[0] in "LN")


def flatten_json_text(text: str) -> str:
    """A JSON reply (escaped Unicode, \\n) turned into its string values, else the text itself."""
    t = (text or "").strip()
    if t[:1] in ("{", "["):
        try:
            obj = json.loads(t)
        except ValueError:
            return text
        parts = []

        def walk(x):
            if isinstance(x, str):
                parts.append(x)
            elif isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(obj)
        return "\n".join(parts)
    return text


def chunks_in_text(chunks, text: str, probe: int = 80, need_both: bool = True) -> set:
    """rids of the chunks whose first and last `probe` key characters both occur in text."""
    hay = text_key(flatten_json_text(text))
    found = set()
    for c in chunks:
        k = text_key(c["content"])
        if len(k) < 20:
            continue
        head, tail = k[:probe], k[-probe:]
        hit = (head in hay and tail in hay) if need_both else (head in hay or tail in hay)
        if hit:
            found.add(c["rid"])
    return found


def match_row_to_chunk(row: dict, chunks) -> str | None:
    """rid of the one chunk a runsql row carries (exact key, else unique containment)."""
    if not row.get("content"):
        return None
    rk = text_key(row["content"])
    if len(rk) < 20:
        return None
    pool = chunks
    if row.get("source"):
        base = os.path.basename(str(row["source"]).split(":")[-1])
        same = [c for c in chunks if c.get("obj") == base]
        pool = same or chunks
    exact = [c["rid"] for c in pool if text_key(c["content"]) == rk]
    if len(exact) == 1:
        return exact[0]
    part = [c["rid"] for c in pool if rk[:120] in text_key(c["content"]) or text_key(c["content"])[:120] in rk]
    return part[0] if len(part) == 1 else None


TRANSFORMS = (
    ("1 - cosine_distance", lambda c, e: 1 - c),
    ("cosine_distance", lambda c, e: c),
    ("1 - cosine_distance/2", lambda c, e: 1 - c / 2),
    ("1/(1 + cosine_distance)", lambda c, e: 1 / (1 + c)),
    ("1 - euclidean", lambda c, e: 1 - e),
    ("euclidean", lambda c, e: e),
    ("1/(1 + euclidean)", lambda c, e: 1 / (1 + e)),
    ("1 - euclidean^2/2", lambda c, e: 1 - e * e / 2),
    ("1 - euclidean/2", lambda c, e: 1 - e / 2),
)


def fit_score_transforms(pairs, tol: float = 1e-3) -> list:
    """pairs: (score, cosine_distance, euclidean_distance). Which formula turns the harness
    distance into Select AI's SCORE, over every matched row."""
    res = []
    for name, f in TRANSFORMS:
        errs = [abs(s - f(c, e)) for s, c, e in pairs]
        worst = max(errs) if errs else None
        res.append({"transform": name, "max_abs_err": worst, "fits": worst is not None and worst <= tol})
    return sorted(res, key=lambda r: (r["max_abs_err"] is None, r["max_abs_err"] or 0.0))


# =============================================================================================
# profile JSON (P11, P16) and the host directory guard (P13, P15)
# =============================================================================================
COPY_ATTRS = ("provider", "credential_name", "region", "oci_compartment_id", "model", "embedding_model",
              "vector_index_name", "conversation", "temperature", "max_tokens", "seed")
NUMERIC_ATTRS = {"temperature", "max_tokens", "seed"}
BOOL_ATTRS = {"conversation"}
REQUIRED_ATTRS = ("provider", "credential_name", "region", "oci_compartment_id", "model", "embedding_model")


def profile_json(attrs: dict, overrides: dict) -> str:
    """A copy of a RAG profile's attributes with overrides, typed as CREATE_PROFILE expects.
    Only COPY_ATTRS are carried. The result holds the compartment: never print it."""
    d = {}
    for k in COPY_ATTRS:
        v = overrides[k] if k in overrides else attrs.get(k)
        if v is None:
            continue
        if k in NUMERIC_ATTRS:
            sv = str(v).strip()
            d[k] = int(sv) if re.fullmatch(r"-?[0-9]+", sv) else float(sv)
        elif k in BOOL_ATTRS:
            d[k] = v if isinstance(v, bool) else str(v).strip().lower() == "true"
        else:
            d[k] = str(v)
    missing = [k for k in REQUIRED_ATTRS if not d.get(k)]
    if missing:
        raise GuardError("the source profile lacks: " + ", ".join(missing))
    return json.dumps(d)


def check_stage_dir(path: str) -> str:
    """The local directory stage-dir fills for 01_stage_files.sh: absolute, no symlink on the way,
    not inside the corpus, this code folder or any kb/ tree (the database directories are written
    by 01_stage_files.sh only, with its own guards)."""
    if not path or not os.path.isabs(path):
        raise GuardError("--dir must be an absolute path")
    real = os.path.realpath(path)
    if real != os.path.normpath(path):
        raise GuardError("--dir must not be, or pass through, a symlink")
    if "kb" in real.strip("/").split("/"):
        raise GuardError("--dir must not be inside a kb/ tree: 01_stage_files.sh writes the database directories")
    for base in (CORPUS_DIR, HERE):
        b = os.path.realpath(base)
        if os.path.commonpath([real, b]) == b:
            raise GuardError("--dir must not be inside the corpus or the code folder")
    if os.path.exists(real) and not os.path.isdir(real):
        raise GuardError("--dir exists and is not a directory")
    return real


# =============================================================================================
# P2: DB-vs-local parity of one model (pure parts; the database side is cmd_p2)
# =============================================================================================
UTC_RX = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
PARITY_MIN_COSINE = 0.9999                     # PLAN.md section 3 (load) and 8.1 P2
PARITY_N = 20
# verify_models.strings_sha256 of its TOKEN_STRINGS (the 20 token-id strings); tests/test_probes.py
# recomputes it from verify_models.py, so the two files cannot drift apart unnoticed
PARITY_STRINGS_SHA256 = "1bf3191756d9a7ce91439099057ee8db74b0ddf20cbc3ab6c0a753d6d9afd684"
PARITY_CSV_HEADER = ("model_key", "status", "min_cosine", "utc")      # run_all.sh reads model_key, status
PARITY_REFERENCE = {"M0": "Oracle's all_MiniLM_L12_v2.onnx", "M1": "Oracle's multilingual_e5_small.onnx"}


def _strings_sha256(texts) -> str:
    return hashlib.sha256(json.dumps(list(texts), ensure_ascii=False).encode("utf-8")).hexdigest()


def load_refvec(path: str, key: str) -> dict:
    """verify_models.py refvec output for `key`, checked before any database call."""
    name = os.path.basename(path)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        raise GuardError(f"cannot read the reference vectors {name}: {type(e).__name__}") from e
    if not isinstance(doc, dict) or doc.get("kind") != "refvec":
        raise GuardError(f"{name} is not a verify_models.py refvec file")
    if doc.get("key") != key:
        raise GuardError(f"{name} holds the reference of {doc.get('key')!r}, not {key}")
    items = doc.get("items")
    if not isinstance(items, list) or len(items) != PARITY_N or doc.get("n") != PARITY_N:
        raise GuardError(f"{name}: expected {PARITY_N} strings")
    texts = [it.get("text") for it in items]
    if not all(isinstance(t, str) and t for t in texts):
        raise GuardError(f"{name}: a string is missing")
    got = _strings_sha256(texts)
    if got != doc.get("strings_sha256") or got != PARITY_STRINGS_SHA256:
        raise GuardError(f"{name}: the strings are not verify_models.py's 20 token-id strings")
    if doc.get("one_text_per_call") is not True:
        raise GuardError(f"{name}: the reference was not computed one text per call")
    dim, cap = doc.get("dim"), doc.get("cap_bytes")
    if not isinstance(dim, int) or dim < 1 or not isinstance(cap, int) or cap < 1:
        raise GuardError(f"{name}: dim or cap_bytes missing")
    for it in items:
        if [it.get("i"), len(it.get("vector") or [])] != [items.index(it) + 1, dim]:
            raise GuardError(f"{name}: string {it.get('i')} has no {dim}-dimension vector")
        if it.get("bytes") != len(it["text"].encode("utf-8")):
            raise GuardError(f"{name}: string {it['i']} byte count does not match its text")
        cp = it.get("capped")
        if it["bytes"] > cap and not (isinstance(cp, dict) and isinstance(cp.get("text"), str)
                                      and it["text"].startswith(cp["text"])
                                      and len(cp["text"].encode("utf-8")) <= cap
                                      and len(cp.get("vector") or []) == dim):
            raise GuardError(f"{name}: string {it['i']} is over {cap} bytes and has no capped reference")
    return doc


def parity_compare(embed, doc: dict, threshold: float = PARITY_MIN_COSINE) -> dict:
    """embed(text) -> the database's vector, one call per text. A text over cap_bytes that the
    database refuses (VARCHAR2 limit under max_string_size=STANDARD) is compared on its capped
    prefix instead, and counted. Status: pass (every string compared, min cosine >= threshold),
    fail (a lower cosine or another dimension: not the same model), error (a string could not be
    embedded at all: no verdict, the gate stays closed)."""
    rows = []
    for it in doc["items"]:
        row = {"i": it["i"], "lang": it["lang"], "bytes": it["bytes"], "mode": "full", "cos": None,
               "dims": None, "error": None}
        ref = it["vector"]
        try:
            v = embed(it["text"])
        except DBError as e:
            if "capped" not in it:
                rows.append(dict(row, error=short_error(e)))
                continue
            row.update(full_error=short_error(e), mode="capped", bytes=it["capped"]["bytes"])
            ref = it["capped"]["vector"]
            try:
                v = embed(it["capped"]["text"])
            except DBError as e2:
                rows.append(dict(row, error=short_error(e2)))
                continue
        v = list(v)
        row["dims"] = len(v)
        if len(v) == len(ref):
            row["cos"] = 1.0 - cosine_distance(v, ref)
        rows.append(row)
    coss = [r["cos"] for r in rows if r["cos"] is not None]
    min_cos = min(coss) if coss else None
    dim_bad = [r["i"] for r in rows if r["dims"] is not None and r["cos"] is None]
    errors = [r["i"] for r in rows if r["error"]]
    if dim_bad or (min_cos is not None and min_cos < threshold):
        status = "fail"
    elif errors:
        status = "error"
    else:
        status = "pass"
    return {"status": status, "min_cosine": min_cos, "threshold": threshold, "rows": rows,
            "compared": len(coss), "capped": sum(r["mode"] == "capped" and r["cos"] is not None for r in rows),
            "dimension_mismatch": dim_bad, "errors": errors}


def append_parity_csv(path: str, key: str, status: str, min_cos, utc: str) -> str:
    """Append one row to model_parity.csv (atomic rewrite; the header is checked, never changed)."""
    if key not in MODELS or status not in ("pass", "fail", "error") or not UTC_RX.fullmatch(utc or ""):
        raise GuardError("refusing to write a malformed parity row")
    header = ",".join(PARITY_CSV_HEADER)
    line = f"{key},{status},{'' if min_cos is None else format(min_cos, '.10f')},{utc}"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # read-modify-write under an exclusive lock, so two p2 runs cannot drop each other's row
    with open(path + ".lock", "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if text.split("\n", 1)[0].strip() != header:
                raise GuardError(f"{os.path.basename(path)} has another header; expected {header}")
            if not text.endswith("\n"):
                text += "\n"
        else:
            text = header + "\n"
        write_atomic(path, text + line + "\n")
    return line


# =============================================================================================
# P0 status: the transcript of P0 into results/probes/p0.status (the file MODELS is gated on)
# =============================================================================================
P0_STATUS_LINE = re.compile(r"^P0 STATUS: (PASS|FAIL)\b[ \t]*(.*?)[ \t]*$", re.M)
P0_HEADER = "== P0  capacity and safety"
P0_NEED_BYTES = 6 * 1024 ** 3        # the same 6 GB as 04_probes.sql P0 (twice the ~2.8 GB of lab models)


# the gate lines 04_probes.sql P0 prints; a PASS needs every one of them, each passing
P0_GATE_LINES = (("character set", re.compile(r"^GATE character set[ \t]*: PASS\b", re.M)),
                 ("archive / FRA", re.compile(r"^GATE archive / FRA[ \t]*: (?:PASS\b|n/a\b)", re.M)),
                 ("ASKORACLE quota", re.compile(r"^GATE ASKORACLE quota[ \t]*: PASS\b", re.M)),
                 ("room SYSAUX", re.compile(r"^GATE room SYSAUX[ \t]*: PASS\b", re.M)))
P0_QUOTA_TS = re.compile(r"^GATE ASKORACLE quota[ \t]*: PASS - .*? on ([A-Z][A-Z0-9_$#]*)[ \t]*$", re.M)
P0_GATE_FAIL = re.compile(r"^GATE [^:\n]*: FAIL\b", re.M)


def p0_probe_verdict(text: str):
    """(ok, reasons, utc) from a transcript of 04_probes.sql P0 (v1.1 or later). A PASS counts only
    when the transcript holds one P0 run, one STATUS line, every gate line passing and none failing:
    a truncated, edited or concatenated transcript fails closed."""
    if text.count(P0_HEADER) != 1:
        return False, ["not a transcript of exactly one 04_probes.sql P0 run"], None
    found = P0_STATUS_LINE.findall(text)
    if not found:
        return False, ["no 'P0 STATUS:' line: P0 did not finish, or ran from 04_probes.sql before v1.1"], None
    if len(found) > 1:
        return False, [f"{len(found)} 'P0 STATUS:' lines in one transcript"], None
    verdict, rest = found[0]
    m = UTC_RX.search(rest)
    utc = m.group(0) if m else None
    if verdict != "PASS":
        return False, [UTC_RX.sub("", rest).strip() or "a hard gate failed"], utc
    if not utc:
        return False, ["the PASS line carries no UTC time"], None
    missing = [name for name, rx in P0_GATE_LINES if not rx.search(text)]
    q = P0_QUOTA_TS.search(text)
    if q and q.group(1) != "SYSAUX" and not re.search(r"^GATE room " + re.escape(q.group(1)) + r"[ \t]*: PASS\b",
                                                      text, re.M):
        missing.append(f"room {q.group(1)}")
    elif not q:
        missing.append("ASKORACLE's tablespace")
    if P0_GATE_FAIL.search(text):
        return False, ["a GATE line says FAIL although the STATUS line says PASS"], utc
    if missing:
        return False, ["PASS without its gate lines: " + ", ".join(dict.fromkeys(missing))], utc
    return True, [], utc


def _adhoc_facts(text: str) -> dict:
    """Values from the read-only ad-hoc capacity check (results/probes/p0_capacity_adhoc.sql)."""
    facts = {}
    m = re.search(r"^NLS_CHARACTERSET[ \t]+(\S+)[ \t]*$", text, re.M)
    facts["charset"] = m.group(1) if m else None
    m = re.search(r"^(NOARCHIVELOG|ARCHIVELOG)[ \t]*$", text, re.M)
    facts["log_mode"] = m.group(1) if m else None
    m = re.search(r"^pga_aggregate_limit[ \t]+(\S+)[ \t]*$", text, re.M)
    facts["pga_aggregate_limit"] = m.group(1) if m else None
    m = re.search(r"ASKORACLE_QUOTA_BYTES[ \t]*\n[- \t]+\n[ \t]*([A-Z][A-Z0-9_$#]*)[ \t]+(-?\d+)[ \t]*$", text, re.M)
    facts["askoracle_ts"], facts["askoracle_quota"] = (m.group(1), int(m.group(2))) if m else (None, None)
    facts["free_gb"] = {t: float(v) for t, v in re.findall(r"^([A-Z][A-Z0-9_]*)[ \t]+(\d*\.?\d+)[ \t]*$", text, re.M)
                        if t not in ("NOARCHIVELOG", "ARCHIVELOG")}
    facts["files_gb"] = {t: (float(mx), float(cur), ae == "YES") for t, mx, cur, ae in
                         re.findall(r"^([A-Z][A-Z0-9_]*)[ \t]+(\d*\.?\d+)[ \t]+(\d*\.?\d+)[ \t]+(YES|NO)[ \t]*$", text, re.M)}
    return facts


def _adhoc_room_gb(facts: dict, ts: str):
    if ts not in facts["files_gb"]:
        return None
    mx, cur, auto = facts["files_gb"][ts]
    free = facts["free_gb"].get(ts, 0.0)
    return free + (max(mx, cur) - cur if auto else 0.0)


def p0_adhoc_verdict(text: str):
    """(ok, reasons, facts): the hard gates of 04_probes.sql P0 applied to the ad-hoc check's output.
    A value the transcript does not show fails closed."""
    f = _adhoc_facts(text)
    need_gb = P0_NEED_BYTES / 1024 ** 3
    reasons = []
    if f["charset"] != "AL32UTF8":
        reasons.append(f"character-set ({f['charset'] or 'not in the transcript'})")
    if f["log_mode"] is None:
        reasons.append("log mode not in the transcript")
    elif f["log_mode"] == "ARCHIVELOG":
        reasons.append("recovery-area (ARCHIVELOG; the ad-hoc check shows no headroom: run 04_probes.sql P0)")
    q = f["askoracle_quota"]
    if q is None:
        reasons.append("askoracle-quota (not in the transcript)")
    elif q != -1 and q < P0_NEED_BYTES:
        reasons.append(f"askoracle-quota ({q} bytes < {need_gb:.0f} GB)")
    for ts, tag in ((f["askoracle_ts"], "askoracle-room"), ("SYSAUX", "sysaux-room")):
        room = _adhoc_room_gb(f, ts) if ts else None
        if room is None:
            reasons.append(f"{tag} (not in the transcript)")
        elif room < need_gb:
            reasons.append(f"{tag} ({room:.1f} GB < {need_gb:.0f} GB)")
        f[tag] = room
    return (not reasons), reasons, f



# =============================================================================================
# database layer: the only code that talks to Oracle
# =============================================================================================
NO_MATCH = re.compile(r"ORA-20000\b.*no matching results", re.I | re.S)
TRANSIENT = re.compile(r"\b(?:429|500|502|503|504)\b|timed?[ -]?out|ORA-29273|ORA-12170|ORA-03113|"
                       r"ORA-03135|ORA-12541|DPY-4011|DPY-4024|DPI-1080", re.I)


def _simple(name: str) -> str:
    if not SIMPLE.match(name or ""):
        raise GuardError(f"not a plain Oracle name: {name!r}")
    return name


def _probe_index(name: str) -> str:
    if not PROBE_INDEX.match(name or ""):
        raise GuardError(f"not a probe index name: {name!r}")
    return name


def _model(name: str) -> str:
    if name not in MODELS.values():
        raise GuardError(f"unknown model {name!r}")
    return name


def parse_embedding_model(value):
    m = re.fullmatch(r"database:([A-Za-z][A-Za-z0-9_$#]*)\.([A-Za-z][A-Za-z0-9_$#]*)", (value or "").strip(), re.I)
    return (m.group(1).upper(), m.group(2).upper()) if m else (None, None)


PWD_FILE_VAR = "RAG_LAB_PWD_FILE"


def read_password_file(path: str) -> str:
    """The password held in <path>: a regular file (no symlink) owned by this OS user and closed
    to group and others, one line. run_all.sh writes one per run (0600) and shreds it on exit.
    Error messages never show the path or the content."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        raise GuardError(f"{PWD_FILE_VAR}: cannot open the password file ({e.strerror})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise GuardError(f"{PWD_FILE_VAR}: not a regular file")
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise GuardError(f"{PWD_FILE_VAR}: the file must belong to this user and be closed to others (chmod 600)")
        data = b""
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            data += chunk
            if len(data) > 4096:
                raise GuardError(f"{PWD_FILE_VAR}: the file is too large for a password")
    finally:
        os.close(fd)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise GuardError(f"{PWD_FILE_VAR}: the file is not UTF-8") from None
    if text.endswith("\n"):
        text = text[:-1]
    if text.endswith("\r"):
        text = text[:-1]
    if not text or "\n" in text or "\r" in text:
        raise GuardError(f"{PWD_FILE_VAR}: the file must hold one non-empty line")
    return text


def lab_password(isatty=None, prompt=None) -> str:
    """RAG_LAB_PWD_FILE first, then RAG_LAB_PWD, then a hidden prompt on a terminal. Both
    variables leave this process's environment here, so no later child inherits them."""
    path = os.environ.pop(PWD_FILE_VAR, None)
    pwd = os.environ.pop("RAG_LAB_PWD", None)
    if path:
        return read_password_file(path)
    if pwd:
        return pwd
    if not (isatty or sys.stdin.isatty)():
        raise GuardError("RAG_LAB_PWD_FILE and RAG_LAB_PWD are not set and there is no terminal to prompt on")
    pwd = (prompt or getpass.getpass)("RAG_LAB password: ")
    if not pwd:
        raise GuardError("no password given")
    return pwd


class DB:
    def __init__(self, call_timeout_ms: int = 300_000):
        try:
            import oracledb
        except ImportError as e:
            raise GuardError("python-oracledb is not installed in this interpreter") from e
        self.ora = oracledb
        oracledb.defaults.fetch_lobs = False          # CLOBs arrive as str
        dsn = os.environ.get("RAG_LAB_DSN", DEFAULT_DSN)
        if not DSN_RX.match(dsn):
            raise GuardError("RAG_LAB_DSN must look like host:port/service")
        pwd = lab_password()

        register_secret(dsn.split(":")[0].split("/")[0], "<db-host>")     # driver errors name the host
        register_secret(pwd, "<secret>")                                  # never expected, masked anyway

        def _connect():
            return oracledb.connect(user=LAB_USER, password=pwd, dsn=dsn)
        self._connect, self._timeout = _connect, call_timeout_ms
        self.con = None
        self._open()

    def _open(self):
        try:
            self.con = self._connect()
        except self.ora.Error as e:
            raise GuardError("cannot connect as RAG_LAB: " + short_error(e)) from e
        self.con.call_timeout = self._timeout

    def _fail(self, e):
        try:
            healthy = self.con.is_healthy()
        except self.ora.Error:
            healthy = False
        if not healthy:                                # a call timeout can leave the connection unusable
            log.warning("reconnecting after: %s", short_error(e))
            self._open()
        raise DBError(str(e)) from e

    def rows(self, sql: str, binds=None) -> list:
        try:
            with self.con.cursor() as cur:
                cur.execute(sql, binds or {})
                return cur.fetchall()
        except self.ora.Error as e:
            self._fail(e)

    def one(self, sql: str, binds=None):
        r = self.rows(sql, binds)
        return r[0] if r else None

    def run(self, sql: str, binds=None):
        try:
            with self.con.cursor() as cur:
                cur.execute(sql, binds or {})
            self.con.commit()
        except self.ora.Error as e:
            self._fail(e)

    def close(self):
        try:
            self.con.close()
        except self.ora.Error as e:
            log.warning("close failed: %s", short_error(e))

    # --- dictionary --------------------------------------------------------------------------
    def index_exists(self, index: str) -> bool:
        return bool(self.one("select count(*) from user_cloud_vector_indexes where index_name = :i",
                             {"i": index})[0])

    def profile_exists(self, profile: str) -> bool:
        return bool(self.one("select count(*) from user_cloud_ai_profiles where profile_name = upper(:p)",
                             {"p": profile})[0])

    def ix_attrs(self, index: str) -> dict:
        return {str(k).lower(): v for k, v in self.rows(
            "select attribute_name, dbms_lob.substr(attribute_value, 4000, 1) "
            "from user_cloud_vector_index_attributes where index_name = :i", {"i": index})}

    def prof_attrs(self, profile: str) -> dict:
        return {str(k).lower(): v for k, v in self.rows(
            "select attribute_name, dbms_lob.substr(attribute_value, 4000, 1) "
            "from user_cloud_ai_profile_attributes where profile_name = upper(:p)", {"p": profile})}

    def model_visible(self, model: str) -> bool:
        return bool(self.one("select count(*) from all_mining_models where owner = :o and model_name = :m",
                             {"o": MODEL_OWNER, "m": _model(model)})[0])

    def index_model(self, index: str):
        """(owner, model, metric) of an index, from the dictionary: index -> profile_name ->
        embedding_model. The owner must be ASKORACLE (PLAN.md 0.3)."""
        a = self.ix_attrs(_probe_index(index))
        if not a:
            raise GuardError(f"{index} does not exist (run its build probe first)")
        emb = self.prof_attrs(a.get("profile_name") or "").get("embedding_model")
        owner, model = parse_embedding_model(emb)
        if owner != MODEL_OWNER or model not in MODELS.values():
            raise GuardError(f"{index}: index profile {a.get('profile_name')} names {emb!r}, not an ASKORACLE lab model")
        metric = (a.get("vector_distance_metric") or "COSINE").upper()
        return owner, model, metric

    def pipeline_status(self, pipeline: str):
        r = self.one("select status from user_cloud_pipelines where pipeline_name = :p", {"p": _simple(pipeline)})
        return r[0] if r else None

    def pipeline_of(self, index: str):
        """The index's pipeline: its pipeline_name attribute, else <index>$VECPIPELINE when that
        pipeline exists (the name 04_probes.sql drop_leftovers knows), else None."""
        name = self.ix_attrs(_probe_index(index)).get("pipeline_name")
        if name:
            return _simple(str(name).strip())
        guess = _probe_index(index) + "$VECPIPELINE"
        r = self.one("select count(*) from user_cloud_pipelines where pipeline_name = :p", {"p": guess})
        return guess if r and r[0] else None

    def model_size(self, model: str):
        """MODEL_SIZE as ALL_MINING_MODELS reports it (information only: size proves no identity)."""
        try:
            r = self.one("select model_size from all_mining_models where owner = :o and model_name = :m",
                         {"o": MODEL_OWNER, "m": _model(model)})
        except DBError as e:
            log.warning("model_size not readable: %s", short_error(e))
            return None
        return int(r[0]) if r and r[0] is not None else None

    # --- vectors -----------------------------------------------------------------------------
    def embed(self, model: str, text: str) -> list:
        r = self.one("select vector_embedding(" + MODEL_OWNER + "." + _model(model) + " using :t as data) from dual",
                     {"t": text})
        return list(r[0])

    def chunks(self, index: str) -> list:
        return [{"rid": rid, "obj": obj, "content": c or ""} for obj, rid, c in self.rows(
            "select json_value(v.attributes, '$.object_name' returning varchar2(1024)), rowidtochar(v.rowid), v.content "
            'from "' + _probe_index(index) + '$VECTAB" v order by 1, 2')]

    def distances(self, index: str, model: str, text: str) -> dict:
        """rid -> (cosine distance, euclidean distance) of every chunk to the query embedding."""
        sql = ("select rowidtochar(v.rowid), vector_distance(v.embedding, q.e, COSINE), "
               "vector_distance(v.embedding, q.e, EUCLIDEAN) "
               'from "' + _probe_index(index) + '$VECTAB" v cross join '
               "(select vector_embedding(" + MODEL_OWNER + "." + _model(model) + " using :t as data) e from dual) q")
        return {rid: (float(c), float(e)) for rid, c, e in self.rows(sql, {"t": text})}

    def topk(self, index: str, model: str, text: str, k: int) -> list:
        sql = ("select rowidtochar(v.rowid) from \"" + _probe_index(index) + "$VECTAB\" v cross join "
               "(select vector_embedding(" + MODEL_OWNER + "." + _model(model) + " using :t as data) e from dual) q "
               "order by vector_distance(v.embedding, q.e, COSINE), 1 fetch exact first " + str(int(k)) + " rows only")
        return [r[0] for r in self.rows(sql, {"t": text})]

    def vector_checksum(self, index: str) -> tuple:
        """Changes when any stored vector changes: row count and the summed distance to a fixed probe."""
        r = self.one("select count(*), round(sum(vector_distance(v.embedding, q.e, COSINE)), 9) from \""
                     + _probe_index(index) + "$VECTAB\" v cross join (select vector_embedding(" + MODEL_OWNER
                     + ".ALL_MINILM_L12_V2 using 'vector checksum probe' as data) e from dual) q")
        return int(r[0]), float(r[1] or 0)

    # --- files -------------------------------------------------------------------------------
    def file_bytes(self, name: str):
        r = self.one("select dbms_lob.fileexists(bfilename('" + PROBE_DIR + "', :f)) from dual", {"f": name})
        if not r or not r[0]:
            return None
        return int(self.one("select dbms_lob.getlength(bfilename('" + PROBE_DIR + "', :f)) from dual", {"f": name})[0])

    def utl_to_text(self, name: str) -> str:
        try:
            with self.con.cursor() as cur:
                out = cur.var(self.ora.DB_TYPE_CLOB)
                cur.execute("""
                    declare
                      l_bf   bfile := bfilename('""" + PROBE_DIR + """', :f);
                      l_blob blob;
                      l_d    integer := 1;
                      l_s    integer := 1;
                    begin
                      dbms_lob.createtemporary(l_blob, true);
                      dbms_lob.fileopen(l_bf, dbms_lob.file_readonly);
                      dbms_lob.loadblobfromfile(l_blob, l_bf, dbms_lob.lobmaxsize, l_d, l_s);
                      dbms_lob.fileclose(l_bf);
                      :t := dbms_vector_chain.utl_to_text(l_blob);
                      dbms_lob.freetemporary(l_blob);
                    end;""", {"f": name, "t": out})
                v = out.getvalue()
                return v.read() if hasattr(v, "read") else (v or "")
        except self.ora.Error as e:
            self._fail(e)

    # --- Select AI ---------------------------------------------------------------------------
    def generate(self, prompt: str, profile: str, action: str, attributes: str | None = None) -> str:
        if action not in ("narrate", "runsql", "showprompt", "chat"):
            raise GuardError(f"action {action!r} not allowed")
        sql = "select dbms_cloud_ai.generate(prompt => :p, profile_name => :pr, action => :a"
        binds = {"p": prompt, "pr": _simple(profile.upper()), "a": action}
        if attributes is not None:
            sql += ", attributes => :at"
            binds["at"] = attributes
        r = self.one(sql + ") from dual", binds)
        return (r[0] or "") if r else ""

    def set_profile(self, profile: str):
        self.run("begin dbms_cloud_ai.set_profile(profile_name => :p); end;", {"p": _simple(profile)})

    def select_ai(self, action: str, prompt: str) -> str:
        """SELECT AI <action> <prompt>: the statement form that goes through SQL translation (and
        its cache). The prompt is spliced, so it is restricted to plain characters."""
        if action not in ("showprompt", "narrate") or not SELECT_AI_TEXT.match(prompt):
            raise GuardError("SELECT AI probe text must be plain letters, digits and , . ( ) ? -")
        r = self.one("select ai " + action + " " + prompt)
        return (r[0] or "") if r else ""

    def update_index(self, index: str, name: str, value: str):
        if name not in ("match_limit", "profile_name"):
            raise GuardError(f"attribute {name!r} is not changed by the probes")
        self.run("begin dbms_cloud_ai.update_vector_index(index_name => :i, attribute_name => :n, "
                 "attribute_value => :v); end;", {"i": _probe_index(index), "n": name, "v": str(value)})

    def stop_pipeline_if_started(self, pipeline: str) -> bool:
        """Stop an active pipeline (STARTED or RUNNING); fail if it is still active afterwards."""
        if not pipeline or (self.pipeline_status(pipeline) or "").upper() not in ("STARTED", "RUNNING"):
            return False
        self.run("begin dbms_cloud_pipeline.stop_pipeline(pipeline_name => :p, force => true); end;",
                 {"p": _simple(pipeline)})
        after = (self.pipeline_status(pipeline) or "").upper()
        if after in ("STARTED", "RUNNING"):
            raise GuardError(f"pipeline {pipeline} is still {after} after stop_pipeline")
        return True

    def create_scratch_profile(self, name: str, attrs_json: str):
        if not SCRATCH_PROFILE.match(name):
            raise GuardError(f"refusing to create profile {name!r}")
        self.drop_scratch_profile(name)
        self.run("begin dbms_cloud_ai.create_profile(profile_name => :p, attributes => :a, status => 'enabled', "
                 "description => 'RAG lab probe scratch profile'); end;", {"p": name, "a": attrs_json})

    def drop_scratch_profile(self, name: str):
        if not SCRATCH_PROFILE.match(name):
            raise GuardError(f"refusing to drop profile {name!r}")
        if self.profile_exists(name):
            self.run("begin dbms_cloud_ai.drop_profile(profile_name => :p, force => true); end;", {"p": name})


@dataclasses.dataclass
class CallResult:
    status: str               # ok | no_match | error (a finding) | infra_error (retries exhausted)
    text: str | None
    error: str | None
    attempts: int
    ms: int


class Caller:
    """Serial Select AI calls, at least min_interval apart (OCI quota is shared with the existing HR app).
    Transient failures are retried after 5/15/45 s (cap 3). retry_all=True treats every
    failure except the no-match refusal as infra (p17); otherwise a non-transient error is
    returned at once as a finding (p7, p11: an unsupported action or parameter)."""

    def __init__(self, min_interval=1.0, backoff=(5, 15, 45), retry_all=False, sleep=time.sleep,
                 clock=time.monotonic):
        self.min_interval, self.backoff, self.retry_all = float(min_interval), tuple(backoff), retry_all
        self.sleep, self.clock, self._last = sleep, clock, None
        self.calls = 0

    def _pace(self):
        if self._last is not None:
            gap = self.clock() - self._last
            if gap < self.min_interval:
                self.sleep(self.min_interval - gap)
        self._last = self.clock()

    def call(self, fn, *args) -> CallResult:
        err, attempts, ms = None, 0, 0
        for wait in (0,) + self.backoff:
            if wait:
                log.warning("backoff %ss before attempt %d", wait, attempts + 1)
                self.sleep(wait)
            self._pace()
            attempts += 1
            self.calls += 1
            t0 = self.clock()
            try:
                text = fn(*args)
                return CallResult("ok", text or "", None, attempts, int((self.clock() - t0) * 1000))
            except DBError as e:
                ms = int((self.clock() - t0) * 1000)
                msg = str(e)
                if NO_MATCH.search(msg):
                    return CallResult("no_match", None, short_error(msg), attempts, ms)
                err = short_error(msg)
                if not (self.retry_all or TRANSIENT.search(msg)):
                    return CallResult("error", None, err, attempts, ms)
                log.warning("infra error, attempt %d: %s", attempts, err)
        return CallResult("infra_error", None, err, attempts, ms)


def pause_requested(path) -> bool:
    return bool(path) and os.path.exists(path)


# =============================================================================================
# probes that need no database
# =============================================================================================
def cmd_files(a) -> int:
    out = Out("files", a.out_dir)
    rows = load_probe_files()
    out("== probe files (probe_files.txt): what RAG_PROBE_DIR must hold, and where each comes from")
    out(f"   {'probe_name':<34}{'stage':<9}{'role':<18}{'bytes':>9}  sha256 (16)       origin")
    data, missing = [], 0
    for r in rows:
        if r.origin.startswith("probe:"):
            size, digest, where = "-", "-", r.origin
        else:
            path = brief_path(r.origin)
            if os.path.isfile(path):
                size, digest, where = str(os.path.getsize(path)), file_sha256(path)[:16], r.origin
            else:
                size, digest, where = "MISSING", "-", r.origin + (f"   (make with: {r.made_by})" if r.made_by != "-" else "")
                if r.stage == "initial" and r.required == "yes":
                    missing += 1
        out(f"   {r.probe_name:<34}{r.stage:<9}{r.role:<18}{size:>9}  {digest:<17} {where}")
        data.append(dict(dataclasses.asdict(r), bytes=size, sha256_16=digest))
    out("")
    out("   stage=initial: 04_probes.py stage-dir --stage initial --dir D; 01_stage_files.sh probe D")
    out("   stage=p13    : staged after p1 is built, for P13 (04_probes.py stage-dir --stage p13)")
    out.finish({"files": data, "missing_required": missing})
    if missing:
        log.error("%d required probe file(s) missing", missing)
        return 2
    return 0


def cmd_p0_df(a) -> int:
    out = Out("p0-df", a.out_dir)
    if not a.path or not os.path.isabs(a.path) or not os.path.isdir(a.path):
        raise GuardError("--path must be an existing absolute directory (it is not printed)")
    st = os.statvfs(a.path)
    free = st.f_bavail * st.f_frsize
    total = st.f_blocks * st.f_frsize
    gb = 1024 ** 3
    out("== P0 (host)  free space on the database volume (path not shown)")
    out(f"   size {total / gb:.1f} GB, free {free / gb:.1f} GB ({100.0 * free / total:.0f} %)" if total else "   size unknown")
    verdict = "WARN below 10 GB free: staging and redo need room" if free < 10 * gb else "PASS (10 GB or more free)"
    out(f"   {verdict}")
    out.finish({"free_bytes": free, "total_bytes": total})
    return 0


def cmd_p0_status(a) -> int:
    """results/probes/p0.status from the P0 transcript: 'P0 PASS <utc>' or 'P0 FAIL <reason>'.
    run_all.sh MODELS is gated on it. --adhoc reads the read-only check that ran on 29-Sep
    (results/probes/p0_capacity_adhoc.sql) and applies 04_probes.sql P0's hard gates to it."""
    out = Out("p0-status", a.out_dir)
    try:
        with open(a.transcript, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise GuardError(f"cannot read the transcript: {type(e).__name__}") from e
    facts = None
    if a.adhoc:
        if not UTC_RX.fullmatch(a.run_utc or ""):
            raise GuardError("--adhoc needs --run-utc YYYY-MM-DDTHH:MM:SSZ: when the check ran, in UTC")
        ok, reasons, facts = p0_adhoc_verdict(text)
        utc, source = a.run_utc, "the read-only ad-hoc capacity check (p0_capacity_adhoc.sql)"
    else:
        if a.run_utc:
            raise GuardError("--run-utc is for --adhoc only: 04_probes.sql P0 prints its own UTC time")
        ok, reasons, utc = p0_probe_verdict(text)
        source = "04_probes.sql P0"
    line = f"P0 PASS {utc}" if ok else "P0 FAIL " + "; ".join(reasons) + (f"; P0 ran {utc}" if utc else "")
    path = os.path.join(a.results_dir, "probes", "p0.status")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_atomic(path, redact(line) + "\n")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    out(f"== P0 status from {source}")
    out(f"   transcript {os.path.basename(a.transcript)}, sha256 {digest[:16]}")
    if facts:
        out(f"   character set {facts['charset']}, log mode {facts['log_mode']}, pga_aggregate_limit "
            f"{facts['pga_aggregate_limit']}, ASKORACLE quota on {facts['askoracle_ts']}: "
            f"{'UNLIMITED' if facts['askoracle_quota'] == -1 else facts['askoracle_quota']}")
        for tag in ("askoracle-room", "sysaux-room"):
            room = facts.get(tag)
            out(f"   {tag}: {'-' if room is None else format(room, '.1f') + ' GB (free + autoextend)'}")
    out(f"   wrote results/probes/p0.status: {line}")
    out.finish({"source": source, "transcript": os.path.basename(a.transcript), "transcript_sha256": digest,
                "status": line, "pass": ok, "reasons": reasons, "facts": facts})
    return 0 if ok else 5


def cmd_stage_dir(a) -> int:
    """Assemble the probe files of one stage, under their probe names, in a local directory.
    01_stage_files.sh then copies that directory into RAG_PROBE_DIR (the one guarded write path):
      initial:  01_stage_files.sh probe <dir>                        before P5
      p13:      01_stage_files.sh probe <dir> --allow-non-ascii      before 04_probes.sql P13
    P15 needs no directory: 01_stage_files.sh probe-rm PRB-P13-EN-COPY.pdf."""
    out = Out("stage-dir", a.out_dir)
    d = check_stage_dir(a.dir)
    every = load_probe_files()
    by_name = {r.probe_name: r for r in every}
    want = {}
    out(f"== probe files, stage {a.stage}: assembled for 01_stage_files.sh (directory not shown)")
    for r in (x for x in every if x.stage == a.stage):
        src_row = by_name[r.origin[len("probe:"):]] if r.origin.startswith("probe:") else r
        src = brief_path(src_row.origin)
        if not os.path.isfile(src):
            if r.required == "yes":
                raise GuardError(f"{r.probe_name}: its origin {src_row.origin} is missing")
            out(f"   {r.probe_name:<34} skipped: optional, origin missing (make with: {r.made_by})")
            continue
        want[r.probe_name] = src
    os.makedirs(d, exist_ok=True)
    extra = sorted(e.name for e in os.scandir(d) if e.name not in want)
    if extra:
        raise GuardError(f"the directory holds {len(extra)} other entr(y/ies), e.g. {extra[0]!r}: use an empty one "
                         "(01_stage_files.sh stages everything in it)")
    staged = []
    for name, src in sorted(want.items()):
        dst = os.path.join(d, name)
        digest = file_sha256(src)
        if os.path.lexists(dst):
            if os.path.islink(dst) or not os.path.isfile(dst) or file_sha256(dst) != digest:
                raise GuardError(f"{name} exists with other content; refusing to overwrite it")
            state = "present"
        else:
            shutil.copyfile(src, dst)
            os.chmod(dst, 0o644)
            if file_sha256(dst) != digest:
                raise GuardError(f"{name}: the copy does not match its origin")
            state = "copied"
        staged.append({"name": name, "bytes": os.path.getsize(dst), "sha256": digest})
        out(f"   {name:<34} {state:<8}{os.path.getsize(dst):>9} bytes  sha256 {digest[:16]}")
    out("")
    if a.stage == "initial":
        out("   next: 01_stage_files.sh probe <this directory>          (then 05_extraction_gate.sql, p5)")
    else:
        out("   next: 01_stage_files.sh probe <this directory> --allow-non-ascii   (then 04_probes.sql P13)")
        out("   P15 : 01_stage_files.sh probe-rm PRB-P13-EN-COPY.pdf              (then 04_probes.sql P15)")
    out.finish({"stage": a.stage, "files": staged})
    return 0


def cmd_make_noto_control(a) -> int:
    """The P5 negative control: GLF-001-AR printed exactly like the corpus, but with Noto Naskh
    Arabic instead of Amiri (the font that broke 56-63 % of Arabic words locally)."""
    out = Out("make-noto-control", a.out_dir)
    dst = os.path.abspath(a.out)
    for forbidden in (bc.OUT_PDF, bc.OUT_DOCX, os.path.join(ROOT, "corpus", "src")):
        if os.path.commonpath([dst, os.path.abspath(forbidden)]) == os.path.abspath(forbidden):
            raise GuardError("refusing to write the negative control into the corpus itself")
    if not dst.endswith(".pdf"):
        raise GuardError("--out must be a .pdf file")
    if not os.path.exists(a.chrome):
        raise GuardError("Chromium not found (set --chrome or CHROME_BIN)")
    row = next(r for r in load_probe_files() if r.role == "negative-control")
    meta, blocks = bc.parse_source(brief_path(row.source_text))
    if bc.doc_language(meta, blocks) != "ar":
        raise GuardError("the negative control must be an Arabic document")
    noto = []
    for fam, f, w in bc.FONTS:
        if fam == "Doc Arabic":
            f = "NotoNaskhArabic-Bold.ttf" if w >= 700 else "NotoNaskhArabic-Regular.ttf"
        if not os.path.exists(os.path.join(bc.FONT_DIR, f)):
            raise GuardError(f"font missing: corpus/fonts/{f}")
        noto.append((fam, f, w))
    saved = bc.FONTS
    bc.FONTS = tuple(noto)
    try:
        html_text = bc.to_html(meta, blocks, "ar")
    finally:
        bc.FONTS = saved
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="noto_ctl_") as tmp:
        h = os.path.join(tmp, "noto-control.html")
        with open(h, "w", encoding="utf-8") as f:
            f.write(html_text)
        bc.print_pdf(a.chrome, h, dst)
    embedded, pages = bc.pdf_facts(dst)
    if not any("NotoNaskhArabic" in e for e in embedded):
        raise GuardError(f"Noto Naskh Arabic is not embedded ({embedded})")
    local = None
    if shutil.which("pdftotext"):
        local = bc.word_integrity(bc.plain_text(meta, blocks), bc.run(["pdftotext", dst, "-"], timeout=60))
    shown = os.path.relpath(dst, ROOT) if dst.startswith(ROOT + os.sep) else os.path.basename(dst)
    out("== P5 negative control built: GLF-001-AR printed with Noto Naskh Arabic")
    out(f"   {shown}: {pages} pages, fonts {' '.join(embedded)}, sha256 {file_sha256(dst)[:16]}")
    if local is not None:
        out(f"   local pdftotext word recall {local:.3f} (Amiri scores about 0.98; this file should fail the gate)")
    out.finish({"file": shown, "pages": pages, "fonts": embedded, "local_recall": local})
    return 0


# =============================================================================================
# database probes
# =============================================================================================
def _need_index(db: DB, index: str, probe: str):
    if not db.index_exists(index):
        raise GuardError(f"{index} does not exist: run 04_probes.sql {probe} first")


def _need_profile(db: DB, profile: str, how: str):
    if not db.profile_exists(profile):
        raise GuardError(f"profile {profile} does not exist: {how}")


def rag_profile_for(index: str) -> str:
    return "RAG_P_" + _probe_index(index)[len("RAG_"):]


def cmd_p2(a) -> int:
    """P2: the database embeds the 20 token-id strings, one per call, and each vector is compared
    with the local reference of the same ONNX file (verify_models.py refvec, also one per call:
    dynamic INT8 moves a vector by up to ~0.005 cosine inside a batch). Appends
    model_key,status,min_cosine,utc to results/model_parity.csv; BUILD reads the last row per key."""
    key = (a.key or "").strip().upper()
    if key not in MODELS:
        raise GuardError(f"unknown model key {key!r} (M0..M6, M1Q)")
    model = MODELS[key]
    ref_path = a.refvec or os.path.join(a.results_dir, "models", f"{key}_refvec.json")
    doc = load_refvec(ref_path, key)
    out = Out(f"p2_{key}", a.out_dir)
    db = DB()
    try:
        if not db.model_visible(model):
            raise GuardError(f"{MODEL_OWNER}.{model} is not visible to RAG_LAB: run 03_load_models.sql {key} first")
        size = db.model_size(model)
        out(f"== P2  {MODEL_OWNER}.{model} ({key}) in the database vs the local file, 20 strings, one text per call")
        out(f"   reference: {os.path.basename(ref_path)} from {doc.get('model')} ({doc.get('model_bytes')} bytes, sha256 "
            f"{str(doc.get('model_sha256'))[:16]}), onnxruntime {(doc.get('runtime') or {}).get('onnxruntime')}, "
            f"dim {doc['dim']}" + (f"; {PARITY_REFERENCE[key]}" if key in PARITY_REFERENCE else ""))
        out(f"   MODEL_SIZE in the database: {size if size is not None else 'n/a'} (a size proves no identity; "
            "the vectors below do)")
        res = parity_compare(lambda t: db.embed(model, t), doc)
    finally:
        db.close()
    out("")
    out(f"   {'#':>3}  {'lang':<5}{'bytes':>7}  {'mode':<7}{'cosine':>14}  note")
    for r in res["rows"]:
        note = r["error"] or ("full text refused, compared on its capped prefix: " + r["full_error"]
                              if r.get("full_error") else "")
        if r["dims"] is not None and r["cos"] is None:
            note = f"dimension {r['dims']} in the database, {doc['dim']} locally"
        cos = "-" if r["cos"] is None else f"{r['cos']:.10f}"
        out(f"   {r['i']:>3}  {r['lang']:<5}{r['bytes']:>7}  {r['mode']:<7}{cos:>14}  {note}")
    utc = utc_stamp()
    line = append_parity_csv(os.path.join(a.results_dir, "model_parity.csv"), key, res["status"],
                             res["min_cosine"], utc)
    out("")
    mc = "-" if res["min_cosine"] is None else f"{res['min_cosine']:.10f}"
    out(f"VERDICT: {res['status'].upper()}  min cosine {mc} over {res['compared']} of {PARITY_N} strings "
        f"(threshold {PARITY_MIN_COSINE}); {res['capped']} compared on their {doc['cap_bytes']}-byte prefix")
    out(f"   recorded in results/model_parity.csv: {line}")
    out.finish({"key": key, "model": model, "refvec": os.path.basename(ref_path), "model_size": size,
                "reference_sha256": doc.get("model_sha256"), **res, "csv_line": line})
    return 0 if res["status"] == "pass" else 5


def cmd_p4(a) -> int:
    out = Out("p4", a.out_dir)
    keys = [k.strip().upper() for k in a.models.split(",") if k.strip()] if a.models else list(MODELS)
    for k in keys:
        if k not in MODELS:
            raise GuardError(f"unknown model key {k}")
    samples = {lang: sample_words(lang, a.sample_chars) for lang in ("en", "ar")}
    db = DB()
    out("== P4  truncation: the shortest word-boundary prefix whose embedding equals the whole sample's")
    for lang, w in samples.items():
        out(f"   sample {lang}: {len(w)} words, {len(prefix(w, len(w)))} characters, "
            f"{len(prefix(w, len(w)).encode('utf-8'))} bytes (Gulf {lang.upper()} sources in file order)")
    out(f"   equal = cosine distance <= {a.eps:g}; the prefix before the window differs by d_below")
    out("")
    out(f"   {'key':<5}{'model':<26}{'lang':<5}{'window chars':>13}{'words':>7}{'bytes':>7}  {'d_below':>9}  note")
    results = []
    for k in keys:
        model = MODELS[k]
        if not db.model_visible(model):
            out(f"   {k:<5}{model:<26}      not visible to RAG_LAB (not loaded, or no SELECT ON MINING MODEL)")
            continue
        for lang in ("en", "ar"):
            words = samples[lang]
            rec = {"key": k, "model": model, "lang": lang}
            t0 = time.monotonic()
            # VECTOR_EMBEDDING converts its input to VARCHAR2: when the whole sample is refused
            # (max_string_size STANDARD = 4000 bytes), retry once with a shorter sample. An error
            # during the search itself is recorded, never retried on a different sample.
            for cap in (None, 32000, 4000):
                w = words if cap is None else cap_words_to_bytes(words, cap)
                try:
                    db.embed(model, prefix(w, len(w)))
                except DBError as e:
                    rec.update(error=short_error(e), input_cap_bytes=cap)
                    log.warning("%s %s: input of %s bytes refused: %s", k, lang, cap or "all", short_error(e))
                    continue
                try:
                    r = measure_window(w, lambda text, m=model: db.embed(m, text), a.eps)
                    rec.update(r, input_cap_bytes=cap, error=None)
                except DBError as e:
                    rec.update(error=short_error(e), input_cap_bytes=cap)
                break
            rec["seconds"] = round(time.monotonic() - t0, 1)
            results.append(rec)
            if rec.get("error") and "k" not in rec:
                out(f"   {k:<5}{model:<26}{lang:<5}  error: {rec['error']}")
                continue
            note = []
            if not rec["truncated"]:
                note.append(f"no truncation within the sample: window >= {rec['sample_chars']} chars")
            if rec["input_cap_bytes"]:
                note.append(f"input capped at {rec['input_cap_bytes']} bytes (VARCHAR2 limit)")
            if not rec["monotone_ok"]:
                note.append("NOT MONOTONE: a longer prefix differs again; do not use this window")
            if rec["degenerate"]:
                note.append("DEGENERATE: one word embeds like the whole text")
            d_below = "-" if rec["d_below"] is None else f"{rec['d_below']:.2e}"
            out(f"   {k:<5}{model:<26}{lang:<5}{rec['window_chars']:>13}{rec['k']:>7}{rec['window_bytes']:>7}  "
                f"{d_below:>9}  {'; '.join(note)}")
    out("")
    out("   share of a chunk the model reads (window chars / chunk_size; '>=' where no truncation was seen)")
    out(f"   {'key':<5}{'lang':<5}" + "".join(f"{cs:>9}" for cs in CHUNK_SIZES))
    for r in results:
        if "k" not in r:
            continue
        cells = "".join(f"{('>=' if c['lower_bound'] else '') + format(c['embedded'], '.0%'):>9}" for c in coverage(r))
        out(f"   {r['key']:<5}{r['lang']:<5}{cells}")
    out.finish({"eps": a.eps, "sample_chars": a.sample_chars, "results": results})
    db.close()
    return 0


def cmd_p5(a) -> int:
    out = Out("p5", a.out_dir)
    rows = [r for r in load_probe_files() if r.stage == "initial"]
    db = DB()
    have_p1 = db.index_exists(P1)
    p1_chunks = db.chunks(P1) if have_p1 else []
    out("== P5  extraction gate: word recall vs the source (build_corpus.py normaliser), '?', U+0640, reversed numbers")
    out(f"   gate per file: recall >= {MIN_RECALL:.2f}, 0 reversed numbers, 0 '?' not in the source, 0 U+FFFD/U+00BF,"
        " 0 added U+0640")
    out(f"   sources measured: UTL_TO_TEXT{' and the text p1 stored' if have_p1 else ' (p1 not built yet: re-run after P6)'}")
    results, data = {}, []
    for r in rows:
        out("")
        out(f"-- {r.probe_name}   ({r.role}, {r.lang}, {r.format})")
        rec = {"file": r.probe_name, "role": r.role, "lang": r.lang, "format": r.format}
        src = source_plain_text(r.source_text)
        nbytes = db.file_bytes(r.probe_name)
        if nbytes is None:
            out(f"   not in {PROBE_DIR}" + (" - REQUIRED" if r.required == "yes" else " - optional, skipped"))
            results[r.role] = {"pass": None, "reasons": ["not staged"]}
            rec["staged"] = False
            data.append(rec)
            continue
        local = brief_path(r.origin) if not r.origin.startswith("probe:") else None
        lsize = os.path.getsize(local) if local and os.path.isfile(local) else None
        size_note = "" if lsize is None else ("  (same size as the local origin)" if lsize == nbytes
                                              else f"  (WARN: local origin has {lsize} bytes)")
        out(f"   bytes in the directory: {nbytes}{size_note}")
        verdicts = []
        try:
            text = db.utl_to_text(r.probe_name)
            m = extraction_metrics(src, text, strict_q=True)
            ok, why = gate_verdict(m)
            verdicts.append((ok, ["UTL_TO_TEXT: " + x for x in why]))
            rec["utl_to_text"] = dict(m, pass_=ok, reasons=why)
            _print_metrics(out, "UTL_TO_TEXT", m, ok, why)
            out("   sample: " + mark_lost(re.sub(r"\s+", " ", text[:240])))
        except DBError as e:
            verdicts.append((False, ["UTL_TO_TEXT failed: " + short_error(e)]))
            rec["utl_to_text"] = {"error": short_error(e)}
            out("   UTL_TO_TEXT failed: " + short_error(e))
        if have_p1:
            stored = "\n".join(c["content"] for c in p1_chunks if c["obj"] == r.probe_name)
            if stored:
                m2 = extraction_metrics(src, stored, strict_q=False)
                ok2, why2 = gate_verdict(m2)
                verdicts.append((ok2, ["in p1: " + x for x in why2]))
                rec["p1_stored"] = dict(m2, pass_=ok2, reasons=why2)
                _print_metrics(out, "in p1", m2, ok2, why2)
            else:
                verdicts.append((False, ["p1 stored no chunk for this file"]))
                rec["p1_stored"] = {"chunks": 0}
                out("   in p1        : no chunk stored for this file")
        passed = all(v[0] for v in verdicts)
        reasons = [x for v in verdicts for x in v[1]]
        results[r.role] = {"pass": passed, "reasons": reasons}
        rec.update(staged=True, passed=passed, reasons=reasons)
        data.append(rec)
        out(f"   FILE {'PASS' if passed else 'FAIL'}" + ("" if passed else ": " + "; ".join(reasons)))
    d = decide_format(results)
    out("")
    out(f"DECISION: {'STOP' if d['stop'] else 'Gulf documents go in as ' + d['corpus_format'].upper()}")
    for x in d["reasons"]:
        out(f"   because {x}")
    for x in d["warnings"]:
        out(f"   note: {x}")
    out.finish({"files": data, "decision": d, "min_recall": MIN_RECALL, "p1_measured": have_p1})
    db.close()
    return 3 if d["stop"] else 0


def _print_metrics(out, label, m, ok, why):
    out(f"   {label:<13}: recall {m['recall']:.3f} ({m['words_source']} source words, {m['missing_distinct']} distinct"
        f" missing)  reversed numbers {len(m['reversed_numbers'])}  reversed AR words {m['reversed_words']}")
    out(f"   {'':<13}  '?' {m['q_raw']} (source {m['q_source']}, in place of letters {m['q_suspicious']})"
        f"  U+FFFD {m['fffd']}  U+00BF {m['inverted_q']}  U+0640 {m['tatweel']} (added in {m['tatweel_words_added']}"
        f" words)  presentation forms {m['presentation_forms']}  -> {'pass' if ok else 'FAIL'}")
    if m["missing_examples"] and not ok:
        out(f"   {'':<13}  missing e.g.: {mark_lost(' '.join(m['missing_examples'][:6]))}")


def cmd_p7(a) -> int:
    out = Out("p7", a.out_dir)
    db = DB()
    _need_index(db, P1, "P6")
    prof = rag_profile_for(P1)
    _need_profile(db, prof, "run 04_probes.sql P6 (it creates it)")
    caller = Caller(min_interval=a.min_interval)
    try:
        sys.path.insert(0, os.path.join(HERE, "eval"))
        import eval_norm
        sources_rx = eval_norm.SOURCES_HEADER
    except ImportError:
        sources_rx = re.compile(r"^[ \t>#*_\-]*(?:sources?|references?|المصادر)[ \t*_]*(?:[:：]|$)", re.I | re.M)
    chunks = db.chunks(P1)
    out(f"== P7  what GENERATE returns from a RAG profile for runsql, showprompt and narrate ({prof} over {P1})")
    data = []
    for q in (PROBE_QUESTIONS[0], PROBE_QUESTIONS[2]):
        for action, max_lines in (("runsql", 30), ("showprompt", 40), ("narrate", 30)):
            res = caller.call(db.generate, q["text"], prof, action)
            text = res.text or ""
            out("")
            out(f"-- GENERATE(prompt => {q['id']} ({q['lang']}), profile_name => '{prof}', action => '{action}')")
            out(f"   prompt: {q['text']}")
            rec = {"q": q["id"], "action": action, "status": res.status, "error": res.error, "ms": res.ms,
                   "chars": len(text), "sha16": sha16(text)}
            if res.status != "ok":
                out(f"   {res.status}: {res.error}")
            else:
                rows = parse_runsql_rows(text)
                found = chunks_in_text(chunks, text)
                rec.update(json_rows=len(rows), row_keys=rows[0]["keys"] if rows else [], chunks_found=len(found),
                           sources_block=bool(sources_rx.search(text)))
                out(f"   {len(text)} characters, sha256 {sha16(text)}, {res.ms} ms; JSON rows {len(rows)}"
                    + (f" with keys {', '.join(rows[0]['keys'])}" if rows else "")
                    + f"; p1 chunks carried whole {len(found)}"
                    + ("; a Sources block" if rec["sources_block"] else ""))
                out.block(text, max_lines)
                out.save_text(f"{q['id']}_{action}.txt", text)
            data.append(rec)
    out("")
    runsql_rows = [d for d in data if d["action"] == "runsql" and d.get("json_rows")]
    out("VERDICT: runsql " + ("returns rows (keys above): the harness can cross-check Select AI's own ranking"
                              if runsql_rows else "returns no parseable rows: the retrieval layer stays harness-only,"
                              " cross-checked by document names from showprompt"))
    out.finish({"calls": data, "llm_calls": caller.calls})
    db.close()
    return 0


def cmd_p8(a) -> int:
    out = Out("p8", a.out_dir)
    db = DB()
    _need_index(db, P1, "P6")
    prof = rag_profile_for(P1)
    _need_profile(db, prof, "run 04_probes.sql P6 (it creates it)")
    caller = Caller(min_interval=a.min_interval)
    chunks = db.chunks(P1)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    q = f"How many days of annual leave can an employee at the Doha branch carry forward (cache probe {stamp})"
    ml0 = db.ix_attrs(P1).get("match_limit")
    if not ml0 or not re.fullmatch(r"[0-9]{1,3}", str(ml0).strip()):
        raise GuardError(f"{P1} has no readable match_limit to restore afterwards; rebuild it with P6")
    ml0 = str(ml0).strip()
    pipe = db.pipeline_of(P1)
    out(f"== P8  cache: SELECT AI vs GENERATE, the same prompt before and after UPDATE_VECTOR_INDEX match_limit ({P1})")
    out(f"   prompt: {q}")
    if not pipe:
        out(f"   WARN: no pipeline found for {P1}; nothing can be stopped after the updates")
    rows = []
    restarts = []
    try:
        for ml in ("5", "2"):
            db.update_index(P1, "match_limit", ml)
            # UPDATE_VECTOR_INDEX can restart the pipeline, which would re-scan RAG_PROBE_DIR on its
            # own schedule (PLAN.md 2.5): stopped again before anything else runs. A pipeline the
            # update created is looked up again.
            pipe = pipe or db.pipeline_of(P1)
            if db.stop_pipeline_if_started(pipe):
                restarts.append(ml)
            db.set_profile(prof)
            for label, fn, args in (("SELECT AI showprompt", db.select_ai, ("showprompt", q)),
                                    ("GENERATE showprompt", db.generate, (q, prof, "showprompt"))):
                res = caller.call(fn, *args)
                text = res.text or ""
                found = len(chunks_in_text(chunks, text)) if res.status == "ok" else None
                rows.append({"call": label, "match_limit": ml, "status": res.status, "error": res.error,
                             "chars": len(text), "sha16": sha16(text) if res.status == "ok" else None,
                             "chunks": found})
                out.save_text(f"{label.split()[0].lower()}_ml{ml}.txt", text)
    finally:
        try:
            db.update_index(P1, "match_limit", ml0)
            out(f"   match_limit restored to {ml0}")
        finally:
            pipe = pipe or db.pipeline_of(P1)
            if db.stop_pipeline_if_started(pipe):
                restarts.append(f"restore {ml0}")
    pipe_end = db.pipeline_status(pipe) if pipe else None
    if restarts:
        out(f"   UPDATE_VECTOR_INDEX restarted {pipe} after: {', '.join(restarts)}; stopped again each time")
    out(f"   pipeline {pipe or '<none>'} at the end: {pipe_end or '-'}")
    out("")
    out(f"   {'call':<22}{'match_limit':>12}{'status':>9}{'chars':>8}  {'sha256 (16)':<17}{'p1 chunks':>10}")
    for r in rows:
        out(f"   {r['call']:<22}{r['match_limit']:>12}{r['status']:>9}{r['chars']:>8}  {r['sha16'] or '-':<17}"
            f"{'-' if r['chunks'] is None else r['chunks']:>10}" + (f"   {r['error']}" if r["error"] else ""))
    s = {(r["call"], r["match_limit"]): r for r in rows}
    sa5, sa2 = s[("SELECT AI showprompt", "5")], s[("SELECT AI showprompt", "2")]
    g5, g2 = s[("GENERATE showprompt", "5")], s[("GENERATE showprompt", "2")]
    out("")
    if g5["status"] != "ok" or g2["status"] != "ok":
        verdict = "INCONCLUSIVE: GENERATE showprompt failed (see the error)"
    elif g5["sha16"] == g2["sha16"]:
        verdict = "INCONCLUSIVE: the match_limit change did not show in GENERATE's prompt either"
    elif sa5["status"] != "ok" or sa2["status"] != "ok":
        verdict = "GENERATE follows the change (uncached); SELECT AI showprompt failed, so its cache is not shown here"
    elif sa5["sha16"] == sa2["sha16"]:
        verdict = "SELECT AI returned the OLD prompt after the change (cached per statement text); GENERATE did not"
    else:
        verdict = "neither is cached for this RAG prompt: both followed the change"
    out("VERDICT: " + verdict)
    out.finish({"rows": rows, "verdict": verdict, "llm_calls": caller.calls, "pipeline": pipe,
                "pipeline_restarts": restarts, "pipeline_status_end": pipe_end})
    db.close()
    return 0


def cmd_p11(a) -> int:
    out = Out("p11", a.out_dir)
    db = DB()
    _need_index(db, P1, "P6")
    prof = rag_profile_for(P1)
    _need_profile(db, prof, "run 04_probes.sql P6 (it creates it)")
    caller = Caller(min_interval=a.min_interval)
    q = PROBE_QUESTIONS[0]["text"]
    c1 = db.chunks(P1)
    have_p2 = db.index_exists(P2)
    c2 = db.chunks(P2) if have_p2 else []
    out(f"== P11  per-call attributes: GENERATE(..., attributes => ...) on {prof}, and seed")
    rec = {}

    def gen(action, attrs, label):
        res = caller.call(db.generate, q, prof, action, None if attrs is None else json.dumps(attrs))
        text = res.text or ""
        r = {"status": res.status, "error": res.error, "chars": len(text),
             "sha16": sha16(text) if res.status == "ok" else None,
             "p1_chunks": len(chunks_in_text(c1, text)) if res.status == "ok" else None,
             "p2_chunks": len(chunks_in_text(c2, text)) if res.status == "ok" and c2 else None}
        rec[label] = r
        out(f"   {label:<44} {r['status']:<9} chars {r['chars']:>6}  sha {r['sha16'] or '-':<16}"
            f"  p1 chunks {'-' if r['p1_chunks'] is None else r['p1_chunks']:>2}"
            f"  p2 chunks {'-' if r['p2_chunks'] is None else r['p2_chunks']:>2}"
            + (f"  {r['error']}" if r["error"] else ""))
        out.save_text(label.replace(" ", "_").replace("/", "-") + ".txt", text)
        return r

    base = gen("showprompt", None, "a showprompt, no attributes")
    if have_p2:
        over = gen("showprompt", {"vector_index_name": P2}, "b showprompt + vector_index_name RAG_PRB_P2")
        if over["status"] != "ok":
            v_idx = "REJECTED: GENERATE takes no per-call vector_index_name here (" + (over["error"] or "") + ")"
        elif (over["p2_chunks"] or 0) > 0 and (over["p1_chunks"] or 0) == 0:
            v_idx = "APPLIED: one RAG profile could serve every index (per-call vector_index_name)"
        elif over["sha16"] == base["sha16"]:
            v_idx = "IGNORED: accepted, but the prompt is unchanged (keep one RAG profile per index)"
        else:
            v_idx = "UNCLEAR: the prompt changed but not to p2's chunks; inspect the saved texts"
    else:
        v_idx = "NOT TESTED: RAG_PRB_P2 does not exist (run 04_probes.sql P10 first)"
    n0 = gen("narrate", None, "c narrate, no attributes")
    n1 = gen("narrate", {"max_tokens": 16}, "d narrate + max_tokens 16")
    if n1["status"] != "ok":
        v_tok = "REJECTED: " + (n1["error"] or "")
    elif n0["status"] == "ok" and n1["chars"] < 0.5 * n0["chars"]:
        v_tok = f"APPLIED: {n0['chars']} -> {n1['chars']} characters"
    else:
        v_tok = f"NOT APPLIED: {n0['chars']} -> {n1['chars']} characters"
    s1 = gen("narrate", {"seed": 7}, "e narrate + seed 7 (per call)")
    v_seed_call = "accepted" if s1["status"] == "ok" else "REJECTED: " + (s1["error"] or "")
    scratch = "RAG_P_PRB_P1_SEED"
    try:
        try:
            db.create_scratch_profile(scratch, profile_json(db.prof_attrs(prof), {"seed": 7}))
            seed_attr = db.prof_attrs(scratch).get("seed")
            v_seed_prof = f"profile attribute seed accepted (stored as {seed_attr!r})"
            prof_ok = True
        except DBError as e:
            v_seed_prof = "profile attribute seed REJECTED: " + short_error(e)
            prof_ok = False
        out(f"   f CREATE_PROFILE {scratch} with seed 7: {v_seed_prof}")
        if prof_ok:
            r1 = caller.call(db.generate, q, scratch, "narrate")
            r2 = caller.call(db.generate, q, scratch, "narrate")
            same = r1.status == r2.status == "ok" and r1.text == r2.text
            rec["seed_profile_runs"] = {"status": [r1.status, r2.status], "identical": same,
                                        "sha16": [sha16(r1.text), sha16(r2.text)]}
            out(f"   g narrate twice with {scratch}: {r1.status}/{r2.status}, "
                f"{'identical' if same else 'different'} replies ({sha16(r1.text)} / {sha16(r2.text)})")
    finally:
        db.drop_scratch_profile(scratch)
    out("")
    out(f"VERDICT per-call vector_index_name : {v_idx}")
    out(f"VERDICT per-call max_tokens        : {v_tok}")
    out(f"VERDICT per-call seed              : {v_seed_call}")
    out(f"VERDICT seed as a profile attribute: {v_seed_prof}")
    out.finish({"calls": rec, "verdicts": {"vector_index_name": v_idx, "max_tokens": v_tok, "seed_call": v_seed_call,
                                           "seed_profile": v_seed_prof}, "llm_calls": caller.calls})
    db.close()
    return 0


def cmd_p14(a) -> int:
    out = Out("p14", a.out_dir)
    db = DB()
    caller = Caller(min_interval=a.min_interval)
    out("== P14  runsql SCORE vs the harness distance: which formula, so thresholds are set in SCORE units")
    data = {}
    for index, probe in ((P1, "P6"), (P4, "P14")):
        _need_index(db, index, probe)
        prof = rag_profile_for(index)
        _need_profile(db, prof, f"run 04_probes.sql {probe} (it creates it)")
        _, model, metric = db.index_model(index)
        chunks = db.chunks(index)
        by_rid = {c["rid"]: c for c in chunks}
        out("")
        out(f"-- {index} ({metric}, {model}), RAG profile {prof}")
        out(f"   {'q':<5}{'rank':>5}{'SCORE':>11}{'cos dist':>11}{'L2 dist':>11}  matched chunk")
        pairs, per_q = [], []
        for q in PROBE_QUESTIONS:
            res = caller.call(db.generate, q["text"], prof, "runsql")
            if res.status != "ok":
                out(f"   {q['id']:<5}  runsql {res.status}: {res.error}")
                per_q.append({"q": q["id"], "status": res.status, "error": res.error})
                continue
            rows = parse_runsql_rows(res.text)
            dist = db.distances(index, model, q["text"])
            order = sorted(dist, key=lambda rid: (dist[rid][0] if metric != "EUCLIDEAN" else dist[rid][1], rid))
            matched = []
            for i, r in enumerate(rows, 1):
                rid = match_row_to_chunk(r, chunks)
                cd, ed = dist.get(rid, (None, None)) if rid else (None, None)
                if rid and r["score"] is not None:
                    pairs.append((r["score"], cd, ed))
                matched.append(rid)
                out(f"   {q['id']:<5}{i:>5}{'-' if r['score'] is None else format(r['score'], '.6f'):>11}"
                    f"{'-' if cd is None else format(cd, '.6f'):>11}{'-' if ed is None else format(ed, '.6f'):>11}  "
                    + (by_rid[rid]["obj"] if rid else "no unique match"))
            if not rows:
                out(f"   {q['id']:<5}  runsql returned no parseable rows ({len(res.text)} characters)")
            top = [rid for rid in matched if rid]
            same_order = bool(top) and top == order[:len(top)]
            per_q.append({"q": q["id"], "rows": len(rows), "matched": len(top), "harness_order_equal": same_order,
                          "score_key": rows[0]["score_key"] if rows else None})
        fits = fit_score_transforms(pairs)
        best = [f for f in fits if f["fits"]]
        out(f"   {len(pairs)} (SCORE, distance) pairs; formulas ranked by the largest error:")
        for f in fits[:4]:
            out(f"     {f['transform']:<26} max |error| {'-' if f['max_abs_err'] is None else format(f['max_abs_err'], '.2e')}"
                + ("   fits" if f["fits"] else ""))
        verdict = (f"SCORE = {best[0]['transform']}" if best else
                   "no formula fits within 1e-3" if pairs else "not observable: no runsql row matched a chunk")
        out(f"   VERDICT {index}: {verdict}; Select AI order equals the harness order for "
            f"{sum(1 for x in per_q if x.get('harness_order_equal'))} of {len(per_q)} questions")
        data[index] = {"metric": metric, "model": model, "questions": per_q, "fits": fits, "verdict": verdict}
    out.finish({"indexes": data, "llm_calls": caller.calls})
    db.close()
    return 0


def cmd_p16(a) -> int:
    out = Out("p16", a.out_dir)
    db = DB()
    _need_index(db, P1, "P6")
    prof = rag_profile_for(P1)
    _need_profile(db, prof, "run 04_probes.sql P6 (it creates it)")
    _need_profile(db, "RAG_EMB_M1", "run 06_profiles.sql M1 first")
    m0, m1 = MODELS["M0"], MODELS["M1"]
    for m in (m0, m1):
        if not db.model_visible(m):
            raise GuardError(f"ASKORACLE.{m} is not visible to RAG_LAB (03_load_models.sql)")
    _, model, _ = db.index_model(P1)
    if model != m0:
        raise GuardError(f"{P1} must be built on {m0}, not {model}")
    k = int(db.ix_attrs(P1).get("match_limit") or 5)
    pipe = db.pipeline_of(P1)
    orig_profile = _simple((db.ix_attrs(P1).get("profile_name") or "").upper())      # restored exactly
    chunks = db.chunks(P1)
    caller = Caller(min_interval=a.min_interval)
    scratch = "RAG_P_PRB_P1_XM1"
    out("== P16  which profile embeds the query, and a same-dimension mismatch that raises no error")
    out(f"   {P1} stores {m0} vectors (index profile RAG_EMB_M0). {m1} also has 384 dimensions.")
    out(f"   H0 / H1 = the harness's exact top-{k} over {P1} with the query embedded by {m0} / {m1}")
    before = db.vector_checksum(P1)
    h = {q["id"]: (set(db.topk(P1, m0, q["text"], k)), set(db.topk(P1, m1, q["text"], k))) for q in PROBE_QUESTIONS}
    data = {"k": k, "A": [], "B": []}
    try:
        db.create_scratch_profile(scratch, profile_json(db.prof_attrs(prof),
                                                        {"embedding_model": f"database:{MODEL_OWNER}.{m1}"}))
        out("")
        out(f"A. RAG profile {scratch} = {prof} with embedding_model {m1} (deliberately UNPAIRED); index profile unchanged")
        out(f"   {'q':<5}{'paired in H0':>14}{'unpaired in H0':>16}{'unpaired in H1':>16}")
        for q in PROBE_QUESTIONS:
            r_p = caller.call(db.generate, q["text"], prof, "showprompt")
            r_x = caller.call(db.generate, q["text"], scratch, "showprompt")
            s_p = chunks_in_text(chunks, r_p.text or "") if r_p.status == "ok" else set()
            s_x = chunks_in_text(chunks, r_x.text or "") if r_x.status == "ok" else set()
            h0, h1 = h[q["id"]]
            row = {"q": q["id"], "status": [r_p.status, r_x.status], "paired_h0": len(s_p & h0),
                   "x_h0": len(s_x & h0), "x_h1": len(s_x & h1), "found": [len(s_p), len(s_x)],
                   "errors": [r_p.error, r_x.error]}
            data["A"].append(row)
            out(f"   {q['id']:<5}{row['paired_h0']:>11}/{k}{row['x_h0']:>13}/{k}{row['x_h1']:>13}/{k}"
                + ("   " + "; ".join(e for e in row["errors"] if e) if any(row["errors"]) else ""))
        mean = lambda key: sum(r[key] for r in data["A"]) / (len(data["A"]) * k)  # noqa: E731
        if mean("paired_h0") < 0.8:
            v_a = "INCONCLUSIVE: even the paired profile's prompt does not carry the harness top-k"
        elif mean("x_h0") >= 0.8 and mean("x_h0") > mean("x_h1"):
            v_a = ("the INDEX profile embeds the query: the RAG profile's embedding_model is not used for retrieval")
        elif mean("x_h1") >= 0.8:
            v_a = "the RAG profile embeds the query: an unpaired RAG profile retrieves by the other model"
        else:
            v_a = "UNCLEAR: the unpaired profile matches neither harness list"
        out("   VERDICT A: " + v_a)

        out("")
        out(f"B. UPDATE_VECTOR_INDEX {P1} profile_name -> RAG_EMB_M1 (the stored vectors stay {m0}); RAG profile {prof}")
        out(f"   {'q':<5}{'in H0':>8}{'in H1':>8}   status")
        db.update_index(P1, "profile_name", "RAG_EMB_M1")
        stopped = db.stop_pipeline_if_started(pipe)
        if stopped:
            out("   (the update restarted the pipeline; stopped again)")
        for q in PROBE_QUESTIONS:
            r = caller.call(db.generate, q["text"], prof, "showprompt")
            s = chunks_in_text(chunks, r.text or "") if r.status == "ok" else set()
            h0, h1 = h[q["id"]]
            row = {"q": q["id"], "status": r.status, "h0": len(s & h0), "h1": len(s & h1), "error": r.error}
            data["B"].append(row)
            out(f"   {q['id']:<5}{row['h0']:>6}/{k}{row['h1']:>6}/{k}   {r.status}" + (f" {r.error}" if r.error else ""))
        ok_b = [r for r in data["B"] if r["status"] == "ok"]
        if not ok_b:
            v_b = "the switch made every call fail (an error, not a silent mismatch)"
        elif sum(r["h1"] for r in ok_b) / (len(ok_b) * k) >= 0.8:
            v_b = (f"no error, and retrieval now follows {m1} over {m0} vectors: wrong chunks, silently")
        elif sum(r["h0"] for r in ok_b) / (len(ok_b) * k) >= 0.8:
            v_b = "retrieval unchanged: the index profile does not embed the query on this build"
        else:
            v_b = "UNCLEAR: retrieval matches neither harness list"
        out("   VERDICT B: " + v_b)
        data.update(verdict_a=v_a, verdict_b=v_b)
    finally:
        try:
            db.update_index(P1, "profile_name", orig_profile)
            db.stop_pipeline_if_started(pipe)
        finally:
            db.drop_scratch_profile(scratch)
    restored = (db.ix_attrs(P1).get("profile_name") or "").upper() == orig_profile
    unchanged = db.vector_checksum(P1) == before
    out("")
    out(f"   {P1} profile_name restored to {orig_profile}: {'yes' if restored else 'NO - fix before going on'}")
    out(f"   stored vectors unchanged by the switch: {'yes' if unchanged else 'NO - rebuild p1 (P6) before P13/P15'}")
    data.update(restored=restored, vectors_unchanged=unchanged, llm_calls=caller.calls)
    out.finish(data)
    db.close()
    return 0 if restored and unchanged else 2


def load_gold(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    qs = doc.get("questions") if isinstance(doc, dict) else None
    if not isinstance(qs, list) or not qs:
        raise GuardError(f"{path}: no questions list")
    need = ("id", "question", "q_lang", "bucket", "answerable", "facts")
    for q in qs:
        missing = [k for k in need if k not in q]
        if missing:
            raise GuardError(f"{path}: question {q.get('id')} lacks {', '.join(missing)} (gold set v2 expected)")
    ids = [q["id"] for q in qs]
    if len(ids) != len(set(ids)):
        raise GuardError(f"{path}: duplicate question ids")
    return qs


def prior_knowledge_flag(score: dict, question: dict) -> bool:
    """P17: every gold fact produced by chat with no retrieval means the fact is guessable, so
    the question is rewritten before GO-2. Forbidden hits and refusal cues do not clear the
    flag (a hedged or contaminated answer still knew the value). Unanswerable: never flagged."""
    try:
        n_hit, n_all = (int(x) for x in str(score.get("facts_found", "0/0")).split("/"))
    except ValueError:
        return False
    return bool(question.get("answerable")) and n_all > 0 and n_hit == n_all


def read_jsonl_resume(path: str, repair: bool = False) -> list:
    """Records of a JSONL file that a run appends to. Bytes after the last newline are a line cut
    off by a hard kill, not a record: with repair (the caller holds the file's lock) they are
    appended to <path>.torn as evidence and cut from the file, so the next record starts on a
    line of its own; without repair they are skipped. A bad complete line is corruption."""
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
            log.warning("%s: a line cut off by a kill was moved to %s.torn; its call is made again", name, name)
        else:
            log.warning("%s: a line cut off by a kill was skipped", name)
    out = []
    # split on newline bytes only: an answer written with ensure_ascii=False may hold U+2028, which
    # str.splitlines() would treat as a line end; each line is decoded on its own, inside the try
    for n, raw in enumerate(whole.split(b"\n"), 1):
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw.decode("utf-8")))
        except ValueError:                                  # UnicodeDecodeError is a ValueError too
            raise GuardError(f"{os.path.basename(path)}:{n}: corrupt JSONL line (not a cut-off last line); "
                             "look at it before resuming") from None
    return out


def exclusive_lock(path: str, what: str):
    """An exclusive flock on <path>, taken without waiting and held until the returned file is
    closed (or the process ends). Whoever comes second gets GuardError at once (exit 2)."""
    f = open(path, "a", encoding="utf-8")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise GuardError(f"another {what} holds {os.path.basename(path)}: one at a time; "
                         "wait for it to finish (or stop it) and run again") from None
    except OSError as e:
        f.close()
        raise GuardError(f"cannot lock {os.path.basename(path)} ({e.strerror})") from None
    return f


def cmd_p17(a) -> int:
    out = Out("p17", a.out_dir)
    sys.path.insert(0, os.path.join(HERE, "eval"))
    try:
        import eval_norm
    except ImportError as e:
        raise GuardError("eval/eval_norm.py (the answer scorer) is needed for p17") from e
    qs = [q for q in load_gold(a.questions) if q["bucket"] != "D"]          # D: retrieval-only extras
    if a.only:
        keep = {x.strip() for x in a.only.split(",")}
        qs = [q for q in qs if q["id"] in keep]
    if a.limit:
        qs = sorted(qs, key=lambda q: q["id"])[:a.limit]
    by_id = {q["id"]: q for q in qs}
    os.makedirs(a.out_dir, exist_ok=True)
    path = os.path.join(a.out_dir, "p17_chat.jsonl")
    # one p17 at a time over this JSONL, from before the done-set is read until the last line is
    # written: two runs would both see a pair as open and pay for it twice
    lock = exclusive_lock(path + ".lock", "p17 run")
    try:
        return _p17_calls(a, out, eval_norm, qs, by_id, path)
    finally:
        lock.close()


def _p17_calls(a, out, eval_norm, qs, by_id, path) -> int:
    """cmd_p17 under its lock: the chat calls, the JSONL and the summary."""
    db = DB()
    prof = _simple(a.profile.upper())
    _need_profile(db, prof, "run 06_profiles.sql M0 first")
    if db.prof_attrs(prof).get("vector_index_name"):
        raise GuardError(f"{prof} names a vector index; the control needs a profile without retrieval")
    done = set()
    if os.path.exists(path):
        for r in read_jsonl_resume(path, repair=True):          # under the lock: a cut-off line is set aside
            if r.get("status") in ("ok", "no_match"):
                done.add((r["run"], r["id"]))
    caller = Caller(min_interval=a.min_interval, retry_all=True)
    window = collections.deque(maxlen=50)
    out(f"== P17  prior-knowledge control: action chat on {prof} (no retrieval), {len(qs)} questions x {a.runs} runs")
    out(f"   already answered in {os.path.basename(path)}: {len(done)} (skipped); order per run: seeded shuffle, seed = run")
    stopped = None
    with open(path, "a", encoding="utf-8") as jf:
        for run in range(1, a.runs + 1):
            order = sorted(by_id)
            random.Random(run).shuffle(order)
            for qid in order:
                if (run, qid) in done:
                    continue
                if pause_requested(a.pause_file):
                    stopped = "PAUSE file present"
                    break
                q = by_id[qid]
                res = caller.call(db.generate, q["question"], prof, "chat")
                window.append(res.status == "infra_error")
                rec = {"utc": utc_stamp(), "run": run, "id": qid, "q_lang": q["q_lang"], "bucket": q["bucket"],
                       "answerable": bool(q["answerable"]), "status": res.status, "ms": res.ms,
                       "attempts": res.attempts, "error": res.error, "answer": res.text}
                flag, verdict, ff = False, "-", "-"
                if res.status == "ok":
                    sc = eval_norm.score_answer(res.text, q)
                    verdict, ff = sc["verdict"], sc["facts_found"]
                    flag = prior_knowledge_flag(sc, q)
                    rec.update(verdict=verdict, facts_found=ff, flag=flag)
                jf.write(json.dumps(redact_data(rec), ensure_ascii=False) + "\n")
                jf.flush()
                out(f"   run {run}  {qid:<10}{q['q_lang']:<4}{res.status:<12}facts {ff:<6}{verdict:<15}"
                    + ("FLAG" if flag else ""))
                # more than 5 % of a 50-call window, i.e. 3 infra errors among the last 50 calls; it
                # can fire before 50 calls exist (the same rule as eval_rag.InfraWindow)
                if sum(window) > 0.05 * window.maxlen:
                    stopped = "infra errors above 5 % in the last 50 calls"
                    break
            if stopped:
                break
    # summary over everything recorded so far (this and earlier sessions)
    per_q = collections.defaultdict(lambda: {"runs": 0, "flags": 0})
    for r in read_jsonl_resume(path):
        if r.get("status") == "ok" and r["id"] in by_id:
            per_q[r["id"]]["runs"] += 1
            per_q[r["id"]]["flags"] += bool(r.get("flag"))
    flagged = sorted(qid for qid, v in per_q.items() if v["flags"] > 0)
    out("")
    out(f"   answered: {sum(v['runs'] for v in per_q.values())} of {len(qs) * a.runs} (question, run) pairs")
    out(f"   FLAGGED (every fact produced without retrieval in at least one run): {len(flagged)}")
    for qid in flagged:
        out(f"     {qid:<10}{by_id[qid]['q_lang']:<4}{per_q[qid]['flags']} of {per_q[qid]['runs']} runs")
    out("   flagged questions are rewritten, logged and the gold set re-sealed before GO-2 (PLAN.md 5.1, 8.1)")
    write_atomic(os.path.join(a.out_dir, "p17_flagged.json"),
                 json.dumps({"utc": utc_stamp(), "profile": prof, "flagged": flagged,
                             "per_question": per_q}, ensure_ascii=False, indent=1))
    out.finish({"flagged": flagged, "stopped": stopped, "llm_calls": caller.calls})
    db.close()
    if stopped:
        log.error("stopped: %s (re-run p17 to resume)", stopped)
        return 4
    return 0


# =============================================================================================
# command line
# =============================================================================================
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Brief 09 probes that need Python (PLAN.md 8.1).")
    ap.add_argument("-v", "--verbose", action="store_true", help="INFO logging on stderr")
    ap.add_argument("--out-dir", default=DEFAULT_OUT, help="where <probe>.txt/.json go (default results/probes)")
    ap.add_argument("--no-save", action="store_true", help="stdout only")
    ap.add_argument("--corpus-dir", default=CORPUS_DIR,
                    help="where corpus/ (src, pdf, docx, probe) lives when not next to code/")
    ap.add_argument("--results-dir", default=DEFAULT_RESULTS,
                    help="run_all.sh's RESULTS_DIR: model_parity.csv, models/<KEY>_refvec.json, probes/p0.status")
    sub = ap.add_subparsers(dest="probe", required=True)
    sub.add_parser("files", help="list the probe files, their origin, size and sha256 (no DB)")
    s = sub.add_parser("p0-df", help="free space on the database volume (host, no DB)")
    s.add_argument("--path", required=True)
    s = sub.add_parser("p0-status", help="P0 transcript -> results/probes/p0.status, the MODELS gate (no DB)")
    s.add_argument("--transcript", required=True, help="transcript of 04_probes.sql P0 (or of the ad-hoc check)")
    s.add_argument("--adhoc", action="store_true", help="the transcript is the 29-Sep read-only ad-hoc check")
    s.add_argument("--run-utc", default="", help="--adhoc only: when that check ran, YYYY-MM-DDTHH:MM:SSZ")
    s = sub.add_parser("p2", help="DB-vs-local parity of one model on the 20 token-id strings (no LLM)")
    s.add_argument("--key", required=True, help="M0..M6 or M1Q")
    s.add_argument("--refvec", default="", help="default: <results-dir>/models/<KEY>_refvec.json")
    s = sub.add_parser("stage-dir", help="assemble one stage's probe files for 01_stage_files.sh (no DB)")
    s.add_argument("--stage", choices=("initial", "p13"), required=True)
    s.add_argument("--dir", required=True, help="an empty local directory (absolute path)")
    s = sub.add_parser("make-noto-control", help="build the P5 negative control PDF (no DB)")
    s.add_argument("--out", default=DEFAULT_NOTO_OUT)
    s.add_argument("--chrome", default=os.environ.get("CHROME_BIN", bc.DEFAULT_CHROME))
    s = sub.add_parser("p4", help="truncation window per model x language")
    s.add_argument("--models", default="", help="comma-separated keys, default all")
    s.add_argument("--sample-chars", type=int, default=12000)
    s.add_argument("--eps", type=float, default=1e-6)
    sub.add_parser("p5", help="extraction recall and the PDF-or-DOCX decision")
    for name, text in (("p7", "runsql/showprompt/narrate shape"), ("p8", "SELECT AI vs GENERATE cache"),
                       ("p11", "GENERATE attributes override and seed"), ("p14", "SCORE vs distance"),
                       ("p16", "which profile embeds the query")):
        s = sub.add_parser(name, help=text)
        s.add_argument("--min-interval", type=float, default=1.0)
    s = sub.add_parser("p17", help="chat-action prior-knowledge control over the gold set (~324 calls)")
    s.add_argument("--questions", default=DEFAULT_QUESTIONS)
    s.add_argument("--profile", default="RAG_EMB_M0")
    s.add_argument("--runs", type=int, default=3)
    s.add_argument("--only", default="")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--min-interval", type=float, default=1.0)
    s.add_argument("--pause-file", default=DEFAULT_PAUSE)
    return ap


COMMANDS = {"files": cmd_files, "p0-df": cmd_p0_df, "p0-status": cmd_p0_status, "p2": cmd_p2, "stage-dir": cmd_stage_dir,
            "make-noto-control": cmd_make_noto_control, "p4": cmd_p4, "p5": cmd_p5, "p7": cmd_p7, "p8": cmd_p8,
            "p11": cmd_p11, "p14": cmd_p14, "p16": cmd_p16, "p17": cmd_p17}


def main(argv=None) -> int:
    global CORPUS_DIR
    a = build_parser().parse_args(argv)
    CORPUS_DIR = os.path.abspath(a.corpus_dir)
    a.results_dir = os.path.abspath(a.results_dir)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    if a.no_save:
        a.out_dir = None if a.probe != "p17" else a.out_dir      # p17 needs its jsonl to resume
    if getattr(a, "min_interval", 1.0) < 1.0:
        log.error("--min-interval below 1 s is not allowed (OCI quota is shared with the existing HR app)")
        return 2
    if getattr(a, "runs", 1) < 1 or getattr(a, "sample_chars", 12000) < 100:
        log.error("--runs must be >= 1 and --sample-chars >= 100")
        return 2
    try:
        return COMMANDS[a.probe](a)
    except GuardError as e:
        log.error("%s", redact(e))
        return 2
    except DBError as e:
        log.error("database error: %s", short_error(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
