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
  5. Injects/updates <meta> tags for all extracted fields
  6. If a file with the same workID already exists in archive/, keeps whichever has the higher word count
  7. Writes cleaned file to archive/ using the staged filename (no renaming)
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
    """Hand-written tags from tags_review_custom.csv win outright; otherwise map the AO3 tags."""
    fname = meta["source_file"]
    override = _custom_tag_overrides().get(_work_id(fname) or fname)
    if override:
        return override
    return auto_tags(meta["ao3_tags"])

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

def _pick_ship(ship_tag) -> str:
    """Return the highest-priority ship from a relationship <dd> tag."""
    if not ship_tag:
        return ""
    links = [a.get_text(strip=True) for a in ship_tag.find_all("a")]
    if not links:
        links = [s.strip() for s in ship_tag.get_text(",").split(",") if s.strip()]
    if not links:
        return ""
    links_lower = [l.lower() for l in links]
    for canonical in SHIP_PRIORITY:
        if canonical.lower() in links_lower:
            return canonical
    return links[0]

# ---------------------------------------------------------------------------
# AO3 HTML parsing
# ---------------------------------------------------------------------------

def _extract_stats_text(soup: BeautifulSoup) -> str:
    """Return the text of the AO3 stats <dd> block, or empty string."""
    for dt in soup.find_all("dt"):
        if "Stats" in dt.get_text():
            dd = dt.find_next_sibling("dd")
            if dd:
                return dd.get_text()
    return ""

def extract_metadata(soup: BeautifulSoup, source_filename: str) -> dict:
    """
    Extract fic metadata from an AO3-downloaded HTML file.
    Returns a dict with keys: title, author, ship, rating, status,
    summary, lastUpdated, ao3_tags, word_count.
    All values default to empty string if not found.
    """
    meta = {
        "title":       "",
        "author":      "",
        "ship":        "",
        "rating":      "",
        "status":      "",
        "summary":     "",
        "lastUpdated": "",
        "ao3_tags":    [],
        "word_count":  "",
        "source_file": source_filename,
    }

    # --- Title & Author from <title> tag ---
    # AO3 format: "Title - Author - Fandom [Archive of Our Own]"
    title_tag = soup.find("title")
    if title_tag:
        raw_title = title_tag.get_text(strip=True)
        raw_title = re.sub(r"\s*[-–]\s*Archive of Our Own.*$", "", raw_title, flags=re.IGNORECASE)
        parts = [p.strip() for p in re.split(r"\s+-\s+", raw_title)]
        if len(parts) >= 2:
            meta["title"]  = parts[0]
            meta["author"] = parts[1]
        elif len(parts) == 1:
            meta["title"] = parts[0]
            log.warning(f"{source_filename}: could not parse author from <title>: '{raw_title}'")
        else:
            log.warning(f"{source_filename}: empty <title> tag")

    # --- Author fallback 1: byline in the work header ---
    if not meta["author"]:
        byline = soup.select_one(".byline a[rel='author']")
        if byline:
            meta["author"] = byline.get_text(strip=True)

    # --- Author fallback 2: legacy <meta name="author"> written by old scripts ---
    if not meta["author"]:
        legacy = soup.find("meta", attrs={"name": "author"})
        if legacy and legacy.get("content", "").strip():
            meta["author"] = legacy["content"].strip()

    # --- Rating ---
    rating_tag = soup.select_one("dd.rating")
    if not rating_tag:
        for dt in soup.find_all("dt"):
            if dt.get_text(strip=True).lower() == "rating:":
                rating_tag = dt.find_next_sibling("dd")
                if rating_tag:
                    break
    if rating_tag:
        meta["rating"] = rating_tag.get_text(strip=True)

    # --- Ship (Relationships) ---
    ship_tag = soup.select_one("dd.relationship")
    # Fallback: find the <dd> after a <dt> containing "Relationship"
    # (some AO3 downloads omit the class attribute on the dd)
    if not ship_tag:
        for dt in soup.find_all("dt"):
            if "relationship" in dt.get_text(strip=True).lower():
                ship_tag = dt.find_next_sibling("dd")
                if ship_tag:
                    break
    meta["ship"] = _pick_ship(ship_tag)

    # --- Stats block: status, dates, word count ---
    stats_text = _extract_stats_text(soup)

    if stats_text:
        # Status: completed date present → Complete; otherwise check chapters
        if re.search(r"Completed:\s*\d{4}-\d{2}-\d{2}", stats_text):
            meta["status"] = "Complete"
        else:
            chapters_m = re.search(r"Chapters:\s*(\d+)/(\?|\d+)", stats_text)
            if chapters_m:
                current, total = chapters_m.group(1), chapters_m.group(2)
                if total != "?" and current == total:
                    meta["status"] = "Complete"
                else:
                    meta["status"] = "In Progress"
            else:
                meta["status"] = "In Progress"

        # lastUpdated: Completed > Updated > Published
        m = re.search(r"Completed:\s*(\d{4}-\d{2}-\d{2})", stats_text)
        if m:
            meta["lastUpdated"] = m.group(1)
        else:
            m = re.search(r"Updated:\s*(\d{4}-\d{2}-\d{2})", stats_text)
            if m:
                meta["lastUpdated"] = m.group(1)
            else:
                m = re.search(r"Published:\s*(\d{4}-\d{2}-\d{2})", stats_text)
                if m:
                    meta["lastUpdated"] = m.group(1)

        # Word count (stored without commas for easy numeric comparison)
        wc_m = re.search(r"Words:\s*([\d,]+)", stats_text)
        if wc_m:
            meta["word_count"] = wc_m.group(1).replace(",", "")

    # --- Legacy meta tag fallbacks (for files processed by older scripts) ---
    # These fire only when the primary extraction above returned nothing.
    def _legacy(name):
        tag = soup.find("meta", attrs={"name": name})
        return tag["content"].strip() if tag and tag.get("content", "").strip() else ""

    if not meta["rating"]:
        meta["rating"] = _legacy("rating")

    if not meta["ship"]:
        raw_ship = _legacy("ship")
        if raw_ship:
            # Run through priority logic in case legacy value is a comma-separated list
            from bs4 import Tag
            fake_dd = Tag(name="dd")
            for s in raw_ship.split(","):
                a = Tag(name="a")
                a.string = s.strip()
                fake_dd.append(a)
            meta["ship"] = _pick_ship(fake_dd)

    if not meta["status"]:
        raw_status = _legacy("status")
        if raw_status:
            sl = raw_status.lower()
            if "complete" in sl and "in" not in sl:
                meta["status"] = "Complete"
            else:
                meta["status"] = "In Progress"

    if not meta["word_count"]:
        meta["word_count"] = _legacy("words").replace(",", "")

    if not meta["lastUpdated"]:
        # Legacy lastUpdated may be "20260427" (YYYYMMDD) — convert to YYYY-MM-DD
        raw_date = _legacy("lastUpdated")
        if re.match(r"^\d{8}$", raw_date):
            meta["lastUpdated"] = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        elif raw_date:
            meta["lastUpdated"] = raw_date

    if not meta["status"]:
        log.warning(f"{source_filename}: could not determine status")

    # --- Summary ---
    summary_tag = soup.select_one("div.summary blockquote") or soup.select_one("blockquote.userstuff")
    if not summary_tag:
        # Legacy downloads: unclassed <p>Summary</p> followed by a bare <blockquote>
        for p in soup.find_all("p"):
            if p.get_text(strip=True) == "Summary":
                sib = p.find_next_sibling()
                if sib and sib.name == "blockquote":
                    summary_tag = sib
                break
    if summary_tag:
        meta["summary"] = re.sub(r"\s+", " ", summary_tag.get_text(separator=" ", strip=True))

    # --- AO3 additional (freeform) tags, feeds auto tagging ---
    tags_dd = soup.select_one("dd.freeform")
    if not tags_dd:
        for dt in soup.find_all("dt"):
            if dt.get_text(strip=True) == "Additional Tags:":
                tags_dd = dt.find_next_sibling("dd")
                break
    if tags_dd:
        meta["ao3_tags"] = [a.get_text(strip=True) for a in tags_dd.find_all("a")] or [
            t.strip() for t in tags_dd.get_text().split(",") if t.strip()
        ]

    return meta

