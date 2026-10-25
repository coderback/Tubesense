// 3D section map (design v2): a white isometric city with the Central line drawn over it, the track coloured by the
// map's noise bins and the tracker's estimate moving along it. three.js r128 is vendored (static/vendor), so it works
// offline and makes no third-party request. Station positions are real (TfL); the track between them is a smoothed
// schematic line and the buildings are generated, not real footprints. Position = distance / section length, not GPS.
import { h, ensureCss } from "./util.js";
import { noiseColor } from "./colors.js";

const VENDOR = ["vendor/three-r128.min.js", "vendor/three-r128-OrbitControls.js"];
const DEFAULT_STATIONS = {
  LST: { name: "Liverpool Street", lat: 51.517372, lon: -0.083182 },
  BTH: { name: "Bethnal Green", lat: 51.527222, lon: -0.055506 },
  MLE: { name: "Mile End", lat: 51.525122, lon: -0.03364 },
};
const ORDER = ["LST", "BTH", "MLE"];
const THEMES = {
  light: { bg: "#EEF1F8", ground: "#E3E8F2", building: "#FFFFFF", hemiSky: "#FFFFFF", hemiGround: "#9AA6CC", hemi: 0.62, sun: "#FFFFFF", sunI: 0.95,
           ring: "#121735", quiet: "#C9CEE6", glow: "#3D5AFE", glowO: 0.08, train: "#3D5AFE", fogNear: 650, fogFar: 1150 },
  dark: { bg: "#010205", ground: "#04060E", building: "#0B1124", hemiSky: "#AFC0FF", hemiGround: "#05070F", hemi: 0.5, sun: "#8EA2FF", sunI: 0.45,
          ring: "#DCE3FF", quiet: "#2A3459", glow: "#5B7BFF", glowO: 0.25, train: "#5B7BFF", fogNear: 600, fogFar: 1100 },
};

let threeP = null;
function loadScript(src) {
  return new Promise((resolve, reject) => {
    const el = h("script", { src, async: false });
    el.onload = resolve; el.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.append(el);
  });
}
/** Loads the vendored three.js once. Rejects when WebGL is missing or a file fails to load. */
export function loadThree() {
  if (window.THREE?.OrbitControls) return Promise.resolve(window.THREE);
  if (!threeP) {
    threeP = (async () => {
      if (!webglAvailable()) throw new Error("WebGL is not available in this browser");
      if (!window.THREE) await loadScript(VENDOR[0]);
      if (!window.THREE.OrbitControls) await loadScript(VENDOR[1]);
      return window.THREE;
    })();
    threeP.catch(() => { threeP = null; });
  }
  return threeP;
}
export function webglAvailable() {
  try { const c = document.createElement("canvas"); return !!(c.getContext("webgl2") || c.getContext("webgl")); } catch { return false; }
}

/**
 * Mounts the 3D map into `container` (which sets the size). Resolves to a controller, or throws (caller shows a fallback).
 * opts: { theme: "light"|"dark", stations, follow: bool (camera follows the train), interactive: bool, labels: bool }
 */
