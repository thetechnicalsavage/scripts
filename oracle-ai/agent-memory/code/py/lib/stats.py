# v1.1 2026-10-10  Brief 12 statistics for the pre-registered comparisons (PLAN.md 7.1-7.3, frozen in 7.0): exact
#                  McNemar with Holm (C1), the thread-level exact permutation test with the harmful-invalidation gate
#                  (C2), the paired bootstrap bounds and decisions (C3), and the per-query measures they use.
#                  v1.1: Codex review (day 0): c1_family needs exactly 40 queries in each of A, B, C and no other
#                  class; c3_bootstrap needs exactly 40 queries, so a uniformly dropped query cannot change a design.
#                  v1.0: first version (day 0, no database).
"""Frozen statistics code. Every rule here is written in PLAN.md section 7; a change needs a new plan version before the
first measured run of the comparison it touches. Pure Python plus numpy (pinned in requirements.txt); no database."""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

ALPHA = 0.05

# ----------------------------------------------------------------------------------------------------------------- C1
C1_STRATEGIES = ("HYBRID", "VECTOR", "KEYWORD")
C1_CLASSES = ("A", "B", "C")
# Fixed order of the nine confirmatory tests: per class, (X, Y) pairs as PLAN 7.1 lists them.
C1_PAIRS = (("HYBRID", "VECTOR"), ("HYBRID", "KEYWORD"), ("VECTOR", "KEYWORD"))
C1_PER_CLASS = 40


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p on discordant counts b and c: 2 * P(X <= min(b, c)), X ~ Bin(b + c, 0.5), capped at 1;
    1.0 when there is no discordant pair."""
    if b < 0 or c < 0 or int(b) != b or int(c) != c:
        raise ValueError("b and c must be non-negative integers")
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1))
    return min(1.0, 2.0 * tail / (2 ** n))


def holm(pvalues: Sequence[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values, monotone (step-down) and capped at 1, in the input order."""
    m = len(pvalues)
    if m == 0:
        raise ValueError("no p-values")
    if any(not (0.0 <= p <= 1.0) for p in pvalues):
        raise ValueError("p-values must lie in [0, 1]")
    order = sorted(range(m), key=lambda i: (pvalues[i], i))
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def hit_at_k(ranked_ids: Sequence[str], gold_id: str, k: int) -> bool:
    """Gold among the first k results; an empty result list is a miss (PLAN 7.1)."""
    if k < 1:
        raise ValueError("k must be >= 1")
    return gold_id in list(ranked_ids)[:k]


def reciprocal_rank_at_k(ranked_ids: Sequence[str], gold_id: str, k: int = 10) -> float:
    """1/rank of the gold within the first k results, 0 when it is not there (MRR@10 is the mean of this)."""
    top = list(ranked_ids)[:k]
    return 1.0 / (top.index(gold_id) + 1) if gold_id in top else 0.0


@dataclass(frozen=True)
class McNemarTest:
    query_class: str
    x: str
    y: str
    hits_x: int
    hits_y: int
    b: int            # X hit, Y miss
    c: int            # X miss, Y hit
    n: int            # queries in the class
    p: float
    p_holm: float
    verdict: str      # "<X> better", "<Y> better" or "no difference detected"


def c1_family(hit1: Mapping[str, Mapping[str, bool]], query_class: Mapping[str, str]) -> list[McNemarTest]:
    """The nine confirmatory tests of PLAN 7.1 with Holm over all nine at family-wise alpha 0.05.

    hit1[strategy][query_id] is Hit@1 of that query; query_class[query_id] is "A", "B" or "C". X is better than Y on a
    class only if X has more Hit@1 there (b > c) and the Holm-adjusted p is below 0.05.
    """
    per_class = {c: sum(1 for k in query_class.values() if k == c) for c in C1_CLASSES}
    if set(query_class.values()) != set(C1_CLASSES) or any(n != C1_PER_CLASS for n in per_class.values()):
        raise ValueError(f"C1 needs exactly {C1_PER_CLASS} queries in each of A, B, C and no other class, got {per_class}")
    for s in C1_STRATEGIES:
        if s not in hit1:
            raise ValueError(f"missing strategy {s}")
        if set(hit1[s]) != set(query_class):
            raise ValueError(f"strategy {s} does not cover exactly the labelled queries")
    rows = []
    for cls in C1_CLASSES:
        qids = sorted(q for q, k in query_class.items() if k == cls)
        if not qids:
            raise ValueError(f"no queries in class {cls}")
        for x, y in C1_PAIRS:
            b = sum(1 for q in qids if hit1[x][q] and not hit1[y][q])
            c = sum(1 for q in qids if not hit1[x][q] and hit1[y][q])
            rows.append((cls, x, y, sum(hit1[x][q] for q in qids), sum(hit1[y][q] for q in qids), b, c, len(qids),
                         mcnemar_exact(b, c)))
    adjusted = holm([r[-1] for r in rows])
    out = []
    for r, padj in zip(rows, adjusted):
        cls, x, y, hx, hy, b, c, n, p = r
        if padj < ALPHA and b != c:
            verdict = f"{x if b > c else y} better"
        else:
            verdict = "no difference detected"
        out.append(McNemarTest(cls, x, y, hx, hy, b, c, n, p, padj, verdict))
    return out


