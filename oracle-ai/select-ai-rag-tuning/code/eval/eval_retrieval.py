#!/usr/bin/env python3
# v1.5 - RAG tuning lab: deterministic retrieval layer (no LLM) over a lab vector index.
#        v1.5 (30-Sep, GO-3): Caller returns status "declined" at once for Select AI's ORA-20000
#              "Sorry, unfortunately ..." refusal (eval_norm.DECLINED) instead of retrying it as infra.
#        v1.4 (30-Sep, GO-2): runsql agreement compared at min(5, rows Select AI returned), since a
#              profile with match_limit 3 or 4 returns 3 or 4 rows (supp_v24 read 0.6 / 0.8 for full agreement).
#        v1.3 (Codex adversarial review): connect_from_env reads the password from RAG_LAB_PWD_FILE
#              first (a 0600 file of this user's, as run_all.sh writes it), then RAG_LAB_PWD, then a
#              prompt; both variables leave the environment once read (eval_rag.py uses the same).
#        v1.2 (review 1, 29-Sep): masking and the gold/non-gold lookups compare the basename of
#              $.object_name, upper-cased, on both sides (regexp_substr in SQL, the same rule in
#              Corpus.doc_of), and every masked or restricted search is checked afterwards (a
#              masked twin in the hits raises GuardError); a label span (span_has_fact=false)
#              needs its fact in the same line or table row; bucket D stays out of the pooled
#              ALL rows and the threshold calibration (reported in its own rows); MRR split into
#              doc_mrr@10 and ev_mrr@10; best_evidence recorded and used for calibration (doc-level
#              fallback counted); context_ok checked in load_questions.
#        v1.1: after Codex review: index content fingerprint in the stats guard; experiments row
#              allowlisted; host/DSN redaction; span_docs honoured (a span counts only in its own
#              twin, and a span with span_has_fact=false needs a fact value in the same chunk);
#              chunk_coverage embeds substr(content) server-side; torn JSONL tolerated.
#        v1.0: first version (PLAN.md 5.2): exact search, masked twins, evidence-span metrics
#              with the containable ceiling, MRR@10, twin-first, best-gold vs best-non-gold,
#              metric-identity check, in-session and rebuild determinism, runsql cross-check hook.
#
# Run as : RAG_LAB (python-oracledb thin), on the host that reaches the PDB.
# Usage  : RAG_LAB_DSN=localhost:1521/orclpdb1 python3 eval_retrieval.py --config <config_id>
#            [--index RAG_M0_C1024_O128_COS --rag-profile RAG_P_M0_C1024_O128_COS]
#            [--split dev|test|all] [--only ID,ID] [--repeat 2] [--metric-check]
#            [--runsql N|all] [--validate-spans] [--out ../../results]
#          python3 eval_retrieval.py compare A.jsonl B.jsonl      (rebuild determinism, no DB)
#          The password comes from RAG_LAB_PWD_FILE (path of a file owned by this user, mode 600
#          or tighter, one line; run_all.sh passes one), else RAG_LAB_PWD, else a hidden prompt;
#          it is never logged or written.
# Re-run : safe. Read-only against the database (SELECT and, with --runsql, GENERATE 'runsql').
#          Every run writes a new results/retrieval_<INDEX>_<UTC>.jsonl; nothing is overwritten.
# Exit   : 0 ok | 2 precondition/guard failed | 3 stopped (PAUSE file / infra window)
#          4 determinism or index-stats mismatch | 5 evidence span missing (--validate-spans)
#
# Guards (fail closed): identifiers regex + DBMS_ASSERT; index name RAG_*; the query model is
# read from the dictionary (index -> profile_name -> embedding_model) and must equal the RAG
# profile's embedding_model (PAIRING GUARD, PLAN.md 0.4); owner must be ASKORACLE; the model
# and metric must match the index name; $VECTAB column list checked; chunk count,
# sum(length(content)) and a content fingerprint (sha256 over sorted object_name + sha256(content))
# must equal the last recorded run of the same index.
from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import hashlib
import json
import logging
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from eval_norm import DECLINED, NO_MATCH, fact_present, span_key  # noqa: E402
import stats as st                        # noqa: E402

log = logging.getLogger("eval_retrieval")
VERSION = "eval_retrieval.py v1.3"

DEFAULT_QUESTIONS = os.path.join(HERE, "questions.json")
DEFAULT_MANIFEST = os.path.join(HERE, "..", "..", "corpus", "manifest.csv")
DEFAULT_EXPERIMENTS = os.path.join(HERE, "..", "experiments.csv")
DEFAULT_OUT = os.path.join(HERE, "..", "..", "results")
DEFAULT_PAUSE = os.path.join(HERE, "..", "PAUSE")

TOP = 20                                   # FETCH EXACT FIRST 20 ROWS ONLY
K_LIST = (1, 3, 5, 6, 8, 10, 12, 20)       # PLAN.md 5.2
KEEP_CONTENT = 5                           # full chunk text kept for ranks 1..5 (screenshots, runsql)
TIE_TOL = 1e-6
EVIDENCE_LOOKUP = 100                      # restricted search for the best evidence chunk below TOP
ROW_LINES, ROW_CHARS = 8, 400              # label span -> value: the rest of its table row (see fact_in_row)
SPAN_LINES = 8                             # a <= 100-character span may wrap over this many lines
RETRIEVAL_ONLY_BUCKETS = ("D",)            # dialect paraphrases: own rows only, never pooled or calibrated
MODEL_OWNER = "ASKORACLE"
MODEL_KEYS = {"M0": "ALL_MINILM_L12_V2", "M1": "MULTILINGUAL_E5_SMALL", "M2": "MULTILINGUAL_E5_BASE",
              "M3": "MULTILINGUAL_E5_LARGE", "M4": "BGE_M3", "M5": "ARCTIC_EMBED_L_V2",
              "M6": "ARABIC_TRIPLET_V2", "M1Q": "MULTILINGUAL_E5_SMALL_Q"}
METRIC_SUFFIX = {"COS": "COSINE", "DOT": "DOT", "EUC": "EUCLIDEAN", "MAN": "MANHATTAN"}
METRICS = ("COSINE", "DOT", "EUCLIDEAN", "EUCLIDEAN_SQUARED", "MANHATTAN")
INDEX_NAME = re.compile(r"^RAG_(M[0-9]Q?)_C([0-9]+)_O([0-9]+)_(COS|DOT|EUC|MAN)$")
SIMPLE = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")
LAB_INDEX = re.compile(r"^RAG_[A-Z0-9_]{1,100}$")
EMB_MODEL = re.compile(r"^database:([A-Za-z][A-Za-z0-9_$#]{0,127})\.([A-Za-z][A-Za-z0-9_$#]{0,127})$", re.I)
EXPECTED_COLUMNS = {"CONTENT": "CLOB", "ATTRIBUTES": "JSON", "EMBEDDING": "VECTOR"}
EXPERIMENT_FIELDS = ("config_id", "stage", "model_key", "model_name", "chunk_size", "chunk_overlap",
                     "metric", "match_limit", "similarity_threshold", "corpus_format", "index_name",
                     "rag_profile", "answer_layer", "status")          # never free-text notes
RAG_WHITELIST = ("provider", "model", "embedding_model", "vector_index_name", "conversation",
                 "temperature", "max_tokens", "seed", "enable_sources")   # never region/credential/compartment


class GuardError(Exception):
    """A precondition failed: stop before measuring anything (exit 2)."""


class StopRun(Exception):
    """PAUSE file present or infra-error window exceeded (exit 3); resumable."""


class DBCallError(Exception):
    """A database call failed. The message is the driver's text (first line); never a secret."""


# ---------------------------------------------------------------------------------------------
# identifiers and SQL (pure; unit-tested)
# ---------------------------------------------------------------------------------------------
def simple_name(name: str) -> str:
    n = (name or "").strip().upper()
    if not SIMPLE.match(n):
        raise GuardError(f"not a simple Oracle name: {name!r}")
    return n


def lab_index_name(name: str) -> str:
    n = simple_name(name)
    if not LAB_INDEX.match(n):
        raise GuardError(f"not a lab index name (RAG_*): {name!r}")
    return n


