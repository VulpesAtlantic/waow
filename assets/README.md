# AO3 Bookmark Downloader — Setup & Usage

A pipeline for archiving your AO3 bookmarks as HTML files: Phases 1–3 collect and download them into `staging/`, then Phase 4 (`phase4_process.py`) processes them into `archive/`.

---

## Files

| File | What it does |
|---|---|
| `phase1_bookmarks.js` | Collects work URLs from your bookmarks pages |
| `phase2_download_links.js` | Visits each work and grabs the HTML download link |
| `phase3_download.py` | Downloads the HTML files into `staging/` using your exported cookies |

---

## One-time setup

### 1. Install the Cookie-Editor extension

Install **Cookie-Editor** from the Chrome Web Store:
https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm

### 2. Export your AO3 cookies

1. Log into AO3 in Chrome as normal.
2. Click the Cookie-Editor extension icon in your toolbar.
3. Click **Export → Export as JSON**.
4. Save the file as **`cookies.json`** in the same folder as `phase3_download.py`.

> You'll need to re-export cookies if your session expires between runs.

### 3. Install Python dependencies

```bash
pip install requests beautifulsoup4
```

(`requests` is for Phase 3 downloads; `beautifulsoup4` is for processing.)

---

## Running the pipeline

### Phase 1 — Collect bookmark URLs

1. Go to `https://archiveofourown.org/users/willowphile/bookmarks` in Chrome while logged in.
2. Open DevTools → Console (`F12` or `Cmd+Option+J`).
3. **Edit the config at the top of `phase1_bookmarks.js`:**
   - `START_PAGE` — which page to start on (use `1` for a fresh run, or whatever page you left off on)
   - `END_PAGE` — the last page number you want to collect. Check your bookmarks page for the total page count; set this to that number. The script will also stop automatically when it finds an empty page.
4. Paste the entire script into the console and press Enter.
5. Let it run. It will log progress and auto-download **`phase1_bookmarks.csv`** when done (or if it has to abort).

**Resuming Phase 1:** Set `START_PAGE` to the page after the last successful one in your CSV.

---

### Phase 2 — Get download links

1. Stay on AO3 in Chrome (still logged in).
2. Open **`phase2_download_links.js`** in a text editor.
3. Open `phase1_bookmarks.csv` in a text editor and copy its entire contents.
4. Paste the CSV content into the `PHASE1_CSV` variable in the script (between the backticks).
5. Paste the whole script into the DevTools console and press Enter.
6. Auto-downloads **`phase2_download_links.csv`** when done.

**Resuming Phase 2:** The script processes URLs in order. Find the last successful `work_url` in your partial CSV, remove all rows at and before it from `phase1_bookmarks.csv`, and re-run with the trimmed CSV pasted in. The output CSV will be appended to manually — or just re-run from scratch if it's not too many.

> **Note:** Some works will show `no_download_link` — this usually means the work is locked to logged-in users only and the download wasn't available, or the work has been deleted since you bookmarked it. These rows are excluded from Phase 3 automatically.

---

### Phase 3 — Download HTML files

1. Put your Phase 2 CSV and `cookies.json` in `assets/` (next to `phase3_download.py`).
2. Edit the config at the top of `phase3_download.py`. All paths are relative to `assets/`:
   - `PHASE2_CSV` — the Phase 2 CSV for this batch (e.g. `phase2_download_links (4).csv`)
   - `SUMMARY_CSV` — name for this run's summary (give each run its own name so you don't overwrite the last one)
   - `OUTPUT_DIR` — leave as `../staging` so downloads go straight into the project's `staging/` folder
3. Run it (works from any folder):
   ```bash
   python assets/phase3_download.py
   ```
4. Files are saved **directly to `staging/`**, already in the archive's naming format: `{workID}_{slug}.html`, all lowercase (e.g. `62107414_crimson.html`). No renaming step needed.
5. A summary CSV is written to `assets/` under the `SUMMARY_CSV` name.
6. Go straight to **Step 1 — Process new fics** below.

