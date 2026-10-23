"""Online (causal) in-section positioning for the live demo.

Every quantity at time t uses data up to t only:
- Attitude: 'up' in the phone frame is propagated by the gyro and pulled towards a/|a| with a
  30 s time constant (complementary filter), so a hand-held phone's tilt changes don't leak
  gravity into the horizontal acceleration the way a plain low-pass 'up' does.
- yaw: bias-corrected gyro projected on that up, causal 2nd-order low-pass 1 Hz, deg/s
  (+ = left turn, as features.yaw_rate).
- h: |horizontal specific force|, causal low-pass 0.3 Hz, m/s^2. A departure is a jump in h
  (~1 m/s^2 within 2 s as the train pulls away).
Features are computed once with causal filters over the whole recording, which gives exactly what
a streaming filter would have produced at each step; the Tracker then consumes them step by step.
Steps are on wall-clock time (STEP s), so a phyphox pause is a run of steps with no data.

Map (Template): one per section and direction, built offline from earlier clean runs. Its grid is
a base run's L0 grid (repeat.GRID, wall-clock fraction depart -> arrive) with departure redefined
by the same causal detector as online; every other run is DTW-aligned onto it
(position.dtw_warp on yaw, open ends). Per bin it stores the mean / sd of the causal yaw, the mean
seconds the train spends in the bin, and the energy-mean LAeq_1s of the runs' audio.
Metres = fraction x straight-line section length (as in scripts/ablation.py), so the map's metre
axis inherits the base run's speed profile and the chord length; both are approximations.

Tracker: an HMM over (speed ratio r, map bin j). Each step the train advances r x STEP / tau_j
bins; r random-walks between neighbouring levels. The yaw likelihood is Gaussian around the map
(+ an outlier floor) and tempered by GAMMA, because 4 Hz samples of a 1 Hz signal are not
independent. Unknown start (a clip from mid-section) = uniform prior over position.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numba as nb
import numpy as np
import pandas as pd
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, sosfilt, sosfilt_zi

from . import features as F
from . import position as P
from . import repeat as R
from .ingest import Run

STEP = 0.25          # s, tracker step
TAU_ATT = 30.0       # s, attitude filter time constant
JUMP = 0.4           # m/s^2 rise in h within JUMP_WIN that marks a departure
JUMP_MIN = 0.5       # m/s^2, h must also exceed this
JUMP_WIN = 4.0       # s
HANDLED = 0.5        # rad/s, |gyro| above this = phone being handled: no yaw evidence
SPEEDS = np.geomspace(0.6, 1.6, 9)  # speed ratio levels vs the map's mean speed profile
P_SPEED = 0.03       # per-step probability of moving to a neighbouring speed level
GAMMA = 0.25         # likelihood tempering (about one independent yaw sample per second)
YAW_SD_FLOOR = 0.35  # deg/s, added in quadrature to the map's run-to-run sd
OUTLIER = 0.05       # mixture weight of a flat outlier likelihood
NOISE_BINS = 40      # strip-map noise bins per section (~55 m on LST-BTH)


# --------------------------------------------------------------------------- causal features
@nb.njit(cache=True)
def _attitude(A, G, dt, tau):
    n = A.shape[0]
    up = A[0] / np.sqrt((A[0] ** 2).sum())
    out = np.empty_like(A)
    alpha = dt / tau
    for i in range(n):
        w = G[i]
        # a fixed world vector seen from a body rotating at w: du/dt = -w x u
        c0 = w[1] * up[2] - w[2] * up[1]
        c1 = w[2] * up[0] - w[0] * up[2]
        c2 = w[0] * up[1] - w[1] * up[0]
        up = up - np.array([c0, c1, c2]) * dt
        a = A[i] / np.sqrt((A[i] ** 2).sum())
        up = (1 - alpha) * up + alpha * a
        up = up / np.sqrt((up ** 2).sum())
        out[i] = up
    return out


def causal_lowpass(x: np.ndarray, fs: float, fc: float, order: int = 2) -> np.ndarray:
    """Causal Butterworth low-pass along axis 0, started in steady state at x[0]."""
    sos = butter(order, fc, btype="low", fs=fs, output="sos")
    zi = sosfilt_zi(sos)[(slice(None), slice(None)) + (None,) * (x.ndim - 1)] * x[0]
    return sosfilt(sos, x, axis=0, zi=zi)[0]


def stream_features(run: Run, bias: np.ndarray | None = None) -> pd.DataFrame:
    """Causal features on a wall-clock STEP grid over the whole recording.

    Columns: wall, t (experiment time, NaN inside a phyphox pause), valid, yaw (deg/s),
    h (m/s^2), gyr (rad/s, 1 Hz causal low-pass of |gyro|), vib (log10 RMS of 3-40 Hz |acc|
    over the last 2 s). bias: gyro bias (rad/s); ~1e-4 on this phone, so None is fine online.
    """
    acc, gyr = run.sensors["acc"], run.sensors["gyr"]
    t = acc.t.values
    fs = F._fs(t)
    A = acc[["x", "y", "z"]].values
    G = F._on(gyr, t) - (0 if bias is None else bias)
    up = _attitude(A, G, 1.0 / fs, TAU_ATT)
    yaw = causal_lowpass(np.degrees(np.einsum("ij,ij->i", G, up)), fs, 1.0)
    g0 = causal_lowpass(np.linalg.norm(A, axis=1), fs, 1 / 60)  # |g| as the sensor reads it, causal
    lin = A - up * g0[:, None]
    h = np.linalg.norm(causal_lowpass(lin - np.einsum("ij,ij->i", lin, up)[:, None] * up, fs, 0.3), axis=1)
    g = causal_lowpass(np.linalg.norm(G, axis=1), fs, 1.0)
    an = np.linalg.norm(A, axis=1)
    sos = butter(4, [3, 40], btype="band", fs=fs, output="sos")
    bp = sosfilt(sos, an - an[0])
    k2 = int(2 * fs)
    vib = np.log10(np.sqrt(np.convolve(bp ** 2, np.ones(k2) / k2)[: len(bp)]) + 1e-6)  # trailing window
    wall = run.to_wall(t)
    wg = np.arange(wall[0], wall[-1], STEP)
    # last sample at or before each step (causal); a step with no new sample in a pause is invalid
    k = np.searchsorted(wall, wg, side="right") - 1
    valid = (wg - wall[k]) < 2 * STEP
    te = np.where(valid, t[k] + (wg - wall[k]), np.nan)
    return pd.DataFrame({"wall": wg, "t": te, "valid": valid, "yaw": yaw[k], "h": h[k], "gyr": g[k], "vib": vib[k]})


def departures(feat: pd.DataFrame) -> list[dict]:
    """Candidate departure events: h jumps by JUMP within JUMP_WIN while the phone is not handled.

    h is a magnitude, so braking and speed changes jump too: a candidate only means "departure"
    while the train is known to be at a platform. Use departure_after(), armed from the start of a
    recording (made on the platform) or from the tracker's own arrival, and disarmed while tracking.
    Returns [{wall_detect, wall_onset}]: the detection time, and the onset back-dated to the
    minimum of h in the window (known at detection time, so still causal for the tracker).
    """
    h, g, v, w = feat.h.values, feat.gyr.values, feat.valid.values, feat.wall.values
    n = int(JUMP_WIN / STEP)
    out, i = [], n
    while i < len(h):
        win = h[i - n: i + 1]
        j = int(np.argmin(win))
        if (v[i - n: i + 1].all() and h[i] > JUMP_MIN and h[i] - win[j] > JUMP
                and g[i - n: i + 1].max() < HANDLED):
            out.append({"wall_detect": float(w[i]), "wall_onset": float(w[i - n + j])})
            i += int(30 / STEP)  # refractory
        else:
            i += 1
    return out


def departure_after(feat: pd.DataFrame, wall_from: float) -> dict | None:
    """The first candidate departure once the detector is armed at wall_from (train at a platform)."""
    sub = feat[feat.wall >= wall_from].reset_index(drop=True)
    ev = departures(sub)
    return ev[0] if ev else None


# --------------------------------------------------------------------------- map
@dataclass
class Template:
    track: str                 # e.g. "EB LST->BTH"
    length_m: float
    base: str                  # base run key, e.g. "R5:LST-BTH"
    runs: list[str]
    yaw_mu: np.ndarray         # (N,) deg/s, causal yaw
    yaw_sd: np.ndarray         # (N,) deg/s, run-to-run sd (0 with one run)
    tau: np.ndarray            # (N,) s per bin at the mean speed profile
    duration_s: float          # mean section time
    dist_m: np.ndarray = field(default_factory=lambda: np.zeros(0))  # (N,) dead-reckoned metres per bin
    noise_db: np.ndarray = field(default_factory=lambda: np.full(NOISE_BINS, np.nan))  # energy-mean LAeq_1s, dBFS(A)
    noise_n: np.ndarray = field(default_factory=lambda: np.zeros(NOISE_BINS, int))     # seconds per bin

    def to_json(self) -> dict:
        return {k: (v.round(4).tolist() if isinstance(v, np.ndarray) else v) for k, v in self.__dict__.items()}

    def metres(self, frac) -> np.ndarray:
        """Map fraction -> metres from the departure station (dead-reckoned, scaled to length_m)."""
        return np.interp(frac, R.GRID, self.dist_m)

    def speed(self, frac: float, ratio: float) -> float:
        """m/s at a map fraction for a speed ratio (map speed profile x ratio)."""
        j = min(int(round(frac * (len(self.tau) - 1))), len(self.tau) - 2)
        return ratio * (self.dist_m[j + 1] - self.dist_m[j]) / self.tau[j]

    @classmethod
    def from_json(cls, d: dict) -> "Template":
        return cls(**{k: (np.array(v) if isinstance(v, list) and k not in ("runs",) else v) for k, v in d.items()})


@dataclass
class SectionRun:
    """One run over one section, with departure redefined by the causal detector."""
    key: str
    run: Run
    sec: dict                  # sections.json entry with depart_t/depart_wall replaced
    feat: pd.DataFrame         # stream_features of the whole run
    yaw0: pd.DataFrame         # zero-phase features.yaw_rate (for the offline alignment only)
    depart_shift_s: float      # causal onset minus the offline depart (s); NaN if not detected
    departure: dict | None = None  # the armed detector's event, also when it is not this departure
    gaps: list = field(default_factory=list)  # phyphox pauses [(t, missing_s)]
    clean: bool = True         # repeatability.json "clean" (no pause, handling, truncation)
    truncated: bool = False    # an end of the section was not observed (cannot be scored)


MATCH_S = 15.0  # an armed detection within this of the offline departure counts as that departure


def section_run(key: str, run: Run, sec: dict, bias: np.ndarray, feat: pd.DataFrame | None = None,
                arm_wall: float | None = None) -> SectionRun:
    """arm_wall: when the departure detector is armed (the previous arrival, or the recording start)."""
    from .stops import pauses

    feat = stream_features(run, bias) if feat is None else feat
    ev = departure_after(feat, feat.wall.iloc[0] if arm_wall is None else arm_wall)
    s = dict(sec)
    shift = np.nan
    if ev is not None and abs(ev["wall_onset"] - sec["depart_wall"]) <= MATCH_S:
        shift = ev["wall_onset"] - sec["depart_wall"]
        s["depart_wall"] = ev["wall_onset"]
        s["depart_t"] = float(np.interp(ev["wall_onset"], feat.wall, feat.t.ffill().bfill()))
    return SectionRun(key, run, s, feat, F.yaw_rate(run, bias), float(shift), ev,
                      [(p["t"], p["dur"]) for p in pauses(run)])


def grid_yaw(sr: SectionRun, causal: bool) -> np.ndarray:
    """Yaw (deg/s) on the run's own L0 grid; causal stream yaw or zero-phase yaw."""
    if causal:
        f = sr.feat
        return np.interp(R.GRID, (f.wall - sr.sec["depart_wall"]) / (sr.sec["arrive_wall"] - sr.sec["depart_wall"]),
                         np.where(f.valid, f.yaw, np.nan))
    return R.sample(sr.yaw0.t.values, np.degrees(sr.yaw0.yaw.values), R.time_grid(sr.run, sr.sec))