def metric_keyword(metric: str) -> str:
    m = (metric or "").strip().strip('"').upper()
    if m not in METRICS:
        raise GuardError(f"unsupported distance metric {metric!r}")
    return m


def search_sql(index, owner, model, metric, n_exclude=0, n_include=0, limit=TOP) -> str:
    """Exact similarity search over <index>$VECTAB (PLAN.md 5.2, [D11]). Only validated names are
    spliced; the question and every object name are binds (:q, :x0.., :g0..). The query vector
    is computed once (materialised CTE). Ties break on object_name, then rowid."""
    index, owner, model = lab_index_name(index), simple_name(owner), simple_name(model)
    metric = metric_keyword(metric)
    limit = int(limit)
    if not 1 <= limit <= 100:
        raise GuardError("limit must be 1..100")
    if n_exclude and n_include:
        raise GuardError("one filter at a time")
    where = ""
    # v1.2: compare the file name only, case-insensitively, as Corpus.doc_of does: $.object_name
    # may carry a directory prefix or another case on 23.26.1 (PROBES.md: unverified until P7/GO-1)
    base = "upper(regexp_substr(d.obj, '[^/\\\\]+$'))"
    if n_exclude:
        where = "\n where " + base + " not in (" + ", ".join(f"upper(:x{i})" for i in range(n_exclude)) + ")"
    elif n_include:
        where = "\n where " + base + " in (" + ", ".join(f"upper(:g{i})" for i in range(n_include)) + ")"
    return ("with qe as (select /*+ materialize */ vector_embedding(" + owner + "." + model +
            " using :q as data) qv from dual)\n"
            "select d.obj, rowidtochar(d.rid) rid, d.dist, d.content\n"
            "  from (select json_value(v.attributes, '$.object_name' returning varchar2(1024)) obj,\n"
            "               v.rowid rid,\n"
            "               vector_distance(v.embedding, qe.qv, " + metric + ") dist,\n"
            "               v.content\n"
            '          from "' + index + '$VECTAB" v cross join qe) d' + where + "\n"
            " order by d.dist, d.obj, d.rid\n"
            " fetch exact first " + str(limit) + " rows only")


def redact(msg: str) -> str:
    """First line of a driver/HTTP error with anything host- or tenancy-specific masked."""
    s = (str(msg) or "").strip().splitlines()[0] if msg else ""
    s = re.sub(r"ocid1\.[\w.\-]+", "<ocid>", s)
    s = re.sub(r"opc-request-i[d]\S*\s*[:=]?\s*\S+", "<request-id>", s, flags=re.I)   # [d]: keeps leak_scan quiet
    s = re.sub(r"(?:https?|file)://\S+", "<url>", s)
    s = re.sub(r"\S*oraclecloud\.com\S*", "<host>", s)
    s = re.sub(r"\(\s*host\s*=[^)]*\)", "(HOST=<host>)", s, flags=re.I)
    s = re.sub(r"\bhost\s*[=:]\s*[^\s,;)]+", "host=<host>", s, flags=re.I)
    s = re.sub(r"\b[\w.-]+:\d{2,5}/[\w.$#-]+", "<dsn>", s)
    s = re.sub(r"\b(?:[a-z0-9-]+\.){2,}[a-z]{2,}\b", "<host>", s, flags=re.I)
    s = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", s)
    s = re.sub(r"\b[a-z]+-[a-z]+-[0-9]\b", "<region>", s)
    return s[:300]


# ---------------------------------------------------------------------------------------------
# the database interface: OracleLabDB is the only class that talks to Oracle. Tests pass a fake
# object with the same methods.
# ---------------------------------------------------------------------------------------------
class OracleLabDB:
    def __init__(self, connect, call_timeout_ms=180_000):
        import oracledb                         # imported here so tests never need the driver
        self._ora = oracledb
        oracledb.defaults.fetch_lobs = False    # CLOBs arrive as full str (CONTENT, GENERATE)
        self._connect = connect
        self._timeout = call_timeout_ms
        self.conn = None
        self._open()

    def _open(self):
        try:
            self.conn = self._connect()
        except self._ora.Error as e:
            raise GuardError(f"cannot connect: {redact(e)}") from e
        self.conn.call_timeout = self._timeout

    def _rows(self, sql, binds=None):
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, binds or {})
                return cur.fetchall()
        except self._ora.Error as e:
            full = str(e)
            try:
                healthy = self.conn.is_healthy()
            except self._ora.Error:
                healthy = False
            if not healthy:                     # a call timeout can leave a thin connection unusable
                log.warning("event=reconnect reason=%s", redact(full))
                self._open()
            raise DBCallError(full) from e

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except self._ora.Error as e:
                log.warning("event=close_failed error=%s", redact(e))

    def assert_simple_name(self, name):
        r = self._rows("select dbms_assert.simple_sql_name(:n) from dual", {"n": name})
        if not r or r[0][0] != name:
            raise GuardError(f"DBMS_ASSERT rejected {name!r}")
        return name

    def index_attributes(self, index):
        r = self._rows("select attribute_name, dbms_lob.substr(attribute_value, 4000, 1) "
                       "from user_cloud_vector_index_attributes where index_name = :i", {"i": index})
        return {str(k).lower(): v for k, v in r}

    def profile_attributes(self, profile):
        r = self._rows("select attribute_name, dbms_lob.substr(attribute_value, 4000, 1) "
                       "from user_cloud_ai_profile_attributes where profile_name = upper(:p)",
                       {"p": profile})
        return {str(k).lower(): v for k, v in r}

    def vectab_columns(self, index):
        return [(c, t) for c, t in self._rows(
            "select column_name, data_type from user_tab_columns where table_name = :t "
            "order by column_id", {"t": lab_index_name(index) + "$VECTAB"})]

    def index_stats(self, index):
        r = self._rows(
            "select count(*), nvl(sum(length(content)), 0), count(distinct obj), "
            "nvl(sum(case when obj is null then 1 else 0 end), 0) "
            "from (select json_value(v.attributes, '$.object_name' returning varchar2(1024)) obj, "
            'v.content from "' + lab_index_name(index) + '$VECTAB" v)')[0]
        return {"chunks": int(r[0]), "content_chars": int(r[1]), "objects": int(r[2]),
                "null_objects": int(r[3])}

    def pipeline_status(self, pipeline):
        r = self._rows("select status from user_cloud_pipelines where pipeline_name = upper(:p)",
                       {"p": pipeline})
        return r[0][0] if r else None

    def all_chunks(self, index):
        r = self._rows("select json_value(v.attributes, '$.object_name' returning varchar2(1024)) obj, "
                       "rowidtochar(v.rowid) rid, v.content "
                       'from "' + lab_index_name(index) + '$VECTAB" v order by 1, v.rowid')
        return [{"obj": o, "rid": i, "content": c or ""} for o, i, c in r]

    def search(self, index, owner, model, metric, text, exclude=(), include=(), limit=TOP):
        sql = search_sql(index, owner, model, metric, len(exclude), len(include), limit)
        binds = {"q": text}
        binds.update({f"x{i}": v for i, v in enumerate(exclude)})
        binds.update({f"g{i}": v for i, v in enumerate(include)})
        return [{"obj": o, "rid": i, "dist": float(d), "content": c or ""}
                for o, i, d, c in self._rows(sql, binds)]

    def generate(self, prompt, profile, action):
        if action not in ("narrate", "runsql", "showprompt", "chat"):
            raise GuardError(f"action {action!r} not allowed")
        r = self._rows("select dbms_cloud_ai.generate(prompt => :p, profile_name => :pr, "
                       "action => :a) from dual", {"p": prompt, "pr": profile, "a": action})
        return (r[0][0] or "") if r else ""

    def embedding_distance(self, index, owner, model, rid, n_chars, metric="COSINE"):
        """Distance between the stored embedding of one chunk and the model's embedding of the
        first n_chars characters of the same stored CONTENT. The prefix is cut in SQL (SUBSTR on
        the CLOB), so no text crosses the wire and no 4,000-byte bind limit applies."""
        sql = ("select vector_distance(v.embedding, vector_embedding(" + simple_name(owner) + "." +
               simple_name(model) + " using substr(v.content, 1, :n) as data), " +
               metric_keyword(metric) + ") " +
               'from "' + lab_index_name(index) + '$VECTAB" v where v.rowid = chartorowid(:r)')
        r = self._rows(sql, {"n": int(n_chars), "r": rid})
        if not r:
            raise DBCallError(f"rowid {rid} not found")
        return float(r[0][0])


