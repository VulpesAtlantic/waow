import os
import re
import unicodedata
import logging
from bs4 import BeautifulSoup

# =========================
# Logging setup
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# =========================
# Config
# =========================
FOLDER_PATH = r"C:\Users\willo\OneDrive\Documents\GitHub\fics\ficArchive"
OUTPUT_FILE = "index.html"

# Windows reserved base names to avoid
_RESERVED = {
    "con","prn","aux","nul",
    "com1","com2","com3","com4","com5","com6","com7","com8","com9",
    "lpt1","lpt2","lpt3","lpt4","lpt5","lpt6","lpt7","lpt8","lpt9"
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
    s = re.sub(r"[^\w\s\-]", "", s)         # keep word chars, whitespace, hyphen
    s = re.sub(r"\s+", "_", s)              # spaces -> underscore
    s = re.sub(r"_+", "_", s)               # collapse ___ -> _
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
        logging.warning(f"{os.path.basename(file_path)}: no <head>; skipping meta update")
        return None

    # ---- Title & (optional) author/universe extraction
    story_title = None
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
                head.append(soup.new_tag("meta", attrs={"name": "author", "content": author}))

        # Meta: ship (static, as in your script)
        ship_meta = head.find("meta", attrs={"name": "ship"})
        if ship_meta:
            ship_meta["content"] = "Agatha Harkness/Rio Vidal"
        else:
            head.append(soup.new_tag("meta", attrs={"name": "ship", "content": "Agatha Harkness/Rio Vidal"}))
    else:
        logging.warning(f"{os.path.basename(file_path)}: no <title>; will fall back to filename for rename")

    # ---- Styles: remove inline <style>, ensure darkMode.css link
    for style_tag in head.find_all("style"):
        style_tag.decompose()
    if not head.find("link", href="assets/darkMode.css"):
        head.append(soup.new_tag("link", rel="stylesheet", href="assets/darkMode.css"))

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
                    head.append(soup.new_tag("meta", attrs={"name": "rating", "content": rating_text}))

    # ---- Status extraction (your heuristic)
    status_text = "Completed" if "Complete: 20" in soup.get_text() else "Incomplete"
    status_meta = head.find("meta", attrs={"name": "status"})
    if status_meta:
        status_meta["content"] = status_text
    else:
        head.append(soup.new_tag("meta", attrs={"name": "status", "content": status_text}))

    # Save file
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(str(soup))
        logging.info(f"Updated metadata: {os.path.basename(file_path)}")
    except Exception as e:
        logging.error(f"{os.path.basename(file_path)}: failed to write updated HTML: {e}")

    return story_title

def rename_file_using_title(file_path: str, story_title: str):
    """
    Rename file based on story_title (underscores).
    If story_title is None, use the current base filename (minus extension).
    """
    directory, old_name = os.path.split(file_path)
    base_title = story_title if story_title else os.path.splitext(old_name)[0]
    slug = slugify_underscores(base_title)
    new_path = ensure_unique_path(directory, slug, ext=".html")

    if file_path == new_path:
        return file_path  # nothing to do

    try:
        os.rename(file_path, new_path)
        logging.info(f"Renamed {old_name} -> {os.path.basename(new_path)}")
        return new_path
    except Exception as e:
        logging.error(f"{old_name}: failed to rename: {e}")
        return file_path  # keep original path on failure

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

                title = soup.title.string.strip() if soup.title and soup.title.string else filename
                author_tag = soup.find("meta", attrs={"name": "author"})
                ship_tag = soup.find("meta", attrs={"name": "ship"})
                rating_tag = soup.find("meta", attrs={"name": "rating"})
                status_tag = soup.find("meta", attrs={"name": "status"})
                summary_tag = soup.find("meta", attrs={"name": "summary"}) or soup.find("meta", attrs={"name": "description"})

                author = author_tag["content"].strip() if author_tag and "content" in author_tag.attrs else "Unknown"
                ship = ship_tag["content"].strip() if ship_tag and "content" in ship_tag.attrs else "Unknown"
                rating = rating_tag["content"].strip() if rating_tag and "content" in rating_tag.attrs else "Unknown"
                status = status_tag["content"].strip() if status_tag and "content" in status_tag.attrs else "Unknown"
                summary = summary_tag["content"].strip() if summary_tag and "content" in summary_tag.attrs else ""

                cards.append(f"""
                    <div class="card" 
                         data-author="{author.lower()}" 
                         data-ship="{ship.lower()}" 
                         data-rating="{rating.lower()}" 
                         data-status="{status.lower()}">
                        <h2><a href="{filename}">{title}</a></h2>
                        <p><strong>Author:</strong> {author}</p>
                        <p><strong>Ship:</strong> {ship}</p>
                        <p><strong>Rating:</strong> {rating}</p>
                        <p><strong>Status:</strong> {status}</p>
                        <p class="summary">{summary}</p>
                    </div>
                """)

            except Exception as e:
                logging.error(f"Failed to add {filename} to index: {e}")

    # (index_content creation and writing stays the same as in the last script)


    index_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Fanfic Archive Index</title>
    <link rel="stylesheet" href="assets/darkMode.css">
    <style>
        body {{
            font-family: sans-serif;
            padding: 1rem;
            max-width: 1200px;
            margin: auto;
        }}
        .filters {{
            display: flex;
            flex-wrap: wrap;
            gap: 1rem;
            margin-bottom: 1rem;
        }}
        .filters input, .filters select {{
            padding: 0.5rem;
            font-size: 1rem;
        }}
        .card-container {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 1rem;
        }}
        @media (max-width: 600px) {{
            .card-container {{ grid-template-columns: 1fr; }}
        }}
        .card {{
            background-color: #1e1e1e;
            border: 1px solid #444;
            border-radius: 10px;
            padding: 1rem;
            box-shadow: 0 2px 5px rgba(0,0,0,0.5);
            transition: transform 0.2s;
        }}
        .card:hover {{ transform: scale(1.02); }}
        .card h2 {{
            margin-top: 0;
            font-size: 1.2rem;
        }}
        .card a {{
            color: #bb86fc;
            text-decoration: none;
        }}
        .card a:hover {{ text-decoration: underline; }}
        .summary {{
            margin-top: 0.5rem;
            color: #aaa;
            font-style: italic;
        }}
    </style>
