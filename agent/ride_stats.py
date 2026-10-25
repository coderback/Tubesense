"""Per-ride statistics for the ride report: every number Argon may use about one recorded ride, keyed.

Post-hoc (added 25 Oct, after prereg-24oct). Read-only over the pipeline's existing DERIVED outputs; it
recomputes nothing from sensors or audio, and reads no personal notes:
  ride.*                    outputs/inventory.csv: recording length, wall span, audio length, phyphox pauses
  qc.<type>.<k>             outputs/qc.json: each messy-input flag (pause, false start, handled phone,
                            floor-like placement, atypical coupling, truncated section, slow running, ...)
                            with its times and the numbers in its evidence; qc.placement_epoch.<k>
                            (+ qc.placement_epochs: how many), qc.magnetometer, qc.split_run (other recordings of the same trip)
  sync.*                    outputs/sync.json: audio-sensor offset, method, confidence, uncertainty
  section.<A>-<B>.*         outputs/sections.json (timing: duration_s only when both ends were observed; else
                            recorded_span_s, a lower bound with no timetable comparison) and
                            outputs/gonogo/loudness.json (relative level,
                            excluded or not, its place against the same section/direction on other runs)
  trip.contrast_db          outputs/gonogo/loudness.json: within-trip LST-BTH minus BTH-MLE level
  noise.*                   outputs/noise/summary.csv + loudness.json run_context: whole-recording levels,
                            limiter and speech shares, moving-vs-dwell level step (a sync sanity check)
  positioning.<A>-<B>.*     outputs/online/online_eval.json (leave-one-run-out, v1), its stress test on
                            QC-excluded sections, outputs/online/forward_test.json (pre-registered H2b);
                            or a `status` stat saying why the section was not evaluated
Each stat: {key, value, unit, n, detail, source, fields} (the agent/section_stats.py format plus `fields`), so
agent.verify's helpers can check numbers against it. Fractions also carry a *_pct detail so "28 %" can be verified.
Levels are dBFS(A): uncalibrated, relative comparisons only.
`fields` (added for agent.ride_report's schema binding) describes every numeric field of the stat ("value", "n"
and each numeric detail): {unit, label, words, requires, family, section?}. unit is a code (s, min, m, dB = a
difference, dBFS = a phone level, pct, fraction, confidence, count, Hz, deg, uT, ratio, z); label is a short human
name; words are the words a sentence may use to name that quantity (the checker requires one of them, and none of
a same-unit sibling's own words, in the claim's `role`); requires lists the qualifiers a claim citing the field
must carry: relative_level (any dB/dBFS field), lower_bound (a truncated section's recorded span and its start/end
times, which are the recording's edges, not a departure or arrival), approximate
(section times with segmentation_confidence < 0.9), excluded_from_averages, stress_test (tracker scored on a
QC-excluded section). Section timing stats also carry duration_status (complete | lower_bound) and segmentation
(ok | low_confidence) in their detail. A field that is a point in time (t0_s, t1_s, depart/arrive/span times, the
value of a flag whose t0 = t1) carries instant: true (the report refuses duration words on it). ride.id is the run id.
Run: PYTHONPATH=. .venv/Scripts/python -m agent.ride_stats R14 [R4 ...|--all]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "outputs_posthoc" / "ride_stats"
O = ROOT / "outputs"
LEVEL_UNIT = "dBFS(A), uncalibrated phone level: relative comparisons only"

# numbers named in each QC flag's evidence text (qc.py / scripts/qc.py wording), pulled out as detail fields
EVIDENCE = [
    (r"for ([\d.]+) s wall-clock", ("pause_s",)),
    (r"recorded ([\d.]+) s then paused ([\d.]+) s", ("recorded_s", "paused_s")),
    (r"duration (\d+) s vs expected ~(\d+) s", ("section_duration_s", "section_expected_s")),
    (r"phyphox paused ([\d.]+) s inside section", ("pause_s",)),
    (r"(\d+) brief stop\(s\) within 15 s of a platform", ("brief_stops_near_platform",)),
    (r"tilt ([\d.]+) deg", ("tilt_deg",)),
    (r"power x([\d.]+)", ("hf_power_vs_peers",)),
    (r"rattle[^x]*x([\d.]+)", ("rattle_vs_median",)),
    (r"jumped (\d+) uT", ("jump_uT",)),
    (r"median \|B\| (\d+) -> (\d+) uT", ("B_before_uT", "B_after_uT")),
    (r"took (\d+) s vs median (\d+) s", ("duration_s", "median_s")),
    (r"stationary (\d+) s between", ("stationary_s",)),
    (r"changed (\d+) deg", ("reorient_deg",)),
    (r"([\d.]+)-([\d.]+) Hz step periodicity", ("step_hz_lo", "step_hz_hi")),
]


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def stem_of(run: str) -> str:
    """'R14' or 'R14_WB_WCL-LST' -> the recording stem in outputs/qc.json."""
    stems = list(json.loads((O / "qc.json").read_text(encoding="utf-8"))["runs"])
    hit = [s for s in stems if s == run or s.split("_")[0] == run]
    if not hit:
        raise SystemExit(f"unknown run {run!r}; known: {', '.join(s.split('_')[0] for s in stems)}")
    return hit[0]


def stats_hash(stats: dict) -> str:
    return hashlib.sha256(json.dumps(stats, sort_keys=True, default=float).encode()).hexdigest()[:16]


def build(run: str) -> dict[str, dict]:
    stem = stem_of(run)
    rid = stem.split("_")[0]
    stats: dict[str, dict] = {}

    def put(key, value, unit, n=None, detail=None, source=""):
        stats[key] = {"key": key, "value": value, "unit": unit, "n": n, "detail": detail or {}, "source": source}

    put("ride.id", rid, "this recording's id (a ride report may name no other R id, except a split-run partner)",
        None, {}, "outputs/qc.json")

    # ------------------------------------------------------------------ recording (inventory)
    with open(O / "inventory.csv", encoding="utf-8") as fh:
        inv = next(r for r in csv.DictReader(fh) if r["stem"] == stem)
    f = lambda k, nd=1: _r(inv[k], nd) if inv.get(k) not in (None, "") else None  # noqa: E731
    src = "outputs/inventory.csv"
    S = json.loads((O / "sections.json").read_text(encoding="utf-8"))
    rinfo = next(r for r in S["runs"] if r["stem"] == stem)
    put("ride.route", "-".join([rinfo["stations"][0], rinfo["stations"][-1]]), "route", None,
        {"line": rinfo["line"], "direction": rinfo["direction"], "stations": " ".join(rinfo["stations"]),
         "n_sections": len(rinfo["stations"]) - 1}, "outputs/sections.json")
    put("ride.sensor_duration_s", f("acc_dur_s"), "s of phyphox sensor data (experiment time)", None,
        {"sensor_rate_hz": f("acc_rate_hz")}, src)
    put("ride.wall_span_s", f("wall_span_s"), "s wall-clock from phyphox START to final stop (includes pauses)", None, {}, src)
    put("ride.audio_duration_s", f("audio_dur_s"), "s of audio (Voice Memos; levels derived, audio not kept)", None, {}, src)
    put("ride.phyphox_pauses", int(float(inv["n_pauses"] or 0)), "pauses in the phyphox event log", None,
        {"pause_total_s": f("pause_total_s")}, src)

    # ------------------------------------------------------------------ QC flags
    Q = json.loads((O / "qc.json").read_text(encoding="utf-8"))
    q = Q["runs"][stem]
    src = "outputs/qc.json"
    counts: dict[str, int] = {}
    for fl in q["flags"]:
        typ = fl["type"]
        if typ.startswith("magnetometer_") and typ != "magnetometer_reset":
            continue  # summarised as qc.magnetometer below
        counts[typ] = counts.get(typ, 0) + 1
        d = {"severity": fl.get("severity"), "evidence": fl.get("evidence"), "recommendation": fl.get("recommendation")}
        if fl.get("t0") is not None:
            d |= {"t0_s": _r(fl["t0"]), "t1_s": _r(fl["t1"])}
        if typ in ("pause", "false_start") and fl.get("wall1"):
            d["pause_s"] = _r(fl["wall1"] - fl["wall0"], 1)
        for pat, names in EVIDENCE:
            m = re.search(pat, fl.get("evidence") or "")
            if m:
                d |= {nm: float(g) for nm, g in zip(names, m.groups())}
        sec = re.match(r"([A-Z]{3})->([A-Z]{3}):", fl.get("evidence") or "")
        if sec:
            d["section"] = f"{sec[1]}-{sec[2]}"
        if typ in ("split_run", "continuation"):
            m = re.search(r"([\d.]+) min apart", fl["evidence"]) or None
            d |= {"gap_min": float(m[1])} if m else {}
            m = re.search(r"(\d+) s apart", fl["evidence"])
            d |= {"gap_s": float(m[1])} if m else {}
        dur = _r(fl["t1"] - fl["t0"], 1) if fl.get("t0") is not None else None
        if typ in ("pause", "false_start"):
            value, unit = d.get("pause_s"), "s phyphox paused (no sensor data; audio kept running)"
        elif typ == "section_truncated" and dur:
            value, unit = dur, "s of the section inside the recording (one end missing: a lower bound, not a section time)"
        elif dur:
            value, unit = dur, "s flagged interval (experiment time)"
        elif fl.get("t0") is not None:
            value, unit = _r(fl["t0"], 1), "s, time of the event (experiment time)"
        else:
            value, unit = typ, "flag"
        put(f"qc.{typ}.{counts[typ]}", value, unit, None, d, src)
    put("qc.flag_counts", sum(counts.values()), "QC flags on this recording (magnetometer mode not counted)", None,
        dict(counts), src)
    for k, e in enumerate(q["placement_epochs"], 1):
        put(f"qc.placement_epoch.{k}", _r(e["dur"], 1), "s, one resting placement of the phone", None,
            {"t0_s": _r(e["t0"]), "t1_s": _r(e["t1"]), "tilt_deg": _r(e["tilt_med_deg"], 1),
             "screen": e["screen"], "riding_s": _r(e["ride_s"], 1), "label": e["label"],
             "coupling": e.get("coupling"), "hf_power_vs_peers": e.get("hf_vs_peers"),
             "rattle_vs_median": e.get("rattle_vs_median"), "floor_like": e.get("floor_like"),
             "placed_mid_ride": e["placed_mid_ride"]}, src)
    eps = q["placement_epochs"]
    put("qc.placement_epochs", len(eps), "resting placements of the phone found in this recording", None,
        {"floor_like_n": sum(1 for e in eps if e.get("floor_like")),
         "placed_mid_ride_n": sum(1 for e in eps if e["placed_mid_ride"])}, src)
    mg = q["magnetometer"]
    put("qc.magnetometer", mg["mode"], "magnetometer stream (raw = hard-iron bias included; calibrated = iOS)", None,
        {"median_B_uT": mg["median_B_uT"], "mode_confidence": mg["confidence"], "calibration_resets": len(mg["steps"]),
         "note": "underground fields are dominated by train and traction current; not an Earth-field reference"}, src)
    for sp in Q["split_runs"]:
        if stem in sp["runs"]:
            other = [r for r in sp["runs"] if r != stem][0]
            put(f"qc.{sp['type']}_with", other.split("_")[0], "other recording of the same journey", None,
                {"gap_s": sp["gap_s"], "logical_trip": sp["logical_trip_id"], "evidence": sp["evidence"]}, src)

    # ------------------------------------------------------------------ sync
    y = json.loads((O / "sync.json").read_text(encoding="utf-8"))[stem]
    sup = re.match(r"(\d+)/(\d+)", y.get("coarse_support") or "")
    put("sync.offset_s", _r(y["a"], 2), "s, audio time minus sensor wall time at the recording start", None,
        {"method": y["method"], "confidence": y["confidence"], "uncertainty_s": y["uncertainty_s"],
         "xcorr_z": _r(y.get("xcorr_z"), 1), "first_third_lag_s": _r(y.get("xcorr_first_lag_s")),
         "last_third_lag_s": _r(y.get("xcorr_last_lag_s")),
         "band_combos_agreeing": int(sup[1]) if sup else None, "band_combos_total": int(sup[2]) if sup else None,
         "tap_markers_matched": bool(y.get("start_taps_audio") or y.get("end_taps_audio")),
         "notes": y.get("notes")}, "outputs/sync.json")

    # ------------------------------------------------------------------ sections, levels
    L = json.loads((O / "gonogo" / "loudness.json").read_text(encoding="utf-8"))
    agg = {(a["direction"], a["section"]): a for a in L["aggregate_by_section_direction"]["LAeq"]}
    lev = {(s["from"], s["to"]): s for s in L["sections"] if s["stem"] == stem}
    for s in (x for x in S["sections"] if x["stem"] == stem):
        a, b = s["from"], s["to"]
        full = s["depart_observed"] and s["arrive_observed"]
        d = {"departure_observed": s["depart_observed"], "arrival_observed": s["arrive_observed"],
             "tunnel_stops": len(s["tunnel_stops"]), "stops_near_platform": len(s["approach_stops"]),
             "overlaps_pause": s["overlaps_pause"], "segmentation_confidence": s["confidence"],
             "evidence": s["evidence"]}
        if not full:  # the copied text must not hand over the very comparison this stat bans
            d["evidence"] = re.sub(r"^duration \d+ s vs expected ~\d+ s;\s*", "", s["evidence"] or "")
        d["duration_status"] = "complete" if full else "lower_bound"
        d["segmentation"] = "ok" if (s["confidence"] or 0) >= 0.9 else "low_confidence"
        if full:
            put(f"section.{a}-{b}.duration_s", s["duration_s"], "s wall-clock, departure to arrival", None,
                {"timetable_expected_s": s["expected_s"], "depart_t_s": s["depart_t"], "arrive_t_s": s["arrive_t"]} | d,
                "outputs/sections.json")
        else:  # one end is the edge of the recording: a lower bound, not a section time, and no timetable comparison
            miss = "departure" if not s["depart_observed"] else "arrival"
            put(f"section.{a}-{b}.recorded_span_s", s["duration_s"],
                f"s of the section that was recorded ({miss} not observed): a lower bound, NOT the section time; "
                "no timetable comparison is possible", None,
                {"span_start_t_s": s["depart_t"], "span_end_t_s": s["arrive_t"], "missing_end": miss} | d,
                "outputs/sections.json")
        lv = lev.get((a, b))
        if not lv:
            continue
        d = {"seconds": lv["n_s"], "L10": _r(lv["L10"], 1), "L90": _r(lv["L90"], 1),
             "level_minus_trip_mean_db": _r(lv["LAeq_rel_trip"]),
             "excluded_from_averages": lv["placement_excluded"],
             "floor_like_pct": _r(100 * lv["floor_frac"], 1), "handled_pct": _r(100 * lv["handled_frac"], 1),
             "speech_pct": _r(100 * lv["speech_frac"], 1), "limiter_pct": _r(100 * lv["limited_frac"], 1),
             "pause_s_dropped": lv["pause_audio_s_dropped"]}
        ag = agg.get((s["direction"], f"{a}->{b}"))
        if ag and ag["n"]:
            d |= {"section_mean_all_runs": _r(ag["mean"]), "section_sd_all_runs": _r(ag["sd"]),
                  "section_runs_n": ag["n"], "this_run_in_mean": rid in ag["runs"]}
            if not lv["placement_excluded"]:
                d["minus_section_mean_db"] = _r(lv["LAeq"] - ag["mean"])
        put(f"section.{a}-{b}.level", _r(lv["LAeq"]), LEVEL_UNIT, None, d, "outputs/gonogo/loudness.json")
    for t in L["paired_differences"]["per_trip"]:
        if rid in re.split(r"[+_]", t["trip"]):
            secs = [k.split(".")[1] for k in stats if re.fullmatch(r"section\.[A-Z]{3}-[A-Z]{3}\.level", k)]
            lst = next((x for x in secs if "LST" in x.split("-")), "LST-BTH")
            mle = next((x for x in secs if "MLE" in x.split("-")), "BTH-MLE")
            put("trip.contrast_db", _r(t["diff_LAeq"]), f"dB, {lst} level minus {mle} level within this trip (relative)",
                None, {"trip": t["trip"], "minuend_section": lst, "subtrahend_section": mle, "excluded_from_averages": t["placement_excluded"],
                       "pooled_mean_db": _r(L["paired_differences"]["summary"]["placement_ok"]["LAeq"]["mean_db"]),
                       "pooled_trips_n": L["paired_differences"]["summary"]["placement_ok"]["n"]},
                "outputs/gonogo/loudness.json")

    # ------------------------------------------------------------------ whole-recording noise
    with open(O / "noise" / "summary.csv", encoding="utf-8") as fh:
        ns = next(r for r in csv.DictReader(fh) if r["stem"] == stem)
    src = "outputs/noise/summary.csv"
    put("noise.level_overall", _r(ns["LAeq_overall_dbfs"], 1), LEVEL_UNIT + " (whole recording)", int(ns["n_s"]),
        {"p5_1s": _r(ns["LAeq_1s_p5"], 1), "p50_1s": _r(ns["LAeq_1s_p50"], 1), "p95_1s": _r(ns["LAeq_1s_p95"], 1),
         "p95_minus_p5_db": _r(ns["range_p95_p5_db"], 1)}, src)
    put("noise.limiter_fraction", _r(ns["limited_frac"], 3),
        "fraction of seconds whose peak sits at the Voice Memos limiter ceiling (levels there read low)", int(ns["n_s"]),
        {"limiter_pct": _r(100 * float(ns["limited_frac"]), 1),
         "limiter_pct_loudest_10pct": _r(100 * float(ns["limited_frac_top10pct"]), 1), "loudest_subset_pct": 10,
         "clipped_pct": _r(100 * float(ns["clip_frac_0dBFS"]), 2), "ceiling_dbfs": _r(ns["ceiling_dbfs"])}, src)
    put("noise.speech_fraction", _r(ns["speech_flag_frac"], 3),
        "fraction of seconds flagged speech-like (crude modulation flag; such seconds can be dropped)",
        int(ns["n_s"]), {"speech_pct": _r(100 * float(ns["speech_flag_frac"]), 1)}, src)
    rc = L["run_context"].get(stem) or {}
    if rc.get("moving_minus_dwell_db") is not None:
        put("noise.moving_minus_dwell_db", _r(rc["moving_minus_dwell_db"], 1),
            "dB, level while moving minus level at platform dwells (relative; a coarse check that sync puts quiet "
            "dwells where the sensors see stops)", None,
            {"dwell_seconds": rc["n_s_interior_dwell"], "moving_level": _r(rc.get("LAeq_moving"), 1),
             "dwell_level": _r(rc.get("LAeq_interior_dwell"), 1), "dwell_not_quieter": rc["dwell_not_quieter"],
             "edge_offset_suspect": rc["edge_offset_suspect"]}, "outputs/gonogo/loudness.json")

    # ------------------------------------------------------------------ positioning
    OE = json.loads((O / "online" / "online_eval.json").read_text(encoding="utf-8"))
    FT = json.loads((O / "online" / "forward_test.json").read_text(encoding="utf-8"))

    def pos(r):
        return {"timetable_baseline_median_m": r["timetable_vs_independent_m"]["median"],
                "p90_m": r["map_vs_independent_m"]["p90"], "max_m": r["map_vs_independent_m"]["max"],
                "band90_coverage": _r(r["band90_coverage_independent"], 3),
                "band90_coverage_pct": _r(100 * r["band90_coverage_independent"], 1), "band_level_pct": 90,
                "map_runs_n": len(r["map_runs"]), "departure_missed_online": r["departure_missed_online"]}
    for s in (x for x in S["sections"] if x["stem"] == stem):
        sec = f"{s['from']}-{s['to']}"
        ho, done = f"{rid}:{sec}", False
        for rows, kind, unit, src in (
                (OE["runs"], "loo", "leave-one-run-out, clean section, v1 tracker (chord metres)", "outputs/online/online_eval.json"),
                (OE["stress"], "stress", "stress test on a QC-excluded section, v1 tracker (chord metres)", "outputs/online/online_eval.json"),
                (FT["runs"], "forward", "pre-registered forward test (maps frozen 23 Oct), v1 tracker", "outputs/online/forward_test.json")):
            r = next((x for x in rows if x["held_out"] == ho), None)
            if r:
                done = True
                put(f"positioning.{sec}.{kind}_median_m", r["map_vs_independent_m"]["median"],
                    "m, tracker vs the independent curve-edge reference from the gyro (median over the section)", r["map_vs_independent_m"]["n"],
                    {"test": unit, **pos(r)}, src)
        if not done:
            track = f"{s['direction']} {s['from']}->{s['to']}"
            over = sorted({fl["type"] for fl in q["flags"] if fl.get("t0") is not None and fl.get("t1") is not None
                           and fl["t0"] < s["arrive_t"] and fl["t1"] > s["depart_t"]
                           and not (fl["type"].startswith("magnetometer_") and fl["type"] != "magnetometer_reset")})
            n_track = sum(1 for x in OE["runs"] if x["held_out"].endswith(f":{sec}") and x["held_out"].split(":")[0] != rid)
            if track not in OE["lengths_m"]:
                why = "no tracker map for this track (fewer than 2 clean runs on it)"
            elif not n_track:
                why = "no leave-one-run-out map for this track: fewer than 2 clean runs on it"
            elif not (s["depart_observed"] and s["arrive_observed"]):
                why = "section truncated (one end not observed): QC leaves it out of the tracker evaluation"
            else:
                why = ("section excluded from the tracker evaluation by QC; timed flags overlapping it: "
                       f"{', '.join(over) or 'none'}")
            put(f"positioning.{sec}.status", "not evaluated", "tracker evaluation", None, {"reason": why},
                "outputs/online/online_eval.json")
    for k, st in stats.items():
        st["fields"] = field_meta(k, st)
    return stats


# --------------------------------------------------------------------------- per-field metadata (schema binding)
_IV = ["interval", "period", "lasted", "lasting", "for", "duration", "long", "flagged"]
QC_WORDS = {  # words naming the value of each QC flag type
    "pause": ["pause", "paused", "gap", "halt"] + _IV, "false_start": ["pause", "paused", "false", "restart"] + _IV,
    "section_truncated": ["recorded", "span", "only", "truncat", "partial", "inside", "captured"],
    "section_overlaps_pause": ["overlap", "pause"] + _IV, "handled_mid_ride": ["handl", "touch", "moved"] + _IV,
    "handheld": ["hand", "held", "handheld"] + _IV, "walking_mid_file": ["walk", "carried"] + _IV,
    "reorientation": ["reorient", "turn", "rotat"] + _IV, "floor_like_placement": ["floor", "placement", "isolat"] + _IV,
    "atypical_coupling": ["coupling", "atypical", "soft", "rattl", "support"] + _IV,
    "slow_running": ["slow", "took"] + _IV, "unplanned_stop": ["stop", "stood", "stationary", "halt"] + _IV,
    "magnetometer_reset": ["reset", "jump", "calibration", "at"],
}
# detail field -> (unit, label, words)
DETAIL = {
    "t0_s": ("s", "start time (s of experiment time)", ["at", "from", "start", "starting", "started", "began", "beginning", "between", "after"]),
    "t1_s": ("s", "end time (s of experiment time)", ["to", "until", "end", "ending", "ended", "through", "and"]),
    "depart_t_s": ("s", "departure time (s of experiment time)", ["depart", "left", "leaving", "from"]),
    "arrive_t_s": ("s", "arrival time (s of experiment time)", ["arriv", "to", "until", "reached"]),
    "span_start_t_s": ("s", "start of the recorded part (s of experiment time)", ["from", "start", "began", "starting"]),
    "span_end_t_s": ("s", "end of the recorded part (s of experiment time)", ["to", "until", "end", "ended"]),
    "pause_s": ("s", "pause length", ["pause", "paused", "gap", "halt"]),
    "recorded_s": ("s", "seconds recorded before the false-start pause", ["recorded", "ran", "first", "before"]),
    "paused_s": ("s", "pause length after the false start", ["pause", "paused", "gap"]),
    "section_duration_s": ("s", "section duration named in the flag", ["took", "duration", "lasted"]),
    "section_expected_s": ("s", "expected section duration", ["expect", "timetable", "schedul"]),
    "brief_stops_near_platform": ("count", "brief stops within 15 s of a platform", ["stop", "brief"]),
    "tilt_deg": ("deg", "phone tilt", ["tilt", "angle", "inclin"]),
    "hf_power_vs_peers": ("ratio", "20-50 Hz vertical vibration power vs same-section peers", ["power", "vibration", "peers", "vertical"]),
    "rattle_vs_median": ("ratio", "rattle ratio vs the median", ["rattle"]),
    "jump_uT": ("uT", "magnetic field jump", ["jump", "jumped", "changed"]),
    "B_before_uT": ("uT", "median field before the jump", ["before", "from"]),
    "B_after_uT": ("uT", "median field after the jump", ["after", "to"]),
    "duration_s": ("s", "section duration (slow running)", ["took", "duration", "lasted"]),
    "median_s": ("s", "median duration on other runs", ["median", "typical", "usual", "other"]),
    "stationary_s": ("s", "time stationary between stations", ["stationary", "still", "stood", "standing", "stopped"]),
    "reorient_deg": ("deg", "orientation change", ["turn", "rotat", "reorient", "changed"]),
    "step_hz_lo": ("Hz", "lower step frequency", ["step", "walking", "from"]),
    "step_hz_hi": ("Hz", "upper step frequency", ["step", "walking", "to"]),
    "gap_min": ("min", "time between the two recordings", ["apart", "gap", "later", "between"]),
    "gap_s": ("s", "time between the two recordings", ["apart", "gap", "later", "between"]),
    "riding_s": ("s", "seconds of riding in this placement", ["riding", "moving", "ride"]),
    "floor_like_n": ("count", "floor-like placements", ["floor"]),
    "placed_mid_ride_n": ("count", "placements made mid-ride", ["mid", "placed", "moved"]),
    "median_B_uT": ("uT", "median magnetic field magnitude", ["field", "median", "magnet", "b"]),
    "mode_confidence": ("confidence", "confidence of the raw/calibrated call (0-1)", ["confidence"]),
    "calibration_resets": ("count", "iOS calibration resets", ["reset", "calibration"]),
    "n_sections": ("count", "sections in the ride", ["section"]),
    "sensor_rate_hz": ("Hz", "sensor sampling rate", ["rate", "sampl"]),
    "pause_total_s": ("s", "total pause time", ["pause", "paused", "total"]),
    "uncertainty_s": ("s", "sync uncertainty", ["uncertain", "error", "within", "plus"]),
    "xcorr_z": ("z", "cross-correlation peak strength (z-score)", ["z", "score", "peak", "correlation", "strength"]),
    "first_third_lag_s": ("s", "audio-sensor lag in the first third", ["first", "lag", "offset"]),
    "last_third_lag_s": ("s", "audio-sensor lag in the last third", ["last", "final", "lag", "offset"]),
    "band_combos_agreeing": ("count", "band combinations agreeing", ["agree", "agreeing"]),
    "band_combos_total": ("count", "band combinations tried", ["total", "combination", "tried", "of"]),
    "timetable_expected_s": ("s", "timetable section time", ["timetable", "expect", "schedul"]),
    "tunnel_stops": ("count", "stops in the tunnel", ["tunnel"]),
    "stops_near_platform": ("count", "stops near a platform", ["platform", "near"]),
    "segmentation_confidence": ("confidence", "section boundary confidence (0-1)", ["confidence", "segmentation"]),
    "seconds": ("s", "seconds of audio behind the section level", ["second", "covering", "over"]),
    "L10": ("dBFS", "L10 of the section (level exceeded 10 % of the time)", ["l10"]),
    "L90": ("dBFS", "L90 of the section (background level)", ["l90", "background"]),
    "level_minus_trip_mean_db": ("dB", "section level minus the trip mean", ["trip"]),
    "floor_like_pct": ("pct", "share of section seconds floor-like", ["floor"]),
    "handled_pct": ("pct", "share of section seconds handled", ["handl"]),
    "speech_pct": ("pct", "share of seconds speech-like", ["speech"]),
    "limiter_pct": ("pct", "share of seconds at the limiter ceiling", ["limiter", "ceiling", "overall", "all"]),
    "pause_s_dropped": ("s", "audio seconds dropped for a pause", ["dropped", "pause"]),
    "section_mean_all_runs": ("dBFS", "mean level of this section and direction over all runs",
                              ["mean", "average", "runs", "other", "across", "all", "pooled"]),
    "section_sd_all_runs": ("dB", "spread (sd) of this section's level over runs",
                            ["sd", "standard", "spread", "varia", "runs", "all", "across"]),
    "section_runs_n": ("count", "runs in the section mean", ["runs"]),
    "minus_section_mean_db": ("dB", "this level minus the all-runs section mean", ["mean", "average", "runs", "other", "across", "all"]),
    "pooled_mean_db": ("dB", "mean contrast over all clean trips", ["pooled", "mean", "average", "trips"]),
    "pooled_trips_n": ("count", "trips in the pooled mean", ["trips"]),
    "p5_1s": ("dBFS", "5th percentile of the 1 s levels", ["quietest", "p5"]),
    "p50_1s": ("dBFS", "median of the 1 s levels", ["median", "p50", "typical"]),
    "p95_1s": ("dBFS", "95th percentile of the 1 s levels", ["loudest", "p95"]),
    "p95_minus_p5_db": ("dB", "spread of 1 s levels (p95 minus p5)", ["range", "spread"]),
    "limiter_pct_loudest_10pct": ("pct", "share at the limiter among the loudest 10 % of seconds", ["loudest", "top"]),
    "loudest_subset_pct": ("pct", "size of the loudest subset (share of seconds)", ["loudest", "top"]),
    "clipped_pct": ("pct", "share of seconds clipped at full scale", ["clip"]),
    "ceiling_dbfs": ("dBFS", "Voice Memos limiter ceiling", ["ceiling"]),
    "dwell_seconds": ("s", "seconds of platform dwell", ["dwell", "platform", "stationary"]),
    "moving_level": ("dBFS", "level while moving", ["moving", "motion", "running"]),
    "dwell_level": ("dBFS", "level at platform dwells", ["dwell", "platform", "stationary", "standing"]),
    "timetable_baseline_median_m": ("m", "timetable baseline median error", ["timetable", "baseline", "median", "error"]),
    "p90_m": ("m", "tracker p90 error", ["p90", "percentile", "90th", "tracker", "error"]),
    "max_m": ("m", "tracker maximum error", ["max", "maximum", "worst", "largest", "peak", "tracker", "error"]),
    "band90_coverage": ("fraction", "share of the reference inside the tracker's 90 % band", ["coverage", "band", "inside", "within"]),
    "band90_coverage_pct": ("pct", "share of the reference inside the tracker's 90 % band", ["coverage", "band", "inside", "within"]),
    "band_level_pct": ("pct", "nominal level of the tracker's band", ["band", "nominal"]),
    "map_runs_n": ("count", "runs in the tracker map", ["map", "runs"]),
}
INSTANT = ("t0_s", "t1_s", "depart_t_s", "arrive_t_s", "span_start_t_s", "span_end_t_s")
_TEST_WORDS = {"loo": ["leave", "loo", "held"], "forward": ["forward"], "stress": ["stress"]}
FAMILY = {"pct": "share", "fraction": "share"}  # both are written with "%": one family for the sibling-word rule


def _value_meta(key: str, st: dict) -> tuple[str, str, list[str]] | None:
    """(unit, label, words) of a stat's `value`, or None when it is not a number."""
    v = st["value"]
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    p = key.split(".")
    if key == "ride.sensor_duration_s":
        return "s", "seconds of phyphox sensor data", ["sensor", "phyphox", "motion", "data", "recorded", "recording"]
    if key == "ride.wall_span_s":
        return "s", "wall-clock span of the recording", ["wall", "clock", "span", "elapsed", "total"]
    if key == "ride.audio_duration_s":
        return "s", "seconds of audio", ["audio", "sound", "voice"]
    if key == "ride.phyphox_pauses":
        return "count", "phyphox pauses", ["pause"]
    if key == "qc.flag_counts":
        return "count", "QC flags on the recording", ["flag", "qc"]
    if key == "qc.placement_epochs":
        return "count", "resting placements of the phone", ["placement", "position", "rest", "resting"]
    if p[0] == "qc" and p[1] == "placement_epoch":
        return "s", "length of one resting placement", ["placement", "rest", "resting", "lay", "sat", "position", "placed"]
    if p[0] == "qc":
        typ = p[1]
        words = QC_WORDS.get(typ, typ.split("_") + _IV)
        if st["unit"].startswith("s, time of the event"):  # an instant, not a length: no duration words (for, lasted, ...)
            return "s", f"time of the {typ.replace('_', ' ')} (s of experiment time)",                 sorted({w for w in words if w not in _IV} | {"at"})
        if typ == "section_truncated":
            return "s", "recorded part of a truncated section (a lower bound)", words
        return "s", f"length of the {typ.replace('_', ' ')} interval", words
    if key == "sync.offset_s":
        return "s", "audio-sensor offset", ["offset", "shift", "align", "lag"]
    if re.fullmatch(r"section\.[A-Z]{3}-[A-Z]{3}\.duration_s", key):
        return "s", f"{p[1]} section time, departure to arrival", ["took", "duration", "lasted", "ran", "run", "travel", "time", "journey"]
    if re.fullmatch(r"section\.[A-Z]{3}-[A-Z]{3}\.recorded_span_s", key):
        return "s", f"recorded part of {p[1]} (a lower bound, not the section time)", \
            ["recorded", "span", "only", "partial", "captured", "least", "inside"]
    if re.fullmatch(r"section\.[A-Z]{3}-[A-Z]{3}\.level", key):
        return "dBFS", f"{p[1]} section level (relative)", ["level", "laeq", "measured"]
    if key == "trip.contrast_db":
        d = st["detail"]
        return "dB", f"{d.get('minuend_section')} level minus {d.get('subtrahend_section')} level (same trip)", \
            ["contrast", "difference", "minus", "gap", "between"]
    if key == "noise.level_overall":
        return "dBFS", "whole-recording level (relative)", ["overall", "whole", "recording", "level", "average"]
    if key == "noise.limiter_fraction":
        return "fraction", "share of seconds at the limiter ceiling", ["limiter", "ceiling", "overall", "all"]
    if key == "noise.speech_fraction":
        return "fraction", "share of seconds speech-like", ["speech"]
    if key == "noise.moving_minus_dwell_db":
        return "dB", "level while moving minus level at platform dwells", ["step", "difference", "minus", "contrast", "between"]
    if p[0] == "positioning":
        t = p[2].split("_")[0]
        return "m", f"tracker median error on {p[1]} ({t} test)", ["tracker", "median", "error"] + _TEST_WORDS.get(t, [])
    raise ValueError(f"no field metadata for the value of {key}")