PWD_FILE_VAR = "RAG_LAB_PWD_FILE"


def read_password_file(path: str) -> str:
    """The password held in <path>: a regular file (no symlink) owned by this OS user and closed
    to group and others, one line. run_all.sh writes one per run (0600) and shreds it on exit.
    Error messages never show the path or the content."""
    import stat as stat_mod                  # module level 'st' is stats.py
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        raise GuardError(f"{PWD_FILE_VAR}: cannot open the password file ({e.strerror})") from None
    try:
        info = os.fstat(fd)
        if not stat_mod.S_ISREG(info.st_mode):
            raise GuardError(f"{PWD_FILE_VAR}: not a regular file")
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
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


def lab_password(prompt=None) -> str:
    """RAG_LAB_PWD_FILE first, then RAG_LAB_PWD, then a hidden prompt. Both variables leave this
    process's environment here, so no later child inherits them."""
    path = os.environ.pop(PWD_FILE_VAR, None)
    pwd = os.environ.pop("RAG_LAB_PWD", None)
    if path:
        return read_password_file(path)
    if pwd:
        return pwd
    if prompt is None:
        import getpass
        prompt = getpass.getpass
    return prompt("RAG_LAB password: ")


def connect_from_env():
    """Connection factory: RAG_LAB on RAG_LAB_DSN; password from lab_password() (the file, the
    variable or a prompt). The password lives only in this closure."""
    import oracledb
    dsn = os.environ.get("RAG_LAB_DSN", "localhost:1521/orclpdb1")
    pwd = lab_password()

    def _connect():
        return oracledb.connect(user="RAG_LAB", password=pwd, dsn=dsn)
    return _connect


# ---------------------------------------------------------------------------------------------
# serial GENERATE calls: >= min_interval apart, infra errors retried after 5/15/45 s (cap 3)
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass
class CallResult:
    status: str               # ok | no_match | declined | infra_error
    text: str | None
    error: str | None
    attempts: int
    latency_ms: int


class Caller:
    def __init__(self, db, min_interval=1.0, backoff=(5, 15, 45), sleep=time.sleep,
                 clock=time.monotonic):
        self.db, self.min_interval, self.backoff = db, float(min_interval), tuple(backoff)
        self.sleep, self.clock = sleep, clock
        self._last = None

    def _pace(self):
        if self._last is not None:
            gap = self.clock() - self._last
            if gap < self.min_interval:
                self.sleep(self.min_interval - gap)
        self._last = self.clock()

    def call(self, prompt, profile, action) -> CallResult:
        err, attempts, ms = None, 0, 0
        for wait in (0,) + self.backoff:
            if wait:
                log.info("event=backoff seconds=%s attempt=%d", wait, attempts + 1)
                self.sleep(wait)
            self._pace()
            attempts += 1
            t0 = self.clock()
            try:
                text = self.db.generate(prompt, profile, action)
                return CallResult("ok", text or "", None, attempts, int((self.clock() - t0) * 1000))
            except DBCallError as e:
                ms = int((self.clock() - t0) * 1000)
                if NO_MATCH.search(str(e)):      # ORA-20000 no match: a refusal, not an infra error
                    return CallResult("no_match", None, redact(e), attempts, ms)
                if DECLINED.search(str(e)):      # v1.5: Select AI declined an ungrounded answer: a refusal
                    return CallResult("declined", None, redact(e), attempts, ms)
                err = redact(e)
                log.warning("event=infra_error profile=%s attempt=%d error=%s", profile, attempts, err)
        return CallResult("infra_error", None, err, attempts, ms)


def pause_requested(path) -> bool:
    return bool(path) and os.path.exists(path)


# ---------------------------------------------------------------------------------------------
# corpus manifest, gold questions, experiments.csv
# ---------------------------------------------------------------------------------------------
class Corpus:
    """Document ids <-> file names (object_name) <-> language, from corpus/manifest.csv. v1.2:
    file names are matched case-insensitively (the SQL filter upper-cases both sides too)."""

    def __init__(self, rows):
        self.lang, self.files, self.by_file = {}, {}, {}
        for r in rows:
            d = r["id"].strip()
            self.lang[d] = r["lang"].strip().lower()
            self.files[d] = [f.strip() for f in (r.get("pdf"), r.get("docx")) if f and f.strip()]
            for f in self.files[d]:
                if self.by_file.get(f.upper(), d) != d:
                    raise GuardError(f"manifest file names collide ignoring case: {f} ({d} and "
                                     f"{self.by_file[f.upper()]})")
                self.by_file[f.upper()] = d

    @classmethod
    def from_csv(cls, path):
        with open(path, newline="", encoding="utf-8") as f:
            return cls(list(csv.DictReader(f)))

    def doc_of(self, object_name):
        if not object_name:
            return None
        return self.by_file.get(object_name.replace("\\", "/").rsplit("/", 1)[-1].upper())

    def twin(self, doc):
        m = re.match(r"^(GLF-[0-9]{3})-(EN|AR)$", doc or "")
        if not m:
            return None
        t = f"{m.group(1)}-{'AR' if m.group(2) == 'EN' else 'EN'}"
        return t if t in self.lang else None

    def files_of(self, docs):
        out = []
        for d in docs:
            out.extend(self.files.get(d, []))
        return sorted(set(out))


REQUIRED = ("id", "fact_id", "q_lang", "doc_lang", "bucket", "split", "question", "gold_docs",
            "twin_of", "evidence_spans", "facts", "forbidden", "answerable", "tags")
BUCKETS = ("T", "S-EN", "S-AR", "U", "D")


def questions_digest(questions) -> str:
    """sha256 of the canonical question list (sorted keys, compact, UTF-8)."""
    blob = json.dumps(questions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_questions(path, corpus=None):
    """Structural checks the harness depends on (authoring rules live in test_questions.py).
    Returns (meta, questions, seal) where seal = {file_sha256, digest, sealed, ok}."""
    with open(path, "rb") as f:
        raw = f.read()
    data = json.loads(raw.decode("utf-8"))
    qs = data.get("questions")
    if not isinstance(qs, list) or not qs:
        raise GuardError(f"{path}: no questions")
    errs, seen = [], set()
    for q in qs:
        qid = q.get("id", "?")
        miss = [k for k in REQUIRED if k not in q]
        if miss:
            errs.append(f"{qid}: missing {miss}")
            continue
        n_before = len(errs)
        if qid in seen:
            errs.append(f"{qid}: duplicate id")
        seen.add(qid)
        if q["q_lang"] not in ("en", "ar"):
            errs.append(f"{qid}: q_lang {q['q_lang']!r}")
        if q["bucket"] not in BUCKETS:
            errs.append(f"{qid}: bucket {q['bucket']!r}")
        if q["split"] not in ("dev", "test"):
            errs.append(f"{qid}: split {q['split']!r}")
        if not isinstance(q["answerable"], bool):
            errs.append(f"{qid}: answerable must be a bool")
        if q["answerable"] and (not q["gold_docs"] or not q["evidence_spans"] or not q["facts"]):
            errs.append(f"{qid}: answerable question needs gold_docs, evidence_spans and facts")
        if not q["answerable"] and q["gold_docs"]:
            errs.append(f"{qid}: unanswerable question has gold_docs")
        for s in q["evidence_spans"] or []:
            if not isinstance(s, str) or not span_key(s) or len(s) > 100:
                errs.append(f"{qid}: bad evidence span {s!r}")
        for alt in q["facts"] or []:
            if not isinstance(alt, list) or not alt:
                errs.append(f"{qid}: each fact is a non-empty list of alternatives")
        ctx = q.get("context_ok")
        if ctx is not None and (not isinstance(ctx, list) or any(v not in (q["forbidden"] or []) for v in ctx)):
            errs.append(f"{qid}: context_ok must be a list of values that are also forbidden")
        sd = q.get("span_docs")
        if sd is not None and (len(sd) != len(q["evidence_spans"] or [])
                               or (corpus is not None and any(d not in _full_gold(q, corpus) for d in sd))):
            errs.append(f"{qid}: span_docs must name one gold document per span")
        shf = q.get("span_has_fact")
        if shf is not None and (len(shf) != len(q["evidence_spans"] or [])
                                or not all(isinstance(x, bool) for x in shf)):
            errs.append(f"{qid}: span_has_fact must hold one bool per span")
        if corpus is not None:
            for d in q["gold_docs"]:
                if d not in corpus.lang:
                    errs.append(f"{qid}: gold doc {d} not in the manifest")
            if q["bucket"] in ("T", "D") and q["answerable"] and len(errs) == n_before:
                langs = {corpus.lang[x] for x in _full_gold(q, corpus)}
                if langs != {"en", "ar"}:
                    errs.append(f"{qid}: twin bucket needs an EN and an AR gold document")
    if errs:
        raise GuardError("questions.json failed validation:\n  " + "\n  ".join(errs[:30]))
    digest = questions_digest(qs)
    sealed = data.get("sealed_sha256_of_questions")
    seal = {"file_sha256": hashlib.sha256(raw).hexdigest(), "digest": digest, "sealed": sealed,
            "ok": bool(sealed) and sealed == digest}
    return {k: v for k, v in data.items() if k != "questions"}, qs, seal


def select_questions(qs, split="all", only=""):
    keep = {x.strip() for x in only.split(",") if x.strip()} if only else None
    return [q for q in qs if (split == "all" or q["split"] == split) and (keep is None or q["id"] in keep)]


def experiment_row(path, config_id):
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("config_id") == config_id]
    if len(rows) != 1:
        raise GuardError(f"{path}: expected one row for config_id {config_id!r}, found {len(rows)}")
    return rows[0]