</head>
<body>
    <h1>Fanfic Archive</h1>
    <center><p>These are not my fics. These are just some I don't want to lose.</p></center>

    <div class="filters">
        <input type="text" id="searchBox" placeholder="Search title or summary...">
        <select id="authorFilter"><option value="">All Authors</option></select>
        <select id="shipFilter"><option value="">All Ships</option></select>
        <select id="ratingFilter"><option value="">All Ratings</option></select>
        <select id="statusFilter"><option value="">All Statuses</option></select>
    </div>

    <div class="card-container">
        {''.join(cards)}
    </div>

    <script>
    document.addEventListener('DOMContentLoaded', function () {{
        const searchBox = document.getElementById('searchBox');
        const authorFilter = document.getElementById('authorFilter');
        const shipFilter = document.getElementById('shipFilter');
        const ratingFilter = document.getElementById('ratingFilter');
        const statusFilter = document.getElementById('statusFilter');
        const cards = document.querySelectorAll('.card');

        function populateFilter(filter, attr) {{
            const values = Array.from(new Set(Array.from(cards).map(card => card.dataset[attr])))
                .filter(v => v && v !== 'unknown')
                .sort();
            values.forEach(v => {{
                const opt = document.createElement('option');
                opt.value = v;
                opt.textContent = v.charAt(0).toUpperCase() + v.slice(1);
                filter.appendChild(opt);
            }});
        }}

        populateFilter(authorFilter, 'author');
        populateFilter(shipFilter, 'ship');
        populateFilter(ratingFilter, 'rating');
        populateFilter(statusFilter, 'status');

        function filterCards() {{
            const search = searchBox.value.toLowerCase();
            const author = authorFilter.value;
            const ship = shipFilter.value;
            const rating = ratingFilter.value;
            const status = statusFilter.value;

            cards.forEach(card => {{
                const matchesSearch = card.innerText.toLowerCase().includes(search);
                const matchesAuthor = !author || card.dataset.author === author;
                const matchesShip = !ship || card.dataset.ship === ship;
                const matchesRating = !rating || card.dataset.rating === rating;
                const matchesStatus = !status || card.dataset.status === status;

                if (matchesSearch && matchesAuthor && matchesShip && matchesRating && matchesStatus) {{
                    card.style.display = '';
                }} else {{
                    card.style.display = 'none';
                }}
            }});
        }}

        searchBox.addEventListener('input', filterCards);
        authorFilter.addEventListener('change', filterCards);
        shipFilter.addEventListener('change', filterCards);
        ratingFilter.addEventListener('change', filterCards);
        statusFilter.addEventListener('change', filterCards);
    }});
    </script>
</body>
</html>
"""

    out_path = os.path.join(directory, output_file)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(index_content)
    logging.info(f"Index created: {out_path}")

# =========================
# Master runner
# =========================
def process_fics(folder_path: str, output_file: str = "index.html"):
    # Pass 1: update metadata & rename each file using the *first* dash-separated segment of <title>
    for filename in os.listdir(folder_path):
        if filename.endswith(".html") and filename != output_file:
            old_path = os.path.join(folder_path, filename)
            story_title = update_html_metadata(old_path)
            new_path = rename_file_using_title(old_path, story_title)
            # (optional) you could re-parse here to propagate title change immediately for index

    # Pass 2: build index
    build_index(folder_path, output_file)

if __name__ == "__main__":
    process_fics(FOLDER_PATH, OUTPUT_FILE)
