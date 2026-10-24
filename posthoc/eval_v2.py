"""Leave-one-run-out evaluation of tracker v2 (WTT metres + stop gate) against the frozen v1 tracker.

Post-hoc, declared after prereg-24oct, 23 Oct dev data only. Any run outside the 23 Oct set (R7+)
is ignored even if it appears in outputs/sections.json.

Same splits and truths as scripts/online_eval.py (its truths() and summary() are imported): for each
track with >= 2 clean runs, each clean run is held out, the map is built from the other clean runs
(online.build_template), and the held-out run's causal features are replayed from its causally
detected departure to its arrival. Scored against the independent truth (curve-edge interpolation,
no DTW) and the proxy truth (offline DTW). Reported per run and per track:
- v1 (frozen online.Tracker) in chord metres, as in outputs/online/online_eval.json (a reproduction
  check), and rescaled to TfL Working Timetable metres (x WTT/chord, posthoc.lengths) so v1 and v2
  are on the same axis;
- v2 (posthoc.tracker_v2: WTT map + stop gate);
- the timetable baseline (time since departure / the map's mean section time), WTT metres;
- 90 % band coverage of the independent truth and the mean band width (WTT metres).
Regression check: per-run deltas v2 - v1 (WTT metres) on the real runs (no tunnel stops on 23 Oct),
and every step where the gate engaged inside a section, with its cue values. Gate detection check:
the gate alone over each whole recording from its first departure: share of the interior of real
platform dwells it flags, and how often it flags while the train runs.
Synthetic stop test: a 30 s stop inserted at 0.3 and 0.6 of each held-out section (the truth freezes,
features after it are shifted by 30 s), in three variants:
- "ideal": constant features as the scratch stop_check.py (yaw 0, h 0, gyr 0, vib and body -3).
  Far quieter than any real standstill (real dwells sit at vib ~-1.6, body ~-2): an upper bound;
- "quiet": the same constant features with vib and body at the run's own median dwell level;
- "dwell": the run's own longest platform dwell (interior from 2 s after the dwell starts to
  DWELL_TRIM_END_S before it ends, so the replay holds no departure jolt; tiled to 30 s): real
  standstill features with passengers, doors and the odd tap. The hardest and most realistic.
Quote these together (attempt D: median end-of-stop error 11 m ideal, 23 m quiet, 165 m on a
replayed real dwell, vs ~336 m for v1), never the ideal number alone.
Per case: error at the end of the stop, max error during it, recovery time (until the error stays
< 50 m to arrival; null = never), median error over the 30 s after, 90 % coverage during and after,
and for v2 how the gate released during the stop (an h jump inside a replayed dwell releases it while
the truth is still frozen). stop_summary lists the cases where v2 ends the stop further off than v1.
The synthetic stop starts and ends instantly (no braking, no re-acceleration), so it does not test
the lag while a train pulls away from a tunnel stop below the map's speed profile.
Stress rows: excluded sections with both ends (R4, floor placement / a phyphox pause) against the
clean map, as online_eval's stress test.

Writes outputs_posthoc/eval_v2.json, outputs_posthoc/eval_v2.png and appends one line per run to
outputs_posthoc/attempts_manual.jsonl (--no-log skips it; TUBESENSE_POSTHOC_OUT=<dir> redirects all
outputs, e.g. for a reviewer's re-run). Writes no bytecode (scripts/ and tubesense/ are frozen).
Run (repo root): PYTHONPATH=. .venv/Scripts/python -m posthoc.eval_v2 [--config '{"rewind": true}'] [--note "..."]
                 [--no-write] [--no-log] [--no-stops]
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True   # importing scripts/online_eval must not write into the frozen scripts/

import argparse
import gc
import json
import os
import pathlib
import time
from datetime import datetime, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tubesense import features as F
from tubesense import online as O
from tubesense.ingest import ROOT, load_run

sys.path.insert(0, str(ROOT / "scripts"))
import online_eval as E  # noqa: E402  (frozen v1 harness: truths, summary)

from . import lengths as LN  # noqa: E402
from . import tracker_v2 as V2  # noqa: E402

OUT = pathlib.Path(os.environ.get("TUBESENSE_POSTHOC_OUT", ROOT / "outputs_posthoc"))
DEV_RUNS = {"R0", "R0.5", "R1", "R1.5", "R2", "R3", "R4", "R5", "R6"}   # 23 Oct; nothing else is loaded
STOP_FRACS = (0.3, 0.6)
STOP_S = 30.0
DWELL_TRIM_END_S = 8.0        # the dwell replay ends this long before the dwell does (no departure jolt)
VARIANTS = ("ideal", "quiet", "dwell")
RECOVER_M = E.LOCK_ERR_M      # 50 m
IDEAL = {"t": np.nan, "valid": True, "yaw": 0.0, "h": 0.0, "gyr": 0.0, "vib": -3.0, "body": -3.0}
DOC = "post-hoc, declared after prereg-24oct, 23 Oct dev data only"


# --------------------------------------------------------------------------- data (one track at a time)
def track_sections() -> tuple[dict, dict]:
    """(secs, {track: [section dicts]}) with online.section_runs' selection rules, 23 Oct runs only."""
    secs = json.load(open(ROOT / "outputs" / "sections.json"))
    meta = json.load(open(ROOT / "outputs" / "gonogo" / "repeatability.json"))["sections"]
    use = [x for x in secs["sections"] if x["run_id"] not in O.EXCLUDE_RUNS and x["run_id"] in DEV_RUNS]
    n = {}
    for x in use:
        n[(x["direction"], x["from"], x["to"])] = n.get((x["direction"], x["from"], x["to"]), 0) + 1
    out = {}
    for x in use:
        trk = (x["direction"], x["from"], x["to"])
        key = f"{x['run_id']}:{x['from']}-{x['to']}"
        if n[trk] >= 2 and key in meta:
            out.setdefault(O.track_name(*trk), []).append({**x, "_key": key, "_meta": meta[key]})
    return secs, out


