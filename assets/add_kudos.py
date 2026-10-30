"""
fic-archive/assets/add_kudos.py
===============================
One-time backfill: injects the kudos reminder script (assets/kudos.js) into the
<head> of every existing fic page. New files get it automatically from
phase4_process.py.

Usage (from project root):
    python assets/add_kudos.py

Covers archive/ and assets/hiddenFics/. originals/ is skipped — those are
untouched backups. Safe to re-run: files that already have it are left alone.
"""

import re
from pathlib import Path

from phase4_process import BASE_DIR, KUDOS_SCRIPT_SRC

SCRIPT_TAG = f'<script defer src="{KUDOS_SCRIPT_SRC}"></script>'
HEAD_CLOSE = re.compile(r"</head\s*>", re.IGNORECASE)


def target_files():
    yield from sorted((BASE_DIR / "archive").glob("*.html"))
    yield from sorted((BASE_DIR / "assets" / "hiddenFics").glob("*.html"))


def main():
    added = skipped = no_head = 0
    for path in target_files():
        html = path.read_text(encoding="utf-8")
        if KUDOS_SCRIPT_SRC in html:
            skipped += 1
            continue
        match = HEAD_CLOSE.search(html)
        if not match:
            print(f"  no </head>, skipped: {path.relative_to(BASE_DIR)}")
            no_head += 1
            continue
        html = html[:match.start()] + SCRIPT_TAG + "\n" + html[match.start():]
        path.write_text(html, encoding="utf-8")
        added += 1
    print(f"Kudos script added: {added}  already present: {skipped}  no <head>: {no_head}")


if __name__ == "__main__":
    main()
