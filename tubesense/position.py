"""In-section positioning without GPS: L0 / L1 baselines, L2 multivariate DTW, and curve landmarks.

Every run's section lives on the L0 grid (repeat.GRID, N points depart -> arrive by wall clock;
inside a phyphox pause -> NaN). Positioning a held-out run A against a reference run B means a
monotone warp f_A -> f_B on that grid.

Feature stack (one row per channel, each z-normalised over the section so placement-dependent
gain and offset drop out):
  yaw    gyro (minus the quietest-dwell bias) projected on up, low-passed 1 Hz (features.yaw_rate), deg/s
  head   heading change since departure: running integral of yaw, deg (not detrended). When yaw is
         masked, heading after the mask must not carry the hidden turn: see bridge_heading
  vib    log10 of the 2 s moving RMS of |acc| band-passed 3-40 Hz
  drive  |horizontal accel|: acc minus its projection on the section-mean up (exact for a phone
         that stays put, see features.dead_reckon), vector low-passed 0.3 Hz, then the norm, m/s^2
  baro   pressure minus its section mean, hPa (0.93 Hz sensor, linearly interpolated)
  mag    field anomaly |B - section mean B| (features.field_anomaly) of the 1 Hz low-passed vector;
         calibrated runs are de-stepped first (features.mag_destep), raw runs used as is, uT

Alignment (dtw_warp): DTW with cost(i, j) = sum_c w_c m_c(i) n_c(j) (a_c(i) - b_c(j))^2 / sum_c w_c m_c(i) n_c(j),
where m / n are validity masks of A / B (NaN or masked -> 0), so a masked channel adds no cost and the
other channels' weights are renormalised. Steps (1,1), (1,0), (0,1) with equal weight; Sakoe-Chiba band.
Warp = mean B index per A index on the optimal path, pinned to 0 / 1 at depart / arrive; rows of A where
every channel is masked carry no information and are linearly interpolated between their neighbours.
With slack > 0 the ends are open instead: the path may start / end within slack of B's depart /
arrive (boundaries are only good to ~+-5 s), and uninformed end rows keep the nearest informed offset.
"""
from __future__ import annotations

import numba as nb
import numpy as np
import pandas as pd
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, sosfiltfilt

from . import features as F
from . import repeat as R
from .ingest import Run

FEATURES = ("yaw", "head", "vib", "drive", "baro", "mag")
BAND = 0.15  # Sakoe-Chiba half-width, fraction of N


def _z(x: np.ndarray) -> np.ndarray:
    return (x - np.nanmean(x)) / (np.nanstd(x) + 1e-12)


# --------------------------------------------------------------------------- features
def vib_series(run: Run, lo: float = 3.0, hi: float = 40.0, win_s: float = 2.0) -> pd.DataFrame:
    """log10 moving RMS of band-passed |acc| over the whole run. Columns: t, vib."""
    acc = run.sensors["acc"]
    t = acc.t.values
    fs = F._fs(t)
    a = np.linalg.norm(acc[["x", "y", "z"]].values, axis=1)
    y = sosfiltfilt(butter(4, [lo, hi], "band", fs=fs, output="sos"), a)
    rms = np.sqrt(uniform_filter1d(y ** 2, max(1, int(win_s * fs)), mode="nearest"))
    return pd.DataFrame({"t": t, "vib": np.log10(rms + 1e-6)})


def drive_series(run: Run, t0: float, t1: float, fc: float = 0.3) -> pd.DataFrame:
    """|horizontal accel| over [t0, t1] about the section-mean up, low-passed fc Hz. Columns: t, drive."""
    acc = run.sensors["acc"]
    w = (acc.t >= t0) & (acc.t <= t1)
    t, a = acc.t.values[w], acc.loc[w, ["x", "y", "z"]].values
    u = a.mean(0) / np.linalg.norm(a.mean(0))
    h = F.lowpass(a - np.outer(a @ u, u), F._fs(t), fc)
    return pd.DataFrame({"t": t, "drive": np.linalg.norm(h, axis=1)})


