"""Build the investigator's document corpus (public TfL / GLA records) and load it into Atlas.

Reproducible: every source is a public URL in SOURCES. Each part is downloaded once into
data/corpus/files/ (gitignored; --refresh re-downloads), its text extracted (PDF: pdfplumber, one
entry per page, only the pages listed in `pages` for big PDFs; HTML: the page's main element with
navigation / related-links boilerplate cut; tables become 'cell | cell' rows), normalised (NFKC
for ligatures and no-break spaces, soft hyphens and zero-width characters removed, curly quotes
straightened, whitespace collapsed) and chunked into ~300-600 words on line boundaries, keeping
the page number(s). Chunks go to data/corpus/chunks.jsonl and are upserted into Atlas
tubesense.documents keyed by '<doc_id>#<chunk_no>' (unchanged chunks are not rewritten, so
autoEmbed does not re-embed them; chunks of a doc that no longer exist are deleted).

Chunk schema: {_id, source='corpus', doc_id, chunk_no, part, title, publisher, doc_date, url,
page, page_end, doc_type, reliability, lines, sections, text, words, note, attribution, hash}.
PDF chunk text carries a '[p.N]' line wherever a new PDF page starts (also at the top), so a quote
can be cited to its page; strip r'\\[p\\.\\d+\\]\\n?' before matching quotes that cross a page break.
Lone page-number lines at the top or bottom of a page are dropped. `sections` holds the section
codes (LST-BTH, BTH-MLE, GPK-VIC) whose two stations are joined by a connector in the chunk
("Liverpool Street to Bethnal Green", "BEG-MIE", "Mile End and Bethnal Green", "a | b"), else the
doc default. `title` is our descriptive title, not the source's own (search matches on it).
doc_date is the date printed on the document (answer / letter / report / notice), or for an FOI
response the page's 'Date published'.

Timetable PDFs (TTN / WTT) also get a parsed-table chunk: each 'A and B' row with the distance read
from the distance column by word position (the plain text interleaves the subtotal column), and
each printed subtotal checked against a run of consecutive rows.

Only chunks tagged source='corpus' are ever rewritten or deleted here, so other documents in the
same collection (evidence packs, claims) are left alone, also by --prune.

If a URL fails, a local pdftotext copy (form-feed page breaks) is used when one is listed; parts
that still cannot be read are printed in a 'missing' list. Nothing is ever paraphrased or invented.
Attribution: no page we fetched carries an Open Government Licence notice, so no chunk claims one;
everything is quoted for citation only.

Run:   .venv/Scripts/python -m atlas.corpus                 (download if needed, chunk, upsert)
       .venv/Scripts/python -m atlas.corpus --no-atlas      (local chunks.jsonl only)
       .venv/Scripts/python -m atlas.corpus --scan DOC_ID   (per-page keyword hits, to pick pages)
       .venv/Scripts/python -m atlas.corpus --refresh --prune
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import requests

from atlas.db import ROOT

OUT = ROOT / "data" / "corpus"
FILES = OUT / "files"
TSDOCS = Path.home() / "AppData" / "Local" / "Temp" / "tsdocs"   # pdftotext copies from the research pass
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 tubesense-corpus"}

# Licence wording: none of the fetched pages shows an OGL notice (GLA footer: '(c) Copyright Greater
# London Authority'; TfL FOI pages: 'Copyright TfL'), so nothing here claims a re-use licence.
NOT_VERIFIED = "Re-use licence not verified; quoted for citation only."
ATTRIBUTION = {
    "TfL-FOI": "Disclosed by Transport for London under FOIA/EIR (ref {ref}), tfl.gov.uk FOI disclosure log; "
               "(c) Transport for London. " + NOT_VERIFIED,
    "TfL-GLA": "Transport for London document published by the London Assembly as a committee paper "
               "(london.gov.uk); (c) Transport for London. " + NOT_VERIFIED,
    "TfL": "Source: Transport for London (board.tfl.gov.uk Board / panel papers); (c) Transport for London. "
           + NOT_VERIFIED,
    "GLA": "Source: Greater London Authority / London Assembly (london.gov.uk); (c) Greater London "
           "Authority. " + NOT_VERIFIED,
    "Brixton Buzz": "Source: Brixton Buzz (brixtonbuzz.com), (c) the publisher. Secondary news report kept "
                    "for citation and cross-checking only; not re-published.",
}
DOC_TYPES = {"foi", "letter", "mqt", "written_answer", "minutes", "presentation", "timetable", "report", "news"}

# Section code -> station-name patterns; a section is tagged only when the two names are joined by a
# connector ('to', 'and', '-', '|', ...), so 'Green Park Southbound Victoria' or a 'Victoria' line
# header under 'Green Park' does not count.
STATIONS = {
    "LST": r"Liverpool\s+St(?:reet|\.)?|\bLVT\b|\bLST\b",
    "BTH": r"Bethnal\s+Gr(?:ee)?n|\bBEG\b|\bBNG\b|\bBTH\b",
    "MLE": r"Mile\s+End|\bMIE\b|\bMLE\b",
    "GPK": r"Green\s+Park|\bGPK\b",
    "VIC": r"(?<!(?:North|South)bound\s)(?<!(?:East|West)bound\s)\bVictoria\b(?!\s+[Ll]ine)|\bVIC\b",
}
SECTIONS = {"LST-BTH": ("LST", "BTH"), "BTH-MLE": ("BTH", "MLE"), "GPK-VIC": ("GPK", "VIC")}
JOIN = r"\s*(?:\((?:[A-Z]{3}|[^()\n]{0,12})\)\s*)?(?:to|and|-|–|—|\||/|→|>)\s*"
LINE_HEADER = re.compile(r"(?m)^[ \t]*(?:Victoria|Central|Northern|Jubilee)(?:\s+line)?[ \t]*$")
PAGE_MARK = re.compile(r"\[p\.\d+\]\n?")


@dataclass
class Part:
    url: str
    kind: str                                # pdf | html
    pages: list[int] | None = None           # 1-based pages to keep (PDF); None = all
    select: str = "main"                     # HTML: CSS selector of the content element
    cut: tuple[str, ...] = ()                # HTML: drop everything from the first of these lines on
    local: str | None = None                 # pdftotext copy in TSDOCS, used only if the URL fails
    note: str | None = None                  # stored on every chunk of this part
    label: str = ""
    table: bool = False                      # timetable distances page: add a parsed-table chunk


@dataclass
class Source:
    doc_id: str
    title: str
    publisher: str                           # key of ATTRIBUTION
    doc_date: str                            # ISO date of the document (answer / letter / publication)
    doc_type: str
    reliability: str                         # primary | secondary
    parts: list[Part]
    lines: list[str] = field(default_factory=lambda: ["Central"])
    sections: list[str] = field(default_factory=list)   # default when a chunk names none


def rng(a: int, b: int) -> list[int]:
    return list(range(a, b + 1))


GLA_DOCS = "https://www.london.gov.uk/about-us/londonassembly/meetings/documents/"
MQ = "https://www.london.gov.uk/who-we-are/what-london-assembly-does/questions-mayor/find-an-answer/"
FOI = "https://tfl.gov.uk/corporate/transparency/freedom-of-information/foi-request-detail?referenceId="
MQ_CUT = ("On this page", "Related questions")
FOI_CUT = ("Attachments", "Want to make a request?")
TT_NOTE = ("column layout jumbled by PDF extraction: subtotal column interleaved with the distances, so the "
           "first number on a line is not always that row's distance; cite distances from the parsed-table chunk")

SOURCES: list[Source] = [
    Source("FOI-1231-2425", "Excessive Noise - Central Line (in-cab LAeq by section, 24 June 2024)", "TfL",
           "2024-08-08", "foi", "primary",
           [Part(FOI + "FOI-1231-2425", "html", cut=FOI_CUT, label="FOI response page")]),
    Source("MQ-2022-1791", "Mayor's Question 2022/1791 Tube Noise (2): sections ground for noise "
           "Dec 2021-May 2022 and planned grinding", "GLA", "2022-06-28", "written_answer", "primary",
           [Part(MQ + "tube-noise-2-22", "html", cut=MQ_CUT, label="answer page"),
            Part(GLA_DOCS + "s99917/Minutes%20-%20Appendix%203a%20-%20Written%20Answer%20Report%20-"
                 "%20Combined%20Appendices.pdf", "pdf", pages=rng(1, 3), local="wa.txt",
                 label="attachment as reproduced in Written Answer Report appendices (s99917)")],
           lines=["Central", "District", "Hammersmith & City", "Jubilee", "Northern", "Victoria"]),
    Source("MQ-2022-2799", "Mayor's Question 2022/2799 Outstanding Rail Grinding Works - Tube Noise", "GLA",
           "2022-12-20", "written_answer", "primary",
           [Part(MQ + "outstanding-rail-grinding-works-tube-noise", "html", cut=MQ_CUT, label="answer page")],
           lines=["Central", "Northern", "Jubilee", "Victoria"]),
    Source("MQT-2026-2156", "Mayor's Question Time 16 July 2026, Question 2026/2156 Tube Noise "
           "(Elly Baker AM), transcript", "GLA", "2026-07-16", "mqt", "primary",
           [Part(GLA_DOCS + "s123225/Minutes%20-%20Appendix%202%20-%20Questions%20to%20the%20Mayor.pdf",
                 "pdf", pages=rng(1, 3), local="mq.txt", label="Minutes Appendix 2 (s123225)")],
           lines=["Central", "Victoria", "Northern", "Jubilee"]),
    Source("TfL-noise-update-2024-08", "TfL Tube Noise: January to June 2024 update to London Assembly "
           "Members (Claire Mann, 9 Aug 2024)", "TfL", "2024-08-09", "letter", "primary",
           [Part(GLA_DOCS + "s112802/Appendix%207a%20-%20Tube%20Noise%20Update%20from%20the%20Chief%20"
                 "Operating%20Officer%20TfL%20received%209%20August%202024.pdf", "pdf", local="tfl_aug2024.txt",
                 label="Appendix 7a (s112802)")],
           lines=["Victoria", "Northern", "Jubilee", "Central", "Piccadilly", "Bakerloo"]),
    Source("TfL-noise-update-2025-08", "TfL Tube Noise Update: January to July 2025 to London Assembly "
           "Members (Claire Mann, 20 Aug 2025)", "TfL", "2025-08-20", "letter", "primary",
           [Part(GLA_DOCS + "s118145/Appendix%2012%20-%20Aug%2025%20Tube%20Noise%20Assembly%20Update%201.pdf",
                 "pdf", local="tfl_aug2025.txt", label="Appendix 12 (s118145)")],
           lines=["Victoria", "Northern", "Jubilee", "Central", "Piccadilly", "Bakerloo"]),
    Source("Weir-letter-2022-12", "Duncan Weir (TfL Head of Track) letter to the Environment Committee, "
           "Tube noise commitments, 9 Dec 2022", "TfL", "2022-12-09", "letter", "primary",
           [Part(GLA_DOCS + "s103062/4c%20Commitment%20Response%20-%20Tube%20noise%20-%20Duncan%20Weir%20-"
                 "%20with%20two%20attachments%20relating%20to%20commitment%201%20.pdf", "pdf", local="weir.txt",
                 label="commitment response with two attachments (s103062)")],
           lines=["Central", "Victoria", "Northern", "Jubilee"]),
    Source("CSOPP-2022-12-tube-noise", "TfL Customer Service and Operational Performance Panel, "
           "Tube Noise presentation, 6 Dec 2022 (item 9b)", "TfL", "2022-12-06", "presentation", "primary",
           [Part("https://board.tfl.gov.uk/documents/s19158/CSOPP%20-%20221206%20-%20item9b%20-"
                 "Tubenoisepresentation.pdf", "pdf", label="presentation")],
           lines=["Central", "Victoria", "Northern", "Jubilee"]),
    Source("FOI-1730-1819", "Cleaning of Central line to stop vibration noise; Rail Roughness Plots "
           "BEG-MIE January 2018", "TfL", "2018-10-22", "foi", "primary",
           [Part(FOI + "FOI-1730-1819", "html", cut=FOI_CUT, label="FOI response page"),
            Part("https://foi.tfl.gov.uk/FOI-1730-1819/Rail%20Roughness%20Plots%20BEG-MIE%20January%202018.pdf",
                 "pdf", local="roughness.txt", label="roughness plots",
                 note="figure-only: requires figure reading (the PDF's text is captions only)")],
           sections=["BTH-MLE"]),
    Source("FOI-3481-2425", "Central line Working Timetable Notice TTN 37/25: distances between stations",
           "TfL", "2025-01-27", "timetable", "primary",
           [Part(FOI + "FOI-3481-2425", "html", cut=FOI_CUT, label="FOI response page"),
            Part("https://foi.tfl.gov.uk/FOI-3481-2425/3481-2425-Working%20Timetable%20Notice%20Central%20"
                 "Line%20TTN%2037-25.pdf", "pdf", pages=[1, 3], label="TTN 37/25 distances pages",
                 note=TT_NOTE, table=True)]),
    Source("FOI-1642-2021", "Circle and Hammersmith & City Working Timetable 31 (18 May 2014): distances "
           "between stations", "TfL", "2014-05-18", "timetable", "primary",
           [Part("https://foi.tfl.gov.uk/FOI-1642-2021/WTT%2031%20Circle%20and%20Hammersmith%20and%20City%20"
                 "18%20May%202014.pdf", "pdf", pages=[1, 3], label="WTT 31 distances pages",
                 note=TT_NOTE, table=True)],
           lines=["Circle", "Hammersmith & City"]),
    Source("FOI-3780-2324", "Noise at work assessment for Hammersmith & City line train operators "
           "(TfL report RP-R2839, 2 Oct 2020), disclosed under FOI-3780-2324", "TfL", "2020-10-02", "report",
           "primary",
           [Part(FOI + "FOI-3780-2324", "html", cut=FOI_CUT, label="FOI response page"),
            Part("https://foi.tfl.gov.uk/FOI-3780-2324/R2839%20HC%20Line%20Noise%20at%20Work_Redacted.pdf",
                 "pdf", local="hc.txt", label="report RP-R2839")],
           lines=["Hammersmith & City"]),
    Source("FOI-2986-2324", "Central line noise: Central line cab recordings (September 2023)", "TfL",
           "2023-12-12", "foi", "primary",                # FOI page 'Date published'
           [Part(FOI + "FOI-2986-2324", "html", cut=FOI_CUT, label="FOI response page"),
            Part("https://foi.tfl.gov.uk/FOI-2986-2324/Central%20Line%20cab%20recordings.pdf", "pdf",
                 label="cab recordings")]),
    Source("BrixtonBuzz-2026-01-27", "The Victoria tube line is officially the loudest in London "
           "(Brixton Buzz, 27 Jan 2026)", "Brixton Buzz", "2026-01-27", "news", "secondary",
           [Part("https://www.brixtonbuzz.com/2026/01/the-victoria-tube-line-is-officially-the-loudest-in-london/",
                 "html", select=".entry-content", label="article",
                 cut=("Please donate to Brixton Buzz", "Share this:", "Like this:"))],
           lines=["Victoria", "Central", "Northern"]),
]


# ---------- download ----------

def fetch(part: Part, path: Path, refresh: bool) -> Path:
    if path.exists() and path.stat().st_size and not refresh:
        return path
    last = None
    for attempt in range(3):
        try:
            r = requests.get(part.url, headers=UA, timeout=90)
            r.raise_for_status()
            if part.kind == "pdf" and not r.content.startswith(b"%PDF"):
                raise ValueError(f"not a PDF ({r.headers.get('content-type')})")
            path.write_bytes(r.content)
            return path
        except Exception as e:   # noqa: BLE001  network / HTTP / content errors all mean 'try again'
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{type(last).__name__}: {last}")


# ---------- extraction: -> list[(page or None, text)] ----------

def pdf_pages(path: Path, keep: list[int] | None) -> list[tuple[int, str]]:
    import pdfplumber
    out = []
    with pdfplumber.open(path) as pdf:
        for i in keep or range(1, len(pdf.pages) + 1):
            if i > len(pdf.pages):
                continue
            p = pdf.pages[i - 1]
            ls = (p.extract_text() or "").split("\n")
            p.close()                                     # free the page's parsed objects (low RAM)
            while ls and re.fullmatch(r"\s*\d{1,3}\s*", ls[-1]):   # page number in the footer
                ls.pop()
            while ls and re.fullmatch(r"\s*\d{1,3}\s*", ls[0]):    # ... or the header
                ls.pop(0)
            out.append((i, "\n".join(ls)))
    return out


NUM = re.compile(r"\d*\.\d\d")


def distance_table(path: Path, keep: list[int] | None) -> list[tuple[int, str]]:
    """Timetable 'DISTANCES BETWEEN STATIONS' pages -> one 'A | B | km' row per 'A and B' line.
    The distance is the number in the left (distance) column nearest the row by height; numbers
    in the next column are subtotals, each checked against a run of consecutive rows."""
    import pdfplumber
    out = []
    with pdfplumber.open(path) as pdf:
        for pg in keep or range(1, len(pdf.pages) + 1):
            p = pdf.pages[pg - 1]
            ws = p.extract_words()
            p.close()
            nums = [w for w in ws if NUM.fullmatch(w["text"]) and w["x0"] < 115]
            dist = [w for w in nums if w["x0"] < 80]
            subs = [w for w in nums if w["x0"] >= 80]
            rows, used = [], set()
            for a in (w for w in ws if w["text"] == "and" and w["x0"] >= 115):
                line = sorted((w for w in ws if abs(w["top"] - a["top"]) <= 2.5 and w["x0"] >= 115),
                              key=lambda w: w["x0"])
                name = []
                for w in line:
                    t = re.sub(r"\.{2,}.*", "", w["text"])          # 'Bois......' -> 'Bois'
                    if not re.search(r"[A-Za-z]", t):
                        if name:
                            break                     # dot leaders / running times start
                        continue
                    name.append(t)
                    if t != w["text"]:
                        break
                m = re.fullmatch(r"(.+?) and (.+)", normalise(" ".join(name)))
                if not m:
                    continue
                near = min(dist, key=lambda w: abs(w["top"] - a["top"]), default=None)
                src = "distance column"
                if near is None or abs(near["top"] - a["top"]) > 4:
                    near = min(subs, key=lambda w: abs(w["top"] - a["top"]), default=None)
                    src = "subtotal column only"
                    if near is None or abs(near["top"] - a["top"]) > 2.5:
                        continue
                used.add(id(near))
                rows.append((a["top"], m[1], m[2], float(near["text"]), src))
            if not rows:
                continue
            rows.sort()
            lines = [f"Distances between stations, km (parsed from PDF page {pg} by word position)",
                     "Station A | Station B | km"]
            lines += [f"{a} | {b} | {km:.2f}" + ("" if src == "distance column" else f" ({src})")
                      for _, a, b, km, src in rows]
            lines.append("Subtotals printed in the second column, checked against consecutive rows:")
            for s in subs:
                if id(s) in used:
                    continue
                v, mid = float(s["text"]), (s["top"] + s["bottom"]) / 2
                hit = None
                for i in range(len(rows)):
                    for j in range(i + 1, len(rows) + 1):
                        if not rows[i][0] - 6 <= mid <= rows[j - 1][0] + 15:
                            continue
                        tot = sum(r[3] for r in rows[i:j])
                        if abs(tot - v) < 0.006 and (hit is None or j - i < hit[1] - hit[0]):   # exact to 0.01
                            hit = (i, j, tot)
                if hit:
                    i, j, tot = hit
                    lines.append(f"{v:.2f} = {rows[i][1]} to {rows[j - 1][2]}, {j - i} row(s) sum to {tot:.2f}: OK")
                else:
                    lines.append(f"{v:.2f}: no run of consecutive parsed rows sums to it exactly; not checked "
                                 "(rows printed as rotated text are not parsed)")
            out.append((pg, "\n".join(lines)))
    return out


def local_pages(name: str, keep: list[int] | None) -> list[tuple[int, str]]:
    pages = (TSDOCS / name).read_text(encoding="utf-8", errors="replace").split("\f")
    return [(i, t) for i, t in enumerate(pages, 1) if (keep is None or i in keep) and t.strip()]


def html_text(path: Path, part: Part) -> list[tuple[None, str]]:
    from bs4 import BeautifulSoup
    s = BeautifulSoup(path.read_bytes(), "html.parser")
    for t in s(["script", "style", "nav", "header", "footer", "noscript", "form", "aside", "button", "svg"]):
        t.decompose()
    root = s.select_one(part.select) or s.select_one("article") or s.body
    for tr in root.find_all("tr"):                        # table row -> one 'a | b | c' line
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        tr.replace_with(s.new_string("\n" + " | ".join(c for c in cells if c) + "\n"))
    lines = [ln.strip() for ln in root.get_text("\n").splitlines()]
    out = []
    for ln in lines:
        if ln in part.cut:
            break
        if ln and ln not in ("Label", "Content", "Label | Content"):
            out.append(ln)
    # glue label lines ("Request ID", ": FOI-...") the way the page shows them
    text = "\n".join(out)
    text = re.sub(r"\n(:\s)", r"\1", text)
    return [(None, text)]


# ---------- normalisation, sections, chunking ----------

QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
                        "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2032": "'"})
# vulgar fractions -> ' 3/4 ' before NFKC, which would glue '2' and '3/4' into '23/4' (timetables)
FRACTIONS = {c: " " + unicodedata.normalize("NFKC", c).replace("\u2044", "/") + " "
             for c in "\u00bc\u00bd\u00be\u2153\u2154\u215b\u215c\u215d\u215e"}


def normalise(t: str) -> str:
    t = t.translate(str.maketrans(FRACTIONS))
    t = unicodedata.normalize("NFKC", t)                   # ligatures (fi), no-break spaces, full-width
    t = re.sub("\u00ad\\s*\n\\s*", "", t)                 # soft hyphen at a line break: join the word
    t = re.sub("[\u00ad\u200b\u200c\u200d\ufeff\ue000-\uf8ff]", "", t)   # + private-use (dingbat fonts)
    t = t.translate(QUOTES).replace("\r", "")
    t = re.sub(r"(?<=[A-Za-z])-\n(?=[a-z])", "-", t)        # hard hyphen at line end: keep, join lines
    t = re.sub(r"\.{4,}", " ... ", t)                       # timetable dot leaders
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def sections_in(text: str) -> list[str]:
    text = LINE_HEADER.sub("", PAGE_MARK.sub("", text))
    out = []
    for code, (a, b) in SECTIONS.items():
        pa, pb = f"(?:{STATIONS[a]})", f"(?:{STATIONS[b]})"
        if re.search(pa + JOIN + pb, text) or re.search(pb + JOIN + pa, text):
            out.append(code)
    return out


def chunk(pages: list[tuple[int | None, str]], target: int = 450, hard: int = 600,
          minimum: int = 150) -> list[dict]:
    """Greedy line packing: close a chunk at the first sentence/paragraph end after `target` words,
    or at `hard` words. A short tail is merged into the previous chunk."""
    stream = [(pg, ln) for pg, txt in pages for ln in normalise(txt).split("\n")]
    chunks, cur, n = [], [], 0
    for pg, ln in stream:
        w = len(ln.split())
        if cur and n + w > hard:
            chunks.append(cur); cur, n = [], 0
        cur.append((pg, ln)); n += w
        if n >= target and (not ln or ln.rstrip().endswith((".", ":", ";", "?", "!"))):
            chunks.append(cur); cur, n = [], 0
    if cur:
        if chunks and sum(len(l.split()) for _, l in cur) < minimum \
                and sum(len(l.split()) for _, l in chunks[-1] + cur) <= hard + minimum:
            chunks[-1] += cur
        else:
            chunks.append(cur)
    out = []
    for c in chunks:
        if not any(l.strip() for _, l in c):
            continue
        lines, last = [], None
        for p, l in c:
            if p is not None and p != last:          # '[p.N]' wherever a new PDF page starts
                lines.append(f"[p.{p}]")
                last = p
            lines.append(l)
        text = "\n".join(lines).strip()
        pgs = [p for p, _ in c if p is not None]
        out.append({"text": text, "page": pgs[0] if pgs else None, "page_end": pgs[-1] if pgs else None})
    return out


# ---------- build ----------

def build(refresh: bool = False, only: set[str] | None = None) -> tuple[list[dict], list[str]]:
    FILES.mkdir(parents=True, exist_ok=True)
    docs, missing = [], []
    for src in SOURCES:
        assert src.doc_type in DOC_TYPES and src.publisher in ATTRIBUTION, src.doc_id
        if only and src.doc_id not in only:
            continue
        n = 0
        for k, part in enumerate(src.parts):
            path = FILES / f"{src.doc_id}__{k}.{part.kind}"
            try:
                fetch(part, path, refresh)
                pages = pdf_pages(path, part.pages) if part.kind == "pdf" else html_text(path, part)
                url = part.url
            except Exception as e:   # noqa: BLE001
                if part.local and (TSDOCS / part.local).exists():
                    print(f"  {src.doc_id} part {k}: {e}; using local copy {part.local}")
                    pages, url = local_pages(part.local, part.pages), part.url
                else:
                    missing.append(f"{src.doc_id} part {k} ({part.label}): {part.url} -> {e}")
                    continue
            if not any(t.strip() for _, t in pages):
                if part.note:          # figure-only PDF: keep a stub so the investigator knows it exists
                    pages = [(1, "(no extractable text)")]
                else:
                    missing.append(f"{src.doc_id} part {k} ({part.label}): no extractable text")
                    continue
            groups = [(chunk(pages), part.note, part.label)]
            if part.table and part.kind == "pdf" and path.exists():
                tab = distance_table(path, part.pages)
                groups.append((chunk(tab), "parsed table: distance column read by word position, "
                               "subtotals checked against the rows", part.label + " (parsed distances)"))
            for cs, note, label in groups:
                for c in cs:
                    text = f"[{note}]\n{c['text']}" if note else c["text"]
                    d = {"_id": f"{src.doc_id}#{n:03d}", "source": "corpus", "doc_id": src.doc_id,
                         "chunk_no": n, "part": label, "title": src.title, "publisher": src.publisher,
                         "doc_date": src.doc_date, "url": url, "page": c["page"], "page_end": c["page_end"],
                         "doc_type": src.doc_type, "reliability": src.reliability, "lines": src.lines,
                         "sections": sections_in(text) or list(src.sections),
                         "text": text, "words": len(text.split()), "note": note,
                         "attribution": attribution(src, url)}
                    d["hash"] = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]
                    docs.append(d)
                    n += 1
    return docs, missing


def attribution(src: Source, url: str) -> str:
    if src.publisher != "TfL":
        return ATTRIBUTION[src.publisher]
    if src.doc_id.startswith("FOI-"):
        return ATTRIBUTION["TfL-FOI"].format(ref=src.doc_id)
    return ATTRIBUTION["TfL-GLA" if "london.gov.uk" in url else "TfL"]


CORPUS = {"source": "corpus"}


def upsert(docs: list[dict], prune: bool = False) -> dict:
    from pymongo import DeleteMany, ReplaceOne
    from atlas.db import db
    coll = db()["documents"]
    have = {d["_id"]: d.get("hash") for d in coll.find(CORPUS, {"hash": 1})}   # never touch other docs
    ops = [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs if have.get(d["_id"]) != d["hash"]]
    ids = {d["_id"] for d in docs}
    built = {d["doc_id"] for d in docs}
    stale = [i for i in have if i not in ids and i.split("#")[0] in built]
    if prune:
        known = {s.doc_id for s in SOURCES}
        stale += [i for i in have if i not in ids and i.split("#")[0] not in known]
    if stale:
        ops.append(DeleteMany({"_id": {"$in": stale}}))
    if ops:
        coll.bulk_write(ops, ordered=False)
    return {"written": len(ops) - bool(stale), "unchanged": len(docs) - (len(ops) - bool(stale)),
            "deleted": len(stale), "corpus_in_atlas": coll.count_documents({"source": "corpus"}),
            "other_docs_in_collection": coll.count_documents({"source": {"$ne": "corpus"}})}


MQ1791_XLSX = "https://www.london.gov.uk/sites/default/files/mqt/1791%20ATTACHMENT.xlsx"


def check_mq1791(docs: list[dict]) -> str:
    """Cross-check: every cell of the official MQ 2022/1791 attachment (xlsx) appears in the s99917
    reproduction we index. Returns a one-line verdict."""
    import openpyxl
    path = FILES / "MQ-2022-1791__attachment.xlsx"
    try:
        fetch(Part(MQ1791_XLSX, "xlsx"), path, False)
    except Exception as e:   # noqa: BLE001
        return f"MQ-2022-1791 xlsx cross-check skipped: {e}"
    squash = lambda x: re.sub(r"\s+", "", x).lower()      # line wraps differ between xlsx and PDF
    text = squash(" ".join(d["text"] for d in docs if d["doc_id"] == "MQ-2022-1791"))
    wb = openpyxl.load_workbook(path, read_only=True)
    cells = [squash(normalise(str(v))) for ws in wb.worksheets
             for row in ws.iter_rows(values_only=True) for v in row if v is not None and str(v).strip()]
    absent = [c for c in cells if c not in text]
    return (f"MQ-2022-1791 xlsx cross-check: {len(cells) - len(absent)}/{len(cells)} attachment cells found "
            f"in the s99917 pages" + (f"; absent: {absent[:8]}" if absent else ""))


def scan(doc_id: str, pattern: str) -> None:
    """Per-page word count and keyword hits of every PDF part (all pages), to choose `pages`."""
    src = next(s for s in SOURCES if s.doc_id == doc_id)
    for k, part in enumerate(src.parts):
        if part.kind != "pdf":
            continue
        path = fetch(part, FILES / f"{src.doc_id}__{k}.pdf", False)
        for pg, t in pdf_pages(path, None):
            hits = sorted({m.group(0) for m in re.finditer(pattern, t, re.I)})
            if hits:
                print(f"part {k} p{pg:4d} {len(t.split()):5d} words  {hits}  | {t[:80]!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download every source")
    ap.add_argument("--no-atlas", action="store_true", help="write chunks.jsonl only")
    ap.add_argument("--prune", action="store_true", help="also delete chunks of doc_ids not in SOURCES")
    ap.add_argument("--only", nargs="*", help="doc_ids to build (default all)")
    ap.add_argument("--scan", metavar="DOC_ID")
    ap.add_argument("--pattern", default=r"Bethnal|Mile End|Liverpool St|grind|noise|distance")
    args = ap.parse_args()
    FILES.mkdir(parents=True, exist_ok=True)
    if args.scan:
        return scan(args.scan, args.pattern)

    docs, missing = build(args.refresh, set(args.only) if args.only else None)
    keep = []
    if args.only and (OUT / "chunks.jsonl").exists():      # partial build: keep the other docs' chunks
        keep = [json.loads(l) for l in open(OUT / "chunks.jsonl", encoding="utf-8")]
        keep = [d for d in keep if d["doc_id"] not in set(args.only)]
    with open(OUT / "chunks.jsonl", "w", encoding="utf-8") as f:
        for d in keep + docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    per = {}
    for d in docs:
        p = per.setdefault(d["doc_id"], [0, 0, set()])
        p[0] += 1; p[1] += d["words"]; p[2].update(d["sections"])
    print(f"{'doc_id':28s} chunks  words  sections")
    for k, (n, w, s) in per.items():
        print(f"{k:28s} {n:6d} {w:6d}  {','.join(sorted(s))}")
    print(f"total {len(docs)} chunks -> {OUT / 'chunks.jsonl'}")
    if not args.only or "MQ-2022-1791" in args.only:
        print(check_mq1791(docs))
    if not args.no_atlas:
        print("atlas:", upsert(docs, args.prune))
    print("missing:", missing or "none")


if __name__ == "__main__":
    main()
