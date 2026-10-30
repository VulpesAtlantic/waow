"""
assets/epub_convert.py
======================
One-off converter for AO3 works downloaded as .epub (AO3's own EPUB export,
including copies that went through Calibre and got split into several files).
Joins the pieces back into a single page in the same layout as an AO3 HTML
download and writes it to staging/ as {workID}_{slug}.html, ready for
phase4_process.py.

Needs beautifulsoup4 (already required by phase4_process.py). Nothing else.

    python assets/epub_convert.py                    # converts everything in original-epubs/
    python assets/epub_convert.py --dry-run          # same, but writes nothing
    python assets/epub_convert.py ~/Downloads/some_folder [more folders or .epub files…]

Skips any work ID already in archive/ or staging/, and any EPUB that isn't an
AO3 download (no preface / chapters markup, or no AO3 work link). A sub-folder
inside the input folder carries through: original-epubs/Aeon/x.epub ->
staging/Aeon/x.html -> archive/Aeon/x.html.

How it works: AO3 EPUBs already contain AO3's own markup (preface with the tag
list and stats, byline and summary, one block per chapter, afterword), so
nothing has to be guessed. The reader's files are read in spine order, their
preface / chapters / afterword blocks are merged, and Calibre's generated
classes (calibre1, calibre7, …) are stripped. The "words" figure compares the
recovered text against AO3's own word count from the Stats row.
"""

import posixpath
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from mht_convert import BASE_DIR, STAGING_DIR, existing_ids, slugify

# used when no folder is given on the command line
DEFAULT_DIR = BASE_DIR / "original-epubs"

WORK_URL = re.compile(r"archiveofourown\.org/works/(\d+)")
CALIBRE_CLASS = re.compile(r"^calibre\d*$")
SECTION_IDS = ("preface", "chapters", "afterword")


# ---------------------------------------------------------------------------
# Reading the EPUB
# ---------------------------------------------------------------------------

def read_pages(z):
    """Return the book's (X)HTML pages as soup objects, in reading order."""
    c = ET.fromstring(z.read("META-INF/container.xml"))
    opf_path = c.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile").get("full-path")
    opf = ET.fromstring(z.read(opf_path))
    ns = "{http://www.idpf.org/2007/opf}"
    manifest = {i.get("id"): i.get("href") for i in opf.iter(f"{ns}item")}
    base = posixpath.dirname(opf_path)

    pages = []
    for ref in opf.iter(f"{ns}itemref"):
        href = manifest.get(ref.get("idref"))
        if not href or not href.lower().split("#")[0].endswith((".xhtml", ".html", ".htm")):
            continue
        member = posixpath.normpath(posixpath.join(base, unquote(href.split("#")[0])))
        pages.append(BeautifulSoup(z.read(member).decode("utf-8", "replace"), "html.parser"))
    return pages