def section_stack(run: Run, sec: dict, yaw: pd.DataFrame, vib: pd.DataFrame, mag: pd.DataFrame) -> dict:
    """Raw and z-normalised feature channels for one section on its L0 grid.

    yaw: features.yaw_rate frame (rad/s); vib: vib_series frame; mag: t,x,y,z frame (de-stepped if
    calibrated). Returns {"t": grid times, "raw": {name: (N,)}, "z": {name: (N,)}}.
    """
    tg = R.time_grid(run, sec)
    t0, t1 = sec["depart_t"], sec["arrive_t"]
    raw = {"yaw": R.sample(yaw.t.values, np.degrees(yaw.yaw.values), tg)}
    w = (yaw.t >= t0) & (yaw.t <= t1)
    ty = yaw.t.values[w]
    h = np.degrees(np.cumsum(yaw.yaw.values[w]) * np.median(np.diff(yaw.t.values)))
    raw["head"] = R.sample(ty, h - h[0], tg)
    raw["vib"] = R.sample(vib.t.values, vib.vib.values, tg)
    dr = drive_series(run, t0, t1)
    raw["drive"] = R.sample(dr.t.values, dr.drive.values, tg)
    b = run.sensors["baro"]
    p = R.sample(b.t.values, b.p.values, tg)
    raw["baro"] = p - np.nanmean(p)
    mv = F.lowpass(mag[["x", "y", "z"]].values, F._fs(mag.t.values), 1.0)
    V = np.column_stack([R.sample(mag.t.values, mv[:, i], tg) for i in range(3)])
    raw["mag"] = F.field_anomaly(V)
    return {"t": tg, "dur": sec["arrive_wall"] - sec["depart_wall"], "raw": raw,
            "z": {k: _z(v) for k, v in raw.items()}}


def bridge_heading(sig: dict, mask: np.ndarray) -> dict:
    """Copy of a section stack whose heading carries no information from yaw inside mask.

    Heading after the masked span is otherwise the integral of the hidden yaw (it leaks the turn).
    Here the turn across the span is replaced by yaw bridged linearly between the span's visible
    edges (yaw 0 at depart), heading inside the span is NaN, and head is re-z-normalised.
    """
    idx = np.flatnonzero(mask)
    y, h = sig["raw"]["yaw"], sig["raw"]["head"]
    ok = np.isfinite(y) & np.isfinite(h)
    after = np.flatnonzero(ok[idx[-1] + 1:]) + idx[-1] + 1 if len(idx) else []
    if not len(idx) or not len(after):
        return sig  # nothing after the span to leak into
    a = after[0]
    before = np.flatnonzero(ok[:idx[0]])
    b, hb, yb, fb = (before[-1], h[before[-1]], y[before[-1]], R.GRID[before[-1]]) if len(before) else (-1, 0.0, 0.0, 0.0)
    turn = 0.5 * (yb + y[a]) * sig["dur"] * (R.GRID[a] - fb)
    hn = h.copy()
    hn[a:] = h[a:] - h[a] + hb + turn
    hn[b + 1:a] = np.nan
    return {**sig, "raw": {**sig["raw"], "head": hn}, "z": {**sig["z"], "head": _z(hn)}}


# --------------------------------------------------------------------------- DTW
@nb.njit(cache=True)
def _dtw_path(A, WA, B, MB, band, pen, slack):
    C, n = A.shape
    m = B.shape[1]
    D = np.full((n + 1, m + 1), np.inf)
    D[0, :slack + 1] = 0.0  # open begin: A's first row may meet any of B's first slack columns
    for i in range(1, n + 1):
        jc = int(round((i - 1) * (m - 1) / max(n - 1, 1))) + 1
        for j in range(max(1, jc - band), min(m, jc + band) + 1):
            num, den = 0.0, 0.0
            for c in range(C):
                w = WA[c, i - 1] * MB[c, j - 1]
                if w > 0:
                    d = A[c, i - 1] - B[c, j - 1]
                    num += w * d * d
                    den += w
            cost = num / den if den > 0 else 0.0
            best = D[i - 1, j - 1]
            if D[i - 1, j] + pen < best:
                best = D[i - 1, j] + pen
            if D[i, j - 1] + pen < best:
                best = D[i, j - 1] + pen
            D[i, j] = cost + best
    pi = np.empty(n + m, np.int64)
    pj = np.empty(n + m, np.int64)
    i, j, k = n, m, 0
    for jj in range(m - slack, m):  # open end
        if D[n, jj] < D[n, j]:
            j = jj
    end = D[n, j]
    while i > 0 and j > 0:
        pi[k], pj[k] = i - 1, j - 1
        k += 1
        d, u, l = D[i - 1, j - 1], D[i - 1, j] + pen, D[i, j - 1] + pen
        if d <= u and d <= l:  # ties -> diagonal
            i, j = i - 1, j - 1
        elif u <= l:
            i -= 1
        else:
            j -= 1
    return pi[:k][::-1], pj[:k][::-1], end


def stack(sig: dict, names, weights=None) -> tuple[np.ndarray, np.ndarray]:
    """(C, N) values (NaN -> 0) and (C, N) weights x validity for the named z-channels."""
    w = np.ones(len(names)) if weights is None else np.asarray(weights, float)
    X = np.vstack([sig["z"][k] for k in names])
    M = np.isfinite(X) * w[:, None]
    return np.nan_to_num(X), M


