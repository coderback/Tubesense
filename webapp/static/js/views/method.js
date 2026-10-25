// Privacy & method: the honest scoreboard (NO-GOs included), pre-registration, the verification loop, the data inventory,
// and a live log of what left this laptop during this session (server side) plus what this browser requested (client side).
import { h, mount as mountEl, fmtNum, fmtUTC, fmtTimeLocal, icon, extLink } from "../util.js";
import { fetchJSON } from "../api.js";
import { clientEgress, on } from "../state.js";
import { card, chip, provChip, screenHeader, whereFrom, verdict, errorCard, emptyState, skeleton } from "../ui.js";

const INVENTORY = [
  ["Raw audio (data/raw/audio/*.m4a)", "Voice Memos recordings of each ride", "Laptop only, gitignored", "No", "Deleted on 1 Nov 2026"],
  ["Raw sensors (data/raw/phyphox/*.xlsx)", "Accelerometer, gyroscope, magnetometer, barometer; device model, wall-clock times", "Laptop only, gitignored", "No", "Kept for re-analysis, never uploaded"],
  ["NIOSH readings and ride notes", "Trip-level LAeq, max and LCpeak (no audio kept); car number, seat placement, incidents", "Laptop only, gitignored", "Trip levels only, in outputs_posthoc/h5_niosh.md", "Kept for re-analysis"],
  ["Audio-derived features (outputs/noise/*_levels.csv)", "1-second A-weighted levels, third-octave bands, a crude speech flag", "GitHub (public)", "Yes, published", "Permanent"],
  ["Positioning and QC outputs", "Derived sensor features, section timings, tracker results", "GitHub (public)", "Yes, published", "Permanent"],
  ["Atlas documents", "Public TfL and London Assembly documents", "MongoDB Atlas (AWS eu-west-1)", "Yes", "Until the project ends"],
  ["Atlas section_stats, attempts, claims, approvals", "Summary statistics, the analyst's metrics log, claims, approval records", "MongoDB Atlas", "Yes", "Until the project ends"],
  ["Gemini prompts", "Public document text, summary statistics, tracker metrics, and a typed question", "Google Gemini API", "Yes", "Google logs prompts for a limited period for abuse monitoring; we do not claim zero retention"],
];
const LOOP = [
  ["Leave-one-run-out.", "Every positioning number comes from a ride the map was not built from. The live tracker rebuilds its map without the ride being shown."],
  ["Independent truth.", "There is no GPS in a tunnel, so the reference comes from physical landmarks: curve edges matched across rides. It shares the gyro with the tracker, so it is not ground truth."],
  ["Fair tests.", "The gyro is hidden around each landmark to test positioning between curves; that result is reported even though it is unflattering."],
  ["Adversarial review.", "Each result was attacked by independent checking passes before being reported; the problems they caught are documented in FINDINGS.md."],
  ["Pre-registration.", "The 24 Oct analysis was written down and frozen (git tag prereg-24oct) before those rides. Anything decided later is labelled post-hoc."],
  ["The agent cannot mark its own homework.", "Argon proposes settings from an allow-list; a harness it cannot edit scores them with a rule fixed before the session; every call is hash-chained and mirrored to Atlas; one sealed scoring was allowed on the 24 Oct rides."],
  ["Every claim in the evidence pack is checked by code.", "agent/verify.py requires each quote to match the stored source verbatim and each number to trace to a quote, our statistics or a recomputed formula, and rejects known overclaims (for example 92.8 dB as TfL's own data)."],
];
const RULES = ["Raw audio is decoded in memory and never written anywhere; no WAV, cache, spectrogram or clip is saved.", "Only 1-second levels and third-octave bands are kept; no speech.",
  "No audio and no raw sensor data are sent to Gemini or Atlas. Gemini sees public documents, summary statistics, tracker metrics and the question you type.",
  "Raw recordings are deleted on 1 Nov 2026. The ICO calls recording conversations “highly intrusive”, so no audio is kept at all.",
  "If other people contribute rides: random per-trip IDs, no wall-clock times in Atlas, and a section is published only once enough trips from several contributors cover it."];
const LIMITS = ["2–3 rides per section, one phone, two days.", "Levels are relative (dBFS(A), uncalibrated). The NIOSH app is validated only to about 85 dB(A).",
  "Errors are agreement with a curve-edge reference that shares the gyro; between curves the tracker is no better than a timetable.",
  "Corrugation is an open hypothesis: the dominant roar peaks are fixed-frequency resonances, so frequency / speed gives no wavelength.",
  "Not a sound level meter, not a safety system, not a hearing-loss claim."];
const POSTHOC = ["Tracker v2 (settings chosen by Argon on the 23 Oct data) and its sealed score", "The live replay, this web app, Ask Argon and the live analyst session", "The model comparison (Why Argon)", "The evidence-pack investigator run, its reviews and the approval step"];

function scoreboard(M) {
  return card({ title: "The honest scoreboard", chips: chip("prereg"), body: h("div", { class: "table-wrap" }, h("table", { class: "scoreboard" },
    h("thead", null, h("tr", null, ["question", "verdict", "evidence", "source", ""].map((t) => h("th", null, t)))),
    h("tbody", null, M.scoreboard.map((r) => h("tr", null, h("td", null, r.question), h("td", null, verdict(r.verdict)), h("td", null, r.evidence), h("td", { class: "mono muted" }, r.source), h("td", null, provChip(r.label))))))),
    footer: "We publish the NO-GOs too. The 24 Oct analysis was frozen before the rides (git tag prereg-24oct)." });
}

