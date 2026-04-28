"""
Run from any folder containing AO3 HTML files:
    python path/to/extract_summaries.py

Writes summaries.csv in the current working directory with:
    filename, created, last_updated, summary
"""

import csv
import os
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup


def get_summary(soup: BeautifulSoup) -> str:
    for p in soup.find_all("p"):
        if p.get_text(strip=True) == "Summary":
            sib = p.find_next_sibling()
            if sib and sib.name == "blockquote":
                return sib.get_text(separator=" ", strip=True)
    return ""


def main():
    folder = Path.cwd()
    rows = []

    for html_file in sorted(folder.glob("*.html")):
        if html_file.name == "index.html":
            continue

        stat = html_file.stat()
        created = datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
        updated = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

        try:
            text = html_file.read_text(encoding="utf-8")
            soup = BeautifulSoup(text, "html.parser")
            summary = get_summary(soup)
        except Exception as e:
            print(f"  Error reading {html_file.name}: {e}")
            summary = ""

        rows.append({
            "filename": html_file.name,
            "created": created,
            "last_updated": updated,
            "summary": summary,
        })
        print(f"  {html_file.name}: {'(no summary found)' if not summary else summary[:60] + '...' if len(summary) > 60 else summary}")

    out = folder / "summaries.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["filename", "created", "last_updated", "summary"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
