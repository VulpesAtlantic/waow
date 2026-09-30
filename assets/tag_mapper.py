"""
Map AO3 additional tags onto the archive's custom tags using tag_mappings.json.

Shared by extract_tags.py (builds the review CSVs) and phase4_process.py /
reprocess.py (applies auto tags to fics with no hand-written custom tags).

Per AO3 tag, lowercased: ignore -> exact -> keyword (first keyword hit wins).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

MAPPINGS_FILE = Path(__file__).resolve().parent / "tag_mappings.json"


def norm(tag: str) -> str:
    """Lowercase and collapse whitespace — every tag comparison goes through this."""
    return re.sub(r"\s+", " ", tag).strip().lower()


@lru_cache(maxsize=None)
def _rules():
    with open(MAPPINGS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    exact = {norm(k): norm(v) for k, v in data.get("exact", {}).items()}
    # Anchor keywords to a word start so "rape" doesn't fire on "grapes"
    keyword = [
        (re.compile(r"(?<![a-z0-9])" + re.escape(norm(r["match"]))), norm(r["tag"]))
        for r in data.get("keyword", [])
    ]
    ignore = {norm(t) for t in data.get("ignore", [])}
    return exact, keyword, ignore


def map_tag(tag: str) -> tuple[str | None, bool]:
    """Return (custom_tag or None, ignored?) for one AO3 tag."""
    exact, keyword, ignore = _rules()
    t = norm(tag)
    if t in ignore:
        return None, True
    if t in exact:
        return exact[t], False
    for pattern, custom in keyword:
        if pattern.search(t):
            return custom, False
    return None, False


def classify(ao3_tags) -> tuple[list[str], list[str]]:
    """
    Split a fic's AO3 tags into (sorted custom tags, unmapped AO3 tags).
    Ignored tags land in neither list.
    """
    custom, unmapped = set(), []
    for tag in ao3_tags:
        if not tag.strip():
            continue
        mapped, ignored = map_tag(tag)
        if mapped:
            custom.add(mapped)
        elif not ignored:
            unmapped.append(tag.strip())
    return sorted(custom), unmapped


def auto_tags(ao3_tags) -> list[str]:
    return classify(ao3_tags)[0]