def dtw_warp(A: np.ndarray, WA: np.ndarray, B: np.ndarray, MB: np.ndarray, band: float = BAND,
             step_penalty: float = 0.0, slack: float = 0.0) -> np.ndarray:
    """Warp of A's grid onto B's grid, as B fractions (0..1) per A index. See module docstring.

    step_penalty > 0 adds that cost to every non-diagonal step (smoother warps; 0 = plain DTW).
    slack > 0 (fraction of N) opens both ends instead of pinning them.
    """
    n, m = A.shape[1], B.shape[1]
    sl = int(round(slack * m))
    pi, pj, _ = _dtw_path(A, WA, B, MB, max(2, int(round(band * n))), float(step_penalty), sl)
    w = np.bincount(pi, weights=pj, minlength=n) / np.maximum(np.bincount(pi, minlength=n), 1)
    info = WA.sum(0) > 0
    idx = np.arange(n)
    if sl == 0:
        w[0], w[-1] = 0.0, m - 1.0  # both runs depart and arrive together: the ends are known anchors
        info[0] = info[-1] = True
    if not info.any():
        return idx / (n - 1)
    if (~info).any():  # interpolate the offset from the diagonal; past the ends it stays constant
        off = np.interp(idx, idx[info], w[info] - idx[info] * (m - 1) / (n - 1))
        w[~info] = idx[~info] * (m - 1) / (n - 1) + off[~info]
    return np.clip(w / (m - 1), 0, 1)


def l1_warp(dA: np.ndarray, dB: np.ndarray) -> np.ndarray:
    """L1 warp: A's dead-reckoned distance fraction on its grid -> B fraction at equal distance fraction."""
    gA = np.interp(np.arange(len(dA)), np.flatnonzero(np.isfinite(dA)), dA[np.isfinite(dA)])
    gB = np.interp(np.arange(len(dB)), np.flatnonzero(np.isfinite(dB)), dB[np.isfinite(dB)])
    gB = np.maximum.accumulate(gB) + np.arange(len(gB)) * 1e-9
    return np.interp(gA, gB, R.GRID)


def warp_at(warp: np.ndarray, f) -> np.ndarray:
    return np.interp(f, R.GRID, warp)


def synthetic_check(rng, n: int = R.N, n_ch: int = 3, amp: float = 0.06) -> dict:
    """Recover a known smooth monotone warp from a noisy multichannel signal; error in grid bins."""
    x = np.linspace(0, 1, n)
    base = [np.convolve(rng.standard_normal(n + 60), np.hanning(25), "same")[30:30 + n] for _ in range(n_ch)]
    # true warp: B fraction of A sample i; fixed ends, slope 1 + 2*pi*amp*(cos + cos) > 0 for amp < 0.08
    true = x + amp * (np.sin(2 * np.pi * x) + 0.5 * np.sin(4 * np.pi * x))
    B = np.vstack([_z(b) for b in base])
    A = np.vstack([_z(np.interp(true, x, b) + 0.15 * rng.standard_normal(n)) for b in base])
    est = dtw_warp(A, np.ones_like(A), B, np.ones_like(B))
    err = np.abs(est - true) * (n - 1)
    # masked variant: drop all channels over 10 % of A, recovery there is interpolation only
    WA = np.ones_like(A)
    WA[:, int(0.45 * n): int(0.55 * n)] = 0
    est_m = dtw_warp(A, WA, B, np.ones_like(B))
    err_m = np.abs(est_m - true) * (n - 1)
    # harder case: one clean channel masked over the window while an unrelated random-walk channel
    # stays, so inside the window the noise gets the full weight (yaw masked + a noisy sensor)
    win = slice(int(0.45 * n), int(0.55 * n))
    A2 = np.vstack([A[0], _z(rng.standard_normal(n).cumsum())])
    B2 = np.vstack([B[0], _z(rng.standard_normal(n).cumsum())])
    W2 = np.ones_like(A2)
    W2[0, win] = 0
    e_noise = np.abs(dtw_warp(A2, W2, B2, np.ones_like(B2))[win] - true[win]) * (n - 1)
    W2[1] = 0
    e_alone = np.abs(dtw_warp(A2, W2, B2, np.ones_like(B2))[win] - true[win]) * (n - 1)
    return {"max_warp_bins": float(np.max(np.abs(true - x)) * (n - 1)), "median_err_bins": float(np.median(err)),
            "p90_err_bins": float(np.percentile(err, 90)), "max_err_bins": float(err.max()),
            "masked_median_err_bins": float(np.median(err_m)), "masked_max_err_bins": float(err_m.max()),
            "one_masked_plus_noise_window_median_err_bins": float(np.median(e_noise)),
            "one_masked_alone_window_median_err_bins": float(np.median(e_alone))}


