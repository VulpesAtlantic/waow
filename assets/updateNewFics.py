"""
Fanfic Archive Processor & Index Builder

This script normalizes, deduplicates, and indexes a local HTML fanfic archive.

What it does, end to end:
- Iterates over all HTML files in the target folder (excluding index.html).
- Parses each file with BeautifulSoup to clean up and standardize metadata:
    * Trims <title> to just the story title (everything before the first " - ").
    * Extracts author (when present), rating, and completion status.
    * Injects or updates meta tags for: author, ship, rating, status, lastUpdated.
    * Removes inline <style> tags and enforces a shared darkMode.css stylesheet.
- Renames each HTML file to a deterministic, filesystem-safe filename derived
  from the story title (slugified with underscores).
    * Avoids Windows-reserved filenames.
    * Detects true duplicates via SHA-256 hashing and removes redundant copies.
    * Handles filename collisions by overwriting or suffixing as needed.
- Logs all actions (renames, updates, dedupes, errors) to logs/renamer.log.
- Builds a responsive, filterable index.html:
    * One “card” per fic with title, author, ship, rating, status, and summary.
    * Client-side filtering by author, ship, rating, status, and text search.
    * Uses a dark-mode-first UI optimized for long-term local browsing.

Intended use:
- Run whenever new AO3 downloads are added or metadata conventions change.
- Keeps the archive tidy, consistently named, deduplicated, and browseable
  without relying on external tools or AO3 itself.

In short: this script is the librarian, archivist, and interior designer
for your fanfic hoard.
"""

import os
import re
import unicodedata
import logging
from bs4 import BeautifulSoup
from pathlib import Path
import hashlib
from datetime import date, datetime
import time

