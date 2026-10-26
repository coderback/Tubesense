// Sound level from microphone PCM, on the phone, in memory. Only one number per second leaves this module; the samples are
// filtered, squared, summed and dropped. Nothing is written to disk and no audio is sent anywhere.
//
// A-weighting: the IEC 61672 analogue filter, poles 20.6, 107.7, 737.9 and 12194 Hz, mapped to the sample rate with the
// bilinear transform as three biquads and normalised to 0 dB at 1 kHz. At 48 kHz it is within 0.05 dB of IEC up to
// 4 kHz, then reads low: -0.5 dB at 8 kHz, -2.7 at 12.5 kHz (test/sound.test.mts prints it). Train roar sits far below.
//
// Units: dBFS (dB re an RMS of 1.0, so a full-scale sine reads -3 dBFS). dB SPL needs a calibration offset, which only a reference
// meter can supply: offset = reference dB(A) - our dBFS(A) in the same steady sound.

const F1 = 20.598997, F2 = 107.65265, F3 = 737.86223, F4 = 12194.217;

export type Biquad = { b: [number, number, number]; a: [number, number, number] }; // a[0] = 1

function hiPair(fs: number, f: number): Biquad {
  // s^2 / (s + w)^2
  const w = 2 * Math.PI * f, k = 2 * fs;
  const c = k / (k + w), p = (k - w) / (k + w);
  return { b: [c * c, -2 * c * c, c * c], a: [1, -2 * p, p * p] };
}

function loPair(fs: number, fa: number, fb: number): Biquad {
  // 1 / ((s + wa)(s + wb))
  const wa = 2 * Math.PI * fa, wb = 2 * Math.PI * fb, k = 2 * fs;
  const pa = (k - wa) / (k + wa), pb = (k - wb) / (k + wb), g = 1 / ((k + wa) * (k + wb));
  return { b: [g, 2 * g, g], a: [1, -(pa + pb), pa * pb] };
}

/** Magnitude of a biquad cascade at f Hz. */
export function cascadeGain(sections: Biquad[], fs: number, f: number): number {
  const th = (2 * Math.PI * f) / fs;
  let mag = 1;
  for (const { b, a } of sections) {
    const re = (c: number[]) => c[0] + c[1] * Math.cos(th) + c[2] * Math.cos(2 * th);
    const im = (c: number[]) => -(c[1] * Math.sin(th) + c[2] * Math.sin(2 * th));
    mag *= Math.hypot(re(b), im(b)) / Math.hypot(re(a), im(a));
  }
  return mag;
}

/** A-weighting as three biquads for sample rate fs, 0 dB at 1 kHz. */
export function aWeighting(fs: number): Biquad[] {
  const s = [hiPair(fs, F4), hiPair(fs, F1), loPair(fs, F2, F3)];
  const g = 1 / cascadeGain(s, fs, 1000);
  s[0] = { b: [s[0].b[0] * g, s[0].b[1] * g, s[0].b[2] * g], a: s[0].a };
  return s;
}

/** The analogue A-weighting in dB (IEC 61672 formula), for tests. */
export function aWeightingDb(f: number): number {
  const f2 = f * f;
  const ra = (F4 * F4 * f2 * f2) / ((f2 + F1 * F1) * Math.sqrt((f2 + F2 * F2) * (f2 + F3 * F3)) * (f2 + F4 * F4));
  return 20 * Math.log10(ra) + 2.0;
}

const dbfs = (meanSq: number) => (meanSq > 0 ? 10 * Math.log10(meanSq) : -Infinity);

/**
 * Per-second sound level. push() each PCM buffer as it arrives; take() returns the A-weighted and unweighted
 * equivalent levels (dBFS) over everything pushed since the last take() and resets. Filter state carries over
 * between buffers so there are no clicks at the edges.
 */
export class LevelMeter {
  private fs = 0;
  private sec: Biquad[] = [];
  private c: Float64Array = new Float64Array(0); // b0 b1 b2 a1 a2 per biquad
  private z: Float64Array = new Float64Array(0); // two state values per biquad (transposed direct form II)
  private sumA = 0;
  private sumZ = 0;
  private sumX = 0;
  private n = 0;
  private maxA = 0; // largest 125 ms A-weighted mean square in the second ("fast")
  private fastSum = 0;
  private fastN = 0;
  private fastLen = 0;
  private settle = 0; // samples to skip while the filter settles after a (re)start

