#!/usr/bin/env python3
# v1.3 - brief 09: turn a Hugging Face sentence-embedding model into ONE ONNX file that Oracle AI
#        Database 26ai can load with DBMS_VECTOR.LOAD_ONNX_MODEL: Oracle's own tokenizer section
#        (cut from its prebuilt model) joined to an INT8 transformer exported here.
#        v1.3: --keep-fp32 REGEX keeps the matching MatMul nodes out of INT8 (nodes_to_exclude). Per-tensor
#              INT8 of every MatMul cost e5-base 0.968/0.948 and per-channel 0.905/0.867; the damage sits in
#              the feed-forward output projections (weight outliers), as measured on M6 (0.48 -> 0.986).
#        v1.2: external-data FP32 exports (>2 GB: bge-m3, arctic, e5-large) failed in gather_table_overrides -
#              each tensor was loaded by hand and then numpy_helper.to_array() tried to load it again relative
#              to the working directory; now one load, with base_dir. New opt-in --per-channel (INT8 MatMul
#              weights per output channel): per-tensor cost e5-base cosine 0.968/0.948 against gates 0.98/0.95.
#        v1.1 (Codex review): the sidecar stores the snapshot relative to the cache directory; an absolute
#              path under a path key, or the home directory anywhere, stops the write (assert_no_abs_paths).
#
# Run as : the build host's OS user, with ~/rag-lab/venv (torch 2.6.0+cpu, transformers 4.44.0,
#          onnx 1.17.0, onnxruntime 1.20.1, onnxruntime-extensions 0.13.0). No database, no docker,
#          no sudo. Normally called by run_conversions.sh, one model at a time.
# Usage  : convert_hf_to_oracle.py --hf-id intfloat/multilingual-e5-base --revision <40-hex commit> \
#            --model-name MULTILINGUAL_E5_BASE --pooling mean --max-tokens 512 --template e5s \
#            --out ~/rag-lab/converted/multilingual_e5_base.onnx
#          BERT WordPiece model with its own vocabulary (AraBERT):
#            ... --template minilm --lower 0 --strip-accents 0 --sep-id 3 --pad-id 0 [--bert-vocab vocab.txt]
#          e5 "query: " variant of Oracle's own e5-small (M1Q): the transformer is Oracle's, untouched:
#            ... --template e5s --reuse-template-transformer --prefix-ids 41,1294,12 --prefix-text "query: "
# Re-run : safe. Work files under --work-dir are replaced; the result is written to <out>.partial
#          and renamed to <out> only after every check below has passed.
#
# Recipe (PLAN.md section 3):
#   1. snapshot_download pinned to a commit sha (a branch or tag name is refused);
#   2. torch module = HF model + pooling (mean | cls) + L2 normalisation, exported to ONNX opset 17 with
#      dynamic batch and sequence axes (external data is used automatically above 2 GB);
#   3. quantize_dynamic: INT8 per-tensor on MatMul and Gather, the pattern found in Oracle's
#      multilingual_e5_small.onnx (int8 MatMul weights, uint8 Gather tables). The embedding tables get the
#      legacy symmetric [0, 255] scale/zero point Oracle's file carries (--gather-quant oracle), set through
#      TensorQuantOverrides; with it the e5-small rebuild is byte-identical in every weight to Oracle's.
#      onnxruntime's quant_pre_process is opt-in (--preprocess): on these exports it stops with
#      "Incomplete symbolic shape inference";
#   4. the tokenizer section of Oracle's template (input 'input' -> 'input_ids', 'attention_mask'
#      [, 'token_type_ids']) is cut out and its truncation constants set to --max-tokens;
#      for the minilm template the BertTokenizer vocabulary and flags and the [SEP]/[PAD] ids are swapped;
#   5. onnx.compose.merge_models, IR 8, output 'embedding' [batch_size, dim], onnx.checker, < 2 GB.
# Guards: the template's SentencePiece model must be byte-identical to the snapshot's
# sentencepiece.bpe.model; special-token ids, pooling, dimension and token cap are cross-checked with
# the model's own config files; the template constants must hold their expected values before rewrite.
# Nothing here reads, prints or stores a credential. Paths in the JSON sidecar are written relative to ~.
import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

CONVERTER_VERSION = "1.1"
log = logging.getLogger("convert_hf_to_oracle")

MAX_FILE_BYTES = 2_000_000_000         # Oracle: "The model size is limited to 2 GB" (kept below 2e9)
TARGET_IR = 8
TARGET_OPSET = 17
MAX_THREADS = 6                        # host politeness rule
PINNED = {"torch": "2.6.0", "transformers": "4.44.0", "onnx": "1.17.0",
          "onnxruntime": "1.20.1", "onnxruntime_extensions": "0.13.0"}
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
MODEL_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
HF_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")

# What each Oracle template must look like before it is touched (values read from the files, 29-Sep).
TEMPLATES = {
    "e5s": {
        "file": "multilingual_e5_small.onnx",
        "outputs": ["input_ids", "attention_mask"],
        "trunc_consts": ["ids_trunc_max_length_const", "mask_trunc_max_length_const"],
        "trunc_value": 512, "eos": 2, "pad": 1, "bos": 0,
        "tokenizer_op": "SentencepieceTokenizer",
    },
    "minilm": {
        "file": "all_MiniLM_L12_v2.onnx",
        "outputs": ["input_ids", "attention_mask", "token_type_ids"],
        "trunc_consts": ["ids_trunc_max_length_const", "mask_trunc_max_length_const",
                         "token_type_trunc_max_length_const"],
        "trunc_value": 256, "eos": 102, "pad": 0, "bos": 101,
        "tokenizer_op": "BertTokenizer",
    },
}
EOS_CONST = "ids_trunc_eos_const"
PAD_CONST = "ids_trunc_pad_const"
PAD_FILL_NODE = "padding_op"           # ConstantOfShape inside the padding SequenceMap body


class ConversionError(RuntimeError):
    """A check failed; the message says which one."""


# ----------------------------------------------------------------------------- small helpers
def tilde(p) -> str:
    """Path as text with the home directory replaced by '~' (reports must not carry /home/<user>)."""
    s = str(p)
    home = str(Path.home())
    return "~" + s[len(home):] if s == home or s.startswith(home + os.sep) else s