# =========================
# Logging setup
# =========================
def setup_logging(log_dir: str = "logs") -> Path:
    """
    Creates a timestamped log file per run and configures:
      - File handler: INFO+ (full detail)
      - Console handler: INFO+ (or WARNING+ if you prefer)
    Returns the log file path for reference.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"renamer_{run_id}.log"

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Clear existing handlers if re-running in same interpreter (e.g., notebook)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # File: keep everything
    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(fmt)

    # Console: show progress + important stuff
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)   # change to logging.WARNING if you only want warnings/errors
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logging.info(f"Logging to: {log_path}")
    return log_path


LOG_FILE = setup_logging()

# =========================
# Config
# =========================
FOLDER_PATH = r"C:\Users\willo\OneDrive\Documents\GitHub\fics\ficArchive"
OUTPUT_FILE = "index.html"

# Windows reserved base names to avoid
_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
}


# =========================
# Filename helpers
# =========================
def slugify_underscores(s: str) -> str:
    """
    Convert a title to a safe file slug:
    - normalize accents to ASCII
    - lower-case
    - replace '&' with 'and' (optional, improves readability)
    - strip punctuation except underscores, hyphens
    - whitespace -> single underscore
    - compress repeated underscores
    - trim leading/trailing separators
    """
    if not s:
        return "untitled"

    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^\w\s\-]", "", s)  # keep word chars, whitespace, hyphen
    s = re.sub(r"\s+", "_", s)  # spaces -> underscore
    s = re.sub(r"_+", "_", s)  # collapse ___ -> _
    s = s.strip("_-")
    if not s:
        s = "untitled"
    if s in _RESERVED:
        s = f"file_{s}"
    return s


def ensure_unique_path(directory: str, base_slug: str, ext: str = ".html") -> str:
    """Return a unique path by appending _2, _3, ... if needed."""
    candidate = os.path.join(directory, base_slug + ext)
    if not os.path.exists(candidate):
        return candidate
    i = 2
    while True:
        candidate = os.path.join(directory, f"{base_slug}_{i}{ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


# =========================
# HTML processing
# =========================
def update_html_metadata(file_path: str):
    """
    Update title & meta tags and return the story_title to be used for renaming.
    - Keeps only the first segment before ' - ' in <title> as the displayed <title>.
    - Writes/updates meta: author, ship, rating, status.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f, "html.parser")
    except Exception as e:
        logging.error(f"{os.path.basename(file_path)}: failed to open/parse: {e}")
        return None

    head = soup.head
    if not head:
        logging.warning(
            f"{os.path.basename(file_path)}: no <head>; skipping meta update"
        )
        return None

    # ---- Title & (optional) author/universe extraction
    story_title = None
    author = None
    if soup.title and soup.title.string:
        full_title = soup.title.string.strip()
        # Everything before the first " - " is the story title for both display and filename
        story_title = full_title.split(" - ", 1)[0].strip()

        # If you *also* want to derive author/universe when present:
        parts = [p.strip() for p in full_title.split(" - ")]
        author = parts[1] if len(parts) >= 2 else None

        # Set the <title> tag to just story_title
        soup.title.string = story_title

        # Meta: author (only if we found one)
        if author:
            author_meta = head.find("meta", attrs={"name": "author"})
            if author_meta:
                author_meta["content"] = author
            else:
                head.append(
                    soup.new_tag("meta", attrs={"name": "author", "content": author})
                )

        # Meta: ship (static, as in your script)
        ship_meta = head.find("meta", attrs={"name": "ship"})
        if ship_meta:
            ship_meta["content"] = "Agatha Harkness/Rio Vidal"
        else:
            head.append(
                soup.new_tag(
                    "meta",
                    attrs={"name": "ship", "content": "Agatha Harkness/Rio Vidal"},
                )
            )
    else:
        logging.warning(
            f"{os.path.basename(file_path)}: no <title>; will fall back to filename for rename"
        )

    # ---- Styles: remove inline <style>, ensure darkMode.css link
    for style_tag in head.find_all("style"):
        style_tag.decompose()
    if not head.find("link", href="assets/darkMode.css"):
        head.append(soup.new_tag("link", rel="stylesheet", href="assets/darkMode.css"))

        # ---- lastUpdated meta (YYYYMMDD; today's date)
        last_updated = date.today().strftime("%Y%m%d")
        lu_meta = head.find("meta", attrs={"name": "lastUpdated"})
        if lu_meta:
            lu_meta["content"] = last_updated
        else:
            head.append(
                soup.new_tag(
                    "meta", attrs={"name": "lastUpdated", "content": last_updated}
                )
            )

    # ---- Rating extraction
    rating_dt = soup.find("dt", string="Rating:")
    if rating_dt:
        rating_dd = rating_dt.find_next_sibling("dd")
        if rating_dd:
            rating_a = rating_dd.find("a")
            if rating_a and rating_a.string:
                rating_text = rating_a.string.strip()
                rating_meta = head.find("meta", attrs={"name": "rating"})
                if rating_meta:
                    rating_meta["content"] = rating_text
                else:
                    head.append(
                        soup.new_tag(
                            "meta", attrs={"name": "rating", "content": rating_text}
                        )
                    )

    # ---- Words extraction from Stats block
    stats_dt = soup.find("dt", string=lambda s: s and s.strip() == "Stats:")
    if stats_dt:
        stats_dd = stats_dt.find_next_sibling("dd")
        if stats_dd:
            stats_text = stats_dd.get_text(" ", strip=True)
            m = re.search(r"\bWords:\s*([\d,]+)\b", stats_text)
            if m:
                words = m.group(1).replace(",", "")
                words_meta = head.find("meta", attrs={"name": "words"})
                if words_meta:
                    words_meta["content"] = words
                else:
                    head.append(
                        soup.new_tag("meta", attrs={"name": "words", "content": words})
                    )

    # ---- Status extraction
    status_text = "Completed" if "Completed: 20" in soup.get_text() else "Incomplete"
    status_meta = head.find("meta", attrs={"name": "status"})
    if status_meta:
        status_meta["content"] = status_text
    else:
        head.append(
            soup.new_tag("meta", attrs={"name": "status", "content": status_text})
        )

    # Save file
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(str(soup))
        logging.info(f"Updated metadata: {os.path.basename(file_path)}")
    except Exception as e:
        logging.error(
            f"{os.path.basename(file_path)}: failed to write updated HTML: {e}"
        )

    return story_title, author


