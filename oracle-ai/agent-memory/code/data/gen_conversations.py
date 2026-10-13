#!/usr/bin/env python3
# v1.3 2026-10-10  Brief 12 C2 conversation set (PLAN.md section 3 and 7.2): 12 customers CUST_2001-CUST_2012, one
#                  thread of 10 messages each, 36 planted evolution events (12 supersedes, 8 refines, 6 supports,
#                  5 contradicts, 5 duplicates), 9 distractor pairs, gold slots with key terms and end-of-thread status.
#                  Seeded and deterministic; frozen in 7.0.
#                  v1.3: reviewer pass (PLAN 7.0, D11): no label disagreement; key terms a faithful extraction would
#                  likely reword got frozen variants (times 8:00/2:00/4:00, another/other/different phone, never/did
#                  not, twice/two times, faulty/defective/damaged cable, short/brief trip, signed for/delivered,
#                  school, memorable/easy/vanity number); labels, slots and statuses unchanged. Codex round 7 removed
#                  "double" and "denies"/"denied" (a refund or contradicting source can use them: new ambiguity).
#                  v1.2: blind Codex re-review: a refines source must imply its target (the SDK invalidates the target,
#                  so a lossy refinement would hide a fact): RF_ALERTS replaced by RF_INSTALL (morning -> 8 am Saturday),
#                  RF_BILLFMT anchors on "paper" (PDF by e-mail implies not on paper), RF_TRIP on "short trip" (a 10-day
#                  pass implies it), RF_SIMDELIV is weekday -> Tuesday (no relative date), RF_INTL's source names the new plan,
#                  RF_CHANNEL's source says "only on WhatsApp"; CT_USAGE names the month on both sides (round 4: "last
#                  month" fell in different months for the two exchanges). Round 5 (timeline read against the message
#                  timestamps): every dated statement uses a complete month or a date before every thread (bill months
#                  April-June, activation dates in May-June, the courier case on 1 and 4 June); the router delivery
#                  moves from morning to evening instead of between weekdays.
#                  v1.1: blind Codex review of the set (day 0, 5 flags, all accepted): RF_ALERTS keeps the channel and
#                  only adds a threshold; CT_ACTIV and SP_REFUND sources name the roaming pack; SU_DEVICE source names
#                  what is taken; CT_PRICE compares the same monthly fee; RF_INTL anchors on "international calls".
#                  v1.0: first version (day 0, no database, no LLM).
"""Generate data/frozen/c2_threads.jsonl, c2_slots.jsonl, c2_links.jsonl and c2_distractors.jsonl.

Run as : the authoring host or the demo VM; no database, no network.
Usage  : python3 data/gen_conversations.py [--out DIR] [--check]
Re-run : safe; the same seed writes byte-identical files (atomic replace).

Rules the generator enforces (and tests/test_generators.py re-checks):
- Five exchanges per thread (user message, then a short assistant acknowledgement that states no fact). Every slot is
  stated in full inside one user message (PLAN 7.2's data rule for the default extraction window of -1): all its key
  terms are in that exchange, and no other exchange of the thread holds all of them.
- Each event's target is stated in an earlier exchange than its source; a source never repeats its target's
  distinctive key term, so the two map to different slots.
- A refines source states a more specific version that implies its target (lossless, as the SDK's link prompt asks),
  so the target's invalidation hides no fact: afternoon -> 2 pm to 4 pm, a weekday -> Tuesday, short trip -> a
  10-day pass, morning -> 8 am, a new plan with international calls -> a new plan with 300 minutes to a named
  country, not on paper -> PDF by e-mail,
  WhatsApp rather than e-mail -> only WhatsApp, only on weekdays.
- With the frozen matcher (py/lib/matcher.py), every slot statement maps to exactly its own slot of the thread.
- Within a thread every scenario comes from a different topic family, so cross-event links have no planted meaning.
- No dated statement can fall after its own exchange: months and dates are all before 1 July 2026, the first thread day.
- End-of-thread status: 'replaced' for the target of a supersedes, refines or duplicates link, else 'current'.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import random
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "py"))
from lib import matcher  # noqa: E402  (path set above; matcher is frozen with this generator)

LOG = logging.getLogger("gen_conversations")

SEED = 20261005
OUT_DEFAULT = HERE / "frozen"
FILES = ("c2_threads.jsonl", "c2_slots.jsonl", "c2_links.jsonl", "c2_distractors.jsonl")
N_THREADS = 12
TYPE_COUNTS = {"supersedes": 12, "refines": 8, "supports": 6, "contradicts": 5, "duplicates": 5}
N_DISTRACTORS = 9
START = dt.datetime(2026, 7, 1, 9, 0, tzinfo=dt.timezone.utc)
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_DAY_DATE = re.compile(r"\b(" + "|".join(_MONTHS) + r") (\d{1,2})\b")

ACKS = ("Thanks, I have noted that on your account.", "Understood, that is recorded.",
        "Noted. Is there anything else I can help with today?", "Thank you. I have added this to your notes.",
        "Got it, I have updated your record.")

# Parameter pools. A value is used at most once per thread.
P = {
    "pu": ("PLN-UNL-60", "PLN-UNL-75", "PLN-MAX-90"),
    "ps": ("PLN-DAT-20", "PLN-DAT-15", "PLN-LTE-10"),
    "branch": ("Harbour Road", "Pearl Square", "North Gate", "Airport Road", "Corniche", "Old Market"),
    "device": ("Zento X2", "Kairo S5", "Brisk 7", "Mirel A4", "Orin 12", "Talo Mini"),
    "lines": (("3", "three", "5", "five"), ("2", "two", "4", "four"), ("4", "four", "6", "six")),
    "cap": (("5", "15"), ("4", "12"), ("6", "20")),
    "gcc": ("RMG-GCC-10D", "RMG-GCC-3D", "RMG-GCC-5D"),
    "wld": ("RMG-WLD-14D", "RMG-WLD-30D", "RMG-WLD-21D"),
    "ordinal": ((("5th", "fifth"), ("20th", "twentieth")), (("1st", "first"), ("15th", "fifteenth")),
                (("10th", "tenth"), ("25th", "twenty-fifth"))),
    "country": ("Oman", "Jordan", "Kuwait", "Bahrain", "Egypt", "India", "Lebanon", "Tunisia"),
    "billmonth": ("April", "May", "June"),          # complete months before every thread (threads start 1 July 2026)
    "amount": ("45", "60", "38", "72"),
    "years": (("9", "2017"), ("7", "2019"), ("11", "2015")),
    "dbm": ("112", "108", "115"),
    "lowuse": (("2", "1.4"), ("3", "2.6"), ("2", "1.7")),
    "actdate": (("3 June", "june 3"), ("14 May", "may 14"), ("22 June", "june 22")),     # before every thread
    "months2": (("March", "June"), ("April", "October"), ("May", "November")),
    "usage": (("5", "18"), ("4", "16"), ("6", "21")),
    "price": (("250", "310"), ("199", "265"), ("280", "345")),
}


@dataclass(frozen=True)
class Scenario:
    sid: str
    etype: str            # relation type, or "distractor"
    family: str
    t_text: str           # target statement (distractor: side A)
    t_keys: tuple         # tuple of variant tuples, may hold {placeholders}
    s_text: str           # source statement (distractor: side B)
    s_keys: tuple
    params: tuple = ()    # names of P pools this scenario draws from


def K(*groups) -> tuple:
    return tuple(tuple(g) if isinstance(g, (list, tuple)) else (g,) for g in groups)


SCENARIOS = (
    # supersedes: the target is replaced by the source
    Scenario("SU_PLAN", "supersedes", "plan", "I'd like my line on the unlimited plan {pu}.", K("{pu}"),
             "Change of plan: from next month put me on the smaller plan {ps} instead.", K("{ps}"), ("pu", "ps")),
    Scenario("SU_PICKUP", "supersedes", "sim", "I will collect my replacement SIM from the {b1} branch.", K("{b1}"),
             "Please change my SIM pickup to the {b2} branch instead.", K("{b2}"), ("branch",)),
    Scenario("SU_LANG", "supersedes", "bill", "Please send my bills in Arabic.", K("arabic"),
             "From now on I want my bills in English.", K("english")),
    Scenario("SU_PAY", "supersedes", "payment", "I pay my bill by direct debit from my bank.", K("direct debit"),
             "From next month I will pay my bill by credit card.", K("credit card")),
    Scenario("SU_DEVICE", "supersedes", "device", "I'll take the {d1} phone on the 24-month instalment.", K("{d1}"),
             "I've decided to take the {d2} phone on the 24-month instalment instead.", K("{d2}"), ("device",)),
    Scenario("SU_LINES", "supersedes", "family_plan", "We need {n1} lines on our family plan.",
             K(("{n1} lines", "{w1} lines")), "We now need {n2} lines on the family plan.",
             K(("{n2} lines", "{w2} lines")), ("lines",)),
    Scenario("SU_CAP", "supersedes", "data", "Cap my son's line at {g1} GB a month.", K("{g1} gb"),
             "Raise my son's monthly data cap to {g2} GB.", K("{g2} gb"), ("cap",)),
    Scenario("SU_DELIV", "supersedes", "home", "Please deliver the new router in the morning.", K("morning"),
             "Please move the router delivery to the evening.", K("evening")),
    Scenario("SU_RPACK", "supersedes", "roaming", "For my trip I want the GCC roaming pack {r1}.", K("{r1}"),
             "For my trip I now want the worldwide roaming pack {r2} instead.", K("{r2}"), ("gcc", "wld")),
    Scenario("SU_BILLDAY", "supersedes", "bill", "Please bill me on the {o1} of each month.", K(("{o1}", "{ow1}")),
             "Please move my billing date to the {o2} of the month.", K(("{o2}", "{ow2}")), ("ordinal",)),
    # refines: the source is a more specific version of the target, which the SDK then invalidates
    Scenario("RF_CHANNEL", "refines", "contact", "Please contact me on WhatsApp rather than by email.",
             K("whatsapp", ("email", "e-mail")), "Contact me only on WhatsApp, and only on weekdays.",
             K("whatsapp", ("weekday", "weekdays"))),
    Scenario("RF_SIMDELIV", "refines", "sim", "Please deliver my new SIM on a weekday.", K("sim", ("weekday", "weekdays")),
             "Deliver the SIM on Tuesday morning, between 9 and 11.", K("sim", "tuesday")),
    Scenario("RF_TRIP", "refines", "roaming", "I need roaming for a short trip to {c1}.",
             K("{c1}", ("short trip", "brief trip", "short visit")),
             "For {c1}, I want the 10-day data-only roaming pass.", K("{c1}", ("10-day", "10 day", "ten-day", "ten day")),
             ("country",)),
    Scenario("RF_INSTALL", "refines", "home", "I want the fibre installation done in the morning.",
             K(("fibre", "fiber"), "morning"), "Please book the fibre installation for 8 am on Saturday.",
             K(("8 am", "8am", "08:00", "8:00"), "saturday")),
    Scenario("RF_CALLTIME", "refines", "contact", "Only call me in the afternoon.", K("afternoon"),
             "Call me only between 2 pm and 4 pm.",
             K(("2 pm", "2pm", "14:00", "2:00"), ("4 pm", "4pm", "16:00", "4:00"))),
    Scenario("RF_INTL", "refines", "intl", "My new plan must include international calls.",
             K(("international call", "international calls", "international calling")),
             "My new plan needs 300 minutes of calls to {c1} each month.",
             K(("300 minutes", "300 mins", "300 minute"), "{c1}"),
             ("country",)),
    Scenario("RF_BILLFMT", "refines", "bill", "I want my monthly bills in digital form, not on paper.", K("paper"),
             "Email me my monthly bills as itemised PDF files.", K("pdf")),
    # supports: the source backs the target; both stay current
    Scenario("SP_REFUND", "supports", "roaming", "My roaming pack {r1} was charged twice on my {bm} bill.",
             K("{r1}", ("twice", "two times")), "Billing confirmed a refund of QAR {amt} for the duplicate charge of roaming pack {r1}.",
             K("refund", "qar {amt}"), ("gcc", "billmonth", "amount")),
    Scenario("SP_INTERNET", "supports", "home", "My home internet drops every evening.",
             K("home internet", "evening"), "The engineer found a faulty cable in my building's distribution box.",
             K(("faulty cable", "defective cable", "damaged cable", "broken cable", "cable fault"))),
    Scenario("SP_LOYALTY", "supports", "loyalty",
             "I have been a customer for {yrs} years, so I qualify for the loyalty discount.", K("loyalty discount"),
             "My first contract with you was signed in {year}.", K("{year}"), ("years",)),
    Scenario("SP_COVERAGE", "supports", "coverage", "Indoor coverage at my office is very weak.", K("office", "weak"),
             "A signal test at my desk measured -{dbm} dBm.", K("{dbm} dbm"), ("dbm",)),
    Scenario("SP_LOWUSE", "supports", "data", "I barely use mobile data, under {g1} GB most months.", K("{g1} gb"),
             "My app shows only {u1} GB used last month.", K("{u1} gb"), ("lowuse",)),
    Scenario("SP_SIMFAULT", "supports", "sim", "My SIM keeps showing no service in my phone.", K("no service"),
             "The shop tested my SIM in another phone and it failed there too.",
             K(("another phone", "other phone", "different phone", "another handset", "second phone"))),
    # contradicts: the source conflicts with the target; both stay current
    Scenario("CT_ACTIV", "contradicts", "roaming", "I never activated the roaming pack {r1}.", K("{r1}", ("never", "did not", "didn't")),
             "The store says its log shows the roaming pack {r1} was activated by SMS from my line on {ad}.",
             K("sms", "{adk}"), ("gcc", "actdate")),
    Scenario("CT_CONTRACT", "contradicts", "contract", "My contract ends in {m1}.", K("contract", "{m1}"),
             "My contract letter says the end date is in {m2}.", K("letter", "{m2}"), ("months2",)),
    Scenario("CT_USAGE", "contradicts", "data", "I only used {g1} GB of data in {bm}.", K("{g1} gb"),
             "The app says I used {g2} GB of data in {bm}.", K("{g2} gb"), ("usage", "billmonth")),
    Scenario("CT_DELIVERY", "contradicts", "sim", "The courier never delivered the new SIM I ordered on 1 June.",
             K("courier", ("never", "did not", "didn't")), "The courier's tracking shows that SIM was signed for at my door on 4 June.",
             K(("signed for", "signed", "delivered"), "june 4")),
    Scenario("CT_PRICE", "contradicts", "price", "I was told the family plan's monthly fee is QAR {p1}.",
             K("qar {p1}"), "My first bill shows the family plan's monthly fee as QAR {p2}.", K("qar {p2}"),
             ("price",)),
    # duplicates: the same report again with a new reference; the SDK invalidates the first report
    Scenario("DU_CHARGE", "duplicates", "roaming", "I'm reporting a double charge for my roaming pack, ticket {t1}.",
             K("{t1}"), "Reporting it again in the app: I was billed twice for the roaming pack, ticket {t2}.",
             K("{t2}")),
    Scenario("DU_SIGNAL", "duplicates", "home", "There has been no signal at my home since this morning, reference {t1}.",
             K("{t1}"), "Logging it again: still no signal at my home, new reference {t2}.", K("{t2}")),
    Scenario("DU_ADDRESS", "duplicates", "bill", "Please update my billing address to the new villa, request {t1}.",
             K("{t1}"), "Second request: please update the billing address to the new villa, request {t2}.",
             K("{t2}")),
    Scenario("DU_ROUTER", "duplicates", "home", "My home router keeps restarting, ticket {t1}.", K("{t1}"),
             "Same problem again: my router keeps restarting, I opened ticket {t2} on the website.", K("{t2}")),
    Scenario("DU_SIMLATE", "duplicates", "sim", "My replacement SIM has not arrived, service request {t1}.", K("{t1}"),
             "Raising it again: my replacement SIM is still missing, service request {t2}.", K("{t2}")),
    # distractor pairs: same topic, independent facts, must not be linked
    Scenario("DX_ROAM", "distractor", "roaming", "My son is going on a school trip to {c1} in December.",
             K("{c1}", "school"), "My wife roams in {c2} every month for work.", K("{c2}", "wife"), ("country",)),
    Scenario("DX_DEVICES", "distractor", "device", "My tablet's battery drains in two hours.", K("tablet", "battery"),
             "My smartwatch needs its own number.", K("smartwatch")),
    Scenario("DX_DATA", "distractor", "data", "My office tablet uses about {g1} GB a month.", K("{g1} gb", "tablet"),
             "My daughter's line needs a {g2} GB data pack for her exams.", K("{g2} gb", "daughter"), ("cap",)),
    Scenario("DX_BILLITEMS", "distractor", "bill", "My {bm} bill had a QAR {amt} late fee.", K("late fee"),
             "My bill this month included a device instalment of QAR {amt2}.", K(("instalment", "installment")),
             ("billmonth", "amount")),
    Scenario("DX_CONTACTS", "distractor", "contact", "My mother's line should get bill reminders by SMS.", K("mother"),
             "My driver's line must never receive marketing messages.", K("marketing")),
    Scenario("DX_HOME", "distractor", "home", "The home router is in the living room.", K("living room"),
             "The fibre box at home is in the garage.", K("garage")),
    Scenario("DX_INTL", "distractor", "intl", "I call my brother in {c1} every Friday.", K("{c1}", "brother"),
             "My colleague in {c2} sends me files over WhatsApp.", K("{c2}", "colleague"), ("country",)),
    Scenario("DX_TRAVEL", "distractor", "travel", "I fly to London twice a year for family visits.", K("london"),
             "My sister visits from Manila every summer.", K("manila")),
    Scenario("DX_NUMBERS", "distractor", "numbers", "I want a memorable number for my shop line.",
             K(("memorable number", "easy number", "vanity number", "easy-to-remember number")),
             "My old prepaid number should stay active for the alarm system.", K("alarm")),
)
BY_TYPE = {t: [s for s in SCENARIOS if s.etype == t] for t in list(TYPE_COUNTS) + ["distractor"]}


def fill_params(sc: Scenario, rng: random.Random, used: set[str], refs: dict[str, int]) -> dict[str, str]:
    """Draw this scenario's parameter values; raise LookupError if a value would repeat inside the thread."""
    v: dict[str, str] = {}
    for name in sc.params:
        if name in ("pu", "ps", "device", "gcc", "wld", "billmonth", "amount", "dbm"):
            pool = [x for x in P[name] if x not in used]
            if not pool:
                raise LookupError(name)
            pick = rng.choice(pool)
        elif name in ("branch", "country"):
            pool = [x for x in P[name] if x not in used]
            if len(pool) < 2:
                raise LookupError(name)
            pick = rng.sample(pool, 2)
        else:
            pick = rng.choice(P[name])
        if name == "pu":
            v["pu"] = pick
        elif name == "ps":
            v["ps"] = pick
        elif name == "branch":
            v["b1"], v["b2"] = pick
        elif name == "device":
            v["d1"] = pick
            v["d2"] = rng.choice([x for x in P["device"] if x not in used and x != pick])
        elif name == "lines":
            v["n1"], v["w1"], v["n2"], v["w2"] = pick
        elif name in ("cap", "usage", "price", "lowuse", "months2"):
            a, b = pick
            key = {"cap": ("g1", "g2"), "usage": ("g1", "g2"), "price": ("p1", "p2"), "lowuse": ("g1", "u1"),
                   "months2": ("m1", "m2")}[name]
            v[key[0]], v[key[1]] = a, b
        elif name == "gcc":
            v["r1"] = pick
        elif name == "wld":
            v["r2"] = pick
        elif name == "ordinal":
            (v["o1"], v["ow1"]), (v["o2"], v["ow2"]) = pick
        elif name == "country":
            v["c1"], v["c2"] = pick
        elif name == "billmonth":
            v["bm"] = pick
        elif name == "amount":
            v["amt"] = pick
            v["amt2"] = rng.choice([x for x in P["amount"] if x != pick and x not in used])
        elif name == "years":
            v["yrs"], v["year"] = pick
        elif name == "dbm":
            v["dbm"] = pick
        elif name == "actdate":
            v["ad"], v["adk"] = pick
        else:
            raise KeyError(name)
    if "{t1}" in sc.t_text:
        prefix = {"DU_CHARGE": "TKT", "DU_SIGNAL": "REF", "DU_ADDRESS": "REQ", "DU_ROUTER": "TKT",
                  "DU_SIMLATE": "SR"}[sc.sid]
        refs[prefix] = refs.get(prefix, 0) + 1
        base = {"TKT": 70000, "REF": 81000, "REQ": 52000, "SR": 63000}[prefix]
        v["t1"] = f"{prefix}-{base + 37 * refs[prefix]}"
        refs[prefix] += 1
        v["t2"] = f"{prefix}-{base + 37 * refs[prefix]}"
    for x in v.values():
        if x in used:
            raise LookupError(x)
    return v