  private setRate(fs: number) {
    this.fs = fs;
    this.sec = aWeighting(fs);
    this.c = Float64Array.from(this.sec.flatMap(({ b, a }) => [b[0], b[1], b[2], a[1], a[2]]));
    this.z = new Float64Array(this.sec.length * 2);
    this.fastLen = Math.max(1, Math.round(fs * 0.125));
    this.settle = Math.round(fs * 0.5);
  }

  /** Float samples in [-1, 1]; interleaved channels are averaged to mono. */
  push(x: Float32Array | number[], fs: number, channels = 1) {
    if (!(fs > 0)) return;
    if (fs !== this.fs) this.setRate(fs);
    const c = this.c, z = this.z, ns = this.sec.length; // flat coefficients: the hot loop runs 48 000 times a second
    for (let i = 0; i + channels <= x.length; i += channels) {
      let v = x[i];
      for (let ch = 1; ch < channels; ch++) v += x[i + ch];
      if (channels > 1) v /= channels;
      let y = v;
      for (let k = 0, j = 0; k < ns; k++, j += 5) {
        const out = c[j] * y + z[2 * k];
        z[2 * k] = c[j + 1] * y - c[j + 3] * out + z[2 * k + 1];
        z[2 * k + 1] = c[j + 2] * y - c[j + 4] * out;
        y = out;
      }
      if (this.settle > 0) {
        this.settle--;
        continue;
      }
      this.sumA += y * y;
      this.sumZ += v * v;
      this.sumX += v;
      this.n++;
      this.fastSum += y * y;
      if (++this.fastN >= this.fastLen) {
        this.maxA = Math.max(this.maxA, this.fastSum / this.fastN);
        this.fastSum = 0;
        this.fastN = 0;
      }
    }
  }

  /** {laeq, lzeq, lafmax} in dBFS for the samples since the last take, or null if there were none. */
  take(): { laeq: number; lzeq: number; lafmax: number; n: number } | null {
    const n = this.n;
    if (!n) return null;
    const mean = this.sumX / n;
    const out = {
      laeq: dbfs(this.sumA / n),
      lzeq: dbfs(Math.max(0, this.sumZ / n - mean * mean)), // DC removed
      lafmax: dbfs(this.maxA || this.sumA / n),
      n,
    };
    this.sumA = this.sumZ = this.sumX = 0;
    this.n = 0;
    this.maxA = 0;
    return out;
  }

  reset() {
    this.fs = 0;
    this.sumA = this.sumZ = this.sumX = 0;
    this.n = this.fastSum = this.fastN = 0;
    this.maxA = 0;
  }
}

/** Mean of dB values on an energy basis (what a meter shows as Leq over several seconds). */
export function energyMeanDb(levels: number[]): number | null {
  const ok = levels.filter((v) => Number.isFinite(v));
  if (!ok.length) return null;
  return 10 * Math.log10(ok.reduce((s, v) => s + 10 ** (v / 10), 0) / ok.length);
}

// ---------------------------------------------------------------------------------------------- frequency content
// Octave bands and the dominant frequency of each second, for the corrugation question (dominant roar frequency ÷ speed).
// Same definitions as tubesense/noise.py where it matters: Hann power spectrum, bands summed from bins, dominant peak in
// 100-2000 Hz of a 5-bin smoothed spectrum, prominence = peak over the median of that range (dB). Differences: octave
// (not third-octave) bands, base-10 centres 63 Hz-8 kHz; 8192-sample frames (5.9 Hz bins at 48 kHz) without overlap,
// to keep the phone's work small. Unweighted (Z), dBFS.

export const OCT_FC = [-4, -3, -2, -1, 0, 1, 2, 3].map((n) => 1000 * 10 ** ((3 * n) / 10)); // 63.1 ... 7943 Hz
export const OCT_LABELS = ['63', '125', '250', '500', '1k', '2k', '4k', '8k'];
export const DOM_RANGE: [number, number] = [100, 2000];
const NFFT = 8192;

/** In-place radix-2 FFT of (re, im), length a power of two. */
export function fft(re: Float64Array, im: Float64Array) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      let t = re[i]; re[i] = re[j]; re[j] = t;
      t = im[i]; im[i] = im[j]; im[j] = t;
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len, wr = Math.cos(ang), wi = Math.sin(ang), half = len >> 1;
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0;
      for (let k = 0; k < half; k++) {
        const a = i + k, b = a + half;
        const tr = re[b] * cr - im[b] * ci, ti = re[b] * ci + im[b] * cr;
        re[b] = re[a] - tr; im[b] = im[a] - ti;
        re[a] += tr; im[a] += ti;
        const nr = cr * wr - ci * wi;
        ci = cr * wi + ci * wr;
        cr = nr;
      }
    }
  }
}

