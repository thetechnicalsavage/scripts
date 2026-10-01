#!/usr/bin/env python3
# v1.5 - brief 09 (Select AI RAG tuning, EN + AR): draw the result charts of PLAN.md section 6
#        as light/dark PNG pairs from the results JSONL. Started from brief 08's make_charts.py
#        v1.1 (theme tokens, rounded bar ends computed in pixel space, bars + run dots).
#        v1.5: public copy: the PNG leak scan (tools/leak_scan.py, lab-internal) and --patterns-file
#              removed; the charts are drawn exactly as before.
#        v1.4 (30-Sep, write-up fixes): chart 35 says on the chart what it lacks. It reads the retrieval
#              rows as the list of built indexes: a built config with no coverage row is drawn as a
#              'not measured' cell and named in the note (the lab measured coverage for configs 1-14
#              only); a cell that pools several configs (MiniLM 1024: overlaps 0, 128, 205) is named too;
#              each cell carries n (chunks measured). A coverage row must name its index (config_id,
#              as flatten_results.py writes it), else exit 2 and nothing is written: the rows are checked
#              before any chart is drawn (Codex review 1). The values and the other charts are unchanged.
#        v1.3 (30-Sep, independent check): every percentage label goes through pct_label(), one
#              Decimal ROUND_HALF_UP helper (f"{v:.0f}" rounded half to even: 12.5% showed as 12%,
#              87.5% as 88%); heatmap cells, chart 34's bar and ceiling labels, chart 40's end
#              labels and chart 60's means. The data and the drawing are otherwise unchanged.
#        v1.2: doc_lang also takes "both" (an unmasked twin-pair run keeps both twins) and "none"
#              (unanswerable), as flatten_results.py v1.0 writes the real results; only chart 50
#              reads doc_lang, on masked rows, where it is always en or ar. --results defaults to
#              results/charts_input, where flatten_results.py writes the three inputs. Checked on
#              the real results: chart 40's default match_limit line is named in the legend (its
#              in-plot label sat on the Arabic curve; the line is muted and dashed so its legend
#              swatch reads in dark mode) and the title names the model and chunking;
#              chart 42's legend fits the figure; notes wrap at spaces only ("Harness-/only" split)
#              and chart 34's no longer leaves one word on a line; chart 50 prints n once, in the
#              note, when every cell has the same n (Codex review: a cell with n = 0 keeps the
#              per-cell labels, so the note never claims an n that cell does not have).
#        v1.1 (Codex review): aggregation split from drawing and unit-tested; chart 50 counts
#              answerable questions only; language/split values validated; one bar per
#              (stage, config) in chart 60; notes wrapped clear of the watermark.
#
# Run as : the local authoring workstation; no database, no network.
# Usage  : python3 flatten_results.py && python3 make_charts.py [--results ../../results/charts_input]
#                                 [--out ../../screenshots]
#                                 [--only 34,50] [--k 5] [--hitk-config RAG_M0_C1024_O128_COS]
#          python3 make_charts.py --synthetic --out DIR     (deterministic fake data, watermarked;
#                                 refuses to write into the brief's screenshots folder)
# Re-run : safe; overwrites the chart PNGs it draws.
# Exit   : 0 charts written; 1 no input for any chart; 2 malformed input rows.
#
# Charts (NN-slug-light.png / NN-slug-dark.png), English labels:
#   34 hit-vs-chunk-size   S3: evidence hit at an equal ~6,000-character budget (k = 9/6/4/3 for
#                          640/1024/1536/2000) with the containable ceiling; M0, overlap 128, dev
#   35 embedded-fraction   share of each chunk the model embeds, model x chunk_size, EN and AR
#   40 hit-at-k            evidence hit@k for k = 1..20, English vs Arabic questions, one index, dev
#   42 score-distributions best gold vs best wrong chunk score per model (fixed 1024/128), dev
#   50 masked-direction    the headline: masked T-bucket facts, model x direction
#                          (EN->EN, AR->AR, AR->EN, EN->AR), containable-conditional hit@k, test
#   60 answer-accuracy     correct answers by config, test split, bar = mean of runs, dots = runs
#
# Input contract (one JSON object per line; a missing file skips its charts with a warning):
#   retrieval.jsonl       (eval_retrieval.py) one row per index x question x masked variant:
#       config_id str, model_key str (M0..M6, M1Q), chunk_size int, chunk_overlap int,
#       split "dev"|"test", qid str, q_lang "en"|"ar", doc_lang "en"|"ar" (language of the gold
#       document left in the candidate set; "both" for an unmasked twin pair, "none" when
#       unanswerable), bucket str, answerable bool, masked bool,
#       containable bool, evidence_rank int|null (1-based rank of the first chunk holding an
#       evidence span, within the top 20), best_gold_score float|null, best_wrong_score float|null;
#       optional metric str (COS|DOT|EUC|MAN; COS assumed when absent)
#   chunk_coverage.jsonl  (chunk_coverage.py) one row per measured chunk:
#       model_key str, chunk_size int, lang "en"|"ar", chunk_chars int, embedded_chars int;
#       v1.4: config_id str (the index, flatten_results.py writes it), which chart 35 needs to name the
#       built configs (retrieval.jsonl) that have no coverage
#   answers.jsonl         (eval_rag.py) one row per config x question x run:
#       config_id str, stage str, split str, qid str, run int, answerable bool, verdict str
# Colours: categorical slots 1-2 (blue, orange) of a palette validated for colour-vision
#          deficiency in both modes; heatmaps use one blue ramp (light: more = darker,
#          dark: more = brighter). Values are labelled directly; text never wears series colour.
import argparse
import collections
import decimal
import json
import logging
import math
import os
import random
import sys
import tempfile
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                     # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, to_rgb  # noqa: E402
from matplotlib.patches import FancyBboxPatch       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BRIEF = os.path.normpath(os.path.join(HERE, "..", ".."))
# (import leak_scan: the lab's PNG leak scan is lab-internal and removed)

