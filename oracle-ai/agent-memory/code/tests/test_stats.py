# v1.1 2026-10-10  Brief 12 tests for py/lib/stats.py (PLAN 7.1-7.3): exact McNemar and the Holm thresholds the plan
#                  states, the C1 family verdicts, the C2 permutation test and gate, the C3 bootstrap decisions and the
#                  per-query measures; happy and failure paths.
#                  v1.1: Codex review (day 0): a uniformly dropped C1 or C3 query and an extra C1 class are refused.
#                  v1.0: first version.
import math

import pytest

from lib import stats as s


def test_mcnemar_known_values():
    assert s.mcnemar_exact(0, 0) == 1.0
    assert s.mcnemar_exact(9, 0) == pytest.approx(2 / 2 ** 9)
    assert s.mcnemar_exact(0, 9) == s.mcnemar_exact(9, 0)
    assert s.mcnemar_exact(5, 5) == 1.0
    assert s.mcnemar_exact(12, 1) == pytest.approx(2 * 14 / 2 ** 13)


@pytest.mark.parametrize("b, c, rejects", [
    (9, 0, True), (8, 0, False), (12, 1, True), (11, 1, False),
    (14, 2, True), (13, 2, False), (16, 3, True), (15, 3, False),
])
def test_first_holm_rejection_thresholds_stated_in_plan(b, c, rejects):
    # PLAN 7.1: the first rejection under Holm over nine tests needs p < 0.05/9.
    assert (s.mcnemar_exact(b, c) < 0.05 / 9) is rejects


def test_mcnemar_rejects_bad_counts():
    with pytest.raises(ValueError):
        s.mcnemar_exact(-1, 2)
    with pytest.raises(ValueError):
        s.mcnemar_exact(1.5, 2)


def test_holm_known_example():
    adj = s.holm([0.01, 0.04, 0.03, 0.005])
    assert adj == pytest.approx([0.03, 0.06, 0.06, 0.02])
    assert s.holm([0.5, 0.9]) == pytest.approx([1.0, 1.0])


def test_holm_failure_paths():
    with pytest.raises(ValueError):
        s.holm([])
    with pytest.raises(ValueError):
        s.holm([0.2, 1.2])


def _c1(hits_by_strategy):
    qclass = {f"{c}{i:02d}": c for c in "ABC" for i in range(40)}
    hit1 = {k: {q: hits_by_strategy[k](q) for q in qclass} for k in s.C1_STRATEGIES}
    return s.c1_family(hit1, qclass)


def test_c1_family_detects_a_clear_win_and_reports_no_difference_otherwise():
    # Class A: HYBRID hits all 40, KEYWORD hits all, VECTOR hits 25: HYBRID vs VECTOR 15-0 (and KEYWORD vs VECTOR 0-15)
    rows = _c1({"HYBRID": lambda q: True, "KEYWORD": lambda q: q[0] == "A",
                "VECTOR": lambda q: not (q[0] == "A" and int(q[1:]) < 15)})
    assert len(rows) == 9
    by = {(r.query_class, r.x, r.y): r for r in rows}
    hv = by[("A", "HYBRID", "VECTOR")]
    assert (hv.b, hv.c, hv.verdict) == (15, 0, "HYBRID better")
    assert by[("A", "VECTOR", "KEYWORD")].verdict == "KEYWORD better"
    assert by[("A", "HYBRID", "KEYWORD")].verdict == "no difference detected"
    assert all(r.p_holm >= r.p for r in rows)
    hk = by[("B", "HYBRID", "KEYWORD")]
    assert (hk.b, hk.c, hk.verdict) == (40, 0, "HYBRID better")


def test_c1_family_8_to_0_is_not_detectable():
    rows = _c1({"HYBRID": lambda q: True, "VECTOR": lambda q: not (q[0] == "B" and int(q[1:]) < 8),
                "KEYWORD": lambda q: True})
    r = next(x for x in rows if (x.query_class, x.x, x.y) == ("B", "HYBRID", "VECTOR"))
    assert (r.b, r.c) == (8, 0) and r.verdict == "no difference detected"


def test_c1_family_failure_paths():
    qclass = {f"{c}{i:02d}": c for c in "ABC" for i in range(40)}
    with pytest.raises(ValueError):
        s.c1_family({"HYBRID": {q: True for q in qclass}}, qclass)
    bad = {k: {"A00": True} for k in s.C1_STRATEGIES}
    with pytest.raises(ValueError):
        s.c1_family(bad, qclass)
    dropped = {q: c for q, c in qclass.items() if q != "B07"}          # one query missing from every strategy
    with pytest.raises(ValueError):
        s.c1_family({k: {q: True for q in dropped} for k in s.C1_STRATEGIES}, dropped)
    extra = dict(qclass, D00="D")
    with pytest.raises(ValueError):
        s.c1_family({k: {q: True for q in extra} for k in s.C1_STRATEGIES}, extra)


def test_hit_and_rr():
    assert s.hit_at_k(["x", "g"], "g", 1) is False and s.hit_at_k(["x", "g"], "g", 5) is True
    assert s.hit_at_k([], "g", 1) is False
    assert s.reciprocal_rank_at_k(["x", "y", "g"], "g") == pytest.approx(1 / 3)
    assert s.reciprocal_rank_at_k(["x"] * 10 + ["g"], "g") == 0.0
    with pytest.raises(ValueError):
        s.hit_at_k(["g"], "g", 0)


def test_percentile_linear():
    assert s.percentile_linear([1, 2, 3, 4], 50) == 2.5
    with pytest.raises(ValueError):
        s.percentile_linear([], 50)


def test_micro_f1():
    assert s.micro_f1([(0, 0, 0)] * 12) == 0.0
    assert s.micro_f1([(3, 1, 0), (0, 0, 2)]) == pytest.approx(6 / 9)


