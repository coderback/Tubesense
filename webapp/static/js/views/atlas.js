// MongoDB Atlas: cluster facts, one question searched three ways (Atlas Search, Vector Search with Automated Embedding, Hybrid
// $rankFusion), records, and Atlas Stream Processing windows. Read-only; a stored snapshot is used (and labelled) when Atlas is unreachable.
import { h, mount as mountEl, clear, fmtNum, fmtUTC, fmtTimeLocal, icon, announce } from "../util.js";
import { MOCK, fetchJSON, postJSON, openJobStream } from "../api.js";
import { state, on, getAtlas, getRides, getHealth, setAtlasStatus } from "../state.js";
import { card, chip, provChip, screenHeader, whereFrom, verdict, openSource, errorCard, emptyState, skeleton, copyButton, startWithReplace, toast, segmented } from "../ui.js";

export const ATLAS_Q = "Was Bethnal Green to Liverpool Street ground for noise?";
const MODES = [["text", "Atlas Search", "BM25 full text (docs_text)"], ["vector", "Vector Search", "autoEmbed voyage-4: Atlas embeds the query (docs_auto)"], ["hybrid", "Hybrid Search", "$rankFusion of both"]];

export function atlasChip(a) {
  return a.mode === "live" ? [chip("live", `LIVE from MongoDB Atlas at ${fmtUTC(a.taken)}`), chip("readonly")]
    : [chip("cached", `CACHED FALLBACK · snapshot ${fmtUTC(a.taken)}`), chip("readonly")];
}

function hit(x, rank, mode) {
  return h("li", { class: "hit" }, h("button", { type: "button", class: "hit__btn", onclick: () => openSource(x._id) },
    h("div", { class: "hit__top" }, h("span", { class: "mono hit__rank" }, `#${rank}`), h("strong", null, x.title || x._id),
      h("span", { class: "mono muted" }, `${x.doc_id} p.${x.page ?? "?"} · score ${fmtNum(x.score, 4)}`)),
    h("div", { class: "muted hit__meta" }, `${x.publisher || ""} · ${x.doc_date || ""}`),
    mode === "hybrid" && x.ranks ? h("div", { class: "chip-row" }, Object.entries(x.ranks).map(([k, v]) => h("span", { class: "chip chip--relative mono" }, `${k}=#${v}`))) : null,
    h("p", { class: "hit__snippet" }, x.snippet || "")));
}

/** Three columns, or (mini) the top hit of each mode in one compact list. */
export function threeWays(a, { mini = false } = {}) {
  const modes = a.search.modes;
  const col = ([k, name, sub]) => {
    const m = modes[k]; if (!m) return null;
    return h("section", { class: "mode" }, h("header", null, h("h3", null, name), h("span", { class: "mono muted" }, `${m.ms} ms`), a.mode === "live" ? chip("live") : chip("cached")),
      mini ? null : h("p", { class: "help" }, `${m.label || sub}`), h("ol", { class: "hits" }, (mini ? m.hits.slice(0, 1) : m.hits).map((x, i) => hit(x, i + 1, k))));
  };
  return h("div", { class: mini ? "modes modes--mini" : "grid grid--3 modes" }, MODES.map(col));
}

async function clusterCard(a) {
  const c = a.cluster;
  return card({ title: "Cluster", chips: atlasChip(a), cls: "cluster", body: [
    h("dl", { class: "kv" }, h("dt", null, "MongoDB"), h("dd", { class: "mono" }, c.version), h("dt", null, "database"), h("dd", { class: "mono" }, c.database)),
    h("div", { class: "grid grid--2" },
      h("div", { class: "table-wrap" }, h("table", null, h("thead", null, h("tr", null, ["collection", "documents"].map((t) => h("th", null, t)))),
        h("tbody", null, c.collections.map((x) => h("tr", null, h("td", { class: "mono" }, x.name, x.kind ? h("span", { class: "chip chip--relative" }, x.kind) : null), h("td", { class: "mono" }, String(x.count))))))),
      h("div", { class: "table-wrap" }, h("table", null, h("thead", null, h("tr", null, ["search index", "type", "status"].map((t) => h("th", null, t)))),
        h("tbody", null, c.indexes.map((x) => h("tr", null, h("td", null, h("span", { class: "mono" }, `${x.collection}.${x.name}`), h("div", { class: "muted" }, x.what)),
          h("td", { class: "mono" }, x.type), h("td", null, x.status === "READY" ? verdict("verified", "READY") : verdict("OPEN", x.status), x.queryable ? h("span", { class: "muted" }, " queryable") : null)))))))] });
}