**Errors while downloading:** `525` (Cloudflare "SSL handshake failed"), other 5xx errors and timeouts mean AO3's servers are overloaded, not that anything's wrong on your end. The script waits and retries those twice (30s, then 90s) before giving up on a fic, and stops the run early if 10 fics in a row fail (AO3 is probably down). A `429` means you're being rate-limited; the script backs off for 1–2 minutes. `403`/`404` fail immediately (locked or deleted work).

**Resuming Phase 3:** Just re-run the script. It skips files that are already in `staging/`. Once `phase4_process.py` has moved them into `archive/`, they're no longer in `staging/`, so re-running the same CSV after processing will download them again. That's harmless: processing keeps whichever copy has the higher word count.

---

## Output files

| File | Contents |
|---|---|
| `phase1_bookmarks.csv` | `work_url`, `collected_at`, `status` |
| `phase2_download_links.csv` | `work_url`, `download_url`, `collected_at`, `status` |
| `phase3_summary*.csv` (name set in config) | `work_url`, `download_url`, `filename`, `updated_at`, `attempted_at`, `status` |
| `../staging/*.html` | The downloaded fic files, named `{workID}_{slug}.html`, waiting for processing |

The `updated_at` column in the Phase 3 summary contains the Unix timestamp from AO3's download URL — this is the last time the work was updated, and is useful for detecting new chapters on future runs.

---

## Processing & archiving downloaded fics

After Phase 3 you have raw AO3 HTML files in `staging/`. The scripts below live in `assets/` and turn those into a clean, browsable archive.

### Which file does what

| File | What it is | Edit by hand? |
|---|---|---|
| `phase4_process.py` | Processes new fics from `staging/` into `archive/` | No (code) |
| `reprocess.py` | Re-runs the processing on everything already in `archive/` | No (code) |
| `extract_tags.py` | Builds `tags_review.csv` so you can review AO3 tags and assign custom tags | No (code) |
| `tags_review.csv` | Generated worksheet: AO3 tags + your current custom tags | Work in it, then save as `tags_review_custom.csv` |
| `tags_review_custom.csv` | **Source of truth for custom tags** | **Yes** |
| `tag_mappings.json` | Rules that turn AO3 tags into custom tags (`exact`, `keyword`, `ignore`) | **Yes** |
| `tag_mapper.py` | Applies `tag_mappings.json`; used by the scripts above | No (code) |
| `tags_unmapped.csv` | Generated: AO3 tags on 2+ fics that no rule maps or ignores | No — review it, then edit `tag_mappings.json` |
| `tags_unmapped_singles.csv` | Generated: the one-off unmapped tags, for laughs | No |
| `custom_summaries.csv` | **Source of truth for custom summaries**; auto-refreshed on every run | **Yes** (only the `custom_summary` column) |
| `../fic_data.json` | Manifest the index reads from. Rebuilt on every run | **No** — edits get overwritten |
| `../index.html` | The archive homepage. Rebuilt on every run | **No** — edits get overwritten |

Both override CSVs are matched to fics by **work ID** (the number at the start of the filename), so renaming a file doesn't lose its custom tags or summary.

> **Retired:** `apply_custom_tags.py` (the pipeline reads `tags_review_custom.csv` itself now) and `extract_summaries.py` (replaced by `custom_summaries.csv`). They're harmless but no longer needed.

---

### Step 1 — Process new fics

Phase 3 already puts downloads in `staging/` (you can also drop AO3 HTML files in by hand), then run:

```bash
python assets/phase4_process.py
```

For each file in `staging/` it will:
- Extract metadata (title, author, ship, rating, status, word count, summary) from the AO3 HTML
- Apply your custom tags (`tags_review_custom.csv`) and custom summary (`custom_summaries.csv`), if any
- Back up the original to `originals/`
- Strip AO3 styles/scripts, inject `darkMode.css`
- Handle duplicate work IDs — keeps whichever version has the higher word count
- Write the cleaned file to `archive/`
- Update `fic_data.json`, refresh `custom_summaries.csv`, and rebuild `index.html`

