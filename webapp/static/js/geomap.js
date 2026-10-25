// GeoMap: a schematic of where the section lies. Straight chords between station coordinates; the train is drawn at
// fraction (distance / section length) along the active chord. It is NOT a GPS position. Inline SVG always works;
// when online a Leaflet + OpenStreetMap basemap, darkened with a CSS filter is drawn behind the same chords (with an automatic fall back to SVG).
import { h, s, store, fmtNum } from "./util.js";
import { noiseColor } from "./colors.js";
import { addClientEgress } from "./state.js";

const LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/";
const SRI = { css: "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=", js: "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" };
export const GEO_CAPTION = "Schematic: straight chords between station coordinates (TfL lineStrings have no intermediate vertices). Position along the chord = distance / section length; not a GPS position.";

let leafletP = null;
function loadLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  if (leafletP) return leafletP;
  leafletP = new Promise((resolve, reject) => {
    const css = h("link", { rel: "stylesheet", href: `${LEAFLET}leaflet.css`, integrity: SRI.css, crossOrigin: "anonymous", referrerPolicy: "no-referrer" });
    const js = h("script", { src: `${LEAFLET}leaflet.js`, integrity: SRI.js, crossOrigin: "anonymous", referrerPolicy: "no-referrer" });
    const timer = setTimeout(() => { reject(new Error("timeout")); }, 4000);
    js.onload = () => { clearTimeout(timer); window.L ? resolve(window.L) : reject(new Error("no L")); };
    js.onerror = () => { clearTimeout(timer); reject(new Error("load failed")); };
    css.onerror = () => reject(new Error("css failed"));
    document.head.append(css, js);
    addClientEgress("cdnjs (Leaflet 1.9.4)", "JavaScript and CSS library files for the optional basemap (no ride data)", "cdnjs.cloudflare.com");
  });
  leafletP.catch(() => { leafletP = null; });
  return leafletP;
}

export class GeoMap {
  constructor() {
    this.pref = store.get("basemap", true) !== false;
    this.init = null; this.paints = []; this.pos = null; this.mode = "svg"; this.tileErrors = 0; this.map = null;
    this.stage = h("div", { class: "geomap__stage" });
    this.noteEl = h("div", { class: "geomap__note help", "aria-live": "off" });
    this.toggle = h("button", { type: "button", class: "btn btn--small btn--secondary", "aria-pressed": String(this.pref), onclick: () => this.setPref(!this.pref) }, "");
    this.el = h("div", { class: "geomap" }, this.stage, this.noteEl, h("p", { class: "geomap__caption" }, GEO_CAPTION));
    this.syncToggle();
    this.ro = new ResizeObserver(() => { if (this.mode === "svg") this.drawSvg(); else this.map?.invalidateSize(); });
    this.ro.observe(this.stage);
    this.drawSvg();
  }
  destroy() { this.ro.disconnect(); try { this.map?.remove(); } catch { /* already gone */ } }
  syncToggle() { this.toggle.textContent = `Basemap: ${this.pref ? "on" : "off"}`; this.toggle.setAttribute("aria-pressed", String(this.pref)); }
  setPref(v) { this.pref = v; store.set("basemap", v); this.syncToggle(); this.draw(); }

  setInit(init) { this.init = init; this.paints = []; this.pos = null; this.draw(); }
  reset() { this.init = null; this.paints = []; this.pos = null; this.draw(); }
  addPaint(p) { this.paints.push(p); if (this.mode === "svg") this.svgPaint(p); else this.lfPaint(p); }
  update(pos) { this.pos = pos; if (this.mode === "svg") this.svgDynamic(); else this.lfDynamic(); }

  // ---- geometry
  stations() {
    const st = this.init?.stations || {};
    return { a: st[this.init?.from], b: st[this.init?.to], all: Object.entries(st).map(([id, v]) => ({ id, ...v })) };
  }
  frac(m) { return Math.min(1, Math.max(0, m / (this.init?.length_m || 1))); }