function recordsCard(a) {
  const r = a.records, ch = a.local_chain || {};
  return card({ title: "Records in Atlas", chips: atlasChip(a), cls: "records", body: [
    h("p", null, `${r.attempts_count} analyst attempts mirrored; local log has ${ch.lines ?? "?"} lines. `, ch.atlas_head_matches ? verdict("MATCH", "Atlas head MATCHES local head") : verdict("OPEN", "Atlas head DIFFERS from local head")),
    h("div", { class: "table-wrap" }, h("table", null, h("thead", null, h("tr", null, ["attempt", "time", "kind", "verdict", "hash", "prev"].map((t) => h("th", null, t)))),
      h("tbody", null, (r.attempts_last || []).map((x) => h("tr", null, h("td", { class: "mono" }, String(x.attempt)), h("td", { class: "mono" }, fmtUTC(x.ts)), h("td", null, x.kind), h("td", null, x.verdict),
        h("td", { class: "mono" }, String(x.hash).slice(0, 10)), h("td", { class: "mono" }, String(x.prev_hash || "").slice(0, 10))))))),
    h("p", null, `${r.section_stats_count} section statistics stored; examples:`),
    h("ul", { class: "plain mono" }, (r.section_stats_examples || []).map((x) => h("li", null, `${x.key} = ${x.value} ${x.unit || ""} (n=${x.n})`)))] });
}