def fmt_keys(keys: tuple, v: dict[str, str]) -> tuple:
    return tuple(tuple(x.format(**v) for x in group) for group in keys)


def plan_types(rng: random.Random) -> list[list[str]]:
    """Event types per thread: one supersedes each, refines in threads 1-8, the 16 others spread so that threads 1-8
    get one more and threads 9-12 two more."""
    pool = ["supports"] * 6 + ["contradicts"] * 5 + ["duplicates"] * 5
    rng.shuffle(pool)
    out = []
    for t in range(N_THREADS):
        types = ["supersedes"] + (["refines"] if t < 8 else [])
        while len(types) < 3:
            types.append(pool.pop())
        out.append(types)
    if pool:
        raise ValueError("event pool not used up")
    return out


def build(seed: int = SEED) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Deterministic: attempt k uses random.Random(f"{seed}:{k}"); the first attempt whose 12 threads all pass the
    per-thread rules is kept (a greedy choice can corner itself in a late thread, so a failed attempt restarts)."""
    for attempt in range(500):
        rng = random.Random(f"{seed}:{attempt}")
        try:
            data = _build_once(rng)
        except LookupError:
            continue
        validate(*data)
        LOG.info("C2 built on attempt %d", attempt)
        return data
    raise ValueError("no consistent C2 set found in 500 attempts")


def _build_once(rng: random.Random) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    types = plan_types(rng)
    with_distractor = sorted(rng.sample(range(N_THREADS), N_DISTRACTORS))
    distractor_order = list(BY_TYPE["distractor"])
    rng.shuffle(distractor_order)
    use_count = {s.sid: 0 for s in SCENARIOS}
    max_use = {"supersedes": 2, "refines": 2, "supports": 1, "contradicts": 1, "duplicates": 1, "distractor": 1}
    refs: dict[str, int] = {}

    threads, slots, links, distractors = [], [], [], []
    for t in range(N_THREADS):
        tno = t + 1
        tid, uid = f"c2_t{tno:02d}", f"CUST_{2000 + tno}"
        dx_sc = distractor_order.pop() if t in with_distractor else None
        result = None
        for _try in range(50):
            try:
                result = _try_thread(rng, tno, tid, uid, types[t], dx_sc, use_count, max_use, refs)
            except LookupError:
                result = None
            if result is not None:
                break
        if result is None:
            raise LookupError(f"thread {tno}")
        th, sl, lk, dx, chosen = result
        for sid in chosen:
            use_count[sid] += 1
        threads.append(th)
        slots += sl
        links += lk
        distractors += dx
    return threads, slots, links, distractors


def _try_thread(rng, tno, tid, uid, etypes, dx_scenario, use_count, max_use, refs):
    chosen, families = [], set()
    for et in etypes:
        options = [s for s in BY_TYPE[et] if use_count[s.sid] < max_use[et] and s.family not in families
                   and s.sid not in chosen]
        if not options:
            raise LookupError(et)
        sc = rng.choice(options)
        chosen.append(sc.sid)
        families.add(sc.family)
    if dx_scenario is not None:
        if dx_scenario.family in families:
            raise LookupError("distractor family")
        chosen.append(dx_scenario.sid)
    scen = [next(s for s in SCENARIOS if s.sid == c) for c in chosen]
    order = list(range(3))
    rng.shuffle(order)
    used: set[str] = set()
    refs_local = dict(refs)
    filled = []
    for sc in scen:
        v = fill_params(sc, rng, used, refs_local)
        used.update(v.values())
        filled.append((sc, v))

    # Exchange schedule: ex1 T(e1) T(e2); ex2 T(e3) [DA]; ex3 S(e1); ex4 S(e2) [DB]; ex5 S(e3).
    ev = [filled[i] for i in order]
    dx = filled[3] if dx_scenario is not None else None
    plan = {1: [("T", 0), ("T", 1)], 2: [("T", 2)] + ([("DA", None)] if dx else []), 3: [("S", 0)],
            4: [("S", 1)] + ([("DB", None)] if dx else []), 5: [("S", 2)]}
    slot_rows, link_rows, dx_rows = [], [], []
    statements_by_ex: dict[int, list[str]] = {}
    for ex, items in plan.items():
        for side, i in items:
            if side in ("T", "S"):
                sc, v = ev[i]
                eno = i + 1
                text = (sc.t_text if side == "T" else sc.s_text).format(**v)
                keys = fmt_keys(sc.t_keys if side == "T" else sc.s_keys, v)
                sid = f"T{tno:02d}-E{eno}{'T' if side == 'T' else 'S'}"
                slot_rows.append({"slot_id": sid, "thread_id": tid, "user_id": uid, "exchange": ex,
                                  "statement": text, "key_terms": [list(g) for g in keys],
                                  "scenario": sc.sid, "side": "target" if side == "T" else "source",
                                  "event_id": f"T{tno:02d}-E{eno}"})
            else:
                sc, v = dx
                text = (sc.t_text if side == "DA" else sc.s_text).format(**v)
                keys = fmt_keys(sc.t_keys if side == "DA" else sc.s_keys, v)
                sid = f"T{tno:02d}-D{'A' if side == 'DA' else 'B'}"
                slot_rows.append({"slot_id": sid, "thread_id": tid, "user_id": uid, "exchange": ex,
                                  "statement": text, "key_terms": [list(g) for g in keys], "scenario": sc.sid,
                                  "side": "distractor_a" if side == "DA" else "distractor_b", "event_id": None})
            statements_by_ex.setdefault(ex, []).append(text)
    for i, (sc, _v) in enumerate(ev):
        eno = i + 1
        link_rows.append({"link_id": f"T{tno:02d}-E{eno}", "thread_id": tid, "user_id": uid,
                          "source_slot": f"T{tno:02d}-E{eno}S", "target_slot": f"T{tno:02d}-E{eno}T",
                          "relation_type": sc.etype, "scenario": sc.sid})
    if dx:
        dx_rows.append({"pair_id": f"T{tno:02d}-D", "thread_id": tid, "user_id": uid, "slot_a": f"T{tno:02d}-DA",
                        "slot_b": f"T{tno:02d}-DB", "scenario": dx[0].sid, "family": dx[0].family})
    gold = [matcher.GoldLink(lk["source_slot"], lk["target_slot"], lk["relation_type"]) for lk in link_rows]
    status = matcher.end_statuses([s["slot_id"] for s in slot_rows], gold)
    for s in slot_rows:
        s["end_status"] = status[s["slot_id"]]

    messages = []
    seq = 0
    for ex in range(1, 6):
        when = START + dt.timedelta(days=4 * (tno - 1) + 2 * (ex - 1))
        seq += 1
        messages.append({"seq": seq, "exchange": ex, "role": "user", "content": " ".join(statements_by_ex[ex]),
                         "timestamp": when.isoformat()})
        seq += 1
        messages.append({"seq": seq, "exchange": ex, "role": "assistant", "content": rng.choice(ACKS),
                         "timestamp": (when + dt.timedelta(minutes=2)).isoformat()})
    thread = {"thread_id": tid, "user_id": uid, "messages": messages}
    try:
        validate_thread(thread, slot_rows, link_rows, dx_rows)
    except ValueError:
        return None
    refs.clear()
    refs.update(refs_local)
    return thread, slot_rows, link_rows, dx_rows, chosen


def _slots(slot_rows: list[dict]) -> list[matcher.Slot]:
    return [matcher.Slot(s["slot_id"], tuple(tuple(g) for g in s["key_terms"]), s["end_status"]) for s in slot_rows]


def validate_thread(thread: dict, slot_rows: list[dict], link_rows: list[dict], dx_rows: list[dict]) -> None:
    """Per-thread rules (see the module docstring); raise ValueError on the first one broken."""
    msgs = thread["messages"]
    if len(msgs) != 10 or [m["role"] for m in msgs] != ["user", "assistant"] * 5:
        raise ValueError(f"{thread['thread_id']}: needs 5 user/assistant exchanges")
    ex_text = {ex: " ".join(m["content"] for m in msgs if m["exchange"] == ex) for ex in range(1, 6)}
    slots = _slots(slot_rows)
    by_id = {s["slot_id"]: s for s in slot_rows}
    for s in slot_rows:
        user_msg = next(m["content"] for m in msgs if m["exchange"] == s["exchange"] and m["role"] == "user")
        if s["statement"] not in user_msg:
            raise ValueError(f"{s['slot_id']}: statement not in its user message")
        slot = next(x for x in slots if x.slot_id == s["slot_id"])
        for ex, text in ex_text.items():
            hit = slot.matches(matcher.normalize(text))
            if ex == s["exchange"] and not hit:
                raise ValueError(f"{s['slot_id']}: not stated in full inside its exchange")
            if ex != s["exchange"] and hit:
                raise ValueError(f"{s['slot_id']}: also stated in full in exchange {ex}")
        r = matcher.map_memory(s["statement"], slots)
        if r.slot_id != s["slot_id"]:
            raise ValueError(f"{s['slot_id']}: its own statement maps to {r.outcome} {r.candidates}")
    for s in slot_rows:
        for month, _day in _DAY_DATE.findall(matcher.normalize(s["statement"])):
            if _MONTHS.index(month) + 1 >= START.month:
                raise ValueError(f"{s['slot_id']}: a dated event on or after the first thread day")
    for ack in (m["content"] for m in msgs if m["role"] == "assistant"):
        if matcher.map_memory(ack, slots).outcome != "unmatched":
            raise ValueError("an acknowledgement states a slot")
    for lk in link_rows:
        if by_id[lk["source_slot"]]["exchange"] <= by_id[lk["target_slot"]]["exchange"]:
            raise ValueError(f"{lk['link_id']}: the target must come in an earlier exchange")
    for d in dx_rows:
        if by_id[d["slot_a"]]["exchange"] == by_id[d["slot_b"]]["exchange"]:
            raise ValueError(f"{d['pair_id']}: distractor sides share an exchange")
    gold = [matcher.GoldLink(lk["source_slot"], lk["target_slot"], lk["relation_type"]) for lk in link_rows]
    status = matcher.end_statuses([s["slot_id"] for s in slot_rows], gold)
    if any(status[s["slot_id"]] != s["end_status"] for s in slot_rows):
        raise ValueError("end statuses disagree with the gold links")
    # A perfect run scores exactly the gold, with no harm; an empty run misses every link.
    mems = {s["slot_id"]: s["statement"] for s in slot_rows}
    perfect = matcher.score_thread(mems, [matcher.PredictedLink(g.source_slot, g.target_slot, g.relation_type)
                                          for g in gold], slots, gold, [(d["slot_a"], d["slot_b"]) for d in dx_rows])
    if (perfect.tp, perfect.fp, perfect.fn, perfect.harmful_invalidations) != (len(gold), 0, 0, 0):
        raise ValueError("a perfect run does not score as perfect")
    fams = [next(s for s in SCENARIOS if s.sid == x["scenario"]).family for x in link_rows] + \
           [d["family"] for d in dx_rows]
    if len(set(fams)) != len(fams):
        raise ValueError("two scenarios of one thread share a topic family")


def validate(threads: list[dict], slots: list[dict], links: list[dict], distractors: list[dict]) -> None:
    if len(threads) != N_THREADS or [t["user_id"] for t in threads] != [f"CUST_{2001 + i}" for i in range(N_THREADS)]:
        raise ValueError("need threads for CUST_2001..CUST_2012 in order")
    if sum(len(t["messages"]) for t in threads) != 120:
        raise ValueError("need 120 messages")
    counts = {k: sum(1 for lk in links if lk["relation_type"] == k) for k in TYPE_COUNTS}
    if counts != TYPE_COUNTS:
        raise ValueError(f"event type counts {counts}")
    if len(links) != 36 or len(distractors) != N_DISTRACTORS:
        raise ValueError("need 36 gold links and 9 distractor pairs")
    if len({s["slot_id"] for s in slots}) != len(slots):
        raise ValueError("duplicate slot ids")
    for t in threads:
        validate_thread(t, [s for s in slots if s["thread_id"] == t["thread_id"]],
                        [lk for lk in links if lk["thread_id"] == t["thread_id"]],
                        [d for d in distractors if d["thread_id"] == t["thread_id"]])


def dump_jsonl(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r, sort_keys=True, ensure_ascii=True) + "\n" for r in rows).encode("ascii")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    data = build(SEED)
    blobs = dict(zip(FILES, (dump_jsonl(x) for x in data)))
    if args.check:
        bad = [f for f, b in blobs.items() if not (args.out / f).is_file() or (args.out / f).read_bytes() != b]
        if bad:
            LOG.error("check failed: %s differ from a fresh generation", ", ".join(bad))
            return 1
        LOG.info("check ok: %s identical to a fresh generation (seed %d)", ", ".join(FILES), SEED)
        return 0
    for f, b in blobs.items():
        atomic_write(args.out / f, b)
        LOG.info("wrote %s (%d bytes)", f, len(b))
    threads, slots, links, dxs = data
    LOG.info("C2: %d threads, %d messages, %d slots, %d gold links, %d distractor pairs, seed %d", len(threads),
             sum(len(t["messages"]) for t in threads), len(slots), len(links), len(dxs), SEED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
