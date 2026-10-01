<!-- v1.1 2026-09-29  Brief 09 model conversion: recipe, recipe proof, M6 and M1Q results, operator run book.
     v1.1: operator decisions of 29-Sep (M6 FP32 and M1Q accepted with token-id exceptions, models permanent in
           ASKORACLE, same model on both sides); per-language cosine gates; models_exceptions.txt; refvec for P2;
           the corrected note that the in-database BERT op keeps hamza and tashkeel even with strip_accents=1.
     v1.0: first version. -->

# Embedding models for brief 09: conversion and parity gates

This folder turns Hugging Face sentence-embedding models into single ONNX files that Oracle AI Database 26ai
can load with `DBMS_VECTOR.LOAD_ONNX_MODEL`, and checks each file before it goes near the database.
Nothing here connects to a database, uses docker or needs sudo. Loading is `03_load_models.sql`'s job.

**State on 29-Sep-2026**

| Key | Oracle model | Result |
|---|---|---|
| M1R (recipe proof) | e5-small rebuilt from HF weights | **Output-identical to Oracle's own `multilingual_e5_small.onnx`**: every large weight byte-identical, 70/70 outputs bit-identical |
| M6 | `ARABIC_TRIPLET_V2` | **Accepted in FP32 (operator, 29-Sep)** with a token-id exception for the in-database BERT tokenizer op's Arabic behaviour. INT8 as specified collapses (cosine 0.48) and stays rejected. To be rebuilt: `run_conversions.sh M6` |
| M1Q | `MULTILINGUAL_E5_SMALL_Q` | **Accepted, exploratory (operator, 29-Sep)**, with its one-in-70 SentencePiece divergence disclosed. To be rebuilt: `run_conversions.sh M1Q` |
| M2, M3, M4, M5 | e5-base, e5-large, bge-m3, arctic-l-v2.0 | the default batch of `run_conversions.sh`, run by the operator on the build host |

Until those runs finish, nothing in `~/rag-lab/converted/` should be loaded that is not listed in its
`models.sha256`.

## Operator decisions (29-Sep-2026)

| # | Decision | Where it is enforced |
|---|---|---|
| D1 | The models are loaded into `ASKORACLE` and stay there permanently; no lab script drops one | `03_load_models.sql`, `99_cleanup.sql` (other owners) |
| D2 | The document side and the question side of every index use the same embedding model | `06`/`07` pairing guards; probe P16 shows what happens otherwise |
| D3 | **M6 is accepted in FP32** (`M6_QUANTIZE=none`, about 539 MB, disclosed like M0, which Oracle ships in FP32). The token-id gates are waived for the in-database BERT tokenizer op's Arabic behaviour. The cosine gates must still pass, pooled and for EN and AR separately | `verify_models.py verify --allow-token-divergence` (M6 only when the build is FP32), `run_conversions.sh` v1.4 |
| D4 | **M1Q is accepted** (exploratory) with its one-in-70 SentencePiece tie-break divergence disclosed. The cosine gates must still pass, including mean cosine >= 0.999 to Oracle's M1 fed `"query: " + text` | as D3 (M1Q only when the build is Oracle's transformer with the `"query: "` splice) |

What a waiver does and does not do:
- The token-id gates are still measured. Every input whose ids differ is listed in the report under
  `token_id_divergence`, and each waived gate keeps its measured value, with `"waived": true` and
  `"measured_pass": false`.
- No other gate is relaxed: structure, runtime, truncation, norms, every cosine gate and (M1Q) the
  Oracle-reference cosine gate.