log = logging.getLogger("make_charts")

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781", grid="#e1e0d9",
                  base="#c3c2b7", s1="#2a78d6", s2="#eb6834", empty="#f0efec",
                  ramp=["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]),
    "dark":  dict(surface="#1a1a19", ink="#ffffff", ink2="#c3c2b7", muted="#898781", grid="#2c2c2a",
                  base="#383835", s1="#3987e5", s2="#d95926", empty="#262624",
                  ramp=["#0d366b", "#104281", "#184f95", "#1c5cab", "#2a78d6", "#5598e7", "#9ec5f4"]),
}
MODEL_ORDER = ["M0", "M1", "M2", "M3", "M4", "M5", "M6", "M1Q"]
MODEL_LABEL = {"M0": "MiniLM-L12 (M0)", "M1": "e5-small (M1)", "M2": "e5-base (M2)", "M3": "e5-large (M3)",
               "M4": "bge-m3 (M4)", "M5": "arctic-l-v2 (M5)", "M6": "Arabic-Triplet (M6)",
               "M1Q": "e5-small + 'query: ' (M1Q)"}
DIRECTIONS = [("en", "en"), ("ar", "ar"), ("ar", "en"), ("en", "ar")]
BUDGET_K = {640: 9, 1024: 6, 1536: 4, 2000: 3}            # ~6,000 characters of context each
FIXED_CHUNK = (1024, 128)
LANG_LABEL = {"en": "English", "ar": "Arabic"}

RETRIEVAL_FIELDS = {"config_id": str, "model_key": str, "chunk_size": int, "chunk_overlap": int, "split": str,
                    "qid": str, "q_lang": str, "doc_lang": str, "bucket": str, "answerable": bool,
                    "masked": bool, "containable": bool, "evidence_rank": (int, type(None)),
                    "best_gold_score": (float, int, type(None)), "best_wrong_score": (float, int, type(None))}
COVERAGE_FIELDS = {"model_key": str, "chunk_size": int, "lang": str, "chunk_chars": int, "embedded_chars": int}
ANSWER_FIELDS = {"config_id": str, "stage": str, "split": str, "qid": str, "run": int, "answerable": bool,
                 "verdict": str}
ENUMS = {"q_lang": {"en", "ar"}, "doc_lang": {"en", "ar", "both", "none"}, "lang": {"en", "ar"},
         "split": {"dev", "test"}}


class InputError(Exception):
    pass


# ---------------------------------------------------------------- loading
def load_jsonl(path, fields):
    """Rows of a JSONL file, each checked against the contract. Missing file -> None."""
    if not os.path.exists(path):
        return None
    rows, errs = [], []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError as e:
                errs.append(f"{os.path.basename(path)}:{n}: not JSON ({e.msg})")
                continue
            for k, t in fields.items():
                if k not in r:
                    errs.append(f"{os.path.basename(path)}:{n}: missing {k}")
                elif not isinstance(r[k], t) or (t is int and isinstance(r[k], bool)) or \
                        (isinstance(r[k], bool) and bool not in (t if isinstance(t, tuple) else (t,))):
                    errs.append(f"{os.path.basename(path)}:{n}: {k} has type {type(r[k]).__name__}")
                elif k in ENUMS and r[k] not in ENUMS[k]:
                    errs.append(f"{os.path.basename(path)}:{n}: {k} = {r[k]!r}, expected one of {sorted(ENUMS[k])}")
                elif isinstance(r[k], float) and not math.isfinite(r[k]):
                    errs.append(f"{os.path.basename(path)}:{n}: {k} is not a finite number")
            rows.append(r)
    if errs:
        raise InputError("; ".join(errs[:10]) + (f" (+{len(errs) - 10} more)" if len(errs) > 10 else ""))
    log.info("%s: %d rows", os.path.basename(path), len(rows))
    return rows


def pct(num, den):
    return 100.0 * num / den if den else float("nan")


_LABEL_NOISE = decimal.Decimal("1e-9")


