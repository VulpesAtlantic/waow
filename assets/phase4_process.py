"""
fic-archive/assets/phase4_process.py
====================================
Personal AO3 fic archive processor.

Usage (from project root):
    python assets/phase4_process.py

What it does:
  1. Scans staging/ for .html files
  2. Backs up each original to originals/
  3. Parses AO3 HTML: extracts title, author, ship, rating, status, summary, lastUpdated, word_count
  4. Strips inline styles, rewrites stylesheet link to ../assets/darkMode.css
      (no tracking script is injected)
  5. Injects/updates <meta> tags for all extracted fields
  6. If a file with the same workID already exists in archive/, keeps whichever has the higher word count
  7. Writes cleaned file to archive/ using the staged filename (no renaming).
      A file staged in a sub-folder keeps it: staging/Starmora/x.html -> archive/Starmora/x.html.
      The index's ship buttons show exactly the fics inside the matching archive/ sub-folder.
  8. Updates fic_data.json manifest
  9. Rebuilds index.html from manifest
  10. Logs all actions to process.log; warnings/errors also printed to terminal

Folder structure expected:
  fic-archive/
  ├── staging/        ← drop new AO3 downloads here (named workID_slug.html)
  ├── archive/        ← processed fics (live)
  ├── originals/      ← untouched backups of downloads
  ├── assets/
  │   ├── darkMode.css
  │   └── phase4_process.py  ← this script
  ├── index.html      ← auto-generated, do not edit manually
  ├── fic_data.json   ← metadata manifest, do not edit manually
  └── process.log

Date & status extraction:
  All dates and status are read from the AO3 stats block inside the HTML.
  - lastUpdated: uses Completed date if present, else Updated, else Published
  - status: "Complete" if a Completed date exists; otherwise "In Progress"

Custom tags & summaries (hand-edited, survive reprocessing):
  - assets/tags_review_custom.csv — custom_tags column, pipe-delimited. A fic
    with custom tags uses exactly those; a fic without gets auto tags mapped
    from its AO3 tags via assets/tag_mappings.json (see tag_mapper.py).
  - assets/custom_summaries.csv  — fill in custom_summary to replace the AO3
    summary on the index; leave blank to use AO3's. The file is refreshed with
    every fic after each run (your custom_summary text is kept).
  Both are matched to fics by workID, so renamed files keep their overrides.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import shutil
import sys
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from bs4 import BeautifulSoup, Comment

from tag_mapper import auto_tags, norm

# ---------------------------------------------------------------------------
# Tracking  (removed: PENDO_SNIPPET is intentionally empty)
# ---------------------------------------------------------------------------

PENDO_SNIPPET = ""  # tracking removed; name kept so add_tracking.py still imports

KUDOS_SCRIPT_SRC = "../assets/kudos.js"

# ---------------------------------------------------------------------------
# Paths  (process.py lives in assets/, project root is one level up)
# ---------------------------------------------------------------------------

BASE_DIR      = Path(__file__).resolve().parent.parent
STAGING_DIR   = BASE_DIR / "staging"
ARCHIVE_DIR   = BASE_DIR / "archive"
ORIGINALS_DIR = BASE_DIR / "originals"
ASSETS_DIR    = BASE_DIR / "assets"
INDEX_FILE    = BASE_DIR / "index.html"
DATA_FILE     = BASE_DIR / "fic_data.json"
LOG_FILE      = BASE_DIR / "process.log"
LOCK_FILE     = BASE_DIR / ".phase4.lock"
CUSTOM_TAGS_FILE      = ASSETS_DIR / "tags_review_custom.csv"
CUSTOM_SUMMARIES_FILE = ASSETS_DIR / "custom_summaries.csv"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging():
    logger = logging.getLogger("fic_archive")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

log = setup_logging()

# ---------------------------------------------------------------------------
# CUSTOM TAGS
# ---------------------------------------------------------------------------

def _read_override_csv(path: Path) -> dict:
    """Load an override CSV into {workID: row}. Missing file → empty dict."""
    rows = {}
    if not path.exists():
        return rows
    # utf-8-sig tolerates the BOM Excel adds when saving
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            fname = (row.get("filename") or "").strip()
            if fname:
                rows[_work_id(fname) or fname] = row
    return rows

@lru_cache(maxsize=None)
def _custom_tag_overrides() -> dict:
    """{workID: [tag, ...]} from tags_review_custom.csv (pipe-delimited column)."""
    out = {}
    for key, row in _read_override_csv(CUSTOM_TAGS_FILE).items():
        raw = (row.get("custom_tags") or "").strip()
        out[key] = [norm(t) for t in raw.split("|") if t.strip()]
    return out

@lru_cache(maxsize=None)
def _custom_summary_overrides() -> dict:
    """{workID: summary} for rows with a non-blank custom_summary."""
    out = {}
    for key, row in _read_override_csv(CUSTOM_SUMMARIES_FILE).items():
        text = re.sub(r"\s+", " ", row.get("custom_summary") or "").strip()
        if text:
            out[key] = text
    return out

def apply_custom_summary(meta: dict) -> str:
    """Return the hand-written summary for this fic if there is one, else the AO3 summary."""
    fname = meta["source_file"]
    return _custom_summary_overrides().get(_work_id(fname) or fname, meta["summary"])

def sync_custom_summaries_csv(manifest: dict):
    """
    Rewrite custom_summaries.csv so it lists every fic in the manifest with its
    current AO3 summary. The custom_summary column is carried over untouched;
    rows for fics no longer in the manifest are kept at the end so no
    hand-written text is ever dropped.
    """
    existing = _read_override_csv(CUSTOM_SUMMARIES_FILE)
    fieldnames = ["filename", "title", "summary_words", "ao3_summary", "custom_summary"]
    rows = []
    for fic in sorted(manifest["fics"], key=lambda f: f["filename"]):
        key = _work_id(fic["filename"]) or fic["filename"]
        ao3 = fic.get("ao3_summary", fic.get("summary", ""))
        rows.append({
            "filename":       fic["filename"],
            "title":          fic["title"],
            "summary_words":  len(ao3.split()),
            "ao3_summary":    ao3,
            "custom_summary": (existing.pop(key, {}).get("custom_summary") or "").strip(),
        })
    for row in existing.values():
        if (row.get("custom_summary") or "").strip():
            rows.append({k: row.get(k, "") for k in fieldnames})

    tmp = CUSTOM_SUMMARIES_FILE.with_name(CUSTOM_SUMMARIES_FILE.name + f".tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, CUSTOM_SUMMARIES_FILE)
    except PermissionError:
        log.warning("custom_summaries.csv is open in another program — not refreshed this run")
    finally:
        if tmp.exists():
            tmp.unlink()

def apply_custom_tags(meta: dict) -> list:
    """Use the original AO3 tags directly; no repo-specific tag mapping or sorting.

    This keeps every original additional tag visible on the archive index rather
    than replacing them with a smaller mapped/custom tag set.
    """
    tags = []
    seen = set()
    for tag in meta.get("ao3_tags", []):
        cleaned = (tag or "").strip()
        if not cleaned:
            continue
        key = norm(cleaned)
        if key in seen:
            continue
        seen.add(key)
        tags.append(cleaned)
    return tags

# ---------------------------------------------------------------------------
# Ship priority
# ---------------------------------------------------------------------------
# Ships are matched case-insensitively against each fic's relationship list.
# First match wins; if none match, the first relationship in the file is used.

SHIP_PRIORITY = [
    "Jennifer Barkley/April Ludgate",
    "Eve Fletcher/Riley Johnson",
    "Agatha Harkness/Rio Vidal",
]

# ... rest of file unchanged below ...