PATH_KEYS = {"snapshot", "file", "model", "source", "template_file", "weights_file"}


def assert_no_abs_paths(obj, where: str = "document") -> None:
    """Fail closed before a JSON document leaks host detail: an absolute path under a path-bearing key, or
    this user's home directory anywhere. Tokens and texts elsewhere (a WordPiece token "/", a date
    "01/04") are legitimate and not checked against the leading-slash rule."""
    home = str(Path.home())
    stack = [(None, obj)]
    while stack:
        key, o = stack.pop()
        if isinstance(o, dict):
            stack.extend(o.items())
        elif isinstance(o, (list, tuple)):
            stack.extend((key, x) for x in o)
        elif isinstance(o, str):
            if (len(home) > 1 and home in o) or (key in PATH_KEYS and o.startswith(("/", "\\"))):
                raise ConversionError(f"{where} would carry a host path under {key!r}: {o[:60]!r}")


def sha256_file(path, bufsize=1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mem_available_gb() -> float:
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / (1024 * 1024)
    except OSError as e:
        raise ConversionError(f"cannot read /proc/meminfo: {e}") from e
    raise ConversionError("MemAvailable not found in /proc/meminfo")


def library_versions() -> dict:
    import onnxruntime
    import onnxruntime_extensions
    import torch
    import transformers
    return {"python": platform.python_version(), "torch": torch.__version__,
            "transformers": transformers.__version__, "onnx": onnx.__version__,
            "onnxruntime": onnxruntime.__version__,
            "onnxruntime_extensions": onnxruntime_extensions.__version__}


def check_pinned_versions(versions: dict) -> None:
    bad = [f"{k} {versions.get(k)} (want {v})" for k, v in PINNED.items()
           if not str(versions.get(k, "")).split("+")[0] == v]
    if bad:
        raise ConversionError("library versions differ from the pinned set: " + "; ".join(bad))


def validate_args_common(hf_id: str, revision: str) -> None:
    if not HF_ID_RE.match(hf_id or ""):
        raise ConversionError(f"--hf-id is not an org/name Hugging Face id: {hf_id!r}")
    if not REVISION_RE.match(revision or ""):
        raise ConversionError("--revision must be a 40-hex commit sha (branch and tag names are refused)")


def parse_int_list(text: str) -> list:
    try:
        vals = [int(x) for x in text.split(",") if x.strip() != ""]
    except ValueError as e:
        raise ConversionError(f"not a comma-separated integer list: {text!r}") from e
    if not vals or any(v < 0 for v in vals):
        raise ConversionError(f"need one or more non-negative ids: {text!r}")
    return vals


# ----------------------------------------------------------------------------- Hugging Face side
META_FILES = ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
              "sentencepiece.bpe.model", "vocab.txt", "modules.json", "sentence_bert_config.json",
              "1_Pooling/config.json", "config_sentence_transformers.json"]


def download_snapshot(hf_id: str, revision: str, cache_dir: Path, with_weights: bool = True) -> tuple:
    """Pinned snapshot with only the files this recipe needs. Returns (snapshot dir, weights file name)."""
    from huggingface_hub import HfApi, snapshot_download
    try:
        files = set(HfApi().list_repo_files(hf_id, revision=revision))
    except Exception as e:  # network / auth / unknown revision
        raise ConversionError(f"cannot list {hf_id}@{revision[:12]}: {type(e).__name__}: {e}") from e
    weights = "model.safetensors" if "model.safetensors" in files else (
        "pytorch_model.bin" if "pytorch_model.bin" in files else None)
    if with_weights and weights is None:
        raise ConversionError(f"{hf_id}: neither model.safetensors nor pytorch_model.bin in the repo")
    allow = [f for f in META_FILES if f in files] + ([weights] if with_weights else [])
    try:
        snap = snapshot_download(hf_id, revision=revision, cache_dir=str(cache_dir), allow_patterns=allow)
    except Exception as e:
        raise ConversionError(f"download of {hf_id}@{revision[:12]} failed: {type(e).__name__}: {e}") from e
    return Path(snap), weights


def read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def st_settings(snap: Path) -> dict:
    """Pooling, token cap and module list from the sentence-transformers files of the snapshot."""
    out = {"modules": [], "pooling": None, "max_seq_length": None}
    if (snap / "modules.json").exists():
        out["modules"] = [m.get("type", "") for m in read_json(snap / "modules.json")]
    unsupported = [m for m in out["modules"] if m.rsplit(".", 1)[-1] not in ("Transformer", "Pooling", "Normalize")]
    if unsupported:
        raise ConversionError(f"sentence-transformers modules not supported by this recipe: {unsupported}")
    pc = snap / "1_Pooling" / "config.json"
    if pc.exists():
        p = read_json(pc)
        modes = [k for k in ("pooling_mode_cls_token", "pooling_mode_mean_tokens", "pooling_mode_max_tokens",
                             "pooling_mode_mean_sqrt_len_tokens", "pooling_mode_weightedmean_tokens",
                             "pooling_mode_lasttoken") if p.get(k)]
        if modes == ["pooling_mode_cls_token"]:
            out["pooling"] = "cls"
        elif modes == ["pooling_mode_mean_tokens"]:
            out["pooling"] = "mean"
        else:
            raise ConversionError(f"pooling modes {modes} are not mean-only or cls-only")
    sb = snap / "sentence_bert_config.json"
    if sb.exists():
        out["max_seq_length"] = read_json(sb).get("max_seq_length")
    return out


def check_model_config(cfg, max_tokens: int, st_max) -> None:
    """The token cap must fit the position table (XLM-R keeps padding_idx + 1 positions aside)."""
    usable = cfg.max_position_embeddings - (cfg.pad_token_id + 1 if cfg.model_type == "xlm-roberta" else 0)
    if max_tokens > usable:
        raise ConversionError(f"--max-tokens {max_tokens} > usable positions {usable} ({cfg.model_type})")
    if st_max and max_tokens > int(st_max):
        raise ConversionError(f"--max-tokens {max_tokens} > the model's max_seq_length {st_max}")


