// Overview (design v2): the section on a 3D map, the headline public-record figure, and what needs attention, each linking
// to its place. Public-record figures are fixed text; the noise colours, pack counts and sealed-test result are read from
// the same endpoints the other screens use. Nothing here is computed in the browser.
import { h, icon, ensureCss, fmtNum, fmtUTC } from "../util.js";
import { chip, card, whereFrom } from "../ui.js";
import { MOCK } from "../api.js";
import { getPack, getAnalyst } from "../state.js";
import { rideSession, listPrecomputed } from "../ridectl.js";
import { createMap3D, initFromJsonl } from "../map3d.js";

const STEPS = [
  ["Ride with a phone", "Motion, magnetic and pressure sensors. Sound becomes 1-second levels; raw audio is never kept.", "#/ride"],
  ["Place the train, map the noise", "No GPS underground. Track curves and a speed model place the train between stations.", "#/ride"],
  ["Build checked evidence", "Gemini 4 Argon drafts from TfL's own records. Code checks every claim. A person approves.", "#/pack"],
];

/** The noise map of a stored ride (init.map): the ride already loaded on the Ride screen, else a precomputed stream. */
async function loadSectionInit() {
  if (rideSession.init?.map) return rideSession.init;
  try {
    const items = await listPrecomputed();
    const pick = items.find((i) => /R9/.test(i.file || "")) || items[0];
    if (pick) {
      const res = await fetch(`/api/ride/precomputed?file=${encodeURIComponent(pick.file)}`, { cache: "no-store" });
      if (res.ok) { const init = initFromJsonl(await res.text()); if (init?.map) return init; }
    }
  } catch { /* fall through */ }
  if (MOCK) {
    try { const res = await fetch("mock/ride_R9.jsonl"); if (res.ok) return initFromJsonl(await res.text()); } catch { /* none */ }
  }
  return null;
}

function fallbackMap(reason) {
  return h("div", { class: "ov-map__fallback", role: "img", "aria-label": "Schematic of the Central line from Liverpool Street to Bethnal Green and Mile End" },
    h("div", { class: "ov-map__line" }, h("span", null, "Liverpool Street"), h("span", null, "Bethnal Green"), h("span", null, "Mile End")),
    h("p", { class: "muted" }, `3D map unavailable: ${reason}. The Ride screen has the 2D views.`));
}

