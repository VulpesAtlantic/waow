"""
assets/docx_convert.py
======================
One-off converter for AO3 works saved as Word documents (.docx): either a
real AO3-style export or a PDF that was converted to Word. Rebuilds each one
in the same layout as an AO3 HTML download and writes it to staging/ as
{workID}_{slug}.html, ready for phase4_process.py.

Needs python-docx (pip install python-docx). Reuses the renderer in
pdf_convert.py, so pdfplumber must be installed too.

    python assets/docx_convert.py ~/Downloads/some_folder [more folders or .docx files…]
    python assets/docx_convert.py --dry-run ~/Downloads/some_folder

Skips any work ID already in archive/ or staging/, and any document with no
AO3 work link (the work ID is the archive's primary key).

How the structure is recovered (Word files carry far less than AO3 HTML):
  * metadata  — the Rating/Fandom/Stats… table on the first page
  * title     — first centered paragraph after that table; "by …" is the author
  * summary / notes — the "Summary" and "Notes" labels; the preface ends at the
                page break that precedes chapter 1
  * chapters  — "Chapter N…" paragraphs or Word Heading styles
  * scene breaks — centered paragraphs made only of symbols
The "words" figure in the output compares the recovered story text against
AO3's own word count from the Stats row, as a sanity check.
"""

import html
import re
import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn

sys.path.insert(0, str(Path(__file__).parent))
from mht_convert import STAGING_DIR, existing_ids, slugify
from pdf_convert import DROP_LINE, SECTION_LABELS, render

WORK_URL = re.compile(r"archiveofourown\.org/works/(\d+)")
CHAPTER_HEAD = re.compile(r"^chapter\s+\d+\b", re.I)
LABEL_AT_END = re.compile(r"^(.*\S)\s+(Summary|Notes|Chapter Summary|Chapter Notes|End Notes)$", re.S)
SYMBOLS_ONLY = re.compile(r"^[\W_]+$")


# ---------------------------------------------------------------------------
# Reading the document
# ---------------------------------------------------------------------------

def read_paragraph(p):
    """Return text, styled html and layout hints for one <w:p>."""
    segs = []        # [text, bold, italic]
    page_break = False
    for r in p.iter(qn("w:r")):
        rpr = r.find(qn("w:rPr"))
        bold = rpr is not None and rpr.find(qn("w:b")) is not None and rpr.find(qn("w:b")).get(qn("w:val")) not in ("0", "false")
        ital = rpr is not None and rpr.find(qn("w:i")) is not None and rpr.find(qn("w:i")).get(qn("w:val")) not in ("0", "false")
        for child in r:
            tag = child.tag
            if tag == qn("w:t"):
                text = child.text or ""
            elif tag == qn("w:tab"):
                text = " "
            elif tag == qn("w:br"):
                if child.get(qn("w:type")) == "page":
                    page_break = True
                    continue
                text = "\n"
            else:
                continue
            if segs and segs[-1][1] == bold and segs[-1][2] == ital:
                segs[-1][0] += text
            else:
                segs.append([text, bold, ital])

    text = "".join(s[0] for s in segs).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text).strip()

    parts = []
    for t, b, i in segs:
        t = html.escape(t.replace("\xa0", " ")).replace("\n", "<br />")
        if not t.strip():
            parts.append(t)
            continue
        lead, core, trail = re.match(r"^(\s*)(.*?)(\s*)$", t, re.S).groups()
        if i:
            core = f"<em>{core}</em>"
        if b:
            core = f"<strong>{core}</strong>"
        parts.append(lead + core + trail)
    inner = re.sub(r"\s+", " ", "".join(parts)).strip()
    inner = re.sub(r"</em>\s*<em>", " ", inner)

    ppr = p.find(qn("w:pPr"))
    centered, style = False, ""
    if ppr is not None:
        jc = ppr.find(qn("w:jc"))
        centered = jc is not None and jc.get(qn("w:val")) == "center"
        ps = ppr.find(qn("w:pStyle"))
        style = ps.get(qn("w:val")) if ps is not None else ""
        if ppr.find(qn("w:pageBreakBefore")) is not None:
            page_break = True  # treated as "break before"; see walk()

    nonblank = [s for s in segs if s[0].strip()]
    return {
        "text": text,
        "html": inner,
        "centered": centered,
        "heading_style": style.lower().startswith("heading"),
        "all_bold": bool(nonblank) and all(s[1] for s in nonblank),
        "page_break": page_break,
        "break_before": ppr is not None and ppr.find(qn("w:pageBreakBefore")) is not None,
    }


def walk(path):
    """Yield ('p', paragraph-dict) and ('tbl', [[cell text…]…]) in body order,
    with 'page_start' set on the first paragraph after a page break."""
    d = docx.Document(path)
    items, pending_break = [], False
    for el in d.element.body.iterchildren():
        if el.tag == qn("w:p"):
            para = read_paragraph(el)
            para["page_start"] = pending_break or para["break_before"]
            pending_break = para["page_break"] and not para["break_before"]
            items.append(("p", para))
        elif el.tag == qn("w:tbl"):
            rows = []
            for tr in el.iter(qn("w:tr")):
                cells = []
                for tc in tr.findall(qn("w:tc")):
                    # a wrapped cell is stored as several paragraphs: join them with a space
                    cells.append(re.sub(r"\s+", " ", " ".join("".join(t.text or "" for t in cp.iter(qn("w:t"))) for cp in tc.iter(qn("w:p")))).strip())
                rows.append(cells)
            items.append(("tbl", rows))
    return d, items


