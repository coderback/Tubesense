/**
 * Pure maths for the Tubesense capture app. No React Native imports, so the node test
 * (test/metrics.test.ts) can run it directly.
 *
 * Conventions (checked against expo-sensors 58 native code, node_modules/expo-sensors):
 * - DeviceMotion.rotationRate is in deg/s. Axis mapping differs by platform:
 *   iOS  alpha = z, beta = y, gamma = x   (ios/DeviceMotionModule.swift)
 *   Android alpha = x, beta = y, gamma = z (android/.../DeviceMotionModule.kt)
 * - accelerationIncludingGravity - acceleration = the gravity vector pointing DOWN in the
 *   phone frame on both platforms (iOS: CMDeviceMotion.gravity * g; Android: -TYPE_GRAVITY).
 * - acceleration = gravity-free (user) acceleration, m/s^2.
 *
 * Feature definitions follow the Python pipeline where they can:
 * - yaw rate = rotation rate projected on the unit UP vector: + = anticlockwise seen from
 *   above = left turn, whatever way up the phone is (tubesense/features.py).
 * - acc_h_rms = RMS over the second of the 0.3 Hz low-passed horizontal acceleration
 *   (live/detect.py 'h'); here from gravity-free DeviceMotion acceleration.
 * - vib_rms = RMS over the second of |a - 1 s mean| (all axes). The Python pipeline uses a
 *   3-40 Hz band; this is a simpler >~1 Hz version, so thresholds are indicative.
 * - stopped = vib_rms below VIB_STOP for >= MIN_STOP_S (= stops.py min_len 3 s); handled
 *   seconds (mean |gyro| > 0.25 rad/s, stops.py GYR_HANDLED) keep the previous state;
 *   sustained |yaw| >= 0.3 deg/s for >= 2 s means moving (stops.py YAW_MOVING / YAW_MIN_S 1.5 s,
 *   rounded up to whole seconds).
 * - knock: |a| spike > KNOCK_MIN (10 m/s^2, live/detect.py); spikes closer than 0.12 s are one
 *   knock (its own ringing); a group of exactly 2 knocks with gaps in [0.12, 1.0] s is a
 *   double knock (tubesense/sync.py triples gmin/gmax, live/detect.py 'double').
 */

export type Vec3 = [number, number, number];
export type Platform = 'ios' | 'android' | string;

export const KNOCK_MIN = 10.0; // m/s^2 above gravity (live/detect.py KNOCK_MIN)
export const KNOCK_GMIN = 0.12; // s, ringing merge window (sync.py gmin)
export const KNOCK_GMAX = 1.0; // s, max gap inside a double knock (sync.py gmax)
/** m/s^2. Per-second RMS of |a - 1 s mean| on phyphox R5/R11/R12 (24 Oct, read-only check):
 * at platforms median 0.10-0.14, moving median 0.33-0.86, moving 10th pct 0.15-0.29. */
export const VIB_STOP = 0.17;
export const MIN_STOP_S = 3; // stops.py stationary_intervals min_len
export const YAW_MOVING = 0.3; // deg/s, stops.py YAW_MOVING
export const YAW_MIN_S = 2; // s, stops.py YAW_MIN_S = 1.5 rounded up to whole seconds
export const GYR_HANDLED_DPS = 0.25 * (180 / Math.PI); // stops.py GYR_HANDLED 0.25 rad/s
export const H_LOWPASS_HZ = 0.3; // live/detect.py horizontal-force low-pass

export const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const norm = (a: Vec3) => Math.sqrt(dot(a, a));
export const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const scale = (a: Vec3, k: number): Vec3 => [a[0] * k, a[1] * k, a[2] * k];

export function unit(a: Vec3): Vec3 | null {
  const n = norm(a);
  return n > 1e-6 ? scale(a, 1 / n) : null;
}

/** DeviceMotion rotationRate {alpha, beta, gamma} (deg/s) -> phone-frame [x, y, z] deg/s. */
export function rotationVector(
  rr: { alpha: number; beta: number; gamma: number },
  platform: Platform,
): Vec3 {
  return platform === 'ios' ? [rr.gamma, rr.beta, rr.alpha] : [rr.alpha, rr.beta, rr.gamma];
}

/** Yaw rate (deg/s, + = left turn) from rotation (deg/s) and the DOWN gravity vector. */
export function yawRate(omega: Vec3, gravityDown: Vec3): number | null {
  const d = unit(gravityDown);
  return d ? -dot(omega, d) : null;
}

/** Component of a perpendicular to the down vector. */
export function horizontal(a: Vec3, gravityDown: Vec3): Vec3 {
  const d = unit(gravityDown);
  if (!d) return a;
  return sub(a, scale(d, dot(a, d)));
}

