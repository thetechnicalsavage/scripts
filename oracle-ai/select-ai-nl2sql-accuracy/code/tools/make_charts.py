#!/usr/bin/env python3
# v1.1 - NL2SQL accuracy lab: draw the result charts from results/*rescored.csv and runs.jsonl.
#
#        v1.1: rounded bar ends computed in pixel space; run dots spread; labels clear the dots.
# Usage : python3 make_charts.py [--results ../../results] [--out ../../screenshots]
# Output: for each chart a light and a dark PNG (…-light.png / …-dark.png).
# Colours: categorical slots 1-2 of a palette validated for colour-vision deficiency
#          (blue, orange), one series = one hue, values labelled directly, one axis.
import argparse
import collections
import csv
import json
import logging
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                     # noqa: E402
from matplotlib.patches import FancyBboxPatch       # noqa: E402

log = logging.getLogger("make_charts")
HERE = os.path.dirname(os.path.abspath(__file__))

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", grid="#e4e3df",
                  s1="#2a78d6", s2="#eb6834"),
    "dark":  dict(surface="#1a1a19", ink="#ffffff", ink2="#c3c2b7", grid="#34332f",
                  s1="#3987e5", s2="#d95926"),
}
STAGES = [  # (stage label in results, axis label)
    ("S0_baseline", "Baseline"),
    ("S1_comments", "+ Comments"),
    ("S2_annotations", "+ Annotations"),
    ("S3_constraints", "+ Constraints¹"),
    ("S4_object_list", "+ Curated list"),
    ("S5_views", "+ Views beside\ntables"),
    ("S5b_views_replace", "Views replace\ntables"),
    ("S5c_view_vocabulary", "+ Vocabulary\non views"),
    ("S6_feedback", "+ Feedback"),
    (None, None),                                   # visual gap: different profile
    ("S7_automated", "Automated,\nwhole schema"),
]


def load(results):
    runs = collections.defaultdict(list)
    for f in ("rescored.csv", "experiments/rescored.csv"):
        p = os.path.join(results, f)
        if os.path.exists(p):
            for r in csv.DictReader(open(p)):
                runs[(r["stage"], r["set"])].append(float(r["accuracy_pct"]))
    same = {}
    for f in ("runs.jsonl", "experiments/runs.jsonl"):
        p = os.path.join(results, f)
        if not os.path.exists(p):
            continue
        sq = collections.defaultdict(list)
        for line in open(p):
            x = json.loads(line)
            sq[(x["stage"], x["set"], x["id"])].append(" ".join((x["sql"] or "").split()))
        for (st, se, _), v in sq.items():
            s = same.setdefault((st, se), [0, 0])
            s[0] += len(set(v)) == 1
            s[1] += 1
    return runs, same


def rbar(ax, x, h, w, color, surface, radius_px=4):
    """Bar anchored flat on the baseline with a rounded data end of ~radius_px pixels.
    Call only after the axes limits are final: the rounding is converted from pixels."""
    if h <= 0:
        return
    fig = ax.figure
    bbox = ax.get_window_extent()
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    ppx = bbox.width / (x1 - x0)          # pixels per x unit
    ppy = bbox.height / (y1 - y0)         # pixels per y unit
    rx = radius_px / ppx
    aspect = ppx / ppy                    # y-units per x-unit at equal pixel length
    ax.add_patch(FancyBboxPatch((x - w / 2, 0), w, h, linewidth=0, facecolor=color,
                                boxstyle=f"round,pad=0,rounding_size={rx}", mutation_aspect=aspect))
    ax.add_patch(plt.Rectangle((x - w / 2, 0), w, min(h, rx * aspect * 1.5), linewidth=0, facecolor=color))