def opf_meta(z):
    """dc:title and dc:creator, used only if the page itself has no title/byline."""
    c = ET.fromstring(z.read("META-INF/container.xml"))
    opf = ET.fromstring(z.read(c.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile").get("full-path")))
    dc = "{http://purl.org/dc/elements/1.1/}"
    t, a = opf.find(f".//{dc}title"), opf.find(f".//{dc}creator")
    return (t.text or "").strip() if t is not None else "", (a.text or "").strip() if a is not None else ""


# ---------------------------------------------------------------------------
# Cleaning and merging
# ---------------------------------------------------------------------------

def clean(tag):
    """Strip Calibre's generated classes/ids and its empty page-break markers."""
    for el in tag.find_all(True):
        if el.attrs is None:      # already removed
            continue
        if (el.get("id") or "").startswith("calibre_pb"):
            el.decompose()
    for el in tag.find_all(True):
        classes = []
        for cls in el.get("class", []):
            if CALIBRE_CLASS.match(cls):
                continue
            classes.append(re.sub(r"^(userstuff)\d+$", r"\1", cls))   # userstuff1/2 -> userstuff
        if classes:
            el["class"] = classes
        else:
            el.attrs.pop("class", None)
        if (el.get("id") or "").startswith("calibre_"):
            del el["id"]
    for span in tag.find_all("span"):    # Calibre wraps runs in bare <span>s
        if not span.attrs:
            span.unwrap()
    return tag


def collect(pages):
    """Merge every page's preface / chapters / afterword blocks, in order."""
    parts = {k: [] for k in SECTION_IDS}
    for soup in pages:
        body = soup.body or soup
        for div in body.find_all("div", recursive=False):
            sid = div.get("id")
            if sid in parts:
                clean(div)
                parts[sid].extend(div.contents)
    return parts


def render_parts(parts):
    out = {}
    for key, nodes in parts.items():
        holder = BeautifulSoup("", "html.parser")
        wrapper = holder.new_tag("div")
        for n in list(nodes):
            wrapper.append(n.extract() if hasattr(n, "extract") else n)
        # drop wrappers left empty after removing page-break markers
        for d in wrapper.find_all("div"):
            if not d.get_text(strip=True) and not d.find(["img", "hr", "br"]):
                d.decompose()
        out[key] = wrapper.decode_contents().strip()
    return out


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def convert(path: Path):
    with zipfile.ZipFile(path) as z:
        pages = read_pages(z)
        opf_title, opf_author = opf_meta(z)
    parts = collect(pages)
    if not parts["preface"] or not parts["chapters"]:
        return None, "not an AO3 EPUB (no preface/chapters markup)"
    sections = render_parts(parts)

    m = WORK_URL.search(sections["preface"])
    if not m:
        return None, "no AO3 work link"
    work_id = m.group(1)

    pre = BeautifulSoup(sections["preface"], "html.parser")
    h1 = pre.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else opf_title or "Untitled"
    byline = pre.select_one(".byline")
    author = re.sub(r"^by\s+", "", byline.get_text(" ", strip=True)) if byline else opf_author or "Anonymous"
    fandom_dd = next((dt.find_next_sibling("dd") for dt in pre.find_all("dt")
                      if dt.get_text(strip=True).lower().startswith("fandom")), None)
    fandoms = fandom_dd.get_text(", ", strip=True) if fandom_dd else ""

    stats = next((dt.find_next_sibling("dd").get_text(" ", strip=True) for dt in pre.find_all("dt")
                  if dt.get_text(strip=True).startswith("Stats")), "")
    want = int(re.sub(r"\D", "", (re.search(r"Words:\s*([\d,]+)", stats) or [None, "0"])[1]) or 0)
    chap_stat = (re.search(r"Chapters:\s*(\S+)", stats) or [None, "?"])[1]
    got = len(BeautifulSoup(sections["chapters"], "html.parser").get_text(" ").split())
    nch = max(1, len(BeautifulSoup(sections["chapters"], "html.parser").select("h2.heading")))

    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    after = sections["afterword"] or '<h2 class="toc-heading">Afterword</h2>'
    doc = f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
<head>
<meta charset="utf-8" />
<title>{esc(title)} - {esc(author)} - {esc(fandoms)}</title>
</head>
<body>

<div id="preface">
{sections["preface"]}
</div>

<div id="chapters" class="userstuff">
{sections["chapters"]}
</div>

<div id="afterword">
{after}
<p class="message">Converted from EPUB by epub_convert.py.</p>
</div>

</body>
</html>
"""
    return work_id, (doc, title, got, want, nch, chap_stat)


def main():
    # Windows consoles default to cp1252, which can't print the "⚠" flag
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    if not args:
        # no folder given: use the project's usual drop folder
        if not DEFAULT_DIR.is_dir():
            print(f"{DEFAULT_DIR} doesn't exist. Create it and put your .epub files in it, or pass a folder.")
            sys.exit(1)
        args = [str(DEFAULT_DIR)]

    files = []   # (file, sub-folder it sat in, or "")
    for arg in args:
        p = Path(arg).expanduser()
        if p.is_dir():
            for f in sorted(p.rglob("*.epub")):
                rel_dirs = f.parent.relative_to(p).parts
                files.append((f, rel_dirs[0] if rel_dirs else ""))
        else:
            files.append((p, ""))

    have = existing_ids()
    for path, folder in files:
        try:
            work_id, result = convert(path)
            if work_id is None:
                print(f"SKIP  {path.name}: {result}")
                continue
            if work_id in have:
                print(f"SKIP  {work_id}: already in archive/ or staging/")
                continue
            doc, title, got, want, nch, chap_stat = result
            out = STAGING_DIR / folder / slugify(work_id, title)
            pct = f"{100 * got / want:.0f}%" if want else "?"
            flag = "" if want and 0.9 <= got / want <= 1.1 else "  ⚠ check word count"
            if not dry:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(doc, encoding="utf-8")
            have.add(work_id)
            print(f"OK    {folder + '/' if folder else ''}{out.name}  ({nch} ch, AO3 {chap_stat}; words {got}/{want} = {pct}){flag}")
        except Exception as e:
            print(f"FAIL  {path.name}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
