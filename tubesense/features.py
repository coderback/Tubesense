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


def mag_norm(run: Run, fc: float = 1.0) -> pd.DataFrame:
    """|B| (uT) low-passed, plus the low-passed vector. Columns: t, B, x, y, z."""
    mag = run.sensors["mag"]
    t = mag.t.values
    v = lowpass(mag[["x", "y", "z"]].values, _fs(t), fc)
    return pd.DataFrame({"t": t, "B": np.linalg.norm(v, axis=1), "x": v[:, 0], "y": v[:, 1], "z": v[:, 2]})
