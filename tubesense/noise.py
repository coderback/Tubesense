"""Per-second loudness and band levels from the run audio.

No calibration exists, so every level is dB re digital full scale (dBFS; an RMS of 1.0
is 0 dB). A-weighting and F time-weighting come from phonometry (IEC 61672-1).
Third-octave bands are summed from a 0.5 s Hann power spectrum (2 Hz bins), which
`band_check()` compares against phonometry's IEC 61260 filter bank.

Only derived per-second features are kept; the decoded audio lives in memory only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import signal

from .ingest import INTERIM, Run, load_audio, load_run

CACHE = INTERIM / "noise"

# Third-octave bands, base-10 exact centres 1000 * 10^(n/10), n = -16..12 (25 Hz .. 16 kHz).
BAND_N = np.arange(-16, 13)
BAND_FC = 1000.0 * 10.0 ** (BAND_N / 10)
BAND_LABELS = ["25", "31.5", "40", "50", "63", "80", "100", "125", "160", "200", "250", "315",
               "400", "500", "630", "800", "1000", "1250", "1600", "2000", "2500", "3150",
               "4000", "5000", "6300", "8000", "10000", "12500", "16000"]
BAND_COLS = [f"b_{s}" for s in BAND_LABELS]

NFFT_S = 0.5          # spectrum frame length (s); 3 frames per 1-s bin at 50 % overlap
DOM_RANGE = (100.0, 2000.0)
SPEECH_BAND = (300.0, 3400.0)
SPEECH_MOD = (2.0, 8.0)
SPEECH_THRESH = 0.45  # crude, uncalibrated: share of envelope modulation energy in 2-8 Hz (~p93)
LIMIT_DB = 1.0        # a second whose peak is within this of the run ceiling counts as limited


def _db(ms: np.ndarray) -> np.ndarray:
    return 10 * np.log10(np.maximum(ms, 1e-20))


def _per_second(v: np.ndarray, sr: int, n: int, how: str = "mean") -> np.ndarray:
    v = v[: n * sr].reshape(n, sr)
    return v.max(axis=1) if how == "max" else v.mean(axis=1)


def _spectra(x: np.ndarray, sr: int, n: int, block: int = 32):
    """Mean one-sided power spectrum per 1-s bin, scaled so bins sum to mean square."""
    L, hop = int(NFFT_S * sr), int(NFFT_S * sr) // 2
    w = np.hanning(L).astype(np.float32)
    scale = 2.0 / (L * np.sum(w.astype(np.float64) ** 2))
    f = np.fft.rfftfreq(L, 1 / sr)
    k = np.arange(0, sr - L + 1, hop)                   # frame offsets inside one second
    out = np.empty((n, len(f)), dtype=np.float32)
    for s0 in range(0, n, block):
        s1 = min(n, s0 + block)
        idx = (np.arange(s0, s1)[:, None] * sr + k[None, :])        # (secs, frames)
        frames = x[idx[..., None] + np.arange(L)] * w               # (secs, frames, L)
        p = np.abs(np.fft.rfft(frames, axis=-1)) ** 2 * scale
        p[..., 0] /= 2
        out[s0:s1] = p.mean(axis=1)
    return f, out


def _band_levels(f: np.ndarray, P: np.ndarray) -> np.ndarray:
    lo, hi = BAND_FC * 10 ** (-1 / 20), BAND_FC * 10 ** (1 / 20)
    return np.stack([_db(P[:, (f >= a) & (f < b)].sum(axis=1)) for a, b in zip(lo, hi)], axis=1)


def _dominant(f: np.ndarray, P: np.ndarray):
    """Peak frequency in DOM_RANGE of a lightly smoothed spectrum + prominence over the median (dB)."""
    m = (f >= DOM_RANGE[0]) & (f <= DOM_RANGE[1])
    Ps = signal.convolve(P[:, m], np.ones((1, 5)) / 5, mode="same")
    i = Ps.argmax(axis=1)
    prom = _db(Ps[np.arange(len(i)), i]) - _db(np.median(Ps, axis=1))
    return f[m][i], prom


def _speech_mod(x: np.ndarray, sr: int, n: int) -> np.ndarray:
    """Share of 300-3400 Hz envelope modulation energy in 2-8 Hz, 2-s window per bin."""
    sos = signal.butter(4, SPEECH_BAND, btype="band", fs=sr, output="sos")
    e = signal.sosfilt(sos, x) ** 2
    er = 100                                            # envelope rate (Hz)
    e = e[: len(e) // (sr // er) * (sr // er)].reshape(-1, sr // er).mean(axis=1)
    W = 2 * er
    fm = np.fft.rfftfreq(W, 1 / er)
    band = (fm >= SPEECH_MOD[0]) & (fm <= SPEECH_MOD[1])
    tot = (fm >= 0.5)
    win = np.hanning(W)
    out = np.full(n, np.nan)
    for i in range(n):
        c = i * er + er // 2
        seg = e[max(0, c - W // 2): c + W // 2]
        if len(seg) < W:
            seg = np.pad(seg, (0, W - len(seg)), mode="edge")
        seg = seg / max(seg.mean(), 1e-20) - 1
        p = np.abs(np.fft.rfft(seg * win)) ** 2
        out[i] = p[band].sum() / max(p[tot].sum(), 1e-20)
    return out


def levels(run: Run | str, use_cache: bool = True) -> pd.DataFrame:
    """One row per 1 s of audio time (t_audio = bin start, s).

    Columns: LAeq_1s, LAFmax_1s, LZeq_1s, peak_dbfs, crest_db (peak - LZeq), dom_hz
    (100-2000 Hz), dom_prom_db, speech_mod, speech_flag, limited, crest_deficit_db,
    b_25 .. b_16000 (unweighted third-octave Leq). All levels in dBFS (uncalibrated).

    Voice Memos applies a peak limiter (ceiling ~-0.7 dBFS). `limited` marks seconds whose
    peak sits at that ceiling; `crest_deficit_db` is how far their crest factor falls below
    the run's unlimited median, an indicative upper bound on how much the limiter lowered
    that second's level (0 for unlimited seconds).
    """
    from phonometry import time_weighting, weighting_filter

    run = load_run(run)
    path = CACHE / f"{run.stem}.parquet"
    if use_cache and path.exists():
        return pd.read_parquet(path)

    x, sr = load_audio(run)
    n = len(x) // sr                                    # trailing partial second dropped
    xa = weighting_filter(x, sr, "A")
    df = pd.DataFrame({"t_audio": np.arange(n, dtype=float)})
    df["LAeq_1s"] = _db(_per_second(xa ** 2, sr, n))
    df["LAFmax_1s"] = _db(_per_second(time_weighting(xa, sr, "fast", initial_state="first"), sr, n, "max"))
    del xa
    df["LZeq_1s"] = _db(_per_second(x.astype(np.float64) ** 2, sr, n))
    df["peak_dbfs"] = 20 * np.log10(np.maximum(_per_second(np.abs(x), sr, n, "max"), 1e-10))
    df["crest_db"] = df.peak_dbfs - df.LZeq_1s
    df["limited"] = df.peak_dbfs > df.peak_dbfs.max() - LIMIT_DB
    ref = df.crest_db[~df.limited].median()
    df["crest_deficit_db"] = np.where(df.limited, np.maximum(ref - df.crest_db, 0), 0.0)
    f, P = _spectra(x, sr, n)
    df["dom_hz"], df["dom_prom_db"] = _dominant(f, P)
    df[BAND_COLS] = _band_levels(f, P)
    del P
    df["speech_mod"] = _speech_mod(x, sr, n)
    df["speech_flag"] = df.speech_mod > SPEECH_THRESH
    df.insert(0, "stem", run.stem)

    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


def energy_mean(db: pd.Series | np.ndarray) -> float:
    """Energy (not arithmetic) mean of levels in dB."""
    v = np.asarray(db, dtype=float)
    return float(10 * np.log10(np.mean(10 ** (v / 10))))


def summary(df: pd.DataFrame) -> dict:
    """Overall level stats + Voice Memos processing indicators for one run."""
    la = df.LAeq_1s
    return {
        "stem": df.stem.iloc[0],
        "n_s": len(df),
        "LAeq_overall_dbfs": energy_mean(la),
        "LAeq_1s_min": float(la.min()), "LAeq_1s_p5": float(la.quantile(.05)),
        "LAeq_1s_p50": float(la.median()), "LAeq_1s_p95": float(la.quantile(.95)),
        "LAeq_1s_max": float(la.max()), "LAFmax_max": float(df.LAFmax_1s.max()),
        "range_p95_p5_db": float(la.quantile(.95) - la.quantile(.05)),
        "LZ_minus_LA_p50_db": float((df.LZeq_1s - la).median()),
        "crest_p50_db": float(df.crest_db.median()), "crest_p5_db": float(df.crest_db.quantile(.05)),
        "peak_max_dbfs": float(df.peak_dbfs.max()),
        "t_peak_max_s": float(df.t_audio[df.peak_dbfs.idxmax()]),
        "n_s_peak_above_-1dBFS": int((df.peak_dbfs > -1).sum()),
        "n_s_peak_above_-3dBFS": int((df.peak_dbfs > -3).sum()),
        # loudest 10 % of seconds: how tightly do they bunch under the max? (a limiter ceiling bunches them)
        "LAeq_top10pct_spread_db": float(la.max() - la.quantile(.9)),
        "speech_flag_frac": float(df.speech_flag.mean()),
        # limiter: share of seconds pinned at the ceiling, overall and among the loudest 10 %
        "limited_frac": float(df.limited.mean()),
        "limited_frac_top10pct": float(df.limited[la > la.quantile(.9)].mean()),
        "crest_p50_unlimited_db": float(df.crest_db[~df.limited].median()),
        "crest_p50_top10pct_db": float(df.crest_db[la > la.quantile(.9)].median()),
        "crest_deficit_p50_top10pct_db": float(df.crest_deficit_db[la > la.quantile(.9)].median()),
    }


def processing(run: Run | str) -> dict:
    """Sample-level check for Voice Memos limiting/clipping (decoded in memory only).

    A natural signal's |x| density keeps falling towards its peak. A limiter shows a
    hard ceiling with samples piling up just below it: `pileup_ratio` = samples in the
    0.5 dB under the ceiling / samples in the 0.5 dB below that (>1 means a pile-up).
    """
    run = load_run(run)
    x, sr = load_audio(run)
    a = np.abs(x)
    ceil = float(np.quantile(a, 1 - 1e-6))             # robust "max" (ignores a handful of samples)
    c_db = 20 * np.log10(ceil)
    n1 = int(((a > ceil * 10 ** (-0.5 / 20)) & (a <= ceil)).sum())
    n2 = int(((a > ceil * 10 ** (-1.0 / 20)) & (a <= ceil * 10 ** (-0.5 / 20))).sum())
    above_m6 = float((a > 10 ** (-6 / 20)).mean())
    return {
        "stem": run.stem,
        "clip_frac_0dBFS": float((a >= 0.999).mean()),
        "ceiling_dbfs": c_db,
        "pileup_ratio": n1 / max(n2, 1),
        "frac_samples_within_1dB_of_ceiling": float((a > ceil * 10 ** (-1 / 20)).mean()),
        "frac_samples_above_-6dBFS": above_m6,
    }


def band_check(run: Run | str, t0: float = 60.0, dur: float = 20.0) -> pd.DataFrame:
    """Compare STFT band levels with phonometry's IEC 61260 octave_filter on one excerpt."""
    from phonometry import octave_filter

    run = load_run(run)
    x, sr = load_audio(run)
    seg = x[int(t0 * sr): int((t0 + dur) * sr)].astype(np.float64)
    ref, ref_fc = octave_filter(seg, sr, fraction=3, limits=[22, 18000], dbfs=True)
    ref, ref_fc = np.asarray(ref, dtype=float), np.asarray(ref_fc, dtype=float)
    f, P = _spectra(seg.astype(np.float32), sr, int(dur))
    ours = _db((10 ** (_band_levels(f, P) / 10)).mean(axis=0))
    near = [int(np.argmin(np.abs(np.log(ref_fc / c)))) for c in BAND_FC]
    return pd.DataFrame({"band": BAND_LABELS, "stft_db": ours, "phonometry_db": ref[near],
                         "diff_db": ours - ref[near]})
