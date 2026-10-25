// Research Box v2: a design concept, not built. Content is the owner's drawing, deck slide 13 and the interactive HTML, verbatim.
import { h, mount as mountEl, icon } from "../util.js";
import { chip, card, screenHeader, whereFrom, modal } from "../ui.js";

const UPGRADES = [
  ["Timebase", "Two apps, synced by a knock", "GNSS PPS + RTC, one hardware clock"],
  ["Acoustic", "Voice Memos limiter, relative only", "2 × I²S MEMS mics, dB(A) on device"],
  ["Magnetic", "One sensor beside the battery", "3 × RM3100 gradient array on a boom"],
  ["Speed", "Inferred from position", "Radar Doppler + sleeper frequency"],
  ["Inertial", "Phone IMU, ~99 Hz", "ADIS16470-class IMU, isolated"],
  ["Pressure", "Phone barometer", "BMP585-class, tens of Hz, vented"],
];
const SPECS = [["Length, with boom", "772 mm"], ["Enclosure", "300 × 180 × 96 mm"], ["Magnetometers", "3 × RM3100, 140 mm pitch"], ["Battery → nearest mag", "≥ 400 mm"],
  ["Inertial", "ADIS16470-class IMU, isolated"], ["Speed sources", "Radar Doppler · sleeper frequency"], ["Pressure", "BMP585-class, tens of Hz, vented"], ["Acoustic", "2 × I²S MEMS, dB(A) on device"],
  ["Timebase", "GNSS PPS + DS3231 RTC"], ["Compute", "Raspberry Pi 5 + RP2040 hub"], ["Est. cost", "£700–1,400 (owned Pi 5)"]];
const COSTS = [
  ["1 · Phone-plus", "Pi 5 (owned), hub, fast barometer, one RM3100, MEMS mic, light sensor, GNSS + RTC, enclosure", "£150–250", "One clock, fast pressure, raw magnetic data, real dB SPL, GPS ground truth above ground"],
  ["2 · Speed", "Vibration accelerometer + fast ADC, mmWave radar", "+£60–350", "Speed underground, which limits dead-reckoning drift"],
  ["3 · High grade", "Industrial IMU, two more RM3100 for the array", "+£300–600", "The bridge to quantum comparison, magnetic gradient"],
  ["Full box", "All stages, owned Pi 5", "≈ £700–1,400", "A second box for the lag test: ≈ £1,300–2,500 total"],
];
const PARTS = [
  [1, "Top shell", "Vented lid · 4 × M3", "Keeps sensors in a fixed, repeatable layout instead of a phone placed differently each ride."],
  [2, "GNSS antenna", "Active patch · SMA", "Ground truth to score GPS-free positioning where satellites can be seen, e.g. Stratford to Leyton."],
  [3, "MEMS microphones ×2", "TDK ICS-43434 · I²S", "Phone mics are uncalibrated and apply auto-gain. These give real dB(A) with headroom for 100 dB+ peaks."],
  [4, "Raspberry Pi 5", "Edge compute · active cooler", "Raw audio never leaves the box; only levels, bands and features are stored."],
  [5, "Sensor hub", "RP2040 · hardware timestamps", "Ends the syncing problems of matching phone sensors to a separate audio recording."],
  [6, "GNSS receiver + RTC", "u-blox · DS3231 · CR2032", "Accurate time across tunnels, so every sensor lines up with the track map."],
  [7, "Industrial IMU", "ADIS16470 class", "Phone IMUs drift within seconds. This is the classical bridge towards a quantum accelerometer."],
  [8, "Isolated sub-plate", "4 × silicone grommets", "Stops rail vibration from saturating the inertial sensors."],
  [9, "Vibration accelerometer", "ADXL1002 · stud-mounted", "Adds a speed check and a direct rail-roughness and corrugation signal."],
  [10, "Fast barometer", "BMP585 class · vented", "The iPhone logs pressure at about 1 Hz. Fast sampling turns doors and ventilation shafts into landmarks."],
  [11, "mmWave radar", "TI IWR6843 · 60 GHz", "Underground there is no speed reference; radar gives ground speed directly, in the dark."],
  [12, "Light sensor", "VEML7700", "Precise station boundary timestamps to anchor every section."],
  [13, "Power pack", "USB-C PD · strapped", "Batteries distort magnetic readings, so distance is part of the design."],
  [14, "Magnetometer pods ×3", "PNI RM3100 · 140 mm pitch", "The gradient across the array separates fixed track features from traction-current fields."],
  [15, "Carbon boom", "Ø12 mm · 440 mm", "Keeps the magnetometers ≥ 400 mm from the battery."],
  [16, "Base plate", "6 mm aluminium", "Every run is mounted the same way, so runs stay comparable."],
  [17, "Isolator feet", "4 × rubber mounts", "Protects the electronics while the vibration sensor stays rigidly coupled."],
];
const tbl = (head, rows) => h("div", { class: "table-wrap" }, h("table", null, h("thead", null, h("tr", null, head.map((t) => h("th", null, t)))), h("tbody", null, rows.map((r) => h("tr", null, r.map((c, i) => h("td", { class: i === 0 ? "strong" : null }, c)))))));

