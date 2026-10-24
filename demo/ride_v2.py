"""Demo beat 0:35-1:15: live REPLAY of a recorded ride through the post-hoc v2 tracker (or the frozen v1).

The dot moves along a noise-coloured strip map with its 90 % band; live noise is painted only when the
tracker is confident (the same rule as scripts/demo.py); a QC panel lists the automatic messy-input
flags (outputs/qc.json); with --synthetic-stop a 30 s stop is inserted into the replay so the audience
sees the v2 stop gate hold the dot (and, with --tracker v1, the frozen tracker run ahead).

    PYTHONPATH=. .venv/Scripts/python -m demo.ride_v2 R5 --track "EB LST->BTH"                  # window, real time
    PYTHONPATH=. .venv/Scripts/python -m demo.ride_v2 R5 --track "EB LST->BTH" --speed 4 --truth
    PYTHONPATH=. .venv/Scripts/python -m demo.ride_v2 R5 --track "EB LST->BTH" --synthetic-stop 0.6 --stop-variant dwell
    PYTHONPATH=. .venv/Scripts/python -m demo.ride_v2 R5 --track "EB LST->BTH" --synthetic-stop 0.6 --tracker v1
    PYTHONPATH=. .venv/Scripts/python -m demo.ride_v2 R5 --track "EB LST->BTH" --save C:/tmp/ride_v2.mp4 --speed 6
    PYTHONPATH=. .venv/Scripts/python -m demo.ride_v2 R5 --track "EB LST->BTH" --snapshots 20,70,90 --outdir C:/tmp/snaps
    .venv/Scripts/python demo/ride_v2.py R5 --track "EB BTH->MLE"        # script path works too (repo root on sys.path)

The departure detector is armed ARM_S before the section's offline departure (so a second leg needs no
--after); the replay ends POST_S after both the tracker and the recording have arrived.

Trackers (one per process; the module settings are global):
- v2 (default): posthoc.tracker_v2.GatedTracker on a map scaled to the TfL Working Timetable length,
  with the Argon-submitted outputs_posthoc/final_config.json applied as agent/_eval.py apply() does
  (GAMMA, YAW_SD_FLOOR, P_SPEED as tubesense.online settings; stop gate on). Post-hoc: chosen on 23 Oct data.
- v1: the frozen, pre-registered tubesense.online.Tracker with its own settings, metres chord-scaled.
Both use the same map (same runs, same fractions): v1's chord map, and for v2 that map rescaled to WTT
metres (posthoc.tracker_v2.scale_template). The map is built from the clean 23 Oct runs of the track
WITHOUT the ride shown (leave-one-out), one track loaded at a time (posthoc.eval_v2.load_track).
v2's paint rule uses PAINT_BAND_M x WTT/chord so it is the same rule in map fractions as v1's.

Synthetic stop (posthoc.eval_v2.insert_stop): 30 s of standstill features inserted at FRAC of the
section (offline section times from outputs/sections.json; the map's mean duration for a new ride);
features after it shift by 30 s, noise is blank during it (there is no audio for a stop that did not
happen). Variants as eval_v2: ideal (constant, far quieter than any real stop), quiet (constant at the
run's median dwell level), dwell (the run's own longest platform dwell replayed: the realistic one).
The stop is labelled SYNTHETIC on screen while it lasts. The curve-edge reference (--truth, on by
default with a stop) freezes during it. It is an offline yaw curve-edge interpolation, not ground truth.

Everything the tracker shows uses data up to that moment (online.stream_features and the gate cues are
causal; no gyro bias, as scripts/demo.py). The audio-sensor sync behind the noise overlay is a fixed
offset from the whole recording. Levels are relative dBFS(A). No audio is loaded if the cached 1-s levels
exist, none is written and none is played. Writes only to --save / --outdir. Nothing calls Gemini.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True   # scripts/ and tubesense/ are frozen: importing them must not write .pyc
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, so `python demo/ride_v2.py` works

import argparse
import importlib.util
import json
import re
import tempfile
import textwrap
import time
from types import SimpleNamespace

import matplotlib
import numpy as np
import pandas as pd

from tubesense import online as O
from tubesense import repeat as R
from tubesense.ingest import ROOT, load_run

# posthoc.eval_v2 and scripts/online_eval force matplotlib.use("Agg") on import; keep the live window possible
_use, matplotlib.use = matplotlib.use, (lambda *a, **k: None)
try:
    from agent import _eval as AE          # apply(): the harness's way of applying a config
    from posthoc import eval_v2 as EV      # track_sections, load_track, insert_stop, dwell_filler, IDEAL, STOP_S
    from posthoc import lengths as LN
    from posthoc import tracker_v2 as V2
finally:
    matplotlib.use = _use
E = EV.E                                   # scripts/online_eval (frozen): truths()


def _load_v1_demo():
    """scripts/demo.py under another name (this package is also called 'demo')."""
    spec = importlib.util.spec_from_file_location("demo_v1_frozen", ROOT / "scripts" / "demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D1 = _load_v1_demo()                       # frozen v1 replay: constants, STATION_NAMES, live_noise
FINAL = ROOT / "outputs_posthoc" / "final_config.json"
QC = ROOT / "outputs" / "qc.json"
LABEL = {"v2": "tracker v2 (post-hoc, chosen on 23 Oct data)", "v1": "tracker v1 (pre-registered)"}
WRAP = 50                                  # QC panel line width (chars)
TXT_EVERY = 0.5                            # s of wall clock between status-text redraws in the live window
POST_S = 4.0                               # s of replay shown after both the tracker and the recording arrived
MAX_POST = 15.0                            # ... or after the recorded arrival if the tracker has not arrived
ARM_S = 10.0                               # departure detector armed this long before the section's offline departure


# --------------------------------------------------------------------------- map (leave-one-out, one track)
def build_maps(track: str, run_id: str) -> tuple[O.Template, O.Template, O.SectionRun | None, O.SectionRun | None]:
    """(chord map, WTT map, the shown ride's SectionRun or None, the map's base SectionRun).

    Map runs: clean 23 Oct section runs of the track, without the shown ride."""
    secs, groups = EV.track_sections()
    items = groups.get(track)
    if not items:
        raise SystemExit(f"no 23 Oct runs for {track}; tracks: {sorted(groups)}")
    srs = EV.load_track(secs, items)
    train = [s for s in srs if s.clean and s.key.split(":")[0] != run_id]
    if not train:
        raise SystemExit(f"no other clean runs to build a leave-one-out map for {track}")
    shown = next((s for s in srs if s.key.split(":")[0] == run_id), None)
    noise = {s.key: ns for s in train if (ns := O.noise_series(s)) is not None}
    tpl_c = O.build_template(track, train, LN.chord_m(*LN.stations_of(track)), noise)
    base = next(s for s in train if s.key == tpl_c.base)
    print(f"map: {tpl_c.runs} (leave-one-out: {run_id} not in it), base {tpl_c.base}")
    return tpl_c, V2.scale_template(tpl_c, LN.wtt_m(track)), shown, base


# --------------------------------------------------------------------------- QC panel
def _span(fs: list[dict]) -> str:
    t0, t1 = min(f["t0"] for f in fs), max(f["t1"] for f in fs)
    return f"{t0:.0f}-{t1:.0f} s" if t1 - t0 >= 1 else f"{t0:.0f} s"


def flag_lines(rid: str, flags: list[dict], brief: bool) -> list[str]:
    """Human-readable QC lines; brief = only the flags that change what the pipeline does."""
    by = {}
    for f in flags:
        by.setdefault(f["type"], []).append(f)
    out = []
    if "floor_like_placement" in by:
        out.append(f"{rid}: phone on the floor detected ({_span(by['floor_like_placement'])}: level, isolated)"
                   " -> excluded from noise averages and maps")
    for f in by.get("pause", []):
        dur = f["evidence"].split(" for ")[1].split(" wall")[0] if " for " in f["evidence"] else "?"
        out.append(f"{rid}: phyphox PAUSE {dur} at {f['t0']:.0f} s -> no sensor steps; tracker coasts")
    for f in by.get("false_start", []):
        m = re.search(r"recorded ([\d.]+) s then paused ([\d.]+) s", f["evidence"])
        out.append(f"{rid}: false start (recorded {float(m[1]):.0f} s, paused {float(m[2]):.0f} s, restarted)"
                   if m else f"{rid}: false start ({f['evidence']})")
    for f in by.get("section_overlaps_pause", []):
        sec = f["evidence"].split(":")[0]
        out.append(f"{rid}: {sec} overlaps the pause -> not in maps")
    for f in by.get("section_truncated", []):
        out.append(f"{rid}: {f['evidence'].split(':')[0]} departure not recorded -> not scored")
    for f in by.get("split_run", []):
        out.append(f"{rid}: split run (alighted at BTH by mistake) -> joined as one LST->MLE trip")
    for f in by.get("slow_running", []):
        sec, rest = f["evidence"].split(" (")[0].split(" took ")
        out.append(f"{rid}: slow {sec} ({rest.split(' for ')[0]}; 'Minor Delays'?)")
    hand = by.get("handled_mid_ride", []) + by.get("handheld", [])
    if hand and not brief:
        out.append(f"{rid}: phone handled x{len(hand)} ({_span(hand)}) -> yaw ignored while handled")
    for f in by.get("atypical_coupling", []) if not brief else []:
        kind = f["evidence"].split(":")[0]
        out.append(f"{rid}: {kind} coupling {f['t0']:.0f}-{f['t1']:.0f} s -> flagged, kept")
    if not brief:
        mode = next((t.split("_")[1] for t in by if t in ("magnetometer_raw", "magnetometer_calibrated")), None)
        if mode:
            n = len(by.get("magnetometer_reset", []))
            out.append(f"{rid}: magnetometer {mode}" + (f", {n} reset{'s' * (n > 1)}" if n else "")
                       + " (not used for position)")
    return out


def qc_text(stem: str, run_id: str) -> str:
    try:
        qc = json.load(open(QC))["runs"]
    except (FileNotFoundError, KeyError):
        return "QC: outputs/qc.json not found"
    rid = lambda s: s.split("_")[0]
    lines = ["MESSY-INPUT QC (automatic, outputs/qc.json)", "", f"this ride ({run_id}):"]
    mine = flag_lines(run_id, qc.get(stem, {}).get("flags", []), brief=False)
    lines += mine or [f"{run_id}: no flags"]
    lines += ["", "other 23 Oct rides:"]
    for s, r in qc.items():
        if s != stem and not s.startswith("R0_"):
            lines += flag_lines(rid(s), [f for f in r["flags"] if f["type"] != "split_run" or rid(s) == "R1"], brief=True)
    return "\n".join(w for ln in lines for w in (textwrap.wrap(ln, WRAP, subsequent_indent="   ") or [""]))


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="run id or file stem, e.g. R5")
    ap.add_argument("--track", required=True, help='e.g. "EB LST->BTH"')
    ap.add_argument("--tracker", choices=("v1", "v2"), default="v2")
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed factor")
    ap.add_argument("--clip", type=float, default=None, help="start this many s after departure, position unknown")
    ap.add_argument("--after", type=float, default=None,
                    help="arm the departure detector this many s into the recording "
                         f"(default: {ARM_S:.0f} s before the section's offline departure; 0 for a new ride)")
    ap.add_argument("--synthetic-stop", type=float, default=None, metavar="FRAC",
                    help="insert a 30 s stop at this fraction of the section (labelled SYNTHETIC on screen)")
    ap.add_argument("--stop-variant", choices=("ideal", "quiet", "dwell"), default="dwell")
    ap.add_argument("--truth", action="store_true", help="overlay the offline curve-edge reference (on with a stop)")
    ap.add_argument("--no-truth", action="store_true", help="no reference overlay, even with a stop")
    ap.add_argument("--save", default=None, help=".mp4 or .gif; renders instead of opening a window")
    ap.add_argument("--snapshots", default=None, help="comma-separated s since departure: save PNG frames")
    ap.add_argument("--outdir", default=None, help="folder for --snapshots (default: the system temp folder)")
    args = ap.parse_args()
    if args.save or args.snapshots:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    t_setup = time.perf_counter()
    v2 = args.tracker == "v2"
    cfg = json.load(open(FINAL))
    prm = AE.apply(cfg["config"]) if v2 else None        # sets online.GAMMA / YAW_SD_FLOOR / P_SPEED for v2 only
    run = load_run(args.run)
    tpl_c, tpl_w, shown, base = build_maps(args.track, run.run_id)
    tpl = tpl_w if v2 else tpl_c
    paint_band = D1.PAINT_BAND_M * (tpl.length_m / tpl_c.length_m)
    L, N = tpl.length_m, len(tpl.yaw_mu)
    a_st, b_st = args.track.split()[1].split("->")
    name = lambda s: D1.STATION_NAMES.get(s, s)

    feat = V2.gate_features(run, O.stream_features(run))  # causal; gate cue 'body' added (ignored by v1)
    if args.after is not None:
        arm = feat.wall.iloc[0] + args.after
    else:   # the track need not be the recording's first section (e.g. R5 "EB BTH->MLE")
        arm = max(feat.wall.iloc[0], shown.sec["depart_wall"] - ARM_S) if shown is not None else feat.wall.iloc[0]
    dep = O.departure_after(feat, arm)
    if dep is None:
        raise SystemExit("no departure detected after the detector was armed")
    onset = dep["wall_onset"]
    print(f"departure detected at +{dep['wall_detect'] - feat.wall.iloc[0]:.1f} s into the recording "
          f"(armed at +{arm - feat.wall.iloc[0]:.1f} s; onset back-dated {dep['wall_detect'] - onset:.2f} s)")
    if shown is not None and abs(onset - shown.sec["depart_wall"]) > ARM_S:
        print(f"WARNING: detected onset is {onset - shown.sec['depart_wall']:+.1f} s from {args.track}'s offline "
              "departure (late detection, or the wrong leg: --after sets when the detector is armed)")
    arrive = shown.sec["arrive_wall"] if shown is not None else onset + tpl.duration_s
    noise = D1.live_noise(run)

    # synthetic stop: features shifted, noise blank, reference frozen while it lasts
    stop = args.synthetic_stop
    ts = onset + stop * (arrive - onset) if stop is not None else np.inf
    if stop is not None:
        if args.stop_variant == "ideal":
            filler = EV.IDEAL
        else:
            try:
                rows, finfo = EV.dwell_filler(json.load(open(ROOT / "outputs" / "sections.json")),
                                              SimpleNamespace(sec={"stem": run.stem}, feat=feat))
            except (StopIteration, ValueError):
                raise SystemExit(f"no recorded platform dwell for {run.stem}: use --stop-variant ideal")
            filler = rows if args.stop_variant == "dwell" else {**EV.IDEAL, "vib": finfo["vib_median"],
                                                                 "body": finfo["body_median"]}
            print(f"stop filler: {args.stop_variant} (dwell at {finfo['station']}, vib {finfo['vib_median']}, "
                  f"body {finfo['body_median']})")
        feat = EV.insert_stop(feat, ts, filler)
        print(f"SYNTHETIC stop ({args.stop_variant}): {ts - onset:.1f}-{ts - onset + EV.STOP_S:.1f} s after departure")
    t_stop = EV.STOP_S if stop is not None else 0.0
    orig = lambda w: w if w < ts else (np.nan if w < ts + t_stop else w - t_stop)   # replay wall -> recording wall

    truth = None
    if (args.truth or stop is not None) and not args.no_truth and shown is not None:
        _, indep, edges = E.truths(shown, base)
        d0, d1 = shown.sec["depart_wall"], shown.sec["arrive_wall"]

        def truth(w):  # frozen during the stop; NaN outside the section
            wt = ts if ts <= w < ts + t_stop else orig(w)
            return float(indep(wt)) if d0 <= wt <= d1 else np.nan
        print(f"curve-edge reference: {len(edges)} matched curve edges (offline, not ground truth)")

    clip = args.clip is not None
    w_start = onset + (args.clip if clip else -D1.PRE_S)
    arr_r = arrive + (t_stop if ts < arrive else 0.0)            # recorded arrival on the replay clock
    w_end = min(feat.wall.iloc[-1], onset + 1.3 * tpl.duration_s + 10 + t_stop, arr_r + MAX_POST)
    steps = feat[(feat.wall >= w_start) & (feat.wall <= w_end)].reset_index(drop=True)
    tracker = V2.GatedTracker(tpl, "unknown" if clip else "depart", prm) if v2 else O.Tracker(tpl, "unknown" if clip else "depart")
    state = {"started": clip, "arrived": False, "arrived_wall": None, "est": None, "trail": [], "yaw": [],
             "mapyaw": [], "last_noise": None, "held": 0, "t_track": 0.0}

    def finished(i: int) -> bool:  # tracker and recording both arrived POST_S ago (the steps end at arr_r + MAX_POST)
        aw = state["arrived_wall"]
        return aw is not None and steps.wall.iloc[i] >= max(aw, arr_r) + POST_S

    def tstep(b) -> dict:
        t0 = time.perf_counter()
        if v2:
            e = tracker.step(b.yaw, bool(b.valid), b.gyr > O.HANDLED, {"vib": b.vib, "body": b.body, "h": b.h})
        else:
            e = tracker.step(b.yaw, bool(b.valid), b.gyr > O.HANDLED)
        state["t_track"] += time.perf_counter() - t0
        return e

    # ---- figure (scripts/demo.py's visual language, plus the QC panel, gate badge and stop banner)
    fig = plt.figure(figsize=(14, 7.6), facecolor="#111")
    gs = fig.add_gridspec(3, 3, height_ratios=[1.25, 1, 0.9], width_ratios=[2.25, 1.1, 1.5], hspace=0.45, wspace=0.08,
                          left=0.05, right=0.985, top=0.90, bottom=0.07)
    ax_map, ax_yaw = fig.add_subplot(gs[0, :]), fig.add_subplot(gs[1:, 0])
    ax_txt, ax_qc = fig.add_subplot(gs[1:, 1]), fig.add_subplot(gs[1:, 2])
    for ax in (ax_map, ax_yaw, ax_txt, ax_qc):
        ax.set_facecolor("#111")
        ax.tick_params(colors="0.8")
        for s in ax.spines.values():
            s.set_color("0.4")
    finite = tpl.noise_db[np.isfinite(tpl.noise_db)]
    vmin, vmax = np.percentile(finite, 5), np.percentile(finite, 99)
    cmap = plt.get_cmap("inferno")
    nb = len(tpl.noise_db)
    im = ax_map.pcolormesh(tpl.metres(np.linspace(0, 1, nb + 1)), [-0.25, 0.25], tpl.noise_db[None, :],
                           cmap=cmap, vmin=vmin, vmax=vmax)
    curve = np.abs(tpl.yaw_mu) > 0.6
    for i0, i1 in zip(*[np.flatnonzero(np.diff(np.r_[0, curve.astype(int), 0]) == s) for s in (1, -1)]):
        x0, x1 = tpl.metres(R.GRID[i0]), tpl.metres(R.GRID[min(i1, N - 1)])
        ax_map.add_patch(plt.Rectangle((x0, -0.42), x1 - x0, 0.1, color="0.55", lw=0))
    ax_map.text(0, -0.62, "curves", color="0.6", fontsize=8, va="center")
    ax_map.set_xlim(-0.03 * L, 1.03 * L)
    ax_map.set_ylim(-0.75, 0.95)
    ax_map.set_yticks([])
    if v2:
        xl = (f"metres from {name(a_st)}, TfL Working Timetable length {L:,.0f} m "
              f"(dead-reckoned profile scaled to it; TfL FOI WTT)")
    else:
        xl = (f"metres from {name(a_st)}, chord-scaled: straight-line length {L:,.0f} m "
              f"(dead-reckoned profile; v1 axis)")
    ax_map.set_xlabel(xl, color="0.8")
    ax_map.text(0, 0.78, name(a_st), color="w", ha="left", fontsize=11, weight="bold")
    ax_map.text(L, 0.78, name(b_st), color="w", ha="right", fontsize=11, weight="bold")
    cb = fig.colorbar(im, ax=ax_map, orientation="vertical", fraction=0.02, pad=0.01)
    cb.set_label("map LAeq\ndBFS(A), relative", color="0.8", fontsize=8)
    cb.ax.tick_params(colors="0.8", labelsize=7)
    band, = ax_map.plot([], [], color="#4fc3f7", lw=6, alpha=0.45, solid_capstyle="butt")
    try:
        qc_flags = json.load(open(QC))["runs"].get(run.stem, {}).get("flags", [])
    except (FileNotFoundError, KeyError):
        qc_flags = []
    live_only = run.run_id in O.EXCLUDE_RUNS or any(f["type"] == "floor_like_placement" for f in qc_flags)
    trail = ax_map.scatter([], [], c=[], cmap=cmap, vmin=vmin, vmax=vmax, s=36, marker="s", edgecolors="none",
                           alpha=0.45 if live_only else 1.0)   # faded: shown live, never averaged
    dot, = ax_map.plot([], [], "o", ms=16, mfc="#4fc3f7", mec="w", mew=2)
    tdot, = ax_map.plot([], [], "o", ms=20, mfc="none", mec="#66bb6a", mew=2)
    ax_map.text(L * 0.5, -0.62, "squares: noise this ride is measuring now, painted where the tracker puts it"
                + (" (LIVE ONLY: ride excluded from noise averages)" if live_only else "")
                + ("   |   hollow green: offline curve-edge reference" if truth else ""),
                color="0.6", fontsize=8, ha="center", va="center")
    banner = ax_map.text(L * 0.5, 0.56, "SYNTHETIC 30 s STOP (inserted for the demo)", color="#111", fontsize=16,
                         weight="bold", ha="center", va="center", visible=False,
                         bbox=dict(boxstyle="round,pad=0.35", fc="#ffd54f", ec="none"))
    badge = ax_map.text(0.995, 0.03, "", transform=ax_map.transAxes, ha="right", va="bottom", fontsize=11,  # inside: blit
                        weight="bold", family="monospace", color="w",
                        bbox=dict(boxstyle="round,pad=0.3", fc="0.25", ec="none"))
    title = fig.suptitle("", color="w", fontsize=13, x=0.05, y=0.985, ha="left")
    dev = run.run_id in EV.DEV_RUNS
    fig.text(0.05, 0.937, f"REPLAY of recorded {'23 Oct ' if dev else ''}ride {run.run_id}, stepped at {1 / O.STEP:.0f} Hz "
             f"with data up to each moment only  |  {LABEL[args.tracker]}"
             + (f", final_config {cfg['config_hash']}" if v2 else ""), color="#ffd54f" if v2 else "0.75", fontsize=9.5)
    fig.text(0.05, 0.914, (f"{run.run_id} is 23 Oct dev data: in-sample for the {'v2 config' if v2 else 'v1 settings'}; "
                           "the map is leave-one-out (built without it)") if dev else
             f"{run.run_id} is not in the 23 Oct dev data: out-of-sample for the tracker settings and the map",
             color="#ffd54f" if v2 else "0.75", fontsize=9)
    y_obs, = ax_yaw.plot([], [], color="#ffb74d", lw=1.4, label="phone yaw rate (live)")
    y_map, = ax_yaw.plot([], [], color="w", lw=1.0, alpha=0.8, label="map yaw at the estimated position")
    ax_yaw.set_ylim(-4, 4)
    ax_yaw.axhline(0, color="0.35", lw=0.8)
    ax_yaw.set_ylabel("deg/s  (+ = left)", color="0.8")
    ax_yaw.set_xlabel("seconds since departure", color="0.8")
    stop_marks = ()   # shading + label, shown once the stop starts (not announced ahead of it)
    if stop is not None:
        stop_marks = (ax_yaw.axvspan(ts - onset, ts - onset + t_stop, color="#ffd54f", alpha=0.15, lw=0, visible=False),
                      ax_yaw.text(ts - onset + t_stop / 2, 3.5, f"synthetic stop\n({args.stop_variant})",
                                  color="#ffd54f", fontsize=8, ha="center", va="top", visible=False))
    ax_yaw.legend(loc="lower left", fontsize=8, facecolor="#222", labelcolor="0.9", edgecolor="0.4")
    for ax in (ax_txt, ax_qc):
        ax.set_xticks([])
        ax.set_yticks([])
    txt = ax_txt.text(0.05, 0.96, "", color="w", fontsize=10, va="top", family="monospace", transform=ax_txt.transAxes)
    ax_txt.text(0.05, 0.04, "relative levels, uncalibrated\nno GPS: gyro curves + speed model", color="0.7",
                fontsize=9, va="bottom", family="monospace", transform=ax_txt.transAxes)
    ax_qc.text(0.03, 0.97, qc_text(run.stem, run.run_id), color="0.85", fontsize=7.6, va="top", family="monospace",
               transform=ax_qc.transAxes)
    title.set_text(f"Tubesense  |  {args.track.split()[0]}  {name(a_st)} -> {name(b_st)}  |  ride {run.run_id}"
                   + ("  (clip)" if clip else "") + (f"  |  + SYNTHETIC stop at {stop:.0%}" if stop is not None else ""))
    ax_yaw.set_xlim(steps.wall.iloc[0] - onset, steps.wall.iloc[-1] - onset)  # fixed axes so live display can blit

    def confident(e):  # scripts/demo.py's rule: lock and a narrow band, held HOLD steps
        ok = e["lock"] >= D1.LOCKED and (tpl.metres(e["q95"]) - tpl.metres(e["q05"])) < paint_band
        state["held"] = state["held"] + 1 if ok else 0
        return state["held"] >= D1.HOLD

    def update(i):
        r = steps.iloc[i]
        if not state["started"] and r.wall >= dep["wall_detect"]:
            # departure confirmed: start at the back-dated onset and catch up on the buffered steps (all past data)
            for b in steps[(steps.wall >= onset) & (steps.wall < r.wall)].itertuples():
                state["est"] = tstep(b)
            state["started"] = True
        if state["started"]:
            state["est"] = e = tstep(r)
            if e["map"] >= 0.995 and not state["arrived"]:
                state["arrived"], state["arrived_wall"] = True, r.wall
        t = r.wall - onset
        in_stop = ts <= r.wall < ts + t_stop
        state["yaw"].append((t, r.yaw if r.valid else np.nan))
        e = state["est"]
        pos = float(tpl.metres(e["map"])) if e else 0.0
        conf = confident(e) if e else False
        wo = orig(r.wall)
        if noise is not None:
            k = np.searchsorted(noise[0], wo) - 1 if np.isfinite(wo) else -1
            if k >= 0 and wo - noise[0][k] < 1.0:
                state["last_noise"] = noise[1][k]
                if conf and (not state["trail"] or state["trail"][-1][2] != k):
                    state["trail"].append((pos, noise[1][k], k))
            elif in_stop:
                state["last_noise"] = None
        if state["trail"]:
            tr = np.array([(p, v) for p, v, _ in state["trail"]])
            trail.set_offsets(np.column_stack([tr[:, 0], np.full(len(tr), 0.38)]))
            trail.set_array(tr[:, 1])
        dot.set_data([pos], [0])
        if e:
            band.set_data(tpl.metres([e["q05"], e["q95"]]), [0, 0])
        tv = truth(r.wall) if truth is not None else np.nan
        tdot.set_data(([tpl.metres(tv)], [0]) if np.isfinite(tv) else ([], []))
        yy = np.array(state["yaw"])
        y_obs.set_data(yy[:, 0], yy[:, 1])
        if e:
            state["mapyaw"].append((t, float(np.interp(e["map"], R.GRID, tpl.yaw_mu))))
            my = np.array(state["mapyaw"])
            y_map.set_data(my[:, 0], my[:, 1])
        banner.set_visible(in_stop)
        for a_ in stop_marks:
            a_.set_visible(r.wall >= ts)
        if not v2:
            badge.set_text("stop gate: none (v1)")
            badge.get_bbox_patch().set_facecolor("0.25")
        elif e and e.get("gate"):
            badge.set_text("stop gate: ENGAGED")
            badge.get_bbox_patch().set_facecolor("#c62828")
        else:
            warm = e is None or not np.isfinite(e.get("gate_z", np.nan))
            badge.set_text("stop gate: " + ("warming up" if warm else "off"))
            badge.get_bbox_patch().set_facecolor("0.25")
        status = ("ARRIVED" if state["arrived"] else "TRACKING" if state["started"] else "WAITING FOR DEPARTURE")
        if clip and e and e["lock"] < D1.LOCKED and not state["arrived"]:
            status = "SEARCHING: matching curves"
        if v2 and e and e.get("gate") and not state["arrived"]:
            status = "HOLDING: train stopped"
        pm = e.get("p_moving", 1.0) if e else 1.0
        speed = tpl.speed(e["map"], e["speed_ratio"]) * pm if e and state["started"] else 0.0
        mapdb = tpl.noise_db[min(int(e["map"] * nb), nb - 1)] if e else np.nan
        nz = state["last_noise"]
        lines = [status, "",
                 f"t {t:+7.1f} s since departure",
                 f"position {pos:7.0f} m" if e else "position       -",
                 f"  90%  {tpl.metres(e['q05']):5.0f}-{tpl.metres(e['q95']):5.0f} m" if e else "",
                 f"  tracker conf. {e['lock']:3.0%} in ±50{' WTT' if v2 else ''} m" if e else "",
                 f"speed  {speed * 3.6:6.0f} km/h" if e and state["started"] else "",
                 "",
                 "noise now  (no audio: synthetic)" if in_stop else
                 f"noise now  {nz:6.1f} dBFS(A)" if nz is not None else "noise now      -",
                 f"map here   {mapdb:6.1f} dBFS(A)" if e else "", ""]
        if v2:
            z = e.get("gate_z", np.nan) if e else np.nan
            lines.append("stop gate " + ("ENGAGED" if e and e.get("gate") else "off" if np.isfinite(z) else "warming up")
                         + (f" (z {z:+.2f})" if np.isfinite(z) else ""))
        elif in_stop:
            lines.append("v1: no zero-speed state")
        if in_stop and e:
            if v2 and e.get("gate") and state.get("engaged_at") is None:
                state["engaged_at"] = r.wall - ts
            state["stop_end"] = (pos - tpl.metres(tv)) if np.isfinite(tv) else np.nan
        if np.isfinite(tv) and e:
            lines.append(f"vs curve-edge ref: {abs(tpl.metres(tv) - pos):4.0f} m")
        txt.set_text("\n".join(lines))
        return dot, band, trail, tdot, banner, badge, y_obs, y_map, *stop_marks, txt

    print(f"setup {time.perf_counter() - t_setup:.1f} s; {len(steps)} steps")
    tag = f"{run.run_id}_{args.tracker}" + (f"_stop{stop:g}_{args.stop_variant}" if stop is not None else "")
    if args.snapshots:
        out = Path(args.outdir) if args.outdir else Path(tempfile.gettempdir()) / "tubesense_ride_v2"
        out.mkdir(parents=True, exist_ok=True)
        want = sorted(float(s) for s in args.snapshots.split(","))
        t0, n_draw, t_draw = time.perf_counter(), 0, 0.0
        for i in range(len(steps)):
            update(i)
            t = steps.wall.iloc[i] - onset
            while want and t >= want[0]:
                td = time.perf_counter()
                p = out / f"ride_v2_{tag}_{want.pop(0):.0f}s.png"
                fig.savefig(p, dpi=90, facecolor=fig.get_facecolor())
                t_draw += time.perf_counter() - td
                n_draw += 1
                print("saved", p)
        el = time.perf_counter() - t0
        print(f"replayed {len(steps)} steps ({len(steps) * O.STEP:.0f} s of ride) in {el:.1f} s; tracker "
              f"{1e3 * state['t_track'] / len(steps):.2f} ms/step; PNG {1e3 * t_draw / max(n_draw, 1):.0f} ms each")
        if stop is not None:
            ea = state.get("engaged_at")
            print(f"end of stop: dot {state.get('stop_end', np.nan):+.0f} m from the curve-edge reference; gate "
                  + (f"engaged {ea:.2f} s into the stop" if ea is not None else "never engaged" if v2 else "n/a (v1)"))
        return
    # the tracker sees every STEP; only every stride-th step is drawn (drawing is the slow part)
    stride = max(1, round(args.speed / (D1.FPS * O.STEP)))
    shown_i = list(range(0, len(steps), stride))
    fps = args.speed / (stride * O.STEP)
    done = {"next": 0, "artists": (), "t0": None, "lag": 0.0, "t_txt": -np.inf}
    dynamic = (dot, band, trail, tdot, banner, badge, y_obs, y_map, *stop_marks)   # moving artists but the status text
    for a_ in (*dynamic, txt):
        a_.set_animated(True)                      # blitted: drawn over a cached background of the static figure

    def advance(k):  # FuncAnimation may call a frame twice (init): only new steps reach the tracker
        for i in range(done["next"], shown_i[k] + 1):
            update(i)
        done["next"] = max(done["next"], shown_i[k] + 1)

    if args.save and args.save.endswith(".mp4"):
        # fast path: the static figure is drawn once, each frame only redraws the moving artists
        import imageio_ffmpeg
        t0 = time.perf_counter()
        fig.set_dpi(80)
        fig.canvas.draw()
        bg = fig.canvas.copy_from_bbox(fig.bbox)
        w, h = fig.canvas.get_width_height()
        fps_i = max(1, round(fps))
        wr = imageio_ffmpeg.write_frames(args.save, (w, h), fps=fps_i, macro_block_size=1, quality=7)
        wr.send(None)
        for k in range(len(shown_i)):
            advance(k)
            fig.canvas.restore_region(bg)
            for a_ in (*dynamic, txt):
                a_.axes.draw_artist(a_)
            wr.send(np.ascontiguousarray(np.asarray(fig.canvas.buffer_rgba())[:, :, :3]))
            if finished(shown_i[k]):
                break
        wr.close()
        print(f"saved {args.save} ({k + 1} frames at {fps_i} fps = {(k + 1) / fps_i:.1f} s, {w}x{h}) "
              f"in {time.perf_counter() - t0:.1f} s")
        return

    def frame(k):
        if done["t0"] is None:
            done["t0"] = time.perf_counter()
        advance(k)
        now = time.perf_counter()
        # lag = how far the drawn replay is behind the wall clock (replay seconds / speed)
        done["lag"] = (now - done["t0"]) - shown_i[k] * O.STEP / args.speed
        # the status text costs ~0.1 s to draw on this machine: refresh it at most every TXT_EVERY s of wall clock
        # (an axes not returned is neither cleared nor redrawn, so the last text stays on screen)
        done["stop"], done["k"] = finished(shown_i[k]), k
        if now - done["t_txt"] >= TXT_EVERY or k == len(shown_i) - 1 or done["stop"]:
            done["t_txt"] = now
            return (*dynamic, txt)
        return dynamic

    def frames():  # ends POST_S after arrival; the window stays open on the last frame
        k = 0
        while k < len(shown_i) and not done.get("stop"):
            yield k
            k += 1

    anim = FuncAnimation(fig, frame, frames=frames, interval=1000 / fps, blit=not args.save, repeat=False,
                         save_count=len(shown_i), cache_frame_data=False)
    if args.save:
        from matplotlib.animation import PillowWriter
        t0 = time.perf_counter()
        for a_ in (*dynamic, txt):
            a_.set_animated(False)
        anim.save(args.save, writer=PillowWriter(fps=max(1, round(fps))), dpi=80,
                  savefig_kwargs={"facecolor": fig.get_facecolor()})
        print(f"saved {args.save} ({len(shown_i)} frames) in {time.perf_counter() - t0:.1f} s")
    else:
        plt.show()
        print(f"live replay: {done.get('k', 0) + 1} frames ({(done.get('k', 0) + 1) / fps:.1f} s); "
              f"{done['lag']:+.1f} s behind the wall clock at the last drawn frame")

if __name__ == "__main__":
    main()
