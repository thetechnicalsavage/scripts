#!/usr/bin/env python3
# v1.5 - (v1.5, GO-3: DECLINED, Select AI's "Sorry, unfortunately" refusal, and SCORED_STATUSES)
#        RAG tuning lab: bilingual (EN/AR) answer scorer and span normaliser. Pure functions,
#        stdlib only, no locale, no randomness, no database.
#        v1.4 (round-3 checker, 30-Sep): Arabic compound day ordinals after يوم/اليوم are one number
#              ("اليوم الثاني والعشرين" -> 22, "اليوم السابع عشر" -> 17): the lexicon pass now runs
#              before the day-context passes in _convert (they split them into "2 و 20", "7 10").
#        v1.3 (review 1, 29-Sep): numbers that are not values are ignored before matching (list
#              markers, grade codes, clock times, section/page references); EN/AR number words
#              become digits (cardinals 1-31, Arabic ordinals for days of the month, Arabic duals,
#              ألف / thousand / lakh multipliers) in answers, questions and terms; a single-digit
#              fact or forbidden value counts only with a unit after it ("7 days", "٥ أيام");
#              the question echo works after number-word conversion; context_ok values (legitimate
#              in the gold document) contaminate only when the facts are missing; refusal cues are
#              anchored to the documents (first-person cues such as "I could not find" need no
#              object; other cues need a corpus word, and "policy" anchors only verbs of stating,
#              so "G7 is not specified", "No mention of G7" or "this policy is not available in
#              KSA" inside a correct answer is not a refusal, in EN and AR alike: Codex review) and
#              applied only when facts are missing or the answer is short, with the missing EN/AR
#              phrasings added; audit on every
#              contaminated answer, every unanswerable wrong, a number word with facts missing, an
#              ignored refusal cue and a context value (audit_reasons lists why); the Sources
#              header is widened and only a trailing block is stripped; GLF ids without a
#              language suffix are stripped.
#        v1.2: Sources block, file names, URLs and document ids stripped before scoring; verdict
#              ladder correct/hedged/contaminated/false_refusal/wrong; question-echo rule; the
#              unanswerable "no corpus number" rule; 1,200-character flag; span_key() for
#              whitespace/punctuation/harakat-insensitive evidence matching; answer language;
#              Arabic terms that carry the article also match after the proclitics "لل"/"بال";
#              (Codex review) Arabic terms need a word end too, so عام no longer matches عاملة.
#        v1.1: refusal cues are regexes (verb-form alternations), not a fixed phrase list.
#
# Run as : imported by eval_retrieval.py / eval_rag.py / validate_gold.py; tested by
#          ../tests/test_eval_norm.py and ../tests/test_questions.py
# Usage  : from eval_norm import norm, span_key, score_answer, fact_present, to_digits
# Re-run : n/a (library). Nothing here reads or writes files except corpus_numbers().
#
# PLAN.md 5.3 scoring order: 1) strip the Sources block, file names and URLs; 2) normalise
# (NFKC; bidi / zero-width controls; harakat and tatweel; Arabic-Indic digits and ٫ ٬ ٪; letter
# folds; thousands separators; casefold); 3) drop numbers that are not values and turn number
# words into digits; 4) match numbers on boundaries (single digits only with a unit), Arabic terms
# with optional proclitics, English terms on word boundaries.
from __future__ import annotations

import decimal
import glob
import os
import re
import unicodedata

# ---------------------------------------------------------------------------------------------
# 1. normalisation
# ---------------------------------------------------------------------------------------------
# Arabic-Indic (U+0660-0669) and Extended/Persian (U+06F0-06F9) digits -> ASCII;
# Arabic decimal (U+066B) / thousands (U+066C) separators; Arabic percent (U+066A).
# NFKC does NOT map these digits (checked), and Python's \d DOES match them, so every number
# regex below uses [0-9] on text that went through this table first.
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩"
                        "۰۱۲۳۴۵۶۷۸۹"
                        "٫٬٪",
                        "01234567890123456789.,%")
_HARAKAT = re.compile("[ً-ٰٟۖ-ۭ]")     # tashkeel, Quranic marks
_TATWEEL = "ـ"
_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا",
                          "ٱ": "ا",                     # alef/hamza seats -> bare alef
                          "ة": "ه",                     # taa marbuta -> haa
                          "ى": "ي", "ی": "ي",  # alef maksura / Farsi yeh -> yeh
                          "ؤ": "و", "ئ": "ي",  # hamza on waw / yeh
                          "ک": "ك",                     # keheh -> kaf
                          "’": "'", "‘": "'"})          # curly apostrophes (don't -> don't)
_THOUSANDS = re.compile(r"(?<=[0-9]),(?=[0-9]{3}(?![0-9]))")   # only true 3-digit groups
_AR_LETTER = "ء-يٮ-ۓۺ-ۿ"
_CLITIC = "(?:و|ف|ب|ل|ك)?"               # و ف ب ل ك
_ARTICLE = "(?:ال|لل)?"                        # ال / لل


