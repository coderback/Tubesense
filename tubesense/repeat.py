"""Repeatability of per-section signals across runs (GO/NO-GO checks a and b).

A section's signal is resampled onto a 0..1 section-fraction grid either by
- "time" (L0): wall-clock fraction depart -> arrive (a phyphox pause becomes NaN), or
- "dist" (L1): dead-reckoned distance fraction (features.dead_reckon).
Pairs of runs are then scored with Pearson r, best r within a +-10 % shift, and DTW on
z-normalised signals, and the same scores are computed against null pairs.
"""
from __future__ import annotations

import numpy as np
from dtaidistance import dtw

from .ingest import Run

N = 400
GRID = np.linspace(0.0, 1.0, N)


def wall_to_t(run: Run, wall: np.ndarray) -> np.ndarray:
    """Inverse of Run.to_wall: Unix wall time -> experiment time (NaN while paused)."""
    wall = np.asarray(wall, dtype=float)
    out = np.full_like(wall, np.nan)
    for _, s in run.segments().iterrows():
        t1 = s.t1 if np.isfinite(s.t1) else 1e12
        m = (wall >= s.wall0) & (wall <= s.wall0 + (t1 - s.t0))
        out[m] = s.t0 + (wall[m] - s.wall0)
    return out


def time_grid(run: Run, sec: dict) -> np.ndarray:
    """Experiment times of the L0 grid for a section (NaN inside a pause)."""
    return wall_to_t(run, sec["depart_wall"] + GRID * (sec["arrive_wall"] - sec["depart_wall"]))


def dist_grid(dr) -> np.ndarray:
    """Experiment times of the L1 grid from a dead_reckon frame (NaN across pause jumps)."""
    f, t = dr.frac.values, dr.t.values
    f = f + np.arange(len(f)) * 1e-12  # strictly increasing for interp
    tg = np.interp(GRID, f, t)
    jump = np.flatnonzero(np.diff(dr.frac.values) > 0.01)  # pause distance step
    for k in jump:
        tg[(GRID > f[k]) & (GRID < f[k + 1])] = np.nan
    return tg


def sample(t: np.ndarray, x: np.ndarray, tg: np.ndarray) -> np.ndarray:
    out = np.interp(np.nan_to_num(tg), t, x)
    out[~np.isfinite(tg)] = np.nan
    return out


def _z(x):
    return (x - np.nanmean(x)) / (np.nanstd(x) + 1e-12)


def pearson(a, b) -> float:
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 20 or np.std(a[m]) == 0 or np.std(b[m]) == 0:
        return np.nan
    return float(np.corrcoef(a[m], b[m])[0, 1])


def best_shift_r(a, b, max_shift: float = 0.10) -> tuple[float, float]:
    """Max Pearson r over shifts of b by up to +-max_shift of the section; (r, shift)."""
    k = int(max_shift * len(a))
    best = (-np.inf, 0.0)
    for s in range(-k, k + 1):
        if s >= 0:
            r = pearson(a[s:], b[: len(b) - s])
        else:
            r = pearson(a[:s], b[-s:])
        if np.isfinite(r) and r > best[0]:
            best = (r, s / len(a))
    return best


def dtw_z(a, b, window: float = 0.10) -> float:
    """DTW distance between z-normalised signals, NaN columns dropped from both."""
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 20:
        return np.nan
    return float(dtw.distance_fast(_z(a[m]).astype(np.double), _z(b[m]).astype(np.double),
                                   window=max(2, int(window * m.sum()))))


def scores(a, b) -> dict:
    r_best, shift = best_shift_r(a, b)
    return {"r": pearson(a, b), "r_best10": r_best, "shift": shift, "dtw": dtw_z(a, b)}


def block_shuffle(x, rng, n_blocks: int = 8) -> np.ndarray:
    blocks = np.array_split(x, n_blocks)
    order = rng.permutation(n_blocks)
    return np.concatenate([blocks[i] for i in order])


def null_scores(a, b, rng, n_shuffle: int = 20) -> dict:
    """Scores of a vs time-reversed b, and the mean over block-shuffled b."""
    rev = scores(a, b[::-1])
    sh = [scores(a, block_shuffle(b, rng)) for _ in range(n_shuffle)]
    return {"reversed": rev, "shuffled": {k: float(np.nanmean([s[k] for s in sh])) for k in ("r", "r_best10", "dtw")}}


# --------------------------------------------------------------------------- per-section signals
def section_signals(run: Run, sec: dict, yaw, mag, gaps: list[tuple[float, float]]) -> dict:
    """All grid signals for one section.

    yaw: features.yaw_rate frame; mag: features.mag_norm frame with column B already chosen
    (calibrated |B|, or bias-corrected |B| for raw runs); gaps: (t, missing_s) pauses.
    Keys: <sig>_<grid> for sig in yaw, head, B, dB, along, v and grid in time, dist.
    """
    from scipy.signal import butter, sosfiltfilt
    from .features import dead_reckon

    dr = dead_reckon(run, sec["depart_t"], sec["arrive_t"], gaps)
    fs = 1 / np.median(np.diff(mag.t.values))
    hp = mag.B.values - sosfiltfilt(butter(2, 0.05, fs=fs, output="sos"), mag.B.values)  # >20 s detrended
    fs_dr = 1 / np.median(np.diff(dr.t.values))
    acc = sosfiltfilt(butter(2, 0.2, fs=fs_dr, output="sos"), dr.a_long.values)  # + = motoring
    along = np.abs(acc)
    out = {"dr_len_m": float(dr.s.iloc[-1]), "v_max": float(dr.v.max())}
    for name, tg in (("time", time_grid(run, sec)), ("dist", dist_grid(dr))):
        out[f"t_{name}"] = tg
        out[f"yaw_{name}"] = sample(yaw.t.values, yaw.yaw.values, tg)
        out[f"B_{name}"] = sample(mag.t.values, mag.B.values, tg)
        out[f"dB_{name}"] = sample(mag.t.values, hp, tg)
        out[f"along_{name}"] = sample(dr.t.values, along, tg)
        out[f"acc_{name}"] = sample(dr.t.values, acc, tg)
        out[f"v_{name}"] = sample(dr.t.values, dr.v.values, tg)
    # heading on a fine time grid, then mapped onto each grid (robust to coarse/uneven grids)
    w = (yaw.t >= sec["depart_t"]) & (yaw.t <= sec["arrive_t"])
    ty, h = yaw.t.values[w], np.degrees(np.cumsum(yaw.yaw.values[w]) * np.median(np.diff(yaw.t.values)))
    for name in ("time", "dist"):
        out[f"head_{name}"] = sample(ty, h - h[0], out[f"t_{name}"])
    out["heading_total_deg"] = float(h[-1] - h[0])
    return out


