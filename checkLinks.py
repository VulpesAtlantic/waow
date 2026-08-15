#!/usr/bin/env python3
"""
Check all <a href> links in an HTML file and report which ones fail.

Usage:
    python check_links.py index.html
    python check_links.py index.html --workers 20 --timeout 10
"""

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests


class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            for name, value in attrs:
                if name.lower() == "href" and value:
                    self.links.append(value)


def extract_links(html_path, base_url=None):
    with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    parser = LinkExtractor()
    parser.feed(content)
    # resolve relative links against base_url if given
    resolved = [urljoin(base_url, link) if base_url else link for link in parser.links]
    # de-dupe while preserving order
    seen = set()
    ordered = []
    for link in resolved:
        if link not in seen:
            seen.add(link)
            ordered.append(link)
    return ordered


def check_link(url, timeout):
    # Try HEAD first (cheap); fall back to GET if server doesn't support HEAD well
    try:
        resp = requests.head(url, allow_redirects=True, timeout=timeout)
        if resp.status_code >= 400 or resp.status_code == 405:
            resp = requests.get(url, allow_redirects=True, timeout=timeout, stream=True)
        return url, resp.status_code, None, resp.url
    except requests.exceptions.RequestException as e:
        return url, None, type(e).__name__, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html_file", help="Path to the HTML file containing the links")
    ap.add_argument("--base-url", default=None,
                     help="Base URL to resolve relative hrefs against, "
                          "e.g. https://agathario.github.io/ficArchive/")
    ap.add_argument("--workers", type=int, default=20, help="Concurrent requests (default 20)")
    ap.add_argument("--timeout", type=int, default=10, help="Per-request timeout in seconds")
    ap.add_argument("--out", default="link_check_results.csv", help="CSV output path")
    args = ap.parse_args()

    links = extract_links(args.html_file, args.base_url)
    if not links:
        print("No <a href> links found.")
        sys.exit(0)

    print(f"Found {len(links)} unique links. Checking with {args.workers} workers...\n")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(check_link, url, args.timeout): url for url in links}
        done = 0
        for fut in as_completed(futures):
            url, status, error, final_url = fut.result()
            results.append((url, status, error, final_url))
            done += 1
            if done % 25 == 0 or done == len(links):
                print(f"  checked {done}/{len(links)}...")

    # Sort: failures first
    def is_bad(row):
        _, status, error, _ = row
        return error is not None or (status is not None and status >= 400)

    results.sort(key=lambda r: (not is_bad(r), r[0]))

    bad = [r for r in results if is_bad(r)]
    good = [r for r in results if not is_bad(r)]

    print(f"\n=== Summary ===")
    print(f"OK:     {len(good)}")
    print(f"BROKEN: {len(bad)}")

    if bad:
        print(f"\n=== Broken links ===")
        for url, status, error, final_url in bad:
            reason = error if error else f"HTTP {status}"
            print(f"  [{reason}] {url}")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["url", "status_code", "error", "final_url"])
        for row in results:
            writer.writerow(row)

    print(f"\nFull results written to {args.out}")


if __name__ == "__main__":
    main()