// ---------------------------------------------------------------- stream processing
function streamCard() {
  const body = h("div", { class: "stack" }), chipsHost = h("span");
  const jobHost = h("div");
  let timer = 0, S = null, mounted = true, health = null, rideSelect, speed = 4, job = null, stream = null;
  const wrap = card({ title: "Atlas Stream Processing", chips: chipsHost, body: [body, jobHost], cls: "stream" });

  const bars = (w) => {
    const vals = w.map((x) => x.level_mean).filter((v) => v !== null && v !== undefined);
    if (!vals.length) return null;
    const lo = Math.min(...vals), hi = Math.max(...vals), span = Math.max(1e-6, hi - lo);
    return h("div", { class: "bars", role: "img", "aria-label": `Mean level per 10 s window, ${fmtNum(lo, 1)} to ${fmtNum(hi, 1)} dBFS(A), oldest on the left` },
      [...w].reverse().map((x) => h("span", { class: "bars__bar", style: { height: `${14 + 86 * ((x.level_mean - lo) / span)}%` }, title: `${x.window_start}: ${fmtNum(x.level_mean, 1)} dBFS(A)` })));
  };
  const winTable = (w) => h("div", { class: "table-wrap" }, h("table", null, h("thead", null, h("tr", null, ["window (UTC wall clock)", "recorded s", "level mean", "level max", "position (m)", "section", "stopped s"].map((t) => h("th", null, t)))),
    h("tbody", null, w.map((x) => h("tr", null, h("td", { class: "mono" }, `${String(x.window_start || "").slice(11, 19)}–${String(x.window_end || "").slice(11, 19)}`), h("td", { class: "mono" }, String(x.n ?? "")),
      h("td", { class: "mono" }, fmtNum(x.level_mean, 1)), h("td", { class: "mono" }, fmtNum(x.level_max, 1)),
      h("td", { class: "mono" }, `${fmtNum(x.s_m_min, 0)}–${fmtNum(x.s_m_max, 0)}`), h("td", null, x.section || ""), h("td", { class: "mono" }, String(x.n_stopped ?? "")))))));
  const WIN_NOTE = "Each window is 10 s of replay wall-clock (~10 x speed recorded seconds, e.g. about 40 s of the ride at 4x); \"recorded s\" is how many recorded seconds fell in it. Levels are relative, dBFS(A), uncalibrated. The processor live_windows groups the rows the replay inserted, inside Atlas.";
  let histOpen = false;   // the 2 s repaint keeps a history panel the viewer opened open
  const stateText = (st) => st === "STARTED" ? "running (STARTED)" : st ? `${st} (not running)` : "state unknown";
  function paint() {
    if (!S) { mountEl(body, skeleton(3)); return; }
    const hist = S.history || [];
    if (S.available || hist.length) setAtlasStatus(true);
    else if (/^Atlas unreachable/i.test(S.reason || "")) setAtlasStatus(false);
    const age = S.age_s === null || S.age_s === undefined ? null : S.age_s >= 120 ? `${Math.round(S.age_s / 60)} min` : `${Math.round(S.age_s)} s`;
    mountEl(chipsHost, S.available ? [chip("live"), (S.windows || []).some((x) => x.replay !== false) ? chip("replay", "FROM A REPLAYED RIDE") : null] : chip("neutral", "NOT LIVE"));
    const procLine = h("p", { class: "muted mono" }, `processor ${S.processor || "live_windows"} · ${stateText(S.processor_state)}${age ? ` · newest window ended ${age} ago` : ""} · checked ${fmtTimeLocal(S.checked_at)}`);
    if (!S.available) {
      mountEl(body, h("div", { class: "empty" }, h("p", null, h("strong", null, S.processor_state && S.processor_state !== "STARTED" ? "Stream Processing is not running" : "No live stream windows")),
          h("p", { class: "muted" }, S.reason || "No reason given.")), procLine,
        hist.length ? h("details", { class: "stream__history", open: histOpen, ontoggle: (e) => { histOpen = e.currentTarget.open; } }, h("summary", null, `History, not live: ${hist.length} windows from an earlier run`),
          h("p", { class: "help" }, "Stored windows the processor wrote before; shown for the record, not as live data."), winTable(hist), h("p", { class: "help" }, WIN_NOTE)) : null);
      return;
    }
    mountEl(body, procLine, bars(S.windows), winTable(S.windows), h("p", { class: "help" }, WIN_NOTE),
      hist.length ? h("details", { class: "stream__history", open: histOpen, ontoggle: (e) => { histOpen = e.currentTarget.open; } }, h("summary", null, `History, not live: ${hist.length} older windows`), winTable(hist)) : null);
  }
  async function poll() {
    if (!mounted) return;
    if (document.visibilityState === "visible") {
      try { S = await fetchJSON("/api/stream/status"); } catch (e) { S = { available: false, reason: e.message, windows: [] }; }
      if (mounted) paint();
    }
    timer = setTimeout(poll, 2000);
  }
  function controls(rides) {
    const mod = health?.modules?.["atlas.stream_replay"], reason = MOCK ? "Mock mode: streaming is not simulated." : !health ? "Server not reachable." : !mod ? "atlas.stream_replay is not installed." : !state.online ? "Offline: Atlas is unreachable." : null;
    const options = (rides?.rides || []).filter((r) => r.available);
    rideSelect = h("select", { "aria-label": "Ride to stream" }, options.map((r) => h("option", { value: `${r.run_id}|${r.track}`, selected: r.run_id === "R9" && r.track === "EB LST->BTH" }, `${r.run_id} · ${r.track.replace("->", "→")} · ${r.day}`)));
    const btn = h("button", { class: "btn", type: "button", disabled: !!reason || !options.length, onclick: startStream }, icon("play", 16), "Stream a ride into Atlas");
    mountEl(jobHost, h("div", { class: "stack stream__ctl" }, h("h3", null, "Stream a ride into Atlas"),
      h("p", { class: "help" }, "Writes per-second derived values (time, position, relative level, section) to Atlas `ingest_live`. No audio, no raw sensors. Heavy job: only one ride, analyst or stream at a time."),
      reason ? emptyState(reason) : null, h("div", { class: "row" }, h("label", { class: "field-inline" }, h("span", null, "ride"), rideSelect), h("span", { class: "help" }, "speed 4×"), btn), h("div", { id: "stream-out" })));
  }
  async function startStream() {
    const [run, track] = rideSelect.value.split("|"), out = jobHost.querySelector("#stream-out");
    mountEl(out, skeleton(2));
    let n = 0, lastRow = null;
    try {
      const j = await startWithReplace((replace) => postJSON("/api/stream/start", { run, track, speed, replace }));
      if (!j) { clear(out); return; }
      job = j; stream?.close();
      stream = openJobStream(j, (e) => {
        if (e.type === "row") { n++; lastRow = e; if (n === 1 && !j.params?.dry_run) setAtlasStatus(true); mountEl(out, h("p", { class: "mono" }, `${n} rows inserted · last position ${fmtNum(e.s_m, 0)} m · level ${fmtNum(e.level_rel_dbA ?? e.level_rel, 1)} dBFS(A) relative`)); }
        else if (e.type === "done") mountEl(out, h("p", null, verdict("verified", "done"), ` ${n} rows inserted into ingest_live for ride ${e.ride_id}. Windows appear above as the processor closes them.`));
        else if (e.type === "error") mountEl(out, errorCard({ message: e.message, title: "The stream replay failed" }));
      }, () => {});
    } catch (e) { mountEl(out, errorCard({ message: e.message, title: "Could not start the stream replay" })); }
  }
  Promise.all([getHealth().catch(() => null), getRides().catch(() => null)]).then(([hl, rides]) => { health = hl; controls(rides); });
  paint(); poll();
  wrap.dispose = () => { mounted = false; clearTimeout(timer); stream?.close(); };
  wrap.status = () => S;
  return wrap;
}

