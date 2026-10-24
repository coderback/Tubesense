# Tubesense

Tubesense turns ordinary London Underground rides, recorded on a phone, into a privacy-safe,
section-by-section noise map. It needs no GPS: phone sensors find the station stops and the curves,
which places the train between stations. A Gemini 4 Argon agent then checks TfL's own noise and
grinding records and drafts a cited evidence pack for a London Assembly member. A deterministic
verifier checks every quote and number, and a human approves the pack as a draft; nothing is sent
automatically.

The repository has three layers:
- **Sensing** (`tubesense/`, `scripts/`, frozen at git tag `prereg-24oct`): which phone signals
  repeat between rides, section loudness from phone audio, GPS-free positioning, a live tracker.
- **Post-hoc improvements** (`posthoc/`): official section lengths and a stop gate for the tracker,
  evaluated on the 23 Oct rides only.
- **Agent and data** (`agent/`, `atlas/`): the Argon analyst and investigator, the scoring harness,
  the verifier, the approval page, and the MongoDB Atlas store and search.

The sensing results are in [`outputs/FINDINGS.md`](outputs/FINDINGS.md); the architecture and build
plan are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); privacy is in [`docs/PRIVACY.md`](docs/PRIVACY.md).

All levels are relative (dB(A) re digital full scale, from an uncalibrated phone). Tubesense is not
a sound level meter, a safety system or a hearing-loss claim.

## Submission (Encode London Hackathon 2026)

