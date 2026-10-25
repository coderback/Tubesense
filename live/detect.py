"""Causal per-second detectors on streaming phyphox samples, reusing the frozen pipeline's logic and constants.

What: LiveDetector.feed(key, samples) takes incremental (t, x[, y, z]) arrays for acc / gyr / mag / baro (phyphox
experiment time, s) and step() returns events: one 'sample' row per completed second plus 'knock', 'state',
'placement_change' and 'warning' events. Only past samples are used (plus a 0.2 s look-ahead for knock peaks).
Why: a live input for the demo that shows the same quantities the offline pipeline uses. It does NOT position the
train; it reports what the phone senses now.

Sources (imported or copied constants, cited; where the frozen code is offline / non-causal the adaptation is noted):
- up / yaw: tubesense.online._attitude (gyro-propagated up pulled to a/|a|, TAU_ATT 30 s), called chunk by chunk
  with the previous up prepended so the filter state carries over; yaw = gyro . up in deg/s, causal 2nd-order
  low-pass 1 Hz (online.stream_features). + = anticlockwise seen from above.
- acc_h_rms: online.stream_features 'h' (|horizontal specific force|, |g| low-passed at 1/60 Hz, 0.3 Hz low-pass),
  RMS over the second.
- vib_rms, body, gyr_body, handled, moving score, stop/move: tubesense.stops.motion_features / moving_score /
  stationary_intervals. Offline they use zero-phase filters and per-run percentiles; here the band filters are
  causal (same bands/orders), RMS windows trail (2 s vib, 3 s body), the log-normalisation uses the percentiles
  (3rd / 70th, span >= 0.3 decade) of the seconds seen so far, widened to the measured cross-ride PRIOR_P3/P70, the 5-hop median becomes a 3 s trailing median,
  stops need >= 3 s (min_len 3.0) and end after > 4.5 s of motion (bridge 4.5) that reaches score 0.75 (the
  'brief weak stir' merge) or after 10 s. Threshold 0.45, handled = 1 s mean |gyro| > 0.25 rad/s, +2 s after.
  The barometer veto clip((dp - 0.15) / 0.2) and the handled fallback clip(dp / 0.12) are unchanged; dp/dt is the
  |slope| of a line fit to the last 7 s of pressure (offline: Savitzky-Golay 7 s), max over 3 s. No walking test.
- knock: tubesense.sync.accel_spikes (|8 Hz high-pass acc|, peaks > max(1.5, 6 x running median), >= 8 samples
  apart), causal high-pass, median over the last ~3 s; a spike is a knock only above KNOCK_MIN (10 m/s^2, measured
  on R5-R15, see the constant), smaller spikes are reported as spike_peak_ms2. Peaks closer than sync.triples' gmin (0.12 s, the knock's
  own ringing) are one knock; knocks with gaps in [gmin, gmax] = [0.12, 1.0] s form a pattern: 2 = 'double'
  (the 24 Oct sync protocol), 3 = 'triple' (the 23 Oct triple tap), 1 = 'single', more = 'burst'.
- placement_change: tubesense.qc.placement_epochs: gravity = 0.3 Hz low-pass of acc (here causal); an epoch is a
  >= 10 s un-handled block; a re-placement is a change of the settled gravity direction > 10 deg.
- door step: tubesense.stops._door_step (mean p over the last 2 s minus the first 2 s of a dwell >= 6 s).
- gaps: tubesense.qc.timing_flags (dt > max(5 x median dt, 0.1 s); barometer 3 s).
"""
from __future__ import annotations

from collections import deque

import numpy as np
from scipy.signal import butter, find_peaks, sosfilt, sosfilt_zi

from tubesense.online import TAU_ATT, _attitude

