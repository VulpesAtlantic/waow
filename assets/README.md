# AO3 Bookmark Downloader — Setup & Usage

A three-phase pipeline for archiving your AO3 bookmarks as HTML files.

---

## Files

| File | What it does |
|---|---|
| `phase1_bookmarks.js` | Collects work URLs from your bookmarks pages |
| `phase2_download_links.js` | Visits each work and grabs the HTML download link |
| `phase3_download.py` | Downloads the HTML files using your exported cookies |

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

### 3. Install Python dependency

```bash
pip install requests
```

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

1. Make sure `phase2_download_links.csv` and `cookies.json` are in the same folder as `phase3_download.py`.
2. Edit the config at the top of `phase3_download.py` if needed (paths, output folder name).
3. Run:
   ```bash
   python phase3_download.py
   ```
4. Files are saved to `ao3_downloads/` with names like `12345678_Some_Title.html`.
5. A summary CSV is written to `phase3_summary.csv`.

**Resuming Phase 3:** Just re-run the script. It checks which files already exist in `ao3_downloads/` and skips them automatically.

---

## Output files

| File | Contents |
|---|---|
| `phase1_bookmarks.csv` | `work_url`, `collected_at`, `status` |
| `phase2_download_links.csv` | `work_url`, `download_url`, `collected_at`, `status` |
| `phase3_summary.csv` | `work_url`, `download_url`, `filename`, `updated_at`, `attempted_at`, `status` |
| `ao3_downloads/*.html` | The actual fic files, named `{work_id}_{Title}.html` |

The `updated_at` column in the Phase 3 summary contains the Unix timestamp from AO3's download URL — this is the last time the work was updated, and is useful for detecting new chapters on future runs.

---

## Processing & archiving downloaded fics

After Phase 3 you have raw AO3 HTML files in `ao3_downloads/`. The scripts below live in `assets/` and turn those into a clean, browsable archive.

---

### Step 1 — Process new fics

Drop the downloaded files into the `staging/` folder at the project root, then run:

```bash
python assets/process.py
```

For each file in `staging/` it will:
- Extract metadata (title, author, ship, rating, status, word count, summary) from the AO3 HTML
- Back up the original to `originals/`
- Strip AO3 styles/scripts, inject `darkMode.css`
- Handle duplicate work IDs — keeps whichever version has the higher word count
- Write the cleaned file to `archive/`
- Update `fic_data.json` and rebuild `index.html`

Files are left in `staging/` only if processing fails. Processed files are moved to `archive/` automatically.

---

### Step 2 — Tag your fics

#### Extract AO3 tags

```bash
python assets/extract_tags.py
```

Scans `archive/` and writes `assets/tags_review.csv` with each fic's filename, title, and AO3 additional tags. If `tags_review_custom.csv` already exists, any custom tags you previously assigned are carried over to matching filenames automatically.

#### Add custom tags

Open `tags_review.csv`, fill in the `custom_tags` column for any fics you want to tag (pipe-delimited, e.g. `angst | slow burn`), and save it as `tags_review_custom.csv`.

#### Apply custom tags to the archive

```bash
python assets/apply_custom_tags.py
```

Reads `tags_review_custom.csv`, writes the `custom_tags` into `fic_data.json`, and rebuilds `index.html`. Safe to re-run any time you update the CSV.

---

### Reprocessing existing fics

If you update the parsing or cleaning logic in `process.py` and want to backfill the change across everything already in `archive/`:

```bash
python assets/reprocess.py
```

This re-extracts metadata and re-injects meta tags for every file in `archive/`, then rebuilds `fic_data.json` and `index.html` from scratch. Safe to re-run as many times as needed. Note: run `apply_custom_tags.py` afterward to restore your custom tags, since reprocess rebuilds the manifest fresh.

---

## Tips

- **How long will this take?** With 10-second delays, 100 bookmarks = ~17 minutes for Phase 1, ~17 minutes for Phase 2, ~17 minutes for Phase 3. Plan for an hour total, plus any retry waits.
- **Don't use AO3 in the same browser tab** while the console scripts are running — it won't break anything but the extra requests won't help with rate limits.
- **The `updated_at` timestamp** is your friend for future incremental runs: compare it against your last run's summary CSV to find only works that have been updated since.
- **Keep your CSVs.** They're your paper trail and your resume points.