export type MotionSample = {
  t: number; // s, monotonic sample time
  acc: Vec3; // gravity-free acceleration, m/s^2
  down: Vec3; // gravity vector pointing down, m/s^2
  omega: Vec3; // rotation rate, deg/s, phone frame
};

export type KnockEvent = { t: number; n: number; peak: number };

/** Streaming double-knock detector. push() returns closed knock groups. */
export class KnockDetector {
  private knocks: { t: number; peak: number }[] = [];
  private lastSpikeT = -Infinity;

  push(t: number, accMag: number): KnockEvent[] {
    if (accMag >= KNOCK_MIN) {
      const last = this.knocks[this.knocks.length - 1];
      if (last && t - this.lastSpikeT < KNOCK_GMIN) {
        last.peak = Math.max(last.peak, accMag); // ringing of the same knock
      } else {
        this.knocks.push({ t, peak: accMag });
      }
      this.lastSpikeT = t;
    }
    return this.close(t);
  }

  /** Emit groups whose last member is more than KNOCK_GMAX old. */
  close(now: number): KnockEvent[] {
    const out: KnockEvent[] = [];
    while (this.knocks.length) {
      let n = 1;
      while (
        n < this.knocks.length &&
        this.knocks[n].t - this.knocks[n - 1].t <= KNOCK_GMAX
      )
        n++;
      const lastT = this.knocks[n - 1].t;
      if (n === this.knocks.length && now - lastT <= KNOCK_GMAX) break; // group may still grow
      const grp = this.knocks.splice(0, n);
      out.push({ t: grp[0].t, n, peak: Math.max(...grp.map((k) => k.peak)) });
    }
    return out;
  }
}

/** Moving/stopped state machine, one call per second. */
export class StopDetector {
  state: 'moving' | 'stopped' = 'moving';
  private quietS = 0;
  private yawS = 0;

  step(vibRms: number | null, yawDps: number | null, handled: boolean): 'moving' | 'stopped' {
    if (handled || vibRms === null) {
      this.yawS = 0;
      return this.state; // handling hides the train's own motion: keep the previous state
    }
    this.yawS = yawDps !== null && Math.abs(yawDps) >= YAW_MOVING ? this.yawS + 1 : 0;
    this.quietS = vibRms < VIB_STOP ? this.quietS + 1 : 0;
    if (this.yawS >= YAW_MIN_S) {
      this.state = 'moving'; // turning on a curve
      this.quietS = 0;
    } else if (this.quietS >= MIN_STOP_S) {
      this.state = 'stopped';
    } else if (vibRms >= VIB_STOP) {
      this.state = 'moving';
    }
    return this.state;
  }
}

export type SecondSummary = {
  yaw_rate_dps: number;
  acc_h_rms: number;
  vib_rms: number;
  handled: boolean;
  h_mean: Vec3; // mean over the second of the 0.3 Hz low-passed horizontal acceleration vector (phone frame), m/s^2
  n: number;
};

/** Per-second summary of motion samples. hState carries the 0.3 Hz low-pass between seconds. */
export function summariseMotion(
  samples: MotionSample[],
  hState: { h: Vec3; t: number | null },
): SecondSummary | null {
  if (!samples.length) return null;
  let yawSum = 0;
  let yawN = 0;
  let gyrSum = 0;
  const mean: Vec3 = [0, 0, 0];
  for (const s of samples) {
    const y = yawRate(s.omega, s.down);
    if (y !== null) {
      yawSum += y;
      yawN++;
    }
    gyrSum += norm(s.omega);
    for (let i = 0; i < 3; i++) mean[i] += s.acc[i] / samples.length;
  }
  let vib2 = 0;
  let h2 = 0;
  const hSum: Vec3 = [0, 0, 0];
  for (const s of samples) {
    vib2 += dot(sub(s.acc, mean), sub(s.acc, mean));
    const dt = hState.t === null ? 0 : Math.max(0, s.t - hState.t);
    const k = hState.t === null ? 1 : 1 - Math.exp(-2 * Math.PI * H_LOWPASS_HZ * dt);
    const h = horizontal(s.acc, s.down);
    hState.h = [0, 1, 2].map((i) => hState.h[i] + k * (h[i] - hState.h[i])) as Vec3;
    hState.t = s.t;
    h2 += dot(hState.h, hState.h);
    for (let i = 0; i < 3; i++) hSum[i] += hState.h[i];
  }
  return {
    yaw_rate_dps: yawN ? yawSum / yawN : 0,
    acc_h_rms: Math.sqrt(h2 / samples.length),
    vib_rms: Math.sqrt(vib2 / samples.length),
    handled: gyrSum / samples.length > GYR_HANDLED_DPS,
    h_mean: scale(hSum, 1 / samples.length),
    n: samples.length,
  };
}