Files are left in `staging/` only if processing fails. Processed files are moved to `archive/` automatically.

---

### Step 2 — Custom tags

1. Build the worksheet:
   ```bash
   python assets/extract_tags.py
   ```
   Scans `archive/` and writes `assets/tags_review.csv` — one row per fic with its title, AO3 additional tags, and any custom tags you've already assigned (carried over from `tags_review_custom.csv`).
2. Open `tags_review.csv`, fill in / edit the `custom_tags` column (pipe-delimited, e.g. `Angst|Slow Burn`), and **save it as `tags_review_custom.csv`** (overwrite the old one).
3. Apply the changes:
   ```bash
   python assets/reprocess.py
   ```

**Auto tags.** A fic with nothing in `custom_tags` gets tags mapped automatically from its AO3 tags using `tag_mappings.json`, so new fics are tagged without any hand work. The `auto_tags` column in `tags_review.csv` shows what each fic would get. Once you fill in `custom_tags` for a fic, those are used *exactly* and auto tags are ignored for it. Everything is lowercase.

**Growing the mappings.** `extract_tags.py` also writes `tags_unmapped.csv`: every AO3 tag on 2+ fics that no rule maps or ignores, most common first. For each one, either:
- add it to `exact` (whole tag → custom tag), or `keyword` (any tag containing that word → custom tag), or
- add it to `ignore` if it's not worth mapping, so it stops showing up.

Then re-run `extract_tags.py` and the list gets shorter. Rules are checked in the order ignore → exact → keyword.

---

### Step 3 — Custom summaries

`custom_summaries.csv` is kept up to date automatically — every run of `phase4_process.py` or `reprocess.py` rewrites it with every fic in the archive. No extract step needed.

1. Open `assets/custom_summaries.csv`. Columns: `filename`, `title`, `summary_words`, `ao3_summary`, `custom_summary`.
2. Sort by `summary_words` to find the wordy ones. Write your shorter version in `custom_summary`. Leave it blank to keep AO3's summary.
3. Save (keep it as CSV), close the file, and run:
   ```bash
   python assets/reprocess.py
   ```

Only the `custom_summary` column is yours — the other columns are overwritten on each run. The original AO3 text is always kept in `ao3_summary` (here and in `fic_data.json`), so clearing a custom summary brings the original back. If a fic leaves the archive, its row is kept at the bottom as long as it has a custom summary.

> Close the CSV before running the scripts. If it's open elsewhere and can't be rewritten, the run still works but prints a warning that the CSV wasn't refreshed.

---

### Reprocessing existing fics

```bash
python assets/reprocess.py
```

Re-extracts metadata for every file in `archive/`, re-injects meta tags, re-applies custom tags and summaries from the two CSVs, then rebuilds `fic_data.json` and `index.html` from scratch. Run it after:
- editing `tags_review_custom.csv` or `custom_summaries.csv`
- changing the parsing/cleaning logic in `phase4_process.py`
- hand-fixing an archive file (e.g. wrapping a summary in `<blockquote>` so it gets picked up)

Safe to re-run as many times as needed. It rewrites every archive file, so expect a big git diff; the story text itself doesn't change.

---

## Tips

- **How long will this take?** With 10-second delays, 100 bookmarks = ~17 minutes for Phase 1, ~17 minutes for Phase 2, ~17 minutes for Phase 3. Plan for an hour total, plus any retry waits.
- **Don't use AO3 in the same browser tab** while the console scripts are running — it won't break anything but the extra requests won't help with rate limits.
- **The `updated_at` timestamp** is your friend for future incremental runs: compare it against your last run's summary CSV to find only works that have been updated since.
- **Keep your CSVs.** They're your paper trail and your resume points.
