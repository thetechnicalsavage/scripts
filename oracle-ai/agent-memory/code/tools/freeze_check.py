#!/usr/bin/env python3
# v1.2 2026-10-10  Brief 12 freeze manifest (PLAN.md 7.0): writes or verifies data/frozen/FREEZE.sha256, the sha256 of
#                  every frozen file plus PLAN.md section 7. run_all.sh calls --verify before S4, S5 and S7.
#                  v1.2: Codex re-review: --verify --manifest-sha256 HEX also pins the manifest's own sha256 (the
#                  value recorded in STATUS.md at the freeze; run_all.sh passes it), so an edited manifest fails.
#                  v1.1: Codex review: the manifest must list exactly the expected paths once each, in order, with
#                  well-formed lines; section 7 is hashed as raw bytes (CRLF kept) and its markers are top-level
#                  headings outside fenced code blocks.
#                  v1.0: first version (day 0).
"""Write or verify the freeze manifest.

Run as : the authoring host or the demo VM; no database, no network.
Usage  : python3 tools/freeze_check.py --write     (only at the freeze; refuses to overwrite an existing manifest)
         python3 tools/freeze_check.py --verify [--manifest-sha256 HEX]
                   (exit 0 all equal, 1 any difference or missing file; with HEX, the manifest file itself must have
                   that sha256, the value recorded in STATUS.md at the freeze, which run_all.sh passes)
Paths in the manifest are relative to code/. PLAN.md section 7 is hashed as the UTF-8 text from the line starting
"## 7." up to, not including, the line starting "## 8." (PLAN 7.0), and listed as "PLAN.md#section-7". Both markers
must be top-level headings ("## 7. ", "## 8. ") outside fenced code blocks; the slice is hashed byte for byte.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
import time
from pathlib import Path

LOG = logging.getLogger("freeze_check")
CODE = Path(__file__).resolve().parents[1]
MANIFEST = CODE / "data" / "frozen" / "FREEZE.sha256"
PLAN = CODE.parent / "PLAN.md"
SECTION = "PLAN.md#section-7"
FROZEN = (
    "data/gen_retrieval_corpus.py", "data/gen_conversations.py", "data/make_bill_images.py",
    "py/lib/hero_seed.py", "py/lib/matcher.py", "py/lib/stats.py",
    "data/frozen/c1_corpus.jsonl", "data/frozen/c1_queries.jsonl", "data/frozen/c3_queries.jsonl",
    "data/frozen/c2_threads.jsonl", "data/frozen/c2_slots.jsonl", "data/frozen/c2_links.jsonl",
    "data/frozen/c2_distractors.jsonl", "data/frozen/bill_images.json",
    "data/frozen/images/bill_tkt48213.png", "data/frozen/images/activation_rmg_gcc_7d.png",
    "data/frozen/images/credit_note_tkt48213.png", "data/frozen/images/quote_pln_fam_4.png",
    "data/frozen/images/vision_check_64.png",
)


_H7 = re.compile(r"## 7\. ")
_H8 = re.compile(r"## 8\. ")
_LINE = re.compile(r"([0-9a-f]{64})  (\S.*)")


def section7(plan_text: str) -> str:
    """Section 7 text, line endings kept. Headings inside fenced code blocks are ignored."""
    lines = plan_text.splitlines(keepends=True)
    start = end = None
    fenced = False
    for i, x in enumerate(lines):
        if x.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if start is None and _H7.match(x):
            start = i
        elif start is not None and _H8.match(x):
            end = i
            break
    if start is None:
        raise ValueError("PLAN.md has no top-level '## 7. ' heading")
    if end is None:
        raise ValueError("PLAN.md has no top-level '## 8. ' heading after section 7")
    return "".join(lines[start:end])


def section7_digest(plan: Path) -> str:
    text = plan.read_bytes().decode("utf-8")          # no newline translation: CRLF changes the hash
    return hashlib.sha256(section7(text).encode("utf-8")).hexdigest()


def current(code: Path = CODE, plan: Path = PLAN) -> dict[str, str]:
    out = {}
    for rel in FROZEN:
        p = code / rel
        if not p.is_file():
            raise FileNotFoundError(rel)
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    out[SECTION] = section7_digest(plan)
    return out


def render(digests: dict[str, str]) -> str:
    head = ("# v1.0 Brief 12 freeze manifest (PLAN.md 7.0), written by tools/freeze_check.py --write.\n"
            "# sha256, two spaces, path relative to code/ (PLAN.md#section-7: the section 7 text, see the tool).\n")
    return head + "".join(f"{digests[k]}  {k}\n" for k in list(FROZEN) + [SECTION])


def parse(text: str) -> list[tuple[str, str]]:
    """(path, digest) in file order; raises ValueError on a malformed line."""
    out = []
    for n, line in enumerate(text.split("\n"), start=1):
        if not line or line.startswith("#"):
            continue
        m = _LINE.fullmatch(line)
        if not m:
            raise ValueError(f"malformed manifest line {n}")
        out.append((m.group(2), m.group(1)))
    return out


def verify(manifest: Path = MANIFEST, code: Path = CODE, plan: Path = PLAN,
           manifest_sha256: str | None = None) -> list[str]:
    """Names of entries that differ, are missing on disk, or are missing from the manifest. With manifest_sha256, a
    manifest whose own sha256 differs (edited lines, comments or digests) fails first."""
    if not manifest.is_file():
        return ["<manifest missing>"]
    if manifest_sha256 is not None and hashlib.sha256(manifest.read_bytes()).hexdigest() != manifest_sha256.lower():
        return ["<manifest sha256 differs from the pinned value>"]
    try:
        entries = parse(manifest.read_bytes().decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return ["<manifest malformed>"]
    expected = list(FROZEN) + [SECTION]
    if [rel for rel, _ in entries] != expected:
        return ["<manifest paths differ from the frozen list>"]
    want = dict(entries)
    bad = []
    for rel in expected:
        try:
            have = section7_digest(plan) if rel == SECTION else hashlib.sha256((code / rel).read_bytes()).hexdigest()
        except (ValueError, OSError):
            bad.append(rel)
            continue
        if want[rel] != have:
            bad.append(rel)
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--verify", action="store_true")
    ap.add_argument("--manifest-sha256", metavar="HEX", help="pinned sha256 of FREEZE.sha256 (with --verify)")
    args = ap.parse_args(argv)
    if args.manifest_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", args.manifest_sha256):
        ap.error("--manifest-sha256 needs 64 hex characters")
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    if args.write:
        if MANIFEST.exists():
            LOG.error("%s exists: the freeze is written once; a change needs a new plan version", MANIFEST.name)
            return 1
        MANIFEST.write_text(render(current()), encoding="ascii")
        LOG.info("wrote %s with %d entries", MANIFEST.name, len(FROZEN) + 1)
        return 0
    bad = verify(manifest_sha256=args.manifest_sha256)
    if bad:
        LOG.error("freeze check FAILED: %s", ", ".join(bad))
        return 1
    LOG.info("freeze check ok: %d entries equal", len(FROZEN) + 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
