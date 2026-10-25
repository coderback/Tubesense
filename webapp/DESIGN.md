# Tubesense web app: design and implementation contract

Status: design for two parallel engineers (backend, frontend). Author: B4 design pass, 25 Oct 2026.
This file is the contract. If something here is ambiguous, the backend engineer decides the
JSON and states the decision in `webapp/server.py`'s docstring; the frontend follows the
example payloads below.

The web app replaces the terminal demo (`demo/ride_v2.py`, `demo/replay_analyst.py`,
`demo/atlas_show.py`, `agent/approve.py`) with one local app that a judge can follow in 3 minutes
and that a phone can open over the LAN. It adds no new science. Every number it shows comes from
code that already exists, recomputed now, or from a file, and the screen says which.

Contents
1. Principles and hard rules
2. Product flow and Demo mode
3. Visual language
4. Screens, one by one
5. Architecture, processes and file layout (ownership)
6. HTTP API (exact)
7. The ride worker (real compute, step by step)
8. Reuse map: which existing function each endpoint calls
9. Refactor of `agent/approve.py` (identical behaviour)
10. States: loading, error, offline, busy, empty
11. Acceptance tests
12. Measured numbers this design relies on
13. Open issues and risks

---

## 1. Principles and hard rules

1. **Every output carries its provenance label.** No ride, agent or Atlas result is rendered
   without one of the chips in §3.4: LIVE, REPLAY, PRECOMPUTED, CACHED, SYNTHETIC, POST-HOC,
   PRE-REGISTERED, SEEDED, CONCEPT · NOT BUILT, MOCK DATA. A chip also gives the original timestamp
   when the content is not from now.
2. **Ride tracking is real compute.** The backend runs the recorded ride through the tracker
   now, step by step, paced by the wall clock (§7). A precomputed stream exists only as a fallback,
   and is labelled PRECOMPUTED with its generation time.
3. **Approval is only a human click.** The only code path that calls `POST /api/pack/approve` is
   the submit handler of the approval form. Demo mode never ticks a box, never fills the name and
   never submits. The backend refuses approval from non-loopback clients unless started with
   `--lan-approve`.
4. **Nothing private leaves the laptop.** The web app itself never calls Gemini. Gemini is called
   only by `agent.ask` and `agent.live_analyst`, run as subprocesses, and they send public document
   text, summary statistics and tracker metrics only. The typed question is the only user text
   sent; the UI says so next to the input. No raw audio, raw sensor arrays or speech are sent or
   written anywhere by the app. The ride worker reads cached 1-s levels; if they are missing,
   `scripts/demo.py live_noise` decodes audio in memory, as the CLI does. Nothing writes audio.
5. **Honest about uncertainty.** Phone levels are "dBFS(A), relative, uncalibrated". Position
   errors are "agreement with an offline curve-edge reference (shares the gyro; not ground truth)".
   The geographic view is "schematic: straight chords between stations". NO-GO results are on the
   Method screen in plain words, and the v2 sealed test says "matched v1, did not beat it".
6. **Frozen code is imported, never edited.** `tubesense/`, `scripts/`, `posthoc/`,
   `agent/harness.py`, `agent/verify.py`, `agent/_eval.py` and `demo/*.py` are imported read-only.
   Only `agent/approve.py` may be refactored (§9).
7. **RAM is tight (about 2 GB free).** At most one heavy job runs at a time: a ride, the live
   analyst or a stream replay. The backend enforces this (§6.3). The web server process loads no
   sensor data and must not import `tubesense.stops`, `tubesense.ingest`, `tubesense.online`,
   `posthoc.*` or `demo.ride_v2` (measured: `tubesense.stops` alone pulls in pandas and scipy, 3.3 s).
   numpy is acceptable (it comes in with `agent.harness`, 0.3 s). Heavy work lives in subprocesses
   that exit when done.
8. **Never print secrets.** `/api/health` reports only booleans for `GEMINI_API_KEY` and
   `MONGODB_URI`. No endpoint returns `.env` content, and no log line contains it.

---

## 2. Product flow and Demo mode

### 2.1 The story (3:00, as in `docs/DEMO.md`)

| Beat | Time | Screen | What is shown | Label |
|---|---|---|---|---|
| 1 Problem | 0:00-0:20 | Story | 87 dB LAeq in-cab (TfL FOI-1231-2425, 24 Jun 2024), 83 dB on BTH→MLE, 85 dB(A) HSE upper action value for reference, 92.8 dB "reported in the press, primary source not located" | PRECOMPUTED (public records) |
| 2 Named user | 0:20-0:35 | Story | Elly Baker AM card; MQT 2026/2156 (16 Jul 2026): TfL turned down most of her data requests on noise complaints and measures (incl. a six-month grinding plan) because grinding is planned only 8 weeks ahead | PRECOMPUTED (public record) |
| 3 Live tracking | 0:35-1:15 | Ride | R9 EB Liverpool Street→Bethnal Green (24 Oct, out-of-sample), tracker v2, 8×: dot, 90 % band, confident noise paint, geographic view, QC flags | REPLAY of a recorded ride · LIVE compute · POST-HOC v2 |
| 4 Agent loop | 1:15-1:50 | Agent | Recorded Argon analyst session, compressed to about 30 s: 6 experiments, 1 accepted, hash chain checked now, sealed test 1.8 m = v1 1.8 m; when the replay ends, one sub-card: "Argon reads TfL's own roughness charts" (§4.10) | REPLAY (recorded 24 Oct 07:58-08:05 UTC); sub-card PRECOMPUTED + PENDING HUMAN CHECK |
| 5 Verified answer | 1:50-2:30 | Verified (Ask + Atlas + canary) | One question: Atlas three ways (LIVE), Argon claims with verifier verdicts (LIVE if the background ask finished, else CACHED), seeded canary REJECTED | LIVE / CACHED / SEEDED |
| 6 Approve | 2:30-3:00 | Pack | Claims verified now, checklist, name, Approve as draft, signature | PRECOMPUTED pack · LIVE verifier and approval |

Story text, word for word (from README and the deck; do not strengthen it):
- Hero: "87 dB" / caption "TfL's own in-cab LAeq, Liverpool Street → Bethnal Green, Central line,
  eastbound. Measured 24 June 2024, released under FOI-1231-2425."
- Tiles: "83 dB · Bethnal Green → Mile End, same TfL measurement"; "85 dB(A) · HSE upper exposure
  action value, for reference"; "92.8 dB · Reported in the press (Brixton Buzz, 27 Jan 2026).
  Primary source not located."
- "Both sections were ground for noise between December 2021 and May 2022 (Mayor's Question
  2022/1791). TfL says grinding must be repeated 'usually 6 months – a year' (TfL Tube Noise
  presentation, 6 Dec 2022)."
- Named user: "Elly Baker AM, Labour transport spokesperson, London Assembly. At Mayor's Question
  Time on 16 July 2026 (2026/2156) she said TfL had turned down most of her data requests on
  residents' noise complaints and the measures taken, including a six-month grinding plan, because
  grinding is planned only eight weeks ahead. Tubesense gives her (1) phone measurements
  placed between stations without GPS and (2) a cited evidence pack that says what the public record
  does and does not show, checked by code and approved by a human." Footer: "The people it ultimately
  protects are those exposed longest: train drivers and their unions (RMT, ASLEF)." Do not imply she
  has seen or endorsed Tubesense.
- Pipeline strip (6 steps, from the deck): RIDE · CLEAN · LOCATE · MEASURE · INVESTIGATE · APPROVE.
- Scope line: "Not a sound level meter, not a safety system, not a hearing-loss claim."

### 2.2 Demo mode

Route `#/demo`. A demo bar is fixed to the bottom of the viewport (top on phones): six numbered
steps, the current step's label chips, an elapsed clock (mm:ss since step 1) with the beat's target
window in grey ("0:35-1:15"), and Prev / Next buttons. Keys: → or PageDown = next, ← or PageUp = prev,
Space = play/pause the current replay, Esc = leave demo mode, `?` = key help. A presenter clicker
(sends PageDown) therefore drives the whole demo.

Each step navigates to its screen and sets that screen into a "demo layout" (larger type, secondary
panels collapsed). Clicks needed for the whole story: 5 × Next, then the approval form (5 ticks, a
name, Approve, Confirm).

Pre-warming (all started on entering step 1, all optional, each shows its own state in a small
"Backstage" popover on the demo bar so the presenter can see readiness):
1. `POST /api/ride/start` with `hold: true` for the demo ride (default R9 "EB LST->BTH", v2, 8×,
   truth on). The worker loads the run, builds the leave-one-out map, finds the departure (about
   10 s cold) and then waits. On entering step 3 the frontend calls `POST /api/jobs/{id}/go`.
   If the job is not ready, step 3 shows its loading phases; if it failed, step 3 offers
   "Restart" and "Play PRECOMPUTED".
2. `GET /api/analyst/session?atlas=1` (cached in memory by the frontend).
3. `GET /api/atlas/search?q=<demo question>` (live, about 6 s; falls back to CACHED itself).
4. `GET /api/pack` and `GET /api/canary`.
5. Optional, setting "Ask Argon live in the background" (default ON only if `/api/ask/status`
   says available and the Gemini key is present): `POST /api/ask/start` with the demo question.
   It is a paid Gemini call, so (25 Oct, implemented in `demo.js`): it starts automatically only
   when the demo is entered at step 1 or 2 (a reload that lands on a later step never starts one);
   an ask already running on the server is followed (`GET /api/jobs`, its buffered events are
   replayed) instead of starting a second; a saved answer to the same question younger than 5
   minutes is reused (shown CACHED). "Ask now" in the demo settings is the presenter's explicit
   override. No other screen starts a live ask by itself: the Ask screen only on its Ask button.
   On step 5 the Verified screen shows this job: finished = LIVE result with its elapsed time
   ("asked live at 0:00, answered after 2 min 10 s"); still running = live progress plus the
   newest cached answer side by side, labelled CACHED with its timestamp.

Demo settings (gear icon on the demo bar; kept in `localStorage`, wrapped in try/catch):
- Ride beat: "R9 out-of-sample (default)" or "R5 + SYNTHETIC 30 s stop (shows the stop gate)"
  (R5 "EB LST->BTH", v2, `synthetic_stop: 0.5`, `stop_variant: "quiet"`, 8×; this is the
  `docs/DEMO.md` beat).
- Speed: 4× or 8× (default 8×: R9 takes about 21 s, R5 + stop about 25 s).
- Demo question (default "Was the Central line between Liverpool Street and Bethnal Green ground to
  reduce noise, and when?").
- Live Ask in background: on/off.
- Analyst replay length: 20 / 30 / 45 s (default 30).

Step 5 layout ("Verified answer", route `#/demo` step 5 renders a composite of three existing
components): left 60 %, the Ask result (final claims, dropped claims struck through, verdict chips);
right 40 %, the Atlas three-ways mini (top 1 hit per mode, ranks, ms, LIVE/CACHED chip) and the
canary card (claim text, SEEDED chip, "Verifier: REJECTED" with reasons, LIVE chip because the
verifier runs now). On phones these stack.

Outside demo mode every screen is reachable from the nav, with full detail.

### 2.3 Navigation

Routes (hash routing, no build step):

| Route | Screen | Nav label |
|---|---|---|
| `#/` | Story | Story |
| `#/ride` | Live tracking | Ride |
| `#/agent` | Analyst (tabs: Recorded session, Live mini session) | Agent |
| `#/ask` | Ask Argon | Ask Argon |
| `#/work` | Argon at work (tabs TfL roughness charts, Re-derived numbers, Ride reports; `?tab=roughness`, `recheck`, `reports`) | Argon at work |
| `#/live` | Live sensors (phone via phyphox, labelled MOCK replay, or the mobile app prototype) | Live sensors |
| `#/pack` | Evidence pack and approval | Evidence pack |
| `#/atlas` | MongoDB Atlas | Atlas |
| `#/why-argon` | Model comparison | Why Argon |
| `#/method` | Privacy & method | Privacy & method |
| `#/box` | Research Box v2 (concept) | Research Box |
| `#/demo` | Demo mode (overlay on the routes above) | Demo (button, top right) |

Query parameters: `#/ride?run=R5&track=EB%20LST-%3EBTH&tracker=v2&speed=8&stop=0.5&variant=quiet`
(deep links); `?mock=1` on the page URL (not the hash) turns on mock mode (§5.4).

Desktop (≥ 1024 px): left nav rail, 232 px, with the wordmark, the nav items, and at the bottom the
connection status ("Server ●", "Internet ●", "Atlas ●" from the last result) and the Demo button.
Tablet (640-1023 px): rail collapses to 64 px icons with tooltips. Phone (< 640 px): bottom tab bar
with Story, Ride, Agent, Pack, More; More opens a sheet with Ask Argon, Argon at work, Live sensors, Atlas, Why Argon,
Privacy & method, Research Box. 16 px side gutters; no horizontal page scroll (wide tables and code
scroll inside their own container).

---

## 3. Visual language

Dark theme only, designed for screen recording at 1920×1080 and 1280×720 and for a phone at
390×844. The palette follows the owner's deck (navy #0E2E68, ink #E9F1FF, amber #F2B33D; fonts
Oswald, IBM Plex Sans, JetBrains Mono) so the app, the deck and the Research Box drawing read as one
project.

### 3.1 Colour tokens (CSS custom properties on `:root`)

| Token | Value | Use |
|---|---|---|
| `--bg` | `#070B14` | page background (set explicitly on `body`) |
| `--surface` | `#0D1424` | cards |
| `--surface-2` | `#121C31` | raised panels, table header, inputs |
| `--surface-3` | `#18253F` | hover, selected row |
| `--line` | `#23314F` | borders, dividers, axis lines |
| `--navy` | `#0E2E68` | brand fills (nav active, headers) |
| `--blue` | `#1F4C95` | secondary brand, PRE-REGISTERED chip |
| `--ink` | `#E9F1FF` | primary text |
| `--ink-2` | `#A9C2EC` | secondary text |
| `--ink-3` | `#6F84AD` | muted text, captions, axis labels |
| `--amber` | `#F2B33D` | accent, SYNTHETIC, CONCEPT, CACHED, focus ring |
| `--cyan` | `#4FC3F7` | tracker dot and 90 % band (same as the matplotlib demo) |
| `--green` | `#4ADE80` | verified, ACCEPTED, GO, LIVE dot, chain link ok |
| `--ref` | `#66BB6A` | the hollow curve-edge reference ring (as in the matplotlib demo) |
| `--red` | `#F87171` | rejected, NO-GO, errors, SEEDED |
| `--violet` | `#B39DDB` | REPLAY |
| `--orange` | `#FB923C` | POST-HOC |
| `--slate` | `#94A3B8` | PRECOMPUTED, neutral chips |

Text on `--bg`/`--surface` must meet WCAG AA (4.5:1 body, 3:1 large). `--ink-3` is for 13 px+
captions only. Status is never colour alone: always a word ("verified", "REJECTED", "GO") and an
icon (check, cross, dot).

**Noise colour scale:** inferno, as in `scripts/demo.py`, so the app and the existing renders match.
Use the upper 85 % of inferno, because its black end vanishes on a dark background. Stops (t from 0
to 1, linear RGB interpolation in `js/colors.js`):
`0.00 #420A68, 0.12 #6A176E, 0.25 #932667, 0.37 #BC3754, 0.50 #DD513A, 0.62 #F37819, 0.75 #FCA50A,
0.87 #F6D746, 1.00 #FCFFA4`. Domain: `[vmin, vmax]` from the ride's `init` event (5th and 99th
percentile of the map's bins, as the matplotlib demo). Missing bins (`null`) are drawn as
`--surface-2` with a 45° hatch. The legend says "map LAeq, dBFS(A), relative (uncalibrated)".

### 3.2 Typography

Google Fonts (one `<link>`, `display=swap`): Oswald 500/600, IBM Plex Sans 400/500/600,
JetBrains Mono 400/600. Fallback stacks (used offline, must look fine):
- display: `"Oswald", "Arial Narrow", "Roboto Condensed", Arial, sans-serif`, uppercase, letter-spacing 0.02em
- body: `"IBM Plex Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`
- mono: `"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`

Scale (desktop / phone): hero number 120/72 px display; H1 40/28 display; H2 24/20 display;
H3 18/16 body 600; body 16/15; small 14; caption 13; data and code 14/13 mono. Line height 1.5 body,
1.1 display. Numbers in readouts use `font-variant-numeric: tabular-nums`. In demo layout every size
above goes up one step (body 18 px, readouts 22 px), so text stays legible in a compressed video.

### 3.3 Spacing, shape, motion

4 px base: 4, 8, 12, 16, 24, 32, 48, 64. Cards: radius 12, padding 16 (phone) / 24 (desktop),
1 px `--line` border, no drop shadows (shadows smear in video). Chips: radius 6, height 24,
padding 0 8, 12 px mono uppercase. Buttons: height 40 (44 on touch), radius 8; primary = `--amber`
background with `#1A1203` text; secondary = outline `--line`, text `--ink`; danger = outline
`--red`. Focus ring: 2 px `--amber`, offset 2. Max content width 1280. Grid gap 16/24.
Motion: 150 ms ease-out for UI; the dot moves with a 100 ms linear transition between ticks; the LIVE
dot pulses at 1 Hz. With `prefers-reduced-motion: reduce` there is no pulse, no shimmer and no
transitions (the dot jumps).

