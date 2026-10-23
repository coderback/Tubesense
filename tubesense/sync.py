"""Sensor <-> audio synchronisation.

Model: audio_t = a + b * (wall - wall0), where wall is Unix time from run.to_wall()
and wall0 is the first START event. Both recordings come from the same iPhone, so
b ~ 1; a is the audio time at which phyphox started.

Two independent estimates:
- Triple taps: clusters of 3 sharp spikes in high-passed |acc| near each end, matched
  to 3 audio transients with the same inter-tap intervals (to ~20 ms).
- Envelope cross-correlation: a coarse lag from 1 s log-envelopes of vibration vs audio
  level (several band combinations, consensus), refined at 10 ms with short-time
  envelope fluctuations (15-45 Hz accel vs 20-60 / 50-400 Hz audio).
Taps win when found at both ends and agree with the correlation; otherwise the
correlation is used and the result is flagged.
"""
from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import numpy as np
from scipy import ndimage, signal

from .ingest import ROOT, Run

SYNC_JSON = ROOT / "outputs" / "sync.json"
UNIQUE_MARGIN = 0.05  # coarse r at the chosen lag must beat any lag > 5 s away by this much
FS_IMU = 99.2  # Hz, measured


# ---------- envelopes ----------

def _bin_wall(run: Run, t: np.ndarray, v: np.ndarray, dt: float):
    """RMS of v in dt bins on wall time relative to wall0. Returns (env, valid mask)."""
    w = run.to_wall(t) - run.segments().wall0.iloc[0]
    n = int(np.ceil(np.nanmax(w) / dt)) + 1
    idx = np.floor(w / dt).astype(int)
    s = np.bincount(idx, v ** 2, n)
    c = np.bincount(idx, None, n)
    env = np.full(n, np.nan)
    env[c > 0] = np.sqrt(s[c > 0] / c[c > 0])
    return env, c > 0


def imu_band(run: Run, lo: float, hi: float | None, key: str = "acc") -> np.ndarray:
    """|band-passed xyz| of an IMU sensor (hi=None -> high-pass)."""
    df = run.sensors[key]
    ny = FS_IMU / 2
    sos = (signal.butter(2, lo / ny, "high", output="sos") if hi is None
           else signal.butter(2, [lo / ny, hi / ny], "band", output="sos"))
    return np.linalg.norm(signal.sosfiltfilt(sos, df[["x", "y", "z"]].values, axis=0), axis=1)


def imu_envelope(run: Run, dt: float, lo: float, hi: float | None, key: str = "acc"):
    return _bin_wall(run, run.sensors[key].t.values, imu_band(run, lo, hi, key), dt)


def audio_envelope(y: np.ndarray, sr: int, dt: float, lo: float, hi: float) -> np.ndarray:
    """Band RMS of audio in dt frames (frame i covers [i*dt, (i+1)*dt))."""
    sos = signal.butter(4, [lo / (sr / 2), min(hi / (sr / 2), 0.99)], "band", output="sos")
    yb = signal.sosfilt(sos, y)
    h = int(round(sr * dt))
    n = len(yb) // h
    return np.sqrt((yb[: n * h].reshape(n, h) ** 2).mean(1))


def _norm_log(e: np.ndarray, smooth: int, detrend: int) -> np.ndarray:
    e = np.log(np.where(np.isfinite(e), e, np.nanmedian(e)) + 1e-9)
    if smooth > 1:
        e = ndimage.uniform_filter1d(e, smooth)
    if detrend:
        e = e - ndimage.uniform_filter1d(e, detrend)
    return e


def _xcorr(s: np.ndarray, m: np.ndarray, q: np.ndarray, lags: np.ndarray) -> np.ndarray:
    """Pearson r between q[l:l+len(s)] and s over mask m, for each lag (in samples).

    Partial overlap is allowed (audio may start after phyphox, e.g. during a pause)
    as long as >= 80% of the valid sensor samples overlap the audio.
    """
    n = len(s)
    out = np.full(len(lags), np.nan)
    need = 0.8 * m.sum()
    for i, l in enumerate(lags):
        i0, i1 = max(0, -l), min(n, len(q) - l)
        if i1 <= i0:
            continue
        mm = m[i0:i1]
        if mm.sum() < need:
            continue
        out[i] = np.corrcoef(q[l + i0: l + i1][mm], s[i0:i1][mm])[0, 1]
    return out


# ---------- envelope cross-correlation ----------

COARSE_IMU = [("acc", 5, 45), ("acc", 15, 45), ("gyr", 2, 45)]
COARSE_AUD = [(20, 200), (200, 2000), (50, 8000)]