/** Mean |B| (uT) over the second; null when there are no samples or the field reads ~0
 * (iOS reports zeros until the calibrated magnetometer is ready). */
export function magnitudeMean(samples: Vec3[]): number | null {
  const m = samples.map(norm).filter((x) => x > 1e-3);
  return m.length ? m.reduce((a, b) => a + b, 0) / m.length : null;
}

/** Pressure history: change over the last `window` seconds (latest - reading >= window s old). */
export function pressureChange(
  history: { t: number; p: number }[],
  now: number,
  window = 10,
): number | null {
  if (!history.length) return null;
  const latest = history[history.length - 1];
  let old: { t: number; p: number } | null = null;
  for (const h of history) if (now - h.t >= window) old = h;
  return old ? latest.p - old.p : null;
}

/** Bounded outbox: keep at most `max` rows, dropping the oldest. */
export function enqueue<T>(queue: T[], row: T, max = 60): T[] {
  const q = queue.concat([row]);
  return q.length > max ? q.slice(q.length - max) : q;
}

export const round = (x: number | null, d = 3) =>
  x === null || !Number.isFinite(x) ? null : Math.round(x * 10 ** d) / 10 ** d;

/** Random session id (not tied to the device or the user). */
export function sessionId(): string {
  const a = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let s = 'm-';
  for (let i = 0; i < 10; i++) s += a[Math.floor(Math.random() * a.length)];
  return s;
}