def _file_sha256(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def rename_file_using_title(
    file_path: str,
    story_title: str,
    author: str = None,
    *,
    overwrite=True,
    keep_backup=False,
):
    """
    Rename/move file to a deterministic '{slug}.html' name derived from story_title.
    If a file with that name already exists:
      - If identical content: delete the source (dedupe) and keep the existing target.
      - If different content:
          * overwrite=True   -> replace the existing target (atomic via os.replace)
          * overwrite=False  -> fall back to suffixing (_2, _3, ...)
    Optional: keep a timestamped backup of the target before overwriting.
    """
    directory, old_name = os.path.split(file_path)
    base_title = story_title if story_title else os.path.splitext(old_name)[0]

    if author:
        base = f"{base_title}__{author}"
    else:
        base = base_title

    slug = slugify_underscores(base)
    target = os.path.join(directory, f"{slug}.html")

    # Normalize for Windows case-insensitive paths
    src_abs = os.path.normcase(os.path.abspath(file_path))
    tgt_abs = os.path.normcase(os.path.abspath(target))

    # 1) Nothing to do if already the canonical name
    if src_abs == tgt_abs:
        return file_path

    # 2) If the target doesn't exist, do a straight rename
    if not os.path.exists(target):
        os.rename(file_path, target)
        logging.info(f"Renamed {old_name} -> {os.path.basename(target)}")
        return target

    # 3) Target exists: compare contents
    try:
        src_hash = _file_sha256(file_path)
        tgt_hash = _file_sha256(target)
    except Exception as e:
        logging.warning(
            f"Hash compare failed ({old_name} vs {os.path.basename(target)}): {e}"
        )
        src_hash = tgt_hash = None

    if src_hash and tgt_hash and src_hash == tgt_hash:
        # Identical: remove the duplicate source and keep canonical target
        try:
            os.remove(file_path)
            logging.info(
                f"Duplicate detected. Removed {old_name}; kept {os.path.basename(target)}"
            )
        except Exception as e:
            logging.error(f"Failed to remove duplicate {old_name}: {e}")
            return file_path
        return target

    # 4) Different content but same slug: either overwrite or suffix
    if overwrite:
        # Optional backup
        if keep_backup:
            import time

            name, ext = os.path.splitext(target)
            backup = f"{name}.backup_{time.strftime('%Y%m%d-%H%M%S')}{ext}"
            try:
                os.replace(target, backup)
                logging.info(f"Backed up existing to {os.path.basename(backup)}")
            except Exception as e:
                logging.error(f"Backup failed for {os.path.basename(target)}: {e}")
                # If backup fails, proceed with overwrite anyway (or bail out if you prefer)

        # Atomic overwrite on same filesystem
        try:
            os.replace(file_path, target)
            logging.info(
                f"Overwrote {os.path.basename(target)} with updated {old_name}"
            )
            return target
        except Exception as e:
            logging.error(f"Failed to overwrite {os.path.basename(target)}: {e}")
            return file_path
    else:
        # Fall back to suffixing behavior
        new_path = ensure_unique_path(directory, slug, ext=".html")
        try:
            os.rename(file_path, new_path)
            logging.info(
                f"Collision kept both: {old_name} -> {os.path.basename(new_path)}"
            )
            return new_path
        except Exception as e:
            logging.error(f"{old_name}: failed to rename on collision: {e}")
            return file_path


# =========================
# Index build
# =========================
def build_index(directory: str, output_file: str):
    cards = []

    for filename in os.listdir(directory):
        if filename.endswith(".html") and filename != output_file:
            full_path = os.path.join(directory, filename)
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    soup = BeautifulSoup(f, "html.parser")

                title = (
                    soup.title.string.strip()
                    if soup.title and soup.title.string
                    else filename
                )
                author_tag = soup.find("meta", attrs={"name": "author"})
                ship_tag = soup.find("meta", attrs={"name": "ship"})
                rating_tag = soup.find("meta", attrs={"name": "rating"})
                status_tag = soup.find("meta", attrs={"name": "status"})
                words_tag = soup.find("meta", attrs={"name": "words"})
                summary_tag = soup.find("meta", attrs={"name": "summary"}) or soup.find(
                    "meta", attrs={"name": "description"}
                )

                author = (
                    author_tag["content"].strip()
                    if author_tag and "content" in author_tag.attrs
                    else "Unknown"
                )
                ship = (
                    ship_tag["content"].strip()
                    if ship_tag and "content" in ship_tag.attrs
                    else "Unknown"
                )
                rating = (
                    rating_tag["content"].strip()
                    if rating_tag and "content" in rating_tag.attrs
                    else "Unknown"
                )
                status = (
                    status_tag["content"].strip()
                    if status_tag and "content" in status_tag.attrs
                    else "Unknown"
                )
                words = (
                    words_tag["content"].strip()
                    if words_tag and "content" in words_tag.attrs
                    else ""
                )

                summary = (
                    summary_tag["content"].strip()
                    if summary_tag and "content" in summary_tag.attrs
                    else ""
                )
                
                words_pill = ""
                if words.isdigit():
                    words_pill = f'<span class="pill">Words: {int(words):,}</span>'
                    
                cards.append(
                    f"""
                <div class="card"
                    data-href="{filename}"
                    data-author="{author.lower()}"
                    data-ship="{ship.lower()}"
                    data-rating="{rating.lower()}"
                    data-status="{status.lower()}">
                    <h2><a class="title-link" href="{filename}">{title}</a></h2>
                    <div class="meta">
                        <span class="pill label-pill">Author: {author}</span>
                        <span class="pill">{ship}</span>
                        <span class="pill">{rating}</span>
                        <span class="pill">{status}</span>
                        {words_pill}
                    </div><p class="summary">{summary}</p>
                </div>
                """
                )

            except Exception as e:
                logging.error(f"Failed to add {filename} to index: {e}")

    index_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Fanfic Archive Index</title>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="assets/darkMode.css">
    <style>
        :root {{
            --bg: #020308;
            --bg-soft: #0b0f19;
            --bg-card: #111827;
            --border-subtle: #1f2937;
            --accent: #bb86fc;
            --accent-soft: rgba(187,134,252,0.1);
            --text-main: #f9fafb;
            --text-soft: #9ca3af;
            --radius-xl: 18px;
            --radius-pill: 999px;
            --shadow-soft: 0 8px 20px rgba(0,0,0,0.6);
            --transition-fast: 0.18s ease-out;
            --font-base: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }}

        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            padding: 0;
            font-family: var(--font-base);
            background-color: var(--bg);
            color: var(--text-main);
        }}

        .page-wrap {{
            padding: 0.75rem 0.75rem 1.25rem;
        }}

        h1 {{
            font-size: 1.4rem;
            margin: 0 0 0.15rem;
            font-weight: 600;
            letter-spacing: 0.02em;
        }}
        .card {{
            cursor: pointer;
            }}

            .card:focus-within,
            .card:hover {{
            transform: scale(1.02);
            }}

        .subtitle {{
            margin: 0 0 0.75rem;
            font-size: 0.75rem;
            color: var(--text-soft);
        }}

        .filters-wrap {{
            position: sticky;
            top: 0;
            z-index: 20;
            padding-bottom: 0.5rem;
            margin: 0 -0.75rem 0.5rem;
            background: linear-gradient(to bottom,
                        rgba(2,3,8,0.98),
                        rgba(2,3,8,0.96),
                        rgba(2,3,8,0.9));
            backdrop-filter: blur(12px);
        }}

        .filter-panel {{
        border: 1px solid #444;
        border-radius: 10px;
        padding: 0.5rem 0.75rem;
        margin-bottom: 1rem;
        background: rgba(255,255,255,0.02);
        position: sticky;
  top: 0.75rem;
  z-index: 10;
  backdrop-filter: blur(6px);
        }}

        .filter-panel > summary {{
        cursor: pointer;
        font-weight: 600;
        padding: 0.25rem 0;
        user-select: none;
        }}

        .filter-panel[open] > summary {{
        margin-bottom: 0.75rem;
        }}

        .filters {{
            display: flex;
            flex-direction: column;
            gap: 0.35rem;
            padding: 0.5rem 0.75rem 0.25rem;
        }}

        .filters input,
        .filters select {{
            width: 100%;
            padding: 0.6rem 0.9rem;
            font-size: 0.82rem;
            border-radius: var(--radius-pill);
            border: 1px solid var(--border-subtle);
            background-color: var(--bg-soft);
            color: var(--text-main);
            outline: none;
            -webkit-appearance: none;
            appearance: none;
            box-shadow: 0 2px 6px rgba(0,0,0,0.35);
        }}

        .filters input::placeholder {{
            color: var(--text-soft);
        }}

        .filters-row-label {{
            font-size: 0.7rem;
            color: var(--text-soft);
            margin-top: 0.15rem;
        }}
        
        .filter-toggle {{
            position: sticky;
            top: 0.5rem;
            z-index: 40;
            width: 100%;
            padding: 0.65rem 0.9rem;
            border-radius: var(--radius-pill);
            border: 1px solid var(--border-subtle);
            background: rgba(17,24,39,0.92);
            color: var(--text-main);
            backdrop-filter: blur(10px);
            font-size: 0.9rem;
            font-weight: 600;
            box-shadow: var(--shadow-soft);
            }}

        .filter-drawer {{
            position: fixed;
            left: 0;
            right: 0;
            top: 0;
            z-index: 60;
            transform: translateY(-110%);
            transition: transform 0.22s ease-out;
            padding: 0.75rem;
            }}

        .filter-drawer.open {{
            transform: translateY(0);
            }}

        .filter-drawer-inner {{
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-xl);
            background: rgba(2,3,8,0.96);
            backdrop-filter: blur(14px);
            box-shadow: var(--shadow-soft);
            padding: 0.75rem;
            }}

        .filter-drawer-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.75rem;
            margin-bottom: 0.5rem;
            }}

        .filter-drawer-title {{
            font-weight: 700;
            font-size: 0.95rem;
            }}

        .filter-close {{
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-pill);
            background: var(--bg-soft);
            color: var(--text-main);
            padding: 0.35rem 0.6rem;
            font-size: 0.95rem;
            }}

        .drawer-backdrop {{
            position: fixed;
            inset: 0;
            z-index: 55;
            background: rgba(0,0,0,0.55);
            }}

        .card-container {{
            display: flex;
            flex-direction: column;
            gap: 0.6rem;
            margin-top: 0.35rem;
        }}

        .card {{
            background-color: var(--bg-card);
            border-radius: var(--radius-xl);
            padding: 0.7rem 0.8rem 0.65rem;
            border: 1px solid var(--border-subtle);
            box-shadow: var(--shadow-soft);
            transition:
                transform var(--transition-fast),
                box-shadow var(--transition-fast),
                border-color var(--transition-fast),
                background-color var(--transition-fast);
        }}

        .card:active {{
            transform: scale(0.98);
            box-shadow: 0 4px 10px rgba(0,0,0,0.7);
            border-color: var(--accent);
            background-color: #0f172a;
        }}

        .card h2 {{
            margin: 0 0 0.15rem;
            font-size: 0.98rem;
            font-weight: 500;
            line-height: 1.2;
        }}

        .card a {{
            color: var(--accent);
            text-decoration: none;
        }}

        .card a:hover {{
            text-decoration: underline;
        }}

        .meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.25rem;
            margin: 0 0 0.25rem;
            font-size: 0.64rem;
        }}

        .pill {{
            padding: 0.18rem 0.5rem;
            border-radius: var(--radius-pill);
            background-color: var(--accent-soft);
            color: var(--accent);
            border: 1px solid rgba(187,134,252,0.22);
            white-space: nowrap;
        }}

        .label-pill {{
            background-color: #111827;
            color: var(--text-soft);
            border-color: #111827;
        }}

        .summary {{
            margin: 0;
            margin-top: 0.15rem;
            font-size: 0.74rem;
            color: var(--text-soft);
            line-height: 1.35;
        }}
    </style>