export async function mount(root) {
  const screen = h("div", { class: "screen box" });
  root.append(screen);
  const img = h("img", { src: "img/research-box-exploded.png", alt: "Exploded isometric drawing of the Research Box v2: 17 numbered parts including a Raspberry Pi 5, sensor hub, two MEMS microphones, GNSS antenna, mmWave radar and a carbon boom carrying three magnetometer pods.", width: 2676, height: 1600, loading: "lazy", class: "box__img" });
  const pic = h("picture", null, h("source", { media: "(max-width: 900px)", srcset: "img/research-box-exploded-1400.webp", type: "image/webp" }), img);
  const zoom = h("button", { type: "button", class: "box__zoom", "aria-label": "Open the drawing full screen", onclick: () => {
    let m;
    const closeBtn = h("button", { class: "btn btn--secondary", type: "button", onclick: () => m.close() }, "Close");
    m = modal({ title: "Research Box v2, exploded view (concept, not built)", cls: "overlay--wide", labelledby: "box-zoom", body: h("div", { class: "box__full" }, h("img", { src: "img/research-box-exploded.png", alt: img.alt }), closeBtn) });
  } }, pic, h("span", { class: "box__zoomhint" }, icon("zoom", 16), "Open full screen"));
  screen.append(
    screenHeader({ title: "Research Box v2", subtitle: "Multi-sensor instrument for GPS-free train positioning · exploded isometric · concept, not built", chips: chip("concept", undefined, { large: true }) }),
    card({ title: "Fig. 1 · exploded view", chips: chip("concept"), body: zoom, footer: "Concept drawing by the author, 24 Oct 2026 (TS-RB v2, rev. A). Not a tested design." }),
    card({ title: "Each upgrade answers a NO-GO", body: tbl(["Signal", "Phone today", "Research Box v2"], UPGRADES) }),
    card({ title: "Specifications", body: tbl(["Item", "Value"], SPECS) }),
    card({ title: "Cost to build", body: tbl(["Stage", "Adds", "Rough cost", "Unlocks"], COSTS),
      footer: "Part numbers are examples to verify, not a tested bill of materials. Rough retail estimates including VAT, before shipping. Not checked against live listings." }),
    h("details", { class: "card" }, h("summary", null, "What each of the 17 parts fixes"), h("div", { class: "card__body" }, tbl(["#", "Component", "Example part", "Limit it removes"], PARTS.map(([n, a, b, c]) => [String(n).padStart(2, "0"), a, b, c])))),
    h("div", { class: "row" }, h("a", { class: "btn btn--secondary", href: "concept/research-box.html", target: "_blank", rel: "noopener noreferrer" }, icon("link", 16), "Open the interactive drawing (needs internet; loads three.js from jsdelivr)")),
    h("p", { class: "help" }, "Deployment on trains would need TfL permission. No partnership with TfL, MoniRail or Imperial is implied."),
    whereFrom("static/img/research-box-exploded.png", "static/concept/research-box.html (a byte copy of the owner's interactive drawing)", "deck slide 13, “Each upgrade answers a NO-GO”"));
}