def truthy(v) -> bool:
    return str(v or "").strip().lower() in ("y", "yes", "true", "1", "x")


# ---------------------------------------------------------------------------------------------
# pairing guard (PLAN.md 0.4 / 2.4)
# ---------------------------------------------------------------------------------------------
def clean(v) -> str:
    s = "" if v is None else str(v).strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        s = s[1:-1].strip()
    return s


def parse_embedding_model(value):
    m = EMB_MODEL.match(clean(value))
    if not m:
        raise GuardError(f"embedding_model is not 'database:OWNER.MODEL': {value!r}")
    return m.group(1).upper(), m.group(2).upper()


def _num(v):
    s = clean(v)
    if not s:
        return None
    try:
        return int(s) if re.fullmatch(r"-?[0-9]+", s) else float(s)
    except ValueError:
        return None


@dataclasses.dataclass
class Pairing:
    index: str
    index_profile: str
    rag_profile: str
    embedding_model: str
    owner: str
    model: str
    metric: str
    match_limit: object
    similarity_threshold: object
    pipeline_name: str | None
    rag_attributes: dict


def resolve_pairing(db, index, rag_profile, expected_owner=MODEL_OWNER, expected_model=None) -> Pairing:
    index, rag_profile = lab_index_name(index), simple_name(rag_profile)
    db.assert_simple_name(index)
    db.assert_simple_name(rag_profile)
    ia = db.index_attributes(index)
    if not ia:
        raise GuardError(f"{index}: not found in USER_CLOUD_VECTOR_INDEX_ATTRIBUTES")
    if not clean(ia.get("profile_name")):
        raise GuardError(f"{index}: no profile_name attribute")
    idx_prof = simple_name(clean(ia.get("profile_name")))
    em_idx = clean(db.profile_attributes(idx_prof).get("embedding_model"))
    ra = db.profile_attributes(rag_profile)
    if not ra:
        raise GuardError(f"RAG profile {rag_profile} not found")
    em_rag = clean(ra.get("embedding_model"))
    if not em_idx or em_idx != em_rag:
        raise GuardError(f"PAIRING GUARD: index profile {idx_prof} embeds with {em_idx!r} but RAG "
                         f"profile {rag_profile} has {em_rag!r}")
    vin = clean(ra.get("vector_index_name")).upper()
    if vin != index:
        raise GuardError(f"RAG profile {rag_profile} points at {vin!r}, not {index}")
    owner, model = parse_embedding_model(em_idx)
    if owner != expected_owner:
        raise GuardError(f"embedding model owner {owner} is not {expected_owner}")
    m = INDEX_NAME.match(index)
    if m and MODEL_KEYS.get(m.group(1)) != model:
        raise GuardError(f"{index}: key {m.group(1)} means {MODEL_KEYS.get(m.group(1))}, "
                         f"dictionary says {model}")
    if expected_model and expected_model.strip().upper() != model:
        raise GuardError(f"experiments.csv says {expected_model}, dictionary says {model}")
    if ia.get("vector_distance_metric"):
        metric = metric_keyword(clean(ia["vector_distance_metric"]))
    else:
        metric = METRIC_SUFFIX[m.group(4)] if m else "COSINE"
        log.warning("event=metric_not_in_dictionary index=%s assumed=%s", index, metric)
    if m and METRIC_SUFFIX[m.group(4)] != metric:
        raise GuardError(f"{index}: name says {METRIC_SUFFIX[m.group(4)]}, dictionary says {metric}")
    db.assert_simple_name(owner)
    db.assert_simple_name(model)
    return Pairing(index=index, index_profile=idx_prof, rag_profile=rag_profile, embedding_model=em_idx,
                   owner=owner, model=model, metric=metric,
                   match_limit=_num(ia.get("match_limit")),
                   similarity_threshold=_num(ia.get("similarity_threshold")),
                   pipeline_name=clean(ia.get("pipeline_name")) or None,
                   rag_attributes={k: clean(ra.get(k)) for k in RAG_WHITELIST if k in ra})


def check_columns(cols):
    have = {c.upper(): (t or "").upper() for c, t in cols}
    bad = [f"{c} {t}" for c, t in EXPECTED_COLUMNS.items() if not have.get(c, "").startswith(t)]
    if bad:
        raise GuardError(f"$VECTAB columns changed; expected {EXPECTED_COLUMNS}, missing/typed: {bad}")
    extra = sorted(set(have) - set(EXPECTED_COLUMNS))
    if extra:
        log.warning("event=vectab_extra_columns columns=%s", extra)
    return extra


def index_fingerprint(chunks) -> str:
    """Order-independent content fingerprint: rowids and chunk order may change on a rebuild,
    object names and chunk text may not."""
    lines = sorted(f"{c['obj']}\t{sha(c['content'])}" for c in chunks)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def snapshot_index(db, index):
    """index_stats plus the content fingerprint; returns (stats, chunks)."""
    stats = db.index_stats(index)
    chunks = db.all_chunks(index)
    stats["fingerprint"] = index_fingerprint(chunks)
    return stats, chunks


def read_jsonl_tolerant(path):
    """Records of a JSONL file; a torn last line (crash mid-write) is skipped with a warning."""
    out = []
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            if n == len(lines):
                log.warning("event=torn_last_line file=%s", os.path.basename(path))
            else:
                raise GuardError(f"{path}:{n}: corrupt JSONL line")
    return out


def previous_stats(out_dir, index):
    """index_stats of the most recent earlier run (retrieval/rag/coverage header) for index."""
    best = None
    if not os.path.isdir(out_dir):
        return None
    for name in sorted(os.listdir(out_dir)):
        if not name.endswith(".jsonl") or not name.startswith(("retrieval_", "rag_", "coverage_")):
            continue
        try:
            with open(os.path.join(out_dir, name), encoding="utf-8") as f:
                head = json.loads(f.readline() or "{}")
        except (OSError, ValueError) as e:
            log.warning("event=unreadable_header file=%s error=%s", name, e)
            continue
        s = (head.get("index_stats") or {}).get(index) if head.get("type") == "header" else None
        if s and (best is None or head.get("utc", "") > best[0]):
            best = (head.get("utc", ""), s)
    return best[1] if best else None


def stats_equal(a, b) -> bool:
    same = (a["chunks"], a["content_chars"]) == (b["chunks"], b["content_chars"])
    if a.get("fingerprint") and b.get("fingerprint"):
        same = same and a["fingerprint"] == b["fingerprint"]
    return same


