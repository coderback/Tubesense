// Synthetic checks for src/metrics.ts. Run: npm test  (node >= 22.18 strips the types).
import {
  DepartureGate,
  KnockDetector,
  StopDetector,
  VibBand,
  angleDeg,
  gravityDirection,
  secGain,
  vibBandSections,
  enqueue,
  ingestUrl,
  magnitudeMean,
  pressureChange,
  rotationVector,
  summariseMotion,
  yawRate,
  type MotionSample,
  type Vec3,
} from '../src/metrics.ts';

let failed = 0;
function check(name: string, ok: boolean, detail = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  ' + detail : ''}`);
  if (!ok) failed++;
}
const near = (a: number | null, b: number, tol = 1e-6) => a !== null && Math.abs(a - b) <= tol;
const G = 9.80665;
const deg = Math.PI / 180;

// Rotate a world vector into the phone frame for a phone tilted by `tilt` deg about its x axis.
function toPhone(v: Vec3, tilt: number): Vec3 {
  const c = Math.cos(tilt * deg);
  const s = Math.sin(tilt * deg);
  return [v[0], c * v[1] + s * v[2], -s * v[1] + c * v[2]];
}

// 1. Yaw: a left turn of 5 deg/s about world up, phone flat, tilted 60 deg, and face down.
for (const tilt of [0, 60, 180]) {
  const down = toPhone([0, 0, -G], tilt); // iOS-style gravity, pointing down
  const omega = toPhone([0, 0, 5], tilt); // anticlockwise from above (right-hand rule about up)
  check(`yaw left turn, tilt ${tilt} deg`, near(yawRate(omega, down), 5, 1e-9), `got ${yawRate(omega, down)}`);
  const right = toPhone([0, 0, -5], tilt);
  check(`yaw right turn, tilt ${tilt} deg`, near(yawRate(right, down), -5, 1e-9));
  // Rotation about a horizontal axis (roll/pitch) must not leak into yaw.
  const pitch = toPhone([3, 0, 0], tilt);
  check(`no yaw from pitch, tilt ${tilt} deg`, near(yawRate(pitch, down), 0, 1e-9));
}

// 2. Platform axis mapping: iOS alpha = z, gamma = x; Android alpha = x, gamma = z.
check('iOS rotationRate mapping', JSON.stringify(rotationVector({ alpha: 1, beta: 2, gamma: 3 }, 'ios')) === '[3,2,1]');
check('Android rotationRate mapping', JSON.stringify(rotationVector({ alpha: 1, beta: 2, gamma: 3 }, 'android')) === '[1,2,3]');

// 3. A second of motion: phone tilted 30 deg, steady 0.8 m/s^2 horizontal braking, 2 deg/s left turn.
{
  const tilt = 30;
  const samples: MotionSample[] = [];
  for (let i = 0; i < 100; i++) {
    samples.push({
      t: i / 100,
      acc: toPhone([0.8, 0, 0], tilt),
      down: toPhone([0, 0, -G], tilt),
      omega: toPhone([0, 0, 2], tilt),
    });
  }
  const hState = { h: [0, 0, 0] as Vec3, t: null as number | null };
  const s = summariseMotion(samples, hState)!;
  check('summary yaw 2 deg/s on a tilted phone', near(s.yaw_rate_dps, 2, 1e-9), `got ${s.yaw_rate_dps}`);
  check('summary acc_h_rms = 0.8 for steady horizontal force', near(s.acc_h_rms, 0.8, 1e-9), `got ${s.acc_h_rms}`);
  check('summary vib_rms ~0 for steady force', near(s.vib_rms, 0, 1e-9));
  check('summary not handled', !s.handled);
}

// 4. Still period then vibration: STOPPED only after 3 quiet seconds, then MOVING again.
{
  const sd = new StopDetector();
  const vib = [0.5, 0.5, 0.05, 0.05, 0.05, 0.05, 0.6];
  const states = vib.map((v) => sd.step(v, 0, false));
  check('still period -> stopped after 3 s', JSON.stringify(states) ===
    JSON.stringify(['moving', 'moving', 'moving', 'moving', 'stopped', 'stopped', 'moving']), states.join(','));
  // Handling during a stop keeps 'stopped'.
  sd.step(0.05, 0, false); sd.step(0.05, 0, false); sd.step(0.05, 0, false);
  check('handled second keeps stopped', sd.step(3.0, 0, true) === 'stopped');
  // Quiet but turning (curve at constant speed) = moving.
  const sd2 = new StopDetector();
  const s2 = [1, 2, 3, 4].map(() => sd2.step(0.05, 0.8, false));
  check('quiet but turning stays moving', s2.every((x) => x === 'moving'), s2.join(','));
}