def style(ax, t, ylabel):
    ax.set_facecolor(t["surface"])
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(t["grid"])
    ax.tick_params(colors=t["ink2"], length=0, labelsize=9)
    ax.yaxis.grid(True, color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_ylabel(ylabel, color=t["ink2"], fontsize=9)


def chart_accuracy(runs, t, out):
    fig, ax = plt.subplots(figsize=(11, 4.8), dpi=200)
    fig.patch.set_facecolor(t["surface"])
    style(ax, t, "Correct answers (% of 22 questions)")
    ax.set_ylim(0, 108)
    ax.set_xlim(-0.6, len(STAGES) - 0.4)
    fig.canvas.draw()
    xs, labels = [], []
    for i, (stage, label) in enumerate(STAGES):
        if stage is None:
            continue
        vals = runs.get((stage, "question"), [])
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        rbar(ax, i, mean, 0.62, t["s1"], t["surface"])
        offs = [(-0.14 + 0.14 * k) for k in range(len(vals))]
        ax.scatter([i + o for o in offs], vals, s=14, color=t["surface"], edgecolor=t["ink"], linewidth=0.8, zorder=3)
        ax.text(i, max(vals + [mean]) + 2.8, f"{mean:.0f}%", ha="center", va="bottom", color=t["ink"],
                fontsize=9, fontweight="bold")
        xs.append(i)
        labels.append(label)
    ax.set_xticks(xs, labels, fontsize=8.5, color=t["ink2"])
    ax.axvline(len(STAGES) - 2, color=t["grid"], linewidth=1, linestyle=(0, (3, 3)))
    ax.set_title("Select AI NL2SQL accuracy, one practice at a time  (bar = mean of 3 runs, dots = each run)",
                 loc="left", color=t["ink"], fontsize=11, pad=12)
    fig.text(0.01, 0.01, "¹ constraints reach the prompt only when object_list names the tables; the curated list "
             "did that in the next step.   Right of the dashed line: a separate profile, not cumulative.",
             color=t["ink2"], fontsize=7.5)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out, facecolor=t["surface"])
    plt.close(fig)


def chart_grouped(pairs, series, title, ylabel, t, out, note=""):
    """pairs: [(group label, [value series1, value series2])]"""
    fig, ax = plt.subplots(figsize=(7.5, 4.4), dpi=200)
    fig.patch.set_facecolor(t["surface"])
    style(ax, t, ylabel)
    w = 0.34
    colors = [t["s1"], t["s2"]]
    ax.set_ylim(0, 110)
    ax.set_xlim(-0.6, len(pairs) - 0.4)
    fig.canvas.draw()
    for gi, (_, vals) in enumerate(pairs):
        for si, v in enumerate(vals):
            x = gi + (si - 0.5) * (w + 0.03)
            rbar(ax, x, v, w, colors[si], t["surface"])
            ax.text(x, v + 2, f"{v:.0f}%", ha="center", va="bottom", color=t["ink"], fontsize=9, fontweight="bold")
    ax.set_xticks(range(len(pairs)), [p[0] for p in pairs], fontsize=9, color=t["ink2"])
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in colors]
    leg = ax.legend(handles, series, loc="lower left", bbox_to_anchor=(0, 1.0), frameon=False, fontsize=9, ncol=2)
    for txt in leg.get_texts():
        txt.set_color(t["ink2"])
    ax.set_title(title, loc="left", color=t["ink"], fontsize=11, pad=30)
    if note:
        fig.text(0.01, 0.01, note, color=t["ink2"], fontsize=7.5)
    fig.tight_layout(rect=(0, 0.05 if note else 0, 1, 1))
    fig.savefig(out, facecolor=t["surface"])
    plt.close(fig)


def mean(v):
    return sum(v) / len(v) if v else 0.0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join(HERE, "..", "..", "results"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "..", "screenshots"))
    a = ap.parse_args()
    runs, same = load(a.results)
    if not runs:
        log.error("no rescored.csv under %s", a.results)
        return 1
    os.makedirs(a.out, exist_ok=True)
    for mode, t in THEMES.items():
        chart_accuracy(runs, t, os.path.join(a.out, f"90-accuracy-by-stage-{mode}.png"))
        chart_grouped([("Before feedback", [mean(runs[("S5c_view_vocabulary", "question")]),
                                            mean(runs[("S5c_view_vocabulary", "paraphrase")])]),
                       ("After feedback", [mean(runs[("S6_feedback", "question")]),
                                           mean(runs[("S6_feedback", "paraphrase")])])],
                      ["Original wording (feedback given)", "Paraphrased (no feedback given)"],
                      "Does feedback generalise to wording it never saw?", "Correct answers (%)", t,
                      os.path.join(a.out, f"91-feedback-generalisation-{mode}.png"),
                      "Feedback was added only for the original wording of 5 questions; every paraphrase is new text.")
        chart_grouped([("case_sensitive_values unset", [mean(runs[("X_csv_unset", "question")]),
                                                        100 * same[("X_csv_unset", "question")][0] / 22]),
                       ("case_sensitive_values = true", [mean(runs[("X_csv_true", "question")]),
                                                         100 * same[("X_csv_true", "question")][0] / 22])],
                      ["Correct answers", "Same SQL in all 3 runs"],
                      "One attribute Oracle's post does not mention", "% of 22 questions", t,
                      os.path.join(a.out, f"93-case-sensitive-values-{mode}.png"),
                      "Unset, Select AI tells the model to wrap string comparisons in UPPER(); it also did so to DATE values.")
        log.info("charts written for %s mode", mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