def test_permutation_identical_modes_give_p_one():
    rows = [(2, 1, 1)] * 12
    r = s.c2_permutation_test(rows, rows)
    assert r.t_observed == 0 and r.p == 1.0 and r.n_assignments == 4096


def test_permutation_extreme_split_reaches_the_minimum_p():
    post = [(9, 0, 0)] * 12
    during = [(0, 0, 9)] * 12
    r = s.c2_permutation_test(post, during)
    assert r.f1_post == 1.0 and r.f1_during == 0.0
    assert r.n_extreme == 2 and r.p == pytest.approx(2 / 4096)


def test_permutation_counts_ties_as_extreme():
    post = [(3, 0, 0)] + [(1, 1, 1)] * 11
    during = [(0, 0, 3)] + [(1, 1, 1)] * 11
    r = s.c2_permutation_test(post, during)
    # only thread 1 differs: swapping it flips the sign, every other swap leaves |T| unchanged -> all extreme
    assert r.p == 1.0


def test_permutation_failure_paths():
    with pytest.raises(ValueError):
        s.c2_permutation_test([(1, 0, 0)] * 11, [(1, 0, 0)] * 11)
    with pytest.raises(ValueError):
        s.c2_permutation_test([(1, 0, -1)] * 12, [(1, 0, 0)] * 12)


def test_c2_verdict_gate():
    win = s.c2_permutation_test([(9, 0, 0)] * 12, [(0, 0, 9)] * 12)
    assert s.c2_verdict(win, [0, 0, 0], [0, 0, 0]).startswith("POST_EXTRACTION links more accurately")
    assert s.c2_verdict(win, [0, 1, 0], [0, 0, 0]).startswith("neither better")
    assert s.c2_verdict(win, [0, 0, 0], [2, 0, 0]).startswith("neither better")
    same = s.c2_permutation_test([(2, 1, 1)] * 12, [(2, 1, 1)] * 12)
    assert s.c2_verdict(same, [0, 0, 0], [0, 0, 0]).startswith("no difference")
    with pytest.raises(ValueError):
        s.c2_verdict(win, [0, 0], [0, 0, 0])


def test_estimated_tokens_matches_sdk_rule():
    # round() is Python's, as in the SDK: 7 chars / 3.5 = 2.0; 1 char -> max(1, 0) = 1; 30 chars -> 8.57 -> 9
    assert s.estimated_tokens(["x" * 7]) == 2
    assert s.estimated_tokens(["x" * 1]) == 1
    assert s.estimated_tokens(["x" * 30]) == 9
    assert s.estimated_tokens([]) == 0
    assert s.estimated_tokens(["x" * 7, "y" * 35]) == 12


def test_recall_precision():
    assert s.recall_precision(["a", "b", "x"], ["a", "b", "c", "d"]) == (0.5, pytest.approx(2 / 3))
    assert s.recall_precision([], ["a"]) == (0.0, 0.0)
    with pytest.raises(ValueError):
        s.recall_precision(["a"], [])


def _c3(prune_tokens, prune_recall, n=40):
    base = {"TOPK10": ([100.0] * n, [0.9] * n), "TOPK5": ([50.0] * n, [0.7] * n)}
    data = {a: (list(prune_tokens), list(prune_recall)) for a in s.C3_PRUNING_ARMS}
    data.update(base)
    return {a: v[0] for a, v in data.items()}, {a: v[1] for a, v in data.items()}


def test_c3_clear_benefit_passes_both_decisions():
    tok, rec = _c3([40.0] * 40, [0.88] * 40)
    out = s.c3_bootstrap(tok, rec)
    assert [d.arm for d in out] == list(s.C3_PRUNING_ARMS)
    assert all(d.decision_a and d.decision_b for d in out)
    assert out[0].r10_upper == pytest.approx(0.4)


def test_c3_recall_loss_fails_decision_a():
    tok, rec = _c3([40.0] * 40, [0.6] * 40)
    out = s.c3_bootstrap(tok, rec)
    assert not any(d.decision_a for d in out) and not any(d.decision_b for d in out)


def test_c3_bootstrap_is_deterministic_and_uses_one_draw():
    tok, rec = _c3([40.0 + (i % 7) for i in range(40)], [0.5 + (i % 5) / 10 for i in range(40)])
    assert s.c3_bootstrap(tok, rec) == s.c3_bootstrap(tok, rec)


def test_c3_zero_token_denominator_never_shows_a_benefit():
    tok, rec = _c3([40.0] * 40, [0.95] * 40)
    tok["TOPK10"] = [0.0] * 40
    tok["TOPK5"] = [0.0] * 40
    out = s.c3_bootstrap(tok, rec)
    assert all(math.isnan(d.r10_upper) and not d.decision_a and not d.decision_b for d in out)


def test_c3_failure_paths():
    tok, rec = _c3([40.0] * 40, [0.9] * 40)
    del tok["PRUNE_EXT"]
    with pytest.raises(ValueError):
        s.c3_bootstrap(tok, rec)
    tok, rec = _c3([40.0] * 39, [0.9] * 39)
    with pytest.raises(ValueError):
        s.c3_bootstrap(tok, rec)
    tok, rec = _c3([40.0] * 39, [0.9] * 39, n=39)                      # the same query missing from every arm
    with pytest.raises(ValueError):
        s.c3_bootstrap(tok, rec)


def test_topk_determinism_check():
    assert s.topk_deterministic({"q1": ["a", "b"]}, {"q1": ["a", "b"]}) == []
    assert s.topk_deterministic({"q1": ["a", "b"]}, {"q1": ["b", "a"]}) == ["q1"]
    with pytest.raises(ValueError):
        s.topk_deterministic({"q1": []}, {"q2": []})
