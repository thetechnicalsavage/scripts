#!/usr/bin/env python3
# v1.2 - typeset a VERBATIM terminal transcript as a terminal-window PNG for the blog.
#
#        v1.1: every line is its own block (no stray blank line after a highlight);
#              caption height fixed.
#        v1.2: window width follows the longest line (1100-1560 px) unless --width is given.
# The text is not edited; only presentation is added. Optional line markers, which are
# stripped before rendering, highlight lines:
#   "!! " at the start of a line -> red tint   (what is wrong)
#   "++ " at the start of a line -> green tint (what is right)
#   "## " at the start of a line -> dim heading line inside the window
# Usage: render_transcript.py --in t.txt --title "SQL*Plus - NL2SQL_LAB" --out shot.png
#                             [--caption "text under the window"] [--width 1100]
# Needs a Chromium / headless-shell binary (CHROME env var, or Playwright's cache).
import argparse
import glob
import html
import logging
import math
import os
import subprocess
import sys
import tempfile

log = logging.getLogger("render_transcript")

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#eef1f5;padding:18px;font-family:'DejaVu Sans Mono','Noto Sans Mono',monospace}
.win{background:#1f2430;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.25);overflow:hidden}
.bar{background:#2b3140;height:34px;display:flex;align-items:center;padding:0 12px;position:relative}
.dot{width:12px;height:12px;border-radius:50%;margin-right:8px}
.t{position:absolute;left:0;right:0;text-align:center;color:#aab2c5;font:13px 'DejaVu Sans',sans-serif}
pre{color:#d8dee9;font-size:14px;line-height:20px;padding:16px 20px;white-space:pre-wrap;word-break:break-all}
.p{color:#88c0d0}.c{color:#7b8496}.e{color:#ff8a8a}
.l{display:block;min-height:20px;margin:0 -20px;padding:0 20px;border-left:3px solid transparent}
.bad{background:rgba(255,90,90,.18);border-left-color:#ff6b6b}
.good{background:rgba(120,220,140,.16);border-left-color:#6bdc8b}
.h{color:#ebcb8b}
.cap{font:13px/1.5 'DejaVu Sans',sans-serif;color:#4a5568;padding:10px 4px 0}
"""


def chrome_binary() -> str:
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    for pat in ("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
                "~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome"):
        hits = sorted(glob.glob(os.path.expanduser(pat)))
        if hits:
            return hits[-1]
    raise SystemExit("no Chromium found: set CHROME=/path/to/chrome")


def line_html(line: str) -> str:
    cls = None
    if line.startswith("!! "):
        cls, line = "bad", line[3:]
    elif line.startswith("++ "):
        cls, line = "good", line[3:]
    elif line.startswith("## "):
        return f'<span class="l h">{html.escape(line[3:])}</span>'
    esc = html.escape(line)
    s = line.lstrip()
    if s.startswith(("SQL>", "SQL> ")):
        esc = f'<span class="p">{esc}</span>'
    elif s.startswith("--"):
        esc = f'<span class="c">{esc}</span>'
    elif "ORA-" in line or "PLS-" in line or s.startswith("ERROR"):
        esc = f'<span class="e">{esc}</span>'
    return f'<span class="l {cls or ""}">{esc}</span>'


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--title", default="SQL*Plus")
    ap.add_argument("--out", required=True)
    ap.add_argument("--caption", default="")
    ap.add_argument("--width", type=int, default=0, help="css px; 0 = fit the longest line")
    a = ap.parse_args()

    text = open(a.src, encoding="utf-8").read().rstrip("\n").expandtabs(8)
    lines = text.split("\n")
    if not a.width:
        longest = max((len(ln[3:] if ln[:3] in ("!! ", "++ ", "## ") else ln) for ln in lines), default=80)
        a.width = int(min(1560, max(1100, longest * 8.43 + 80)))
    body = "".join(line_html(ln) for ln in lines)
    cap = f'<div class="cap">{html.escape(a.caption)}</div>' if a.caption else ""
    doc = (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
           f"<div class='win'><div class='bar'><div class='dot' style='background:#ff5f56'></div>"
           f"<div class='dot' style='background:#ffbd2e'></div><div class='dot' style='background:#27c93f'></div>"
           f"<div class='t'>{html.escape(a.title)}</div></div><pre>{body}</pre></div>{cap}</body></html>")

    # height: wrapped visual lines at ~8.43 px per character of 14 px DejaVu Sans Mono
    cols = max(40, int((a.width - 36 - 40) / 8.43))
    visual = sum(max(1, math.ceil(len(ln.replace("!! ", "").replace("++ ", "")) / cols)) for ln in lines)
    cap_lines = math.ceil(len(a.caption) / max(40, int(a.width / 7.2))) if a.caption else 0
    height = 36 + 34 + 32 + visual * 20 + (cap_lines * 22 + 26 if cap_lines else 0) + 10

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(doc)
        page = f.name
    try:
        r = subprocess.run([chrome_binary(), "--headless", "--disable-gpu", "--hide-scrollbars",
                            "--force-device-scale-factor=2", f"--window-size={a.width},{height}",
                            f"--screenshot={os.path.abspath(a.out)}", f"file://{page}"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not os.path.exists(a.out):
            log.error("chromium failed: %s", r.stderr[-500:])
            return 1
    finally:
        os.unlink(page)
    log.info("wrote %s (%dx%d css px)", a.out, a.width, height)
    return 0


if __name__ == "__main__":
    sys.exit(main())