def coarse_lag(run: Run, y: np.ndarray, sr: int, dt: float = 0.1) -> dict:
    """Consensus lag (s) over IMU x audio band combinations, 1 s smoothing, 60 s detrend."""
    aud = {b: _norm_log(audio_envelope(y, sr, dt, *b), 10, 600) for b in COARSE_AUD}
    lags_all, curves = [], []
    for key, lo, hi in COARSE_IMU:
        e, m = imu_envelope(run, dt, lo, hi, key)
        s = _norm_log(e, 10, 600)
        for b, q in aud.items():
            lags = np.arange(int(-20 / dt), len(q) - len(s) + 1)
            c = _xcorr(s, m, q, lags)
            k = np.nanargmax(c)
            lags_all.append((lags[k] * dt, c[k], f"{key}{lo}-{hi}/aud{b[0]}-{b[1]}"))
            curves.append((lags * dt, c))
    L = np.array([l for l, _, _ in lags_all])
    # consensus: the lag with most other combos within 1 s
    support = np.array([(np.abs(L - l) <= 1.0).sum() for l in L])
    near = np.abs(L - L[np.argmax(support)]) <= 1.0
    lag = float(np.median(L[near]))
    max_lag = (len(y) / sr) - (len(m) * dt)
    # Peak uniqueness: r at the consensus lag minus the best r more than 5 s away, per combo.
    margins = []
    for lg, c in curves:
        at = np.abs(lg - lag) <= 1.0
        far = np.abs(lg - lag) > 5.0
        if at.any() and far.any() and np.isfinite(c[far]).any():
            margins.append(float(np.nanmax(c[at]) - np.nanmax(c[far])))
    return {"lag": lag, "support": int(near.sum()), "n": len(L),
            "spread": float(np.ptp(L[near])), "at_boundary": bool(lag < 0 or lag > max_lag),
            "peak_margin": float(np.median(margins)) if margins else float("nan"),
            "combos": [(round(l, 2), round(float(c), 3), k) for l, c, k in lags_all]}


