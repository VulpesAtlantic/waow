#!/usr/bin/env python3
"""
Extract Additional Tags from archived fic HTML files into review CSVs.

Writes (all in assets/):
  tags_review.csv            one row per fic: AO3 tags, auto_tags (from
                             tag_mappings.json), and your custom_tags
  tags_unmapped.csv          AO3 tags on 2+ fics that nothing maps or ignores —
                             the list to work through when growing tag_mappings.json
  tags_unmapped_singles.csv  the one-off tags, for laughs
"""

import csv
import glob
import os
import re
from collections import Counter, defaultdict
from html.parser import HTMLParser

from tag_mapper import classify, norm

UNMAPPED_MIN_COUNT = 2


class FicParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.additional_tags = []

        self._in_title = False
        self._in_additional_tags_dd = False
        self._in_tag_link = False
        self._next_dd_is_tags = False
        self._current_tag_text = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "title":
            self._in_title = True

        elif tag == "dt":
            pass  # handled in data

        elif tag == "dd" and self._next_dd_is_tags:
            self._in_additional_tags_dd = True
            self._next_dd_is_tags = False

        elif tag == "a" and self._in_additional_tags_dd:
            self._in_tag_link = True
            self._current_tag_text = ""

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._in_tag_link:
            self._in_tag_link = False
            if self._current_tag_text:
                self.additional_tags.append(self._current_tag_text.strip())
        elif tag == "dd":
            self._in_additional_tags_dd = False

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()
        elif self._in_tag_link:
            self._current_tag_text += data
        elif data.strip() == "Additional Tags:":
            self._next_dd_is_tags = True


def extract_from_file(filepath):
    with open(filepath, encoding="utf-8", errors="replace") as f:
        html = f.read()

    parser = FicParser()
    parser.feed(html)
    return parser.title, parser.additional_tags


def work_key(filename):
    """Match fics by workID so custom tags survive a filename change."""
    m = re.match(r"^(\d+)_", filename)
    return m.group(1) if m else filename


def load_existing_custom_tags(custom_csv_path):
    """Return a dict of workID -> custom_tags string from an existing tags_review_custom.csv."""
    existing = {}
    if not os.path.exists(custom_csv_path):
        return existing
    # utf-8-sig tolerates the BOM Excel adds when saving
    with open(custom_csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            fname = row.get("filename", "").strip()
            tags = row.get("custom_tags", "").strip()
            if fname:
                existing[work_key(fname)] = tags
    return existing


def write_unmapped(path, entries):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["tag", "count", "example_titles"])
        for tag, count, titles in entries:
            writer.writerow([tag, count, " | ".join(titles[:3])])


def main():
    assets_dir = os.path.dirname(os.path.abspath(__file__))
    archive_dir = os.path.join(assets_dir, "..", "archive")
    html_files = sorted(glob.glob(os.path.join(archive_dir, "*.html")))

    custom_csv_path = os.path.join(assets_dir, "tags_review_custom.csv")
    existing_custom = load_existing_custom_tags(custom_csv_path)
    if existing_custom:
        print(f"Found {len(existing_custom)} existing custom tag entries — merging.")

    output_path = os.path.join(assets_dir, "tags_review.csv")
    unmapped_count = Counter()
    unmapped_titles = defaultdict(list)

    with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["filename", "title", "additional_tags", "auto_tags", "custom_tags"])

        for filepath in html_files:
            filename = os.path.basename(filepath)
            title, tags = extract_from_file(filepath)
            auto, leftover = classify(tags)
            for tag in leftover:
                unmapped_count[norm(tag)] += 1
                unmapped_titles[norm(tag)].append(title)
            tags_str = " | ".join(tags)
            custom_tags = existing_custom.get(work_key(filename), "")
            writer.writerow([filename, title, tags_str, "|".join(auto), custom_tags])

    carried = sum(1 for p in html_files if existing_custom.get(work_key(os.path.basename(p))))
    print(f"Wrote {len(html_files)} rows to {output_path} ({carried} with existing custom tags carried over)")

    ranked = [(t, c, unmapped_titles[t]) for t, c in sorted(unmapped_count.items(), key=lambda kv: (-kv[1], kv[0]))]
    common = [e for e in ranked if e[1] >= UNMAPPED_MIN_COUNT]
    singles = [e for e in ranked if e[1] < UNMAPPED_MIN_COUNT]
    write_unmapped(os.path.join(assets_dir, "tags_unmapped.csv"), common)
    write_unmapped(os.path.join(assets_dir, "tags_unmapped_singles.csv"), singles)
    print(f"Unmapped AO3 tags: {len(common)} on {UNMAPPED_MIN_COUNT}+ fics -> tags_unmapped.csv, "
          f"{len(singles)} one-offs -> tags_unmapped_singles.csv")


if __name__ == "__main__":
    main()