# ---------------------------------------------------------------------------
# HTML cleaning
# ---------------------------------------------------------------------------

def clean_html(soup: BeautifulSoup, meta: dict) -> BeautifulSoup:
    """
    Transform AO3 HTML for the personal archive:
    - Remove all inline style attributes
    - Remove AO3 <link> stylesheets and <script> tags
    - Inject link to ../assets/darkMode.css
    - Inject/update <meta> tags for all extracted fields
    - Remove HTML comments
    """
    # --- Remove inline styles ---
    for tag in soup.find_all(style=True):
        del tag["style"]

    # --- Remove HTML comments ---
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # --- Remove AO3 external stylesheets & scripts ---
    for link in soup.find_all("link", rel=lambda r: r and "stylesheet" in r):
        link.decompose()
    for script in soup.find_all("script"):
        script.decompose()
    for style in soup.find_all("style"):
        style.decompose()

    # --- Ensure <head> exists ---
    if not soup.head:
        soup.html.insert(0, soup.new_tag("head"))

    head = soup.head

    # --- Charset meta ---
    if not soup.find("meta", charset=True):
        charset_tag = soup.new_tag("meta", charset="utf-8")
        head.insert(0, charset_tag)

    # --- Viewport meta ---
    if not soup.find("meta", attrs={"name": "viewport"}):
        vp = soup.new_tag("meta")
        vp["name"] = "viewport"
        vp["content"] = "width=device-width, initial-scale=1"
        head.append(vp)

    # --- Inject stylesheet link ---
    css_link = soup.new_tag("link", rel="stylesheet", href="../assets/darkMode.css")
    head.append(css_link)

    # --- Inject/update archive meta tags ---
    archive_meta_fields = {
        "fic-author":      meta.get("author", ""),
        "fic-ship":        meta.get("ship", ""),
        "fic-rating":      meta.get("rating", ""),
        "fic-status":      meta.get("status", ""),
        "fic-updated":     meta.get("lastUpdated", ""),
        "fic-tags":        ", ".join(meta.get("custom_tags", [])),
        "fic-word-count":  meta.get("word_count", ""),
    }

    for name, content in archive_meta_fields.items():
        existing = soup.find("meta", attrs={"name": name})
        if existing:
            existing["content"] = content
        else:
            m = soup.new_tag("meta")
            m["name"] = name
            m["content"] = content
            head.append(m)

    # --- Update <title> to just the fic title ---
    title_tag = soup.find("title")
    if title_tag:
        title_tag.string = meta.get("title", "Untitled")
    else:
        t = soup.new_tag("title")
        t.string = meta.get("title", "Untitled")
        head.append(t)

    return soup

# ---------------------------------------------------------------------------
# WorkID collision handling
# ---------------------------------------------------------------------------

def _work_id(filename: str) -> str | None:
    """Extract the numeric workID prefix from a filename like '12345_slug.html'."""
    m = re.match(r"^(\d+)_", filename)
    return m.group(1) if m else None

def _parse_word_count(text: str) -> int:
    """Parse a word count string (with or without commas) to int."""
    return int(re.sub(r"[^\d]", "", text) or "0")

def _word_count_from_file(filepath: Path) -> int:
    """Read word count from an existing HTML file's meta tag or stats block."""
    try:
        html = filepath.read_text(encoding="utf-8", errors="replace")
        # Try fic-word-count or legacy words meta tag
        m = re.search(
            r'<meta[^>]*name="(?:fic-word-count|words)"[^>]*content="([\d,]+)"'
            r'|<meta[^>]*content="([\d,]+)"[^>]*name="(?:fic-word-count|words)"',
            html, re.IGNORECASE,
        )
        if m:
            return _parse_word_count(m.group(1) or m.group(2))
        # Fall back to stats block
        m = re.search(r"Words:\s*([\d,]+)", html)
        if m:
            return _parse_word_count(m.group(1))
    except Exception:
        pass
    return 0