# ----------------------------------------------------------------------------- torch export
def build_embedder(hf_model, pooling: str):
    import torch

    class Embedder(torch.nn.Module):
        """HF encoder -> pooled -> L2-normalised sentence embedding (what Oracle's graphs compute)."""

        def __init__(self, m, pool):
            super().__init__()
            self.m, self.pool = m, pool

        def forward(self, input_ids, attention_mask, token_type_ids=None):
            kw = {"input_ids": input_ids, "attention_mask": attention_mask}
            if token_type_ids is not None:
                kw["token_type_ids"] = token_type_ids
            h = self.m(**kw).last_hidden_state
            if self.pool == "cls":
                x = h[:, 0]
            else:
                w = attention_mask.unsqueeze(-1).to(h.dtype)
                x = (h * w).sum(dim=1) / w.sum(dim=1).clamp(min=1e-9)
            return x / x.norm(p=2, dim=1, keepdim=True).clamp(min=1e-12)

    return Embedder(hf_model, pooling).eval()


def export_fp32(snap: Path, pooling: str, with_token_type: bool, out_path: Path, threads: int) -> int:
    """Export the pooled model to ONNX opset 17. Returns the embedding dimension."""
    import torch
    from transformers import AutoModel, AutoTokenizer
    torch.set_num_threads(threads)
    tok = AutoTokenizer.from_pretrained(str(snap), local_files_only=True)
    # eager attention: no SDPA fast paths that branch on the mask content while tracing
    model = AutoModel.from_pretrained(str(snap), local_files_only=True, torch_dtype=torch.float32,
                                      attn_implementation="eager")
    emb = build_embedder(model, pooling)
    # two rows of different length so the traced graph sees real padding
    enc = tok(["Annual leave policy for the Gulf region.", "سياسة الإجازة السنوية والعطلات الرسمية في منطقة الخليج"],
              padding=True, return_tensors="pt")
    names = ["input_ids", "attention_mask"]
    args = [enc["input_ids"], enc["attention_mask"]]
    if with_token_type:
        names.append("token_type_ids")
        args.append(torch.zeros_like(enc["input_ids"]))
    axes = {n: {0: "batch_size", 1: "sequence"} for n in names}
    axes["embedding"] = {0: "batch_size"}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with torch.no_grad():
        ref = emb(*args)
        torch.onnx.export(emb, tuple(args), str(out_path), input_names=names, output_names=["embedding"],
                          dynamic_axes=axes, opset_version=TARGET_OPSET, do_constant_folding=True,
                          dynamo=False)
    dim = int(ref.shape[1])
    if dim != int(model.config.hidden_size):
        raise ConversionError(f"exported dimension {dim} != hidden_size {model.config.hidden_size}")
    del model, emb
    return dim


def legacy_symmetric_uint8(w: np.ndarray) -> tuple:
    """Scale and zero point that onnxruntime's older compute_scale_zp gives a symmetric uint8 range
    [0, 255] (Python-float arithmetic). onnxruntime 1.20.1 spreads a symmetric range over 254 steps
    instead, so it cannot produce these values itself. They reproduce the three embedding tables of
    Oracle's multilingual_e5_small.onnx byte for byte (checked 29-Sep; README "Recipe proof")."""
    rmin, rmax = min(float(w.min()), 0.0), max(float(w.max()), 0.0)
    absmax = max(abs(rmin), abs(rmax))
    if absmax == 0.0:
        return np.float32(1.0), 0
    rmin, rmax = -absmax, absmax
    scale = (rmax - rmin) / 255.0
    return np.float32(scale), int(round(0 - rmin / scale))


def gather_table_overrides(fp32_path: Path) -> dict:
    """TensorQuantOverrides pinning every float Gather table to the legacy symmetric uint8 parameters.
    External data is read one tensor at a time, so a 2 GB export is never held twice."""
    m = onnx.load(str(fp32_path), load_external_data=False)
    inits = {i.name: i for i in m.graph.initializer}
    names = sorted({n.input[0] for n in m.graph.node if n.op_type == "Gather" and n.input[0] in inits
                    and inits[n.input[0]].data_type == TensorProto.FLOAT})
    if not names:
        raise ConversionError("no float Gather tables found in the FP32 export")
    out = {}
    for name in names:
        t = inits[name]
        # One load only: to_array() reads external data itself when given base_dir. Loading it by hand
        # first left the external-data fields in place, so to_array() loaded again relative to the cwd.
        scale, zp = legacy_symmetric_uint8(numpy_helper.to_array(t, base_dir=str(fp32_path.parent)))
        if not 0 <= zp <= 255:
            raise ConversionError(f"zero point {zp} out of uint8 range for {name}")
        out[name] = [{"scale": np.array(scale, dtype=np.float32), "zero_point": np.array(zp, dtype=np.uint8)}]
        t.ClearField("raw_data")
    return out


def fp32_keep_nodes(fp32_path: Path, pattern: str) -> list:
    """Names of the MatMul nodes whose name matches pattern (re.search); they stay FP32. A pattern that
    matches nothing is an error, so a typo never silently quantises everything."""
    m = onnx.load(str(fp32_path), load_external_data=False)
    rx = re.compile(pattern)
    names = sorted(n.name for n in m.graph.node if n.op_type == "MatMul" and n.name and rx.search(n.name))
    if not names:
        raise ConversionError(f"--keep-fp32 {pattern!r} matches no MatMul node in the FP32 export")
    return names