# --- constants taken from the frozen modules (see docstring)
THR_STOP = 0.45          # stops.stationary_intervals thr
MIN_STOP_S = 3           # stops.stationary_intervals min_len
BRIDGE_S = 4.5           # stops.stationary_intervals bridge
STIR_MAX = 0.75          # stops.stationary_intervals merge: a stir that never reaches 0.75 is not a departure
MERGE_S = 10             # stops.stationary_intervals merge window
GYR_HANDLED = 0.25       # stops.motion_features handled: 1 s mean |gyro| > 0.25 rad/s
HANDLED_PAD_S = 2        # stops.motion_features: +-2 s around handling (causal: after only)
DP_VETO, DP_SPAN, DP_HANDLED = 0.15, 0.2, 0.12   # stops.moving_score
SPIKE_K, SPIKE_FLOOR, SPIKE_DIST = 6.0, 1.5, 8   # sync.accel_spikes
GMIN, GMAX = 0.12, 1.0   # sync.triples
KNOCK_MIN = 10.0         # m/s^2, NEW (measured, not frozen): 24 Oct ride-edge knocks peak 29.7-45.9 m/s^2; un-handled
                         # in-ride spikes peak <= 8.9 on 9/11 rides R5-R15 (R7 40.9, R8 18.3: phone handled)
REORIENT_DEG = 10.0      # qc.classify / placement_epochs: brief handling that re-orients < 10 deg = same placement
EPOCH_S = 10             # qc.placement_epochs: blocks >= 10 s
DOOR_MIN_S = 6           # stops._door_step
LOOKAHEAD = 0.2          # s of later samples needed before a second is summarised (knock peaks)
KEEP_S = 12.0            # rolling sample history
BARO_LAG_S = 5           # iOS barometer samples arrive ~3.5 s late; look back this far for the latest reading
# Normalisation priors (measured, not frozen): median over R3-R15 (13 Central rides, phone on seat/floor) of each ride's
# 3rd / 70th percentile of log10 stops.motion_features body / vib / gyr_body (un-handled hops); per-ride sd
# 0.24 / 0.34 / 0.10 (p3) and 0.18 / 0.26 / 0.11 (p70) decade. The offline code normalises on the whole ride, which
# always contains dwells and running; a live history may hold only one of them (a capture that starts mid-ride, or
# on a platform), so the span is widened to at least the typical ride's.
PRIOR_P3 = {"body": -2.14, "vib": -1.44, "gyr_body": -3.07}
PRIOR_P70 = {"body": -0.61, "vib": -0.30, "gyr_body": -1.95}


class _Sos:
    """Stateful causal SOS filter, started in steady state at the first sample (as online.causal_lowpass)."""

    def __init__(self, sos: np.ndarray):
        self.sos, self.zi = sos, None

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if self.zi is None:
            z = sosfilt_zi(self.sos)
            self.zi = z[(slice(None), slice(None)) + (None,) * (x.ndim - 1)] * x[0]
        y, self.zi = sosfilt(self.sos, x, axis=0, zi=self.zi)
        return y


class _Roll:
    """Rolling columns (t + named arrays) trimmed to the last KEEP_S seconds."""

    def __init__(self):
        self.cols: dict[str, np.ndarray] = {}

    def add(self, **cols):
        for k, v in cols.items():
            v = np.asarray(v, float)
            self.cols[k] = v if k not in self.cols else np.concatenate([self.cols[k], v])

    def trim(self, t_min: float):
        if "t" in self.cols and len(self.cols["t"]):
            i = int(np.searchsorted(self.cols["t"], t_min))
            if i:
                self.cols = {k: v[i:] for k, v in self.cols.items()}

    def win(self, t0: float, t1: float) -> np.ndarray:
        t = self.cols.get("t", np.empty(0))
        return (t >= t0) & (t < t1)

    def __getitem__(self, k):
        return self.cols.get(k, np.empty(0))


def _lognorm(x: float, hist: deque, key: str) -> float:
    """stops._lognorm against the seconds seen so far, widened to at least the cross-run prior span."""
    ref = np.log10(np.asarray(hist if len(hist) else [x]) + 1e-6)
    lo, hi = min(np.percentile(ref, 3), PRIOR_P3[key]), max(np.percentile(ref, 70), PRIOR_P70[key])
    return float((np.log10(x + 1e-6) - lo) / max(hi - lo, 0.3))