def norm(s: str) -> str:
    """Normalise EN/AR text for fact matching. Order matters: NFKC first folds presentation
    forms (U+FB50-FEFF) to base letters; format controls (Cf: bidi marks, ZWJ/ZWNJ, BOM, soft
    hyphen) go next; digits are mapped after NFKC."""
    s = unicodedata.normalize("NFKC", s or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    s = _HARAKAT.sub("", s).replace(_TATWEEL, "")
    s = s.translate(_DIGITS).translate(_LETTERS)
    s = _THOUSANDS.sub("", s)                            # 2,500 -> 2500
    s = s.casefold()
    return re.sub(r"\s+", " ", s).strip()


def span_key(s: str) -> str:
    """Key for evidence-span containment: norm(), then only letters and digits survive, plus a
    '.' that sits between two digits (so 2.5 never collapses into 25). Whitespace, line breaks,
    punctuation, table pipes and markdown are ignored, which is what PDF/DOCX extraction
    damages (PLAN.md 5.1/5.2)."""
    t = norm(s)
    out = []
    for i, ch in enumerate(t):
        cat = unicodedata.category(ch)
        if cat[0] in "LN":
            out.append(ch)
        elif ch == "." and 0 < i < len(t) - 1 and t[i - 1].isdigit() and t[i + 1].isdigit():
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------------------------
# 2. stripping what the model did not write from knowledge of the documents
# ---------------------------------------------------------------------------------------------
# A line that starts a Sources block (markdown decoration allowed). v1.3: "Sources used:",
# "Source documents:", "References used:", "المصادر المستخدمة:" are headers too, and only a
# TRAILING block is removed: the last header, with answer text before it, followed either by at
# most SOURCES_MAX_LINES lines or only by source-like lines. A leading "Source: ..." line is kept
# (its document id is removed below), so it can no longer empty the answer. Probe P7 settles the
# real narrate format; pin the header here if it differs. (04_probes.py P7 reuses this regex.)
SOURCES_HEADER = re.compile(
    r"^[ \t>#*_\-]*(?:(?:sources?|references?|citations?)(?:[ \t]+(?:used|consulted|cited|retrieved"
    r"|documents?))?|المصادر(?:[ \t]+المستخدم[ةه])?|المصدر|المراجع)[ \t*_]*(?:[:：]|$)", re.I | re.M)
SOURCES_MAX_LINES = 40
_URL = re.compile(r"(?:https?|file|ftp)://\S+", re.I)
_FILE = re.compile(r"[^\s()\[\]<>\"'`]+\.(?:pdf|docx?|txt|html?|json|xml|csv|md)\b", re.I)
# v1.3: GLF ids without the language suffix (GLF-004, GLF-A05) are ids too
DOC_ID = re.compile(r"\b(?:GLF-(?:[0-9]{3}|[AE][0-9]{2})(?:-(?:EN|AR))?|(?:HRP|ESS|MSS)-[0-9]{3})\b", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•▪◦·]|[0-9٠-٩]{1,2}[.)])\s")


def _source_like(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    return len(s) <= 300 and bool(_BULLET.match(line) or _URL.search(s) or _FILE.search(s) or DOC_ID.search(s))


def strip_sources(answer: str) -> str:
    """Remove a trailing Sources block, URLs, file names and document ids (they carry numbers such
    as HRP-013 and file names that would otherwise count as facts)."""
    a = answer or ""
    last = None
    for last in SOURCES_HEADER.finditer(a):
        pass
    if last is not None and a[:last.start()].strip():
        after = a[last.end():].strip("\n").splitlines()
        if len(after) <= SOURCES_MAX_LINES or all(_source_like(x) for x in after):
            a = a[:last.start()]
    a = _URL.sub(" ", a)
    a = _FILE.sub(" ", a)
    a = DOC_ID.sub(" ", a)
    return a.strip()


# ---------------------------------------------------------------------------------------------
# 3. number words -> digits (v1.3). Applied to norm()'d text, so every lexicon entry below is
#    folded with norm() too (أ -> ا, ة -> ه, ى -> ي, casefold). Scope, as agreed in review 1:
#    cardinals 1-31 (EN, AR), EN ordinals 10-31, AR ordinals 11-31 anywhere and 1-10 only as a day
#    of the month ("اليوم الخامس", "الخامس من الشهر"), AR duals of units ("يومين" -> "2 يوم"),
#    and the multipliers ألف/آلاف, thousand, k, lakh. English "first".."ninth" are never converted
#    ("the first day" is not "1 day").
# ---------------------------------------------------------------------------------------------
_EN_ONES = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_EN_ORD_ONES = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth")
_EN_TEENS = ("ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
             "eighteen", "nineteen")
_EN_ORD_TEENS = ("tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth",
                 "seventeenth", "eighteenth", "nineteenth")
_AR_ONES = {1: ("واحد", "واحدة"), 2: ("اثنان", "اثنين", "اثنتان", "اثنتين"), 3: ("ثلاثة", "ثلاث"),
            4: ("أربعة", "أربع"), 5: ("خمسة", "خمس"), 6: ("ستة", "ست"), 7: ("سبعة", "سبع"),
            8: ("ثمانية", "ثماني", "ثمان"), 9: ("تسعة", "تسع")}
_AR_TENS = {10: ("عشرة", "عشر"), 20: ("عشرون", "عشرين"), 30: ("ثلاثون", "ثلاثين")}
_AR_ORD = {1: ("الأول", "الأولى"), 2: ("الثاني", "الثانية"), 3: ("الثالث", "الثالثة"),
           4: ("الرابع", "الرابعة"), 5: ("الخامس", "الخامسة"), 6: ("السادس", "السادسة"),
           7: ("السابع", "السابعة"), 8: ("الثامن", "الثامنة"), 9: ("التاسع", "التاسعة"),
           10: ("العاشر", "العاشرة")}
_AR_ORD_TENS = {20: ("العشرون", "العشرين"), 30: ("الثلاثون", "الثلاثين")}
_AR_DUALS = {"يوم": ("يومين", "يومان"), "ساعة": ("ساعتين", "ساعتان"), "شهر": ("شهرين", "شهران"),
             "أسبوع": ("أسبوعين", "أسبوعان"), "سنة": ("سنتين", "سنتان"), "عام": ("عامين", "عامان"),
             "مرة": ("مرتين", "مرتان")}


def _en_lexicon() -> dict:
    w = {x: i for i, x in enumerate(_EN_ONES, 1)}
    w.update({x: i for i, x in enumerate(_EN_TEENS, 10)})
    w.update({x: i for i, x in enumerate(_EN_ORD_TEENS, 10)})
    w.update({"twenty": 20, "thirty": 30, "twentieth": 20, "thirtieth": 30})
    for tens, tv in (("twenty", 20), ("thirty", 30)):
        for i in range(1, 10):
            if tv + i <= 31:
                for sep in ("-", " "):
                    w[tens + sep + _EN_ONES[i - 1]] = tv + i
                    w[tens + sep + _EN_ORD_ONES[i - 1]] = tv + i
    return w


def _ar_lexicons():
    """(anywhere, day_context) {normalised phrase: value}."""
    any_, day = {}, {}
    for v, forms in _AR_ONES.items():
        for f in forms:
            any_[f] = v
    for v, forms in _AR_TENS.items():
        for f in forms:
            any_[f] = v
    any_.update({"مئة": 100, "مائة": 100})
    teen_ones = dict(_AR_ONES)
    teen_ones[1] = ("أحد", "إحدى")
    teen_ones[2] = ("اثنا", "اثني", "اثنتا", "اثنتي")
    for u, forms in teen_ones.items():                          # 11-19: "خمسة عشر", "خمس عشرة"
        for f in forms:
            any_[f + " عشر"] = 10 + u
            any_[f + " عشرة"] = 10 + u
    for tv in (20, 30):                                          # 21-29, 31: "سبعة وعشرون"
        for u, forms in _AR_ONES.items():
            if tv + u > 31:
                continue
            for f in forms + (("إحدى",) if u == 1 else ()):
                for t in _AR_TENS[tv]:
                    any_[f + " و" + t] = tv + u
    for v, forms in _AR_ORD.items():                             # ordinals 1-10: day context only
        for f in forms:
            day[f] = v
    for v, forms in _AR_ORD_TENS.items():
        for f in forms:
            any_[f] = v
    ord_ones = dict(_AR_ORD)
    ord_ones[1] = ("الحادي", "الحادية")
    for u in range(1, 10):                                        # 11-19: "الخامس عشر"
        for f in ord_ones[u]:
            any_[f + " عشر"] = 10 + u
            any_[f + " عشرة"] = 10 + u
    for tv in (20, 30):                                          # 21-29, 31: "السابع والعشرين"
        for u in range(1, 10):
            if tv + u > 31:
                continue
            for f in ord_ones[u]:
                for t in _AR_ORD_TENS[tv]:
                    any_[f + " و" + t] = tv + u
    return {norm(k): v for k, v in any_.items()}, {norm(k): v for k, v in day.items()}


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=lambda x: (-len(x), x)))