def quantize_int8(fp32_path: Path, work: Path, preprocess: bool, gather_quant: str,
                  per_channel: bool = False, keep_fp32: list = None) -> tuple:
    """quant_pre_process (optional) + quantize_dynamic, INT8 per-tensor on MatMul and Gather.

    MatMul weights: int8, symmetric, per tensor (onnxruntime default for QInt8) - byte-identical to
    Oracle's e5-small. Gather tables (word/position/token-type embeddings) are quantised with the
    activation type (uint8); gather_quant picks their range:
      oracle     legacy symmetric uint8 over 255 steps, via TensorQuantOverrides (Oracle's files);
      symmetric  onnxruntime 1.20.1 ActivationSymmetric (254 steps);
      asymmetric onnxruntime 1.20.1 default (min/max).
    In dynamic mode ActivationSymmetric reaches nothing else in these graphs (no Relu/Clip is quantised).
    per_channel=True quantises MatMul weights per output channel (opt-in; Oracle's e5-small is per tensor).
    Returns (INT8 model path, {table: [scale, zero point]}).
    """
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from onnxruntime.quantization.shape_inference import quant_pre_process
    big = fp32_path.stat().st_size > MAX_FILE_BYTES or any(
        p.suffix != ".onnx" for p in fp32_path.parent.iterdir())     # torch wrote external data
    src = fp32_path
    if preprocess:
        pre_dir = work / "pre"
        shutil.rmtree(pre_dir, ignore_errors=True)
        pre_dir.mkdir(parents=True)
        pre = pre_dir / "model_pre.onnx"
        try:
            quant_pre_process(str(fp32_path), str(pre), skip_optimization=False, skip_onnx_shape=False,
                              skip_symbolic_shape=False, save_as_external_data=big, all_tensors_to_one_file=True,
                              external_data_location="model_pre.data" if big else None)
        except Exception as e:  # e.g. "Incomplete symbolic shape inference"
            raise ConversionError(f"quant_pre_process failed: {type(e).__name__}: {e}") from e
        if not pre.is_file():   # it logs, rather than raises, some optimiser failures
            raise ConversionError("quant_pre_process wrote no output")
        src = pre
    extra = {"ActivationSymmetric": gather_quant == "symmetric"}
    tables = {}
    if gather_quant == "oracle":
        overrides = gather_table_overrides(src)
        extra["TensorQuantOverrides"] = overrides
        tables = {k: [float(v[0]["scale"]), int(v[0]["zero_point"])] for k, v in overrides.items()}
    out = work / "model_int8.onnx"
    for p in (out, work / "model_int8.onnx.data"):
        if p.exists():
            p.unlink()
    try:
        quantize_dynamic(str(src), str(out), op_types_to_quantize=["MatMul", "Gather"], per_channel=per_channel,
                         nodes_to_exclude=list(keep_fp32 or []),
                         reduce_range=False, weight_type=QuantType.QInt8, use_external_data_format=False,
                         extra_options=extra)
    except Exception as e:  # onnxruntime raises plain Exceptions here
        raise ConversionError(f"quantize_dynamic failed: {type(e).__name__}: {e}") from e
    if not out.is_file():
        raise ConversionError("quantize_dynamic wrote no output")
    return out, tables


# ----------------------------------------------------------------------------- graph surgery
def _defined_names(g: onnx.GraphProto) -> set:
    names = {i.name for i in g.input} | {i.name for i in g.initializer}
    for n in g.node:
        names.update(o for o in n.output if o)
    return names


def _subgraphs(node: onnx.NodeProto):
    for a in node.attribute:
        if a.type == onnx.AttributeProto.GRAPH:
            yield a.g
        elif a.type == onnx.AttributeProto.GRAPHS:
            yield from a.graphs


def _free_names(g: onnx.GraphProto) -> set:
    """Names a (sub)graph reads from its enclosing scope."""
    defined = _defined_names(g)
    used = set()
    for n in g.node:
        used.update(i for i in n.input if i)
        for sg in _subgraphs(n):
            used |= _free_names(sg)
    return used - defined


def _node_reads(node: onnx.NodeProto) -> set:
    r = {i for i in node.input if i}
    for sg in _subgraphs(node):
        r |= _free_names(sg)
    return r


def _walk_nodes(g: onnx.GraphProto):
    for n in g.node:
        yield n
        for sg in _subgraphs(n):
            yield from _walk_nodes(sg)


def extract_tokenizer(template: onnx.ModelProto, outputs: list) -> onnx.ModelProto:
    """Cut the part of the template that turns 'input' into the given tensors (nodes kept verbatim)."""
    g = template.graph
    producer = {}
    for idx, n in enumerate(g.node):
        for o in n.output:
            producer[o] = idx
    keep, stack = set(), list(outputs)
    for o in outputs:
        if o not in producer:
            raise ConversionError(f"template has no tensor named {o!r}")
    while stack:
        t = stack.pop()
        idx = producer.get(t)
        if idx is None or idx in keep:
            continue
        keep.add(idx)
        stack.extend(_node_reads(g.node[idx]))
    nodes = [g.node[i] for i in sorted(keep)]
    reads = set()
    for n in nodes:
        reads |= _node_reads(n)
    produced = {o for n in nodes for o in n.output}
    inits = [i for i in g.initializer if i.name in reads]
    init_names = {i.name for i in inits}
    graph_inputs = [i for i in g.input if i.name in reads]
    unresolved = reads - produced - init_names - {i.name for i in graph_inputs}
    if unresolved:
        raise ConversionError(f"tokenizer section reads tensors it does not define: {sorted(unresolved)}")
    if [i.name for i in graph_inputs] != ["input"]:
        raise ConversionError(f"tokenizer section inputs are {[i.name for i in graph_inputs]}, expected ['input']")
    outs = [helper.make_tensor_value_info(o, TensorProto.INT64, ["batch_size", "sequence"]) for o in outputs]
    new_g = helper.make_graph(nodes, "oracle_tokenizer", graph_inputs, outs, initializer=inits)
    domains = {n.domain for n in _walk_nodes(new_g)}
    versions = {}
    for op in template.opset_import:           # the template lists some domains twice; keep one each
        versions.setdefault(op.domain, op.version)
    opsets = [helper.make_opsetid(d, versions[d]) for d in sorted(domains | {""}) if d in versions]
    m = helper.make_model(new_g, opset_imports=opsets, producer_name="oracle-template-tokenizer")
    m.ir_version = TARGET_IR
    return m


def _find_constant(g: onnx.GraphProto, out_name: str) -> onnx.NodeProto:
    hits = [n for n in _walk_nodes(g) if n.op_type == "Constant" and out_name in n.output]
    if len(hits) != 1:
        raise ConversionError(f"expected one Constant producing {out_name!r}, found {len(hits)}")
    return hits[0]


def _get_attr(node: onnx.NodeProto, name: str) -> onnx.AttributeProto:
    for a in node.attribute:
        if a.name == name:
            return a
    raise ConversionError(f"node {node.name or node.op_type} has no attribute {name!r}")


def _set_attr(node: onnx.NodeProto, name: str, value) -> None:
    kept = [a for a in node.attribute if a.name != name]
    if len(kept) == len(node.attribute):
        raise ConversionError(f"node {node.name or node.op_type} has no attribute {name!r}")
    del node.attribute[:]
    node.attribute.extend(kept + [helper.make_attribute(name, value)])


