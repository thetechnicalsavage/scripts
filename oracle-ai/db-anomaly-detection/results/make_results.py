#!/usr/bin/env python3
# v1.1 2026-10-07 - v1.1 (phase 6.3): report text only. The header says v1.1 and why, section 1 carries the correction
#        note for the write-time TTDs (QUERIES.sql v1.1 computes them at full precision), section 10 names v1.1 of both
#        files and how this build's spool was made. No computation changed.
# v1.0 2026-10-07 - phase 6.2 of app 900 / brief 10: builds results/ from the output of results/QUERIES.sql only.
#        Splits the SQL*Plus spool on its "@@ <name>" lines into csv/<name>.csv (numbers given a leading zero, nothing
#        else changed), then writes the Markdown report (test_results.md), the wide per-scenario matrix
#        (csv/scenario_matrix_test_wide.csv) and four PNG charts (charts/). No database access, no randomness.
# Usage : python3 make_results.py <queries.out>      (matplotlib 3.10, numpy 2.2; Python 3.12)
from __future__ import annotations

import csv
import io
import logging
import re
import statistics  # medians of the write lag (derived from Q02 rows)
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

LOG = logging.getLogger("make_results")
HERE = Path(__file__).resolve().parent
CSV_DIR = HERE / "csv"
CHART_DIR = HERE / "charts"

# PLAN.md section 7 order; fixed colour per detector (dataviz reference palette, slots 1-7, validated light mode)
DETS = ["MSET", "SVM", "EM", "PCA", "IFOREST", "STATIC", "SEASONAL"]
LABEL = {"MSET": "MSET-SPRT (D1)", "SVM": "One-class SVM (D2)", "EM": "EM anomaly (D3)", "PCA": "PCA residual (D4)",
         "IFOREST": "Isolation Forest (D5)", "STATIC": "Static thresholds (R1)", "SEASONAL": "Seasonal baseline (R2)"}
SETTING = {"MSET": "alert count 3, window 5", "SVM": "outlier rate .02", "EM": "outlier rate .02",
           "PCA": "99th pct residual", "IFOREST": "contamination .02", "STATIC": "99.5th pct",
           "SEASONAL": "k = 3"}
COLOR = {"MSET": "#2a78d6", "SVM": "#eb6834", "EM": "#1baf7a", "PCA": "#eda100", "IFOREST": "#e87ba4",
         "STATIC": "#008300", "SEASONAL": "#4a3aa7"}
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
WASH = "#f0efec"


# ------------------------------------------------------------------------------------------------ parsing
def split_spool(path: Path) -> dict[str, list[dict[str, str]]]:
    """'@@ name' blocks of CSV -> {name: rows}. Fails on any ORA-/SP2- line (a query that did not run)."""
    text = path.read_text(encoding="utf-8")
    bad = [ln for ln in text.splitlines() if re.match(r"^(ORA|SP2)-\d+", ln.strip())]
    if bad:
        raise SystemExit(f"spool holds errors, refusing to build: {bad[:3]}")
    blocks: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("@@ "):
            cur = line[3:].strip()
            blocks[cur] = []
        elif cur and line.strip():
            blocks[cur].append(line)
    blocks.pop("end", None)
    out = {}
    for name, lines in blocks.items():
        rows = list(csv.DictReader(io.StringIO("\n".join(lines))))
        out[name] = [{k: lead0(v) for k, v in r.items()} for r in rows]
    return out


def lead0(v: str) -> str:
    """SQL*Plus prints .5 and -.5; give numbers a leading zero (no other change)."""
    if v is None:
        return ""
    if re.fullmatch(r"-?\.\d+", v):
        return v.replace(".", "0.", 1)
    return v


def write_csv(name: str, rows: list[dict[str, str]]) -> None:
    CSV_DIR.mkdir(exist_ok=True)
    with open(CSV_DIR / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
        if not rows:
            fh.write("")
            return
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def f(v: str) -> float | None:
    return float(v) if v not in ("", None) else None


def fmt(v: str | float | None, nd: int = 2, dash: str = "-") -> str:
    if v in ("", None):
        return dash
    return f"{float(v):.{nd}f}"


def hhmm(ts: str) -> str:
    return ts[11:16] if ts else "-"


def hhmmss(ts: str) -> str:
    return ts[11:19] if ts else "-"


def by_det(rows, **match):
    return [r for r in rows if all(r.get(k) == v for k, v in match.items())]


def one(rows, **match):
    hit = by_det(rows, **match)
    if len(hit) != 1:
        raise SystemExit(f"expected one row for {match}, found {len(hit)}")
    return hit[0]


# ------------------------------------------------------------------------------------------------ charts
def style_axes(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
        ax.spines[side].set_linewidth(1)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.grid(axis="x", color=GRID, linewidth=1)
    ax.set_axisbelow(True)


def rounded_hbar(ax, y: float, x0: float, length: float, height: float, color: str, radius_px: float = 4) -> None:
    """A horizontal bar with a 4px rounded data end and a square baseline end."""
    if length <= 0:
        return
    fig = ax.figure
    fig.canvas.draw()
    bbox = ax.get_window_extent()
    x_lo, x_hi = ax.get_xlim()
    y_lo, y_hi = ax.get_ylim()
    px_x = bbox.width / (x_hi - x_lo)
    px_y = bbox.height / abs(y_hi - y_lo)
    r_x = radius_px / px_x
    ax.add_patch(FancyBboxPatch((x0, y - height / 2), length, height,
                                boxstyle=f"round,pad=0,rounding_size={min(r_x, length / 2)}",
                                mutation_aspect=px_x / px_y, linewidth=0, facecolor=color))
    if length > r_x:
        ax.add_patch(Rectangle((x0, y - height / 2), length - r_x, height, linewidth=0, facecolor=color))


def chart_catches(score_rows, out: Path) -> None:
    slices = [("LOW", "LOW intensity"), ("HIGH", "HIGH intensity"), ("ALL", "All incidents")]
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.2), dpi=150, sharey=True)
    fig.patch.set_facecolor(SURFACE)
    ys = list(range(len(DETS)))[::-1]
    for ax, (sl, title) in zip(axes, slices):
        rows = {r["DETECTOR"]: r for r in by_det(score_rows, RUN_TAG="EVAL_TEST", SLICE=sl)}
        total = int(rows["MSET"]["N_INCIDENTS"])
        style_axes(ax)
        ax.set_xlim(0, total * 1.28)
        ax.set_ylim(-0.6, len(DETS) - 0.4)
        ax.set_xticks(range(0, total + 1, 2 if total > 8 else 1))
        ax.set_title(f"{title} ({total})", loc="left", fontsize=10, color=INK, pad=8)
        for det, y in zip(DETS, ys):
            n = int(rows[det]["N_CAUGHT"])
            ax.add_patch(Rectangle((0, y - 0.17), total, 0.34, facecolor=WASH, linewidth=0))
            rounded_hbar(ax, y, 0, n, 0.34, COLOR[det])
            ax.text(max(n, total) + total * 0.03, y, f"{n}/{total}", va="center", ha="left", fontsize=9, color=INK)
        ax.set_xlabel("incidents caught", fontsize=9, color=INK2)
    axes[0].set_yticks(ys)
    axes[0].set_yticklabels([LABEL[d] for d in DETS], fontsize=9, color=INK)
    fig.suptitle("Test day: injected incidents caught, by detector and intensity", x=0.01, ha="left",
                 fontsize=12, color=INK, y=0.99)
    fig.text(0.01, 0.92, "Grey track = incidents in that slice. EVAL_TEST, 2026-10-06 00:27Z to 2026-10-07 00:27Z.",
             fontsize=9, color=INK2)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)


