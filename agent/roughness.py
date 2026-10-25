"""Argon reads TfL's 2018 rail roughness plots (FOI-1730-1819, BTH->MLE eastbound) into checkable numbers. POST-HOC.

Why: TfL's own roughness measurements are the only primary-source route to corrugation evidence we have (our
frequency/speed method was refuted: the roar peaks are fixed resonances). The PDF is figure-only: its text layer
holds the title and 4 captions; every axis, tick label, header line and trace is inside 4 embedded raster images
(1600x598 px), so a model has to read the figures.
How: the public PDF goes inline (application/pdf) to gemini-4-argon (thinking medium) with a response schema
(per figure: verbatim caption and header line, both panels' axes, threshold, global max and peaks, rough zones,
the spectrum level at every printed wavelength band; plus unlabelled elements and a short summary). The SAME
request is sent RUNS times as independent calls. Then deterministic checks, nothing averaged away:
  counts      pages / captions / embedded images / figures; each figure on the page its caption is on;
  captions    each caption is a verbatim substring of pdfplumber's text of its page (dashes and the U+FFFD the
              text layer has in place of the dash are normalised to '-');
  labels      axis labels are looked up in the page text (they are raster, so this is reported as 'not in the
              text layer' rather than passed); rail/band labels must be in the figure's own caption, and the
              position axis title must name the caption's band; axis labels/extremes are compared with the
              printed tick labels transcribed below (TICKS, text read by Claude, not measurements);
  ranges      every reading, band level, zone and threshold lies inside its panel's declared axis range;
  arithmetic  header exceedance % x section length = header exceedance km (rounding tolerance);
  digitizer   a model-free pixel reader on the embedded images: tick marks -> linear axis calibration (edge
              ticks as a check), trace (pure #800000) -> global max, per-column envelope -> exceedance bounds
              the header % must fall in, spectrum marker at each band tick -> dB; rough zones must sit above
              the threshold and above the rest of the trace; pixel-identical panels across figures;
  readings    EVERY max Argon reports is checked against the pixels (run-vs-run agreement cannot catch an error
              both runs make): the envelope maximum within +-10 m of Argon's x must equal Argon's y within
              TOL_AGREE x span; spectrum readings at a printed band must match the pixel level;
  tfl anchor  the only ground truth not written by Claude: TfL's own FOI response states elevated roughness
              between 720 and 880 m (parsed from the corpus text); a zone passing the zone rule must overlap it
              in every figure;
  agreement   run vs run, run vs digitizer, run vs Claude's independent read (reference_points.json), with
              tolerances fixed in advance: agree <= 5 % of the axis span, tight <= 2 %; text must match exactly
              after case, whitespace, dash and micro-sign normalisation. The two spectra are each printed twice
              (pixel-identical), so they are counted once (58 distinct band readings, not 116);
  summary     Argon's prose is NEVER marked verified. A sentence is rejected for BANNED phrasing, identifying an
              unlabelled line, a number that is not in Argon's own extraction (consistency only, circular), or a
              peak / band-level / 'X-Y dB higher' claim the pixels contradict; every other sentence is 'unverified';
  colours     each unlabelled line colour found in the pixels must be named in the text of an element of the
              right panel (position vs spectrum); wrong colour words for the vertical lines are flagged.
Zone rule (fixed before the first run): envelope median inside > threshold and >= 2x the envelope median outside.
Nothing here goes into the evidence pack until a human confirms HUMAN_CHECK.md (written once, never overwritten).
Only the public PDF is sent to Gemini. Outputs: outputs_posthoc/roughness/{roughness.json, roughness.md,
HUMAN_CHECK.md, runs/pdf_<n>.json}; --atlas also upserts into the Atlas collection `roughness` (one document keyed
on the source and the runs' timestamps; Argon's summary prose is left out of it, readings carry their pixel check).
--cached is deterministic: same runs in, byte-identical outputs out. A live batch is saved only when every call succeeded
(runs carry a batch id; --cached refuses a set from different batches; a failed batch is parked in runs/_partial/).
Run: PYTHONPATH=. .venv/Scripts/python -m agent.roughness [--runs 2] [--cached] [--atlas]
     (--cached re-checks the saved runs/pdf_<n>.json without calling Gemini)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from pydantic import BaseModel, Field

from atlas.db import load_env

from . import verify as V

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "data" / "corpus" / "files" / "FOI-1730-1819__1.pdf"
CORPUS = ROOT / "data" / "corpus" / "chunks.jsonl"
ANCHOR_CHUNK = "FOI-1730-1819#000"           # TfL's covering response text (the plots themselves are #001)
READ_WINDOW_M = 10.0                          # an Argon position reading is checked against the pixel envelope within +-10 m
OUT = ROOT / "outputs_posthoc" / "roughness"
MODEL, THINKING = "gemini-4-argon", "medium"
TOL_AGREE, TOL_TIGHT = 0.05, 0.02          # fractions of the axis span, fixed before any run
SOURCE = {"doc_id": "FOI-1730-1819", "corpus_chunk": "FOI-1730-1819#001", "publisher": "TfL", "published": "2018-10-22",
          "title": "Rail Roughness Plots BEG-MIE January 2018",
          "url": "https://foi.tfl.gov.uk/FOI-1730-1819/Rail%20Roughness%20Plots%20BEG-MIE%20January%202018.pdf"}

# Printed tick labels, transcribed by Claude from the figure images (text, not readings). The digitizer maps tick
# MARKS found in the pixels onto these; a count mismatch or a non-linear fit stops it.
BANDS = ["2000", "1600", "1250", "1000", "800", "630", "500", "400", "315", "250", "200", "160", "125", "100", "80.0",
         "63.0", "50.0", "40.0", "31.5", "25.0", "20.0", "16.0", "12.5", "10.0", "8.0", "6.3", "5.0", "4.0", "3.15"]
_X_L = [-4.6276] + list(range(100, 1500, 100)) + [1534.0]
_X_R = [0.0] + list(range(100, 1500, 100)) + [1544.5434]
TICKS = {1: {"pos_x": _X_L, "pos_y": [143, 120, 100, 80, 60, 40, 20, 0], "spec_y": [47, 40, 30, 20, 10, 0, -10, -20, -31]},
         2: {"pos_x": _X_L, "pos_y": [343, 300, 250, 200, 150, 100, 50, 0], "spec_y": [47, 40, 30, 20, 10, 0, -10, -20, -31]},
         3: {"pos_x": _X_R, "pos_y": [117, 100, 80, 60, 40, 20, 0], "spec_y": [48, 40, 30, 20, 10, 0, -10, -20, -31]},
         4: {"pos_x": _X_R, "pos_y": [400, 350, 300, 250, 200, 150, 100, 50, 0], "spec_y": [48, 40, 30, 20, 10, 0, -10, -20, -31]}}
HEADER_THR = {1: 10, 2: 30, 3: 10, 4: 30}   # the '>N µm' printed in each header line (transcribed), for the profile only
SPANS = {n: (abs(t["pos_x"][-1] - t["pos_x"][0]), abs(t["pos_y"][0] - t["pos_y"][-1]), abs(t["spec_y"][0] - t["spec_y"][-1]))
         for n, t in TICKS.items()}          # per figure: position span (m), trace span (µm), spectrum span (dB)
AXIS_TITLES = {"pos_x": "Position (m)", "spec_x": "Wavelength (mm)", "spec_y": "dB rel 1µmRMS",
               "pos_y": "{band} MA Pk-Pk (µm)"}
# Fixed plot layout of the plotting software (pixel rows/cols in each 1600x598 figure image).
LAY = {"pos_ytick_cols": (68, 76), "spec_ytick_cols": (64, 72), "pos_xtick_rows": (255, 263), "spec_xtick_rows": (537, 545),
       "pos_box": (30, 254, 79, 1543), "spec_box": (316, 536, 74, 1565)}
TRACE = (128, 0, 0)
# Exact pixel colours of the unlabelled lines (from the images) and the words that count as naming each one.
LINE_COLOURS = {(128, 128, 128): ("grey", ("grey", "gray")), (255, 0, 255): ("magenta", ("magenta", "purple", "violet", "pink")),
                (128, 128, 0): ("olive", ("olive", "khaki", "mustard", "brown")), (255, 204, 204): ("pink", ("pink", "salmon", "light red"))}


# ---------------------------------------------------------------- response schema
class Axis(BaseModel):
    label: str = Field(description="the axis title exactly as printed in the figure")
    units: str = Field(description="the units as printed, e.g. 'm'")
    min: float = Field(description="numeric value of the lowest printed tick label")
    max: float = Field(description="numeric value of the highest printed tick label")
    tick_labels: list[str] = Field(description="every printed tick label of this axis, left-to-right or top-to-bottom, verbatim")


class Reading(BaseModel):
    kind: Literal["global_max", "local_max", "threshold_line", "other"]
    x: float = Field(description="in the x axis units (for the spectrum: the wavelength in mm)")
    y: float = Field(description="in the y axis units")
    note: str


class BandLevel(BaseModel):
    band_mm: str = Field(description="the wavelength tick label exactly as printed, e.g. '125' or '31.5'")
    db: float = Field(description="level of the dark-red marker curve at that band, to 0.1 dB")


class Zone(BaseModel):
    start: float = Field(description="position in m")
    end: float = Field(description="position in m")
    note: str


class PositionPanel(BaseModel):
    x_axis: Axis
    y_axis: Axis
    threshold_value: Optional[float] = Field(None, description="value of the dashed horizontal line read against the y axis, or null")
    readings: list[Reading] = Field(description="the global maximum of the trace, up to 5 other tallest separate peaks, and the threshold line")
    rough_zones: list[Zone] = Field(description="stretches of >= 25 m where the trace stays mostly above the dashed line")


class SpectrumPanel(BaseModel):
    x_axis: Axis
    y_axis: Axis
    series: list[str] = Field(description="each plotted line: colour, markers, and whether any legend or label names it")
    band_levels: list[BandLevel] = Field(description="the dark-red marker curve at EVERY printed wavelength tick label")
    readings: list[Reading] = Field(description="local maxima of the dark-red curve (bands higher than both neighbours)")


class HeaderValues(BaseModel):
    main_av_pp_um: float
    exceedance_pct: float
    exceedance_km: float
    threshold_um: float


class Figure(BaseModel):
    figure_no: int
    page: int
    figure_caption: str = Field(description="the caption under the figure, verbatim from the page")
    header_text: str = Field(description="the line of text printed at the top INSIDE the figure image, verbatim")
    header_values: HeaderValues
    rail_label: str = Field(description="the rail label as printed, e.g. in the caption")
    band_label: str = Field(description="the wavelength band as printed in the caption")
    direction: str = Field(description="travel direction / route as printed in the document")
    position_panel: PositionPanel
    spectrum_panel: SpectrumPanel


class Extraction(BaseModel):
    document_title: str = Field(description="the title lines of page 1, verbatim")
    n_pages: int
    n_figures: int
    figures: list[Figure]
    unlabelled_elements: list[str] = Field(description="plotted elements that no legend or label in the PDF identifies; describe, do not identify")
    summary: list[str] = Field(description="at most 6 plain sentences, limited to what the figures show")


SYSTEM = """You read a public Transport for London FOI disclosure (PDF, 2 pages, 4 figures) and transcribe its figures
into the JSON schema. Rules:
- Report only what is printed or plotted. Text fields (captions, header line, axis titles, tick labels) are verbatim.
- Each figure image has a header line of text at its top, an upper panel (a trace against position) and a lower panel
  (a spectrum against wavelength). Axis min/max are the numeric values of the extreme printed tick labels.
- Read numbers against the gridlines and tick marks, interpolating between them; give positions to the nearest metre
  and spectrum levels to 0.1 dB. Give a band level for every printed wavelength tick label.
- Do not identify lines, markers or limits that no legend or label in the PDF names: list them in
  unlabelled_elements as descriptions (colour, shape, where), and do not guess what standard or limit they are.
