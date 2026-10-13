# v1.2 2026-10-10  Brief 12 tests for the seeded generators and the frozen sets (PLAN section 3, 7.0-7.3): the frozen
#                  files equal a fresh generation, the counts and labels the plan fixes hold when re-derived from the
#                  files, the hero seeds follow the SDK's link rules, and each validator rejects a broken set.
#                  v1.2: the freeze-manifest test moved to test_freeze_check.py.
#                  v1.1: a dated C2 event on or after the first thread day is refused (blind review round 5).
#                  v1.0: first version.
import copy
import json
from pathlib import Path

import pytest

import gen_conversations as gc
import gen_retrieval_corpus as gr
import make_bill_images as mbi
from lib import hero_seed as hs
from lib import matcher as m

FROZEN = Path(__file__).resolve().parents[1] / "data" / "frozen"


def _jsonl(name):
    return [json.loads(x) for x in (FROZEN / name).read_text(encoding="ascii").splitlines()]


@pytest.fixture(scope="module")
def c1():
    return gr.build(gr.SEED)


@pytest.fixture(scope="module")
def c2():
    return gc.build(gc.SEED)


# ---------------------------------------------------------------------------------------------- frozen = regenerated
def test_frozen_retrieval_files_equal_a_fresh_generation(c1):
    for name, rows in zip(gr.FILES, c1):
        assert (FROZEN / name).read_bytes() == gr.dump_jsonl(rows), name


def test_frozen_conversation_files_equal_a_fresh_generation(c2):
    for name, rows in zip(gc.FILES, c2):
        assert (FROZEN / name).read_bytes() == gc.dump_jsonl(rows), name


def test_frozen_images_equal_a_fresh_drawing():
    for rel, data in mbi.build().items():
        assert (FROZEN / rel).read_bytes() == data, rel


# ---------------------------------------------------------------------------------------- C1 and C3, from the files
def test_corpus_counts_and_identifiers():
    corpus = _jsonl("c1_corpus.jsonl")
    assert len(corpus) == 400
    assert {k: sum(1 for x in corpus if x["kind"] == k) for k in gr.KIND_COUNTS} == gr.KIND_COUNTS
    ids = [x["identifier"] for x in corpus]
    assert len(set(ids)) == 400
    for x in corpus:
        assert gr.ID_RE.findall(x["content"]) == [x["identifier"]]
        assert x["user_id"] == "BIZ_0001"
    pairs = {}
    for x in corpus:
        if x["pair_id"]:
            pairs.setdefault(x["pair_id"], []).append(x["identifier"])
    assert len(pairs) == 30 and all(gr.osa_distance(*v) == 1 for v in pairs.values())
    assert {"TKT-48213", "TKT-48231"} in [set(v) for v in pairs.values()]
    assert {"PLN-UNL-50", "PLN-UNL-5"} in [set(v) for v in pairs.values()]


def test_c1_queries_rederived_from_the_files():
    corpus = {x["memory_id"]: x for x in _jsonl("c1_corpus.jsonl")}
    queries = _jsonl("c1_queries.jsonl")
    assert [sum(1 for q in queries if q["qclass"] == c) for c in "ABC"] == [40, 40, 40]
    assert len({q["gold_memory_id"] for q in queries}) == 120
    for q in queries:
        gold = corpus[q["gold_memory_id"]]
        if q["qclass"] in "AC":
            # the identifier in the question occurs in exactly one memory, the gold
            ident = gr.ID_RE.findall(q["text"])
            assert ident == [gold["identifier"]]
            holders = [x for x in corpus.values() if gr.ID_RE.findall(x["content"]) == ident]
            assert [h["memory_id"] for h in holders] == [gold["memory_id"]]
        else:
            assert not gr.ID_SHAPE_RE.search(q["text"]) and not gr.MONTH_WORD_RE.search(q["text"])
            assert len(gr.overlap(q["text"], gold["content"])) <= 1
            assert q["overlap_terms"] == gr.overlap(q["text"], gold["content"])
    for c in "AC":
        assert sum(q["near_collision"] for q in queries if q["qclass"] == c) == 15