def check_stats(now, before, index, accept_change=False):
    if before is None:
        return True
    same = stats_equal(now, before)
    if not same and not accept_change:
        raise GuardError(f"{index}: chunk count/content length/content fingerprint changed since "
                         f"the last run ({before['chunks']}/{before['content_chars']} -> "
                         f"{now['chunks']}/{now['content_chars']}); a pipeline may have run")
    return same


# ---------------------------------------------------------------------------------------------
# per-question retrieval runs and metrics (pure; unit-tested)
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Run:
    label: str          # unmasked | mask_ar | mask_en
    direction: str      # e.g. EN->AR, AR->BOTH, EN->NONE
    gold: list          # document ids that count as gold in this run
    masked: list        # document ids removed from the search (WHERE ... NOT IN)


def _full_gold(q, corpus):
    g = list(dict.fromkeys(q["gold_docs"]))
    if q["bucket"] in ("T", "D"):
        g += [t for t in (corpus.twin(d) for d in g) if t and t not in g]
    return sorted(g)


def plan_runs(q, corpus):
    """T and D questions: unmasked (twin-first) plus both masks, giving EN->EN, EN->AR, AR->AR and
    AR->EN on the same facts (harness-only: Select AI has no per-query filter)."""
    ql = q["q_lang"].upper()
    if not q["answerable"]:
        return [Run("unmasked", f"{ql}->NONE", [], [])]
    gold = _full_gold(q, corpus)
    if q["bucket"] in ("T", "D"):
        en = [d for d in gold if corpus.lang[d] == "en"]
        ar = [d for d in gold if corpus.lang[d] == "ar"]
        return [Run("unmasked", f"{ql}->BOTH", gold, []),
                Run("mask_ar", f"{ql}->EN", en, ar),
                Run("mask_en", f"{ql}->AR", ar, en)]
    langs = sorted({corpus.lang[d] for d in gold})
    return [Run("unmasked", f"{ql}->{langs[0].upper() if len(langs) == 1 else 'MIXED'}", gold, [])]


def similarity(dist, metric):
    """SCORE units are settled by probe P14. Until then only COSINE has a similarity (1 - d)."""
    return None if dist is None or metric != "COSINE" else 1.0 - dist


def sha(text) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def chunk_keys_by_doc(chunks, corpus):
    """{doc: [(span_key(content), content)]} for containment checks."""
    out = {}
    for c in chunks:
        out.setdefault(corpus.doc_of(c["obj"]), []).append((span_key(c["content"]), c["content"]))
    return out


def span_list(q, with_facts=True):
    """[(span_key, doc or None, fact alternatives or None)] from the gold set v2 fields:
    span_docs[i] names the twin that holds span i, so a span counts only in chunks of its own
    document; span_has_fact[i] = false (a row label, a column header, a sentence next to the
    value) means a fact alternative must sit in the same line or table row (span_in_chunk).
    Without the fields: any gold doc, no fact requirement."""
    spans = q.get("evidence_spans") or []
    docs = q.get("span_docs") or [None] * len(spans)
    has = q.get("span_has_fact") or [True] * len(spans)
    alts = [x for g in q.get("facts") or [] for x in g] or None
    return [(span_key(s), d, None if (h or not with_facts) else alts) for s, d, h in zip(spans, docs, has)]


def _spans(span_keys):
    # accepts [(key, doc, alts)], [(key, doc)] or plain [key]
    out = []
    for x in span_keys:
        x = x if isinstance(x, tuple) else (x,)
        out.append((x + (None, None))[:3])
    return out


def fact_in_row(content, k, alts) -> bool:
    """v1.2 (review 1): the label span k and a fact alternative share a line or a table row.
    The window is the line(s) holding the span plus the rest of its row: the next ROW_LINES
    non-empty lines within ROW_CHARS characters, because python-docx, pdftotext and (probably)
    Oracle's filter put each table cell on its own line after the row label, and a column header
    or lead-in sentence sits a few lines above its value (checked on the corpus, 29-Sep). A value
    elsewhere in the chunk no longer counts, and a single-digit fact needs its unit (eval_norm)."""
    lines = (content or "").splitlines()
    keys = [span_key(x) for x in lines]
    for j in range(len(lines)):
        for i in range(j, max(j - SPAN_LINES, -1), -1):   # the span may wrap over narrow table columns
            if k in "".join(keys[i:j + 1]):
                if k in "".join(keys[i:j]):               # already complete before line j: seen there
                    break
                window, chars = lines[i:j + 1], 0
                for x in lines[j + 1:]:
                    if not x.strip():
                        continue
                    if len(window) - (j + 1 - i) >= ROW_LINES or chars + len(x) > ROW_CHARS:
                        break
                    window.append(x)
                    chars += len(x)
                if fact_present("\n".join(window), alts, list_markers=False):
                    return True
                break
    return False


def span_in_chunk(spans, doc, key, content) -> bool:
    return any(k and k in key and sd in (None, doc) and (alts is None or fact_in_row(content, k, alts))
               for k, sd, alts in spans)


def containable(run_gold, span_keys, keys_by_doc) -> bool:
    spans = _spans(span_keys)
    return any(span_in_chunk(spans, d, ck, content)
               for d in run_gold for ck, content in keys_by_doc.get(d, []))


def annotate(hits, run_gold, span_keys, corpus, metric, keep=KEEP_CONTENT):
    out = []
    spans = _spans(span_keys)
    for rank, h in enumerate(hits, 1):
        doc = corpus.doc_of(h["obj"])
        gold = doc is not None and doc in run_gold
        ck = span_key(h["content"])
        rec = {"rank": rank, "object_name": h["obj"], "doc_id": doc, "rid": h["rid"],
               "distance": h["dist"], "similarity": similarity(h["dist"], metric), "gold": gold,
               "evidence": gold and span_in_chunk(spans, doc, ck, h["content"]),
               "content_sha256": sha(h["content"]), "content_chars": len(h["content"] or "")}
        if rank <= keep:
            rec["content"] = h["content"]
        out.append(rec)
    return out


def first_rank(hits, flag):
    return next((h["rank"] for h in hits if h[flag]), None)


def twin_first(hits, q_lang, corpus):
    same = [h["rank"] for h in hits if h["gold"] and corpus.lang.get(h["doc_id"]) == q_lang]
    other = [h["rank"] for h in hits if h["gold"] and corpus.lang.get(h["doc_id"]) != q_lang]
    if not same and not other:
        return None
    return "same" if same and (not other or same[0] < other[0]) else "other"


def hit_at(rec, k, flag="evidence", threshold=None) -> bool:
    for h in rec["hits"]:
        if h["rank"] > k:
            break
        if h[flag]:
            if threshold is None:
                return True
            if h["similarity"] is None:
                raise ValueError("threshold needs SCORE units (P14) for this metric")
            if h["similarity"] >= threshold:
                return True
    return False


def mrr_at(rec, k=10, flag="gold") -> float:
    r = first_rank(rec["hits"], flag)
    return 1.0 / r if r and r <= k else 0.0


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 6) if xs else None


