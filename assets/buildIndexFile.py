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

# =========================
# Card index build
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


build_index(FOLDER_PATH, OUTPUT_FILE)