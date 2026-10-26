// Checks for src/sound.ts. Run: npm test  (node >= 22.18 strips the types).
import { LevelMeter, Spectrum, aWeighting, aWeightingDb, cascadeGain, energyMeanDb, fft } from '../src/sound.ts';

let failed = 0;
function check(name: string, ok: boolean, detail = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  ' + detail : ''}`);
  if (!ok) failed++;
}
const db = (g: number) => 20 * Math.log10(g);

// the digital filter against the analogue A-weighting curve, at 48 kHz and 44.1 kHz
for (const fs of [48000, 44100]) {
  const s = aWeighting(fs);
  const rows: string[] = [];
  let worstLow = 0;
  for (const f of [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 12500, 16000]) {
    const err = db(cascadeGain(s, fs, f)) - aWeightingDb(f);
    rows.push(`${f} Hz ${err >= 0 ? '+' : ''}${err.toFixed(2)}`);
    if (f <= 4000) worstLow = Math.max(worstLow, Math.abs(err));
  }
  check(`A-weighting at ${fs} Hz within 0.5 dB of IEC up to 4 kHz`, worstLow < 0.5, `worst ${worstLow.toFixed(2)} dB`);
  console.log('      error vs IEC: ' + rows.join(' · '));
  check(`0 dB at 1 kHz (${fs})`, Math.abs(db(cascadeGain(s, fs, 1000))) < 1e-9);
}

function sine(fs: number, f: number, amp: number, seconds: number) {
  const x = new Float32Array(Math.round(fs * seconds));
  for (let i = 0; i < x.length; i++) x[i] = amp * Math.sin((2 * Math.PI * f * i) / fs);
  return x;
}

// a 1 kHz sine at amplitude 0.1: RMS 0.0707 -> -23.0 dBFS, A- and Z-weighted alike
{
  const m = new LevelMeter();
  const fs = 48000, x = sine(fs, 1000, 0.1, 2);
  for (let i = 0; i < x.length; i += 4096) m.push(x.subarray(i, i + 4096), fs); // buffers of odd sizes, state carries over
  const r = m.take();
  const want = db(0.1 / Math.SQRT2);
  check('1 kHz sine: LAeq = RMS level', !!r && Math.abs(r.laeq - want) < 0.05, `${r?.laeq.toFixed(2)} vs ${want.toFixed(2)}`);
  check('1 kHz sine: LZeq = RMS level', !!r && Math.abs(r.lzeq - want) < 0.05, `${r?.lzeq.toFixed(2)}`);
  check('steady sine: LAFmax = LAeq', !!r && Math.abs(r.lafmax - r.laeq) < 0.1, `${r?.lafmax.toFixed(2)}`);
  check('take() resets', m.take() === null);
}

// a 63 Hz rumble is cut by about 26 dB in A, not in Z
{
  const m = new LevelMeter();
  const fs = 48000;
  m.push(sine(fs, 63, 0.1, 2), fs);
  const r = m.take()!;
  const cut = r.lzeq - r.laeq;
  check('63 Hz: A-weighting cuts ~26 dB', Math.abs(cut - -aWeightingDb(63)) < 0.5, `${cut.toFixed(2)} dB`);
}

// stereo interleaved is averaged to mono; silence is -Infinity, not NaN
{
  const m = new LevelMeter();
  const fs = 48000, mono = sine(fs, 1000, 0.2, 1), st = new Float32Array(mono.length * 2);
  for (let i = 0; i < mono.length; i++) st[2 * i] = st[2 * i + 1] = mono[i];
  m.push(st, fs, 2);
  const r = m.take()!;
  check('stereo averaged to mono', Math.abs(r.laeq - db(0.2 / Math.SQRT2)) < 0.1, r.laeq.toFixed(2));
  const q = new LevelMeter();
  q.push(new Float32Array(48000), fs);
  check('silence -> -Infinity', q.take()!.laeq === -Infinity);
}

// a 0.25 s burst inside a quiet second: Leq is the energy average, LAFmax catches the burst
{
  const m = new LevelMeter();
  const fs = 48000;
  m.push(sine(fs, 1000, 0.01, 0.6), fs); // the first 0.5 s is the settle period
  const quiet = sine(fs, 1000, 0.01, 0.75), loud = sine(fs, 1000, 0.3, 0.25);
  m.take();
  m.push(quiet, fs);
  m.push(loud, fs);
  const r = m.take()!;
  check('burst: LAFmax well above LAeq', r.lafmax - r.laeq > 4, `${r.laeq.toFixed(1)} / ${r.lafmax.toFixed(1)}`);
}

// spectrum: a 1 kHz sine of amplitude 0.1 lands in the 1 kHz octave at its RMS level; peak at 1 kHz
{
  const sp = new Spectrum();
  const fs = 48000;
  const x = sine(fs, 1000, 0.1, 1.2);
  for (let i = 0; i < x.length; i += 4800) sp.push(x.subarray(i, i + 4800), fs);
  const r = sp.take()!;
  const want = db(0.1 / Math.SQRT2);
  check('1 kHz sine in the 1k octave at its RMS level', r.oct[4] != null && Math.abs(r.oct[4]! - want) < 0.2, `${r.oct[4]} vs ${want.toFixed(2)}`);
  check('other octaves far below', (r.oct[2] ?? -999) < want - 40 && (r.oct[7] ?? -999) < want - 40, `${r.oct[2]} / ${r.oct[7]}`);
  check('peak at 1 kHz (±2 Hz)', r.peakHz != null && Math.abs(r.peakHz - 1000) < 2, `${r.peakHz} Hz, prominence ${r.peakPromDb} dB`);
  check('7 frames in 1.2 s', r.frames === 7, `${r.frames}`);
}
// a 437 Hz tone in white noise: still found, with lower prominence; out-of-range 60 Hz hum ignored
{
  const sp = new Spectrum();
  const fs = 44100, n = fs * 2;
  const x = new Float32Array(n);
  let seed = 1;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648) - 0.5;
  for (let i = 0; i < n; i++) x[i] = 0.05 * Math.sin((2 * Math.PI * 437 * i) / fs) + 0.3 * Math.sin((2 * Math.PI * 60 * i) / fs) + 0.05 * rnd();
  sp.push(x, fs);
  const r = sp.take()!;
  check('437 Hz tone found in noise, 60 Hz hum outside 100-2000 Hz ignored', r.peakHz != null && Math.abs(r.peakHz - 437) < 3, `${r.peakHz} Hz, prom ${r.peakPromDb} dB`);
  check('hum shows in the 63 Hz octave', (r.oct[0] ?? -999) > (r.oct[2] ?? 0), `${r.oct[0]} vs ${r.oct[2]}`);
}
{
  const re = new Float64Array(8), im = new Float64Array(8);
  re[1] = 1;
  fft(re, im);
  check('fft of a unit impulse at 1 has |X| = 1 everywhere', [...re].every((v, i) => Math.abs(Math.hypot(v, im[i]) - 1) < 1e-12));
}

check('energy mean of 80 and 90 dB is 87.4', Math.abs((energyMeanDb([80, 90]) ?? 0) - 87.4) < 0.05);
check('energy mean ignores -Infinity', energyMeanDb([-Infinity, 70]) === 70);
check('energy mean of nothing is null', energyMeanDb([]) === null);

console.log(failed ? `\n${failed} sound check(s) failed` : '\nall sound checks passed');
process.exit(failed ? 1 : 0);