def load_track(secs: dict, items: list[dict]) -> list[O.SectionRun]:
    """SectionRuns of one track (clean or not), built exactly as online.section_runs, plus the gate cue."""
    out, cache = [], {}
    for sec in items:
        stem = sec["stem"]
        if stem not in cache:
            run = load_run(stem)
            dw = next(r for r in secs["runs"] if r["stem"] == stem)["dwells"]
            bias = F.gyro_bias(run, [(d["t0"], d["t1"]) for d in dw])
            cache[stem] = (run, bias, V2.gate_features(run, O.stream_features(run, bias)))
        run, bias, feat = cache[stem]
        prev = [x["arrive_wall"] for x in secs["sections"] if x["stem"] == stem and x["arrive_wall"] <= sec["depart_wall"]]
        clean = {k: v for k, v in sec.items() if not k.startswith("_")}
        sr = O.section_run(sec["_key"], run, clean, bias, feat, max(prev) if prev else None)
        sr.clean = bool(sec["_meta"]["clean"])
        sr.truncated = any(f["type"] == "section_truncated" for f in sec["_meta"]["flags"])
        out.append(sr)
    return out


def dwell_filler(secs: dict, sr: O.SectionRun) -> tuple[pd.DataFrame, dict]:
    """Interior (wall0 + 2 s .. wall1 - DWELL_TRIM_END_S) of the recording's longest platform dwell."""
    dw = next(r for r in secs["runs"] if r["stem"] == sr.sec["stem"])["dwells"]
    d = max((x for x in dw if not x["virtual"]), key=lambda x: x["duration_s"])
    f = sr.feat
    rows = f[(f.wall >= d["wall0"] + 2) & (f.wall <= d["wall1"] - DWELL_TRIM_END_S) & f.valid]
    return rows, {"station": d["station"], "duration_s": d["duration_s"], "truncated": d["truncated"],
                  "steps": len(rows), "handled_steps": int((rows.gyr > O.HANDLED).sum()),
                  "vib_median": round(float(rows.vib.median()), 3), "body_median": round(float(rows.body.median()), 3)}


