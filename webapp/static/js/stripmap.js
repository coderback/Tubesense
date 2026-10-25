// StripMap: the section as a horizontal strip. Rows: noise painted live this ride, tracker band + dot (+ reference ring),
// map noise bins (leave-one-out map), curves (landmarks), axis, colour legend. Drawn at real pixel width so text never stretches.
import { h, s, fmtNum, reduceMotion, fixArrow } from "./util.js";
import { noiseColor, NOISE_STOPS_CSS } from "./colors.js";

const NS = "http://www.w3.org/2000/svg";
const STOPS = NOISE_STOPS_CSS.split(", ").map((x) => { const [c, p] = [x.slice(0, x.lastIndexOf(" ")), x.slice(x.lastIndexOf(" ") + 1)]; return [c, p]; });

export class StripMap {
  constructor() {
    this.el = h("div", { class: "stripmap" });
    this.svgHost = h("div", { class: "stripmap__svg" });
    this.overlay = h("div", { class: "stripmap__overlay", hidden: true, role: "status" });
    this.empty = h("div", { class: "stripmap__empty" }, "The strip fills in when a ride starts: map noise, curves, then the tracker.");
    this.el.append(this.svgHost, this.overlay);
    this.init = null; this.paints = []; this.showRef = true;
    this.cur = { s: null, lo: null, hi: null, ref: null }; this.tgt = { s: null, lo: null, hi: null, ref: null };
    this.raf = 0; this.lastFrame = 0; this.W = 0;
    this.ro = new ResizeObserver(() => { const w = Math.round(this.el.clientWidth); if (w && Math.abs(w - this.W) > 1) this.layout(); });
    this.ro.observe(this.el);
    this.layout();
  }
  destroy() { this.ro.disconnect(); cancelAnimationFrame(this.raf); }

  setInit(init) { this.init = init; this.paints = []; this.cur = { s: null, lo: null, hi: null, ref: null }; this.tgt = { ...this.cur }; this.layout(); }
  reset() { this.init = null; this.paints = []; this.cur = { s: null, lo: null, hi: null, ref: null }; this.tgt = { ...this.cur }; this.setSynthetic(null); this.layout(); }
  setSynthetic(text) { this.overlay.hidden = !text; this.overlay.textContent = text || ""; }
  setShowRef(v) { this.showRef = v; this.applyDynamic(); }

  addPaint(p) {
    this.paints.push(p);
    if (this.gPaint) this.paintRect(p);
  }
  setTarget(t) {
    const prev = this.tgt;
    this.tgt = { s: t.s, lo: t.lo, hi: t.hi, ref: t.ref };
    if (reduceMotion() || prev.s === null || t.s === null) { this.cur = { ...this.tgt }; this.applyDynamic(); return; }
    if (!this.raf) { this.lastFrame = performance.now(); this.raf = requestAnimationFrame((n) => this.frame(n)); }
  }
  frame(now) {
    const dt = Math.min(0.1, (now - this.lastFrame) / 1000); this.lastFrame = now;
    const k = 1 - Math.exp(-dt / 0.09);
    let moving = false;
    for (const key of ["s", "lo", "hi", "ref"]) {
      const t = this.tgt[key], c = this.cur[key];
      if (t === null || t === undefined) { this.cur[key] = t; continue; }
      if (c === null || c === undefined) { this.cur[key] = t; continue; }
      const d = t - c;
      if (Math.abs(d) > 0.05) { this.cur[key] = c + d * k; moving = true; } else this.cur[key] = t;
    }
    this.applyDynamic();
    this.raf = moving ? requestAnimationFrame((n) => this.frame(n)) : 0;
  }

  x(m) { return this.L0 + (m / this.len) * this.plotW; }