def fine_lag(run: Run, y: np.ndarray, sr: int, c0: float, search: float = 3.0,
             dt: float = 0.01) -> dict:
    """10 ms lag from short-time envelope fluctuations around coarse lag c0.

    Reports the lag for the whole run and for the first/last third (consistency check).
    z = (peak - median) / std of the correlation over the search window.
    """
    e, m = imu_envelope(run, dt, 15, 45, "acc")
    m = m & ~ndimage.binary_dilation(~m, np.ones(5))   # drop bins at pause edges
    s = _norm_log(e, 1, 50)
    lags = np.arange(int((c0 - search) / dt), int((c0 + search) / dt) + 1)
    best = None
    for band in [(20, 60), (50, 400)]:
        q = _norm_log(audio_envelope(y, sr, dt, *band), 1, 50)
        res = {"band": list(band)}
        n = len(s)
        for part, sl in [("all", slice(0, n)), ("first", slice(0, n // 3)), ("last", slice(2 * n // 3, n))]:
            mm = np.zeros_like(m)
            mm[sl] = m[sl]
            c = _xcorr(s, mm, q, lags)
            k = np.nanargmax(c)
            z = (c[k] - np.nanmedian(c)) / np.nanstd(c)
            res[part] = {"lag": float(lags[k] * dt), "r": float(c[k]), "z": float(z)}
        if best is None or res["all"]["z"] > best["all"]["z"]:
            best = res
    return best


# ---------- triple taps ----------

def accel_spikes(run: Run, k: float = 6.0, floor: float = 1.5):
    """Sharp spikes in 8 Hz high-passed |acc|: (wall-rel times, heights)."""
    hp = imu_band(run, 8, None, "acc")
    w = run.to_wall(run.sensors["acc"].t.values) - run.segments().wall0.iloc[0]
    base = ndimage.median_filter(hp, 301)
    pk, _ = signal.find_peaks(hp, height=np.maximum(floor, k * base), distance=8)
    return w[pk], hp[pk]


def audio_transients(y: np.ndarray, sr: int, t0: float, t1: float, thr: float = 10.0,
                     hop: float = 0.002):
    """Impulsive onsets (1-8 kHz energy jump over a 0.3 s running median) in [t0, t1] s."""
    i0, i1 = max(0, int(t0 * sr)), min(len(y), int(t1 * sr))
    e = audio_envelope(y[i0:i1], sr, hop, 1000, 8000)
    d = 10 * np.log10(e ** 2 + 1e-12)
    d = d - ndimage.median_filter(d, int(0.3 / hop))
    pk, _ = signal.find_peaks(d, height=thr, distance=int(0.08 / hop))
    return t0 + (pk + 0.5) * hop, d[pk]


def triples(t: np.ndarray, gmin: float = 0.12, gmax: float = 1.0):
    """All time triples with both gaps in [gmin, gmax] s."""
    out = []
    for i, j, k in combinations(range(len(t)), 3):
        g1, g2 = t[j] - t[i], t[k] - t[j]
        if gmin <= g1 <= gmax and gmin <= g2 <= gmax:
            out.append((i, j, k))
    return out


def match_taps(ts: np.ndarray, hs: np.ndarray, ta: np.ndarray, da: np.ndarray,
               lag0: float, window: float, tol: float = 0.02):
    """Best accel triple <-> audio triple with matching gaps and offset within lag0 +- window.

    Returns dict(sensor=[3], audio=[3], mismatch_ms, offset) or None.
    """
    best = None
    A = triples(ta)
    for tri in triples(ts):
        s = ts[list(tri)]
        for atri in A:
            a = ta[list(atri)]
            mis = np.max(np.abs(np.diff(a) - np.diff(s)))
            off = float(np.mean(a - s))
            if mis > tol or abs(off - lag0) > window:
                continue
            score = mis - 1e-3 * (hs[list(tri)].min() + da[list(atri)].min() / 10)
            if best is None or score < best["score"]:
                best = {"sensor": s.tolist(), "audio": a.tolist(), "mismatch_ms": float(mis * 1e3),
                        "offset": off, "score": float(score)}
    return best


# ---------- full sync for a run ----------

def sync_run(run: Run, y: np.ndarray, sr: int, edge_s: float = 60.0) -> dict:
    T = float(np.nanmax(run.to_wall(run.sensors["acc"].t.values)) - run.segments().wall0.iloc[0])
    dur = len(y) / sr
    co = coarse_lag(run, y, sr)
    fi = fine_lag(run, y, sr, co["lag"])
    fa = fi["all"]
    fine_ok = fa["z"] >= 4.0 and abs(fa["lag"] - co["lag"]) <= 1.0
    parts_agree = all(abs(fi[k]["lag"] - fa["lag"]) <= 0.1 for k in ("first", "last"))
    lag0 = fa["lag"] if fine_ok else co["lag"]
    win = 0.3 if fine_ok else 2.0

    ts, hs = accel_spikes(run)
    taps, rejected = {}, []
    for end, (lo, hi) in {"start": (0, edge_s), "end": (T - edge_s, T)}.items():
        m = (ts >= lo) & (ts <= hi)
        ta, da = audio_transients(y, sr, lo + lag0 - win - 1, hi + lag0 + win + 1)
        taps[end] = match_taps(ts[m], hs[m], ta, da, lag0, win)
    # A single 3-tap interval match within a +-2 s window happens by chance among
    # train clatter, so accept taps only if both ends agree or the 10 ms xcorr confirms.
    if taps["start"] and taps["end"]:
        if abs(taps["start"]["offset"] - taps["end"]["offset"]) > 0.05:
            rejected = [f"{e} triple offset {taps[e]['offset']:.3f}s" for e in taps]
            taps = {"start": None, "end": None}
    for e in ("start", "end"):
        t_ = taps[e]
        if t_ and not (taps["start"] and taps["end"]) and not (fine_ok and abs(t_["offset"] - fa["lag"]) < 0.15):
            rejected.append(f"{e} triple offset {t_['offset']:.3f}s (unconfirmed)")
            taps[e] = None

    notes = []
    if rejected:
        notes.append("rejected chance tap matches: " + ", ".join(rejected))
    if run.segments().shape[0] > 1:
        notes.append(f"{run.segments().shape[0] - 1} phyphox pause(s); sensors mapped via to_wall, "
                     "audio inside pauses has no sensor data")
    s_t = np.array(sum(([*taps[e]["sensor"]] for e in taps if taps[e]), []))
    a_t = np.array(sum(([*taps[e]["audio"]] for e in taps if taps[e]), []))
    if taps["start"] and taps["end"]:
        b, a = np.polyfit(s_t, a_t, 1)
        method = "taps_both_ends"
    elif taps["start"] or taps["end"]:
        b, a = 1.0, float(np.mean(a_t - s_t))
        method = "taps_one_end"
        notes.append(f"taps only at {'start' if taps['start'] else 'end'}; b fixed at 1")
    else:
        b, a = 1.0, lag0
        method = "xcorr_fine" if fine_ok else "xcorr_coarse"
        notes.append("no tap triple matched at either end; b fixed at 1 (same-device clocks)")
    res = (a + b * s_t - a_t) * 1e3 if len(s_t) else np.array([])

    unique = co["peak_margin"] >= UNIQUE_MARGIN and co["support"] >= co["n"] // 2 + 1
    if method == "taps_both_ends" and np.abs(res).max() < 20 and fine_ok and abs(a - fa["lag"]) < 0.1:
        conf, unc = "high", 0.02
    elif method.startswith("taps") or (method == "xcorr_fine" and parts_agree and fa["z"] >= 4.5 and unique):
        conf, unc = "medium", 0.1
    elif unique and fine_ok and all(abs(fi[k]["lag"] - fa["lag"]) <= 1.0 for k in ("first", "last")):
        conf, unc = "low", 0.5
    else:
        conf, unc = "low", 5.0
        notes.append(("correlation peak not unique (median margin %.3f over the best lag >5 s away)"
                      % co["peak_margin"]) if not unique else "fine lag unconfirmed or first/last thirds disagree by >1 s")
        notes.append("offset only constrained to ~+-5 s (station-dip alignment)")
    if method == "xcorr_fine" and not parts_agree:
        notes.append(f"10 ms xcorr first/last thirds disagree ({fi['first']['lag']:.2f}/{fi['last']['lag']:.2f} s)")
    if method == "xcorr_coarse":
        notes.append(f"10 ms xcorr inconclusive (z={fa['z']:.1f})")
    if co["support"] < co["n"] // 2 + 1:
        notes.append(f"coarse band combos disagree ({co['support']}/{co['n']} within 1 s); "
                     "offset may be wrong by seconds")
    t_aud = a + b * (run.to_wall(run.sensors["acc"].t.values) - run.segments().wall0.iloc[0])
    covered = float(np.mean((t_aud >= 0) & (t_aud <= dur)))
    if covered < 0.999:
        notes.append(f"only {covered:.1%} of sensor samples fall inside the audio "
                     f"(audio starts {-a:.1f} s after phyphox START)" if a < 0 else
                     f"only {covered:.1%} of sensor samples fall inside the audio")

    drift = None
    if fine_ok:
        tf, tl = T / 6, 5 * T / 6
        drift = (fi["last"]["lag"] - fi["first"]["lag"]) / (tl - tf) * 1e6

    return {
        "a": float(a), "b": float(b), "drift_ppm": float((b - 1) * 1e6),
        "drift_ppm_xcorr_first_vs_last": drift,
        # one 10 ms lag step between the first- and last-third centres (2T/3 apart)
        "drift_resolution_ppm": 0.01 / (2 * T / 3) * 1e6,
        "sensor_span_wall_s": T, "audio_duration_s": dur,
        "start_taps_sensor_wall": taps["start"]["sensor"] if taps["start"] else None,
        "start_taps_audio": taps["start"]["audio"] if taps["start"] else None,
        "end_taps_sensor_wall": taps["end"]["sensor"] if taps["end"] else None,
        "end_taps_audio": taps["end"]["audio"] if taps["end"] else None,
        "residuals_ms": [round(float(r), 2) for r in res],
        "xcorr_lag_s": fa["lag"], "xcorr_peak": fa["r"], "xcorr_z": fa["z"],
        "xcorr_band_hz": fi["band"],
        "xcorr_first_lag_s": fi["first"]["lag"], "xcorr_last_lag_s": fi["last"]["lag"],
        "coarse_lag_s": co["lag"], "coarse_support": f"{co['support']}/{co['n']}",
        "coarse_peak_margin": co["peak_margin"], "uncertainty_s": unc,
        "coarse_combos": co["combos"],
        "method": method, "confidence": conf, "notes": "; ".join(notes),
        "wall0": float(run.segments().wall0.iloc[0]),
    }


# ---------- public mapping ----------

_PARAMS: dict | None = None


def load_params(path: Path = SYNC_JSON) -> dict:
    global _PARAMS
    if _PARAMS is None:
        _PARAMS = json.loads(Path(path).read_text())
    return _PARAMS


def _p(run: Run) -> dict:
    return load_params()[run.stem]


def sensor_to_audio(run: Run, t_sensor) -> np.ndarray:
    """phyphox experiment time -> audio file seconds (NaN outside the sensor recording)."""
    p = _p(run)
    w = run.to_wall(np.asarray(t_sensor, dtype=float)) - p["wall0"]
    return p["a"] + p["b"] * w


def audio_to_sensor(run: Run, t_audio) -> np.ndarray:
    """Audio seconds -> phyphox experiment time; NaN inside a pause or outside the recording."""
    p = _p(run)
    wall = p["wall0"] + (np.asarray(t_audio, dtype=float) - p["a"]) / p["b"]
    out = np.full_like(wall, np.nan, dtype=float)
    for _, s in run.segments().iterrows():
        dur = s.t1 - s.t0
        if not np.isfinite(dur):
            dur = run.sensors["acc"].t.max() - s.t0
        m = (wall >= s.wall0) & (wall <= s.wall0 + dur)
        out[m] = s.t0 + (wall[m] - s.wall0)
    return out