def test_c3_gold_is_topic_by_month():
    corpus = _jsonl("c1_corpus.jsonl")
    c3 = _jsonl("c3_queries.jsonl")
    assert len(c3) == 40
    for q in c3:
        gold = sorted(x["memory_id"] for x in corpus if x["topic"] == q["topic"] and x["month"] == q["month"])
        assert gold == q["gold_memory_ids"] and 2 <= len(gold) <= 5


def test_retrieval_validator_rejects_broken_sets(c1):
    corpus, queries, c3 = (copy.deepcopy(x) for x in c1)
    bad = copy.deepcopy(corpus)
    bad[1]["content"] = bad[1]["content"].replace(bad[1]["identifier"], bad[0]["identifier"])
    bad[1]["identifier"] = bad[0]["identifier"]
    with pytest.raises(ValueError):
        gr.validate(bad, queries, c3)
    badq = copy.deepcopy(queries)
    b = next(q for q in badq if q["qclass"] == "B")
    b["text"] = b["text"] + " " + " ".join(sorted(gr.content_stems(corpus[0]["content"])))
    with pytest.raises(ValueError):
        gr.validate(corpus, badq, c3)
    badc3 = copy.deepcopy(c3)
    badc3[0]["gold_memory_ids"] = badc3[0]["gold_memory_ids"][:1]
    with pytest.raises(ValueError):
        gr.validate(corpus, queries, badc3)


def test_overlap_rule_is_strict_about_word_forms():
    assert gr.overlap("charged twice", "a charge was made") == ["charg"]
    assert gr.overlap("Which of the bills?", "the bill") == ["bill"]
    assert gr.overlap("the and of", "the and of") == []


# ------------------------------------------------------------------------------------------------ C2, from the files
def test_c2_counts_and_types():
    threads, links, dxs = _jsonl("c2_threads.jsonl"), _jsonl("c2_links.jsonl"), _jsonl("c2_distractors.jsonl")
    assert [t["user_id"] for t in threads] == [f"CUST_{2001 + i}" for i in range(12)]
    assert sum(len(t["messages"]) for t in threads) == 120
    assert {k: sum(1 for x in links if x["relation_type"] == k) for k in gc.TYPE_COUNTS} == gc.TYPE_COUNTS
    assert len(dxs) == 9


def test_c2_every_slot_is_stated_in_one_exchange_and_maps_to_itself():
    threads = {t["thread_id"]: t for t in _jsonl("c2_threads.jsonl")}
    slots = _jsonl("c2_slots.jsonl")
    links = _jsonl("c2_links.jsonl")
    for tid, t in threads.items():
        ts = [x for x in slots if x["thread_id"] == tid]
        objs = [m.Slot(x["slot_id"], tuple(tuple(g) for g in x["key_terms"]), x["end_status"]) for x in ts]
        for x, o in zip(ts, objs):
            stated_in = [e for e in range(1, 6)
                         if o.matches(m.normalize(" ".join(mm["content"] for mm in t["messages"] if mm["exchange"] == e)))]
            assert stated_in == [x["exchange"]], x["slot_id"]
            assert m.map_memory(x["statement"], objs).slot_id == x["slot_id"]
        gold = [m.GoldLink(lk["source_slot"], lk["target_slot"], lk["relation_type"])
                for lk in links if lk["thread_id"] == tid]
        assert m.end_statuses([x["slot_id"] for x in ts], gold) == {x["slot_id"]: x["end_status"] for x in ts}