def const_value(g: onnx.GraphProto, out_name: str):
    n = _find_constant(g, out_name)
    a = n.attribute[0]
    if a.name == "value_ints":
        return list(a.ints)
    if a.name == "value_int":
        return int(a.i)
    if a.name == "value":
        return numpy_helper.to_array(a.t).tolist()
    raise ConversionError(f"constant {out_name!r} uses unsupported attribute {a.name!r}")


def set_const(g: onnx.GraphProto, out_name: str, expected, new) -> None:
    """Rewrite a Constant after checking it still holds the value the template is known to have."""
    cur = const_value(g, out_name)
    if cur != expected:
        raise ConversionError(f"{out_name} is {cur}, expected {expected}: template differs from the one inspected")
    n = _find_constant(g, out_name)
    attr = n.attribute[0].name
    _set_attr(n, attr, [int(v) for v in new] if isinstance(new, list) else int(new))


def set_pad_fill(g: onnx.GraphProto, expected: int, pad_id: int) -> None:
    hits = [n for n in _walk_nodes(g) if n.op_type == "ConstantOfShape" and n.name == PAD_FILL_NODE]
    if len(hits) != 1:
        raise ConversionError(f"expected one ConstantOfShape named {PAD_FILL_NODE!r}, found {len(hits)}")
    t = _get_attr(hits[0], "value").t
    cur = numpy_helper.to_array(t).tolist()
    if cur != [expected]:
        raise ConversionError(f"padding fill is {cur}, expected [{expected}]")
    _set_attr(hits[0], "value", numpy_helper.from_array(np.array([pad_id], dtype=np.int64)))


def set_truncation(g: onnx.GraphProto, tpl: dict, max_tokens: int) -> None:
    for c in tpl["trunc_consts"]:
        set_const(g, c, [tpl["trunc_value"]], [max_tokens])


def tokenizer_node(g: onnx.GraphProto, op_type: str) -> onnx.NodeProto:
    hits = [n for n in _walk_nodes(g) if n.op_type == op_type]
    if len(hits) != 1:
        raise ConversionError(f"expected one {op_type} node, found {len(hits)}")
    return hits[0]


def sentencepiece_model_bytes(g: onnx.GraphProto) -> bytes:
    return _get_attr(tokenizer_node(g, "SentencepieceTokenizer"), "model").s


def set_bert_vocab(g: onnx.GraphProto, vocab_bytes: bytes, lower: int, strip_accents: int) -> None:
    n = tokenizer_node(g, "BertTokenizer")
    _get_attr(n, "vocab_file")                 # must exist before it is replaced
    _set_attr(n, "vocab_file", vocab_bytes)
    _set_attr(n, "do_lower_case", int(lower))
    _set_attr(n, "strip_accents", int(strip_accents))


def splice_prefix(g: onnx.GraphProto, prefix_ids: list) -> None:
    """Insert fixed token ids right after <s> in every row, before padding and truncation.

    Works on the e5s template layout: SequenceMap(token_seqs, max_value) whose body pads one row
    ('seq_input') to 'max_value'. The body gets Concat(row[:1], prefix, row[1:]); the outer
    max_value grows by len(prefix). Truncation still runs afterwards, so the cap includes the prefix,
    exactly as the Hugging Face tokenizer does for tok("query: " + text, truncation=True).
    """
    maps = [n for n in g.node if n.op_type == "SequenceMap" and list(n.input) == ["token_seqs", "max_value"]]
    if len(maps) != 1:
        raise ConversionError(f"expected one SequenceMap(token_seqs, max_value), found {len(maps)}")
    sm = maps[0]
    body = _get_attr(sm, "body").g
    if [i.name for i in body.input] != ["seq_input", "max_value"]:
        raise ConversionError(f"unexpected SequenceMap body inputs {[i.name for i in body.input]}")
    k = len(prefix_ids)
    c = lambda name, vals: helper.make_node(  # noqa: E731
        "Constant", [], [name], value=numpy_helper.from_array(np.array(vals, dtype=np.int64)))
    new_nodes = [
        c("pfx_ids", prefix_ids), c("pfx_zero", [0]), c("pfx_one", [1]), c("pfx_end", [np.iinfo(np.int64).max]),
        helper.make_node("Slice", ["seq_input", "pfx_zero", "pfx_one"], ["pfx_head"], name="pfx_head_slice"),
        helper.make_node("Slice", ["seq_input", "pfx_one", "pfx_end"], ["pfx_tail"], name="pfx_tail_slice"),
        helper.make_node("Concat", ["pfx_head", "pfx_ids", "pfx_tail"], ["seq_input_pfx"], axis=0,
                         name="pfx_concat"),
    ]
    for n in body.node:
        for i, name in enumerate(n.input):
            if name == "seq_input":
                n.input[i] = "seq_input_pfx"
    old = list(body.node)
    del body.node[:]
    body.node.extend(new_nodes + old)
    # outer graph: max_value + k, placed just before the SequenceMap
    idx = list(g.node).index(sm)
    add_nodes = [c("pfx_len", [k]),
                 helper.make_node("Add", ["max_value", "pfx_len"], ["max_value_pfx"], name="pfx_max_add")]
    sm.input[1] = "max_value_pfx"
    nodes = list(g.node)
    del g.node[:]
    g.node.extend(nodes[:idx] + add_nodes + nodes[idx:])


def set_output_shape(m: onnx.ModelProto, dim: int) -> None:
    outs = [o for o in m.graph.output]
    if [o.name for o in outs] != ["embedding"]:
        raise ConversionError(f"graph outputs are {[o.name for o in outs]}, expected ['embedding']")
    tt = outs[0].type.tensor_type
    if tt.elem_type != TensorProto.FLOAT:
        raise ConversionError("output 'embedding' is not float32")
    del tt.shape.dim[:]
    d0 = tt.shape.dim.add()
    d0.dim_param = "batch_size"
    d1 = tt.shape.dim.add()
    d1.dim_value = int(dim)


def op_counts(m: onnx.ModelProto) -> dict:
    counts = {}
    for n in _walk_nodes(m.graph):
        counts[n.op_type] = counts.get(n.op_type, 0) + 1
    return dict(sorted(counts.items()))


