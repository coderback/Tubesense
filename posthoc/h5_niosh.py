"""H5 (pre-registered addendum, outputs/PLAN_24Oct.md): absolute levels from the NIOSH Sound Level Meter app.

Written after the rides, as the addendum allows. It does what the plan specifies where the data
permit, and labels every deviation:

1. ABSOLUTE LEVEL. The plan asks for LAeq per line, section and direction (mean +- sd over runs).
   The NIOSH app exported one report per trip (Liverpool Street <-> Mile End, Bethnal Green dwell and
   platform time included), so levels are per trip, n = 1 per direction. DEVIATION.
   Reported against the HSE 85 dB(A) upper action level and next to TfL's in-cab section figures
   (FOI-1231-2425) and the reported 92.8 dB; the plan named 92.8 as "TfL's FOI figure", but no primary
   FOI for it was found (see CLAUDE.md), so both are shown.
2. OFFSET TO OUR RELATIVE LEVELS. Same-ride offset is impossible (one phone; NIOSH and Voice Memos
   cannot record together). Instead: NIOSH trip LAeq minus the Voice Memos trip LAeq (dBFS(A), energy
   mean of all 1-s levels, outputs/noise/*_levels.csv) of the same-direction Central rides recorded the
   same evening (R7-R12). DIFFERENT RIDES AND TRAINS: a labelled deviation, indicative only.
   The slope of the offset against level needs several sections per ride; with one trip per direction
   it is not computed.
3. WITHIN-TRIP LST->BTH minus BTH->MLE from NIOSH: not possible (no per-section readings).

Inputs: data/raw/niosh.csv (levels only, no audio), outputs/noise/*_levels.csv (run scripts/run_gonogo.py
first). Writes outputs_posthoc/h5_niosh.json and .md.

Run (repo root):
    PYTHONPATH=. .venv/Scripts/python posthoc/h5_niosh.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from tubesense.noise import energy_mean

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs_posthoc"
HSE_UPPER = 85.0
# TfL in-cab LAeq, FOI-1231-2425 (24 Jun 2024): EB and WB per section
FOI = {("EB", "LST-BTH"): 87, ("EB", "BTH-MLE"): 83, ("WB", "MLE-BTH"): 82, ("WB", "BTH-LST"): 86}
REPORTED_LST_BTH = 92.8  # secondary (Brixton Buzz via JRPass), no primary FOI located
SAME_EVENING = {"EB": ["R7_EB_LST-MLE", "R9_EB_LST-MLE", "R11_EB_LST-MLE"],
                "WB": ["R8_WB_MLE-LST", "R10_WB_MLE-LST", "R12_WB_MLE-LST"]}
DIR = {("LST", "MLE"): "EB", ("MLE", "LST"): "WB"}


def vm_trip_laeq(stem: str) -> float | None:
    p = ROOT / "outputs" / "noise" / f"{stem}_levels.csv"
    if not p.exists():
        return None
    return float(energy_mean(pd.read_csv(p).LAeq_1s.to_numpy()))


def direction_gap(trips: list[dict]) -> dict | None:
    """POST-HOC (not in the plan): EB minus WB trip level, NIOSH vs Voice Memos (same-evening mean)."""
    by = {t["direction"]: t for t in trips}
    if not {"EB", "WB"} <= by.keys():
        return None
    vm = {d: energy_mean(np.array([v for v in by[d]["vm_same_evening_dbfs"].values() if v is not None]))
          for d in ("EB", "WB")}
    summ = pd.read_csv(ROOT / "outputs" / "noise" / "summary.csv").set_index("stem")
    stems = [s for d in ("EB", "WB") for s in SAME_EVENING[d] if s in summ.index]
    lim, top = summ.loc[stems, "limited_frac"], summ.loc[stems, "limited_frac_top10pct"]
    return {"niosh_eb_minus_wb_db": round(by["EB"]["LAeq_dBA"] - by["WB"]["LAeq_dBA"], 1),
            "vm_limited_frac": [round(float(lim.min()), 2), round(float(lim.max()), 2)],
            "vm_limited_frac_top10pct": [round(float(top.min()), 2), round(float(top.max()), 2)],
            "vm_eb_minus_wb_db": round(vm["EB"] - vm["WB"], 1),
            "note": "one NIOSH trip per direction on different trains; Voice Memos trip levels are limiter-bound"}


def main() -> dict:
    niosh = pd.read_csv(ROOT / "data" / "raw" / "niosh.csv")
    trips = []
    for r in niosh.to_dict("records"):
        d = DIR[(r["from"], r["to"])]
        vm = {s: vm_trip_laeq(s) for s in SAME_EVENING[d]}
        vm_ok = [v for v in vm.values() if v is not None]
        offs = [r["LAeq_dBA"] - v for v in vm_ok]
        trips.append({
            "run_id": r["run_id"], "line": r["line"], "direction": d, "from": r["from"], "to": r["to"],
            "LAeq_dBA": r["LAeq_dBA"], "max_level_dBA": r["LAFmax_dBA"], "LCpeak_dB": r["LCpeak_dB"],
            "above_hse_upper_db": round(r["LAeq_dBA"] - HSE_UPPER, 1),
            "foi_in_cab_sections": {k[1]: v for k, v in FOI.items() if k[0] == d},
            "vm_same_evening_dbfs": {s: (round(v, 2) if v is not None else None) for s, v in vm.items()},
            "offset_db": {"median": round(float(np.median(offs)), 2), "min": round(min(offs), 2),
                          "max": round(max(offs), 2), "n_vm_rides": len(offs)} if offs else None,
        })
    res = {
        "status": "pre-registered H5 with deviations: per-trip not per-section; offset across rides, not same ride",
        "hse_upper_action_dBA": HSE_UPPER,
        "reported_lst_bth_dB": REPORTED_LST_BTH,
        "trips": trips,
        "part3": "not possible: NIOSH exported one reading per trip",
        "posthoc_direction_gap": direction_gap(trips),
        "limits": ["iPhone built-in mic validated to ~0.5 dB(A) only up to ~85 dB (Huyan et al. 2023); "
                   "these trips are above that, so readings may be low",
                   "not a certified meter: indicative", "n = 1 trip per direction; different trains",
                   "NIOSH trip span includes platform waits and the Bethnal Green dwell",
                   "our phone was on a seat in the 2nd car (passenger saloon); TfL's figures are in-cab"],
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "h5_niosh.json").write_text(json.dumps(res, indent=1))
    (OUT / "h5_niosh.md").write_text(render(res))
    return res


def render(res: dict) -> str:
    L = ["# H5: absolute levels from the NIOSH SLM app (indicative)", "",
         "Pre-registered in the `outputs/PLAN_24Oct.md` addendum. **Deviations:** NIOSH exported one reading "
         "per trip (Liverpool Street <-> Mile End, incl. the Bethnal Green dwell), not per section; the "
         "offset to Voice Memos is across different rides on the same evening, not the same ride.", "",
         "## 1. Trip levels", "",
         "| Ride | Direction | LAeq dB(A) | Max dB(A) | LCpeak dB | vs HSE 85 dB(A) | TfL in-cab sections (FOI-1231-2425) |",
         "|---|---|---|---|---|---|---|"]
    for t in res["trips"]:
        foi = ", ".join(f"{k} {v}" for k, v in t["foi_in_cab_sections"].items())
        L.append(f"| {t['run_id']} | {t['direction']} {t['from']}->{t['to']} | {t['LAeq_dBA']:.1f} | "
                 f"{t['max_level_dBA']:.1f} | {t['LCpeak_dB']:.1f} | {t['above_hse_upper_db']:+.1f} dB | {foi} |")
    L += ["", f"The widely quoted {res['reported_lst_bth_dB']} dB for Liverpool Street -> Bethnal Green is "
          "secondary (no primary FOI located). A per-trip level cannot be compared with a per-section figure "
          "directly; it includes quieter platform and dwell time.", "",
          "## 2. Offset NIOSH minus Voice Memos (different rides, same evening)", "",
          "| NIOSH ride | Voice Memos trips (dBFS(A)) | offset median [min, max] dB |", "|---|---|---|"]
    for t in res["trips"]:
        vm = ", ".join(f"{s.split('_')[0]} {v:.1f}" for s, v in t["vm_same_evening_dbfs"].items() if v is not None)
        o = t["offset_db"]
        L.append(f"| {t['run_id']} | {vm or 'n/a'} | " +
                 (f"{o['median']:.1f} [{o['min']:.1f}, {o['max']:.1f}] (n={o['n_vm_rides']}) |" if o else "n/a |"))
    L += ["", "The slope of the offset against level (the Voice Memos limiter) needs per-section readings: "
          "not computed.", ""]
    g = res["posthoc_direction_gap"]
    if g:
        L += ["**Post-hoc observation (not in the plan):** eastbound minus westbound trip level is "
              f"{g['niosh_eb_minus_wb_db']:+.1f} dB on NIOSH but {g['vm_eb_minus_wb_db']:+.1f} dB on Voice Memos, "
              "so the offset is not one constant. Two readings on different trains cannot separate a train or "
              "direction effect from Voice Memos compressing loud trips. On the six same-evening Voice Memos trips "
              f"the limiter was engaged in {g['vm_limited_frac'][0]:.0%}-{g['vm_limited_frac'][1]:.0%} of seconds "
              f"and in {g['vm_limited_frac_top10pct'][0]:.0%}-{g['vm_limited_frac_top10pct'][1]:.0%} of the "
              "loudest 10 % of seconds. Do not convert Voice Memos levels to dB(A) with a single offset.", ""]
    L += ["## 3. Within-trip LST->BTH minus BTH->MLE", "", res["part3"] + ".", "",
          "## Limits", ""] + [f"- {x}" for x in res["limits"]] + [""]
    return "\n".join(L)


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