### 3.4 Provenance chips (exact text, colours and when to use them)

| Chip | Style | Meaning | Must appear on |
|---|---|---|---|
| `LIVE` | green outline, pulsing green dot | computed or called now, by this app | ride compute, verifier runs, Atlas live queries, live ask, live analyst, approval |
| `REPLAY` | violet solid, dark text | recorded data or a recorded session replayed | ride source data ("REPLAY of recorded ride R9, 24 Oct"), analyst session |
| `PRECOMPUTED · <date time UTC>` | slate outline | an output computed earlier | evidence pack (25 Oct 03:20 UTC), precomputed ride fallback, public-record figures |
| `CACHED · <date time UTC>` | amber outline | a stored copy of a live call, used as a fallback | Atlas snapshot, cached Ask answers |
| `SYNTHETIC` | amber solid, dark text | inserted for the demo, did not happen | the synthetic 30 s stop (banner while it lasts, and in the run header) |
| `POST-HOC` | orange outline | decided after the pre-registration | tracker v2, its final_config, eval_v2 numbers, the model comparison |
| `PRE-REGISTERED` | blue solid | fixed at git tag `prereg-24oct` before the 24 Oct rides | H1-H5 verdicts, tracker v1 |
| `SEEDED` | red outline | a deliberately false test input | the canary claim |
| `CONCEPT · NOT BUILT` | amber outline, large | design only | Research Box screen header and image caption |
| `MOCK DATA` | red solid, page-wide banner | frontend fixtures, not the backend | every page in mock mode |
| `RELATIVE dB` | slate outline | uncalibrated phone level | next to every phone-derived level |

### 3.5 Components (frontend builds these once and reuses them)

- `Chip(kind, text)`; `ChipRow` in each screen header.
- `Card(title, chips, body, footer)`; `StatTile(value, unit, caption, source)` (the source line is
  mandatory: file path or document reference).
- `Verdict(kind)`: GO, NO-GO, WEAK, OPEN, MEASURED, EXPLORATORY, "MATCHED, NOT BEATEN", ACCEPTED,
  REJECTED, verified, FAILS. Icon + word + colour.
- `ClaimCard(claim, check, opts)`: id, type chip (source / our measurement / calculated /
  inference, the labels used by `agent/pack.py`), text, citations (expand to source drawer), stat
  keys, formula (expression = result), verdict. `opts.struck` renders the text struck through with
  the reasons below (dropped claims).
- `SourceDrawer(chunk_id, quote)`: right-side drawer (bottom sheet on phones): title, publisher,
  date, page, URL (opens in a new tab, `rel=noopener`), the chunk text with the quote highlighted.
- `HashChain(lines)`: a horizontal row of blocks (attempt number, kind, `hash[:8]`), linked by
  lines that turn green when the chain check passed. Wraps on phones.
- `StripMap`, `GeoMap`, `YawChart` (§4.2).
- `EventFeed(events)`: vertical timeline of agent events with timestamps and an auto-scroll that
  stops when the user scrolls up ("Jump to latest" pill).
- `Markdown(md)`: a small renderer in `js/md.js` (about 120 lines): headings, paragraphs, lists,
  ordered lists, bold, italic, inline code, code blocks, links, `~~strike~~`, `---`, pipe tables,
  footnote references `[^n]` and definitions. It **escapes HTML first** and never uses raw
  `innerHTML` on model or document text.
- `Skeleton(lines)`, `ErrorCard(message, retry, fallback)`, `EmptyState(text, action)`,
  `Toast(text)`, `Banner(kind, text)`.
- `Segmented(options)`: for speed 1×/4×/8× and tracker v1/v2.

---

## 4. Screens, one by one

Each screen header: H1 title, one-line subtitle, chip row. Each screen lists its data sources in a
"Where this comes from" footer (file paths and endpoints), small `--ink-3` text.

### 4.1 Story (`#/`)

- Hero (§2.1 text): big "87 dB", caption, three `StatTile`s (83 dB, 85 dB(A), 92.8 dB reported).
  Chip: `PRECOMPUTED · public records`.
- Grinding line (MQ 2022/1791; TfL 6 Dec 2022).
- Named user card (§2.1).
- Pipeline strip: six steps; each step links to its screen (LOCATE → Ride, INVESTIGATE → Ask Argon,
  APPROVE → Evidence pack, CLEAN → Ride's QC panel, MEASURE → Method).
- Two buttons: primary "Start the 3-minute demo" (`#/demo`), secondary "Open live tracking".
- Scope line (§2.1).
- No API calls except `/api/health` (for the status dots).

### 4.2 Live tracking (`#/ride`)

Header: "EB Liverpool Street → Bethnal Green · ride R9 · 24 Oct" (from the `init` event) and chips
`REPLAY of recorded ride R9 (24 Oct)` · `LIVE compute` · `POST-HOC tracker v2, config 175b8d99d8c4`
(or `PRE-REGISTERED tracker v1`) · `RELATIVE dB` · `SYNTHETIC stop` (only when one is set). Under the
title, the sample line from `init.sample_note`, for example "R9 is not in the 23 Oct dev data:
out-of-sample for the tracker settings and the map" or "R5 is 23 Oct dev data: in-sample for the v2
config; the map is leave-one-out (built without it)".

Controls (one row on desktop, a collapsible "Ride settings" panel on phones):
- Ride: a select grouped by day from `GET /api/rides` ("R9 · EB LST→BTH · 24 Oct · out-of-sample ·
  offline v2 score 1.8 m"); unavailable rides are disabled with their reason.
- Tracker: segmented v2 / v1.
- Speed: segmented 1× / 4× / 8×. Changing it mid-ride sends `POST /api/jobs/{id}/control
  {"speed": n}`.
- Synthetic stop: off / at 30 % / 50 % / 70 % of the section; variant select (quiet, ideal, dwell)
  with help text from `ride_v2` ("quiet: constant at the run's median dwell level; ideal: far
  quieter than any real stop; dwell: the run's own longest platform dwell replayed, the realistic
  one"). Disabled with a reason when the ride has no recorded dwell.
- Reference overlay: on/off (default on).
- Buttons: Start (primary), Pause/Resume, Stop. While loading, Start shows the phase
  ("Loading run", "Building leave-one-out map", "Finding departure").
- Link "Play PRECOMPUTED instead" (only if `GET /api/ride/precomputed` says available).

Main area, desktop:

```
+-----------------------------------------------------------------------------------+
| STRIP MAP (full width, height 180)                                                |
|  Liverpool Street                                                   Bethnal Green |
|  ▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮  <- painted live squares (this ride)  |
|  ███████████████████[====●====]████████████  <- map noise bins + 90 % band + dot  |
|  ▬▬   ▬▬▬▬▬   ▬▬▬▬▬▬     ▬▬▬▬▬▬▬     ▬ ▬ ▬▬    <- curves (landmarks)                |
|  0        500       1000       1500       2000  m   colour legend (relative dB)   |
|  "metres from Liverpool Street, TfL Working Timetable length 2,270 m"             |
+--------------------------------------+--------------------------------------------+
| GEOGRAPHIC VIEW (schematic)          | READOUT                                    |
|  LST ●───────●────── BTH ───── MLE   |  TRACKING                       (status)   |
|                                      |  t  +62.3 s since departure                |
|                                      |  position  881 m   90 %: 849-881 m         |
|                                      |  tracker conf. 98 % within ±50 m           |
|                                      |  speed 51 km/h                             |
|                                      |  noise now  -17.5 dBFS(A)  RELATIVE        |
|                                      |  map here   -18.2 dBFS(A)                  |
|                                      |  stop gate  off (z +0.26)                  |
|                                      |  vs curve-edge ref  2 m                    |
+--------------------------------------+--------------------------------------------+
| YAW CHART: phone yaw rate (live) vs map yaw at the estimate, deg/s, + = left      |
+-----------------------------------------------------------------------------------+
| MESSY-INPUT QC (automatic, outputs/qc.json): this ride / other rides              |
+-----------------------------------------------------------------------------------+
```

Phone: strip map (height 140) → readout as a 2×4 grid of compact tiles → geographic view (height
220) → yaw chart (height 120) → QC (collapsed, with a count badge).

**StripMap** (SVG, `viewBox="0 0 1000 180"`, `preserveAspectRatio="none"` only for the bars; text is
drawn in a separate non-stretched layer or uses `vector-effect`). x = s_m / length_m.
- Station names at both ends (bold), ticks at 0 and L.
- Map noise row: 40 rectangles from `init.map.bin_edges_m` (41 edges, unequal metres) coloured by
  `init.map.noise_db` (null = hatched).
- Curves row: bars from `init.map.curves_m` ([x0, x1] pairs), label "curves".
- Painted row: one square per `paint` object in ticks, at `paint.s_m`, coloured by `paint.level`;
  opacity 0.45 and the legend note "(LIVE ONLY: ride excluded from noise averages)" if
  `init.live_only`.
- 90 % band: translucent cyan bar from `band_lo` to `band_hi`, height 22.
- Dot: cyan circle r 9 with a 2 px white stroke, at `s_m`.
- Reference: hollow `--ref` ring r 11 at `truth_s_m` when not null and the overlay is on.
- Synthetic stop: while `in_synthetic_stop`, an amber banner over the map: "SYNTHETIC 30 s STOP
  (inserted for the demo)".
- Axis: ticks every 250 m (every 500 m on phones), label `init.axis_label`.
- Legend: colour bar `vmin`..`vmax` "map LAeq, dBFS(A), relative".
- Caption line: "squares: noise this ride is measuring now, painted only where the tracker is
  confident (lock ≥ 50 % within ±50 m and the whole 90 % band narrower than 206 m, held 2 s)". The
  numbers come from `init.paint_rule`.

**GeoMap**. Purpose: show where the section is in London; it is not a GPS track.
- Projection: equirectangular around lat0 = 51.522: x = (lon − lon0) · cos(lat0), y = −(lat − lat0).
- Draw the three Central stations LST, BTH, MLE (coordinates from `init.stations`, which come from
  `tubesense/stops.py STATIONS`) joined by straight chords; the active section's chord is thicker.
- Train position: the point at fraction s_m / length_m along the active chord, from the departure
  station to the arrival station. The 90 % band is a thicker cyan segment along the chord. Painted
  squares are small dots along the chord, offset 6 px to one side.
- Caption, always visible: "Schematic: straight chords between station coordinates (TfL lineStrings
  have no intermediate vertices). Position along the chord = distance / section length; not a GPS
  position."
- Basemap: when online, Leaflet 1.9.4 from `https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/`
  (CSS and JS, pinned, with the SRI hashes published by cdnjs) with CARTO `dark_nolabels` tiles and
  OSM/CARTO attribution, the same chords and markers drawn as Leaflet layers. Fallback to the inline
  SVG when the script fails to load within 4 s, when `navigator.onLine` is false, or when 3 tile
  errors occur. A toggle "Basemap: on/off" sits in the card header (remembered in localStorage).
  The Privacy screen states: "Map tiles are fetched by your browser from CARTO; requests reveal the
  viewed area of east London, no ride data."

**Readout**. Status line large (`WAITING FOR DEPARTURE`, `TRACKING`, `HOLDING: train stopped`,
`ARRIVED`; from `tick.status`), then the rows above from tick fields. `vs curve-edge ref` only when
`truth_s_m` is not null; its tooltip: "offline curve-edge reference, interpolated between curve edges
matched to the map's base run; it shares the gyro with the tracker; not ground truth". Footer text:
"relative levels, uncalibrated · no GPS: gyro curves + speed model".

**YawChart** (canvas, devicePixelRatio aware). x = seconds since departure over the
`init.t_range`; y = −4..4 deg/s; two lines: phone yaw (`tick.yaw`, amber `#FFB74D`) and map yaw at
the estimate (`tick.map_yaw`, white 80 %). Shaded amber span during the synthetic stop, shown only
once the stop starts.

**QC panel**. From `init.qc`: "this ride" lines first (full), then "other rides" (brief), exactly the
human-readable lines `demo/ride_v2.flag_lines` produces; each line gets a severity dot from the raw
flag. Title "MESSY-INPUT QC (automatic, outputs/qc.json)".

**End of ride**. On the `end` event, a summary card slides over the readout: steps, seconds of ride,
wall time, tracker ms/step; if a reference exists, "median |dot − reference| 1.9 m over N steps
(agreement with the offline curve-edge reference, not ground truth)"; for 24 Oct rides, the offline
score from `init.offline_score` ("sealed v2 test, attempt 8: median 1.8 m, 90 % coverage 0.997,
scored once on 25 Oct 00:52 UTC"); with a synthetic stop, "end of stop: dot +4 m from the reference;
gate engaged 2.75 s into the stop" (or "never engaged"). Buttons: Replay, Change ride.

**Honesty footer on this screen** (always visible, small): "Errors are agreement with an offline
curve-edge reference, not GPS truth. Between curves the tracker is no better than a timetable
(11 vs 16 m, FINDINGS (f)). The v2 stop gate never engaged on a real ride (0 of 10 sealed-test
sections; 0 of 3 real platform stops on an Elizabeth line test ride)."

### 4.3 Agent (`#/agent`)

Two tabs.

**Tab 1, Recorded session** (default). Chips: `REPLAY · recorded 24 Oct 07:58-08:05 UTC` ·
`POST-HOC` · `LIVE checks` (for the recomputed checks only).
- Integrity panel (top, always visible), from `GET /api/analyst/session`: "hash chain INTACT, 9
  lines" (or BROKEN at …), "harness SHA-256 on disk = every log line: MATCH", "= harness at tag
  v2-posthoc (147c35e): MATCH", "Atlas mirror: 9/9 log lines found by hash" (or the unreachable
  text), each with "checked now at HH:MM:SS".
- Rule card: allowed settings (`allowed`) and the acceptance rule text (`rule.text`).
- Baseline card: config and metrics, attempt 0.
- Event feed, played back with compressed time: the recorded gaps between events are scaled so the
  whole session takes `replay_s` (default 30 s; speed buttons 20/30/45 s; Step mode advances on
  Next; "Show all" renders everything at once). Between events a thin progress line says "+46 s in the
  recording (Argon reasoning + harness scoring 8 held-out runs)".
  - Tool calls: one line, "Argon called list_experiments()".
  - Experiment card: number, hypothesis (rationale, clamp to 4 lines with "more"), config diff
    chips ("YAW_SD_FLOOR 0.35→0.2"), metrics (median error, coverage, band width and its % change),
    "paired per-run error delta: mean −0.81 m, 95 % CI [−1.94, −0.05] m (the rule uses this, not the
    median)", verdict `ACCEPTED`/`REJECTED` with the decisive reason, and "recomputed now:
    accept() on the logged metrics → same verdict" (green) or "DIFFERENT VERDICT" (red, should never
    happen), plus attempt number and `hash[:12]`.
  - Submit card (amber): the submitted config, `config_hash`, the git tag check.
  - The HashChain component fills in block by block as attempts appear; the last block (attempt 8,
    kind "test") appears with the sealed test card.
- Sealed test card (after the feed): "v2-posthoc sealed test (attempt 8, 25 Oct 00:52:56 UTC):
  median 1.8 m, coverage 0.936, n = 10" vs "v1 pre-registered: 1.8 m, coverage 0.951" with verdict
  chip `MATCHED, NOT BEATEN` and the line "The dev gain did not carry over; the log shows there was
  one try." Rows come from `sealed.rows`.
- Earlier failed session note (amber): `failed_session`.
- Argon's own report, rendered with `Markdown`, inside a quote frame headed "Written by
  gemini-4-argon, verbatim from analyst_session.jsonl. Its words, not ours."
- Caveat line (from the replay script's logic): "a small gain - error CI upper bound −0.05 m (only
  just below 0), 1 of 6 configurations accepted, 8 dev runs from one morning, errors vs a yaw
  curve-edge proxy".

**Tab 2, Live mini session**. Chips: `LIVE · gemini-4-argon · thinking medium` once started.
- Explainer: "Starts a new, short analyst session now: Argon proposes up to 2 experiments, a fixed
  harness scores each on the 23 Oct rides. Each Argon turn takes 30-60 s and scoring about 45 s, so
  expect 2-5 minutes. Sent to Gemini: tracker settings and metrics only. The recorded log and the
  sealed test are not touched." (The last sentence is shown only if the `start` event confirms it,
  e.g. a `log` field naming a scratch log; otherwise show the `start` event's fields verbatim.)
- Select "max experiments: 1 / 2" (default 2), Start, Stop.
- Event feed rendering the contract events: `argon_text` (quote frame), `tool_call` (name + args as
  chips), `tool_result` (collapsed JSON with key metrics pulled out when present: `median_error_m`,
  `coverage_mean`, `band_width_mean_m`), `verdict` (same verdict component as tab 1, with `reason`
  and `metrics`), `chain` ("hash chain checked now: ok, N lines"), `final` (summary card), `error`
  (ErrorCard with "Show the recorded session instead").
- Elapsed timer; a "busy" state if another heavy job runs (§10).

### 4.4 Ask Argon (`#/ask`)

Chips: `LIVE · gemini-4-argon · thinking medium` (live) or `CACHED · <ts>` (cached).
- Input: text field (max 300 characters, counter), Ask button, and 3 suggestion chips:
  "Was the Central line between Liverpool Street and Bethnal Green ground to reduce noise, and
  when?", "What noise levels has TfL itself reported for Liverpool Street to Bethnal Green?",
  "Did TfL refuse to share noise data with Assembly Members?". Note under the input: "Your question
  is sent to Gemini with public TfL and London Assembly documents. Do not type personal data."
