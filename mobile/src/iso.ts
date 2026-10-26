// Isometric 3D look for the capture app's map (design v2, same camera as the web app's three.js map). Pure functions,
// no React, so they can be tested with plain node. React Native has no WebGL in Expo Go, so the city is projected here
// and drawn with react-native-svg.
//
// World: metres. v runs along the section (start station at v = 0, end station at v = L), u is to the right of the line,
// z is up. The line itself is schematic (a gentle S-bend), and the buildings are generated, not real.

export type Pt = { x: number; y: number };
export type Rect = { left: number; top: number; right: number; bottom: number };

export const YAW_DEG = -28; // camera turned so the line runs up and slightly right, like the web map
export const ELEV_DEG = 50; // camera looking down at 50 degrees

export type Cam = { k: number; sinF: number; cosF: number; sinE: number; cosE: number };
export function camera(k: number, yawDeg = YAW_DEG, elevDeg = ELEV_DEG): Cam {
  const f = (yawDeg * Math.PI) / 180, e = (elevDeg * Math.PI) / 180;
  return { k, sinF: Math.sin(f), cosF: Math.cos(f), sinE: Math.sin(e), cosE: Math.cos(e) };
}
/** Depth along the view: larger is further from the viewer (drawn first). */
export const depth = (c: Cam, u: number, v: number) => u * c.sinF + v * c.cosF;
/** World -> screen, with the world origin at screen (0, 0). Shift the result to move the camera. */
export function project(c: Cam, u: number, v: number, z = 0): Pt {
  const xr = u * c.cosF - v * c.sinF;
  const yr = depth(c, u, v);
  return { x: c.k * xr, y: -c.k * (yr * c.sinE + z * c.cosE) };
}

// ---------------------------------------------------------------- the line
/** Lateral offset of the line at v (metres). Starts and ends on u = 0. */
export function lineU(v: number, L: number): number {
  const t = v / L;
  return 0.1 * L * Math.sin(t * Math.PI * 1.25) * Math.sin(t * Math.PI);
}
export function linePoints(L: number, s0 = 0, s1 = L, n = 60): { u: number; v: number }[] {
  const out: { u: number; v: number }[] = [];
  for (let i = 0; i <= n; i++) {
    const v = s0 + ((s1 - s0) * i) / n;
    out.push({ u: lineU(v, L), v });
  }
  return out;
}
export const ptsString = (pts: Pt[]) => pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');

// ---------------------------------------------------------------- the city
export type Box = { u: number; v: number; a: number; b: number; h: number; d: number }; // centre, half sizes, height, depth
/** Generated blocks around the line (not real buildings), clear of it, sorted far to near for painting. */
export function cityBoxes(L: number, c: Cam, seed = 11, cell = 84, reach = 760, clear = 46): Box[] {
  let s = seed;
  const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const out: Box[] = [];
  for (let v = -420; v <= L + 420; v += cell) {
    const mid = lineU(Math.min(L, Math.max(0, v)), L);
    for (let u = mid - reach; u <= mid + reach; u += cell) {
      if (rnd() < 0.14) continue;
      const a = cell * (0.22 + rnd() * 0.16), b = cell * (0.22 + rnd() * 0.16);
      const cu = u + (rnd() - 0.5) * cell * 0.2, cv = v + (rnd() - 0.5) * cell * 0.2;
      const near = Math.abs(rnd() - 0.5) < 0.18;
      const h = 8 + rnd() ** 2 * (near ? 70 : 38);
      if (distToLine(cu, cv, L) < clear + Math.max(a, b)) continue;
      out.push({ u: cu, v: cv, a, b, h, d: depth(c, cu, cv) });
    }
  }
  return out.sort((p, q) => q.d - p.d);
}
export function distToLine(u: number, v: number, L: number): number {
  if (v < 0) return Math.hypot(u, v);
  if (v > L) return Math.hypot(u, v - L);
  return Math.abs(u - lineU(v, L)); // the bend is gentle, so the lateral gap is close enough
}

