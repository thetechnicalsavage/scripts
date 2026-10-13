# v1.0 2026-10-10  Brief 12 hero seeds (PLAN.md section 3): customer CUST_1001, memories M1-M10 and their six links,
#                  shared by step 1 (store DEMO) and step 2 (DEMO, DEMO_AUTO); frozen with the sets (PLAN 7.0).
#                  v1.0: first version (day 0, no database). Pure data: importing it needs no SDK and no database.
"""Hero customer CUST_1001: ten memories over five weeks, typed so that every link endpoint matches the stored type
(API_NOTES B17), six links covering all five types with one link per endpoint pair (the SDK refuses identical and
reverse links, B22). Event times are passed to add_memory as timestamp= (B28), in UTC.

Expected from the SDK's code (B4, B21), checked in step 1: after seeding, M1, M2, M3 and M7 are invalid (targets of a
supersedes, refines or duplicates link) and the other six are valid. Every name, code and ticket is fictional.
"""
from __future__ import annotations

from dataclasses import dataclass

USER_ID = "CUST_1001"
STORE_ID = "DEMO"
STORE_ID_AUTO = "DEMO_AUTO"

# Step 1 questions (PLAN section 6, step 1).
Q_PLAN_NOW = "Which plan does the customer want now?"
Q_EVER_UNLIMITED = "Did the customer ever want unlimited data?"

INVALIDATING = frozenset({"supersedes", "refines", "duplicates"})


@dataclass(frozen=True)
class HeroMemory:
    key: str            # M1..M10, as in PLAN section 3
    memory_id: str      # record id passed to add_memory(memory_id=...)
    memory_type: str    # "fact" or "preference"
    timestamp: str      # event time, ISO 8601 with an explicit UTC offset
    content: str


@dataclass(frozen=True)
class HeroLink:
    source: str         # newer memory (key)
    target: str         # older memory (key)
    relation_type: str


MEMORIES: tuple[HeroMemory, ...] = (
    HeroMemory("M1", "cust1001_m01", "preference", "2026-08-31T09:05:00+00:00",
               "The customer wants the unlimited plan PLN-UNL-50."),
    HeroMemory("M2", "cust1001_m02", "preference", "2026-09-08T10:12:00+00:00",
               "The customer now needs only 20 GB of data a month, but with GCC roaming."),
    HeroMemory("M3", "cust1001_m03", "fact", "2026-09-16T08:40:00+00:00",
               "The roaming add-on RMG-GCC-7D was charged twice on the customer's September bill; ticket TKT-48213 "
               "was opened."),
    HeroMemory("M4", "cust1001_m04", "fact", "2026-09-23T13:30:00+00:00",
               "Billing confirmed a refund of one RMG-GCC-7D charge for ticket TKT-48213."),
    HeroMemory("M5", "cust1001_m05", "fact", "2026-09-16T08:46:00+00:00",
               "The customer says she never activated the roaming add-on RMG-GCC-7D."),
    HeroMemory("M6", "cust1001_m06", "fact", "2026-09-18T11:02:00+00:00",
               "The activation log shows that RMG-GCC-7D was activated by SMS from the customer's line on 03-Sep-2026."),
    HeroMemory("M7", "cust1001_m07", "preference", "2026-08-31T09:07:00+00:00",
               "The customer prefers WhatsApp messages rather than phone calls."),
    HeroMemory("M8", "cust1001_m08", "preference", "2026-09-21T16:20:00+00:00",
               "The customer wants contact by WhatsApp only, never phone calls, and only after 18:00."),
    HeroMemory("M9", "cust1001_m09", "fact", "2026-09-20T19:15:00+00:00",
               "The customer reported the same double charge for RMG-GCC-7D again through the app."),
    HeroMemory("M10", "cust1001_m10", "preference", "2026-10-02T10:00:00+00:00",
               "The customer is moving to the family plan PLN-FAM-4."),
)

LINKS: tuple[HeroLink, ...] = (
    HeroLink("M2", "M1", "supersedes"),
    HeroLink("M4", "M3", "supports"),
    HeroLink("M6", "M5", "contradicts"),
    HeroLink("M8", "M7", "refines"),
    HeroLink("M9", "M3", "duplicates"),
    HeroLink("M10", "M2", "supersedes"),
)

EXPECTED_INVALID = frozenset({"M1", "M2", "M3", "M7"})

# The synthetic bill screenshot that belongs to ticket TKT-48213 (data/make_bill_images.py).
TICKET_IMAGE = {"TKT-48213": "bill_tkt48213.png"}


def by_key() -> dict[str, HeroMemory]:
    return {m.key: m for m in MEMORIES}


def memories_in_time_order() -> list[HeroMemory]:
    """The order step 2 adds them to DEMO_AUTO (timestamp, then key for equal times)."""
    return sorted(MEMORIES, key=lambda m: (m.timestamp, int(m.key[1:])))


def expected_statuses() -> dict[str, str]:
    """Status each memory should have after seeding, from the SDK rule (B4): the target of a supersedes, refines or
    duplicates link is invalid, every other memory is valid."""
    invalid = {lk.target for lk in LINKS if lk.relation_type in INVALIDATING}
    return {m.key: ("invalid" if m.key in invalid else "valid") for m in MEMORIES}


def validate() -> None:
    """Raise ValueError if the seeds break a rule of PLAN section 3 or the SDK's link rules."""
    mems = by_key()
    if len(MEMORIES) != 10 or set(mems) != {f"M{i}" for i in range(1, 11)}:
        raise ValueError("hero needs exactly M1..M10")
    if len({m.memory_id for m in MEMORIES}) != 10:
        raise ValueError("memory ids must be unique")
    if {lk.relation_type for lk in LINKS} != {"supersedes", "refines", "supports", "contradicts", "duplicates"}:
        raise ValueError("the six links must cover all five relation types")
    pairs = set()
    for lk in LINKS:
        if lk.source not in mems or lk.target not in mems or lk.source == lk.target:
            raise ValueError(f"bad link {lk}")
        pair = frozenset((lk.source, lk.target))
        if pair in pairs:
            raise ValueError(f"more than one link on the endpoint pair {sorted(pair)} (B22)")
        pairs.add(pair)
        if mems[lk.source].memory_type != mems[lk.target].memory_type:
            raise ValueError(f"link {lk} joins different memory types (B17)")
        if mems[lk.source].timestamp <= mems[lk.target].timestamp:
            raise ValueError(f"link {lk}: the source must be newer than the target")
    if frozenset(k for k, s in expected_statuses().items() if s == "invalid") != EXPECTED_INVALID:
        raise ValueError("expected statuses disagree with PLAN section 3 (M1, M2, M3, M7 invalid)")
    if len(set(m.timestamp[:10] for m in MEMORIES)) < 2:
        raise ValueError("timestamps must span the five weeks")