- If `/api/ask/status` says unavailable: the input is disabled with the reason, and the cached list
  is shown.
- Pipeline view, five stages in a horizontal stepper (vertical on phones), each with its own card
  that fills as events arrive:
  1. Retrieval: `retrieval.hits` as rows (title, publisher, date, page, score; click opens the
     SourceDrawer) and `stats` as small key chips ("48 section statistics available").
  2. Draft (round n): claims as ClaimCards with type chips, no verdict yet.
  3. Verifier (round n): each claim gets its verdict (check or cross), reasons listed under
     rejected ones. Header "agent/verify.py, deterministic, no model".
  4. Fix round (round n+1): only if there is a second draft; reuses stages 2-3 inside a "Round 2"
     group.
  5. Final: verified claims (green border) and dropped claims (`struck`, with reasons). Footer:
     elapsed seconds, rounds, model, `file`.
- Timer during the run; "Cancel" stops the job (the subprocess is killed; nothing partial is saved by
  the app).
- Cached answers: a list (from `/api/ask/cached`) with question, timestamp, "n verified / n dropped".
  Clicking one renders the same five stages from the saved `result` (the frontend converts
  `result.rounds` into the same events; see `eventsFromResult` in §5.3), labelled CACHED with its
  timestamp. A button "Use cached answer" is also offered in the error state of a live ask.

### 4.5 Evidence pack (`#/pack`)

Chips: `PRECOMPUTED · 25 Oct 03:20 UTC · gemini-4-argon (thinking medium)` · `LIVE verifier` ·
status chip `AWAITING HUMAN APPROVAL` (amber) / `APPROVED AS DRAFT by <name> · <ts>` (green) /
`REJECTED · <ts>` (red, with reason).
Sections, top to bottom:
1. Summary: pack id, "16 verified claims · 0 dropped · 4 information requests", "Draft for Elly
   Baker AM's office. Nothing is sent: approval creates a signed draft." If
   `verifier_sha != verifier_sha_now`: amber note "The verifier has changed since the pack was
   generated; the checks below are re-run with the current verifier."
2. Headline claims (`headline_ids`), as ClaimCards.
3. Claims table (desktop) / claim cards (phone): id, type chip, section title, text, "verified now"
   or "FAILS: reasons", sources (chunk ids, each opens the SourceDrawer with its quote highlighted) and
   stats (key = value unit, n). Filter chips by type and section.
4. Information requests (`asks`): numbered, with the "why".
5. "How this pack got here" timeline from `GET /api/pack/history`: Draft 1 (15 claims, 5 rejected
   by the verifier, fixed in round 2) → Independent review 1 (21 findings, 4 major, e.g. "a
   commitment the Mayor did not make") → Draft 2 → Review 2 (3 minor) → Final (16 verified, 0
   dropped). The review step says who reviewed: "Claude Code review agents run by the developer,
   against the full sources".
6. Canary card: "Seeded test claim (deliberately false)" + text + `SEEDED` chip + button "Run the
   verifier on it" → `GET /api/canary` → "Verifier: REJECTED" with reasons (`LIVE`).
7. Full pack (collapsible, rendered Markdown; footnotes are links to the source URLs).
8. **Approval form** (sticky card on desktop, at the end on phones):
   - The 5 checklist items from `checklist`, exact text.
   - Next to item 1, a passive counter: "You opened 9 of 14 cited sources in this session" (counts
     SourceDrawer opens; it does not block approval).
   - "Approver name" (required, trimmed, max 80 chars).
   - "Approve as draft" (primary) is disabled until all 5 are ticked and a name is entered. Clicking it
     opens a confirm dialog: "Approve pack <id> as a DRAFT under the name '<name>'? This writes a
     signed record (SHA-256 over pack, name, time, verifier version) locally and to Atlas
     `approvals`. Nothing is sent to anyone." Buttons: Cancel / Approve.
   - On success: green card with signature (mono, copy button), draft path, Atlas status, "Nothing
     was sent." On 400: list the missing items. On 409: "N claims no longer verify" with their
     reasons; the form stays. On 403: "Approval is only allowed from the laptop running the server."
   - "Reject" (danger, secondary): opens a reason textarea (required in the UI) → `POST
     /api/pack/reject`.
   - Existing approvals for this pack (from `approvals`) are listed under the form. If an approval
     exists, the form still works (a second approval is a second record), but the header shows the
     latest.
   - If `GET /api/pack` reports `posthoc_out` other than `outputs_posthoc`, a red banner says "TEST
     MODE: approvals go to <path> and are not mirrored to Atlas".

### 4.6 MongoDB Atlas (`#/atlas`)

Chips: `LIVE from MongoDB Atlas at <taken>` or `CACHED FALLBACK · snapshot <taken>`; `read-only`.
1. Cluster card: MongoDB version, database, collections with counts ("time series" tag), search
   indexes (name, type, status as READY chip, queryable, definition, e.g. "autoEmbed voyage-4 on
   'text'").
2. "One question, three ways": question input (default `Was Bethnal Green to Liverpool Street ground
   for noise?`, the `atlas_show` default) and Search. Three columns (stacked on phones):
   - Atlas Search (docs_text, BM25 full text) · ms
   - Vector Search (docs_auto, autoEmbed voyage-4: Atlas embeds the query) · ms
   - Hybrid Search ($rankFusion of both) · ms, with per-hit `ranks text=#1 semantic=#1`
   Each hit: doc_id, page, score, publisher, date, title, snippet; click opens the SourceDrawer.
   Below: the hybrid pipeline JSON exactly as run (collapsible, mono, copy button) and the line
   "fused score = sum over pipelines of weight / (60 + rank) (reciprocal rank fusion)".
   If the snapshot answers a different question: "(cached snapshot answers '<q>', not the question
   asked)".
3. Records in Atlas: attempts count, last 3 (attempt, ts, kind, verdict, hash, prev), "Atlas head
   MATCHES local head" (or DIFFERS); `section_stats` count and 4 examples.
4. Stream Processing card, from `GET /api/stream/status` (polled every 2 s while visible):
   - available: processor name, a table of the latest windows (window start/end, n, level mean, level
     max, s_m range, section) and a small bar chart of `level_mean` per window; chip `LIVE`.
   - not available: grey card "Stream Processing not running" + the reason verbatim. No windows,
     no placeholder numbers.
   - Button "Stream a ride into Atlas" (ride select limited to the catalogue, speed 4×) →
     `POST /api/stream/start`; shows rows inserted (count, last s_m and level) from the job's events;
     disabled with a reason if `atlas.stream_replay` is missing. Text: "Writes per-second derived
     values (time, position, relative level, section) to Atlas `ingest_live`. No audio, no raw
     sensors."
5. Scored-feature table (static): Atlas Search, Vector Search, Hybrid Search, Automated Embedding +
   Voyage AI, Stream Processing (status from the card above), with where each is used.

### 4.7 Why Argon (`#/why-argon`)

Chips: `POST-HOC` · `n = 4 per model, one task`.
- From `GET /api/compare`. If `json` is present: model cards (gemini-4-argon, gemini-3.8-flash)
  showing the primary summary (round-0 rejection rate, final dropped, converged within budget, total
  seconds) as medians and ranges, then a per-run table. Unknown JSON shapes: render top-level scalar
  fields as a key/value table and rely on the Markdown below.
- Always render `md` (the protocol and results) with `Markdown`.
- If neither exists: EmptyState "The comparison has not been run yet. Protocol written before any
  run: outputs_posthoc/model_comparison.md" (and render the md if only it exists).
- Static note: "If Flash does as well as Argon, this page says so (protocol rule)."

### 4.8 Privacy & method (`#/method`)

Chips: `PRE-REGISTERED` (prereg-24oct) · `POST-HOC` where relevant.
1. Honest scoreboard (from `GET /api/method`): question, verdict chip, evidence, source path.
2. Pre-registration: tag `prereg-24oct` and its commit, "frozen code unchanged since the tag:
   yes/no" (from `git diff --quiet`), what was decided later and labelled post-hoc.
3. Sealed test: one paragraph + numbers (from the API).
4. Verification loop (static bullets from README "Verification loop").
5. Privacy: the data inventory table (static, from docs/PRIVACY.md: raw audio, raw sensors, NIOSH,
   audio-derived features, Atlas collections, Gemini prompts) and the rules (no audio kept, 1-s
   levels only, Gemini sees public documents and summary statistics, raw audio deleted 1 Nov 2026,
   ICO "highly intrusive").
6. "What left this laptop in this session": the egress log (`GET /api/egress`), newest first:
   time, service, what was sent; plus client-side entries for map tiles and Google Fonts.
7. Limits: 2-3 rides per section, one phone, two days; levels relative; NIOSH validated only to
   about 85 dB(A); corrugation is an open hypothesis.

### 4.10 Argon at work (`#/work`) (added 25 Oct)

Three more real Argon jobs, each shown with the deterministic check that judges it and what that
check rejected. Header chips: `POST-HOC` · `gemini-4-argon · thinking medium`. Phone: More sheet.
Data from `webapp/api/work_api.py` (§6.17); the agent modules are never imported by the server.

1. **TfL roughness charts** (`agent/roughness.py`). Chips `PRECOMPUTED · <extraction ts> · 2 runs`,
   `POST-HOC`, and a large amber `PENDING HUMAN CHECK` (shows `n/10 rows` when partly filled, and
   `HUMAN CHECKED · <name>` only when every row of `HUMAN_CHECK.md` has a reading and a y/n and a
   name is given). Tiles: TfL's stated 720-880 m (FOI-1730-1819#000), Argon's stretch common to all
   four figures (both runs, zone rule), the model-free pixel onset. TfL's two sentences verbatim in a
   quote frame ("the only reference in this loop that Claude did not write") + the note that the
   causal claim is TfL's. The four figures (served from an allow-list, click for full size) with the
   header line, the digitizer's tallest spike vs each run's reading (marked when it fails the pixel
   check) and the confirmed stretch. "The checks caught Argon's mistakes": per run, readings off
   the pixels, zones failing the rule, summary sentences rejected (struck, with reasons), colour
   errors; the error both runs made (agreement is not proof). The check table parsed from
   `roughness.md`. The human-check rows. "Re-check saved runs now" runs `agent.roughness --cached`
   (no Gemini, a few seconds, byte-identical outputs) as a job; afterwards the header adds
   `LIVE · checks re-run now`. Limits and the full `roughness.md` (collapsed).
2. **Re-derived numbers** (`agent/recheck.py`). Chips `PRECOMPUTED · <ts>`. Tiles: n match, numbers
   traced to sandbox output, checks without executed code, seconds. Per check: comparison table
   (quantity, ours, Argon's, tolerance, match, in sandbox output), Argon's definition notes, its
   executed code, the sandbox output and the prompt (collapsed). "Run live" streams
   `agent.recheck --jsonl` (about 35-50 s; the table fills per `compare` event). "Run the canary"
   streams `agent.recheck --canary` (offline): its output is plain text, relayed as log lines; the
   UI counts `: caught` / `MISSED` and the `CANARY PASS` or `CANARY FAIL` line and says it counted them.
3. **Ride reports** (`agent/ride_report.py`, format being redesigned: rendered tolerantly). Chips
   `POST-HOC`, `DRAFT · PENDING HUMAN APPROVAL`. Saved reports list (newest opens), claims with
   type, qualifier badges, a facts table (written value, stat.field, stored value + unit + label
   looked up in the stored `ride_stats/<run>.json`, the naming words), comparisons, cites; dropped
   claims struck with reasons; the CLI's own `.md` (with its per-number trace) collapsed. "Write
   live" streams `agent.ride_report <run> --jsonl` (`start, stats, draft, verdicts, final, error`).
   **No approval in the web app**: approval is `--approve NAME` on the command line only.

Demo (beat 4 sub-card, decided 25 Oct): when the analyst replay ends, a compact card "Argon reads
TfL's own roughness charts" (figure 1, TfL 720-880 m vs Argon's stretch, "N readings off the
pixels, N summary sentences, N colour errors caught by code across the 2 runs and kept out",
PRECOMPUTED + PENDING HUMAN CHECK) appears under the sealed-test card. Why a sub-card and not a
seventh beat: the 3:00 budget is full; beat 4's claim is "Argon works, and code it cannot edit
judges it", and the replay leaves its last few seconds free, so a second Argon job whose checks
caught real mistakes (including one both runs made) strengthens the verification-loop point (50 %
of the Gemini judging) without new narration time. Nothing in it is live, and it says so.

### 4.11 Live sensors (`#/live`) (added 25 Oct)

What the phone senses now, one summary per second. It never positions the train and never reads the
microphone. Nav label "Live sensors" (rail after Argon at work; phone: More sheet). Data: the `live`
job (`live.capture --jsonl`, §6.18) or, for the mobile app prototype, rows polled from memory.

- Header chips `NO MICROPHONE` · `PER-SECOND SUMMARIES ONLY`. Under it, verbatim: "This shows what the
  phone senses now. Off a train it cannot place the train; positioning runs on recorded rides (Ride
  page)." and "No microphone. Raw samples stay in memory; only per-second summaries leave the laptop
  (to Atlas only when the toggle is on)."
- Status bar: a big status pill, `LIVE` (green, pulsing dot: phyphox on a phone), `MOCK` (amber solid:
  a recorded ride replayed through a phyphox-compatible server; the full mock label from `hello` is shown
  next to it, and a `--url` that turns out to be a mock server is labelled MOCK too), `APP` (cyan: the
  Tubesense mobile app prototype), `CONNECTING` (amber dashed: started, no data yet or the phone stopped
  answering; the reasons go to Events) or `STOPPED` (slate, with how it ended). Beside it the elapsed /
  requested seconds and seconds summarised, a knock flash ("knock"; a double knock = "sync knock
  detected", green, 5 s), the MOVING / STOPPED badge (LEARNING while the stop detector has no decision)
  and Stop (kills the job).
- Source card: phone address (default `http://172.20.10.1`, kept per browser) + Probe (`GET
  /api/live/probe`: reachable or not, this laptop's address towards the phone, the advice, sensors,
  1 s rates, measuring or not); Start live; Start mock (R9) with 1×/4×; length 1, 2, 5 or 10 min;
  "Send to Atlas" switch, off by default. When on: "Rows go to Atlas `tubesense.ingest_live` ... The
  Stream Processing card on the Atlas page shows their windows when the processor runs" (the Atlas link
  opens a new tab, because a capture nobody follows is cancelled after 60 s). Tip box verbatim: "Venue
  Wi-Fi often isolates devices: put the laptop on the phone's hotspot. Keep phyphox open with Allow
  remote access on and the screen awake."
- Now: rotation about gravity (deg/s), vibration RMS (m/s²), pressure (hPa), pressure change over
  10 s (from the samples; the app sends its own `d_pressure_10s`), |B| (µT), placement (screen up/down,
  tilt, handled), sensor rates (Hz).
- Last 60 seconds: two SVG traces on the phone's experiment-time axis (−60 s ... now): yaw rate
  "rotation about gravity, deg/s, + = turning left" (symmetric axis, dashed zero) and vibration RMS;
  STOPPED seconds shaded amber, knocks as dashed amber lines; a null or a > 1.6 s hole breaks the line.
- Events: hello, state changes (with dwell and door pressure step), placement changes, knocks (pattern,
  gaps, peak), warnings / errors / info (Atlas connect / failure updates the sidebar Atlas dot), bye;
  "Process log" (stderr lines) collapsed.
- Tubesense mobile app card (chip `APP · PROTOTYPE`): sessions that posted in the last 10 minutes,
  Follow (disabled while a capture runs). Following polls rows once a second and drives the same
  readouts and traces; with the switch on the page holds a 20 s Atlas lease, renewed every 10 s.
- On load the page re-attaches to a running `live` job (all buffered events are replayed).
- Mock mode (`?mock=1`): the buttons are disabled with the reason; nothing is simulated.

### 4.9 Research Box v2 (`#/box`)

Chips: `CONCEPT · NOT BUILT` (large, top right, and again on the image caption).
- Title "Research Box v2", subtitle "Multi-sensor instrument for GPS-free train positioning ·
  exploded isometric · concept, not built".
- Image: `static/img/research-box-exploded.png` (copied from the owner's
  `tubesense-box-render-exploded.png`; 2676×1600, also produce a 1400 px wide JPEG/WebP for phones),
  zoomable (tap opens full screen).
- "Each upgrade answers a NO-GO" table (from deck slide 14): Timebase (two apps synced by a knock →
  GNSS PPS + RTC), Acoustic (Voice Memos limiter, relative only → 2 × I²S MEMS mics, dB(A) on
  device), Magnetic (one sensor beside the battery → 3 × RM3100 gradient array on a boom), Speed
  (inferred from position → radar Doppler + sleeper frequency), Inertial (phone IMU about 99 Hz →
  ADIS16470-class IMU, isolated), Pressure (phone barometer → BMP585-class, tens of Hz, vented).
- Specifications table and staged cost table (Stage 1 £150-250, Stage 2 +£60-350, Stage 3
  +£300-600, full box ≈ £700-1,400) with the owner's caveats verbatim: "Part numbers are examples
  to verify, not a tested bill of materials." and "Rough retail estimates including VAT, before
  shipping. Not checked against live listings."
