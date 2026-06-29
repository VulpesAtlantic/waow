#!/usr/bin/env python3
"""
Read custom_tags from a CSV and write them into fic_data.json,
then rebuild index.html using process.py's build_index logic.

CSV format: filename, title, additional_tags, custom_tags
custom_tags column is pipe-delimited; stored in JSON as a list.
"""

import csv
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
FIC_DATA = BASE_DIR / "fic_data.json"

# Pull build_index (and everything it needs) from process.py
sys.path.insert(0, str(ASSETS_DIR))
import process  # noqa: E402


def main():
    csv_path = ASSETS_DIR / "tags_review_custom.csv"

    # Load the CSV into a lookup: filename -> [tag, tag, ...]
    updates = {}
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            filename = row["filename"].strip()
            raw = row.get("custom_tags", "").strip()
            tags = [t.strip() for t in raw.split("|") if t.strip()] if raw else []
            updates[filename] = tags

    # Load manifest
    with open(FIC_DATA, encoding="utf-8") as f:
        manifest = json.load(f)

    # Apply updates
    changed = 0
    for fic in manifest["fics"]:
        fname = fic.get("filename") or fic.get("source_file", "")
        if fname in updates:
            fic["custom_tags"] = updates[fname]
            changed += 1

    print(f"Updated custom_tags on {changed}/{len(manifest['fics'])} fics.")

    # Save manifest
    with open(FIC_DATA, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # Rebuild index.html
    process.build_index(manifest)
    print("index.html rebuilt.")


if __name__ == "__main__":
    main()