def percentile_linear(values: Sequence[float], q: float) -> float:
    """Percentile with numpy's 'linear' method, q in [0, 100] (exploratory latency medians and p90)."""
    if not values:
        raise ValueError("no values")
    return float(np.percentile(np.asarray(values, dtype=float), q, method="linear"))


# ----------------------------------------------------------------------------------------------------------------- C2
C2_N_THREADS = 12
C2_TOL = 1e-12


def micro_f1(counts: Sequence[tuple[int, int, int]]) -> float:
    """2*sum(TP) / (2*sum(TP) + sum(FP) + sum(FN)) over threads; 0 when the denominator is 0 (PLAN 7.2)."""
    tp = sum(c[0] for c in counts)
    fp = sum(c[1] for c in counts)
    fn = sum(c[2] for c in counts)
    den = 2 * tp + fp + fn
    return 2 * tp / den if den else 0.0


@dataclass(frozen=True)
class PermutationResult:
    f1_post: float
    f1_during: float
    t_observed: float
    n_extreme: int
    n_assignments: int
    p: float


def c2_permutation_test(post: Sequence[tuple[int, int, int]],
                        during: Sequence[tuple[int, int, int]]) -> PermutationResult:
    """Exact paired permutation test of PLAN 7.2.

    post[t] and during[t] are (TP, FP, FN) of thread t summed over the 3 runs. For every s in {0,1}^12 (identity
    included) the two modes' counts are swapped in each thread with s_t = 1, both micro-F1 values are recomputed and
    T(s) = F1(POST) - F1(DURING). p = #{s : |T(s)| >= |T| - 1e-12} / 4096; ties count as extreme.
    """
    if len(post) != len(during) or len(post) != C2_N_THREADS:
        raise ValueError(f"need {C2_N_THREADS} threads for each mode")
    for row in list(post) + list(during):
        if len(row) != 3 or any((not isinstance(v, int)) or v < 0 for v in row):
            raise ValueError("counts must be (TP, FP, FN) non-negative integers")
    t_obs = micro_f1(post) - micro_f1(during)
    extreme = 0
    total = 0
    for s in itertools.product((0, 1), repeat=C2_N_THREADS):
        a = [during[i] if s[i] else post[i] for i in range(C2_N_THREADS)]
        b = [post[i] if s[i] else during[i] for i in range(C2_N_THREADS)]
        if abs(micro_f1(a) - micro_f1(b)) >= abs(t_obs) - C2_TOL:
            extreme += 1
        total += 1
    return PermutationResult(micro_f1(post), micro_f1(during), t_obs, extreme, total, extreme / total)


def c2_verdict(perm: PermutationResult, harmful_post_runs: Sequence[int], harmful_during_runs: Sequence[int]) -> str:
    """PLAN 7.2's rule. A mode 'links more accurately' only if its micro-F1 is higher, the test rejects (p < 0.05) and
    neither automatic mode made a harmful invalidation in any of its 3 runs. With any harmful invalidation the
    'higher F1, but it invalidated N facts that were still current' sentence is used and neither is called better."""
    if len(harmful_post_runs) != 3 or len(harmful_during_runs) != 3:
        raise ValueError("need harmful-invalidation counts for 3 runs of each mode")
    hp, hd = sum(harmful_post_runs), sum(harmful_during_runs)
    if hp > 0 or hd > 0:
        return (f"neither better: harmful invalidations POST {hp}, DURING {hd} over 3 runs "
                f"(micro-F1 POST {perm.f1_post:.3f}, DURING {perm.f1_during:.3f}, p {perm.p:.4f})")
    if perm.p < ALPHA and perm.f1_post != perm.f1_during:
        winner = "POST_EXTRACTION" if perm.f1_post > perm.f1_during else "DURING_EXTRACTION"
        return f"{winner} links more accurately (p {perm.p:.4f})"
    return f"no difference we could detect with 12 threads (p {perm.p:.4f})"