def write_lag(inc_rows, tag="EVAL_TEST"):
    """Minutes from the opening interval's begin to the live row write, over caught in-database incidents (Q02)."""
    lags = [float(r["TTD_WRITE_MIN"]) - float(r["TTD_MIN"]) for r in inc_rows
            if r["RUN_TAG"] == tag and r["CAUGHT"] == "Y" and r["TTD_WRITE_MIN"]]
    return statistics.median(lags), min(lags), max(lags), len(lags)


def chart_ttd(inc_rows, score_rows, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    style_axes(ax)
    ax.set_xscale("log")
    ax.set_xlim(0.3, 200)
    ax.set_xticks([0.5, 1, 2, 5, 10, 20, 50, 100])
    ax.set_xticklabels(["0.5", "1", "2", "5", "10", "20", "50", "100"])
    ys = list(range(len(DETS)))[::-1]
    ax.set_ylim(-0.6, len(DETS) - 0.4)
    for det, y in zip(DETS, ys):
        caught = [r for r in by_det(inc_rows, RUN_TAG="EVAL_TEST", DETECTOR=det) if r["CAUGHT"] == "Y"]
        if not caught:
            ax.text(0.33, y, "no incident caught", va="center", fontsize=9, color=MUTED)
            continue
        for r in caught:
            t = max(float(r["TTD_MIN"]), 0.31)
            off = 0.12 if r["INTENSITY"] == "HIGH" else -0.12   # fixed offset by intensity, no jitter
            ax.scatter([t], [y + off], s=46, zorder=3, linewidths=1.6,
                       facecolors=COLOR[det] if r["INTENSITY"] == "HIGH" else SURFACE, edgecolors=COLOR[det])
        med = f(one(score_rows, RUN_TAG="EVAL_TEST", DETECTOR=det, SLICE="ALL")["TTD_MEDIAN"])
        ax.plot([med, med], [y - 0.3, y + 0.3], color=INK, linewidth=1.5, zorder=4)
        p90 = f(one(score_rows, RUN_TAG="EVAL_TEST", DETECTOR=det, SLICE="ALL")["TTD_P90"])
        ax.text(205, y, f"{len(caught)} caught · median {med:.1f} · p90 {p90:.1f}", va="center", ha="left",
                fontsize=8.5, color=INK2,
                clip_on=False)
    ax.set_yticks(ys)
    ax.set_yticklabels([LABEL[d] for d in DETS], fontsize=9, color=INK)
    ax.set_xlabel("minutes from incident start to the opening interval of the alert (log scale)", fontsize=9,
                  color=INK2)
    hi = ax.scatter([], [], s=46, facecolors=INK2, edgecolors=INK2, label="HIGH incident")
    lo = ax.scatter([], [], s=46, facecolors=SURFACE, edgecolors=INK2, linewidths=1.6, label="LOW incident")
    md, = ax.plot([], [], color=INK, linewidth=1.5, label="median")
    ax.legend(handles=[hi, lo, md], loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=3, frameon=False,
              fontsize=9, labelcolor=INK2)
    fig.suptitle("Test day: time to detect, one dot per caught incident (dots at the same minute overlap)",
                 x=0.01, ha="left", fontsize=12,
                 color=INK)
    med, lo, hi, n = write_lag(inc_rows)
    fig.text(0.01, 0.9, f"The live alert row is written {med:.2f} min after the opening interval begins (median of {n} "
             f"in-database catches; range {lo:.2f}-{hi:.2f}).", fontsize=9, color=INK2)
    fig.tight_layout(rect=(0, 0, 0.83, 0.9))
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)


def chart_fa(fa_rows, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.0), dpi=150, sharey=True)
    fig.patch.set_facecolor(SURFACE)
    ys = list(range(len(DETS)))[::-1]
    vmax = max(int(r["N_FALSE_ALARMS"]) for r in fa_rows)
    for ax, (tag, title) in zip(axes, [("EVAL_DEV", "Dev day (tuning)"), ("EVAL_TEST", "Test day (held out)")]):
        style_axes(ax)
        ax.set_xlim(0, max(vmax, 3) + 1.2)
        ax.set_ylim(-0.6, len(DETS) - 0.4)
        ax.set_xticks(range(0, max(vmax, 3) + 1))
        ax.set_title(title, loc="left", fontsize=10, color=INK, pad=8)
        ax.axvline(2, color=MUTED, linewidth=1, zorder=1)
        ax.text(2.06, -0.55, "tuning limit: 2", fontsize=8, color=MUTED, va="bottom")
        for det, y in zip(DETS, ys):
            n = int(one(fa_rows, RUN_TAG=tag, DETECTOR=det)["N_FALSE_ALARMS"])
            rounded_hbar(ax, y, 0, n, 0.34, COLOR[det])
            ax.text(n + 0.08, y, str(n), va="center", ha="left", fontsize=9, color=INK)
        ax.set_xlabel("false-alarm episodes in the 24 h", fontsize=9, color=INK2)
    axes[0].set_yticks(ys)
    axes[0].set_yticklabels([LABEL[d] for d in DETS], fontsize=9, color=INK)
    fig.suptitle("False alarms per day, by detector", x=0.01, ha="left", fontsize=12, color=INK)
    fig.text(0.01, 0.9, "An episode that starts outside every injected run and its 30-min cool-down. "
             "SEASONAL's 1 is a single episode open all day.", fontsize=9, color=INK2)
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)


