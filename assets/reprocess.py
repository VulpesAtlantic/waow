"""
assets/reprocess.py
====================
Re-extract metadata from every file already in archive/, update their
<meta> tags in place, rebuild fic_data.json, and regenerate index.html.

Run this any time you update the parsing or cleaning logic in process.py
and want to backfill the change across all existing fics:

    python assets/reprocess.py

Safe to re-run as many times as you like — it overwrites in place.
"""

import sys
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

# Import shared logic from process.py (same directory)
sys.path.insert(0, str(Path(__file__).parent))
from process import (
    ARCHIVE_DIR,
    apply_custom_tags,
    build_index,
    clean_html,
    extract_metadata,
    save_manifest,
    upsert_fic,
)


def reprocess():
    files = sorted(ARCHIVE_DIR.glob("*.html"))
    if not files:
        print("No HTML files found in archive/ — nothing to do.")
        return

    print(f"Reprocessing {len(files)} file(s) in archive/...")
    manifest = {"fics": [], "last_updated": ""}
    ok_count = 0
    err_count = 0

    for filepath in files:
        try:
            raw = filepath.read_text(encoding="utf-8", errors="replace")
            soup = BeautifulSoup(raw, "html.parser")

            meta = extract_metadata(soup, filepath.name)
            meta["custom_tags"] = apply_custom_tags(meta)

            if not meta["title"]:
                meta["title"] = filepath.stem
            if not meta["author"]:
                meta["author"] = "unknown"

            # Re-inject updated meta tags into the file
            soup = clean_html(soup, meta)
            filepath.write_text(str(soup), encoding="utf-8")

            fic_entry = {
                "filename":    filepath.name,
                "title":       meta["title"],
                "author":      meta["author"],
                "ship":        meta["ship"],
                "rating":      meta["rating"],
                "status":      meta["status"],
                "summary":     meta["summary"],
                "lastUpdated": meta["lastUpdated"],
                "word_count":  meta["word_count"],
                "custom_tags": meta["custom_tags"],
                "processed":   datetime.now().isoformat(),
                "source_file": filepath.name,
            }
            upsert_fic(manifest, fic_entry)
            ok_count += 1

        except Exception as e:
            print(f"  [ERROR] {filepath.name}: {e}")
            err_count += 1

    save_manifest(manifest)
    build_index(manifest)

    print(f"Done. {ok_count} reprocessed, {err_count} errors.")
    if err_count:
        print("  ↳ Check process.log for details.")


if __name__ == "__main__":
    reprocess()