export type BoxFaces = { top: Pt[]; sideA: Pt[]; sideB: Pt[] };
/** The top and the two side faces the camera can see. Side A faces along u, side B along v. */
export function boxFaces(c: Cam, bx: Box): BoxFaces {
  const su = c.sinF < 0 ? 1 : -1; // the u face whose normal points towards the viewer
  const sv = c.cosF > 0 ? -1 : 1;
  const P = (du: number, dv: number, z: number) => project(c, bx.u + du * bx.a, bx.v + dv * bx.b, z);
  const top = [P(-1, -1, bx.h), P(1, -1, bx.h), P(1, 1, bx.h), P(-1, 1, bx.h)];
  const sideA = [P(su, -1, 0), P(su, 1, 0), P(su, 1, bx.h), P(su, -1, bx.h)];
  const sideB = [P(-1, sv, 0), P(1, sv, 0), P(1, sv, bx.h), P(-1, sv, bx.h)];
  return { top, sideA, sideB };
}
/** Soft shadow on the ground: hull of the footprint and the footprint pushed away from the light. */
export function boxShadow(c: Cam, bx: Box, lu = 0.55, lv = 0.35): Pt[] {
  const pts: Pt[] = [];
  for (const [du, dv] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
    const u = bx.u + du * bx.a, v = bx.v + dv * bx.b;
    pts.push(project(c, u, v), project(c, u + lu * bx.h, v + lv * bx.h));
  }
  return hull(pts);
}
export function hull(points: Pt[]): Pt[] {
  const p = [...points].sort((a, b) => a.x - b.x || a.y - b.y);
  const cross = (o: Pt, a: Pt, b: Pt) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lower: Pt[] = [], upper: Pt[] = [];
  for (const q of p) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0) lower.pop();
    lower.push(q);
  }
  for (let i = p.length - 1; i >= 0; i--) {
    const q = p[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0) upper.pop();
    upper.push(q);
  }
  return [...lower.slice(0, -1), ...upper.slice(0, -1)];
}
export const polyPath = (pts: Pt[]) => `M${pts.map((p) => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join('L')}Z`;

/**
 * Camera turn for following the train heading-up: the line's local direction points up the screen, kept a little off
 * vertical (`offsetDeg`) so the buildings still show two sides. Rounded to `stepDeg` so the city is re-projected rarely.
 */
export function headingYaw(s: number, L: number, offsetDeg = -20, stepDeg = 4): number {
  const ds = 5, a = Math.max(0, Math.min(L - ds, s));
  const deg = (Math.atan2(lineU(a + ds, L) - lineU(a, L), ds) * 180) / Math.PI;
  return Math.round((deg + offsetDeg) / stepDeg) * stepDeg;
}

// ---------------------------------------------------------------- framing
/** Scale and shift that fit the whole section (both stations with a margin) into the rectangle. */
export function fitSection(L: number, r: Rect, marginM = 180): { k: number; dx: number; dy: number } {
  const unit = camera(1);
  const pts = linePoints(L, -marginM, L + marginM, 40).map((p) => project(unit, p.u, p.v));
  for (const side of [-1, 1]) pts.push(project(unit, side * marginM, L / 2));
  const minX = Math.min(...pts.map((p) => p.x)), maxX = Math.max(...pts.map((p) => p.x));
  const minY = Math.min(...pts.map((p) => p.y)), maxY = Math.max(...pts.map((p) => p.y));
  const k = Math.min((r.right - r.left) / (maxX - minX), (r.bottom - r.top) / (maxY - minY));
  return { k, dx: (r.left + r.right) / 2 - (k * (minX + maxX)) / 2, dy: (r.top + r.bottom) / 2 - (k * (minY + maxY)) / 2 };
}
/** Scale that shows about `spanM` metres of line from the bottom to the top of the rectangle, for following the train. */
export const followScale = (r: Rect, spanM = 1000) => (r.bottom - r.top) / (spanM * Math.sin((ELEV_DEG * Math.PI) / 180));

// ---------------------------------------------------------------- noise colours (shared with the web app's noise map)
export const NOISE_STOPS = ['#C9C6F7', '#9C8CF2', '#7B5CE6', '#C84AB0', '#F06A4A', '#F99A2E', '#FFC53D'];
const hex = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
/** t in [0, 1]: quiet (lilac) to loud (amber). */
export function noiseColor(t: number): string {
  const x = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0)) * (NOISE_STOPS.length - 1);
  const i = Math.min(NOISE_STOPS.length - 2, Math.floor(x)), f = x - i;
  const a = hex(NOISE_STOPS[i]), b = hex(NOISE_STOPS[i + 1]);
  return `#${a.map((v, j) => Math.round(v + (b[j] - v) * f).toString(16).padStart(2, '0')).join('')}`;
}
/**
 * Where a sound level sits on the colour scale. dB(A) when calibrated (60-100 dB(A)); otherwise the phone's own A-weighted
 * dBFS, which is only relative (-50 to -10 dBFS), so colours compare places within a ride, not with a meter.
 */
export const noiseT = (db: number, calibrated: boolean) => (calibrated ? (db - 60) / 40 : (db + 50) / 40);
