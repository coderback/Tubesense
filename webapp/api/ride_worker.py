"""Web app ride worker: REPLAY of a recorded ride through the tracker, stepped by the wall clock, as JSON lines.

What: the per-step logic of demo/ride_v2.py without matplotlib drawing (its helpers are imported, not
copied). It emits one `init` event (map bins, curves, paint rule, QC lines, offline score), then `tick`
events (position, 90 % band, confidence, noise, gate, reference), then `end`, one JSON object per line on
stdout. Library prints go to stderr. Errors are `{"type": "error"}` plus exit code 1.
Why: the dot on the web page is real compute: the tracker (post-hoc v2 or frozen v1) sees only data up to each
moment, as in the CLI demo. Differences from the CLI, both from webapp/DESIGN.md section 7: the departure
detector is armed ARM_S before the section's departure for every ride (second legs work on 24 Oct rides), and
the curve-edge reference also works for 24 Oct rides (the SectionRun is built as posthoc.eval_v2.track_sections
does, without its 23 Oct filter). Levels are relative dBFS(A) from cached 1-s levels; no audio is written.
Pacing: step i is released at t_play0 + i * STEP / speed; ticks are thinned to ~16/s (4/s at 1x) except those
carrying a paint, a departure, a status change or a synthetic-stop edge. stdin (with --stdin-control):
`go` (after --hold), `speed N`, `pause`, `resume`; EOF while holding cancels.
Run (repo root):
  PYTHONPATH=. .venv/Scripts/python.exe -B -m webapp.api.ride_worker --run R9 --track "EB LST->BTH" --speed 8
  PYTHONPATH=. .venv/Scripts/python.exe -B -m webapp.api.ride_worker --run R9 --track "EB LST->BTH" --fast --out webapp/precomputed/R9_EB-LST-BTH_v2_8x.jsonl
Options: --tracker v1|v2, --stop FRAC --stop-variant ideal|quiet|dwell, --no-truth, --hold, --fast, --out FILE.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True      # tubesense/, scripts/ and posthoc/ are frozen: importing must not write .pyc
OUT = sys.stdout
sys.stdout = sys.stderr              # library prints (e.g. build_maps' "map: ...") must not corrupt the JSON lines

import argparse
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

SINK = {"fh": None}                  # --out file (fast mode) instead of stdout


def _plain(o):
    """JSON-safe copy: NaN/inf -> None, numpy scalars/arrays -> Python, floats rounded by the caller."""
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if hasattr(o, "tolist"):
        return _plain(o.tolist())
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    return o


def emit(obj: dict) -> None:
    fh = SINK["fh"] or OUT
    fh.write(json.dumps(_plain(obj), allow_nan=False) + "\n")
    fh.flush()


def r1(x):
    return None if x is None or not math.isfinite(x) else round(float(x), 1)


def r2(x):
    return None if x is None or not math.isfinite(x) else round(float(x), 2)


class Control:
    """stdin commands: go, speed N, pause, resume. Polled, never a blocking read: on Windows a thread blocked in a
    read of the inherited stdin makes the main thread's later extension-module imports (numpy) hang."""

    def __init__(self, speed: float):
        self.speed, self.paused, self.is_go, self.eof, self.buf = float(speed), False, False, False, b""

    def _available(self) -> bytes:
        fd = sys.stdin.fileno()
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes
            n = wintypes.DWORD()
            if not ctypes.windll.kernel32.PeekNamedPipe(msvcrt.get_osfhandle(fd), None, 0, None, ctypes.byref(n), None):
                if ctypes.GetLastError() == 109:        # ERROR_BROKEN_PIPE: the server went away
                    self.eof = True
                return b""
            return os.read(fd, n.value) if n.value else b""
        import select
        if not select.select([fd], [], [], 0)[0]:
            return b""
        data = os.read(fd, 4096)
        self.eof = self.eof or not data
        return data

    def poll(self) -> None:
        try:
            self.buf += self._available()
        except (OSError, ValueError):
            self.eof = True
        *lines, self.buf = self.buf.split(b"\n")
        for line in lines:
            w = line.decode("utf-8", "replace").split()
            if not w:
                continue
            if w[0] == "go":
                self.is_go = True
            elif w[0] == "speed" and len(w) > 1:
                try:
                    v = float(w[1])
                except ValueError:
                    continue
                if v > 0:
                    self.speed = v
            elif w[0] == "pause":
                self.paused = True
            elif w[0] == "resume":
                self.paused = False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True, help="run id, e.g. R9")
    ap.add_argument("--track", required=True, help='e.g. "EB LST->BTH"')
    ap.add_argument("--tracker", choices=("v1", "v2"), default="v2")
    ap.add_argument("--speed", type=float, default=8.0)
    ap.add_argument("--stop", type=float, default=None, help="synthetic 30 s stop at this fraction of the section")
    ap.add_argument("--stop-variant", choices=("ideal", "quiet", "dwell"), default="quiet")
    ap.add_argument("--no-truth", action="store_true")
    ap.add_argument("--hold", action="store_true", help="after setup wait for `go` on stdin")
    ap.add_argument("--stdin-control", action="store_true", help="read go/speed/pause/resume from stdin")
    ap.add_argument("--fast", action="store_true", help="no pacing, every step emitted (precomputed streams)")
    ap.add_argument("--out", default=None, help="write the JSON lines to this file (with --fast)")
    a = ap.parse_args()
    t_start = time.perf_counter()
    if a.fast and a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        SINK["fh"] = open(a.out, "w", encoding="utf-8")
        emit({"type": "meta", "label": "PRECOMPUTED", "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "argv": {"run": a.run, "track": a.track, "tracker": a.tracker, "speed": a.speed, "stop": a.stop,
                       "stop_variant": a.stop_variant, "truth": not a.no_truth}})
    ctl = Control(a.speed)

    def status(phase: str, message: str) -> None:
        emit({"type": "status", "phase": phase, "message": message, "elapsed_s": round(time.perf_counter() - t_start, 1)})

    try:
        run_worker(a, ctl, status, t_start)
    except SystemExit as e:
        emit({"type": "error", "message": str(e.code) if isinstance(e.code, str) else f"exit {e.code}"})
        sys.exit(1)
    except Exception as e:  # noqa: BLE001  (every failure is an event, never a fake ride)
        emit({"type": "error", "message": f"{type(e).__name__}: {e}"[:500]})
        sys.exit(1)