def merge(tok: onnx.ModelProto, tr: onnx.ModelProto, io: list) -> onnx.ModelProto:
    tok.ir_version = TARGET_IR
    tr.ir_version = TARGET_IR
    missing = [n for n in io if n not in {i.name for i in tr.graph.input}]
    if missing:
        raise ConversionError(f"transformer lacks inputs {missing}")
    extra = [i.name for i in tr.graph.input if i.name not in io]
    if extra:
        raise ConversionError(f"transformer has inputs the tokenizer does not feed: {extra}")
    try:
        m = onnx.compose.merge_models(tok, tr, io_map=[(n, n) for n in io])
    except Exception as e:
        raise ConversionError(f"merge_models failed: {e}") from e
    dedupe_opsets(m)
    return m


def dedupe_opsets(m: onnx.ModelProto) -> None:
    """merge_models lists a domain once per input model (Oracle's own files show the same); keep one."""
    seen = {}
    for op in m.opset_import:
        if op.domain in seen and seen[op.domain] != op.version:
            raise ConversionError(f"opset conflict for domain {op.domain!r}: {seen[op.domain]} vs {op.version}")
        seen.setdefault(op.domain, op.version)
    del m.opset_import[:]
    m.opset_import.extend(helper.make_opsetid(d, v) for d, v in seen.items())


# ----------------------------------------------------------------------------- final checks
def ort_session(model_path_or_bytes, threads: int):
    import onnxruntime as ort
    from onnxruntime_extensions import get_library_path
    so = ort.SessionOptions()
    so.register_custom_ops_library(get_library_path())
    so.intra_op_num_threads = threads
    so.inter_op_num_threads = 1
    return ort.InferenceSession(model_path_or_bytes, so, providers=["CPUExecutionProvider"])


def smoke_test(path: Path, dim: int, threads: int) -> dict:
    s = ort_session(str(path), threads)
    texts = np.array(["Annual leave is granted per calendar year.", "يحق للموظف إجازة سنوية مدفوعة الأجر.",
                      "word " * 5000], dtype=object)
    out = s.run(["embedding"], {"input": texts})[0]
    if out.shape != (3, dim):
        raise ConversionError(f"smoke test: output shape {out.shape}, expected (3, {dim})")
    norms = np.linalg.norm(out, axis=1)
    if not np.all(np.abs(norms - 1.0) < 1e-3):
        raise ConversionError(f"smoke test: embedding norms {norms.tolist()} are not 1")
    if not np.all(np.isfinite(out)):
        raise ConversionError("smoke test: non-finite values in the embedding")
    return {"shape": list(out.shape), "norms": [round(float(x), 6) for x in norms]}


def finalize(m: onnx.ModelProto, dim: int, out: Path) -> dict:
    m.ir_version = TARGET_IR
    dedupe_opsets(m)        # merged models and Oracle's own template both list the default domain twice
    default = [o.version for o in m.opset_import if o.domain == ""]
    if default != [TARGET_OPSET]:
        raise ConversionError(f"default-domain opset is {default}, expected [{TARGET_OPSET}]")
    ins = [(i.name, i.type.tensor_type.elem_type) for i in m.graph.input]
    if ins != [("input", TensorProto.STRING)]:
        raise ConversionError(f"graph inputs are {ins}, expected one string tensor named 'input'")
    set_output_shape(m, dim)
    try:
        onnx.checker.check_model(m)
    except Exception as e:
        raise ConversionError(f"onnx.checker: {e}") from e
    size = m.ByteSize()
    if size >= MAX_FILE_BYTES:
        raise ConversionError(f"merged model is {size} bytes, over the {MAX_FILE_BYTES}-byte limit")
    partial = out.with_name(out.name + ".partial")
    out.parent.mkdir(parents=True, exist_ok=True)
    onnx.save_model(m, str(partial), save_as_external_data=False)
    return {"partial": partial, "bytes": partial.stat().st_size}