export async function createMap3D(container, opts = {}) {
  ensureCss("css/map3d.css");
  const THREE = await loadThree();
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const stations = { ...DEFAULT_STATIONS, ...(opts.stations || {}) };
  let theme = THEMES[opts.theme] ? opts.theme : "light";
  let T = THEMES[theme];
  const follow = !!opts.follow;

  // ---- projection: equirectangular around the section, 1 unit = 10 m
  const lat0 = 51.522, lon0 = -0.058, cos0 = Math.cos(lat0 * Math.PI / 180);
  const P = (s) => new THREE.Vector3((s.lon - lon0) * cos0 * 111320 / 10, 0, -(s.lat - lat0) * 110540 / 10);
  const pos = Object.fromEntries(ORDER.map((k) => [k, P(stations[k])]));
  // schematic section curves (gentle bends between real station points)
  const curveLB = new THREE.CatmullRomCurve3([pos.LST, pos.LST.clone().lerp(pos.BTH, 0.25).add(new THREE.Vector3(-2, 0, 3)),
    pos.LST.clone().lerp(pos.BTH, 0.5).add(new THREE.Vector3(-8, 0, -14)), pos.LST.clone().lerp(pos.BTH, 0.78).add(new THREE.Vector3(-4, 0, -6)), pos.BTH], false, "centripetal");
  const curveBM = new THREE.CatmullRomCurve3([pos.BTH, pos.BTH.clone().lerp(pos.MLE, 0.5).add(new THREE.Vector3(0, 0, 6)), pos.MLE], false, "centripetal");
  const SECTIONS = { "LST-BTH": curveLB, "BTH-MLE": curveBM };

  // ---- renderer, scene, camera
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, preserveDrawingBuffer: false });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputEncoding = THREE.sRGBEncoding;
  const canvas = renderer.domElement;
  canvas.className = "map3d__canvas";
  canvas.setAttribute("aria-hidden", "true");
  container.append(canvas);

  const scene = new THREE.Scene();
  applyBackdrop();
  const center = pos.LST.clone().lerp(pos.MLE, 0.5).add(new THREE.Vector3(0, 0, -20));
  const ZOOM = opts.zoom || 1;
  let view = opts.focus && SECTIONS[opts.focus] ? SECTIONS[opts.focus].getPointAt(0.5) : center.clone();
  const OFFSET = follow ? new THREE.Vector3(-390, 340, 220) : new THREE.Vector3(-260, 340, 380);
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 1, 3000);
  camera.position.copy(view).add(OFFSET);
  camera.zoom = ZOOM;
  const controls = new THREE.OrbitControls(camera, canvas);
  controls.target.copy(view);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.42;
  controls.minZoom = 0.6; controls.maxZoom = 4;
  controls.screenSpacePanning = false;
  controls.enabled = opts.interactive !== false;

  const hemi = new THREE.HemisphereLight(T.hemiSky, T.hemiGround, T.hemi);
  const sun = new THREE.DirectionalLight(T.sun, T.sunI);
  sun.position.copy(center).add(new THREE.Vector3(-220, 400, 160));
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -420, right: 420, top: 300, bottom: -300, near: 10, far: 1200 });
  sun.shadow.radius = 4; sun.shadow.bias = -0.0008; sun.shadow.normalBias = 0.6;
  sun.target.position.copy(center);
  scene.add(hemi, sun, sun.target);

  const groundMat = new THREE.MeshLambertMaterial({ color: T.ground });
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(2600, 2000), groundMat);
  ground.rotation.x = -Math.PI / 2; ground.position.copy(center); ground.receiveShadow = true;
  scene.add(ground);

  // ---- generated city blocks (instanced), kept clear of the line corridor
  const corridor = [...curveLB.getSpacedPoints(160), ...curveBM.getSpacedPoints(120)];
  const nearLine = (x, z) => corridor.some((p) => (p.x - x) ** 2 + (p.z - z) ** 2 < 16 * 16);
  let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const boxes = [];
  for (let gx = -340; gx <= 340; gx += 18) {
    for (let gz = -200; gz <= 200; gz += 18) {
      if (rnd() < 0.18) continue;
      const x = center.x + gx + (rnd() - 0.5) * 3, z = center.z + gz + (rnd() - 0.5) * 3;
      if (nearLine(x, z)) continue;
      const tall = rnd() < 0.08;
      boxes.push([x, z, 10 + rnd() * 5, 10 + rnd() * 5, tall ? 26 + rnd() * 28 : 4 + rnd() ** 2 * 12]);
    }
  }
  const boxGeo = new THREE.BoxGeometry(1, 1, 1); boxGeo.translate(0, 0.5, 0);
  const cityMat = new THREE.MeshLambertMaterial({ color: T.building });
  const city = new THREE.InstancedMesh(boxGeo, cityMat, boxes.length);
  const m4 = new THREE.Matrix4();
  boxes.forEach((b, i) => { m4.makeScale(b[2], b[4], b[3]); m4.setPosition(b[0], 0, b[1]); city.setMatrixAt(i, m4); });
  city.castShadow = true; city.receiveShadow = true;
  scene.add(city);

  // ---- the line: one tube per section, vertex-coloured by noise bins (quiet colour where there is no data)
  const SEGS = 240, RAD = 10;
  const tubes = {};
  for (const [key, curve] of Object.entries(SECTIONS)) {
    const geo = new THREE.TubeGeometry(curve, SEGS, 2.6, RAD, false);
    geo.setAttribute("color", new THREE.Float32BufferAttribute(new Float32Array((SEGS + 1) * (RAD + 1) * 3), 3));
    const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ vertexColors: true }));
    mesh.position.y = 2.2;
    scene.add(mesh);
    const glow = new THREE.Mesh(new THREE.TubeGeometry(curve, 120, 7, 8, false), new THREE.MeshBasicMaterial({ color: T.glow, transparent: true, opacity: T.glowO, depthWrite: false }));
    glow.position.y = 0.2; scene.add(glow);
    tubes[key] = { mesh, geo, glow, noise: null };
  }
  const tmpC = new THREE.Color();
  function paintTube(key) {
    const tb = tubes[key], attr = tb.geo.getAttribute("color"), n = tb.noise;
    for (let i = 0; i <= SEGS; i++) {
      let css = null;
      if (n) {
        const sm = (i / SEGS) * n.length;
        let b = n.edges.findIndex((e, j) => j > 0 && sm <= e) - 1;
        if (b < 0) b = n.db.length - 1;
        css = noiseColor(n.db[b], n.vmin, n.vmax);
      }
      tmpC.set(css || T.quiet);
      if (css) tmpC.convertSRGBToLinear?.();
      for (let j = 0; j <= RAD; j++) { const k = (i * (RAD + 1) + j) * 3; attr.array[k] = tmpC.r; attr.array[k + 1] = tmpC.g; attr.array[k + 2] = tmpC.b; }
    }
    attr.needsUpdate = true;
  }
  Object.keys(tubes).forEach(paintTube);

  // ---- stations
  const stMat = new THREE.MeshLambertMaterial({ color: "#FFFFFF" });
  const ringMat = new THREE.MeshBasicMaterial({ color: T.ring });
  for (const k of ORDER) {
    const s = new THREE.Mesh(new THREE.CylinderGeometry(6, 6, 4, 32), stMat); s.position.copy(pos[k]).setY(2); s.castShadow = true; scene.add(s);
    const r = new THREE.Mesh(new THREE.TorusGeometry(6, 1.1, 8, 40), ringMat); r.rotation.x = Math.PI / 2; r.position.copy(pos[k]).setY(4.1); scene.add(r);
  }

  // ---- train estimate + 90 % band
  const train = new THREE.Group();
  const trainMat = new THREE.MeshLambertMaterial({ color: T.train });
  const puck = new THREE.Mesh(new THREE.CylinderGeometry(5.5, 5.5, 3, 32), trainMat); puck.position.y = 4; puck.castShadow = true;
  const cap = new THREE.Mesh(new THREE.CylinderGeometry(3, 3, 3.2, 3), new THREE.MeshBasicMaterial({ color: "#FFFFFF" })); cap.position.y = 4.2;
  const pulseMat = new THREE.MeshBasicMaterial({ color: T.train, transparent: true, opacity: 0.4, side: THREE.DoubleSide, depthWrite: false });
  const pulse = new THREE.Mesh(new THREE.RingGeometry(7, 9, 48), pulseMat); pulse.rotation.x = -Math.PI / 2; pulse.position.y = 0.6;
  train.add(puck, cap, pulse); train.visible = false; scene.add(train);
  const bandMat = new THREE.MeshBasicMaterial({ color: T.train, transparent: true, opacity: 0.25, depthWrite: false });
  let band = null;

  // ---- labels (HTML, projected each frame)
  const layer = h("div", { class: "map3d__labels", "aria-hidden": "true" });
  container.append(layer);
  const labelEls = opts.labels === false ? [] : ORDER.map((k) => {
    const el = h("span", { class: ["map3d__pill", k === "MLE" && "map3d__pill--dim"] }, h("i", null, "C"), stations[k].name);
    layer.append(el); return { el, p: pos[k].clone().setY(8) };
  });
  const tag = h("span", { class: "map3d__tag" }); tag.hidden = true; layer.append(tag);

  // ---- state + render loop
  let trainPoint = null, raf = 0, alive = true;
  const v = new THREE.Vector3();
  function size() {
    const w = container.clientWidth || 1, hh = container.clientHeight || 1, a = w / hh;
    const s = follow ? (w < 600 ? 110 : 130) : (w < 600 ? 230 : 175);
    camera.left = -s * a; camera.right = s * a; camera.top = s; camera.bottom = -s;
    camera.updateProjectionMatrix();
    renderer.setSize(w, hh, false);
  }
  const ro = new ResizeObserver(size); ro.observe(container); size();
  function project(p) { v.copy(p).project(camera); return [(v.x + 1) / 2 * container.clientWidth, (1 - v.y) / 2 * container.clientHeight, v.z]; }
  function frame(now) {
    if (!alive) return;
    if (follow && trainPoint) { controls.target.lerp(trainPoint, 0.12); camera.position.copy(controls.target).add(OFFSET); }
    const k = (now / 1000) % 1.6 / 1.6;
    pulse.scale.setScalar(1 + (reduce ? 0.6 : k * 1.6)); pulseMat.opacity = reduce ? 0.3 : 0.45 * (1 - k);
    controls.update();
    renderer.render(scene, camera);
    for (const l of labelEls) { const [x, y, z] = project(l.p); l.el.style.transform = `translate(${x}px, ${y}px) translate(-50%, -130%)`; l.el.hidden = z > 1; }
    if (trainPoint && !tag.hidden) { const [x, y] = project(trainPoint.clone().setY(6)); tag.style.transform = `translate(${x}px, ${y}px) translate(-50%, 26px)`; }
    raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);

  // ---- API
  const keyFor = (from, to) => (SECTIONS[`${from}-${to}`] ? { key: `${from}-${to}`, rev: false } : SECTIONS[`${to}-${from}`] ? { key: `${to}-${from}`, rev: true } : null);
  return {
    /** Colour one section from the map's bins (init.map). Metres are measured from `from`; a reversed section is flipped. */
    setNoise({ from, to, edges, db, vmin, vmax, length }) {
      const k = keyFor(from, to); if (!k || !edges || !db) return;
      const L = length || edges[edges.length - 1];
      let e = edges, d = db;
      if (k.rev) { e = edges.map((x) => L - x).reverse(); d = db.slice().reverse(); }
      tubes[k.key].noise = { edges: e, db: d, vmin, vmax, length: L };
      paintTube(k.key);
    },
    /** Place the estimate: s_m metres from `from` along the section of `length` m; lo/hi = 90 % band. null hides it. */
    setTrain(t) {
      if (!t || t.s_m == null || !isFinite(t.s_m)) { train.visible = false; tag.hidden = true; if (band) band.visible = false; return; }
      const k = keyFor(t.from, t.to); if (!k) return;
      const curve = SECTIONS[k.key], L = t.length || 1;
      const frac = (m) => { const f = Math.min(1, Math.max(0, m / L)); return k.rev ? 1 - f : f; };
      trainPoint = curve.getPointAt(frac(t.s_m));
      train.position.copy(trainPoint); train.visible = true;
      if (t.label) { tag.hidden = false; tag.replaceChildren(h("b", null, t.label), h("span", null, t.sub || "estimated, no GPS")); } else tag.hidden = true;
      if (band) { scene.remove(band); band.geometry.dispose(); band = null; }
      if (t.lo != null && t.hi != null && isFinite(t.lo) && isFinite(t.hi)) {
        const a = frac(Math.min(t.lo, t.hi)), b = frac(Math.max(t.lo, t.hi));
        const f0 = Math.min(a, b), f1 = Math.max(a, b);
        if (f1 - f0 > 0.002) {
          const pts = Array.from({ length: 12 }, (_, i) => curve.getPointAt(f0 + (f1 - f0) * i / 11));
          band = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 24, 4.6, 10, false), bandMat);
          band.position.y = 2.2; scene.add(band);
        }
      }
    },
    setTheme(name) {
      if (!THEMES[name] || name === theme) return;
      theme = name; T = THEMES[name];
      groundMat.color.set(T.ground); cityMat.color.set(T.building); ringMat.color.set(T.ring);
      hemi.color.set(T.hemiSky); hemi.groundColor.set(T.hemiGround); hemi.intensity = T.hemi; sun.color.set(T.sun); sun.intensity = T.sunI;
      trainMat.color.set(T.train); pulseMat.color.set(T.train); bandMat.color.set(T.train);
      Object.values(tubes).forEach((tb) => { tb.glow.material.color.set(T.glow); tb.glow.material.opacity = T.glowO; });
      applyBackdrop(); Object.keys(tubes).forEach(paintTube);
    },
    showCity(on) { city.visible = !!on; },
    resetView() { controls.target.copy(trainPoint && follow ? trainPoint : view); camera.position.copy(controls.target).add(OFFSET); camera.zoom = ZOOM; camera.updateProjectionMatrix(); },
    /** Centre the view on one section ("LST-BTH" or "BTH-MLE", either direction). */
    focus(from, to) {
      const k = keyFor(from, to); if (!k) return;
      view = SECTIONS[k.key].getPointAt(0.5);
      controls.target.copy(view); camera.position.copy(view).add(OFFSET); camera.zoom = ZOOM; camera.updateProjectionMatrix();
    },
    destroy() {
      alive = false; cancelAnimationFrame(raf); ro.disconnect(); controls.dispose();
      scene.traverse((o) => { o.geometry?.dispose?.(); const m = o.material; (Array.isArray(m) ? m : m ? [m] : []).forEach((x) => x.dispose?.()); });
      renderer.dispose(); renderer.forceContextLoss?.(); canvas.remove(); layer.remove();
    },
  };
  function applyBackdrop() { scene.background = new THREE.Color(T.bg); scene.fog = new THREE.Fog(T.bg, T.fogNear, T.fogFar); }
}

/** Reads the noise map (init.map) of a stored ride stream: first `init` event of a JSONL text. */
export function initFromJsonl(text) {
  for (const line of String(text).split("\n")) {
    if (!line.includes('"init"')) continue;
    try { const ev = JSON.parse(line); if (ev.type === "init") return ev; } catch { /* keep looking */ }
  }
  return null;
}