_EN_WORDS = _en_lexicon()
_AR_WORDS, _AR_DAY_ORD = _ar_lexicons()
_AR_DUAL = {norm(f): "2 " + norm(unit) for unit, forms in _AR_DUALS.items() for f in forms}
_AR_DUAL.update({norm("ألفين"): "2000", norm("ألفان"): "2000"})
_AR_HEAD = "(?<![" + _AR_LETTER + "])"
_AR_TAIL = "(?![" + _AR_LETTER + "])"
_EN_WORDS_RX = re.compile(r"\b(" + _alt(_EN_WORDS) + r")\b")      # dashes are folded to "-" first
_AR_WORDS_RX = re.compile(_AR_HEAD + "(و|ف|ب|ل|ك)?(" + _alt(_AR_WORDS) + ")" + _AR_TAIL)
_AR_DUAL_RX = re.compile(_AR_HEAD + "(و|ف|ب|ل|ك)?(" + _alt(_AR_DUAL) + ")" + _AR_TAIL)
_AR_DAY_BEFORE = re.compile(_AR_HEAD + "((?:في )?(?:ال)?يوم )(" + _alt(_AR_DAY_ORD) + ")" + _AR_TAIL)
_AR_DAY_AFTER = re.compile(_AR_HEAD + "(" + _alt(_AR_DAY_ORD) + ")( من (?:كل )?(?:ال)?شهر)")
_MULTIPLIERS = (
    (re.compile(r"(?<![0-9.])([0-9]+(?:\.[0-9]+)?)[ \t]*(?:الفا|الف|الاف)" + _AR_TAIL), 1000),
    (re.compile(r"(?<![0-9.])([0-9]+(?:\.[0-9]+)?)[ \t]*thousand\b"), 1000),
    (re.compile(r"(?<![0-9.])([0-9]+(?:\.[0-9]+)?)k\b"), 1000),
    (re.compile(r"(?<![0-9.])([0-9]+(?:\.[0-9]+)?)[ \t]*(?:lakhs?|lacs?)\b"), 100000),
)
_A_LAKH = re.compile(r"\ba lakh\b")
# spelled numbers the converter leaves alone; with a missing fact they still send the answer to audit
_OTHER_NUMBER_WORDS = re.compile(
    r"\b(?:forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|lakhs?|crores?|dozen|weeks?)\b|"
    + _AR_HEAD + "(?:و|ف|ب|ل|ك)?(?:" + _alt([norm(x) for x in (
        "أربعون", "أربعين", "خمسون", "خمسين", "ستون", "ستين", "سبعون", "سبعين", "ثمانون", "ثمانين",
        "تسعون", "تسعين", "ألف", "آلاف", "مليون", "أسبوع", "أسبوعين", "أسابيع")]) + ")" + _AR_TAIL)
_DASHES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
                         "\u2212": "-"})


