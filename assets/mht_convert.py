"""
assets/mht_convert.py
=====================
One-off converter for AO3 work pages saved from the browser as .mht
("Save page as… → Webpage, Single File"). Rebuilds each one in the same
layout as an AO3 HTML download and writes it to staging/ as
{workID}_{slug}.html, ready for phase4_process.py.

    python assets/mht_convert.py ~/Downloads/some_folder [more folders or .mht files…]

Skips any work ID already in archive/ or staging/.
Warns when the saved page holds fewer chapters than AO3's stats say
(e.g. a single chapter was saved instead of the entire work).
"""

import html
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

BASE_DIR    = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = BASE_DIR / "archive"
STAGING_DIR = BASE_DIR / "staging"

TAG_FIELDS = [
    # (work-page dd class, download label)
    ("rating",       "Rating:"),
    ("warning",      "Archive Warning:"),
    ("category",     "Category:"),
    ("fandom",       "Fandoms:"),
    ("relationship", "Relationships:"),
    ("character",    "Characters:"),
    ("freeform",     "Additional Tags:"),
]


def mht_html(path: Path) -> bytes:
    """Return the main text/html part of a Blink-saved .mht file."""
    raw = path.read_bytes()
    boundary = re.search(rb'boundary="([^"]+)"', raw).group(1)
    for part in raw.split(b"--" + boundary):
        head, sep, body = part.partition(b"\r\n\r\n")
        if not sep:
            head, sep, body = part.partition(b"\n\n")
        if b"Content-Type: text/html" in head:
            if b"quoted-printable" in head.lower():
                import quopri
                body = quopri.decodestring(body)
            return body
    raise ValueError("no text/html part found")


def slugify(work_id: str, title: str) -> str:
    """Mimic AO3's download filename (title cut to ~24 chars on a word break)
    run through phase3_download.safe_filename."""
    words = re.sub(r"[^\w\s]", "", title).split()
    name = ""
    for w in words:
        if name and len(name) + 1 + len(w) > 24:
            break
        name = f"{name}_{w}" if name else w
    name = re.sub(r"[\s_]+", "_", name or "untitled").strip("_")
    return f"{work_id}_{name}".lower() + ".html"


def text(tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)) if tag else ""


def tag_links(dd) -> str:
    # Skip AO3's "Show warnings / Show additional tags" placeholder, which is
    # all the page holds when the account hides those tags.
    links = [a for a in dd.find_all("a") if "show_hidden" not in a.get("href", "")]
    return ", ".join(
        f'<a href="{html.escape(a.get("href", ""))}">{html.escape(a.get_text(strip=True))}</a>'
        for a in links
    )


def userstuff(module) -> str:
    """Inner HTML of a notes/summary module's blockquote, or ''."""
    if not module:
        return ""
    bq = module.find("blockquote")
    return bq.decode_contents().strip() if bq else ""


def chapter_body(div) -> str:
    for landmark in div.select("h3.landmark"):
        landmark.decompose()
    return div.decode_contents().strip()


