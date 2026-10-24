"""Post-hoc tracker v2: the frozen v1 grid HMM (tubesense.online.Tracker) plus a causal STOP GATE.

Why: v1's speed levels (online.SPEEDS, 0.6-1.6 x the map's speed profile) have no zero, so when the
train stands still in a tunnel the posterior keeps advancing. A synthetic 30 s stop leaves v1
342-450 m ahead on LST->BTH and it does not always recover (docs/ARCHITECTURE.md, decision 3).

Stop gate (all causal: each step uses data up to that step only):
- Cues per STEP (0.25 s): vib = log10 RMS of 3-40 Hz |acc| over the last 2 s (online.stream_features)
  and body = log10 RMS of 0.5-3 Hz acc (car-body bounce/sway, the strongest cue in stops.moving_score)
  over the last 3 s, causal Butterworth.
- Both are placement dependent (hand vs floor differ by ~1 decade), so the gate works on the drop
  below a running "moving" reference: the median of each cue over the last REF_S seconds of
  un-gated, valid, un-handled steps that were not themselves quiet (z >= Z_ON, or no reference yet),
  so the steps of a stop (or a slow approach) that precede engaging do not drag the reference down.
  z = mean of the two drops (log10 units). The gate is off until REF_MIN_S of reference exist (the
  first seconds after departure).
- Engage: z < Z_ON on every un-handled valid step for ENGAGE_S. Release: z > Z_OFF for RELEASE_S,
  or a departure-like jump in h (online.JUMP within online.JUMP_WIN, h > online.JUMP_MIN). The step
  that releases records why in StopGate.released ("loud" / "jump" / "invalid").
  Handled steps (|gyro| > online.HANDLED) hold the state; a phyphox pause (invalid step) releases it,
  i.e. falls back to v1 (no data = assume moving).
- While engaged, P(moving) = PM_STOP, else 1. Prediction: p <- P(moving) predict(p) + (1 - P(moving)) p;
  the yaw likelihood is tempered by GAMMA x P(moving) (a standing train's yaw says nothing about where
  it is). REWIND (optional): on engaging, restore the posterior from the start of the quiet run, so
  the ENGAGE_S confirmation delay does not leave the estimate ahead.
Templates: v1's online.build_template, with metres scaled to the TfL Working Timetable length
(posthoc.lengths) instead of the straight-line chord; scale_template converts an existing map.
Not identical to v1 on a WTT map even with the gate off: Tracker.estimate's "lock" (posterior mass
within +-50 m of the MAP) uses tpl.length_m, so on a WTT map the window is +-50 WTT metres, i.e.
2.9-7 % fewer grid bins than v1's chord map on the Central sections (more on ALE-WCL, factor 0.83).
map / frac / q05 / q95 are unaffected. Re-check the demo's confident-paint rule (PAINT_BAND_M on band
x length) before it uses WTT maps.

Defaults = attempt C of outputs_posthoc/attempts_manual.jsonl (A: z_on -0.6 / z_off -0.4, no rewind, chosen a
priori from the cue distributions; B: + rewind; C: + z_on -0.8 / z_off -0.6 after A and B engaged for 6 s on
R6:BTH-LST's slow (8.5 m/s) approach to Liverpool Street, where vib and body fall as low as at a platform).
Deterministic: no random numbers anywhere. Interface mirrors online.track / online.Tracker.step.
Declared after prereg-24oct; developed on the 23 Oct rides only.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, replace

import numpy as np
import pandas as pd
from scipy.signal import butter, sosfilt

from tubesense import features as F
from tubesense import online as O
from tubesense.ingest import Run

from . import lengths as LN


@dataclass(frozen=True)
class GateParams:
    z_on: float = -0.8        # log10 drop below the moving reference that counts as "quiet" (A/B: -0.6)
    z_off: float = -0.6       # log10 drop above which a step counts as "moving again" (A/B: -0.4)
    engage_s: float = 3.0     # quiet this long (un-handled valid steps) engages the gate
    release_s: float = 1.0    # moving-again this long releases it (or an h jump, immediately)
    pm_stop: float = 0.02     # P(moving) while engaged (a leak, so a false engage cannot stall forever)
    ref_s: float = 60.0       # moving reference window
    ref_min_s: float = 10.0   # gate off until this much reference exists
    rewind: bool = True       # restore the posterior from the start of the quiet run on engaging (A: False)
    cues: tuple = ("vib", "body")

    def to_json(self) -> dict:
        d = asdict(self)
        d["cues"] = list(self.cues)
        return d


BODY_BAND = (0.5, 3.0)  # Hz
BODY_WIN = 3.0          # s, trailing


def _trailing_log_rms(x: np.ndarray, fs: float, band: tuple[float, float], win: float) -> np.ndarray:
    sos = butter(3, list(band), "band", fs=fs, output="sos")
    y = sosfilt(sos, x - x[0], axis=0)
    e = (y ** 2).sum(1) if y.ndim > 1 else y ** 2
    k = max(1, int(win * fs))
    return np.log10(np.sqrt(np.convolve(e, np.ones(k) / k)[: len(e)]) + 1e-6)


def gate_features(run: Run, feat: pd.DataFrame) -> pd.DataFrame:
    """feat (online.stream_features of this run) plus the causal 'body' cue on the same wall grid."""
    acc = run.sensors["acc"]
    t = acc.t.values
    body = _trailing_log_rms(acc[["x", "y", "z"]].values, F._fs(t), BODY_BAND, BODY_WIN)
    wall = run.to_wall(t)
    k = np.clip(np.searchsorted(wall, feat.wall.values, side="right") - 1, 0, len(t) - 1)  # last sample <= step
    out = feat.copy()
    out["body"] = body[k]
    return out


class StopGate:
    """Causal stationarity detector; update() once per STEP, returns P(moving)."""

    def __init__(self, prm: GateParams = GateParams()):
        self.prm = prm
        self.n_engage = int(round(prm.engage_s / O.STEP))
        self.n_release = max(1, int(round(prm.release_s / O.STEP)))
        self.n_ref_min = int(round(prm.ref_min_s / O.STEP))
        self.ref = {c: deque(maxlen=int(round(prm.ref_s / O.STEP))) for c in prm.cues}
        self.h_hist = deque(maxlen=int(O.JUMP_WIN / O.STEP) + 1)
        self.engaged = False
        self.quiet = 0
        self.loud = 0
        self.just_engaged = False
        self.released = ""      # why the gate released on this step ("" = it did not)
        self.z = np.nan

    def _z(self, cues: dict) -> float:
        if len(self.ref[self.prm.cues[0]]) < self.n_ref_min:
            return np.nan
        return float(np.mean([cues[c] - np.median(self.ref[c]) for c in self.prm.cues]))

    def update(self, cues: dict, valid: bool, handled: bool) -> float:
        self.just_engaged, self.released = False, ""
        if not valid or not all(np.isfinite(cues.get(c, np.nan)) for c in self.prm.cues):
            self.released = "invalid" if self.engaged else ""
            self.engaged, self.quiet, self.loud, self.z = False, 0, 0, np.nan
            self.h_hist.clear()
            return 1.0
        h = cues.get("h", np.nan)
        if np.isfinite(h):
            self.h_hist.append(h)
        if handled:                               # hold the state; no evidence either way
            return self.prm.pm_stop if self.engaged else 1.0
        z = self.z = self._z(cues)
        if not self.engaged:
            self.quiet = self.quiet + 1 if z < self.prm.z_on else 0   # NaN (no reference yet) compares False
            if self.quiet >= self.n_engage:
                self.engaged, self.loud, self.just_engaged = True, 0, True
            elif not self.quiet:                              # moving (or no reference yet): extend the reference
                for c in self.prm.cues:
                    self.ref[c].append(cues[c])
        else:
            self.loud = self.loud + 1 if z > self.prm.z_off else 0
            hh = np.asarray(self.h_hist)
            jump = len(hh) > 1 and hh[-1] > O.JUMP_MIN and hh[-1] - hh.min() > O.JUMP
            if self.loud >= self.n_release or jump:
                self.engaged, self.quiet = False, 0
                self.released = "loud" if self.loud >= self.n_release else "jump"
        return self.prm.pm_stop if self.engaged else 1.0


class GatedTracker(O.Tracker):
    """online.Tracker with a StopGate. step() takes the gate cues as a dict (vib, body, h)."""

    def __init__(self, tpl: O.Template, start: str = "depart", prm: GateParams = GateParams()):
        super().__init__(tpl, start)
        self.gate = StopGate(prm)
        self.hist = deque(maxlen=self.gate.n_engage)   # posteriors before each of the last n_engage steps

    def step(self, yaw: float, valid: bool = True, handled: bool = False, cues: dict | None = None) -> dict:
        pm = self.gate.update(cues or {}, valid, handled)
        self.hist.append(self.p.copy())
        if self.gate.just_engaged and self.gate.prm.rewind and len(self.hist) == self.hist.maxlen:
            self.p = self.hist[0].copy()      # posterior before the first quiet step
        else:
            p0 = self.p
            self._predict()
            if pm < 1.0:
                self.p = pm * self.p + (1.0 - pm) * p0
            if valid and not handled and np.isfinite(yaw) and pm > 0:
                z = (yaw - self.tpl.yaw_mu) / self.sd
                lik = (1 - O.OUTLIER) * np.exp(-0.5 * z * z) / self.sd + O.OUTLIER / 10.0
                self.p = self.p * (lik ** (O.GAMMA * pm))[None, :]
        self.p /= self.p.sum()
        e = self.estimate()
        e["p_moving"], e["gate"], e["gate_z"], e["gate_release"] = pm, self.gate.engaged, self.gate.z, self.gate.released
        return e


def track(tpl: O.Template, feat: pd.DataFrame, wall0: float, wall1: float, start: str = "depart",
          prm: GateParams = GateParams()) -> pd.DataFrame:
    """As online.track, with the stop gate. feat must carry gate_features' 'body' column."""
    tr = GatedTracker(tpl, start, prm)
    sub = feat[(feat.wall >= wall0) & (feat.wall <= wall1)]
    rows = []
    for r in sub.itertuples():
        e = tr.step(r.yaw, bool(r.valid), r.gyr > O.HANDLED, {"vib": r.vib, "body": r.body, "h": r.h})
        e.pop("posterior")
        rows.append({"wall": r.wall, "yaw": r.yaw, **e})
    return pd.DataFrame(rows)


def scale_template(tpl: O.Template, length_m: float) -> O.Template:
    """The same map with its metre axis scaled to length_m (identical to building it with length_m)."""
    return replace(tpl, length_m=float(length_m), dist_m=tpl.dist_m * (length_m / tpl.length_m))


def build_template_wtt(track: str, srs: list[O.SectionRun], noise: dict | None = None) -> O.Template:
    """online.build_template with the Working Timetable length (posthoc.lengths)."""
    return O.build_template(track, srs, LN.wtt_m(track), noise)