def warp_onto(a: SectionRun, b: SectionRun, slack: float = 0.035, pen: float = 0.1) -> np.ndarray:
    """Offline DTW (zero-phase yaw, open ends) of a's L0 grid onto b's: b fraction per a index.

    The step penalty stops plain DTW's hold-then-jump on long constant stretches (straights, one
    steady curve), where yaw carries no position information (the ablation's penalty sweep).
    """
    A, B = P._z(grid_yaw(a, False))[None], P._z(grid_yaw(b, False))[None]
    return P.dtw_warp(np.nan_to_num(A), np.isfinite(A) * 1.0, np.nan_to_num(B), np.isfinite(B) * 1.0,
                      step_penalty=pen, slack=slack)


def _on_base(w: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Resample a run's per-grid values x onto base fractions, given its warp w (base frac per run index)."""
    ws = np.maximum.accumulate(w) + np.arange(len(w)) * 1e-9
    return np.interp(R.GRID, ws, x, left=np.nan, right=np.nan)


def build_template(track: str, srs: list[SectionRun], length_m: float, noise: dict | None = None) -> Template:
    """Map from clean section runs. noise: {key: (t_sensor seconds, LAeq_1s)} per run, optional."""
    base = sorted(srs, key=lambda s: (np.isnan(s.depart_shift_s), not s.sec.get("arrive_observed", True),
                                      -s.sec["confidence"], s.key))[0]
    ys, taus, ds, nb_db = [], [], [], [[] for _ in range(NOISE_BINS)]
    for s in srs:
        w = R.GRID.copy() if s is base else warp_onto(s, base)
        ys.append(_on_base(w, grid_yaw(s, True)))
        dur = s.sec["arrive_wall"] - s.sec["depart_wall"]
        tb = _on_base(w, R.GRID * dur)                     # run time at each base fraction
        tau = np.gradient(tb)
        taus.append(np.where(tau > 0, tau, np.nan))
        # dead-reckoned distance fraction (v = 0 at both ends; features.dead_reckon) on the base grid
        dr = F.dead_reckon(s.run, s.sec["depart_t"], s.sec["arrive_t"], s.gaps)
        ds.append(_on_base(w, R.sample(dr.t.values, dr.frac.values, R.time_grid(s.run, s.sec))))
        if noise and s.key in noise:
            ts, db = noise[s.key]
            wall = np.interp(ts, s.feat.t.dropna(), s.feat.wall[s.feat.t.notna()])
            f = (wall - s.sec["depart_wall"]) / dur
            ok = (f >= 0) & (f <= 1) & np.isfinite(db)
            fb = np.interp(f[ok], R.GRID, np.maximum.accumulate(w))
            for b_, d in zip(np.minimum((fb * NOISE_BINS).astype(int), NOISE_BINS - 1), db[ok]):
                nb_db[b_].append(d)
    Y = np.vstack(ys)
    T = np.vstack(taus)
    mu = np.nanmean(Y, 0)
    sd = np.nanstd(Y, 0) if len(ys) > 1 else np.zeros(R.N)
    tau = uniform_filter1d(np.nan_to_num(np.nanmean(T, 0), nan=np.nanmean(T)), 9, mode="nearest")
    ndb = np.array([10 * np.log10(np.mean(10 ** (np.array(v) / 10))) if v else np.nan for v in nb_db])
    d = np.nanmean(np.vstack(ds), 0)
    ok = np.isfinite(d)
    d = np.maximum.accumulate(np.interp(R.GRID, R.GRID[ok], d[ok]))
    d = (d - d[0]) / (d[-1] - d[0])
    return Template(track, float(length_m), base.key, [s.key for s in srs], np.nan_to_num(mu),
                    uniform_filter1d(np.nan_to_num(sd), 5), tau, float(tau.sum()), d * length_m,
                    ndb, np.array([len(v) for v in nb_db]))


# --------------------------------------------------------------------------- tracker
class Tracker:
    """Causal HMM over (speed level, map bin). Call step() once per STEP with that step's features."""

    def __init__(self, tpl: Template, start: str = "depart"):
        self.tpl = tpl
        self.N, self.K = len(tpl.yaw_mu), len(SPEEDS)
        self.adv = SPEEDS[:, None] * STEP / tpl.tau[None, :]      # (K, N) bins per step
        self.sd = np.sqrt(tpl.yaw_sd ** 2 + YAW_SD_FLOOR ** 2)
        # from a departure the last bin is the arrival and absorbs; from an unknown start (train known
        # to be moving) a hypothesis that would overrun the station is dropped instead of piling up there
        self.absorb = start == "depart"
        p = np.zeros((self.K, self.N))
        if start == "depart":
            p[:, 0] = 1.0
        else:                                                      # clip from an unknown position
            p[:] = 1.0
        self.p = p / p.sum()

    def _predict(self):
        K, N = self.K, self.N
        x = np.arange(N)[None, :] + self.adv
        p = self.p if self.absorb else self.p * (x <= N - 1)
        lo = np.minimum(np.floor(x).astype(int), N - 1)
        fr = np.where(lo >= N - 1, 0.0, x - lo)
        hi = np.minimum(lo + 1, N - 1)
        rows = np.arange(K)[:, None] * N
        q = np.bincount((rows + lo).ravel(), (p * (1 - fr)).ravel(), K * N)
        q += np.bincount((rows + hi).ravel(), (p * fr).ravel(), K * N)
        q = q.reshape(K, N)
        mix = (1 - 2 * P_SPEED) * q
        mix[1:] += P_SPEED * q[:-1]
        mix[:-1] += P_SPEED * q[1:]
        mix[0] += P_SPEED * q[0]
        mix[-1] += P_SPEED * q[-1]
        self.p = mix

    def step(self, yaw: float, valid: bool = True, handled: bool = False) -> dict:
        self._predict()
        if valid and not handled and np.isfinite(yaw):
            z = (yaw - self.tpl.yaw_mu) / self.sd
            lik = (1 - OUTLIER) * np.exp(-0.5 * z * z) / self.sd + OUTLIER / 10.0
            self.p *= (lik ** GAMMA)[None, :]
        self.p /= self.p.sum()
        return self.estimate()

    def estimate(self, lock_m: float = 50.0) -> dict:
        """Posterior mean ("frac"), MAP, 5/95 % quantiles (map fractions), speed ratio, and "lock" =
        posterior mass within +-lock_m of the MAP. The MAP is the headline: the mean trails the train
        at curve edges, where hypotheses already past the edge are cut off before it is seen."""
        pos = self.p.sum(0)
        c = np.cumsum(pos)
        f = R.GRID
        spd = self.p.sum(1)
        j = int(np.argmax(pos))
        h = max(1, int(round(lock_m / self.tpl.length_m * (self.N - 1))))
        return {"frac": float((pos * f).sum()), "map": float(f[j]), "lock": float(pos[max(0, j - h): j + h + 1].sum()),
                "q05": float(np.interp(0.05, c, f)), "q95": float(np.interp(0.95, c, f)),
                "speed_ratio": float((spd * SPEEDS).sum()), "posterior": pos}


def track(tpl: Template, feat: pd.DataFrame, wall0: float, wall1: float, start: str = "depart") -> pd.DataFrame:
    """Run a Tracker over feat steps in [wall0, wall1]. Returns one row per step (no posterior)."""
    tr = Tracker(tpl, start)
    sub = feat[(feat.wall >= wall0) & (feat.wall <= wall1)]
    rows = []
    for r in sub.itertuples():
        e = tr.step(r.yaw, bool(r.valid), r.gyr > HANDLED)
        e.pop("posterior")
        rows.append({"wall": r.wall, "yaw": r.yaw, **e})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- loading
EXCLUDE_RUNS = {"R0"}  # Victoria-line noise reference, not a repeat ride


def track_name(d: str, a: str, b: str) -> str:
    return f"{d} {a}->{b}"


def section_runs(clean_only: bool = True) -> dict[str, list[SectionRun]]:
    """SectionRuns per track from outputs/sections.json, for every section ridden at least twice (as the
    ablation); clean flags from outputs/gonogo/repeatability.json."""
    import json

    from .ingest import ROOT, load_run

    secs = json.load(open(ROOT / "outputs" / "sections.json"))
    meta = json.load(open(ROOT / "outputs" / "gonogo" / "repeatability.json"))["sections"]
    use = [x for x in secs["sections"] if x["run_id"] not in EXCLUDE_RUNS]
    n = {}
    for x in use:
        n[(x["direction"], x["from"], x["to"])] = n.get((x["direction"], x["from"], x["to"]), 0) + 1
    out, cache = {}, {}
    for sec in use:
        trk = (sec["direction"], sec["from"], sec["to"])
        key = f"{sec['run_id']}:{sec['from']}-{sec['to']}"
        if n[trk] < 2 or key not in meta or (clean_only and not meta[key]["clean"]):
            continue
        stem = sec["stem"]
        if stem not in cache:
            run = load_run(stem)
            dw = next(r for r in secs["runs"] if r["stem"] == stem)["dwells"]
            bias = F.gyro_bias(run, [(d["t0"], d["t1"]) for d in dw])
            cache[stem] = (run, bias, stream_features(run, bias))
        run, bias, feat = cache[stem]
        # armed at the previous arrival in this recording (stand-in for the tracker's own arrival), else at its start
        prev = [x["arrive_wall"] for x in secs["sections"] if x["stem"] == stem and x["arrive_wall"] <= sec["depart_wall"]]
        sr = section_run(key, run, sec, bias, feat, max(prev) if prev else None)
        sr.clean = bool(meta[key]["clean"])
        sr.truncated = any(f["type"] == "section_truncated" for f in meta[key]["flags"])
        out.setdefault(track_name(*trk), []).append(sr)
    return out


def noise_series(sr: SectionRun) -> tuple[np.ndarray, np.ndarray] | None:
    """(sensor experiment time of each 1-s audio bin centre, LAeq_1s) via the sync mapping; None if unavailable."""
    from . import noise, sync

    try:
        lv = noise.levels(sr.run)
        t = sync.audio_to_sensor(sr.run, lv.t_audio.values + 0.5)
    except (KeyError, FileNotFoundError):
        return None
    ok = np.isfinite(t)
    return t[ok], lv.LAeq_1s.values[ok]