- `--allow-token-divergence` is refused for any key other than M6 and M1Q, for an M6 build that is not
  FP32 or an M1Q build that is not the splice, and for a build of another model or revision than the one
  pinned in `run_conversions.sh` (the sidecar's `model_name`, `hf_id` and `revision` are checked).
- Each file that passes under a waiver is listed twice. `converted/models.sha256` (every verified file)
  and `converted/models_exceptions.txt` (`KEY sha256 reason`, the format `01_stage_files.sh` reads) carry
  the same sha256. Every run of `run_conversions.sh` reconciles the file at its start and end: a line that is
  malformed, names another key, or whose sha256 `models.sha256` does not list for that key's file is dropped,
  with a warning. The copy in this folder, `models_exceptions.txt`, holds the two entries commented out,
  with placeholder sha256 values, until the build host's file replaces it; an active placeholder line would
  stop `01_stage_files.sh`.

## Files

| File | What it does |
|---|---|
| `convert_hf_to_oracle.py` | one model: pinned download, torch export, INT8, tokenizer graft, merge, checks, `<out>.build.json` sidecar |
| `verify_models.py` | `passages`: builds the parity passage set (workstation). `verify`: the parity gates, writes `<KEY>_parity.json`. `refvec`: the per-string reference vectors that probe P2 compares the database with |
| `run_conversions.sh` | the batch: M2, M4, M5, M3 by default, one at a time, with the memory, disk and thread guards. M6 (FP32) and M1Q run when named, under their exceptions |
| `parity_passages.json` | 50 passages (25 EN, 25 AR, 400/1,000/2,000 characters) from `corpus/src`, deterministic |
| `test_convert.py` | 52 unit tests: graph surgery, argument guards, legacy table quantisation, opset de-duplication, passage builder and passage-set validation, host-path guard, per-language cosine gates, the token-id waiver, refvec, and `run_conversions.sh` against a fake python |
| `models.sha256` | sha256 of every converted file that passed (`sha256sum -c` format). Header only for now: none has passed |
| `models_exceptions.txt` | `KEY sha256 reason` for the files that passed under an operator exception (M6, M1Q). Commented-out placeholders here until the build host's copy replaces it |

Reports and build sidecars are copied to `results/models/`.

## Where it runs

On the build host, as its normal OS user, under `~/rag-lab` only:

```
~/rag-lab/venv             python 3.12, torch 2.6.0+cpu, transformers 4.44.0, onnx 1.17.0,
                           onnxruntime 1.20.1 (the database's version), onnxruntime-extensions 0.13.0
~/rag-lab/oracle-prebuilt  multilingual_e5_small.onnx, all_MiniLM_L12_v2.onnx (Oracle's files, the templates)
~/rag-lab/code             the files in this folder
~/rag-lab/hf/cache         pinned Hugging Face snapshots          ~/rag-lab/converted  passed models + sidecars
~/rag-lab/work             FP32 export and INT8 transformer        ~/rag-lab/rejected   failed models
~/rag-lab/reports          <KEY>_parity.json                        ~/rag-lab/logs       one log per model and run
~/rag-lab/tmp              TMPDIR for every step
```

The converter refuses to run if any library differs from that pinned set. Politeness rules are enforced in
the code, not left to the caller: at most 6 threads (torch, onnxruntime, OMP/MKL), `nice -n 10`, one model at
a time (`flock`), and at least 16 GB "available" in `free -g` before an export (checked in the shell and
again in Python).

## The recipe

1. **Download** the snapshot pinned to a 40-hex commit (branch and tag names are refused), with only the
   files the recipe needs: config, tokenizer files, sentence-transformers module files, and one weights file
   (`model.safetensors`, else `pytorch_model.bin`).
2. **Cross-check the model's own files** before anything is built:
   - pooling in `1_Pooling/config.json` must equal `--pooling`;
   - modules must be Transformer, Pooling and Normalize only;
   - the token cap must fit the position table and `max_seq_length`.
3. **Export** a torch module (HF encoder, then mean or CLS pooling, then L2 normalisation) to ONNX opset 17,
   with dynamic batch and sequence axes. Eager attention is used so no SDPA fast path branches on the mask
   while tracing. torch writes external data by itself above 2 GB.
4. **Quantise** with `onnxruntime.quantization.quantize_dynamic`, per tensor, on MatMul and Gather:
   - MatMul weights become int8 symmetric (the onnxruntime default).
   - The three embedding tables are Gather inputs, so they become uint8. Their scale and zero point are pinned
     through `TensorQuantOverrides` to the legacy symmetric range over 255 steps that Oracle's file carries
     (`--gather-quant oracle`, the default). onnxruntime 1.20.1 on its own spreads a symmetric range over 254
     steps (`symmetric`) or uses min/max (`asymmetric`). See the recipe proof for why this matters.
   - `quant_pre_process` is off. On these exports it stops with "Incomplete symbolic shape inference", and its
     optimiser step cannot write models over 2 GB.
5. **Graft the tokenizer.** The section of Oracle's template that turns the string input `input` into
   `input_ids` and `attention_mask` (plus `token_type_ids` for BERT) is cut out with its nodes verbatim.
   - Its truncation constants are rewritten to `--max-tokens`. Each constant must still hold the value it had
     when the template was inspected, or the run stops.
   - `e5s` template (XLM-R SentencePiece): the SentencePiece model inside Oracle's file must be byte-identical
     to the snapshot's `sentencepiece.bpe.model`, and bos/eos/pad must be 0/2/1.
   - `minilm` template (BERT WordPiece): the BertTokenizer op gets the model's `vocab.txt` and its
     `do_lower_case`/`strip_accents` values (checked against the model's tokenizer config). The
     end-of-sequence constant, the pad constant and the padding fill are also set (for AraBERT: [SEP]=3,
     [PAD]=0).
6. **Merge** with `onnx.compose.merge_models`, keeping one opset entry per domain (Oracle's own files list
   the default domain twice). Then:
   - set IR 8 and the output `embedding` to `[batch_size, dim]`;
   - run `onnx.checker`;
   - save as one file under 2e9 bytes, written as `.partial` and renamed only after a smoke test under
     onnxruntime-extensions.

### Parity gates (`verify_models.py verify`)

| Gate | Threshold |
|---|---|
| Structure: IR 8, opset 17, one string input `input`, one float output `embedding [batch_size, dim]`, checker, size, sha256 = sidecar | all |
| Runtime is onnxruntime 1.20.1 with the extensions library registered | exact |
| Token ids fed to the transformer = HF **slow** tokenizer on 20 EN/AR strings, run singly and batched | 20/20 each |
| Token ids on the 50 passages | 50/50 |
| At least one string longer than the cap (truncation path exercised) | ≥ 1 |
| Cosine to the HF FP32 model on the 50 passages, one text per call | mean ≥ 0.98, min ≥ 0.95, pooled **and** for the 25 EN and the 25 AR passages separately |
| Every output unit length | within 1e-3 |
| With `--oracle-ref`: token ids equal Oracle's graph; mean cosine to Oracle's output | 70/70; ≥ 0.999 |
| M1Q only: transformer initializers identical to Oracle's e5-small | all |

The per-language gates are new in v1.2 (review finding). A pooled mean hides an Arabic-only loss when
the English half is exact: the M6 FP32 build is EN 1.0000 and AR 0.9893 / 0.9651. Every build measured on
29-Sep also passes per language (M1R: EN 0.9866 / 0.9797, AR 0.9845 / 0.9805; M1Q: EN 0.9866 / 0.9808,
AR 0.9849 / 0.9799; M6 FP32 as above). Reports written by v1.1 carry the same figures under
`cosine_vs_hf_fp32.by_lang`, so a v1.1 report can be checked against the new gates by hand.

The 20 strings cover tashkeel, Arabic-Indic digits and separators (`٢٧`, `٣٫٥٪`, `١٬٥٠٠`), Latin inside
Arabic, Arabic punctuation, tatweel, the hamza forms, a U+200F mark, whitespace runs, and one 6,000-character
text per language.

**Why the slow tokenizer is the reference.** Oracle's tokenizer ops follow the original implementations: the
sentencepiece library for XLM-R, and BERT WordPiece. Hugging Face's Rust "fast" tokenizer is what
sentence-transformers uses, and it differs on a few inputs. For XLM-R there were two cases in the recipe
proof:
- It emits an extra `▁` token after trailing whitespace or a trailing U+200F (strings 5 and 15).
- It splits Arabic-Indic digit groups differently: `٬٠٠٠` becomes `٠|٠٠` where sentencepiece and Oracle's
  graph give `٠٠|٠`. This happened in 5 of the 25 Arabic passages.

Every such difference is listed in the report under `fast_tokenizer_agreement`, not hidden.

**Not gated: batched vs single.** Dynamic INT8 picks one activation scale per batch. The same text therefore
gets a slightly different vector alone than inside a batch: cosine 0.9954-0.9988 for Oracle's own e5-small.
Any database-vs-local parity check (probe P2, cosine ≥ 0.9999) must compare one text per call on
both sides.

### Reference vectors for probe P2 (`verify_models.py refvec`)

`refvec` embeds the 20 token-id strings with the ONNX file the database loads, one string per call, and
writes `<KEY>_refvec.json`:
- one vector per string (float32 values);
- the sha256 of the model file and of the strings;
- the onnxruntime version (1.20.1 required, the database's version).

The two 6,000-character strings (6,000 and 11,021 bytes) also get a reference for their 4,000-byte
prefix, the most a VARCHAR2 input takes under `max_string_size = STANDARD`. Probe P2 (`04_probes.py p2`)
embeds the same strings in the database one per call and needs cosine ≥ 0.9999 on every one.
M0 and M1 are compared with Oracle's own files:

```bash
cd ~/rag-lab && P=venv/bin/python; V=code/verify_models.py
$P $V refvec --model oracle-prebuilt/all_MiniLM_L12_v2.onnx     --out reports/M0_refvec.json
$P $V refvec --model oracle-prebuilt/multilingual_e5_small.onnx --out reports/M1_refvec.json
$P $V refvec --model converted/multilingual_e5_base.onnx        --out reports/M2_refvec.json
#  M3 multilingual_e5_large, M4 bge_m3, M5 arctic_embed_l_v2, M6 arabic_triplet_v2, M1Q multilingual_e5_small_q
```

Copy `reports/*_refvec.json` to `results/models/` with the parity reports; P2 reads them from there.

## Recipe proof: e5-small rebuilt from Hugging Face weights

`intfloat/multilingual-e5-small@614241f6`, template `e5s`, mean pooling, 512 tokens, compared with Oracle's
`multilingual_e5_small.onnx` (report: `results/models/M1R_parity.json`).

| Check | Result |
|---|---|
| Large weight tensors (≥ 1,024 values) byte-identical to Oracle's | **86 of 86** |
| Token ids equal to Oracle's graph (20 strings + 50 passages) | 70 of 70 |
| Outputs bit-identical to Oracle's | **70 of 70** (cosine 1.0) |
| Cosine to HF FP32, 50 passages | mean 0.9855, min 0.9797 (EN 0.9866, AR 0.9845), the same as Oracle's file |
| File size | 123,164,732 bytes (Oracle's: 123,021,105; the graph differs, not the numbers) |
| Rebuilt twice (converter v1.0, then v1.1) | identical sha256 `b146b932…fd52`: the build is deterministic |

How it got there, kept because it explains the design:
- **Default onnxruntime 1.20.1 settings got close but not all the way.** Every INT8 MatMul weight and all 122
  LayerNorm and bias tensors came out identical, but mean cosine to Oracle was only 0.9976.
- **The embedding tables were the difference.** Transplanting Oracle's three quantised tables into that build
  made the outputs bit-identical (70/70).
- **Oracle's tables follow onnxruntime's older formula,** scale = 2·absmax/255 with Python-float rounding.
  The pinned onnxruntime cannot produce it by itself; the per-tensor overrides reproduce all three tables
  byte for byte.
- **The size of the effect.** Mean cosine to FP32 is 0.9857 with min/max tables and 0.9855 with Oracle's. The
  difference is negligible for quality, but it decides whether "the same recipe as Oracle's" is literally true.
  PLAN.md 0.6 relies on that claim for M2-M5.

## M6: Arabic-Triplet-Matryoshka-V2

`Omartificial-Intelligence-Space/Arabic-Triplet-Matryoshka-V2@408d4838`, BERT with the AraBERTv02 vocabulary
(64,000 entries, cased), template `minilm` with the vocabulary swapped, `do_lower_case=0`, `strip_accents=0`,
[SEP]=3, [PAD]=0, mean pooling, 512 tokens, 768 dimensions. The model ships no Normalize module; the graph
L2-normalises, as Oracle's pipeline does (cosine is unchanged). Reports: `results/models/M6_int8_rejected_parity.json`
and `M6_fp32_candidate_parity.json`.

**Verdict: fails as specified.** Two independent problems; each needs an operator decision.

**1. Per-tensor INT8 destroys this model.**
- The FP32 export is exact: cosine 1.0000 to HF on every passage fed the same ids.
- Quantising the MatMul weights per tensor (Oracle's e5-small recipe) drops cosine to 0.35-0.62
  (`M6_int8_stage_isolation.json`).
- **Cause.** The five worst FFN `output.dense` weights have outliers 77-100× their 99.9th percentile. As a
  result 81-90% of their values round to zero at one step per tensor (`M6_vs_M1_weight_outliers.json`).
  e5-small's five worst are 26-42× and 43-62%, and it survives.

**2. The in-database BERT tokenizer op splits Arabic differently from Hugging Face.** This is the same op
that runs Oracle's MiniLM (M0). It also keeps hamza and tashkeel even with `strip_accents=1` (finding 1
below); for M6 the flag is 0 anyway, so that part is not a difference here.
- Arabic punctuation (`،` `؟` `٫` `٪`) is not split off as punctuation.
- A word WordPiece cannot fully cover becomes its matched pieces plus `[UNK]`, where HF gives one `[UNK]`
  for the whole word. Tashkeel and tatweel words hit this path.
- Result: 12 of 20 strings match. All 25 English passages match, and none of the 25 Arabic passages does.
- This is op behaviour, not a graft error. With identical ids the output is identical (see the FP32 row
  below).

Measured options (cosine to HF FP32 on the 50 passages):

| Option | File | Mean / min cosine | Tokenizer | Note |
|---|---|---|---|---|
| INT8 as specified | 136 MB | 0.477 / 0.335 | op | rejected (`M6_int8_rejected_parity.json`) |
| FP32 (`M6_QUANTIZE=none`) | 539 MB | 0.9946 / 0.9651 end to end; EN 1.0000, AR 0.9893 | op | cosine gates pass; token-id gates fail, so it is rejected until the exception below is agreed. PGA per scoring session estimated at about the file size, not measured (`M6_fp32_candidate_parity.json`) |
| INT8, 12 FFN-output MatMuls FP32, per tensor | 220 MB | 0.9862 / 0.9679 | HF ids | not built as a loadable file yet; converter option needed (`M6_mixed_precision.json`) |
| same, per channel | 221 MB | 0.9911 / 0.9796 | HF ids | as above |
| drop M6 | - | - | - | PLAN.md default for a parity failure; the post says why |

- **Decision (operator, 29-Sep): FP32**, disclosed like M0 (Oracle ships M0 in FP32 too), measured with the
  op's tokenization, because that is what the database will run. The token-id gates are waived for M6 (D3);
  every cosine gate still applies, and the FP32 candidate passes them per language (EN 1.0000, AR mean 0.9893,
  min 0.9651). `M6_QUANTIZE=none` is now the default in `run_conversions.sh`.
- **Cost of the mixed-precision options.** They keep the file smaller but need a converter change and break
  "one INT8 recipe for all multilingual models" in a different way. Not pursued.

## M1Q: the e5 "query: " prefix

**Verdict: implemented; exact on 69 of 70 inputs, so not strictly clean (see Result).**

- **What it is.** M1Q is Oracle's own e5-small with its transformer and INT8 weights untouched. The tokenizer
  section also splices the ids of `"query: "` (`41, 1294, 12` = `▁que`, `ry`, `:`) after `<s>` in every
  row, before padding and truncation. M1 vs M1Q therefore differs only in the prefix, both for documents and
  for questions (the symmetric mode the e5 authors describe).
- **Why it should be exact, and the one place it is not.** SentencePiece pieces never cross a `▁` boundary,
  and `"query: "` ends in a space. Tokenising `"query: " + text` is therefore the same as `<s>` + those 3 ids
  + the ids of `text`. This held for both HF tokenizers on probes with a leading `:)`, leading spaces and
  Arabic-Indic digits, and on 69 of the 70 verify inputs. The exception is below.
- **Where it sits in the graph.** 7 nodes in the SequenceMap body (4 constants, 2 `Slice`, 1 `Concat`), plus
  a constant and an `Add` on the padded length. The cap still includes the prefix, as HF truncation of the
  concatenated string does.

**Result: 69 of 70 inputs exact; one divergence; operator decision needed.**
(`M1Q_rejected_parity.json`; the file sits in `rejected/`.)

- **What passed.**
  - The transformer is byte-identical to Oracle's.
  - Token ids match on 20/20 strings, single and batched.
  - Against Oracle's M1 fed `"query: " + text`, 69/70 outputs are bit-identical.
  - Cosine to HF FP32 is mean 0.9857, min 0.9799.
- **What failed.** Passage ar-23 contains `١٠٬٠٠٠`.
  - SentencePiece segments the trailing zeros as `٠٠|٠` when the text is tokenized alone (the splice), and as
    `٠|٠٠` when `"query: "` precedes it (the concatenation). Its tie-break on equal-score merges therefore
    depends on the preceding text.
  - The two resulting vectors differ by cosine 0.9985, within the range of INT8's own batched-vs-single
    differences (0.9954-0.9988).
  - The strict gates `passage_token_ids_50_of_50` and `oracle_token_ids_equal` compare with the
    concatenation, so the model was rejected.

So the splice is clean in construction and exact except for SentencePiece tie-breaks on repeated digit
pieces.

**Decision (operator, 29-Sep): accepted (option a), exploratory, with the divergence disclosed.** It
measures the prefix effect to within 0.0015 cosine on the worst input. The token-id gates
(`passage_token_ids_50_of_50`, `oracle_token_ids_equal`) are waived for M1Q (D4) and the divergent inputs
are listed in the report. The cosine gates stay, the Oracle-reference one (mean ≥ 0.999) included. M1Q
is not in the default batch; `run_conversions.sh M1Q` builds it.

## Findings worth keeping for the post

1. **Correction: the in-database BERT tokenizer op keeps hamza and tashkeel even with `strip_accents=1`.**
   - Oracle's `all_MiniLM_L12_v2.onnx` (M0) sets `do_lower_case=1, strip_accents=1`. In
     onnxruntime-extensions (0.13 and 0.14, pinned by `test_convert.py`), that strips Latin accents
     (`café` → `cafe`) but leaves Arabic hamza and tashkeel alone: `أ` stays `أ`, and `يَحِقُّ` keeps its marks.
   - Hugging Face's BERT normaliser (NFD, then drop Mn) removes both.
   - The research note's line "strip_accents=1 would turn أ and إ into ا" (`results/model_research.md`)
     describes Hugging Face, not the graph that runs in the database. For M0 (H1) this means Arabic
     queries and documents reach the model with their hamza and diacritics, as written.
2. **Oracle's e5-small is reproducible from public weights.** The only non-default step is the older
   embedding-table quantisation, pinned here.
3. **Two tokenizer divergences on Arabic text** between sentencepiece (= Oracle) and the HF fast tokenizer:
   the trailing `▁`, and the digit-group split.
4. **Batch composition moves INT8 vectors by up to about 0.005 cosine** (see above).

## Operator run book (M2, M3, M4, M5, then M6 and M1Q)

Copy the v1.4 scripts only after any running batch has finished: bash reads a running script from its
file as it goes, so replacing `run_conversions.sh` under a running batch can break that batch, and a new
`verify_models.py` would change the gates for the models the batch has not reached yet.

```bash
# workstation: copy the scripts (the passage file is already built and committed)
scp code/models/{convert_hf_to_oracle.py,verify_models.py,run_conversions.sh,test_convert.py,parity_passages.json} <build-host>:rag-lab/code/

# build host
cd ~/rag-lab/code && ORACLE_PREBUILT_DIR=~/rag-lab/oracle-prebuilt ~/rag-lab/venv/bin/python test_convert.py
nohup ./run_conversions.sh > ~/rag-lab/logs/run_conversions.out 2>&1 < /dev/null &
tail -f ~/rag-lab/logs/run_conversions.out           # one INFO line per step; per-model logs in ~/rag-lab/logs
# then the two accepted exceptions (M6 in FP32 by default)
nohup ./run_conversions.sh M6 M1Q > ~/rag-lab/logs/run_conversions_exceptions.out 2>&1 < /dev/null &
```

- **What to expect (estimates).** M2 takes about 15 minutes. The 560M models (M4, M5, M3) take about 20-45
  minutes each, most of it in the verifier (slow tokenizer, then the FP32 reference). Measured for
  comparison: e5-small took 2 minutes to convert and 8 to verify, and M6 took 2 and 12.
- **Check for the M6 failure mode.** Every model gets the same per-tensor INT8 recipe, so an e5-large, bge-m3
  or arctic with AraBERT-like weight outliers would fail the cosine gate the same way. If one does, the
  stage-isolation numbers above are the template for the follow-up.
- **Peak memory.** Estimated at 8-10 GB for the 560M exports (FP32 export 2.2 GB plus quantisation copies),
  inside the 16 GB guard.
- **Failures.** A failed model goes to `~/rag-lab/rejected/` with its log named in the output, and the batch
  continues with the next model.

Afterwards, copy the results back to the workstation:

```bash
scp <build-host>:rag-lab/converted/models.sha256 code/models/models.sha256
scp <build-host>:rag-lab/converted/models_exceptions.txt code/models/models_exceptions.txt
scp <build-host>:rag-lab/reports/*_parity.json <build-host>:rag-lab/reports/*_refvec.json results/models/
scp <build-host>:rag-lab/converted/*.build.json results/models/
```

`03_load_models.sql` then loads from `RAG_MODEL_DIR`. Before a file is staged there, check it on the build
host with `sha256sum -c models.sha256` in `~/rag-lab/converted`.

Re-running is safe. `FORCE=1` rebuilds a listed model, and `KEEP_WORK=1` keeps the FP32 and INT8
intermediates. `MIN_AVAIL_GB`, `MIN_DISK_GB`, `WAIT_MIN` and `THREADS` (at most 6) tune the guards.

## Unit tests

`python3 test_convert.py -v`: 52 tests. v1.1's 25 passed on the workstation (onnxruntime 1.24.4, extensions
0.14.0) and on the build host (1.20.1 / 0.13.0) on 29-Sep; v1.2's 52 passed on the workstation. The graph
and refvec tests use Oracle's templates. Set `ORACLE_PREBUILT_DIR` to their folder, otherwise they are
skipped and the run says so. The `run_conversions.sh` tests use a fake python in a temporary
`RAG_LAB_HOME`, never `~/rag-lab`, so they are safe while a batch runs.

## What not to claim

- **Parity here is local.** It is against onnxruntime 1.20.1 on the build host. The database's own embedding
  of the same strings is probe P2 (`04_probes.py p2`, against `refvec`), and a model is not used until it
  passes there.
- **M6 and M1Q passed under disclosed exceptions**, not the strict token-id gates. Say so wherever their
  numbers appear; M1Q is exploratory.
- **MTEB scores in PLAN.md section 3 are upper bounds.** Select AI sends raw text, without the prompts MTEB
  used, except M1Q's fixed prefix.
- **INT8 costs about 1.5 cosine points against FP32** (0.9855 for e5-small). The multilingual models share
  one recipe, and M0 is FP32 as Oracle ships it.
- **Revisions are pinned** to the commits listed in `run_conversions.sh`. A later upstream revision is a
  different model.

## Follow-ups

- M2-M5 conversions and their reports (operator, `run_conversions.sh`). The batch started on 29-Sep runs the
  v1.1 verifier, so check its reports' `cosine_vs_hf_fp32.by_lang` against the per-language gates (EN and
  AR each mean ≥ 0.98, min ≥ 0.95), or re-verify with v1.2.
- M6 (FP32) and M1Q rebuilds with v1.4 (`run_conversions.sh M6 M1Q`), then copy `models_exceptions.txt` back.
- `refvec` for every loaded key, then probe P2 per key (PROBES.md section 1b).
- Mention finding 1 in the M0 discussion. The research note `results/model_research.md` states the Hugging
  Face behaviour, not the graph's.