// 5. Double knock: two spikes 0.35 s apart (each ringing for 50 ms) at 100 Hz.
function knockRun(spikeTimes: number[], dur = 4): ReturnType<KnockDetector['push']> {
  const kd = new KnockDetector();
  const out = [];
  for (let i = 0; i < dur * 100; i++) {
    const t = i / 100;
    const hit = spikeTimes.some((k) => t >= k && t < k + 0.05);
    out.push(...kd.push(t, hit ? 30 : 0.2));
  }
  out.push(...kd.close(Infinity));
  return out;
}
{
  const ev = knockRun([1.0, 1.35]);
  check('double knock detected', ev.length === 1 && ev[0].n === 2 && near(ev[0].t, 1.0, 1e-9), JSON.stringify(ev));
  const single = knockRun([1.0]);
  check('single knock is n=1', single.length === 1 && single[0].n === 1);
  const far = knockRun([0.5, 2.5]);
  check('two knocks 2 s apart are two singles', far.length === 2 && far.every((e) => e.n === 1));
  const ring = knockRun([1.0, 1.08]); // 0.08 s apart: the same knock's ringing
  check('spikes < 0.12 s apart merge', ring.length === 1 && ring[0].n === 1, JSON.stringify(ring));
  const quiet = knockRun([]);
  check('no knocks in a still period', quiet.length === 0);
}

// 6. Magnetometer, pressure, outbox, URL.
check('|B| mean', near(magnitudeMean([[30, 40, 0], [0, 0, 50]]), 50));
check('|B| null when zeros', magnitudeMean([[0, 0, 0]]) === null);
{
  const h = Array.from({ length: 15 }, (_, i) => ({ t: i, p: 1013 + 0.04 * i }));
  check('pressure change over 10 s', near(pressureChange(h, 14), 0.4, 1e-9), `${pressureChange(h, 14)}`);
  check('pressure change null before 10 s', pressureChange(h.slice(0, 5), 4) === null);
}
{
  let q: number[] = [];
  for (let i = 0; i < 75; i++) q = enqueue(q, i, 60);
  check('outbox keeps last 60 rows', q.length === 60 && q[0] === 15 && q[59] === 74);
}
check('ingest URL from IP', ingestUrl(' 172.20.10.3 ') === 'http://172.20.10.3:8770/api/live/ingest');
check('ingest URL keeps port', ingestUrl('http://172.20.10.3:9000/') === 'http://172.20.10.3:9000/api/live/ingest');
check('ingest URL rejects junk', ingestUrl('not a host!') === null);

// 3-40 Hz vibration band: -3 dB at both edges, flat in the middle, gravity (DC) and slow sway removed
{
  const secs = vibBandSections(100);
  const dB = (f: number) => 20 * Math.log10(secGain(secs, 100, f));
  check('vib band -3 dB at 3 Hz', Math.abs(dB(3) + 3.01) < 0.1, dB(3).toFixed(2));
  check('vib band -3 dB at 40 Hz', Math.abs(dB(40) + 3.01) < 0.1, dB(40).toFixed(2));
  check('vib band flat at 12 Hz', Math.abs(dB(12)) < 0.1, dB(12).toFixed(3));
  check('vib band rejects 0.5 Hz sway by > 40 dB', dB(0.5) < -40, dB(0.5).toFixed(1));
  const vb = new VibBand();
  const sec = (f: number, amp: number, off: number): Vec3[] =>
    Array.from({ length: 100 }, (_, i) => [amp * Math.sin(2 * Math.PI * f * (off + i / 100)), 0, 0] as Vec3);
  for (let s = 0; s < 3; s++) vb.push(sec(15, 1, s), 100); // settle
  vb.take();
  vb.push(sec(15, 1, 3), 100);
  const r = vb.take();
  check('15 Hz sine of amplitude 1 -> band RMS 0.707', r !== null && Math.abs(r - Math.SQRT1_2) < 0.01, `${r?.toFixed(3)}`);
  const v2 = new VibBand();
  for (let s = 0; s < 5; s++) v2.push(Array.from({ length: 100 }, () => [0, 0, 9.81] as Vec3), 100);
  v2.take();
  v2.push(Array.from({ length: 100 }, () => [0, 0, 9.81] as Vec3), 100);
  check('constant gravity -> ~0 band RMS', (v2.take() ?? 1) < 1e-3);
  check('no samples -> null', new VibBand().take() === null);
}