def summarize(records, ks=K_LIST, threshold=None):
    """One row per (split, bucket, direction), plus ALL rows per split for answerable questions.
    v1.2: bucket D (retrieval_only dialect paraphrases of T facts) has its own rows only; pooling
    it would count those facts twice."""
    recs = [r for r in records if r.get("type") == "query" and r.get("pass", 1) == 1]
    groups = {}
    for r in recs:
        # every run by its own bucket and direction (masked twins give EN->EN, EN->AR, ...)
        groups.setdefault((r["split"], r["bucket"], r["direction"]), []).append(r)
        # one unmasked run per question for the pooled rows, so no question counts twice
        if r["run_label"] == "unmasked" and r["bucket"] not in RETRIEVAL_ONLY_BUCKETS:
            groups.setdefault((r["split"], "ALL" if r["answerable"] else "U", "ALL"), []).append(r)
    rows = []
    for (split, bucket, direction), g in sorted(groups.items()):
        n = len(g)
        row = {"split": split, "bucket": bucket, "direction": direction, "n": n}
        ans = [r for r in g if r["answerable"]]
        cont = [r for r in ans if r["containable"]]
        row["containable"] = _rate(len(cont), len(ans))
        for k in ks:
            if ans:
                row[f"doc_hit@{k}"] = _rate(sum(hit_at(r, k, "gold", threshold) for r in ans), len(ans))
                row[f"ev_hit@{k}"] = _rate(sum(hit_at(r, k, "evidence", threshold) for r in ans), len(ans))
                row[f"ev_hit_cond@{k}"] = _rate(sum(hit_at(r, k, "evidence", threshold) for r in cont), len(cont))
        if ans:   # doc-MRR: first chunk of a gold document; ev-MRR: first chunk holding the evidence
            row["doc_mrr@10"] = round(sum(mrr_at(r, 10, "gold") for r in ans) / len(ans), 4)
            row["ev_mrr@10"] = round(sum(mrr_at(r, 10, "evidence") for r in ans) / len(ans), 4)
        tf = [r["twin_first"] for r in g if r.get("twin_first")]
        if tf:
            row["twin_first_same"] = _rate(tf.count("same"), len(tf))
        row["best_gold_dist_median"] = _med([(r["best_gold"] or {}).get("distance") for r in g])
        row["best_non_gold_dist_median"] = _med([(r["best_non_gold"] or {}).get("distance") for r in g])
        pairs = [r for r in g if r["best_gold"] and r["best_non_gold"]]
        if pairs:
            row["gold_beats_non_gold"] = _rate(sum(r["best_gold"]["distance"] < r["best_non_gold"]["distance"]
                                                   for r in pairs), len(pairs))
        rows.append(row)
    return rows


def _rate(x, n):
    if not n:
        return {"x": 0, "n": 0, "rate": None}
    lo, hi = st.wilson(x, n)
    return {"x": x, "n": n, "rate": round(x / n, 4), "ci95": [round(lo, 4), round(hi, 4)]}


def calibrate_threshold(records):
    """PLAN.md 4.1 (dev split only, SCORE units): the threshold that maximises balanced accuracy
    over answerable (best evidence-chunk similarity >= thr) and unanswerable (top-1 similarity
    < thr) questions; ties go to the lower value. Uses unmasked runs, never bucket D. v1.2: the
    gold chunk is the best chunk holding the evidence; a question with no evidence chunk in the
    index (not containable) falls back to the best gold-document chunk and is counted."""
    rs = [r for r in records if r.get("type") == "query" and r.get("pass", 1) == 1
          and r["run_label"] == "unmasked" and r["split"] == "dev"
          and r["bucket"] not in RETRIEVAL_ONLY_BUCKETS]
    pos, fallback = [], 0
    for r in rs:
        if not r["answerable"]:
            continue
        if r.get("best_evidence"):
            pos.append(r["best_evidence"]["similarity"])
        elif r["best_gold"]:
            pos.append(r["best_gold"]["similarity"])
            fallback += 1
    neg = [r["hits"][0]["similarity"] for r in rs if not r["answerable"] and r["hits"]]
    if not pos or not neg or any(x is None for x in pos + neg):
        return None
    best = None
    for t in sorted(set([0.0] + pos + neg)):
        tpr = sum(p >= t for p in pos) / len(pos)
        tnr = sum(x < t for x in neg) / len(neg)
        ba = (tpr + tnr) / 2
        if best is None or ba > best["balanced_accuracy"] + 1e-12:
            best = {"threshold": round(t, 6), "balanced_accuracy": round(ba, 4),
                    "answerable_pass": round(tpr, 4), "unanswerable_pass": round(tnr, 4),
                    "n_answerable": len(pos), "n_unanswerable": len(neg),
                    "n_doc_level_fallback": fallback}
    return best


def choose_match_limit(row, candidates=(3, 5, 6, 8, 10, 12), margin=0.03, key="ev_hit"):
    """PLAN.md 4.1: the smallest k with dev hit@k >= hit@12 - 3 pp."""
    top = (row.get(f"{key}@12") or {}).get("rate")
    if top is None:
        return None
    for k in candidates:
        r = (row.get(f"{key}@{k}") or {}).get("rate")
        if r is not None and r >= top - margin - 1e-12:
            return k
    return 12


# ---------------------------------------------------------------------------------------------
# metric identity and determinism (pure; unit-tested)
# ---------------------------------------------------------------------------------------------
def _groups(ranking, tol):
    gid, g = [], 0
    for i in range(len(ranking)):
        if i and abs(ranking[i][1] - ranking[i - 1][1]) > tol:
            g += 1
        gid.append(g)
    return gid


def rankings_equivalent(a, b, tol=TIE_TOL, truncated=True) -> bool:
    """a, b: [(key, distance)] in rank order. Equal, except that items whose distances tie within
    tol (in both lists) may swap, and a tie group cut by the top-N limit may differ at the end."""
    if len(a) != len(b):
        return False
    ga, gb = _groups(a, tol), _groups(b, tol)
    for i, ((ka, _), (kb, _)) in enumerate(zip(a, b)):
        if ka == kb:
            continue
        in_a = kb in {a[j][0] for j in range(len(a)) if ga[j] == ga[i]}
        in_b = ka in {b[j][0] for j in range(len(b)) if gb[j] == gb[i]}
        if in_a and in_b:
            continue
        if truncated and ga[i] == ga[-1] and gb[i] == gb[-1]:
            continue
        return False
    return True


def top_overlap(a, b, k=10) -> float:
    sa, sb = {x for x, _ in a[:k]}, {x for x, _ in b[:k]}
    return len(sa & sb) / max(len(sa), 1)


def compare_passes(p1, p2, k=10):
    """In-session determinism: same top-k (object, rowid) and identical distances."""
    diffs, max_d = [], 0.0
    for key, r1 in p1.items():
        r2 = p2.get(key)
        if r2 is None:
            diffs.append(key)
            continue
        l1 = [(h["object_name"], h["rid"]) for h in r1["hits"][:k]]
        l2 = [(h["object_name"], h["rid"]) for h in r2["hits"][:k]]
        if l1 != l2:
            diffs.append(key)
        for h1, h2 in zip(r1["hits"][:k], r2["hits"][:k]):
            max_d = max(max_d, abs(h1["distance"] - h2["distance"]))
    return {"compared": len(p1), "different": [list(d) for d in diffs], "max_abs_distance_diff": max_d}