def pair_table(sig: dict[str, dict], keys: list[str], key: str, transform=None, rng=None) -> list[dict]:
    """All pairs (i < j) of the given section keys: scores + nulls for signal `key`.

    transform(key_id, x) lets the caller map a signal into a common orientation
    (e.g. reverse and sign-flip a westbound run).
    """
    rng = rng or np.random.default_rng(0)
    tf = transform or (lambda k, x: x)
    rows = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            xa, xb = tf(a, sig[a][key]), tf(b, sig[b][key])
            rows.append({"a": a, "b": b, **scores(xa, xb), "null": null_scores(xa, xb, rng)})
    return rows


def cross_table(sig: dict[str, dict], keys_a: list[str], keys_b: list[str], key: str, transform=None) -> list[dict]:
    """Scores for every (a in keys_a, b in keys_b) pair, e.g. different-section nulls."""
    tf = transform or (lambda k, x: x)
    return [{"a": a, "b": b, **scores(tf(a, sig[a][key]), tf(b, sig[b][key]))} for a in keys_a for b in keys_b]


def summarise(rows: list[dict]) -> dict:
    """Mean (and min/max) of each score over a list of pair rows; nulls averaged too."""
    if not rows:
        return {}
    out = {"n_pairs": len(rows)}
    for k in ("r", "r_best10", "dtw"):
        v = np.array([r[k] for r in rows], dtype=float)
        out[k] = {"mean": float(np.nanmean(v)), "min": float(np.nanmin(v)), "max": float(np.nanmax(v))}
    if "null" in rows[0]:
        for n in ("reversed", "shuffled"):
            out[f"null_{n}"] = {k: float(np.nanmean([r["null"][n][k] for r in rows])) for k in ("r", "r_best10", "dtw")}
    return out


def train_state(acc: np.ndarray, v: np.ndarray, thr: float = 0.15) -> np.ndarray:
    """'motoring' / 'braking' / 'coasting' / '' per grid point from signed along-track accel."""
    st = np.full(len(acc), "", dtype=object)
    st[acc > thr] = "motoring"
    st[acc < -thr] = "braking"
    st[(np.abs(acc) < thr / 2) & (v > 3)] = "coasting"
    return st


def rolling_sd(x: np.ndarray, k: int = 7) -> np.ndarray:
    """Local standard deviation over k grid points (NaN-aware)."""
    pad = np.pad(x, k // 2, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(pad, k)
    return np.nanstd(win, axis=1)


# --------------------------------------------------------------------------- spectrum-matched surrogates
def phase_surrogate(x: np.ndarray, rng) -> np.ndarray:
    """Random-phase surrogate with the same power spectrum as x (NaNs bridged, then restored)."""
    m = np.isfinite(x)
    y = np.interp(np.arange(len(x)), np.flatnonzero(m), x[m])
    mu = y.mean()
    F = np.fft.rfft(y - mu)
    ph = np.exp(2j * np.pi * rng.random(len(F)))
    ph[0] = 1.0
    if len(x) % 2 == 0:
        ph[-1] = 1.0
    s = np.fft.irfft(F * ph, n=len(x)) + mu
    s[~m] = np.nan
    return s


def surrogate_test(a, b, rng, n: int = 200, max_shift: float = 0.10) -> dict:
    """Best-shift r of (a, b) against n phase-randomised surrogates of b.

    The different-section null under-states how well two smooth low-passed signals can
    correlate by chance; a spectrum-matched surrogate keeps the smoothness and the broad
    shape statistics, so p is the honest 'could this be chance?' number for one pair.
    """
    r, shift = best_shift_r(a, b, max_shift)
    null = np.array([best_shift_r(a, phase_surrogate(b, rng), max_shift)[0] for _ in range(n)])
    return {"r_best": float(r), "shift": float(shift), "r_plain": pearson(a, b),
            "p": float((1 + (null >= r).sum()) / (n + 1)), "null_p99": float(np.percentile(null, 99)),
            "null_p95": float(np.percentile(null, 95))}


def surrogate_pairs(sig: dict[str, dict], keys_a: list[str], keys_b: list[str] | None, key: str, rng,
                    transform=None, max_shift: float = 0.10, n: int = 200) -> list[dict]:
    """surrogate_test for every pair within keys_a (keys_b None) or across keys_a x keys_b."""
    tf = transform or (lambda k, x: x)
    pairs = ([(a, b) for i, a in enumerate(keys_a) for b in keys_a[i + 1:]] if keys_b is None
             else [(a, b) for a in keys_a for b in keys_b])
    return [{"a": a, "b": b, **surrogate_test(tf(a, sig[a][key]), tf(b, sig[b][key]), rng, n, max_shift)}
            for a, b in pairs]
