"""Station stop detection and station-to-station segmentation (sensor experiment time).

Motion is scored per 0.5 s hop from three placement-robust cues:
- car-body motion: accel 0.5-3 Hz band (bounce/sway of a moving car; near zero at a dwell),
- running vibration: accel 3-40 Hz band,
- tunnel pressure waves: |dp/dt| from the barometer (flat at a platform).
Each cue is log-normalised per run, so a phone on a cushion and one on the floor score alike.
Stationary intervals are then matched to the run's known station sequence by picking the
subset whose section durations best fit the expected travel times; the rest are tunnel stops.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd
from scipy import signal

from tubesense.ingest import Run

HOP = 0.5  # s

# Station code -> (name, lat, lon). Straight-line spacing only feeds the expected travel time.
STATIONS = {
    "VIC": ("Victoria", 51.4965, -0.1447),
    "GPK": ("Green Park", 51.5067, -0.1428),
    "OXC": ("Oxford Circus", 51.5152, -0.1415),
    "TCR": ("Tottenham Court Road", 51.5165, -0.1310),
    "HOL": ("Holborn", 51.5174, -0.1201),
    "CYL": ("Chancery Lane", 51.5185, -0.1111),
    "STP": ("St Paul's", 51.5146, -0.0973),
    "BNK": ("Bank", 51.5133, -0.0886),
    "LST": ("Liverpool Street", 51.517372, -0.083182),
    "BTH": ("Bethnal Green", 51.527222, -0.055506),
    "MLE": ("Mile End", 51.525122, -0.03364),
}

ROUTES = {
    "VIC_OXC": ["VIC", "GPK", "OXC"],
    "OXC-LST": ["OXC", "TCR", "HOL", "CYL", "STP", "BNK", "LST"],
    "LST-BTH": ["LST", "BTH"],
    "BTH-MLE": ["BTH", "MLE"],
    "LST-MLE": ["LST", "BTH", "MLE"],
    "MLE-LST": ["MLE", "BTH", "LST"],
}


def route_stations(run: Run) -> list[str]:
    return ROUTES[run.route]


def distance_m(a: str, b: str) -> float:
    (_, la1, lo1), (_, la2, lo2) = STATIONS[a], STATIONS[b]
    dy = (la2 - la1) * 111_195
    dx = (lo2 - lo1) * 111_195 * np.cos(np.radians((la1 + la2) / 2))
    return float(np.hypot(dx, dy))


def expected_run_s(a: str, b: str) -> float:
    """Rough station-to-station running time: 25 s accel/brake overhead + cruise at 18 m/s
    over a track 10% longer than the straight line."""
    return 25 + 1.1 * distance_m(a, b) / 18


def pauses(run: Run) -> list[dict]:
    """phyphox pauses: experiment time t (where time freezes) and wall interval [wall0, wall1]."""
    seg = run.segments()
    out = []
    for i in range(len(seg) - 1):
        a, b = seg.iloc[i], seg.iloc[i + 1]
        w0 = a.wall0 + (a.t1 - a.t0)
        out.append({"t": float(a.t1), "wall0": float(w0), "wall1": float(b.wall0), "dur": float(b.wall0 - w0)})
    return out


def _band_rms(x: np.ndarray, fs: float, lo: float, hi: float, win: float) -> np.ndarray:
    sos = signal.butter(3, [lo, hi], "band", fs=fs, output="sos")
    e = (signal.sosfiltfilt(sos, x, axis=0) ** 2).sum(1)
    k = max(1, int(win * fs))
    return np.sqrt(np.convolve(e, np.ones(k) / k, "same"))


def _walking(amag: np.ndarray, fs: float, t: np.ndarray, tg: np.ndarray, win: float = 4.0) -> np.ndarray:
    """Step periodicity: autocorrelation peak at 0.4-0.7 s lag (1.4-2.5 Hz) with real amplitude."""
    sos = signal.butter(3, [1.0, 4.0], "band", fs=fs, output="sos")
    y = signal.sosfiltfilt(sos, amag)
    k = int(win * fs)
    lags = np.arange(int(0.4 * fs), int(0.7 * fs))
    out = np.zeros(len(tg), bool)
    for i, tc in enumerate(tg):
        j = np.searchsorted(t, tc)
        s = y[max(0, j - k // 2): j + k // 2]
        if len(s) < k // 2 or s.std() < 0.5:
            continue
        s = s - s.mean()
        ac = np.array([np.dot(s[:-l], s[l:]) for l in lags]) / np.dot(s, s)
        out[i] = ac.max() > 0.4
    return out


def motion_features(run: Run) -> pd.DataFrame:
    """Per-hop features on a regular grid of experiment time."""
    a, g, b = run.sensors["acc"], run.sensors["gyr"], run.sensors["baro"]
    t = a.t.to_numpy()
    fs = 1 / np.median(np.diff(t))
    A = a[["x", "y", "z"]].to_numpy()
    G = np.column_stack([np.interp(t, g.t, g[c]) for c in "xyz"])
    tg = np.arange(t[0] + HOP, t[-1] - HOP, HOP)

    def on_grid(v):
        return np.interp(tg, t, v)

    vib = on_grid(_band_rms(A, fs, 3, 40, 2.0))
    body = on_grid(_band_rms(A, fs, 0.5, 3, 3.0))
    gyr_body = on_grid(_band_rms(G, fs, 0.3, 3, 3.0))
    k = int(1.0 * fs)
    gyr_norm = on_grid(np.convolve(np.linalg.norm(G, axis=1), np.ones(k) / k, "same"))
    # Barometer (~1 Hz): smooth, differentiate, then rolling max over +-3 s.
    tb = np.arange(b.t.iloc[0], b.t.iloc[-1], 1.0)
    pb = signal.savgol_filter(np.interp(tb, b.t, b.p), 7, 2)
    dp = np.abs(np.gradient(pb, tb))
    dp = pd.Series(dp).rolling(3, center=True, min_periods=1).max().to_numpy()
    p = np.interp(tg, tb, pb)
    dpdt = np.interp(tg, tb, dp)
    walk = _walking(np.linalg.norm(A, axis=1), fs, t, tg)
    # Rotation about the vertical (deg/s, 1 s mean): a train on a curve is moving, a dwell is not.
    up = signal.sosfiltfilt(signal.butter(2, 0.3, fs=fs, output="sos"), A, axis=0)
    up /= np.linalg.norm(up, axis=1, keepdims=True)
    yaw = np.abs(on_grid(np.convolve(np.degrees((G * up).sum(1)), np.ones(int(fs)) / int(fs), "same")))
    # Handling (taps, picking up) plus 2 s either side, where filter ringing from the taps leaks.
    k = int(2.0 / HOP)
    handled = np.convolve((gyr_norm > 0.25) | walk, np.ones(2 * k + 1), "same") > 0
    return pd.DataFrame({"t": tg, "vib": vib, "body": body, "gyr_body": gyr_body, "gyr_norm": gyr_norm,
                         "p": p, "dpdt": dpdt, "walk": walk, "handled": handled, "yaw_abs": yaw})


def _lognorm(x: np.ndarray, ref: np.ndarray) -> np.ndarray:
    lx, lr = np.log10(x + 1e-6), np.log10(ref + 1e-6)
    lo, hi = np.percentile(lr, 3), np.percentile(lr, 70)
    return (lx - lo) / max(hi - lo, 0.3)


def moving_score(f: pd.DataFrame) -> np.ndarray:
    """~0 at a dwell, ~1 while running. Handled windows fall back to the barometer alone."""
    ok = ~f.handled.to_numpy()
    ref = f[ok] if ok.sum() > 50 else f
    zb = _lognorm(f.body.to_numpy(), ref.body.to_numpy())
    zv = _lognorm(f.vib.to_numpy(), ref.vib.to_numpy())
    zg = _lognorm(f.gyr_body.to_numpy(), ref.gyr_body.to_numpy())
    dp = f.dpdt.to_numpy()
    s = (2 * zb + zv + zg) / 4
    # Platforms see pressure swings too (trains on the other platform), so the barometer only
    # vetoes a stop when the swing is tunnel-sized; inside handled windows it is all we have.
    s = np.maximum(s, np.clip((dp - 0.15) / 0.2, 0, 1.5))
    # Median-filter the un-handled samples only, so tap transients don't leak into neighbours.
    s[ok] = signal.medfilt(s[ok], 5)
    return np.where(ok, s, np.clip(dp / 0.12, 0, 1.5))


def _intervals(mask: np.ndarray, t: np.ndarray, min_len: float, bridge: float) -> list[list[float]]:
    """Runs of True in mask as [t0, t1], bridging gaps shorter than `bridge` s."""
    iv = []
    d = np.diff(np.r_[0, mask.astype(int), 0])
    for i0, i1 in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
        t0, t1 = t[i0] - HOP / 2, t[i1 - 1] + HOP / 2
        if iv and t0 - iv[-1][1] < bridge:
            iv[-1][1] = t1
        else:
            iv.append([t0, t1])
    return [x for x in iv if x[1] - x[0] >= min_len]


def stationary_intervals(f: pd.DataFrame, score: np.ndarray, thr: float = 0.45) -> pd.DataFrame:
    """Stops = quiet, un-handled windows (score < thr). Handled windows (taps) with calm
    pressure may sit inside a stop; on their own they only count at the recording edges
    (the protocol is to triple-tap while standing at the platform), flagged `edge_inferred`."""
    t = f.t.to_numpy()
    h = f.handled.to_numpy()
    dp = f.dpdt.to_numpy()
    quiet = (score < thr) & ~h
    iv = _intervals(quiet | (h & (dp < 0.15)), t, min_len=3.0, bridge=4.5)
    # Merge stops split by a brief, weak stir (passengers boarding, a creep along the platform).
    merged = []
    for t0, t1 in iv:
        if merged:
            g = (t > merged[-1][1]) & (t < t0)
            if t0 - merged[-1][1] < 10 and (not g.any() or score[g].max() < 0.75):
                merged[-1][1] = t1
                continue
        merged.append([t0, t1])
    rows = []
    for t0, t1 in merged:
        m = (t >= t0) & (t <= t1)
        at_start, at_end = bool(t0 <= t[0] + 1), bool(t1 >= t[-1] - 1)
        edge = quiet[m].sum() * HOP < 2.0
        if edge and not ((at_start or at_end) and np.median(dp[m]) < 0.06):
            continue
        p = f.p.to_numpy()[m]
        rows.append({
            "t0": t0, "t1": t1, "dur": t1 - t0,
            "score": float(np.median(score[m & ~h])) if (m & ~h).any() else np.nan,
            "dpdt_med": float(np.median(dp[m])),
            "p_range": float(p.max() - p.min()),
            "handled_frac": float(h[m].mean()),
            "at_start": at_start, "at_end": at_end, "edge_inferred": bool(edge),
        })
    return pd.DataFrame(rows)


YAW_MOVING = 0.3   # deg/s sustained >= YAW_MIN_S with no handling = the train is turning, so moving
YAW_MIN_S = 1.5
EDGE_S = 8.0       # only the first/last EDGE_S of a dwell can be trimmed


def refine_edges(f: pd.DataFrame, t0: float, t1: float) -> tuple[float, float]:
    """Trim a dwell where the yaw rate shows the train already turning (gyro motion veto).

    The vibration score can stay low for the first seconds of a departure (or the last of an
    arrival); sustained rotation about the vertical cannot happen at a standstill.
    """
    t, y, h = f.t.to_numpy(), f.yaw_abs.to_numpy(), f.handled.to_numpy()
    turning = (y > YAW_MOVING) & ~h
    k = int(YAW_MIN_S / HOP)
    # departure: a turning run that is still going at t1 and started within the last EDGE_S
    m = (t > t1 - EDGE_S) & (t <= t1)
    idx = np.flatnonzero(m)
    if len(idx) and turning[idx[-1]]:
        j = idx[-1]
        while j - 1 >= idx[0] and turning[j - 1]:
            j -= 1
        if idx[-1] - j + 1 >= k and t[j] - t0 >= 3:
            t1 = float(t[j] - HOP / 2)
    m = (t >= t0) & (t < t0 + EDGE_S)
    idx = np.flatnonzero(m)
    if len(idx) and turning[idx[0]]:
        j = idx[0]
        while j + 1 <= idx[-1] and turning[j + 1]:
            j += 1
        if j - idx[0] + 1 >= k and t1 - t[j] >= 3:
            t0 = float(t[j] + HOP / 2)
    return t0, t1


def _assign(cand: pd.DataFrame, stations: list[str], t: np.ndarray, moving: np.ndarray):
    """Choose len(stations) dwells (in order) from candidates + virtual start/end boundaries.

    Cost: squared log-ratio of section durations vs expected, plus penalties for choosing
    short / pressure-active stops as stations, for leaving long stops unexplained, and for
    train motion recorded before the first or after the last chosen station.
    """
    t_lo, t_hi = float(t[0]), float(t[-1])
    K = len(stations)
    exp = [expected_run_s(a, b) for a, b in zip(stations, stations[1:])]
    C = [dict(r) for _, r in cand.iterrows()]
    v0 = {"t0": t_lo, "t1": t_lo, "dur": 0.0, "virtual": True, "dpdt_med": 0.0}
    v1 = {"t0": t_hi, "t1": t_hi, "dur": 0.0, "virtual": True, "dpdt_med": 0.0}
    best = (np.inf, None)
    idx = range(len(C))
    for use_v0 in (False, True):
        for use_v1 in (False, True):
            n = K - use_v0 - use_v1
            if n < 0 or n > len(C):
                continue
            for comb in combinations(idx, n):
                pick = ([v0] if use_v0 else []) + [C[i] for i in comb] + ([v1] if use_v1 else [])
                cost = 0.0
                for j in range(K - 1):
                    d = pick[j + 1]["t0"] - pick[j]["t1"]
                    if d <= 5:
                        cost = np.inf
                        break
                    sig = 0.6 if (pick[j].get("virtual") or pick[j + 1].get("virtual")) else 0.35
                    # a virtual edge means the section is truncated -> only penalise if too long
                    lr = np.log(d / exp[j])
                    if pick[j].get("virtual") or pick[j + 1].get("virtual"):
                        lr = max(lr, 0)
                    cost += (lr / sig) ** 2
                for i, c in enumerate(C):
                    chosen = i in comb
                    if chosen:
                        cost += 2.0 * max(0, 8 - c["dur"]) / 8 + 3.0 * (c["dpdt_med"] > 0.06)
                    else:
                        cost += 1.5 * min(c["dur"], 40) / 20 * (c["dpdt_med"] < 0.06)
                cost += 2.0 * use_v0 + 2.0 * use_v1
                outside = (t < pick[0]["t0"]) | (t > pick[-1]["t1"])
                cost += (moving & outside).sum() * HOP / 10
                if cost < best[0]:
                    best = (cost, pick)
    return best


def segment_run(run: Run) -> dict:
    """Detect dwells, map to stations, return sections + tunnel stops for one run."""
    f = motion_features(run)
    score = moving_score(f)
    cand = stationary_intervals(f, score)
    stations = route_stations(run)
    # Usable window: drop leading/trailing handling (triple taps, pocketing the phone).
    t = f.t.to_numpy()
    h = f.handled.to_numpy()
    lead = t[np.argmax(~h)] if (~h).any() else t[0]
    trail = t[len(h) - 1 - np.argmax(~h[::-1])] if (~h).any() else t[-1]
    cost, pick = _assign(cand, stations, t, (score > 0.75) & ~h)
    exp = [expected_run_s(a, b) for a, b in zip(stations, stations[1:])]

    def wall(x):
        return float(run.to_wall(np.array([x]))[0])

    pz = pauses(run)
    dwells = []
    for s, c in zip(stations, pick):
        virtual = bool(c.get("virtual"))
        if not virtual:
            c = dict(c)
            c["t0_vib"], c["t1_vib"] = c["t0"], c["t1"]
            c["t0"], c["t1"] = refine_edges(f, c["t0"], c["t1"])
        dwells.append({
            "station": s, "t0": float(c["t0"]), "t1": float(c["t1"]),
            "wall0": wall(c["t0"]), "wall1": wall(c["t1"]),
            "duration_s": float(c["t1"] - c["t0"]),
            "truncated": bool(virtual or c.get("at_start") or c.get("at_end")),
            "edge_inferred": bool(c.get("edge_inferred", False)),
            "virtual": virtual,
            "pressure_flat": bool(c.get("dpdt_med", 0) < 0.06),
            "door_dp_hpa": _door_step(f, c) if not virtual else None,
            "yaw_trimmed_s": round(float((c["t0"] - c.get("t0_vib", c["t0"])) + (c.get("t1_vib", c["t1"]) - c["t1"])), 1),
        })
    chosen = {(round(c["t0"], 2), round(c["t1"], 2)) for c in pick if not c.get("virtual")}
    others = [r for _, r in cand.iterrows() if (round(r.t0, 2), round(r.t1, 2)) not in chosen]

    sections = []
    for j in range(len(stations) - 1):
        d0, d1 = dwells[j], dwells[j + 1]
        dep, arr = d0["t1"], d1["t0"]
        inside = [[float(r.t0), float(r.t1)] for r in others if r.t0 > dep and r.t1 < arr]
        # A stop within 15 s of a platform dwell is a stop-short / creep at the platform, not the tunnel.
        near = [x for x in inside if x[0] - dep < 15 or arr - x[1] < 15]
        ts = [x for x in inside if x not in near]
        w0, w1 = wall(dep), wall(arr)
        dur = w1 - w0
        ov = [p for p in pz if p["wall0"] < w1 and p["wall1"] > w0]
        ratio = dur / exp[j]
        conf = 0.9
        ev = [f"duration {dur:.0f} s vs expected ~{exp[j]:.0f} s"]
        if d0["virtual"] or d1["virtual"]:
            conf -= 0.4
            ev.append("one end not observed (recording starts/ends while moving)")
        if not (d0["pressure_flat"] and d1["pressure_flat"]):
            conf -= 0.2
            ev.append("a bounding stop has tunnel-like pressure changes")
        if not 0.6 < ratio < 1.6:
            conf -= 0.3
            ev.append("duration far from expected")
        if ts:
            ev.append(f"{len(ts)} unplanned stop(s) in tunnel")
        if near:
            ev.append(f"{len(near)} brief stop(s) within 15 s of a platform (stop-short / creep)")
        if ov:
            conf -= 0.1
            ev.append(f"phyphox paused {sum(p['dur'] for p in ov):.1f} s inside section (no sensor data then)")
        if dep < lead + 1 or arr > trail - 1:
            ev.append("boundary falls in a handled period (taps/phone in hand); timing from barometer only")
            conf -= 0.1
        sections.append({
            "stem": run.stem, "run_id": run.run_id, "direction": run.direction,
            "from": d0["station"], "to": d1["station"],
            "depart_t": round(dep, 2), "arrive_t": round(arr, 2),
            "depart_wall": round(w0, 2), "arrive_wall": round(w1, 2),
            "duration_s": round(dur, 1), "expected_s": round(exp[j], 1),
            "depart_observed": not d0["virtual"], "arrive_observed": not d1["virtual"],
            "tunnel_stops": ts, "approach_stops": near, "overlaps_pause": bool(ov),
            "confidence": round(max(conf, 0.05), 2), "evidence": "; ".join(ev),
        })
    return {"stem": run.stem, "run_id": run.run_id, "direction": run.direction,
            "stations": stations, "assign_cost": round(float(cost), 2),
            "usable": [float(lead), float(trail)], "dwells": dwells, "sections": sections,
            "other_stops": [[float(r.t0), float(r.t1)] for r in others],
            "_features": f, "_score": score}


def _door_step(f: pd.DataFrame, c: dict) -> float | None:
    """Pressure change across a dwell (end - start, hPa): doors opening to the platform air."""
    t, p = f.t.to_numpy(), f.p.to_numpy()
    if c["t1"] - c["t0"] < 6:
        return None
    a = p[(t >= c["t0"]) & (t < c["t0"] + 2)]
    b = p[(t > c["t1"] - 2) & (t <= c["t1"])]
    return round(float(b.mean() - a.mean()), 3) if len(a) and len(b) else None
