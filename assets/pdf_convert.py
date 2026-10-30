"""
assets/pdf_convert.py
=====================
One-off converter for AO3 works downloaded (or printed) as PDF. Rebuilds
each one in the same layout as an AO3 HTML download and writes it to
staging/ as {workID}_{slug}.html, ready for phase4_process.py.

Needs pdfplumber (pip install pdfplumber).

    python assets/pdf_convert.py                    # converts everything in original-pdfs/
    python assets/pdf_convert.py --dry-run          # same, but writes nothing
    python assets/pdf_convert.py ~/Downloads/some_folder [more folders or .pdf files…]

Skips any work ID already in archive/ or staging/ (and PDFs with no AO3
work link, e.g. Google Docs exports). PDFs lose some formatting, so this
rebuilds paragraphs from line spacing and italics/bold from font names.
The "words" column in the output compares the recovered text against
AO3's word count as a sanity check.
"""

import html
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
from mht_convert import BASE_DIR, STAGING_DIR, existing_ids, slugify

# used when no folder is given on the command line
DEFAULT_DIR = BASE_DIR / "original-pdfs"


META_LABELS = (
    r"Rating|Archive Warnings?|Categor(?:y|ies)|Fandoms?|Relationships?|Characters?|"
    r"Additional Tags|Language|Series|Collections|Stats"
)
META_OUT = {  # normalised label → download label
    "rating": "Rating:", "archive warning": "Archive Warning:", "category": "Category:",
    "fandom": "Fandoms:", "relationship": "Relationships:", "character": "Characters:",
    "additional tags": "Additional Tags:", "language": "Language:", "series": "Series:",
    "collections": "Collections:",
}
SECTION_LABELS = {
    "Summary": "summary", "Notes": "notes",
    "Chapter Summary": "ch_summary", "Chapter Notes": "ch_notes",
    "Chapter End Notes": "ch_end", "End Notes": "end",
}
DROP_LINE = re.compile(
    r"^\(?See the end of the (chapter|work) for (more )?notes\.?\)?$"
    r"|^Please drop by the Archive and comment"
    r"|^Works inspired by this one",
    re.I,
)


# ---------------------------------------------------------------------------
# Line extraction
# ---------------------------------------------------------------------------

def read_lines(pdf):
    """Return every text line as a dict with geometry and styled words."""
    lines = []
    for pno, page in enumerate(pdf.pages):
        words = page.extract_words(extra_attrs=["fontname", "size"], keep_blank_chars=False)
        rows = []
        for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
            if rows and abs(rows[-1][0]["top"] - w["top"]) < 2.5:
                rows[-1].append(w)
            else:
                rows.append([w])
        for row in rows:
            row.sort(key=lambda w: w["x0"])
            fn = [w["fontname"] for w in row]
            lines.append({
                "page": pno, "top": row[0]["top"], "x0": row[0]["x0"], "x1": row[-1]["x1"],
                "size": max(w["size"] for w in row),
                "bold": all("Bold" in f for f in fn),
                "words": row,
                "text": join_words(row, plain=True),
                "page_w": page.width,
            })
    return drop_repeats(lines, len(pdf.pages))


def drop_repeats(lines, npages):
    """Remove running headers/footers and bare page numbers."""
    key = lambda l: (round(l["top"] / 4), re.sub(r"\d+", "#", l["text"]))
    counts = Counter(key(l) for l in lines)
    limit = max(3, npages * 0.4)
    return [
        l for l in lines
        if counts[key(l)] < limit and not re.fullmatch(r"(Page )?\d+( of \d+)?", l["text"])
    ]


def style(w):
    f = w["fontname"]
    return ("Bold" in f, "Italic" in f or "Oblique" in f)


def join_words(words, plain=False):
    """Join a line's words, adding spaces only where there's a visible gap and
    wrapping italic/bold runs in <em>/<strong>."""
    out, cur, prev = [], None, None
    for w in words:
        gap = prev is not None and (w["x0"] - prev["x1"]) > 0.15 * w["size"]
        st = (False, False) if plain else style(w)
        t = w["text"] if plain else html.escape(w["text"])
        if st != cur:
            if cur is not None:
                out.append(close(cur))
            if gap:
                out.append(" ")
            out.append(open_(st))
            cur = st
        elif gap:
            out.append(" ")
        out.append(t)
        prev = w
    if cur is not None:
        out.append(close(cur))
    return "".join(out)