# ----------------------------------------------------------------------------------------------------------------- C3
C3_ARMS = ("TOPK10", "TOPK5", "PRUNE_FAST", "PRUNE_EXT", "PRUNE_EXH")
C3_PRUNING_ARMS = ("PRUNE_FAST", "PRUNE_EXT", "PRUNE_EXH")
C3_SEED = 20261009
C3_N_QUERIES = 40
C3_RESAMPLES = 10_000
C3_DECISIONS = 6                         # 3 pruning arms x 2 decisions
C3_TAIL = ALPHA / C3_DECISIONS           # 0.00833..., one-sided
C3_R10_MAX = 0.80
C3_D10_MIN = -0.10
C3_R5_MAX = 1.00
TOKEN_CHARS = 3.5                        # oracleagentmemory 26.8.0, apis/message.pyc:344 (API_NOTES B20)


def estimated_tokens(formatted_contents: Sequence[str]) -> int:
    """Returned estimated tokens of one result list, exactly as the SDK's budget code counts them:
    sum of max(1, round(len(formatted_content) / 3.5)), with Python's round (half to even) like the SDK."""
    return sum(max(1, round(len(fc) / TOKEN_CHARS)) for fc in formatted_contents)


def recall_precision(returned_ids: Sequence[str], gold_ids: Sequence[str]) -> tuple[float, float]:
    """Gold recall and precision of one returned list. An empty list has precision 0 (PLAN 7.3, v0.3 precision)."""
    gold = set(gold_ids)
    if not gold:
        raise ValueError("empty gold set")
    got = list(dict.fromkeys(returned_ids))
    hit = sum(1 for r in got if r in gold)
    return hit / len(gold), (hit / len(got) if got else 0.0)


@dataclass(frozen=True)
class C3ArmDecision:
    arm: str
    r10_upper: float
    d10_lower: float
    r5_upper: float
    d5_lower: float
    decision_a: bool   # smaller context without a material recall loss
    decision_b: bool   # better than simply taking 5


def c3_bootstrap(tokens: Mapping[str, Sequence[float]], recall: Mapping[str, Sequence[float]],
                 seed: int = C3_SEED, resamples: int = C3_RESAMPLES) -> list[C3ArmDecision]:
    """Paired bootstrap of PLAN 7.3.

    tokens[arm][q] and recall[arm][q] are per-query values (pruning arms: means over their 3 runs). One draw,
    numpy default_rng(seed).integers(0, n, size=(resamples, n)), is shared by every arm and every decision. Bounds are
    one-sided percentile bounds at 0.05/6 with numpy's 'linear' quantile. A resample whose token denominator is 0 gives a
    non-finite ratio; a non-finite bound makes its condition false (no benefit can be shown).
    """
    for arm in C3_ARMS:
        if arm not in tokens or arm not in recall:
            raise ValueError(f"missing arm {arm}")
    n = len(tokens["TOPK10"])
    if n != C3_N_QUERIES:
        raise ValueError(f"C3 needs exactly {C3_N_QUERIES} queries, got {n}")
    for arm in C3_ARMS:
        if len(tokens[arm]) != n or len(recall[arm]) != n:
            raise ValueError(f"arm {arm} does not have {n} queries")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(resamples, n))
    tok = {a: np.asarray(tokens[a], dtype=float)[idx].sum(axis=1) for a in C3_ARMS}
    rec = {a: np.asarray(recall[a], dtype=float)[idx].mean(axis=1) for a in C3_ARMS}

    def ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)

    def upper(x: np.ndarray) -> float:
        return float(np.quantile(x, 1.0 - C3_TAIL, method="linear")) if np.all(np.isfinite(x)) else math.nan

    def lower(x: np.ndarray) -> float:
        return float(np.quantile(x, C3_TAIL, method="linear")) if np.all(np.isfinite(x)) else math.nan

    out = []
    for arm in C3_PRUNING_ARMS:
        r10u = upper(ratio(tok[arm], tok["TOPK10"]))
        d10l = lower(rec[arm] - rec["TOPK10"])
        r5u = upper(ratio(tok[arm], tok["TOPK5"]))
        d5l = lower(rec[arm] - rec["TOPK5"])
        # NaN compares false, so a non-finite bound never establishes a benefit.
        a = bool(r10u < C3_R10_MAX and d10l > C3_D10_MIN)
        b = bool(d5l > 0.0 and r5u <= C3_R5_MAX)
        out.append(C3ArmDecision(arm, r10u, d10l, r5u, d5l, a, b))
    return out


def topk_deterministic(run1: Mapping[str, Sequence[str]], run2: Mapping[str, Sequence[str]]) -> list[str]:
    """Query ids whose two top-K runs returned different lists (PLAN 7.3 determinism check; empty means identical)."""
    if set(run1) != set(run2):
        raise ValueError("the two runs cover different queries")
    return sorted(q for q in run1 if list(run1[q]) != list(run2[q]))
