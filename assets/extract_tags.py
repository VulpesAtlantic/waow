#!/usr/bin/env python3
"""Extract Additional Tags from archived fic HTML files into a CSV."""

import csv
import glob
import os
from html.parser import HTMLParser


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


def main():
    archive_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "archive")
    html_files = sorted(glob.glob(os.path.join(archive_dir, "*.html")))

    output_path = os.path.join(os.path.dirname(__file__), "tags_review.csv")

    with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["filename", "title", "additional_tags"])

        for filepath in html_files:
            filename = os.path.basename(filepath)
            title, tags = extract_from_file(filepath)
            tags_str = " | ".join(tags)
            writer.writerow([filename, title, tags_str])

    print(f"Wrote {len(html_files)} rows to {output_path}")


if __name__ == "__main__":
    main()