def run_worker(a, ctl: Control, status, t_start: float) -> None:
    import numpy as np

    status("loading", "Importing the tracker code and loading the run")
    import matplotlib
    matplotlib.use("Agg")
    from demo import ride_v2 as RV           # RV.EV, RV.E, RV.O, RV.D1, RV.V2, RV.AE, RV.LN, RV.R, RV.FINAL, RV.QC
    EV, E, O, D1, V2, AE, R = RV.EV, RV.E, RV.O, RV.D1, RV.V2, RV.AE, RV.R

    v2 = a.tracker == "v2"
    cfg = json.load(open(RV.FINAL))
    prm = AE.apply(cfg["config"]) if v2 else None
    run = RV.load_run(a.run)
    dev = run.run_id in EV.DEV_RUNS
    a_st, b_st = a.track.split()[1].split("->")
    key = f"{run.run_id}:{a_st}-{b_st}"

    status("map", "Building the leave-one-out map")
    tpl_c, tpl_w, shown, base = RV.build_maps(a.track, run.run_id)
    tpl = tpl_w if v2 else tpl_c
    paint_band = D1.PAINT_BAND_M * (tpl.length_m / tpl_c.length_m)
    L, N, nb = tpl.length_m, len(tpl.yaw_mu), len(tpl.noise_db)

    secs = json.load(open(RV.ROOT / "outputs" / "sections.json"))
    sec_row = next((x for x in secs["sections"] if x["run_id"] == run.run_id and x["from"] == a_st and x["to"] == b_st
                    and f"{x['direction']} {x['from']}->{x['to']}" == a.track), None)
    if sec_row is None:
        raise SystemExit(f"{a.run} has no {a.track} section in outputs/sections.json")
    truth_note = None
    if shown is None:       # 24 Oct ride: build its SectionRun as eval_v2.track_sections does, without the DEV_RUNS filter
        try:
            meta = json.load(open(RV.ROOT / "outputs" / "gonogo" / "repeatability.json"))["sections"][key]
            shown = EV.load_track(secs, [{**sec_row, "_key": key, "_meta": meta}])[0]
        except Exception as e:  # noqa: BLE001
            truth_note = f"no reference: {type(e).__name__}: {e}"[:200]
    sec = shown.sec if shown is not None else sec_row

    status("features", "Computing causal features and the noise levels")
    feat = V2.gate_features(run, O.stream_features(run))
    noise = D1.live_noise(run)

    status("departure", "Finding the departure")
    arm = max(feat.wall.iloc[0], sec["depart_wall"] - RV.ARM_S)
    dep = O.departure_after(feat, arm)
    if dep is None:
        raise SystemExit("no departure detected after the detector was armed")
    onset = dep["wall_onset"]
    warn = None
    if abs(onset - sec_row["depart_wall"]) > RV.ARM_S:
        warn = (f"detected onset is {onset - sec_row['depart_wall']:+.1f} s from the offline departure "
                "(late detection, or the wrong leg)")
    arrive = sec["arrive_wall"]

    stop = a.stop
    ts = onset + stop * (arrive - onset) if stop is not None else math.inf
    if stop is not None:
        if a.stop_variant == "ideal":
            filler = EV.IDEAL
        else:
            try:
                rows, finfo = EV.dwell_filler(secs, SimpleNamespace(sec={"stem": run.stem}, feat=feat))
            except (StopIteration, ValueError):
                raise SystemExit(f"no recorded platform dwell for {run.stem}: use stop variant ideal")
            filler = rows if a.stop_variant == "dwell" else {**EV.IDEAL, "vib": finfo["vib_median"], "body": finfo["body_median"]}
        feat = EV.insert_stop(feat, ts, filler)
    t_stop = EV.STOP_S if stop is not None else 0.0
    orig = lambda w: w if w < ts else (math.nan if w < ts + t_stop else w - t_stop)   # replay wall -> recording wall

    truth, edges = None, []
    if not a.no_truth:
        if shown is None:
            truth_note = truth_note or "no reference: the section could not be loaded"
        else:
            try:
                _, indep, edges = E.truths(shown, base)
                d0, d1 = sec["depart_wall"], sec["arrive_wall"]

                def truth(w):   # frozen during the stop; NaN outside the section
                    wt = ts if ts <= w < ts + t_stop else orig(w)
                    return float(indep(wt)) if d0 <= wt <= d1 else math.nan
            except Exception as e:  # noqa: BLE001
                truth, truth_note = None, f"no reference: {type(e).__name__}: {e}"[:200]

    w_start = onset - D1.PRE_S
    arr_r = arrive + (t_stop if ts < arrive else 0.0)
    w_end = min(feat.wall.iloc[-1], onset + 1.3 * tpl.duration_s + 10 + t_stop, arr_r + RV.MAX_POST)
    steps = feat[(feat.wall >= w_start) & (feat.wall <= w_end)].reset_index(drop=True)
    tracker = V2.GatedTracker(tpl, "depart", prm) if v2 else O.Tracker(tpl, "depart")
    wall, yaw, valid = steps.wall.to_numpy(), steps.yaw.to_numpy(), steps.valid.to_numpy().astype(bool)
    gyr, vib, body, hh = steps.gyr.to_numpy(), steps.vib.to_numpy(), steps.body.to_numpy(), steps.h.to_numpy()
    n_steps = len(steps)

    # ---- init event
    finite = tpl.noise_db[np.isfinite(tpl.noise_db)]
    vmin, vmax = float(np.percentile(finite, 5)), float(np.percentile(finite, 99))
    curve = np.abs(tpl.yaw_mu) > 0.6
    curves = []
    for i0, i1 in zip(*[np.flatnonzero(np.diff(np.r_[0, curve.astype(int), 0]) == s) for s in (1, -1)]):
        curves.append([r1(tpl.metres(R.GRID[i0])), r1(tpl.metres(R.GRID[min(i1, N - 1)]))])
    qc_all = json.load(open(RV.QC))["runs"] if Path(RV.QC).exists() else {}
    qc_flags = qc_all.get(run.stem, {}).get("flags", [])
    live_only = run.run_id in O.EXCLUDE_RUNS or any(f["type"] == "floor_like_placement" for f in qc_flags)
    qc = qc_block(RV, qc_all, run, qc_flags)
    name = lambda s: D1.STATION_NAMES.get(s, s)
    axis = (f"metres from {name(a_st)}, TfL Working Timetable length {L:,.0f} m (dead-reckoned profile scaled to it; TfL FOI WTT)"
            if v2 else f"metres from {name(a_st)}, chord-scaled: straight-line length {L:,.0f} m (dead-reckoned profile; v1 axis)")
    score = offline_score(RV, key, dev)
    from tubesense.stops import STATIONS as ST
    stations = {s: {"name": ST[s][0], "lat": ST[s][1], "lon": ST[s][2]} for s in ("LST", "BTH", "MLE")}
    note = (f"{run.run_id} is 23 Oct dev data: in-sample for the {'v2 config' if v2 else 'v1 settings'}; the map is "
            "leave-one-out (built without it)") if dev else \
        f"{run.run_id} is not in the 23 Oct dev data: out-of-sample for the tracker settings and the map"
    t_range = [r2(wall[0] - onset), r2(wall[-1] - onset)]
    init = {"type": "init", "run_id": run.run_id, "stem": run.stem, "track": a.track, "section": a.track,
            "from": a_st, "to": b_st, "from_name": name(a_st), "to_name": name(b_st), "day": "23 Oct" if dev else "24 Oct",
            "stations": stations, "tracker": a.tracker, "tracker_label": RV.LABEL[a.tracker],
            "config": cfg["config"] if v2 else None, "config_hash": cfg.get("config_hash") if v2 else None,
            "length_m": r1(L), "length_chord_m": r1(tpl_c.length_m), "axis_label": axis, "sample_note": note,
            "map": {"runs": tpl.runs, "base": tpl.base, "leave_one_out": f"{run.run_id} not in it", "n_bins": nb,
                    "bin_edges_m": [r1(x) for x in tpl.metres(np.linspace(0, 1, nb + 1))],
                    "noise_db": [r2(x) for x in tpl.noise_db], "vmin": r2(vmin), "vmax": r2(vmax),
                    "duration_s": r1(tpl.duration_s), "curves_m": curves},
            "paint_rule": {"locked": D1.LOCKED, "lock_window_m": 50, "band_max_m": r1(paint_band),
                           "hold_steps": D1.HOLD, "hold_s": D1.HOLD * O.STEP},
            "step_s": O.STEP, "speed": int(ctl.speed) if float(ctl.speed).is_integer() else ctl.speed, "pre_s": D1.PRE_S, "t_range": t_range, "n_steps": n_steps,
            "levels_unit": "dBFS(A), relative, uncalibrated", "live_only": live_only,
            "departure": {"armed_s": r1(arm - feat.wall.iloc[0]), "detect_s": r1(dep["wall_detect"] - feat.wall.iloc[0]),
                          "backdated_s": r2(dep["wall_detect"] - onset)},
            "synthetic_stop": None if stop is None else {
                "start_s": r1(ts - onset), "end_s": r1(ts - onset + t_stop), "variant": a.stop_variant,
                "label": "SYNTHETIC 30 s STOP (inserted for the demo)"},
            "truth_available": truth is not None,
            "truth_label": (f"offline curve-edge reference ({len(edges)} matched curve edges; shares the gyro; not ground truth)"
                            if truth is not None else truth_note or "reference switched off"),
            "qc": qc, "offline_score": score, "setup_s": round(time.perf_counter() - t_start, 1)}
    emit(init)
    if warn:
        emit({"type": "warning", "message": warn})
    status("ready", "Ready")

    if a.hold and not a.fast:
        emit({"type": "holding"})
        while not ctl.is_go:
            ctl.poll()
            if ctl.eof:
                return
            time.sleep(0.05)
    play(a, ctl, locals())