# --------------------------------------------------------------------------- landmarks
def curve_landmarks(run: Run, sec: dict, yaw: pd.DataFrame, thr: float = 0.6, min_s: float = 4.0,
                    smooth_s: float = 1.0, edge: str = "level") -> list[dict]:
    """Curve onsets and ends from yaw rate, as L0 section fractions.

    A curve = a run of >= min_s with |yaw| > thr deg/s (1 s moving average) of one sign; its
    level = median |yaw| over that run. edge="level": onset / end = where |yaw| of that sign falls
    below half the level, walking outwards (speed-dependent: a train slowing in a curve ends it
    early). edge="heading": walk out to 0.2 x level, then onset / end = where the heading reaches
    15 % / 85 % of the curve's turn (position-locked, so speed-independent); overlapping curves of
    one sign are merged. Fraction = wall-clock position between depart and arrive. Censored edges
    are dropped: a walk that reaches the section start/end or a phyphox pause (time frozen, so the
    true edge is unknown).
    """
    t0, t1 = sec["depart_t"], sec["arrive_t"]
    w = (yaw.t >= t0) & (yaw.t <= t1)
    t = yaw.t.values[w]
    fs = F._fs(t)
    y = uniform_filter1d(np.degrees(yaw.yaw.values[w]), max(1, int(smooth_s * fs)), mode="nearest")
    frac = (run.to_wall(t) - sec["depart_wall"]) / (sec["arrive_wall"] - sec["depart_wall"])
    cut = np.flatnonzero(np.diff(frac) > 0.01) + 1  # first sample after a pause
    bounds = np.r_[0, cut, len(t)]
    out = []
    for s0, s1 in zip(bounds[:-1], bounds[1:]):
        ys = y[s0:s1]
        for sgn in (1, -1):
            spans = []
            on = sgn * ys > thr
            edges = np.flatnonzero(np.diff(np.r_[0, on.astype(int), 0]))
            for a, b in zip(edges[::2], edges[1::2]):
                if b - a < min_s * fs:
                    continue
                lev = np.median(sgn * ys[a:b])
                k = 0.5 if edge == "level" else 0.2
                i0 = a
                while i0 > 0 and sgn * ys[i0 - 1] > lev * k:
                    i0 -= 1
                i1 = b - 1
                while i1 < len(ys) - 1 and sgn * ys[i1 + 1] > lev * k:
                    i1 += 1
                if edge == "heading":
                    if spans and i0 <= spans[-1][1]:
                        spans[-1][1] = max(spans[-1][1], i1)
                    else:
                        spans.append([i0, i1])
                    continue
                for kind, i, censored in (("on", i0, i0 == 0), ("off", i1, i1 == len(ys) - 1)):
                    if not censored:
                        out.append({"frac": float(frac[s0 + i]), "t": float(t[s0 + i]), "kind": kind, "sign": sgn,
                                    "level_deg_s": round(float(sgn * lev), 2)})
            for i0, i1 in spans:
                if i0 == 0 or i1 == len(ys) - 1:
                    continue  # turn not seen in full
                h = np.cumsum(sgn * ys[i0:i1 + 1])
                for kind, q in (("on", 0.15), ("off", 0.85)):
                    i = s0 + i0 + int(np.searchsorted(h, q * h[-1]))
                    out.append({"frac": float(frac[i]), "t": float(t[i]), "kind": kind, "sign": sgn,
                                "turn_deg": round(float(h[-1] / fs), 1)})
    return sorted(out, key=lambda d: d["frac"])


def match_landmarks(la: list[dict], lb: list[dict], tol: float = 0.10) -> list[tuple[int, int]]:
    """Pairs (i, j) of landmarks A[i] ~ B[j]: by order when kinds/signs line up, else nearest within tol."""
    sig = lambda L: [(d["kind"], d["sign"]) for d in L]
    if len(la) == len(lb) and sig(la) == sig(lb):
        return list(zip(range(len(la)), range(len(lb))))
    pairs, used = [], set()
    for i, a in enumerate(la):
        c = [(abs(b["frac"] - a["frac"]), j) for j, b in enumerate(lb)
             if j not in used and (b["kind"], b["sign"]) == (a["kind"], a["sign"]) and abs(b["frac"] - a["frac"]) <= tol]
        if c:
            j = min(c)[1]
            used.add(j)
            pairs.append((i, j))
    return pairs