def enforce_filename_case(directory: Path, intended: str) -> bool:
    """
    Make the on-disk entry match `intended` exactly, including case.

    macOS/Windows filesystems are case-insensitive, so writing to
    'archive/foo.html' when 'archive/Foo.html' already exists silently
    overwrites the old file and keeps the old name. Git (core.ignorecase=true)
    never notices, so the committed name drifts from the manifest and every
    link 404s on a case-sensitive server like GitHub Pages.

    Returns True if a repair was made.
    """
    actual = next(
        (p.name for p in directory.glob("*.html")
         if p.name.lower() == intended.lower() and p.name != intended),
        None,
    )
    if not actual:
        return False

    # Two-step rename: a direct move is a no-op on a case-insensitive volume.
    tmp = directory / f".case-fix-{os.getpid()}-{intended}"
    (directory / actual).rename(tmp)
    tmp.rename(directory / intended)
    log.warning(
        f"{intended}: case mismatch in {directory.name}/ — "
        f"renamed '{actual}' to match the manifest. "
        f"Commit this rename or the live link will 404."
    )
    print(f"  [CASE] {directory.name}/{actual} -> {intended}")
    return True


def find_existing_by_workid(filename: str) -> Path | None:
    """Return the first archive file that shares the same workID prefix, if different name."""
    wid = _work_id(filename)
    if not wid:
        return None
    for f in ARCHIVE_DIR.glob(f"{wid}_*.html"):
        if f.name != filename:
            return f
    return None

# ---------------------------------------------------------------------------
# Manifest (fic_data.json)
# ---------------------------------------------------------------------------

def load_manifest() -> dict:
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            log.error(f"Could not parse fic_data.json: {e} — starting fresh manifest.")
    return {"fics": [], "last_updated": ""}