def play(a, ctl: Control, c: dict) -> None:
    """The paced loop (ride_v2's update()), emitting ticks."""
    import numpy as np
    RV, O, D1, R, tpl, tracker, v2 = c["RV"], c["O"], c["D1"], c["R"], c["tpl"], c["tracker"], c["v2"]
    wall, yaw, valid, gyr, vib, body, hh = (c[k] for k in ("wall", "yaw", "valid", "gyr", "vib", "body", "hh"))
    onset, dep, ts, t_stop, arr_r, orig, truth, noise = (c[k] for k in ("onset", "dep", "ts", "t_stop", "arr_r", "orig", "truth", "noise"))
    paint_band, nb, n_steps, stop = c["paint_band"], c["nb"], c["n_steps"], c["stop"]
    st = {"started": False, "arrived": False, "arrived_wall": None, "e": None, "held": 0, "last_noise": None,
          "last_k": None, "t_track": 0.0, "status": None, "engaged_at": None, "stop_end": None, "in_stop": False}
    err, t_arrived = [], None

    def tstep(i: int):
        t0 = time.perf_counter()
        if v2:
            e = tracker.step(yaw[i], bool(valid[i]), gyr[i] > O.HANDLED, {"vib": vib[i], "body": body[i], "h": hh[i]})
        else:
            e = tracker.step(yaw[i], bool(valid[i]), gyr[i] > O.HANDLED)
        st["t_track"] += time.perf_counter() - t0
        return e

    def finished(i: int) -> bool:
        aw = st["arrived_wall"]
        return aw is not None and wall[i] >= max(aw, arr_r) + RV.POST_S

    fast = a.fast
    t_play0, base_i = time.perf_counter(), 0
    speed = ctl.speed
    paused_at = None
    emitted_last_status = None
    i = 0
    while i < n_steps:
        if not fast:
            ctl.poll()
            while ctl.paused and not ctl.eof:        # pause: re-base the clock on resume
                if paused_at is None:
                    paused_at = time.perf_counter()
                time.sleep(0.05)
                ctl.poll()
            if paused_at is not None:
                t_play0 += time.perf_counter() - paused_at
                paused_at = None
            if ctl.speed != speed:                   # speed change: re-base so earlier steps are not replayed
                t_play0 = time.perf_counter() - i * O.STEP / ctl.speed
                speed = ctl.speed
            if ctl.eof and a.stdin_control:
                return
            wait = t_play0 + i * O.STEP / speed - time.perf_counter()
            if wait > 0:
                time.sleep(min(wait, 0.2))           # short sleeps so commands are polled; leftover wait is re-evaluated
                if t_play0 + i * O.STEP / speed - time.perf_counter() > 0:
                    continue
        w = wall[i]
        departure_now = False
        if not st["started"] and w >= dep["wall_detect"]:
            for j in np.flatnonzero((wall >= onset) & (wall < w)):     # catch up on buffered past steps
                st["e"] = tstep(j)
            st["started"], departure_now = True, True
            emit({"type": "departure", "t": r2(w - onset), "detect_s": r1(dep["wall_detect"] - c["feat"].wall.iloc[0]),
                  "backdated_s": r2(dep["wall_detect"] - onset)})
        if st["started"]:
            st["e"] = e = tstep(i)
            if e["map"] >= 0.995 and not st["arrived"]:
                st["arrived"], st["arrived_wall"], t_arrived = True, w, w - onset
        e = st["e"]
        t = w - onset
        in_stop = ts <= w < ts + t_stop
        pos = float(tpl.metres(e["map"])) if e else None
        lo = float(tpl.metres(e["q05"])) if e else None
        hi = float(tpl.metres(e["q95"])) if e else None
        conf = False
        if e:
            ok = e["lock"] >= D1.LOCKED and (hi - lo) < paint_band
            st["held"] = st["held"] + 1 if ok else 0
            conf = st["held"] >= D1.HOLD
        wo = orig(w)
        paint = None
        if noise is not None:
            k = int(np.searchsorted(noise[0], wo)) - 1 if np.isfinite(wo) else -1
            if k >= 0 and wo - noise[0][k] < 1.0:
                lv = float(noise[1][k])
                st["last_noise"] = lv if math.isfinite(lv) else None
                if conf and st["last_noise"] is not None and k != st["last_k"]:
                    paint, st["last_k"] = {"s_m": r1(pos), "level": r2(lv), "k": k}, k
            elif in_stop:
                st["last_noise"] = None
        status_s = ("ARRIVED" if st["arrived"] else "TRACKING" if st["started"] else "WAITING FOR DEPARTURE")
        if v2 and e and e.get("gate") and not st["arrived"]:
            status_s = "HOLDING: train stopped"
        gz = e.get("gate_z", math.nan) if e else math.nan
        gz = float(gz) if isinstance(gz, (int, float)) and not isinstance(gz, bool) else math.nan
        gate_state = "none" if not v2 else "engaged" if e and e.get("gate") else "off" if math.isfinite(gz) else "warming"
        tv = truth(w) if truth is not None else math.nan
        truth_m = float(tpl.metres(tv)) if math.isfinite(tv) else None
        if truth_m is not None and e and st["started"]:
            err.append(abs(truth_m - pos))
        if in_stop and e:
            if v2 and e.get("gate") and st["engaged_at"] is None:
                st["engaged_at"] = w - ts
            st["stop_end"] = (pos - truth_m) if truth_m is not None else None
        pm = e.get("p_moving", 1.0) if e else 1.0
        speed_kmh = tpl.speed(e["map"], e["speed_ratio"]) * pm * 3.6 if e and st["started"] else None
        every = max(1, math.ceil(speed / 4))
        edge = in_stop != st["in_stop"]
        last = finished(i) or i == n_steps - 1
        if fast or i % every == 0 or paint or departure_now or status_s != st["status"] or edge or last:
            emit({"type": "tick", "i": i, "t": r2(t), "section": a.track, "s_m": r1(pos), "band_lo": r1(lo), "band_hi": r1(hi),
                  "lock": r2(e["lock"]) if e else None, "confident": conf,
                  "level_rel": None if in_stop else r2(st["last_noise"]) if st["last_noise"] is not None else None,
                  "level_map": r2(tpl.noise_db[min(int(e["map"] * nb), nb - 1)]) if e else None, "paint": paint,
                  "speed_kmh": r1(speed_kmh) if speed_kmh is not None else None,
                  "yaw": r2(yaw[i]) if valid[i] else None,
                  "map_yaw": r2(float(np.interp(e["map"], R.GRID, tpl.yaw_mu))) if e else None,
                  "stopped": bool(v2 and e and e.get("gate")), "gate_state": gate_state, "gate_z": r2(gz),
                  "status": status_s, "departure": departure_now, "in_synthetic_stop": bool(in_stop),
                  "truth_s_m": r1(truth_m)})
        st["status"], st["in_stop"] = status_s, in_stop
        if last:
            break
        i += 1
    n_done = i + 1
    summary = {"steps": n_done, "ride_s": round(n_done * O.STEP, 2),
               "wall_s": round(time.perf_counter() - t_play0, 1), "tracker_ms_per_step": round(1e3 * st["t_track"] / max(n_done, 1), 2),
               "arrived_t": r1(t_arrived), "vs_truth_median_m": r1(float(np.median(err))) if err else None,
               "vs_truth_n": len(err), "stop": None}
    if stop is not None:
        summary["stop"] = {"end_err_m": None if st["stop_end"] is None else round(float(st["stop_end"])),
                           "gate_engaged_after_s": r2(st["engaged_at"]) if st["engaged_at"] is not None else None}
    emit({"type": "end", "summary": summary})


