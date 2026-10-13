#!/usr/bin/env python3
# v1.0 2026-10-10  Brief 12 synthetic images (PLAN.md section 3, steps 0 and 6): four bill-like PNGs for the hero
#                  customer CUST_1001 with the descriptions we write and four questions labelled with their gold image,
#                  plus the 64x64 synthetic PNG for step 0's vision check. Pillow (pinned), deterministic; frozen in 7.0.
#                  v1.0: first version (day 0, no database, no LLM).
"""Draw data/frozen/images/*.png and write data/frozen/bill_images.json.

Run as : the authoring host or the demo VM; no database, no network.
Usage  : python3 data/make_bill_images.py [--out DIR] [--check]
Re-run : safe; same Pillow version, same bytes (atomic replace). The frozen PNGs, not a re-run on another host, are
         what step 6 uploads; their sha256 is in data/frozen/FREEZE.sha256.

Every operator, customer, code and amount is fictional; each image says "synthetic" on it. No phone number, e-mail
address, person name or real brand appears.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LOG = logging.getLogger("make_bill_images")
OUT_DEFAULT = Path(__file__).resolve().parent / "frozen"
W, H = 720, 900
INK, MUTED, RULE, ALERT, PAPER = (20, 24, 31), (90, 98, 110), (200, 205, 212), (178, 34, 34), (255, 255, 255)
FOOTER = "Synthetic data for a demo. Fictional operator. Not a real bill."

IMAGES = (
    {
        "image_id": "cust1001_img_bill_sep",
        "file": "bill_tkt48213.png",
        "title": "Monthly bill - September 2026",
        "meta": ("Customer: CUST_1001", "Line: 1", "Bill date: 15 Sep 2026"),
        "rows": (("Plan monthly fee", "QAR 180.00", False),
                 ("Roaming add-on RMG-GCC-7D (03 Sep)", "QAR 35.00", False),
                 ("Roaming add-on RMG-GCC-7D (03 Sep)", "QAR 35.00", True),
                 ("Total due", "QAR 250.00", False)),
        "note": "Duplicate charge under review: ticket TKT-48213",
        "description": "Synthetic September 2026 bill for CUST_1001 showing the roaming add-on RMG-GCC-7D charged "
                       "twice; the duplicate is under review in ticket TKT-48213.",
    },
    {
        "image_id": "cust1001_img_activation",
        "file": "activation_rmg_gcc_7d.png",
        "title": "Add-on activation record",
        "meta": ("Customer: CUST_1001", "Line: 1", "Record printed: 18 Sep 2026"),
        "rows": (("Add-on", "RMG-GCC-7D", False), ("Channel", "SMS", False),
                 ("Activated", "03 Sep 2026 21:14", True), ("Status", "active", False)),
        "note": "Activation requested from the customer's own line",
        "description": "Synthetic activation record for CUST_1001: the roaming add-on RMG-GCC-7D was activated by SMS "
                       "on 03 Sep 2026.",
    },
    {
        "image_id": "cust1001_img_credit_note",
        "file": "credit_note_tkt48213.png",
        "title": "Credit note",
        "meta": ("Customer: CUST_1001", "Reference: ticket TKT-48213", "Date: 23 Sep 2026"),
        "rows": (("Refund of one RMG-GCC-7D charge", "QAR -35.00", True), ("Applied to", "next bill", False)),
        "note": "Duplicate roaming charge refunded",
        "description": "Synthetic credit note for CUST_1001 refunding one duplicate RMG-GCC-7D charge after ticket "
                       "TKT-48213.",
    },
    {
        "image_id": "cust1001_img_quote_family",
        "file": "quote_pln_fam_4.png",
        "title": "Price quote",
        "meta": ("Customer: CUST_1001", "Quote date: 02 Oct 2026", "Valid until: 31 Oct 2026"),
        "rows": (("Family plan PLN-FAM-4", "4 lines", False), ("Shared data", "30 GB", False),
                 ("Monthly price", "QAR 320.00", True)),
        "note": "Move from the current plan at the next bill cycle",
        "description": "Synthetic price quote for CUST_1001's move to the family plan PLN-FAM-4 with four lines.",
    },
)
# Step 6: search(..., record_types=['image']) for each question; gold by construction.
QUESTIONS = (
    {"qid": "IMG-1", "text": "Show me the bill where the roaming add-on was charged twice.",
     "gold_image_id": "cust1001_img_bill_sep"},
    {"qid": "IMG-2", "text": "Is there a record of how the roaming add-on was switched on?",
     "gold_image_id": "cust1001_img_activation"},
    {"qid": "IMG-3", "text": "Which document refunded the duplicate roaming charge?",
     "gold_image_id": "cust1001_img_credit_note"},
    {"qid": "IMG-4", "text": "Show the price quote for the family plan.", "gold_image_id": "cust1001_img_quote_family"},
)
VISION_CHECK = {"file": "vision_check_64.png", "size": 64,
                "expected": "a white circle on a blue background (any wording that says so passes)"}


def _font(size: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.load_default(size=size)
    if not isinstance(font, ImageFont.FreeTypeFont):
        raise RuntimeError("Pillow was built without FreeType; the frozen images need its bundled font")
    return font


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=9)
    return buf.getvalue()


def draw_document(spec: dict) -> bytes:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    big, mid, small = _font(30), _font(22), _font(17)
    d.rectangle((0, 0, W, 86), fill=(232, 236, 241))
    d.text((32, 16), "FICTIONAL MOBILE OPERATOR", font=mid, fill=MUTED)
    d.text((32, 46), "SYNTHETIC SAMPLE", font=small, fill=ALERT)
    d.text((32, 112), spec["title"], font=big, fill=INK)
    y = 166
    for line in spec["meta"]:
        d.text((32, y), line, font=mid, fill=INK)
        y += 32
    y += 16
    d.line((32, y, W - 32, y), fill=RULE, width=2)
    y += 18
    for label, value, highlight in spec["rows"]:
        if highlight:
            d.rectangle((24, y - 6, W - 24, y + 30), outline=ALERT, width=3)
        d.text((40, y), label, font=mid, fill=INK)
        tw = d.textlength(value, font=mid)
        d.text((W - 40 - tw, y), value, font=mid, fill=ALERT if highlight else INK)
        y += 52
    d.line((32, y, W - 32, y), fill=RULE, width=2)
    y += 26
    d.text((32, y), spec["note"], font=mid, fill=ALERT)
    d.text((32, H - 48), FOOTER, font=small, fill=MUTED)
    return _png(img)


def draw_vision_check() -> bytes:
    n = VISION_CHECK["size"]
    img = Image.new("RGB", (n, n), (31, 58, 147))
    ImageDraw.Draw(img).ellipse((12, 12, n - 13, n - 13), fill=(255, 255, 255))
    return _png(img)


def manifest() -> bytes:
    data = {
        "user_id": "CUST_1001",
        "images": [{"image_id": s["image_id"], "file": f"images/{s['file']}", "mime_type": "image/png",
                    "description": s["description"]} for s in IMAGES],
        "questions": list(QUESTIONS),
        "vision_check": {"file": f"images/{VISION_CHECK['file']}", "mime_type": "image/png",
                         "expected": VISION_CHECK["expected"]},
    }
    return (json.dumps(data, sort_keys=True, indent=1, ensure_ascii=True) + "\n").encode("ascii")


def build() -> dict[str, bytes]:
    """Relative path under the output directory -> file bytes."""
    ids = [s["image_id"] for s in IMAGES]
    if len(set(ids)) != len(ids) or any(q["gold_image_id"] not in ids for q in QUESTIONS):
        raise ValueError("image ids must be unique and every question needs an existing gold image")
    out = {f"images/{s['file']}": draw_document(s) for s in IMAGES}
    out[f"images/{VISION_CHECK['file']}"] = draw_vision_check()
    out["bill_images.json"] = manifest()
    return out


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
    files = build()
    if args.check:
        bad = [f for f, b in files.items() if not (args.out / f).is_file() or (args.out / f).read_bytes() != b]
        if bad:
            LOG.error("check failed: %s differ from a fresh drawing", ", ".join(bad))
            return 1
        LOG.info("check ok: %d files identical to a fresh drawing", len(files))
        return 0
    for rel, b in files.items():
        atomic_write(args.out / rel, b)
        LOG.info("wrote %s (%d bytes)", rel, len(b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
