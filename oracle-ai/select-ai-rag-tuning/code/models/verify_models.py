#!/usr/bin/env python3
# v1.2 - brief 09: parity gates for a converted embedding model, before it goes near the database.
#        v1.2: cosine gates per language as well as pooled (EN and AR each mean >= 0.98, min >= 0.95);
#              --allow-token-divergence for the operator-accepted exceptions M6 and M1Q (29-Sep):
#              token-id gates are reported and waived, every cosine gate still applies; 'refvec'
#              writes the per-string local reference vectors (one string per call) for probe P2.
#        v1.1 (Codex review): the passage file must be the full 50/25/25 set with a matching texts
#              sha256 (no gate can pass on a smaller file); reports are checked for absolute paths.
#
# Run as : 'passages' on the workstation that holds corpus/src (no model needed);
#          'verify' on the build host, with ~/rag-lab/venv (onnxruntime 1.20.1 = the database's version,
#          onnxruntime-extensions 0.13.0). No database, no docker, no sudo.
# Usage  : verify_models.py passages --src ../../corpus/src --out parity_passages.json
#          verify_models.py verify --key M6 --model ~/rag-lab/converted/arabic_triplet_v2.onnx \
#            --passages ~/rag-lab/code/parity_passages.json --report-dir ~/rag-lab/reports \
#            [--oracle-ref ~/rag-lab/oracle-prebuilt/multilingual_e5_small.onnx] [--threads 6] \
#            [--allow-token-divergence]          (M6 and M1Q only: the operator's 29-Sep exceptions)
#          verify_models.py refvec --model ~/rag-lab/converted/bge_m3.onnx --out ~/rag-lab/reports/M4_refvec.json
#            (the key comes from --key or from the <KEY>_refvec.json file name; M0 and M1 use Oracle's
#            all_MiniLM_L12_v2.onnx and multilingual_e5_small.onnx)
# Re-run : safe. 'passages' is deterministic (same corpus -> byte-identical file); 'verify' overwrites
#          <report-dir>/<KEY>_parity.json; 'refvec' overwrites its --out file (written atomically).
#          Exit 0 = every gate passed (or refvec written), 1 = a gate failed, 2 = error.
#
# Gates (PLAN.md section 3):
#   structure : IR 8, opset 17, one string input 'input', one float output 'embedding' [batch, dim],
#               onnx.checker, < 2e9 bytes, sha256 equal to the build sidecar;
#   runtime   : onnxruntime 1.20.1 with onnxruntime-extensions registered (--allow-runtime relaxes this);
#   token ids : the ids the grafted graph feeds the transformer equal the HF *slow* tokenizer's
#               (prefix_text + text, truncation at the cap) on 20 EN/AR strings, run singly AND batched,
#               and on the 50 passages. Slow = the implementation Oracle's tokenizer ops follow (the
#               sentencepiece library for XLM-R, WordPiece in Python for BERT). The Rust "fast" tokenizer
#               differs on a few inputs (XLM-R: a trailing '▁' after trailing whitespace, and the split of
#               Arabic-Indic digit groups such as '٬٠٠٠'); its agreement is reported, not gated;
#   cosine    : 50 corpus passages, one at a time, against the HF FP32 model: mean >= 0.98, min >= 0.95,
#               pooled AND for the 25 EN and the 25 AR passages separately (a pooled mean hides an
#               Arabic-only loss when the English half is exact, as for the M6 FP32 build);
#   norms     : every output within 1e-3 of unit length;
#   --oracle-ref (recipe proof / M1Q): token ids equal Oracle's graph on prefix_text + text, and mean
#               cosine to Oracle's embedding >= 0.999.
#   --allow-token-divergence (M6, M1Q): the token-id gates above are still measured and every
#               differing input is listed under token_id_divergence; they are marked waived instead of
#               failed. Nothing else is relaxed. Any other key is refused (exit 2), and so is an M6
#               build that is not FP32 or an M1Q build that is not the "query: " splice, and a build of
#               another model or revision than the one the exception was granted for.
# refvec    : the 20 token-id strings embedded one per call by the given ONNX file, plus, for a string
#               over 4,000 UTF-8 bytes, the same for its 4,000-byte prefix (what the database accepts
#               under max_string_size=STANDARD). 04_probes.py p2 compares the database with these.
# Batched-vs-single cosine is reported, not gated: dynamic INT8 quantisation picks one activation scale
# per batch, so Oracle's own INT8 model gives slightly different vectors for a text scored alone or in a
# batch. Reports carry no absolute home paths.
import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import convert_hf_to_oracle as conv   # noqa: E402  (shared helpers; same directory)

VERIFY_VERSION = "1.2"
PASSAGES_VERSION = "1.0"
REFVEC_VERSION = "1.0"
log = logging.getLogger("verify_models")
DB_ORT_VERSION = "1.20.1"
GATE_MEAN, GATE_MIN, GATE_ORACLE_MEAN, NORM_TOL = 0.98, 0.95, 0.999, 1e-3
GATE_LANGS = ("en", "ar")               # the passage set is 25 + 25; each half is gated on its own
BATCH = 10
REFVEC_CAP_BYTES = 4000                 # VARCHAR2 under max_string_size=STANDARD (VECTOR_EMBEDDING input)

