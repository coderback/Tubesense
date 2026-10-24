"""S0 Elizabeth line test ride (Woolwich -> Liverpool Street, 24 Oct 2026): marker test, real-stop
check of the post-hoc stop gate, and cross-line levels.

EXPLORATORY, POST-HOC, n=1, OUTSIDE prereg-24oct. Nothing here feeds the frozen pipeline: the
'S0' stem is not matched by tubesense.ingest.RUN_RE, and no tubesense/, scripts/, outputs/ or
existing posthoc/ file is changed. Every number is from one ride on one phone.

Three parts:
1. MARKER TEST. The rider made start/end markers: two finger snaps and a figure-8 wave of the
   phone. Snaps are measured in the audio (time, 2-6 kHz excess, decay) and looked for in the
   accelerometer; the figure-8 is measured in the gyro (time, peak, axis balance) and looked for
   in the audio (handling-noise correlation, with a random-lag null). The sync lag comes from
   tubesense.sync.coarse_lag / fine_lag and, independently, from impulsive events that both the
   accelerometer and the microphone saw (jolts to the phone).
2. REAL-STOP CHECK of posthoc.tracker_v2.StopGate (GateParams defaults = attempt D; thresholds
   tuned on the Central line 23 Oct only, so this is an out-of-sample line). S0 has no map, so only
   the gate is exercised, not the tracker.
3. CROSS-LINE LEVELS. Per-second A-weighted LAeq (phonometry weighting_filter, as tubesense/noise.py)
   and per-second peak dBFS at the native 48 kHz, decoded in streamed blocks in memory; sections
   from the motion stops; the same statistics recomputed for the Central line 23 Oct from
   outputs/noise/*_levels.csv; the 16 vs 48 kHz decode bias on S0 and on R5 LST->BTH.

Privacy: audio is decoded in memory only; only derived per-second levels/envelopes are kept, and
only in the outputs listed below (no waveform is written).

Run (repo root):
    PYTHONPATH=. .venv/Scripts/python posthoc/s0_elizabeth.py
Writes outputs_posthoc/s0_elizabeth.json, .md, .png.
"""
from __future__ import annotations

import gc
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import ndimage, signal

from tubesense import features as F
from tubesense import online as O
from tubesense import stops as S
from tubesense import sync as SY
from tubesense.ingest import ROOT, Run, load_audio, load_run
from tubesense.noise import energy_mean

from . import tracker_v2 as V2

OUT = ROOT / "outputs_posthoc"
OUT_JSON, OUT_MD, OUT_PNG = OUT / "s0_elizabeth.json", OUT / "s0_elizabeth.md", OUT / "s0_elizabeth.png"
S0 = Run("S0", "WB", "WOL-LST", "S0_WB_WOL-LST")
LABEL = "exploratory / post-hoc / n=1 / outside prereg-24oct"

SR_LO = 16000          # decode rate for envelopes, markers and sync
HF_BAND = (2000, 6000)  # snap band
HF_DT = 0.005          # s, snap envelope frame
SNAP_MIN_DB = 18.0     # excess over a 1 s running median
SNAP_WIN_S = 10.0      # look for the snap pairs in the first / last 10 s of the audio
FIG8_RMS = 1.0         # rad/s, 1 s RMS |gyro| above this = vigorous handling
FIG8_MIN_S = 5.0       # an episode this long counts as a figure-8 wave
JOLT_MIN = 5.0         # m/s^2, 8 Hz high-passed |acc| (sync.accel_spikes height) for a "strong jolt"
ONSET_BAND = (100, 6000)
LONG_DWELL_S = 30.0    # a platform dwell; shorter stationary intervals are "short stops"
TRIM_DWELL_S = 3.0     # dwell levels: drop this much at each end (doors / edge error)
CENTRAL_STEMS = ["R0.5_EB_OXC-LST", "R1_EB_LST-BTH", "R1.5_EB_BTH-MLE", "R2_WB_MLE-LST",
                 "R3_EB_LST-MLE", "R4_WB_MLE-LST", "R5_EB_LST-MLE", "R6_WB_MLE-LST"]


def r(x, n=2):
    """Round floats (and nested containers) for JSON; NaN -> None."""
    if isinstance(x, dict):
        return {k: r(v, n) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [r(v, n) for v in x]
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer, int)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else round(float(x), n)
    return x


# --------------------------------------------------------------------------- audio helpers
def stream_blocks(path: Path, sr: int | None = None, block_s: int = 60):
    """Yield (rate, block) mono float32 blocks of exactly block_s seconds (last one shorter), in memory."""
    import av
    with av.open(str(path)) as c:
        st = c.streams.audio[0]
        rate = sr or st.rate
        rs = av.AudioResampler(format="flt", layout="mono", rate=rate)
        blk, buf, n = block_s * rate, [], 0

        def frames():
            for packet in c.demux(st):
                try:
                    fr = packet.decode()
                except av.error.InvalidDataError:
                    continue
                for f in fr:
                    for g in rs.resample(f):
                        yield g.to_ndarray().reshape(-1)
            for g in rs.resample(None):
                yield g.to_ndarray().reshape(-1)

        for a in frames():
            buf.append(a)
            n += len(a)
            if n >= blk:
                x = np.concatenate(buf)
                yield rate, x[:blk]
                buf, n = [x[blk:]], len(x) - blk
        if n:
            yield rate, np.concatenate(buf)


def per_second_levels(path: Path, sr: int | None = None, pad_s: float = 2.0) -> pd.DataFrame:
    """LAeq_1s (A-weighted, phonometry) and peak_dbfs per audio second, streamed with +-pad_s context
    so the weighting filter sees continuous signal across block edges. Trailing partial second dropped
    (as tubesense.noise.levels)."""
    from phonometry import weighting_filter
    la, pk, rate = [], [], None
    prev_tail, cur = np.zeros(0, np.float32), None

    def process(pre, x, post):
        xx = np.concatenate([pre, x, post])
        ya = weighting_filter(xx, rate, "A")[len(pre): len(pre) + len(x)]
        n = len(x) // rate
        la.append(10 * np.log10(np.maximum((ya[: n * rate].astype(np.float64) ** 2).reshape(n, rate).mean(1), 1e-20)))
        pk.append(20 * np.log10(np.maximum(np.abs(x[: n * rate]).reshape(n, rate).max(1), 1e-10)))

    for rate, blk in stream_blocks(path, sr):
        pad = int(pad_s * rate)
        if cur is not None:
            process(prev_tail, cur, blk[:pad])
            prev_tail = cur[-pad:]
        cur = blk
    process(prev_tail, cur, np.zeros(0, np.float32))
    la, pk = np.concatenate(la), np.concatenate(pk)
    return pd.DataFrame({"t_audio": np.arange(len(la), dtype=float), "LAeq_1s": la, "peak_dbfs": pk})


def band_env_db(y: np.ndarray, sr: int, lo: float, hi: float, dt: float) -> np.ndarray:
    sos = signal.butter(4, [lo, hi], "bandpass", fs=sr, output="sos")
    e = signal.sosfiltfilt(sos, y) ** 2
    h = int(round(dt * sr))
    m = len(e) // h
    return 10 * np.log10(e[: m * h].reshape(m, h).mean(1) + 1e-14)


def stats(la: np.ndarray, pk: np.ndarray, ceiling: float) -> dict:
    if len(la) == 0:
        return {"n_s": 0}
    return {"n_s": int(len(la)), "LAeq": energy_mean(la), "L10": float(np.percentile(la, 90)),
            "L90": float(np.percentile(la, 10)), "peak_gt_m1dBFS_frac": float((pk > -1).mean()),
            "limited_frac": float((pk > ceiling - 1.0).mean())}


# --------------------------------------------------------------------------- part 0: motion
def motion(run: Run) -> dict:
    f = S.motion_features(run)
    sc = S.moving_score(f)
    st = S.stationary_intervals(f, sc)
    rows = st.to_dict("records")
    for x in rows:
        x["kind"] = ("edge_handled" if x["edge_inferred"] else
                     "platform_dwell" if x["dur"] >= LONG_DWELL_S else "short_stop")
    long_ = [x for x in rows if x["kind"] == "platform_dwell"]
    names = ["CUH", "CWF", "WCL"]
    if len(long_) != 3:
        raise RuntimeError(f"expected 3 platform dwells, found {len(long_)}")
    for x, nm in zip(long_, names):
        x["station"] = nm
    # Woolwich departure: end of the last non-edge stationary interval > 60 s before CUH;
    # Liverpool Street arrival: start of the first non-edge stationary interval > 60 s after WCL.
    pre = [x for x in rows if x["kind"] != "edge_handled" and x["t1"] < long_[0]["t0"] - 60]
    post = [x for x in rows if x["kind"] != "edge_handled" and x["t0"] > long_[-1]["t1"] + 60]
    dep_wol, arr_lst = pre[-1]["t1"], post[0]["t0"]
    pre[-1]["station"], post[0]["station"] = "WOL (end of)", "LST (arrival)"
    edges = [("WOL", dep_wol)] + [(x["station"], (x["t0"], x["t1"])) for x in long_] + [("LST", arr_lst)]
    secs = []
    for (a, ta), (b, tb) in zip(edges[:-1], edges[1:]):
        d = ta if np.isscalar(ta) else ta[1]
        e = tb if np.isscalar(tb) else tb[0]
        secs.append({"section": f"{a}->{b}", "from": a, "to": b, "depart_t": float(d), "arrive_t": float(e),
                     "duration_s": float(e - d)})
    return {"feat": f.assign(score=sc), "stationary": rows, "dwells": long_, "sections": secs,
            "depart_wol": float(dep_wol), "arrive_lst": float(arr_lst), "imu_end": float(run.sensors["acc"].t.iloc[-1])}


