"""Orientation-independent motion and magnetic features from phone IMU streams.

All functions work in the phone frame and need no knowledge of how the phone sits:
- "up" is the low-passed accelerometer (phyphox Accelerometer includes g, and the
  specific force at rest points up), re-estimated continuously so a phone that is
  moved mid-ride keeps a correct vertical.
- yaw rate = gyro projected on up: positive = anticlockwise seen from above = left turn,
  whatever way up the phone is.
Time `t` is phyphox experiment time throughout (see ingest.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import butter, sosfiltfilt

from .ingest import Run


def _fs(t: np.ndarray) -> float:
    return 1.0 / np.median(np.diff(t))


def lowpass(x: np.ndarray, fs: float, fc: float, order: int = 4) -> np.ndarray:
    sos = butter(order, fc, btype="low", fs=fs, output="sos")
    return sosfiltfilt(sos, x, axis=0)


def _on(df: pd.DataFrame, t: np.ndarray) -> np.ndarray:
    """Interpolate a t,x,y,z frame onto times t -> (n, 3)."""
    return np.column_stack([np.interp(t, df.t.values, df[c].values) for c in "xyz"])


def up_vector(run: Run, t: np.ndarray, fc: float = 0.4) -> np.ndarray:
    """Unit 'up' vector in the phone frame at times t (low-passed specific force)."""
    acc = run.sensors["acc"]
    a = acc[["x", "y", "z"]].values
    g = lowpass(a, _fs(acc.t.values), fc)
    g = _on(pd.DataFrame({"t": acc.t, "x": g[:, 0], "y": g[:, 1], "z": g[:, 2]}), t)
    return g / np.linalg.norm(g, axis=1, keepdims=True)


def gyro_bias(run: Run, quiet: list[tuple[float, float]]) -> np.ndarray:
    """Median gyro vector over the quietest of the given stationary windows (rad/s)."""
    gyr = run.sensors["gyr"]
    best, best_sd = np.zeros(3), np.inf
    for t0, t1 in quiet:
        w = (gyr.t > t0 + 1) & (gyr.t < t1 - 1)
        if w.sum() < 200:
            continue
        sd = gyr.loc[w, ["x", "y", "z"]].std().sum()
        if sd < best_sd:
            best, best_sd = gyr.loc[w, ["x", "y", "z"]].median().values, sd
    return best


def yaw_rate(run: Run, bias: np.ndarray | None = None, fc: float = 1.0) -> pd.DataFrame:
    """Yaw rate about local vertical (rad/s, + = left turn), low-passed to fc Hz.

    Columns: t, yaw.
    """
    gyr = run.sensors["gyr"]
    t = gyr.t.values
    w = gyr[["x", "y", "z"]].values - (0 if bias is None else bias)
    yaw = np.einsum("ij,ij->i", w, up_vector(run, t))
    return pd.DataFrame({"t": t, "yaw": lowpass(yaw, _fs(t), fc)})


def dead_reckon(run: Run, t0: float, t1: float, gaps: list[tuple[float, float]] = (), fc: float = 0.5) -> pd.DataFrame:
    """L1 speed/distance proxy for one section: integrate along-track accel with v=0 at both ends.

    Gravity = mean specific force over [t0, t1]: with v=0 at both ends the train's own
    mean acceleration is zero, so this is exact for a phone that stays put (a low-pass
    "up" would absorb the slow traction/braking accel). Along-track axis = first principal
    axis of the horizontal residual; sign chosen so mean speed > 0; linear drift removed
    so v(t1) = 0; v clipped at 0. `gaps` = (t, missing_s) pauses: speed held across them.
    Returns t, a_long (m/s^2), v (m/s), s (m, unscaled) and frac (s / s_end).
    """
    acc = run.sensors["acc"]
    w = (acc.t >= t0) & (acc.t <= t1)
    t = acc.t.values[w]
    a3 = acc.loc[w, ["x", "y", "z"]].values
    g = a3.mean(0)
    u = g / np.linalg.norm(g)
    lin = a3 - g
    h = lowpass(lin - np.outer(lin @ u, u), _fs(t), fc)
    axis = np.linalg.svd(h - h.mean(0), full_matrices=False)[2][0]
    a = h @ axis
    dt = np.diff(t, prepend=t[0])
    v = np.cumsum(a * dt)
    if v.mean() < 0:
        a, v = -a, -v
    v = v - (t - t[0]) / (t[-1] - t[0]) * v[-1]
    v = np.clip(v, 0, None)
    ds = v * dt
    for tp, miss in gaps:  # distance covered while phyphox was paused
        k = np.searchsorted(t, tp)
        if 0 < k < len(t):
            ds[k] += v[k - 1] * miss
    s = np.cumsum(ds)
    return pd.DataFrame({"t": t, "a_long": a, "v": v, "s": s, "frac": s / s[-1]})


def mag_destep(run: Run, min_ut: float = 8.0, k: float = 8.0, w: int = 20,
               calm_rad_s: float = 0.5) -> tuple[pd.DataFrame, list[dict]]:
    """Remove one-sample offset steps from the magnetometer vector (iOS calibration updates).

    A step is a one-sample jump > `min_ut` and > k x the local sample-to-sample change, with the
    gyro calm, whose new level persists (not a handling spike). Each step is subtracted from the
    rest of the run, so the output carries one unknown constant bias, like a raw stream.
    Gradual re-convergence of the iOS estimate is not removed.
    Returns the corrected t,x,y,z frame and the steps [{t, step_uT: [x, y, z], size_uT}].
    """
    from scipy.ndimage import median_filter

    mag = run.sensors["mag"]
    t = mag.t.values
    M = mag[["x", "y", "z"]].values.copy()
    gn = np.linalg.norm(_on(run.sensors["gyr"], t), axis=1)
    dn = np.linalg.norm(np.diff(M, axis=0), axis=1)
    loc = median_filter(dn, size=101, mode="nearest")
    steps, last = [], -np.inf
    for i in np.flatnonzero((dn > min_ut) & (dn > k * (loc + 0.3))):
        if i < w or i + 1 + 5 * w > len(M) or gn[max(0, i - 10): i + 10].max() > calm_rad_s or t[i] - last < 0.5:
            continue
        pre, post = np.median(M[i - w + 1: i + 1], 0), np.median(M[i + 1: i + 1 + w], 0)
        late = np.median(M[i + 1 + 4 * w: i + 1 + 5 * w], 0)
        s = post - pre
        if np.linalg.norm(s) > 0.6 * dn[i] and np.linalg.norm(late - post) < 0.7 * np.linalg.norm(s):
            steps.append((i + 1, s))
            last = t[i]
    for i, s in steps:
        M[i:] -= s
    out = pd.DataFrame({"t": t, "x": M[:, 0], "y": M[:, 1], "z": M[:, 2]})
    return out, [{"t": round(float(t[i]), 2), "step_uT": np.round(s, 1).tolist(),
                  "size_uT": round(float(np.linalg.norm(s)), 1)} for i, s in steps]


def field_anomaly(V: np.ndarray) -> np.ndarray:
    """|B - mean(B)| over a section's (n, 3) field samples: free of any constant bias.

    Valid for raw and calibrated streams alike, as long as the phone does not rotate in the
    train during the section (rotation-invariant otherwise).
    """
    return np.linalg.norm(V - np.nanmean(V, axis=0), axis=1)


def mag_norm(run: Run, fc: float = 1.0) -> pd.DataFrame:
    """|B| (uT) low-passed, plus the low-passed vector. Columns: t, B, x, y, z."""
    mag = run.sensors["mag"]
    t = mag.t.values
    v = lowpass(mag[["x", "y", "z"]].values, _fs(t), fc)
    return pd.DataFrame({"t": t, "B": np.linalg.norm(v, axis=1), "x": v[:, 0], "y": v[:, 1], "z": v[:, 2]})