def find_work_id(doc, items):
    """AO3 work ID from visible text first, then from hyperlink targets."""
    blob = " ".join(p["text"] for k, p in items[:12] if k == "p")
    blob += " " + " ".join(c for k, rows in items[:12] if k == "tbl" for r in rows for c in r)
    m = WORK_URL.search(blob)
    if m:
        return m.group(1)
    for rel in doc.part.rels.values():
        m = WORK_URL.search(getattr(rel, "target_ref", "") or "")
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Building the structure pdf_convert.render() expects
# ---------------------------------------------------------------------------

def build_structure(items):
    st = {"meta": [], "title": "", "author": "", "summary": [], "notes": [], "chapters": [], "end": []}
    state, section, chapter = "meta", None, None
    saw_table, saw_page_break = False, False

    def new_chapter(heading=""):
        ch = {"heading": heading, "ch_summary": [], "ch_notes": [], "body": [], "ch_end": []}
        st["chapters"].append(ch)
        return ch

    for kind, item in items:
        if kind == "tbl":
            if not saw_table:
                saw_table = True
                for row in item:
                    if len(row) >= 2 and row[0].endswith(":"):
                        st["meta"].append(f"{row[0]} {row[1]}")
            continue

        p = item
        text = p["text"]
        if not text:
            continue
        if DROP_LINE.search(text):
            continue

        # --- cover page: wait for the metadata table, then the title block ---
        if state == "meta":
            if saw_table and p["centered"]:
                st["title"], state = text, "title"
            continue
        if state == "title":
            if text.lower().startswith("by ") and not st["author"]:
                st["author"], state = text[3:].strip(), "preface"
            continue

        # --- a "Notes" label glued to the end of the previous paragraph ---
        glued = LABEL_AT_END.match(text)
        if state == "preface" and glued and glued.group(2) in SECTION_LABELS and not p["all_bold"]:
            body_html = re.sub(r"\s*(Chapter Summary|Chapter Notes|End Notes|Summary|Notes)$", "", p["html"])
            _put(st, state, section, chapter, body_html, p)
            text = glued.group(2)
            p = {**p, "page_start": False}

        if text in SECTION_LABELS:
            section = SECTION_LABELS[text]
            if section in ("ch_summary", "ch_notes") and (chapter is None or chapter["body"]):
                chapter = new_chapter()
                state = "body"
            continue

        if state == "preface":
            # the preface ends at the page break that precedes chapter 1
            if p["page_start"] and section in ("summary", "notes"):
                saw_page_break = True
                state, section = "body", "body"
            elif p["heading_style"] or CHAPTER_HEAD.match(text):
                state, section = "body", "body"
            else:
                _put(st, state, section, chapter, p["html"], p)
                continue

        if p["heading_style"] or (CHAPTER_HEAD.match(text) and p["all_bold"] and len(text) < 120):
            state, section, chapter = "body", "body", new_chapter(text)
            continue

        if section in ("ch_summary", "ch_notes") and not p["page_start"]:
            chapter[section].append(p["html"])
            continue
        if section in ("ch_end", "end"):
            (st["end"] if section == "end" else chapter["ch_end"]).append(p["html"])
            continue

        section = "body"
        if chapter is None:
            chapter = new_chapter()
        scene_break = p["centered"] and SYMBOLS_ONLY.match(text)
        chapter["body"].append('<p class="center">' + p["html"] + "</p>" if scene_break else "<p>" + p["html"] + "</p>")
    st["_saw_page_break"] = saw_page_break
    return st


def _put(st, state, section, chapter, html_text, p):
    if not html_text:
        return
    if state == "preface" and section in ("summary", "notes"):
        st[section].append(html_text)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def convert(path: Path):
    d, items = walk(path)
    work_id = find_work_id(d, items)
    if not work_id:
        return None, "no AO3 work link"
    st = build_structure(items)
    saw_break = st.pop("_saw_page_break", True)
    doc, title, got, want, nch, chap_stat = render(st, work_id)
    doc = doc.replace("Converted from PDF by pdf_convert.py.", "Converted from Word by docx_convert.py.")
    return work_id, (doc, title, got, want, nch, chap_stat, saw_break)


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    if not args:
        print(__doc__)
        sys.exit(1)
    files = []
    for arg in args:
        p = Path(arg).expanduser()
        files += sorted(f for f in p.glob("*.docx") if not f.name.startswith("~$")) if p.is_dir() else [p]

    have = existing_ids()
    for path in files:
        try:
            work_id, result = convert(path)
            if work_id is None:
                print(f"SKIP  {path.name}: {result}")
                continue
            if work_id in have:
                print(f"SKIP  {work_id}: already in archive/ or staging/")
                continue
            doc, title, got, want, nch, chap_stat, saw_break = result
            out = STAGING_DIR / slugify(work_id, title)
            pct = f"{100 * got / want:.0f}%" if want else "?"
            flag = "" if want and 0.9 <= got / want <= 1.1 else "  ⚠ check word count"
            if not saw_break:
                flag += "  ⚠ no page break after summary/notes, check the preface"
            if not dry:
                out.write_text(doc, encoding="utf-8")
            have.add(work_id)
            print(f"OK    {out.name}  ({nch} ch, AO3 {chap_stat}; words {got}/{want} = {pct}){flag}")
        except Exception as e:
            print(f"FAIL  {path.name}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
