// YawChart: phone yaw rate (live) against the map's yaw at the tracker's estimate, deg/s, + = left. Canvas, DPR aware.
import { h, rafThrottle } from "./util.js";

const C = { line: "#23314F", ink3: "#8398C0", ink2: "#A9C2EC", phone: "#FFB74D", map: "rgba(255,255,255,.82)", stop: "rgba(242,179,61,.16)", head: "rgba(79,195,247,.6)" };

export class YawChart {
  constructor() {
    this.canvas = h("canvas", { class: "yaw__canvas", role: "img", "aria-label": "Yaw rate chart: phone yaw rate against map yaw at the estimate" });
    this.el = h("div", { class: "yaw" }, this.canvas,
      h("div", { class: "yaw__legend" },
        h("span", null, h("i", { class: "sw", style: { background: C.phone } }), "phone yaw rate (live)"),
        h("span", null, h("i", { class: "sw", style: { background: "#fff" } }), "map yaw at the estimate"),
        h("span", { class: "muted" }, "deg/s, + = left")));
    this.data = []; this.range = [-8, 165]; this.stop = null; this.draw = rafThrottle(() => this.paint());
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(this.el);
  }
  destroy() { this.ro.disconnect(); }
  setInit(init) { this.range = init.t_range || [-8, 165]; this.data = []; this.stop = null; this.draw(); }
  reset() { this.data = []; this.stop = null; this.draw(); }
  add(t) {
    this.data.push(t);
    if (t.in_synthetic_stop) { if (!this.stop) this.stop = { a: t.t, b: t.t }; this.stop.b = t.t; this.stop.open = true; }
    else if (this.stop?.open) { this.stop.open = false; }
    this.draw();
  }
  load(ticks) { this.data = []; this.stop = null; ticks.forEach((t) => this.add(t)); }

  paint() {
    const cv = this.canvas, W = Math.max(240, Math.round(this.el.clientWidth)), H = W < 560 ? 110 : 150, dpr = window.devicePixelRatio || 1;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); cv.style.width = W + "px"; cv.style.height = H + "px"; }
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
    const L = 34, R = 8, T = 8, B = 20, pw = W - L - R, ph = H - T - B, [t0, t1] = this.range;
    const X = (t) => L + ((t - t0) / (t1 - t0)) * pw, Y = (v) => T + ph * (1 - (Math.max(-4, Math.min(4, v)) + 4) / 8);
    g.font = "11px 'JetBrains Mono', ui-monospace, monospace"; g.fillStyle = C.ink3; g.strokeStyle = C.line; g.lineWidth = 1; g.textAlign = "right";
    for (const v of [-4, -2, 0, 2, 4]) { g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(L + pw, Y(v)); g.globalAlpha = v === 0 ? 1 : 0.5; g.stroke(); g.globalAlpha = 1; g.fillText(String(v), L - 6, Y(v) + 4); }
    g.textAlign = "center";
    const step = t1 - t0 > 120 ? 30 : 20;
    for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) { g.fillText(`${t}s`, X(t), H - 5); }
    if (this.stop) { g.fillStyle = C.stop; g.fillRect(X(this.stop.a), T, Math.max(2, X(this.stop.b) - X(this.stop.a)), ph); }
    const line = (key, color, w) => {
      g.strokeStyle = color; g.lineWidth = w; g.beginPath(); let pen = false;
      for (const d of this.data) {
        const v = d[key];
        if (v === null || v === undefined) { pen = false; continue; }
        if (!pen) { g.moveTo(X(d.t), Y(v)); pen = true; } else g.lineTo(X(d.t), Y(v));
      }
      g.stroke();
    };
    line("map_yaw", C.map, 1.4); line("yaw", C.phone, 1.6);
    const last = this.data[this.data.length - 1];
    if (last) { g.strokeStyle = C.head; g.lineWidth = 1; g.beginPath(); g.moveTo(X(last.t), T); g.lineTo(X(last.t), T + ph); g.stroke(); }
  }
}