def chart_drift(minutes, events, peaks, out: Path) -> None:
    ts = [datetime.strptime(r["TS"], "%Y-%m-%d %H:%M:%S") for r in minutes]
    inc = one(events, EVENT="incident")
    i0 = datetime.strptime(inc["TS"], "%Y-%m-%d %H:%M:%S")
    i1 = datetime.strptime(inc["TS_END"], "%Y-%m-%d %H:%M:%S")
    m_open = one(events, EVENT="episode MSET")
    s_first = one(events, EVENT="first static breach")
    s_open = [e for e in events if e["EVENT"] == "episode STATIC"]
    s_open.sort(key=lambda e: e["TS"])
    m_t = datetime.strptime(m_open["TS"], "%Y-%m-%d %H:%M:%S")
    s_t = datetime.strptime(s_open[0]["TS"], "%Y-%m-%d %H:%M:%S")
    sb_t = datetime.strptime(s_first["TS"], "%Y-%m-%d %H:%M:%S")
    sigs = [("LIO_TXN", "LIO_TXN_X"), ("CPU_TXN", "CPU_TXN_X"), ("RT_TXN", "RT_TXN_X"), ("APP_P95_MS", "APP_P95_MS_X")]
    fig, axes = plt.subplots(5, 1, figsize=(10, 9.2), dpi=150, sharex=True,
                             gridspec_kw={"height_ratios": [1.1, 1, 1, 1, 1]})
    fig.patch.set_facecolor(SURFACE)
    # panel 0: alert state rows (flagged minutes as ticks; episode openings as markers)
    ax = axes[0]
    style_axes(ax)
    ax.grid(False)
    ax.set_ylim(-0.7, 1.7)
    ax.set_yticks([1, 0])
    ax.set_yticklabels(["MSET-SPRT", "Static thresholds"], fontsize=9, color=INK)
    ax.axvspan(i0, i1, color=WASH, zorder=0)
    for k, (flag, det) in enumerate([("MSET_FLAG", "MSET"), ("STATIC_FLAG", "STATIC")]):
        y = 1 - k
        xs = [t for t, r in zip(ts, minutes) if r[flag] == "1"]
        ax.vlines(xs, y - 0.22, y + 0.22, color=COLOR[det], linewidth=1.2, zorder=2)
    ax.scatter([m_t], [1.5], marker="v", s=60, color=COLOR["MSET"], zorder=4)
    ax.text(m_t, 1.5, f"  alert opens {m_t:%H:%M}, +{float(m_open['MINUTES_FROM_START']):.1f} min",
            va="center", fontsize=8.5, color=INK)
    ax.scatter([sb_t], [-0.5], marker="o", s=40, facecolors=SURFACE, edgecolors=COLOR["STATIC"], linewidths=1.5,
               zorder=4)
    ax.scatter([s_t], [-0.5], marker="^", s=60, color=COLOR["STATIC"], zorder=4)
    fired = s_open[0]["DETAIL"].split("fired=")[1].split(" ")[0]
    ax.text(s_t, -0.5, f"  first breach {sb_t:%H:%M} ({s_first['DETAIL']}); alert opens {s_t:%H:%M} on {fired}, "
            "not an expected signal", va="center", fontsize=8.5, color=INK)
    ax.set_title("Flagged minutes (ticks: static = any signal past its threshold; its alert needs the same signal "
                 "3 minutes running) and alert openings", loc="left", fontsize=9.5, color=INK2, pad=4)
    # panels 1-4: each expected signal as a multiple of its static upper threshold
    for ax, (sig, col) in zip(axes[1:], sigs):
        style_axes(ax)
        ax.grid(axis="y", color=GRID, linewidth=1)
        ax.grid(axis="x", visible=False)
        ax.axvspan(i0, i1, color=WASH, zorder=0)
        xs = [t for t, r in zip(ts, minutes) if r[col]]
        vs = [float(r[col]) for r in minutes if r[col]]
        ax.plot(xs, vs, color=INK2, linewidth=1.2, zorder=3)
        ax.axhline(1.0, color=COLOR["STATIC"], linewidth=1.2, zorder=2)
        ax.set_ylim(0, 1.2)
        ax.set_yticks([0, 0.5, 1.0])
        ax.axvline(m_t, color=COLOR["MSET"], linewidth=1, zorder=2)
        ax.axvline(s_t, color=COLOR["STATIC"], linewidth=1, zorder=2)
        pk = one(peaks, SIGNAL_CODE=sig)
        ax.set_title(f"{sig}  ·  peak in the incident {float(pk['PEAK_X']):.3f}× its static threshold "
                     f"at {hhmm(pk['PEAK_TS'])}, {pk['MINUTES_AT_OR_ABOVE']} min at or above it",
                     loc="left", fontsize=9.5, color=INK2, pad=4)
    axes[1].text(ts[-1], 1.04, "static threshold (99.5th pct of training) = 1.0", fontsize=8, color=INK2,
                 va="bottom", ha="right")
    axes[-1].set_xlabel("time (UTC), 2026-10-06; shaded = the injected slow_drift LOW run", fontsize=9,
                        color=INK2)
    import matplotlib.dates as mdates
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[-1].xaxis.set_major_locator(mdates.MinuteLocator(byminute=[0, 30]))
    fig.suptitle(f"Slow drift, test day (run {inc['DETAIL']}, {i0:%H:%M}-{i1:%H:%M}Z): MSET-SPRT and the "
                 "static thresholds", x=0.01, ha="left", fontsize=11.5, color=INK)
    fig.text(0.01, 0.945, "Each panel: the signal divided by its static upper threshold (values above 1 breach it). "
             "Blue line = MSET-SPRT alert opens; green line = static alert opens.", fontsize=9, color=INK2)
    fig.tight_layout(rect=(0, 0, 1, 0.935))
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)