**Problem.** Parts of the Tube are loud enough to be a workplace health concern. TfL's own in-cab
measurements put Liverpool Street→Bethnal Green at 87 dB LAeq and Bethnal Green→Mile End at 83 dB
on 24 June 2024 (FOI-1231-2425); a January 2026 press report puts Liverpool Street–Bethnal Green at
92.8 dB, second loudest on the network (secondary source, primary not located). The main cause is
rail corrugation, ripples worn into the rail that return after grinding: TfL says grinding must be
repeated "usually 6 months – a year" (TfL Tube Noise presentation, 6 Dec 2022). Both sections were
ground for noise in Dec 2021–May 2022 (Mayor's Question 2022/1791), but TfL does not publish
section-level noise or grinding data.

**Named user.** Elly Baker AM, Labour transport spokesperson on the London Assembly. At Mayor's
Question Time on 16 July 2026 (question 2026/2156) she said TfL had turned down most of her requests
for noise data and plans grinding only eight weeks ahead. Tubesense gives her an independent,
checkable ranking of sections from ordinary rides, a cited evidence pack that says plainly what the
public record does and does not show, and the specific information requests to make next. The
people it ultimately protects are those exposed longest: train drivers, whose cab noise TfL itself
measures, and their unions (RMT, ASLEF).

**Tracks entered:** Google DeepMind (Build with Gemini 4 Argon), MongoDB Atlas, The Real World, and
Agents That Ship.

**Demo video:** TODO: add the link to the 3-minute video.

**Built during the hackathon.** Every commit in this repo dates from 23–24 Oct 2026, after kickoff.
The data is our own rides from 23 Oct, plus the 24 Oct rides analysed under the pre-registered plan.

### What works today, and the evidence

| result | verdict | evidence (in `outputs/`) |
|---|---|---|
| Phone gyro curve signatures repeat ride to ride | **GO**: same-section r 0.76–0.99 vs different-section 0.20 | `FINDINGS.md` (a), `gonogo/gyro_*.png` |
| Positioning between stations without GPS (offline) | DTW on yaw places held-out curve edges to a median 6 m, against 24 m for the time-fraction baseline | `FINDINGS.md` (e), `positioning/improvement_log.md` |
| Live tracker (causal, what the demo runs) | Median 2.2 m on held-out rides against an independent truth, vs 11.2 m for a timetable. Between curves it is no better than the timetable (11 vs 16 m). Its 90 % band covers the truth 92 % of the time | `FINDINGS.md` (f), `online/online_eval.json` |
| Section loudness from phone audio | **WEAK**: Liverpool Street→Bethnal Green is louder than Bethnal Green→Mile End on 5 of 5 trips, by only +0.6 dB (relative levels) | `FINDINGS.md` (c), `gonogo/loudness_sections.png` |
| Magnetometer as a position fingerprint | **NO-GO** for \|B\|; the bias-free field anomaly is **WEAK** and adds nothing to positioning | `FINDINGS.md` (b), (e) |
| Tracker v2: TfL Working Timetable lengths + stop gate (post-hoc, 23 Oct only) | Identical to v1 on the real rides. After a simulated 30 s tunnel stop, median end-of-stop error 338 m → 11 m (idealised stop), 23 m (platform-level quiet), 165 m (a real platform dwell replayed); worse than v1 in 2 of 16 replayed-dwell cases | `outputs_posthoc/eval_v2.json` |
| Argon analyst tuning the tracker against a fixed harness (post-hoc, 23 Oct only) | 6 experiments; 1 accepted by the pre-declared rule: band 15.9 % narrower, mean per-run error −0.81 m (95 % CI −1.94 to −0.05), Bethnal Green→Mile End 8.8 → 5.9 m. To be scored once on the 24 Oct rides (pending) | `outputs_posthoc/analyst_session.md`, `attempts.jsonl` |
| Investigator corpus and search in Atlas | 14 public documents, 44 chunks. Hybrid search ranks the expected document first on 12/12 test questions (questions written by us, not blind) | `atlas/search.py` |

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
  ride existed, and the harness allows exactly one scoring on the 24 Oct rides.
- **Every claim in the evidence pack is checked by code.** `agent/verify.py` requires each quote to
  match the stored source verbatim and each number to trace to a quote, our statistics or a
  recomputed formula, and rejects known overclaims (e.g. 92.8 dB as "TfL's own data"). Rejected
  claims go back to the agent with the reason, and anything still failing is dropped and listed.

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
| Investigator | `gemini-4-argon` (thinking medium), structured JSON output, long context | `agent/investigator.py` | Reads the whole document corpus plus the Atlas hybrid-search hits for six sub-questions and our measured statistics, and returns typed claims (sourced / measured / derived / inference) with verbatim quotes, plus suggested information requests. Up to two rounds of fixes after the verifier rejects claims. Status: built and tested offline; its first full run is pending (the API key hit its spending cap). |

Only public document text and summary statistics are sent to Gemini; no audio and no raw sensor data.
Each Argon call takes roughly 30–60 s, so the demo replays the recorded analyst session rather than
calling the model live. Station-announcement transcription (`gemini-3.5-transcribe`) is designed
(see `docs/ARCHITECTURE.md`) but not built.

### MongoDB Atlas

Cluster: dedicated M10 on AWS eu-west-1, MongoDB 8.0, auto-scaling capped at M30. `atlas/setup.py`
creates everything idempotently.

| Scored feature | Where | What it does |
|---|---|---|
| Atlas Search | `documents.docs_text` | Full-text search over the TfL and London Assembly corpus |
| Automated Embedding + Voyage AI | `documents.docs_auto` (`autoEmbed`, `voyage-4`) | Atlas embeds each chunk and each query itself |
| Vector Search | `$vectorSearch` on `docs_auto`; `yaw_windows.yaw_vec` index (64-d, euclidean) created for sensor fingerprints | Semantic retrieval for the investigator. Sensor fingerprints are not loaded yet |
| Hybrid Search | `$rankFusion` over `docs_text` + `docs_auto` in `atlas/search.py` | The investigator's retrieval: ranks the expected document first on 12/12 test questions |
| Stream Processing | not set up | Planned: a recorded ride streamed into Atlas for the live map |

Other collections: `attempts` (the analyst's hash-chained log), `section_stats` (the statistics the
investigator may cite), `claims`, `approvals`. Only public document text is embedded (Automated
Embedding runs in a US region); rider data is never embedded.

### Where the human approves

`agent/approve.py` serves a page on `http://127.0.0.1:8765/` (local only). It shows the evidence
pack, every claim with its type, sources and a fresh verification result, and a checklist:

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

- **Our own rides:** 9 recordings on 23 Oct 2026 (Victoria and Central lines), made on an iPhone 12 with
  phyphox 1.2.1 (accelerometer, gyroscope, magnetometer, barometer) and Voice Memos audio. Raw files
  are not published.
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

## Live demo

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
  and public documents.
- The ICO's video-surveillance guidance calls recording conversations between members of the public
  "highly intrusive", so the design keeps no audio at all.
- Station labels currently come from the stop sequence.