// gravity direction and placement change
{
  const mk = (down: Vec3): MotionSample => ({ t: 0, acc: [0, 0, 0], down, omega: [0, 0, 0] });
  const flat = gravityDirection([mk([0, 0, -9.8]), mk([0, 0, -9.81])]);
  check('flat phone: gravity along -z', !!flat && near(flat[2], -1, 1e-9));
  const upright = gravityDirection([mk([0, -9.8, 0])]);
  check('flat -> upright is a 90 deg change', !!flat && !!upright && near(angleDeg(flat, upright), 90, 1e-9));
  check('same direction is 0 deg', !!flat && angleDeg(flat, flat) < 1e-6);
  check('no samples -> null', gravityDirection([]) === null);
}


// departure gate: a sustained push departs; shaking, a still desk and starting to walk do not
{
  const g1 = new DepartureGate();
  let t = 0;
  for (; t < 30 && !g1.step([0.8, 0, 0], false); t++);
  check('train push 0.8 m/s^2 departs within 6 s', g1.departed && t <= 6, `${t + 1} s`);
  const g2 = new DepartureGate();
  let maxPush = 0;
  for (let i = 0; i < 600; i++) {
    g2.step([i % 2 ? 1.5 : -1.5, i % 3 ? 0.8 : -0.8, 0], false);
    maxPush = Math.max(maxPush, g2.push);
  }
  check('shaking back and forth (±1.5 m/s^2) never departs', !g2.departed, `5 s mean push reached ${maxPush.toFixed(2)}`);
  const g2b = new DepartureGate();
  let seed = 7;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648) * 2 - 1;
  // fidgeting: per-second horizontal force ±0.5 m/s^2, smoothed like the 0.3 Hz low-pass (hand-held rows 25 Oct:
  // acc_h_rms median 0.08, 90th pct 0.33 when not handled). Vigorous waving at ±0.8 without turning the phone does
  // trigger (~20 per hour in this model); in practice it turns the phone, which clears the window.
  let hh: Vec3 = [0, 0, 0];
  for (let i = 0; i < 3600; i++) {
    const x = [rnd() * 0.5, rnd() * 0.5, 0];
    hh = hh.map((v, j) => v + 0.85 * (x[j] - v)) as Vec3;
    g2b.step(hh, false);
  }
  check('an hour of fidgeting (±0.5 m/s^2) never departs', !g2b.departed);
  const g2c = new DepartureGate();
  let k = 0;
  for (; k < 30 && !g2c.step([0.7 + 0.25 * Math.sin(k), 0.15 * Math.cos(k), 0], false); k++);
  check('a wobbly but steady 0.7 m/s^2 pull still departs', g2c.departed, `${k + 1} s`);
  const g3 = new DepartureGate();
  for (let i = 0; i < 120; i++) g3.step([0.01, -0.01, 0], false);
  check('a still desk never departs', !g3.departed);
  const g4 = new DepartureGate();
  // start walking: 0 -> 1.4 m/s in 1 s, then steady with step jiggle
  g4.step([1.4, 0, 0], false);
  for (let i = 0; i < 60; i++) g4.step([i % 2 ? 0.3 : -0.3, 0, 0], false);
  check('starting to walk never departs', !g4.departed, `push ${g4.push.toFixed(2)}`);
  const g5 = new DepartureGate();
  for (let i = 0; i < 30; i++) g5.step([0.8, 0, 0], i % 3 === 2); // handled every third second
  check('handling clears the window', !g5.departed);
  const g6 = new DepartureGate();
  g6.force();
  check('rider can force departure', g6.step(null, true));
}

console.log(failed ? `\n${failed} FAILED` : '\nall passed');
if (failed) throw new Error(`${failed} checks failed`);
