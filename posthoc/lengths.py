"""Section lengths from TfL Working Timetables (post-hoc v2), replacing v1's straight-line chords.

v1 (tubesense.online.Template, scripts/online_eval.lengths) scales every map to the straight-line
distance between station coordinates, because TfL's Route/Sequence lineStrings join these
stations with single straight segments. The Working Timetable (WTT) gives the along-track
section length the timetable is built on, which is the better metre axis.

Sources (TfL FOI releases, fetched 24 Oct 2026):
- Central line Timetable Notice TTN 37/25, FOI-3481-2425:
  https://foi.tfl.gov.uk/FOI-3481-2425/3481-2425-Working%20Timetable%20Notice%20Central%20Line%20TTN%2037-25.pdf
  Liverpool Street - Bethnal Green 2,270 m; Bethnal Green - Mile End 1,640 m.
- Circle and Hammersmith & City WTT 31 (18 May 2014), FOI-1642-2021:
  https://foi.tfl.gov.uk/FOI-1642-2021/WTT%2031%20Circle%20and%20Hammersmith%20and%20City%2018%20May%202014.pdf
  Liverpool Street - Aldgate East 920 m; Aldgate East - Whitechapel 820 m.
OSM platform-centre lengths agree with these to a median 1.3 % (docs/ARCHITECTURE.md, decision 2).
WTT lengths are given per section, not per direction; both directions use the same figure here.
ALE-WCL is SHORTER than its chord (820 vs 993 m): the TfL API station coordinates are not platform
centres there, so the chord is not a lower bound. Only the Central sections are used on 23 Oct data.

Because the v1 tracker works in map fractions and its map's metre axis is fraction-proportional
(dead-reckoned profile x length_m), a v1 metre figure converts exactly to WTT metres by the factor
WTT / chord (rescale_m).

Run: .venv/Scripts/python -m posthoc.lengths   (prints the table and writes outputs_posthoc/lengths.json)
"""
from __future__ import annotations

import json

from tubesense.ingest import ROOT
from tubesense.stops import STATIONS, distance_m

OUT = ROOT / "outputs_posthoc"

FOI_CENTRAL = "TfL FOI-3481-2425, Central line Working Timetable Notice TTN 37/25"
FOI_HC = "TfL FOI-1642-2021, Circle and Hammersmith & City WTT 31 (18 May 2014)"
URL_CENTRAL = ("https://foi.tfl.gov.uk/FOI-3481-2425/3481-2425-Working%20Timetable%20Notice%20Central%20Line"
               "%20TTN%2037-25.pdf")
URL_HC = ("https://foi.tfl.gov.uk/FOI-1642-2021/WTT%2031%20Circle%20and%20Hammersmith%20and%20City%2018%20May"
          "%202014.pdf")

# frozenset({a, b}) -> (metres, citation, url); direction-agnostic
WTT_M: dict[frozenset, tuple[float, str, str]] = {
    frozenset({"LST", "BTH"}): (2270.0, FOI_CENTRAL, URL_CENTRAL),
    frozenset({"BTH", "MLE"}): (1640.0, FOI_CENTRAL, URL_CENTRAL),
    frozenset({"LST", "ALE"}): (920.0, FOI_HC, URL_HC),
    frozenset({"ALE", "WCL"}): (820.0, FOI_HC, URL_HC),
}


def stations_of(track: str) -> tuple[str, str]:
    """'EB LST->BTH' or 'LST-BTH' -> ('LST', 'BTH')."""
    s = track.split()[-1].replace("->", "-")
    a, b = s.split("-")
    return a, b


def chord_m(a: str, b: str) -> float:
    """v1's straight-line length (tubesense.stops.distance_m), rounded as outputs/positioning/track_lengths.json."""
    return round(distance_m(a, b), 1)


def section_length(a: str, b: str) -> dict:
    """WTT length for a section in either direction, falling back to the v1 chord when not tabulated.

    Returns {m, source, url, chord_m, factor}; factor = m / chord_m (1.0 on fallback).
    """
    ch = chord_m(a, b)
    hit = WTT_M.get(frozenset({a, b}))
    if hit is None:
        return {"m": ch, "source": "fallback: straight-line chord (v1)", "url": None, "chord_m": ch, "factor": 1.0}
    m, src, url = hit
    return {"m": m, "source": src, "url": url, "chord_m": ch, "factor": round(m / ch, 5)}


def wtt_m(track: str) -> float:
    return section_length(*stations_of(track))["m"]


def factor(track: str) -> float:
    """Multiply a v1 (chord-scaled) metre figure by this to express it in WTT metres."""
    return section_length(*stations_of(track))["factor"]


def rescale_m(x, track: str):
    """v1 metres (chord-scaled map) -> WTT metres. Works on scalars and numpy arrays."""
    return x * factor(track)


def rescale_summary(s: dict, track: str) -> dict:
    """Rescale a scripts/online_eval.summary() dict (n, median, mean, p90, max) to WTT metres."""
    f = factor(track)
    return {k: (v if k == "n" else round(v * f, 1)) for k, v in s.items()}


def table() -> dict:
    out = {}
    for key in WTT_M:
        a, b = sorted(key, key=list(STATIONS).index)
        out[f"{a}-{b}"] = section_length(a, b)
    return out


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    t = table()
    for k, v in t.items():
        print(f"{k}: WTT {v['m']:.0f} m vs chord {v['chord_m']:.1f} m (x{v['factor']:.4f})  [{v['source']}]")
    json.dump({"doc": __doc__, "sections": t}, open(OUT / "lengths.json", "w"), indent=1)