- Footer verbatim: "Deployment on trains would need TfL permission. No partnership with TfL,
  MoniRail or Imperial is implied."
- Link "Open the interactive drawing (needs internet; loads three.js from jsdelivr)" →
  `static/concept/research-box.html` (a byte copy of the owner's HTML, opened in a new tab).
- No API calls.

---

## 5. Architecture, processes and file layout

### 5.1 Processes

```
browser (laptop / phone on LAN)
   |  HTTP JSON + SSE (EventSource)
   v
webapp/server.py  (ThreadingHTTPServer, stdlib only, 127.0.0.1:8770; no numpy/pandas imports)
   |-- static files: webapp/static/**
   |-- light, in-process: pack/approve (agent.approve, agent.pack, agent.verify), analyst session
   |     (demo.replay_analyst helpers, agent.harness), Atlas search (demo.atlas_show.gather_live),
   |     stream status (atlas.live_view), compare, method, egress
   |-- JobManager: subprocesses with JSONL on stdout, relayed as SSE
         ride     -> python -B -m webapp.api.ride_worker ...        (heavy)
         analyst  -> python -B -m agent.live_analyst --jsonl ...    (heavy)
         stream   -> python -B -m webapp.api.stream_worker ...      (heavy)
         ask      -> python -B -m agent.ask "<q>" --jsonl [--cached] (light: network)
```

Why subprocesses: (1) the trackers' settings are module globals (`agent/_eval.apply` sets
`tubesense.online.GAMMA` etc.), so v1 and v2 cannot share a process; (2) a heavy job's memory is
returned when it exits; (3) cancel = terminate; (4) `agent.ask` and `agent.live_analyst` already
define a JSONL CLI.

All subprocesses run with `cwd` = repo root, `env` = the server's environment plus
`PYTHONPATH=<repo root>`, `PYTHONIOENCODING=utf-8`, `PYTHONUNBUFFERED=1`, the interpreter
`sys.executable` and the `-B` flag (frozen modules must not get `.pyc` files). Arguments are passed
as a list (never a shell string).

### 5.2 File layout and ownership

Backend engineer owns (creates or edits only these):

```
webapp/__init__.py            empty
webapp/server.py              CLI, ThreadingHTTPServer, router, static files, JSON/SSE helpers, security checks
webapp/api/__init__.py        empty
webapp/api/jobs.py            JobManager: start/cancel/go/control, heavy-job lock, event buffer, SSE relay
webapp/api/rides.py           ride catalogue (/api/rides), start, precomputed listing
webapp/api/ride_worker.py     subprocess: the paced tracker replay, JSONL out (§7)
webapp/api/stream_worker.py   subprocess: atlas.stream_replay.replay with JSONL out
webapp/api/analyst_api.py     recorded session payload; live session start
webapp/api/ask_api.py         ask status, start, cached list and file
webapp/api/pack_api.py        pack, history, source, canary, approve, reject (calls agent.approve functions)
webapp/api/atlas_api.py       search (live with CACHED fallback), stream status
webapp/api/compare_api.py     model comparison files
webapp/api/method_api.py      scoreboard, prereg/git checks, sealed numbers
webapp/api/egress.py          in-memory egress log
webapp/api/work_api.py        Argon at work: roughness, recheck, ride reports (reads outputs, starts jobs; §6.17)
webapp/api/selftest.py        endpoint checks against a running server (§11.1)
webapp/precomputed/*.jsonl    precomputed ride streams (fallback; generated by ride_worker --fast)
agent/approve.py              refactor only, identical behaviour (§9)
```

Frontend engineer owns:

```
webapp/static/index.html            shell, nav, <link> fonts, manifest, meta (theme-color #070B14, apple-touch-icon,
                                    apple-mobile-web-app-capable, viewport), loads js/main.js as type=module
webapp/static/manifest.webmanifest  name "Tubesense", short_name "Tubesense", start_url "./#/", display standalone,
                                    background_color/theme_color #070B14, icons 192/512 (+ maskable 512)
webapp/static/sw.js                 app-shell cache (index, css, js, icons, fonts css if fetched); NEVER caches /api/*
webapp/static/css/app.css           tokens (§3), layout, components
webapp/static/js/main.js            router, shell, status dots, mock-mode switch
webapp/static/js/api.js             fetchJSON, postJSON (sets Content-Type: application/json), openJobStream (SSE)
webapp/static/js/ui.js              Chip, Card, StatTile, Verdict, ClaimCard, SourceDrawer, HashChain, EventFeed,
                                    Skeleton, ErrorCard, EmptyState, Toast, Banner, Segmented
webapp/static/js/md.js              Markdown renderer (escapes HTML first)
webapp/static/js/colors.js          inferno scale (§3.1)
webapp/static/js/stripmap.js        StripMap
webapp/static/js/geomap.js          GeoMap (SVG + optional Leaflet)
webapp/static/js/yawchart.js        YawChart
webapp/static/js/demo.js            Demo mode bar, pre-warm, keys, settings
webapp/static/js/views/{story,ride,agent,ask,pack,atlas,compare,method,box,verified,work}.js
webapp/static/img/research-box-exploded.png (+ -1400.webp)   copied from C:/Users/tobio/Downloads (read-only source)
webapp/static/concept/research-box.html                      byte copy of the owner's HTML
webapp/static/icons/icon.svg, icon-192.png, icon-512.png, icon-512-maskable.png, apple-touch-icon.png (180)
webapp/static/mock/*.json, mock/ride_R9.jsonl                fixtures for mock mode (§5.4)
```

Nobody edits `webapp/DESIGN.md` except the lead/designer.

### 5.3 Frontend runtime contract

- `api.js`:
  - `fetchJSON(path)` → parsed JSON or throws `{status, error, message}` from the error body (§6.1).
  - `postJSON(path, body)` → same; always `Content-Type: application/json`.
  - `openJobStream(job, onEvent, onEnd)`: `new EventSource(job.events_url)`; `onmessage` parses
    `data` as JSON and calls `onEvent`. On the event `{"type":"job_end"}` it calls `es.close()` and
    `onEnd`. On `onerror` it closes the stream (no auto-reconnect loop) and calls `onEnd({status:
    "lost"})`; the UI then offers "Reconnect" (reopens the same URL; the server replays buffered
    events from `Last-Event-ID` or from the start).
- `eventsFromResult(result)` (in `views/ask.js`): converts a saved ask result into the live event
  sequence: `start` (from question/model/thinking/ts), `retrieval` (from `result.retrieval`), then
  per round `draft` (`claims`) and `verdicts` (`verdicts`), then `final`. Rendering code is
  therefore shared between live and cached.
- No framework. ES modules, `type="module"`. No `eval`, no inline event handler attributes.
- localStorage only for per-viewer conveniences (demo settings, basemap toggle, last ride); every
  access in try/catch; the app works with it empty.

### 5.4 Mock mode (for parallel development)

`index.html?mock=1` makes `api.js` serve fixtures from `static/mock/` instead of `/api`:
`health.json`, `rides.json`, `pack.json`, `pack_history.json`, `analyst_session.json`,
`atlas_search.json`, `stream_status.json`, `compare.json`, `method.json`, `egress.json`,
`ask_cached.json`, `ask_result.json`, and `ride_R9.jsonl` (one event per line, replayed by a timer
at the requested speed). Every page shows the red `MOCK DATA` banner. Fixtures follow the example
payloads in §6 exactly; once the backend runs, the frontend engineer regenerates them from real
responses. The service worker never caches `mock/`.

---

## 6. HTTP API

### 6.1 Conventions

- Base: `http://127.0.0.1:8770` (default). All JSON is UTF-8. `Cache-Control: no-store` on `/api/*`.
- Errors: HTTP status + body `{"error": "<code>", "message": "<human text>"}`. Codes used:
  `bad_request` (400), `forbidden` (403), `not_found` (404), `busy` (409), `unavailable` (503),
  `upstream` (502, Atlas or a subprocess failed), `internal` (500, message without stack traces;
  the stack goes to the server log).
- POST bodies must be JSON with `Content-Type: application/json`, max 16 KB; otherwise 400. If the
  request has an `Origin` header, its host must equal the `Host` header; otherwise 403 (blocks
  cross-site form posts and DNS rebinding tricks with a foreign origin).
- Static: any path not starting with `/api/` is served from `webapp/static/` (resolved path must stay
  inside it; `/` → `index.html`; unknown → 404). Content types from `mimetypes` plus
  `.webmanifest → application/manifest+json`, `.js → text/javascript`, `.jsonl → application/x-ndjson`.
  `Cache-Control: no-cache` for static files.
- SSE (`/api/jobs/{id}/events`): headers `Content-Type: text/event-stream`, `Cache-Control:
  no-cache`, `X-Accel-Buffering: no`. Each event:
  `id: <seq>\ndata: <one-line JSON>\n\n` (no `event:` field; the type is inside the JSON). A comment
  line `: ping\n\n` every 15 s. The first line sent is `retry: 3600000\n\n` (discourages browser
  auto-reconnect storms). After the job's `job_end` event the server closes the connection.
- Times are ISO 8601 UTC with seconds (`2026-10-25T03:20:20+00:00`).

### 6.2 Endpoint list

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | server, modules and environment status |
| GET | `/api/rides` | ride catalogue, stations, defaults |
| POST | `/api/ride/start` | start a paced tracker replay (job) |
| GET | `/api/ride/precomputed` | list or fetch a PRECOMPUTED ride stream |
| GET | `/api/jobs` | list jobs |
| GET | `/api/jobs/{id}` | one job |
| GET | `/api/jobs/{id}/events` | SSE stream of a job's events |
| POST | `/api/jobs/{id}/go` | release a job started with `hold: true` |
| POST | `/api/jobs/{id}/control` | speed / pause / resume a ride job |
| POST | `/api/jobs/{id}/cancel` | stop a job |
| GET | `/api/analyst/session` | recorded analyst session + checks recomputed now |
| POST | `/api/analyst/live/start` | start `agent.live_analyst` (job) |
| GET | `/api/ask/status` | is `agent.ask` available |
| POST | `/api/ask/start` | start `agent.ask` (job) |
| GET | `/api/ask/cached` | `agent.ask.list_cached()` |
| GET | `/api/ask/cached/{name}` | one cached ask result file |
| GET | `/api/pack` | evidence pack, claims with fresh verification, approvals |
| GET | `/api/pack/history` | drafts and reviews behind the pack |
| GET | `/api/source/{chunk_id}` | one public corpus chunk (for the SourceDrawer) |
| GET | `/api/canary` | the seeded false claim and the verifier's verdict, now |
| POST | `/api/pack/approve` | human approval (loopback only by default) |
| POST | `/api/pack/reject` | rejection with reason |
| GET | `/api/atlas/search` | cluster, three-way search, records (LIVE, CACHED fallback) |
| GET | `/api/stream/status` | Atlas Stream Processing windows or honest not-running |
| POST | `/api/stream/start` | stream a ride into Atlas `ingest_live` (job) |
| GET | `/api/compare` | model comparison files |
| GET | `/api/work/roughness` | roughness extraction summary + check table + human-check state (§6.17) |
| GET | `/api/work/roughness/file/{name}` | figure1-4.png, page1-2.png only (allow-list) |
| POST | `/api/work/roughness/recheck` | job: `agent.roughness --cached` |
| GET | `/api/work/recheck` | latest recheck summary |
| POST | `/api/work/recheck/start` | job: `agent.recheck --jsonl` (Gemini) |
| POST | `/api/work/recheck/canary` | job: `agent.recheck --canary` (offline) |
| GET | `/api/work/reports` | saved ride reports and runs |
| GET | `/api/work/reports/{run}` | one report + fact lookups + the CLI's .md |
| POST | `/api/work/reports/start` | job: `agent.ride_report <run> --jsonl` (Gemini) |
| POST | `/api/live/start` | job `live`: `live.capture --jsonl` (phone or MOCK) (§6.18) |
| POST | `/api/live/stop` | kill the running `live` job |
| GET | `/api/live/probe?url=` | ask a phyphox phone: sensors, rates, or network advice |
| POST | `/api/live/ingest` | one row per second from the mobile app prototype (LAN clients only) |
| GET | `/api/live/app`, `/api/live/app/rows` | app sessions / rows (memory only) |
| POST | `/api/live/app/atlas` | `{on}`: 20 s lease to also insert app rows into `ingest_live` |
| GET | `/api/method` | scoreboard, pre-registration and sealed-test facts |
| GET | `/api/egress` | what this server sent to network services this session |

### 6.3 Jobs (shared by ride, analyst, ask, stream)

Job object (returned by every start endpoint with HTTP 202, and by `GET /api/jobs/{id}`):

```json
{"id": "j7f3a9c21", "kind": "ride", "status": "holding",
 "params": {"run": "R9", "track": "EB LST->BTH", "tracker": "v2", "speed": 8, "synthetic_stop": null,
            "stop_variant": "quiet", "truth": true, "hold": true},
 "label": "R9 EB LST->BTH, tracker v2, 8x",
 "created": "2026-10-25T09:12:01+00:00", "started": "2026-10-25T09:12:01+00:00", "ended": null,
 "n_events": 5, "events_url": "/api/jobs/j7f3a9c21/events", "exit_code": null, "error": null}
```

`status`: `starting` (process launched), `holding` (ride worker ready and waiting for `go`),
`running`, `done`, `error`, `cancelled`. `kind`: `ride`, `analyst`, `ask`, `stream`, `live` (and the §6.17 kinds).

Rules:
- Heavy kinds = `ride`, `analyst`, `stream`, `live` (+ the §6.17 kinds). If a heavy job is `starting|holding|running`, starting
  another heavy job returns 409 `{"error":"busy","message":"...","running":<job object>}`, unless the
  body has `"replace": true`, in which case the running heavy job is cancelled first (the UI asks
  "Stop the running <label> and start this?"). At most one `ask` job at a time (409 otherwise).
- Event buffer: every event gets `seq` (1, 2, ...) and is kept in memory (cap 20,000 per job; ride
  ticks beyond the cap drop the oldest `tick` events but never `init`, `end`, `error`). Finished jobs
  are kept 30 minutes, at most 20 jobs.
- Relay: each stdout line of the subprocess is parsed as JSON. A non-JSON line becomes
  `{"type":"log","stream":"stdout","text":"..."}`; each stderr line becomes `{"type":"log",
  "stream":"stderr","text":"..."}` (cap 500 log events per job; the UI shows them only in a "Logs"
  drawer). When the process exits, the server appends
  `{"type":"job_end","status":"done|error|cancelled","exit_code":0,"seconds":21.4}`. Exit code ≠ 0
  without a prior `error` event adds `{"type":"error","message":"process exited with code N"}` first.
- Subscribers: `GET /api/jobs/{id}/events` sends all buffered events with `seq > Last-Event-ID`
  (header) or `> ?after=N`, then live ones. Several subscribers may follow one job (laptop and
  phone).
- Orphans: a `ride`, `stream` or `live` job with no subscriber for 60 s while `running` is cancelled. Ask and
  analyst jobs are not (their cost is already spent; `agent.ask` saves its own result file).
- Control: the server writes one line to the subprocess's stdin: `go`, `speed <n>`, `pause`,
  `resume`. Only ride jobs accept `control`.

`POST /api/jobs/{id}/go` → 200 job object; 409 if the job is not `holding`.
`POST /api/jobs/{id}/control` body `{"speed": 1|4|8}` or `{"action": "pause"|"resume"}` → 200 job;
400 for other values; 409 if the job is not a running ride.
`POST /api/jobs/{id}/cancel` → 200 job (terminates the process; status `cancelled`).
`GET /api/jobs` → `{"jobs": [job, ...]}` newest first.
Unknown id → 404.

### 6.4 `GET /api/health`

```json
{"ok": true, "time": "2026-10-25T09:00:00+00:00", "server": "tubesense-webapp", "port": 8770,
 "lan": false, "lan_approve": false,
 "posthoc_out": "outputs_posthoc", "posthoc_out_is_default": true, "approval_mirror": true,
 "env": {"gemini_api_key": true, "mongodb_uri": true},
 "modules": {"agent.ask": true, "agent.live_analyst": false, "atlas.live_view": false,
             "atlas.stream_replay": false},
 "files": {"investigation": true, "atlas_snapshot": true, "model_comparison_json": false,
           "model_comparison_md": true, "analyst_session": true},
 "heavy_job": null}
```

Module availability is checked with `importlib.util.find_spec` (no import). `env` values are only
`bool(os.environ.get(...))` after `atlas.db.load_env()`; never the values. `heavy_job` is the
running heavy job object or null.

### 6.5 `GET /api/rides`

Built at server start (and on `?refresh=1`) from JSON only: `outputs/sections.json`,
`outputs/gonogo/repeatability.json`, `outputs/qc.json`, `outputs/online/forward_test.json`,
`outputs_posthoc/attempts.jsonl` (attempt 8 `metrics.per_run` and `metrics.runs`),
`outputs_posthoc/lengths.json` (WTT and chord lengths), and file existence in `data/raw/phyphox/`.
Station names and coordinates are constants in `rides.py` (LST, BTH, MLE as below, identical to
`tubesense/stops.py STATIONS`); do not import `tubesense.stops` or `posthoc.lengths` in the server
(they pull in pandas and scipy). It must not load sensor data.

Rules:
- Tracks: the 4 Central tracks with 23 Oct maps: `EB LST->BTH`, `EB BTH->MLE`, `WB MLE->BTH`,
  `WB BTH->LST`.