function egressCard() {
  const body = h("div"), card_ = card({ title: "What left this laptop in this session", chips: chip("live"), body, footer: "Server entries are in memory only and reset when the server restarts. Client entries are requests your browser made itself." });
  async function paint() {
    let server = [], since = null, err = null;
    try { const r = await fetchJSON("/api/egress"); server = (r.entries || []).map((e) => ({ ...e, client: false })); since = r.since; } catch (e) { err = e.message; }
    const all = [...server, ...clientEgress].sort((a, b) => String(b.ts).localeCompare(String(a.ts)));
    mountEl(body, err ? errorCard({ title: "Egress log not available", message: err, retry: paint }) : null, since ? h("p", { class: "help" }, `Server started ${fmtUTC(since)}.`) : null,
      all.length ? h("div", { class: "table-wrap" }, h("table", null, h("thead", null, h("tr", null, ["time", "service", "what was sent", "by"].map((t) => h("th", null, t)))),
        h("tbody", null, all.map((e) => h("tr", null, h("td", { class: "mono" }, fmtTimeLocal(e.ts)), h("td", null, e.service), h("td", null, e.sent, e.count > 1 ? h("span", { class: "muted" }, ` (${e.count} requests)`) : null), h("td", null, e.client ? chip("neutral", "BROWSER") : chip("neutral", "SERVER"))))))) : emptyState("Nothing has left this laptop in this session yet."));
  }
  paint();
  card_.refresh = paint;
  return card_;
}

export async function mount(root, ctx) {
  const screen = h("div", { class: "screen method" });
  root.append(screen);
  const head = h("div"), top = h("div", { class: "stack" });
  const eg = egressCard();
  screen.append(head, top);
  mountEl(head, screenHeader({ title: "Privacy & method", subtitle: "What was claimed, what failed, how it was checked, and what data exists.", chips: [chip("prereg", "PRE-REGISTERED (prereg-24oct)"), chip("posthoc")] }));
  mountEl(top, card({ body: skeleton(8) }));
  let M = null, err = null;
  try { M = await fetchJSON("/api/method"); } catch (e) { err = e; }
  const pre = M?.prereg, s = M?.sealed;
  const blocks = [];
  blocks.push(err ? errorCard({ title: "Scoreboard not available", message: err.message, retry: () => mount(root, ctx) }) : scoreboard(M));
  blocks.push(card({ title: "Pre-registration", chips: [chip("prereg"), chip("posthoc", "POST-HOC after the tag")], body: [
    pre ? h("dl", { class: "kv" }, h("dt", null, "git tag"), h("dd", { class: "mono" }, pre.tag), h("dt", null, "commit"), h("dd", { class: "mono" }, String(pre.tag_commit || "n/a").slice(0, 12)),
      h("dt", null, "frozen code unchanged since the tag"), h("dd", null, pre.frozen_unchanged === null ? "unknown (git unavailable)" : pre.frozen_unchanged ? verdict("MATCH", "yes: tubesense/ and scripts/ are identical to the tag") : verdict("BROKEN", "NO: the frozen code changed")),
      h("dt", null, "checked"), h("dd", { class: "mono" }, `now (${fmtTimeLocal(pre.checked_at)})`)) : emptyState("Pre-registration status not available."),
    h("p", null, "Decided after the tag and labelled post-hoc everywhere in the app:"), h("ul", null, POSTHOC.map((t) => h("li", null, t)))] }));
  if (s) blocks.push(card({ title: "The sealed test", chips: [verdict("MATCHED, NOT BEATEN"), chip("posthoc")], body: [
    h("p", null, `After the tuning session the chosen configuration was scored once on rides recorded after it was chosen (attempt 8, ${fmtUTC(s.v2.ts)}). Result: median ${fmtNum(s.v2.median_error_m, 1)} m, 90 % coverage ${fmtNum(s.v2.coverage_mean, 3)} on ${s.v2.n_runs} sections. The pre-registered tracker v1 scored ${fmtNum(s.v1.median_m, 1)} m, coverage ${fmtNum(s.v1.coverage, 3)}.`),
    h("p", null, "The tuning gain did not carry over, and the hash-chained log shows there was one try.")] }));
  blocks.push(card({ title: "The verification loop", body: h("ul", { class: "loop" }, LOOP.map(([a, b]) => h("li", null, h("strong", null, a), ` ${b}`))) }));
  blocks.push(card({ title: "Privacy: what data exists", body: [h("div", { class: "table-wrap" }, h("table", null, h("thead", null, h("tr", null, ["data", "contents", "where it lives", "leaves the laptop?", "retention"].map((t) => h("th", null, t)))),
    h("tbody", null, INVENTORY.map((r) => h("tr", null, r.map((c, i) => h("td", { class: i === 0 ? "strong" : null }, c))))))), h("h3", null, "Rules"), h("ul", null, RULES.map((t) => h("li", null, t)))],
    footer: "From docs/PRIVACY.md. The public inventory shows the contributor's own journey times, published with consent." }));
  blocks.push(eg);
  blocks.push(card({ title: "Map tiles and fonts", body: h("p", null, "Map tiles are fetched by your browser from OpenStreetMap (tile.openstreetmap.org); requests reveal the viewed area of east London, no ride data. Fonts come from Google Fonts. Both are optional: with the network off the app uses system fonts and the SVG schematic."), cls: "tiles-note" }));
  blocks.push(card({ title: "Limits", body: h("ul", null, LIMITS.map((t) => h("li", null, t))) }));
  mountEl(top, blocks);
  const off = on("egress", () => eg.refresh());
  const t = setInterval(() => eg.refresh(), 15000);
  screen.append(whereFrom("GET /api/method", "GET /api/egress", "outputs/FINDINGS.md", "docs/PRIVACY.md", "README (Verification loop)"));
  return () => { off(); clearInterval(t); };
}
