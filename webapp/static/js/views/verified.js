// Demo step 5, "Verified answer": a composite of three existing components. Left: the Ask result (LIVE if the background ask
// finished, otherwise live progress next to the newest CACHED answer). Right: Atlas three ways (top hit per mode) and the seeded canary.
import { h, mount as mountEl, fmtDur, fmtClock, fmtUTC } from "../util.js";
import { fetchJSON } from "../api.js";
import { state, getAtlas, DEMO_QUESTION } from "../state.js";
import { setChips } from "../demo.js";
import { card, chip, provChip, screenHeader, whereFrom, errorCard, skeleton, emptyState } from "../ui.js";
import { askStore, loadCachedRun, AskRun, eventsFromResult } from "../askctl.js";
import { renderAskRun, startElapsedTicker, recheckState } from "./ask.js";
import { threeWays, atlasChip } from "./atlas.js";
import { canaryCard } from "./pack.js";

export async function mount(root, ctx) {
  const screen = h("div", { class: "screen verified is-demo" });
  root.append(screen);
  const q = state.demo.settings.question || DEMO_QUESTION;
  const left = h("div", { class: "stack" }), right = h("div", { class: "stack" });
  const head = h("div");
  // header and demo-bar chips follow what is on screen: LIVE only for a live answer, CACHED with its timestamp for a saved one; SEEDED is the canary
  const setHead = (...chips) => { const list = [...chips.filter(Boolean), chip("seeded")]; mountEl(head, screenHeader({ title: "Verified answer", subtitle: q, chips: list })); setChips(5, list.map((c) => c.cloneNode(true))); };
  setHead();
  screen.append(head, h("div", { class: "split" }, left, right),
    whereFrom("agent.ask (live) or outputs_posthoc/asks (cached)", "GET /api/atlas/search", "GET /api/canary", "agent/verify.py"));
  let unsub = null, tick = 0;
  const recheckChip = (c) => (recheckState(c.result).kind === "ok" ? chip("live", "re-checked now") : chip("cached", "re-check: see the answer"));

  // ---- right column: Atlas three ways + canary
  const atlasHost = h("div");
  right.append(atlasHost, canaryCard({ auto: true }));
  mountEl(atlasHost, card({ title: "Atlas, three ways", body: skeleton(4) }));
  getAtlas(q, "live").then((a) => mountEl(atlasHost, card({ title: "Atlas, three ways", chips: atlasChip(a), body: [threeWays(a, { mini: true }), a.question_mismatch ? h("p", { class: "warn" }, `(cached snapshot answers '${a.search.question}', not the question asked)`) : null] })))
    .catch((e) => mountEl(atlasHost, errorCard({ title: "Atlas search failed", message: e.message })));

  // ---- left column: the answer
  const cachedHost = h("div");
  async function newestCached() {
    try {
      const l = (await fetchJSON("/api/ask/cached")).items || [];
      // the newest saved answer to THIS question (the server lists the newest per question); never another question's answer
      const norm = (t) => String(t || "").toLowerCase().replace(/\s+/g, " ").trim();
      const pick = l.filter((x) => norm(x.question) === norm(q)).sort((a, b) => String(b.ts).localeCompare(String(a.ts)))[0];
      return pick ? await loadCachedRun(pick.name, { remember: false }) : null;
    } catch { return null; }
  }
  const live = askStore.current && !askStore.current.cached && askStore.current.question === q ? askStore.current : null;
  async function draw() {
    const run = live;
    if (run?.status === "done") {
      setHead(chip("live", "LIVE answer"));
      mountEl(left, h("div", { class: "row" }, chip("live", `LIVE · asked live at ${fmtClock((run.t0 - state.demo.started) / 1000)}, answered after ${fmtDur((run.t1 - run.t0) / 1000)}`)), renderAskRun(run, { compact: true }));
    } else if (run?.busy) {
      setHead(chip("live", "LIVE ask running"));
      mountEl(left, card({ title: "Live ask in progress", chips: chip("live"), body: renderAskRun(run, { progressOnly: true, onCancel: () => run.cancel() }) }), cachedHost);
      if (!cachedHost.firstChild) newestCached().then((c) => {
        if (!c || run.status !== "running" && run.status !== "starting") return;
        setHead(chip("live", "LIVE ask running"), provChip("CACHED", c.result?.ts, "shown meanwhile"), recheckChip(c));
        mountEl(cachedHost, h("div", { class: "row" }, provChip("CACHED", c.result?.ts, "newest saved answer")), renderAskRun(c, { compact: true }));
      });
    } else {
      mountEl(left, skeleton(4), cachedHost);
      const c = await newestCached();
      setHead(c ? provChip("CACHED", c.result?.ts, "no live answer") : null, c ? recheckChip(c) : null);
      mountEl(left, ...(run?.status === "error" ? [errorCard({ title: "The live ask did not finish", message: run.error })] : []),
        c ? [h("div", { class: "row" }, provChip("CACHED", c.result?.ts, "newest saved answer, shown because no live answer is ready")), renderAskRun(c, { compact: true })]
          : emptyState("No live answer, and no saved answer to this exact question. Ask it on the Ask Argon screen first."));
    }
  }
  draw();
  if (live) unsub = live.subscribe(() => { cancelAnimationFrame(tick); tick = requestAnimationFrame(draw); });
  const stop = startElapsedTicker(left);
  return () => { unsub?.(); stop(); cancelAnimationFrame(tick); };
}