- A ride entry per section in `sections.json` with `run_id` in R1..R12 (incl. R1.5) on those tracks.
- `available` = phyphox file exists AND the section's key is in `repeatability.json` with
  `clean: true` AND at least one other clean 23 Oct run (R1-R6, R1.5) exists on the track (the
  leave-one-out map). Else `available: false` with `reason` (e.g. "section_truncated: arrival not
  recorded", "no other clean 23 Oct run for a leave-one-out map", "phyphox file missing").
- `day`: "23 Oct" for R1-R6 and R1.5, else "24 Oct". `sample_note`: as `ride_v2` prints it (§4.2).
- `departure_missed_online`: from `forward_test.json` runs (24 Oct only). Such rides are available
  but not `recommended`, and the UI shows "the online departure detector missed this departure in
  the forward test; expect a late or wrong start".
- `offline_score` (24 Oct only): `{"v2": {"median_m", "p90_m", "coverage", "width_m", "source":
  "outputs_posthoc/attempts.jsonl attempt 8 (sealed test, scored once 2026-10-25T00:52:56+00:00)"},
  "v1": {"median_m", "p90_m", "coverage", "source": "outputs/online/forward_test.json (pre-registered
  H2b)"}}`; null for 23 Oct rides.
- `has_dwell`: the run has a non-virtual dwell in `sections.json` runs[].dwells (needed for
  synthetic-stop variants `quiet` and `dwell`; `ideal` always works).
- `recommended`: R9 `EB LST->BTH` (default demo) and R5 `EB LST->BTH` (stop demo).

```json
{"stations": {"LST": {"name": "Liverpool Street", "lat": 51.517372, "lon": -0.083182},
              "BTH": {"name": "Bethnal Green", "lat": 51.527222, "lon": -0.055506},
              "MLE": {"name": "Mile End", "lat": 51.525122, "lon": -0.03364}},
 "tracks": [{"track": "EB LST->BTH", "direction": "EB", "from": "LST", "to": "BTH",
             "length_wtt_m": 2270.0, "length_chord_m": 2205.9,
             "length_source": "TfL FOI-3481-2425, Central line Working Timetable Notice TTN 37/25",
             "map_runs_23oct": ["R1:LST-BTH", "R3:LST-BTH", "R5:LST-BTH"]}],
 "rides": [{"run_id": "R9", "stem": "R9_EB_LST-MLE", "key": "R9:LST-BTH", "track": "EB LST->BTH",
            "day": "24 Oct", "available": true, "reason": null, "recommended": true,
            "sample_note": "R9 is not in the 23 Oct dev data: out-of-sample for the tracker settings and the map",
            "departure_missed_online": false, "has_dwell": true, "duration_s": 142.0,
            "offline_score": {"v2": {"median_m": 1.8, "p90_m": 4.8, "coverage": 0.997, "width_m": 67.1,
                                     "source": "outputs_posthoc/attempts.jsonl attempt 8 (sealed test, scored once 2026-10-25T00:52:56+00:00)"},
                              "v1": {"median_m": 1.8, "p90_m": null, "coverage": 1.0,
                                     "source": "outputs/online/forward_test.json (pre-registered H2b)"}}}],
 "defaults": {"demo": {"run": "R9", "track": "EB LST->BTH", "tracker": "v2", "speed": 8, "truth": true},
              "stop_demo": {"run": "R5", "track": "EB LST->BTH", "tracker": "v2", "speed": 8,
                            "synthetic_stop": 0.5, "stop_variant": "quiet", "truth": true}},
 "speeds": [1, 4, 8]}
```

### 6.6 `POST /api/ride/start`

Body:
```json
{"run": "R9", "track": "EB LST->BTH", "tracker": "v2", "speed": 8,
 "synthetic_stop": null, "stop_variant": "quiet", "truth": true, "hold": false, "replace": false}
```
Validation: `run`+`track` must be an `available` catalogue ride (400 otherwise); `tracker` in
{v1, v2}; `speed` in {1, 4, 8}; `synthetic_stop` null or 0.1-0.9; `stop_variant` in {ideal, quiet,
dwell} and not quiet/dwell when `has_dwell` is false; `truth`, `hold`, `replace` booleans.
Starts `python -B -m webapp.api.ride_worker --run R9 --track "EB LST->BTH" --tracker v2 --speed 8
[--stop 0.5 --stop-variant quiet] [--no-truth] [--hold]`. Returns 202 job. Events: §7.3.

### 6.7 `GET /api/ride/precomputed`

- No query → `{"items": [{"file": "R9_EB-LST-BTH_v2_8x.jsonl", "run": "R9", "track": "EB LST->BTH",
  "tracker": "v2", "synthetic_stop": null, "generated": "2026-10-25T10:02:11+00:00"}]}` from
  `webapp/precomputed/*.jsonl` (first line of each file is the `meta` event).
- `?file=<name>` (name must match `^[\w.\-]+\.jsonl$` and exist) → `application/x-ndjson`, the file
  as is. The frontend replays it on a timer at the chosen speed, using `t` of the ticks, and shows
  `PRECOMPUTED · <generated>` instead of `LIVE compute`.
- Generated by `PYTHONPATH=. .venv/Scripts/python.exe -B -m webapp.api.ride_worker --run R9 --track
  "EB LST->BTH" --tracker v2 --fast --out webapp/precomputed/R9_EB-LST-BTH_v2_8x.jsonl` (no pacing;
  same events; first line `{"type":"meta","label":"PRECOMPUTED","generated":...,"argv":[...]}`).
  Generate R9 v2 and R5 v2 with the 50 % quiet stop.

### 6.8 `GET /api/analyst/session?atlas=0|1`

Calls, in process, `demo.replay_analyst`: `jsonl(H.LOG)`, `jsonl(SESSION)`, `integrity(att)`,
`verdict(vs, m)`, `recheck(base, m, vs)`, `sealed_test(att)`, `failed_session(att)`, `cfg_diff(cfg)`,
`ci(c)`, and with `atlas=1` `atlas_mirror(att)` (bounded by its 4 s client timeout). Note:
`integrity()` runs `git` (fast, local). Cache the payload for 60 s except `checked_at` fields.

```json
{"label": "REPLAY", "model": "gemini-4-argon", "thinking": "medium",
 "recorded": {"start": "2026-10-24T07:58:22+00:00", "end": "2026-10-24T08:04:37+00:00", "seconds": 374.4},
 "checked_at": "2026-10-25T09:13:00+00:00",
 "integrity": {"chain_ok": true, "lines": 9, "broken": [], "harness_sha": "ad1b570e...", "sha_ok": true,
               "tag": "v2-posthoc", "tag_commit": "147c35e...", "tag_sha_ok": true,
               "final_hash_ok": true, "final_at_tag_ok": true},
 "atlas_mirror": "9/9 log lines found in Atlas `attempts` by hash",
 "allowed": {"GAMMA": [0.1, 0.25, 0.5, 1.0], "YAW_SD_FLOOR": [0.2, 0.35, 0.5, 0.8], "P_SPEED": [0.01, 0.03, 0.1], "stop_gate": [true, false]},
 "rule": {"text": "ACCEPT only if mean 90% coverage >= 0.85 and no track's coverage drops > 0.03 below baseline, AND (95% CI of the paired per-run error delta entirely < 0, or band >= 10% narrower with CI upper <= 0.5 m). Fixed in the harness before the session; Argon cannot edit it."},
 "baseline": {"attempt": 0, "ts": "2026-10-24T07:53:47+00:00", "config": {"GAMMA": 0.25, "YAW_SD_FLOOR": 0.35, "P_SPEED": 0.03, "stop_gate": true},
              "metrics": {"median_error_m": 2.3, "coverage_mean": 0.918, "band_width_mean_m": 73.8, "n_runs": 8}},
 "events": [
   {"ts": "2026-10-24T07:58:22+00:00", "kind": "start", "max_experiments": 10},
   {"ts": "2026-10-24T07:58:41+00:00", "kind": "tool", "name": "list_experiments"},
   {"ts": "2026-10-24T08:01:04+00:00", "kind": "tool", "name": "run_experiment", "n": 3,
    "rationale": "Test whether reducing YAW_SD_FLOOR ...", "change": "YAW_SD_FLOOR 0.35->0.2",
    "args": {"GAMMA": 0.25, "YAW_SD_FLOOR": 0.2, "P_SPEED": 0.03, "stop_gate": true},
    "attempt": 3, "hash": "…", "prev_hash": "…",
    "metrics": {"median_error_m": 2.25, "coverage_mean": 0.897, "band_width_mean_m": 52.0, "n_runs": 8},
    "vs": {"width_change": -0.295, "error_delta_ci_m": [-1.61, -0.8, -0.14], "accepted": false,
           "track_coverage_drop": {"EB BTH->MLE": 0.058, "...": "one entry per track"}},
    "verdict": {"accepted": false, "reason": "EB BTH->MLE coverage fell 0.058 (limit 0.03) - error CI upper -0.14 m < 0, the error gain alone would have passed", "recheck_same": true},
    "refused": null},
   {"ts": "2026-10-24T08:04:05+00:00", "kind": "tool", "name": "submit_final", "change": "YAW_SD_FLOOR 0.35->0.2, P_SPEED 0.03->0.1",
    "rationale": "…", "config_hash": "175b8d99d8c4"},
   {"ts": "2026-10-24T08:04:37+00:00", "kind": "report", "text": "### Experiment Report …", "seconds": 374.4, "usage": {"prompt": 8309, "output": 852}}],
 "chain": [{"attempt": 0, "kind": "dev", "hash": "…", "prev_hash": null}],
 "sealed": {"rows": ["v2-posthoc sealed test (00:52:56 UTC, attempt 8): median 1.8 m, coverage 0.936, band 55.5 m, timetable 11.85 m, n=10",
                     "v1 pre-registered (frozen maps, outputs/online/forward_test.json): median 1.8 m, coverage 0.951, timetable 11.2 m, n=10  (v1 metres chord-scaled, v2 WTT)",
                     "pre-registered H2b GO: median <= 5 m and coverage >= 0.80 on LST<->BTH (outputs/PLAN_24Oct.md)"],
            "v2": {"attempt": 8, "ts": "2026-10-25T00:52:56+00:00", "median_error_m": 1.8, "coverage_mean": 0.936, "band_width_mean_m": 55.5, "n_runs": 10},
            "v1": {"median_m": 1.8, "coverage": 0.951, "n_sections": 10}},
 "failed_session": "session 1 (07:53-07:57 UTC) FAILED on a bug in our tool wrapper (TypeError: isinstance() arg 2 must be a type, a tuple of types, or a union): only the baseline (attempt 0) scored, 0 experiments, 0 submissions logged; its report says it submitted the baseline, the harness log shows no submission. Kept: outputs_posthoc/analyst_session_failed1.md",
 "caveat": "Caveat: a small gain - error CI upper bound -0.05 m (only just below 0), 1 of 6 configurations accepted, 8 dev runs from one morning, errors vs a yaw curve-edge proxy; see the single sealed test above."}
```
(Values marked … are filled from the files; the other numbers in this example are the real ones,
checked on 25 Oct with `demo.replay_analyst.verdict()`; `tag_commit` is
`147c35ecf6add30d1ce99b20a38a3614c96a3f82`. Real verdicts: attempts 1, 3 rejected on EB BTH->MLE
coverage (drops 0.107 and 0.058); 2, 5, 6 rejected for no gain; 4 accepted with error CI upper
−0.05 m. `run_experiment` events with an `error` in their result set `refused` to the error text and
leave `metrics`/`vs`/`verdict` null.)

### 6.9 `POST /api/analyst/live/start`

Body `{"max_experiments": 1|2, "replace": false}` (default 2). 503 `unavailable` if
`agent.live_analyst` is missing (`find_spec`) or the Gemini key is absent. Starts
`python -B -m agent.live_analyst --jsonl --max-experiments N`. Events are relayed verbatim (contract:
`start`, `argon_text`, `tool_call`, `tool_result`, `verdict`, `chain`, `final`, `error`). Egress
entry: "Google Gemini API (gemini-4-argon): tracker settings and metrics (no sensor data)".

### 6.10 Ask

`GET /api/ask/status` →
`{"available": true, "reason": null, "gemini_api_key": true, "model": "gemini-4-argon", "thinking": "medium"}`
(`available` false with reason "agent.ask not installed yet" if `find_spec` fails).

`POST /api/ask/start` body `{"question": "...", "cached": false}`. Validation: question is a string,
stripped, 5-300 characters, control characters removed; 400 otherwise. 409 if an ask job runs.
503 if unavailable (or, for a live ask, no Gemini key). Starts `python -B -m agent.ask "<question>"
--jsonl [--cached]`. Events are relayed verbatim per the `agent/ask.py` contract:
`start`, `retrieval`, `draft`, `verdicts`, `final` (with `result`), `error`. Egress entry for live
asks: "Google Gemini API (gemini-4-argon): your question, public document chunks, summary statistics"
and "MongoDB Atlas: your question (hybrid search)".

`GET /api/ask/cached` → `{"available": true, "items": [{"question": "...", "ts": "...", "file":
"outputs_posthoc/asks/2026-10-25T091500_was-the-central-line.json", "name":
"2026-10-25T091500_was-the-central-line.json", "n_verified": 6, "n_dropped": 1}]}` (calls
`agent.ask.list_cached()` in process, adds `name` = basename). If the module is missing:
`{"available": false, "reason": "...", "items": []}`.

`GET /api/ask/cached/{name}` → `agent.ask.replay(name)` (25 Oct; previously the raw file, which says
`cached: false`): the saved result labelled `cached: true`, `label`, original `ts`, `exact_match`,
`superseded_by` (a newer saved answer to the same question, or null), and `reverified {ts,
verifier_sha, same_verifier, same_corpus, all_ok, results}` (verifier and answer gate re-run now;
claims that fail now move to `dropped_claims`). `name` must match `^[\w.\-]+\.json$` and resolve
inside `outputs_posthoc/asks/`; 404 otherwise (also when replay raises LookupError). The UI says
"re-checked now" only when `reverified.all_ok` and `reverified.same_verifier` are both true; otherwise
"passes the verifier now" or "passes the current verifier (changed since saved)". It renders
`final_review` as a second review card ("Final check of the revision", can only withhold), the
`answer-fix` round as "answer line rewritten (claims pinned)", `answer.rejected_line` as its text
(struck) plus reasons, an INCOMPLETE banner when `incomplete` is set, per-claim `review_status`,
the `label` when `exact_match` is false and a link to `superseded_by`. Demo step 5 shows only a saved
answer to the same question (never another question's answer); its header chips follow what is on
screen (LIVE when a live answer is shown; `CACHED · <ts>` for a saved one, also beside a running ask).

### 6.11 Pack

`GET /api/pack` (calls the refactored `agent.approve` functions, §9):

```json
{"label": "PRECOMPUTED", "generated": "2026-10-25T03:20:20+00:00", "pack_id": "8d251cb4c2ebfb3c",
 "model": "gemini-4-argon", "thinking": "medium", "corpus_hash": "c8b8d65b7a24335c",
 "verifier_sha": "e2baa052cdd5…", "verifier_sha_now": "e2baa052cdd5…",
 "n_verified": 16, "n_dropped": 0, "headline_ids": ["C01", "C05", "C11", "C09", "C15"],
 "claims": [{"id": "C01", "section": "grinding", "section_title": "2. Grinding and track work on record",
             "type": "sourced", "type_label": "source",
             "text": "According to Mayor's Question 2022/1791, ...",
             "citations": [{"chunk_id": "MQ-2022-1791#001",
                            "quote": "Bethnal Green to Liverpool Street",
                            "title": "...", "publisher": "...", "doc_date": "...", "page": 1, "url": "https://..."}],
             "stats": [{"key": "level.EB.LST-BTH", "value": -18.66, "unit": "dBFS(A), uncalibrated", "n": 6}],
             "formula": null,
             "check": {"ok": true, "reasons": []}}],
 "dropped": [{"claim": {"id": "...", "text": "..."}, "reasons": ["..."]}],
 "asks": [{"request": "...", "why": "..."}],
 "checklist": ["I have read every claim and opened its sources.", "...5 items, exact text from agent/approve.py"],
 "md": "# Evidence pack: ...",
 "status": {"state": "awaiting", "latest": null},
 "approvals": [],
 "posthoc_out": "outputs_posthoc", "mirror": true, "drafts": ["outputs_posthoc/drafts/evidence_pack_8d251cb4c2ebfb3c.md"]}
```
`status.state` is `awaiting` | `approved` | `rejected` from the latest record for this `pack_id` in
`approvals.jsonl` (missing file = no records). `stats` resolves `stat_keys` through
`agent.verify.load_stats()`. `check` is `agent.verify.verify_all(pack.for_verifier(claims))`, run
now. 503 `unavailable` if `investigation.json` is missing ("Investigator output not present: the
pack cannot be shown").

`GET /api/pack/history` →
```json
{"steps": [
  {"kind": "draft", "label": "Draft 1", "file": "outputs_posthoc/investigation_draft1.json", "ts": "2026-10-25T02:08:16+00:00",
   "rounds": [{"round": 0, "proposed": 15, "rejected": 5, "seconds": 0.0}, {"round": 1, "proposed": 15, "rejected": 0, "seconds": 0.0}],
   "verified": 15, "dropped": 0},
  {"kind": "review", "label": "Independent review 1", "file": "outputs_posthoc/pack_review.json", "n": 21, "major": 4, "minor": 17,
   "reviewer": "Claude Code review agents run by the developer, against the full sources",
   "examples": [{"id": "R01", "claim_id": "C03", "severity": "major", "phrase": "...", "problem": "..."}]},
  {"kind": "draft", "label": "Draft 2 (revised from review 1)", "file": "outputs_posthoc/investigation_draft2.json", "...": "..."},
  {"kind": "review", "label": "Recheck", "file": "outputs_posthoc/pack_review2.json", "n": 3, "major": 0, "minor": 3, "examples": []},
  {"kind": "final", "label": "Final (targeted revision)", "file": "outputs_posthoc/investigation.json", "ts": "2026-10-25T03:20:20+00:00",
   "verified": 16, "dropped": 0, "revision": {"of": "2026-10-25T02:49:40+00:00", "review_file": "pack_review2.json", "n_findings": 3}}]}
```
`proposed` = `len(rounds[i].verification)`, `rejected` = count of `ok: false`. `major/minor` counted
from `severity`. Examples: up to 4 major findings, `problem` truncated to 400 chars. Missing files
are skipped.

`GET /api/source/{chunk_id}` (`chunk_id` URL-encoded, e.g. `MQ-2022-1791%23001`) → from
`agent.verify.load_chunks()` (public corpus `data/corpus/chunks.jsonl`, loaded once):
`{"chunk_id", "doc_id", "title", "publisher", "doc_date", "page", "page_end", "url", "reliability",
"text"}`. Optional `?quote=` → adds `"quote_found": bool` using
`agent.verify.norm(quote) in agent.verify.norm(text)`. 404 if unknown.

`GET /api/canary` →
`{"label": "SEEDED", "claim": {...agent.verify.CANARY...}, "ok": false, "reasons": ["..."], "checked_at": "...", "verifier_sha": "..."}`
(calls `agent.approve.canary()`, §9).

`POST /api/pack/approve`. Body:
`{"pack_id": "8d251cb4c2ebfb3c", "approver": "Tobi Ojebiyi", "checked": [0, 1, 2, 3, 4]}`.
- 403 `forbidden` if the client address is not loopback (`127.0.0.1`/`::1`) and the server was not
  started with `--lan-approve`.
- Otherwise calls `agent.approve.approve(pid, who, checked)` (§9) and maps its result:
  - 200 `{"decision": "approved", "record": {...the record as written...}, "signature": "...",
    "draft": "outputs_posthoc/drafts/APPROVED_8d251cb4c2ebfb3c.md", "atlas": "mirrored" |
    "skipped: <ExceptionName>" | "not mirrored (test output dir)"}`
  - 400 `{"error": "incomplete", "message": "...", "missing": ["<checklist text>", ...],
    "approver_missing": false, "pack_changed": false}`
  - 409 `{"error": "verification_failed", "message": "2 claims no longer verify", "fails":
    [{"id": "C04", "reasons": ["..."]}]}`
- Egress entry when mirrored: "MongoDB Atlas `approvals`: approval record (name, pack id,
  signature)".

`POST /api/pack/reject` body `{"pack_id": "...", "reason": "..."}` → 200
`{"decision": "rejected", "record": {...}}` (calls `agent.approve.reject(pid, why)`; same loopback
rule as approve).

### 6.12 `GET /api/atlas/search?q=...&k=3&mode=live|cached`

- `mode=live` (default): `demo.atlas_show.gather_live(q, k)` in a worker thread with an overall
  25 s deadline (its own phases have 10 s deadlines). On any exception (including `SystemExit` from a
  missing URI), fall back to `outputs_posthoc/atlas_snapshot.json`.
- `mode=cached`: the snapshot only, no network.
- `q`: default `demo.atlas_show.DEFAULT_Q`; 3-200 chars. `k`: 1-5, default 3.
- Never writes the snapshot (only the CLI `--snapshot` does).

```json
{"mode": "live", "taken": "2026-10-25T09:14:02+00:00", "fallback_reason": null,
 "question_mismatch": false,
 "cluster": {"version": "8.0.34", "database": "tubesense", "collections": [{"name": "documents", "count": 44, "kind": ""}],
             "indexes": [{"collection": "documents", "name": "docs_auto", "type": "vectorSearch", "status": "READY", "queryable": true,
                          "what": "autoEmbed voyage-4 on 'text'; filters doc_type,sections"}]},
 "search": {"question": "Was Bethnal Green to Liverpool Street ground for noise?",
            "modes": {"text": {"label": "Atlas Search  (docs_text, BM25 full text)", "ms": 180,
                               "hits": [{"_id": "MQ-2022-1791#001", "doc_id": "MQ-2022-1791", "page": 1, "title": "...", "publisher": "...",
                                         "doc_date": "...", "score": 6.12, "snippet": "..."}]},
                      "vector": {"label": "...", "ms": 240, "hits": []},
                      "hybrid": {"label": "...", "ms": 310, "hits": [{"...": "...", "ranks": {"text": 1, "semantic": 1}}]}},
            "hybrid_pipeline": [{"$rankFusion": {"...": "..."}}, {"$limit": 3}, {"$project": {"...": "..."}}]},
 "records": {"attempts_count": 9, "attempts_last": [], "section_stats_count": 48, "section_stats_examples": []},
 "local_chain": {"lines": 9, "broken": [], "head_hash": "...", "atlas_head_matches": true}}
```
`mode` is `cached` when the snapshot was used (live failed or `mode=cached`), with
`fallback_reason` = exception class name (e.g. "ServerSelectionTimeoutError"). `local_chain` from
`demo.atlas_show._local_chain()`, plus `atlas_head_matches` computed as `render()` does. Egress
entry on live: "MongoDB Atlas: your search text (read-only queries)".

### 6.13 Stream Processing

`GET /api/stream/status?ride_id=<optional>` → if `atlas.live_view` imports, call
`latest(ride_id, 30)` in a thread with a 10 s deadline and return its dict as is plus
`"checked_at"`. If the module is missing or the call raises or times out:
`{"available": false, "reason": "atlas.live_view not installed" | "<ExceptionName>: <message, 200 chars>", "processor": null, "windows": [], "checked_at": "..."}`.
The backend never invents windows. `atlas.live_view.latest()` (25 Oct) returns `windows` only while live
(processor STARTED and the newest window ended less than 90 s ago) and older ones under `history`, with
`processor_state` and `age_s`; the card labels windows "10 s of replay wall-clock (~10 x speed recorded
seconds)", shows `history` in a collapsed "History, not live" panel, and states the processor state
in words ("STOPPED (not running)", "state unknown"). Any successful Atlas call (pre-warm search, Atlas
screen, a live ask's retrieval, stream status with windows or history, streamed rows, analyst mirror
found) sets the sidebar's Atlas dot to live; an unreachable Atlas sets it to unavailable.

`POST /api/stream/start` body `{"run": "R9", "track": "EB LST->BTH", "speed": 4, "replace": false}`
(ride must be in the catalogue; speed 1-8) → 202 job; 503 if `atlas.stream_replay` is missing.
Starts `python -B -m webapp.api.stream_worker --run R9 --track "EB LST->BTH" --speed 4`, which calls
`atlas.stream_replay.replay(run, track, speed, on_row=emit, ride_id=None)` and emits
`{"type":"row", ...row}` for each inserted row, then `{"type":"done","ride_id":"..."}`, or
`{"type":"error","message"}`. Heavy job. Egress entry: "MongoDB Atlas `ingest_live`: per-second
derived values (t, position, relative level, section)".

### 6.14 `GET /api/compare`

`{"available": bool, "json": <outputs_posthoc/model_comparison.json parsed> | null, "md": "<.md text>" | null, "json_mtime": "...", "md_mtime": "..."}`.
`available` = either file exists.

### 6.15 `GET /api/method`

```json
{"scoreboard": [
   {"question": "Do gyro curve signatures repeat ride to ride?", "verdict": "GO", "evidence": "same-section r 0.76-0.99 vs different-section 0.20", "source": "outputs/FINDINGS.md (a)", "label": "PRE-REGISTERED"},
   {"question": "Positioning on new rides with frozen maps (H2b)", "verdict": "GO", "evidence": "median 1.95 m vs 8.2 m timetable on LST<->BTH, 90 % coverage 0.96", "source": "outputs/online/forward_test.json", "label": "PRE-REGISTERED"},
   {"question": "Does it transfer to the Hammersmith & City (H1/H2a)?", "verdict": "NO-GO", "evidence": "one usable pair, r 0.72 vs null max 0.84; tracker 52 m vs timetable 47 m", "source": "outputs/FINDINGS.md (24 Oct)", "label": "PRE-REGISTERED"},
   {"question": "Rank section loudness from phone audio", "verdict": "NO-GO", "evidence": "louder on 10 of 11 trips, mean +0.54 dB; the rule needs every trip (R10 WB -0.13 dB)", "source": "outputs/gonogo/loudness.json", "label": "PRE-REGISTERED"},
   {"question": "Magnetometer as a position fingerprint", "verdict": "NO-GO", "evidence": "|B| NO-GO; no stable platform signature (sd 19 vs 22 µT)", "source": "outputs/FINDINGS.md (b), H4", "label": "PRE-REGISTERED"},
   {"question": "Absolute level, NIOSH app (H5, indicative)", "verdict": "MEASURED", "evidence": "91.9 dB(A) EB, 87.3 dB(A) WB, one whole trip each; app validated only to about 85 dB(A)", "source": "outputs_posthoc/h5_niosh.json", "label": "PRE-REGISTERED"},
   {"question": "Tracker v2 (Argon-tuned) on the sealed 24 Oct test", "verdict": "MATCHED, NOT BEATEN", "evidence": "median 1.8 m vs v1 1.8 m; coverage 0.936 vs 0.951; stop gate engaged in 0 of 10 sections", "source": "outputs_posthoc/attempts.jsonl attempt 8", "label": "POST-HOC"},
   {"question": "Corrugation wavelength from the roar", "verdict": "OPEN", "evidence": "dominant roar peaks are fixed-frequency resonances (9 of 9 runs), so frequency / speed gives no wavelength", "source": "docs/ARCHITECTURE.md", "label": "POST-HOC"}],
 "prereg": {"tag": "prereg-24oct", "tag_commit": "…", "frozen_unchanged": true, "checked_at": "..."},
 "sealed": {"v2": {"median_error_m": 1.8, "coverage_mean": 0.936, "n_runs": 10, "ts": "2026-10-25T00:52:56+00:00"},
            "v1": {"median_m": 1.8, "coverage": 0.951, "n_sections": 10}},
 "h5": [{"run_id": "N1", "direction": "EB", "LAeq_dBA": 91.9}, {"run_id": "N2", "direction": "WB", "LAeq_dBA": 87.3}]}
```
The scoreboard rows are fixed text in `method_api.py` (copied from `outputs/FINDINGS.md` and the
deck), each with its source path. `sealed` and `h5` are read from the files. `prereg.tag_commit` =
`git rev-list -n1 prereg-24oct`; `frozen_unchanged` = `git diff --quiet prereg-24oct -- tubesense
scripts` exit code 0 (null if git fails).

### 6.16 `GET /api/egress`

`{"since": "<server start>", "entries": [{"ts", "service", "sent", "endpoint"}]}`, newest first, max
200, memory only (never written to disk). Entries are added by the endpoints that call out (§6.9-6.13
list the texts). The server itself makes no other outbound calls.

---

### 6.17 Argon at work (`webapp/api/work_api.py`, added 25 Oct)

Reads only the CLIs' JSON and Markdown outputs (tolerantly: unknown fields ignored, missing ones
rendered as absent). Jobs use the job manager; kinds `roughness`, `recheck`, `recheck_canary`,
`ride_report` are **heavy** (one at a time together with ride, analyst and stream; 409 `busy` or
`replace`), never orphan-cancelled. A JSON line with an `event` key (agent.ride_report) is relayed
with `type` = event; a `type` it carried (the exception name on `error`) becomes `type_name`.
In test mode (`TUBESENSE_POSTHOC_OUT` set) recheck and ride-report jobs get `--no-atlas`.

