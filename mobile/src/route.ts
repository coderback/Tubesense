// Route model for the capture app's map (design v2). Pure functions, no React, so they can be tested with plain node.
//
// The phone cannot place itself underground: there is no GPS, and the tracker runs on the laptop. The map therefore shows
//   1. the laptop tracker's estimate, when the ingest reply carries one (optional `position`, see CONTRACT.md), else
//   2. a rough time-based guess: seconds spent moving / the section's usual run time (the project's L0 baseline),
// and always says which one it is.

export type StationId = 'LST' | 'BTH' | 'MLE';
export const STATIONS: Record<StationId, { name: string; short: string }> = {
  LST: { name: 'Liverpool Street', short: 'Liverpool St' },
  BTH: { name: 'Bethnal Green', short: 'Bethnal Green' },
  MLE: { name: 'Mile End', short: 'Mile End' },
};

export type Section = { key: string; from: StationId; to: StationId; lengthM: number; usualRunS: number; usualNote: string };
// Track lengths: TfL Working Timetable (TTN 37/25, FOI-3481-2425). Run time: the noise map's duration for LST->BTH
// (rides R1, R3, R5, 23 Oct, 142.2 s); BTH<->MLE is scaled from it by track length, so it is rougher.
export const SECTIONS: Section[] = [
  { key: 'EB LST->BTH', from: 'LST', to: 'BTH', lengthM: 2270, usualRunS: 142, usualNote: 'usual run time from rides R1, R3, R5 (23 Oct)' },
  { key: 'EB BTH->MLE', from: 'BTH', to: 'MLE', lengthM: 1640, usualRunS: 103, usualNote: 'usual run time scaled by track length (rough)' },
  { key: 'WB MLE->BTH', from: 'MLE', to: 'BTH', lengthM: 1640, usualRunS: 103, usualNote: 'usual run time scaled by track length (rough)' },
  { key: 'WB BTH->LST', from: 'BTH', to: 'LST', lengthM: 2270, usualRunS: 142, usualNote: 'usual run time from eastbound rides (rough)' },
];
export const nextSection = (s: Section): Section => SECTIONS.find((x) => x.from === s.to && x.key[0] === s.key[0]) ?? s;
export const reverseSection = (s: Section): Section => SECTIONS.find((x) => x.from === s.to && x.to === s.from) ?? s;

export type Position = {
  sM: number; // metres from the section's start station
  lo: number | null; // 90 % band (tracker) or rough range (time guess)
  hi: number | null;
  lengthM: number;
  source: 'tracker' | 'time';
};

/** L0 time guess: counts seconds spent moving after the first moving second; "arrived" after a stop late in the section. */
export class TimeGuess {
  movingS = 0;
  departed = false;
  arrived = false;
  readonly section: Section;
  constructor(section: Section) {
    this.section = section;
  }
  step(state: 'moving' | 'stopped' | null): void {
    if (this.arrived || state === null) return;
    if (state === 'moving') {
      this.departed = true;
      this.movingS += 1;
    } else if (this.departed && this.movingS >= 0.6 * this.section.usualRunS) {
      this.arrived = true;
    }
  }
  position(): Position | null {
    if (!this.departed) return null;
    const L = this.section.lengthM;
    const s = this.arrived ? L : Math.min(L, (this.movingS / this.section.usualRunS) * L);
    const spread = this.arrived ? 0 : 0.15 * L;
    return { sM: s, lo: Math.max(0, s - spread), hi: Math.min(L, s + spread), lengthM: L, source: 'time' };
  }
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
/** Reads an optional tracker estimate from the ingest reply: { position: { s_m, length_m, band_lo?, band_hi?, section? } }. */
export function parseServerPosition(reply: unknown, section: Section): Position | null {
  if (!reply || typeof reply !== 'object') return null;
  const p = (reply as { position?: unknown }).position;
  if (!p || typeof p !== 'object') return null;
  const o = p as Record<string, unknown>;
  const s = num(o.s_m), L = num(o.length_m) ?? section.lengthM;
  if (s === null || L <= 0) return null;
  if (typeof o.section === 'string' && o.section !== section.key) return null;
  return { sM: Math.min(L, Math.max(0, s)), lo: num(o.band_lo), hi: num(o.band_hi), lengthM: L, source: 'tracker' };
}

// ---------------------------------------------------------------- drawing helpers (screen space, heading-up)
export type Pt = { x: number; y: number };
/** The line runs from the bottom (start station) to the top (end station) with a gentle S-bend. t in [0, 1]. */
export function routePoint(t: number, w: number, top: number, bottom: number): Pt {
  const y = bottom - (bottom - top) * t;
  const x = w * 0.5 + Math.sin(t * Math.PI * 1.25 + 0.4) * w * 0.12;
  return { x, y };
}
export function routePolyline(w: number, top: number, bottom: number, t0 = 0, t1 = 1, n = 48): string {
  const out: string[] = [];
  for (let i = 0; i <= n; i++) {
    const p = routePoint(t0 + ((t1 - t0) * i) / n, w, top, bottom);
    out.push(`${p.x.toFixed(1)},${p.y.toFixed(1)}`);
  }
  return out.join(' ');
}

export type Block = { x: number; y: number; w: number; d: number; h: number };
/** Generated city blocks (not real buildings), kept clear of the line, sorted back to front for an oblique view. */
export function cityBlocks(w: number, hgt: number, top: number, bottom: number, seed = 7): Block[] {
  let s = seed;
  const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const out: Block[] = [];
  for (let y = 10; y < hgt + 40; y += 44) {
    for (let x = -10; x < w + 10; x += 46) {
      if (rnd() < 0.15) continue;
      const bw = 26 + rnd() * 14, bd = 14 + rnd() * 8, bh = 6 + rnd() ** 2 * 30;
      const bx = x + (rnd() - 0.5) * 6, by = y + (rnd() - 0.5) * 6;
      const t = Math.min(1, Math.max(0, (bottom - by) / Math.max(1, bottom - top)));
      const rx = routePoint(t, w, top, bottom).x;
      if (bx < rx + 30 && bx + bw > rx - 30) continue;
      out.push({ x: bx, y: by, w: bw, d: bd, h: bh });
    }
  }
  return out.sort((a, b) => a.y - b.y);
}

export const fmtM = (m: number): string => `${Math.round(m).toLocaleString('en-GB')} m`;
export const fmtClock = (s: number): string => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