function featureTable(streamOn) {
  const rows = [
    ["Atlas Search (full text)", "Index docs_text over the 44 chunks of 14 public documents (TfL letters, FOI replies, Mayor's Questions, Assembly papers).", "Ask Argon retrieval; this screen"],
    ["Vector Search", "Index docs_auto: semantic matches on the same chunks.", "Ask Argon retrieval; this screen"],
    ["Hybrid Search", "$rankFusion of the two with reciprocal rank fusion.", "Ask Argon and the investigator pick the chunks Argon may quote"],
    ["Automated Embedding + Voyage AI", "autoEmbed field, voyage-4: Atlas embeds documents and queries. Only public text is embedded.", "docs_auto"],
    ["Stream Processing", streamOn ? "Processor live_windows groups replayed ride rows into 10 s windows." : "Not running right now (see the card above for the reason).", "Stream card above"],
  ];
  return card({ title: "The six scored Atlas features, and where each is used", body: h("div", { class: "table-wrap" }, h("table", null,
    h("thead", null, h("tr", null, ["feature", "how it is used here", "where"].map((t) => h("th", null, t)))), h("tbody", null, rows.map((r) => h("tr", null, h("td", null, h("strong", null, r[0])), h("td", null, r[1]), h("td", { class: "muted" }, r[2])))))),
    footer: "Time series collections and geospatial indexes are also used for storage but are not among the scored features." });
}