def test_conversation_validator_rejects_a_split_fact(c2):
    threads, slots, links, dxs = (copy.deepcopy(x) for x in c2)
    t = threads[0]
    ts = [x for x in slots if x["thread_id"] == t["thread_id"]]
    victim = ts[0]
    for msg in t["messages"]:
        if msg["exchange"] == victim["exchange"] and msg["role"] == "user":
            msg["content"] = msg["content"].replace(victim["statement"], "")
    with pytest.raises(ValueError):
        gc.validate_thread(t, ts, [x for x in links if x["thread_id"] == t["thread_id"]],
                           [x for x in dxs if x["thread_id"] == t["thread_id"]])


def test_conversation_validator_rejects_a_wrong_end_status(c2):
    threads, slots, links, dxs = (copy.deepcopy(x) for x in c2)
    t = threads[1]
    ts = [x for x in slots if x["thread_id"] == t["thread_id"]]
    target = next(x for x in ts if x["end_status"] == "replaced")
    target["end_status"] = "current"
    with pytest.raises(ValueError):
        gc.validate_thread(t, ts, [x for x in links if x["thread_id"] == t["thread_id"]],
                           [x for x in dxs if x["thread_id"] == t["thread_id"]])


def test_conversation_validator_rejects_a_future_dated_event(c2):
    threads, slots, links, dxs = (copy.deepcopy(x) for x in c2)
    victim = next(x for x in slots if "June" in x["statement"] and x["side"] == "source")
    t = next(x for x in threads if x["thread_id"] == victim["thread_id"])
    new = victim["statement"].replace("June", "September")
    for msg in t["messages"]:
        msg["content"] = msg["content"].replace(victim["statement"], new)
    victim["statement"] = new
    with pytest.raises(ValueError):
        gc.validate_thread(t, [x for x in slots if x["thread_id"] == t["thread_id"]],
                           [x for x in links if x["thread_id"] == t["thread_id"]],
                           [x for x in dxs if x["thread_id"] == t["thread_id"]])


# ------------------------------------------------------------------------------------------------------- hero, images
def test_hero_seeds_follow_plan_section_3():
    hs.validate()
    assert {k for k, v in hs.expected_statuses().items() if v == "invalid"} == {"M1", "M2", "M3", "M7"}
    assert [x.key for x in hs.memories_in_time_order()][:2] == ["M1", "M7"]
    assert hs.by_key()["M10"].content.endswith("PLN-FAM-4.")


def test_hero_validator_rejects_a_reverse_link(monkeypatch):
    monkeypatch.setattr(hs, "LINKS", hs.LINKS + (hs.HeroLink("M1", "M2", "supports"),))
    with pytest.raises(ValueError):
        hs.validate()


def test_hero_validator_rejects_a_type_mismatch(monkeypatch):
    monkeypatch.setattr(hs, "LINKS", hs.LINKS[:-1] + (hs.HeroLink("M10", "M3", "supersedes"),))
    with pytest.raises(ValueError):
        hs.validate()


def test_images_manifest_and_png_chunks():
    data = json.loads((FROZEN / "bill_images.json").read_text(encoding="ascii"))
    assert len(data["images"]) == 4 and len(data["questions"]) == 4
    ids = {i["image_id"] for i in data["images"]}
    assert all(q["gold_image_id"] in ids for q in data["questions"])
    for rel in [i["file"] for i in data["images"]] + [data["vision_check"]["file"]]:
        blob = (FROZEN / rel).read_bytes()
        assert blob[:8] == b"\x89PNG\r\n\x1a\n"
        chunks, i = [], 8
        while i < len(blob):
            n = int.from_bytes(blob[i:i + 4], "big")
            chunks.append(blob[i + 4:i + 8])
            i += 12 + n
        assert set(chunks) <= {b"IHDR", b"IDAT", b"IEND"}, rel      # no text or metadata chunks


def test_images_validator_rejects_a_dangling_question(monkeypatch):
    monkeypatch.setattr(mbi, "QUESTIONS", mbi.QUESTIONS + ({"qid": "X", "text": "?", "gold_image_id": "nope"},))
    with pytest.raises(ValueError):
        mbi.build()