class LiveDetector:
    def __init__(self):
        self.raw = {k: _Roll() for k in ("acc", "gyr", "mag", "baro")}  # raw samples (rolling)
        self.f = _Roll()                    # filtered acc-timeline features
        self.fs: float | None = None
        self.up_last: np.ndarray | None = None
        self.filt: dict[str, _Sos] = {}
        self.next_sec: int | None = None
        self.t_filtered = -np.inf
        self.last_t = {k: None for k in self.raw}
        self.med_dt = {k: None for k in self.raw}
        self.hist = {k: deque(maxlen=3600) for k in ("body", "vib", "gyr_body")}
        self.scores: deque = deque(maxlen=3)
        self.handled_until = -np.inf
        self.dp_hist: deque = deque(maxlen=3)
        self.state, self.cand_run, self.non_run, self.non_max = "unknown", 0, 0, 0.0
        self.quiet_in_run = 0
        self.stop_start: float | None = None
        self.knocks: list[tuple[float, float, bool]] = []   # (t, peak, handled) of knocks not yet grouped
        self.last_spike_t = -np.inf
        self.grav_sec: deque = deque(maxlen=EPOCH_S)  # per-second unit gravity of the current un-handled block
        self.placement_g: np.ndarray | None = None
        self.block_len = 0
        self.events: list[dict] = []
        self.first_t = {k: None for k in self.raw}
        self.gap_secs: set[int] = set()
        self.p_sec: deque = deque(maxlen=900)   # (second, mean hPa) for the door step
        self.p_prev: float | None = None

    # ------------------------------------------------------------------ input
    def feed(self, key: str, a: np.ndarray):
        """a: (n, 1 + k) array of t, values (phyphox experiment time). Out-of-order rows are dropped."""
        if a is None or not len(a) or key not in self.raw:
            return
        a = a[np.argsort(a[:, 0], kind="stable")]
        lt = self.last_t[key]
        if lt is not None:
            a = a[a[:, 0] > lt]
            if not len(a):
                return
        t = a[:, 0]
        tt = t if lt is None else np.r_[lt, t]
        if len(tt) > 1:
            dt = np.diff(tt)
            if self.med_dt[key] is None and len(dt) >= 20:
                self.med_dt[key] = float(np.median(dt))
            md = self.med_dt[key]
            if md:
                lim = 3.0 if key == "baro" else max(5 * md, 0.1)
                for j in np.flatnonzero(dt > lim):
                    self.gap_secs.update(range(int(np.floor(tt[j])), int(np.floor(tt[j + 1])) + 1))
                    self.events.append({"type": "warning", "kind": "sample_gap", "sensor": key,
                                        "t0": round(float(tt[j]), 3), "t1": round(float(tt[j + 1]), 3),
                                        "message": f"{key} gap {dt[j] * 1000:.0f} ms vs median {md * 1000:.1f} ms"})
        self.last_t[key] = float(t[-1])
        if self.first_t[key] is None:
            self.first_t[key] = float(t[0])
        names = ["p"] if key == "baro" else ["x", "y", "z"]
        self.raw[key].add(t=t, **{n: a[:, 1 + i] for i, n in enumerate(names[: a.shape[1] - 1])})

    # ------------------------------------------------------------------ filters on the acc timeline
    def _design(self, fs: float):
        self.fs = fs
        ny = fs / 2
        hi40 = min(40.0, 0.45 * fs)
        self.filt = {
            "hp8": _Sos(butter(2, 8 / ny, "high", output="sos")),                       # sync.imu_band
            "vib": _Sos(butter(3, [3 / ny, hi40 / ny], "band", output="sos")),          # stops vib 3-40 Hz
            "body": _Sos(butter(3, [0.5 / ny, 3 / ny], "band", output="sos")),          # stops body 0.5-3 Hz
            "gbody": _Sos(butter(3, [0.3 / ny, 3 / ny], "band", output="sos")),         # stops gyr_body 0.3-3 Hz
            "yaw": _Sos(butter(2, 1.0 / ny, output="sos")),                             # online yaw 1 Hz
            "g0": _Sos(butter(2, (1 / 60) / ny, output="sos")),                         # online |g| 1/60 Hz
            "h": _Sos(butter(2, 0.3 / ny, output="sos")),                               # online h 0.3 Hz
            "grav": _Sos(butter(2, 0.3 / ny, output="sos")),                            # qc._gravity 0.3 Hz
        }

    def _advance(self):
        acc, gyr = self.raw["acc"], self.raw["gyr"]
        if not len(acc["t"]) or not len(gyr["t"]):
            return
        if self.fs is None:
            if len(acc["t"]) < 50:
                return
            self._design(1.0 / float(np.median(np.diff(acc["t"]))))
        m = (acc["t"] > self.t_filtered) & (acc["t"] <= gyr["t"][-1])
        if not m.any():
            return
        t = acc["t"][m]
        A = np.column_stack([acc["x"][m], acc["y"][m], acc["z"][m]])
        G = np.column_stack([np.interp(t, gyr["t"], gyr[c]) for c in "xyz"])
        # online._attitude with the state carried over: prepend the previous up (as a specific force, zero rotation)
        if self.up_last is None:
            up = _attitude(A, G, 1.0 / self.fs, TAU_ATT)
        else:
            up = _attitude(np.vstack([self.up_last * np.linalg.norm(A[0]), A]), np.vstack([np.zeros(3), G]),
                           1.0 / self.fs, TAU_ATT)[1:]
        self.up_last = up[-1].copy()
        F = self.filt
        yaw = F["yaw"](np.degrees(np.einsum("ij,ij->i", G, up)))
        g0 = F["g0"](np.linalg.norm(A, axis=1))
        lin = A - up * g0[:, None]
        h = np.linalg.norm(F["h"](lin - np.einsum("ij,ij->i", lin, up)[:, None] * up), axis=1)
        grav = F["grav"](A)
        self.f.add(t=t, hp=np.linalg.norm(F["hp8"](A), axis=1), vib2=(F["vib"](A) ** 2).sum(1),
                   body2=(F["body"](A) ** 2).sum(1), gbody2=(F["gbody"](G) ** 2).sum(1), yaw=yaw, h=h,
                   gn=np.linalg.norm(G, axis=1), gx=grav[:, 0], gy=grav[:, 1], gz=grav[:, 2])
        self.t_filtered = float(t[-1])

    # ------------------------------------------------------------------ per second
    def step(self) -> list[dict]:
        self._advance()
        if self.next_sec is None and len(self.f["t"]):
            self.next_sec = int(np.floor(self.f["t"][0])) + 1
        while self.next_sec is not None and self.t_filtered >= self.next_sec + 1 + LOOKAHEAD:
            self._second(self.next_sec)
            self.next_sec += 1
            for r in list(self.raw.values()) + [self.f]:
                r.trim(self.next_sec - KEEP_S)
        ev, self.events = self.events, []
        return ev

    def _second(self, k: int):
        f, raw = self.f, self.raw
        m = f.win(k, k + 1)
        rates = {}
        for key, rr in raw.items():
            if self.first_t[key] is None:
                continue
            w = min(10.0, k + 1 - self.first_t[key])
            rates[key] = round(float(rr.win(k + 1 - w, k + 1).sum() / w), 1) if w > 0 else None
        row = {"type": "sample", "t": k, "state": self.state, "rates_hz": rates, "gap": k in self.gap_secs}
        self.gap_secs.discard(k)
        if not m.any():
            row.update(handled=None, knock=False, placement_change=False)
            self.events.append(row)
            return

        def trail(col: str, w: float) -> float:
            mm = f.win(k + 1 - w, k + 1)
            return float(np.sqrt(f[col][mm].mean()))

        vib, body, gbody = trail("vib2", 2.0), trail("body2", 3.0), trail("gbody2", 3.0)
        if f["gn"][m].mean() > GYR_HANDLED:
            self.handled_until = k + 1 + HANDLED_PAD_S
        handled = k < self.handled_until

        # ---- barometer: mean hPa, 1 s change, |dp/dt| (line fit over 7 s, max over 3 s)
        p_now = dp = d_p = slope = None
        rb = raw["baro"]
        if len(rb["t"]):
            pm = rb.win(k, k + 1)
            if not pm.any():
                # iOS delivers barometer samples ~3.5 s behind the motion sensors (measured live, 25 Oct): when this
                # second has none yet, use the latest reading of the last BARO_LAG_S seconds
                lm = np.flatnonzero(rb.win(k + 1 - BARO_LAG_S, k + 1))
                pm = np.zeros(len(rb["t"]), bool)
                if len(lm):
                    pm[lm[-1]] = True
            if pm.any():
                p_now = float(rb["p"][pm].mean())
                self.p_sec.append((k, p_now))
                d_p = None if self.p_prev is None else p_now - self.p_prev
                self.p_prev = p_now
            mm = rb.win(k + 1 - 7 - BARO_LAG_S, k + 1)
            if mm.sum() >= 4:
                slope = float(np.polyfit(rb["t"][mm], rb["p"][mm], 1)[0])
                self.dp_hist.append(abs(slope))
            dp = max(self.dp_hist) if self.dp_hist else None

        # ---- moving score (stops.moving_score, causal)
        if not handled:
            for name, v in (("body", body), ("vib", vib), ("gyr_body", gbody)):
                self.hist[name].append(v)
            sc = (2 * _lognorm(body, self.hist["body"], "body") + _lognorm(vib, self.hist["vib"], "vib")
                  + _lognorm(gbody, self.hist["gyr_body"], "gyr_body")) / 4
            if dp is not None:
                sc = max(sc, float(np.clip((dp - DP_VETO) / DP_SPAN, 0, 1.5)))
        else:
            sc = float(np.clip(dp / DP_HANDLED, 0, 1.5)) if dp is not None else np.nan
        self.scores.append(sc)
        fin = [x for x in self.scores if np.isfinite(x)]
        score = float(np.median(fin)) if fin else np.nan

        # ---- stop / move state machine (stops.stationary_intervals, causal)
        quiet = (not handled) and np.isfinite(score) and score < THR_STOP
        cand = quiet or (handled and dp is not None and dp < DP_VETO)
        evidence = (not handled) or dp is not None   # a handled second with no barometer says nothing
        if cand:
            self.cand_run += 1
            self.quiet_in_run += int(quiet)
            self.non_run, self.non_max = 0, 0.0
        elif evidence:
            self.non_run += 1
            self.non_max = max(self.non_max, score if np.isfinite(score) else 0.0)
            self.cand_run = self.quiet_in_run = 0
        if self.state != "stopped" and self.cand_run >= MIN_STOP_S and self.quiet_in_run >= 2:
            t0 = k + 1 - self.cand_run
            self.events.append({"type": "state", "t": t0, "state": "stopped", "from": self.state,
                                "detected_t": k + 1})
            self.state, self.stop_start = "stopped", t0
        elif self.state != "moving" and ((self.non_run > BRIDGE_S and self.non_max >= STIR_MAX)
                                         or self.non_run >= MERGE_S):
            t1 = k + 1 - self.non_run
            ev = {"type": "state", "t": t1, "state": "moving", "from": self.state, "detected_t": k + 1}
            if self.stop_start is not None:
                ev["dwell_s"] = t1 - self.stop_start
                ev["door_step_hpa"] = self._door_step(self.stop_start, t1)
            self.events.append(ev)
            self.state, self.stop_start = "moving", None

        # ---- knocks (sync.accel_spikes, causal)
        wk = f.win(k - 0.3, k + 1 + LOOKAHEAD)
        hp, tw = f["hp"][wk], f["t"][wk]
        base = float(np.median(f["hp"][f.win(k - 2, k + 1 + LOOKAHEAD)]))
        pk, _ = find_peaks(hp, height=max(SPIKE_FLOOR, SPIKE_K * base), distance=SPIKE_DIST)
        knock_peak = spike_peak = 0.0
        for j in pk:
            tp, hv = float(tw[j]), float(hp[j])
            if not (k <= tp < k + 1):
                continue
            spike_peak = max(spike_peak, hv)
            if hv < KNOCK_MIN and not (self.knocks and tp - self.last_spike_t < GMIN):
                continue
            knock_peak = max(knock_peak, hv)
            if self.knocks and tp - self.last_spike_t < GMIN:   # the knock's own ringing
                self.knocks[-1] = (self.knocks[-1][0], max(self.knocks[-1][1], hv), self.knocks[-1][2])
            else:
                self.knocks.append((tp, hv, bool(handled)))
            self.last_spike_t = tp
        self._group_knocks(now=k + 1)

        # ---- placement (qc.placement_epochs, causal)
        g = np.array([f["gx"][m].mean(), f["gy"][m].mean(), f["gz"][m].mean()])
        g = g / (np.linalg.norm(g) + 1e-9)
        tilt = float(np.degrees(np.arccos(np.clip(abs(g[2]), 0, 1))))
        placed = False
        if handled:
            self.block_len = 0
            self.grav_sec.clear()
        else:
            self.block_len += 1
            self.grav_sec.append(g)
            if self.block_len == EPOCH_S:
                gs = np.median(np.array(self.grav_sec), axis=0)
                gs = gs / np.linalg.norm(gs)
                ang = None if self.placement_g is None else float(
                    np.degrees(np.arccos(np.clip(gs @ self.placement_g, -1, 1))))
                if ang is None or ang > REORIENT_DEG:
                    placed = True
                    self.events.append({"type": "placement_change", "t": k + 1 - EPOCH_S, "detected_t": k + 1,
                                        "initial": ang is None, "reorient_deg": None if ang is None else round(ang, 1),
                                        "screen": "up" if gs[2] > 0 else "down",
                                        "tilt_deg": round(float(np.degrees(np.arccos(np.clip(abs(gs[2]), 0, 1)))), 1)})
                    self.placement_g = gs

        rm = raw["mag"]
        mr = rm.win(k, k + 1)
        b_abs = (float(np.linalg.norm(np.column_stack([rm[c][mr] for c in "xyz"]), axis=1).mean())
                 if len(rm["t"]) and mr.any() else None)

        def r(x, n=3):
            return None if x is None or not np.isfinite(x) else round(float(x), n)

        row.update({
            "state": self.state, "yaw_rate_dps": r(f["yaw"][m].mean()), "vib_rms": r(vib, 4),
            "acc_h_rms": r(float(np.sqrt((f["h"][m] ** 2).mean())), 4), "body_rms": r(body, 4),
            "gyr_body_rms": r(gbody, 4), "moving_score": r(score), "handled": bool(handled),
            "knock": knock_peak > 0, "knock_peak_ms2": r(knock_peak, 2) if knock_peak else None,
            "spike_peak_ms2": r(spike_peak, 2) if spike_peak else None,
            "placement_change": placed, "tilt_deg": r(tilt, 1), "screen": "up" if g[2] > 0 else "down",
            "pressure_hpa": r(p_now, 3), "d_pressure_hpa": r(d_p, 3), "dpdt_hpa_s": r(slope, 4),
            "b_abs_ut": r(b_abs, 2),
        })
        self.events.append(row)

    def flush(self) -> list[dict]:
        """End of stream: emit pending knock groups."""
        self._group_knocks(now=np.inf)
        ev, self.events = self.events, []
        return ev

    def _group_knocks(self, now: float):
        """Emit knock groups whose last member is > GMAX old (nothing more can join them)."""
        while self.knocks:
            grp = [self.knocks[0]]
            for kn in self.knocks[1:]:
                if kn[0] - grp[-1][0] <= GMAX:
                    grp.append(kn)
                else:
                    break
            if now - grp[-1][0] <= GMAX:
                return
            n = len(grp)
            self.events.append({"type": "knock", "t": round(grp[0][0], 3), "n": n,
                                "pattern": {1: "single", 2: "double", 3: "triple"}.get(n, "burst"),
                                "times": [round(x[0], 3) for x in grp],
                                "gaps_s": [round(b[0] - a[0], 3) for a, b in zip(grp, grp[1:])],
                                "peak_ms2": round(max(x[1] for x in grp), 1), "sync_marker": n == 2})
            self.knocks = self.knocks[n:]

    def _door_step(self, t0: float, t1: float) -> float | None:
        """stops._door_step on per-second means: mean p over the last 2 s minus the first 2 s of the dwell."""
        if t1 - t0 < DOOR_MIN_S:
            return None
        a = [p for s, p in self.p_sec if t0 <= s < t0 + 2]
        b = [p for s, p in self.p_sec if t1 - 2 <= s < t1]
        return round(float(np.mean(b) - np.mean(a)), 3) if a and b else None