export type SpectrumSecond = { oct: (number | null)[]; peakHz: number | null; peakPromDb: number | null; frames: number };

export class Spectrum {
  private fs = 0;
  private win = new Float64Array(NFFT);
  private frame = new Float64Array(NFFT);
  private fill = 0;
  private re = new Float64Array(NFFT);
  private im = new Float64Array(NFFT);
  private psum = new Float64Array(NFFT / 2); // summed one-sided power per bin since the last take()
  private frames = 0;
  private norm = 1;

  constructor() {
    let w2 = 0;
    for (let i = 0; i < NFFT; i++) {
      this.win[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / NFFT);
      w2 += this.win[i] ** 2;
    }
    // one-sided bin power -> mean square: a sine of RMS r sums to r^2 over its bins
    this.norm = 2 / (NFFT * w2);
  }

  push(x: Float32Array | number[], fs: number, channels = 1) {
    if (!(fs > 0)) return;
    if (fs !== this.fs) {
      this.fs = fs;
      this.fill = 0;
      this.psum.fill(0);
      this.frames = 0;
    }
    for (let i = 0; i + channels <= x.length; i += channels) {
      let v = x[i];
      for (let ch = 1; ch < channels; ch++) v += x[i + ch];
      this.frame[this.fill++] = channels > 1 ? v / channels : v;
      if (this.fill === NFFT) this.analyse();
    }
  }

  private analyse() {
    const { re, im, frame, win, psum } = this;
    let mean = 0;
    for (let i = 0; i < NFFT; i++) mean += frame[i];
    mean /= NFFT;
    for (let i = 0; i < NFFT; i++) {
      re[i] = (frame[i] - mean) * win[i];
      im[i] = 0;
    }
    fft(re, im);
    for (let k = 1; k < NFFT / 2; k++) psum[k] += re[k] * re[k] + im[k] * im[k];
    this.frames++;
    this.fill = 0;
  }

  /** Octave levels (dBFS), dominant frequency and its prominence over the frames since the last take(). */
  take(): SpectrumSecond | null {
    const nf = this.frames;
    if (!nf || !this.fs) return null;
    const df = this.fs / NFFT, P = this.psum, k = this.norm / nf;
    const oct = OCT_FC.map((fc) => {
      const lo = fc * 10 ** (-3 / 20), hi = fc * 10 ** (3 / 20);
      let s = 0, n = 0;
      for (let b = Math.ceil(lo / df); b < hi / df && b < P.length; b++, n++) s += P[b];
      return n && s > 0 ? Math.round(10 * Math.log10(s * k) * 10) / 10 : null;
    });
    // dominant peak: 5-bin moving average inside DOM_RANGE, parabolic interpolation on dB
    const b0 = Math.ceil(DOM_RANGE[0] / df), b1 = Math.min(P.length - 3, Math.floor(DOM_RANGE[1] / df));
    const sm: number[] = [];
    for (let b = b0; b <= b1; b++) sm.push((P[b - 2] + P[b - 1] + P[b] + P[b + 1] + P[b + 2]) / 5);
    let peakHz: number | null = null, peakPromDb: number | null = null;
    if (sm.length > 2) {
      let j = 0;
      for (let i = 1; i < sm.length; i++) if (sm[i] > sm[j]) j = i;
      if (sm[j] > 0) {
        let off = 0;
        if (j > 0 && j < sm.length - 1 && sm[j - 1] > 0 && sm[j + 1] > 0) {
          const a = Math.log(sm[j - 1]), b = Math.log(sm[j]), c = Math.log(sm[j + 1]);
          const d = a - 2 * b + c;
          off = d < 0 ? (0.5 * (a - c)) / d : 0;
        }
        peakHz = Math.round((b0 + j + off) * df * 10) / 10;
        const med = [...sm].sort((p, q) => p - q)[sm.length >> 1];
        peakPromDb = med > 0 ? Math.round(10 * Math.log10(sm[j] / med) * 10) / 10 : null;
      }
    }
    this.psum.fill(0);
    this.frames = 0;
    return { oct, peakHz, peakPromDb, frames: nf };
  }

  reset() {
    this.fs = 0;
    this.fill = 0;
    this.psum.fill(0);
    this.frames = 0;
  }
}