# --------------------------------------------------------------------------- part 1: markers
def gyro_episodes(run: Run) -> list[dict]:
    g = run.sensors["gyr"]
    G = g[["x", "y", "z"]].values
    w = np.linalg.norm(G, axis=1)
    tg = g.t.values
    edges = np.arange(tg[0], tg[-1], 1.0)
    idx = np.searchsorted(tg, edges)
    eps = []
    for i0, i1, t0 in zip(idx[:-1], idx[1:], edges[:-1]):
        if i1 - i0 < 10:
            continue
        rms = float(np.sqrt((w[i0:i1] ** 2).mean()))
        if rms <= FIG8_RMS:
            continue
        if eps and t0 - eps[-1]["t1"] <= 2.0:
            eps[-1]["t1"] = float(t0 + 1)
            eps[-1]["rms"] = max(eps[-1]["rms"], rms)
        else:
            eps.append({"t0": float(t0), "t1": float(t0 + 1), "rms": rms})
    out = []
    for e in eps:
        m = (tg >= e["t0"]) & (tg < e["t1"])
        ax_rms = np.sqrt((G[m] ** 2).mean(0))
        # dominant rotation frequency per axis (a figure-8 shows the same slow period on several axes)
        dom = []
        for k in range(3):
            x = G[m, k] - G[m, k].mean()
            fr = np.fft.rfftfreq(len(x), np.median(np.diff(tg)))
            p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
            band = (fr >= 0.2) & (fr <= 5)
            dom.append(float(fr[band][np.argmax(p[band])]))
        out.append({"t0": e["t0"], "t1": e["t1"], "dur_s": e["t1"] - e["t0"],
                    "peak_rad_s": float(w[m].max()), "rms_1s_peak_rad_s": e["rms"],
                    "axis_rms_rad_s": ax_rms.tolist(), "axis_share": (ax_rms / ax_rms.sum()).tolist(),
                    "axis_balance_min_over_max": float(ax_rms.min() / ax_rms.max()),
                    "dominant_hz_xyz": dom,
                    "figure8": (e["t1"] - e["t0"]) >= FIG8_MIN_S})
    return out


def mag_sphere(run: Run, t0: float, t1: float) -> dict:
    """Hard-iron sphere fit of the raw magnetometer over a figure-8 (the usual reason to wave one)."""
    m = run.sensors["mag"]
    P = m[(m.t >= t0) & (m.t <= t1)][["x", "y", "z"]].values
    A = np.c_[2 * P, np.ones(len(P))]
    c, *_ = np.linalg.lstsq(A, (P ** 2).sum(1), rcond=None)
    ctr = c[:3]
    rad = float(np.sqrt(c[3] + ctr @ ctr))
    res = np.linalg.norm(P - ctr, axis=1) - rad
    return {"n": int(len(P)), "radius_uT": rad, "misfit_rel": float(np.sqrt((res ** 2).mean()) / rad),
            "centre_uT": ctr.tolist(), "raw_B_median_uT": float(np.median(np.linalg.norm(P, axis=1)))}


def snaps(hf: np.ndarray, dur: float) -> dict:
    base = signal.medfilt(hf, 201)
    ex = hf - base
    cand = signal.find_peaks(ex, height=SNAP_MIN_DB, distance=int(0.08 / HF_DT))[0]

    def decay_ms(i):
        k = i
        while k < len(hf) - 1 and hf[k] > hf[i] - 15 and (k - i) * HF_DT < 0.3:
            k += 1
        return (k - i) * HF_DT * 1000

    allc = [{"t_audio": float(i * HF_DT), "excess_db": float(ex[i]), "decay_ms": decay_ms(i)} for i in cand]
    out = {"n_candidates_whole_file": len(allc), "excess": ex}
    for end, sel in (("start", [c for c in allc if c["t_audio"] < SNAP_WIN_S]),
                     ("end", [c for c in allc if c["t_audio"] > dur - SNAP_WIN_S])):
        # the rider's pattern: two sharp clicks 0.2-2 s apart; take the strongest such pair
        best = None
        for a in sel:
            for b in sel:
                if 0.2 <= b["t_audio"] - a["t_audio"] <= 2.0:
                    s = min(a["excess_db"], b["excess_db"])
                    if best is None or s > best[0]:
                        best = (s, [a, b])
        out[end] = {"candidates": sel, "pair": best[1] if best else None}
    return out


def shared_events(run: Run, onset_ex: np.ndarray, dt: float) -> dict:
    """Lag from impulsive events seen by BOTH the accelerometer (jolt) and the microphone (thump/click).

    Independent of coarse_lag: scans lags -20..+30 s for the lag at which the strong accel jolts land
    on audio onsets (broadband 100-6000 Hz excess over a 1 s running median), with the score at all
    other lags as the null."""
    ts, hs = SY.accel_spikes(run)                       # wall-relative = experiment time here (no pause)
    order = np.argsort(-hs)
    keep = []
    for i in order:                                     # merge spikes within 0.25 s, keep the highest
        if all(abs(ts[i] - ts[j]) > 0.25 for j in keep):
            keep.append(i)
    keep = sorted(keep, key=lambda i: ts[i])
    strong = [i for i in keep if hs[i] >= JOLT_MIN]
    exm = ndimage.maximum_filter1d(onset_ex, 7)        # +-15 ms tolerance
    lags = np.round(np.arange(-20.0, 30.0, dt), 3)

    def score(L, idx):
        k = np.round((ts[idx] + L) / dt).astype(int)
        ok = (k >= 0) & (k < len(exm))
        return float(np.mean(np.where(ok, exm[np.clip(k, 0, len(exm) - 1)], 0.0)))

    sc = np.array([score(L, strong) for L in lags])
    kb = int(np.argmax(sc))
    Lb = float(lags[kb])
    far = np.abs(lags - Lb) > 1.0
    per = []
    for i in keep:
        k0 = int(round((ts[i] + Lb) / dt))
        w = onset_ex[max(0, k0 - 20): k0 + 21]          # +-0.1 s around the predicted audio time
        if len(w) == 0:
            continue
        j = int(np.argmax(w))
        per.append({"t_sensor": float(ts[i]), "jolt_m_s2": float(hs[i]), "strong": bool(hs[i] >= JOLT_MIN),
                    "audio_excess_db_at_lag": float(exm[min(k0, len(exm) - 1)]),
                    "audio_peak_within_0.1s_db": float(w[j]),
                    "offset_s": float((max(0, k0 - 20) + j) * dt - ts[i])})
    good = [p for p in per if p["strong"] and p["audio_peak_within_0.1s_db"] >= 10]
    offs = np.array([p["offset_s"] for p in good])
    return {"n_spikes": len(keep), "n_strong": len(strong), "strong_times_s": [float(ts[i]) for i in strong],
            "lag_scan_best_s": Lb, "score_best_db": float(sc[kb]),
            "null_score_median_db": float(np.median(sc[far])), "null_score_p99_db": float(np.percentile(sc[far], 99)),
            "null_score_max_db": float(sc[far].max()),
            "null_z": float((sc[kb] - np.median(sc[far])) / sc[far].std()),
            "refined_lag_median_s": float(np.median(offs)) if len(offs) else None,
            "refined_lag_spread_s": float(np.ptp(offs)) if len(offs) else None,
            "n_refined": int(len(offs)), "events": per, "_lags": lags, "_score": sc}


def fig8_audio(wb: np.ndarray, lo: np.ndarray, ep: dict, lag: float, seed: int = 0) -> dict:
    """Pearson r between gyro |w| and audio 20-300 Hz envelope (0.1 s bins) over a figure-8, at the
    shared-event lag, at the best lag within +-10 s, and at 2000 random lags (null)."""
    s0, s1 = int(ep["t0"] / 0.1), int(ep["t1"] / 0.1)
    s = wb[s0:s1]

    def corr(L):
        i0 = int(round((ep["t0"] + L) / 0.1))
        a = lo[i0: i0 + len(s)]
        if i0 < 0 or len(a) < len(s):
            return np.nan
        return float(np.corrcoef(a, s)[0, 1])

    Ls = np.round(np.arange(lag - 10, lag + 10, 0.1), 2)
    rs = np.array([corr(L) for L in Ls])
    kb = int(np.nanargmax(rs))
    rng = np.random.default_rng(seed)
    lo_l, hi_l = -ep["t0"], len(lo) * 0.1 - ep["t1"]
    null = []
    while len(null) < 2000:
        L = rng.uniform(lo_l, hi_l)
        if abs(L - lag) < 3:
            continue
        v = corr(L)
        if np.isfinite(v):
            null.append(v)
    null = np.array(null)
    r_at = corr(lag)
    return {"window_sensor_s": [ep["t0"], ep["t1"]], "r_at_shared_event_lag": r_at,
            "best_lag_within_10s": float(Ls[kb]), "r_best": float(rs[kb]),
            "null_r_p50": float(np.median(null)), "null_r_p95": float(np.percentile(null, 95)),
            "null_r_p99": float(np.percentile(null, 99)),
            "percentile_of_r_at_lag_in_null": float((null < r_at).mean() * 100),
            "percentile_of_r_best_in_null": float((null < rs[kb]).mean() * 100)}