  draw() {
    if (!this.init || !this.pref) { this.leave(""); return; }
    if (!navigator.onLine) { this.leave("Offline: showing the schematic without a basemap."); return; }
    if (this.mode === "leaflet") { this.lfBuild(); return; }
    this.note("Loading basemap…");
    this.drawSvg();
    loadLeaflet().then((L) => { if (this.pref && this.init && navigator.onLine) this.enter(L); else this.note(""); })
      .catch(() => this.leave("Basemap unavailable (offline or blocked): showing the schematic."));
  }
  note(t) { this.noteEl.textContent = t; }
  leave(why) {
    if (this.map) { try { this.map.remove(); } catch { /* ok */ } this.map = null; }
    this.mode = "svg"; this.note(why || ""); this.drawSvg();
  }
  enter(L) {
    this.mode = "leaflet"; this.note("");
    this.stage.replaceChildren();
    const el = h("div", { class: "geomap__leaflet" });
    this.stage.append(el);
    const st = this.stations();
    const pts = st.all.map((x) => [x.lat, x.lon]);
    this.map = L.map(el, { zoomControl: false, attributionControl: true, scrollWheelZoom: false, dragging: true, keyboard: false, zoomSnap: 0.25 });
    this.map.fitBounds(pts, { paddingTopLeft: [28, 48], paddingBottomRight: [28, 40] });   // room for the labels
    this.tileErrors = 0;
    const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 17, attribution: "© OpenStreetMap contributors" }).addTo(this.map);
    tiles.on("tileload", () => addClientEgress("OpenStreetMap map tiles", "Map tile requests: they reveal the viewed area of east London, no ride data", "tile.openstreetmap.org"));
    tiles.on("tileerror", () => { if (++this.tileErrors >= 3) this.leave("Map tiles failed to load: showing the schematic."); });
    this.L = L; this.lf = {};
    this.lfBuild();
  }
  lfBuild() {
    const L = this.L; if (!L || !this.map) return;
    if (this.lf.group) this.lf.group.remove();
    const st = this.stations(), g = L.layerGroup().addTo(this.map); this.lf = { group: g };
    const ll = (x) => [x.lat, x.lon];
    const order = st.all;
    for (let i = 0; i < order.length - 1; i++) {
      const active = order[i].id === this.init.from && order[i + 1].id === this.init.to || order[i].id === this.init.to && order[i + 1].id === this.init.from;
      L.polyline([ll(order[i]), ll(order[i + 1])], { color: active ? "#E9F1FF" : "#6F84AD", weight: active ? 5 : 3, opacity: active ? 0.9 : 0.7 }).addTo(g);
    }
    const east = [...order].sort((a, b) => b.lon - a.lon)[0], west = [...order].sort((a, b) => a.lon - b.lon)[0];
    for (const x of order) {   // easternmost label below its dot, westernmost to its right, the others above: Bethnal Green and Mile End never overlap
      const dir = x === east ? "bottom" : x === west ? "right" : "top", off = dir === "right" ? [8, 0] : dir === "bottom" ? [0, 6] : [0, -6];
      L.circleMarker(ll(x), { radius: 6, color: "#E9F1FF", weight: 2, fillColor: "#0E2E68", fillOpacity: 1 })
        .bindTooltip(x.name, { permanent: true, direction: dir, offset: off, className: "geo-tip" }).addTo(g);
    }
    this.lf.band = L.polyline([], { color: "#4FC3F7", weight: 9, opacity: 0.5 }).addTo(g);
    this.lf.dot = L.circleMarker(ll(st.a), { radius: 8, color: "#fff", weight: 2, fillColor: "#4FC3F7", fillOpacity: 1 });
    this.lf.paint = L.layerGroup().addTo(g);
    this.paints.forEach((p) => this.lfPaint(p));
    this.lfDynamic();
  }
  at(m, off = 0) {
    const { a, b } = this.stations(); const f = this.frac(m);
    const lat = a.lat + (b.lat - a.lat) * f, lon = a.lon + (b.lon - a.lon) * f;
    if (!off) return [lat, lon];
    const dx = (b.lon - a.lon) * Math.cos(51.522 * Math.PI / 180), dy = b.lat - a.lat, n = Math.hypot(dx, dy) || 1;
    return [lat + (dx / n) * off, lon - (dy / n) * off / Math.cos(51.522 * Math.PI / 180)];
  }
  lfPaint(p) {
    if (!this.lf.paint || !this.init) return;
    const c = noiseColor(p.level, this.init.map.vmin, this.init.map.vmax);
    if (c) this.L.circleMarker(this.at(p.s_m, 0.0004), { radius: 3, stroke: false, fillColor: c, fillOpacity: 1 }).addTo(this.lf.paint);
  }
  lfDynamic() {
    if (!this.lf?.dot || !this.init) return;
    const p = this.pos;
    if (!p || p.s === null || p.s === undefined) { this.lf.dot.remove(); this.lf.band.setLatLngs([]); return; }
    if (!this.map.hasLayer(this.lf.dot)) this.lf.dot.addTo(this.lf.group);
    this.lf.dot.setLatLng(this.at(p.s));
    this.lf.band.setLatLngs(p.lo !== null && p.hi !== null ? [this.at(p.lo), this.at(p.hi)] : []);
  }

  // ---- inline SVG renderer
  drawSvg() {
    const W = Math.max(260, Math.round(this.stage.clientWidth || 480)), H = Math.max(200, Math.round(this.stage.clientHeight || 240));
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", class: "geomap__svg",
      "aria-label": this.init ? `Schematic map: ${this.init.from_name} to ${this.init.to_name} along a straight chord` : "Schematic map, waiting for a ride" });
    this.stage.replaceChildren(svg);
    this.svg = svg;
    if (!this.init) { svg.append(s("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "sm-tick" }, "Schematic map appears when a ride starts")); return; }
    const st = this.stations().all, lat0 = 51.522, k = Math.cos(lat0 * Math.PI / 180);
    const raw = st.map((x) => ({ ...x, X: (x.lon - st[0].lon) * k, Y: -(x.lat - lat0) }));
    const xs = raw.map((p) => p.X), ys = raw.map((p) => p.Y);
    const pad = 44, bw = Math.max(1e-9, Math.max(...xs) - Math.min(...xs)), bh = Math.max(1e-9, Math.max(...ys) - Math.min(...ys));
    const sc = Math.min((W - 2 * pad) / bw, (H - 2 * pad) / bh);
    const ox = (W - bw * sc) / 2 - Math.min(...xs) * sc, oy = (H - bh * sc) / 2 - Math.min(...ys) * sc;
    this.pt = (x) => ({ x: ox + (x.lon - st[0].lon) * k * sc, y: oy + -(x.lat - lat0) * sc });
    this.P = Object.fromEntries(raw.map((p) => [p.id, this.pt(p)]));
    for (let i = 0; i < raw.length - 1; i++) {
      const a = this.P[raw[i].id], b = this.P[raw[i + 1].id];
      const active = [raw[i].id, raw[i + 1].id].sort().join() === [this.init.from, this.init.to].sort().join();
      svg.append(s("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: active ? "var(--ink)" : "var(--ink-3)", "stroke-width": active ? 4 : 2.5, "stroke-linecap": "round", "stroke-opacity": active ? 0.9 : 0.7 }));
    }
    // labels never collide (Bethnal Green and Mile End are close at phone width): the westernmost station's label runs
    // east from it, the easternmost's runs west and sits below its dot, the ones between are centred above
    const byX = [...raw].sort((a, b) => a.X - b.X);
    for (const p of raw) {
      const q = this.P[p.id], i = byX.indexOf(p), first = i === 0, last = i === byX.length - 1 && byX.length > 1;
      const lab = first ? { x: q.x - 8, y: q.y + 24, a: "start" } : last ? { x: q.x + 8, y: q.y + 24, a: "end" } : { x: q.x, y: q.y - 14, a: "middle" };
      svg.append(s("circle", { cx: q.x, cy: q.y, r: 6, fill: "var(--navy)", stroke: "var(--ink)", "stroke-width": 2 }),
        s("text", { x: lab.x, y: lab.y, "text-anchor": lab.a, class: "sm-name" }, p.name));
    }
    this.gp = s("g"); svg.append(this.gp);
    this.sband = s("line", { stroke: "var(--cyan)", "stroke-width": 10, "stroke-opacity": 0.45, "stroke-linecap": "round", visibility: "hidden" });
    this.sdot = s("circle", { r: 8, fill: "var(--cyan)", stroke: "#fff", "stroke-width": 2, visibility: "hidden" });
    svg.append(this.sband, this.sdot);
    this.paints.forEach((p) => this.svgPaint(p));
    this.svgDynamic();
  }
  svgAt(m, off = 0) {
    const a = this.P[this.init.from], b = this.P[this.init.to], f = this.frac(m);
    const x = a.x + (b.x - a.x) * f, y = a.y + (b.y - a.y) * f;
    const dx = b.x - a.x, dy = b.y - a.y, n = Math.hypot(dx, dy) || 1;
    return { x: x - (dy / n) * off, y: y + (dx / n) * off };
  }
  svgPaint(p) {
    if (!this.gp || !this.P) return;
    const c = noiseColor(p.level, this.init.map.vmin, this.init.map.vmax); if (!c) return;
    const q = this.svgAt(p.s_m, 7);
    this.gp.append(s("circle", { cx: q.x, cy: q.y, r: 2.6, fill: c }));
  }
  svgDynamic() {
    if (!this.sdot || !this.P) return;
    const p = this.pos;
    if (!p || p.s === null || p.s === undefined) { this.sdot.setAttribute("visibility", "hidden"); this.sband.setAttribute("visibility", "hidden"); return; }
    const q = this.svgAt(p.s); this.sdot.setAttribute("cx", q.x); this.sdot.setAttribute("cy", q.y); this.sdot.setAttribute("visibility", "visible");
    if (p.lo !== null && p.hi !== null) {
      const a = this.svgAt(p.lo), b = this.svgAt(p.hi);
      this.sband.setAttribute("x1", a.x); this.sband.setAttribute("y1", a.y); this.sband.setAttribute("x2", b.x); this.sband.setAttribute("y2", b.y); this.sband.setAttribute("visibility", "visible");
    } else this.sband.setAttribute("visibility", "hidden");
  }
}
export { fmtNum };