- `GET /api/work/roughness` → `{available, label: "PRECOMPUTED", ts, checked_file_mtime, model,
  thinking, source, tolerances, tfl {stated, pixels, by_figure, all_figures_overlap, union,
  overlaps}, figures [{n, image, rail, band, page, caption, header, max_digitizer, max_by_run,
  max_pixel_ok_by_run, zones_confirmed, zones_rejected, spectrum_peaks}], runs [{run, ts, seconds,
  usage, ok, n_failures, readings {n, pass, fail, failed[]}, zones {n, fail, failed[]}, summary[],
  colours[], other_failures[]}], check_table (rows of roughness.md "## Checks"), check_notes,
  plain_words, limits, md, human_check {exists, state (pending, partial, done, missing), rows[],
  n_rows, n_filled, checked_by, date}, files}`.
- `GET /api/work/roughness/file/{name}`: `figure1..4.png`, `page1..2.png` from
  `outputs_posthoc/roughness/pages/`, nothing else (404).
- `POST /api/work/roughness/recheck` → 202 job `python -B -m agent.roughness --cached` (no egress).
- `GET /api/work/recheck` → `{available, label, ts, file_mtime, model, thinking, tolerances, inputs,
  summary, checks {name: {title, error, seconds, attempts, usage, code_executed_ok, comparisons[],
  code[], outputs[], notes, prompt, table_sha256}}, history[{file, ts, n_match, n_compared, errors}]}`.
- `POST /api/work/recheck/start` → 202 job `agent.recheck --jsonl` (503 without module or Gemini key).
  Egress: "Google Gemini API (gemini-4-argon, code execution): derived tables only ..." and
  "MongoDB Atlas `rechecks`". Events: `start, quantity, argon_code, argon_result, compare, error, final`.
- `POST /api/work/recheck/canary` → 202 job `agent.recheck --canary` (plain-text output as log events).
- `GET /api/work/reports` → `{module, gemini_api_key, items [{run, ts, review, n_verified, n_dropped,
  claims_in_range, seconds, schema, ...}], runs [{run, has_report, has_stats}], approval}`.
- `GET /api/work/reports/{run}` (R1..R15, R1.5) → `{run, label, file, review, record (without
  rounds), rounds, lookups {claim_id: [fact + found, stored, unit, label, source]}, last_verdicts,
  md, approval}`; 404 if no report.