# --------------------------------------------------------------------------- scoring
def score(est: pd.DataFrame, tpl: O.Template, tp: np.ndarray, ti: np.ndarray, onset: float) -> dict:
    m = tpl.metres
    tt = np.clip((est.wall.values - onset) / tpl.duration_s, 0, 1)
    err = lambda f, tr: np.abs(m(f) - m(tr))
    inside = lambda tr: float(((tr >= est.q05.values - 1e-9) & (tr <= est.q95.values + 1e-9)).mean())
    w = m(est.q95.values) - m(est.q05.values)
    return {"map_vs_independent_m": E.summary(err(est["map"].values, ti)),
            "map_vs_proxy_m": E.summary(err(est["map"].values, tp)),
            "mean_vs_independent_m": E.summary(err(est.frac.values, ti)),
            "timetable_vs_independent_m": E.summary(err(tt, ti)),
            "band90_coverage_independent": round(inside(ti), 3),
            "band90_coverage_proxy": round(inside(tp), 3),
            "band90_width_m": {"mean": round(float(w.mean()), 1), "median": round(float(np.median(w)), 1)},
            "_err": err(est["map"].values, ti)}


def gate_events(est: pd.DataFrame, a: O.SectionRun, tpl: O.Template, ti: np.ndarray) -> list[dict]:
    """Contiguous engaged stretches inside the section, with where and the cue values (why)."""
    g = est.gate.values.astype(int)
    d = np.diff(np.r_[0, g, 0])
    f = a.feat.set_index("wall")
    out = []
    for i0, i1 in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
        w0 = est.wall.values[i0]
        c = f.loc[est.wall.values[i0:i1]]
        out.append({"t_from_departure_s": round(float(w0 - a.sec["depart_wall"]), 2),
                    "t_to_arrival_s": round(float(a.sec["arrive_wall"] - w0), 2),
                    "duration_s": round((i1 - i0) * O.STEP, 2), "truth_m": round(float(tpl.metres(ti[i0])), 1),
                    "gate_z_at_engage": round(float(est.gate_z.values[i0]), 3),
                    "vib_median": round(float(c.vib.median()), 3), "body_median": round(float(c.body.median()), 3),
                    "h_median": round(float(c.h.median()), 3)})
    return out


def gate_detection(secs: dict, srs: list[O.SectionRun], prm: V2.GateParams) -> dict:
    """The gate alone over each recording from its first departure: dwell recall, running false-alarm rate."""
    out = {}
    for sr in srs:
        stem = sr.sec["stem"]
        if stem in out:
            continue
        rs = [x for x in secs["sections"] if x["stem"] == stem]
        dw = next(r for r in secs["runs"] if r["stem"] == stem)["dwells"]
        f = sr.feat[sr.feat.wall >= min(x["depart_wall"] for x in rs)]
        gate = V2.StopGate(prm)
        eng = np.array([gate.update({"vib": r.vib, "body": r.body, "h": r.h}, bool(r.valid), r.gyr > O.HANDLED) < 1
                        for r in f.itertuples()])
        w = f.wall.values
        run_ = np.zeros(len(f), bool)
        for x in rs:
            run_ |= (w >= x["depart_wall"] + 5) & (w <= x["arrive_wall"] - 5)
        dwell = np.zeros(len(f), bool)
        for d in dw:
            if not d["virtual"] and d["wall0"] > w[0]:        # dwells after the first departure only
                dwell |= (w >= d["wall0"] + prm.engage_s + 2) & (w <= d["wall1"] - 2)
        out[stem] = {"dwell_steps": int(dwell.sum()),
                     "dwell_flagged": round(float(eng[dwell].mean()), 3) if dwell.any() else None,
                     "running_steps": int(run_.sum()), "running_flagged_steps": int(eng[run_].sum())}
    return out


# --------------------------------------------------------------------------- synthetic stop
def insert_stop(feat: pd.DataFrame, ts: float, filler: pd.DataFrame | dict) -> pd.DataFrame:
    """filler: a dict of constant features (ideal / quiet) or the dwell rows to tile."""
    pre, post = feat[feat.wall < ts], feat[feat.wall >= ts].copy()
    post["wall"] += STOP_S
    walls = np.arange(ts, ts + STOP_S, O.STEP)
    if isinstance(filler, dict):
        gap = pd.DataFrame({"wall": walls, **filler})
    else:
        gap = filler.iloc[np.resize(np.arange(len(filler)), len(walls))].copy()
        gap["wall"] = walls
    return pd.concat([pre, gap[feat.columns]], ignore_index=True).pipe(lambda x: pd.concat([x, post], ignore_index=True))