export async function mount(root, ctx) {
  const screen = h("div", { class: "screen atlas" });
  root.append(screen);
  const disposers = [];
  const head = h("div"), searchHost = h("div"), clusterHost = h("div"), recordsHost = h("div");
  const stream = streamCard(); disposers.push(() => stream.dispose());
  const featHost = h("div");
  mountEl(head, screenHeader({ title: "MongoDB Atlas", subtitle: "The public record lives in Atlas. Search it three ways; the same hybrid retrieval feeds Ask Argon.", chips: [chip("readonly")] }));
  screen.append(head, clusterHost, searchHost, recordsHost, stream, featHost, whereFrom("GET /api/atlas/search (demo/atlas_show.py, read-only)", "GET /api/stream/status (atlas/live_view.py)", "outputs_posthoc/atlas_snapshot.json (CACHED fallback)"));
  mountEl(clusterHost, card({ title: "Cluster", body: skeleton(4) }));
  mountEl(featHost, featureTable(false));

  const q = h("input", { type: "text", id: "atlas-q", value: ctx.query.get("q") || ATLAS_Q, maxlength: 200, "aria-label": "Search text" });
  const results = h("div"), pipeHost = h("div");
  const status = h("p", { class: "help", role: "status" });
  const go = h("button", { class: "btn", type: "submit" }, icon("atlas", 16), "Search");
  const formEl = h("form", { class: "row", style: { alignItems: "end" }, onsubmit: (e) => { e.preventDefault(); search(false); } }, h("label", { class: "field grow" }, "Question (any text; Atlas searches the 44 public chunks)", q), go);
  mountEl(searchHost, card({ title: "One question, three ways", body: [formEl, status, results, pipeHost], cls: "threeway" }));
  let seq = 0;

  async function search(cached) {
    const me = ++seq, text = q.value.trim();
    if (text.length < 3) { toast("Enter at least 3 characters."); return; }
    go.disabled = true; mountEl(results, skeleton(6)); clear(pipeHost);
    const t0 = Date.now();
    status.textContent = cached ? "Reading the stored snapshot…" : "Searching Atlas (about 6 s; falls back to the stored snapshot if Atlas is unreachable)…";
    try {
      const a = await getAtlas(text, cached ? "cached" : "live", true);
      if (me !== seq) return;
      render(a, text);
      setAtlasStatus(a.mode === "live", a.mode !== "live");
      announce(a.mode === "live" ? "Atlas results ready" : "Cached Atlas snapshot shown");
      status.textContent = `${a.mode === "live" ? "LIVE" : "CACHED"} · ${((Date.now() - t0) / 1000).toFixed(1)} s round trip`;
    } catch (e) {
      if (me !== seq) return;
      status.textContent = ""; setAtlasStatus(false);
      mountEl(results, errorCard({ message: e.message, title: "Atlas search failed", retry: () => search(false), fallback: () => search(true), fallbackLabel: "Show CACHED snapshot" }));
    }
    go.disabled = false;
  }
  function render(a, asked) {
    mountEl(results, h("div", { class: "row" }, atlasChip(a)), a.question_mismatch ? h("p", { class: "warn" }, `(cached snapshot answers '${a.search.question}', not the question asked)`) : null,
      a.fallback_reason ? h("p", { class: "warn" }, `Live search failed (${a.fallback_reason}); showing the stored snapshot.`) : null, threeWays(a));
    mountEl(pipeHost, h("details", { class: "pipeline" }, h("summary", null, "Hybrid pipeline, exactly as run"),
      h("div", { class: "row" }, copyButton(() => JSON.stringify(a.search.hybrid_pipeline, null, 2), "Copy pipeline")),
      h("pre", { class: "json mono" }, JSON.stringify(a.search.hybrid_pipeline, null, 2)),
      h("p", { class: "help" }, "fused score = sum over pipelines of weight / (60 + rank) (reciprocal rank fusion)")));
    clusterCard(a).then((c) => mountEl(clusterHost, c));
    mountEl(recordsHost, recordsCard(a));
  }
  search(false);
  let shown = false;
  const fi = setInterval(() => { const on = !!stream.status()?.available; if (on !== shown) { shown = on; mountEl(featHost, featureTable(on)); } }, 1500);
  disposers.push(() => clearInterval(fi));
  return () => { seq++; disposers.forEach((d) => d()); };
}
