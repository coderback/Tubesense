"""Magnetometer section features, repeatability scoring, hard-iron estimate and platform signatures.

Shared by scripts/mag_anomaly.py (does the field anomaly repeat at all) and scripts/mag_compare.py
(does it repeat equally on a deep-tube and a sub-surface line).
- Field anomaly: |B - section mean B| (features.field_anomaly) on the L0 grid; any constant bias cancels,
  so raw and calibrated runs compare. The driving model regresses it on |a|, traction-only a and speed
  (features.dead_reckon); "resid" is what is left, "_mid" variants keep section fractions MID only,
  away from the pull-away / braking traction peaks.
- Hard iron (raw stream only): the raw magnetometer reads B + c with c fixed in the phone frame. Over a
  few seconds the field is ~constant, so while the phone rotates a lot the samples lie on a sphere
  centred on c: a linear least-squares sphere fit per such window, median over windows.
- Platform signature: |B - c| at a stationary platform dwell, raw runs only (the calibrated stream's
  offset changes mid-ride, so its levels are not comparable across runs).
"""
from __future__ import annotations

import itertools

import numpy as np

from . import features as F
from . import repeat as R
from .ingest import Run

N_SURR = 200
MID = (0.25, 0.85)       # section fraction away from the pull-away / braking traction peaks
FIT_WIN_S = 4.0          # hard-iron fit window
FIT_MIN_ROT_DEG = 90.0   # the phone must turn at least this much within a window
DWELL_MIN_S = 10.0       # platform dwells shorter than this are not used for signatures
DWELL_TRIM_S = 2.0       # trimmed from both ends of a dwell (door / start / stop transients)
STILL_RAD_S = 0.3        # |gyro| below this counts as still


def section_features(run, sec, mag, mag_fixed):
    """|B| (de-stepped), field anomaly with and without de-stepping, and driving proxies on the L0 grid."""
    tg = R.time_grid(run, sec)

    def grid(df):
        return np.column_stack([R.sample(df.t.values, df[c].values, tg) for c in "xyz"])
    V, V0 = grid(mag_fixed), grid(mag)
    dr = F.dead_reckon(run, sec["depart_t"], sec["arrive_t"])
    a = R.sample(dr.t.values, dr.a_long.values, tg)
    v = R.sample(dr.t.values, dr.v.values, tg)
    anom = F.field_anomaly(V)
    # traction proxy: linear model of the anomaly on |a|, traction-only a, and speed
    X = np.column_stack([np.ones_like(a), np.abs(a), np.clip(a, 0, None), v / (np.nanmax(v) + 1e-9)])
    ok = np.isfinite(anom) & np.isfinite(X).all(1)
    beta = np.linalg.lstsq(X[ok], anom[ok], rcond=None)[0]
    drive = np.full_like(anom, np.nan)
    drive[ok] = X[ok] @ beta
    return {"B": np.linalg.norm(V, axis=1), "anom": anom, "anom_nodestep": F.field_anomaly(V0),
            "drive": drive, "resid": anom - drive, "r2_drive": float(1 - np.nanvar(anom - drive) / np.nanvar(anom))}


def add_mid(sig: dict) -> None:
    """Add "<f>_mid" copies of anom / resid / drive that keep only section fractions MID."""
    mid = (R.GRID >= MID[0]) & (R.GRID <= MID[1])
    for s in sig.values():
        for f in ("anom", "resid", "drive"):
            s[f"{f}_mid"] = np.where(mid, s[f], np.nan)


def score(sig, meta, key, rng, modes=("raw", "calibrated"), keys=None, with_null=False):
    """Same-section pairs (surrogate p) vs the different-section null, clean sections only.

    keys: restrict to these sections (e.g. one line); with_null: also return the null r values.
    """
    keys = [k for k in (sig if keys is None else keys) if meta[k]["clean"] and meta[k]["mag_mode"] in modes]
    trk = lambda k: (meta[k]["dir"], meta[k]["from"], meta[k]["to"])
    same, diff = [], []
    for a, b in itertools.combinations(keys, 2):
        r = R.best_shift_r(sig[a][key], sig[b][key])[0]
        if trk(a) != trk(b):
            diff.append(r)
            continue
        sur = [R.best_shift_r(sig[a][key], R.phase_surrogate(np.nan_to_num(sig[b][key]), rng))[0] for _ in range(N_SURR)]
        same.append({"pair": f"{a} vs {b}", "modes": [meta[a]["mag_mode"], meta[b]["mag_mode"]], "r": round(r, 3),
                     "surrogate_p": round(float(np.mean(np.array(sur) >= r)), 3),
                     "surrogate_p99": round(float(np.percentile(sur, 99)), 3)})
    if not same or not diff:
        return {"pairs": same, "n_pairs": len(same), "null_n": len(diff), "note": "not enough sections"}
    rs = np.array([s["r"] for s in same])
    out = {"pairs": same, "same_mean_r": round(float(rs.mean()), 3), "same_min_r": round(float(rs.min()), 3),
           "n_significant": int(sum(s["surrogate_p"] < 0.05 for s in same)), "n_pairs": len(same),
           "null_mean_r": round(float(np.mean(diff)), 3), "null_p95_r": round(float(np.percentile(diff, 95)), 3),
           "null_n": len(diff)}
    if with_null:
        out["null_r"] = [round(float(x), 3) for x in diff]
    return out