def qc_block(RV, qc_all: dict, run, qc_flags: list[dict]) -> dict:
    """This ride's QC lines (full) and the other rides' (brief), as demo.ride_v2.qc_text builds them."""
    rid = lambda s: s.split("_")[0]
    sev = {"low": 1, "medium": 2, "high": 3}
    lines = RV.flag_lines(run.run_id, qc_flags, brief=False) or [f"{run.run_id}: no flags"]
    by_line: dict[str, str] = {}
    groups = {"hand": ("handled_mid_ride", "handheld"), "mag": ("magnetometer_raw", "magnetometer_calibrated", "magnetometer_reset")}
    seen = set()
    for f in qc_flags:
        grp = next((g for g in groups.values() if f["type"] in g), (f["type"],))
        if grp in seen:
            continue
        seen.add(grp)
        fl = [x for x in qc_flags if x["type"] in grp]
        worst = max((x.get("severity", "low") for x in fl), key=lambda s: sev.get(s, 0))
        for line in RV.flag_lines(run.run_id, fl, brief=False):
            by_line[line] = worst
    items = [{"text": ln, "severity": by_line.get(ln, "info")} for ln in lines]
    other = []
    for s, r in qc_all.items():
        if s != run.stem and not s.startswith("R0_"):
            other += RV.flag_lines(rid(s), [f for f in r["flags"] if f["type"] != "split_run" or rid(s) == "R1"], brief=True)
    flags = [{"type": f["type"], "t0": f.get("t0"), "t1": f.get("t1"), "severity": f.get("severity"),
              "evidence": (f.get("evidence") or "")[:400]} for f in qc_flags]
    return {"this_ride": lines, "this_ride_items": items, "other": other, "flags": flags}


def offline_score(RV, key: str, dev: bool) -> dict | None:
    """The sealed-test score of this section for 24 Oct rides (attempt 8 per_run), null for 23 Oct rides."""
    if dev:
        return None
    try:
        att = [json.loads(x) for x in (RV.ROOT / "outputs_posthoc" / "attempts.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        t = next(x for x in reversed(att) if x["kind"] == "test")
        pr = t["metrics"]["per_run"][key]
        rr = next((r for r in t["metrics"].get("runs", []) if r["held_out"] == key), {})
        return {"v2": {"median_m": pr["median_m"], "p90_m": rr.get("p90_m"), "coverage": pr["coverage"], "width_m": pr["width_m"],
                       "source": f"outputs_posthoc/attempts.jsonl attempt {t['attempt']} (sealed test, scored once {t['ts']})"}}
    except (OSError, StopIteration, KeyError, ValueError):
        return None


if __name__ == "__main__":
    main()