export async function mount(root, ctx) {
  ensureCss("css/overview.css");
  const screen = h("div", { class: "screen overview story" });
  root.append(screen);
  const disposers = [];

  // ---- map panel
  const stage = h("div", { class: "ov-map__stage" });
  const legend = h("div", { class: "ov-legend", hidden: true },
    h("span", { class: "ov-legend__bar", "aria-hidden": "true" }), h("span", { class: "ov-legend__text" }));
  const heroCard = h("div", { class: "ov-hero" },
    h("span", { class: "ov-eyebrow" }, "In the cab, TfL’s own measurement"),
    h("div", { class: "ov-hero__num", "aria-label": "87 decibels" }, "87", h("span", null, " dB")),
    h("p", null, "Liverpool Street → Bethnal Green, Central line, eastbound. Measured 24 June 2024, released under FOI-1231-2425."),
    chip("precomputed", "PUBLIC RECORD · FOI-1231-2425"));
  const player = h("a", { class: "ov-player", href: "#/ride" },
    h("span", { class: "ov-player__btn", "aria-hidden": "true" }, icon("play", 16)),
    h("span", { class: "ov-player__text" }, h("b", null, "Watch a recorded ride being located"), h("span", null, "Ride R9 · 24 Oct · no GPS")),
    icon("next", 18));
  const mapPanel = h("section", { class: "ov-map", "aria-label": "Map of the section" }, stage, heroCard, legend, player);

  // ---- side panels (filled from the API)
  const packCard = h("a", { class: "ov-card ov-card--needs", href: "#/pack" },
    h("span", { class: "ov-card__k" }, "Evidence pack"), h("strong", { class: "ov-card__t" }, "Loading…"));
  const agentCard = h("a", { class: "ov-card", href: "#/agent" },
    h("span", { class: "ov-card__k" }, "Agent loop"), h("strong", { class: "ov-card__t" }, "Loading…"));
  const phoneCard = h("a", { class: "ov-card ov-card--dark", href: "#/live" },
    h("span", { class: "ov-card__icon", "aria-hidden": "true" }, icon("live", 22)),
    h("span", { class: "ov-card__body" }, h("strong", null, "Live phone"), h("span", null, "Connect the capture app or phyphox. No microphone.")),
    icon("next", 18));
  const tiles = h("div", { class: "ov-tiles" },
    h("div", { class: "ov-tile" }, h("b", null, "83 dB"), h("span", null, "Bethnal Green → Mile End, same TfL measurement")),
    h("div", { class: "ov-tile" }, h("b", null, "85 dB(A)"), h("span", null, "HSE upper exposure action value, for reference")),
    h("div", { class: "ov-tile ov-tile--wide" }, h("b", { class: "muted" }, "92.8 dB"), h("span", null, "Reported in the press (Brixton Buzz, 27 Jan 2026). Primary source not located.")));
  const side = h("aside", { class: "ov-side" }, packCard, agentCard, phoneCard, tiles);

  // ---- context below the fold
  const named = card({ id: "named-user", cls: "named", title: "Built for Elly Baker AM", chips: chip("precomputed", "PUBLIC RECORD"), body: [
    h("p", { class: "muted" }, "Labour transport spokesperson, London Assembly"),
    h("p", null, "At Mayor's Question Time on 16 July 2026 (2026/2156) she said TfL had turned down most of her data requests on residents' noise complaints and the measures taken, including a six-month grinding plan, because grinding is planned only eight weeks ahead. Tubesense gives her (1) phone measurements placed between stations without GPS and (2) a cited evidence pack that says what the public record does and does not show, checked by code and approved by a human."),
    h("p", { class: "muted" }, "The people it ultimately protects are those exposed longest: train drivers and their unions (RMT, ASLEF).")],
    footer: "Source: Mayor's Question 2026/2156, 16 Jul 2026. This does not imply she has seen or endorsed Tubesense." });
  const grinding = h("p", { class: "ov-note" }, "Both sections were ground for noise between December 2021 and May 2022 (Mayor's Question 2022/1791). TfL says grinding must be repeated “usually 6 months – a year” (TfL Tube Noise presentation, 6 Dec 2022).");
  const steps = h("ol", { class: "ov-steps" }, STEPS.map(([t, d, href], i) => h("li", null, h("a", { href },
    h("span", { class: "ov-steps__n" }, String(i + 1)), h("strong", null, t), h("span", null, d)))));

  screen.append(
    h("header", { class: "screen-head ov-head" },
      h("span", { class: "ov-eyebrow" }, "Central line · Liverpool Street → Bethnal Green → Mile End"),
      h("h1", { id: "screen-title", tabindex: -1 }, "Ordinary rides. No GPS. Every claim checked.")),
    h("div", { class: "ov-grid" }, mapPanel, side),
    grinding,
    h("section", { class: "ov-how" }, h("h2", null, "How it works"), steps),
    named,
    h("p", { class: "scope" }, "Not a sound level meter, not a safety system, not a hearing-loss claim."),
    whereFrom("TfL FOI-1231-2425", "Mayor's Question 2022/1791", "TfL Tube Noise presentation (6 Dec 2022)", "Mayor's Question 2026/2156", "/api/pack", "/api/analyst/session", "/api/ride/precomputed (noise map)"));

  // ---- side panel data
  getPack().then((P) => {
    const status = String(P.status?.state || P.status || "").toLowerCase();
    const approved = /approved/.test(status);
    packCard.classList.toggle("ov-card--needs", !approved);
    packCard.replaceChildren(
      h("span", { class: "ov-card__k" }, approved ? "Evidence pack · approved as draft" : "Needs you"),
      h("strong", { class: "ov-card__t" }, approved ? "Evidence pack approved as a draft" : "Evidence pack ready for your sign-off"),
      h("span", { class: "ov-card__row" },
        h("span", { class: "ov-ring", "aria-hidden": "true" }, String(P.n_verified ?? "?")),
        h("span", null, `${P.n_verified ?? "?"} claims verified · ${P.n_dropped ?? 0} dropped · ${(P.asks || []).length} questions for TfL`)),
      h("span", { class: "ov-card__meta" }, `${P.label || "PRECOMPUTED"} · ${fmtUTC(P.generated)}`));
  }).catch(() => packCard.replaceChildren(h("span", { class: "ov-card__k" }, "Evidence pack"), h("strong", { class: "ov-card__t" }, "Open the pack"), h("span", { class: "ov-card__meta" }, "Could not load the summary.")));

  getAnalyst(0).then((S) => {
    const v2 = S.sealed?.v2, v1 = S.sealed?.v1;
    agentCard.replaceChildren(
      h("span", { class: "ov-card__k" }, "Agent loop · sealed test"),
      h("strong", { class: "ov-card__big" }, v2 ? `${fmtNum(v2.median_error_m, 1)} m` : "—"),
      h("span", null, v2 && v1 ? `Argon's tracker v2 vs pre-registered v1 (${fmtNum(v1.median_m, 1)} m): matched, not beaten.` : "Recorded analyst session."),
      h("span", { class: "ov-card__meta" }, "Errors vs an offline curve-edge reference, not GPS truth"));
  }).catch(() => agentCard.replaceChildren(h("span", { class: "ov-card__k" }, "Agent loop"), h("strong", { class: "ov-card__t" }, "Open the recorded session")));

  // ---- 3D map, then the noise bins of a stored ride
  let map = null;
  try {
    map = await createMap3D(stage, { theme: "light" });
    disposers.push(() => map.destroy());
    const init = await loadSectionInit();
    if (init?.map && map) {
      map.setNoise({ from: init.from, to: init.to, edges: init.map.bin_edges_m, db: init.map.noise_db, vmin: init.map.vmin, vmax: init.map.vmax, length: init.length_m });
      legend.hidden = false;
      legend.querySelector(".ov-legend__text").textContent = `Noise along ${init.from_name || init.from} → ${init.to_name || init.to}: map from ${(init.map.runs || []).join(", ") || "recorded rides"} · relative dBFS(A), uncalibrated`;
    }
    if (rideSession.last && rideSession.init && map) {
      const t = rideSession.last, i = rideSession.init;
      map.setTrain({ from: i.from, to: i.to, s_m: t.s_m, lo: t.band_lo, hi: t.band_hi, length: i.length_m, label: `${fmtNum(t.s_m, 0)} m` });
    }
  } catch (e) { stage.append(fallbackMap(String(e.message || e))); }

  if (ctx.demo?.step === 2) setTimeout(() => { named.scrollIntoView({ behavior: "smooth", block: "center" }); named.classList.add("spotlight"); }, 250);
  if (ctx.demo) screen.classList.add("is-demo");
  return () => disposers.forEach((d) => { try { d(); } catch (e) { console.error(e); } });
}