def field_meta(key: str, st: dict) -> dict[str, dict]:
    """Every numeric field of one stat -> {unit, label, words, requires, family, section?}; raises on an unknown
    numeric field, so a new stat cannot reach the ride-report checker without a unit."""
    sec = re.match(r"(?:section|positioning)\.([A-Z]{3}-[A-Z]{3})\.", key)
    sec = sec[1] if sec else (st["detail"].get("section") if key.startswith("qc.") else None)
    out = {}
    vm = _value_meta(key, st)
    if vm:
        out["value"] = vm
    if st.get("n") is not None:
        out["n"] = ("s", "seconds of audio", ["second", "seconds"]) if key.startswith("noise.") else \
            ("count", "samples scored", ["sample", "point", "epoch", "scored"])
    for nm, v in (st.get("detail") or {}).items():
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        if key == "qc.flag_counts":
            out[nm] = ("count", f"{nm.replace('_', ' ')} flags", nm.split("_") + ["flag"])
        elif nm in DETAIL:
            out[nm] = DETAIL[nm]
            if nm in ("t0_s", "t1_s") and key.startswith("qc."):  # "paused at 47.2 s": the event's own words may name its times
                typ = key.split(".")[1]
                ev = ["placement", "rest", "resting", "placed"] if typ == "placement_epoch" else                     [w for w in QC_WORDS.get(typ, typ.split("_")) if w not in _IV]
                out[nm] = (out[nm][0], out[nm][1], out[nm][2] + [w for w in ev if w not in out[nm][2]])
        else:
            raise ValueError(f"no field metadata for {key}.{nm}")
    d = st.get("detail") or {}
    low_conf = d.get("segmentation") == "low_confidence"
    meta = {}
    for nm, (unit, label, words) in out.items():
        req = []
        if unit in ("dB", "dBFS"):
            req.append("relative_level")
        if (key.endswith(".recorded_span_s") and nm in ("value", "span_start_t_s", "span_end_t_s")) or                 (key.startswith("qc.section_truncated") and nm in ("value", "t0_s", "t1_s")):
            req.append("lower_bound")  # the span's ends are the recording's edges, not a departure / arrival
        if low_conf and (nm == "value" or nm in ("depart_t_s", "arrive_t_s", "span_start_t_s", "span_end_t_s")):
            req.append("approximate")
        if d.get("excluded_from_averages") is True and nm in ("value", "L10", "L90", "level_minus_trip_mean_db"):
            req.append("excluded_from_averages")
        if key.startswith("positioning.") and "stress" in key:
            req.append("stress_test")
        if unit == "dBFS" and "level" not in words:  # every phone level is "a level"; the other words say which
            words = list(words) + ["level"]
        meta[nm] = {"unit": unit, "label": label, "words": list(words), "requires": req, "family": FAMILY.get(unit, unit)}
        if nm in INSTANT or (nm == "value" and st["unit"].startswith("s, time of the event")):
            meta[nm]["instant"] = True  # a point in time: the ride report refuses duration words (for, lasted) on it
        if sec:
            meta[nm]["section"] = sec
    return meta


def write(run: str) -> tuple[Path, dict]:
    stats = build(run)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    p = OUTDIR / f"{stem_of(run).split('_')[0]}.json"
    p.write_text(json.dumps(stats, indent=1, default=float), encoding="utf-8")
    return p, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    runs = [s.split("_")[0] for s in json.loads((O / "qc.json").read_text(encoding="utf-8"))["runs"]
            if s.split("_")[0] not in ("R0", "R0.5")] if a.all else a.runs
    for r in runs:
        p, st = write(r)
        print(f"{r}: {len(st)} stats -> {p.relative_to(ROOT)} (sha {stats_hash(st)})")
        if len(runs) == 1:
            for k, v in st.items():
                print(f"  {k:40s} {v['value']} | {v['unit'][:60]}")


if __name__ == "__main__":
    main()
