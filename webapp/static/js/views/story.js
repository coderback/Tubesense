// Story: the problem, the named user, and how the pipeline answers. Public-record figures only; no API calls beyond /api/health (shell).
import { h, mount as mountEl, icon } from "../util.js";
import { chip, card, statTile, screenHeader, whereFrom } from "../ui.js";

const PIPE = [
  ["RIDE", "A phone in a pocket: accelerometer, gyro, magnetometer, barometer, audio.", "#/ride"],
  ["CLEAN", "Sync, pauses, a phone on the floor, split runs: flagged from the data.", "#/ride"],
  ["LOCATE", "Curve signatures place the train between stations. No GPS.", "#/ride"],
  ["MEASURE", "1-second A-weighted levels and bands. Raw audio is never kept.", "#/method"],
  ["INVESTIGATE", "Gemini 4 Argon reads TfL and Assembly records from MongoDB Atlas.", "#/ask"],
  ["APPROVE", "Code checks every quote and number. A human signs the draft.", "#/pack"],
];

export async function mount(root, ctx) {
  const screen = h("div", { class: "screen story" });
  root.append(screen);
  const hero = h("section", { class: "hero card" },
    h("div", { class: "hero__num", "aria-label": "87 decibels" }, "87", h("span", { class: "hero__unit" }, "dB")),
    h("div", { class: "hero__text" },
      h("p", { class: "hero__caption" }, "TfL's own in-cab LAeq, Liverpool Street → Bethnal Green, Central line, eastbound. Measured 24 June 2024, released under FOI-1231-2425."),
      h("div", { class: "chip-row" }, chip("precomputed", "PRECOMPUTED · public records"))));
  const tiles = h("div", { class: "grid grid--3" },
    statTile({ value: "83", unit: "dB", caption: "Bethnal Green → Mile End, same TfL measurement", source: "TfL FOI-1231-2425" }),
    statTile({ value: "85", unit: "dB(A)", caption: "HSE upper exposure action value, for reference", source: "HSE, Control of Noise at Work Regulations 2005", tone: "slate" }),
    statTile({ value: "92.8", unit: "dB", caption: "Reported in the press (Brixton Buzz, 27 Jan 2026). Primary source not located.", source: "Brixton Buzz, 27 Jan 2026 (secondary source, reported figure)", tone: "amber" }));
  const grinding = card({ cls: "card--flat", body: h("p", { class: "lead" }, "Both sections were ground for noise between December 2021 and May 2022 (Mayor's Question 2022/1791). TfL says grinding must be repeated “usually 6 months – a year” (TfL Tube Noise presentation, 6 Dec 2022)."),
    footer: "Sources: Mayor's Question 2022/1791; TfL Tube Noise presentation, 6 Dec 2022" });
  const named = card({ id: "named-user", cls: "named", title: "Elly Baker AM", chips: chip("precomputed", "PRECOMPUTED · public record"), body: [
    h("p", { class: "muted" }, "Labour transport spokesperson, London Assembly"),
    h("p", null, "At Mayor's Question Time on 16 July 2026 (2026/2156) she said TfL had turned down most of her data requests on residents' noise complaints and the measures taken, including a six-month grinding plan, because grinding is planned only eight weeks ahead. Tubesense gives her (1) phone measurements placed between stations without GPS and (2) a cited evidence pack that says what the public record does and does not show, checked by code and approved by a human."),
    h("p", { class: "muted" }, "The people it ultimately protects are those exposed longest: train drivers and their unions (RMT, ASLEF).")],
    footer: "Source: Mayor's Question 2026/2156, 16 Jul 2026. This does not imply she has seen or endorsed Tubesense." });
  const pipe = h("ol", { class: "pipeline-strip" }, PIPE.map(([name, text, href], i) => h("li", null, h("a", { href }, h("span", { class: "pipe__n" }, String(i + 1).padStart(2, "0")), h("strong", null, name), h("span", { class: "pipe__t" }, text)))));
  const ctas = h("div", { class: "row" }, h("a", { class: "btn", href: "#/demo" }, icon("play", 16), "Start the 3-minute demo"), h("a", { class: "btn btn--secondary", href: "#/ride" }, "Open live tracking"));
  screen.append(
    screenHeader({ title: "Ordinary rides. No GPS. Every claim checked.", subtitle: "Tubesense turns phone recordings of Tube rides into a section-by-section noise record, then drafts a cited evidence pack that a human approves.", chips: [chip("precomputed", "PRECOMPUTED · public records")] }),
    hero, tiles, grinding, named, card({ title: "How it works", body: pipe }), ctas,
    h("p", { class: "scope" }, "Not a sound level meter, not a safety system, not a hearing-loss claim."),
    whereFrom("TfL FOI-1231-2425", "Mayor's Question 2022/1791", "TfL Tube Noise presentation (6 Dec 2022)", "Mayor's Question 2026/2156", "README.md"));
  if (ctx.demo?.step === 2) setTimeout(() => { named.scrollIntoView({ behavior: "smooth", block: "center" }); named.classList.add("spotlight"); }, 250);
  if (ctx.demo) screen.classList.add("is-demo");
}
