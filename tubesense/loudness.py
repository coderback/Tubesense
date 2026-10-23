"""GO/NO-GO (c): A-weighted loudness per station-to-station section.

Joins three upstream products:
- per-second levels in audio time (noise.levels, dBFS(A), uncalibrated),
- sections in phyphox experiment time (outputs/sections.json),
- sensor -> audio mapping via wall time (sync.sensor_to_audio; handles R4's pauses).
Placement exclusions come from outputs/qc.json (floor_like_placement flags), not from
hand-picked run ids. Audio keeps running through phyphox pauses, but those seconds have no
sensor position and the phone was being handled (R4's battery alert), so they are dropped.

Every level here is dB(A) re full scale. Only within-phone comparisons are meaningful,
and the Voice Memos limiter compresses the loud end (~1-3 dB), biasing differences
towards zero.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy import stats

from .ingest import ROOT, Run, list_runs, load_run
from .noise import energy_mean, levels
from .sync import audio_to_sensor, load_params, sensor_to_audio

OUT = ROOT / "outputs"
TOP_FRAC = 0.25      # "top25" variant: energy mean of the loudest 25 % of seconds (length-neutral)
TRIM_S = 10.0          # "core" variant: drop this much at each end (accel/brake + sync error)
FLOOR_EXCLUDE = 0.25   # exclude a section-run from averages if >= this share is floor-like placement
WITHIN_DB = 6.0        # count seconds within this of the section-run max

# Section pairs for the headline test, per direction: (hot section, comparison section).
PAIRS = {"EB": (("LST", "BTH"), ("BTH", "MLE")), "WB": (("BTH", "LST"), ("MLE", "BTH"))}
TRACK = {frozenset({"LST", "BTH"}): "LST-BTH", frozenset({"BTH", "MLE"}): "BTH-MLE"}


def _json(name: str) -> dict:
    return json.loads((OUT / name).read_text())


def _overlap(t0: float, t1: float, spans: list[tuple[float, float]]) -> float:
    """Share of [t0, t1] covered by spans (spans assumed non-overlapping)."""
    tot = sum(max(0.0, min(t1, b) - max(t0, a)) for a, b in spans)
    return tot / (t1 - t0) if t1 > t0 else 0.0


def trip_ids() -> dict[str, str]:
    """stem -> logical trip id; split runs (qc.json) share one id, all others keep their stem."""
    ids = {r.stem: r.stem for r in list_runs()}
    for s in _json("qc.json")["split_runs"]:
        if s["type"] == "split_run" and s["logical_trip_id"]:
            for stem in s["runs"]:
                ids[stem] = s["logical_trip_id"]
    return ids


def metrics(la: np.ndarray, lafmax: np.ndarray, deficit: np.ndarray) -> dict:
    """Level statistics of a run of 1-s LAeq values (dBFS(A)).

    LAeq_limiter_ub adds each second's crest deficit back: an indicative upper bound on the
    level without the Voice Memos limiter, not a correction.
    """
    if len(la) == 0:
        return {"n_s": 0}
    mx = float(la.max())
    return {
        "n_s": int(len(la)),
        "LAeq": energy_mean(la),                       # energy average over the section
        "LAeq_1s_mean": float(la.mean()),              # arithmetic mean of 1-s levels
        "L10": float(np.percentile(la, 90)),           # level exceeded 10 % of the time
        "L90": float(np.percentile(la, 10)),           # level exceeded 90 % of the time
        "LAeq_1s_max": mx,
        "LAFmax_max": float(np.max(lafmax)),
        "n_s_within6_of_max": int((la >= mx - WITHIN_DB).sum()),
        "top25_LAeq": energy_mean(np.sort(la)[-max(1, int(np.ceil(TOP_FRAC * len(la)))):]),
        "LAeq_limiter_ub": energy_mean(la + deficit),
    }


def section_rows(run: Run, df: pd.DataFrame, sections: list[dict], qc_run: dict) -> list[dict]:
    """One row per section of this run: audio window, metric variants, QC context, 1-s series."""
    tc = df.t_audio.to_numpy() + 0.5                   # bin centres
    has_sensor = np.isfinite(audio_to_sensor(run, tc))  # False inside a phyphox pause
    floor = [(f["t0"], f["t1"]) for f in qc_run["flags"] if f["type"] == "floor_like_placement"]
    hand = [(f["t0"], f["t1"]) for f in qc_run["flags"] if f["type"] in ("handheld", "handled_mid_ride")]
    pauses = [f for f in qc_run["flags"] if f["type"] == "pause"]
    rows = []
    for s in sections:
        a0, a1 = (float(v) for v in sensor_to_audio(run, [s["depart_t"], s["arrive_t"]]))
        win = (tc >= a0) & (tc < a1)
        m = win & has_sensor
        core = (tc >= a0 + TRIM_S) & (tc < a1 - TRIM_S) & has_sensor
        sp = df.speech_flag.to_numpy()
        la, fm, cd = df.LAeq_1s.to_numpy(), df.LAFmax_1s.to_numpy(), df.crest_deficit_db.to_numpy()
        pause_s = sum(f["wall1"] - f["wall0"] for f in pauses
                      if s["depart_wall"] < f["wall0"] < s["arrive_wall"])
        row = {
            "stem": run.stem, "run_id": run.run_id, "direction": run.direction,
            "section": f"{s['from']}->{s['to']}", "from": s["from"], "to": s["to"],
            "depart_t": s["depart_t"], "arrive_t": s["arrive_t"],
            "a0": a0, "a1": a1, "audio_dur_s": a1 - a0, "wall_dur_s": s["duration_s"],
            "within_audio": bool(a0 >= 0 and a1 <= tc[-1] + 0.5),
            "pause_audio_s_dropped": int((win & ~has_sensor).sum()),
            "seg_confidence": s["confidence"], "depart_observed": s["depart_observed"],
            "arrive_observed": s["arrive_observed"], "pause_s_in_section": pause_s,
            "floor_frac": _overlap(s["depart_t"], s["arrive_t"], floor),
            "handled_frac": _overlap(s["depart_t"], s["arrive_t"], hand),
            "speech_frac": float(sp[m].mean()) if m.any() else np.nan,
            "limited_frac": float(df.limited.to_numpy()[m].mean()) if m.any() else np.nan,
            "crest_deficit_db_p50": float(np.median(df.crest_deficit_db.to_numpy()[m])) if m.any() else np.nan,
        }
        row["placement_excluded"] = row["floor_frac"] >= FLOOR_EXCLUDE
        for tag, mask in (("", m), ("nospeech_", m & ~sp), ("core_", core)):
            row.update({tag + k: v for k, v in metrics(la[mask], fm[mask], cd[mask]).items()})
        row["series_t_rel"] = np.round(tc[m] - a0, 1).tolist()
        row["series_LAeq_1s"] = np.round(la[m], 1).tolist()
        rows.append(row)
    return rows


def edge_check(run: Run, df: pd.DataFrame, run_seg: dict, win: float = 10.0) -> list[dict]:
    """Do sensor-derived arrivals/departures line up with the quiet dip in the audio?

    For each interior arrival (dwell start) and departure (dwell end): the offset (s) of the
    minimum of the 3-s smoothed level within +-win of the event, and the level step
    (moving side minus dwell side, dB). Expect offsets near 0 and positive steps.
    """
    tc = df.t_audio.to_numpy() + 0.5
    la = np.convolve(df.LAeq_1s.to_numpy(), np.ones(3) / 3, "same")
    dw = run_seg["dwells"]
    out = []
    for i, d in enumerate(dw):
        for kind, key, first_last in (("arrival", "t0", i == 0), ("departure", "t1", i == len(dw) - 1)):
            if first_last and d["truncated"]:
                continue                               # recording starts/ends inside this dwell
            a = float(sensor_to_audio(run, [d[key]])[0])
            if not (tc[0] + win <= a <= tc[-1] - win):
                continue
            w = np.abs(tc - a) <= win
            off = float(tc[w][np.argmin(la[w])] - a)
            sgn = -1 if kind == "arrival" else 1      # moving side: before arrival, after departure
            moving = (tc - a) * sgn
            step = energy_mean(df.LAeq_1s[(moving > 3) & (moving <= 20)]) - \
                energy_mean(df.LAeq_1s[(moving < -1) & (moving >= -10)])
            out.append({"stem": run.stem, "station": d["station"], "event": kind,
                        "edge_inferred": d["edge_inferred"], "min_offset_s": off, "step_db": float(step)})
    return out


def build() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """All section-runs, edge-check events and per-run audio context (moving vs dwell)."""
    S, Q = _json("sections.json"), _json("qc.json")
    sync = load_params()
    rows, edges, ctx = [], [], {}
    for r in list_runs():
        run = load_run(r)
        df = levels(run)
        secs = [s for s in S["sections"] if s["stem"] == run.stem]
        seg = next(x for x in S["runs"] if x["stem"] == run.stem)
        rows += section_rows(run, df, secs, Q["runs"][run.stem])
        edges += edge_check(run, df, seg)
        tc = df.t_audio.to_numpy() + 0.5
        mov = np.zeros(len(tc), bool)
        for s in secs:
            a0, a1 = sensor_to_audio(run, [s["depart_t"], s["arrive_t"]])
            mov |= (tc >= a0) & (tc < a1)
        dwm = np.zeros(len(tc), bool)
        for d in seg["dwells"]:
            if not d["truncated"]:
                a0, a1 = sensor_to_audio(run, [d["t0"], d["t1"]])
                dwm |= (tc >= a0) & (tc < a1)
        ctx[run.stem] = {
            "sync_method": sync[run.stem].get("method"), "sync_confidence": sync[run.stem].get("confidence"),
            "LAeq_moving": energy_mean(df.LAeq_1s[mov]),
            "LAeq_interior_dwell": energy_mean(df.LAeq_1s[dwm]) if dwm.any() else None,
            "n_s_interior_dwell": int(dwm.sum()),
        }
        if dwm.any():
            ctx[run.stem]["moving_minus_dwell_db"] = ctx[run.stem]["LAeq_moving"] - ctx[run.stem]["LAeq_interior_dwell"]
    tab = pd.DataFrame(rows)
    trips = trip_ids()
    tab.insert(1, "trip", tab.stem.map(trips))
    tab["track"] = [TRACK.get(frozenset({a, b}), f"{a}-{b}") for a, b in zip(tab["from"], tab["to"])]
    # Run-relative level: section LAeq minus the energy mean of all running seconds on the same trip.
    for trip, g in tab.groupby("trip"):
        ref = energy_mean(np.concatenate([np.asarray(x) for x in g.series_LAeq_1s]))
        tab.loc[g.index, "LAeq_rel_trip"] = g.LAeq - ref
    ev = pd.DataFrame(edges)
    for stem, g in ev.groupby("stem"):
        ctx[stem]["edge_min_offset_s_median"] = float(g.min_offset_s.median())
        ctx[stem]["edge_step_db_median"] = float(g.step_db.median())
        ctx[stem]["edge_min_offset_s_range"] = [float(g.min_offset_s.min()), float(g.min_offset_s.max())]
        # an offset at the +-10 s window edge means no dip was found near the event
        ctx[stem]["edge_window_hits"] = int((g.min_offset_s.abs() > 9.5).sum())
        ctx[stem]["edge_offset_suspect"] = bool(abs(g.min_offset_s.median()) > 5)
        ctx[stem]["dwell_not_quieter"] = bool(g.step_db.median() < 1)
    return tab, ev, ctx


def aggregate(tab: pd.DataFrame, col: str = "LAeq") -> pd.DataFrame:
    """Per section+direction mean/sd/n over placement-included runs; excluded runs listed apart."""
    out = []
    for (d, sec), g in tab.groupby(["direction", "section"], sort=False):
        inc, exc = g[~g.placement_excluded], g[g.placement_excluded]
        out.append({
            "direction": d, "section": sec, "track": g.track.iloc[0], "n": len(inc),
            "mean": float(inc[col].mean()), "sd": float(inc[col].std(ddof=1)) if len(inc) > 1 else np.nan,
            "runs": ",".join(inc.run_id),
            "excluded": ",".join(f"{r}:{v:.2f}" for r, v in zip(exc.run_id, exc[col])),
        })
    return pd.DataFrame(out)


def paired(tab: pd.DataFrame, cols=("LAeq", "nospeech_LAeq", "core_LAeq", "top25_LAeq", "L10",
                                     "LAeq_1s_mean", "LAeq_limiter_ub", "limited_frac")) -> dict:
    """Within-trip differences (LST-BTH section minus BTH-MLE section), per direction and pooled."""
    diffs = []
    for trip, g in tab.groupby("trip"):
        d = g.direction.iloc[0]
        if d not in PAIRS:
            continue
        (h0, h1), (c0, c1) = PAIRS[d]
        hot = g[(g["from"] == h0) & (g["to"] == h1)]
        cmp_ = g[(g["from"] == c0) & (g["to"] == c1)]
        if hot.empty or cmp_.empty:
            continue
        h, c = hot.iloc[0], cmp_.iloc[0]
        diffs.append({"trip": trip, "direction": d, "same_file": h.stem == c.stem,
                      "placement_excluded": bool(h.placement_excluded or c.placement_excluded),
                      **{f"diff_{k}": float(h[k] - c[k]) for k in cols}})
    D = pd.DataFrame(diffs)
    summ = {}
    for label, sel in (("all", D), ("placement_ok", D[~D.placement_excluded]),
                       ("same_file_placement_ok", D[D.same_file & ~D.placement_excluded])):
        s = {"n": len(sel), "trips": sel.trip.tolist()}
        for k in cols:
            v = sel[f"diff_{k}"].to_numpy()
            s[k] = {"mean_db": float(v.mean()), "sd_db": float(v.std(ddof=1)) if len(v) > 1 else None,
                    "min_db": float(v.min()), "max_db": float(v.max()), "n_positive": int((v > 0).sum()),
                    # one-sided sign test: P(all this many positive | no difference)
                    "sign_test_p_one_sided": float(stats.binomtest(int((v > 0).sum()), len(v), 0.5,
                                                                  alternative="greater").pvalue)}
        summ[label] = s
    return {"per_trip": D.to_dict("records"), "summary": summ}


def ranking(tab: pd.DataFrame) -> list[dict]:
    """All section-runs ranked loudest first by LAeq (cross-run; weak) with the within-trip relative level."""
    cols = ["run_id", "trip", "direction", "section", "LAeq", "LAeq_rel_trip", "L10", "LAFmax_max",
            "placement_excluded"]
    r = tab.sort_values("LAeq", ascending=False)[cols].reset_index(drop=True)
    r.insert(0, "rank", np.arange(1, len(r) + 1))
    return r.round(2).to_dict("records")