def accel_at(run: Run, t_sensor: float, half: float = 0.15) -> dict:
    """8 Hz high-passed |acc| around a sensor time vs its local 30 s median (or 'outside')."""
    acc = run.sensors["acc"]
    if not (acc.t.iloc[0] <= t_sensor <= acc.t.iloc[-1]):
        return {"inside_imu": False}
    hp = SY.imu_band(run, 8, None, "acc")
    t = acc.t.values
    m = np.abs(t - t_sensor) <= half
    ref = np.abs(t - t_sensor) <= 15
    return {"inside_imu": True, "hp_acc_max": float(hp[m].max()), "hp_acc_local_median": float(np.median(hp[ref])),
            "ratio": float(hp[m].max() / np.median(hp[ref]))}


# --------------------------------------------------------------------------- part 2: stop gate
def gate_test(run: Run, mo: dict, la_sensor: pd.Series) -> dict:
    dw = mo["dwells"]
    bias = F.gyro_bias(run, [(d["t0"], d["t1"]) for d in dw])
    feat = V2.gate_features(run, O.stream_features(run, bias))
    feat = feat[feat.t >= mo["depart_wol"]].reset_index(drop=True)
    prm = V2.GateParams()
    gate = V2.StopGate(prm)
    eng, z, rel, quiet, rv, rb = [], [], [], [], [], []
    for x in feat.itertuples():
        pm = gate.update({"vib": x.vib, "body": x.body, "h": x.h}, bool(x.valid), x.gyr > O.HANDLED)
        eng.append(pm < 1.0)
        z.append(gate.z)
        rel.append(gate.released)
        quiet.append(gate.quiet)
        rv.append(np.median(gate.ref["vib"]) if len(gate.ref["vib"]) else np.nan)   # diagnostic only
        rb.append(np.median(gate.ref["body"]) if len(gate.ref["body"]) else np.nan)
    feat["engaged"], feat["z"], feat["released"] = np.array(eng), np.array(z), rel
    feat["ref_vib"], feat["ref_body"] = rv, rb
    feat["handled"] = feat.gyr > O.HANDLED
    t = feat.t.values
    E = feat.engaged.values

    def stretches(mask):
        d = np.diff(np.r_[0, mask.astype(int), 0])
        return [(float(t[i0]), float(t[i1 - 1] + O.STEP)) for i0, i1 in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1))]

    def cue_summary(m):
        sub = feat[m]
        return {"vib_med": float(sub.vib.median()), "body_med": float(sub.body.median()),
                "h_med": float(sub.h.median()), "abs_yaw_med_deg_s": float(sub.yaw.abs().median()),
                "abs_yaw_max_deg_s": float(sub.yaw.abs().max()), "z_med": float(np.nanmedian(sub.z)),
                "handled_frac": float(sub.handled.mean()), "engaged_frac": float(sub.engaged.mean())}

    def interval_report(t0, t1, interior_lead):
        full = (t >= t0) & (t <= t1)
        inner = (t >= t0 + interior_lead) & (t <= t1 - 2)
        first = np.flatnonzero(full & E)
        eng_before = bool(E[np.flatnonzero(full)[0]]) if full.any() else False
        rel_in = [feat.released.values[i] for i in np.flatnonzero(full) if feat.released.values[i]]
        zz = feat.z.values
        kmin = int(np.nanargmin(np.where(full, zz, np.nan))) if full.any() else None
        i0, i1 = np.flatnonzero(full)[[0, -1]]
        # non-causal diagnostic: drop below the median of the preceding section's running steps
        sec = max((s for s in mo["sections"] if s["arrive_t"] <= t0 + 1), key=lambda s: s["arrive_t"], default=None)
        if sec is not None:
            rm = (t >= sec["depart_t"] + 5) & (t <= sec["arrive_t"] - 5)
            zr = 0.5 * ((feat.vib[full] - feat.vib[rm].median()) + (feat.body[full] - feat.body[rm].median()))
        rep = {"t0": t0, "t1": t1, "dur_s": t1 - t0,
               "z_min": float(zz[kmin]) if kmin is not None else None,
               "z_min_at_s_after_t0": float(t[kmin] - t0) if kmin is not None else None,
               "ref_vib_at_t0_t1": [float(feat.ref_vib[i0]), float(feat.ref_vib[i1])],
               "ref_body_at_t0_t1": [float(feat.ref_body[i0]), float(feat.ref_body[i1])],
               "noncausal_drop_vs_section_running_median": float(np.median(zr)) if sec is not None else None,
               "interior_flagged_frac": float(E[inner].mean()) if inner.any() else None,
               "full_flagged_frac": float(E[full].mean()),
               "engaged_on_entry": eng_before,
               "engage_delay_s": (float(t[first[0]] - t0) if len(first) and not eng_before else
                                  (0.0 if eng_before else None)),
               "releases_inside": rel_in,
               "release_after_end_s": None,
               "cues": cue_summary(full)}
        if len(first):
            after = np.flatnonzero((t > t0) & ~E)
            after = after[t[after] > t[first[0]]]
            if len(after):
                rep["release_after_end_s"] = float(t[after[0]] - t1)
        return rep

    dwell_rep = {d["station"]: interval_report(d["t0"], d["t1"], prm.engage_s + 2) for d in dw}
    short = [x for x in mo["stationary"] if x["kind"] == "short_stop" and x["t0"] >= mo["depart_wol"]]
    short_rep = {f"{x['t0']:.1f}-{x['t1']:.1f}": interval_report(x["t0"], x["t1"], prm.engage_s + 2) for x in short}
    for x, (k, v) in zip(short, short_rep.items()):
        nxt = [d for d in dw if d["t0"] >= x["t1"]]
        if nxt and nxt[0]["t0"] - x["t1"] < 30:
            gm = (t > x["t1"]) & (t < nxt[0]["t0"])
            v["gap_to_next_dwell"] = {"station": nxt[0]["station"], "gap_s": nxt[0]["t0"] - x["t1"],
                                      "cues": cue_summary(gm), "t0": x["t1"], "t1": nxt[0]["t0"]}
    # running = inside a section, >= 5 s from its edges, outside any short stationary interval
    run_m = np.zeros(len(t), bool)
    for s in mo["sections"]:
        run_m |= (t >= s["depart_t"] + 5) & (t <= s["arrive_t"] - 5)
    in_short = np.zeros(len(t), bool)
    for x in short:
        in_short |= (t >= x["t0"] - 2) & (t <= x["t1"] + 2)
    running = run_m & ~in_short
    false_eng = []
    for a, b in stretches(E):
        mm = (t >= a) & (t < b)
        if not (mm & running).any():
            continue
        sec = next((s for s in mo["sections"] if s["depart_t"] <= a <= s["arrive_t"]), None)
        false_eng.append({"t0": a, "t1": b, "dur_s": b - a, "section": sec["section"] if sec else None,
                          "s_after_departure": a - sec["depart_t"] if sec else None,
                          "s_before_arrival": sec["arrive_t"] - a if sec else None,
                          "running_steps_engaged": int((mm & running).sum()), "cues": cue_summary(mm)})
    # reference cue levels for characterising the short stops
    dwell_in = np.zeros(len(t), bool)
    for d in dw:
        dwell_in |= (t >= d["t0"] + 5) & (t <= d["t1"] - 5)
    ref = {"platform_dwell_interiors": cue_summary(dwell_in), "running": cue_summary(running),
           "running_vib_p5": float(np.percentile(feat.vib[running], 5)),
           "running_body_p5": float(np.percentile(feat.body[running], 5)),
           "running_z_p1": float(np.nanpercentile(feat.z[running], 1)),
           "running_z_p5": float(np.nanpercentile(feat.z[running], 5))}
    # audio LAeq (1 s) by interval, sensor time
    def la_med(a, b):
        v = la_sensor[(la_sensor.index >= a) & (la_sensor.index < b)]
        return float(v.median()) if len(v) else None
    for k, rep in list(dwell_rep.items()) + list(short_rep.items()):
        rep["LAeq_1s_median_dbfs"] = la_med(rep["t0"], rep["t1"])
        if "gap_to_next_dwell" in rep:
            rep["gap_to_next_dwell"]["LAeq_1s_median_dbfs"] = la_med(rep["gap_to_next_dwell"]["t0"], rep["gap_to_next_dwell"]["t1"])
    ref["platform_dwell_LAeq_1s_median"] = float(np.median([dwell_rep[k]["LAeq_1s_median_dbfs"] for k in dwell_rep]))
    ref["running_LAeq_1s_median"] = float(la_sensor[np.isin(np.floor(la_sensor.index), np.floor(t[running]))].median())
    lst = (t >= mo["arrive_lst"]) & (t <= mo["arrive_lst"] + 15)
    return {"params": prm.to_json(), "gyro_bias_rad_s": bias.tolist(),
            "start_sensor_s": float(t[0]), "end_sensor_s": float(t[-1]), "n_steps": int(len(t)),
            "dwells": dwell_rep, "short_stops": short_rep,
            "lst_arrival_first_15s": {"engaged_frac": float(E[lst].mean()), "cues": cue_summary(lst)},
            "engaged_steps_total": int(E.sum()), "z_min_whole_ride": float(np.nanmin(feat.z.values)),
            "z_min_whole_ride_at_s": float(t[int(np.nanargmin(feat.z.values))]),
            "running_steps": int(running.sum()), "running_s": float(running.sum() * O.STEP),
            "false_engagements_running": false_eng,
            "false_engagement_count": len(false_eng),
            "false_engaged_running_s": float(sum(x["running_steps_engaged"] for x in false_eng) * O.STEP),
            "reference_levels": ref,
            "engaged_stretches": [{"t0": a, "t1": b} for a, b in stretches(E)],
            "_feat": feat}


