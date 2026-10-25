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
  for (const s of samples) {
    vib2 += dot(sub(s.acc, mean), sub(s.acc, mean));
    const dt = hState.t === null ? 0 : Math.max(0, s.t - hState.t);
    const k = hState.t === null ? 1 : 1 - Math.exp(-2 * Math.PI * H_LOWPASS_HZ * dt);
    const h = horizontal(s.acc, s.down);
    hState.h = [0, 1, 2].map((i) => hState.h[i] + k * (h[i] - hState.h[i])) as Vec3;
    hState.t = s.t;
    h2 += dot(hState.h, hState.h);
  }
  return {
    yaw_rate_dps: yawN ? yawSum / yawN : 0,
    acc_h_rms: Math.sqrt(h2 / samples.length),
    vib_rms: Math.sqrt(vib2 / samples.length),
    handled: gyrSum / samples.length > GYR_HANDLED_DPS,
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