def pct_label(v) -> str:
    """A percentage as a whole-number label, halves rounded UP (12.5 -> '13%', 87.5 -> '88%').
    The value is first taken to 9 decimals (ROUND_HALF_EVEN), so float noise such as
    12.499999999999998 from a mean of runs counts as the half it stands for. NaN (no rows) -> '–'.
    Anything but a finite int or float is refused: a label never hides bad input."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TypeError(f"pct_label needs a number, got {type(v).__name__}")
    if isinstance(v, float) and math.isnan(v):
        return "–"
    if not math.isfinite(v):
        raise ValueError(f"pct_label needs a finite number, got {v!r}")
    d = decimal.Decimal(repr(v)).quantize(_LABEL_NOISE, rounding=decimal.ROUND_HALF_EVEN)
    return f"{d.quantize(decimal.Decimal(1), rounding=decimal.ROUND_HALF_UP)}%"


def hit(r, k):
    return r["evidence_rank"] is not None and r["evidence_rank"] <= k


def pick_config(rows, model, chunk, overlap, metric="COS"):
    """The config_id for one model at one chunking; the lowest id wins when several match
    (identical rebuilds), and the choice is logged."""
    ids = sorted({r["config_id"] for r in rows if r["model_key"] == model and r["chunk_size"] == chunk
                  and r["chunk_overlap"] == overlap and r.get("metric", "COS") == metric})
    if len(ids) > 1:
        log.info("%s %d/%d: %d configs, using %s", model, chunk, overlap, len(ids), ids[0])
    return ids[0] if ids else None


def models_present(keys):
    return [m for m in MODEL_ORDER if m in keys] + sorted(k for k in keys if k not in MODEL_ORDER)


# ---------------------------------------------------------------- drawing helpers
def rbar(ax, x, h, w, color, radius_px=4):
    """Bar anchored flat on the baseline with a rounded data end of ~radius_px pixels.
    Call only after the axes limits are final: the rounding is converted from pixels."""
    if not h or h <= 0 or math.isnan(h):
        return
    bbox = ax.get_window_extent()
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    ppx = bbox.width / (x1 - x0)
    ppy = bbox.height / (y1 - y0)
    rx = radius_px / ppx
    aspect = ppx / ppy
    ax.add_patch(FancyBboxPatch((x - w / 2, 0), w, h, linewidth=0, facecolor=color,
                                boxstyle=f"round,pad=0,rounding_size={rx}", mutation_aspect=aspect))
    ax.add_patch(plt.Rectangle((x - w / 2, 0), w, min(h, rx * aspect * 1.5), linewidth=0, facecolor=color))


def style(ax, t, ylabel="", ygrid=True):
    ax.set_facecolor(t["surface"])
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(t["base"])
    ax.tick_params(colors=t["ink2"], length=0, labelsize=9)
    if ygrid:
        ax.yaxis.grid(True, color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    if ylabel:
        ax.set_ylabel(ylabel, color=t["ink2"], fontsize=9)


def new_fig(t, size):
    fig, ax = plt.subplots(figsize=size, dpi=200)
    fig.patch.set_facecolor(t["surface"])
    return fig, ax


def legend(ax, t, handles, labels, ncol=2):
    leg = ax.legend(handles, labels, loc="lower left", bbox_to_anchor=(0, 1.0), frameon=False, fontsize=9, ncol=ncol)
    for txt in leg.get_texts():
        txt.set_color(t["ink2"])


def footer(fig, t, note, synthetic):
    """Note (wrapped to ~70% of the width) bottom left, watermark bottom right.
    Returns the bottom margin, as a figure fraction, that tight_layout must keep free."""
    w_in, h_in = fig.get_size_inches()
    lines = textwrap.wrap(note, width=max(40, int(w_in * 0.70 * 17)), break_on_hyphens=False) if note else []
    if lines:
        fig.text(0.01, 0.012, "\n".join(lines), color=t["ink2"], fontsize=7.5, va="bottom", linespacing=1.35)
    if synthetic:
        fig.text(0.99, 0.012, "SYNTHETIC DATA - not a result", color=t["muted"], fontsize=8, ha="right", va="bottom")
    n = max(len(lines), 1 if synthetic else 0)
    return (n * 7.5 * 1.35 + 10) / (h_in * 72) if n else 0


def finish(fig, ax, t, title, note, out, synthetic, top_pad=30):
    ax.set_title(title, loc="left", color=t["ink"], fontsize=11, pad=top_pad)
    bottom = footer(fig, t, note, synthetic)
    fig.tight_layout(rect=(0, bottom, 1, 1))
    fig.savefig(out, facecolor=t["surface"])
    plt.close(fig)


def ramp(t):
    return LinearSegmentedColormap.from_list("seq", t["ramp"])


def ink_on(color, t):
    r, g, b = to_rgb(color)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#0b0b0b" if lum > 0.5 else "#ffffff"


def heatmap(ax, t, grid, rows, cols, sub=None):
    """grid[i][j] in 0..100 or nan; sub[i][j] a small second line (e.g. n=17)."""
    cmap = ramp(t)
    for i in range(len(rows)):
        for j in range(len(cols)):
            v = grid[i][j]
            color = t["empty"] if math.isnan(v) else cmap(v / 100.0)
            ax.add_patch(plt.Rectangle((j + 0.03, i + 0.05), 0.94, 0.9, facecolor=color, linewidth=0))
            label = pct_label(v)
            fg = t["ink2"] if math.isnan(v) else ink_on(color, t)
            ax.text(j + 0.5, i + (0.44 if sub else 0.5), label, ha="center", va="center", color=fg,
                    fontsize=10, fontweight="bold")
            if sub and sub[i][j]:
                ax.text(j + 0.5, i + 0.74, sub[i][j], ha="center", va="center", color=fg, fontsize=7)
    ax.set_xlim(0, len(cols))
    ax.set_ylim(len(rows), 0)
    ax.set_xticks([j + 0.5 for j in range(len(cols))], cols, fontsize=9, color=t["ink2"])
    ax.set_yticks([i + 0.5 for i in range(len(rows))], rows, fontsize=9, color=t["ink2"])
    ax.xaxis.tick_top()
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_facecolor(t["surface"])


# ---------------------------------------------------------------- aggregation (no drawing; unit-tested)
def agg_34(ret):
    """[(chunk_size, k, hit %, containable %, n)] for M0, overlap 128, COSINE, dev, not masked.
    hit = evidence span within the budget k, over all answerable questions (same denominator
    as the ceiling, so the bar can be read against it)."""
    rows = [r for r in ret if r["model_key"] == "M0" and r["chunk_overlap"] == 128 and r["split"] == "dev"
            and not r["masked"] and r["answerable"] and r.get("metric", "COS") == "COS"]
    out = []
    for c in sorted(BUDGET_K):
        cid = pick_config(rows, "M0", c, 128)
        if not cid:
            continue
        rs = [r for r in rows if r["config_id"] == cid]
        k = BUDGET_K[c]
        out.append((c, k, pct(sum(hit(r, k) for r in rs), len(rs)), pct(sum(r["containable"] for r in rs), len(rs)),
                    len(rs)))
    return out


def agg_35(cov):
    """{(lang, model, chunk_size): embedded %}, character-weighted."""
    agg = collections.defaultdict(lambda: [0, 0])
    for r in cov:
        a = agg[(r["lang"], r["model_key"], r["chunk_size"])]
        a[0] += r["embedded_chars"]
        a[1] += r["chunk_chars"]
    return {k: pct(e, n) for k, (e, n) in agg.items() if n}


def _cfg_key(c):
    return (0, int(c), "") if c.isdigit() else (1, 0, c)


def gaps_35(cov, ret):
    """v1.4: what chart 35 must say about its own rows. Returns (missing, pooled, n):
    missing = the indexes the retrieval rows show as built and evaluated that have no coverage row,
              [(model_key, chunk_size, config)] with config = experiments_config (else the index);
    pooled  = {(model_key, chunk_size): [config, ...]} for cells whose coverage pools several indexes;
    n       = {(lang, model_key, chunk_size): chunks measured}.
    Every coverage row must name its index (config_id), else InputError: a gap is never guessed."""
    built = {}
    for r in ret or []:
        built.setdefault(r["config_id"], (r["model_key"], r["chunk_size"],
                                          str(r.get("experiments_config") or r["config_id"])))
    have, n = set(), collections.Counter()
    for r in cov:
        idx = r.get("config_id")
        if not isinstance(idx, str) or not idx:
            raise InputError("chunk_coverage.jsonl: a row without config_id (its index); chart 35 needs it "
                             "to name the built configs that have no coverage")
        have.add(idx)
        n[(r["lang"], r["model_key"], r["chunk_size"])] += 1
    missing = sorted((v for k, v in built.items() if k not in have), key=lambda v: _cfg_key(v[2]))
    cells = collections.defaultdict(list)
    for idx in have:
        if idx in built:
            cells[built[idx][:2]].append(built[idx][2])
    pooled = {k: sorted(v, key=_cfg_key) for k, v in cells.items() if len(v) > 1}
    return missing, pooled, dict(n)


def note_35(missing, pooled) -> str:
    parts = ["chunk_size counts characters; each model reads a fixed number of tokens, so the tail of a "
             "long chunk is never embedded. Cell = share of chunk characters inside the embedded prefix, "
             "n = chunks measured; a dash alone = no index at that size."]
    if pooled:
        rank = {m: i for i, m in enumerate(MODEL_ORDER)}
        cells = sorted(pooled.items(), key=lambda kv: (rank.get(kv[0][0], len(rank)), kv[0][0], kv[0][1]))
        parts.append("; ".join(f"{MODEL_LABEL.get(m, m)} {c} pools configs {', '.join(v)}"
                               for (m, c), v in cells) + ".")
    if missing:
        parts.append(f"No coverage file for config{'s' if len(missing) > 1 else ''} "
                     f"{', '.join(v[2] for v in missing)} ("
                     + ", ".join(f"{MODEL_LABEL.get(m, m)} {c}" for m, c, _ in missing)
                     + "): built and evaluated, drawn as 'not measured'.")
    return " ".join(parts)


def agg_40(ret, config=None):
    """(config_id, {lang: (n, [hit % for k = 1..20])}) on dev, not masked, answerable."""
    base = [r for r in ret if r["split"] == "dev" and not r["masked"] and r["answerable"]]
    cid = config or pick_config(base, "M0", *FIXED_CHUNK)
    rows = [r for r in base if r["config_id"] == cid]
    out = {}
    for lg in ("en", "ar"):
        rs = [r for r in rows if r["q_lang"] == lg]
        if rs:
            out[lg] = (len(rs), [pct(sum(hit(r, k) for r in rs), len(rs)) for k in range(1, 21)])
    return cid, out


def agg_42(ret):
    """[(model, [best gold scores], [best wrong scores])] at 1024/128 COSINE, dev, answerable."""
    base = [r for r in ret if r["split"] == "dev" and not r["masked"] and r["answerable"]]
    out = []
    for m in models_present({r["model_key"] for r in base}):
        cid = pick_config(base, m, *FIXED_CHUNK)
        if not cid:
            continue
        rs = [r for r in base if r["config_id"] == cid]
        out.append((m, sorted(r["best_gold_score"] for r in rs if r["best_gold_score"] is not None),
                    sorted(r["best_wrong_score"] for r in rs if r["best_wrong_score"] is not None)))
    return out


def agg_50(ret, k=5):
    """(models, grid of hit %, grid of n) for masked twin-pair facts on test at 1024/128,
    containable-conditional: only answerable questions whose span is intact in some chunk."""
    base = [r for r in ret if r["split"] == "test" and r["masked"] and r["bucket"] == "T" and r["answerable"]]
    models, grid, ns = [], [], []
    for m in models_present({r["model_key"] for r in base}):
        cid = pick_config(base, m, *FIXED_CHUNK)
        if not cid:
            continue
        g, n = [], []
        for ql, dl in DIRECTIONS:
            rs = [r for r in base if r["config_id"] == cid and r["q_lang"] == ql and r["doc_lang"] == dl
                  and r["containable"]]
            g.append(pct(sum(hit(r, k) for r in rs), len(rs)))
            n.append(len(rs))
        models.append(m)
        grid.append(g)
        ns.append(n)
    return models, grid, ns


def agg_60(ans):
    """[((stage, config_id), [% correct per run, in run order], max questions per run)] on the
    test split, answerable only. One bar per (stage, config): the same index may be reported
    by two stages (S8 best = S10). One verdict per question and run (the last one if repeated)."""
    verdicts = collections.defaultdict(dict)            # (stage, config, run) -> {qid: verdict}
    for r in ans:
        if r["split"] != "test" or not r["answerable"]:
            continue
        v = verdicts[(r["stage"], r["config_id"], r["run"])]
        if r["qid"] in v and v[r["qid"]] != r["verdict"]:
            log.warning("chart 60: %s %s run %s question %s appears twice with different verdicts; last one kept",
                        r["stage"], r["config_id"], r["run"], r["qid"])
        v[r["qid"]] = r["verdict"]
    per = collections.defaultdict(dict)                 # (stage, config) -> {run: (correct, n)}
    for (st, cid, run), v in verdicts.items():
        per[(st, cid)][run] = (sum(x == "correct" for x in v.values()), len(v))

    def order(key):
        st, cid = key
        num = int("".join(ch for ch in st.split("_")[0] if ch.isdigit()) or 99)
        return (num, st, cid)
    return [(key, [pct(*per[key][run]) for run in sorted(per[key])], max(n for _, n in per[key].values()))
            for key in sorted(per, key=order)]


# ---------------------------------------------------------------- charts
def chart_34(ret, t, out, synthetic):
    data = agg_34(ret)
    if not data:
        return False
    fig, ax = new_fig(t, (7.5, 4.4))
    style(ax, t, "% of answerable dev questions")
    ax.set_ylim(0, 110)
    ax.set_xlim(-0.6, len(data) - 0.4)
    fig.canvas.draw()
    for i, (c, k, h, ceil, _) in enumerate(data):
        rbar(ax, i, h, 0.5, t["s1"])
        ax.hlines(ceil, i - 0.32, i + 0.32, color=t["ink"], linewidth=2, zorder=3)
        ax.text(i, ceil + 2, f"ceiling {pct_label(ceil)}", ha="center", va="bottom", color=t["ink2"], fontsize=8)
        inside = h >= 14 and ceil - h < 8
        ax.text(i, h - 2 if inside else h + 2, pct_label(h), ha="center", va="top" if inside else "bottom",
                color=ink_on(t["s1"], t) if inside else t["ink"], fontsize=9, fontweight="bold")
    ax.set_xticks(range(len(data)), [f"{c} chars\nk = {k}" for c, k, *_ in data], fontsize=9, color=t["ink2"])
    legend(ax, t, [plt.Rectangle((0, 0), 1, 1, color=t["s1"]), plt.Line2D([], [], color=t["ink"], linewidth=2)],
           ["Evidence span retrieved within k", "Containable ceiling (span intact in some chunk)"])
    ns = sorted({n for *_, n in data})
    finish(fig, ax, t, "chunk_size at an equal context budget (~6,000 characters), MiniLM, overlap 128",
           f"Dev split, n = {'/'.join(map(str, ns))} answerable questions per bar; "
           "k x chunk_size ≈ 6,000 characters.", out, synthetic)
    return True


def chart_35(cov, t, out, synthetic, expected=None):
    data = agg_35(cov)
    langs = [lg for lg in ("en", "ar") if any(k[0] == lg for k in data)]
    if not langs:
        return False
    if expected is None:
        log.warning("chart 35: no retrieval rows, so the built configs without coverage cannot be named")
    missing, pooled, n = gaps_35(cov, expected)
    absent = {(m, c) for m, c, _ in missing}
    models = models_present({k[1] for k in data} | {m for m, _ in absent})
    sizes = sorted({k[2] for k in data} | {c for _, c in absent})
    fig, axes = plt.subplots(1, len(langs), figsize=(4.2 * len(langs) + 1.6, 0.5 * len(models) + 2.2), dpi=200,
                             squeeze=False)
    fig.patch.set_facecolor(t["surface"])
    for ax, lg in zip(axes[0], langs):
        grid = [[data.get((lg, m, c), float("nan")) for c in sizes] for m in models]
        sub = [[f"n={n[(lg, m, c)]}" if n.get((lg, m, c)) else "not measured" if (m, c) in absent else ""
                for c in sizes] for m in models]
        heatmap(ax, t, grid, [MODEL_LABEL.get(m, m) for m in models], [f"{c}" for c in sizes], sub)
        ax.set_xlabel(f"{LANG_LABEL[lg]} chunks, chunk_size (characters)", color=t["ink2"], fontsize=9)
        if ax is not axes[0][0]:
            ax.set_yticks([])
    fig.suptitle("Share of each chunk the embedding model actually reads (character-weighted)",
                 x=0.01, ha="left", color=t["ink"], fontsize=11)
    bottom = footer(fig, t, note_35(missing, pooled), synthetic)
    fig.tight_layout(rect=(0, bottom, 1, 0.95))
    fig.savefig(out, facecolor=t["surface"])
    plt.close(fig)
    return True


def chart_40(ret, t, out, synthetic, config=None):
    cid, data = agg_40(ret, config)
    if not data:
        return False
    fig, ax = new_fig(t, (7.5, 4.4))
    style(ax, t, "Evidence span retrieved (% of answerable)")
    ks = list(range(1, 21))
    ax.set_xlim(0.5, 23.5)
    ax.set_ylim(0, 105)
    handles, labels = [], []
    for lg, color in (("en", t["s1"]), ("ar", t["s2"])):
        if lg not in data:
            continue
        n, ys = data[lg]
        ln, = ax.plot(ks, ys, color=color, linewidth=2, solid_capstyle="round", solid_joinstyle="round")
        ax.scatter([20], [ys[-1]], s=40, color=color, edgecolor=t["surface"], linewidth=1.5, zorder=3)
        ax.text(20.6, ys[-1], f"{LANG_LABEL[lg]} {pct_label(ys[-1])}", va="center", color=t["ink"], fontsize=9)
        handles.append(ln)
        labels.append(f"{LANG_LABEL[lg]} questions (n = {n})")
    ref = ax.axvline(5, color=t["muted"], linewidth=1, linestyle=(0, (4, 3)))   # recessive, readable in the legend
    handles.append(ref)                                 # named in the legend: never on top of a curve
    labels.append("default match_limit 5")
    ax.set_xticks([1, 3, 5, 6, 8, 10, 12, 20])
    ax.set_xlabel("k (chunks retrieved)", color=t["ink2"], fontsize=9)
    legend(ax, t, handles, labels, ncol=3)
    finish(fig, ax, t, f"Evidence hit@k by question language, {short_config(cid)}",
           "Dev split, answerable questions, not masked.", out, synthetic)
    return True


def median(vals):
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def chart_42(ret, t, out, synthetic):
    data = [d for d in agg_42(ret) if d[1] or d[2]]
    if not data:
        return False
    fig, ax = new_fig(t, (8.5, 0.62 * len(data) + 1.9))
    style(ax, t, ygrid=False)
    ax.xaxis.grid(True, color=t["grid"], linewidth=0.8)
    allv = [v for _, g, w in data for v in g + w]
    for i, (m, gold, wrong) in enumerate(data):
        for vals, dy, color in ((gold, -0.17, t["s1"]), (wrong, 0.17, t["s2"])):
            if not vals:
                continue
            # deterministic jitter (golden-ratio sequence), no random state
            ys = [i + dy + (((j * 0.6180339887) % 1.0) - 0.5) * 0.18 for j in range(len(vals))]
            ax.scatter(vals, ys, s=9, color=color, alpha=0.6, edgecolor="none", zorder=2)
            med = median(vals)
            ax.plot([med, med], [i + dy - 0.14, i + dy + 0.14], color=t["ink"], linewidth=2, zorder=3)
    ax.set_yticks(range(len(data)), [MODEL_LABEL.get(m, m) for m, *_ in data], fontsize=9, color=t["ink2"])
    ax.set_ylim(len(data) - 0.5, -0.5)
    lo, hi = min(allv), max(allv)
    ax.set_xlim(max(-1.0, lo - 0.05), min(1.0, hi + 0.05) if hi > lo else hi + 0.05)
    ax.set_xlabel("similarity score of the best chunk (1 - cosine distance)", color=t["ink2"], fontsize=9)
    legend(ax, t, [plt.Line2D([], [], marker="o", linestyle="", color=t["s1"]),
                   plt.Line2D([], [], marker="o", linestyle="", color=t["s2"]),
                   plt.Line2D([], [], marker="|", linestyle="", markersize=11, markeredgewidth=2, color=t["ink"])],
           ["best chunk, gold document", "best chunk, any other document", "median"], ncol=3)
    finish(fig, ax, t, "Score distributions per model: a threshold does not carry over",
           "Dev split, answerable questions, each model at 1024/128 COSINE. One dot per question.", out, synthetic)
    return True


def cell_n_labels(ns):
    """(per-cell 'n=' labels or None, note suffix). One n, not 0, in every cell is said once in the
    note; otherwise each cell keeps its own label (an empty cell shows none)."""
    all_n = {n for row in ns for n in row}
    if len(all_n) == 1 and 0 not in all_n:
        return None, f", n = {next(iter(all_n))} per cell"
    return [[f"n={n}" if n else "" for n in row] for row in ns], ""


def chart_50(ret, t, out, synthetic, k=5):
    models, grid, ns = agg_50(ret, k)
    if not models:
        return False
    sub, n_note = cell_n_labels(ns)
    fig, ax = new_fig(t, (7.8, 0.55 * len(models) + 2.3))
    heatmap(ax, t, grid, [MODEL_LABEL.get(m, m) for m in models],
            [f"{q.upper()} → {d.upper()}" for q, d in DIRECTIONS], sub)
    ax.set_xlabel("question language → language of the only gold document left", color=t["ink2"], fontsize=9,
                  labelpad=8)
    finish(fig, ax, t, f"Same facts, four directions: evidence hit@{k}, each model at 1024/128",
           f"Test split, twin-pair facts, containable-conditional{n_note}. Harness-only: the other-language "
           "twin is removed from the candidates (Select AI has no per-query filter).", out, synthetic, top_pad=34)
    return True


def chart_60(ans, t, out, synthetic):
    data = agg_60(ans)
    if not data:
        return False
    fig, ax = new_fig(t, (max(7.5, 1.1 * len(data) + 2), 4.6))
    style(ax, t, "Correct answers (% of answerable test questions)")
    ax.set_ylim(0, 110)
    ax.set_xlim(-0.6, len(data) - 0.4)
    fig.canvas.draw()
    for i, (_, vals, _) in enumerate(data):
        mean = sum(vals) / len(vals)
        rbar(ax, i, mean, 0.5, t["s1"])
        offs = [(-0.12 + 0.24 * j / max(1, len(vals) - 1)) if len(vals) > 1 else 0 for j in range(len(vals))]
        ax.scatter([i + o for o in offs], vals, s=14, color=t["surface"], edgecolor=t["ink"], linewidth=0.8, zorder=3)
        ax.text(i, max(vals + [mean]) + 2.8, pct_label(mean), ha="center", va="bottom", color=t["ink"],
                fontsize=9, fontweight="bold")
    labels = [f"{st.split('_')[0]}\n{short_config(c)}" for (st, c), _, _ in data]
    ax.set_xticks(range(len(data)), labels, fontsize=8, color=t["ink2"])
    n_q = max(n for *_, n in data)
    finish(fig, ax, t, "Select AI RAG answer accuracy by configuration  (bar = mean of runs, dots = each run)",
           f"Test split, up to {n_q} answerable questions per run, DBMS_CLOUD_AI.GENERATE narrate, "
           "temperature 0.", out, synthetic)
    return True


def short_config(cid):
    """RAG_M0_C1024_O128_COS -> 'M0 1024/128'; anything else is shown as is."""
    p = cid.split("_")
    if len(p) >= 5 and p[0] == "RAG" and p[2].startswith("C") and p[3].startswith("O"):
        return f"{p[1]} {p[2][1:]}/{p[3][1:]}"
    return cid


CHARTS = {
    "34": ("hit-vs-chunk-size", "retrieval", chart_34),
    "35": ("embedded-fraction", "coverage", chart_35),
    "40": ("hit-at-k", "retrieval", chart_40),
    "42": ("score-distributions", "retrieval", chart_42),
    "50": ("masked-direction", "retrieval", chart_50),
    "60": ("answer-accuracy", "answers", chart_60),
}


# ---------------------------------------------------------------- synthetic data
def write_synthetic(d, seed=9):
    """Small deterministic fake results in the input contract, for the render test."""
    rng = random.Random(seed)
    ret, cov, ans = [], [], []
    configs = [("M0", c, 128) for c in (640, 1024, 1536, 2000)] + [(m, 1024, 128) for m in MODEL_ORDER[1:7]]
    skill = {"M0": (0.9, 0.1), "M1": (0.85, 0.7), "M2": (0.87, 0.75), "M3": (0.9, 0.8), "M4": (0.92, 0.88),
             "M5": (0.9, 0.84), "M6": (0.5, 0.8)}
    for m, c, o in configs:
        cid = f"RAG_{m}_C{c}_O{o}_COS"
        en_p, ar_p = skill[m]
        for split in ("dev", "test"):
            for qi in range(24):
                for masked, bucket in ((False, "S-EN"), (True, "T")):
                    for ql in ("en", "ar"):
                        for dl in (("en", "ar") if masked else (ql,)):
                            p = (en_p if ql == "en" else ar_p) * (1.0 if ql == dl else 0.8)
                            rank = rng.randint(1, 3) if rng.random() < p else (rng.randint(4, 20) if rng.random() < 0.5 else None)
                            gold = round(min(0.99, 0.55 + 0.4 * p + rng.uniform(-0.1, 0.1)), 4)
                            ret.append(dict(config_id=cid, model_key=m, chunk_size=c, chunk_overlap=o, split=split,
                                            qid=f"Q{qi:03d}{ql}", q_lang=ql, doc_lang=dl, bucket=bucket, answerable=True,
                                            masked=masked, containable=rng.random() < min(0.97, 0.7 + c / 8000),
                                            evidence_rank=rank, best_gold_score=gold,
                                            best_wrong_score=round(gold - rng.uniform(-0.05, 0.2), 4), metric="COS"))
        for lg in ("en", "ar"):
            for _ in range(20):
                cap = {"M0": 256, "M4": 1024, "M5": 1024}.get(m, 512) * (4.2 if lg == "en" else 3.1)
                for cs in (640, 1024, 1536, 2000):
                    chars = rng.randint(int(cs * 0.8), cs)
                    cov.append(dict(model_key=m, chunk_size=cs, lang=lg, chunk_chars=chars,
                                    embedded_chars=min(chars, int(cap * rng.uniform(0.9, 1.1))),
                                    config_id=f"RAG_{m}_C{cs}_O{o}_COS"))
    for stage, m in (("S1", "M0"), ("S8", "M1"), ("S8", "M4"), ("S10", "M4")):
        cid = f"RAG_{m}_C1024_O128_COS"
        for run in (1, 2, 3):
            for qi in range(30):
                ok = rng.random() < skill[m][qi % 2] * 0.9
                ans.append(dict(config_id=cid, stage=stage, split="test", qid=f"Q{qi:03d}", run=run,
                                answerable=True, verdict="correct" if ok else "wrong"))
    for name, rows in (("retrieval.jsonl", ret), ("chunk_coverage.jsonl", cov), ("answers.jsonl", ans)):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join(BRIEF, "results", "charts_input"))
    ap.add_argument("--out", default=None, help="default: the brief's screenshots folder")
    ap.add_argument("--only", default="", help="comma-separated chart numbers, e.g. 34,50")
    ap.add_argument("--k", type=int, default=5, help="k for chart 50 (default 5, the Oracle default)")
    ap.add_argument("--hitk-config", default=None, help="config_id for chart 40 (default: M0 at 1024/128)")
    ap.add_argument("--synthetic", action="store_true", help="render from deterministic fake data (needs --out)")
    a = ap.parse_args(argv)

    shots = os.path.join(BRIEF, "screenshots")
    out = a.out or shots
    if a.synthetic:
        if not a.out or os.path.realpath(a.out) == os.path.realpath(shots):
            log.error("--synthetic needs an --out folder other than the brief's screenshots folder")
            return 2
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    if only - set(CHARTS):
        log.error("unknown chart(s): %s", ",".join(sorted(only - set(CHARTS))))
        return 2
    with tempfile.TemporaryDirectory(prefix="charts_synth_") as td:
        src = a.results
        if a.synthetic:
            write_synthetic(td)
            src = td
        try:
            data = {"retrieval": load_jsonl(os.path.join(src, "retrieval.jsonl"), RETRIEVAL_FIELDS),
                    "coverage": load_jsonl(os.path.join(src, "chunk_coverage.jsonl"), COVERAGE_FIELDS),
                    "answers": load_jsonl(os.path.join(src, "answers.jsonl"), ANSWER_FIELDS)}
        except InputError as e:
            log.error("malformed input: %s", e)
            return 2
        # v1.4 (Codex review 1): chart 35's rows are checked before ANY chart is drawn, so a refusal
        # never leaves the charts drawn before it (34 comes first in a full run)
        if data["coverage"] is not None and (not only or "35" in only):
            try:
                gaps_35(data["coverage"], data["retrieval"])
            except InputError as e:
                log.error("malformed input: %s", e)
                return 2
        os.makedirs(out, exist_ok=True)
        written = []
        for num, (slug, source, fn) in CHARTS.items():
            if only and num not in only:
                continue
            if data[source] is None:
                log.warning("chart %s skipped: no %s input", num, source)
                continue
            for mode, t in THEMES.items():
                path = os.path.join(out, f"{num}-{slug}-{mode}.png")
                kw = ({"k": a.k} if num == "50" else {"config": a.hitk_config} if num == "40"
                      else {"expected": data["retrieval"]} if num == "35" else {})
                try:
                    drawn = fn(data[source], t, path, a.synthetic, **kw)
                except InputError as e:            # v1.4: chart 35 refuses rows it cannot account for
                    log.error("malformed input: %s", e)
                    return 2
                if not drawn:
                    log.warning("chart %s skipped: no rows match its filter", num)
                    break
                # (the lab leak-scanned each PNG here with tools/leak_scan.py: lab-internal, removed)
                written.append(path)
    if not written:
        log.error("no chart written (no input under %s)", a.results)
        return 1
    log.info("%d chart files written to %s", len(written), out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
