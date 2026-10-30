"""
fic-archive/assets/add_tracking.py
==================================
One-time backfill: injects the Pendo tracking snippet (PENDO_SNIPPET from
phase4_process.py) into the <head> of every existing served HTML page.
New files get it automatically from phase4_process.py.

Usage (from project root):
    python assets/add_tracking.py

Covers archive/, assets/hiddenFics/ and the root pages (index.html, rank.html,
...). originals/ is skipped — those are untouched backups.
Safe to re-run: files that already contain the snippet are left alone.
"""

import re
from pathlib import Path

from phase4_process import BASE_DIR, PENDO_SNIPPET

APP_ID = "2238e862-9499-45dd-85a4-23c98c0f6d92"
HEAD_CLOSE = re.compile(r"</head\s*>", re.IGNORECASE)


def target_files():
    yield from sorted(BASE_DIR.glob("*.html"))
    yield from sorted((BASE_DIR / "archive").glob("*.html"))
    yield from sorted((BASE_DIR / "assets" / "hiddenFics").glob("*.html"))


def main():
    added = skipped = no_head = 0
    for path in target_files():
        html = path.read_text(encoding="utf-8")
        if APP_ID in html:
            skipped += 1
            continue
        match = HEAD_CLOSE.search(html)
        if not match:
            print(f"  no </head>, skipped: {path.relative_to(BASE_DIR)}")
            no_head += 1
            continue
        html = html[:match.start()] + PENDO_SNIPPET + "\n" + html[match.start():]
        path.write_text(html, encoding="utf-8")
        added += 1
    print(f"Tracking added: {added}  already present: {skipped}  no <head>: {no_head}")


if __name__ == "__main__":
    main()
