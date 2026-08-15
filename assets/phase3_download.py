#!/usr/bin/env python3
"""
AO3 File Downloader — Phase 3

Downloads HTML files from AO3 using links collected in Phase 2.
Requires:
  - phase2_download_links.csv  (output of Phase 2 JS script)
  - cookies.json               (exported from Chrome via Cookie-Editor extension)

Usage:
  python phase3_download.py

Install dependencies first:
  pip install requests
"""

import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote


# ==============================================================================
# CONFIGURATION — edit these paths before running
# ==============================================================================

PHASE2_CSV    = 'phase2_download_links (4).csv'   # path to your Phase 2 CSV
COOKIES_FILE  = 'cookies.json'                # path to your exported cookies JSON
OUTPUT_DIR    = '../staging'               # folder where .html files will be saved
SUMMARY_CSV   = 'phase3_summary29260721.csv'          # output summary

DELAY_SECONDS      = 10     # seconds between downloads
RETRY_WAIT_1       = 60     # first retry wait (seconds) after 429
RETRY_WAIT_2       = 120    # second retry wait after 429
MAX_429_TOTAL      = 5      # abort if we hit this many 429s total
REQUEST_TIMEOUT    = 30     # seconds before a request times out

# ==============================================================================


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def load_cookies(cookies_file: str) -> dict:
    """
    Load cookies from a Cookie-Editor JSON export.
    Returns a dict suitable for requests: { name: value, ... }
    """
    with open(cookies_file, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    # Cookie-Editor exports a list of cookie objects with at least 'name' and 'value'
    cookies = {}
    for cookie in raw:
        if 'archiveofourown.org' in cookie.get('domain', ''):
            cookies[cookie['name']] = cookie['value']

    if not cookies:
        print("⚠️  Warning: no archiveofourown.org cookies found in the file.")
        print("   Make sure you exported cookies while on an AO3 page.")
    else:
        print(f"✓ Loaded {len(cookies)} AO3 cookies.")

    return cookies


def load_phase2_csv(csv_path: str) -> list[dict]:
    """
    Load the Phase 2 CSV. Returns only rows with status='success' and a download URL.
    """
    rows = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('status') == 'success' and row.get('download_url'):
                rows.append(row)
    return rows


def extract_work_id(work_url: str) -> str:
    """Extract numeric work ID from a URL like https://archiveofourown.org/works/12345"""
    match = re.search(r'/works/(\d+)', work_url)
    return match.group(1) if match else 'unknown'


def extract_filename_from_url(download_url: str) -> str:
    """
    Extract and decode the original filename from a download URL.
    AO3 download URLs look like:
      /downloads/12345/Some%20Title.html?updated_at=1234567890
    """
    path = urlparse(download_url).path          # e.g. /downloads/12345/Some%20Title.html
    raw_name = path.split('/')[-1]              # e.g. Some%20Title.html
    return unquote(raw_name)                    # e.g. Some Title.html


def extract_updated_at(download_url: str) -> str:
    """Extract the updated_at query parameter from a download URL."""
    qs = parse_qs(urlparse(download_url).query)
    return qs.get('updated_at', [''])[0]


def safe_filename(work_id: str, original_name: str) -> str:
    """
    Build a safe output filename: {work_id}_{original_name}
    Strips characters that are problematic on Windows/Mac/Linux.
    """
    # Remove anything that isn't alphanumeric, space, dot, dash, underscore, or parens
    safe_orig = re.sub(r'[^\w\s\-.()\[\]]', '_', original_name)
    # Collapse multiple underscores/spaces
    safe_orig = re.sub(r'[\s_]+', '_', safe_orig).strip('_')
    return f"{work_id}_{safe_orig}".lower()


def download_file(session, download_url: str, output_path: Path) -> tuple[bool, str]:
    """
    Download a single file. Returns (success: bool, status: str).
    Handles 429 retries internally.
    """
    attempt = 0
    while attempt < 3:
        try:
            response = session.get(download_url, timeout=REQUEST_TIMEOUT, stream=True)
        except Exception as e:
            return False, f'network_error: {e}'

        if response.status_code == 200:
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True, 'success'

        elif response.status_code == 429:
            attempt += 1
            wait = RETRY_WAIT_1 if attempt == 1 else RETRY_WAIT_2
            print(f"  ⚠️  429 Too Many Requests (attempt {attempt}) — waiting {wait}s...")
            time.sleep(wait)

        elif response.status_code in (403, 404):
            return False, f'http_{response.status_code}'

        else:
            return False, f'http_{response.status_code}'

    return False, 'rate_limited_fatal'


def main():
    import requests  # import here so the error is clear if not installed

    # -------------------------------------------------------------------------
    # Validate inputs
    # -------------------------------------------------------------------------
    if not os.path.exists(PHASE2_CSV):
        print(f"❌ Phase 2 CSV not found: {PHASE2_CSV}")
        sys.exit(1)

    if not os.path.exists(COOKIES_FILE):
        print(f"❌ Cookies file not found: {COOKIES_FILE}")
        print("   See README section on exporting cookies with Cookie-Editor.")
        sys.exit(1)

    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # Load data
    # -------------------------------------------------------------------------
    print(f"\n📂 AO3 File Downloader — Phase 3")
    print(f"   Output folder : {output_dir.resolve()}")
    print(f"   Phase 2 CSV   : {PHASE2_CSV}")
    print(f"   Cookies file  : {COOKIES_FILE}\n")

    cookies = load_cookies(COOKIES_FILE)
    rows    = load_phase2_csv(PHASE2_CSV)

    if not rows:
        print("❌ No downloadable rows found in Phase 2 CSV (check status column).")
        sys.exit(1)

    print(f"✓ {len(rows)} files to download.\n")

    # Check which files are already downloaded (resume support)
    already_done = set(f.name for f in output_dir.glob('*.html'))
    print(f"✓ {len(already_done)} files already present — will skip these.\n")

    # -------------------------------------------------------------------------
    # Set up requests session
    # -------------------------------------------------------------------------
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (compatible; personal archiving script)',
    })

    # -------------------------------------------------------------------------
    # Download loop
    # -------------------------------------------------------------------------
    summary = []   # list of dicts for the final CSV
    total_429s = 0

    for i, row in enumerate(rows):
        work_url     = row['work_url']
        download_url = row['download_url']
        work_id      = extract_work_id(work_url)
        orig_name    = extract_filename_from_url(download_url)
        updated_at   = extract_updated_at(download_url)
        out_filename = safe_filename(work_id, orig_name)
        out_path     = output_dir / out_filename

        print(f"[{i+1}/{len(rows)}] {work_id} — {orig_name}")

        # Resume: skip if file exists and has content
        if out_filename in already_done and out_path.stat().st_size > 0:
            print(f"  ⏭️  Already downloaded — skipping.")
            summary.append({
                'work_url'    : work_url,
                'download_url': download_url,
                'filename'    : out_filename,
                'updated_at'  : updated_at,
                'attempted_at': timestamp(),
                'status'      : 'skipped_already_exists',
            })
            continue

        success, status = download_file(session, download_url, out_path)

        if status == 'rate_limited_fatal':
            total_429s += 1
            print(f"  🛑 Fatal rate limit on {work_url}")

        if success:
            print(f"  ✓ Saved: {out_filename}")
        else:
            print(f"  ✗ Failed ({status}): {work_url}")
            # Clean up empty/partial file
            if out_path.exists() and out_path.stat().st_size == 0:
                out_path.unlink()

        summary.append({
            'work_url'    : work_url,
            'download_url': download_url,
            'filename'    : out_filename if success else '',
            'updated_at'  : updated_at,
            'attempted_at': timestamp(),
            'status'      : status,
        })

        if total_429s >= MAX_429_TOTAL:
            print(f"\n🛑 Hit {MAX_429_TOTAL} fatal rate limit events — aborting.")
            break

        if i < len(rows) - 1:
            print(f"  ⏳ Waiting {DELAY_SECONDS}s...")
            time.sleep(DELAY_SECONDS)

    # -------------------------------------------------------------------------
    # Write summary CSV
    # -------------------------------------------------------------------------
    fieldnames = ['work_url', 'download_url', 'filename', 'updated_at', 'attempted_at', 'status']
    with open(SUMMARY_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary)

    successes = sum(1 for r in summary if r['status'] == 'success')
    skipped   = sum(1 for r in summary if r['status'] == 'skipped_already_exists')
    failures  = sum(1 for r in summary if r['status'] not in ('success', 'skipped_already_exists'))

    print(f"\n{'='*60}")
    print(f"✅ Phase 3 complete.")
    print(f"   Downloaded : {successes}")
    print(f"   Skipped    : {skipped}  (already existed)")
    print(f"   Failed     : {failures}")
    print(f"   Summary    : {SUMMARY_CSV}")
    print(f"   Files in   : {output_dir.resolve()}")


if __name__ == '__main__':
    main()
