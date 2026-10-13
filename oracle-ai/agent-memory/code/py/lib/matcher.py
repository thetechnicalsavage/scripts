# v1.0 2026-10-10  Brief 12 C2 matcher (PLAN.md 7.2, frozen with the sets in 7.0): maps an extracted memory to a gold
#                  fact slot by key terms and scores predicted links against the gold links of one thread.
#                  v1.0: first version (day 0, no database).
"""Frozen C2 matching rule.

A memory maps to a slot when its final stored text contains every key term of that slot after normalisation
(case, whitespace, units, dates). A key term is a list of variants; it is present when any variant occurs on token
boundaries (no letter or digit directly before or after it), so "5 gb" is not found inside "15 gb". A memory that maps
to two or more slots of its thread is ambiguous and unmatched. A predicted link is correct when its source maps to the
gold source slot, its target to the gold target slot and its relation type equals the gold type (direction matters);
each gold link is credited at most once, every other predicted link is a false positive.

Pure Python, no database, no network. Used by step 5 (py/50_autolink_bakeoff.py) and by data/gen_conversations.py for
its self-checks.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

RELATION_TYPES = ("supersedes", "refines", "supports", "contradicts", "duplicates")
# The SDK invalidates the target of these three (API_NOTES B4); a gold slot that is the target of one of them is
# "replaced" at the end of its thread, every other slot is "current" (PLAN 7.2, v0.3 precision).
INVALIDATING_TYPES = frozenset({"supersedes", "refines", "duplicates"})

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_MONTH_ALT = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?" \
             r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
_ORD = r"(?:st|nd|rd|th)?"

_RE_DASHES = re.compile(r"[‐‑‒–—−]")
_RE_QUOTES = re.compile(r"[‘’‚‛]")
_RE_DQUOTES = re.compile(r"[“”„‟]")
# 2026-09-03 (ISO)
_RE_ISO_DATE = re.compile(r"(?<![0-9])(\d{4})-(\d{1,2})-(\d{1,2})(?![0-9])")
# 3 September 2026, 3rd of Sep, 03-Sep, 3 sept. 2026
_RE_DAY_MONTH = re.compile(r"(?<![a-z0-9])(\d{1,2})" + _ORD + r"(?:\s+of)?(?:\s+|-)(" + _MONTH_ALT +
                           r")\.?(?:,?\s+\d{4})?(?![a-z])")
# September 3, Sep 3rd 2026, sept. 3
_RE_MONTH_DAY = re.compile(r"(?<![a-z0-9])(" + _MONTH_ALT + r")\.?\s+(\d{1,2})" + _ORD +
                           r"(?:,?\s+\d{4})?(?![a-z0-9])")
_RE_MONTH_WORD = re.compile(r"(?<![a-z0-9])(" + _MONTH_ALT + r")\.?(?![a-z0-9])")
_RE_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_RE_TRAILING_ZERO_DECIMALS = re.compile(r"(?<![0-9.])(\d+)\.0+(?![0-9])")
_RE_TRAILING_ZEROS = re.compile(r"(?<![0-9.])(\d+\.\d*?[1-9])0+(?![0-9])")
# 20GB, 20 GB, 20 gigabytes, 112dBm, 80%, 80 per cent
_RE_UNITS = re.compile(r"(?<![a-z0-9_.])(\d+(?:\.\d+)?)\s*(gigabytes?|gb|megabytes?|mb|tb|gbps|mbps|dbm)(?![a-z0-9])")
_RE_PERCENT = re.compile(r"(?<![a-z0-9_.])(\d+(?:\.\d+)?)\s*(?:%|percent|per\s+cent)(?![a-z0-9])")
# QAR 45, 45 QAR, QAR45, 45 riyals, QR 45
_RE_CURRENCY_BEFORE = re.compile(r"(?<![a-z0-9])(?:qar|qr)\s*(\d+(?:\.\d+)?)(?![0-9])")
_RE_CURRENCY_AFTER = re.compile(r"(?<![a-z0-9_.])(\d+(?:\.\d+)?)\s*(?:qar|qr|riyals?)(?![a-z0-9])")
_RE_SPACES = re.compile(r"\s+")
_UNIT_CANON = {"gigabyte": "gb", "gigabytes": "gb", "megabyte": "mb", "megabytes": "mb"}


def _month_name(token: str) -> str:
    """Full lower-case month name for a month word or abbreviation."""
    token = token.rstrip(".")
    for name in _MONTHS:
        if name.startswith(token[:3]):
            return name
    raise ValueError(f"not a month: {token!r}")


def normalize(text: str) -> str:
    """Case, whitespace, unit and date normalisation (PLAN 7.2). Deterministic and locale-free.

    Dates with a day become "<month> <day>" and the year is dropped ("3 September 2026", "2026-09-03", "03-Sep" and
    "Sep 3rd" all become "september 3"); a month word alone becomes its full name ("Sept" -> "september"). Numbers
    lose thousands separators and trailing decimal zeros ("120.00" -> "120", "1.40" -> "1.4"). Units: "20GB", "20 gigabytes" -> "20 gb"; "80%" -> "80 percent";
    "QAR45", "45 riyals" -> "qar 45". Numeric dates with slashes are left as they are (dd/mm and mm/dd are ambiguous).
    """
    if not isinstance(text, str):
        raise TypeError("normalize() needs a str")
    s = unicodedata.normalize("NFKC", text).casefold()
    s = _RE_DASHES.sub("-", s)
    s = _RE_QUOTES.sub("'", s)
    s = _RE_DQUOTES.sub('"', s)

    def iso(m: re.Match) -> str:
        month, day = int(m.group(2)), int(m.group(3))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return m.group(0)
        return f"{_MONTHS[month - 1]} {day}"

    s = _RE_ISO_DATE.sub(iso, s)
    s = _RE_DAY_MONTH.sub(lambda m: f"{_month_name(m.group(2))} {int(m.group(1))}", s)
    s = _RE_MONTH_DAY.sub(lambda m: f"{_month_name(m.group(1))} {int(m.group(2))}", s)
    s = _RE_MONTH_WORD.sub(lambda m: _month_name(m.group(1)), s)
    s = _RE_THOUSANDS.sub("", s)
    s = _RE_TRAILING_ZERO_DECIMALS.sub(r"\1", s)
    s = _RE_TRAILING_ZEROS.sub(r"\1", s)
    s = _RE_UNITS.sub(lambda m: f"{m.group(1)} {_UNIT_CANON.get(m.group(2), m.group(2))}", s)
    s = _RE_PERCENT.sub(lambda m: f"{m.group(1)} percent", s)
    s = _RE_CURRENCY_BEFORE.sub(lambda m: f"qar {m.group(1)}", s)
    s = _RE_CURRENCY_AFTER.sub(lambda m: f"qar {m.group(1)}", s)
    return _RE_SPACES.sub(" ", s).strip()


def term_present(term: str, normalized_text: str) -> bool:
    """True when the normalised term occurs in already-normalised text on token boundaries."""
    t = normalize(term)
    if not t:
        raise ValueError("empty key term")
    return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", normalized_text) is not None


@dataclass(frozen=True)
class Slot:
    """One gold fact slot of a thread. key_terms: every entry must be present; an entry lists its variants."""
    slot_id: str
    key_terms: tuple[tuple[str, ...], ...]
    end_status: str = "current"

    def __post_init__(self) -> None:
        if not self.key_terms:
            raise ValueError(f"slot {self.slot_id}: no key terms")
        for variants in self.key_terms:
            if not variants or any(not normalize(v) for v in variants):
                raise ValueError(f"slot {self.slot_id}: empty key term")
        if self.end_status not in ("current", "replaced"):
            raise ValueError(f"slot {self.slot_id}: end_status must be current or replaced")

    def matches(self, normalized_text: str) -> bool:
        return all(any(term_present(v, normalized_text) for v in variants) for variants in self.key_terms)


@dataclass(frozen=True)
class MatchResult:
    slot_id: str | None
    outcome: str                      # "matched", "unmatched" or "ambiguous"
    candidates: tuple[str, ...] = ()


def map_memory(text: str, slots: Sequence[Slot]) -> MatchResult:
    """Map one memory text to at most one slot of its own thread."""
    norm = normalize(text)
    hits = tuple(s.slot_id for s in slots if s.matches(norm))
    if len(hits) == 1:
        return MatchResult(hits[0], "matched", hits)
    if not hits:
        return MatchResult(None, "unmatched", ())
    return MatchResult(None, "ambiguous", hits)


@dataclass(frozen=True)
class GoldLink:
    source_slot: str
    target_slot: str
    relation_type: str

    def __post_init__(self) -> None:
        if self.relation_type not in RELATION_TYPES:
            raise ValueError(f"unknown relation type {self.relation_type!r}")
        if self.source_slot == self.target_slot:
            raise ValueError("a gold link cannot be a self-link")


@dataclass(frozen=True)
class PredictedLink:
    source_memory_id: str
    target_memory_id: str
    relation_type: str

    def __post_init__(self) -> None:
        if self.relation_type not in RELATION_TYPES:
            raise ValueError(f"unknown relation type {self.relation_type!r}")


@dataclass
class ThreadScore:
    """Counts for one thread of one run. tp + fn == number of gold links; tp + fp == number of predicted links."""
    tp: int = 0
    fp: int = 0
    fn: int = 0
    harmful_invalidations: int = 0          # invalidating link whose target maps to a slot the gold marks current
    invalidations_unmapped_target: int = 0  # invalidating link whose target maps to no slot (reported, not harmful)
    distractor_links: int = 0               # any link, either direction, between the two slots of a distractor pair
    pair_hits: int = 0                      # gold links whose slot pair has any predicted link (type and direction ignored)
    gold_links: int = 0
    predicted_links: int = 0
    slots_covered: int = 0
    slots_total: int = 0
    memories_matched: int = 0
    memories_unmatched: int = 0
    memories_ambiguous: int = 0
    credited: list[GoldLink] = field(default_factory=list)


def score_thread(memories: Mapping[str, str], predicted: Iterable[PredictedLink], slots: Sequence[Slot],
                 gold: Sequence[GoldLink], distractor_pairs: Sequence[tuple[str, str]]) -> ThreadScore:
    """Score one thread of one run.

    memories: memory_id -> final stored text, for every memory of the thread's user in the store.
    predicted: the store's MEMORY_LINK rows for that user.
    """
    slot_ids = {s.slot_id for s in slots}
    if len(slot_ids) != len(slots):
        raise ValueError("duplicate slot ids")
    for g in gold:
        if g.source_slot not in slot_ids or g.target_slot not in slot_ids:
            raise ValueError(f"gold link {g} names an unknown slot")
    for a, b in distractor_pairs:
        if a not in slot_ids or b not in slot_ids or a == b:
            raise ValueError(f"bad distractor pair {(a, b)}")
    status = {s.slot_id: s.end_status for s in slots}

    sc = ThreadScore(gold_links=len(gold), slots_total=len(slots))
    mem_slot: dict[str, str | None] = {}
    covered: set[str] = set()
    for mid, text in memories.items():
        r = map_memory(text, slots)
        mem_slot[mid] = r.slot_id
        if r.outcome == "matched":
            sc.memories_matched += 1
            covered.add(r.slot_id)  # type: ignore[arg-type]
        elif r.outcome == "ambiguous":
            sc.memories_ambiguous += 1
        else:
            sc.memories_unmatched += 1
    sc.slots_covered = len(covered)

    gold_set = {(g.source_slot, g.target_slot, g.relation_type): g for g in gold}
    distractors = {frozenset(p) for p in distractor_pairs}
    credited: set[tuple[str, str, str]] = set()
    linked_pairs: set[frozenset] = set()
    for link in predicted:
        sc.predicted_links += 1
        s = mem_slot.get(link.source_memory_id)
        t = mem_slot.get(link.target_memory_id)
        key = (s, t, link.relation_type)
        if s is not None and t is not None and key in gold_set and key not in credited:
            credited.add(key)  # type: ignore[arg-type]
            sc.tp += 1
            sc.credited.append(gold_set[key])  # type: ignore[index]
        else:
            sc.fp += 1
        if link.relation_type in INVALIDATING_TYPES:
            if t is None:
                sc.invalidations_unmapped_target += 1
            elif status[t] == "current":
                sc.harmful_invalidations += 1
        if s is not None and t is not None and s != t:
            pair = frozenset((s, t))
            linked_pairs.add(pair)
            if pair in distractors:
                sc.distractor_links += 1
    sc.fn = len(gold) - sc.tp
    sc.pair_hits = sum(1 for g in gold if frozenset((g.source_slot, g.target_slot)) in linked_pairs)
    return sc


def end_statuses(slot_ids: Iterable[str], gold: Sequence[GoldLink]) -> dict[str, str]:
    """Gold end-of-thread status: 'replaced' for the target of a gold supersedes, refines or duplicates link."""
    replaced = {g.target_slot for g in gold if g.relation_type in INVALIDATING_TYPES}
    return {sid: ("replaced" if sid in replaced else "current") for sid in slot_ids}


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """Precision, recall, F1 with 0 for an empty denominator (PLAN 7.2)."""
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
    return p, r, f