# ----------------------------------------------------------------------------- main
def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Convert a HF embedding model to an Oracle-loadable ONNX file.")
    ap.add_argument("--hf-id", required=True)
    ap.add_argument("--revision", required=True, help="40-hex commit sha")
    ap.add_argument("--model-name", required=True, help="Oracle model name, e.g. BGE_M3")
    ap.add_argument("--pooling", required=True, choices=["mean", "cls"])
    ap.add_argument("--max-tokens", required=True, type=int)
    ap.add_argument("--template", required=True, choices=sorted(TEMPLATES))
    ap.add_argument("--template-dir", default=os.path.expanduser("~/rag-lab/oracle-prebuilt"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--work-dir", default=None, help="default: <out dir>/../work/<model name lower>")
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/rag-lab/hf/cache"))
    ap.add_argument("--threads", type=int, default=MAX_THREADS)
    ap.add_argument("--min-avail-gb", type=float, default=16.0)
    ap.add_argument("--quantize", choices=["int8", "none"], default="int8",
                    help="'none' keeps the FP32 transformer (as Oracle ships MiniLM); must still fit in one file")
    ap.add_argument("--gather-quant", choices=["oracle", "symmetric", "asymmetric"], default="oracle",
                    help="range of the uint8 embedding tables; 'oracle' reproduces Oracle's e5-small exactly")
    ap.add_argument("--keep-fp32", default=None, metavar="REGEX",
                    help="MatMul nodes whose name matches stay FP32, e.g. 'layer[.][0-9]+/output/dense/MatMul'")
    ap.add_argument("--per-channel", action="store_true",
                    help="INT8 MatMul weights per output channel (default per tensor, like Oracle's e5-small)")
    ap.add_argument("--preprocess", action="store_true",
                    help="run onnxruntime quant_pre_process first (off: it fails on these exports)")
    ap.add_argument("--reuse-template-transformer", action="store_true",
                    help="keep the template's own transformer (M1Q); needs --template e5s and --prefix-ids")
    ap.add_argument("--prefix-ids", default=None, help="token ids spliced after <s> (e5s only), e.g. 41,1294,12")
    ap.add_argument("--prefix-text", default=None, help="text those ids encode; cross-checked with the HF tokenizer")
    ap.add_argument("--bert-vocab", default=None, help="minilm template: vocab.txt (default: the snapshot's)")
    ap.add_argument("--lower", type=int, choices=[0, 1], default=None)
    ap.add_argument("--strip-accents", type=int, choices=[0, 1], default=None)
    ap.add_argument("--sep-id", type=int, default=None)
    ap.add_argument("--pad-id", type=int, default=None)
    return ap.parse_args(argv)


def run(a) -> int:
    validate_args_common(a.hf_id, a.revision)
    if not MODEL_NAME_RE.match(a.model_name):
        raise ConversionError("--model-name must be an upper-case Oracle name (A-Z, 0-9, _)")
    if not 1 <= a.threads <= MAX_THREADS:
        raise ConversionError(f"--threads must be 1..{MAX_THREADS}")
    if a.max_tokens < 8:
        raise ConversionError("--max-tokens must be at least 8")
    tpl = TEMPLATES[a.template]
    a.cache_dir = str(Path(os.path.expanduser(a.cache_dir)).resolve())   # '~' is never passed on literally
    out = Path(os.path.expanduser(a.out)).resolve()
    if out.suffix != ".onnx":
        raise ConversionError("--out must end in .onnx")
    work = Path(os.path.expanduser(a.work_dir)) if a.work_dir else out.parent.parent / "work" / a.model_name.lower()
    template_path = Path(os.path.expanduser(a.template_dir)) / tpl["file"]
    if not template_path.is_file():
        raise ConversionError(f"template not found: {tilde(template_path)}")
    prefix_ids = parse_int_list(a.prefix_ids) if a.prefix_ids else []
    if prefix_ids and a.template != "e5s":
        raise ConversionError("--prefix-ids is implemented for the e5s (SentencePiece) template only")
    if a.reuse_template_transformer and (a.template != "e5s" or not prefix_ids):
        raise ConversionError("--reuse-template-transformer needs --template e5s and --prefix-ids")
    bert_flags = [a.lower, a.strip_accents, a.sep_id, a.pad_id]
    if a.template == "minilm" and any(v is None for v in bert_flags):
        raise ConversionError("--template minilm needs --lower, --strip-accents, --sep-id and --pad-id")
    if a.template == "e5s" and (a.bert_vocab or any(v is not None for v in bert_flags)):
        raise ConversionError("--bert-vocab/--lower/--strip-accents/--sep-id/--pad-id apply to --template minilm only")

    versions = library_versions()
    check_pinned_versions(versions)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    log.info("model %s <- %s@%s, template %s, pooling %s, cap %d", a.model_name, a.hf_id, a.revision[:12],
             a.template, a.pooling, a.max_tokens)
    snap, weights = download_snapshot(a.hf_id, a.revision, Path(a.cache_dir),
                                      with_weights=not a.reuse_template_transformer)
    st = st_settings(snap)
    if st["pooling"] and st["pooling"] != a.pooling:
        raise ConversionError(f"--pooling {a.pooling} but the model's 1_Pooling/config.json says {st['pooling']}")
    from transformers import AutoConfig, AutoTokenizer
    cfg = AutoConfig.from_pretrained(str(snap), local_files_only=True)
    check_model_config(cfg, a.max_tokens, st["max_seq_length"])
    hf_tok = AutoTokenizer.from_pretrained(str(snap), local_files_only=True)

    template = onnx.load(str(template_path))
    template_sha = sha256_file(template_path)
    info = {"tokenizer": {}}

    # ---- tokenizer-side identity checks
    if a.template == "e5s":
        sp_file = snap / "sentencepiece.bpe.model"
        if not sp_file.is_file():
            raise ConversionError("snapshot has no sentencepiece.bpe.model; the e5s graft needs it")
        sp_tpl = sentencepiece_model_bytes(template.graph)
        if hashlib.sha256(sp_tpl).hexdigest() != sha256_file(sp_file):
            raise ConversionError("template SentencePiece model differs from the snapshot's sentencepiece.bpe.model")
        ids = (hf_tok.bos_token_id, hf_tok.eos_token_id, hf_tok.pad_token_id)
        if ids != (tpl["bos"], tpl["eos"], tpl["pad"]):
            raise ConversionError(f"HF bos/eos/pad ids {ids} != template {tpl['bos'], tpl['eos'], tpl['pad']}")
        info["tokenizer"]["sentencepiece_sha256"] = hashlib.sha256(sp_tpl).hexdigest()
        if prefix_ids:
            if a.prefix_text is None:
                raise ConversionError("--prefix-ids needs --prefix-text so the ids can be cross-checked")
            # the ids that the HF tokenizer puts between <s> and the text for prefix_text + text
            probe = hf_tok(a.prefix_text + "x")["input_ids"]
            plain = hf_tok("x")["input_ids"]
            want = probe[1:len(probe) - len(plain) + 1]
            if want != prefix_ids or probe[len(want) + 1:] != plain[1:]:
                raise ConversionError(f"--prefix-ids {prefix_ids} are not what the HF tokenizer produces "
                                      f"for {a.prefix_text!r} ({want})")
    else:
        vocab_path = Path(os.path.expanduser(a.bert_vocab)) if a.bert_vocab else snap / "vocab.txt"
        if not vocab_path.is_file():
            raise ConversionError(f"vocab file not found: {tilde(vocab_path)}")
        vocab_bytes = vocab_path.read_bytes()
        vocab = vocab_bytes.decode("utf-8").split("\n")
        if vocab and vocab[-1] == "":
            vocab = vocab[:-1]
        if len(vocab) != cfg.vocab_size:
            raise ConversionError(f"vocab has {len(vocab)} entries, config.vocab_size is {cfg.vocab_size}")
        for token, want in (("[SEP]", a.sep_id), ("[PAD]", a.pad_id)):
            if vocab.index(token) != want or hf_tok.convert_tokens_to_ids(token) != want:
                raise ConversionError(f"{token} id: vocab {vocab.index(token)}, HF "
                                      f"{hf_tok.convert_tokens_to_ids(token)}, argument {want}")
        for token in ("[CLS]", "[UNK]", "[MASK]"):
            if token not in vocab:
                raise ConversionError(f"{token} missing from the vocabulary")
        init_kw = hf_tok.init_kwargs
        hf_lower = bool(init_kw.get("do_lower_case", False))
        hf_strip = init_kw.get("strip_accents")
        hf_strip = hf_lower if hf_strip is None else bool(hf_strip)   # BERT: None follows do_lower_case
        if (bool(a.lower), bool(a.strip_accents)) != (hf_lower, hf_strip):
            raise ConversionError(f"--lower {a.lower} --strip-accents {a.strip_accents} differ from the model's "
                                  f"tokenizer (do_lower_case={hf_lower}, strip_accents={hf_strip})")
        info["tokenizer"].update({"vocab_sha256": hashlib.sha256(vocab_bytes).hexdigest(), "vocab_size": len(vocab),
                                  "do_lower_case": a.lower, "strip_accents": a.strip_accents,
                                  "sep_id": a.sep_id, "pad_id": a.pad_id})

    work.mkdir(parents=True, exist_ok=True)
    if a.reuse_template_transformer:
        # M1Q: Oracle's full e5-small graph, only the tokenizer section changes
        merged = template
        dim = int(merged.graph.output[0].type.tensor_type.shape.dim[1].dim_value)
        if a.max_tokens != tpl["trunc_value"]:
            set_truncation(merged.graph, tpl, a.max_tokens)
        splice_prefix(merged.graph, prefix_ids)
        quant = {"source": "template transformer, unchanged"}
    else:
        avail = mem_available_gb()
        if avail < a.min_avail_gb:
            raise ConversionError(f"only {avail:.1f} GB memory available, need {a.min_avail_gb} GB")
        fp32_dir = work / "fp32"
        shutil.rmtree(fp32_dir, ignore_errors=True)
        fp32 = fp32_dir / "model.onnx"
        log.info("exporting FP32 ONNX (opset %d) to %s", TARGET_OPSET, tilde(fp32))
        dim = export_fp32(snap, a.pooling, a.template == "minilm", fp32, a.threads)
        if a.keep_fp32 and a.quantize != "int8":
            raise ConversionError("--keep-fp32 only applies with --quantize int8")
        if a.quantize == "int8":
            log.info("quantising INT8 (MatMul, Gather; %s; pre-process=%s)",
                     "per-channel" if a.per_channel else "per-tensor", a.preprocess)
            keep = fp32_keep_nodes(fp32, a.keep_fp32) if a.keep_fp32 else []
            if keep:
                log.info("keeping %d MatMul nodes FP32 (--keep-fp32 %s)", len(keep), a.keep_fp32)
            int8, tables = quantize_int8(fp32, work, preprocess=a.preprocess, gather_quant=a.gather_quant,
                                         per_channel=a.per_channel, keep_fp32=keep)
            tr = onnx.load(str(int8))
        else:
            log.info("keeping the FP32 transformer (--quantize none)")
            if any(p.suffix != ".onnx" for p in fp32.parent.iterdir()):
                raise ConversionError("--quantize none: the FP32 export uses external data (> 2 GB); not loadable as one file")
            int8, tables = fp32, {}
            tr = onnx.load(str(fp32))
        tok = extract_tokenizer(template, tpl["outputs"])
        set_truncation(tok.graph, tpl, a.max_tokens)
        if a.template == "minilm":
            set_bert_vocab(tok.graph, vocab_bytes, a.lower, a.strip_accents)
            set_const(tok.graph, EOS_CONST, tpl["eos"], a.sep_id)
            set_const(tok.graph, PAD_CONST, tpl["pad"], a.pad_id)
            set_pad_fill(tok.graph, tpl["pad"], a.pad_id)
        if prefix_ids:
            splice_prefix(tok.graph, prefix_ids)
        merged = merge(tok, tr, tpl["outputs"])
        quant = {"mode": a.quantize, "transformer_bytes": int8.stat().st_size}
        if a.quantize == "int8":
            quant.update({"op_types": ["MatMul", "Gather"], "weight_type": "QInt8",
                          "reduce_range": False, "quant_pre_process": bool(a.preprocess),
                          "gather_quant": a.gather_quant, "per_channel": bool(a.per_channel),
                          "keep_fp32": a.keep_fp32, "keep_fp32_nodes": len(keep) if a.keep_fp32 else 0,
                          "gather_tables": tables})

    fin = finalize(merged, dim, out)
    smoke = smoke_test(fin["partial"], dim, a.threads)
    counts = op_counts(merged)
    del merged
    os.replace(fin["partial"], out)
    sha = sha256_file(out)
    build = {
        "converter_version": CONVERTER_VERSION, "created_utc": utc_now(), "model_name": a.model_name,
        "hf_id": a.hf_id, "revision": a.revision, "weights_file": weights,
        "weights_sha256": sha256_file(snap / weights) if weights else None,
        "snapshot": str(snap.resolve().relative_to(a.cache_dir)) if snap.resolve().is_relative_to(a.cache_dir)
        else snap.name, "pooling": a.pooling, "max_tokens": a.max_tokens,
        "template": a.template, "template_file": tpl["file"], "template_sha256": template_sha,
        "tokenizer_outputs": tpl["outputs"], "prefix_ids": prefix_ids, "prefix_text": a.prefix_text,
        "reuse_template_transformer": bool(a.reuse_template_transformer), "tokenizer": info["tokenizer"],
        "quantization": quant, "versions": versions,
        "output": {"file": out.name, "bytes": out.stat().st_size, "sha256": sha, "ir_version": TARGET_IR,
                   "opset": TARGET_OPSET, "dim": dim, "input": "input", "output": "embedding"},
        "st_modules": st["modules"], "normalised_in_graph": True, "smoke_test": smoke, "op_counts": counts,
    }
    side = out.with_name(out.name + ".build.json")
    assert_no_abs_paths(build, "build sidecar")
    with open(side, "w", encoding="utf-8") as f:
        json.dump(build, f, ensure_ascii=False, indent=2)
    log.info("wrote %s (%d bytes, sha256 %s, dim %d)", tilde(out), build["output"]["bytes"], sha, dim)
    return 0


def main(argv=None) -> int:
    # root stays at WARNING: onnxruntime's quantiser logs one INFO line per tensor on the root logger
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")
    logging.Formatter.converter = time.gmtime      # UTC stamps
    log.setLevel(logging.INFO)
    a = parse_args(argv)
    try:
        return run(a)
    except ConversionError as e:
        log.error("conversion failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