def save_manifest(manifest: dict, quiet: bool = False):
    """
    Write fic_data.json atomically.

    Serialise to a temp file in the same directory, then os.replace() it over
    the real one — a rename within a filesystem is atomic, so a crash or an
    interrupt can never leave a half-written manifest behind.
    """
    manifest["last_updated"] = datetime.now().isoformat()
    tmp = DATA_FILE.with_name(DATA_FILE.name + f".tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, DATA_FILE)
    finally:
        if tmp.exists():
            tmp.unlink()
    if not quiet:
        log.info("Manifest saved to fic_data.json")

def upsert_fic(manifest: dict, fic_entry: dict) -> dict:
    """Add or update a fic entry in the manifest, matched on 'filename'."""
    existing = next((f for f in manifest["fics"] if f["filename"] == fic_entry["filename"]), None)
    if existing:
        existing.update(fic_entry)
        log.info(f"Updated manifest entry: {fic_entry['filename']}")
    else:
        manifest["fics"].append(fic_entry)
        log.info(f"Added manifest entry: {fic_entry['filename']}")
    return manifest

def remove_fic_from_manifest(manifest: dict, filename: str):
    """Remove a fic entry from the manifest by filename."""
    manifest["fics"] = [f for f in manifest["fics"] if f["filename"] != filename]

# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------

INDEX_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agathario Archive</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      background: #0b0917;
      color: #d8cff0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      min-height: 100vh;
    }}

    /* ---- Header ---- */
    .archive-header {{
      padding: 2.5rem 1rem 1.25rem;
      text-align: center;
      border-bottom: 1px solid #1e1838;
      margin-bottom: 2rem;
    }}
    /* Title: Cinzel caps in Agatha purple → Rio green (falls back to Georgia offline) */
    .archive-header h1 {{
      font-family: "Cinzel", Georgia, serif;
      font-size: clamp(1.15rem, 6vw, 1.55rem); /* stays on one line on small phones */
      font-weight: 500;
      letter-spacing: 0.18em;
      padding-left: 0.18em; /* balance trailing letter-spacing */
      background: linear-gradient(90deg, #c4a8ff, #7fc98f);
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
    }}
    .header-stars {{
      font-size: 0.8rem;
      letter-spacing: 0.6em;
      padding-left: 0.6em; /* balance trailing letter-spacing */
      color: #5a4a8a;
      margin-bottom: 0.4rem;
    }}
    .header-rule {{
      height: 1px;
      width: 180px;
      margin: 0.7rem auto 0;
      background: linear-gradient(90deg, transparent, #c4a8ff, #7fc98f, transparent);
      opacity: 0.7;
    }}
    .archive-header p {{
      margin-top: 0.35rem;
      font-size: 0.8rem;
      color: #6a5e8a;
    }}
    .last-updated {{
      font-size: 0.72rem;
      color: #3d3560;
      margin-top: 0.2rem;
    }}

    /* ---- Collapsible filter/search ---- */
    .filter-section {{
      max-width: 660px;
      margin: 0 auto 0.75rem;
      padding: 0 1rem;
    }}
    .filter-toggle {{
      list-style: none;
      cursor: pointer;
      font-size: 0.78rem;
      color: #6a5e8a;
      padding: 0.3rem 0.7rem;
      border: 1px solid #1e1838;
      border-radius: 6px;
      display: inline-block;
      user-select: none;
    }}
    .filter-toggle::-webkit-details-marker {{ display: none; }}
    details[open] .filter-toggle {{
      border-color: #c4a8ff;
      color: #c4a8ff;
    }}
    .filter-bar {{
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      padding-top: 0.75rem;
    }}
    .search-input {{
      width: 100%;
      padding: 0.6rem 1rem;
      background: #17112b;
      border: 1px solid #2a2248;
      border-radius: 8px;
      color: #d8cff0;
      font-size: 0.9rem;
      box-sizing: border-box;
    }}
    .search-input:focus {{
      outline: none;
      border-color: #c4a8ff;
    }}
    .chip-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      align-items: center;
    }}
    .chip-label {{
      font-size: 0.72rem;
      color: #5a5078;
      padding-right: 0.2rem;
      white-space: nowrap;
    }}
    .chip {{
      padding: 0.25rem 0.7rem;
      border-radius: 999px;
      border: 1px solid #1e1838;
      background: transparent;
      color: #6a5e8a;
      font-size: 0.75rem;
      cursor: pointer;
      transition: all 0.15s;
      white-space: nowrap;
    }}
    .chip.active {{
      background: #2a1f50;
      border-color: #5030a8;
      color: #c4a8ff;
    }}

    /* ---- Sort row ---- */
    .sort-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      align-items: center;
      max-width: 660px;
      margin: 0 auto 1.25rem;
      padding: 0 1rem;
    }}

    /* ---- Card grid ---- */
    .card-grid {{
      display: flex;
      flex-direction: column;
      gap: 0.85rem;
      padding: 0 1rem 4rem;
      max-width: 660px;
      margin: 0 auto;
    }}

    /* ---- Fic card ---- */
    .fic-card {{
      background: #13102a;
      border: 1px solid #221a40;
      border-radius: 10px;
      padding: 1rem 1.2rem 0.9rem;
      text-decoration: none;
      color: inherit;
      display: block;
      transition: border-color 0.15s, background 0.15s;
    }}
    .fic-card:hover {{
      border-color: #4a36a0;
      background: #181330;
    }}
    .card-title {{
      font-size: 1.1rem;
      font-weight: 600;
      color: #cbb8ff;
      line-height: 1.3;
      margin-bottom: 0.2rem;
    }}
    .card-author {{
      font-size: 0.8rem;
      color: #7a6da0;
      margin-bottom: 0.55rem;
    }}

    /* meta row: ship / rating / status / words */
    .card-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.3rem;
      margin-bottom: 0.35rem;
    }}
    /* extra custom tags row */
    .card-tags {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.3rem;
      margin-bottom: 0.35rem;
    }}

    /* ---- Badges ---- */
    .badge {{
      font-size: 0.7rem;
      padding: 0.18rem 0.55rem;
      border-radius: 999px;
      border: 1px solid #2a2248;
      background: #1a1638;
      color: #9080c0;
      white-space: nowrap;
    }}
    .badge.ship    {{ border-color: #4a34a0; color: #c4a8ff; background: #1e1540; }}
    .badge.rating-G {{ color: #72cc80; border-color: #2a4030; background: #111e18; }}
    .badge.rating-T {{ color: #d4b86a; border-color: #3a3018; background: #1c1a10; }}
    .badge.rating-M {{ color: #d48860; border-color: #3a2418; background: #1c1310; }}
    .badge.rating-E {{ color: #d46880; border-color: #3a1828; background: #1c1018; }}
    .badge.complete {{ color: #60b4cc; border-color: #1c3a48; background: #0e1e28; }}
    .badge.wip      {{ color: #a888d8; border-color: #2a1e48; background: #150e28; }}
    .badge.words    {{ color: #6a6090; border-color: #201c38; background: transparent; }}

    .card-summary {{
      font-size: 0.8rem;
      color: #8878b0;
      line-height: 1.55;
      margin-top: 0.3rem;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .no-results {{
      text-align: center;
      color: #5a5078;
      padding: 3rem 0;
      font-size: 0.9rem;
    }}
    .count-label {{
      font-size: 0.75rem;
      color: #5a5078;
      text-align: center;
      padding-bottom: 0.75rem;
    }}

    /* ---- Back to top ---- */
    .to-top {{
      position: fixed;
      right: 1.25rem;
      bottom: 1.25rem;
      width: 2.6rem;
      height: 2.6rem;
      border-radius: 50%;
      border: 1px solid #4a34a0;
      background: #1e1540;
      color: #c4a8ff;
      font-size: 1.1rem;
      cursor: pointer;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.2s, background 0.15s;
    }}
    .to-top.visible {{
      opacity: 0.9;
      pointer-events: auto;
    }}
    .to-top:hover {{ background: #2a1f50; opacity: 1; }}

    /* ---- Stats link + dialog ---- */
    .archive-header {{ position: relative; }}
    .stats-link {{
      position: absolute;
      top: 0.9rem;
      right: 1rem;
      background: none;
      border: 1px solid transparent;
      border-radius: 6px;
      color: #3d3560;
      font-size: 0.72rem;
      letter-spacing: 0.08em;
      padding: 0.25rem 0.5rem;
      cursor: pointer;
      transition: color 0.15s, border-color 0.15s;
    }}
    .stats-link:hover, .stats-link:focus-visible {{
      color: #c4a8ff;
      border-color: #2a2248;
      outline: none;
    }}
    .stats-dialog {{
      width: min(560px, calc(100vw - 2rem));
      max-height: 85vh;
      margin: auto;
      padding: 0;
      background: #13102a;
      color: #d8cff0;
      border: 1px solid #2a2248;
      border-radius: 12px;
    }}
    .stats-dialog::backdrop {{ background: rgba(5, 3, 12, 0.75); }}
    .stats-inner {{ padding: 1.4rem 1.4rem 1.6rem; }}
    .stats-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.1rem;
    }}
    .stats-head h2 {{
      font-family: "Cinzel", Georgia, serif;
      font-size: 1rem;
      font-weight: 500;
      letter-spacing: 0.14em;
      color: #c4a8ff;
    }}
    .stats-close {{
      background: none;
      border: none;
      color: #6a5e8a;
      font-size: 1.4rem;
      line-height: 1;
      cursor: pointer;
    }}
    .stats-close:hover {{ color: #c4a8ff; }}
    .stat-tiles {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 0.6rem;
    }}
    @media (min-width: 480px) {{
      .stat-tiles {{ grid-template-columns: repeat(4, 1fr); }}
    }}
    .stat-tile {{
      background: #0f0c22;
      border: 1px solid #221a40;
      border-radius: 8px;
      padding: 0.7rem 0.75rem;
    }}
    .stat-num {{
      font-size: 1.35rem;
      font-weight: 600;
      color: #e4dcff;
      font-variant-numeric: tabular-nums;
    }}
    .stat-cap {{
      font-size: 0.66rem;
      color: #6a5e8a;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-top: 0.15rem;
    }}
    .stat-fun {{
      font-size: 0.8rem;
      color: #8878b0;
      line-height: 1.5;
      margin-top: 0.8rem;
    }}
    .stat-fun b {{ color: #cbb8ff; font-weight: 600; }}
    .stats-section {{ margin-top: 1.5rem; }}
    .stats-section h3 {{
      font-size: 0.68rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: #5a5078;
      margin-bottom: 0.55rem;
    }}
    .stat-fic {{
      font-size: 0.8rem;
      color: #7a6da0;
      line-height: 1.5;
      margin-bottom: 0.35rem;
    }}
    .stat-fic a {{ color: #cbb8ff; text-decoration: none; }}
    .stat-fic a:hover {{ text-decoration: underline; }}
    .bar-row {{
      display: grid;
      grid-template-columns: minmax(0, 1.3fr) 1fr 2.4rem;
      gap: 0.6rem;
      align-items: center;
      font-size: 0.78rem;
      padding: 0.16rem 0;
    }}
    .bar-label {{
      color: #b8a8e0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    button.bar-label {{
      background: none;
      border: none;
      padding: 0;
      font: inherit;
      text-align: left;
      cursor: pointer;
    }}
    button.bar-label:hover {{ color: #c4a8ff; text-decoration: underline; }}
    .bar-track {{
      height: 6px;
      background: #1a1638;
      border-radius: 999px;
      overflow: hidden;
    }}
    .bar-fill {{
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, #8a6ae0, #7fc98f);
    }}
    .bar-val {{
      text-align: right;
      color: #7a6da0;
      font-variant-numeric: tabular-nums;
    }}
    .split-bar {{
      display: flex;
      gap: 2px;
      height: 8px;
      border-radius: 999px;
      overflow: hidden;
    }}
    .split-legend {{
      display: flex;
      gap: 1.1rem;
      font-size: 0.76rem;
      color: #8878b0;
      margin-top: 0.45rem;
    }}
    .split-legend i {{
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      margin-right: 0.35rem;
    }}
    .stats-hint {{
      font-size: 0.7rem;
      color: #4a4068;
      margin-top: 1.4rem;
      text-align: center;
    }}
    .pinned-filter {{
      text-align: center;
      margin-bottom: 0.75rem;
    }}
    .pinned-filter:empty {{ display: none; }}
  </style>
</head>
<body>

<div class="archive-header">
  <button class="stats-link" id="stats-open" type="button" title="Archive stats">✦ stats</button>
  <div class="header-stars" aria-hidden="true">✦ ⋆ ☾ ⋆ ✦</div>
  <h1>Agathario Archive</h1>
  <div class="header-rule"></div>
  <p id="fic-count"></p>
  <p class="last-updated">last updated {last_updated}</p>
</div>

<details class="filter-section">
  <summary class="filter-toggle">Filter &amp; search ▾</summary>
  <div class="filter-bar">
    <input class="search-input" type="search" id="search"
           placeholder="Search title, author, ship, summary, tags…" autocomplete="off">
    <div class="chip-row" id="rating-chips">
      <span class="chip-label">Rating:</span>
    </div>
    <div class="chip-row" id="status-chips">
      <span class="chip-label">Status:</span>
    </div>
  </div>
</details>

<div class="sort-row">
  <span class="chip-label">Sort:</span>
  <button class="chip active" data-sort="default">Default</button>
  <button class="chip" data-sort="wc_desc">Words</button>
  <button class="chip" data-sort="updated">Updated</button>
  <button class="chip" data-sort="alpha">A–Z</button>
</div>

<div class="pinned-filter" id="pinned-filter"></div>
<div class="count-label" id="visible-count"></div>
<div class="card-grid" id="card-grid"></div>

<button class="to-top" id="to-top" type="button" aria-label="Back to top" title="Back to top">↑</button>

<dialog class="stats-dialog" id="stats-dialog" aria-labelledby="stats-title">
  <div class="stats-inner">
    <div class="stats-head">
      <h2 id="stats-title">Archive Stats</h2>
      <button class="stats-close" id="stats-close" type="button" aria-label="Close">×</button>
    </div>
    <div id="stats-body"></div>
    <p class="stats-hint">tap a tag or author to filter the archive</p>
  </div>
</dialog>

<script>
const FICS = {fics_json};

let sortMode = "default";

function parseWordCount(wc) {{
  if (!wc) return 0;
  return parseInt(String(wc).replace(/,/g, ""), 10) || 0;
}}

function fmtWordCount(wc) {{
  const n = parseWordCount(wc);
  if (!n) return "";
  if (n >= 1000) return (n / 1000).toFixed(n % 1000 === 0 ? 0 : 1) + "k";
  return String(n);
}}

function getSortedFics() {{
  if (sortMode === "wc_desc")  return [...FICS].sort((a, b) => parseWordCount(b.word_count) - parseWordCount(a.word_count));
  if (sortMode === "updated")  return [...FICS].sort((a, b) => (b.lastUpdated || "").localeCompare(a.lastUpdated || ""));
  if (sortMode === "alpha")    return [...FICS].sort((a, b) => (a.title || "").toLowerCase().localeCompare((b.title || "").toLowerCase()));
  return FICS;
}}

function ratingClass(r) {{
  if (!r) return "";
  const up = r.toUpperCase();
  if (up.includes("GENERAL")) return "rating-G";
  if (up.includes("TEEN"))    return "rating-T";
  if (up.includes("MATURE"))  return "rating-M";
  if (up.includes("EXPLICIT")) return "rating-E";
  return "";
}}

function statusClass(s) {{
  if (!s) return "";
  const sl = s.toLowerCase();
  if (sl === "complete" || sl === "completed") return "complete";
  if (sl.includes("progress")) return "wip";
  return "";
}}

// Short chip labels for ratings, in AO3's order (unlisted ratings go last, A–Z)
const RATING_LABELS = {{
  "general audiences":     "GA",
  "teen and up audiences": "T",
  "mature":                "M",
  "explicit":              "E",
  "not rated":             "NR",
}};

function buildChips(containerId, values, key, labels = {{}}) {{
  const container = document.getElementById(containerId);
  const order = Object.keys(labels);
  const rank = v => {{ const i = order.indexOf(v.toLowerCase()); return i === -1 ? order.length : i; }};
  [...new Set(values.filter(Boolean))]
    .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
    .forEach(val => {{
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.textContent = labels[val.toLowerCase()] || val;
    chip.title = val;
    chip.dataset.key = key;
    chip.dataset.val = val.toLowerCase();
    chip.addEventListener("click", () => {{
      chip.classList.toggle("active");
      render();
    }});
    container.appendChild(chip);
  }});
}}

function activeFilters(containerId) {{
  return [...document.querySelectorAll(`#${{containerId}} .chip.active`)]
    .map(c => c.dataset.val);
}}

function render() {{
  const query = document.getElementById("search").value.toLowerCase().trim();
  const ratingFilters = activeFilters("rating-chips");
  const statusFilters = activeFilters("status-chips");

  const grid = document.getElementById("card-grid");
  grid.innerHTML = "";

  let visible = 0;
  getSortedFics().forEach(fic => {{
    const searchable = [fic.title, fic.author, fic.ship, fic.summary, ...(fic.custom_tags || [])].join(" ").toLowerCase();
    if (query && !searchable.includes(query)) return;
    if (ratingFilters.length && !ratingFilters.includes((fic.rating || "").toLowerCase())) return;
    if (statusFilters.length && !statusFilters.includes((fic.status || "").toLowerCase())) return;
    if (pinned && (pinned.key === "tag"
        ? !(fic.custom_tags || []).includes(pinned.val)
        : fic.author !== pinned.val)) return;

    visible++;
    const card = document.createElement("a");
    card.className = "fic-card";
    card.href = "archive/" + fic.filename;

    const rc = ratingClass(fic.rating);
    const sc = statusClass(fic.status);
    const wc = fmtWordCount(fic.word_count);
    const extraTags = (fic.custom_tags || []);

    card.innerHTML = `
      <div class="card-title">${{fic.title || "Untitled"}}</div>
      <div class="card-author">by ${{fic.author || "Unknown"}}</div>
      <div class="card-meta">
        ${{fic.ship   ? `<span class="badge ship">${{fic.ship}}</span>` : ""}}
        ${{rc         ? `<span class="badge ${{rc}}">${{fic.rating}}</span>` : ""}}
        ${{sc         ? `<span class="badge ${{sc}}">${{fic.status}}</span>` : ""}}
        ${{wc         ? `<span class="badge words">${{wc}} words</span>` : ""}}
      </div>
      ${{extraTags.length ? `<div class="card-tags">${{extraTags.map(t => `<span class="badge">${{t}}</span>`).join("")}}</div>` : ""}}
      ${{fic.summary ? `<p class="card-summary">${{fic.summary}}</p>` : ""}}
    `;
    grid.appendChild(card);
  }});

  if (visible === 0) {{
    grid.innerHTML = '<p class="no-results">No fics match your filters.</p>';
  }}

  document.getElementById("pinned-filter").innerHTML = pinned
    ? `<button class="chip active" type="button" title="Clear filter">${{pinned.key === "tag" ? "Tag" : "Author"}}: ${{esc(pinned.val)}} ✕</button>`
    : "";
  document.getElementById("visible-count").textContent =
    visible === FICS.length ? "" : `Showing ${{visible}} of ${{FICS.length}}`;
  document.getElementById("fic-count").textContent =
    `${{FICS.length}} fic${{FICS.length !== 1 ? "s" : ""}} archived`;
}}

document.querySelectorAll(".sort-row .chip").forEach(chip => {{
  chip.addEventListener("click", () => {{
    document.querySelectorAll(".sort-row .chip").forEach(c => c.classList.remove("active"));
    chip.classList.add("active");
    sortMode = chip.dataset.sort;
    render();
  }});
}});

buildChips("rating-chips", FICS.map(f => f.rating), "rating", RATING_LABELS);
buildChips("status-chips", FICS.map(f => f.status), "status");

document.getElementById("search").addEventListener("input", render);

const toTop = document.getElementById("to-top");
window.addEventListener("scroll", () => {{
  toTop.classList.toggle("visible", window.scrollY > 600);
}}, {{ passive: true }});
toTop.addEventListener("click", () => window.scrollTo({{ top: 0, behavior: "smooth" }}));

// ---- Archive stats ----
// Tags too common to be interesting on their own; gets a second "the rest" list
const BIG_THREE_TAGS = ["Smut", "Fluff", "Angst"];
const RATING_COLORS = {{
  "general audiences":     "#72cc80",
  "teen and up audiences": "#d4b86a",
  "mature":                "#d48860",
  "explicit":              "#d46880",
}};

function esc(s) {{
  return String(s).replace(/[&<>"']/g, c => ({{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }})[c]);
}}

function fmtBig(n) {{
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e4) return Math.round(n / 1e3) + "k";
  return n.toLocaleString();
}}

function topCounts(values, n = Infinity) {{
  const counts = new Map();
  values.forEach(v => {{ if (v) counts.set(v, (counts.get(v) || 0) + 1); }});
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, n);
}}

// Horizontal bar list. With `key`, labels are buttons that pin a filter on the index.
function barRows(rows, {{ key, colors = {{}} }} = {{}}) {{
  const max = Math.max(1, ...rows.map(r => r[1]));
  return rows.map(([label, count]) => {{
    const lbl = key
      ? `<button class="bar-label" type="button" data-key="${{key}}" data-val="${{esc(label)}}" title="${{esc(label)}}">${{esc(label)}}</button>`
      : `<span class="bar-label" title="${{esc(label)}}">${{esc(label)}}</span>`;
    const color = colors[label.toLowerCase()];
    const style = `width:${{(count / max * 100).toFixed(1)}}%` + (color ? `;background:${{color}}` : "");
    return `<div class="bar-row">${{lbl}}<div class="bar-track"><div class="bar-fill" style="${{style}}"></div></div><span class="bar-val">${{count}}</span></div>`;
  }}).join("");
}}

function ficLine(label, fic) {{
  if (!fic) return "";
  return `<p class="stat-fic">${{label}}: <a href="archive/${{esc(fic.filename)}}">${{esc(fic.title || "Untitled")}}</a>
    by ${{esc(fic.author || "Unknown")}} · ${{parseWordCount(fic.word_count).toLocaleString()}} words</p>`;
}}

function buildStats() {{
  const withWords = FICS.filter(f => parseWordCount(f.word_count) > 0)
    .sort((a, b) => parseWordCount(b.word_count) - parseWordCount(a.word_count));
  const total = withWords.reduce((sum, f) => sum + parseWordCount(f.word_count), 0);
  const avg = withWords.length ? Math.round(total / withWords.length) : 0;
  const authors = new Set(FICS.map(f => f.author).filter(Boolean)).size;
  const complete = FICS.filter(f => statusClass(f.status) === "complete").length;
  const wip = FICS.filter(f => statusClass(f.status) === "wip").length;
  const donePct = complete + wip ? complete / (complete + wip) * 100 : 0;

  const ratingOrder = Object.keys(RATING_LABELS);
  const rank = r => {{ const i = ratingOrder.indexOf(r.toLowerCase()); return i === -1 ? ratingOrder.length : i; }};
  const ratings = topCounts(FICS.map(f => f.rating)).sort((a, b) => rank(a[0]) - rank(b[0]));

  const allTags = FICS.flatMap(f => f.custom_tags || []);
  const otherTags = allTags.filter(t => !BIG_THREE_TAGS.includes(t));

  document.getElementById("stats-body").innerHTML = `
    <div class="stat-tiles">
      <div class="stat-tile" title="${{total.toLocaleString()}} words"><div class="stat-num">${{fmtBig(total)}}</div><div class="stat-cap">words</div></div>
      <div class="stat-tile"><div class="stat-num">${{FICS.length}}</div><div class="stat-cap">fics</div></div>
      <div class="stat-tile"><div class="stat-num">${{authors}}</div><div class="stat-cap">authors</div></div>
      <div class="stat-tile" title="${{avg.toLocaleString()}} words"><div class="stat-num">${{fmtBig(avg)}}</div><div class="stat-cap">avg length</div></div>
    </div>
    <p class="stat-fun">That's about <b>${{Math.round(total / 90000).toLocaleString()}}</b> novels' worth, or
      <b>${{Math.round(total / 250 / 60 / 24)}}</b> days of nonstop reading at 250 words a minute.</p>

    <div class="stats-section">
      <h3>Longest &amp; shortest</h3>
      ${{ficLine("Longest", withWords[0])}}
      ${{ficLine("Shortest", withWords[withWords.length - 1])}}
    </div>

    <div class="stats-section">
      <h3>Status</h3>
      <div class="split-bar">
        <div style="width:${{donePct.toFixed(1)}}%;background:#60b4cc"></div>
        <div style="flex:1;background:#a888d8"></div>
      </div>
      <div class="split-legend">
        <span><i style="background:#60b4cc"></i>Complete ${{complete}}</span>
        <span><i style="background:#a888d8"></i>In progress ${{wip}}</span>
      </div>
    </div>

    <div class="stats-section">
      <h3>Ratings</h3>
      ${{barRows(ratings, {{ colors: RATING_COLORS }})}}
    </div>

    <div class="stats-section">
      <h3>Most common tags</h3>
      ${{barRows(topCounts(allTags, 10), {{ key: "tag" }})}}
    </div>

    <div class="stats-section">
      <h3>Most common tags that aren't smut, fluff, or angst</h3>
      ${{barRows(topCounts(otherTags, 10), {{ key: "tag" }})}}
    </div>

    <div class="stats-section">
      <h3>Most archived authors</h3>
      ${{barRows(topCounts(FICS.map(f => f.author), 10), {{ key: "author" }})}}
    </div>
  `;
}}

let pinned = null;  // {{ key: "tag" | "author", val }}, set by clicking a label in the stats dialog
const statsDialog = document.getElementById("stats-dialog");

document.getElementById("stats-open").addEventListener("click", () => {{
  buildStats();
  statsDialog.showModal();
}});
document.getElementById("stats-close").addEventListener("click", () => statsDialog.close());
statsDialog.addEventListener("click", e => {{
  if (e.target === statsDialog) {{ statsDialog.close(); return; }}  // backdrop click
  const btn = e.target.closest("button.bar-label[data-key]");
  if (!btn) return;
  pinned = {{ key: btn.dataset.key, val: btn.dataset.val }};
  statsDialog.close();
  render();
  window.scrollTo({{ top: 0, behavior: "smooth" }});
}});
document.getElementById("pinned-filter").addEventListener("click", () => {{
  pinned = null;
  render();
}});

render();
</script>
</body>
</html>
"""

def build_index(manifest: dict):
    """Generate index.html from the fic manifest."""
    fics_json = json.dumps(manifest["fics"], ensure_ascii=False)
    try:
        dt = datetime.fromisoformat(manifest.get("last_updated", ""))
        last_updated = dt.strftime(f"%B {dt.day}, %Y")
    except ValueError:
        last_updated = "unknown"
    html = INDEX_TEMPLATE.format(fics_json=fics_json, last_updated=last_updated)
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"index.html rebuilt ({len(manifest['fics'])} fics)")

# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def ensure_dirs():
    for d in [STAGING_DIR, ARCHIVE_DIR, ORIGINALS_DIR, ASSETS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

def process_file(filepath: Path, manifest: dict) -> bool:
    """
    Process a single staged HTML file.
    Returns True on success or intentional skip, False on error.
    """
    filename = filepath.name
    log.info(f"--- Processing: {filename}")

    # --- Read ---
    try:
        raw_html = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        log.error(f"{filename}: failed to read file: {e}")
        return False

    # --- Parse ---
    soup = BeautifulSoup(raw_html, "html.parser")

    # --- Extract metadata ---
    meta = extract_metadata(soup, filename)
    log.info(f"{filename}: extracted — title='{meta['title']}' author='{meta['author']}' "
             f"status='{meta['status']}' lastUpdated='{meta['lastUpdated']}' words='{meta['word_count']}'")

    if not meta["title"]:
        log.warning(f"{filename}: no title found, using filename as fallback")
        meta["title"] = filepath.stem

    if not meta["author"]:
        log.warning(f"{filename}: no author found, using 'unknown'")
        meta["author"] = "unknown"

    # --- Apply custom tags ---
    meta["custom_tags"] = apply_custom_tags(meta)

    # --- WorkID collision check ---
    existing = find_existing_by_workid(filename)
    if existing:
        old_wc = _word_count_from_file(existing)
        new_wc = _parse_word_count(meta.get("word_count", "0"))
        if new_wc >= old_wc:
            log.info(
                f"{filename}: replacing {existing.name} "
                f"(new: {new_wc} words >= old: {old_wc} words)"
            )
            print(f"  Replacing {existing.name} → {filename} ({old_wc} → {new_wc} words)")
            remove_fic_from_manifest(manifest, existing.name)
            existing.unlink()
        else:
            log.warning(
                f"{filename}: skipped — existing {existing.name} has more words "
                f"({old_wc} > {new_wc}); remove it manually to force replacement"
            )
            print(f"  [SKIP] {filename}: existing {existing.name} has more words ({old_wc} > {new_wc})")
            filepath.unlink()
            return True  # not an error, just a no-op

    # --- Back up original ---
    original_dest = ORIGINALS_DIR / filename
    if not original_dest.exists():
        shutil.copy2(filepath, original_dest)
        log.info(f"{filename}: backed up to originals/")
    else:
        log.info(f"{filename}: original backup already exists, skipping copy")
    enforce_filename_case(ORIGINALS_DIR, filename)

    # --- Clean HTML ---
    soup = clean_html(soup, meta)

    # --- Write to archive (keep staged filename — no renaming) ---
    archive_dest = ARCHIVE_DIR / filename
    try:
        archive_dest.write_text(str(soup), encoding="utf-8")
        log.info(f"{filename}: written to archive/")
    except Exception as e:
        log.error(f"{filename}: failed to write to archive: {e}")
        return False

    # The write above may have landed in a differently-cased existing file.
    enforce_filename_case(ARCHIVE_DIR, filename)

    # --- Update manifest ---
    fic_entry = {
        "filename":    filename,
        "title":       meta["title"],
        "author":      meta["author"],
        "ship":        meta["ship"],
        "rating":      meta["rating"],
        "status":      meta["status"],
        "summary":     apply_custom_summary(meta),
        "ao3_summary": meta["summary"],
        "lastUpdated": meta["lastUpdated"],
        "word_count":  meta["word_count"],
        "custom_tags": meta["custom_tags"],
        "processed":   datetime.now().isoformat(),
        "source_file": filename,
    }
    upsert_fic(manifest, fic_entry)

    # --- Remove from staging ---
    filepath.unlink()
    log.info(f"{filename}: removed from staging/")

    return True

@contextmanager
def run_lock():
    """
    Refuse to run while another phase-4 process is working.

    Two concurrent runs both load fic_data.json, both mutate their own copy,
    and whichever saves last wins — silently discarding the other's entries
    while its file moves stay on disk. That is exactly how the manifest and
    archive/ drifted apart on 2026-07-22.
    """
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            holder = LOCK_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            holder = "unknown"
        msg = (
            f"Another run appears to be in progress (lock held by pid {holder}).\n"
            f"  If that process is gone, delete {LOCK_FILE} and try again."
        )
        log.error(msg.replace("\n", " "))
        print(f"\n[ABORT] {msg}")
        sys.exit(1)

    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        try:
            LOCK_FILE.unlink()
        except FileNotFoundError:
            pass


def main():
    ensure_dirs()
    log.info("=" * 60)
    log.info(f"Run started: {datetime.now().isoformat()}")

    staged_files = sorted(STAGING_DIR.glob("*.html"))
    if not staged_files:
        log.info("No HTML files found in staging/. Nothing to do.")
        print("No files found in staging/ — nothing to process.")
        return

    print(f"Found {len(staged_files)} file(s) in staging/. Processing...")
    log.info(f"Found {len(staged_files)} file(s) to process")

    manifest = load_manifest()

    success_count = 0
    fail_count = 0

    for filepath in staged_files:
        ok = process_file(filepath, manifest)
        if ok:
            success_count += 1
        else:
            fail_count += 1
        # Persist after every file: process_file has already moved things on
        # disk, so an interrupt here must not lose the matching manifest edit.
        save_manifest(manifest, quiet=True)

    save_manifest(manifest)
    sync_custom_summaries_csv(manifest)
    build_index(manifest)

    summary = f"Done. {success_count} processed, {fail_count} failed."
    print(summary)
    if fail_count:
        print("  ↳ Check process.log for error details.")
    log.info(summary)
    log.info("=" * 60)

if __name__ == "__main__":
    with run_lock():
        main()