- The summary is at most 6 sentences about what the figures show (where the trace is highest, how the rails and
  bands compare, the shape of the spectra). No causes, no noise claims, no statement about the track today."""
PROMPT = "Transcribe all four figures of this PDF into the schema."


# ---------------------------------------------------------------- helpers
def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").replace("�", "-")
    s = re.sub(r"[‐-―−]", "-", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def tight(s: str) -> str:
    return norm(s).replace(" ", "").replace("μ", "u").replace("µ", "u")


def pdf_facts() -> dict:
    with pdfplumber.open(PDF) as pdf:
        pages = [{"page": i, "text": p.extract_text() or "", "n_images": len(p.images)} for i, p in enumerate(pdf.pages, 1)]
    caps = {int(m.group(1)): p["page"] for p in pages for m in re.finditer(r"Figure (\d+)", p["text"])}
    return {"n_pages": len(pages), "pages": pages, "caption_pages": caps, "n_images": sum(p["n_images"] for p in pages)}


def figure_images() -> dict[int, np.ndarray]:
    """The embedded figure images at native resolution, in page order (figure n = n-th image)."""
    out, n = {}, 0
    doc = pdfium.PdfDocument(str(PDF))
    for i in range(len(doc)):
        for obj in doc[i].get_objects():
            if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                n += 1
                out[n] = np.asarray(obj.get_bitmap(render=False).to_pil().convert("RGB")).astype(int)
    return out


def tfl_anchor() -> Optional[dict]:
    """TfL's own words on this site, parsed (not typed) from the FOI response text in the corpus, read-only: the stretch
    it calls elevated and the grinding it planned. None if the chunk or the sentence is missing."""
    try:
        with CORPUS.open(encoding="utf-8") as fh:
            text = next((json.loads(ln)["text"] for ln in fh if f'"_id": "{ANCHOR_CHUNK}"' in ln), None)
    except OSError:
        return None
    t = re.sub(r"\s+", " ", text or "")
    m = re.search(r"The rail roughness measurements confirm an elevated rail roughness levels between (\d+) and (\d+) ?m\b[^.]*\.", t)
    g = re.search(r"We will undertake rail grinding at this location in ([A-Z][a-z]+ \d{4})[^.]*\.", t)
    if not m:
        return None
    return {"chunk": ANCHOR_CHUNK, "zone_m": [float(m.group(1)), float(m.group(2))], "zone_sentence": m.group(0),
            "grinding_planned": g.group(1) if g else None, "grinding_sentence": g.group(0) if g else None,
            "note": "the causal claim ('likely cause of the noise') is TfL's; we did not link roughness to noise"}


def env_window(d: dict, x: float, half: float = READ_WINDOW_M) -> Optional[float]:
    """Maximum of the pixel trace envelope within +-half m of x (None if the trace has no column there)."""
    w = np.abs(d["_cx"] - x) <= half
    return round(float(d["_ytop"][w].max()), 1) if w.any() else None


def stretches(d: dict, thr: float, gap: float = 10.0, minlen: float = 100.0) -> list[list[int]]:
    """Stretches >= minlen m where the 10 m rolling median of the envelope stays above 2 x the printed threshold
    (gaps <= gap m bridged). Model-free: where the trace jumps up and stays up."""
    cx, y = d["_cx"], d["_ytop"]
    med = np.array([np.median(y[(cx >= x - 5) & (cx <= x + 5)]) for x in cx])
    out: list[list[float]] = []
    for x in cx[med > 2 * thr]:
        if out and x - out[-1][1] <= gap:
            out[-1][1] = x
        else:
            out.append([x, x])
    return [[round(a), round(b)] for a, b in out if b - a >= minlen]


# ---------------------------------------------------------------- model-free pixel digitizer
def _centres(mask_1d: np.ndarray, offset: int) -> list[float]:
    idx, groups = np.where(mask_1d)[0], []
    for i in idx:
        if groups and i == groups[-1][-1] + 1:
            groups[-1].append(i)
        else:
            groups.append([i])
    return [float(np.mean(g)) + offset for g in groups]


def _calib(px: list[float], vals: list[float]) -> dict:
    """Linear fit on the interior ticks; the edge ticks (data extremes / rounded labels) are a check."""
    if len(px) != len(vals):
        raise ValueError(f"found {len(px)} tick marks for {len(vals)} printed labels")
    a, b = np.polyfit(px[1:-1], vals[1:-1], 1)
    pred = a * np.asarray(px) + b
    res = (pred - np.asarray(vals)) / a           # residuals in pixels
    return {"a": float(a), "b": float(b), "interior_max_px": round(float(np.abs(res[1:-1]).max()), 2),
            "edge_px": [round(float(res[0]), 2), round(float(res[-1]), 2)]}


def digitize(img: np.ndarray, fig: int) -> dict:
    t, L = TICKS[fig], LAY
    dark = img.max(axis=2) < 100
    c0, c1 = L["pos_ytick_cols"]; r0, r1, b0, b1 = L["pos_box"]
    ycal = _calib(_centres(dark[r0:r1, c0:c1].sum(1) >= (c1 - c0 - 1), r0), t["pos_y"])
    xr0, xr1 = L["pos_xtick_rows"]
    xcal = _calib(_centres(dark[xr0:xr1, b0 - 2:b1 + 2].sum(0) >= (xr1 - xr0 - 1), b0 - 2), t["pos_x"])
    red = (img == TRACE).all(axis=2)
    box = red[r0:r1, b0:b1]
    cols = np.where(box.any(0))[0]
    top = np.array([np.where(box[:, c])[0].min() for c in cols]) + r0
    bot = np.array([np.where(box[:, c])[0].max() for c in cols]) + r0
    cx = xcal["a"] * (cols + b0) + xcal["b"]
    ytop, ybot = ycal["a"] * top + ycal["b"], ycal["a"] * bot + ycal["b"]
    ties = _centres(top == top.min(), 0)          # separate spikes reaching the same top pixel row
    gmax = {"x": round(float(np.interp(ties[0], np.arange(len(cx)), cx)), 1), "y": round(float(ytop.max()), 1),
            "tied_x": [round(float(np.interp(t_, np.arange(len(cx)), cx)), 1) for t_ in ties]}
    # spectrum
    c0, c1 = L["spec_ytick_cols"]; s0, s1, sb0, sb1 = L["spec_box"]
    sycal = _calib(_centres(dark[s0:s1, c0:c1].sum(1) >= (c1 - c0 - 1), s0), t["spec_y"])
    xr0, xr1 = L["spec_xtick_rows"]
    bandpx = _centres(dark[xr0:xr1, sb0:sb1].sum(0) >= (xr1 - xr0 - 1), sb0)
    if len(bandpx) != len(BANDS):
        raise ValueError(f"found {len(bandpx)} band ticks for {len(BANDS)} printed bands")
    sred = (img[s0:s1, sb0:sb1] == TRACE).all(axis=2)
    levels, overdrawn = {}, []
    for lab, p in zip(BANDS, bandpx):
        c = int(round(p)) - sb0
        rows = np.where(sred[:, c - 1:c + 2].any(1))[0]
        if len(rows) >= 3:
            row = float(np.median(rows))
        else:   # marker hidden under another line: interpolate the dark-red line from 2-4 px either side
            sides = [np.where(sred[:, cc])[0] for cc in (c - 4, c - 3, c - 2, c + 2, c + 3, c + 4)]
            l, r = [np.median(x) for x in sides[:3] if len(x)], [np.median(x) for x in sides[3:] if len(x)]
            row = float((np.mean(l) + np.mean(r)) / 2) if l and r else None
            overdrawn.append(lab)
        levels[lab] = None if row is None else round(float(sycal["a"] * (row + s0) + sycal["b"]), 1)
    pos_px, spec_px = img[r0:r1, b0:b1].reshape(-1, 3), img[s0:s1, sb0:sb1].reshape(-1, 3)
    colours = {name: {"position": int((pos_px == c).all(1).sum()), "spectrum": int((spec_px == c).all(1).sum())}
               for c, (name, _) in LINE_COLOURS.items()}
    return {"line_colour_pixels": colours, "pos_x_calib": xcal, "pos_y_calib": ycal, "spec_y_calib": sycal, "global_max": gmax,
            "n_columns": int(len(cols)), "x_range": [t["pos_x"][0], t["pos_x"][-1]],
            "_cx": cx, "_ytop": ytop, "_ybot": ybot, "band_levels": levels, "bands_interpolated_overdrawn": overdrawn}


def exceedance_bounds(d: dict, thr: float) -> list[float]:
    """% of trace columns entirely above thr (lower bound) and with any part above thr (upper bound)."""
    return [round(100 * float((d["_ybot"] > thr).mean()), 1), round(100 * float((d["_ytop"] > thr).mean()), 1)]


def local_peaks(levels: dict, min_db: float = 1.0) -> list[dict]:
    """Bands whose level exceeds both neighbours by >= min_db (end bands excluded)."""
    labs = [b for b in BANDS if levels.get(b) is not None]
    return [{"band_mm": labs[i], "db": levels[labs[i]],
             "prominence_db": round(min(levels[labs[i]] - levels[labs[i - 1]], levels[labs[i]] - levels[labs[i + 1]]), 1)}
            for i in range(1, len(labs) - 1)
            if levels[labs[i]] - levels[labs[i - 1]] >= min_db and levels[labs[i]] - levels[labs[i + 1]] >= min_db]


# ---------------------------------------------------------------- Gemini
class ExtractionError(RuntimeError):
    """A Gemini call that returned nothing usable (empty, truncated, blocked, or off-schema)."""


def extract(n: int, mode: str) -> dict:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=900_000))
    parts = [types.Part.from_bytes(data=PDF.read_bytes(), mime_type="application/pdf",
                                   media_resolution=types.PartMediaResolutionLevel.MEDIA_RESOLUTION_HIGH),
             types.Part.from_text(text=PROMPT)]
    cfg = types.GenerateContentConfig(system_instruction=SYSTEM, response_mime_type="application/json",
                                      response_schema=Extraction, max_output_tokens=32_768,
                                      thinking_config=types.ThinkingConfig(thinking_level=THINKING))
    t = time.time()
    r = client.models.generate_content(model=MODEL, contents=[types.Content(role="user", parts=parts)], config=cfg)
    fin = r.candidates[0].finish_reason if r.candidates else None
    fin = getattr(fin, "name", str(fin))
    if not r.text:
        raise ExtractionError(f"run {n}: empty response (finish_reason {fin})")
    if fin != "STOP":
        raise ExtractionError(f"run {n}: finish_reason {fin} (output cut off or blocked; max_output_tokens {cfg.max_output_tokens})")
    try:
        ex = Extraction.model_validate_json(r.text).model_dump()
    except ValueError as e:   # pydantic.ValidationError is a ValueError
        raise ExtractionError(f"run {n}: response is not valid against the schema: {str(e)[:300]}") from e
    u = r.usage_metadata
    rec = {"run": n, "mode": mode, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": MODEL,
           "model_version": r.model_version, "finish_reason": fin, "thinking": THINKING, "media_resolution": "high",
           "seconds": round(time.time() - t, 1), "usage": {"prompt": u.prompt_token_count, "output": u.candidates_token_count,
                                                          "thoughts": u.thoughts_token_count}, "extraction": ex}
    return rec


# ---------------------------------------------------------------- deterministic checks
def _span(ax: dict) -> float:
    return abs(ax["max"] - ax["min"]) or 1.0


def _inr(v: float, ax: dict, eps: float = 1e-9) -> bool:
    lo, hi = sorted((ax["min"], ax["max"]))
    return lo - eps <= v <= hi + eps


def check_readings(ex: dict, dig: dict) -> list[dict]:
    """Every global/local maximum Argon reports, against the pixels. Position: the trace envelope's maximum within
    +-READ_WINDOW_M of Argon's x must equal Argon's y within TOL_AGREE x the trace span. Spectrum: at a printed band, the
    pixel marker level must equal Argon's y within TOL_AGREE x the dB span. ok=None: not checkable (x not a printed band)."""
    rows = []
    for g in ex["figures"]:
        n = g["figure_no"]
        if n not in dig:
            continue
        _, sy, ss = SPANS[n]
        for r in g["position_panel"]["readings"]:
            if r["kind"] not in ("global_max", "local_max"):
                continue
            env = env_window(dig[n], r["x"])
            err = None if env is None else round(abs(r["y"] - env), 1)
            rows.append({"figure": n, "panel": "position", "kind": r["kind"], "x": r["x"], "x_units": "m", "argon_y": r["y"], "y_units": "µm",
                         "pixel": env, "where": f"within ±{READ_WINDOW_M:.0f} m of x", "abs_err": err, "tolerance": round(TOL_AGREE * sy, 1),
                         "ok": None if env is None else bool(err <= TOL_AGREE * sy)})
        for r in g["spectrum_panel"]["readings"]:
            if r["kind"] not in ("global_max", "local_max"):
                continue
            band = next((b for b in BANDS if abs(float(b) - r["x"]) < 1e-6), None)
            dv = dig[n]["band_levels"].get(band) if band else None
            err = None if dv is None else round(abs(r["y"] - dv), 1)
            rows.append({"figure": n, "panel": "spectrum", "kind": r["kind"], "x": r["x"], "x_units": "mm", "argon_y": r["y"], "y_units": "dB",
                         "pixel": dv, "where": "at that band", "abs_err": err, "tolerance": round(TOL_AGREE * ss, 1),
                         "ok": None if dv is None else bool(err <= TOL_AGREE * ss)})
    return rows