# Token-id gates, and the only keys whose token-id divergence the operator accepted (29-Sep-2026).
# The reasons are copied into the report; one line each, no tab (like models_exceptions.txt's reasons).
TOKEN_ID_GATES = ("token_ids_single_20_of_20", "token_ids_batched_20_of_20", "passage_token_ids_50_of_50",
                  "oracle_token_ids_equal")
ACCEPTED_TOKEN_DIVERGENCE = {
    "M6": "operator decision 29-Sep-2026: ARABIC_TRIPLET_V2 accepted in FP32 (M6_QUANTIZE=none); the in-database "
          "BERT tokenizer op splits Arabic punctuation and partly covered words differently from Hugging Face, "
          "so the token-id gates are waived and the cosine gates, measured with the op's ids, must pass",
    "M1Q": "operator decision 29-Sep-2026: MULTILINGUAL_E5_SMALL_Q accepted (exploratory) with its SentencePiece "
           "tie-break divergence disclosed (the spliced 'query: ' prefix segments one Arabic-Indic digit group "
           "differently, 1 of 70 inputs on 29-Sep); the cosine gates must pass",
}
REFVEC_KEY_RE = r"^(M[0-6]|M1Q)$"
# the builds the exceptions were granted for (run_conversions.sh spec(); a later revision is another model)
ACCEPTED_BUILDS = {
    "M6": {"model_name": "ARABIC_TRIPLET_V2", "hf_id": "Omartificial-Intelligence-Space/Arabic-Triplet-Matryoshka-V2",
           "revision": "408d483803e83aaea0aceec550deac66e5f8dc11"},
    "M1Q": {"model_name": "MULTILINGUAL_E5_SMALL_Q", "hf_id": "intfloat/multilingual-e5-small",
            "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3"},
}

_LONG_EN = ("Employees accrue annual leave from the first day of service and may carry forward a "
            "limited balance into the next calendar year, subject to the approval of their line manager. ")
_LONG_AR = ("يستحق الموظف الإجازة السنوية اعتبارا من اليوم الأول للخدمة ويجوز له ترحيل رصيد محدود إلى السنة "
            "التالية بعد موافقة المدير المباشر وإدارة الموارد البشرية. ")