- `POST /api/work/reports/start` body `{"run": "R9"}` → 202 job `agent.ride_report R9 --jsonl`.
  Egress: Gemini (that ride's derived statistics only) and "MongoDB Atlas `ride_reports`".

### 6.18 Live sensors (`webapp/api/live_api.py`, `webapp/api/live_ingest.py`, added 25 Oct)

- `POST /api/live/start` body `{"url": "http://172.20.10.1"}` or `{"mock": "R9", "speed": 1}`, plus
  `"seconds"` (5-600, default 120), `"atlas"` (default false), `"replace"`. `url` and `mock` are mutually
  exclusive (400); neither = the default URL. URL rule (the server fetches it): `http` only, no user
  info, path empty or `/`, no query or fragment, host = an IP literal that is private, loopback or
  link-local (not unspecified, multicast or reserved) or `localhost`, optional port; a domain name is
  refused. `mock` = `R0`-`R15`, `R1.5` or `synthetic`; speed 1-8. → 202 job, kind `live`, heavy, orphan-
  cancelled after 60 s. Runs `python -B -m live.capture (--url U | --mock R --speed S) --seconds N --jsonl
  [--atlas]`; the events file is `outputs_posthoc/live_capture/<ride_id>.jsonl` (derived values only). In
  test mode (`TUBESENSE_POSTHOC_OUT` set) the file goes under that folder and `--atlas` also gets
  `--cleanup` (the capture deletes its own rows at the end); the normal server never adds `--cleanup`.
  Events (relayed as they are): `hello`, `sample` (per second), `knock`, `state`, `placement_change`,
  `warning`, `info`, `error`, `bye` (see `live/capture.py`). Egress when `atlas`: "MongoDB Atlas
  `ingest_live`: one document per second of derived motion values ...; no audio, no raw samples".
- `POST /api/live/stop` → `{"stopped": true, "job": <job>}` or `{"stopped": false, "message"}`. A kill:
  no `bye`, `job_end` status `cancelled`.
- `GET /api/live/probe?url=` (same URL rule) → in the server process, 2 s timeouts, about 1-2 s:
  `{url, laptop_ip, checked_at, ok, reachable, error, advice[], sensors, seconds}` and when ok also
  `{mock, experiment, device, measuring, session, rates_hz (over 1 s), warnings[]}`. Advice: refused =
  phyphox not serving (open it, Allow remote access, screen awake); timeout = not on the same network
  (hotspot); a different /24 than the laptop's own address is named.
- `POST /api/live/ingest` (mobile app prototype): accepted only from a loopback or private-LAN client
  address (403 otherwise); no Origin needed (React Native sends none), a foreign browser Origin is still
  refused, Host rules as everywhere (`--lan` for the phone). The body must be exactly
  `{source: "tubesense-mobile", session: [A-Za-z0-9_-]{1,64}, t: 0-172800, ts: ISO 8601, yaw_rate_dps,
  acc_h_rms, vib_rms: numbers, b_abs_ut, pressure_hpa, d_pressure_10s: number or null, state:
  "moving"|"stopped", knock: bool, rates: {motion, mag, baro: number or null}}`: a missing or extra key,
  a wrong type or an out-of-range value is 400. → `{ok, seq, kept, atlas}`. Memory only: the last 300
  rows per session, at most 12 sessions; nothing written to disk.
- `GET /api/live/app` → `{sessions: [{session, n, kept, first_seen, last_seen, age_s, last_seq, rates,
  client_local}], keep_rows, atlas: {on, lease_s, inserted, failed, last_error}, source}`.
  `GET /api/live/app/rows?session=S&after=N` → the session summary + `rows` with `seq > N`; 404 for an
  unknown session.
- `POST /api/live/app/atlas {"on": true|false}` → the atlas state. `on` opens a 20 s lease (the page
  renews it every 10 s while following); while it holds, each accepted row is inserted (background
  thread) into `ingest_live` as `{ride_id: "app-" + 8 random hex per session (never the session string or
  a device name), t (from the session's first row), phone_t, ts, source: "tubesense-mobile", live: true,
  replay: false, yaw_rate_dps, vib_rms, acc_h_rms, state, stopped, handled: null, knock, pressure_hpa,
  b_abs_ut, section: "live capture", s_m: null, level_rel_dbA: null, confident: false}`. Egress logged
  (throttled to once a minute).

## 7. The ride worker (real compute, step by step)

`webapp/api/ride_worker.py`, run as `python -B -m webapp.api.ride_worker`. It re-implements the
per-step logic of `demo/ride_v2.main()` (lines 207-470) without matplotlib drawing, importing every
helper instead of copying it.

### 7.1 Setup

```
sys.dont_write_bytecode = True            # first line, before any tubesense import
OUT = sys.stdout; sys.stdout = sys.stderr # library prints (e.g. build_maps' "map: ...") must not corrupt the JSONL
emit(obj) -> OUT.write(json.dumps(obj, allow_nan=False) + "\n"); OUT.flush()   # NaN/inf -> null before dumping
import matplotlib; matplotlib.use("Agg")
from demo import ride_v2 as RV           # gives RV.EV, RV.E, RV.O, RV.D1, RV.V2, RV.AE, RV.LN, RV.R, RV.FINAL, RV.QC
```

Steps (emit `{"type":"status","phase":..., "message":..., "elapsed_s":...}` before each):
1. `phase "loading"`: `cfg = json.load(RV.FINAL)`; `prm = AE.apply(cfg["config"])` only for v2;
   `run = tubesense.ingest.load_run(args.run)`.
2. `phase "map"`: `tpl_c, tpl_w, shown, base = RV.build_maps(track, run.run_id)`;
   `tpl = tpl_w if v2 else tpl_c`; `paint_band = D1.PAINT_BAND_M * tpl.length_m / tpl_c.length_m`.
3. `phase "features"`: `feat = V2.gate_features(run, O.stream_features(run))`;
   `noise = D1.live_noise(run)` (cached 1-s levels; levels only).
4. `phase "departure"`: the section's row `sec` from `outputs/sections.json` (run_id, from, to).
   Arm the detector at `max(feat.wall[0], sec["depart_wall"] - RV.ARM_S)` for every ride (this is
   what `ride_v2` does for 23 Oct rides, extended to 24 Oct rides so second legs work).
   `dep = O.departure_after(feat, arm)`; none → `error` "no departure detected after the detector was
   armed". If `abs(dep["wall_onset"] - sec["depart_wall"]) > RV.ARM_S` emit
   `{"type":"warning","message":"detected onset is +X s from the offline departure (late detection, or the wrong leg)"}`.
   `onset = dep["wall_onset"]`; `arrive = sec["arrive_wall"]`.
5. Reference (`truth`, default on): 23 Oct rides: `_, indep, edges = E.truths(shown, base)`.
   24 Oct rides: build the SectionRun like `posthoc.eval_v2.track_sections` does, without the DEV_RUNS
   filter: `item = {**sec, "_key": "R9:LST-BTH", "_meta": repeatability["sections"]["R9:LST-BTH"]}`;
   `sr = EV.load_track(secs, [item])[0]`; `_, indep, edges = E.truths(sr, base)` (tested: R9 gives 10
   matched curve edges, about 2 s). Any exception → no reference, and `init.truth_available` false with
   the reason.
6. Synthetic stop (optional), exactly as `ride_v2` lines 236-255: `ts = onset + frac * (arrive -
   onset)`; filler from `EV.IDEAL` or `EV.dwell_filler(...)`; `feat = EV.insert_stop(feat, ts, filler)`;
   `t_stop = EV.STOP_S`; `orig(w)` maps replay wall → recording wall (NaN inside the stop); the
   reference is frozen during the stop.
7. Steps: `w_start = onset - D1.PRE_S`; `arr_r = arrive + (t_stop if ts < arrive else 0)`;
   `w_end = min(feat.wall[-1], onset + 1.3 * tpl.duration_s + 10 + t_stop, arr_r + RV.MAX_POST)`;
   `steps = feat[(feat.wall >= w_start) & (feat.wall <= w_end)]`.
   Tracker: `V2.GatedTracker(tpl, "depart", prm)` (v2) or `O.Tracker(tpl, "depart")` (v1).
8. Emit `init` (§7.3), then `status phase "ready"`. With `--hold`: emit `{"type":"holding"}` and block
   reading stdin until a line `go`. (The JobManager sets job status `holding` on that event.)

### 7.2 Loop (paced)

For step i (ride time `t = steps.wall[i] - onset`), exactly the `update()` logic of `ride_v2`:
- Departure confirmation: when `steps.wall[i] >= dep["wall_detect"]` and not started, run the tracker
  over the buffered steps from `onset` up to now (past data only), set started, and mark this tick
  `departure: true`; also emit `{"type":"departure","t":..., "detect_s": <s into recording>,
  "backdated_s": <wall_detect - onset>}`.
- Tracker step: v2 `tracker.step(b.yaw, bool(b.valid), b.gyr > O.HANDLED, {"vib": b.vib, "body":
  b.body, "h": b.h})`; v1 `tracker.step(b.yaw, bool(b.valid), b.gyr > O.HANDLED)`. Arrived when
  `e["map"] >= 0.995`.
- Position `s_m = tpl.metres(e["map"])`, band `tpl.metres(e["q05"])`, `tpl.metres(e["q95"])`.
- Confident: `e["lock"] >= D1.LOCKED and (band_hi - band_lo) < paint_band`, held `D1.HOLD`
  consecutive steps.
- Noise: `wo = orig(wall)`; `k = searchsorted(noise[0], wo) - 1`; if `k >= 0` and `wo - noise[0][k] <
  1.0`: `level_rel = noise[1][k]`, and if confident and k differs from the last painted k, add
  `paint = {"s_m": s_m, "level": level_rel, "k": k}`. During the synthetic stop `level_rel = null`.
- Status: `ARRIVED` if arrived; `TRACKING` if started; else `WAITING FOR DEPARTURE`; v2 with
  `e["gate"]` and not arrived → `HOLDING: train stopped`.
- Speed: `tpl.speed(e["map"], e["speed_ratio"]) * e.get("p_moving", 1.0) * 3.6` km/h once started.
- Map level here: `tpl.noise_db[min(int(e["map"] * nb), nb - 1)]`.
- Reference: `truth_s_m = tpl.metres(indep(wt))` with `wt = ts if in_stop else orig(wall)`, only
  within `[d0, d1]` of the reference section, else null.
- End: when arrived and `steps.wall[i] >= max(arrived_wall, arr_r) + RV.POST_S`, or the steps run
  out.

Pacing: `deadline = t_play0 + (i * O.STEP) / speed` (re-based when the speed changes or after a
pause); sleep until the deadline, then step. A stdin reader thread handles `speed N`, `pause`,
`resume` (and `go` before the loop). Emission throttle: emit a `tick` every
`max(1, ceil(speed / 4))` steps (1×: 4/s, 4×: 16/s, 8×: 16/s), but always emit a tick that carries a
`paint`, a `departure`, a change of `status`, or the start/end of the synthetic stop. `--fast`: no
sleeping, every step emitted, output to `--out`.

### 7.3 Ride events

`init` (example values are real, computed for R9 "EB LST->BTH", v2):
```json
{"type": "init", "run_id": "R9", "stem": "R9_EB_LST-MLE", "track": "EB LST->BTH", "section": "EB LST->BTH",
 "from": "LST", "to": "BTH", "from_name": "Liverpool Street", "to_name": "Bethnal Green", "day": "24 Oct",
 "stations": {"LST": {"name": "Liverpool Street", "lat": 51.517372, "lon": -0.083182},
              "BTH": {"name": "Bethnal Green", "lat": 51.527222, "lon": -0.055506},
              "MLE": {"name": "Mile End", "lat": 51.525122, "lon": -0.03364}},
 "tracker": "v2", "tracker_label": "tracker v2 (post-hoc, chosen on 23 Oct data)",
 "config": {"GAMMA": 0.25, "YAW_SD_FLOOR": 0.2, "P_SPEED": 0.1, "stop_gate": true}, "config_hash": "175b8d99d8c4",
 "length_m": 2270.0, "length_chord_m": 2205.9,
 "axis_label": "metres from Liverpool Street, TfL Working Timetable length 2,270 m (dead-reckoned profile scaled to it; TfL FOI WTT)",
 "sample_note": "R9 is not in the 23 Oct dev data: out-of-sample for the tracker settings and the map",
 "map": {"runs": ["R1:LST-BTH", "R3:LST-BTH", "R5:LST-BTH"], "base": "R1:LST-BTH", "leave_one_out": "R9 not in it",
         "n_bins": 40, "bin_edges_m": [0.0, 7.3, 22.1, 50.7, 88.6, 134.4, "... 41 values ..."],
         "noise_db": [-22.9, -27.19, -27.31, -25.01, -22.97, -18.5, -17.3, -21.88, "... 40 values, null for empty bins ..."],
         "vmin": -27.2, "vmax": -15.89, "duration_s": 142.2,
         "curves_m": [[0.1, 0.3], [5.8, 61.6], [296.8, 498.0], [657.3, 887.8], [1182.6, 1476.2], [1823.3, 1829.5],
                      [1841.9, 1860.5], [1866.7, 1879.0], [1885.2, 1897.6], [1903.8, 1940.7], [1951.7, 1963.3], [2075.9, 2226.9]]},
 "paint_rule": {"locked": 0.5, "lock_window_m": 50, "band_max_m": 205.8, "hold_steps": 8, "hold_s": 2.0},
 "step_s": 0.25, "speed": 8, "pre_s": 8.0, "t_range": [-8.0, 165.0], "n_steps": 661,
 "levels_unit": "dBFS(A), relative, uncalibrated",
 "live_only": false,
 "departure": {"armed_s": 23.3, "detect_s": 32.8, "backdated_s": 1.25},
 "synthetic_stop": null,
 "truth_available": true, "truth_label": "offline curve-edge reference (10 matched curve edges; shares the gyro; not ground truth)",
 "qc": {"this_ride": ["R9: BTH->MLE departure not recorded -> not scored", "R9: phone handled x1 (9-12 s) -> yaw ignored while handled",
                      "R9: magnetometer raw (not used for position)"],
        "other": ["R4: phone on the floor detected (...) -> excluded from noise averages and maps", "..."],
        "flags": [{"type": "section_truncated", "t0": 220.26, "t1": 334.51, "severity": "low", "evidence": "BTH->MLE: arrive not recorded ..."}]},
 "offline_score": {"v2": {"median_m": 1.8, "p90_m": 4.8, "coverage": 0.997, "width_m": 67.1, "source": "outputs_posthoc/attempts.jsonl attempt 8 (sealed test, scored once 2026-10-25T00:52:56+00:00)"}},
 "setup_s": 9.6}
```
Field rules: `curves_m` from `abs(tpl.yaw_mu) > 0.6` runs, as `ride_v2` draws them; `bin_edges_m =
tpl.metres(linspace(0, 1, nb + 1))`; `vmin/vmax` = 5th/99th percentile of finite `noise_db`;
`synthetic_stop` = `{"start_s": 71.4, "end_s": 101.4, "variant": "quiet", "label": "SYNTHETIC 30 s
STOP (inserted for the demo)"}` when set; `live_only` = run in `O.EXCLUDE_RUNS` or a
`floor_like_placement` flag (paint at reduced opacity); `qc` lines from `RV.flag_lines` (this ride
`brief=False`; other rides `brief=True`, skipping `R0_*` and keeping `split_run` only for R1, as
`RV.qc_text`); `axis_label` as `ride_v2` (v1: "metres from Liverpool Street, chord-scaled:
straight-line length 2,206 m (dead-reckoned profile; v1 axis)"); `sample_note` as `ride_v2` lines
351-353; `tracker_label` from `RV.LABEL`.

`tick`:
```json
{"type": "tick", "i": 241, "t": 60.25, "section": "EB LST->BTH",
 "s_m": 881.1, "band_lo": 848.8, "band_hi": 881.0, "lock": 0.98, "confident": true,
 "level_rel": -17.54, "level_map": -18.2, "paint": {"s_m": 881.1, "level": -17.54, "k": 100},
 "speed_kmh": 51.0, "yaw": 1.62, "map_yaw": 1.48,
 "stopped": false, "gate_state": "off", "gate_z": 0.26,
 "status": "TRACKING", "departure": false, "in_synthetic_stop": false,
 "truth_s_m": 879.3}
```
Before departure: `s_m`, `band_lo`, `band_hi`, `lock`, `speed_kmh`, `map_yaw` are null and
`status` is `WAITING FOR DEPARTURE`. `gate_state`: `none` (v1), `warming` (gate_z not finite),
`off`, `engaged`. `stopped` = v2 gate engaged. `paint` is null on most ticks. All floats rounded to 2
decimals (metres to 1).

Other ride events: `status`, `warning`, `holding`, `departure`, and
```json
{"type": "end", "summary": {"steps": 661, "ride_s": 165.25, "wall_s": 20.9, "tracker_ms_per_step": 0.67,
 "arrived_t": 141.0, "vs_truth_median_m": 1.9, "vs_truth_n": 560,
 "stop": null}}
```
(`stop` = `{"end_err_m": 4, "gate_engaged_after_s": 2.75}` with a synthetic stop; `vs_truth_*` null
without a reference.) Errors: `{"type":"error","message":"..."}` then exit code 1.

---

## 8. Reuse map

| Endpoint | Existing code called (read-only unless stated) |
|---|---|
| `/api/rides` | JSON files listed in §6.5 (incl. `outputs_posthoc/lengths.json`); station constants in `rides.py` copied from `tubesense/stops.py STATIONS` (not imported: it pulls in pandas and scipy) |
| ride worker | `demo.ride_v2`: `build_maps`, `flag_lines`, `qc_text` logic, `FINAL`, `QC`, `LABEL`, `ARM_S`, `POST_S`, `MAX_POST`; `scripts/demo.py` via `RV.D1`: `live_noise`, `PRE_S`, `LOCKED`, `HOLD`, `PAINT_BAND_M`, `STATION_NAMES`; `tubesense.online`: `stream_features`, `departure_after`, `Tracker`, `HANDLED`, `STEP`, `EXCLUDE_RUNS`; `posthoc.tracker_v2`: `gate_features`, `GatedTracker`; `posthoc.eval_v2`: `insert_stop`, `dwell_filler`, `IDEAL`, `STOP_S`, `DEV_RUNS`, `load_track`; `scripts/online_eval.truths` via `EV.E`; `agent._eval.apply`; `tubesense.ingest.load_run` |
| `/api/analyst/session` | `demo.replay_analyst`: `jsonl`, `integrity`, `verdict`, `recheck`, `sealed_test`, `failed_session`, `atlas_mirror`, `cfg_diff`, `ci`, `SESSION`; `agent.harness`: `LOG`, `ALLOWED`, `BASELINE`, `MIN_COVERAGE`, `TRACK_COV_DROP`, `WIDTH_GAIN`, `ERR_SLACK_M`, `_check`, `verify_log` |
| `/api/analyst/live/start` | `agent.live_analyst` CLI (subprocess) |
| `/api/ask/*` | `agent.ask` CLI (subprocess), `agent.ask.list_cached()` |
| `/api/pack`, `/api/canary`, approve, reject | refactored `agent.approve` (§9), which uses `agent.pack.render`, `for_verifier`, `TITLES`, `LABEL`, `OUTDIR`, `INV`, `DRAFTS` and `agent.verify.verify_all`, `CANARY`, `VERIFIER_SHA`, `load_chunks`, `load_stats` |
| `/api/pack/history` | the JSON files only |
| `/api/source/{id}` | `agent.verify.load_chunks`, `agent.verify.norm` |
| `/api/atlas/search` | `demo.atlas_show.gather_live`, `_local_chain`, `SNAPSHOT`, `DEFAULT_Q` |
| `/api/stream/*` | `atlas.live_view.latest`, `atlas.stream_replay.replay` (may not exist yet) |
| `/api/compare` | files only |
| `/api/method` | files + `git` |

Server-process imports must stay light: `agent.verify`, `agent.pack`, `agent.approve`,
`agent.harness`, `demo.replay_analyst`, `demo.atlas_show`, `atlas.db`, `atlas.search` (pymongo).
Measured import cost on this machine: `agent.harness` 0.30 s (brings numpy), `demo.replay_analyst`
0.33 s, `atlas.search` 0.70 s, `agent.approve` 0.22 s, `agent.verify` 0.05 s, `demo.atlas_show`
0.04 s; none of them imports pandas, scipy or matplotlib. At startup, log a warning if `pandas` or
`matplotlib` is in `sys.modules` (it means a heavy module slipped into the server).

---

## 9. Refactor of `agent/approve.py` (identical behaviour)

Goal: one implementation of the approval logic, used by both the existing page (port 8765) and the
web app. The HTML page, its URLs, its form fields, its status codes and the files it writes must stay
byte-for-byte the same.

New module-level functions (the `Handler` is rewritten to call them):

```python
def load() -> tuple[dict, str, str]                       # = current _load(): (inv, md, pid); keep _load as an alias
def checks(inv: dict) -> dict[str, dict]                  # {claim_id: {"id","ok","reasons"}} via V.verify_all(P.for_verifier(...))
def canary() -> dict                                      # V.verify_all([V.CANARY])[0] plus {"claim": V.CANARY}
def records(pack_id: str | None = None) -> list[dict]     # approvals.jsonl lines (all, or for one pack), [] if missing
def approve(pid: str, who: str, checked: list[int] | set[int]) -> tuple[int, dict]
    # 400 {"missing": [...checklist texts], "approver_missing": bool, "pack_changed": bool}
    # 409 {"fails": [{"id","reasons"}]}
    # 200 {"record": rec, "signature": sig, "draft": rec["draft"], "md": md}
    # Same order and logic as do_POST today: load(); ts; missing/who/pid check; re-verify; signature =
    # sha256(md + who + ts + V.VERIFIER_SHA); write DRAFTS/APPROVED_<pid>.md; _record(...)
def reject(pid: str, why: str) -> dict                    # _record({"ts","decision":"rejected","pack_id","reason"}) as today
```

`Handler.do_POST` builds `checked` from the form keys `c0..c4` and calls `approve(form.get("pid"),
form.get("who", "").strip(), checked)`, then renders exactly the current HTML for each outcome.
`RECORDS`, `MIRROR`, `CHECKLIST`, `CSS` and `_record` are unchanged. `MIRROR` stays as defined
(Atlas only when `P.OUTDIR` is the real `outputs_posthoc`). The module still reads
`TUBESENSE_POSTHOC_OUT` at import time, so the web server must be started with the variable set for
tests (§11).

The running process on port 8765 keeps its already-loaded code; editing the file does not affect it.
Do not stop or restart it.

---

## 10. States

| State | Where | Behaviour |
|---|---|---|
| Loading | every data card | `Skeleton` with the expected shape (3-6 lines, or the strip-map outline); ride start shows the worker's `status.phase` text and elapsed seconds |
| Long-running agent | Ask, Live analyst | stage stepper with elapsed timer and "Argon is thinking (30-60 s per turn)"; Cancel |
| Error | any request | `ErrorCard`: the `message` from the API, a Retry button, and the honest fallback where one exists: Atlas → "Show CACHED snapshot"; Ask → "Use a cached answer"; Ride → "Play PRECOMPUTED"; Live analyst → "Show the recorded session". Never a silent fallback: the chip changes to CACHED / PRECOMPUTED |
| Busy (409) | start buttons | dialog "A <label> job is running. Stop it and start this one?" → re-POST with `replace: true` |
| Server unreachable | global | `/api/health` polled every 10 s; on failure a red top banner "Server not reachable at <origin>. Is `python -m webapp.server` running?" and start buttons disabled |
| No internet | global | when `navigator.onLine` is false or Atlas returned a network fallback: amber banner "Offline: Atlas, Gemini and map tiles are unavailable. Local replays and checks still work." Basemap → SVG; Atlas → CACHED; Ask live disabled |
| Unavailable module | Ask, Live analyst, Stream, Why Argon | grey EmptyState with the server's reason (e.g. "agent.live_analyst not installed yet"); no fake content |
| Empty | Approvals list, cached asks, stream windows | one line explaining why ("No approval yet: approval needs a human") |
| Test mode | Pack | red banner when `posthoc_out_is_default` is false |
| Mock mode | global | red MOCK DATA banner |
| Stream lost | SSE | "Connection lost" inline with Reconnect (replays from the last `seq`) |

---

## 11. Acceptance tests

All commands from the repo root in Git Bash. `B=http://127.0.0.1:8770`.

### 11.1 Backend

Start: `PYTHONPATH=. .venv/Scripts/python.exe -m webapp.server` → prints
`Tubesense web app: http://127.0.0.1:8770/  (local only; --lan to open to the network)`.

| # | Test | Pass condition |
|---|---|---|
| B1 | `curl -s $B/api/health` | `ok: true`, `env` values are booleans, `lan: false`; the response contains no substring of the real key/URI (check with a script that reads `.env` and greps, without printing it) |
| B2 | `curl -s -o /dev/null -w "%{http_code}" $B/../../.env` and `$B/%2e%2e/.env` | 404 (path traversal blocked) |
| B3 | `curl -s $B/api/rides` | R9 `EB LST->BTH` available + recommended; R9 `EB BTH->MLE` unavailable with a `section_truncated` reason; `stations` has 3 Central stations with the coordinates in §6.5; server RSS after the call < 150 MB |
| B4 | `curl -s -X POST -H "Content-Type: application/json" -d '{"run":"R9","track":"EB LST->BTH","tracker":"v2","speed":8}' $B/api/ride/start` then `curl -sN $B/api/jobs/<id>/events` | events in order: `status`(loading, map, features, departure, ready) → `init` → `tick`... (one has `departure: true`) → `end` → `job_end status done`; wall time from start to `end` ≤ 35 s (≈ 10 s setup + 21 s ride); `init.map.curves_m` equals §7.3; `end.summary.vs_truth_median_m` < 10; at least 20 ticks with `paint` |
| B5 | same with `"synthetic_stop":0.5,"stop_variant":"quiet","run":"R5"` | `init.synthetic_stop.start_s` ≈ 71.4; ticks with `in_synthetic_stop: true` and `level_rel: null`; `end.summary.stop.gate_engaged_after_s` ≈ 2.75 and `end_err_m` within ±10 m of +4 (the CLI printed these values for the same settings) |
| B6 | v1: `"tracker":"v1"` on R9 | `init.tracker_label` "tracker v1 (pre-registered)", `length_m` 2205.9, all ticks `gate_state: "none"` |
| B7 | hold/go: start with `"hold":true`, wait for job status `holding`, `POST /api/jobs/<id>/go` | ticks start only after `go`; `POST go` twice → second returns 409 |
| B8 | control: during a 1× ride `POST control {"speed":8}` | tick rate rises; `{"speed":3}` → 400 |
| B9 | busy: start a ride while one runs | 409 `busy` with `running`; with `"replace":true` → 202 and the first job ends `cancelled` |
| B10 | cancel + orphan: start a ride, never subscribe | cancelled after 60 s; no `python` worker left (`tasklist | grep python` count back to baseline) |
| B11 | precomputed: generate R9 file (§6.7), `curl $B/api/ride/precomputed` | lists it with `generated`; `?file=` returns its lines; first line `type: meta`, `label: PRECOMPUTED` |
| B12 | `curl -s "$B/api/analyst/session?atlas=0"` | `integrity.chain_ok` true, `lines` 9; 6 `run_experiment` events, exactly 1 with `verdict.accepted` true (attempt 4), every `recheck_same` true; `sealed.v2.median_error_m` 1.8 |
| B13 | `curl -s $B/api/pack` | `pack_id` `8d251cb4c2ebfb3c`, 16 claims all `check.ok`, 5 checklist items equal to `agent.approve.CHECKLIST`; `md` equals `agent.pack.render(inv)[0]` |
| B14 | `curl -s "$B/api/source/MQ-2022-1791%23001?quote=Bethnal%20Green%20to%20Liverpool%20Street"` | `quote_found: true`, `url` present |
| B15 | `curl -s $B/api/canary` | `ok: false`, reasons mention the press figure |
| B16 | approval in a scratch dir: `mkdir -p $S/drafts; cp outputs_posthoc/investigation.json $S/`; start a second server `TUBESENSE_POSTHOC_OUT=$S PYTHONPATH=. .venv/Scripts/python.exe -m webapp.server --port 8771`; health shows `posthoc_out_is_default: false`, `approval_mirror: false`. POST approve with 4 boxes → 400 listing the missing item; with 5 boxes and no name → 400 `approver_missing`; wrong `pack_id` → 400 `pack_changed`; full → 200, `$S/drafts/APPROVED_8d251cb4c2ebfb3c.md` exists, `$S/approvals.jsonl` has the record, `atlas` "not mirrored (test output dir)". **Never run this against port 8770 with the default output dir.** |
| B17 | approve from a non-loopback address (`--lan` server, curl from the LAN IP) | 403 `forbidden`; with `--lan-approve` the request reaches the logic (test only in the scratch setup) |
| B18 | CSRF: POST approve with `Content-Type: application/x-www-form-urlencoded` | 400; with `Origin: http://evil.example` | 403 |
| B19 | `agent/approve.py` unchanged behaviour: run the old file (from `git show HEAD:agent/approve.py` saved to scratch as a module copy) and the new one on two ports with the same scratch `TUBESENSE_POSTHOC_OUT`; `GET /` and `GET /canary` HTML identical; POST /approve with missing boxes → identical 400 HTML; full approval → same file names and record keys (signature differs only by timestamp) |
| B20 | `curl -s "$B/api/atlas/search?q=did%20TfL%20refuse%20to%20share%20noise%20data"` | `mode: live` online, ≤ 10 s, three modes with hits and ms; with the network off → `mode: cached`, `fallback_reason` set, response ≤ 25 s |
| B21 | `curl -s $B/api/stream/status` while `atlas.live_view` is missing | `available: false`, `windows: []`, reason "atlas.live_view not installed" |
| B22 | ask: `POST /api/ask/start {"question":"x"}` | 400 (too short); valid question while `agent.ask` is missing → 503; when present, events `start` → `retrieval` → `draft` → `verdicts` → … → `final` → `job_end` |
| B23 | `curl -s $B/api/compare`, `/api/method`, `/api/egress` | compare `available` true while the .md exists; method `frozen_unchanged` true; egress lists the Atlas search from B20 and nothing else |
| B24 | `PYTHONPATH=. .venv/Scripts/python.exe -m webapp.api.selftest --base $B` | runs B1, B3, B12-B15, B21, B23 read-only checks and a ride at 8× (skip with `--no-ride`); prints PASS/FAIL per check; never calls approve unless `--scratch-approve` and health says the output dir is not the default |

### 11.2 Frontend

Check at 1920×1080, 1280×720 and 390×844 (Chrome device mode), and on a real phone over `--lan`.

| # | Screen | Pass condition |
|---|---|---|
| F1 | all | no horizontal page scroll at 390 px; body text ≥ 15 px; every agent/ride/Atlas output shows its provenance chip; dark background in screenshots, no white flashes on navigation |
| F2 | Story | the four figures and their sources exactly as §2.1; 92.8 says "reported", never "TfL" |
| F3 | Ride | R9 at 8× finishes in ≤ 25 s after `go`; the dot never leaves the band without the band following; painted squares appear only after ≥ 2 s of confidence; SVG geo view works with the network off; the schematic caption is visible; the QC panel lists R9's flags; the end card shows the offline score and the reference median |
| F4 | Ride stop demo | R5 + 50 % quiet stop: SYNTHETIC banner while it lasts, status "HOLDING: train stopped", stop shading appears only when the stop starts |
| F5 | Agent tab 1 | full replay in 30 s ± 3; the HashChain fills to 9 blocks; the accepted experiment is attempt 4; the sealed card says "MATCHED, NOT BEATEN"; Argon's report rendered with the "its words" frame |
| F6 | Agent tab 2 | with `agent.live_analyst` missing: grey unavailable state, no fake events; with it present: events stream and render |
| F7 | Ask | live ask streams the five stages; a rejected claim shows ✗ with reasons; dropped claims are struck through; a cached answer renders identically with the CACHED chip and its timestamp |
| F8 | Pack | 16 claims verified; opening a source highlights the quote; Approve disabled until 5 ticks + name; confirm dialog text as §4.5; tested only against the port-8771 scratch server; the canary shows REJECTED with SEEDED and LIVE chips |
| F9 | Pack safety | grep: `postJSON('/api/pack/approve'` appears exactly once in `static/js/`, inside the form submit handler; `demo.js` contains no call to it and does not set `checked` on checkboxes |
| F10 | Atlas | three columns render with ms; pipeline JSON copy works; stream card shows "not running" honestly when unavailable |
| F11 | Demo mode | PageDown walks steps 1-6; the ride is pre-warmed (step 3 starts within 1 s of entering it when the job is `holding`); elapsed clock and beat windows visible; Esc exits |
| F12 | Offline | with Wi-Fi off and the server running: Story, Ride (SVG map, fonts fall back), Agent tab 1, Pack (claims, canary, approval in scratch mode) all work; Atlas shows CACHED; Ask live disabled with the reason |
| F13 | PWA | on desktop Chrome at `http://127.0.0.1:8770` the app is installable (manifest + service worker on localhost); on iOS Safari over LAN, "Add to Home Screen" shows the icon and opens standalone; the service worker never caches `/api/*` (check in DevTools → Application → Cache Storage) |
| F14 | Accessibility | keyboard-only navigation reaches every control with a visible amber focus ring; status is never colour alone; `prefers-reduced-motion` removes pulses and transitions; streaming status uses a polite `aria-live` region updated at most once per second |
| F15 | Security | no `innerHTML` with model or document text outside `md.js` (which escapes first); external links have `rel="noopener noreferrer"` |
| F16 | Mock mode | `?mock=1` renders every screen from fixtures with the MOCK DATA banner |

---

## 12. Measured numbers this design relies on (25 Oct, this machine)

- `demo.ride_v2 R5 --track "EB LST->BTH" --synthetic-stop 0.5 --stop-variant quiet --snapshots 60`:
  whole process 11.1 s wall; setup 2.4 s after imports; 783 steps (196 s of ride) replayed in 2.3 s;
  tracker 0.60 ms/step; "end of stop: dot +4 m from the curve-edge reference; gate engaged 2.75 s into
  the stop".
- `demo.ride_v2 R9 --track "EB LST->BTH" --snapshots 60`: 9.6 s wall; departure detected at +32.8 s
  (armed at 0, onset back-dated 1.25 s); setup 1.9 s; 661 steps (165 s) in 2.1 s; 0.67 ms/step.
- Importing `demo.ride_v2` (with numpy, pandas, matplotlib, the frozen modules): 5.1 s.
- R9 reference for a 24 Oct ride via `EV.load_track` with a constructed item + `E.truths`: 10 matched
  curve edges, 2.0 s, works (§7.1 step 5).
- R9 tick sample (v2, arm from `sections.json`): step 40 → MAP 32.6 m, 90 % 19.4-47.2 m, lock 1.00;
  step 240 → 881.1 m, 848.8-881.0 m, lock 0.98; `gate_release` is a string (do not cast it to
  float). `live_noise(R9)` returns 339 one-second levels from the cache.
- Pacing consequence: R9 at 8× ≈ 21 s of animation after `go`, at 4× ≈ 41 s, at 1× ≈ 2 min 45 s.

---

## 13. Open issues and risks

1. **Cold start of a ride is about 10 s.** Demo mode hides it with `hold`/`go`; outside the demo the
   loading phases are shown. Do not try to keep a resident worker: the tracker settings are
   process-global.
2. **Departure detection is the weak link** (it missed 2 of 10 Central departures in the forward
   test). R7 LST→BTH and R10 BTH→LST are marked in the catalogue; the worker emits a warning if the
   detected onset is far from the offline one. Never "fix" the departure from the offline time in
   the live path: that would not be causal.
3. **`agent.ask`, `agent.live_analyst`, `atlas.live_view`, `atlas.stream_replay` and
   `model_comparison.json` may not exist when the engineers start.** Every consumer has an honest
   unavailable state; the backend checks with `find_spec` on each request (cheap), so they light up
   when they land without a restart (except modules imported in process: `agent.ask.list_cached`
   and `atlas.live_view` are imported lazily per request).
4. **`agent.live_analyst` must not write the recorded `outputs_posthoc/attempts.jsonl`** (B1b's
   responsibility). The web app shows the `start` event fields so the presenter can see which log it
   uses.
5. **LAN mode exposes the app to the network**: anyone on the Wi-Fi could start Gemini or Atlas
   jobs. `--lan` prints a warning; approval stays loopback-only unless `--lan-approve`. Use a phone
   hotspot, not venue Wi-Fi.
6. **PWA over LAN**: service workers need a secure context; `http://<LAN IP>` is not one, so on a
   phone the app is a home-screen bookmark (iOS works; Android Chrome makes a shortcut, not an
   installed app). Full install works on the laptop (`localhost`).
7. **Google Fonts and map tiles are third-party requests from the browser.** Both are optional;
   the app looks correct with system fonts and the SVG map. The Privacy screen lists them.
8. **Model-comparison JSON shape is unknown** until B2 lands; the screen degrades to rendering the
   Markdown.
9. **Memory**: one heavy job at a time is enforced, but the presenter's other terminals (the
   approval server on 8765, IDE) also use RAM. The ride worker peaks well under the old
   matplotlib window (no figure), but measure RSS during B4 and report it.
10. **The 8765 approval server stays up.** Both servers write the same `approvals.jsonl`; that is
    fine (append-only lines, each its own record).
