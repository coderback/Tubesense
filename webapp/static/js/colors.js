// Noise colour scale: the upper 85 % of inferno (same stops as scripts/demo.py renders), linear RGB interpolation.
const STOPS = [
  [0.00, "#420A68"], [0.12, "#6A176E"], [0.25, "#932667"], [0.37, "#BC3754"], [0.50, "#DD513A"],
  [0.62, "#F37819"], [0.75, "#FCA50A"], [0.87, "#F6D746"], [1.00, "#FCFFA4"],
].map(([t, hex]) => [t, [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))]);

export const NOISE_STOPS_CSS = STOPS.map(([t, c]) => `rgb(${c.join(",")}) ${(t * 100).toFixed(0)}%`).join(", ");

export function noiseColor(v, vmin, vmax) {
  if (v === null || v === undefined || !isFinite(v)) return null;
  const t = Math.min(1, Math.max(0, (v - vmin) / Math.max(1e-9, vmax - vmin)));
  for (let i = 1; i < STOPS.length; i++) {
    if (t <= STOPS[i][0]) {
      const [t0, c0] = STOPS[i - 1], [t1, c1] = STOPS[i];
      const f = (t - t0) / (t1 - t0);
      return `rgb(${c0.map((x, k) => Math.round(x + (c1[k] - x) * f)).join(",")})`;
    }
  }
  return `rgb(${STOPS[STOPS.length - 1][1].join(",")})`;
}