# --------------------------------------------------------------------------- part 3: levels
def central_levels() -> dict:
    """Central 23 Oct section and dwell statistics recomputed from outputs/noise/*_levels.csv with S0's
    definitions; section windows (a0, a1) from outputs/gonogo/loudness_sections.csv (sync.sensor_to_audio
    of outputs/sections.json), dwells from outputs/sections.json mapped the same way."""
    ls = pd.read_csv(ROOT / "outputs" / "gonogo" / "loudness_sections.csv")
    secj = json.loads((ROOT / "outputs" / "sections.json").read_text())
    syn = json.loads((ROOT / "outputs" / "sync.json").read_text())
    rows, runs = [], {}
    for stem in CENTRAL_STEMS:
        lv = pd.read_csv(ROOT / "outputs" / "noise" / f"{stem}_levels.csv")
        run = load_run(stem)
        tc = lv.t_audio.values + 0.5
        has_sensor = np.isfinite(SY.audio_to_sensor(run, tc))
        la, pk = lv.LAeq_1s.values, lv.peak_dbfs.values
        ceil = float(pk.max())
        mov = np.zeros(len(tc), bool)
        for x in ls[ls.stem == stem].itertuples():
            m = (tc >= x.a0) & (tc < x.a1) & has_sensor
            mov |= m
            st = stats(la[m], pk[m], ceil)
            rows.append({"stem": stem, "run_id": x.run_id, "direction": x.direction, "section": x.section,
                         "track": x.track, "placement_excluded": bool(x.placement_excluded), **st,
                         "LAeq_csv": float(x.LAeq), "limited_frac_csv": float(x.limited_frac)})
        dwm = np.zeros(len(tc), bool)
        rj = next(q for q in secj["runs"] if q["stem"] == stem)
        for d in rj["dwells"]:
            if d["virtual"] or d["truncated"]:
                continue
            a0, a1 = (float(v) for v in SY.sensor_to_audio(run, [d["t0"] + TRIM_DWELL_S, d["t1"] - TRIM_DWELL_S]))
            dwm |= (tc >= a0) & (tc < a1) & has_sensor
        sm, sd = stats(la[mov], pk[mov], ceil), stats(la[dwm], pk[dwm], ceil)
        runs[stem] = {"moving": sm, "dwell": sd,
                      "moving_minus_dwell_db": (sm["LAeq"] - sd["LAeq"]) if sd["n_s"] else None,
                      "whole_file_peak_gt_m1dBFS_frac": float((pk > -1).mean()),
                      "ceiling_dbfs": ceil, "sync_confidence": syn[stem]["confidence"],
                      "sync_uncertainty_s": syn[stem]["uncertainty_s"]}
    df = pd.DataFrame(rows)
    use = df[~df.placement_excluded]
    lb = use[use.track.isin(["LST-BTH", "BTH-MLE", "MLE-BTH", "BTH-LST"])]
    tracks = {}
    for (d, s), g in lb.groupby(["direction", "section"]):
        tracks[f"{d} {s}"] = {"n": int(len(g)), "LAeq_mean_of_runs": float(g.LAeq.mean()),
                              "peak_gt_m1dBFS_frac_mean": float(g.peak_gt_m1dBFS_frac.mean()),
                              "limited_frac_mean": float(g.limited_frac.mean())}
    return {"sections": df, "runs": runs, "tracks_lst_bth_mle": tracks,
            "LAeq_range_all_included": [float(use.LAeq.min()), float(use.LAeq.max())],
            "LAeq_range_lst_bth_mle_tracks": [min(v["LAeq_mean_of_runs"] for v in tracks.values()),
                                              max(v["LAeq_mean_of_runs"] for v in tracks.values())],
            "peak_gt_m1_frac_range_included": [float(use.peak_gt_m1dBFS_frac.min()), float(use.peak_gt_m1dBFS_frac.max())],
            "limited_frac_range_included": [float(use.limited_frac.min()), float(use.limited_frac.max())],
            "max_abs_diff_vs_loudness_sections_csv_db": float((df.LAeq - df.LAeq_csv).abs().max()),
            "max_abs_diff_limited_frac_vs_csv": float((df.limited_frac - df.limited_frac_csv).abs().max()),
            "n_section_runs": int(len(df)), "n_included": int(len(use))}


def r5_bias() -> dict:
    """R5 LST->BTH: LAeq from a 16 kHz decode vs the 48 kHz levels CSV (and our own 48 kHz stream)."""
    stem = "R5_EB_LST-MLE"
    ls = pd.read_csv(ROOT / "outputs" / "gonogo" / "loudness_sections.csv")
    x = ls[(ls.stem == stem) & (ls.section == "LST->BTH")].iloc[0]
    run = load_run(stem)
    from phonometry import weighting_filter
    y, sr = load_audio(run, sr=SR_LO)
    n = len(y) // sr
    ya = weighting_filter(y, sr, "A")
    la16 = 10 * np.log10(np.maximum((ya[: n * sr].astype(np.float64) ** 2).reshape(n, sr).mean(1), 1e-20))
    del y, ya
    gc.collect()
    lv48 = per_second_levels(run.audio_path)
    csv = pd.read_csv(ROOT / "outputs" / "noise" / f"{stem}_levels.csv")
    tc = np.arange(n) + 0.5
    m = (tc >= x.a0) & (tc < x.a1)
    m48 = (lv48.t_audio.values + 0.5 >= x.a0) & (lv48.t_audio.values + 0.5 < x.a1)
    mc = (csv.t_audio.values + 0.5 >= x.a0) & (csv.t_audio.values + 0.5 < x.a1)
    return {"section": "R5 EB LST->BTH", "a0": float(x.a0), "a1": float(x.a1),
            "LAeq_16k": energy_mean(la16[m]), "LAeq_48k_stream": energy_mean(lv48.LAeq_1s.values[m48]),
            "LAeq_48k_csv": energy_mean(csv.LAeq_1s.values[mc]),
            "bias_16k_minus_48k_csv_db": energy_mean(la16[m]) - energy_mean(csv.LAeq_1s.values[mc]),
            "stream_vs_csv_max_abs_1s_db": float(np.abs(lv48.LAeq_1s.values[: len(csv)] - csv.LAeq_1s.values[: len(lv48)]).max())}


def s0_levels(lv48: pd.DataFrame, la16: np.ndarray, mo: dict, lag: float) -> dict:
    tc = lv48.t_audio.values + 0.5
    la, pk = lv48.LAeq_1s.values, lv48.peak_dbfs.values
    ceil = float(pk.max())
    rows, mov = [], np.zeros(len(tc), bool)
    for s in mo["sections"]:
        a0, a1 = s["depart_t"] + lag, s["arrive_t"] + lag
        m = (tc >= a0) & (tc < a1)
        mov |= m
        m16 = m[: len(la16)]
        rows.append({**s, "a0": a0, "a1": a1, **stats(la[m], pk[m], ceil),
                     "LAeq_16k": energy_mean(la16[m16]), "bias_16k_minus_48k_db": energy_mean(la16[m16]) - energy_mean(la[m])})
    dwm = np.zeros(len(tc), bool)
    for d in mo["dwells"]:
        dwm |= (tc >= d["t0"] + TRIM_DWELL_S + lag) & (tc < d["t1"] - TRIM_DWELL_S + lag)
    sm, sd = stats(la[mov], pk[mov], ceil), stats(la[dwm], pk[dwm], ceil)
    imu = (tc >= lag) & (tc < mo["imu_end"] + lag)
    return {"sections": rows, "moving": sm, "dwell": sd, "moving_minus_dwell_db": sm["LAeq"] - sd["LAeq"],
            "ceiling_dbfs": ceil, "whole_file_peak_gt_m1dBFS_frac": float((pk > -1).mean()),
            "imu_span_peak_gt_m1dBFS_frac": float((pk[imu] > -1).mean()),
            "section_LAeq_range": [min(x["LAeq"] for x in rows), max(x["LAeq"] for x in rows)],
            "n_s": int(len(la))}