def open_(st):
    return ("<strong>" if st[0] else "") + ("<em>" if st[1] else "")


def close(st):
    return ("</em>" if st[1] else "") + ("</strong>" if st[0] else "")


# ---------------------------------------------------------------------------
# Paragraphs
# ---------------------------------------------------------------------------

def layout_stats(lines):
    body_size = Counter(round(l["size"]) for l in lines for _ in l["words"]).most_common(1)[0][0]
    body = [l for l in lines if round(l["size"]) == body_size]
    body_x = Counter(round(l["x0"]) for l in body).most_common(1)[0][0]
    right_x = statistics.quantiles([l["x1"] for l in body], n=10)[-1] if len(body) > 10 else max(l["x1"] for l in body)
    gaps = [b["top"] - a["top"] for a, b in zip(body, body[1:]) if a["page"] == b["page"] and b["top"] > a["top"]]
    pitch = statistics.median(sorted(gaps)[: max(1, len(gaps) // 2)]) if gaps else body_size * 1.2
    return body_size, body_x, right_x, pitch


def is_heading(l, body_size):
    return l["size"] >= body_size * 1.2 and l["bold"]


def paragraphs(lines, body_size, body_x, right_x, pitch):
    """Group lines into paragraph blocks."""
    paras, cur = [], None
    for l in lines:
        new = cur is None or is_heading(l, body_size) != is_heading(cur["last"], body_size)
        if not new and is_heading(l, body_size):
            # a long heading wrapped onto a second line
            prev = cur["last"]
            new = l["page"] != prev["page"] or (l["top"] - prev["top"]) > l["size"] * 2
        elif not new:
            prev = cur["last"]
            if l["page"] != prev["page"]:
                # page break: a full-width previous line means the paragraph continues
                new = prev["x1"] < right_x - 0.12 * (right_x - body_x)
            else:
                new = (l["top"] - prev["top"]) > pitch * 1.4
            # a line jutting right starts a new block; one stepping left does too,
            # unless it's the second line under an indented first line
            new = new or l["x0"] > prev["x0"] + 6 or (l["x0"] < prev["x0"] - 6 and len(cur["lines"]) > 1)
            new = new or l["text"] in SECTION_LABELS or prev["text"] in SECTION_LABELS
        if new:
            cur = {"lines": [l], "last": l, "x0": l["x0"], "page": l["page"]}
            paras.append(cur)
        else:
            cur["lines"].append(l)
            cur["last"] = l
    for p in paras:
        p["text"] = " ".join(l["text"] for l in p["lines"])
        p["html"] = " ".join(join_words(l["words"]) for l in p["lines"]).replace("</em> <em>", " ")
        p["heading"] = is_heading(p["lines"][0], body_size)
        p["indented"] = p["x0"] > body_x + 6
        p["centered"] = abs((p["lines"][0]["x0"] + p["lines"][0]["x1"]) / 2 - p["lines"][0]["page_w"] / 2) < 15 and p["x0"] > body_x + 30
    return paras


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

def parse_meta(text):
    text = re.sub(r"\s+", " ", text)
    parts = re.split(rf"\b({META_LABELS}):", text)
    meta = {}
    for label, value in zip(parts[1::2], parts[2::2]):
        k = label.lower().rstrip("s") if label not in ("Additional Tags", "Collections", "Stats") else label.lower()
        k = {"categorie": "category", "categorie s": "category"}.get(k, k)
        meta.setdefault(k, value.strip())
    url = re.search(r"archiveofourown\.org/works/(\d+)", text)
    return meta, (url.group(1) if url else None)


def stats_lines(s):
    out = []
    for key in ("Published", "Completed", "Updated", "Words", "Chapters"):
        m = re.search(rf"{key}:\s*([\d,/?-]+)", s)
        if m:
            out.append(f"{key}: {m.group(1)}")
    return out


def build_structure(paras):
    """Walk paragraphs into meta text, preface, chapters and afterword.

    Summary/notes text is indented relative to story text in AO3 PDFs, so
    after a section label, indented paragraphs belong to that section and
    the first unindented one starts (or resumes) the story. The title-page
    preface is the exception: some renderers don't indent it, so there the
    page break into chapter 1 marks the boundary instead.
    """
    st = {"meta": [], "title": "", "author": "", "summary": [], "notes": [], "chapters": [], "end": []}
    state, section, chapter, seen_stats, last_page = "meta", None, None, False, -1

    def new_chapter(heading=""):
        ch = {"heading": heading, "ch_summary": [], "ch_notes": [], "body": [], "ch_end": []}
        st["chapters"].append(ch)
        return ch

    for i, p in enumerate(paras):
        text = p["text"].strip()
        page_changed, last_page = p["page"] != last_page, p["page"]
        if DROP_LINE.search(text):
            continue
        if state == "meta":
            if i > 0 and p["heading"] and seen_stats:
                state, st["title"] = "title", text
            else:
                st["meta"].append(text)
                seen_stats = seen_stats or "Stats:" in text
            continue
        if state == "title" and text.startswith("by ") and not st["author"]:
            st["author"] = text[3:].strip()
            state = "preface"
            continue
        # Word-made PDFs sometimes glue the next label onto the end of the
        # previous paragraph ("...with him. Notes"): split it off.
        glued = re.match(r"^(.*\S)\s+(Summary|Notes)$", text, re.S)
        if glued and text not in SECTION_LABELS and state in ("title", "preface") and section in ("summary", "notes"):
            st[section].append(re.sub(r"\s+(Summary|Notes)$", "", p["html"]))
            section = SECTION_LABELS[glued.group(2)]
            continue
        if text in SECTION_LABELS:
            section = SECTION_LABELS[text]
            if section in ("ch_summary", "ch_notes") and (chapter is None or chapter["body"]):
                chapter = new_chapter()
            continue
        if p["heading"]:
            state, section, chapter = "body", "body", new_chapter(text)
            continue
        if state in ("title", "preface"):
            if section in ("summary", "notes") and (p["indented"] or not page_changed):
                st[section].append(p["html"])
                continue
            state = "body"
        if section in ("ch_summary", "ch_notes", "ch_end", "end") and p["indented"]:
            (st["end"] if section == "end" else chapter[section]).append(p["html"])
            continue
        if section == "end":
            continue  # stray text after the work's end notes
        section = "body"
        if chapter is None:
            chapter = new_chapter()
        chapter["body"].append(('<p class="center">' if p["centered"] else "<p>") + p["html"] + "</p>")
    return st


def blockquote(paras):
    return '<blockquote class="userstuff">' + "".join(f"<p>{x}</p>" for x in paras) + "</blockquote>"


def tag_dd(value):
    return ", ".join(html.escape(t.strip()) for t in value.split(",") if t.strip())


def render(st, work_id):
    meta, _ = parse_meta(" \n".join(st["meta"]))
    title = st["title"] or "Untitled"
    author = st["author"] or "Anonymous"
    rows = []
    for k, label in META_OUT.items():
        if k in meta and meta[k]:
            val = tag_dd(meta[k]) if k not in ("rating", "language") else html.escape(meta[k])
            rows.append(f"<dt>{label}</dt>\n<dd>{val}</dd>")
    rows.append("<dt>Stats:</dt>\n<dd>\n" + "\n".join(stats_lines(meta.get("stats", ""))) + "\n</dd>")

    pre = ""
    if st["summary"]:
        pre += f"<p>Summary</p>\n{blockquote(st['summary'])}\n"
    if st["notes"]:
        pre += f"<p>Notes</p>\n{blockquote(st['notes'])}\n"

    chs = []
    multi = len(st["chapters"]) > 1
    for n, ch in enumerate(st["chapters"], 1):
        m = ""
        if ch["ch_summary"]:
            m += f"<p>Chapter Summary</p>\n{blockquote(ch['ch_summary'])}\n"
        if ch["ch_notes"]:
            m += f"<p>Chapter Notes</p>\n{blockquote(ch['ch_notes'])}\n"
        heading = ch["heading"] or (f"Chapter {n}" if multi else "")
        block = ""
        if heading or m:
            block += f'<div class="meta group">\n<h2 class="heading">{html.escape(heading)}</h2>\n{m}</div>\n'
        block += '<div class="userstuff">\n' + "\n".join(ch["body"]) + "\n</div>\n"
        if ch["ch_end"]:
            block += f'<div class="endnotes">\n<p>Chapter End Notes</p>\n{blockquote(ch["ch_end"])}\n</div>\n'
        chs.append(block)

    after = f"<p>Notes</p>\n{blockquote(st['end'])}\n" if st["end"] else ""
    fandoms = meta.get("fandom", "")
    url = f"https://archiveofourown.org/works/{work_id}"
    t = html.escape(title)
    doc = f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
<head>
<meta charset="utf-8" />
<title>{t} - {html.escape(author)} - {html.escape(fandoms)}</title>
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
<div class="byline">by <a rel="author" href="https://archiveofourown.org/users/{html.escape(author)}">{html.escape(author)}</a></div>
{pre}</div>
</div>

<div id="chapters" class="userstuff">
{chr(10).join(chs)}
</div>

<div id="afterword">
<h2 class="toc-heading">Afterword</h2>
{after}<p class="message">Converted from PDF by pdf_convert.py.</p>
</div>

</body>
</html>
"""
    body_words = sum(len(re.sub(r"<[^>]+>", " ", b).split()) for ch in st["chapters"] for b in ch["body"])
    ao3_words = int(re.sub(r"\D", "", re.search(r"Words:\s*([\d,]+)", meta.get("stats", "") + " Words: 0").group(1)) or 0)
    chap_stat = (re.search(r"Chapters:\s*(\S+)", meta.get("stats", "")) or [None, "?"])[1]
    return doc, title, body_words, ao3_words, len(st["chapters"]), chap_stat


def convert(path: Path):
    with pdfplumber.open(path) as pdf:
        first = pdf.pages[0].extract_text() or ""
        m = re.search(r"archiveofourown\.org/works/(\d+)", first)
        if not m:
            return None, "no AO3 work link on first page"
        lines = read_lines(pdf)
    stats = layout_stats(lines)
    st = build_structure(paragraphs(lines, *stats))
    return m.group(1), render(st, m.group(1))


def main():
    # Windows consoles default to cp1252, which can't print the "⚠" flag
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    if not args:
        # no folder given: use the project's usual drop folder
        if not DEFAULT_DIR.is_dir():
            print(f"{DEFAULT_DIR} doesn't exist. Create it and put your .pdf files in it, or pass a folder.")
            sys.exit(1)
        args = [str(DEFAULT_DIR)]
    files = []   # (file, sub-folder it sat in, or "")
    for arg in args:
        p = Path(arg).expanduser()
        if p.is_dir():
            for f in sorted(p.rglob("*.pdf")):
                rel_dirs = f.parent.relative_to(p).parts
                files.append((f, rel_dirs[0] if rel_dirs else ""))
        else:
            files.append((p, ""))

    have = existing_ids()
    for path, folder in files:
        try:
            with pdfplumber.open(path) as pdf:
                first = pdf.pages[0].extract_text() or ""
            m = re.search(r"archiveofourown\.org/works/(\d+)", first)
            if not m:
                print(f"SKIP  {path.name}: no AO3 work link")
                continue
            if m.group(1) in have:
                print(f"SKIP  {m.group(1)}: already in archive/ or staging/")
                continue
            work_id, (doc, title, got, want, nch, chap_stat) = convert(path)
            out = STAGING_DIR / folder / slugify(work_id, title)
            pct = f"{100 * got / want:.0f}%" if want else "?"
            flag = "" if want and 0.9 <= got / want <= 1.1 else "  ⚠ check"
            if not dry:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(doc, encoding="utf-8")
            have.add(work_id)
            print(f"OK    {folder + '/' if folder else ''}{out.name}  ({nch} ch, AO3 {chap_stat}; words {got}/{want} = {pct}){flag}")
        except Exception as e:
            print(f"FAIL  {path.name}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