</head>
<body>
<div class="page-wrap">
    <h1>Fanfic Archive</h1>
    <p class="subtitle">Not my fics. Just ones I refuse to lose.</p>
<details class="filter-panel" open>
  <summary>Search & Filters</summary>
    <div class="filters-wrap">
        <div class="filters">
            <input type="text" id="searchBox" placeholder="Search by title or summary...">
            <div class="filters-row-label">Filter by:</div>
            <select id="authorFilter"><option value="">All authors</option></select>
            <select id="shipFilter"><option value="">All ships</option></select>
            <select id="ratingFilter"><option value="">All ratings</option></select>
            <select id="statusFilter"><option value="">All statuses</option></select>
        </div>
    </div>
</details>
    <div class="card-container">
        {''.join(cards)}
    </div>
</div>

<script>
document.addEventListener('DOMContentLoaded', function () {{
  const searchBox = document.getElementById('searchBox');
  const authorFilter = document.getElementById('authorFilter');
  const shipFilter = document.getElementById('shipFilter');
  const ratingFilter = document.getElementById('ratingFilter');
  const statusFilter = document.getElementById('statusFilter');
  const cards = Array.from(document.querySelectorAll('.card'));

  // Drawer controls
  const drawer = document.getElementById('filterDrawer');
  const backdrop = document.getElementById('drawerBackdrop');
  const toggleBtn = document.getElementById('filterToggle');
  const closeBtn = document.getElementById('filterClose');

  function openDrawer() {{
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    toggleBtn.setAttribute('aria-expanded', 'true');
    backdrop.hidden = false;
  }}

  function closeDrawer() {{
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    toggleBtn.setAttribute('aria-expanded', 'false');
    backdrop.hidden = true;
  }}

  toggleBtn.addEventListener('click', () => {{
    const isOpen = drawer.classList.contains('open');
    isOpen ? closeDrawer() : openDrawer();
  }});

  closeBtn.addEventListener('click', closeDrawer);
  backdrop.addEventListener('click', closeDrawer);

  // Make entire card clickable + keyboard accessible
  cards.forEach(card => {{
    card.setAttribute('tabindex', '0');
    card.setAttribute('role', 'link');

    card.addEventListener('click', (e) => {{
      // If they tapped a real interactive element, don't hijack it
      const interactive = e.target.closest('a, button, input, select, textarea, summary');
      if (interactive) return;

      const href = card.dataset.href;
      if (href) window.location.href = href;
    }});

    card.addEventListener('keydown', (e) => {{
      if (e.key === 'Enter' || e.key === ' ') {{
        e.preventDefault();
        const href = card.dataset.href;
        if (href) window.location.href = href;
      }}
      if (e.key === 'Escape') closeDrawer();
    }});
  }});

  function populateFilter(filter, attr) {{
    const values = Array.from(new Set(cards.map(c => c.dataset[attr])))
      .filter(v => v && v !== 'unknown')
      .sort();

    values.forEach(v => {{
      const opt = document.createElement('option');
      opt.value = v;
      opt.textContent = v;
      filter.appendChild(opt);
    }});
  }}

  populateFilter(authorFilter, 'author');
  populateFilter(shipFilter, 'ship');
  populateFilter(ratingFilter, 'rating');
  populateFilter(statusFilter, 'status');

  function filterCards() {{
    const search = (searchBox.value || '').toLowerCase();
    const author = authorFilter.value;
    const ship = shipFilter.value;
    const rating = ratingFilter.value;
    const status = statusFilter.value;

    cards.forEach(card => {{
      // Search only title + summary for a cleaner “mobile mental model”
      const titleText = (card.querySelector('h2')?.innerText || '').toLowerCase();
      const summaryText = (card.querySelector('.summary')?.innerText || '').toLowerCase();
      const matchesSearch = !search || titleText.includes(search) || summaryText.includes(search);

      const matchesAuthor = !author || card.dataset.author === author;
      const matchesShip = !ship || card.dataset.ship === ship;
      const matchesRating = !rating || card.dataset.rating === rating;
      const matchesStatus = !status || card.dataset.status === status;

      card.style.display = (matchesSearch && matchesAuthor && matchesShip && matchesRating && matchesStatus) ? '' : 'none';
    }});
  }}

  searchBox.addEventListener('input', filterCards);
  authorFilter.addEventListener('change', filterCards);
  shipFilter.addEventListener('change', filterCards);
  ratingFilter.addEventListener('change', filterCards);
  statusFilter.addEventListener('change', filterCards);

  // Optional: auto-close drawer after changing filters on mobile
  [authorFilter, shipFilter, ratingFilter, statusFilter].forEach(el => {{
    el.addEventListener('change', () => closeDrawer());
  }});
}});
</script>
</body>
</html>"""

    out_path = os.path.join(directory, output_file)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(index_content)
    logging.info(f"Index created: {out_path}")


# =========================
# Master runner
# =========================
def process_fics(folder_path: str, output_file: str = "index.html"):
    t0 = time.time()
    logging.info("=== Fanfic archive processing: START ===")
    logging.info(f"Folder: {folder_path}")

    processed = 0
    updated = 0
    renamed = 0
    deduped = 0
    failed = 0

    # Pass 1: update metadata & rename
    logging.info("Phase 1/2: Updating metadata + renaming files...")
    for filename in os.listdir(folder_path):
        if not (filename.endswith(".html") and filename != output_file):
            continue

        processed += 1
        old_path = os.path.join(folder_path, filename)

        try:
            story_title, author = update_html_metadata(old_path)
            updated += 1  # if update_html_metadata returns without raising, count it

            new_path = rename_file_using_title(old_path, story_title, author)

            # crude but useful: infer action types
            if os.path.normcase(os.path.abspath(new_path)) != os.path.normcase(os.path.abspath(old_path)):
                # It either renamed or deduped; check if old still exists
                if not os.path.exists(old_path):
                    deduped += 1
                else:
                    renamed += 1

        except Exception as e:
            failed += 1
            logging.exception(f"Failed processing {filename}: {e}")

    logging.info(
        f"Phase 1 complete. Files seen={processed}, updated={updated}, renamed={renamed}, deduped={deduped}, failed={failed}"
    )

    # Pass 2: build index
    logging.info("Phase 2/2: Building index.html...")
    try:
        build_index(folder_path, output_file)
        logging.info("Phase 2 complete. Index built successfully.")
    except Exception as e:
        logging.exception(f"Index build failed: {e}")

    dt = time.time() - t0
    logging.info(f"=== Fanfic archive processing: END ({dt:.2f}s) ===")


if __name__ == "__main__":
    process_fics(FOLDER_PATH, OUTPUT_FILE)