/** Light check on the laptop address typed by the user (IPv4 or host name, optional :port). */
export function ingestUrl(host: string): string | null {
  const h = host.trim().replace(/^https?:\/\//, '').replace(/\/.*$/, '');
  if (!/^[A-Za-z0-9.-]+(:\d{1,5})?$/.test(h)) return null;
  return `http://${h.includes(':') ? h : `${h}:8770`}/api/live/ingest`;
}

// ---------------------------------------------------------------------------------------------- 25 Oct additions
/**
 * Running-vibration band, as tubesense/stops.py `vib`: acceleration band-passed 3-40 Hz (Butterworth, order 3), squared
 * and summed over the axes, RMS over the second. Differences from the pipeline: causal (sosfilt, not sosfiltfilt), a
 * 3 Hz high-pass cascaded with a 40 Hz low-pass instead of scipy's band design (within 0.1 dB in the pass band for a band
 * this wide), and 1 s instead of 2 s windows. Sent for parity with the pipeline only; the stop gate stays on vib_rms,
 * which separated dwells from running slightly better on the phyphox rides (AUC 0.936 vs 0.918, 15 runs, 25 Oct).
 */
type Sec = { b0: number; b1: number; b2: number; a1: number; a2: number };

function butter3(fc: number, fs: number, high: boolean): Sec[] {
  const K = Math.tan((Math.PI * Math.min(fc, 0.49 * fs)) / fs);
  const n2 = 1 / (1 + K + K * K); // second-order section, Q = 1
  const s2: Sec = high
    ? { b0: n2, b1: -2 * n2, b2: n2, a1: 2 * (K * K - 1) * n2, a2: (1 - K + K * K) * n2 }
    : { b0: K * K * n2, b1: 2 * K * K * n2, b2: K * K * n2, a1: 2 * (K * K - 1) * n2, a2: (1 - K + K * K) * n2 };
  const s1: Sec = high
    ? { b0: 1 / (K + 1), b1: -1 / (K + 1), b2: 0, a1: (K - 1) / (K + 1), a2: 0 }
    : { b0: K / (K + 1), b1: K / (K + 1), b2: 0, a1: (K - 1) / (K + 1), a2: 0 };
  return [s2, s1];
}

/** Magnitude of a section cascade at f Hz (for tests). */
export function secGain(secs: Sec[], fs: number, f: number): number {
  const w = (2 * Math.PI * f) / fs;
  let g = 1;
  for (const s of secs) {
    const nr = s.b0 + s.b1 * Math.cos(w) + s.b2 * Math.cos(2 * w), ni = -(s.b1 * Math.sin(w) + s.b2 * Math.sin(2 * w));
    const dr = 1 + s.a1 * Math.cos(w) + s.a2 * Math.cos(2 * w), di = -(s.a1 * Math.sin(w) + s.a2 * Math.sin(2 * w));
    g *= Math.hypot(nr, ni) / Math.hypot(dr, di);
  }
  return g;
}

export function vibBandSections(fs: number): Sec[] {
  return [...butter3(3, fs, true), ...butter3(40, fs, false)];
}

export class VibBand {
  private fs = 0;
  private secs: Sec[] = [];
  private z: Float64Array[] = []; // per axis: two states per section
  private sum = 0;
  private n = 0;

  /** Feed one second of gravity-free acceleration; fs is the nominal sample rate (DeviceMotion asks for 100 Hz). */
  push(acc: Vec3[], fs: number) {
    if (!(fs > 0)) return;
    if (Math.abs(fs - this.fs) > 0.1 * fs) {
      this.fs = fs;
      this.secs = vibBandSections(fs);
      this.z = [0, 1, 2].map(() => new Float64Array(this.secs.length * 2));
    }
    for (const a of acc) {
      let e = 0;
      for (let ax = 0; ax < 3; ax++) {
        const z = this.z[ax];
        let y = a[ax];
        for (let k = 0; k < this.secs.length; k++) {
          const s = this.secs[k];
          const out = s.b0 * y + z[2 * k];
          z[2 * k] = s.b1 * y - s.a1 * out + z[2 * k + 1];
          z[2 * k + 1] = s.b2 * y - s.a2 * out;
          y = out;
        }
        e += y * y;
      }
      this.sum += e;
      this.n++;
    }
  }

  /** RMS since the last take(), m/s^2, or null with no samples. */
  take(): number | null {
    if (!this.n) return null;
    const r = Math.sqrt(this.sum / this.n);
    this.sum = 0;
    this.n = 0;
    return r;
  }
}

/** Mean gravity direction over the second as a unit vector in the phone frame (pointing down), or null. */
export function gravityDirection(samples: MotionSample[]): Vec3 | null {
  const m: Vec3 = [0, 0, 0];
  for (const s of samples) {
    const u = unit(s.down);
    if (u) for (let i = 0; i < 3; i++) m[i] += u[i];
  }
  return unit(m);
}

/** Angle between two directions, degrees. A large change means the phone was moved (hand -> seat, pocket, floor). */
export function angleDeg(a: Vec3, b: Vec3): number {
  const ua = unit(a), ub = unit(b);
  if (!ua || !ub) return 0;
  return (Math.acos(Math.max(-1, Math.min(1, dot(ua, ub)))) * 180) / Math.PI;
}

/**
 * Departure gate: the ride starts only when the phone feels a train pull away, not when it vibrates.
 * Vibration alone (vib_rms) cannot tell a hand, a desk or a walk from a running train. A departing train pushes the
 * phone the same way for many seconds; a hand cannot (0.45 m/s^2 held for 5 s would carry the phone 5.6 m).
 * push = |mean of the last PUSH_S per-second mean horizontal acceleration vectors| (0.3 Hz low-pass, phone frame).
 * On the 23-24 Oct phyphox rides (29 departures, Central + H&C; gravity from the end of each dwell, then gyro only)
 * every departure reached push >= 0.62 m/s^2 (median 1.21), most within 0-6 s of the doors closing. Starting to walk
 * (0 -> 1.4 m/s) gives at most 0.28. Handled seconds (mean |gyro| > 0.25 rad/s) clear the window, because the phone
 * frame turns. Every second in the window must also push the same way (component along the mean >= DEPART_EACH):
 * traction pulls steadily, a shaken phone swings both ways. Departed after DEPART_N consecutive seconds that pass.
 */
export const PUSH_S = 5;
export const DEPART_PUSH = 0.45; // m/s^2
export const DEPART_N = 2;
export const DEPART_EACH = 0.2; // m/s^2, each second's push along the window's direction

export class DepartureGate {
  private win: Vec3[] = [];
  private above = 0;
  departed = false;
  push = 0;

  step(hMean: Vec3 | null, handled: boolean): boolean {
    if (this.departed) return true;
    if (!hMean || handled) {
      this.win = [];
      this.above = 0;
      this.push = 0;
      return false;
    }
    this.win = [...this.win, hMean].slice(-PUSH_S);
    const m: Vec3 = [0, 0, 0];
    for (const v of this.win) for (let i = 0; i < 3; i++) m[i] += v[i] / this.win.length;
    this.push = this.win.length === PUSH_S ? norm(m) : 0;
    const dir = unit(m);
    const steady = !!dir && this.win.every((v) => dot(v, dir) >= DEPART_EACH);
    this.above = this.push >= DEPART_PUSH && steady ? this.above + 1 : 0;
    if (this.above >= DEPART_N) this.departed = true;
    return this.departed;
  }

  /** The rider says the train has left (fallback when the push was missed, or for a demo off the train). */
  force() {
    this.departed = true;
  }
}