# ------------------------------------------------------------------------------------------------ markdown
def md_table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def build(d: dict[str, list[dict[str, str]]]) -> str:
    sc, gr, inc, mc = d["q03_scoreboard"], d["q01_grade_result"], d["q02_incidents"], d["q05_mcnemar"]
    t = lambda det, sl="ALL": one(sc, RUN_TAG="EVAL_TEST", DETECTOR=det, SLICE=sl)  # noqa: E731
    g = lambda det, tag="EVAL_TEST": one(gr, RUN_TAG=tag, DETECTOR=det)  # noqa: E731
    best = one(mc, RUN_TAG="EVAL_TEST", BEST_RIVAL="Y")
    bdet = best["RIVAL_DETECTOR"]
    ms, bs = g("MSET"), g(bdet)
    beats = (int(bs["N_CAUGHT"]), -float(bs["FA_PER_24H"]), -(f(bs["TTD_MEDIAN"]) or 1e9)) > \
            (int(ms["N_CAUGHT"]), -float(ms["FA_PER_24H"]), -(f(ms["TTD_MEDIAN"]) or 1e9))
    others_0fa_same = [r for r in mc if r["RUN_TAG"] == "EVAL_TEST" and r["RIVAL_DETECTOR"] != bdet
                       and int(r["RIVAL_CAUGHT"]) >= int(ms["N_CAUGHT"]) and float(r["RIVAL_FA_PER_24H"]) < float(ms["FA_PER_24H"])]
    head = (f"On the held-out test day a rival beat MSET-SPRT. {LABEL[bdet]} caught {bs['N_CAUGHT']} of "
            f"{bs['N_INCIDENTS']} injected incidents with {int(float(bs['FA_PER_24H']))} false alarms per 24 h; "
            f"MSET-SPRT caught {ms['N_CAUGHT']} of {ms['N_INCIDENTS']} with {int(float(ms['FA_PER_24H']))}. "
            f"The difference in catches is not significant (exact McNemar b = {best['MCNEMAR_B']}, "
            f"c = {best['MCNEMAR_C']}, p = {fmt(best['MCNEMAR_P'], 2)}).") if beats else (
            f"MSET-SPRT was not beaten on the test day: it caught {ms['N_CAUGHT']} of {ms['N_INCIDENTS']} with "
            f"{int(float(ms['FA_PER_24H']))} false alarms per 24 h; the best rival, {LABEL[bdet]}, caught "
            f"{bs['N_CAUGHT']} with {int(float(bs['FA_PER_24H']))} (exact McNemar p = {fmt(best['MCNEMAR_P'], 2)}).")
    if beats and others_0fa_same:
        head += " " + " ".join(
            f"{LABEL[r['RIVAL_DETECTOR']]} also caught {r['RIVAL_CAUGHT']} with {int(float(r['RIVAL_FA_PER_24H']))} "
            "false alarms." for r in others_0fa_same)
    L = []
    L.append("<!-- v1.1 2026-10-07 - v1.1 (phase 6.3): write-time TTDs recomputed at full precision (QUERIES.sql v1.1; "
             "v1.0 cut the write timestamp to whole seconds); nothing else changed. See the note under section 1.\n"
             "     v1.0 2026-10-07 - phase 6.2 of app 900 / brief 10: the graded test day, per docs/phase6.md 6.2 and "
             "PLAN.md v1.7 sections 7 and 7a.\n     Generated by results/make_results.py v1.1 from results/csv/ "
             "(the output of results/QUERIES.sql v1.1); edit the generator, not this file. -->")
    L.append("# Test-day results (phase 6.2): MSET-SPRT against its rivals\n")
    L.append(f"**Headline.** {head}\n")
    sc0 = d["q00_scored"]
    test_rows = by_det(sc0, RUN_TAG="EVAL_TEST")
    L.append("All times UTC. Observer ANOMOPS on ORCLPDB1 (Oracle AI Database 26ai 23.26.1). Test day EVAL_TEST = "
             f"[{test_rows[0]['GRADE_FROM'][:16]}, {test_rows[0]['GRADE_TO'][:16]}) = [EVAL_FROM + 1 day, EVAL_TO); "
             f"dev day EVAL_DEV = [{by_det(sc0, RUN_TAG='EVAL_DEV')[0]['GRADE_FROM'][:16]}, "
             f"{by_det(sc0, RUN_TAG='EVAL_DEV')[0]['GRADE_TO'][:16]}). Fourteen injected incidents per day "
             f"({t('MSET', 'LOW')['N_INCIDENTS']} LOW and {t('MSET', 'HIGH')['N_INCIDENTS']} HIGH on the test day). "
             "Small counts are stated as counts.\n")

    # 1. scoreboard
    L.append("## 1. Scoreboard, test day (EVAL_TEST)\n")
    L.append("Caught = an alert episode starts between the incident's start and its end + 10 min. Time to detect "
             "(TTD) is in minutes: to the begin time of the interval whose scoring opens the episode (PLAN 7a), "
             "and to the moment the live scorer wrote that interval's row (the alert row's write time). False "
             "alarms = episodes starting outside every injected run and its 30-min cool-down. ATTR_P = the chance "
             "that an expected signal is in the opening minute's top 3 with ties broken at random (the primary "
             "attribution score, PLAN 7a v1.7); lenient = top 3 including ties; tie = signals sharing the weight "
             "at position 3.\n")
    rows = []
    for det in DETS:
        a, lo, hi = t(det), t(det, "LOW"), t(det, "HIGH")
        rows.append([f"**{LABEL[det]}**" if det in ("MSET", bdet) else LABEL[det], SETTING[det],
                     f"{lo['N_CAUGHT']}/{lo['N_INCIDENTS']}", f"{hi['N_CAUGHT']}/{hi['N_INCIDENTS']}",
                     f"**{a['N_CAUGHT']}/{a['N_INCIDENTS']}**",
                     f"{fmt(a['TTD_MEDIAN'])} / {fmt(a['TTD_P90'])}",
                     f"{fmt(a['TTD_WRITE_MEDIAN'])} / {fmt(a['TTD_WRITE_P90'])}" if det != "IFOREST" else "n/a",
                     f"{int(float(a['FA_PER_24H']))}",
                     fmt(a["ATTR_P_MEAN"], 3) if det != "IFOREST" else "n/a",
                     f"{a['N_LENIENT_HIT']}/{a['N_LENIENT_SCORED']}" if det != "IFOREST" else "n/a",
                     fmt(a["TIE_SIZE_MEDIAN"], 1) if det != "IFOREST" else "n/a"])
    L.append(md_table(["Detector", "Chosen setting (dev)", "LOW", "HIGH", "All", "TTD median / p90, opening interval",
                       "TTD median / p90, row written", "False alarms / 24 h", "ATTR_P mean", "Lenient",
                       "Median tie"], rows))
    L.append("\n**Correction (v1.1).** v1.0 of this file computed the write-time TTDs from the write timestamp "
             "(SCORE_MINUTE.SCORED_TS, TIMESTAMP(6)) cut to whole seconds, which made each one 0 to 1 s too short "
             "(v1.0 showed 3.13 for every in-database median and 5.83 for MSET-SPRT's p90). The figures here, in "
             "csv/q02_incidents, csv/q03_scoreboard, csv/q11_live_alerts and the note on charts/time_to_detect.png "
             "are at full precision (QUERIES.sql v1.1). The opening-interval TTDs, catches, false alarms, attribution "
             "and McNemar were never affected.")
    L.append("\nIsolation Forest (D5) is an offline reference with no live path and no attribution (PLAN 7), so "
             "its write-time TTD and attribution are n/a. SEASONAL caught nothing, so it has no TTD or "
             "attribution.\n")
    L.append("### 1a. The same, by intensity\n")
    rows = []
    for det in DETS:
        for sl in ("LOW", "HIGH", "ALL"):
            a = t(det, sl)
            rows.append([LABEL[det] if sl == "LOW" else "", sl.lower() if sl != "ALL" else "all",
                         f"{a['N_CAUGHT']}/{a['N_INCIDENTS']}", fmt(a["TTD_MEDIAN"]), fmt(a["TTD_P90"]),
                         fmt(a["TTD_WRITE_MEDIAN"]) if det != "IFOREST" else "n/a",
                         fmt(a["TTD_WRITE_P90"]) if det != "IFOREST" else "n/a",
                         (fmt(a["ATTR_P_MEAN"], 3) if det != "IFOREST" else "n/a"),
                         (f"{a['N_LENIENT_HIT']}/{a['N_LENIENT_SCORED']}" if det != "IFOREST" else "n/a"),
                         (fmt(a["TIE_SIZE_MEDIAN"], 1) if det != "IFOREST" else "n/a"),
                         (f"{int(float(a['FA_PER_24H']))}" if sl == "ALL" else "")])
    L.append(md_table(["Detector", "Slice", "Caught", "TTD median", "TTD p90", "Write TTD median",
                       "Write TTD p90", "ATTR_P mean", "Lenient", "Median tie", "FA / 24 h"], rows))
    L.append("")

    # 2. McNemar
    L.append("## 2. MSET-SPRT against every rival, paired per incident (exact McNemar)\n")
    L.append("b = incidents MSET-SPRT caught and the rival missed, c = the reverse; two-sided exact p. Best rival "
             "(PLAN 7a): most incidents caught, then fewest false alarms per 24 h, then lower median TTD. "
             "grade()'s values; the recomputation from GRADE_INCIDENT (QUERIES.sql Q05) agrees for every row.\n")
    rows = []
    for r in sorted(by_det(mc, RUN_TAG="EVAL_TEST"), key=lambda r: (int(r["PLAN7A_RANK"]), DETS.index(r["RIVAL_DETECTOR"]))):
        agree = r["MCNEMAR_B"] == r["B_RE"] and r["MCNEMAR_C"] == r["C_RE"] and r["MCNEMAR_P"] == r["P_RE"]
        if not agree:
            raise SystemExit(f"McNemar recomputation disagrees for {r['RIVAL_MODEL']}")
        rows.append([LABEL[r["RIVAL_DETECTOR"]], r["PLAN7A_RANK"], f"{r['RIVAL_CAUGHT']}/{r['N_INCIDENTS']}",
                     f"{r['MSET_CAUGHT']}/{r['N_INCIDENTS']}", r["BOTH_CAUGHT"], r["BOTH_MISSED"], r["MCNEMAR_B"],
                     r["MCNEMAR_C"], fmt(r["MCNEMAR_P"], 4), f"{int(float(r['RIVAL_FA_PER_24H']))} vs "
                     f"{int(float(r['MSET_FA']))}", fmt(r["RIVAL_TTD_MEDIAN"]) + " vs " + fmt(r["MSET_TTD_MEDIAN"]),
                     "**yes**" if r["BEST_RIVAL"] == "Y" else ""])
    L.append(md_table(["Rival", "PLAN 7a rank", "Rival caught", "MSET-SPRT caught", "Both caught", "Both missed",
                       "b", "c", "Exact p", "FA / 24 h (rival vs MSET)", "TTD median (rival vs MSET)",
                       "Best rival"], rows))
    disc = [x for x in inc if x["RUN_TAG"] == "EVAL_TEST"]
    def disc_list(rdet, want):
        out = []
        for rid in sorted({x["RUN_ID"] for x in disc}, key=int):
            m = one(disc, RUN_ID=rid, DETECTOR="MSET")
            o = one(disc, RUN_ID=rid, DETECTOR=rdet)
            if (m["CAUGHT"], o["CAUGHT"]) == want:
                out.append(f"{rid} {o['SCENARIO']} {o['INTENSITY']}")
        return "; ".join(out) or "none"
    L.append(f"\nThe discordant incidents against the best rival ({LABEL[bdet]}): MSET-SPRT only: "
             f"{disc_list(bdet, ('Y', 'N'))}; {LABEL[bdet]} only: {disc_list(bdet, ('N', 'Y'))}. "
             f"Missed by MSET-SPRT: {disc_list('MSET', ('N', 'N'))}.\n")

    # 3. matrix
    L.append("## 3. Per-scenario matrix, test day\n")
    L.append("Cell = minutes to detect (opening interval) for a caught incident, 'missed' otherwise; for a "
             "scenario and intensity that ran twice, caught/runs and the minutes in run order.\n")
    mat = by_det(d["q06_scenario_matrix"], RUN_TAG="EVAL_TEST")
    keys = []
    for r in mat:
        k = (r["SCENARIO"], r["INTENSITY"])
        if k not in keys:
            keys.append(k)
    wide = []
    rows = []
    for sc_, it in keys:
        cells = []
        rec = {"scenario": sc_, "intensity": it}
        for det in DETS:
            r = one(mat, SCENARIO=sc_, INTENSITY=it, DETECTOR=det)
            n, c = int(r["N_INCIDENTS"]), int(r["N_CAUGHT"])
            if n == 1:
                cell = r["TTD_LIST"] if c == 1 else "missed"
            else:
                cell = f"{c}/{n}: " + ", ".join(r["TTD_LIST"].split())
            cells.append(cell)
            rec[det] = cell
        rec["run_ids"] = one(mat, SCENARIO=sc_, INTENSITY=it, DETECTOR="MSET")["RUN_IDS"]
        wide.append(rec)
        rows.append([f"{sc_} {it}", rec["run_ids"]] + cells)
    totals = ["**caught**", ""] + [f"**{t(det)['N_CAUGHT']}/{t(det)['N_INCIDENTS']}**" for det in DETS]
    L.append(md_table(["Scenario, intensity", "Run"] + [LABEL[x] for x in DETS], rows + [totals]))
    L.append("")
    write_csv("scenario_matrix_test_wide", wide)

    # 4. false alarms
    L.append("## 4. False alarms\n")
    fa = d["q08_fa_per_day"]
    rows = [[LABEL[det], one(fa, RUN_TAG="EVAL_DEV", DETECTOR=det)["N_FALSE_ALARMS"],
             one(fa, RUN_TAG="EVAL_TEST", DETECTOR=det)["N_FALSE_ALARMS"],
             one(fa, RUN_TAG="EVAL_TEST", DETECTOR=det)["EPISODES"]] for det in DETS]
    L.append(md_table(["Detector", "Dev day", "Test day", "Test-day episodes (all)"], rows))
    L.append("\nEvery false-alarm episode of the two official days (QUERIES.sql Q07):\n")
    rows = [[r["RUN_TAG"], LABEL[r["DETECTOR"]], r["EPISODE_NO"], r["START_TS"][5:19],
             r["END_TS"][11:19] if r["END_TS"] else f"open at {hhmmss(r['LAST_TS'])}", r["MINUTES"],
             r["FIRED"] or r["TOP_SIGNALS"] or "(no stored detail)"] for r in d["q07_false_alarms"]]
    L.append(md_table(["Tag", "Detector", "Episode", "Start", "End", "Minutes", "Fired (R1/R2) or top signals"],
                      rows))
    L.append("")

    # 5. slow drift
    ev, pk = d["q10_drift_events"], d["q16_drift_peaks"]
    incd = one(ev, EVENT="incident")
    L.append("## 5. The slow-drift incident on the test day\n")
    L.append(f"Run {incd['DETAIL']}, {incd['TS'][11:19]}-{incd['TS_END'][11:19]}. Expected signals (PLAN section 6): "
             f"{one(inc, RUN_TAG='EVAL_TEST', DETECTOR='MSET', SCENARIO='slow_drift')['EXPECTED_SIGNALS']}.\n")
    rows = [[e["EVENT"], e["TS"][11:19] if e["TS"] else ("none in [start, end + 10 min]" if e["EVENT"].startswith("first")
                                                         else "-"), fmt(e["MINUTES_FROM_START"], 1), e["DETAIL"]]
            for e in ev if e["EVENT"] != "incident"]
    L.append(md_table(["Event", "Time", "Minutes from start", "Detail"], rows))
    L.append("\nPeak of each expected signal inside the incident, as a multiple of its static upper threshold "
             "(QUERIES.sql Q16):\n")
    L.append(md_table(["Signal", "Static threshold", "Peak (x threshold)", "At", "Minutes at or above"],
                      [[r["SIGNAL_CODE"], fmt(r["STATIC_HI"], 3), fmt(r["PEAK_X"], 3), hhmmss(r["PEAK_TS"]),
                        r["MINUTES_AT_OR_ABOVE"]] for r in pk]))
    L.append("")

    # 6. findings the rules carry (stated, not acted on)
    L.append("## 6. What the numbers carry (stated, not adjusted)\n")
    test_inc = by_det(d["q14_test_incidents"])
    fa_test = by_det(d["q07_false_alarms"], RUN_TAG="EVAL_TEST")
    m_fa = [r for r in fa_test if r["DETECTOR"] == "MSET"]
    pre_open = []
    for r in d["q07_false_alarms"]:
        st = datetime.strptime(r["START_TS"], "%Y-%m-%d %H:%M:%S")
        for x in test_inc + by_det(d["q02_incidents"], RUN_TAG="EVAL_DEV", DETECTOR="MSET"):
            xs = datetime.strptime(x.get("START_TS") or x.get("INC_START"), "%Y-%m-%d %H:%M:%S")
            if 0 < (xs - st).total_seconds() <= 60:
                pre_open.append((r, x))
    peak = max(pk, key=lambda r: float(r["PEAK_X"]))
    mset_drift = one(inc, RUN_TAG="EVAL_TEST", DETECTOR="MSET", SCENARIO="slow_drift")
    st_drift = one(inc, RUN_TAG="EVAL_TEST", DETECTOR="STATIC", SCENARIO="slow_drift")
    em = t("EM")
    lagm, lagl, lagh, lagn = write_lag(inc)
    ttd_vals = sorted({x["TTD_MIN"] for x in inc if x["RUN_TAG"] == "EVAL_TEST" and x["CAUGHT"] == "Y"},
                      key=float)[:4]
    m_first = sorted({(r["TOP_SIGNALS"] or "").split(",")[0] for r in m_fa})
    ep = d["q15_episodes"]
    st_more = [e for e in ep if e["RUN_TAG"] == "EVAL_TEST" and e["DETECTOR"] == "STATIC"
               and e["CLASSIFICATION"].startswith(f"inside window of {incd['DETAIL'].split()[0]} ")
               and e["FIRED"] == st_drift["FIRED"]]
    rival_ties = [float(t(x)["TIE_SIZE_MEDIAN"]) for x in DETS if x not in ("MSET", "IFOREST") and t(x)["TIE_SIZE_MEDIAN"]]
    seas = [e for e in ep if e["DETECTOR"] == "SEASONAL"]
    seas_txt = "; ".join(f"{e['RUN_TAG']}: {sum(1 for x in seas if x['RUN_TAG'] == e['RUN_TAG'])} episode(s), the first "
                         f"opening {hhmmss(e['START_TS'])}, status {e['STATUS']} at the end of the day"
                         for e in seas if e["EPISODE_NO"] == "1")
    inc_secs = sorted({x["INC_START"][17:19] for x in inc if x["RUN_TAG"] == "EVAL_TEST"})
    iv_secs = sorted({x["TS"][17:19] for x in d["q09_drift_minutes"]})
    meds = [float(t(x)["TTD_MEDIAN"]) for x in DETS if t(x)["TTD_MEDIAN"]]
    long_runs = sorted({f"{x['RUN_ID']} {x['SCENARIO']} {x['INTENSITY']}" for x in inc
                        if x["RUN_TAG"] == "EVAL_TEST" and x["CAUGHT"] == "Y" and x["TTD_WRITE_MIN"]
                        and float(x["TTD_WRITE_MIN"]) - float(x["TTD_MIN"]) > 1.7})
    bullets = [
        f"**A rival beats MSET-SPRT on the test day.** {LABEL[bdet]} ranks first among the rivals by PLAN 7a's order "
        f"and ranks above MSET-SPRT on the same order ({bs['N_CAUGHT']} vs {ms['N_CAUGHT']} caught, "
        f"{int(float(bs['FA_PER_24H']))} vs {int(float(ms['FA_PER_24H']))} false alarms). With one discordant "
        f"incident (b = {best['MCNEMAR_B']}, c = {best['MCNEMAR_C']}) the test cannot tell the two apart on catches. "
        "No rival is significantly better or worse than MSET-SPRT on catches except the seasonal baseline, "
        f"which is significantly worse (p = {fmt(one(mc, RUN_TAG='EVAL_TEST', RIVAL_DETECTOR='SEASONAL')['MCNEMAR_P'], 4)}).",
        f"**MSET-SPRT's false alarms rose from {one(d['q08_fa_per_day'], RUN_TAG='EVAL_DEV', DETECTOR='MSET')['N_FALSE_ALARMS']} "
        f"on the dev day to {len(m_fa)} on the test day**, above the 2 per 24 h the tuning rule allowed on dev. "
        "The rule is applied on dev only, so the chosen setting stands. The test-day episodes open at "
        + ", ".join(f"{hhmmss(r['START_TS'])} ({r['TOP_SIGNALS']})" for r in m_fa)
        + (f"; {m_first[0]} is the first signal named in each" if len(m_first) == 1 else "")
        + ". Their cause is not established here.",
        f"**Slow drift was not caught the way the thesis expects.** No expected signal crossed its static threshold "
        f"during run {incd['DETAIL'].split()[0]} (closest: {peak['SIGNAL_CODE']} at {float(peak['PEAK_X']):.3f}x for one "
        f"minute, 0 minutes at or above). The static detector still counts as catching it at "
        f"{fmt(st_drift['TTD_MIN'], 1)} min, through an episode that fired on {st_drift['FIRED']}, which is not in "
        f"slow_drift's expected set (ATTR_P {fmt(st_drift['ATTR_P'], 0)}); it opened {len(st_more)} more {st_drift['FIRED']} "
        f"episode(s) inside the run, at {', '.join(hhmmss(e['START_TS']) for e in st_more)}. MSET-SPRT caught it at "
        f"{fmt(mset_drift['TTD_MIN'], 1)} min and stayed flagged to the end, but its opening minute named "
        f"{mset_drift['TOP3']} first (ATTR_P {fmt(mset_drift['ATTR_P'], 0)}). The catching rule counts both as caught; "
        "the post has to say how.",
        f"**EM anomaly (D3) attributes nothing:** ATTR_P {fmt(em['ATTR_P_MEAN'], 3)} over its {em['N_CAUGHT']} catches; "
        f"for {em['N_CAUGHT_NO_DETAILS']} of them the opening minute has no stored detail signal at all.",
        f"**MSET-SPRT's ties:** median tie size at the cut {fmt(t('MSET')['TIE_SIZE_MEDIAN'], 1)} signals, so its "
        f"lenient rate ({t('MSET')['N_LENIENT_HIT']}/{t('MSET')['N_LENIENT_SCORED']}) is far above its ATTR_P mean "
        f"({fmt(t('MSET')['ATTR_P_MEAN'], 3)}). The rivals' median ties are {min(rival_ties):g}-{max(rival_ties):g} signals.",
        f"**Seasonal baseline (R2) at k = 3** ({seas_txt}) catches nothing, because a catch needs an episode to "
        "start inside an incident, and it counts 1 false alarm a day, as results/dev_tuning.md section 6.1 expected.",
        f"**Time to detect comes in whole-minute steps** ({', '.join(fmt(v, 2) for v in ttd_vals)} ... min): the "
        f"test-day incidents start at second {inc_secs[0]}-{inc_secs[-1]} of a minute and the metric intervals begin "
        f"at second {iv_secs[0]}-{iv_secs[-1]}, so every median sits at {min(meds):.2f}-{max(meds):.2f} min (the "
        "second interval of the incident). Medians therefore barely separate the detectors; the p90s do.",
        f"**Write time.** The live scorer wrote an interval's row {lagm:.2f} min after it began (median over {lagn} "
        f"in-database catches; {lagl:.2f}-{lagh:.2f}). Every lag over 1.7 min belongs to run(s) "
        f"{', '.join(long_runs)}, whose minutes had null application signals; the live scorer waits up to 5 min for "
        "an incomplete minute. For the chosen variants that never ran live, the write "
        "time is the live FINAL model of the same detector scoring that interval in the same job run.",
    ]
    for r, x in pre_open:
        run = x.get("RUN_ID")
        nm = f"{run} {x.get('SCENARIO')} {x.get('INTENSITY')}"
        bullets.append(f"**{LABEL[r['DETECTOR']]}, {r['RUN_TAG']}: an episode opened on the interval that contains "
                       f"the start of run {nm}** ({hhmmss(r['START_TS'])} vs {hhmmss(x.get('START_TS') or x.get('INC_START'))}). "
                       "grade() times an episode from its interval's begin, so it counts as a false alarm and the run "
                       "as missed (no new episode starts in its window). Applied as written.")
    L += [f"- {b}" for b in bullets]
    L.append("")

    # 7. what was run
    L.append("## 7. What was run\n")
    s0 = d["q00_scored"]
    def span(tag):
        rs = by_det(s0, RUN_TAG=tag)
        ins = [r for r in rs if r["DETECTOR"] != "IFOREST"]
        return (min(r["FIRST_SCORED_TS"] for r in ins)[11:19], max(r["LAST_SCORED_TS"] for r in ins)[11:19],
                one(rs, DETECTOR="IFOREST")["FIRST_SCORED_TS"][11:19], min(r["GRADED_TS"] for r in rs)[11:19])
    dv, ts_ = span("EVAL_DEV"), span("EVAL_TEST")
    ifr = one(s0, RUN_TAG="EVAL_TEST", DETECTOR="IFOREST")
    L.append(f"- **Scoring, once.** `PKG_ANOM_SCORE.score_range(model, from, to, tag)` for the six in-database "
             f"models into SCORE_EVAL (never SCORE_MINUTE): EVAL_DEV {dv[0]}-{dv[1]}Z, EVAL_TEST {ts_[0]}-{ts_[1]}Z on "
             "2026-10-07 (Q00). Isolation Forest: `tools/iforest.py` on the demo host, contamination "
             f"{ifr['IFOREST_CONTAMINATION']}, random_state {ifr['IFOREST_RANDOM_STATE']}, trained on the FINAL "
             f"window's {int(ifr['N_ROWS']):,} rows (Q00), EVAL_DEV scored at {dv[2]}Z and EVAL_TEST at {ts_[2]}Z. "
             "From the run record, not a query: the scoring script refuses to run when either tag or any test-day "
             "minute already holds a SCORE_EVAL row (the guard in ops/p6_62_score.sql), and both Isolation Forest runs "
             "exited 0 with a secret scan of their logs finding 0 hits (ops/p6_62_iforest.sh).")
    rows = []
    for r in by_det(s0, RUN_TAG="EVAL_TEST"):
        rd = one(s0, RUN_TAG="EVAL_DEV", DETECTOR=r["DETECTOR"])
        rows.append([LABEL[r["DETECTOR"]], r["MODEL_NAME"], f"{r['VARIANT']} ({r['STATUS']})",
                     f"{rd['MINUTES']} / {rd['FLAGGED']} / {rd['UNSCORED']}",
                     f"{r['MINUTES']} / {r['FLAGGED']} / {r['UNSCORED']}"])
    L.append("\n" + md_table(["Detector", "Model", "Variant (status)", "EVAL_DEV minutes / flagged / unscored",
                              "EVAL_TEST minutes / flagged / unscored"], rows) + "\n")
    L.append(f"- **Official grading** (contract 13.1), graded {dv[3]}Z and {ts_[3]}Z: "
             "`grade('EVAL_DEV', EVAL_FROM, EVAL_FROM + 1, 'SCHEDULE', p_refresh => 'N')` and "
             "`grade('EVAL_TEST', EVAL_FROM + 1, EVAL_TO, 'SCHEDULE', p_refresh => 'N')`. `p_refresh => 'N'` (the "
             "default is 'Y') was used only after checking that the truth loop's last refresh of INCIDENT_TRUTH came "
             "after the last evaluation run had ended, so grading made no call over ANOM_CHAOS_LINK. "
             f"`PKG_ANOM_UI.official_day` returns '{by_det(s0, RUN_TAG='EVAL_DEV')[0]['OFFICIAL_DAY']}' and "
             f"'{by_det(s0, RUN_TAG='EVAL_TEST')[0]['OFFICIAL_DAY']}' for the two tags, so page 3 shows them.")
    q12 = d["q12_dev_matches_devtune"]
    L.append(f"- **EVAL_DEV reproduces the dev-tuning record.** For all {len(q12)} chosen candidates the "
             "official dev-day grading equals results/dev_tuning.md (caught, false alarms, TTD, attribution, every "
             "incident's catch and opening minute: QUERIES.sql Q12, verdict SAME), and the scored minutes match the "
             "DEVTUNE rows one for one (flags, scores, breach lists and stored details).")
    L.append(f"- **The recomputed scoreboard equals GRADE_RESULT** for all {d['q04_check_scoreboard'][0]['MODELS_CHECKED']} "
             "graded models (Q04: 0 differing rows); McNemar recomputed from GRADE_INCIDENT agrees with grade() (Q05).")
    live = d["q11_live_alerts"]
    lc = [r for r in live if r["CAUGHT"] == "Y"]
    same = [r for r in lc if r["LIVE_SAME_START"] == "1"]
    L.append(f"- **Live cross-check** (Q11): the two chosen models that ran live (MSET and STATIC FINAL) opened a live "
             f"ALERT row on the same interval as the graded episode for {len(same)} of their {len(lc)} test-day catches.")
    q13 = {r["ITEM"]: r["VALUE"] for r in d["q13_preconditions_test_day"]}
    L.append("- **Not touched.** From the queries: "
             f"{q13['models created after FINAL_AT that are not dev-tuning CANDIDATEs']} models created after FINAL_AT "
             "other than the dev-tuning CANDIDATEs, and the six ACTIVE models are the FINAL ones, activated at FINAL_AT "
             "(Q13). From the run record (ops/), not a query: this phase trained, activated and dropped no in-database "
             "model (tools/iforest.py fits Isolation Forest afresh on each run, on the same rows with the same seed, as "
             "contract section 8 specifies); "
             "`score_range` writes its batch cost into MODEL_REGISTRY.SCORE_MS_PER_MIN of each model it scores (as on "
             "the dev day); grading made no link call, so oradb1 was neither read nor changed; only ANOMOPS tables "
             "were written (SCORE_EVAL, SCORE_EVAL_DETAIL, ALERT_EVAL, GRADE_INCIDENT, GRADE_RESULT under the two "
             "official tags, and the IFOREST_0.02 registry row's MERGE by tools/iforest.py).\n")

    # 8. preconditions
    L.append("## 8. Phase 6.0, test-day half (deferred to here by results/dev_tuning.md section 3)\n")
    L.append(md_table(["Check", "Result"], [[r["ITEM"], r["VALUE"]] for r in d["q13_preconditions_test_day"]]))
    L.append("\nThe 8 unscored minutes are the minutes of blocking_chain HIGH in which no application transaction "
             "completed (APP_P50_MS and APP_P95_MS null); PLAN 7a leaves them unscored by every detector. Two metric "
             "intervals map to one application minute twice, hence 8 feature minutes for 7 empty application minutes.\n")
    L.append("Test-day incidents (Q14):\n")
    L.append(md_table(["Run", "Scenario", "Intensity", "Start", "End", "Minutes", "Plan row"],
                      [[r["RUN_ID"], r["SCENARIO"], r["INTENSITY"], r["START_TS"][11:19], r["END_TS"][11:19],
                        fmt(r["MINUTES"], 1), r["PLAN_ID"]] for r in d["q14_test_incidents"]]))
    L.append("")

    # 9. dev day under the official tag
    L.append("## 9. The dev day under the official tag (EVAL_DEV)\n")
    rows = []
    for det in DETS:
        a = one(sc, RUN_TAG="EVAL_DEV", DETECTOR=det, SLICE="ALL")
        lo_, hi_ = one(sc, RUN_TAG="EVAL_DEV", DETECTOR=det, SLICE="LOW"), one(sc, RUN_TAG="EVAL_DEV", DETECTOR=det, SLICE="HIGH")
        gg = g(det, "EVAL_DEV")
        rows.append([LABEL[det], f"{lo_['N_CAUGHT']}/{lo_['N_INCIDENTS']}", f"{hi_['N_CAUGHT']}/{hi_['N_INCIDENTS']}",
                     f"{a['N_CAUGHT']}/{a['N_INCIDENTS']}", f"{fmt(a['TTD_MEDIAN'])} / {fmt(a['TTD_P90'])}",
                     a["N_FALSE_ALARMS"], fmt(a["ATTR_P_MEAN"], 3) if det != "IFOREST" else "n/a",
                     (f"b {gg['MCNEMAR_B']}, c {gg['MCNEMAR_C']}, p {fmt(gg['MCNEMAR_P'], 4)}" if det != "MSET" else "reference")
                     + (" (best rival)" if gg["BEST_RIVAL"] == "Y" else "")])
    L.append(md_table(["Detector", "LOW", "HIGH", "All", "TTD median / p90", "False alarms", "ATTR_P mean",
                       "McNemar vs MSET-SPRT"], rows))
    L.append("\nOn the dev day EM and STATIC tie on every PLAN 7a key (11 caught, 0 false alarms, the same median TTD); "
             "grade() then breaks the tie by model name, so EM carries the dev-day best-rival flag." if
             one(mc, RUN_TAG="EVAL_DEV", BEST_RIVAL="Y")["RIVAL_DETECTOR"] == "EM" and
             int(one(mc, RUN_TAG="EVAL_DEV", RIVAL_DETECTOR="STATIC")["PLAN7A_RANK"]) == 1 else "")
    L.append("")

    # 10. files
    L.append("## 10. Files and how to reproduce\n")
    L.append("- `QUERIES.sql` v1.1: every query behind every number here (Q00-Q16), read-only, as ANOMOPS. v1.1 "
             "changes only the three write-time columns (Q02, Q03, Q11) to full precision.\n"
             "- `make_results.py` v1.1: `sqlplus -s ANOMOPS@ORCLPDB1 @QUERIES.sql > queries.out; "
             "python3 make_results.py queries.out` rebuilds `csv/`, this file and `charts/`. It refuses to build when "
             "the spool holds an ORA- or SP2- error, when Q04 finds a difference or when Q12 is not SAME.\n"
             "- This build (v1.1, 07-Oct): the phase 6.2 spool with its Q02, Q03 and Q11 blocks replaced by those of a "
             "full re-run of QUERIES.sql v1.1 at 01:39Z. In that re-run every other block equals the 6.2 spool cell for "
             "cell, except Q13's 'INCIDENT_TRUTH last refresh', a time-of-query value that the truth loop moves every "
             "5 minutes; it keeps its 6.2 value here.\n"
             "- `csv/`: q00_scored, q01_grade_result (GRADE_RESULT), q02_incidents (GRADE_INCIDENT + write time), "
             "q03_scoreboard, q04_check_scoreboard, q05_mcnemar, q06_scenario_matrix (+ scenario_matrix_test_wide), "
             "q07_false_alarms, q08_fa_per_day, q09_drift_minutes, q10_drift_events, q11_live_alerts, "
             "q12_dev_matches_devtune, q13_preconditions_test_day, q14_test_incidents, q15_episodes, q16_drift_peaks.\n"
             "- `charts/`: catches_by_detector_intensity.png, time_to_detect.png, false_alarms_per_day.png, "
             "slow_drift_timeline.png (matplotlib; one colour per detector throughout, MSET-SPRT blue, static green).\n"
             "- `ops/`: the scripts as run, in order: p6_62_checks.sql and p6_62_checks2.sql (the opening checks), "
             "p6_62_score.sql (the one scoring of both official tags), p6_62_iforest.sh (D5 on the demo host), "
             "p6_62_grade.sql (the EVAL_DEV reproduction check and the official grading).\n"
             "- `dev_tuning.md` v1.0 (phase 6.1, unchanged; sha256 ec3baf61...ae4d, as recorded in STATUS.md).")
    L.append("")
    return "\n".join(L), head


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","msg":"%(message)s"}')
    if len(argv) != 2:
        print(__doc__ or "usage: make_results.py <queries.out>", file=sys.stderr)
        return 1
    d = split_spool(Path(argv[1]))
    need = ["q00_scored", "q01_grade_result", "q02_incidents", "q03_scoreboard", "q04_check_scoreboard",
            "q05_mcnemar", "q06_scenario_matrix", "q07_false_alarms", "q08_fa_per_day", "q09_drift_minutes",
            "q10_drift_events", "q11_live_alerts", "q12_dev_matches_devtune", "q13_preconditions_test_day",
            "q14_test_incidents", "q15_episodes", "q16_drift_peaks"]
    missing = [n for n in need if n not in d or not d[n]]
    if missing:
        raise SystemExit(f"missing or empty query output: {missing}")
    chk = d["q04_check_scoreboard"][0]
    if chk["DIFFERING_ROWS"] != "0":
        raise SystemExit("Q04: the recomputed scoreboard differs from GRADE_RESULT")
    if any(r["VERDICT"] != "SAME" for r in d["q12_dev_matches_devtune"]):
        raise SystemExit("Q12: EVAL_DEV does not reproduce the dev-tuning record")
    for name, rows in d.items():
        write_csv(name, rows)
        LOG.info("wrote csv/%s.csv rows=%d", name, len(rows))
    md, head = build(d)
    (HERE / "test_results.md").write_text(md + "\n", encoding="utf-8")
    CHART_DIR.mkdir(exist_ok=True)
    chart_catches(d["q03_scoreboard"], CHART_DIR / "catches_by_detector_intensity.png")
    chart_ttd(d["q02_incidents"], d["q03_scoreboard"], CHART_DIR / "time_to_detect.png")
    chart_fa(d["q08_fa_per_day"], CHART_DIR / "false_alarms_per_day.png")
    chart_drift(d["q09_drift_minutes"], d["q10_drift_events"], d["q16_drift_peaks"],
                CHART_DIR / "slow_drift_timeline.png")
    LOG.info("headline: %s", head)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
