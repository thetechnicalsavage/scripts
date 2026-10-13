# v1.0 2026-10-10  Brief 12 tests for py/lib/matcher.py (PLAN 7.2): normalisation, key terms on token boundaries,
#                  slot mapping (matched, unmatched, ambiguous) and link scoring; happy and failure paths.
#                  v1.0: first version.
import pytest

from lib import matcher as m


@pytest.mark.parametrize("text, expected", [
    ("I used 20GB", "i used 20 gb"),
    ("I used 20 GB", "i used 20 gb"),
    ("twenty: 20 gigabytes", "twenty: 20 gb"),
    ("On 3 September 2026", "on september 3"),
    ("2026-09-03", "september 3"),
    ("03-Sep", "september 3"),
    ("Sep 3rd, 2026", "september 3"),
    ("the Sept bill", "the september bill"),
    ("QAR45", "qar 45"),
    ("45 riyals", "qar 45"),
    ("1,200.00 QAR", "qar 1200"),
    ("1.40 GB", "1.4 gb"),
    ("80%", "80 percent"),
    ("-112dBm", "-112 dbm"),
    ("  WhatsApp \n  only ", "whatsapp only"),
    ("PLN-UNL-50", "pln-unl-50"),
    ("RMG-GCC-7D", "rmg-gcc-7d"),
])
def test_normalize_happy(text, expected):
    assert m.normalize(text) == expected


def test_normalize_rejects_non_text():
    with pytest.raises(TypeError):
        m.normalize(None)


def test_numeric_slash_dates_are_left_alone():
    assert m.normalize("03/09/2026") == "03/09/2026"


@pytest.mark.parametrize("term, text, present", [
    ("5 gb", "cap at 5GB", True),
    ("5 gb", "cap at 15 GB", False),             # token boundary
    ("PLN-UNL-5", "moved to PLN-UNL-50", False),
    ("PLN-UNL-50", "moved to PLN-UNL-50.", True),
    ("september 3", "activated on 2026-09-03", True),
    ("qar 45", "a refund of 45 QAR", True),
    ("app", "send it in the app", True),
    ("app", "I am happy", False),
])
def test_term_present(term, text, present):
    assert m.term_present(term, m.normalize(text)) is present


def test_empty_term_fails():
    with pytest.raises(ValueError):
        m.term_present("   ", "anything")


SLOTS = [
    m.Slot("A", (("whatsapp",), ("email", "e-mail"))),
    m.Slot("B", (("whatsapp",), ("weekday", "weekdays"))),
    m.Slot("C", (("PLN-DAT-20",),)),
]


def test_map_memory_matched_unmatched_ambiguous():
    assert m.map_memory("Customer prefers WhatsApp over e-mail.", SLOTS) == m.MatchResult("A", "matched", ("A",))
    assert m.map_memory("Customer wants a new phone.", SLOTS).outcome == "unmatched"
    r = m.map_memory("WhatsApp instead of email, weekdays only.", SLOTS)
    assert r.outcome == "ambiguous" and r.slot_id is None and r.candidates == ("A", "B")


def test_slot_rejects_bad_definitions():
    with pytest.raises(ValueError):
        m.Slot("X", ())
    with pytest.raises(ValueError):
        m.Slot("X", (("",),))
    with pytest.raises(ValueError):
        m.Slot("X", (("a",),), end_status="valid")


def _thread():
    slots = [m.Slot("T1", (("PLN-UNL-60",),), "replaced"), m.Slot("S1", (("PLN-DAT-20",),)),
             m.Slot("T2", (("arabic",),), "replaced"), m.Slot("S2", (("english",),)),
             m.Slot("DA", (("london",),)), m.Slot("DB", (("manila",),))]
    gold = [m.GoldLink("S1", "T1", "supersedes"), m.GoldLink("S2", "T2", "supersedes")]
    mems = {"m1": "Wants the unlimited plan PLN-UNL-60.", "m2": "Now wants plan PLN-DAT-20 from next month.",
            "m3": "Bills in Arabic.", "m4": "Bills in English from now on.", "m5": "Flies to London twice a year.",
            "m6": "Sister visits from Manila.", "m7": "Likes football."}
    return slots, gold, mems, [("DA", "DB")]


def test_score_perfect_run():
    slots, gold, mems, dx = _thread()
    pred = [m.PredictedLink("m2", "m1", "supersedes"), m.PredictedLink("m4", "m3", "supersedes")]
    sc = m.score_thread(mems, pred, slots, gold, dx)
    assert (sc.tp, sc.fp, sc.fn, sc.harmful_invalidations, sc.distractor_links) == (2, 0, 0, 0, 0)
    assert sc.slots_covered == 6 and sc.memories_unmatched == 1 and sc.pair_hits == 2


def test_score_direction_type_harm_and_distractors():
    slots, gold, mems, dx = _thread()
    pred = [m.PredictedLink("m1", "m2", "supersedes"),    # reversed: FP, and it invalidates the current S1 -> harmful
            m.PredictedLink("m4", "m3", "refines"),       # wrong type: FP, target replaced -> not harmful
            m.PredictedLink("m6", "m5", "supports"),      # distractor link: FP
            m.PredictedLink("m7", "m3", "duplicates")]    # unmapped source: FP
    sc = m.score_thread(mems, pred, slots, gold, dx)
    assert (sc.tp, sc.fp, sc.fn) == (0, 4, 2)
    assert sc.harmful_invalidations == 1 and sc.distractor_links == 1
    assert sc.pair_hits == 2          # type-agnostic, unordered: both gold pairs have some link


def test_gold_link_credited_once():
    slots, gold, mems, dx = _thread()
    mems = dict(mems, m8="Plan PLN-DAT-20 confirmed.")
    pred = [m.PredictedLink("m2", "m1", "supersedes"), m.PredictedLink("m8", "m1", "supersedes")]
    sc = m.score_thread(mems, pred, slots, gold, dx)
    assert (sc.tp, sc.fp, sc.fn) == (1, 1, 1)


def test_unmapped_invalidating_target_is_counted_apart():
    slots, gold, mems, dx = _thread()
    sc = m.score_thread(mems, [m.PredictedLink("m2", "m7", "supersedes")], slots, gold, dx)
    assert sc.invalidations_unmapped_target == 1 and sc.harmful_invalidations == 0


def test_score_rejects_bad_inputs():
    slots, gold, mems, dx = _thread()
    with pytest.raises(ValueError):
        m.score_thread(mems, [], slots, [m.GoldLink("S1", "ZZ", "supersedes")], dx)
    with pytest.raises(ValueError):
        m.score_thread(mems, [], slots, gold, [("DA", "DA")])
    with pytest.raises(ValueError):
        m.PredictedLink("a", "b", "replaces")
    with pytest.raises(ValueError):
        m.GoldLink("S1", "S1", "supports")


def test_end_statuses_and_prf():
    gold = [m.GoldLink("b", "a", "supersedes"), m.GoldLink("d", "c", "supports"), m.GoldLink("f", "e", "duplicates")]
    st = m.end_statuses("abcdef", gold)
    assert st == {"a": "replaced", "b": "current", "c": "current", "d": "current", "e": "replaced", "f": "current"}
    assert m.prf(0, 0, 0) == (0.0, 0.0, 0.0)
    p, r, f = m.prf(3, 1, 2)
    assert (p, r) == (0.75, 0.6) and f == pytest.approx(6 / 9)