_DASH = r"[-–�]"
_PEAK = re.compile(r"~?(\d+(?:\.\d+)?)\s*[µμ�u]m\s+at\s+~?(\d+(?:\.\d+)?)\s*m\b")
_LEVEL = re.compile(r"~?(-?\d+(?:\.\d+)?)\s*dB\s+at\s+~?(\d+(?:\.\d+)?)\s*mm\b")
_HIGHER = re.compile(rf"\b(RHR|LHR)(?: curve)? (?:is|are) ~?(\d+(?:\.\d+)?)\s*{_DASH}\s*(\d+(?:\.\d+)?)\s*dB higher than (?:the )?(RHR|LHR)\b")
_RANGE = re.compile(r"from (\d+(?:\.\d+)?)\s*mm (?:down )?to (\d+(?:\.\d+)?)\s*mm")
RAIL_FIGS = {"LHR": {1, 2}, "RHR": {3, 4}}


def _ctx_update(ctx: dict, before: str) -> None:
    """Fold the last 'Figure N' / rail / band mentioned in `before` into the running context of a sentence."""
    fm = list(re.finditer(rf"Fig(?:ure)?s?\.?\s*(\d(?:\s*(?:,|and|&|{_DASH})\s*\d)*)", before))
    if fm:
        ctx["fig"] = {int(x) for x in re.findall(r"\d", fm[-1].group(1))}
    rm = list(re.finditer(r"\b(LHR|RHR)\b", before))
    if rm:
        ctx["rail"] = RAIL_FIGS[rm[-1].group(1)]
    bm = list(re.finditer(rf"\b(30\s*{_DASH}\s*100|100\s*{_DASH}\s*300)\s*mm", before))
    if bm:
        ctx["band"] = {1, 3} if bm[-1].group(1).startswith("30") else {2, 4}


def _figs(ctx: dict, band_after: Optional[set[int]]) -> set[int]:
    c = {1, 2, 3, 4}
    for k, v in ctx.items():
        c &= (band_after if k == "band" and band_after else v) or {1, 2, 3, 4}
    return c or {1, 2, 3, 4}


def pixel_claims(s: str, dig: dict) -> list[dict]:
    """The claims in one summary sentence that the pixels can test (anything else stays unchecked):
    'V µm at X m'  trace envelope within ±10 m of X; the figure comes from the words around the claim (_figs_for);
    'peak of V dB at B mm'  pixel band level within TOL_TIGHT x span, either rail (the rail is not attributed);
    'RHR is a-b dB higher than LHR ... from X mm to Y mm'  pixel level difference over those bands, ±0.5 dB.
    ok=False: the pixels contradict it. ok=None: not checkable. Number-only agreement with Argon's own extraction proves nothing."""
    out, prev, ctx = [], 0, {"fig": None, "rail": None, "band": None}
    found = sorted([*_PEAK.finditer(s), *_LEVEL.finditer(s), *_HIGHER.finditer(s)], key=lambda m: m.start())
    for m in found:
        gap = s[prev:m.start()]
        if m.re is _PEAK:
            _ctx_update(ctx, gap)
            ba = re.match(rf"\s*(?:\([^)]*\)\s*)?in (?:the )?(30\s*{_DASH}\s*100|100\s*{_DASH}\s*300)\s*mm", s[m.end():m.end() + 60])
            figs = _figs(ctx, ({1, 3} if ba.group(1).startswith("30") else {2, 4}) if ba else None) & set(dig)
            end_ = m.end() + (ba.end() if ba else 0)
            if ba:
                ctx["band"] = None            # a band written after a claim belongs to that claim only
            v, x = float(m.group(1)), float(m.group(2))
            env = {n: env_window(dig[n], x) for n in sorted(figs)}
            ok = any(e is not None and abs(e - v) <= TOL_AGREE * SPANS[n][1] for n, e in env.items())
            out.append({"claim": m.group(0), "figures": sorted(figs), "ok": ok,
                        "pixel": " / ".join(f"fig {n}: {e} µm within ±{READ_WINDOW_M:.0f} m" for n, e in env.items())})
        elif m.re is _LEVEL:
            peaky = re.search(r"(?:peaks?|maxim\w+|reach\w*|level)\b[^.;()]{0,25}$", s[:m.start()]) or (
                out and out[-1].get("kind") == "level" and re.fullmatch(r"(?:,|,? and|;)\s*", gap))
            b = next((b for b in BANDS if abs(float(b) - float(m.group(2))) < 1e-6), None)
            if not peaky or b is None:
                continue                      # a difference ('by 5-6.5 dB at 12.5 mm'), not a level: not tested
            v = float(m.group(1))
            lv = {n: dig[n]["band_levels"].get(b) for n in (1, 3)}
            ok = any(e is not None and abs(e - v) <= TOL_TIGHT * SPANS[n][2] for n, e in lv.items())
            out.append({"claim": m.group(0), "kind": "level", "figures": [1, 3], "ok": ok,
                        "pixel": " / ".join(f"{'LHR' if n == 1 else 'RHR'}: {e} dB" for n, e in lv.items())})
        else:
            who, a, b_, other = m.group(1), float(m.group(2)), float(m.group(3)), m.group(4)
            rg = _RANGE.search(s, m.end())
            if rg is None or who == other:
                out.append({"claim": m.group(0), "figures": [], "ok": None, "pixel": "no 'from X mm to Y mm' range found"})
                prev = m.end()
                continue
            hi_mm, lo_mm = max(float(rg.group(1)), float(rg.group(2))), min(float(rg.group(1)), float(rg.group(2)))
            fw, fo = min(RAIL_FIGS[who]), min(RAIL_FIGS[other])
            diffs = [dig[fw]["band_levels"][bd] - dig[fo]["band_levels"][bd] for bd in BANDS
                     if lo_mm <= float(bd) <= hi_mm and dig[fw]["band_levels"].get(bd) is not None and dig[fo]["band_levels"].get(bd) is not None]
            ok = all(a - 0.5 <= d <= b_ + 0.5 for d in diffs) if diffs else None
            out.append({"claim": f"{m.group(0)} {rg.group(0)}", "figures": [fw, fo], "ok": ok,
                        "pixel": f"{who} - {other} over {len(diffs)} bands: {min(diffs):+.1f} to {max(diffs):+.1f} dB" if diffs else "no bands"})
        prev = end_ if m.re is _PEAK else m.end()
    return out


def check_unlabelled(elements: list[str], dig: dict) -> tuple[list[str], list[dict]]:
    """Each unlabelled line colour found in the pixels of a panel must be named, by a colour word, in an element that is
    about THAT panel. The vertical lines on the position panels must not be called red/green (the pixels hold only pink)."""
    fails, rows = [], []
    panels = []
    for e in elements:
        pos, spec = re.search(r"\b(upper|position)\b", e, re.I), re.search(r"\b(lower|spectrum)\b", e, re.I)
        panels.append("position" if pos and not spec else "spectrum" if spec and not pos else "both")
    for c, (nm, words) in LINE_COLOURS.items():
        for pn in ("position", "spectrum"):
            if sum(d["line_colour_pixels"][nm][pn] for d in dig.values()) <= 200:
                continue
            ok = any(p in (pn, "both") and any(w in e.lower() for w in words) for e, p in zip(elements, panels))
            rows.append({"colour": nm, "rgb": list(c), "panel": pn, "named_in_an_element_about_that_panel": ok})
            if not ok:
                fails.append(f"unlabelled elements: the {nm} line {c} on the {pn} panel is not named by its colour in an element about that panel")
    for e, p in zip(elements, panels):
        if p == "position" and "vertical" in e.lower():
            bad = sorted(set(re.findall(r"(?<!light )(?<!pale )\b(red|green|blue|yellow|orange)\b", e.lower())))
            rows.append({"element": e[:200], "panel": p, "wrong_colour_words": bad})
            if bad:
                fails.append(f"unlabelled elements: vertical lines on the position panel are called {'/'.join(bad)} but the pixels hold only pink (255, 204, 204)")
    return fails, rows