# --------------------------------------------------------------------------- figure
def figure(run, mo, eps, hf_ex, snp, sh, gt, lv48, lag, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    BLUE, ORANGE, AQUA, GREY, INK, MUTED = "#2a78d6", "#eb6834", "#1baf7a", "#8a8984", "#0b0b0b", "#52514e"
    plt.rcParams.update({"font.size": 8.5, "axes.edgecolor": "#c9c8c2", "axes.labelcolor": INK,
                         "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.grid": True, "grid.color": "#ecebe7", "grid.linewidth": 0.6,
                         "axes.titlesize": 8.5, "axes.titlelocation": "left"})
    g = run.sensors["gyr"]
    w = np.linalg.norm(g[["x", "y", "z"]].values, axis=1)
    tg = g.t.values
    th = np.arange(len(hf_ex)) * HF_DT - lag            # audio frames on sensor time
    hfc = np.clip(hf_ex, -10, 45)                       # file-edge filter transients clipped for display
    T_END = float(run.sensors["acc"].t.iloc[-1])
    XL = (-6, 906)
    fig = plt.figure(figsize=(12, 15))
    gs = fig.add_gridspec(7, 2, height_ratios=[1, 1, 1, 1, 1.1, 0.9, 1.1], hspace=0.62, wspace=0.12,
                          top=0.935, bottom=0.04, left=0.06, right=0.98)

    def no_imu(ax, a, b):
        if a < 0:
            ax.axvspan(a, 0, color=GREY, alpha=0.18, lw=0)
        if b > T_END:
            ax.axvspan(T_END, b, color=GREY, alpha=0.18, lw=0)

    zoom = {"start": (-6, 20), "end": (884, 906)}
    for j, (nm, (a, b)) in enumerate(zoom.items()):
        ax = fig.add_subplot(gs[0, j])
        m = (tg >= a) & (tg <= b)
        ax.plot(tg[m], w[m], color=BLUE, lw=0.8)
        for e in eps:
            if e["figure8"]:
                ax.axvspan(e["t0"], e["t1"], color=BLUE, alpha=0.08, lw=0)
        for p in sh["events"]:
            if p["strong"] and a <= p["t_sensor"] <= b:
                ax.axvline(p["t_sensor"], color=ORANGE, lw=0.9, ls="--")
        no_imu(ax, a, b)
        ax.set_xlim(a, b)
        ax.set_title(f"{nm}: gyro |w| (rad/s)")
        ax2 = fig.add_subplot(gs[1, j])
        m = (th >= a) & (th <= b)
        ax2.plot(th[m], hfc[m], color=MUTED, lw=0.6)
        sp = snp[nm]["pair"] or []
        for i, s_ in enumerate(sp):
            ts_ = s_["t_audio"] - lag
            ax2.plot([ts_], [min(s_["excess_db"], 44)], "v", color=ORANGE, ms=8)
        if sp:
            ax2.annotate("2 snaps", (sp[-1]["t_audio"] - lag, 40), textcoords="offset points", xytext=(6, -4), color=INK, fontsize=8)
        for p in sh["events"]:
            if p["strong"] and a <= p["t_sensor"] <= b:
                ax2.axvline(p["t_sensor"], color=ORANGE, lw=0.9, ls="--")
        no_imu(ax2, a, b)
        ax2.set_xlim(a, b)
        ax2.set_ylim(-10, 48)
        ax2.set_title(f"{nm}: audio 2-6 kHz excess (dB)")
        ax2.set_xlabel("sensor time (s)")
    fig.text(0.06, 0.965, f"Zooms: blue band = figure-8, grey = outside the IMU recording, dashed orange = strong jolt seen by "
             f"both streams; audio shifted onto sensor time with the shared-jolt lag {lag:.3f} s.", fontsize=8, color=MUTED)
    ax = fig.add_subplot(gs[2, :])
    k = max(1, len(tg) // 20000)
    ax.plot(tg[::k], w[::k], color=BLUE, lw=0.5)
    for e in eps:
        ax.axvspan(e["t0"], e["t1"], color=BLUE if e["figure8"] else GREY, alpha=0.15, lw=0)
    ax.set_xlim(*XL)
    ax.set_title("whole ride: gyro |w| (rad/s); blue = figure-8 episodes, grey = other handling")
    ax = fig.add_subplot(gs[3, :])
    nb = len(hfc) // 20
    hb = hfc[: nb * 20].reshape(nb, 20).max(1)
    ax.plot(np.arange(nb) * 0.1 - lag, hb, color=MUTED, lw=0.4)
    for nm in ("start", "end"):
        for s_ in snp[nm]["pair"] or []:
            ax.plot([s_["t_audio"] - lag], [min(s_["excess_db"], 44)], "v", color=ORANGE, ms=7)
    no_imu(ax, -lag, len(hfc) * HF_DT - lag)
    ax.set_xlim(*XL)
    ax.set_ylim(-10, 48)
    ax.set_title("whole ride: audio 2-6 kHz excess (dB, 0.1 s max); orange triangles = snap pairs, both outside the IMU recording (grey)")
    ax = fig.add_subplot(gs[4, :])
    f = mo["feat"]
    ax.plot(f.t, f.score, color=INK, lw=0.6)
    ax.axhline(0.45, color=MUTED, lw=0.7, ls=":")
    for x in mo["stationary"]:
        c = AQUA if x["kind"] == "platform_dwell" else (ORANGE if x["kind"] == "short_stop" else GREY)
        ax.axvspan(x["t0"], x["t1"], color=c, alpha=0.2, lw=0)
    for d in mo["dwells"]:
        ax.text((d["t0"] + d["t1"]) / 2, 1.55, d["station"], ha="center", fontsize=8, color=INK)
    ax.set_ylim(-0.1, 1.7)
    ax.set_xlim(*XL)
    ax.set_title("motion score (stops.moving_score); aqua = platform dwell, orange = short stop, grey = handled edge; dotted = stop threshold 0.45")
    ax = fig.add_subplot(gs[5, :])
    gf = gt["_feat"]
    ax.plot(gf.t, gf.z, color=BLUE, lw=0.6)
    prm = gt["params"]
    ax.axhline(prm["z_on"], color=ORANGE, lw=0.9, ls="--")
    ax.axhline(prm["z_off"], color=MUTED, lw=0.7, ls=":")
    ax.text(XL[1] - 2, prm["z_on"] - 0.05, f"z_on {prm['z_on']}", ha="right", va="top", fontsize=7.5, color=INK)
    for x in mo["stationary"]:
        if x["kind"] in ("platform_dwell", "short_stop"):
            ax.axvspan(x["t0"], x["t1"], color=AQUA if x["kind"] == "platform_dwell" else ORANGE, alpha=0.2, lw=0)
    eng = int(gf.engaged.sum())
    ax.set_xlim(*XL)
    ax.set_ylim(-1.0, 0.9)
    ax.set_title(f"stop gate (attempt D defaults) causal z = mean log10 drop of vib, body below the moving reference; "
                 f"engages after 3 s below z_on. Engaged steps: {eng}")
    ax = fig.add_subplot(gs[6, :])
    ts_ = lv48.t_audio.values + 0.5 - lag
    ax.plot(ts_, lv48.LAeq_1s, color=INK, lw=0.6)
    ymin, ymax = lv48.LAeq_1s.min() - 2, lv48.LAeq_1s.max() + 4
    for s in mo["sections"]:
        ax.axvspan(s["depart_t"], s["arrive_t"], color=BLUE, alpha=0.08, lw=0)
        ax.text((s["depart_t"] + s["arrive_t"]) / 2, ymax - 1.5, f"{s['section']}  {s['LAeq']:.1f}", ha="center", fontsize=8, color=INK)
    ax.set_ylim(ymin, ymax)
    ax.set_xlim(*XL)
    ax.set_xlabel("sensor time (s)")
    ax.set_title("per-second LAeq, dBFS(A) uncalibrated, 48 kHz; blue = sections (stations inferred), label = section LAeq (energy mean)")
    fig.suptitle(f"S0 Elizabeth line WOL->LST, 24 Oct 2026 - {LABEL}", x=0.06, y=0.985, ha="left", fontsize=10.5, color=INK)
    fig.savefig(path, dpi=130)
    plt.close(fig)


# --------------------------------------------------------------------------- main
def main() -> dict:
    run = load_run(S0)
    acc = run.sensors["acc"]
    ev = run.events
    rec = {"start_text": str(ev["system time text"].iloc[0]), "events": ev.event.tolist(),
           "imu_t_first": float(acc.t.iloc[0]), "imu_t_last": float(acc.t.iloc[-1]),
           "stop_event_t": float(ev["experiment time"].iloc[-1]) if len(ev) > 1 else None,
           "imu_rate_hz": float(1 / np.median(np.diff(acc.t.values))),
           "imu_max_gap_s": float(np.diff(acc.t.values).max())}
    mo = motion(run)

    # ---- 16 kHz pass: envelopes, markers, sync, 16 kHz levels (all in memory)
    y, sr = load_audio(run, sr=SR_LO)
    dur = len(y) / sr
    co = SY.coarse_lag(run, y, sr)
    fi = SY.fine_lag(run, y, sr, co["lag"])
    hf = band_env_db(y, sr, *HF_BAND, HF_DT)
    on = band_env_db(y, sr, *ONSET_BAND, HF_DT)
    on_ex = on - ndimage.median_filter(on, 201)
    lo = band_env_db(y, sr, 20, 300, 0.1)
    from phonometry import weighting_filter
    n = len(y) // sr
    ya = weighting_filter(y, sr, "A")
    la16 = 10 * np.log10(np.maximum((ya[: n * sr].astype(np.float64) ** 2).reshape(n, sr).mean(1), 1e-20))
    del y, ya, on
    gc.collect()

    snp = snaps(hf, dur)
    hf_ex = snp.pop("excess")
    eps = gyro_episodes(run)
    for e in eps:
        if e["figure8"]:
            e["mag_sphere_fit"] = mag_sphere(run, e["t0"], e["t1"])
    sh = shared_events(run, on_ex, HF_DT)
    lag = sh["refined_lag_median_s"] if sh["refined_lag_median_s"] is not None else co["lag"]
    for end in ("start", "end"):
        for s_ in snp[end]["pair"] or []:
            s_["t_sensor_at_lag"] = s_["t_audio"] - lag
            s_["accel"] = accel_at(run, s_["t_audio"] - lag)
    g = run.sensors["gyr"]
    w = np.linalg.norm(g[["x", "y", "z"]].values, axis=1)
    wb = np.interp(np.arange(0, g.t.values[-1], 0.1), g.t.values, w)
    f8 = [e for e in eps if e["figure8"]]
    f8_audio = {("start" if e["t0"] < 450 else "end"): fig8_audio(wb, lo, e, lag) for e in f8}
    # is the end figure-8 cut by the recording end?
    tg = g.t.values
    last = [float(np.sqrt((w[(tg >= x) & (tg < x + 0.25)] ** 2).mean())) for x in np.arange(tg[-1] - 2.0, tg[-1], 0.25)]
    rest = float(np.median(w[(tg > mo["dwells"][1]["t0"] + 5) & (tg < mo["dwells"][1]["t1"] - 5)]))
    end_cut = {"imu_end_s": float(tg[-1]), "end_fig8": [e for e in f8 if e["t0"] > 450][0] if len(f8) > 1 else None,
               "rms_w_last_2s_per_0.25s": last, "rms_w_at_platform_rest": rest,
               "handled_threshold_rad_s": O.HANDLED,
               "stop_event_minus_start_s": rec["stop_event_t"],
               "audio_seconds_after_imu_end": dur - (tg[-1] + lag)}

    # ---- 48 kHz levels, streamed
    lv48 = per_second_levels(run.audio_path)
    la_sensor = pd.Series(lv48.LAeq_1s.values, index=lv48.t_audio.values + 0.5 - lag)
    gt = gate_test(run, mo, la_sensor)
    lev = s0_levels(lv48, la16, mo, lag)
    for s, x in zip(mo["sections"], lev["sections"]):
        s["LAeq"] = x["LAeq"]
    cen = central_levels()
    bias = r5_bias()

    figure(run, mo, eps, hf_ex, snp, sh, gt, lv48, lag, OUT_PNG)

    res = {
        "label": LABEL, "run": S0.stem, "line": "Elizabeth (stations inferred: WOL, CUH, CWF, WCL, LST)",
        "recording": rec, "audio_duration_s": dur,
        "motion": {"stationary_intervals": mo["stationary"], "sections": mo["sections"],
                   "depart_wol_s": mo["depart_wol"], "arrive_lst_s": mo["arrive_lst"],
                   "note": "sensor time; edges = stops.stationary_intervals edges; stations inferred from three long dwells"},
        "part1_markers": {
            "sync": {"coarse_lag": {k: v for k, v in co.items() if k != "combos"}, "coarse_combos": co["combos"],
                     "fine_lag": fi, "shared_event_lag": {k: v for k, v in sh.items() if not k.startswith("_")},
                     "lag_used_s": lag,
                     "drift_ppm_fine_first_vs_last": (fi["last"]["lag"] - fi["first"]["lag"]) / (2 * tg[-1] / 3) * 1e6,
                     "drift_resolution_ppm": 0.01 / (2 * tg[-1] / 3) * 1e6},
            "snaps": snp, "gyro_episodes": eps, "figure8_in_audio": f8_audio, "end_cut": end_cut},
        "part2_stop_gate": {k: v for k, v in gt.items() if not k.startswith("_")},
        "part3_levels": {"s0": lev, "central_23oct": {k: (v.to_dict("records") if isinstance(v, pd.DataFrame) else v)
                                                       for k, v in cen.items()},
                         "decode_bias_r5": bias},
    }
    res = r(res, 3)
    OUT_JSON.write_text(json.dumps(res, indent=1))
    OUT_MD.write_text(markdown(res))
    return res


# --------------------------------------------------------------------------- markdown
def markdown(R: dict) -> str:
    p1, p2, p3 = R["part1_markers"], R["part2_stop_gate"], R["part3_levels"]
    sy, sh = p1["sync"], p1["sync"]["shared_event_lag"]
    co, fi = sy["coarse_lag"], sy["fine_lag"]
    snp, f8a, ec = p1["snaps"], p1["figure8_in_audio"], p1["end_cut"]
    s0, c, b = p3["s0"], p3["central_23oct"], p3["decode_bias_r5"]
    f8 = [e for e in p1["gyro_episodes"] if e["figure8"]]
    L = []
    a = L.append
    a("# S0 Elizabeth line test ride (WOL -> LST, 24 Oct 2026)\n")
    a(f"**{R['label']}.** One ride, one phone, recorded after the pre-registration and kept out of the frozen "
      "pipeline (the `S0` stem is not matched by `ingest.RUN_RE`). Stations are inferred, not observed: the three long dwells "
      "are taken to be CUH, CWF and WCL. Levels are dBFS(A), uncalibrated. Source: `posthoc/s0_elizabeth.py`. Every number "
      "is in `s0_elizabeth.json`, and `s0_elizabeth.png` shows the traces.\n")
    a(f"Recording: phyphox START {R['recording']['start_text']}, events {R['recording']['events']}, IMU "
      f"{R['recording']['imu_t_first']}-{R['recording']['imu_t_last']} s at {R['recording']['imu_rate_hz']} Hz "
      f"(largest gap {R['recording']['imu_max_gap_s']} s, so continuous); audio {R['audio_duration_s']} s.\n")

    # ------------------------------------------------------------------ part 1
    a("## 1. Marker test\n")
    a("**Result.** The snaps and the figure-8 do not work as sync markers in this file. All four snaps fall outside "
      "the IMU recording, and the figure-8 does not show in the audio above chance. The file does contain shared events, "
      f"though: {sh['n_refined']} hard jolts to the phone, around the start figure-8 and the handling just after it, are "
      f"loud in both streams. They give a lag of **{sh['refined_lag_median_s']} s** (spread {sh['refined_lag_spread_s']} s), "
      f"which is {abs(sh['refined_lag_median_s'] - co['lag']):.2f} s from `coarse_lag` ({co['lag']} s) and "
      f"{abs(sh['refined_lag_median_s'] - fi['all']['lag']):.2f} s from `fine_lag` ({fi['all']['lag']} s). "
      "**Protocol change:** after starting both recorders, knock the phone case firmly 3 times. The accelerometer and the "
      "microphone both register a knock. Do this at both ends, while both recorders are running.\n")
    a("### Sync lag (audio_t = sensor_t + lag)\n")
    a("| method | lag (s) | quality |\n|---|---|---|")
    a(f"| `sync.coarse_lag` (1 s envelopes, 9 band combos) | {co['lag']} | support {co['support']}/{co['n']}, spread {co['spread']} s, peak margin {co['peak_margin']} |")
    a(f"| `sync.fine_lag` (10 ms; whole / first third / last third) | {fi['all']['lag']} / {fi['first']['lag']} / {fi['last']['lag']} | z {fi['all']['z']} / {fi['first']['z']} / {fi['last']['z']} (band {fi['band']} Hz) |")
    a(f"| shared jolts: lag scan -20..+30 s, no prior (strong jolts, n={sh['n_strong']}) | {sh['lag_scan_best_s']} | score {sh['score_best_db']} dB; other lags: median {sh['null_score_median_db']}, p99 {sh['null_score_p99_db']}, max {sh['null_score_max_db']} (z {sh['null_z']}) |")
    a(f"| shared jolts: per-event onset, audio peak >= 10 dB (n={sh['n_refined']}) | **{sh['refined_lag_median_s']}** | spread {sh['refined_lag_spread_s']} s |")
    a("")
    endj = [e for e in sh["events"] if e["t_sensor"] > 880]
    a(f"Drift: the first and last thirds of `fine_lag` differ by one 10 ms step ({sy['drift_ppm_fine_first_vs_last']} ppm, "
      f"resolution {sy['drift_resolution_ppm']} ppm). The last third is weak (z {fi['last']['z']}). All strong jolts fall in "
      f"the first {max(sh['strong_times_s']):.0f} s, so this file cannot test end-vs-start agreement with events. "
      + (f"The only jolt near the end ({endj[0]['t_sensor']} s, {endj[0]['jolt_m_s2']} m/s^2) gives an audio onset of just "
         f"{endj[0]['audio_peak_within_0.1s_db']} dB, which is too weak to count. " if endj else "")
      + "Both clocks are on the same device, and drift below ~20 ppm is about 0.02 s over 15 min.\n")
    a("Shared events (jolt = 8 Hz high-passed |acc| from `sync.accel_spikes`; audio = 100-6000 Hz onset excess over a 1 s median, "
      "peak within +-0.1 s of sensor t + scan lag). Weak jolts are listed for completeness. Their offsets are forced near the lag "
      "by the +-0.1 s window, so only rows with an audio peak >= 10 dB count as evidence.\n")
    a("| sensor t (s) | jolt (m/s^2) | audio peak (dB) | offset (s) |\n|---|---|---|---|")
    for e in sh["events"]:
        if e["strong"] or e["audio_peak_within_0.1s_db"] >= 8:
            a(f"| {e['t_sensor']} | {e['jolt_m_s2']}{' (strong)' if e['strong'] else ''} | {e['audio_peak_within_0.1s_db']} | {e['offset_s']} |")
    a(f"\n({len(sh['events'])} jolts in total; rows with jolt < {JOLT_MIN} m/s^2 and audio < 8 dB are omitted here and listed in the JSON.)\n")
    a("### Snaps (audio: 2-6 kHz excess over a 1 s running median)\n")
    a("| end | audio t (s) | excess (dB) | decay to -15 dB (ms) | sensor t at lag (s) | inside IMU recording |\n|---|---|---|---|---|---|")
    for end in ("start", "end"):
        for s_ in snp[end]["pair"] or []:
            a(f"| {end} | {s_['t_audio']} | {s_['excess_db']} | {s_['decay_ms']} | {s_['t_sensor_at_lag']} | {'yes' if s_['accel']['inside_imu'] else 'no'} |")
    a(f"\nThe snaps are clean audio markers: excess of 35-39 dB with a decay of 10 ms or less. Each pair sits before phyphox "
      f"START (audio {sh['refined_lag_median_s']} s) or after the IMU end (audio {ec['imu_end_s'] + sh['refined_lag_median_s']:.2f} s). "
      "The accelerometer therefore cannot show them in this file, and nothing can be checked on it. A snap also never moves the phone. "
      "Two of the start-window clicks, at audio 9.30 and 16.27 s, have the same 2-6 kHz signature but come with 10-12 m/s^2 jolts. "
      "They are the phone being knocked, not snaps (they appear in the shared-event table at sensor 4.47 and 11.39 s).\n")
    a("### Figure-8 (gyro)\n")
    a("| t (s) | dur (s) | peak / max 1 s RMS (rad/s) | axis RMS share x/y/z | min/max axis | dominant Hz x/y/z | figure-8 | raw mag sphere fit |\n|---|---|---|---|---|---|---|---|")
    for e in p1["gyro_episodes"]:
        ms = e.get("mag_sphere_fit")
        a(f"| {e['t0']}-{e['t1']} | {e['dur_s']} | {e['peak_rad_s']} / {e['rms_1s_peak_rad_s']} | {'/'.join(str(v) for v in e['axis_share'])} | "
          f"{e['axis_balance_min_over_max']} | {'/'.join(str(v) for v in e['dominant_hz_xyz'])} | {'yes' if e['figure8'] else 'no (handling)'} | "
          f"{'radius %s uT, misfit %s' % (ms['radius_uT'], ms['misfit_rel']) if ms else ''} |")
    a("\nBoth figure-8s show in the gyro, balanced across the three axes (min/max axis RMS 0.84-0.85). As a magnetometer "
      "calibration they fail: the sphere-fit misfit is 22-35 % of the radius, and the ~33 uT radius is well below the Earth "
      "field (~49 uT in London). Both waves were presumably made on a platform (start: before boarding at Woolwich; end: after alighting at Liverpool Street), where the field is distorted.\n")
    a("Figure-8 in the audio: Pearson r between gyro |w| and the 20-300 Hz envelope (0.1 s bins). The null is r at 2000 random lags.\n")
    a("| figure-8 | r at shared-event lag | percentile of that r in the null | best lag in +-10 s (r) | null p95 / p99 |\n|---|---|---|---|---|")
    for k, v in f8a.items():
        a(f"| {k} {v['window_sensor_s']} | {v['r_at_shared_event_lag']} | {v['percentile_of_r_at_lag_in_null']} | "
          f"{v['best_lag_within_10s']} ({v['r_best']}) | {v['null_r_p95']} / {v['null_r_p99']} |")
    a("\nAt the true lag the correlation is at chance level (start) or negative (end). The 'best' lags (6.3 s and 0.7 s) disagree "
      "with each other and with the jolts. The wave leaves no usable handling-noise signature in the microphone.\n")
    last = ec["rms_w_last_2s_per_0.25s"]
    a(f"**End of recording.** The IMU stops at {ec['imu_end_s']} s (PAUSE event {ec['stop_event_minus_start_s']} s after START, "
      "a round 15 min, which suggests a phyphox timer but is not confirmed). In the last 2 s, RMS |w| per 0.25 s is "
      f"{last} rad/s. That is still above the 0.5 rad/s handled threshold until about 0.75 s before the end, and far above "
      f"platform rest ({ec['rms_w_at_platform_rest']} rad/s). The end figure-8 lasts {f8[-1]['dur_s'] if f8 else 'n/a'} s against "
      f"{f8[0]['dur_s'] if f8 else 'n/a'} s at the start. It was dying down, but the phone was still moving when recording "
      f"stopped, so its tail **was probably cut**. The audio runs {ec['audio_seconds_after_imu_end']} s past the IMU end, and the "
      "end snaps fall in that stretch.")
    pair_t = {s_["t_audio"] for s_ in (snp["end"]["pair"] or [])}
    extra = [s_ for s_ in snp["end"]["candidates"] if s_["t_audio"] not in pair_t]
    for s_ in extra:
        ts_ = s_["t_audio"] - sh["refined_lag_median_s"]
        a(f" There is also a weaker 2-6 kHz click at audio {s_['t_audio']} s (excess {s_['excess_db']} dB, decay {s_['decay_ms']} ms), "
          f"which is sensor ~{ts_:.2f} s at the shared-event lag, {ts_ - ec['imu_end_s']:+.2f} s from the IMU end. It may be the "
          "screen tap that stopped phyphox, but its cause is not determined, so the timer reading stays unconfirmed either way. "
          "It is not used as a sync or drift event.")
    a("\n")

    # ------------------------------------------------------------------ part 2
    a("## 2. Stop gate on real stops (gate only)\n")
    a("S0 has no map, so this tests the gate and not the tracker. `GateParams()` defaults (attempt D, tuned on the Central line "
      f"23 Oct only): {p2['params']}. The gate runs causally from the inferred Woolwich departure ({p2['start_sensor_s']} s) to the "
      f"end of the recording: {p2['n_steps']} steps of {O.STEP} s, with valid/handled passed as `GatedTracker.step` does. "
      f"Gyro bias comes from the three long dwells.\n")
    eng_total = p2["engaged_steps_total"]
    a(f"**Result: the gate never engaged** ({eng_total} engaged steps in the whole ride). That covers the three platform dwells "
      f"of 53-64 s each. Its lowest z was {p2['z_min_whole_ride']} at {p2['z_min_whole_ride_at_s']} s, which is about 4 s into the "
      f"CUH dwell and just short of z_on = {p2['params']['z_on']}. On the 23 Oct Central runs the same gate flagged 69-85 % of "
      "dwell interiors on R2-R6 (`attempts_manual.jsonl` D). So on this line **recall is 0/3 platform dwells**, and the zero false "
      "engagements while running say nothing, because the gate was never on.\n")
    a("| interval | dur (s) | interior flagged | engage delay (s) | z median / min (at s) | moving ref vib at start -> end | drop vs section running median (non-causal) | vib / body median | abs yaw median / max (deg/s) | LAeq_1s median |\n|---|---|---|---|---|---|---|---|---|---|")
    for k, v in list(p2["dwells"].items()) + [(f"short {k}", v) for k, v in p2["short_stops"].items()]:
        cc = v["cues"]
        a(f"| {k} | {v['dur_s']} | {v['interior_flagged_frac']} | {v['engage_delay_s'] if v['engage_delay_s'] is not None else 'never'} | "
          f"{cc['z_med']} / {v['z_min']} ({v['z_min_at_s_after_t0']}) | {v['ref_vib_at_t0_t1'][0]} -> {v['ref_vib_at_t0_t1'][1]} | "
          f"{v['noncausal_drop_vs_section_running_median']} | {cc['vib_med']} / {cc['body_med']} | {cc['abs_yaw_med_deg_s']} / {cc['abs_yaw_max_deg_s']} | {v['LAeq_1s_median_dbfs']} |")
    rf = p2["reference_levels"]
    pd_, rn = rf["platform_dwell_interiors"], rf["running"]
    a(f"| *ref: platform dwell interiors* | | | | {pd_['z_med']} | | | {pd_['vib_med']} / {pd_['body_med']} | {pd_['abs_yaw_med_deg_s']} / {pd_['abs_yaw_max_deg_s']} | {rf['platform_dwell_LAeq_1s_median']} |")
    a(f"| *ref: running* | | | | {rn['z_med']} (p5 {rf['running_z_p5']}, p1 {rf['running_z_p1']}) | | | {rn['vib_med']} / {rn['body_med']} (p5 {rf['running_vib_p5']} / {rf['running_body_p5']}) | {rn['abs_yaw_med_deg_s']} / {rn['abs_yaw_max_deg_s']} | {rf['running_LAeq_1s_median']} |")
    a("")
    drops = [v["noncausal_drop_vs_section_running_median"] for v in p2["dwells"].values()]
    a("**Why it missed (diagnosis, not a fix).** Measured against the running median of the preceding section, each dwell sits "
      f"{min(drops)} to {max(drops)} log10 below running (non-causal column). That is around z_on = -0.8, and smaller than the "
      "causal dwell z medians of -0.93 to -1.10 that the Central runs reached. Two things combine. (a) On this train and placement "
      "the dwell/running contrast is smaller to begin with. (b) The causal reference makes it worse. One possible reason (not "
      "measured; n=1, no speed reference) is a longer, smoother braking approach, which would put slow approach steps into the "
      f"60 s 'moving' reference before the stop. Either way, the best z in any dwell is only {max(v['z_min'] for v in p2['dwells'].values())} to {min(v['z_min'] for v in p2['dwells'].values())}. Because z stays above "
      "z_on, the quiet counter stays at 0, and the dwell steps themselves keep being appended to the reference (`StopGate.update` "
      "appends whenever `quiet == 0`). The reference then sinks into the dwell: the vib reference falls by about 0.8 log10 during each "
      "dwell (column 'moving ref'), and z drifts back towards 0. Attempt D only stopped pre-engage steps with z < z_on from "
      "polluting the reference. Steps that are quiet but above z_on still get in. Any fix (for example freezing the reference "
      "while z < z_off, or using a slower reference) would be a new post-hoc attempt, and it must not be tuned on this one ride.\n")
    a(f"**False engagements while running:** {p2['false_engagement_count']} ({p2['false_engaged_running_s']} s over "
      f"{p2['running_s']} s of running, defined as sections minus 5 s at each edge, minus the short stops +-2 s). This is not "
      "evidence of specificity, because the gate never engaged anywhere.\n")
    a("### The short stationary intervals\n")
    a("| short stop | then | gap to dwell (s) | gap vib / body | gap abs yaw median | gap LAeq_1s median |\n|---|---|---|---|---|---|")
    for k, v in p2["short_stops"].items():
        g = v.get("gap_to_next_dwell")
        if g:
            a(f"| {k} | {g['station']} dwell | {g['gap_s']} | {g['cues']['vib_med']} / {g['cues']['body_med']} | {g['cues']['abs_yaw_med_deg_s']} | {g['LAeq_1s_median_dbfs']} |")
        else:
            a(f"| {k} | LST arrival (terminal, rider alights) | - | - | - | - |")
    v = p2["short_stops"].get("685.8-696.3")
    if v:
        cc = v["cues"]
        a(f"\n**685.8-696.3 s (10.5 s, about 9 s before the inferred WCL platform stop).** The speed evidence points to a "
          f"standstill, or a crawl too slow to make rolling noise. The audio level is {v['LAeq_1s_median_dbfs']} dBFS(A), "
          f"at platform-dwell level ({rf['platform_dwell_LAeq_1s_median']}) and about 9 dB below running "
          f"({rf['running_LAeq_1s_median']}). Yaw is {cc['abs_yaw_med_deg_s']} deg/s, the same as at a dwell (running median "
          f"{rn['abs_yaw_med_deg_s']}), so the train is not curving. Vib / body are {cc['vib_med']} / {cc['body_med']}, below "
          f"the running p5 ({rf['running_vib_p5']} / {rf['running_body_p5']}) but about 0.4 log10 above the platform dwells "
          f"({pd_['vib_med']} / {pd_['body_med']}). The barometer is flat (`stops.stationary_intervals` dpdt median ~0.01 hPa/s). "
          f"The gate did not engage: z median {cc['z_med']}, min {v['z_min']}. With no position or speed reference in the tunnel, "
          "a stop-short / creep cannot be told apart from a signal check here. The 456.3-459.8 s interval before CWF shows the "
          "same pattern.\n")
        gp = [x["gap_to_next_dwell"] for x in p2["short_stops"].values() if x.get("gap_to_next_dwell")]
        if gp:
            a("Between each short stop and its platform dwell (the 'gap' columns above), the audio stays near dwell level "
              f"({', '.join(str(g['LAeq_1s_median_dbfs']) for g in gp)}), while car-body motion rises "
              f"({', '.join(str(g['cues']['body_med']) for g in gp)} log10, against {pd_['body_med']} at a dwell). That pattern fits "
              "a stop short of the platform followed by a slow creep onto the stopping mark. If so, the CWF and WCL arrival "
              f"edges used here (`stops.stationary_intervals`) mark the end of the creep and could be up to "
              f"{max(g['gap_s'] for g in gp)} s after the first standstill. That is a reading, not an observation.\n")

    # ------------------------------------------------------------------ part 3
    a("## 3. Cross-line levels and limiter (same level statistics on both lines)\n")
    a("The section and dwell edges come from different detectors on the two lines: S0 uses raw `stops.stationary_intervals` "
      "edges; Central uses `outputs/sections.json` (refined segment edges mapped through `outputs/sync.json`). Over multi-minute "
      "sections this should matter little, but it is not identical.\n")
    a("Level statistics, identical for S0 and Central: 1 s A-weighted LAeq (phonometry `weighting_filter`, 48 kHz) over seconds whose "
      "centre falls in the section's audio window. LAeq = energy mean. L10 / L90 = 90th / 10th percentile of the 1 s levels. "
      "Peak > -1 dBFS = share of seconds whose sample peak exceeds -1 dBFS. Limited = share within 1 dB of the recording's own "
      "ceiling (`noise.LIMIT_DB`). Dwell = non-truncated platform dwells minus 3 s at each end. Central figures are recomputed from "
      "`outputs/noise/*_levels.csv` with the windows in `outputs/gonogo/loudness_sections.csv` (built from `outputs/sections.json` "
      f"and `outputs/sync.json`). They reproduce that CSV to within {c['max_abs_diff_vs_loudness_sections_csv_db']} dB.\n")
    a("| S0 section (stations inferred) | dur (s) | LAeq | L10 | L90 | peak > -1 dBFS | limited | LAeq from 16 kHz decode (diff) |\n|---|---|---|---|---|---|---|---|")
    for x in s0["sections"]:
        a(f"| {x['section']} | {x['duration_s']} | {x['LAeq']} | {x['L10']} | {x['L90']} | {x['peak_gt_m1dBFS_frac']} | {x['limited_frac']} | {x['LAeq_16k']} ({x['bias_16k_minus_48k_db']:+}) |")
    a(f"\nS0 moving {s0['moving']['LAeq']} vs platform dwell {s0['dwell']['LAeq']}, a difference of **{s0['moving_minus_dwell_db']:+} dB**. "
      f"Moving: peak > -1 dBFS {s0['moving']['peak_gt_m1dBFS_frac']}, limited {s0['moving']['limited_frac']}. "
      f"Recording ceiling {s0['ceiling_dbfs']} dBFS. Whole file: peak > -1 dBFS {s0['whole_file_peak_gt_m1dBFS_frac']}.\n")
    a(f"Central 23 Oct ({c['n_section_runs']} section-runs; {c['n_included']} not placement-excluded):\n")
    a("| Central track (included runs) | n | LAeq (mean of runs) | peak > -1 dBFS | limited |\n|---|---|---|---|---|")
    for k, v in c["tracks_lst_bth_mle"].items():
        a(f"| {k} | {v['n']} | {v['LAeq_mean_of_runs']} | {v['peak_gt_m1dBFS_frac_mean']} | {v['limited_frac_mean']} |")
    a(f"\nAll included Central section-runs, including R0.5 OXC->LST: LAeq {c['LAeq_range_all_included'][0]} to {c['LAeq_range_all_included'][1]}, "
      f"peak > -1 dBFS {c['peak_gt_m1_frac_range_included'][0]}-{c['peak_gt_m1_frac_range_included'][1]}, limited "
      f"{c['limited_frac_range_included'][0]}-{c['limited_frac_range_included'][1]}.\n")
    a("| Central run | moving LAeq | dwell LAeq (n s) | moving - dwell | moving peak > -1 dBFS | moving limited | sync (`outputs/sync.json`) |\n|---|---|---|---|---|---|---|")
    for k, v in c["runs"].items():
        md_ = v["moving_minus_dwell_db"]
        a(f"| {k} | {v['moving'].get('LAeq')} | {v['dwell'].get('LAeq')} ({v['dwell'].get('n_s')}) | {md_ if md_ is not None else '-'} | "
          f"{v['moving'].get('peak_gt_m1dBFS_frac')} | {v['moving'].get('limited_frac')} | {v['sync_confidence']} (+-{v['sync_uncertainty_s']} s) |")
    a(f"\nDecode rate: {b['section']} gives {b['LAeq_16k']} from a 16 kHz decode against {b['LAeq_48k_csv']} in the 48 kHz CSV "
      f"(difference {b['bias_16k_minus_48k_csv_db']:+} dB). Our streamed 48 kHz decode reproduces the CSV to {b['stream_vs_csv_max_abs_1s_db']} dB "
      "per second. On S0 the 16 kHz minus 48 kHz difference is below 0.01 dB per section. S0 is reported at 48 kHz, so decode "
      "rate adds no bias.\n")
    tr = c["tracks_lst_bth_mle"]
    lo_c, hi_c = min(v["LAeq_mean_of_runs"] for v in tr.values()), max(v["LAeq_mean_of_runs"] for v in tr.values())
    a("### Reading (ranking direction only)\n")
    a(f"- Each S0 section ({s0['section_LAeq_range'][0]} to {s0['section_LAeq_range'][1]} dBFS(A)) is **quieter in this phone's "
      f"recording** than every LST-BTH-MLE track on the Central line ({lo_c} to {hi_c}), by {lo_c - s0['section_LAeq_range'][1]:.1f}-"
      f"{hi_c - s0['section_LAeq_range'][0]:.1f} dB. The ranking runs in the expected direction. "
      + (("Some included Central section-runs are quieter than S0's loudest section: "
          + ", ".join(f"{x['run_id']} {x['section']} {x['LAeq']}" for x in c['sections']
                      if not x['placement_excluded'] and x['LAeq'] < s0['section_LAeq_range'][1]) + ".")
         if any(not x['placement_excluded'] and x['LAeq'] < s0['section_LAeq_range'][1] for x in c['sections']) else "") + "\n")
    a(f"- The limiter is pinned on the Central line far more often. Central LST-BTH-MLE seconds within 1 dB of the ceiling: "
      f"{min(v['limited_frac_mean'] for v in tr.values())}-{max(v['limited_frac_mean'] for v in tr.values())}. S0 sections: "
      f"{min(x['limited_frac'] for x in s0['sections'])}-{max(x['limited_frac'] for x in s0['sections'])}. The limiter compresses the "
      "loud end, so the Central levels are biased low and the true gap is, if anything, larger than measured.\n")
    a(f"- Gain is a caveat. S0's platform dwells ({s0['dwell']['LAeq']}) are also 3-6 dB quieter than the Central R2-R6 dwells "
      "(-25.3 to -28.5; R0.5's busy central-London dwells and R1's 7 s of dwell are outliers), yet "
      f"S0's moving-minus-dwell contrast (+{s0['moving_minus_dwell_db']} dB) falls inside the Central R2-R6 range "
      "(+6.6 to +9.2 dB). Part of the absolute gap may therefore come from different stations or a different recording gain "
      "(the mic mode is unknown), not from the train alone.\n")
    a("- Not like-for-like: same phone and app (Voice Memos), but the mic mode is unknown, the car and stock differ, the placement "
      "differs, and S0 is one run against 2-3 per Central track. Central sync is mostly low confidence (+-5 s), while S0 sync is "
      "~0.05 s from the jolts. With multi-minute sections that matters little.\n")
    a("- TfL context only, not a comparison: in-cab LAeq on the Central line is 82-87 dB(A) (FOI-1231-2425, 24 Jun 2024), and "
      "on the Hammersmith & City line LST-ALE-WCL it is 64-67 dB(A) (FOI-3780-2324, 2020, S7 stock). Neither covers the "
      "Elizabeth line, and the investigator corpus has no TfL Elizabeth line noise figure. "
      "None of these dBFS numbers converts to dB(A).\n")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