  layout() {
    const W = Math.max(280, Math.round(this.el.clientWidth || 800)); this.W = W;
    const compact = W < 560;
    const init = this.init;
    this.len = init?.length_m || 2270;
    this.L0 = compact ? 50 : 78; this.plotW = W - this.L0 - 16;
    const R = compact
      ? { names: 12, paint: [24, 12], track: [40, 26], map: [72, 24], curves: [100, 6], axis: 112, legend: 142, H: 192 }
      : { names: 14, paint: [26, 16], track: [50, 26], map: [84, 32], curves: [122, 8], axis: 138, legend: 168, H: 204 };
    this.R = R;
    const svg = s("svg", { viewBox: `0 0 ${W} ${R.H}`, width: W, height: R.H, role: "img", class: "stripmap__svg-el",
      "aria-label": init ? `Strip map of ${init.from_name} to ${init.to_name}, ${Math.round(this.len)} metres. Rows: noise measured this ride, tracker position and 90 percent band, map noise, curves.` : "Strip map, waiting for a ride" });
    const defs = s("defs", null,
      s("pattern", { id: "hatch", width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
        s("rect", { width: 6, height: 6, fill: "var(--surface-2)" }), s("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: "var(--ink-3)", "stroke-width": 1.5 })),
      s("linearGradient", { id: "noise-grad", x1: 0, x2: 1, y1: 0, y2: 0 }, STOPS.map(([c, p]) => s("stop", { offset: p, "stop-color": c }))));
    svg.append(defs);
    const rowLabel = (txt, y, hgt) => s("text", { x: this.L0 - 8, y: y + hgt / 2 + 4, "text-anchor": "end", class: "sm-label" }, txt);
    svg.append(rowLabel(compact ? "live" : "this ride", R.paint[0], R.paint[1]), rowLabel(compact ? "track" : "tracker", R.track[0], R.track[1]),
      rowLabel("map", R.map[0], R.map[1]), rowLabel("curves", R.curves[0] - 2, R.curves[1]));
    // backgrounds for rows
    for (const r of [R.paint, R.map]) svg.append(s("rect", { x: this.L0, y: r[0], width: this.plotW, height: r[1], fill: "var(--surface-2)", rx: 3 }));
    svg.append(s("line", { x1: this.L0, x2: this.L0 + this.plotW, y1: R.track[0] + R.track[1] / 2, y2: R.track[0] + R.track[1] / 2, stroke: "var(--line)", "stroke-width": 2 }));
    if (init) {
      const map = init.map || {};
      const edges = map.bin_edges_m || [], db = map.noise_db || [];
      const g = s("g", { class: "sm-bins" });
      for (let i = 0; i < db.length; i++) {
        const x0 = this.x(edges[i]), x1 = this.x(edges[i + 1]);
        const c = noiseColor(db[i], map.vmin, map.vmax);
        g.append(s("rect", { x: x0, y: R.map[0], width: Math.max(0.5, x1 - x0 + 0.4), height: R.map[1], fill: c || "url(#hatch)" },
          s("title", null, `${Math.round(edges[i])}–${Math.round(edges[i + 1])} m: ${db[i] === null ? "no data in the map" : fmtNum(db[i], 1) + " dBFS(A), relative"}`)));
      }
      svg.append(g);
      const gc = s("g", { class: "sm-curves" });
      for (const [a, b] of map.curves_m || []) gc.append(s("rect", { x: this.x(a), y: R.curves[0], width: Math.max(1.5, this.x(b) - this.x(a)), height: R.curves[1], fill: "var(--ink-2)", rx: 2 }));
      svg.append(gc);
      // axis
      const step = compact ? 500 : 250;
      const ga = s("g", { class: "sm-axis" });
      for (let m = 0; m <= this.len; m += step) {
        if (m !== 0 && this.x(this.len) - this.x(m) < 62) continue;
        ga.append(s("line", { x1: this.x(m), x2: this.x(m), y1: R.axis - 6, y2: R.axis, stroke: "var(--ink-3)" }), s("text", { x: this.x(m), y: R.axis + 12, "text-anchor": "middle", class: "sm-tick" }, String(m)));
      }
      ga.append(s("line", { x1: this.x(this.len), x2: this.x(this.len), y1: R.axis - 6, y2: R.axis, stroke: "var(--ink-3)" }),
        s("text", { x: this.x(this.len), y: R.axis + 12, "text-anchor": "end", class: "sm-tick" }, `${Math.round(this.len)} m`));
      svg.append(ga);
      svg.append(s("text", { x: this.L0, y: R.names, class: "sm-name", "text-anchor": "start" }, init.from_name),
        s("text", { x: this.L0 + this.plotW, y: R.names, class: "sm-name", "text-anchor": "end" }, init.to_name));
      // legend
      const lw = Math.min(220, this.plotW * (compact ? 0.45 : 0.3)), lx = this.L0 + this.plotW - lw;
      // phones: the label gets its own line above the bar (it is wider than the space left of it); desktop: label left of the bar
      const by = compact ? R.legend + 12 : R.legend;
      svg.append(s("rect", { x: lx, y: by, width: lw, height: 9, fill: "url(#noise-grad)", rx: 2 }),
        s("text", { x: lx, y: by + 22, class: "sm-tick", "text-anchor": "start" }, fmtNum(map.vmin, 1)),
        s("text", { x: lx + lw, y: by + 22, class: "sm-tick", "text-anchor": "end" }, fmtNum(map.vmax, 1)),
        compact ? s("text", { x: this.L0, y: R.legend + 6, class: "sm-tick", "text-anchor": "start" }, "map LAeq, dBFS(A), relative")
          : s("text", { x: lx - 8, y: by + 8, class: "sm-tick", "text-anchor": "end" }, "map LAeq, dBFS(A), relative"));
      this.gPaint = s("g", { class: "sm-paint", opacity: init.live_only ? 0.45 : 1 });
      svg.append(this.gPaint);
    } else {
      this.gPaint = null;
      svg.append(s("text", { x: this.L0, y: R.names, class: "sm-name" }, "Waiting for a ride"));
    }
    // dynamic layer (band, ring, dot) on top
    this.band = s("rect", { y: R.track[0] + 2, height: 22, fill: "var(--cyan)", "fill-opacity": 0.3, stroke: "var(--cyan)", "stroke-opacity": 0.8, rx: 3, visibility: "hidden" });
    this.ring = s("circle", { r: 11, fill: "none", stroke: "var(--ref)", "stroke-width": 2.5, cy: R.track[0] + R.track[1] / 2, visibility: "hidden" });
    this.dot = s("circle", { r: 9, fill: "var(--cyan)", stroke: "#fff", "stroke-width": 2, cy: R.track[0] + R.track[1] / 2, visibility: "hidden" });
    svg.append(this.band, this.ring, this.dot);
    this.svgHost.replaceChildren(svg);
    this.paints.forEach((p) => this.paintRect(p));
    this.applyDynamic();
  }

  paintRect(p) {
    const R = this.R, c = noiseColor(p.level, this.init.map.vmin, this.init.map.vmax);
    if (!c) return;
    const w = Math.max(3, this.plotW / this.len * 14);
    this.gPaint.append(s("rect", { x: this.x(p.s_m) - w / 2, y: R.paint[0], width: w, height: R.paint[1], fill: c }));
  }

  applyDynamic() {
    const { s: sm, lo, hi, ref } = this.cur;
    const show = (n, v) => n.setAttribute("visibility", v ? "visible" : "hidden");
    show(this.dot, sm !== null && sm !== undefined);
    if (sm !== null && sm !== undefined) this.dot.setAttribute("cx", this.x(sm));
    const hasBand = lo !== null && lo !== undefined && hi !== null && hi !== undefined;
    show(this.band, hasBand);
    if (hasBand) { const x0 = this.x(Math.min(lo, hi)), x1 = this.x(Math.max(lo, hi)); this.band.setAttribute("x", x0); this.band.setAttribute("width", Math.max(2, x1 - x0)); }
    const hasRef = this.showRef && ref !== null && ref !== undefined;
    show(this.ring, hasRef);
    if (hasRef) this.ring.setAttribute("cx", this.x(ref));
  }
}

export function paintRuleText(rule) {
  if (!rule) return "";
  return `squares: noise this ride is measuring now, painted only where the tracker is confident (lock ≥ ${Math.round(rule.locked * 100)} % within ±${rule.lock_window_m} m and the whole 90 % band narrower than ${Math.round(rule.band_max_m)} m, held ${rule.hold_s} s)`;
}
export { NS, fixArrow };