def build(soup: BeautifulSoup, work_id: str) -> tuple[str, str, int, str, list]:
    """Return (html, title, saved_chapter_count, ao3_chapter_stat, hidden_tag_fields)."""
    title  = text(soup.select_one("#workskin h2.title"))
    byline = soup.select_one("#workskin h3.byline")
    authors = byline.find_all("a", rel="author") if byline else []
    author_names = ", ".join(a.get_text(strip=True) for a in authors) or text(byline) or "Anonymous"
    author_html = ", ".join(
        f'<a rel="author" href="{html.escape(a.get("href", ""))}">{html.escape(a.get_text(strip=True))}</a>'
        for a in authors
    ) or html.escape(author_names)
    fandoms = ", ".join(a.get_text(strip=True) for a in soup.select("dd.fandom a.tag"))

    # --- tags block ---
    rows, hidden = [], []
    for cls, label in TAG_FIELDS:
        dd = soup.select_one(f"dl.work.meta dd.{cls}")
        links = tag_links(dd) if dd else ""
        if links:
            rows.append(f"<dt>{label}</dt>\n<dd>{links}</dd>")
        elif dd:
            hidden.append(label.rstrip(":"))
    lang = soup.select_one("dl.work.meta dd.language")
    if lang:
        rows.append(f"<dt>Language:</dt>\n<dd>{html.escape(text(lang))}</dd>")
    series = soup.select_one("dl.work.meta dd.series")
    if series:
        rows.append(f"<dt>Series:</dt>\n<dd>{html.escape(text(series))}</dd>")

    stats = {}
    for dt in soup.select("dl.stats dt"):
        dd = dt.find_next_sibling("dd")
        stats[dt.get_text(strip=True).rstrip(":")] = text(dd)
    stat_lines = [f"Published: {stats.get('Published', '')}"]
    for key in ("Completed", "Updated"):
        if key in stats:
            stat_lines.append(f"{key}: {stats[key]}")
    stat_lines.append(f"Words: {stats.get('Words', '')}")
    stat_lines.append(f"Chapters: {stats.get('Chapters', '')}")
    rows.append("<dt>Stats:</dt>\n<dd>\n" + "\n".join(stat_lines) + "\n</dd>")

    # --- preface summary / notes ---
    preface = soup.select_one("#workskin > div.preface")
    pre_extra = ""
    s = userstuff(preface.select_one("div.summary") if preface else None)
    if s:
        pre_extra += f'<p>Summary</p>\n<blockquote class="userstuff">{s}</blockquote>\n'
    n = userstuff(preface.select_one("div.notes") if preface else None)
    if n:
        pre_extra += f'<p>Notes</p>\n<blockquote class="userstuff">{n}</blockquote>\n'

    # --- chapters ---
    chapters_html = []
    chapter_divs = soup.select("#chapters > div.chapter")
    if chapter_divs:
        for ch in chapter_divs:
            heading = text(ch.select_one("h3.title")).replace(" : ", ": ")
            meta = ""
            cs = userstuff(ch.select_one("div.preface div.summary"))
            if cs:
                meta += f'<p>Chapter Summary</p>\n<blockquote class="userstuff">{cs}</blockquote>\n'
            cn = userstuff(ch.select_one("div.preface div.notes:not(.end)"))
            if cn:
                meta += f'<p>Chapter Notes</p>\n<blockquote class="userstuff">{cn}</blockquote>\n'
            body = ch.select_one("div.userstuff")
            end = userstuff(ch.select_one("div.end.notes"))
            block = (
                f'<div class="meta group">\n<h2 class="heading">{html.escape(heading)}</h2>\n{meta}</div>\n'
                f'<div class="userstuff">\n{chapter_body(body) if body else ""}\n</div>\n'
            )
            if end:
                block += f'<div class="endnotes">\n<p>Chapter End Notes</p>\n<blockquote class="userstuff">{end}</blockquote>\n</div>\n'
            chapters_html.append(block)
    else:
        body = soup.select_one("#chapters > div.userstuff")
        chapters_html.append(f'<div class="userstuff">\n{chapter_body(body) if body else ""}\n</div>\n')

    work_end = userstuff(soup.select_one("#work_endnotes"))
    after = f'<p>Notes</p>\n<blockquote class="userstuff">{work_end}</blockquote>\n' if work_end else ""

    url = f"https://archiveofourown.org/works/{work_id}"
    t = html.escape(title)
    doc = f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
<head>
<meta charset="utf-8" />
<title>{t} - {html.escape(author_names)} - {html.escape(fandoms)}</title>
</head>
<body>

<div id="preface">
<h2 class="toc-heading">Preface</h2>
<p class="message">
<b>{t}</b><br />
Posted originally on the <a href="https://archiveofourown.org/">Archive of Our Own</a> at <a href="{url}">{url}</a>.
</p>
<div class="meta">
<dl class="tags">
{chr(10).join(rows)}
</dl>
<h1>{t}</h1>
<div class="byline">by {author_html}</div>
{pre_extra}</div>
</div>

<div id="chapters" class="userstuff">
{chr(10).join(chapters_html)}
</div>

<div id="afterword">
<h2 class="toc-heading">Afterword</h2>
{after}<p class="message">Converted from a saved .mht page by mht_convert.py.</p>
</div>

</body>
</html>
"""
    return doc, title, len(chapter_divs) or 1, stats.get("Chapters", ""), hidden


def existing_ids() -> set[str]:
    ids = set()
    for d in (ARCHIVE_DIR, STAGING_DIR):
        for p in d.rglob("*.html"):
            m = re.match(r"^(\d+)_", p.name)
            if m:
                ids.add(m.group(1))
    return ids


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    files = []
    for arg in sys.argv[1:]:
        p = Path(arg).expanduser()
        files += sorted(p.glob("*.mht")) if p.is_dir() else [p]

    have = existing_ids()
    for path in files:
        try:
            raw = mht_html(path)
            soup = BeautifulSoup(raw, "html.parser")
            m = re.search(r"archiveofourown\.org/works/(\d+)", raw.decode("utf-8", "replace"))
            if not m:
                print(f"SKIP  {path.name}: no work ID found")
                continue
            work_id = m.group(1)
            if work_id in have:
                print(f"SKIP  {work_id}: already in archive/ or staging/")
                continue

            doc, title, saved, ao3_chapters, hidden = build(soup, work_id)
            out = STAGING_DIR / slugify(work_id, title)
            out.write_text(doc, encoding="utf-8")
            have.add(work_id)

            posted = ao3_chapters.split("/")[0]
            warn = ""
            if posted.isdigit() and saved < int(posted):
                warn = f"  ⚠ only {saved} of {posted} posted chapters were saved"
            if hidden:
                warn += f"  ⚠ hidden on saved page: {', '.join(hidden)}"
            print(f"OK    {out.name}  ({saved} ch, AO3 {ao3_chapters}){warn}")
        except Exception as e:
            print(f"FAIL  {path.name}: {e}")


if __name__ == "__main__":
    main()