def hard_iron(run: Run) -> dict:
    """Raw-magnetometer hard-iron vector c (uT, phone frame) from sphere fits over rotating windows."""
    m, g = run.sensors["mag"], run.sensors["gyr"]
    t = m.t.values
    M = m[["x", "y", "z"]].values
    G = F._on(g, t)
    fs = F._fs(t)
    n = int(FIT_WIN_S * fs)
    ang = np.degrees(np.cumsum(np.linalg.norm(G, axis=1)) / fs)  # accumulated rotation angle
    fits = []
    for i in range(0, len(t) - n, n // 2):
        if ang[i + n - 1] - ang[i] < FIT_MIN_ROT_DEG:
            continue
        X = M[i: i + n]
        A = np.column_stack([2 * X, np.ones(n)])
        sol, *_ = np.linalg.lstsq(A, (X ** 2).sum(1), rcond=None)
        c = sol[:3]
        rad = np.sqrt(max(sol[3] + c @ c, 0))
        resid = np.abs(np.linalg.norm(X - c, axis=1) - rad)
        if rad > 0 and np.median(resid) < 0.15 * rad:  # a field that changed inside the window does not fit
            fits.append(np.r_[c, rad])
    if not fits:
        return {"c": None, "n_windows": 0}
    F_ = np.array(fits)
    c = np.median(F_[:, :3], 0)
    return {"c": c.round(2).tolist(), "n_windows": len(fits), "spread_uT": float(np.median(np.linalg.norm(F_[:, :3] - c, axis=1))),
            "field_uT_median": float(np.median(F_[:, 3]))}


def rotating_windows(run: Run) -> list[np.ndarray]:
    """Raw (n, 3) magnetometer samples of FIT_WIN_S windows in which the phone turns >= FIT_MIN_ROT_DEG."""
    m, g = run.sensors["mag"], run.sensors["gyr"]
    t = m.t.values
    M = m[["x", "y", "z"]].values
    fs = F._fs(t)
    n = int(FIT_WIN_S * fs)
    ang = np.degrees(np.cumsum(np.linalg.norm(F._on(g, t), axis=1)) / fs)
    return [M[i: i + n] for i in range(0, len(t) - n, n // 2) if ang[i + n - 1] - ang[i] >= FIT_MIN_ROT_DEG]


def hard_iron_joint(windows: list[np.ndarray], iters: int = 3) -> dict:
    """One offset c shared by all windows, each with its own field strength: |x|^2 = 2 x.c + k_w.

    A window that turns about one axis only constrains c in the plane of the turn; pooling windows that
    turn about different axes pins all three components. Windows whose field changed inside them
    (relative misfit > 15 %) are dropped and the fit repeated.
    """
    keep = list(range(len(windows)))
    for _ in range(iters):
        if len(keep) < 2:
            return {"c": None, "n_windows": len(keep)}
        X = np.vstack([windows[i] for i in keep])
        cols = np.zeros((len(X), len(keep)))
        row = 0
        for j, i in enumerate(keep):
            cols[row: row + len(windows[i]), j] = 1
            row += len(windows[i])
        sol, *_ = np.linalg.lstsq(np.column_stack([2 * X, cols]), (X ** 2).sum(1), rcond=None)
        c = sol[:3]
        mis = []
        for j, i in enumerate(keep):
            rad = np.sqrt(max(sol[3 + j] + c @ c, 1e-9))
            mis.append(np.median(np.abs(np.linalg.norm(windows[i] - c, axis=1) - rad)) / rad)
        new = [i for i, e in zip(keep, mis) if e < 0.15]
        if new == keep:
            break
        keep = new
    # conditioning: how well the windows' directions span 3-D (smallest / largest singular value)
    D = np.vstack([w - w.mean(0) for w in (windows[i] for i in keep)])
    sv = np.linalg.svd(D, compute_uv=False)
    return {"c": c.round(2).tolist(), "n_windows": len(keep), "conditioning": round(float(sv[-1] / sv[0]), 3)}


def platform_signatures(run: Run, dwells: list[dict], c) -> list[dict]:
    """|B - c| (uT) per stationary platform dwell: median over its still, trimmed interior."""
    m, g = run.sensors["mag"], run.sensors["gyr"]
    t = m.t.values
    B = np.linalg.norm(m[["x", "y", "z"]].values - np.asarray(c), axis=1)
    still = np.linalg.norm(F._on(g, t), axis=1) < STILL_RAD_S
    out = []
    for d in dwells:
        if d.get("virtual") or d["duration_s"] < DWELL_MIN_S:
            continue
        w = (t > d["t0"] + DWELL_TRIM_S) & (t < d["t1"] - DWELL_TRIM_S) & still
        if w.sum() < 2 * F._fs(t):
            continue
        out.append({"station": d["station"], "B_uT": round(float(np.median(B[w])), 1),
                    "iqr_uT": round(float(np.subtract(*np.percentile(B[w], [75, 25]))), 1), "n_s": round(float(w.sum() / F._fs(t)), 1)})
    return out