def _num_text(value: decimal.Decimal) -> str:
    """Decimal-exact text of a number: 7500, 175000, 2.5 (no float rounding)."""
    s = format(value.normalize(), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _convert(t: str):
    """(text with number words as digits, number of words converted other than 'one'/'واحد').
    t must already be norm()'d."""
    count = [0]

    def sub(rx, fn, text):
        def rep(m):
            out, counted = fn(m)
            count[0] += counted
            return out
        return rx.sub(rep, text)

    def ar(m):
        v = _AR_WORDS[m.group(2)]
        return ((m.group(1) + " ") if m.group(1) else "") + str(v), int(v != 1)

    def dual(m):
        return ((m.group(1) + " ") if m.group(1) else "") + _AR_DUAL[m.group(2)], 1

    t = t.translate(_DASHES)
    # v1.4: the lexicon pass (compound ordinals 11-31 among its entries) runs BEFORE the day-context
    # passes. Every multi-word entry that starts with an ordinal 1-10 ("الثاني والعشرين", "السابع
    # عشر") is a compound, and a compound is the longer match, so it must win. Run after, the day
    # pass took the ordinal alone: "اليوم الثاني والعشرين" -> "اليوم 2 و 20", "اليوم السابع عشر" ->
    # "اليوم 7 10". Reordering keeps the lexicon the one list of compounds; a lookahead on
    # _AR_DAY_BEFORE would need a second list of teen/tens continuations. The lexicon pass never
    # converts a lone ordinal 1-10, في, يوم/اليوم or "من (كل) الشهر", so the day passes still convert
    # every standalone day ordinal they converted before.
    t = sub(_AR_WORDS_RX, ar, t)
    t = sub(_AR_DAY_BEFORE, lambda m: (m.group(1) + str(_AR_DAY_ORD[m.group(2)]), 1), t)
    t = sub(_AR_DAY_AFTER, lambda m: (str(_AR_DAY_ORD[m.group(1)]) + m.group(2), 1), t)
    t = sub(_AR_DUAL_RX, dual, t)
    t = sub(_EN_WORDS_RX, lambda m: (str(_EN_WORDS[m.group(1)]), int(m.group(1) != "one")), t)
    t = sub(_A_LAKH, lambda m: ("100000", 1), t)
    for rx, k in _MULTIPLIERS:
        t = sub(rx, lambda m, k=k: (_num_text(decimal.Decimal(m.group(1)) * k), 1), t)
    return re.sub(r"[ \t]+", " ", t).strip(), count[0]


def to_digits(text: str) -> str:
    """norm() plus EN/AR number words as digits: 'within five days' -> 'within 5 days',
    'في السابع والعشرين' -> 'في 27', '٤٩ ألف' -> '49000', 'INR 1 lakh' -> 'inr 100000'."""
    return _convert(norm(text))[0]


# ---------------------------------------------------------------------------------------------
# 4. numbers that are not values (v1.3), and the scoring text
# ---------------------------------------------------------------------------------------------
# A list marker at the start of a line ("1.", "2)", "(3)", "٤-" followed by text). Applied to the
# raw answer, before norm() joins the lines; not applied to chunk text, where a wrapped line can
# start with a value.
_LIST_MARKER = re.compile(r"^[ \t>*_#]*(?:[0-9٠-٩۰-۹]{1,2}[.)\-]|\([0-9٠-٩۰-۹]{1,2}\))(?=[ \t]+\S)", re.M)
_GRADE = re.compile(r"\bg[0-9]{1,2}\b")                                  # G3, g10 (after casefold)
_TIME = re.compile(r"(?<![0-9])[0-9]{1,2}:[0-9]{2}(?![0-9])")            # 20:00, 09:30
_SECTION = re.compile(
    r"(?:\b(?:sections?|sec\.|s\.|clauses?|pages?|pp?\.|paragraphs?|para\.|articles?|annex(?:es)?|appendix"
    r"|tables?)|§)[ \t]*[0-9]+(?:\.[0-9]+)*"
    "|" + _AR_HEAD + "(?:و|ف|ب|ل|ك)?(?:ال)?(?:قسم|بند|فقره|صفحه|جدول|ماده|ملحق)[ \t]*(?:رقم[ \t]*)?"
    r"[0-9]+(?:\.[0-9]+)*")


def scoring_text(text: str, list_markers: bool = True) -> str:
    """The text numbers are matched in: list markers dropped (answers only), norm(), number words
    as digits, then clock times, grade codes and section/page/table references removed."""
    s = text or ""
    if list_markers:
        s = _LIST_MARKER.sub(" ", s)
    s, _ = _convert(norm(s))
    s = _TIME.sub(" ", s)
    s = _GRADE.sub(" ", s)
    s = _SECTION.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def number_words(text: str) -> int:
    """How many spelled numbers the text holds ('one'/'واحد' not counted: too common)."""
    t = norm(text)
    n = _convert(t)[1]
    return n + len(_OTHER_NUMBER_WORDS.findall(t))


# ---------------------------------------------------------------------------------------------
# 5. matching
# ---------------------------------------------------------------------------------------------
_NUMBER = re.compile(r"(?<![0-9.])[0-9]+(?:\.[0-9]+)?(?![0-9]|\.[0-9])")
_SINGLE = frozenset("123456789")
# v1.3: what may follow a single-digit value for it to count ("7 calendar days", "4-hour",
# "5th", "٥ أيام عمل", "4 (four) hours"). Without it, 1-9 are list numbers, steps, floors.
_EN_UNIT = (r"(?:days?|weeks?|months?|years?|yrs?|hours?|hrs?|h|minutes?|mins?|instal{1,2}ments?"
            r"|per[ \t]?cent|percent|km|kms|kilomet(?:er|re)s?)\b")
_EN_UNIT_MOD = r"(?:calendar|working|business|consecutive|full|paid|clear|whole|additional|extra|more)"
_AR_UNIT = "(?:" + _alt([norm(x) for x in (
    "يوم", "أيام", "ساعة", "ساعات", "شهر", "شهور", "أشهر", "أسبوع", "أسابيع", "سنة", "سنوات", "سنين", "عام",
    "أعوام", "دقيقة", "دقائق", "مرات", "قسط", "أقساط", "كيلومتر", "بالمئة", "في المئة", "بالمائة",
    "في المائة")]) + ")"
_UNIT_AFTER = re.compile(r"\)?(?:[ \t]*\([^()]{0,16}\))?(?:(?:st|nd|rd|th)\b|[ \t]*%|[ \t]*-?[ \t]*(?:"
                         + _EN_UNIT_MOD + r"[ \t]+)?" + _EN_UNIT + "|[ \t]*" + _AR_UNIT + ")")


def _is_single(t: str) -> bool:
    return canon_number(t) in _SINGLE


def _pattern(term: str, unit: bool = True) -> re.Pattern:
    """Regex for one fact/forbidden term on scoring_text(). unit=False is the question-echo form:
    a number stated in the question counts with or without a unit."""
    return _compiled(term, unit)


def _compiled_uncached(term: str, unit: bool) -> re.Pattern:
    t, _ = _convert(norm(term))
    if not t:
        raise ValueError(f"empty fact term {term!r}")
    if re.fullmatch(r"[0-9.]+", t):                       # a number: not inside a longer number
        body = r"(?<![0-9.])" + re.escape(t) + r"(?![0-9]|\.[0-9])"
        if unit and _is_single(t):
            body += "(?=" + _UNIT_AFTER.pattern + ")"
        return re.compile(body)
    if re.search("[" + _AR_LETTER + "]", t):              # Arabic: proclitics allowed, no run-on
        if t[0].isdigit():                                 # "2 سنه" (from سنتين): a digit boundary
            return re.compile(r"(?<![0-9.])" + re.escape(t) + "(?![" + _AR_LETTER + "])")
        head = "(?<![" + _AR_LETTER + "])" + _CLITIC
        tail = "(?![" + _AR_LETTER + "])"                  # whole word: عام is not inside عاملة
        if t.startswith("ال") and len(t) - 2 >= 3:
            # the term carries the article: keep it REQUIRED but accept its "لل" form, so
            # الدوحه matches بالدوحه / للدوحه but bare دوحه (or رياض in رياض الاطفال) does not
            return re.compile(head + "(?:ال|لل)" + re.escape(t[2:]) + tail)
        return re.compile(head + _ARTICLE + re.escape(t) + tail)
    return re.compile(r"(?<!\w)" + re.escape(t) + r"(?!\w)")


_CACHE: dict = {}


def _compiled(term: str, unit: bool) -> re.Pattern:
    key = (term, unit)
    if key not in _CACHE:
        _CACHE[key] = _compiled_uncached(term, unit)
    return _CACHE[key]


def fact_present(answer: str, alternatives, list_markers: bool = True) -> bool:
    """A gold fact is a list of acceptable surface forms; any one match counts. list_markers=False
    for chunk text (eval_retrieval), where a line may start with a wrapped value."""
    a = scoring_text(answer, list_markers)
    return any(_pattern(x).search(a) for x in alternatives)


def canon_number(x: str) -> str:
    """'01' -> '1', '2.50' -> '2.5', '2.0' -> '2'."""
    if "." in x:
        i, f = x.split(".", 1)
        f = f.rstrip("0")
        i = i.lstrip("0") or "0"
        return i + ("." + f if f else "")
    return x.lstrip("0") or "0"


def numbers_in(text: str) -> set:
    return {canon_number(n) for n in _NUMBER.findall(norm(text))}


def values_in(pre: str) -> set:
    """Numbers in a scoring_text() that read as values: a single digit only with a unit."""
    out = set()
    for m in _NUMBER.finditer(pre):
        c = canon_number(m.group())
        if c in _SINGLE and not _UNIT_AFTER.match(pre, m.end()):
            continue
        out.add(c)
    return out


def corpus_numbers(src_dir: str) -> frozenset:
    """Every number in the corpus source texts (corpus/src/**/*.txt), canonicalised. Used by the
    unanswerable rule: a refusal that still quotes a corpus number is not a clean refusal."""
    files = sorted(glob.glob(os.path.join(src_dir, "**", "*.txt"), recursive=True))
    if not files:
        raise FileNotFoundError(f"no corpus source texts under {src_dir}")
    out = set()
    for f in files:
        with open(f, encoding="utf-8") as fh:
            out |= numbers_in(fh.read())
    return frozenset(out)


# ---------------------------------------------------------------------------------------------
# 6. refusal cues (regexes over norm()'d text). v1.3: a cue must be about the documents.
#    Self-contained: first-person cues ("I could not find", "I don't know", "لم أجد", "لا أعرف",
#    "لا يمكنني العثور") and "none of the documents". Every other cue needs a document word next to
#    it: in the same sentence in English, within 8 words in Arabic (one Arabic sentence often chains
#    several clauses with و). Corpus words (documents, context, files, excerpts, الوثائق, المستندات,
#    النص ...) anchor every cue. "policy"/"السياسة" anchors only verbs of stating ("the policy does
#    not mention", "لا تنص السياسة") and containing verbs whose object is information ("the policies
#    do not include information", "لا تتضمن السياسات معلومات"), because "this policy is not
#    available in KSA" is a rule, not a refusal. So a gap about a sub-topic inside a correct answer ("G7 is not specified", "No mention
#    of G7", "not available for KSA", "لا يوجد صرف للدرجة G4") is not a refusal in either language
#    (review 1 findings 5 and 6; Codex review rounds 1-3).
# ---------------------------------------------------------------------------------------------
_DOC_CORPUS_EN = (r"(?:documents?|docs?|files?|context|sources?|texts?|excerpts?|materials?|records?|knowledge base"
                  r"|(?:provided|given|available|retrieved|supplied|attached|shared) (?:information|content|data"
                  r"|polic(?:y|ies))|information (?:provided|given|available|retrieved|supplied))")
DOC_CORPUS_EN = re.compile(r"\b" + _DOC_CORPUS_EN + r"\b")
DOC_ANY_EN = re.compile(r"\b(?:" + _DOC_CORPUS_EN + r"|polic(?:y|ies)|handbooks?|guidelines?)\b")
_REFUSAL_EN_SELF = [
    r"\b(?:i|we)(?: really)? (?:cannot|can't|can not|could not|couldn't|was unable to|am unable to|were unable to"
    r"|are unable to|did not|didn't) (?:find|locate|answer|determine|confirm|identify|see)\b",
    r"\b(?:i|we) (?:do not|don't) know\b",
    r"\bno (?:relevant |specific |further )?(?:information|details|data) (?:was|were|could be|can be|has been) found\b",
    r"\b(?:i|we) (?:don't|do not) have (?:enough |sufficient |any )?(?:information|details|data)\b",
    r"\bnone of the (?:provided |given |available |retrieved |supplied |attached )?(?:documents?|docs?|files?"
    r"|sources?|polic(?:y|ies)|context|texts?|excerpts?|materials?|records?)\b",
]
_REFUSAL_EN_STATING = [          # anchored by a corpus word or by "policy"
    r"\b(?:do|does|did)(?: not|n't) (?:explicitly |specifically |clearly )?(?:mention|specify|state|say|address"
    r"|discuss|define|describe|outline|list|reference|indicate)\b",
    r"(?:\bnot|n't) (?:explicitly |specifically |clearly )?(?:mentioned|specified|stated|addressed|discussed"
    r"|defined|described|outlined|listed|referenced|documented)\b",
    r"\b(?:any|no) (?:mention|reference|indication)s? (?:of|to|about)\b",
    r"\bmakes? no (?:mention|reference)\b",
    # containing verbs whose object is information itself (Codex round 4)
    r"\b(?:do|does|did)(?: not|n't) (?:contain|include|provide|have|give|offer) (?:any |specific |detailed |further "
    r"|relevant )?(?:information|details|data|guidance|references?|mentions?)\b",
    r"\b(?:contains?|includes?|provides?|has|have|gives?|offers?) no (?:relevant |specific |further |detailed )?"
    r"(?:information|details|data|guidance|references?|mentions?)\b",
]
_REFUSAL_EN_CONTAINING = [       # anchored by a corpus word only
    r"\b(?:do|does|did)(?: not|n't) (?:contain|include|cover|provide|have)\b",
    r"(?:\bnot|n't) (?:covered|available|found|included|provided|contained|present)\b",
    r"\bno (?:[a-z]+ ){0,3}?(?:information|details|mention|data|reference|provision|polic(?:y|ies)|guidance"
    r"|rules?)\b",
    r"\b(?:contains?|contained|includes?|included|has|have|had|provides?|provided|gives?|offers?) no\b",
    r"\bno\b[^.!?;]{0,60}?\b(?:could|can|was|were|is|are) (?:be )?(?:found|located|identified)\b",
    r"\bnothing (?:about|on|regarding|in)\b",
    r"\bnot (?:in|part of) the\b",
    r"\b(?:cannot|can't|could not|couldn't) be (?:found|determined|confirmed|answered)\b",
    r"\bunable to (?:find|locate|answer|determine|confirm)\b",
]
_AR_SELF = ["لم أجد", "لا أجد", "لم أتمكن من", "لا أتمكن من", "لم أستطع", "لا أستطيع العثور",
            "لا يمكنني العثور", "لا يمكنني إيجاد", "لم يمكنني العثور", "تعذر العثور", "تعذر علي العثور",
            "لم يتم العثور على معلومات", "لم يتم العثور على أي معلومات", "ليس لدي معلومات",
            "ليست لدي معلومات", "لا أملك معلومات", "لا تتوفر لدي", "لا أعرف", "لا أعلم"]
_AR_STATING = ["لم يرد", "لم ترد", "لا يرد", "لم يذكر", "لم تذكر", "لا تذكر", "لا يذكر", "لم يتم ذكر",
               "غير مذكور", "غير مذكورة", "لم يتم التطرق", "لا تنص", "لم تنص", "لا ينص", "لم ينص", "لا تشير",
               "لم تشر", "لا يشير", "لم يشر", "لم تتطرق", "لا تتطرق", "لم يتطرق", "لا يتطرق", "لا تتناول",
               "لم تتناول", "لا يتناول", "لم يتناول", "لا توضح", "لم توضح", "لا يوضح", "لم يوضح", "لا تحدد",
               "لم تحدد", "لا يحدد", "لم يحدد", "أي إشارة", "أي ذكر", "لا يوجد ذكر", "لا توجد إشارة"]
_AR_CONTAINING = ["لا تتضمن", "لا يتضمن", "لم تتضمن", "لم يتضمن", "لا تحتوي", "لا يحتوي", "لم تحتو", "لا تغطي",
                  "لا يغطي", "لا يوجد", "لا توجد", "لا تتوفر", "لا يتوفر", "غير متوفر", "غير متوفرة",
                  "ليست متوفرة", "ليس متوفرا", "غير موجود", "غير موجودة", "ليست موجودة", "لا يمكن العثور",
                  "لا يمكن إيجاد", "لم يمكن العثور", "لم يتم العثور"]
# document words: "معلومات", "تفاصيل", "ذكر" are what is missing, not where (as in English)
_AR_CORPUS_IN = ["وثائق", "وثيقة", "مستندات", "مستند", "نصوص", "سياق", "مصادر", "ملفات"]
_AR_CORPUS_WORD = ["نص", "ملف"]
_AR_POLICY_IN = ["سياسات", "سياسة"]
_AR_INFO = ["معلومات", "تفاصيل", "بيانات", "إشارة", "ذكر"]      # "لا تتضمن السياسات معلومات": a refusal
AR_WINDOW = 8                                                    # words either side of an Arabic cue


def _ar_doc(inside, words=()) -> re.Pattern:
    """A token containing one of `inside`, or one of `words` as a whole word (clitics allowed)."""
    alts = []
    if inside:
        alts.append("[^\\s.!?؟]*(?:" + _alt([norm(x) for x in inside]) + ")[^\\s.!?؟]*")
    if words:
        alts.append(_AR_HEAD + "(?:و|ف|ب|ل|ك)?(?:ال)?(?:" + _alt([norm(x) for x in words]) + ")" + _AR_TAIL)
    if not alts:
        raise ValueError("empty Arabic word list")
    return re.compile("|".join(alts))


def _ar_cues(cues) -> re.Pattern:
    return re.compile(_AR_HEAD + "(?:و|ف)?(?:" + _alt([norm(x) for x in cues]) + ")" + _AR_TAIL)


_AR_CORPUS_RX = _ar_doc(_AR_CORPUS_IN, _AR_CORPUS_WORD)
_AR_ANY_DOC_RX = _ar_doc(_AR_CORPUS_IN + _AR_POLICY_IN, _AR_CORPUS_WORD)
_AR_INFO_RX = _ar_doc((), _AR_INFO)
_AR_SELF_RX = _ar_cues(_AR_SELF)
_AR_STATING_RX = _ar_cues(_AR_STATING)
_AR_CONTAINING_RX = _ar_cues(_AR_CONTAINING)
REFUSAL_SELF = [re.compile(x) for x in _REFUSAL_EN_SELF] + [_AR_SELF_RX]
REFUSAL_EN_STATING = [re.compile(x) for x in _REFUSAL_EN_STATING]
REFUSAL_EN_CONTAINING = [re.compile(x) for x in _REFUSAL_EN_CONTAINING]
_SENTENCE = re.compile(r"[.!?;؟]\s")


def _ar_refusal(t: str) -> bool:
    """Arabic anchored cues: a stating verb with a document or policy word within AR_WINDOW words of
    the same sentence; a containing/existing verb with a corpus word, or with a policy word when
    its object (the next 4 words) is information itself ("لا تتضمن السياسات معلومات")."""
    for s in re.split(r"[.!?؟]", t):
        for rx, stating in ((_AR_STATING_RX, True), (_AR_CONTAINING_RX, False)):
            for m in rx.finditer(s):
                right = s[m.end():].split()
                window = " ".join(s[:m.start()].split()[-AR_WINDOW:] + right[:AR_WINDOW])
                if _AR_CORPUS_RX.search(window):
                    return True
                if (stating or _AR_INFO_RX.search(" ".join(right[:4]))) and _AR_ANY_DOC_RX.search(window):
                    return True
    return False

NO_MATCH = re.compile(r"ORA-20000\b.*no matching results", re.I | re.S)
# v1.5 (GO-3, 30-Sep): Select AI refuses an answer it cannot ground in the retrieved sources with
# ORA-20000 "Sorry, unfortunately the response ... was not generated using the sources of your data"
# (and, on some first attempts, "... a valid response was not generated ..."). That is a refusal
# by Select AI, scored like the no-match refusal, never an infra error.
DECLINED = re.compile(r"ORA-20000\b.*sorry, unfortunately (?:the response for your natural language prompt was not generated using the sources|a valid response was not generated)", re.I | re.S)
SCORED_STATUSES = ("ok", "no_match", "declined")
LONG_ANSWER = 1200
SHORT_ANSWER = 200          # v1.3: at or below this many scored characters a refusal cue always counts


def refusal_cue(text: str) -> bool:
    t = norm(text)
    if any(r.search(t) for r in REFUSAL_SELF) or _ar_refusal(t):
        return True
    for s in _SENTENCE.split(t):
        if DOC_CORPUS_EN.search(s) and any(r.search(s) for r in REFUSAL_EN_STATING + REFUSAL_EN_CONTAINING):
            return True
        if DOC_ANY_EN.search(s) and any(r.search(s) for r in REFUSAL_EN_STATING):
            return True
    return False


def answer_lang(text: str):
    """'ar' when Arabic letters outnumber Latin letters, 'en' otherwise, None with no letters."""
    ar = sum(1 for ch in text or "" if "؀" <= ch <= "ۿ" or "ﭐ" <= ch <= "﻿")
    la = sum(1 for ch in text or "" if ch.isascii() and ch.isalpha())
    if not ar and not la:
        return None
    return "ar" if ar > la else "en"


def _unique(terms):
    """Terms with the same normalised value once (the first spelling kept): '21' and '٢١'."""
    seen, out = set(), []
    for x in terms:
        if norm(x) not in seen:
            seen.add(norm(x))
            out.append(x)
    return out


def question_text(question: str) -> str:
    """The question as the echo rule sees it: scoring_text() without list markers, so number words
    are digits ('two days ago' -> '2 days ago') and grade codes are gone."""
    return scoring_text(question, list_markers=False)


def _drop_echo(alternatives, question_pre: str):
    """Question-echo rule: an alternative that already appears in the question proves nothing."""
    keep, echoed = [], []
    for x in alternatives:
        (echoed if _pattern(x, unit=False).search(question_pre) else keep).append(x)
    return keep, echoed


# ---------------------------------------------------------------------------------------------
# 7. verdicts
# ---------------------------------------------------------------------------------------------
VERDICTS = ("correct", "hedged", "contaminated", "false_refusal", "wrong")


def score_answer(answer, question: dict, *, no_match: bool = False,
                 corpus_nums: frozenset = frozenset()) -> dict:
    """Score one narrate answer against one gold question record.

    answerable: correct (all facts, no forbidden, no refusal cue) | hedged (refusal cue + all
                facts) | contaminated (a forbidden value; a context_ok value only when a fact is
                missing) | false_refusal (refusal cue or the no-match error without all facts) |
                wrong.
    unanswerable: correct (no-match error, or a refusal cue quoting no corpus number that is not
                already in the question) | hedged (refusal cue + such a number) | contaminated
                (a forbidden value) | wrong (no refusal).
    A refusal cue counts for an answerable question only when a fact is missing or the answer is
    at most SHORT_ANSWER characters; otherwise it is recorded and sent to audit.
    Infra errors are never passed here: they are never scored."""
    qtext = question.get("question", "")
    qpre = question_text(qtext)
    raw = answer or ""
    stripped = strip_sources(raw)
    an = norm(stripped)
    pre = scoring_text(stripped)
    answerable = bool(question.get("answerable", True))

    facts, echo = [], []
    for alt in question.get("facts") or []:
        keep, echoed = _drop_echo(alt, qpre)
        facts.append(keep)
        echo.extend(echoed)
    forbidden, f_echo = _drop_echo(question.get("forbidden") or [], qpre)
    echo.extend(f_echo)
    ctx_keys = {norm(x) for x in question.get("context_ok") or []}

    hit = [bool(k) and any(_pattern(x).search(pre) for x in k) for k in facts]
    all_facts = bool(hit) and all(hit)
    bad = _unique([f for f in forbidden if _pattern(f).search(pre)])       # "21" and "٢١" count once
    soft = [f for f in bad if norm(f) in ctx_keys]
    hard = [f for f in bad if norm(f) not in ctx_keys]
    cue = bool(an) and refusal_cue(an)
    short = len(stripped) <= SHORT_ANSWER
    cue_applied = cue and (not answerable or not all_facts or short)
    empty = not an and not no_match
    q_nums = numbers_in(qtext) | {canon_number(n) for n in _NUMBER.findall(qpre)}
    stray = sorted(values_in(pre) & set(corpus_nums) - q_nums) if not answerable else []
    n_words = number_words(stripped)

    if answerable:
        if no_match:
            verdict = "false_refusal"
        elif empty:
            verdict = "wrong"
        elif hard or (soft and not all_facts):
            verdict = "contaminated"
        elif cue_applied:
            verdict = "hedged" if all_facts else "false_refusal"
        else:
            verdict = "correct" if all_facts else "wrong"
    else:
        if no_match:
            verdict = "correct"
        elif bad:
            verdict = "contaminated"
        elif cue_applied:
            verdict = "hedged" if stray else "correct"
        else:
            verdict = "wrong"

    over = len(stripped) > LONG_ANSWER
    lang = answer_lang(stripped)
    reasons = [name for name, on in (
        ("verdict_" + verdict, verdict in ("hedged", "false_refusal")),
        ("contaminated", verdict == "contaminated"),
        ("context_value", bool(soft) and verdict != "contaminated"),
        ("refusal_cue", cue),
        ("refusal_cue_ignored", cue and not cue_applied),
        ("over_1200", over),
        ("empty", empty),
        ("question_echo", bool(echo)),
        ("corpus_number", bool(stray)),
        ("number_word", answerable and not all_facts and n_words > 0 and not no_match),
        ("unanswerable_wrong", not answerable and verdict == "wrong"),
    ) if on]
    return {
        "verdict": verdict,
        "correct": verdict == "correct",
        "facts_found": f"{sum(hit)}/{len(hit)}",
        "forbidden_hits": bad,
        "context_hits": soft,
        "refusal_cue": cue,
        "refusal_cue_applied": cue_applied,
        "no_match": bool(no_match),
        "empty": empty,
        "echo_terms": echo,
        "corpus_numbers_quoted": stray,
        "number_words": n_words,
        "chars_raw": len(raw),
        "chars_scored": len(stripped),
        "over_1200": over,
        "answer_lang": lang,
        "answer_lang_matches_q_lang": (lang == question.get("q_lang")) if lang else None,
        "audit": bool(reasons),
        "audit_reasons": reasons,
    }


def score(answer, facts, forbidden=(), answerable=True) -> dict:
    """v1.1 compatibility wrapper (prototype API). An ORA-20000 no-match text counts as the
    no-match refusal; any other ORA- text is an infra error and must not be scored."""
    no_match = bool(answer) and bool(NO_MATCH.search(answer))
    if answer and answer.lstrip().startswith("ORA-") and not no_match:
        raise ValueError("infra error text must not be scored")
    r = score_answer(None if no_match else answer,
                     {"question": "", "facts": facts, "forbidden": list(forbidden),
                      "answerable": answerable}, no_match=no_match)
    r["contaminated"] = bool(r["forbidden_hits"])
    r["refused"] = r["refusal_cue"] or r["no_match"]
    return r