def check_run(ex: dict, facts: dict, dig: dict, anchor: Optional[dict] = None) -> dict:
    fails, notes, rows, zones, sentences = [], [], [], [], []
    f = lambda cond, msg: None if cond else fails.append(msg)  # noqa: E731
    figs = ex["figures"]
    # counts
    f(ex["n_pages"] == facts["n_pages"], f"n_pages {ex['n_pages']} != {facts['n_pages']}")
    f(ex["n_figures"] == len(figs) == len(facts["caption_pages"]) == facts["n_images"],
      f"figures: declared {ex['n_figures']}, returned {len(figs)}, captions {len(facts['caption_pages'])}, images {facts['n_images']}")
    f(sorted(g["figure_no"] for g in figs) == sorted(facts["caption_pages"]), "figure numbers do not match the captions")
    ptext = {p["page"]: norm(p["text"]) for p in facts["pages"]}
    f(all(norm(line) in ptext[1] for line in ex["document_title"].splitlines() if line.strip()),
      "document_title is not verbatim page-1 text")
    for g in figs:
        n, pp, sp = g["figure_no"], g["position_panel"], g["spectrum_panel"]
        cap = norm(g["figure_caption"])
        f(facts["caption_pages"].get(n) == g["page"], f"fig {n}: page {g['page']} but caption is on p.{facts['caption_pages'].get(n)}")
        f(bool(cap) and cap in ptext.get(g["page"], ""), f"fig {n}: caption not verbatim in page text: {g['figure_caption']!r}")
        f(re.search(rf"\bfigure {n}\b", cap) is not None, f"fig {n}: caption names another figure")
        f(norm(g["rail_label"]) in cap, f"fig {n}: rail label {g['rail_label']!r} not in its caption")
        f(tight(g["band_label"]) in tight(cap), f"fig {n}: band label {g['band_label']!r} not in its caption")
        f(tight(g["band_label"]) in tight(pp["y_axis"]["label"]), f"fig {n}: position y title does not name the caption band")
        # axis labels: text layer, then the transcribed titles
        for pname, panel, keys in (("pos", pp, ("x", "y")), ("spec", sp, ("x", "y"))):
            for k in keys:
                ax = panel[f"{k}_axis"]
                in_text = norm(ax["label"]) in ptext.get(g["page"], "")
                want = AXIS_TITLES[f"{pname}_{k}"].format(band=g["band_label"].replace(" ", ""))
                rows.append({"figure": n, "axis": f"{pname}_{k}", "label": ax["label"], "in_text_layer": in_text,
                             "matches_printed": tight(ax["label"]) == tight(want)})
                if pname + "_" + k in ("pos_x", "pos_y", "spec_y"):
                    lab = TICKS[n][f"{pname}_{k}"]
                    f(abs(min(ax["min"], ax["max"]) - min(lab)) < 1e-6 and abs(max(ax["min"], ax["max"]) - max(lab)) < 1e-6,
                      f"fig {n} {pname}_{k}: range [{ax['min']}, {ax['max']}] != printed [{min(lab)}, {max(lab)}]")
                else:
                    f({ax["min"], ax["max"]} == {3.15, 2000.0}, f"fig {n} spec_x: range [{ax['min']}, {ax['max']}] != printed [3.15, 2000]")
        # ranges
        for r in pp["readings"]:
            f(_inr(r["x"], pp["x_axis"]) and _inr(r["y"], pp["y_axis"]), f"fig {n}: position reading {r} outside its axes")
        for z in pp["rough_zones"]:
            f(_inr(z["start"], pp["x_axis"]) and _inr(z["end"], pp["x_axis"]) and z["start"] < z["end"], f"fig {n}: zone {z} invalid")
        if pp["threshold_value"] is not None:
            f(_inr(pp["threshold_value"], pp["y_axis"]), f"fig {n}: threshold outside the y axis")
        for r in sp["readings"]:
            f(_inr(r["x"], sp["x_axis"]) and _inr(r["y"], sp["y_axis"]), f"fig {n}: spectrum reading {r} outside its axes")
        got = {b["band_mm"]: b["db"] for b in sp["band_levels"]}
        f(set(got) == set(BANDS), f"fig {n}: band labels differ from the printed ticks: missing {sorted(set(BANDS) - set(got))}, "
                                  f"extra {sorted(set(got) - set(BANDS))}")
        f(all(_inr(v, sp["y_axis"]) for v in got.values()), f"fig {n}: a band level is outside the y axis")
        # arithmetic + header consistency
        hv = g["header_values"]
        length_km = (TICKS[n]["pos_x"][-1] - TICKS[n]["pos_x"][0]) / 1000
        f(abs(hv["exceedance_pct"] / 100 * length_km - hv["exceedance_km"]) <= 0.005 * length_km + 0.0005,
          f"fig {n}: {hv['exceedance_pct']}% x {length_km:.4f} km != {hv['exceedance_km']} km")
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", g["header_text"])]
        f(all(v in nums for v in hv.values()), f"fig {n}: header_values not all in header_text")
        if pp["threshold_value"] is not None:
            f(abs(pp["threshold_value"] - hv["threshold_um"]) <= TOL_TIGHT * _span(pp["y_axis"]),
              f"fig {n}: dashed line {pp['threshold_value']} vs header threshold {hv['threshold_um']}")
        # against the digitizer (model-free)
        d = dig[n]
        lo, hi = exceedance_bounds(d, hv["threshold_um"])
        f(lo - 1 <= hv["exceedance_pct"] <= hi + 1, f"fig {n}: header exceedance {hv['exceedance_pct']}% outside the plotted trace's [{lo}, {hi}]%")
        for z in pp["rough_zones"]:
            zr = zone_rule(d, z, hv["threshold_um"]) | {"figure": n}
            zones.append(zr)
            f(zr["ok"], f"fig {n}: zone {z['start']:.0f}-{z['end']:.0f} m not confirmed by the zone rule "
                        f"(envelope median {zr['median_in']} inside vs {zr['median_out']} outside, threshold {hv['threshold_um']})")
    # every max Argon reports against the pixels (run-vs-run agreement cannot catch errors both runs make)
    readings = check_readings(ex, dig)
    for r in readings:
        f(r["ok"] is not False, f"fig {r['figure']}: {r['kind']} reading {r['argon_y']} {r['y_units']} at {r['x']:g} {r['x_units']} fails the pixel check: "
                                f"pixels give {r['pixel']} {r['y_units']} {r['where']} (|Δ| {r['abs_err']} > {r['tolerance']})")
    # TfL's own stated stretch must be confirmed (zone rule) in every figure, by this run's zones
    anchor_rows = []
    if anchor:
        lo, hi = anchor["zone_m"]
        for n in sorted(dig):
            hit = [z for z in zones if z["figure"] == n and z["ok"] and z["end"] > lo and z["start"] < hi]
            anchor_rows.append({"figure": n, "overlapping_zones": [[z["start"], z["end"]] for z in hit], "ok": bool(hit)})
            f(bool(hit), f"fig {n}: no zone passing the zone rule overlaps TfL's stated {lo:.0f}-{hi:.0f} m")
    # summary: never 'verified'. rejected = banned phrasing / identifies a line / a claim the pixels contradict. A number that
    # is not in Argon's own extraction is recorded (consistency only, circular) but does not by itself reject
    supp = _supported_numbers(ex)
    for s in ex["summary"]:
        why = [f"banned: {w}" for pat, w in V.BANNED if re.search(pat, s, re.I)]
        if re.search(r"\b(ISO|3095|TSI|EN ?15610|intervention|limit|acoustic grinding)\b", s, re.I) and not re.search(r"unlabel|not identif|no legend", s, re.I):
            why.append("identifies an unlabelled element")
        stray = []
        for m in re.finditer(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", s):
            v, dec = float(m.group().replace(",", "")), len(m.group().split(".")[1]) if "." in m.group() else 0
            if not any(abs(v - w) <= max(0.5 * 10 ** -dec, 0.02 * abs(w)) for w in supp):
                stray.append(m.group())
        claims = pixel_claims(s, dig)
        why += [f"contradicted by the pixels: {c['claim']} ({c['pixel']})" for c in claims if c["ok"] is False]
        sentences.append({"sentence": s, "status": "rejected" if why else "unverified", "reasons": why,
                          "consistent_with_extraction": not stray, "numbers_not_in_extraction": stray, "pixel_claims": claims})
        f(not why, f"summary sentence {len(sentences)} rejected: {'; '.join(why)}")
    # unlabelled elements: each line colour present in the pixels must be named, per panel
    ufails, urows = check_unlabelled(ex["unlabelled_elements"], dig)
    for m_ in ufails:
        f(False, m_)
    notes.append(f"axis titles in the PDF text layer: {sum(r['in_text_layer'] for r in rows)}/{len(rows)} "
                 "(the axes are raster inside the figure images; the text layer holds the title and captions only)")
    f(all(r["matches_printed"] for r in rows), "axis titles differ from the printed titles: " +
      "; ".join(f"fig {r['figure']} {r['axis']} {r['label']!r}" for r in rows if not r["matches_printed"]))
    return {"ok": not fails, "failures": fails, "notes": notes, "axis_labels": rows, "zones": zones, "summary": sentences,
            "readings": readings, "tfl_anchor": anchor_rows, "unlabelled": urows}

def zone_rule(d: dict, z: dict, thr: float) -> dict:
    """A rough zone is confirmed when the trace envelope's median inside it is above the threshold and at least twice
    the envelope median outside it (rule fixed before the first run)."""
    inside = (d["_cx"] >= z["start"]) & (d["_cx"] <= z["end"])
    if inside.sum() < 5:
        return {"start": z["start"], "end": z["end"], "ok": False, "median_in": None, "median_out": None}
    mi, mo = float(np.median(d["_ytop"][inside])), float(np.median(d["_ytop"][~inside]))
    return {"start": z["start"], "end": z["end"], "ok": bool(mi > thr and mi >= 2 * mo), "median_in": round(mi, 1),
            "median_out": round(mo, 1)}


def _supported_numbers(ex: dict) -> list[float]:
    vals = [2018.0, ex["n_pages"], ex["n_figures"]]
    for g in ex["figures"]:
        pp, sp = g["position_panel"], g["spectrum_panel"]
        vals += [g["figure_no"], g["page"], *g["header_values"].values(), *[float(x) for x in re.findall(r"\d+(?:\.\d+)?", g["figure_caption"])]]
        for ax in (pp["x_axis"], pp["y_axis"], sp["x_axis"], sp["y_axis"]):
            vals += [ax["min"], ax["max"]]
        vals += [pp["x_axis"]["max"] / 1000, (pp["x_axis"]["max"] - pp["x_axis"]["min"]) / 1000]   # length in km
        vals += [v for r in pp["readings"] + sp["readings"] for v in (r["x"], r["y"])]
        vals += [v for z in pp["rough_zones"] for v in (z["start"], z["end"], z["end"] - z["start"])]
        vals += [b["db"] for b in sp["band_levels"]] + [float(b) for b in BANDS]
    return [float(v) for v in vals if v is not None]


def gmax(g: dict) -> Optional[dict]:
    r = [x for x in g["position_panel"]["readings"] if x["kind"] == "global_max"]
    return r[0] if r else None


def _merge(zones: list[dict], gap: float) -> list[dict]:
    """Join zones that touch or overlap (gap <= 1 % of the span), so a zone split in two is compared as one."""
    out = []
    for z in sorted(zones, key=lambda z: z["start"]):
        if out and z["start"] - out[-1]["end"] <= gap:
            out[-1] = {**out[-1], "end": max(out[-1]["end"], z["end"])}
        else:
            out.append(dict(z))
    return out


COPY_OF = {2: 1, 4: 3}      # the lower panel of Fig 2 / Fig 4 is the same image as Fig 1 / Fig 3 (checked pixel by pixel)


def spec_figs(pix: dict) -> list[int]:
    """Figures whose spectrum is a DISTINCT image: a pixel-identical copy is not counted again."""
    return [n for n in (1, 2, 3, 4) if not (n in COPY_OF and pix.get(f"{COPY_OF[n]}v{n}"))]


def _st(v: list[float]) -> Optional[dict]:
    return {"n": len(v), "median": round(float(np.median(v)), 2), "max": round(float(max(v)), 2)} if v else None


def _pair_zones(za: list[dict], zb: list[dict]) -> tuple[list[tuple], list[int], list[int]]:
    """Symmetric zone matching: every zone's best-overlap partner on the other side; pairs from both directions are united,
    zones with no overlapping partner are returned unmatched (so compare(a, b) and compare(b, a) give the same rows)."""
    def ov(z: dict, y: dict) -> float:
        return min(z["end"], y["end"]) - max(z["start"], y["start"])
    pairs = set()
    for i, z in enumerate(za):
        c = [(ov(z, y), j) for j, y in enumerate(zb) if ov(z, y) > 0]
        if c:
            pairs.add((i, max(c)[1]))
    for j, y in enumerate(zb):
        c = [(ov(z, y), i) for i, z in enumerate(za) if ov(z, y) > 0]
        if c:
            pairs.add((max(c)[1], j))
    return (sorted(pairs), [i for i in range(len(za)) if i not in {p[0] for p in pairs}],
            [j for j in range(len(zb)) if j not in {p[1] for p in pairs}])


def compare(a: dict, b: dict, spans: dict, pix: dict) -> dict:
    """Two extractions, item by item, each distinct item once. Text exact (after case/space/dash/µ normalisation); numbers
    within TOL_AGREE / TOL_TIGHT of the axis span, and as absolute errors (m, µm, dB). Spectrum bands are taken from the
    distinct spectrum images only (a pixel-identical copy in Fig 2 / Fig 4 is not a second reading)."""
    rows = []
    A, B = ({g["figure_no"]: g for g in e["figures"]} for e in (a, b))
    sf = spec_figs(pix)
    for n in sorted(set(A) & set(B)):
        ga, gb, sx, sy, ss = A[n], B[n], *spans[n]
        rows.append({"figure": n, "item": "header_text", "a": ga["header_text"], "b": gb["header_text"],
                     "agree": tight(ga["header_text"]) == tight(gb["header_text"])})
        rows.append({"figure": n, "item": "caption", "a": ga["figure_caption"], "b": gb["figure_caption"],
                     "agree": norm(ga["figure_caption"]) == norm(gb["figure_caption"])})
        for key in ("main_av_pp_um", "exceedance_pct", "exceedance_km", "threshold_um"):
            rows.append({"figure": n, "item": f"header.{key}", "a": ga["header_values"][key], "b": gb["header_values"][key],
                         "agree": ga["header_values"][key] == gb["header_values"][key]})
        ma, mb = gmax(ga), gmax(gb)
        if ma and mb:
            dx, dy = abs(ma["x"] - mb["x"]) / sx, abs(ma["y"] - mb["y"]) / sy
            rows.append({"figure": n, "item": "global_max", "a": [ma["x"], ma["y"]], "b": [mb["x"], mb["y"]],
                         "abs_dx_m": round(abs(ma["x"] - mb["x"]), 1), "abs_dy_um": round(abs(ma["y"] - mb["y"]), 1),
                         "dx_span": round(dx, 4), "dy_span": round(dy, 4), "agree": max(dx, dy) <= TOL_AGREE, "tight": max(dx, dy) <= TOL_TIGHT})
        if n in sf:
            la = {x["band_mm"]: x["db"] for x in ga["spectrum_panel"]["band_levels"]}
            lb = {x["band_mm"]: x["db"] for x in gb["spectrum_panel"]["band_levels"]}
            for band in BANDS:
                if band in la and band in lb:
                    d = abs(la[band] - lb[band]) / ss
                    rows.append({"figure": n, "item": f"band {band} mm", "a": la[band], "b": lb[band], "abs_db": round(abs(la[band] - lb[band]), 1),
                                 "d_span": round(d, 4), "agree": d <= TOL_AGREE, "tight": d <= TOL_TIGHT})
        za, zb = (_merge(g_["position_panel"]["rough_zones"], 0.01 * sx) for g_ in (ga, gb))
        pairs, ua, ub = _pair_zones(za, zb)
        for i, j in pairs:
            z, y = za[i], zb[j]
            d = max(abs(z["start"] - y["start"]), abs(z["end"] - y["end"]))
            rows.append({"figure": n, "item": "zone", "a": [z["start"], z["end"]], "b": [y["start"], y["end"]], "boundary_m": round(d, 1),
                         "d_span": round(d / sx, 4), "agree": d / sx <= TOL_AGREE, "tight": d / sx <= TOL_TIGHT})
        rows += [{"figure": n, "item": "zone", "a": [za[i]["start"], za[i]["end"]], "b": None, "agree": False} for i in ua]
        rows += [{"figure": n, "item": "zone", "a": None, "b": [zb[j]["start"], zb[j]["end"]], "agree": False} for j in ub]
    num = [r for r in rows if "tight" in r]
    bands = [r for r in rows if r["item"].startswith("band")]
    zr = [r for r in rows if r["item"] == "zone"]
    stats = {"text_items": {"n": sum(r["item"] in ("header_text", "caption") for r in rows),
                            "agree": sum(r["agree"] for r in rows if r["item"] in ("header_text", "caption"))},
             "header_numbers": {"n": sum(r["item"].startswith("header.") for r in rows),
                                "agree": sum(r["agree"] for r in rows if r["item"].startswith("header."))},
             "global_max": {"dx_m": _st([r["abs_dx_m"] for r in rows if r["item"] == "global_max"]),
                            "dy_um": _st([r["abs_dy_um"] for r in rows if r["item"] == "global_max"])},
             "bands_distinct": {"abs_db": _st([r["abs_db"] for r in bands]), "n_over_1p5_db": sum(r["abs_db"] > 1.5 for r in bands)},
             "zones": {"matched": sum(r["b"] is not None and r["a"] is not None for r in zr), "unmatched": sum(r["a"] is None or r["b"] is None for r in zr),
                       "boundary_m": _st([r["boundary_m"] for r in zr if "boundary_m" in r])}}
    return {"rows": rows, "n": len(rows), "n_agree": sum(r["agree"] for r in rows), "n_numeric": len(num),
            "n_numeric_tight": sum(r["tight"] for r in num), "stats": stats, "disagreements": [r for r in rows if not r["agree"]]}


def vs_digitizer(ex: dict, dig: dict, spans: dict, pix: dict) -> dict:
    """Argon against the pixel digitizer: the global maximum of each figure, and the band levels of each DISTINCT spectrum.
    Absolute errors are the result; 'tight' (<= 2 % of the span, 1.56 dB) is loose against the 1.5-1.8 dB peaks reported.
    A global maximum that fails the +-10 m pixel check of check_readings is neither 'agree' nor 'tight' (pixel_check_ok False)."""
    rows, copies = [], {}
    sf = spec_figs(pix)
    px_ok = {r["figure"]: r["ok"] for r in check_readings(ex, dig) if r["panel"] == "position" and r["kind"] == "global_max"}
    for g in ex["figures"]:
        n = g["figure_no"]
        if n not in dig or n not in spans:
            continue
        sx, sy, ss = spans[n]
        m, dm = gmax(g), dig[n]["global_max"]
        if m:   # any of the spikes tied at the top pixel row counts as the maximum
            ax = min(abs(m["x"] - t) for t in dm["tied_x"])
            d_ = max(ax / sx, abs(m["y"] - dm["y"]) / sy)
            ok_ = px_ok.get(n) is not False      # a maximum that fails check_readings (the +-10 m pixel window) is not 'agree'/'tight' here either
            rows.append({"figure": n, "item": "global_max", "argon": [m["x"], m["y"]], "digitizer": [dm["x"], dm["y"]],
                         "abs_dx_m": round(ax, 1), "abs_dy_um": round(abs(m["y"] - dm["y"]), 1), "pixel_check_ok": px_ok.get(n),
                         "agree": d_ <= TOL_AGREE and ok_, "tight": d_ <= TOL_TIGHT and ok_})
        lv = {b["band_mm"]: b["db"] for b in g["spectrum_panel"]["band_levels"]}
        if n in sf:
            for band, db in lv.items():
                dv = dig[n]["band_levels"].get(band)
                if dv is not None:
                    d = abs(db - dv) / ss
                    rows.append({"figure": n, "item": f"band {band} mm", "argon": db, "digitizer": dv, "abs_db": round(abs(db - dv), 1),
                                 "agree": d <= TOL_AGREE, "tight": d <= TOL_TIGHT})
        elif n in COPY_OF:
            src = {b["band_mm"]: b["db"] for f_ in ex["figures"] if f_["figure_no"] == COPY_OF[n] for b in f_["spectrum_panel"]["band_levels"]}
            diffs = [abs(lv[b] - src[b]) for b in lv if b in src]
            copies[str(n)] = {"copy_of": COPY_OF[n], "max_abs_db_between_copies": round(max(diffs), 1) if diffs else None, "n": len(diffs)}
    bands = [r for r in rows if r["item"].startswith("band")]
    gm = [r for r in rows if r["item"] == "global_max"]
    per_spec = {str(n): _st([r["abs_db"] for r in bands if r["figure"] == n]) for n in sf}
    return {"rows": rows, "n": len(rows), "n_agree": sum(r["agree"] for r in rows), "n_tight": sum(r["tight"] for r in rows),
            "global_max": {"n": len(gm), "dx_m": _st([r["abs_dx_m"] for r in gm]), "dy_um": _st([r["abs_dy_um"] for r in gm])},
            "bands_distinct": {"n": len(bands), "abs_db": _st([r["abs_db"] for r in bands]), "n_over_1p5_db": sum(r["abs_db"] > 1.5 for r in bands),
                               "per_spectrum_fig": per_spec},
            "copies": copies, "disagreements": [r for r in rows if not r["agree"]]}


def vs_reference(ref: dict, ex: Optional[dict], dig: dict, spans: dict) -> list[dict]:
    """Claude's independent read against a run (if given) or the digitizer. A figure the run omits is a disagreement."""
    out = []
    F = {g["figure_no"]: g for g in ex["figures"]} if ex else {}
    for n, h in ref["header_text"].items():
        n = int(n)
        if ex:
            out.append({"ref": f"header fig {n}", "claude": h, "other": F[n]["header_text"] if n in F else None,
                        "agree": n in F and tight(h) == tight(F[n]["header_text"])})
    for p in ref["points"]:
        n, (sx, sy, ss) = p["figure"], spans[p["figure"]]
        g = F.get(n)
        if ex and g is None:
            out.append({"ref": p["id"], "what": p["what"], "claude": [p["x"], p["y"]], "other": None, "d_span": None, "agree": False, "tight": False})
            continue
        if p["panel"] == "position":
            o = gmax(g) if ex else dig[n]["global_max"]
            xs = (o.get("tied_x") or [o["x"]]) if o else []
            o = [min(xs, key=lambda t: abs(t - p["x"])), o["y"]] if o else None
            d = max(abs(p["x"] - o[0]) / sx, abs(p["y"] - o[1]) / sy) if o else None
        else:
            band = next(b for b in BANDS if float(b) == p["x"])
            o = ({b["band_mm"]: b["db"] for b in g["spectrum_panel"]["band_levels"]}.get(band) if ex else dig[n]["band_levels"].get(band))
            d = abs(p["y"] - o) / ss if o is not None else None
        out.append({"ref": p["id"], "what": p["what"], "claude": [p["x"], p["y"]], "other": o,
                    "d_span": None if d is None else round(d, 4), "agree": d is not None and d <= TOL_AGREE,
                    "tight": d is not None and d <= TOL_TIGHT})
    return out


# ---------------------------------------------------------------- reports
def human_check(anchor: Optional[dict] = None) -> str:
    """The write-once checklist. Rows 9-10 cover what the machine could not settle: a reading both runs get wrong, and TfL's stated stretch."""
    lo_hi = f'"between {anchor["zone_m"][0]:.0f} and {anchor["zone_m"][1]:.0f} m"' if anchor else ""
    tfl = ("TfL's response says elevated roughness " + lo_hi) if anchor else "TfL's response states a stretch of elevated roughness"
    return f"""# Human check: TfL 2018 rail roughness plots (FOI-1730-1819)

POST-HOC. Nothing from `roughness.json` goes into the evidence pack until these are confirmed by eye.
Open `outputs_posthoc/roughness/pages/figure<N>.png` (native resolution) or the PDF
(`data/corpus/files/FOI-1730-1819__1.pdf`). Read against the gridlines; write what YOU see, not what the table expects.

| # | Page | Figure | Panel | What to read | Your reading | Agrees with roughness.md? (y/n) |
|---|------|--------|-------|--------------|--------------|------------------------------|
| 1 | 1 | 1 (LHR 30-100mm) | text above the plot | The header line, exactly ("Main Av P-P: ... Exceedence: ...") | | |
| 2 | 2 | 4 (RHR 100-300mm) | text above the plot | The header line, exactly | | |
| 3 | 1 | 1 | upper (Position, m) | Position (m) and height (µm) of the single tallest spike | | |
| 4 | 2 | 4 | upper (Position, m) | Position (m) and height (µm) of the single tallest spike | | |
| 5 | 1 | 1 | upper | Position (m) where the trace jumps up and stays high (start of the rough stretch) | | |
| 6 | 1 | 1 | lower (Wavelength, mm) | dB of the dark-red curve at 125 mm, and at 200 mm (is 125 a bump?) | | |
| 7 | 2 | 3 | lower (Wavelength, mm) | dB of the dark-red curve at 200 mm, and at 250 mm (is 200 a bump?) | | |
| 8 | 1-2 | all | lower | Is there any legend naming the grey, magenta and olive lines? (y/n) | | |
| 9 | 2 | 4 | upper (Position, m) | Height (µm) of the tallest spike between 150 and 260 m, and its position | | |
| 10 | 1-2 | 1 and 3 | upper (Position, m) | Position (m) where the trace rises and stays high, and where it falls back, on each rail ({tfl}: does the rise start near its lower end?) | | |

Checked by: ____________  Date: __________
"""


def consolidate(runs: list[dict], dig: dict, spans: dict) -> dict:
    """What survives everything: zones every run reports (merged, ends within TOL_AGREE of the span) that pass the
    zone rule; spectrum local peaks found by the digitizer and by every run; header lines identical in every run."""
    out = {}
    for n in sorted(dig):
        G = [{g["figure_no"]: g for g in r["extraction"]["figures"]}.get(n) for r in runs]
        if any(g is None for g in G):
            continue
        sx, thr = spans[n][0], G[0]["header_values"]["threshold_um"]
        merged = [_merge(g["position_panel"]["rough_zones"], 0.01 * sx) for g in G]
        zones = []
        for z in merged[0]:
            ys = []
            for m in merged[1:]:
                ov = [(min(z["end"], y["end"]) - max(z["start"], y["start"]), y) for y in m]
                ov = [o for o in ov if o[0] > 0]
                if ov:
                    ys.append(max(ov, key=lambda o: o[0])[1])
            agree = len(ys) == len(merged) - 1 and all(
                max(abs(z["start"] - y["start"]), abs(z["end"] - y["end"])) <= TOL_AGREE * sx for y in ys)
            rule = zone_rule(dig[n], z, thr)
            zones.append({"start_by_run": [z["start"]] + [y["start"] for y in ys], "end_by_run": [z["end"]] + [y["end"] for y in ys],
                          "runs_agree": agree, "zone_rule": rule, "confirmed": bool(agree and rule["ok"])})
        runpk = [{p["band_mm"] for p in local_peaks({b["band_mm"]: b["db"] for b in g["spectrum_panel"]["band_levels"]})} for g in G]
        dpk = local_peaks(dig[n]["band_levels"])
        out[str(n)] = {"header_text": G[0]["header_text"],
                       "header_same_in_all_runs": len({tight(g["header_text"]) for g in G}) == 1,
                       "global_max_by_run": [[gmax(g)["x"], gmax(g)["y"]] if gmax(g) else None for g in G],
                       "global_max_digitizer": dig[n]["global_max"], "zones": zones,
                       "spectrum_peaks_all_agree": [p for p in dpk if all(p["band_mm"] in k for k in runpk)],
                       "spectrum_peaks_digitizer_only": [p["band_mm"] for p in dpk if not all(p["band_mm"] in k for k in runpk)]}
    return out


def _rng(v: list[float]) -> str:
    return f"{min(v):.0f}" if round(min(v)) == round(max(v)) else f"{min(v):.0f}-{max(v):.0f}"


def common_zone(C: dict) -> list[dict]:
    """A confirmed zone of the first figure that overlaps a confirmed zone in every other figure, with its partners ([] if none)."""
    def ov(a: dict, b: dict) -> bool:
        return min(a["end_by_run"]) > max(b["start_by_run"]) and min(b["end_by_run"]) > max(a["start_by_run"])
    if not C:
        return []
    conf = {n: [z for z in C[n]["zones"] if z["confirmed"]] for n in C}
    first = min(C, key=int)
    for z in conf[first]:
        partners = [next((y for y in conf[n] if ov(z, y)), None) for n in C if n != first]
        if all(partners):
            return [z, *partners]
    return []


def plain_words(C: dict, onsets: dict, anchor: Optional[dict]) -> str:
    """One paragraph from the confirmed results only (a zone confirmed in every figure, peaks found by every reader).
    'Elevated' means above the plotted threshold line (>10 / >30 µm per the header), nothing more."""
    common, out = common_zone(C), []
    if common:
        st = [x for z in common for x in z["start_by_run"]]
        en = [x for z in common for x in z["end_by_run"]]
        on = sorted(v for v in onsets.values() if v is not None)
        out.append(f"in January 2018 one stretch of this eastbound section sat above the plotted threshold line (>10 µm in the "
                   f"30-100 mm band, >30 µm in 100-300 mm, per the headers) in all four figures (both rails, both wavelength bands). "
                   f"Argon read it as starting at about {min(st):.0f}-{max(st):.0f} m and ending at about {min(en):.0f}-{max(en):.0f} m "
                   "of TfL's plotted position, depending on rail and band (bounds as read by Argon; the zone rule confirms that the "
                   "trace inside is elevated, it does not check the bounds)"
                   + (f"; the model-free pixel onset, where the 10 m median stays above twice the threshold, is {on[0]}-{on[-1]} m" if on else "") + ".")
        if anchor:
            lo, hi = anchor["zone_m"]
            ok = min(en) > lo and max(st) < hi
            out.append(f"TfL's own response to the same request states elevated rail roughness between {lo:.0f} and {hi:.0f} m; "
                       + ("the stretch above overlaps it." if ok else "the stretch above does NOT overlap it."))
    else:
        out.append("no stretch above the threshold line is confirmed in all four figures.")
    for n, rail in (("1", "LHR"), ("3", "RHR")):
        pk = C.get(n, {}).get("spectrum_peaks_all_agree", [])
        if pk:
            out.append(f"The {rail} spectrum has small local bumps at " + ", ".join(
                f"{p['band_mm']} mm (+{p['prominence_db']} dB)" for p in pk) + " (level above both neighbouring bands).")
    return " ".join(out)


CHECK_ROWS = [("Counts: 2 pages, 4 captions, 4 images, 4 figures; each figure on its caption's page",
               r"n_pages|figures:|figure numbers|caption is on"),
              ("Captions found in pdfplumber page text (after case, whitespace, dash normalisation)", r"caption not verbatim|names another"),
              ("Document title found in page-1 text", r"document_title"),
              ("Rail/band labels in the figure's own caption; position axis names the band", r"rail label|band label|does not name"),
              ("Axis titles and extreme tick labels = printed", r"axis titles differ|range \["),
              ("Every reading / band / zone / threshold inside its axes", r"outside its|invalid|band labels differ|outside the y"),
              ("Header % x length = header km; header numbers in the header text", r"km !=|not all in header|dashed line"),
              ("Header exceedance % inside the plotted trace's bounds (digitizer)", r"outside the plotted"),
              ("Rough zones pass the zone rule (rule fixed before the first run)", r"not confirmed by the zone rule"),
              ("Every global/local maximum Argon reports matches the pixels (±10 m window; spectrum at the band)", r"fails the pixel check"),
              ("A zone passing the zone rule overlaps TfL's stated 720-880 m in every figure", r"overlaps TfL"),
              ("Summary prose not rejected (banned phrasing, identified unlabelled line, claim the pixels contradict). Never 'verified'", r"summary sentence"),
              ("Unlabelled lines named by their colour per panel; no wrong colour words", r"unlabelled elements")]


def _fmt(st: Optional[dict], unit: str) -> str:
    return "n/a" if not st else f"median {st['median']:g} / max {st['max']:g} {unit}"


def shared_errors(rr: list[dict]) -> list[str]:
    """Readings that BOTH runs get wrong in the same way (the pixel check fails both): run-vs-run agreement cannot see these."""
    out = []
    for i, r1 in enumerate(rr):
        for r2 in rr[i + 1:]:
            for a in r1["checks"].get("readings", []):
                for b in r2["checks"].get("readings", []):
                    if a["ok"] is False and b["ok"] is False and a["figure"] == b["figure"] and a["panel"] == b["panel"] == "position" \
                            and abs(a["x"] - b["x"]) <= TOL_AGREE * SPANS[a["figure"]][0] and abs(a["argon_y"] - b["argon_y"]) <= TOL_AGREE * SPANS[a["figure"]][1]:
                        out.append(f"Fig {a['figure']}: run {r1['run']} reads {a['argon_y']:g} µm at {a['x']:g} m and run {r2['run']} reads "
                                   f"{b['argon_y']:g} µm at {b['x']:g} m; the pixels give at most {max(a['pixel'], b['pixel'])} µm within ±{READ_WINDOW_M:.0f} m")
    return out


def anchor_block(rec: dict) -> list[str]:
    a = rec.get("tfl_anchor")
    if not a:
        return ["TfL's own statement on this site was not found in the corpus (`data/corpus/chunks.jsonl`); the anchor check was skipped.", ""]
    st = a["stated"]
    L = [f"TfL's response to the same FOI request (corpus chunk `{st['chunk']}`, whitespace normalised):", "", f"> {st['zone_sentence']}", ""]
    if st.get("grinding_sentence"):
        L += [f"> {st['grinding_sentence']}", ""]
    L += ["The causal claim (\"likely cause of the noise at this site\") is TfL's, not ours: we did not link roughness to noise. "
          "This is the only reference in the loop that Claude did not write.", ""]
    lo, hi = st["zone_m"]
    def per_run(r: dict) -> str:
        miss = [x["figure"] for x in r["checks"]["tfl_anchor"] if not x["ok"]]
        return f"run {r['run']}: " + ("a zone passing the zone rule overlaps it in all four figures" if not miss else "no such zone in fig " + ", ".join(map(str, miss)))
    L += ["Checks against it: " + "; ".join(per_run(r) for r in rec["runs"]) + ".", ""]
    if a.get("common_zone_range"):
        bf, ov_ = a.get("common_zone_by_figure") or {}, a.get("common_zone_all_figures_overlap_range")
        L += ["A stretch is confirmed (both runs, zone rule) in all four figures, but its ends differ by rail: "
              + "; ".join(f"Fig {n} starts at {_rng(v['start_m'])} m and ends at {_rng(v['end_m'])} m" for n, v in bf.items())
              + (f". The overlap common to all four is about {ov_[0]:.0f}-{ov_[1]:.0f} m" if ov_ else ". No overlap common to all four")
              + f" (union of the four: {a['common_zone_range'][0]:.0f}-{a['common_zone_range'][1]:.0f} m, not elevated everywhere; "
              f"overlaps TfL's {lo:.0f}-{hi:.0f} m: {'yes' if a['common_zone_overlaps'] else 'NO'}).", ""]
    L.append("Model-free pixel check, per figure (onset = first stretch >= 100 m where the 10 m median envelope stays above 2 x the printed threshold; "
             f"median = envelope median inside {lo:.0f}-{hi:.0f} m vs elsewhere): " + "; ".join(
                 f"Fig {n}: onset {v['onset_m']} m, median {v['median_in_um']} vs {v['median_out_um']} µm" for n, v in a["pixels"].items()) + ".")
    return L + [""]


def write_md(rec: dict) -> str:
    C, R1, rr = rec["consolidated"], rec["runs"][0]["extraction"], rec["runs"]
    F = {g["figure_no"]: g for g in R1["figures"]}
    lab = {n: f"Fig {n} ({F[n]['rail_label']} {F[n]['band_label']}, p.{F[n]['page']})" for n in F}
    L = ["# TfL 2018 rail roughness plots, Bethnal Green -> Mile End eastbound (FOI-1730-1819)", "",
         f"**POST-HOC. Argon-extracted, deterministically checked, pending human confirmation (`HUMAN_CHECK.md`).** "
         f"{MODEL}, thinking {THINKING}, {len(rr)} independent calls on the PDF alone; checks and pixel digitizer in "
         f"`agent/roughness.py`; full record in `roughness.json`. Extraction time (latest run) {rec['ts']}. The reading, anchor and "
         "summary checks below were added after the first two runs, on review, and applied to the saved runs.", "",
         f"Source: TfL, *{SOURCE['title']}* (FOI-1730-1819, published {SOURCE['published']}), "
         "`data/corpus/files/FOI-1730-1819__1.pdf`. 2 pages, 4 figures. The PDF text layer holds only the title and the "
         "4 captions; every axis, number and trace is inside 4 embedded raster images (1600x598 px).", "",
         "Each figure: a header line (`Main Av P-P`, `Exceedence` above a threshold); an upper panel, moving-average "
         "peak-to-peak roughness in the caption's wavelength band (µm) against Position (m), with a dashed threshold line; "
         "a lower panel, a roughness spectrum (dB rel 1 µm RMS) in third-octave wavelength bands from 2000 mm to 3.15 mm.", "",
         "## TfL's own statement about this site", ""] + anchor_block(rec) + [
         "## What the plots show (only what survived every check)", ""]
    for n in sorted(C, key=int):
        c, k = C[n], int(n)
        conf = [z for z in c["zones"] if z["confirmed"]]
        zs = "; ".join(f"{_rng(z['start_by_run'])} to {_rng(z['end_by_run'])} m (over runs; envelope median "
                       f"{z['zone_rule']['median_in']} µm inside vs {z['zone_rule']['median_out']} µm elsewhere)" for z in conf) or "none"
        gm = c["global_max_digitizer"]
        runs_gm = ", ".join(f"{v[0]:.0f} m / {v[1]:.0f} µm" + ("" if ok_ is not False else " [fails the pixel check]")
                            for v, ok_ in zip(c["global_max_by_run"], c["global_max_pixel_ok_by_run"]) if v)
        tied = " and ".join(f"{x:.0f}" for x in gm["tied_x"])
        L.append(f"- **{lab[k]}**: header `{c['header_text']}`. Tallest spike {gm['y']:.0f} µm at {tied} m "
                 f"(digitizer; Argon runs {runs_gm}). Stretch above the threshold line reported by both runs and passing the zone rule: {zs}.")
    L.append("")
    for n, rail in (("1", "LHR"), ("3", "RHR")):
        pk = ", ".join(f"{p['band_mm']} mm ({p['db']} dB, {p['prominence_db']} dB above both neighbours)"
                       for p in C[n]["spectrum_peaks_all_agree"]) or "none"
        only = C[n]["spectrum_peaks_digitizer_only"]
        L.append(f"- **{rail} spectrum** (p.{F[int(n)]['page']}; the same image in Figs {n} and {int(n) + 1}): local peaks found by "
                 f"the digitizer and by every run: {pk}." + (f" Digitizer only: {', '.join(only)} mm." if only else ""))
    onsets = {n: (rec["tfl_anchor"]["pixels"][n]["onset_m"] if rec.get("tfl_anchor") else None) for n in C}
    L += ["", "In plain words: " + plain_words(C, onsets, (rec.get("tfl_anchor") or {}).get("stated")), "",
          "## Checks", "", "| Check | " + " | ".join(f"Run {r['run']}" for r in rr) + " |", "|---|" + "---|" * len(rr)]

    def cnt(r: dict, pat: str) -> int:
        return sum(1 for f_ in r["checks"]["failures"] if re.search(pat, f_))
    for name, pat in CHECK_ROWS:
        word = "rejected" if name.startswith("Summary") else "fail"
        L.append(f"| {name} | " + " | ".join("pass" if not cnt(r, pat) else f"**{cnt(r, pat)} {word}**" for r in rr) + " |")
    ag_lines = []
    for k, v in rec["agreement"].items():
        s_ = v["stats"]
        ag_lines.append(
            f"{k}: text {s_['text_items']['agree']}/{s_['text_items']['n']}, header numbers {s_['header_numbers']['agree']}/{s_['header_numbers']['n']}; "
            f"4 global maxima |Δx| {_fmt(s_['global_max']['dx_m'], 'm')}, |Δy| {_fmt(s_['global_max']['dy_um'], 'µm')}; "
            f"{s_['bands_distinct']['abs_db']['n'] if s_['bands_distinct']['abs_db'] else 0} distinct band readings |Δ| {_fmt(s_['bands_distinct']['abs_db'], 'dB')} "
            f"({s_['bands_distinct']['n_over_1p5_db']} differ by more than 1.5 dB, the size of the smallest peak prominence reported); "
            f"zones: {s_['zones']['matched']} matched (boundaries differ {_fmt(s_['zones']['boundary_m'], 'm')}), {s_['zones']['unmatched']} reported by one run only")
    L += ["", f"- Axis titles found in the PDF text layer: {sum(x['in_text_layer'] for x in rr[0]['checks']['axis_labels'])}/"
          f"{len(rr[0]['checks']['axis_labels'])} (they are raster), so they are compared with the printed titles transcribed in the code.",
          f"- Tolerances fixed before any run: agree <= {TOL_AGREE:.0%} of the axis span, tight <= {TOL_TIGHT:.0%} (31 m, 1.56 dB: coarse against the "
          "1.5-1.8 dB peaks reported, so absolute errors are given instead); text exact after case, whitespace, dash and µ normalisation. "
          "Zone rule: envelope median inside > threshold and >= 2x the median outside.",
          "- Run vs run, each distinct item once (absolute errors): " + "; ".join(ag_lines) + ".",
          "- Argon vs the model-free pixel digitizer, per run: " + "; ".join(
              f"run {k}: 4 global maxima |Δx| {_fmt(v['global_max']['dx_m'], 'm')}, |Δy| {_fmt(v['global_max']['dy_um'], 'µm')}; "
              f"{v['bands_distinct']['abs_db']['n'] if v['bands_distinct']['abs_db'] else 0} distinct band readings (29 LHR + 29 RHR) |Δ| "
              f"{_fmt(v['bands_distinct']['abs_db'], 'dB')}, {v['bands_distinct']['n_over_1p5_db']} over 1.5 dB (LHR {_fmt(v['bands_distinct']['per_spectrum_fig'].get('1'), 'dB')}; "
              f"RHR {_fmt(v['bands_distinct']['per_spectrum_fig'].get('3'), 'dB')})" for k, v in rec["vs_digitizer"].items()) + ".",
          "- Fig 2 and Fig 4 print the same spectrum image as Fig 1 and Fig 3 (pixel-identical lower halves: " + ", ".join(f"Fig {k[0]} vs {k[2]} {'yes' if v else 'NO'}" for k, v in rec["pixel_identical"].items()) +
          "), so those band levels are counted once, not twice. " + "; ".join(
              f"run {k}: Argon's copies differ by at most " + ", ".join(f"{c['max_abs_db_between_copies']} dB (Fig {f_} vs Fig {c['copy_of']})" for f_, c in v["copies"].items())
              for k, v in rec["vs_digitizer"].items()) + ". Identical values are consistent with Argon copying the numbers across; they are not an independent re-read.",
          "- Every position and spectrum maximum Argon reported, against the pixels (±10 m window, <= 5 % of the span): " + "; ".join(
              f"run {r['run']} {sum(1 for x in r['checks']['readings'] if x['ok'])}/{len(r['checks']['readings'])} pass"
              for r in rr) + ". Readings on the shoulder of a taller spike within ±10 m fail by construction.",
          "- Run-vs-run agreement cannot catch an error both runs make. Found: " + (" | ".join(shared_errors(rr)) or "none of this kind") +
          (". Both runs agree with each other there, and the pixels contradict both." if shared_errors(rr) else "."),
          "- Claude's independent visual read (`reference_points.json`, recorded before any Gemini call; 4 header lines + 6 points): "
          + "; ".join(f"vs {k} {sum(x['agree'] for x in v)}/{len(v)} agree" for k, v in rec["vs_reference"].items()) + ".",
          f"- Digitizer calibration: tick marks found = printed labels on every axis; interior residual max "
          f"{rec['digitizer_calib_max_px']} px, edge labels within {rec['digitizer_edge_max_px']} px. Share of trace columns wholly / "
          "partly above the printed threshold: " + ", ".join(f"Fig {n} {d['exceedance_bounds_pct_at_printed_threshold']} %"
                                                            for n, d in rec["digitizer"].items()) + " (the header % lies inside each).",
          "", "Rejected or unconfirmed Argon output (kept in `roughness.json`, not used above). Argon's summary prose is never marked "
          "verified: 'unverified' means no check rejected it, not that it is right.", ""]
    for r in rr:
        bad = [z for z in r["checks"]["zones"] if not z["ok"]]
        rej = [x for x in r["checks"]["summary"] if x["status"] == "rejected"]
        badr = [x for x in r["checks"].get("readings", []) if x["ok"] is False]
        other = [f_ for f_ in r["checks"]["failures"] if not re.search(r"not confirmed by the zone rule|summary sentence|fails the pixel check", f_)]
        L.append(f"- Run {r['run']}: {len(bad)} zones fail the zone rule ("
                 + ", ".join(f"Fig {z['figure']} {z['start']:.0f}-{z['end']:.0f} m" for z in bad) + f"); "
                 f"{len(badr)} of {len(r['checks'].get('readings', []))} maxima readings fail the pixel check ("
                 + ", ".join(f"Fig {x['figure']} {x['kind']} {x['argon_y']:g} {x['y_units']} at {x['x']:g} {x['x_units']}, pixels {x['pixel']}" for x in badr) + f"); "
                 f"{len(rej)} of {len(r['checks']['summary'])} summary sentences rejected, {len(r['checks']['summary']) - len(rej)} unverified"
                 + "".join(f" [{'; '.join(x['reasons'])}]" for x in rej)
                 + (f"; other: {'; '.join(other)}" if other else "") + ".")
    L += ["- Argon's descriptions of the unlabelled lines are reported only where they pass the pixel-colour check; the "
          "pixel colours are grey, magenta and olive (spectrum lines) and pink (vertical lines on the position panels), and "
          "the PDF identifies none of them. Marker shape is not checked (Claude's read: hollow circles).", "",
          "## Limits", "",
          "- A January 2018 measurement, eastbound Bethnal Green to Mile End. TfL's response (same FOI) planned grinding at this location for "
          "February 2019; whether it happened is not in our corpus. TfL's answer to MQ 2022/1791 lists Mile End to Bethnal Green (and Bethnal Green to Liverpool Street) among the "
          "sections that \"benefited from rail grinding to help reduce noise levels (December 2021-May 2022)\". "
          "The plots say nothing about the rail after January 2018.",
          "- These are TfL's own rail roughness measurements, read off a picture of a plot. We did not measure corrugation, "
          "and we did not link a roughness value to a noise level: that link (\"likely cause of the noise at this site\") is TfL's statement.",
          "- 'Elevated' here means above the plotted threshold line (>10 µm in the 30-100 mm band, >30 µm in 100-300 mm, per the headers); "
          "the PDF does not say what the lines mean.",
          "- Position (m) is TfL's plotted position (-4.6 to 1534.0 m LHR, 0 to 1544.5 m RHR); its zero point is not "
          "stated, so it is not mapped onto our track bins (TfL's WTT section length is 1,640 m).",
          "- Not explained in the PDF: what LHR/RHR stand for (conventionally left/right-hand rail), the grey, magenta and "
          "olive spectrum lines, the pink vertical lines, and why Figs 1 and 2 print the same exceedance (41%, 0.626 km) "
          "for different thresholds.",
          "- Argon's summary prose is not verified: only the claims of the form 'V µm at X m', 'peak of V dB at B mm' and 'a-b dB higher than ... "
          "from X mm to Y mm' are tested against the pixels; the rest (bounds of stretches, comparisons, shapes) is unchecked.",
          "- Status: **pending human confirmation**. Nothing here goes into the evidence pack until `HUMAN_CHECK.md` is filled in."]
    return "\n".join(L) + "\n"


def atlas_doc(rec: dict) -> dict:
    """The Atlas document: the record without the PDF text, Argon's summary prose left out (unverified), and every position /
    spectrum reading carrying its pixel check so nobody downstream quotes an unchecked reading as a fact. Every rough zone
    carries its zone-rule result, the unlabelled-element text carries the colour check, and Argon's free-text notes are
    marked as unverified prose."""
    d = json.loads(json.dumps({k: v for k, v in rec.items() if k != "pdf_facts"}, default=float))
    for r in d["runs"]:
        chk = {(x["figure"], x["panel"], x["kind"], x["x"], x["argon_y"]): x for x in r["checks"].get("readings", [])}
        zr = {(z["figure"], z["start"], z["end"]): z for z in r["checks"].get("zones", [])}
        r["extraction"].pop("summary", None)
        r["extraction"]["summary_status"] = "left out: Argon prose, unverified (see roughness.json)"
        ufail = [f_ for f_ in r["checks"]["failures"] if f_.startswith("unlabelled elements")]
        r["extraction"]["unlabelled_elements_status"] = ("Argon prose, unverified; colour check " + ("PASSED" if not ufail else "FAILED: " + " | ".join(ufail)) +
                                                          ": the colour words in it are not to be quoted unless the check passed")
        r["extraction"]["unlabelled_colour_check"] = r["checks"].get("unlabelled", [])
        for g in r["extraction"]["figures"]:
            for pn, key in (("position", "position_panel"), ("spectrum", "spectrum_panel")):
                for rd in g[key]["readings"]:
                    c = chk.get((g["figure_no"], pn, rd["kind"], rd["x"], rd["y"]))
                    rd["pixel_check"] = None if c is None else {"ok": c["ok"], "pixel": c["pixel"], "abs_err": c["abs_err"], "tolerance": c["tolerance"]}
                    rd["note_status"] = "Argon prose, unverified"
            for z in g["position_panel"]["rough_zones"]:
                c = zr.get((g["figure_no"], z["start"], z["end"]))
                z["zone_rule"] = None if c is None else {"ok": c["ok"], "median_in": c["median_in"], "median_out": c["median_out"]}
                z["note_status"] = "Argon prose, unverified"
        for sx in r["checks"].get("summary", []):
            sx.pop("sentence", None)
    d["_id"] = f"{SOURCE['doc_id']}:{rec['mode']}:" + "+".join(r["ts"] for r in rec["runs"])
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--mode", default="pdf", choices=("pdf",), help="the PDF only (inline application/pdf)")
    ap.add_argument("--cached", action="store_true", help="re-check the saved runs; no Gemini call; deterministic output")
    ap.add_argument("--atlas", action="store_true", help="upsert the record into the Atlas collection `roughness`")
    a = ap.parse_args()
    for st_ in (sys.stdout, sys.stderr):   # the prints carry Δ / µ: a pipe or log on a cp1252 Windows console must not crash a run
        if hasattr(st_, "reconfigure"):
            st_.reconfigure(encoding="utf-8", errors="replace")
    load_env()
    OUT.mkdir(parents=True, exist_ok=True)
    facts, imgs, anchor = pdf_facts(), figure_images(), tfl_anchor()
    dig = {n: digitize(im, n) for n, im in imgs.items()}
    spans = SPANS
    runs = []
    batch = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for i in range(1, a.runs + 1):
        p = OUT / "runs" / f"{a.mode}_{i}.json"
        if a.cached:
            runs.append(json.loads(p.read_text(encoding="utf-8")))
        else:
            print(f"run {i}: calling {MODEL} ({a.mode}) ...", flush=True)
            try:
                runs.append(extract(i, a.mode) | {"batch": batch})
            except ExtractionError as e:   # a partial batch is parked in runs/_partial/, never mixed with the saved set
                if runs:
                    (OUT / "runs" / "_partial").mkdir(parents=True, exist_ok=True)
                    for r_ in runs:
                        (OUT / "runs" / "_partial" / f"{a.mode}_{r_['run']}.json").write_text(json.dumps(r_, indent=1, ensure_ascii=False), encoding="utf-8")
                sys.exit(f"FAILED: {e} (the saved runs/{a.mode}_<n>.json are untouched; {len(runs)} finished run(s) of this batch are in runs/_partial/)")
            print(f"  {runs[-1]['seconds']} s, tokens {runs[-1]['usage']}, finish_reason {runs[-1]['finish_reason']}", flush=True)
    batches = {r.get("batch") for r in runs}
    if len(batches) > 1:   # (a pre-batch legacy set has batch None in every run and passes)
        sys.exit(f"REFUSED: runs/{a.mode}_<n>.json come from different batches {sorted(map(str, batches))}; delete or re-run them as one set")
    if not a.cached:   # all runs succeeded: replace the saved set in one go
        (OUT / "runs").mkdir(parents=True, exist_ok=True)
        for r_ in runs:
            (OUT / "runs" / f"{a.mode}_{r_['run']}.json").write_text(json.dumps(r_, indent=1, ensure_ascii=False), encoding="utf-8")
        shutil.rmtree(OUT / "runs" / "_partial", ignore_errors=True)
    for r in runs:
        try:
            r["checks"] = check_run(r["extraction"], facts, dig, anchor)
        except Exception as e:  # noqa: BLE001  (a malformed extraction is a failed check, not a crash)
            r["checks"] = {"ok": False, "failures": [f"check crashed: {type(e).__name__}: {e}"], "notes": [""], "axis_labels": [],
                           "zones": [], "summary": [], "readings": [], "tfl_anchor": [], "unlabelled": []}
        print(f"run {r['run']}: checks {'PASS' if r['checks']['ok'] else 'FAIL'} ({len(r['checks']['failures'])} failures)")
        for fmsg in r["checks"]["failures"]:
            print("   x", fmsg)
    ref = json.loads((OUT / "reference_points.json").read_text(encoding="utf-8"))
    pix = {f"{p}v{q}": bool((imgs[p][300:] == imgs[q][300:]).all()) for p, q in ((1, 2), (3, 4))}
    agreement = {f"{i}v{j}": compare(runs[i - 1]["extraction"], runs[j - 1]["extraction"], spans, pix)
                 for i in range(1, len(runs) + 1) for j in range(i + 1, len(runs) + 1)}
    vdig = {str(r["run"]): vs_digitizer(r["extraction"], dig, spans, pix) for r in runs}
    peaks = {"digitizer": {str(n): local_peaks(dig[n]["band_levels"]) for n in dig},
             "argon_run1": {str(g["figure_no"]): local_peaks({b["band_mm"]: b["db"] for b in g["spectrum_panel"]["band_levels"]})
                            for g in runs[0]["extraction"]["figures"]}}
    dig_out = {str(n): {k: v for k, v in d.items() if not k.startswith("_")} | {
        "exceedance_bounds_pct_at_printed_threshold": exceedance_bounds(d, HEADER_THR[n]),
        "elevated_stretches": stretches(d, HEADER_THR[n])}
        for n, d in dig.items()}
    for n, d in dig.items():   # coarse profile for the human reader: max of the envelope per 100 m
        edges = np.arange(np.floor(d["_cx"].min() / 100) * 100, d["_cx"].max() + 100, 100)
        dig_out[str(n)]["envelope_max_per_100m"] = {f"{e:.0f}": round(float(d["_ytop"][(d["_cx"] >= e) & (d["_cx"] < e + 100)].max()), 0)
                                                    for e in edges[:-1] if ((d["_cx"] >= e) & (d["_cx"] < e + 100)).any()}
    cal = [c for d in dig.values() for c in (d["pos_x_calib"], d["pos_y_calib"], d["spec_y_calib"])]
    cons = consolidate(runs, dig, spans)
    for n, c in cons.items():   # did each run's global-max reading pass the pixel check?
        c["global_max_pixel_ok_by_run"] = [next((x["ok"] for x in r["checks"].get("readings", []) if x["figure"] == int(n) and x["kind"] == "global_max"
                                                 and x["panel"] == "position"), None) for r in runs]
    tfl = None
    if anchor:
        lo, hi = anchor["zone_m"]
        cz = common_zone(cons)
        pixels = {}
        for n, d in dig.items():
            m = (d["_cx"] >= lo) & (d["_cx"] <= hi)
            st_ = dig_out[str(n)]["elevated_stretches"]
            pixels[str(n)] = {"onset_m": st_[0][0] if st_ else None, "median_in_um": round(float(np.median(d["_ytop"][m])), 1),
                              "median_out_um": round(float(np.median(d["_ytop"][~m])), 1)}
        rng_ = [min(x for z in cz for x in z["start_by_run"]), max(x for z in cz for x in z["end_by_run"])] if cz else None
        by_fig = {n: {"start_m": [min(z["start_by_run"]), max(z["start_by_run"])], "end_m": [min(z["end_by_run"]), max(z["end_by_run"])]}
                  for n, z in zip(sorted(cons, key=int), cz)}
        # the union rng_ is NOT elevated everywhere: the overlap common to all four figures is [latest start, earliest end]
        both = [max(v["start_m"][1] for v in by_fig.values()), min(v["end_m"][0] for v in by_fig.values())] if by_fig else None
        tfl = {"stated": anchor, "pixels": pixels, "common_zone_range": rng_, "common_zone_by_figure": by_fig,
               "common_zone_all_figures_overlap_range": both if both and both[0] < both[1] else None,
               "common_zone_overlaps": bool(rng_ and rng_[1] > lo and rng_[0] < hi)}
    rec = {"ts": max(r["ts"] for r in runs), "status": "post-hoc; pending human confirmation",
           "source": SOURCE, "mode": a.mode, "model": MODEL, "thinking": THINKING,
           "tolerances": {"agree_fraction_of_axis_span": TOL_AGREE, "tight_fraction_of_axis_span": TOL_TIGHT,
                          "text": "exact after case, whitespace, dash and micro-sign normalisation",
                          "position_reading_vs_pixels": f"envelope maximum within +-{READ_WINDOW_M:g} m of x equals y within {TOL_AGREE:.0%} of the trace span",
                          "exceedance_arithmetic_km": "0.005 x length + 0.0005 (integer % rounding)",
                          "exceedance_vs_trace_pct": "within the digitizer's [lower, upper] bound +- 1",
                          "rough_zone": "envelope median inside > threshold and >= 2 x envelope median outside",
                          "tfl_anchor": "a zone passing the zone rule overlaps the stretch TfL's response calls elevated (parsed from the corpus)",
                          "summary": "never verified; rejected on banned phrasing, an identified unlabelled line, or a claim the pixels contradict"},
           "pdf_facts": {k: v for k, v in facts.items() if k != "pages"} | {"page_text": {p["page"]: p["text"] for p in facts["pages"]}},
           "pixel_identical": pix, "digitizer": dig_out,
           "digitizer_calib_max_px": max(c["interior_max_px"] for c in cal),
           "digitizer_edge_max_px": max(abs(e) for c in cal for e in c["edge_px"]),
           "tfl_anchor": tfl, "consolidated": cons, "runs": runs, "agreement": agreement, "vs_digitizer": vdig, "peaks": peaks,
           "vs_reference": {"digitizer": vs_reference(ref, None, dig, spans), **{f"run{r['run']}": vs_reference(ref, r["extraction"], dig, spans) for r in runs}}}
    rec = json.loads(json.dumps(rec, default=float))
    (OUT / "roughness.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "roughness.md").write_text(write_md(rec), encoding="utf-8")
    hc = OUT / "HUMAN_CHECK.md"
    if not hc.exists():
        hc.write_text(human_check((tfl or {}).get("stated")), encoding="utf-8")
    for k, v in agreement.items():
        s_ = v["stats"]
        print(f"agreement {k}: global max |dx| {_fmt(s_['global_max']['dx_m'], 'm')}, |dy| {_fmt(s_['global_max']['dy_um'], 'um')}; "
              f"{s_['bands_distinct']['abs_db']['n']} distinct bands |d| {_fmt(s_['bands_distinct']['abs_db'], 'dB')}; zones {s_['zones']['matched']} matched, "
              f"{s_['zones']['unmatched']} unmatched")
    for k, v in vdig.items():
        print(f"run {k} vs digitizer: global max |dx| {_fmt(v['global_max']['dx_m'], 'm')}, |dy| {_fmt(v['global_max']['dy_um'], 'um')}; "
              f"{v['bands_distinct']['abs_db']['n']} distinct bands |d| {_fmt(v['bands_distinct']['abs_db'], 'dB')}; copies {v['copies']}")
    for r in runs:
        rd = r["checks"].get("readings", [])
        print(f"run {r['run']} maxima readings vs pixels: {sum(1 for x in rd if x['ok'])}/{len(rd)} pass, {sum(1 for x in rd if x['ok'] is False)} fail")
    if tfl:
        print("tfl anchor", tfl["stated"]["zone_m"], "common zone", tfl["common_zone_range"], "overlaps:", tfl["common_zone_overlaps"],
              "| pixel onsets", {n: v["onset_m"] for n, v in tfl["pixels"].items()})
    for k, v in rec["vs_reference"].items():
        print(f"Claude ref vs {k}:", ", ".join(f"{r['ref']}={'ok' if r['agree'] else 'DIFF'}" for r in v))
    if a.atlas:
        try:
            from atlas.db import db
            doc = atlas_doc(rec)
            db().roughness.replace_one({"_id": doc["_id"]}, doc, upsert=True)
            gone = db().roughness.delete_many({"_id": {"$ne": doc["_id"]}, "source.doc_id": SOURCE["doc_id"]}).deleted_count
            print(f"atlas: upserted roughness/{doc['_id']} ({gone} older version(s) of this record removed)")
        except Exception as e:  # noqa: BLE001
            print(f"(atlas skipped: {type(e).__name__}: {str(e)[:120]})")
    print(f"-> {OUT / 'roughness.json'}")


if __name__ == "__main__":
    main()