# 20 fixed strings: English, Arabic, mixed; tashkeel, Arabic-Indic digits, Latin inside Arabic, punctuation,
# tatweel, hamza forms, bidi mark, whitespace runs, and two texts longer than any cap (truncation path).
TOKEN_STRINGS = [
    ("en", "Annual leave is 27 working days per year."),
    ("en", "EMPLOYEES MAY CARRY FORWARD UP TO 10 DAYS INTO THE NEXT YEAR."),
    ("en", "Café résumé naïve façade: the employee's rôle — see §4.2."),
    ("en", "Policy HRP-013 v2.1 caps the allowance at QAR 30,000.00 (3.5% of basic pay)."),
    ("en", "  multiple   spaces\tand\ttabs\nand a new line  "),
    ("en", "How many days of sick leave do I get?"),
    ("ar", "يحق للموظف الحصول على إجازة سنوية مدفوعة الأجر."),
    ("ar", "يَحِقُّ لِلْمُوَظَّفِ الْحُصُولُ عَلَى إِجَازَةٍ سَنَوِيَّةٍ مَدْفُوعَةِ الْأَجْرِ."),
    ("ar", "مدة الإجازة ٢٧ يوم عمل وتصرف المستحقات خلال ١٥ يوماً من تاريخ الطلب."),
    ("ar", "تطبق سياسة HR-013 على جميع موظفي Doha Office منذ عام 2025."),
    ("ar", "هل يحق لي إجازة مرضية؟ نعم، بشرط تقديم شهادة طبية؛ انظر البند (٤)."),
    ("ar", "الإجـــازة السنـــوية للموظفـــين"),
    ("ar", "أ إ آ ء ؤ ئ ة ى: إدارة شؤون الموظفين في المؤسسة"),
    ("ar", "نسبة البدل ٣٫٥٪ من الراتب الأساسي، والحد الأقصى ١٬٥٠٠ ريال."),
    ("ar", "‏رقم الطلب 4471 مسجل لدى الموارد البشرية‏"),
    ("mix", "The مكافأة نهاية الخدمة (end-of-service gratuity) is 21 days of basic salary per year."),
    ("ar", "كم عدد أيام الإجازة المرضية المدفوعة في السنة؟"),
    ("mix", "Ramadan hours: ساعات العمل في رمضان ست ساعات يومياً (6 hours), Sunday–Thursday."),
    ("en", (_LONG_EN * 60)[:6000]),
    ("ar", (_LONG_AR * 50)[:6000]),
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------- passages
def _snap_start(body: str, start: int) -> int:
    if start <= 0:
        return 0
    for i in range(start, min(len(body), start + 200)):
        if body[i].isspace():
            return i + 1
    return start


def _snap_end(body: str, start: int, end: int) -> int:
    end = min(end, len(body))
    floor = start + int((end - start) * 0.8)
    for i in range(end - 1, floor, -1):
        if body[i].isspace():
            return i
    return end


def build_passages(src: Path, n_en: int = 25, n_ar: int = 25) -> dict:
    """Deterministic parity passages: windows of 400/1000/2000 characters from the corpus sources."""
    files = sorted(src.glob("*/*.txt"))
    if not files:
        raise ValueError(f"no */*.txt files under {src}")
    pools = {"en": [], "ar": []}
    for f in files:
        text = f.read_text(encoding="utf-8")
        if "#BODY\n" not in text:
            raise ValueError(f"{f.name}: no #BODY marker")
        body = text.split("#BODY\n", 1)[1].strip()
        lang = "ar" if "-AR-" in f.name else "en"
        pools[lang].append((hashlib.sha256(f.name.encode("utf-8")).hexdigest(), f, body,
                            hashlib.sha256(text.encode("utf-8")).hexdigest()))
    lengths = [400, 1000, 2000]
    passages = []
    for lang, n in (("en", n_en), ("ar", n_ar)):
        pool = sorted(pools[lang], key=lambda t: t[0])
        if not pool:
            raise ValueError(f"no {lang} source files")
        for k in range(n):
            _, f, body, fsha = pool[k % len(pool)]
            rnd = k // len(pool)
            length = min(lengths[k % 3], len(body))
            start = _snap_start(body, (k * 7919 + rnd * 104729) % max(1, len(body) - length))
            end = _snap_end(body, start, start + length)
            text = body[start:end].strip()
            passages.append({"pid": f"{lang}-{k + 1:02d}", "lang": lang,
                             "source": f"{f.parent.name}/{f.name}", "source_sha256": fsha,
                             "start": start, "chars": len(text), "text": text})
    blob = json.dumps([p["text"] for p in passages], ensure_ascii=False).encode("utf-8")
    return {"version": PASSAGES_VERSION, "n": len(passages), "n_en": n_en, "n_ar": n_ar,
            "window_chars": lengths, "texts_sha256": hashlib.sha256(blob).hexdigest(), "passages": passages}


def check_passages(doc: dict, n_en: int = 25, n_ar: int = 25) -> None:
    """The gates are defined on the full set; refuse a trimmed, edited or re-ordered file."""
    ps = doc.get("passages") or []
    langs = [p.get("lang") for p in ps]
    if len(ps) != n_en + n_ar or langs.count("en") != n_en or langs.count("ar") != n_ar:
        raise ValueError(f"passage file has {len(ps)} passages ({langs.count('en')} en, {langs.count('ar')} ar); "
                         f"the gates need {n_en} en + {n_ar} ar")
    if any(not p.get("text") for p in ps):
        raise ValueError("passage file has an empty passage")
    blob = json.dumps([p["text"] for p in ps], ensure_ascii=False).encode("utf-8")
    if hashlib.sha256(blob).hexdigest() != doc.get("texts_sha256"):
        raise ValueError("passage texts do not match texts_sha256 (file edited after it was built)")


def cmd_passages(a) -> int:
    doc = build_passages(Path(a.src), a.n_en, a.n_ar)
    out = Path(a.out)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    log.info("wrote %d passages (%d en, %d ar) to %s, texts sha256 %s", doc["n"], a.n_en, a.n_ar,
             out.name, doc["texts_sha256"])
    return 0


# ----------------------------------------------------------------------------- ONNX side
def session_with_ids(model_path: Path, threads: int):
    """Session over the final file that also returns the tensors the tokenizer feeds the transformer."""
    import onnx
    m = onnx.load(str(model_path))
    have = {o.name for o in m.graph.output}
    for name in ("input_ids", "attention_mask"):
        if name not in have:
            m.graph.output.append(onnx.helper.make_tensor_value_info(name, onnx.TensorProto.INT64, None))
    s = conv.ort_session(m.SerializeToString(), threads)
    del m
    return s


def run_graph(sess, texts: list):
    emb, ids, mask = sess.run(["embedding", "input_ids", "attention_mask"],
                              {"input": np.array(texts, dtype=object)})
    rows = [[int(t) for t, k in zip(ids[i], mask[i]) if k] for i in range(len(texts))]
    return emb.astype(np.float64), rows


def run_single(sess, texts: list):
    embs, rows = [], []
    for t in texts:
        e, r = run_graph(sess, [t])
        embs.append(e[0])
        rows.append(r[0])
    return np.stack(embs), rows


def run_batched(sess, texts: list):
    embs, rows = [], []
    for i in range(0, len(texts), BATCH):
        e, r = run_graph(sess, texts[i:i + BATCH])
        embs.append(e)
        rows.extend(r)
    return np.concatenate(embs), rows


# ----------------------------------------------------------------------------- HF reference
class HFReference:
    def __init__(self, snap: Path, pooling: str, max_tokens: int, threads: int, need_model: bool = True):
        import torch
        from transformers import AutoModel, AutoTokenizer
        torch.set_num_threads(threads)
        self.torch = torch
        # slow = reference (sentencepiece / Python WordPiece); fast = what sentence-transformers uses
        self.tok = AutoTokenizer.from_pretrained(str(snap), local_files_only=True, use_fast=False)
        self.tok_fast = AutoTokenizer.from_pretrained(str(snap), local_files_only=True, use_fast=True)
        self.model = AutoModel.from_pretrained(str(snap), local_files_only=True, torch_dtype=torch.float32,
                                               attn_implementation="eager").eval() if need_model else None
        self.pooling, self.cap = pooling, max_tokens

    def ids(self, texts: list) -> list:
        return [self.tok(t, truncation=True, max_length=self.cap)["input_ids"] for t in texts]

    def fast_ids(self, texts: list) -> list:
        return [self.tok_fast(t, truncation=True, max_length=self.cap)["input_ids"] for t in texts]

    def n_tokens_untruncated(self, text: str) -> int:
        return len(self.tok(text, truncation=False)["input_ids"])

    def embed(self, texts: list) -> np.ndarray:
        out = []
        with self.torch.no_grad():
            for i in range(0, len(texts), 8):
                enc = self.tok(texts[i:i + 8], padding=True, truncation=True, max_length=self.cap,
                               return_tensors="pt")
                h = self.model(**enc).last_hidden_state
                if self.pooling == "cls":
                    x = h[:, 0]
                else:
                    w = enc["attention_mask"].unsqueeze(-1).to(h.dtype)
                    x = (h * w).sum(1) / w.sum(1).clamp(min=1e-9)
                x = x / x.norm(p=2, dim=1, keepdim=True).clamp(min=1e-12)
                out.append(x.double().numpy())
        return np.concatenate(out)


def weight_digest(m, min_elems: int = 1024):
    """Multiset of large initializers by (type, shape, sha256 of values): names differ between exporters."""
    import collections
    from onnx import numpy_helper
    out = collections.Counter()
    for i in m.graph.initializer:
        n = int(np.prod(i.dims)) if len(i.dims) else 1
        if n >= min_elems:
            out[(i.data_type, tuple(i.dims), hashlib.sha256(numpy_helper.to_array(i).tobytes()).hexdigest())] += 1
    return out


def cos_rows(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a / np.linalg.norm(a, axis=1, keepdims=True)
    b = b / np.linalg.norm(b, axis=1, keepdims=True)
    return np.sum(a * b, axis=1)


def stats(v) -> dict:
    v = np.asarray(v, dtype=np.float64)
    return {"n": int(v.size), "mean": round(float(v.mean()), 6), "min": round(float(v.min()), 6),
            "p05": round(float(np.percentile(v, 5)), 6), "max": round(float(v.max()), 6)}


def first_diff(a: list, b: list):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return None if len(a) == len(b) else min(len(a), len(b))


def gate(gates: dict, name: str, value, threshold, ok: bool) -> None:
    gates[name] = {"value": value, "threshold": threshold, "pass": bool(ok)}


def cosine_gates(gates: dict, c, langs: list) -> None:
    """Pooled gates (names kept from v1.1) plus the same thresholds for each language on its own."""
    c = np.asarray(c, dtype=np.float64)
    gate(gates, "cosine_mean_ge_0.98", round(float(c.mean()), 6), GATE_MEAN, c.mean() >= GATE_MEAN)
    gate(gates, "cosine_min_ge_0.95", round(float(c.min()), 6), GATE_MIN, c.min() >= GATE_MIN)
    for lg in GATE_LANGS:
        v = c[np.array([x == lg for x in langs], dtype=bool)]
        if v.size == 0:                       # check_passages makes this impossible; fail closed anyway
            gate(gates, f"cosine_{lg}_passages_present", 0, ">=1", False)
            continue
        gate(gates, f"cosine_{lg}_mean_ge_0.98", round(float(v.mean()), 6), GATE_MEAN, v.mean() >= GATE_MEAN)
        gate(gates, f"cosine_{lg}_min_ge_0.95", round(float(v.min()), 6), GATE_MIN, v.min() >= GATE_MIN)


def check_divergence_key(key: str, allow: bool) -> None:
    """--allow-token-divergence exists for the two exceptions the operator accepted, nothing else."""
    if allow and key not in ACCEPTED_TOKEN_DIVERGENCE:
        raise conv.ConversionError(f"--allow-token-divergence is accepted for {', '.join(ACCEPTED_TOKEN_DIVERGENCE)} "
                                   f"only (operator decision 29-Sep-2026), not {key}")


def check_waiver_build(key: str, build: dict) -> None:
    """The exceptions cover one construction each: M6 as an FP32 transformer, M1Q as Oracle's e5-small with
    the "query: " splice, each from its pinned model and revision. A waiver asked for any other build is
    refused."""
    want = ACCEPTED_BUILDS.get(key) or {}
    wrong = [f"{k}={build.get(k)!r}" for k, v in want.items() if build.get(k) != v]
    if not want or wrong:
        raise conv.ConversionError(f"the {key} exception does not cover this build ({', '.join(wrong) or 'unknown key'})")
    q = build.get("quantization") or {}
    if key == "M6" and q.get("mode") != "none":
        raise conv.ConversionError("the M6 exception covers the FP32 build only (M6_QUANTIZE=none); this build is "
                                   f"quantised ({q.get('weight_type') or q.get('mode') or 'unknown'})")
    if key == "M1Q" and not (build.get("reuse_template_transformer") is True and build.get("prefix_text") == "query: "):
        raise conv.ConversionError("the M1Q exception covers Oracle's e5-small transformer with the 'query: ' splice only")


def waive_token_gates(gates: dict, key: str) -> list:
    """Mark failed token-id gates as waived (value and threshold kept, measured result kept). Other gates,
    the cosine gates included, are untouched. Returns the names waived."""
    check_divergence_key(key, True)
    waived = []
    for name in TOKEN_ID_GATES:
        g = gates.get(name)
        if g is not None and not g["pass"]:
            g.update({"pass": True, "waived": True, "measured_pass": False,
                      "waiver": f"operator exception {key}"})
            waived.append(name)
    return waived


# ----------------------------------------------------------------------------- verify
def structure(model_path: Path, build: dict, gates: dict) -> dict:
    import onnx
    size = model_path.stat().st_size
    sha = conv.sha256_file(model_path)
    m = onnx.load(str(model_path))
    ins = [(i.name, i.type.tensor_type.elem_type, len(i.type.tensor_type.shape.dim)) for i in m.graph.input]
    outs = []
    for o in m.graph.output:
        dims = [d.dim_value if d.HasField("dim_value") else d.dim_param for d in o.type.tensor_type.shape.dim]
        outs.append((o.name, o.type.tensor_type.elem_type, dims))
    opset = [o.version for o in m.opset_import if o.domain == ""]
    try:
        onnx.checker.check_model(m)
        checker = "ok"
    except Exception as e:  # reported as a failed gate, not raised
        checker = f"failed: {e}"
    dim = build["output"]["dim"]
    info = {"file": model_path.name, "bytes": size, "sha256": sha, "ir_version": m.ir_version,
            "opset": opset, "inputs": ins, "outputs": outs, "checker": checker,
            "op_counts": conv.op_counts(m)}
    del m
    gate(gates, "file_under_2e9_bytes", size, conv.MAX_FILE_BYTES, size < conv.MAX_FILE_BYTES)
    gate(gates, "ir_version_8", info["ir_version"], 8, info["ir_version"] == 8)
    gate(gates, "opset_17", opset, [17], opset == [17])
    gate(gates, "input_is_string_input", ins, [("input", 8, 1)], ins == [("input", 8, 1)])
    gate(gates, "output_is_embedding", outs, [("embedding", 1, ["batch_size", dim])],
         outs == [("embedding", 1, ["batch_size", dim])])
    gate(gates, "onnx_checker", checker, "ok", checker == "ok")
    gate(gates, "sha256_matches_build", sha, build["output"]["sha256"], sha == build["output"]["sha256"])
    return info


def cmd_verify(a) -> int:
    key = a.key.upper()
    check_divergence_key(key, a.allow_token_divergence)      # before any file is read
    import onnxruntime
    import onnxruntime_extensions
    t0 = time.monotonic()
    model_path = Path(os.path.expanduser(a.model)).resolve()
    build_path = Path(os.path.expanduser(a.build_info)) if a.build_info else \
        model_path.with_name(model_path.name + ".build.json")
    build = conv.read_json(build_path)
    if a.allow_token_divergence:
        check_waiver_build(key, build)
    passages = conv.read_json(Path(os.path.expanduser(a.passages)))
    check_passages(passages)
    if len(TOKEN_STRINGS) != 20:
        raise ValueError("the token-id gate is defined on 20 strings")
    if not 1 <= a.threads <= conv.MAX_THREADS:
        raise conv.ConversionError(f"--threads must be 1..{conv.MAX_THREADS}")
    gates, report = {}, {"key": key, "verify_version": VERIFY_VERSION, "created_utc": utc_now(),
                         "model": model_path.name, "build": {k: build.get(k) for k in (
                             "model_name", "hf_id", "revision", "pooling", "max_tokens", "template",
                             "template_sha256", "prefix_ids", "prefix_text", "reuse_template_transformer",
                             "tokenizer", "quantization", "converter_version")}}

    # ---- runtime
    ort_v, ext_v = onnxruntime.__version__, onnxruntime_extensions.__version__
    report["runtime"] = {"onnxruntime": ort_v, "onnxruntime_extensions": ext_v,
                         "extensions_registered": True, "threads": a.threads}
    gate(gates, "onnxruntime_is_db_version", ort_v, DB_ORT_VERSION, ort_v == DB_ORT_VERSION or a.allow_runtime)

    # ---- structure
    report["structure"] = structure(model_path, build, gates)
    dim = build["output"]["dim"]

    # ---- HF reference (pinned snapshot; weights downloaded if the convert step did not need them)
    snap, _ = conv.download_snapshot(build["hf_id"], build["revision"], Path(os.path.expanduser(a.cache_dir)),
                                     with_weights=True)
    ref = HFReference(snap, build["pooling"], build["max_tokens"], a.threads)
    prefix = build.get("prefix_text") or ""

    sess = session_with_ids(model_path, a.threads)
    strings = [s for _, s in TOKEN_STRINGS]
    hf_ids = ref.ids([prefix + s for s in strings])
    e_single, g_single = run_single(sess, strings)
    e_batch, g_batch = run_batched(sess, strings)
    cases = []
    for i, ((lang, s), want) in enumerate(zip(TOKEN_STRINGS, hf_ids)):
        cases.append({"i": i + 1, "lang": lang, "chars": len(s), "text": s if len(s) <= 120 else s[:60] + " ...",
                      "hf_tokens_untruncated": ref.n_tokens_untruncated(prefix + s), "hf_tokens": len(want),
                      "match_single": g_single[i] == want, "match_batch": g_batch[i] == want,
                      "first_diff_single": first_diff(g_single[i], want),
                      "graph_ids_head": g_single[i][:12], "hf_ids_head": want[:12]})
    n_single = sum(c["match_single"] for c in cases)
    n_batch = sum(c["match_batch"] for c in cases)
    report["token_ids"] = {"n": len(cases), "match_single": n_single, "match_batch": n_batch, "cases": cases}
    gate(gates, "token_ids_single_20_of_20", n_single, len(cases), n_single == len(cases))
    gate(gates, "token_ids_batched_20_of_20", n_batch, len(cases), n_batch == len(cases))
    truncated = [c for c in cases if c["hf_tokens_untruncated"] > build["max_tokens"]]
    gate(gates, "truncation_exercised", len(truncated), ">=1", len(truncated) >= 1)

    # ---- cosine against HF FP32 on the corpus passages, one text at a time
    texts = [p["text"] for p in passages["passages"]]
    langs = [p["lang"] for p in passages["passages"]]
    o_single, o_ids = run_single(sess, texts)
    hf_emb = ref.embed([prefix + t for t in texts])
    if hf_emb.shape[1] != dim or o_single.shape[1] != dim:
        raise conv.ConversionError(f"dimension mismatch: onnx {o_single.shape[1]}, hf {hf_emb.shape[1]}, build {dim}")
    c = cos_rows(o_single, hf_emb)
    p_ids_hf = ref.ids([prefix + t for t in texts])
    p_ids_match = sum(a_ == b_ for a_, b_ in zip(o_ids, p_ids_hf))
    by_lang = {lg: stats([x for x, l2 in zip(c, langs) if l2 == lg]) for lg in sorted(set(langs))}
    # separates conversion error (ids equal) from tokenizer divergence (ids differ)
    same_ids = [a_ == b_ for a_, b_ in zip(o_ids, p_ids_hf)]
    by_ids = {k: stats([x for x, m in zip(c, same_ids) if m == want]) for k, want in (("ids_equal", True),
              ("ids_differ", False)) if any(m == want for m in same_ids)}
    report["cosine_vs_hf_fp32"] = {
        "passages_sha256": passages.get("texts_sha256"), "reference_tokenizer": "slow (use_fast=False)",
        **stats(c), "by_lang": by_lang, "by_token_ids": by_ids,
        "passage_token_ids_match": p_ids_match,
        "truncated_passages": sum(len(x) >= build["max_tokens"] for x in p_ids_hf),
        "worst": sorted(({"pid": p["pid"], "cos": round(float(x), 6)} for p, x in zip(passages["passages"], c)),
                        key=lambda d: d["cos"])[:5]}
    cosine_gates(gates, c, langs)
    gate(gates, "passage_token_ids_50_of_50", p_ids_match, len(texts), p_ids_match == len(texts))
    # every input whose ids differ from the reference, listed whether or not a waiver applies
    report["token_id_divergence"] = {
        "reference": "HF slow tokenizer on prefix_text + text",
        "strings_single": [{"i": x["i"], "lang": x["lang"], "first_diff": x["first_diff_single"]}
                           for x in cases if not x["match_single"]],
        "strings_batched": [x["i"] for x in cases if not x["match_batch"]],
        "passages": [{"pid": p["pid"], "lang": p["lang"], "first_diff": first_diff(o, h)}
                     for p, o, h in zip(passages["passages"], o_ids, p_ids_hf) if o != h]}

    # ---- agreement with the HF fast tokenizer (information: known sentencepiece-vs-Rust differences)
    fast_s = ref.fast_ids([prefix + s for s in strings])
    fast_p = ref.fast_ids([prefix + t for t in texts])
    diffs = []
    for label, graph_rows, fast_rows in (("string", g_single, fast_s), ("passage", o_ids, fast_p)):
        for i, (gr, fr) in enumerate(zip(graph_rows, fast_rows)):
            d = first_diff(gr, fr)
            if d is not None:
                ident = f"s{i + 1:02d}" if label == "string" else passages["passages"][i]["pid"]
                diffs.append({"input": ident, "at": d, "len_graph": len(gr), "len_fast": len(fr),
                              "graph": ref.tok.convert_ids_to_tokens(gr[max(0, d - 1):d + 3]),
                              "fast": ref.tok_fast.convert_ids_to_tokens(fr[max(0, d - 1):d + 3])})
    report["fast_tokenizer_agreement"] = {
        "strings": sum(x == y for x, y in zip(g_single, fast_s)), "passages": sum(x == y for x, y in zip(o_ids, fast_p)),
        "of": [len(strings), len(texts)], "differences": diffs,
        "note": "reference for the gates is the slow tokenizer; listed here so every divergence is visible"}

    # ---- norms and batch sensitivity (information)
    all_out = np.concatenate([e_single, e_batch, o_single])
    norms = np.linalg.norm(all_out, axis=1)
    worst_norm = float(np.max(np.abs(norms - 1.0)))
    gate(gates, "unit_norm", round(worst_norm, 8), NORM_TOL, worst_norm < NORM_TOL)
    o_batch, _ = run_batched(sess, texts)
    report["batch_vs_single"] = {"strings": stats(cos_rows(e_single, e_batch)),
                                 "passages": stats(cos_rows(o_single, o_batch)),
                                 "note": "dynamic INT8 quantisation scales per batch; not gated"}

    # ---- Oracle reference: the recipe proof (M1 rebuilt) or the M1Q splice proof
    if a.oracle_ref:
        oref = Path(os.path.expanduser(a.oracle_ref))
        osess = session_with_ids(oref, a.threads)
        oe_s, og_s = run_single(osess, [prefix + s for s in strings])
        oe_p, og_p = run_single(osess, [prefix + t for t in texts])
        ids_eq = sum(x == y for x, y in zip(g_single, og_s)) + sum(x == y for x, y in zip(o_ids, og_p))
        c_or = cos_rows(np.concatenate([e_single, o_single]), np.concatenate([oe_s, oe_p]))
        c_or_hf = cos_rows(oe_p, hf_emb)
        import onnx
        om = onnx.load(str(oref))
        mine_m = onnx.load(str(model_path))
        wd_mine, wd_ora = weight_digest(mine_m), weight_digest(om)
        del mine_m
        bit_same = int(sum(np.array_equal(x, y) for x, y in zip(np.concatenate([e_single, o_single]),
                                                                  np.concatenate([oe_s, oe_p]))))
        report["oracle_ref"] = {
            "file": oref.name, "sha256": conv.sha256_file(oref), "input_prefix": prefix,
            "token_ids_equal": ids_eq, "token_ids_total": len(strings) + len(texts),
            "cosine_vs_oracle": stats(c_or), "bit_identical_outputs": bit_same,
            "large_weights": {"mine": sum(wd_mine.values()), "oracle": sum(wd_ora.values()),
                              "byte_identical": sum((wd_mine & wd_ora).values())},
            "oracle_vs_hf_fp32": stats(c_or_hf),
            "oracle_vs_hf_fp32_by_lang": {lg: stats([x for x, l2 in zip(c_or_hf, langs) if l2 == lg])
                                          for lg in sorted(set(langs))},
            "oracle_op_counts": conv.op_counts(om)}
        if build.get("reuse_template_transformer"):
            mine = onnx.load(str(model_path))
            oi = {i.name: i.raw_data for i in om.graph.initializer}
            same = all(oi.get(i.name) == i.raw_data for i in mine.graph.initializer) and \
                len(mine.graph.initializer) == len(om.graph.initializer)
            report["oracle_ref"]["transformer_initializers_identical"] = same
            gate(gates, "transformer_identical_to_template", same, True, same)
            del mine
        del om
        total = len(strings) + len(texts)
        gate(gates, "oracle_token_ids_equal", ids_eq, total, ids_eq == total)
        gate(gates, "cosine_vs_oracle_mean_ge_0.999", round(float(c_or.mean()), 6), GATE_ORACLE_MEAN,
             c_or.mean() >= GATE_ORACLE_MEAN)
        report["token_id_divergence"]["vs_oracle"] = (
            [f"s{i + 1:02d}" for i, (x, y) in enumerate(zip(g_single, og_s)) if x != y]
            + [p["pid"] for p, x, y in zip(passages["passages"], o_ids, og_p) if x != y])

    # the operator's exceptions: token-id gates waived and reported; the cosine gates above still decide
    if a.allow_token_divergence:
        waived = waive_token_gates(gates, key)
        report["token_id_waiver"] = {"key": key, "reason": ACCEPTED_TOKEN_DIVERGENCE[key], "waived_gates": waived,
                                     "cosine_gates_enforced": True}
        if waived:
            log.warning("%s: token-id gates waived by operator exception: %s", key, ", ".join(waived))

    report["gates"] = gates
    report["passed"] = all(g["pass"] for g in gates.values())
    report["seconds"] = round(time.monotonic() - t0, 1)
    conv.assert_no_abs_paths(report, "parity report")
    rdir = Path(os.path.expanduser(a.report_dir))
    rdir.mkdir(parents=True, exist_ok=True)
    rpath = rdir / f"{key}_parity.json"
    with open(rpath, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=str)
        f.write("\n")
    failed = [k for k, g in gates.items() if not g["pass"]]
    waived = [k for k, g in gates.items() if g.get("waived")]
    status = ("PASSED" if not failed else "FAILED " + ", ".join(failed)) + \
        (f" (waived: {', '.join(waived)})" if waived else "")
    log.info("%s: %s; cosine vs HF FP32 mean %.4f min %.4f (%s); token ids %d/%d single, %d/%d batched; report %s",
             key, status, c.mean(), c.min(),
             ", ".join(f"{lg} {by_lang[lg]['mean']:.4f}/{by_lang[lg]['min']:.4f}" for lg in GATE_LANGS if lg in by_lang),
             n_single, len(cases), n_batch, len(cases), conv.tilde(rpath))
    return 0 if not failed else 1


# ----------------------------------------------------------------------------- refvec (probe P2)
def cap_utf8(text: str, max_bytes: int) -> str:
    """Longest prefix of whole characters whose UTF-8 size is at most max_bytes."""
    out, n = [], 0
    for ch in text:
        b = len(ch.encode("utf-8"))
        if n + b > max_bytes:
            break
        out.append(ch)
        n += b
    return "".join(out)


def strings_sha256(texts) -> str:
    """Identity of the parity strings; 04_probes.py pins the same value (PARITY_STRINGS_SHA256)."""
    return hashlib.sha256(json.dumps(list(texts), ensure_ascii=False).encode("utf-8")).hexdigest()


def embed_one(sess, text: str) -> list:
    """One text, one call: dynamic INT8 scales per batch, so batching would move the vector."""
    out = sess.run(["embedding"], {"input": np.array([text], dtype=object)})[0]
    if out.shape[0] != 1 or not np.all(np.isfinite(out)):
        raise conv.ConversionError(f"embedding of one text has shape {out.shape} or non-finite values")
    return out[0].astype(np.float32).tolist()          # float32 values, exact as JSON numbers


def ref_items(embed, strings=TOKEN_STRINGS, cap_bytes: int = REFVEC_CAP_BYTES) -> tuple:
    """embed(text) -> vector, called once per text. Returns (items, dim)."""
    items = []
    for i, (lang, s) in enumerate(strings, 1):
        it = {"i": i, "lang": lang, "bytes": len(s.encode("utf-8")), "text": s, "vector": list(embed(s))}
        if it["bytes"] > cap_bytes:
            t = cap_utf8(s, cap_bytes)
            it["capped"] = {"bytes": len(t.encode("utf-8")), "text": t, "vector": list(embed(t))}
        items.append(it)
    dims = {len(it["vector"]) for it in items} | {len(it["capped"]["vector"]) for it in items if "capped" in it}
    if len(dims) != 1:
        raise conv.ConversionError(f"reference vectors have different dimensions: {sorted(dims)}")
    return items, dims.pop()


def refvec_key(key, out: str) -> str:
    k = (key or "").upper()
    if not k:
        m = re.match(r"^([A-Za-z0-9]+)_refvec\.json$", Path(out).name)
        k = m.group(1).upper() if m else ""
    if not re.match(REFVEC_KEY_RE, k):
        raise conv.ConversionError("give --key (M0..M6, M1Q) or name the output <KEY>_refvec.json")
    return k


def cmd_refvec(a) -> int:
    key = refvec_key(a.key, a.out)
    model_path = Path(os.path.expanduser(a.model)).resolve()
    if not model_path.is_file():
        raise conv.ConversionError(f"model file not found: {model_path.name}")
    if not 1 <= a.threads <= conv.MAX_THREADS:
        raise conv.ConversionError(f"--threads must be 1..{conv.MAX_THREADS}")
    import onnxruntime
    import onnxruntime_extensions
    ort_v = onnxruntime.__version__
    if ort_v != DB_ORT_VERSION and not a.allow_runtime:
        raise conv.ConversionError(f"onnxruntime {ort_v}: the reference must come from {DB_ORT_VERSION}, the "
                                   "database's version (--allow-runtime overrides, for tests)")
    t0 = time.monotonic()
    sess = conv.ort_session(str(model_path), a.threads)
    items, dim = ref_items(lambda t: embed_one(sess, t))
    doc = {"kind": "refvec", "version": REFVEC_VERSION, "verify_version": VERIFY_VERSION, "key": key,
           "created_utc": utc_now(), "model": model_path.name, "model_bytes": model_path.stat().st_size,
           "model_sha256": conv.sha256_file(model_path),
           "runtime": {"onnxruntime": ort_v, "onnxruntime_extensions": onnxruntime_extensions.__version__,
                       "threads": a.threads},
           "one_text_per_call": True, "cap_bytes": REFVEC_CAP_BYTES, "n": len(items), "dim": dim,
           "strings_sha256": strings_sha256(s for _, s in TOKEN_STRINGS), "items": items,
           "seconds": round(time.monotonic() - t0, 1)}
    conv.assert_no_abs_paths(doc, "refvec")
    out = Path(os.path.expanduser(a.out))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".partial")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    log.info("%s: %d reference vectors (%d capped at %d bytes), dim %d, from %s (sha256 %s) -> %s", key, len(items),
             sum("capped" in it for it in items), REFVEC_CAP_BYTES, dim, model_path.name, doc["model_sha256"][:16],
             conv.tilde(out))
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")
    logging.Formatter.converter = time.gmtime
    log.setLevel(logging.INFO)
    conv.log.setLevel(logging.INFO)
    ap = argparse.ArgumentParser(description="Parity gates for converted embedding models.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("passages", help="build the deterministic parity passage set")
    p.add_argument("--src", required=True, help="corpus/src directory (india/, gulf/)")
    p.add_argument("--out", required=True)
    p.add_argument("--n-en", type=int, default=25)
    p.add_argument("--n-ar", type=int, default=25)
    v = sub.add_parser("verify", help="run the parity gates on one converted model")
    v.add_argument("--key", required=True, help="model key, e.g. M6")
    v.add_argument("--model", required=True)
    v.add_argument("--build-info", default=None, help="default: <model>.build.json")
    v.add_argument("--passages", required=True)
    v.add_argument("--report-dir", default=os.path.expanduser("~/rag-lab/reports"))
    v.add_argument("--oracle-ref", default=None, help="Oracle prebuilt model to compare with (recipe proof, M1Q)")
    v.add_argument("--cache-dir", default=os.path.expanduser("~/rag-lab/hf/cache"))
    v.add_argument("--threads", type=int, default=conv.MAX_THREADS)
    v.add_argument("--allow-runtime", action="store_true", help="do not require onnxruntime 1.20.1")
    v.add_argument("--allow-token-divergence", action="store_true",
                   help="M6 and M1Q only (operator decision 29-Sep): report and waive the token-id gates; "
                        "every cosine gate still applies")
    r = sub.add_parser("refvec", help="per-string reference vectors of one ONNX file for probe P2 (one per call)")
    r.add_argument("--model", required=True, help="the ONNX file the database loads (Oracle's own for M0 and M1)")
    r.add_argument("--out", required=True, help="<KEY>_refvec.json")
    r.add_argument("--key", default=None, help="model key; default: taken from the --out file name")
    r.add_argument("--threads", type=int, default=conv.MAX_THREADS)
    r.add_argument("--allow-runtime", action="store_true", help="do not require onnxruntime 1.20.1")
    a = ap.parse_args(argv)
    try:
        return {"passages": cmd_passages, "verify": cmd_verify, "refvec": cmd_refvec}[a.cmd](a)
    except (conv.ConversionError, ValueError, OSError, KeyError) as e:
        log.error("%s failed: %s: %s", a.cmd, type(e).__name__, e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
