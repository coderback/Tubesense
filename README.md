# Tubesense

Tubesense turns ordinary London Underground rides, recorded on a phone, into privacy-safe
measurements placed between stations without GPS: phone sensors find the station stops and the
curves, which places the train to a median of about 2 m on new rides. A Gemini 4 Argon agent then
checks TfL's own noise and grinding records and drafts a cited evidence pack for a London Assembly
member. A deterministic verifier checks every quote and number, and a human approves the pack as a
draft; nothing is sent automatically.

### For judges: start here

| | |
|---|---|
| **Live demo** | https://tubesense-production.up.railway.app (hosted copy; human approval and live phone sensors are switched off there and run on the presenter's laptop) |
| **Demo video** | see *Submission* below |
| **Run it locally** | `pip install -e .` then `PYTHONPATH=. python -m webapp.server` and open http://127.0.0.1:8770/#/demo (needs `.env` with `GEMINI_API_KEY`, `MONGODB_URI` for the live agent and Atlas parts) |
| **Paper and deck** | [Technical paper (PDF, 3 pages)](docs/Tubesense_technical_paper.pdf) · [Deck (PDF)](docs/Tubesense_deck.pdf) · diagrams: [verification loop](docs/diagrams/verification_loop.png), [Atlas data flow](docs/diagrams/atlas_data_flow.png). Both as submitted on 25 Oct, so they predate the phone-app work described below |
| **Results, GO and NO-GO** | [What works today](#what-works-today-and-the-evidence) · [`outputs/FINDINGS.md`](outputs/FINDINGS.md) |
| **Gemini 4 Argon** | [Gemini models and agents used](#gemini-models-and-agents-used) · [Verification loop](#verification-loop) |
| **MongoDB Atlas** (all six scored features) | [MongoDB Atlas](#mongodb-atlas) |
| **The Real World** | [Messy inputs](#messy-inputs-handled-automatically-the-real-world) · [Responsibility](#responsibility) · [Privacy](#privacy) |
| **Agents That Ship** | [Where the human approves](#where-the-human-approves) · [Web app](#web-app-what-the-demo-runs) |

The repository has four layers:
- **Sensing** (`tubesense/`, `scripts/`, frozen at git tag `prereg-24oct`): which phone signals
  repeat between rides, section loudness from phone audio, GPS-free positioning, a live tracker.
- **Post-hoc improvements** (`posthoc/`): official section lengths and a stop gate for the tracker,
  evaluated on the 23 Oct rides only.
- **Agent and data** (`agent/`, `atlas/`): the Argon agents (analyst, investigator, Ask Argon, roughness
  reader, recheck, ride reports), the scoring harness, the verifier, the approval page, and the MongoDB
  Atlas store, search and stream processing.
- **App** (`webapp/`, `live/`, `mobile/`): the web app the demo runs on (live ride tracking, the agents at work,
  the evidence pack with human approval), and live phone sensors read from phyphox over the network.

The sensing results are in [`outputs/FINDINGS.md`](outputs/FINDINGS.md); the architecture and build
plan are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); privacy is in [`docs/PRIVACY.md`](docs/PRIVACY.md).

Phone-audio levels are relative (dB(A) re digital full scale, from an uncalibrated phone). The only
absolute levels are two indicative readings from the NIOSH Sound Level Meter app (H5 below).
Tubesense is not a sound level meter, a safety system or a hearing-loss claim.

## Submission (Encode London Hackathon 2026)

**Problem.** Parts of the Tube are loud enough to be a workplace health concern. TfL's own in-cab
measurements put Liverpool Street→Bethnal Green at 87 dB LAeq and Bethnal Green→Mile End at 83 dB
on 24 June 2024 (FOI-1231-2425); a January 2026 press report puts Liverpool Street–Bethnal Green at
92.8 dB, second loudest on the network (secondary source, primary not located). TfL attributes the
noise mainly to rail corrugation, ripples worn into the rail that return after grinding; it says grinding must be
repeated "usually 6 months – a year" (TfL Tube Noise presentation, 6 Dec 2022). Both sections were
ground for noise in Dec 2021–May 2022 (Mayor's Question 2022/1791), but TfL does not publish
section-level noise or grinding data.

**Named user.** Elly Baker AM, Labour transport spokesperson on the London Assembly. At Mayor's
Question Time on 16 July 2026 (question 2026/2156) she said TfL had turned down most of her data
requests on residents' noise complaints and the measures taken, including a six-month grinding plan,
because grinding is planned only eight weeks ahead. Tubesense gives her two things:
- **A way to place ordinary phone measurements between stations without GPS.** The median error is
  about 2 m, measured against curve landmarks on Central line rides recorded after the maps were frozen.
- **A cited evidence pack** that says plainly what the public record does and does not show. Every
  quote and number is checked by code, the framing is reviewed against the full sources, and a human
  approves the pack before use. It also drafts the specific information requests to make next.

It is built for Elly Baker AM's office, but the people it serves are everyone who lives with that noise:
train drivers in the cab every shift (TfL's own figures are cab measurements; their unions are RMT and
ASLEF), passengers riding through the same sections, and residents above the line, whose noise complaints
were among the data she asked TfL for. Tubesense measures inside the train only; it does not measure noise
or vibration in homes, and it makes no hearing-damage claim.

**Tracks entered:** Google DeepMind (Build with Gemini 4 Argon), MongoDB Atlas, The Real World, and
Agents That Ship.

**Demo video:** TODO: add the link to the 3-minute video.

**Built during the hackathon.** Every commit in this repo dates from 23–25 Oct 2026, after kickoff.
The data is our own rides from 23 Oct, plus the 24 Oct rides analysed under the pre-registered plan.

### What works today, and the evidence

| result | verdict | evidence (in `outputs/`) |
|---|---|---|
| Phone gyro curve signatures repeat ride to ride | **GO**: same-section r 0.76–0.99 vs different-section 0.20 | `FINDINGS.md` (a), `gonogo/gyro_*.png` |
| Positioning between stations without GPS (offline) | DTW on yaw places held-out curve edges to a median 6 m, against 24 m for the time-fraction baseline | `FINDINGS.md` (e), `positioning/improvement_log.md` |
| Live tracker (causal, what the demo runs) | Median 2.2 m on held-out rides against an independent truth, vs 11.2 m for a timetable. Between curves it is no better than the timetable (11 vs 16 m). Its 90 % band covers the truth 92 % of the time | `FINDINGS.md` (f), `online/online_eval.json` |
| Section loudness from phone audio | **NO-GO** on both days. The frozen rule needs Liverpool Street→Bethnal Green to be louder than Bethnal Green→Mile End on every trip, and one of 11 (R10 westbound, −0.13 dB) went the other way. The mean difference, +0.54 dB, is also inside the Voice Memos limiter's compression (23 Oct alone: WEAK, 5 of 5, +0.6 dB) | `FINDINGS.md` (24 Oct, (c)), `gonogo/loudness.json` |
| Magnetometer as a position fingerprint | **NO-GO** for \|B\|; the bias-free field anomaly is **WEAK** and adds nothing to positioning | `FINDINGS.md` (b), (e) |
| Tracker v2: TfL Working Timetable lengths + stop gate (post-hoc, 23 Oct only) | Identical to v1 on the real rides. After a simulated 30 s tunnel stop, median end-of-stop error 338 m → 11 m (idealised stop), 23 m (platform-level quiet), 165 m (a real platform dwell replayed); worse than v1 in 2 of 16 replayed-dwell cases. **Out of sample it failed:** on a single Elizabeth line test ride the gate (tuned on Central data) caught 0 of 3 real platform stops, because the stopped-vs-moving contrast was smaller and its moving reference absorbed the stop. It was left unchanged so the sealed 24 Oct test stayed valid. **In that test the gate never engaged** on 10 real sections, including one with a real 6 s stop between stations | `outputs_posthoc/eval_v2.json`, `outputs_posthoc/s0_elizabeth.md`, `FINDINGS.md` (24 Oct) |
| Elizabeth line test ride (exploratory, n = 1, outside the pre-registration) | A knock on the phone was visible to both microphone and accelerometer and synced them to 4.838 ± 0.021 s, agreeing with whole-ride sync to 0.04 s; finger snaps and a figure-8 wave each showed in only one stream. The phone ranks the Elizabeth line 3.5–5.6 dB quieter than the Central line sections (relative levels, ranking direction only) | `outputs_posthoc/s0_elizabeth.md` |
| Argon analyst tuning the tracker against a fixed harness (post-hoc, 23 Oct only) | 6 experiments; 1 accepted by the pre-declared rule: band 15.9 % narrower, mean per-run error −0.81 m (95 % CI −1.94 to −0.05), Bethnal Green→Mile End 8.8 → 5.9 m. **Scored once on the 24 Oct rides, it matched v1 and did not beat it:** median 1.8 m vs 1.8 m, 90 % coverage 0.94 vs 0.95 on 10 sections. The dev gain did not carry over | `outputs_posthoc/analyst_session.md`, `attempts.jsonl` |
| **24 Oct, pre-registered H2b:** the frozen 23 Oct maps on new Central rides | **GO**: median 1.95 m on Liverpool Street⇄Bethnal Green vs 8.2 m for a timetable; 90 % band coverage 0.96 (6 sections; threshold ≤ 5 m and ≥ 0.80) | `FINDINGS.md` (24 Oct), `online/forward_test.json` |
| **24 Oct, pre-registered H1/H2a:** a second line, Hammersmith & City | **NO-GO**: only one usable pair. Its gyro signature (r 0.72) did not beat the 0.84 different-section maximum, and the tracker (52 m) did no better than a timetable (47 m). The online departure detector fired late on both runs | `FINDINGS.md` (24 Oct), `gonogo/repeatability.json`, `online/online_eval.json` |
| **24 Oct, pre-registered H3/H4:** magnetism, deep tube vs sub-surface; platform signatures | H3: the pre-registered test says the lines differ (excess r +0.23, CI 0.10–0.36), but the Hammersmith & City side is a single pair, so we do not claim it. H4 (exploratory; street-level calibration 45.6 µT, misfit 0.5 %): inside a stopped train the field is 2–3× Earth's, and repeat visits to one platform vary nearly as much as different stations do (sd 19 vs 22 µT), so there is no stable platform fingerprint in this data | `FINDINGS.md` (24 Oct), `gonogo/mag_compare.json` |
| **24 Oct, pre-registered H5:** absolute level, NIOSH SLM app (indicative) | LAeq 91.9 dB(A) Liverpool Street→Mile End and 87.3 dB(A) back, one trip each, iPhone built-in microphone (validated only up to about 85 dB(A)). The phone's Voice Memos levels do not track this difference, so they cannot be turned into dB(A) with one offset | `outputs_posthoc/h5_niosh.md` |
| Investigator corpus and search in Atlas | 14 public documents, 44 chunks. Hybrid search ranks the expected document first on 12/12 test questions (questions written by us, not blind) | `atlas/search.py` |
| Argon reads TfL's 2018 rail-roughness charts (post-hoc) | The FOI PDF is four charts stored as images. Argon's readings matched a separate pixel reader on 120/120 values (median 0.3 dB). Both runs find one rough stretch at about 725–905 m on both rails and both wavelength bands, overlapping TfL's own statement in the same FOI ("elevated rail roughness levels between 720 and 880m", grinding planned Feb 2019). The checks also caught Argon's mistakes (line colours misnamed, readings that did not match the pixels, two summary sentences with numbers it worked out itself), which were left out. TfL's 2018 data, Bethnal Green→Mile End only; **pending a human check by eye** | `outputs_posthoc/roughness/roughness.md`, `HUMAN_CHECK.md` |
| Argon re-derives our headline numbers with its own code (post-hoc) | Given only summary tables and the frozen rule text, never our answers, Argon's code (code execution) reproduced all 65 values (the loudness contrast and NO-GO, section means, H2b 1.95 m, the NIOSH arithmetic) in about 35 s. A canary plants errors just outside tolerance in our values, and the comparison catches every one | `outputs_posthoc/recheck/latest.md` |
| Flash vs Argon on the same investigator task (post-hoc) | 6 runs each. **On the verifier loop there was no difference:** 12/12 valid outputs, every rejection fixed in one round, nothing dropped, the same time (median 107 s vs 110 s). Argon used much more of the record (it cited the Mayor's 16 Jul 2026 transparency promise in 5/6 runs vs 0/6, and our NO-GO loudness verdict in 6/6 vs 1/6), but also made more framing errors the verifier cannot catch (median 3 major per run vs 0; one unblinded judge), mostly when joining two documents in one claim | `outputs_posthoc/model_comparison.md` |

The 23 Oct numbers in this table are from the outputs as they were at git tag `prereg-24oct`. Since the
24 Oct run, `outputs/` holds both days, so some of those files now give pooled numbers.

### Verification loop

- **Leave-one-run-out.** Every positioning number comes from a ride the map was not built from. The
  demo rebuilds its map without the ride being shown.
- **Independent truth.** There is no GPS in a tunnel, so the truth comes from physical landmarks: the
  start and end of each curve, matched across rides. The headline tracker number is scored against
  interpolation between those landmarks with no DTW, so it doesn't share the tracker's method.
- **Fair tests.** The gyro is hidden around each landmark to test positioning between curves, and that
  result is reported even though it is unflattering.
- **Adversarial review.** Each result was attacked by independent checking passes before being
  reported. They caught a heading leak, an inflated "37 m → 3 m" demo line, a metre axis that was
  really clock time, and a departure detector that fired on braking. All of these are fixed and
  documented in `FINDINGS.md`.
- **Reproducible.** `scripts/run_gonogo.py --clean` regenerates every number and plot (~12 min).
- **Pre-registration.** The 24 Oct analysis was written down and frozen (git tag `prereg-24oct`)
  before those rides. Anything decided later is labelled post-hoc.
- **The agent cannot mark its own homework.** The Argon analyst proposes tracker settings from an
  allow-list and calls `agent/harness.py`, which it cannot edit. The harness scores each proposal on
  the 23 Oct rides only, decides acceptance with a rule fixed before the session (every section's
  calibration must hold, not just the average), and logs every call to an append-only, hash-chained
  log mirrored to Atlas. One configuration was submitted and tagged (`v2-posthoc`) before any 24 Oct
  ride existed, and the harness allows exactly one scoring on the 24 Oct rides. That scoring ran on
  25 Oct (attempt 8 in the log): v2 matched v1 and did not beat it.
- **Every claim in the evidence pack is checked by code.** `agent/verify.py` requires each quote to
  match the stored source verbatim and each number to trace to a quote, our statistics or a
  recomputed formula, and rejects known overclaims (e.g. 92.8 dB as "TfL's own data"). Rejected
  claims go back to the agent with the reason, and anything still failing is dropped and listed.
- **Then the meaning is reviewed against the full sources.** The verifier cannot tell whether a
  verbatim quote is used in the sense its source means. Before approval, independent review agents
  read every claim against the full documents, and skeptics tried to refute each finding. Here these
  were Claude Code agents run by the developer; the findings file Argon revises from is plain JSON. On the
  25 Oct run they upheld 21 findings the verifier had passed, 4 of them major:
  - a description from a 2024 letter placed inside a 2022 letter;
  - a commitment the Mayor did not make;
  - the wrong reason for our own NO-GO;
  - "publicly available documents" when we had read 14.

  Argon revised its draft, the verifier re-checked every claim, and a recheck found 3 minor issues,
  fixed in a targeted revision (`outputs_posthoc/pack_review*.json`, `investigation_draft*.json`).
- **Live answers get the same treatment, automatically.** Ask Argon (`agent/ask.py`) runs the verifier
  loop on every claim, then a separate Argon review call checks the verified claims against their cited
  sources only; flagged claims go back for one revision, and a claim returned unchanged is withheld.
  The one-line answer may cite verified claims only and is checked in code for unsupported numbers and
  overclaims (e.g. a ranking from our NO-GO phone data, "corrugation has returned", hearing damage).
  A cached answer is re-verified at the moment it is shown.
- **Two independent computations must agree.** Argon re-derives our headline statistics with its own
  code, and code compares its numbers with ours to tolerances fixed in advance (`agent/recheck.py`).

### Messy inputs, handled automatically (The Real World)

None of these were hand-coded by run name; all are detected from the data and listed in `outputs/qc.json`:

- **A phone on the floor.** Found from its tilt and how much vibration got through.
- **Two phyphox pauses** (a false start, and a battery alert mid-section). phyphox hides pauses in its
  timestamps; they were recovered from its event log.
- **A trip split across two files**, after getting off at the wrong stop.
- **Which runs had a calibrated vs raw magnetometer.** This reversed our own notes.
- **iOS recalibrating the magnetometer mid-ride.**
- **Triple-tap sync marks that turned out to be silent in the audio.** Sync falls back to matching
  vibration against sound.

### Responsibility

- **Privacy.** Raw audio never leaves the laptop and is never written anywhere. Only 1-second levels
  and frequency bands are kept, and speech cannot be reconstructed from them. See *Privacy* below.
- **Honesty about uncertainty.**
  - Levels are relative, not certified dB.
  - The tracker shows a 90 % band, and that band is checked against the truth.
  - Weak results are labelled WEAK or NO-GO.
  - The limits are listed in `FINDINGS.md`: 2–3 rides per section, one phone, one morning.

### Gemini models and agents used

| Agent | Model | File | What it does |
|---|---|---|---|
| Analyst | `gemini-4-argon` (thinking medium), function calling | `agent/analyst.py` | Proposes tracker settings with a stated hypothesis, runs them through the fixed harness (four tools: list settings, baseline, run, submit), explains the results and submits one configuration. The session is recorded in `outputs_posthoc/analyst_session.md`. A first session failed on a bug in our tool wrapper and is kept, labelled, in `analyst_session_failed1.md`. |
| Investigator | `gemini-4-argon` (thinking medium), structured JSON output, long context | `agent/investigator.py` | Reads the whole document corpus plus the Atlas hybrid-search hits for six sub-questions and our measured statistics, and returns typed claims (sourced / measured / derived / inference) with verbatim quotes, plus suggested information requests. Up to two rounds of fixes after the verifier rejects claims; with `--review` it revises its own verified draft to resolve review findings. Run on 25 Oct: 15 claims, 5 rejected by the verifier and fixed; after the review rounds, 16 claims, all verified, 0 dropped, plus 4 information requests. The pack (`outputs_posthoc/drafts/`) awaits human approval. |

| Ask Argon (live) | `gemini-4-argon` (thinking medium), structured output | `agent/ask.py` | Answers a question in 2–5 cited claims from Atlas hybrid-search hits and our statistics, through the verifier loop (up to two fix rounds), a separate Argon review of the claims against their cited sources, and a code-checked one-line answer. Typically 15–60 s. Every answer is saved and stored in Atlas `asks`; a saved answer can be replayed offline, labelled CACHED and re-verified on screen. |
| Live analyst | `gemini-4-argon` (thinking medium), function calling | `agent/live_analyst.py` | A short live continuation of the analyst: it states a falsifiable hypothesis, the unchanged harness scores the setting on the 23 Oct rides (about 40 s), the pre-declared rule decides, and Argon says whether the hypothesis held. It has no submit or test tools (the one sealed test is spent) and logs to its own hash chain (`attempts_live.jsonl`, Atlas `attempts_live`), so the main chain is untouched. In the session recorded on 25 Oct both hypotheses held and both settings were rejected. |
| Roughness reader | `gemini-4-argon` (thinking medium), PDF input, structured output | `agent/roughness.py` | Reads the chart images in TfL's 2018 rail-roughness FOI PDF; checked against the page text, the axes and a model-free pixel reader; a human checks by eye before use. |
| Recheck | `gemini-4-argon` (thinking medium), code execution | `agent/recheck.py` | Re-derives our headline statistics with its own code from summary tables; code compares the numbers with ours. |
| Ride reports | `gemini-4-argon` (thinking medium), structured output | `agent/ride_report.py` | A plain-English report of one recorded ride (pauses, phone moved, sync quality, section times). Each number must name the stored field it comes from, and code checks its value, sign, unit and source field. Wording is not fully checkable in code (two adversarial reviews kept finding phrasings, such as synonyms and negations, that a word list misses), so a person reads and approves each report before use. |
| Model comparison | `gemini-4-argon` vs `gemini-3.8-flash` | `agent/compare_models.py` | The investigator task, 6 runs per model, protocol written before the runs (see the results table). |

Only public document text and summary statistics are sent to Gemini; no audio and no raw sensor data.
Every Argon call uses thinking level medium. Station-announcement transcription
(`gemini-3.5-transcribe`) is designed (see `docs/ARCHITECTURE.md`) but not built.

### MongoDB Atlas

Cluster: dedicated M10 on AWS eu-west-1, MongoDB 8.0, auto-scaling capped at M30. `atlas/setup.py`
creates everything idempotently.

| Scored feature | Where | What it does |
|---|---|---|
| Atlas Search | `documents.docs_text` | Full-text search over the TfL and London Assembly corpus |
| Automated Embedding + Voyage AI | `documents.docs_auto` (`autoEmbed`, `voyage-4`) | Atlas embeds each chunk and each query itself |
| Vector Search | `$vectorSearch` on `docs_auto`; `yaw_windows.yaw_vec` index (64-d, euclidean) created for sensor fingerprints | Semantic retrieval for the investigator. Sensor fingerprints are not loaded yet |
| Hybrid Search | `$rankFusion` over `docs_text` + `docs_auto` in `atlas/search.py` | The investigator's retrieval: ranks the expected document first on 12/12 test questions |
| Stream Processing | processor `live_windows` (`atlas/stream/processor.json`), SP2 workspace in AWS Dublin | Reads the change stream of `ingest_live`, where each ride (a replay run through the tracker now, or live phone sensors) arrives one row per second under a random trip id; groups 10 s tumbling windows (mean and max relative level, distance covered, confident and stopped seconds) and `$merge`s them into `live_view`, which the web app shows. Windows are 10 s of wall clock, so at 4× replay one window covers about 40 s of the ride. `atlas/stream_setup.py` creates, starts, stops and tears it down; the processor runs (and is billed) only while in use |

Other collections: `attempts` (the analyst's hash-chained log) and `attempts_live` (the live session's
own chain), `section_stats` (the statistics the investigator may cite), `claims`, `approvals`, `asks`,
`roughness`, `rechecks`, `ride_reports`, `ingest_live` and `live_view`. Only public document text is embedded (Automated
Embedding runs in a US region); rider data is never embedded.

### Where the human approves

The web app's Evidence pack page (and the standalone `agent/approve.py` page on
`http://127.0.0.1:8765/`, local only; both use the same checks) shows the evidence pack, every claim
with its type, sources and a fresh verification result, and a checklist:

- I have read every claim and opened its sources.
- Our own numbers are labelled relative / indicative, never as certified dB.
- The limits section says what the evidence cannot show.
- No personal data, speech or raw audio is included.
- I understand approval creates a draft only: nothing is sent automatically.

Approval needs every box, a name, and the claims to still pass the verifier. It writes
`outputs_posthoc/drafts/APPROVED_<pack_id>.md` and a record signed with SHA-256 (pack + approver +
time + verifier version) to `outputs_posthoc/approvals.jsonl` and Atlas `approvals`. Rejections are
recorded with a reason. `/canary` shows the verifier rejecting a deliberately false, labelled claim.

### Data sources

- **Our own rides:** 9 recordings on 23 Oct 2026 (Victoria and Central lines) and, on 24 Oct, 6 Central
  and 3 Hammersmith & City rides, 2 NIOSH-only trips and a street-level magnetometer calibration. All
  made on an iPhone 12 with phyphox 1.2.1 (accelerometer and gyroscope at about 99 Hz, magnetometer,
  barometer at about 1 Hz) and Voice Memos audio. Raw files are not published.
- **TfL rail roughness plots** (FOI-1730-1819, Jan 2018, Bethnal Green→Mile End eastbound): read by the
  roughness agent; context only, not our measurement.
- **TfL Unified API:** the public `Line/central/Route/Sequence`, fetched for track geometry. Its
  lineStrings join stations with straight segments, so the frozen v1 uses straight-line lengths and
  flags them. Station coordinates are in `tubesense/stops.py`.
- **TfL Working Timetables** (Central TTN 37/25 via FOI-3481-2425; Circle & H&C WTT 31 via
  FOI-1642-2021): official section lengths used by the post-hoc v2 (`posthoc/lengths.py`), e.g.
  Liverpool Street–Bethnal Green 2.27 km, Bethnal Green–Mile End 1.64 km.
- **TfL noise figures**, for context and ranking checks only. No TfL figure is used in a calculation.
  The primary source is TfL's in-cab LAeq of 24 June 2024 (FOI-1231-2425: Liverpool Street→Bethnal
  Green 87 dB, Bethnal Green→Mile End 83 dB). The 92.8 dB figure is a press report (27 Jan 2026).
- **Investigator corpus** (`atlas/corpus.py`, 14 public TfL / London Assembly documents plus one news
  report, 44 chunks in Atlas `documents`). What it does and does not cover:
  - Grinding or reconditioning evidence for Liverpool Street–Bethnal Green comes only from Mayor's
    Question 2022/1791 (ground Dec 2021–May 2022) and the Duncan Weir letter of 9 Dec 2022
    (reconditioning planned for FY2023/24). Neither TfL noise-update letter (Aug 2024, Aug 2025)
    mentions Liverpool Street, Bethnal Green, Mile End or Central line grinding.
  - The 92.8 dB figure appears only in a secondary source (Brixton Buzz, 27 Jan 2026); the primary
    FOI response is not in the corpus, so it is cited as "reported".
  - Stored titles are our own descriptive titles, not the sources' titles. TfL and GLA re-use licences
    were not verified, so everything is quoted for citation only.
- **Prior work** we build on and cite:
  - Royal Holloway 2019 (Sensors 19(19):4184) and 2017 (arXiv 1704.00148)
  - MLoc (arXiv 2003.10531)
  - Dieckow et al. 2025 (arXiv 2507.19327)
  - Huyan et al. 2023 (JMIR, PMC10686533)

## Setup

Python 3.13, Windows or macOS/Linux. No system ffmpeg is needed, because PyAV decodes the iPhone .m4a files.

```
python -m venv .venv
.venv/Scripts/python -m pip install -e .        # macOS/Linux: .venv/bin/python
```

## Data layout (not in git)

```
data/raw/phyphox/<stem>.xlsx    phyphox export: Accelerometer, Gyroscope, Magnetometer, Barometer, Metadata Time
data/raw/audio/<stem>.m4a       Voice Memos recording of the same ride
```

`<stem>` is `R<id>_<DIR>_<FROM>-<TO>`, for example `R3_EB_LST-MLE` or `R0_NB_VIC_OXC`. The `.xlsx` and
the `.m4a` file of a run share the same stem. Station codes and routes are listed in `tubesense/stops.py`.
`data/interim/` holds caches: the parquet sensor tables and the per-second noise levels.

## Run

```
.venv/Scripts/python scripts/run_gonogo.py           # whole chain, about 11-12 min
.venv/Scripts/python scripts/run_gonogo.py --clean   # wipe outputs/ first (keeps FINDINGS.md)
```

The chain runs these steps in order. Each step is also a standalone script in `scripts/`:

| step | module | writes |
|---|---|---|
| inventory | `tubesense/inventory.py` | `outputs/inventory.csv`, `inventory.md`: sensors, rates, gaps, pauses, audio format |
| noise | `tubesense/noise.py` | `outputs/noise/<stem>_levels.csv` (1-s LAeq, LAFmax, third-octave bands), `<stem>.png`, `summary.*` |
| sync | `tubesense/sync.py` | `outputs/sync.json` (audio offset and uncertainty per run), `outputs/sync/<stem>.png` |
| segment | `tubesense/stops.py` | `outputs/sections.json` (dwells and station-to-station sections), `outputs/segment/<stem>.png` |
| qc | `tubesense/qc.py` | `outputs/qc.json` (messy-input flags), `outputs/qc/*.png` |
| repeatability | `tubesense/features.py`, `tubesense/repeat.py` | `outputs/gonogo/repeatability.json`, `gyro_*.png`, `mag_*.png` |
| mag_anomaly | `tubesense/magnetics.py`, `features.py` (`mag_destep`, `field_anomaly`) | `outputs/gonogo/mag_anomaly.json`, `mag_anomaly.png`: bias-free magnetometer follow-up |
| mag_compare | `tubesense/magnetics.py` | `outputs/gonogo/mag_compare.json`, `mag_compare.png`: field-anomaly repeatability per line (deep tube vs sub-surface), raw vs calibrated; platform signatures once a `CAL_*` recording validates |
| ablation | `tubesense/position.py` | `outputs/positioning/ablation.json`, `ablation.png`, `gap_length.png`, `improvement_log.md`: held-out positioning error per sensor set (L0, L1, DTW), per section, per line and overall |
| online_eval | `tubesense/online.py` | `outputs/online/online_eval.json`, `online_eval.png`, `clip_lock.png`, `track_<run>.png`: leave-one-run-out test of the live tracker; `maps.json`: the demo maps |
| forward_test | `tubesense/online.py` | `outputs/online/forward_test.json`: rides recorded after the freeze, tracked with the frozen maps in `outputs/prereg/` |
| loudness | `tubesense/loudness.py` | `outputs/gonogo/loudness.json`, `loudness_sections.csv/png`, `loudness_<stem>.png` |

Lines and stations live in `tubesense/stops.py` (`STATIONS`, `LINES`). A new route only needs its stations
there; every analysis then picks up any section ridden at least twice. The 24 Oct rides (second line:
Hammersmith & City) are analysed under the pre-registered plan in [`outputs/PLAN_24Oct.md`](outputs/PLAN_24Oct.md),
frozen at git tag `prereg-24oct`.

Time conventions: sensor time is phyphox experiment time, which stops while phyphox is paused.
`Run.to_wall()` maps it to wall-clock time, and `sync.sensor_to_audio()` maps it to seconds in the
audio file.

## Web app (what the demo runs)

```
PYTHONPATH=. .venv/Scripts/python -m webapp.server        # http://127.0.0.1:8770/  (local only; --lan to open to the network)
```

Standard library only, no build step; it works at phone width and can be added to a phone's home
screen. Pages: Story; Ride (a recorded ride replayed through the tracker now, second by second: the dot
on a noise-coloured strip map with its 90 % band, a geographic view (schematic, straight lines between
stations), the messy-input flags); Agent (the recorded analyst session with its hash chain re-checked,
and a live session); Ask Argon (live, with every verifier verdict shown and rejected claims struck
through); Argon at work (roughness charts, recheck, ride reports); Evidence pack (approval); Atlas (the
same question through full-text, vector and hybrid search, and the Stream Processing windows); Why Argon;
Privacy & method; Research Box (a concept for where this goes next, not built). The Demo button walks
the 3-minute path. Heavy work (tracker, agents) runs in separate processes whose output is streamed to
the page; anything replayed, cached or precomputed is labelled as such.

### Live phone sensors

With phyphox's "Allow remote access" on, the laptop reads the phone's sensors over the network
(`live/phyphox.py`) and runs the live detectors (`live/detect.py`): yaw rate about gravity, moving or
stopped, the double knock used for sync, placement changes, pressure steps. Only per-second summaries
leave the laptop (to Atlas `ingest_live`, then the stream processor). Off a train this shows live
sensing, not position. Venue Wi-Fi often isolates devices; connect the laptop to the phone's hotspot.

```
PYTHONPATH=. .venv/Scripts/python -m live.capture --url http://172.20.10.1 --jsonl [--atlas]
PYTHONPATH=. .venv/Scripts/python -m live.capture --mock R9 --jsonl   # no phone: replays a recording, labelled MOCK
```

The web app's Live sensors page runs the same capture with a live yaw trace, the moving/stopped state,
knocks, placement, pressure and |B|. Tested on the iPhone 12 over its hotspot on 25 Oct: rotation,
stillness, pressure and |B| come through live. The knock detector is tuned on the rides' firm case knocks
(peaks 30–46 m/s²); light knocks on a phone lying on a table (4–7 m/s²) stay below its 10 m/s² threshold.

### Phone capture app (`mobile/`, extended after the 25 Oct submission)

`mobile/` is a React Native / Expo app that turns the phone's own sensors into one summary row per
second and posts it to the web app's `/api/live/ingest`. The prototype in the submission had never run
on a phone. On 25 Oct, after the deadline, it was run on the iPhone 12 through Expo Go over the hotspot
(motion 100 Hz, magnetometer 20 Hz, barometer 1 Hz, one row a second) and extended:

- **Sound level.** Microphone samples are streamed into memory, A-weighted (within 0.05 dB of the
  IEC 61672 curve up to 4 kHz at 48 kHz), summed for the second and dropped. Audio is never written
  to a file or sent; each row carries only the level and the loudest 125 ms. Levels are dBFS (relative)
  until calibrated in the app against a reference meter such as the NIOSH SLM app; then it shows
  indicative dB(A). Not a certified meter.
- **Frequency content.** Octave bands from 63 Hz to 8 kHz and the dominant frequency in 100–2000 Hz
  with its prominence, defined as in `tubesense/noise.py`, for the corrugation question.
- **Raw magnetometer** alongside the calibrated one, so live rows match the raw phyphox Central runs.
- **The pipeline's 3–40 Hz vibration band.** It is sent for consistency with the pipeline, but the
  stop detector keeps the simpler measure: on the 15 recorded rides it separated stopped from moving
  slightly better (AUC 0.936 vs 0.918).
- **Gravity direction and a "phone moved" flag** when the phone turns more than 30° in 10 s.
- **A departure gate.** The ride starts when the phone feels the train pull away (a horizontal push
  averaging ≥ 0.45 m/s² over 5 s, every second in the same direction, phone not handled), not on
  vibration, which a hand-held phone also produces. All 29 recorded departures reached ≥ 0.62 m/s²
  (median 1.21); in simulation, fidgeting and starting to walk do not trigger it. A **Train left**
  button starts the ride by hand. Not yet tested on a train.
- **An isometric 3D map** of the section (schematic line, generated buildings).

It type-checks and its maths passes its tests (`npm test`). Steps and the row format are in
`mobile/README.md` and `mobile/CONTRACT.md`.

### Terminal demo (earlier)

`scripts/demo.py` replays a ride through the online tracker: a dot moves along a strip map of the
section coloured by the map's noise level, with a 90 % band, while the ride's own 1-s noise is painted
where the tracker puts it. Everything shown at a moment uses only data up to that moment. If the ride
is one of the map's runs, the map is rebuilt without it (leave-one-out).

```
.venv/Scripts/python scripts/demo.py R5 --track "EB LST->BTH"                 # window, real time
.venv/Scripts/python scripts/demo.py R5 --track "EB LST->BTH" --speed 6 --truth  # 6x, with the offline check
.venv/Scripts/python scripts/demo.py R5 --track "EB LST->BTH" --clip 45        # start mid-section, position unknown
.venv/Scripts/python scripts/demo.py R5 --track "EB BTH->MLE" --after 160     # second leg: arm after the BTH arrival
.venv/Scripts/python scripts/demo.py R5 --track "EB LST->BTH" --save outputs/online/demo.mp4 --speed 6
```

A new ride (e.g. Sunday's) only needs `data/raw/phyphox/<stem>.xlsx` and `data/raw/audio/<stem>.m4a`;
sync and 1-s levels are computed on the fly if missing. Saving an MP4 takes ~4 min at 6x (frames are
drawn by matplotlib); the live window redraws only the moving parts.

## Agent and Atlas layer

Credentials live in a gitignored `.env` (`GEMINI_API_KEY`, `MONGODB_URI`). From the repo root, with
`PYTHONPATH=.`:

```
.venv/Scripts/python -m atlas.setup              # collections + search indexes (idempotent)
.venv/Scripts/python -m atlas.corpus             # download, chunk and load the public corpus
.venv/Scripts/python -m atlas.search             # retrieval eval; or: -m atlas.search "a question"
.venv/Scripts/python -m posthoc.eval_v2          # tracker v2 vs v1, 23 Oct leave-one-run-out
.venv/Scripts/python -m agent.analyst            # recorded Argon analyst session (23 Oct only)
.venv/Scripts/python -m agent.harness --verify   # check the attempt log's hash chain
.venv/Scripts/python -m agent.section_stats      # statistics the investigator may cite
.venv/Scripts/python -m agent.investigator       # Argon investigator + verifier loop
.venv/Scripts/python -m agent.pack               # render the evidence pack from verified claims
.venv/Scripts/python -m agent.approve            # human approval page on 127.0.0.1:8765
.venv/Scripts/python -m agent.ask "Was Liverpool Street to Bethnal Green ground for noise?"   # live; --cached replays
.venv/Scripts/python -m agent.live_analyst --jsonl --max-experiments 2   # live analyst, own hash chain
.venv/Scripts/python -m agent.roughness --cached # re-check the saved roughness-chart readings
.venv/Scripts/python -m agent.recheck            # Argon re-derives our headline numbers (code execution)
.venv/Scripts/python -m agent.ride_report R14    # ride report (pending human approval)
.venv/Scripts/python -m agent.compare_models summarise   # Flash vs Argon tables
.venv/Scripts/python -m atlas.stream_setup       # Stream Processing plan; create / start --yes / stop / teardown --yes
.venv/Scripts/python -m atlas.stream_replay R5 --track "EB LST->BTH" --speed 4   # a ride into ingest_live
```

## Privacy

Summary below; the full data inventory, boundaries and retention are in [`docs/PRIVACY.md`](docs/PRIVACY.md).

- Raw recordings stay on this machine. `data/raw/`, `data/interim/` and every audio extension
  (`*.m4a`, `*.wav`, ...) are gitignored. Raw audio is deleted on 1 Nov 2026.
- Audio is decoded in memory and never written to disk, in any format or cache. The only audio
  products kept are 1-second A-weighted levels, third-octave bands and coarse indicators (a crude
  speech flag, limiter flags). These are coarser than the 125 ms third-octave frames that Gontier et
  al. (2017) found make speech almost unintelligible.
- Gemini receives only public document text and summary statistics; Atlas holds only derived data
  and public documents. Rides streamed to Atlas carry a random trip id, never the ride name, and live
  phone sensors send only per-second summaries. phyphox live capture uses no microphone; the phone app
  (added after submission) sends a per-second sound level and octave bands computed on the phone, never
  audio.
- The ICO's video-surveillance guidance calls recording conversations between members of the public
  "highly intrusive", so the design keeps no audio at all.
- Station labels currently come from the stop sequence.
