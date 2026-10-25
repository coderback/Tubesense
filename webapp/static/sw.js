// App-shell service worker. Network first for the app's own files (so edits show immediately), cache as the offline fallback.
// It never touches /api/* (live data, SSE, approvals) or mock/*, and ignores cross-origin requests (fonts, map tiles).
const VERSION = "tubesense-shell-v4";
const SHELL = [
  "./", "index.html", "manifest.webmanifest", "css/app.css", "icons/icon.svg", "icons/icon-192.png", "icons/apple-touch-icon.png",
  "js/main.js", "js/util.js", "js/api.js", "js/state.js", "js/ui.js", "js/md.js", "js/colors.js", "js/ridectl.js", "js/askctl.js", "js/demo.js", "js/mock.js",
  "js/stripmap.js", "js/geomap.js", "js/yawchart.js", "js/map3d.js", "css/overview.css", "css/map3d.css", "css/ride.css",
  "vendor/three-r128.min.js", "vendor/three-r128-OrbitControls.js",
  "js/views/story.js", "js/views/ride.js", "js/views/agent.js", "js/views/ask.js", "js/views/pack.js", "js/views/atlas.js",
  "js/views/compare.js", "js/views/method.js", "js/views/box.js", "js/views/verified.js", "js/views/work.js", "js/views/live.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => Promise.all(SHELL.map((u) => c.add(u).catch(() => null)))).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.includes("/mock/")) return;
  e.respondWith(
    fetch(req).then((res) => {
      if (res.ok && res.type === "basic") { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match(req).then((hit) => hit || (req.mode === "navigate" ? caches.match("index.html") : Response.error()))));
});