def stop_case(est: pd.DataFrame, tpl: O.Template, indep, ts: float) -> dict:
    w = est.wall.values
    wt = np.where(w < ts, w, np.where(w < ts + STOP_S, ts, w - STOP_S))   # truth frozen during the stop
    y = tpl.metres(indep(wt))
    x = tpl.metres(est["map"].values)
    err = np.abs(x - y)
    cov = (y >= tpl.metres(est.q05.values) - 1e-6) & (y <= tpl.metres(est.q95.values) + 1e-6)
    st, af = (w >= ts) & (w < ts + STOP_S), w >= ts + STOP_S
    a30 = af & (w < ts + STOP_S + 30)
    bad = np.flatnonzero(err[af] >= RECOVER_M)
    if not len(bad):
        rec = 0.0
    elif bad[-1] == af.sum() - 1:
        rec = None                                                           # still >= 50 m at arrival
    else:
        rec = round(float(w[af][bad[-1] + 1] - (ts + STOP_S)), 2)
    i_end = np.flatnonzero(st)[-1]
    out = {"err_end_of_stop_m": round(float(err[i_end]), 1),
           "signed_end_of_stop_m": round(float(x[i_end] - y[i_end]), 1),
           "err_max_during_stop_m": round(float(err[st].max()), 1),
           "recovery_s": rec, "left_s_at_recovery": None if rec is None else round(float(w[-1] - (ts + STOP_S) - rec), 1),
           "err_median_30s_after_m": round(float(np.median(err[a30])), 1),
           "err_median_after_m": round(float(np.median(err[af])), 1),
           "err_at_arrival_m": round(float(err[-1]), 1),
           "coverage_during_stop": round(float(cov[st].mean()), 3), "coverage_after_stop": round(float(cov[af].mean()), 3)}
    if "gate" in est:
        g = est.gate.values
        on = np.flatnonzero(g & st)
        out["gate_engaged_share_of_stop"] = round(float(g[st].mean()), 3)
        out["gate_engage_delay_s"] = round(float(w[on[0]] - ts), 2) if len(on) else None
        out["gate_engaged_steps_after_stop"] = int(g[af].sum())
        rel = est.gate_release.values[st]
        out["gate_released_during_stop"] = {k: int((rel == k).sum()) for k in ("loud", "jump", "invalid") if (rel == k).any()}
    return out


