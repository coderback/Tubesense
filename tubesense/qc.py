"""Automatic messy-input QC: pauses, phone placement, split runs, magnetometer mode.

Nothing here is keyed on run names: every flag is derived from the sensor data, the
phyphox event log and the route encoded in the file stem.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import signal

from tubesense.ingest import Run
from tubesense.stops import HOP, motion_features, moving_score, pauses, route_stations

EARTH_UT = 49.0  # London total field, approx.


# --------------------------------------------------------------------------- pauses / gaps
def timing_flags(run: Run) -> list[dict]:
    flags = []
    seg = run.segments()
    for i, p in enumerate(pauses(run)):
        first = seg.iloc[i]
        false_start = i == 0 and (first.t1 - first.t0) < 5
        flags.append({
            "type": "false_start" if false_start else "pause",
            "t0": round(p["t"], 2), "t1": round(p["t"], 2),
            "wall0": round(p["wall0"], 2), "wall1": round(p["wall1"], 2),
            "severity": "low" if false_start else "medium",
            "evidence": (f"phyphox recorded {first.t1 - first.t0:.2f} s then paused {p['dur']:.1f} s before "
                         "restarting" if false_start else
                         f"phyphox PAUSE at t={p['t']:.2f} s for {p['dur']:.1f} s wall-clock; experiment time "
                         "does not advance, audio keeps running"),
            "recommendation": "use wall time (run.to_wall) to align audio; ignore the first segment" if false_start
            else "sensor gap: no IMU/baro for this interval; audio for it has no position. OK for positioning "
                 "outside the gap; treat affected section's sensor features as incomplete",
        })
    for k, df in run.sensors.items():
        dt = np.diff(df.t.to_numpy())
        med = np.median(dt)
        for j in np.where(dt > max(5 * med, 0.1 if k != "baro" else 3.0))[0]:
            flags.append({"type": "sample_gap", "sensor": k, "t0": round(float(df.t.iloc[j]), 3),
                          "t1": round(float(df.t.iloc[j + 1]), 3), "severity": "low",
                          "evidence": f"{k} dt {dt[j] * 1000:.0f} ms vs median {med * 1000:.1f} ms",
                          "recommendation": "interpolate across"})
    return flags


# --------------------------------------------------------------------------- placement
def _gravity(run: Run, tg: np.ndarray) -> np.ndarray:
    a = run.sensors["acc"]
    t = a.t.to_numpy()
    fs = 1 / np.median(np.diff(t))
    lp = signal.sosfiltfilt(signal.butter(2, 0.3, fs=fs, output="sos"), a[["x", "y", "z"]].to_numpy(), axis=0)
    g = np.column_stack([np.interp(tg, t, lp[:, i]) for i in range(3)])
    return g / np.linalg.norm(g, axis=1, keepdims=True)


def _band_power(x: np.ndarray, fs: float, lo: float, hi: float) -> float:
    f, P = signal.welch(x - x.mean(), fs=fs, nperseg=256)
    m = (f >= lo) & (f < hi)
    return float(P[m].sum() * (f[1] - f[0]))


def placement_epochs(run: Run, f: pd.DataFrame | None = None, score: np.ndarray | None = None) -> list[dict]:
    """Split a run at handling events and describe how the phone sat in each epoch."""
    f = motion_features(run) if f is None else f
    score = moving_score(f) if score is None else score
    t = f.t.to_numpy()
    h = f.handled.to_numpy()
    grav = _gravity(run, t)
    # Hand-held: sustained 0.3-3 Hz rotation well above what a resting phone sees from the car.
    handheld = pd.Series(f.gyr_body.to_numpy()).rolling(int(5 / HOP), center=True, min_periods=1).median().to_numpy() > 0.03
    busy = h | handheld
    d = np.diff(np.r_[0, (~busy).astype(int), 0])
    blocks = [(i0, i1) for i0, i1 in zip(np.where(d == 1)[0], np.where(d == -1)[0]) if (i1 - i0) * HOP >= 10]

    a, gy = run.sensors["acc"], run.sensors["gyr"]
    ta = a.t.to_numpy()
    fs = 1 / np.median(np.diff(ta))
    A = a[["x", "y", "z"]].to_numpy()
    G = np.column_stack([np.interp(ta, gy.t, gy[c]) for c in "xyz"])

    epochs = []
    for k, (i0, i1) in enumerate(blocks):
        sl = slice(i0, i1)
        gv = grav[sl]
        tilt = np.degrees(np.arccos(np.clip(np.abs(gv[:, 2]), 0, 1)))
        ride = (score[sl] > 0.75)
        tr = t[sl][ride]
        e = {"t0": float(t[i0]), "t1": float(t[i1 - 1]), "dur": float((i1 - i0) * HOP),
             "tilt_med_deg": float(np.median(tilt)), "tilt_iqr_deg": float(np.subtract(*np.percentile(tilt, [75, 25]))),
             "screen": "up" if np.median(gv[:, 2]) > 0 else "down",
             "ride_s": float(ride.sum() * HOP)}
        # Orientation change vs previous epoch (a re-placement).
        if epochs:
            pv = np.array(epochs[-1]["_g"])
            e["reorient_deg"] = float(np.degrees(np.arccos(np.clip(np.dot(pv, np.median(gv, 0) / np.linalg.norm(np.median(gv, 0))), -1, 1))))
        e["_g"] = (np.median(gv, 0) / np.linalg.norm(np.median(gv, 0))).tolist()
        # Preceded by a hand-held stretch while the train was moving -> phone put down mid-ride.
        prev = slice(max(0, i0 - int(20 / HOP)), i0)
        e["placed_mid_ride"] = bool(i0 > int(10 / HOP) and handheld[prev].mean() > 0.3 and np.median(f.dpdt.to_numpy()[prev]) > 0.05)
        # brief handling that does not re-orient the phone keeps the same placement
        if epochs and e["reorient_deg"] < 10 and epochs[-1]["placed_mid_ride"]:
            e["placed_mid_ride"] = True
        if ride.sum() * HOP >= 20:
            # spectra over riding samples only, in 4 s chunks (kept per chunk for same-section comparison)
            ch = []
            for tc in tr[:: int(4 / HOP)]:
                m = (ta >= tc - 2) & (ta < tc + 2)
                if m.sum() < 300:
                    continue
                z = A[m] @ np.median(gv, 0)  # along gravity (vertical), robust to tilt
                ch.append((float(tc), _band_power(z, fs, 3, 10), _band_power(z, fs, 20, 50),
                           sum(_band_power(G[m, i], fs, 3, 40) for i in range(3))))
            ch = np.array(ch)
            e.update({"vert_3_10": float(np.median(ch[:, 1])), "vert_20_50": float(np.median(ch[:, 2])),
                      "hf_ratio": float(np.median(ch[:, 2]) / np.median(ch[:, 1])),
                      "gyro_3_40": float(np.median(ch[:, 3])),
                      "vib_rms": float(np.median(f.vib.to_numpy()[sl][ride])), "_chunks": ch.tolist()})
        epochs.append(e)
    handheld_iv = _runs_of(handheld & ~h, t, 5)
    return epochs, handheld_iv


def _runs_of(mask, t, min_len):
    d = np.diff(np.r_[0, mask.astype(int), 0])
    return [[float(t[i0]), float(t[i1 - 1])] for i0, i1 in zip(np.where(d == 1)[0], np.where(d == -1)[0])
            if (i1 - i0) * HOP >= min_len]


def classify_placement(all_epochs: dict[str, list[dict]], sections: dict[str, list[dict]]) -> None:
    """Label resting epochs against same-section, same-direction peers (in place).

    Track vibration differs a lot between sections and directions (EB is 2-10x WB here), so
    each 4 s chunk's vertical 20-50 Hz power is divided by the median of the OTHER runs over
    the same section and direction (runs already found isolated are left out of the
    reference in a second pass); an epoch's ratio is the median over its chunks. Single-run
    routes (R0, R0.5) have no peers and stay unclassified.
    A second, section-independent cue is 'rattle': gyro 3-40 Hz power / vertical 20-50 Hz
    power, i.e. how much the phone rotates per unit of vertical vibration, against the
    median over all resting epochs.
    """
    def key(stem, tc):
        for sec in sections[stem]:
            if sec["depart_t"] <= tc <= sec["arrive_t"]:
                return (sec["direction"], sec["from"], sec["to"])
        return None

    per = {}  # (stem, section key) -> [vert 20-50 Hz power]
    for stem, eps in all_epochs.items():
        for e in eps:
            for tc, _, hi, _ in e.get("_chunks", []):
                k = key(stem, tc)
                if k:
                    per.setdefault((stem, k), []).append(hi)
    med = {sk: float(np.median(v)) for sk, v in per.items()}
    judged = [e for eps in all_epochs.values() for e in eps if "vert_20_50" in e]
    for e in judged:
        e["rattle"] = float(e["gyro_3_40"] / e["vert_20_50"])
    rattle_ref = float(np.median([e["rattle"] for e in judged]))

    def ratio(stem, e, skip, min_peers):
        rh = []
        for tc, _, hi, _ in e["_chunks"]:
            k = key(stem, tc)
            peers = [m for (s2, k2), m in med.items() if k2 == k and s2 != stem and s2 not in skip]
            if k is not None and len(peers) >= min_peers:
                rh.append(hi / np.median(peers))
        return (float(np.median(rh)) if len(rh) >= 5 else None), len(rh)

    isolated = {stem for stem, eps in all_epochs.items() for e in eps
                if "vert_20_50" in e and (ratio(stem, e, set(), 2)[0] or 1) < 0.2}
    for stem, eps in all_epochs.items():
        for e in eps:
            if "vert_20_50" not in e:
                e["label"] = "too short to judge"
                continue
            r, n = ratio(stem, e, isolated - {stem}, 1)
            e["n_chunks_vs_peers"] = n
            e["rattle_vs_median"] = round(e["rattle"] / rattle_ref, 2)
            e["floor_like"] = False
            if r is None:
                e["label"] = "resting; no same-section peers to compare"
                e["coupling"] = "unknown"
                continue
            e["hf_vs_peers"] = round(r, 3)
            if r < 0.2:
                coupling = "isolated (soft/compliant support)"
            elif r > 3.0:
                coupling = "hard contact"
            elif e["rattle_vs_median"] > 3:
                coupling = "rattling"
            else:
                coupling = "typical"
            e["coupling"] = coupling
            level = e["tilt_med_deg"] < 2.0
            e["floor_like"] = bool(level and coupling.startswith("isolated"))
            e["floor_evidence"] = {"level_<2deg": level, "isolated_vs_peers": coupling.startswith("isolated"),
                                   "put_down_mid_ride_after_handheld": e["placed_mid_ride"]}
            e["label"] = f"resting, {coupling}" + ("; FLOOR-LIKE (level + isolated)" if e["floor_like"] else "")


def slow_sections(sections: list[dict], tol: float = 0.08) -> list[dict]:
    """Sections more than `tol` slower than the median of the same section+direction (>= 3 runs)."""
    by, out = {}, []
    for s in sections:
        if s["depart_observed"] and s["arrive_observed"]:
            by.setdefault((s["direction"], s["from"], s["to"]), []).append(s)
    for ss in by.values():
        if len(ss) < 3:
            continue
        med = float(np.median([s["duration_s"] for s in ss]))
        for s in ss:
            if s["duration_s"] > (1 + tol) * med and not s["overlaps_pause"]:
                out.append({"stem": s["stem"], "section": f"{s['from']}->{s['to']}", "t0": s["depart_t"],
                            "t1": s["arrive_t"], "duration_s": s["duration_s"], "median_s": med})
    return out


# --------------------------------------------------------------------------- split runs
def split_runs(runs: list[Run]) -> list[dict]:
    """Consecutive recordings on the same line+direction where one ends where the next starts."""
    out = []
    rs = sorted(runs, key=lambda r: r.events["system time"].iloc[0])
    for a, b in zip(rs, rs[1:]):
        sa, sb = route_stations(a), route_stations(b)
        end_a = float(a.to_wall(np.array([a.sensors["acc"].t.iloc[-1]]))[0])
        start_b = float(b.events["system time"].iloc[0])
        gap = start_b - end_a
        if sa[-1] != sb[0] or a.direction != b.direction or gap > 30 * 60:
            continue
        combined = sa + sb[1:]
        planned = any(combined == route_stations(r) for r in runs if r not in (a, b))
        if planned:
            kind = "split_run"
            ev = (f"{a.stem} ends at {sa[-1]} and {b.stem} starts there, same direction {a.direction}, "
                  f"{gap / 60:.1f} min apart; {'-'.join([combined[0], combined[-1]])} with {sa[-1]} intermediate "
                  f"matches the planned route of other runs, so this is one intended trip recorded in two files "
                  f"(alighted at {sa[-1]} and re-boarded)")
        else:
            kind = "continuation"
            ev = (f"{a.stem} ends at {sa[-1]} where {b.stem} starts, same direction, {gap:.0f} s apart; not a "
                  "planned route pairing (likely a deliberate new run, possibly the same train)")
        out.append({"type": kind, "runs": [a.stem, b.stem], "gap_s": round(gap, 1),
                    "logical_trip_id": f"{a.run_id}+{b.run_id}_{a.direction}_{combined[0]}-{combined[-1]}" if planned else None,
                    "stations": combined, "evidence": ev})
    return out


# --------------------------------------------------------------------------- magnetometer
def mag_steps(run: Run, jump_ut: float = 60.0, calm_rad_s: float = 0.3) -> list[dict]:
    """One-sample jumps in the field vector while the phone is not rotating.

    A real field (Earth, train, tunnel) changes smoothly at 100 Hz; a jump of > 60 uT between
    consecutive samples with the gyro calm is the iOS calibration re-estimating its offset
    (|B| typically snaps to near 0 and then re-converges).
    """
    m, g = run.sensors["mag"], run.sensors["gyr"]
    t = m.t.to_numpy()
    M = m[["x", "y", "z"]].to_numpy()
    d = np.linalg.norm(np.diff(M, axis=0), axis=1)
    gn = np.linalg.norm(np.column_stack([np.interp(t, g.t, g[c]) for c in "xyz"]), axis=1)
    B = np.linalg.norm(M, axis=1)
    out = []
    for i in np.flatnonzero((d > jump_ut) & (gn[1:] < calm_rad_s)):
        out.append({"t": round(float(t[i + 1]), 2), "jump_uT": round(float(d[i]), 0),
                    "B_before_uT": round(float(np.median(B[max(0, i - 200): i + 1])), 0),
                    "B_after_uT": round(float(np.median(B[i + 1: i + 201])), 0)})
    return out


def magnetometer_mode(run: Run, f: pd.DataFrame | None = None) -> dict:
    """Calibrated (iOS device-motion field) vs raw, from stream provenance + physics.

    (1) provenance: phyphox's calibrated field comes from the device-motion stream, so its
        timestamps coincide with the gyroscope's; a raw magnetometer has its own clock/rate.
    (2) phone-frame mean field: a raw stream carries the fixed device hard-iron bias, so the
        mean vector is large and repeats between raw runs; calibrated data is centred nearer 0.
    (3) |B| level vs ~49 uT (weak underground: traction current and car steel dominate).
    """
    m, g = run.sensors["mag"], run.sensors["gyr"]
    shared = float(np.isin(np.round(m.t.to_numpy(), 7), np.round(g.t.to_numpy(), 7)).mean())
    rate_m, rate_g = 1 / np.median(np.diff(m.t)), 1 / np.median(np.diff(g.t))
    f = motion_features(run) if f is None else f
    ok = np.interp(m.t.to_numpy(), f.t, (~f.handled).astype(float)) > 0.5
    M = m[["x", "y", "z"]].to_numpy()[ok]
    mean_vec = M.mean(0)
    return {"ts_shared_with_gyro": round(shared, 3), "rate_mag_hz": round(float(rate_m), 3),
            "rate_gyro_hz": round(float(rate_g), 3), "mean_vec_uT": np.round(mean_vec, 1).tolist(),
            "mean_vec_norm_uT": round(float(np.linalg.norm(mean_vec)), 1),
            "median_B_uT": round(float(np.median(np.linalg.norm(M, axis=1))), 1),
            "steps": mag_steps(run)}


def classify_magnetometer(info: dict[str, dict], known_calibrated: str) -> None:
    """Group runs by provenance; the group containing the known-calibrated run is 'calibrated'."""
    ref = info[known_calibrated]
    raw_vecs = []
    for stem, d in info.items():
        same_source = (d["ts_shared_with_gyro"] > 0.9) == (ref["ts_shared_with_gyro"] > 0.9) and \
            abs(d["rate_mag_hz"] - d["rate_gyro_hz"]) < 0.05
        d["mode"] = "calibrated" if same_source else "raw"
        if d["mode"] == "raw":
            raw_vecs.append(np.array(d["mean_vec_uT"]))
    if len(raw_vecs) >= 2:
        c = np.mean(raw_vecs, 0)
        spread = float(np.mean([np.linalg.norm(v - c) for v in raw_vecs]))
    for stem, d in info.items():
        ev = [f"mag timestamps {'==' if d['ts_shared_with_gyro'] > 0.9 else '!='} gyro timestamps; "
              f"mag rate {d['rate_mag_hz']} Hz vs gyro {d['rate_gyro_hz']} Hz "
              f"({'same' if d['mode'] == 'calibrated' else 'different'} stream signature as {known_calibrated}, "
              "known calibrated)"]
        ev.append(f"phone-frame mean field |{d['mean_vec_norm_uT']}| uT, median |B| {d['median_B_uT']} uT "
                  f"(Earth ~{EARTH_UT:.0f} uT)")
        conf = 0.8
        if d["mode"] == "raw" and len(raw_vecs) >= 2:
            dist = float(np.linalg.norm(np.array(d["mean_vec_uT"]) - c))
            ev.append(f"mean vector {dist:.0f} uT from the raw-group centre {np.round(c, 0).tolist()} "
                      f"(group spread {spread:.0f} uT): a fixed hard-iron bias repeating across rides")
            conf = 0.9 if dist < 80 else 0.7
        if d["mode"] == "calibrated" and d["median_B_uT"] > 150:
            ev.append("|B| far above Earth field despite calibrated stream: train/tunnel fields or an unconverged "
                      "iOS calibration; level is not an Earth-field reference")
            conf = 0.65
        if d["mode"] == "calibrated" and d["steps"]:
            ev.append(f"{len(d['steps'])} one-sample jump(s) with the phone still "
                      f"(t = {', '.join(str(x['t']) for x in d['steps'])} s): calibration offset re-estimated "
                      "mid-ride, so |B| levels are only comparable between jumps")
        if stem == known_calibrated:
            conf = 1.0
        d["confidence"] = conf
        d["evidence"] = "; ".join(ev)