def compare_files(a_path, b_path, k=10):
    """Rebuild determinism (index 1 vs 1r): counts, content length, top-k (object, content sha)."""
    def load(p):
        head, recs = None, {}
        with open(p, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r.get("type") == "header":
                    head = r
                elif r.get("type") == "query" and r.get("pass", 1) == 1:
                    recs[(r["id"], r["run_label"])] = r
        return head, recs
    ha, ra = load(a_path)
    hb, rb = load(b_path)
    sa, sb = ha["index_stats"][ha["index"]], hb["index_stats"][hb["index"]]
    stats_same = stats_equal(sa, sb)
    diffs, max_d = [], 0.0
    for key, r1 in ra.items():
        r2 = rb.get(key)
        l1 = [(h["object_name"], h["content_sha256"]) for h in r1["hits"][:k]]
        l2 = [(h["object_name"], h["content_sha256"]) for h in r2["hits"][:k]] if r2 else None
        if l1 != l2:
            diffs.append(list(key))
        if r2:
            for h1, h2 in zip(r1["hits"][:k], r2["hits"][:k]):
                max_d = max(max_d, abs(h1["distance"] - h2["distance"]))
    return {"stats_a": sa, "stats_b": sb, "stats_same": stats_same, "compared": len(ra),
            "different": diffs, "max_abs_distance_diff": max_d,
            "identical": stats_same and not diffs and len(ra) == len(rb)}


# ---------------------------------------------------------------------------------------------
# Select AI runsql cross-check (format unverified until probe P7: parse defensively)
# ---------------------------------------------------------------------------------------------
def parse_runsql(raw):
    """Returns [{content, source, score}] or None when the output is not a JSON row list."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(data, dict):
        data = next((data[k] for k in ("rows", "items", "data", "results")
                     if isinstance(data.get(k), list)), None)
    if not isinstance(data, list):
        return None
    out = []
    for r in data:
        if not isinstance(r, dict):
            return None
        low = {str(k).lower(): v for k, v in r.items()}
        out.append({"content": low.get("data") or low.get("content") or low.get("text"),
                    "source": low.get("source") or low.get("object_name") or low.get("url"),
                    "score": low.get("score")})
    return out


def runsql_agreement(parsed, hits, corpus, k=5):
    # Select AI returns at most the profile's match_limit rows (3 or 4 on the S3 builds), so the
    # comparison depth is min(k, rows returned); comparing 3 rows with the harness top 5 capped the
    # agreement at 0.6 even when every row matched (v1.4).
    depth = min(k, len(parsed)) if parsed else k
    h = hits[:depth]
    s = parsed[:depth]
    hk = {span_key(x.get("content", "")) for x in h if x.get("content")}
    sk = {span_key(x["content"]) for x in s if x.get("content")}
    hd = {x["doc_id"] for x in h}
    sd = {corpus.doc_of(str(x["source"])) for x in s if x.get("source")}
    return {"chunk_agreement": round(len(hk & sk) / max(len(hk), len(sk), 1), 4) if sk else None,
            "doc_agreement": round(len(hd & sd) / max(len(hd), len(sd), 1), 4) if sd else None,
            "rows": len(parsed)}


# ---------------------------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------------------------
def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def stamp_of(t):
    return t.strftime("%Y%m%dT%H%M%SZ")


class JsonlWriter:
    def __init__(self, path):
        self.path = path
        self.f = open(path, "a", encoding="utf-8")

    def write(self, rec):
        self.f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        self.f.flush()

    def close(self):
        self.f.close()


def guard_hits(rows, corpus, qid, masked=(), allowed=None):
    """v1.2 (review 1): a filtered search must return what the filter promised. A masked document
    in the hits (the SQL filter removed nothing, e.g. $.object_name in another form) or a hit
    outside an include list stops the run instead of silently skewing the masked directions."""
    for r in rows:
        d = corpus.doc_of(r["obj"])
        if d in masked:
            raise GuardError(f"{qid}: masked document {d} came back from the search as {r['obj']!r}; "
                             "the object_name filter did not apply")
        if allowed is not None and d not in allowed:
            raise GuardError(f"{qid}: restricted search returned {r['obj']!r} ({d}), outside {sorted(allowed)}")
    return rows


def _best(h):
    return {"distance": h["distance"], "similarity": h["similarity"], "doc_id": h["doc_id"], "rank": h["rank"]}


def query_record(db, p: Pairing, q, run: Run, corpus, keys_by_doc, metric=None, pass_no=1):
    metric = metric or p.metric
    spans = span_list(q)
    if run.masked and not corpus.files_of(run.masked):
        raise GuardError(f"{q['id']}: masked documents {run.masked} have no files in the manifest")
    t0 = time.monotonic()
    raw = guard_hits(db.search(p.index, p.owner, p.model, metric, q["question"],
                               exclude=corpus.files_of(run.masked), limit=TOP), corpus, q["id"], run.masked)
    hits = annotate(raw, run.gold, spans, corpus, metric)
    best_gold = next((_best(h) for h in hits if h["gold"]), None)
    if best_gold is None and run.gold:          # gold ranked below 20: one restricted lookup
        g = guard_hits(db.search(p.index, p.owner, p.model, metric, q["question"],
                                 include=corpus.files_of(run.gold), limit=1),
                       corpus, q["id"], run.masked, set(run.gold))
        if g:
            best_gold = {"distance": g[0]["dist"], "similarity": similarity(g[0]["dist"], metric),
                         "doc_id": corpus.doc_of(g[0]["obj"]), "rank": None}
    cont = containable(run.gold, spans, keys_by_doc) if run.gold else None
    best_ev = next((_best(h) for h in hits if h["evidence"]), None)
    if best_ev is None and cont:                # evidence ranked below 20: search the gold documents
        g = guard_hits(db.search(p.index, p.owner, p.model, metric, q["question"],
                                 include=corpus.files_of(run.gold), limit=EVIDENCE_LOOKUP),
                       corpus, q["id"], run.masked, set(run.gold))
        best_ev = next((dict(_best(h), rank=None) for h in annotate(g, run.gold, spans, corpus, metric, keep=0)
                        if h["evidence"]), None)
    best_non = next((_best(h) for h in hits if not h["gold"]), None)
    if best_non is None and hits:
        g = guard_hits(db.search(p.index, p.owner, p.model, metric, q["question"],
                                 exclude=corpus.files_of(run.gold + run.masked), limit=1),
                       corpus, q["id"], tuple(run.gold) + tuple(run.masked))
        if g:
            best_non = {"distance": g[0]["dist"], "similarity": similarity(g[0]["dist"], metric),
                        "doc_id": corpus.doc_of(g[0]["obj"]), "rank": None}
    return {"type": "query", "pass": pass_no, "utc": utc_now().isoformat(), "index": p.index,
            "metric": metric, "id": q["id"], "fact_id": q["fact_id"], "q_lang": q["q_lang"],
            "bucket": q["bucket"], "split": q["split"], "answerable": q["answerable"],
            "run_label": run.label, "direction": run.direction, "gold_docs": run.gold,
            "masked_docs": run.masked,
            "containable": cont,
            "hits": hits, "first_gold_rank": first_rank(hits, "gold"),
            "first_evidence_rank": first_rank(hits, "evidence"),
            "twin_first": twin_first(hits, q["q_lang"], corpus)
            if run.label == "unmasked" and q["bucket"] in ("T", "D") else None,
            "best_gold": best_gold, "best_evidence": best_ev, "best_non_gold": best_non,
            "elapsed_ms": int((time.monotonic() - t0) * 1000)}


def validate_spans(questions, corpus, keys_by_doc):
    missing = []
    for q in questions:
        if not q["answerable"]:
            continue
        gold = _full_gold(q, corpus)
        for (k, sd, _), s in zip(span_list(q, with_facts=False), q["evidence_spans"]):
            if not containable(gold, [(k, sd)], keys_by_doc):
                missing.append({"id": q["id"], "span": s, "doc": sd})
    return missing


def metric_check(db, p, q, run, corpus):
    lists = {}
    for m in ("COSINE", "DOT", "EUCLIDEAN", "MANHATTAN"):
        rows = guard_hits(db.search(p.index, p.owner, p.model, m, q["question"],
                                    exclude=corpus.files_of(run.masked), limit=TOP), corpus, q["id"], run.masked)
        lists[m] = [((r["obj"], r["rid"]), r["dist"]) for r in rows]
    ref = lists["COSINE"]
    return {"type": "metric_check", "id": q["id"], "run_label": run.label, "split": q["split"],
            "identical": {m: rankings_equivalent(ref, lists[m]) for m in ("DOT", "EUCLIDEAN", "MANHATTAN")},
            "top10_overlap": {m: round(top_overlap(ref, lists[m]), 4) for m in ("DOT", "EUCLIDEAN", "MANHATTAN")}}


def runsql_selection(questions, n):
    if n == "all":
        return list(questions)
    dev = sorted((q for q in questions if q["split"] == "dev"),
                 key=lambda q: hashlib.sha256(q["id"].encode()).hexdigest())
    return dev[:int(n)]


def run(args, db, corpus, questions, meta, seal, pairing, row=None) -> int:
    started = utc_now()
    stamp = args.stamp or stamp_of(started)
    os.makedirs(args.out, exist_ok=True)
    cols = db.vectab_columns(pairing.index)
    extra = check_columns(cols)
    stats_now, chunks = snapshot_index(db, pairing.index)
    if stats_now["null_objects"]:
        raise GuardError(f"{pairing.index}: {stats_now['null_objects']} chunks without $.object_name")
    before = previous_stats(args.out, pairing.index)
    check_stats(stats_now, before, pairing.index, args.accept_stats_change)
    pipe = db.pipeline_status(pairing.pipeline_name) if pairing.pipeline_name else None
    if pipe and str(pipe).upper() != "STOPPED":
        log.warning("event=pipeline_not_stopped index=%s status=%s", pairing.index, pipe)
    unknown = sorted({c["obj"] for c in chunks if corpus.doc_of(c["obj"]) is None})
    if unknown:
        log.warning("event=unknown_objects index=%s count=%d", pairing.index, len(unknown))
    keys = chunk_keys_by_doc(chunks, corpus)

    path = os.path.join(args.out, f"retrieval_{pairing.index}_{stamp}.jsonl")
    w = JsonlWriter(path)
    w.write({"type": "header", "tool": VERSION, "utc": started.isoformat(), "stamp": stamp,
             "config_id": args.config, "index": pairing.index,
             "experiments_row": {k: row.get(k) for k in EXPERIMENT_FIELDS} if row else None,
             "pairing": dataclasses.asdict(pairing), "index_stats": {pairing.index: stats_now},
             "previous_stats": before, "pipeline_status": pipe, "vectab_extra_columns": extra,
             "unknown_objects": unknown, "questions_file": os.path.basename(args.questions),
             "questions_meta_version": meta.get("version"), "seal": seal, "split": args.split,
             "only": args.only, "n_questions": len(questions), "top": TOP, "k_list": list(K_LIST)})
    log.info("event=start index=%s model=%s.%s metric=%s questions=%d out=%s", pairing.index,
             pairing.owner, pairing.model, pairing.metric, len(questions), path)
    rc = 0
    try:
        passes = {}
        for pass_no in range(1, args.repeat + 1):
            passes[pass_no] = {}
            for q in questions:
                if pause_requested(args.pause_file):
                    raise StopRun(f"PAUSE file present: {args.pause_file}")
                for r in plan_runs(q, corpus):
                    rec = query_record(db, pairing, q, r, corpus, keys, pass_no=pass_no)
                    passes[pass_no][(q["id"], r.label)] = rec
                    w.write(rec)
        records = list(passes[1].values())
        summary = {"type": "summary", "utc": utc_now().isoformat(), "rows": summarize(records),
                   "threshold_calibration_dev": calibrate_threshold(records)
                   if pairing.metric == "COSINE" else None}
        if args.repeat > 1:
            det = compare_passes(passes[1], passes[args.repeat])
            summary["in_session_determinism"] = det
            if det["different"] or det["max_abs_distance_diff"] != 0.0:
                log.error("event=nondeterministic compared=%d different=%d max_diff=%g",
                          det["compared"], len(det["different"]), det["max_abs_distance_diff"])
                rc = 4
        if args.metric_check:
            mc = []
            for q in questions:
                for r in plan_runs(q, corpus):
                    rec = metric_check(db, pairing, q, r, corpus)
                    mc.append(rec)
                    w.write(rec)
            summary["metric_check"] = {
                m: {"identical": sum(x["identical"][m] for x in mc), "n": len(mc),
                    "mean_top10_overlap": round(sum(x["top10_overlap"][m] for x in mc) / max(len(mc), 1), 4)}
                for m in ("DOT", "EUCLIDEAN", "MANHATTAN")}
        if args.runsql:
            caller = Caller(db, min_interval=args.min_interval)
            agree = []
            for q in runsql_selection(questions, args.runsql):
                if pause_requested(args.pause_file):
                    raise StopRun(f"PAUSE file present: {args.pause_file}")
                res = caller.call(q["question"], pairing.rag_profile, "runsql")
                parsed = parse_runsql(res.text) if res.status == "ok" else None
                base = passes[1][(q["id"], "unmasked")]
                ag = runsql_agreement(parsed, base["hits"], corpus) if parsed is not None else None
                if ag:
                    agree.append(ag)
                w.write({"type": "runsql", "id": q["id"], "status": res.status, "error": res.error,
                         "attempts": res.attempts, "latency_ms": res.latency_ms, "raw": res.text,
                         "parsed": parsed is not None, "agreement": ag})
            ch = [a["chunk_agreement"] for a in agree if a["chunk_agreement"] is not None]
            summary["runsql"] = {"parsed": len(agree), "mean_chunk_agreement":
                                 round(sum(ch) / len(ch), 4) if ch else None,
                                 "below_0.95": bool(ch) and sum(ch) / len(ch) < 0.95}
        if args.validate_spans:
            missing = validate_spans(questions, corpus, keys)
            summary["spans_missing"] = missing
            if missing:
                log.error("event=spans_missing count=%d first=%s", len(missing), missing[0]["id"])
                rc = rc or 5
        after, _ = snapshot_index(db, pairing.index)
        summary["index_stats_after"] = after
        if not stats_equal(after, stats_now):
            log.error("event=index_changed_during_run index=%s", pairing.index)
            rc = 4
        w.write(summary)
        report(summary["rows"])
    finally:
        w.close()
    log.info("event=done index=%s rc=%d out=%s", pairing.index, rc, path)
    return rc


def report(rows, ks=(1, 5, 10, 20)):
    out = sys.stdout
    head = (["split", "bucket", "direction", "n", "contain"] + [f"ev@{k}" for k in ks]
            + ["ev|c@5", "docmrr10", "evmrr10"])
    out.write(" ".join(f"{h:>9}" for h in head) + "\n")
    for r in rows:
        def f(key):
            v = r.get(key)
            return "-" if not v or v.get("rate") is None else f"{v['rate']:.3f}"
        cells = [r["split"], r["bucket"], r["direction"], str(r["n"]), f("containable")]
        cells += [f(f"ev_hit@{k}") for k in ks] + [f("ev_hit_cond@5"), str(r.get("doc_mrr@10", "-")),
                                                    str(r.get("ev_mrr@10", "-"))]
        out.write(" ".join(f"{c:>9}" for c in cells) + "\n")


def main(argv=None) -> int:
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["compare"]:
        if len(argv) != 3:
            log.error("usage: eval_retrieval.py compare A.jsonl B.jsonl")
            return 2
        res = compare_files(argv[1], argv[2])
        sys.stdout.write(json.dumps(res, indent=1) + "\n")
        return 0 if res["identical"] else 4
    ap = argparse.ArgumentParser(description="Select AI RAG lab: retrieval layer (no LLM)")
    ap.add_argument("--config", help="config_id in experiments.csv")
    ap.add_argument("--index", help="lab index (when not using --config)")
    ap.add_argument("--rag-profile", help="default RAG_P_<index without RAG_>")
    ap.add_argument("--experiments", default=DEFAULT_EXPERIMENTS)
    ap.add_argument("--questions", default=DEFAULT_QUESTIONS)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--split", choices=("all", "dev", "test"), default="all")
    ap.add_argument("--only", default="")
    ap.add_argument("--repeat", type=int, default=1, choices=(1, 2))
    ap.add_argument("--metric-check", action="store_true")
    ap.add_argument("--runsql", default="0", help="0 | N dev questions | all")
    ap.add_argument("--validate-spans", action="store_true")
    ap.add_argument("--accept-stats-change", action="store_true",
                    help="only for a deliberate rebuild under the same name")
    ap.add_argument("--allow-unsealed", action="store_true")
    ap.add_argument("--min-interval", type=float, default=1.0)
    ap.add_argument("--call-timeout", type=int, default=180, help="seconds")
    ap.add_argument("--pause-file", default=DEFAULT_PAUSE)
    ap.add_argument("--stamp", default="")
    a = ap.parse_args(argv)
    a.runsql = 0 if a.runsql in ("0", "") else ("all" if a.runsql == "all" else int(a.runsql))

    db = None
    try:
        row = experiment_row(a.experiments, a.config) if a.config else None
        index = lab_index_name((row or {}).get("index_name") or a.index or "")
        rag = a.rag_profile or (row or {}).get("rag_profile") or ("RAG_P_" + index[4:])
        corpus = Corpus.from_csv(a.manifest)
        meta, qs, seal = load_questions(a.questions, corpus)
        if not seal["ok"]:
            if not a.allow_unsealed:
                raise GuardError(f"questions.json seal mismatch (digest {seal['digest']}, sealed "
                                 f"{seal['sealed']}); use --allow-unsealed only for a dry run")
            log.warning("event=unsealed_questions digest=%s", seal["digest"])
        qs = select_questions(qs, a.split, a.only)
        db = OracleLabDB(connect_from_env(), call_timeout_ms=a.call_timeout * 1000)
        pairing = resolve_pairing(db, index, rag, expected_model=(row or {}).get("model_name"))
        return run(a, db, corpus, qs, meta, seal, pairing, row)
    except GuardError as e:
        log.error("event=guard_failed %s", e)
        return 2
    except StopRun as e:
        log.warning("event=stopped %s", e)
        return 3
    except DBCallError as e:
        log.error("event=db_error %s", redact(e))
        return 2
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