# --------------------------------------------------------------------------- per held-out run
def held_out(secs, trk: str, train: list[O.SectionRun], a: O.SectionRun, prm: V2.GateParams, stops: bool) -> tuple:
    tpl_c = O.build_template(trk, train, LN.chord_m(*LN.stations_of(trk)))   # v1 map (no noise: not needed here)
    tpl_w = V2.scale_template(tpl_c, LN.wtt_m(trk))
    base = next(s for s in train if s.key == tpl_c.base)
    proxy, indep, _ = E.truths(a, base)
    onset, arrive = a.sec["depart_wall"], a.sec["arrive_wall"]
    e1 = O.track(tpl_c, a.feat, onset, arrive)
    e2 = V2.track(tpl_w, a.feat, onset, arrive, prm=prm)
    tp, ti = proxy(e1.wall.values), indep(e1.wall.values)
    s1c, s1w, s2 = score(e1, tpl_c, tp, ti, onset), score(e1, tpl_w, tp, ti, onset), score(e2, tpl_w, tp, ti, onset)
    err1, err2 = s1w.pop("_err"), s2.pop("_err")
    s1c.pop("_err")
    row = {"held_out": a.key, "track": trk, "clean": a.clean, "map_runs": tpl_c.runs, "map_base": tpl_c.base,
           "departure_missed_online": bool(np.isnan(a.depart_shift_s)),
           "v1_chord": s1c, "v1_wtt": s1w, "v2": s2,
           "delta_v2_minus_v1_wtt": {
               "median_m": round(s2["map_vs_independent_m"]["median"] - s1w["map_vs_independent_m"]["median"], 2),
               "p90_m": round(s2["map_vs_independent_m"]["p90"] - s1w["map_vs_independent_m"]["p90"], 2),
               "coverage": round(s2["band90_coverage_independent"] - s1w["band90_coverage_independent"], 3),
               "max_abs_stepwise_diff_m": round(float(np.max(np.abs(tpl_w.metres(e2["map"].values) -
                                                                    tpl_w.metres(e1["map"].values)))), 2)},
           "gate_steps_in_section": int(e2.gate.sum()), "gate_events": gate_events(e2, a, tpl_w, ti)}
    traces = {"t": e1.wall.values - onset, "err_v1": err1, "err_v2": err2}
    if not stops:
        return row, traces, []
    fill, finfo = dwell_filler(secs, a)
    row["synthetic_stop_dwell_source"] = finfo
    quiet = {**IDEAL, "vib": finfo["vib_median"], "body": finfo["body_median"]}
    cases, ex = [], []
    for f0 in STOP_FRACS:
        ts = onset + f0 * (arrive - onset)
        for variant, filler in (("ideal", IDEAL), ("quiet", quiet), ("dwell", fill)):
            f2 = insert_stop(a.feat, ts, filler)
            c1 = O.track(tpl_w, f2, onset, arrive + STOP_S)
            c2 = V2.track(tpl_w, f2, onset, arrive + STOP_S, prm=prm)
            cases.append({"held_out": a.key, "track": trk, "stop_frac": f0, "variant": variant,
                          "v1": stop_case(c1, tpl_w, indep, ts), "v2": stop_case(c2, tpl_w, indep, ts)})
            if variant == "ideal" and f0 == 0.6:
                w = c1.wall.values
                wt = np.where(w < ts, w, np.where(w < ts + STOP_S, ts, w - STOP_S))
                y = tpl_w.metres(indep(wt))
                ex.append({"key": a.key, "t": w - onset, "ts": ts - onset, "truth": y,
                           "v1": tpl_w.metres(c1["map"].values), "v2": tpl_w.metres(c2["map"].values),
                           "v2_q05": tpl_w.metres(c2.q05.values), "v2_q95": tpl_w.metres(c2.q95.values)})
    row["synthetic_stops"] = cases
    return row, traces, ex


# --------------------------------------------------------------------------- aggregation, figure
def med_of_runs(rows, who, key):
    v = [r[who][key]["median"] for r in rows if r[who][key].get("n")]
    return round(float(np.median(v)), 1) if v else None


def stop_summary(cases: list[dict]) -> dict:
    out = {}
    for variant in VARIANTS:
        cs = [c for c in cases if c["variant"] == variant]
        worse = [c for c in cs if c["v2"]["err_end_of_stop_m"] > c["v1"]["err_end_of_stop_m"]]
        out[variant] = {
            "n_cases_v2_worse_end_of_stop": len(worse),
            "v2_worse_end_of_stop": [{"case": f"{c['held_out']}@{c['stop_frac']}",
                                      "v1_signed_m": c["v1"]["signed_end_of_stop_m"], "v2_signed_m": c["v2"]["signed_end_of_stop_m"],
                                      "v2_engage_delay_s": c["v2"]["gate_engage_delay_s"],
                                      "v2_released_during_stop": c["v2"]["gate_released_during_stop"]} for c in worse],
            "n_cases_v2_released_by_h_jump_during_stop": sum("jump" in c["v2"]["gate_released_during_stop"] for c in cs),
            "n_cases_v2_never_engaged": sum(c["v2"]["gate_engage_delay_s"] is None for c in cs)}
        for who in ("v1", "v2"):
            rec = [c[who]["recovery_s"] for c in cs]
            out[variant][who] = {
                "n_cases": len(cs),
                "err_end_of_stop_m_median": round(float(np.median([c[who]["err_end_of_stop_m"] for c in cs])), 1),
                "err_end_of_stop_m_max": round(float(np.max([c[who]["err_end_of_stop_m"] for c in cs])), 1),
                "never_recovered": int(sum(r is None for r in rec)),
                "recovered_only_in_last_10s": int(sum(c[who]["left_s_at_recovery"] is not None and
                                                      c[who]["left_s_at_recovery"] < 10 for c in cs)),
                "recovery_s_median_of_recovered": round(float(np.median([r for r in rec if r is not None])), 1)
                if any(r is not None for r in rec) else None,
                "err_median_30s_after_m_median": round(float(np.median([c[who]["err_median_30s_after_m"] for c in cs])), 1),
                "coverage_during_stop_mean": round(float(np.mean([c[who]["coverage_during_stop"] for c in cs])), 3),
                "coverage_after_stop_mean": round(float(np.mean([c[who]["coverage_after_stop"] for c in cs])), 3)}
    return out


def per_track(rows) -> dict:
    out = {}
    for trk in dict.fromkeys(r["track"] for r in rows):
        rs = [r for r in rows if r["track"] == trk]
        out[trk] = {"n_runs": len(rs), "wtt_m": LN.wtt_m(trk), "chord_m": LN.chord_m(*LN.stations_of(trk)),
                    **{f"{who}_{k}": (round(float(np.mean([r[who][k] for r in rs])), 3) if k.startswith("band90_cov")
                                      else round(float(np.mean([r[who][k]["mean"] for r in rs])), 1))
                       for who in ("v1_wtt", "v2") for k in ("band90_coverage_independent", "band90_width_m")},
                    **{f"{who}_median_err_m_median_of_runs": med_of_runs(rs, who, "map_vs_independent_m")
                       for who in ("v1_chord", "v1_wtt", "v2")},
                    "timetable_median_err_m_median_of_runs": med_of_runs(rs, "v2", "timetable_vs_independent_m")}
    return out


def plot(rows, examples, cases, path):
    fig, ax = plt.subplots(2, 2, figsize=(13, 8.5))
    x = np.arange(len(rows))
    lab = [r["held_out"] for r in rows]
    for k, (who, key, c, nm) in enumerate([("v1_wtt", "map_vs_independent_m", "tab:blue", "v1 (frozen), WTT metres"),
                                           ("v2", "map_vs_independent_m", "tab:red", "v2 (WTT + stop gate)"),
                                           ("v2", "timetable_vs_independent_m", "0.6", "timetable baseline")]):
        ax[0, 0].bar(x + (k - 1) * 0.27, [r[who][key]["median"] for r in rows], 0.27, color=c, label=nm)
        ax[0, 0].scatter(x + (k - 1) * 0.27, [r[who][key]["p90"] for r in rows], color="k", marker="_", s=50)
    ax[0, 0].set_yscale("log")
    ax[0, 0].set_xticks(x, lab, rotation=25, fontsize=8)
    ax[0, 0].set_ylabel("error vs independent truth (m)\nbar = median, tick = p90")
    ax[0, 0].set_title("Real 23 Oct runs, leave-one-run-out (no tunnel stops)", fontsize=9)
    ax[0, 0].legend(fontsize=7)
    for who, c, nm, st, ms in (("v1_wtt", "tab:blue", "v1", "s-", 9), ("v2", "tab:red", "v2", "o--", 5)):
        ax[0, 1].plot(x, [r[who]["band90_coverage_independent"] for r in rows], st, color=c, ms=ms, label=f"{nm} coverage")
    ax[0, 1].axhline(0.9, color="k", ls=":", lw=0.8)
    ax[0, 1].set_ylim(0.5, 1.02)
    ax[0, 1].set_xticks(x, lab, rotation=25, fontsize=8)
    ax[0, 1].set_ylabel("90 % band coverage")
    t2 = ax[0, 1].twinx()
    for who, c in (("v1_wtt", "tab:blue"), ("v2", "tab:red")):
        t2.bar(x + (0.15 if who == "v2" else -0.15), [r[who]["band90_width_m"]["mean"] for r in rows], 0.3, color=c, alpha=0.2)
    t2.set_ylabel("mean band width (m, bars)")
    ax[0, 1].legend(fontsize=7, loc="lower left")
    ax[0, 1].set_title("Calibration on the real runs (v2 on top of v1 where identical)", fontsize=9)
    for e in examples[:3]:
        ax[1, 0].plot(e["t"], e["truth"], "k--", lw=1)
        ax[1, 0].plot(e["t"], e["v1"], color="tab:blue", lw=1)
        ax[1, 0].plot(e["t"], e["v2"], color="tab:red", lw=1)
        ax[1, 0].fill_between(e["t"], e["v2_q05"], e["v2_q95"], color="tab:red", alpha=0.12)
        ax[1, 0].axvspan(e["ts"], e["ts"] + STOP_S, color="0.85", alpha=0.4)
    ax[1, 0].plot([], [], "k--", label="independent truth (frozen in the stop)")
    ax[1, 0].plot([], [], color="tab:blue", label="v1 MAP")
    ax[1, 0].plot([], [], color="tab:red", label="v2 MAP (+90 % band)")
    ax[1, 0].set_xlabel("time since departure (s)")
    ax[1, 0].set_ylabel("metres from departure (WTT)")
    ax[1, 0].set_title(f"Synthetic 30 s stop at 0.6 ('ideal'), {', '.join(e['key'] for e in examples[:3])}", fontsize=9)
    ax[1, 0].legend(fontsize=7)
    lbl = [f"{c['held_out']}@{c['stop_frac']}" for c in cases if c["variant"] == "ideal"]
    xx = np.arange(len(lbl))
    for k, (variant, who, c, nm) in enumerate([("ideal", "v1", "tab:blue", "v1 ideal"), ("ideal", "v2", "tab:red", "v2 ideal"),
                                               ("dwell", "v1", "lightsteelblue", "v1 dwell"), ("dwell", "v2", "salmon", "v2 dwell")]):
        ax[1, 1].bar(xx + (k - 1.5) * 0.2, [max(cc[who]["err_end_of_stop_m"], 0.5) for cc in cases if cc["variant"] == variant],
                     0.2, color=c, label=nm)
    ax[1, 1].axhline(RECOVER_M, color="k", ls=":", lw=0.8)
    ax[1, 1].set_yscale("log")
    ax[1, 1].set_xticks(xx, lbl, rotation=60, fontsize=7)
    ax[1, 1].set_ylabel("error at end of the stop (m)")
    ax[1, 1].set_title("Synthetic 30 s stop: error when the train pulls away", fontsize=9)
    ax[1, 1].legend(fontsize=7, ncol=2)
    for a_ in ax.ravel():
        a_.grid(alpha=0.3)
    fig.suptitle(f"Tracker v2 vs frozen v1. {DOC}", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


# --------------------------------------------------------------------------- main
def main(prm: V2.GateParams, note: str = "", write: bool = True, stops: bool = True, log: bool = True) -> dict:
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    secs, groups = track_sections()
    rows, stress, examples, gdet = [], [], [], {}
    for trk, items in groups.items():
        srs = load_track(secs, items)
        clean = [s for s in srs if s.clean]
        gdet.update(gate_detection(secs, srs, prm))
        if len(clean) >= 2:
            for a in clean:
                row, _, ex = held_out(secs, trk, [s for s in clean if s is not a], a, prm, stops)
                rows.append(row)
                examples += ex
                d = row["delta_v2_minus_v1_wtt"]
                print(f"{a.key:13s} v1 chord {row['v1_chord']['map_vs_independent_m']['median']:5.1f} | v1 WTT "
                      f"{row['v1_wtt']['map_vs_independent_m']['median']:5.1f} | v2 {row['v2']['map_vs_independent_m']['median']:5.1f}"
                      f" (d {d['median_m']:+.2f}, p90 d {d['p90_m']:+.2f}, max|d| {d['max_abs_stepwise_diff_m']}) | cov "
                      f"{row['v1_wtt']['band90_coverage_independent']:.3f}->{row['v2']['band90_coverage_independent']:.3f}"
                      f" | gate steps {row['gate_steps_in_section']}", flush=True)
                for c in row.get("synthetic_stops", []):
                    print(f"    stop {c['variant']:5s}@{c['stop_frac']}: end {c['v1']['err_end_of_stop_m']:6.1f} -> "
                          f"{c['v2']['err_end_of_stop_m']:6.1f} m | rec {c['v1']['recovery_s']} -> {c['v2']['recovery_s']} s"
                          f" | cov after {c['v1']['coverage_after_stop']:.2f} -> {c['v2']['coverage_after_stop']:.2f}"
                          f" | gate {c['v2']['gate_engaged_share_of_stop']:.2f} delay {c['v2']['gate_engage_delay_s']}", flush=True)
        for a in [s for s in srs if not s.clean and not s.truncated]:
            if clean:
                row, _, _ = held_out(secs, trk, clean, a, prm, stops=False)
                stress.append(row)
                print(f"STRESS {a.key}: v1 WTT {row['v1_wtt']['map_vs_independent_m']} | v2 {row['v2']['map_vs_independent_m']}"
                      f" | gate steps {row['gate_steps_in_section']}", flush=True)
        del srs, clean
        gc.collect()
    cases = [c for r in rows for c in r.get("synthetic_stops", [])]
    deltas = [r["delta_v2_minus_v1_wtt"] for r in rows]
    overall = {"n_runs": len(rows),
               **{f"{who}_map_vs_independent_m_median_of_runs": med_of_runs(rows, who, "map_vs_independent_m")
                  for who in ("v1_chord", "v1_wtt", "v2")},
               **{f"{who}_map_vs_proxy_m_median_of_runs": med_of_runs(rows, who, "map_vs_proxy_m") for who in ("v1_wtt", "v2")},
               "timetable_vs_independent_m_median_of_runs_wtt": med_of_runs(rows, "v2", "timetable_vs_independent_m"),
               **{f"{who}_band90_coverage_independent_mean": round(float(np.mean([r[who]["band90_coverage_independent"]
                                                                                  for r in rows])), 3) for who in ("v1_wtt", "v2")},
               **{f"{who}_band90_width_m_mean": round(float(np.mean([r[who]["band90_width_m"]["mean"] for r in rows])), 1)
                  for who in ("v1_wtt", "v2")},
               "regression": {"max_abs_delta_median_m": max(abs(d["median_m"]) for d in deltas),
                              "max_abs_delta_p90_m": max(abs(d["p90_m"]) for d in deltas),
                              "max_abs_delta_coverage": max(abs(d["coverage"]) for d in deltas),
                              "max_abs_stepwise_diff_m": max(d["max_abs_stepwise_diff_m"] for d in deltas),
                              "runs_with_gate_engaged_in_section": [r["held_out"] for r in rows if r["gate_steps_in_section"]]},
               "synthetic_stop": stop_summary(cases) if cases else None,
               "runtime_s": round(time.time() - t0, 1)}
    res = {"doc": DOC, "module_doc": __doc__, "note": note, "gate_params": prm.to_json(),
           "v1_settings": {k: getattr(O, k) for k in ("STEP", "P_SPEED", "GAMMA", "YAW_SD_FLOOR", "OUTLIER", "HANDLED",
                                                       "JUMP", "JUMP_MIN", "JUMP_WIN")},
           "lengths": {trk: LN.section_length(*LN.stations_of(trk)) for trk in groups},
           "v1_reference": "outputs/online/online_eval.json (v1_chord rows must reproduce its map_vs_independent_m)",
           "attempts_log": "outputs_posthoc/attempts_manual.jsonl (every configuration tried, with its metrics)",
           "overall": overall, "per_track": per_track(rows), "gate_detection": gdet, "runs": rows, "stress": stress}
    if write:
        json.dump(res, open(OUT / "eval_v2.json", "w"), indent=1, default=float)
        if examples:
            plot(rows, examples, cases, OUT / "eval_v2.png")
    if log:
        with open(OUT / "attempts_manual.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "doc": DOC, "note": note,
                                 "config": prm.to_json(), "metrics": {k: v for k, v in overall.items() if k != "runtime_s"},
                                 "gate_detection": gdet, "written_outputs": write}, default=float) + "\n")
    print("OVERALL", json.dumps(overall, default=float, indent=1))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="{}", help="JSON overrides of GateParams")
    ap.add_argument("--note", default="")
    ap.add_argument("--no-write", action="store_true", help="log the attempt only; leave eval_v2.json/png alone")
    ap.add_argument("--no-stops", action="store_true")
    ap.add_argument("--no-log", action="store_true", help="do not append to attempts_manual.jsonl")
    a = ap.parse_args()
    main(V2.GateParams(**json.loads(a.config)), a.note, not a.no_write, not a.no_stops, not a.no_log)
