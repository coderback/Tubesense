# Tubesense: architecture and build plan

Sat 24 Oct 2026. Deadline Sun 25 Oct 12:00 London; **target submission 10:00** to leave a buffer.

This came from a research sweep (8 web-research dimensions, 4 exploratory data probes on the 23 Oct rides), an adversarial check of every claim the pitch or the build depends on, and a judge-style critique. All analyses on the 23 Oct data in this document are **post-hoc and exploratory** unless marked otherwise.

**Frozen v1:** git tag `prereg-24oct` covers the code in `tubesense/` and `scripts/`. `scripts/run_gonogo.py` regenerates `outputs/`; outputs are not hand-edited. All new code goes in `posthoc/`, `agent/`, `atlas/` or `demo/` and writes to `outputs_posthoc/`. Pre-registered v1 numbers are always reported first.

## 1. Product statement and what changed

Tubesense turns ordinary Tube rides recorded on a phone into a section-by-section, **relative** noise map, without GPS. The gyro locates the train between stations against curve maps built from our own rides. An Argon investigator then reads TfL's and the London Assembly's own records and drafts a cited evidence pack for Elly Baker AM. A deterministic verifier checks every quote and number, and a human approves the pack. Approval produces a draft; nothing is sent automatically. It is not a sound level meter, a safety system or a corrugation gauge.

**Decisions that matter most after the research:**

| # | Decision | Evidence |
|---|---|---|
| 1 | **Change the lead statistic.** Lead with TfL's own in-cab measurement: 87 dB(A) LAeq on LST→BTH (24 Jun 2024). Quote 92.8 dB only as "reported". | [FOI-1231-2425](https://tfl.gov.uk/corporate/transparency/freedom-of-information/foi-request-detail?referenceId=FOI-1231-2425) (verified) gives cab LAeq for both directions: EB LST→BTH 87, BTH→MLE 83; WB BTH→LST 86, MLE→BTH 82. TfL says readings are taken every two months. The 92.8 dB figure first appears in [Brixton Buzz, 27 Jan 2026](https://www.brixtonbuzz.com/2026/01/the-victoria-tube-line-is-officially-the-loudest-in-london/), credited to JRPass's review of TfL FOI data. That source gives no FOI reference, metric, position or date, and the primary was not located. |
| 2 | **Use TfL Working Timetable section lengths** in post-hoc v2: LST–BTH 2,270 m, BTH–MLE 1,640 m, LST–ALE 920 m, ALE–WCL 820 m. | Central TTN 37/25 ([FOI-3481-2425](https://foi.tfl.gov.uk/FOI-3481-2425/3481-2425-Working%20Timetable%20Notice%20Central%20Line%20TTN%2037-25.pdf)) and Circle/H&C WTT 31 ([FOI-1642-2021](https://foi.tfl.gov.uk/FOI-1642-2021/WTT%2031%20Circle%20and%20Hammersmith%20and%20City%2018%20May%202014.pdf)). OSM platform-centre lengths agree with them to a median 1.3%. v1 uses straight-line chords, which under-scale every metre figure by 2.9% (LST–BTH) and 7.1% (BTH–MLE). On the H&C the chords are 14% and 21% off. |
| 3 | **No particle filter.** The existing grid HMM (9 speeds × 400 bins) already is the exact Bayes filter. v2 adds a **stop gate**. Vector Search is only a clip-mode prior and section identifier. | A synthetic 30 s tunnel stop leaves the frozen tracker 342–450 m ahead on LST→BTH, and it never recovers in 2 of 6 cases: `SPEEDS` has no zero level (verified by re-running). kNN does not improve tracking in general (§4). |
| 4 | **Demote corrugation to an open hypothesis.** Drop "dominant roar frequency ÷ speed". What we have is a repeatable along-track roar profile. A blind location test against TfL's 2018 roughness plot is possible only on the clap-synced 24 Oct runs. | The spectrum is sharpest at β=0 (fixed frequency) in 9/9 section-runs, so dividing by speed turns cabin and vehicle resonances into fake wavelengths. There is a weak speed-scaled residual in the one well-synced run (R1.5). |
| 5 | **Fit each Gemini role to what the current docs support, and measure Argon on our own data.** (a) Transcription uses the *file* model `gemini-3.5-transcribe` through `interactions.create` with word timestamps, not the Live API. (b) Argon proposes and explains; a fixed local harness does the scoring through function calls. (c) Argon reads figures and image-only tables that text extraction cannot. (d) Every investigator claim passes a deterministic verifier. | [Transcribe docs](https://ai.google.dev/gemini-api/docs/transcribe) (2026-10-23): only the file model gives word timestamps, and custom vocabulary is rejected when word timestamps are on. The live model gives utterance-level times only, needs 16 kHz PCM and has 10-minute sessions. Code execution: 30 s limit, no installs, so it cannot run `dtaidistance` or `phonometry`. Argon model page (2026-10-14): function calling, code execution, thinking low/medium/high, PDF/image input, a 1,048,576-token context shared by input and output, no Live API. |
| 6 | **One regular `documents` collection carries 5 of the 6 scored Atlas features.** Stream Processing is the 6th, run on a **replay**. | Time series collections support none of the scored features (verified). TfL's Aug 2026 coverage map shows no 4G/5G on the Central line east of Bank or on the H&C at Aldgate East, so a live ride cannot stream from the tunnel. |
| 7 | **Check the Voice Memos microphone mode before trusting any noise number.** | Apple's current iPhone guide documents Voice Isolation, set from Control Center, as filtering out background sound. If any ride used it, that ride's levels and band shapes are invalid. |

## 2. Architecture

Boxes marked `(if time: Cn)` are below the hard cut line in §7 and may not be built. The README must describe only what was built.

```
 PHONE                              LAPTOP  (trusted zone)                                          CLOUD
 phyphox .xlsx (IMU 99 Hz, baro) ─┐
 Voice Memos .m4a (raw audio) ────┼─► INGEST + QC  [v1 frozen: ingest, inventory, qc, sync, stops]
 NIOSH SLM levels (2nd phone) ────┤     placement / pause / split-run / mag-mode flags
 notes.csv (car, placement, stops)┘     sync: claps ±<1 s (24 Oct, to be measured); 23 Oct ±5 s heuristic (R1/R3 off by ~7 s)
                                         audio decoded IN MEMORY only
                                         │
                      SIGNAL PROCESSING [v1 frozen]
                      causal 1 Hz Butterworth yaw (live) / zero-phase (offline) · vib 3–40 Hz RMS
                      stop + departure detection · 1 s LAeq (IEC 61672-1 A + F weighting, phonometry)
                      FFT third-octaves (within 0.7 dB of IEC 61260 from 25 Hz to 8 kHz; <100 Hz unreliable)
             ┌───────────────────────────┼───────────────────────────────┐
             ▼                           ▼                               ▼
   POSITIONING                    NOISE MAP                        ROAR PROFILE [experiment]
   v1: gyro curve map (DTW)       v1: section LAeq, hard bins      200–1250 Hz level vs track
       grid Bayes filter 9×400    v2 (if time: C2): soft bins,     EB vs WB direction control
   v2: WTT lengths                    σx = v·σsync, ≥100 m bins     (if time: C3) blind 720–880 m
       stop gate (P(moving))          on 23 Oct runs, ≥3-run rule   test, 24 Oct runs only
   (if time: C1) smoother             speed-normalised contrast     NO wavelength numbers
   (if time: C4) kNN clip prior ◄── Atlas Vector Search
   roadmap: speed-ridge obs.
             └────── derived features ───┴──► [B3] ──────────────────────► MONGODB ATLAS (M10, ≥8.0)
                     random trip_id, phone_class only                         documents (regular):
                                                                                search idx ───── Atlas Search
                                                                                autoEmbed voyage-4 ─ Automated Embedding + Voyage AI
                                                                                $rankFusion ──── Hybrid Search
                                                                              (if time: C4) yaw_windows ─ Vector Search on sensors
                                                                              (if time: C5) ingest_live → SP2 → live_view ─ Stream Processing
                                                                              section_stats, attempts (insert-only), claims, approvals
                                                                              autoEmbed inference runs in a US region: public text only
  GEMINI LAYER (laptop orchestrates; bounded retries, timeouts, idempotent upserts)
  ┌──────────────────────────────────────────────────────────────────────────────────────────────┐
  │ B. Analyst: Argon ⇄ function calls ⇄ FIXED HARNESS (agent/harness.py, read-only, hashed)     │
  │    dev = 23 Oct (leave-one-run-out) · 24 Oct sealed test: ONE scoring, counter in the log  [B2]│
  │ D. Investigator: Argon over the public corpus + $rankFusion top-k + section stats         [B2]│
  │    + reads figures/image tables (roughness plots, image-only tables) → "figure-read" claims │
  │    → claims JSON {id, text, type: sourced|measured|derived|figure-read, citations, formula} │
  │ (if time: C6) A. Transcriber: ≤8 s windows around dwells ─[B1]─► gemini-3.5-transcribe       │
  │ (if time: C7) C. Labeller: 1 s band images, speech seconds greyed ─[B4]─► Argon, vs rules    │
  └─────────────────────────────────────────────────┬────────────────────────────────────────────┘
                                                    ▼
             DETERMINISTIC VERIFIER (agent/verify.py): normalised exact-substring quotes; 'derived'
             claims recomputed from formula + inputs; table figures checked against parsed tables;
             'figure-read' claims flagged for human check; banned-phrase list; ≤2 re-ask rounds
                                                    ▼
             HUMAN APPROVAL (agent/approve.py): per-claim sources, evidence grade, uncertainty,
             privacy checklist → Approve = DRAFT + approvals record. Signature = SHA-256(pack +
             approver + timestamp + verifier version), stored in Atlas and in the JSONL chain.
```

**Privacy boundaries (what leaves the laptop):**

| Boundary | What leaves | What never leaves | Retention |
|---|---|---|---|
| **GitHub (public)** | Frozen v1 outputs: derived 1 s levels and bands, QC, and `outputs/inventory.md`. That file **already lists millisecond ride start and pause times next to station pairs**. | Raw audio and `.xlsx` (`data/raw/` is gitignored) | Permanent. This is one consenting contributor's own trips; say so. Tobi decides whether the regenerated 24 Oct inventory is committed. |
| B1 → Gemini transcribe (if built) | ≤8 s audio windows around stops, base64 in memory | Whole rides, files on disk | `store=false`. If the Files API is used, `files.delete` straight after (otherwise 48 h). Paid terms: not used to improve Google products, logged for a limited period for abuse monitoring ([terms](https://ai.google.dev/gemini-api/terms)). |
| B2 → Argon | Metrics JSON, public document text and figures, section stats | Audio, raw IMU, phyphox metadata | Stored by default (55 days on the paid tier). Delete by ID after the session. `background=True` requires storage. |
| B3 → Atlas | Derived features, random trip IDs, service-date band | Device model, OS, wall-clock times, audio | Under our control. autoEmbed sends text to a US region, so only public TfL/GLA text is embedded, never claims or rider-derived text. |
| B4 → Argon labeller (if built) | 1 s third-octave images with speech-flagged seconds greyed | Anything finer than 1 s | As B2 |
| Grounding (Search/Maps) | Not used with any rider data | Everything rider-derived | 30 days, cannot be disabled ([ZDR](https://ai.google.dev/gemini-api/docs/zdr)) |

The "no wall clock" promise applies to Atlas and to future contributors. It does not apply to the frozen v1 outputs already on GitHub.

## 3. Components

Status: **v1** = exists, frozen; **v2** = new post-hoc analysis; **agent** = new Gemini and verification code; **atlas** = new storage and search code; **road** = roadmap. Hours are re-priced against the research estimates.

| Component | Purpose | In → out | Status | Location | h |
|---|---|---|---|---|---|
| ingest / inventory / qc / sync / stops / noise / position / online | Everything that exists | xlsx, m4a → `outputs/` | v1 | `tubesense/`, `scripts/` | 0 |
| H5 NIOSH analysis | Addendum H5 exactly as written (committed a6dddfa, *after* the tag) | `niosh.csv` + v1 levels → `h5.json` | v2 (addendum-specified) | `posthoc/h5_niosh.py` | 0.75 |
| Section lengths | WTT lengths with citation; rescale metres | TTN → `lengths.json` | v2 | `posthoc/lengths.py` | 0.25 |
| Tracker v2: stop gate | P(moving) from the `stops` moving score; ≥3 s stationary freezes prediction | v1 `Tracker` subclass | v2 | `posthoc/tracker_v2.py` | 1.5 |
| Calibration report | 90% coverage and band width per section (PIT/CRPS if time) | LOO tracks → `calibration.json` | v2 | `posthoc/calibration.py` | 0.5 |
| Harness | Fixed scorer: leave-one-run-out on dev; `score_test` with a global budget of 1; timeouts; read-only | config → metrics JSON | agent | `agent/harness.py` | 2.0 |
| Analyst session | Argon chooses from a small fixed grid and the allow-list, calls the harness, explains; recorded | harness → `attempts.jsonl` | agent | `agent/analyst.py` | 1.0 |
| Corpus builder | Chunk primary documents (page numbers, subtotal-checked tables) | PDFs → `documents` | atlas | `atlas/corpus.py` | 1.5 |
| Atlas setup + search | Search, autoEmbed and `$rankFusion` indexes and query; fallbacks inside Atlas | — | atlas | `atlas/setup.py`, `atlas/search.py` | 1.5 |
| Investigator | Argon: "Was this section ground, and is it still loud?" plus figure reading → claims JSON | corpus, top-k, stats → claims | agent | `agent/investigator.py` | 1.5 |
| Verifier | Normalised quotes, derived-claim recompute, parsed-table checks, banned phrases, re-ask loop | claims → verified + rejected | agent | `agent/verify.py` | 1.25 |
| Approval gate | Local page; Approve → draft + signed record | verified claims → `drafts/`, `approvals` | agent | `agent/approve.py` | 0.75 |
| Demo v2 wrapper | v1 replay plus the stop gate; QC flags overlay; Atlas-visible steps | run → window/MP4 | agent | `demo/demo_v2.py` | 1.0 |
| Fixed-lag smoother (C1) | 5 s-delayed trail; full forward-backward pass for the offline map | posteriors → smoothed | v2 | `posthoc/smoother.py` | 0.75 |
| Noise map v2 (C2) | Soft bins, sync σ (7 s for R1/R3), bootstrap CI, ≥3-run rule, speed-normalised contrast | levels + posteriors → `section_bins` | v2 | `posthoc/noise_map.py` | 1.5 |
| Roar location test (C3) | 24 Oct clap-synced EB BTH→MLE vs FOI-1730-1819 720–880 m, WB control | levels CSV → `roar.json` | v2 experiment | `posthoc/roar.py` | 2.0 |
| `yaw_windows` + clip prior (C4) | Exact kNN → section ID and a prior with a 20% uniform floor; numpy cross-check | causal yaw → `yaw_windows` | atlas | `atlas/windows.py` | 2.5 |
| Stream Processing replay (C5) | Replay → SP2 1 s tumbling window → `$merge` into `live_view`, which the strip map reads | replay → `live_view` | atlas | `atlas/live_push.py`, `atlas/stream.js` | 2.5 |
| Transcriber (C6) | Cross-check station names on the stop labels | ≤8 s windows → word times | agent | `agent/transcribe.py` | 2.0 |
| Noise typing (C7) | Rules (tonality ΔL = 2L_i − L_{i−1} − L_{i+1}, hiss, roar) + Argon labeller + kappa vs ~100 hand-labelled seconds | levels CSV → labels, kappa | agent | `agent/noise_types.py` | 3.5 |
| Speed-ridge observation | Accelerometer order tracking as a speed cue | accel PSD → f0(t) | road | — | — |
| README + `docs/PRIVACY.md` | Corrections, what was built, B-boundaries, retention | — | docs | — | 1.5 |

## 4. Methods: keep, change or drop

These numbers are exploratory, from the 23 Oct data. Every positioning "truth" comes from gyro yaw curve edges, so errors are **agreement with a yaw-landmark proxy**, not physical accuracy.

| Method | Decision | Evidence | Change |
|---|---|---|---|
| Filtering | **Keep** | Causal 1 Hz yaw filter; maps are built with the same filter, so its lag cancels. IMU is uniform at 99.2 Hz. | None. |
| Integrating acceleration (L1) | **Rejected baseline** | L1 worse than L0 by +15.7 m (CI 0.1–36). Dead-reckoned lengths are 0.43–0.77 of the chord, a systematic speed under-estimate. The gradient (an accelerometer cannot feel the slope) is plausible, not proven. | Report as tested and rejected. A gradient-aware Kalman filter with zero-velocity updates is on the roadmap. |
| A-weighting | **Keep** | phonometry 3.3.0 implements IEC 61672-1 poles and F weighting (read in source). | Label the bands "FFT-synthesised third-octaves, within 0.7 dB of an IEC 61260 bank, 25 Hz–8 kHz". Bands below 100 Hz are unreliable (iOS input high-pass). |
| Spectrograms | **Keep** | The dominant 100–2000 Hz peak does not follow speed (r −0.19 to +0.11). | Coarse 1 s band images for labelling. A distance-domain (order-tracked) spectrum needs better speed and sync; it is roadmap. |
| DTW template matching | **Keep, no new variants** | `dtw_warp` is already dependent multivariate DTW with a Sakoe-Chiba band and open ends. Yaw DTW 6 m vs L0 24 m at held-out curve edges. | DTW builds the maps; the HMM tracks live. |
| kNN in Atlas Vector Search | **Section ID + clip prior only (C4)** | 20 s windows, top-1: median 4.5 m, 14.5% >100 m, p90 542 m. The top-5 distinct modes contain a <100 m hit in >99% of queries. Fused prior: LST→BTH lock 14.8 → 10.8 s only with 10 s windows; worse on WB BTH→LST (18.5 → 21.6 s). No leakage found. | Exact search (<10k docs), euclidean on raw deg/s, filter `run_id $ne heldOut`. Do not claim faster locking in general. |
| Particle filter | **Drop** | The grid is exact over 3,600 cells; a PF adds sampling noise and breaks deterministic re-runs. | Call it a "grid Bayes filter (HMM) over position × speed". A PF only when the state grows (junctions, gyro bias). |
| Gemini writes/tests/improves | **Keep, narrowed** | The sandbox cannot run our libraries. The existing 243-point weight grid found nothing better than yaw alone (7.3 m vs 6 m). | Argon proposes from an allow-list (GAMMA, YAW_SD_FLOOR, stop-gate thresholds, channel on/off) and calls the harness. It explains results and proposes one new structural hypothesis. It never edits the harness. |
| Argon-tuned weights via LOO | **Small fixed grid, honest outcome** | Tempering barely moves the error but changes band width a lot (LST→BTH 87 → 41 m). No single GAMMA calibrates both sections (BTH→MLE coverage 0.90 → 0.72). About 8 curve edges per section give the paired bootstrap little power. | A 4×4 GAMMA × YAW_SD_FLOOR grid run by the harness. Argon reads it and explains. Accept only gains beyond the CI. **Pre-written likely headline: "the loop rejected N changes"**, and show one accepted and one rejected attempt from the real log. |
| Gemini labelling spectrograms (C7) | **A measured contest** | There is no published accuracy for Argon on this task, and older-model studies are not used to judge it. | Rules and Argon label the same ~100 stratified seconds. Cohen's kappa against Tobi's labels, with 20 re-labelled for intra-rater agreement. Whichever agrees better leads. No audio is sent. |
| Announcement transcription (C6) | **File model** | Only the file model gives word timestamps (verified). | Verbatim with word timestamps, no custom vocabulary; fuzzy-match to the stop list. Inline base64 in memory; fallback `files.upload(BytesIO)` then `files.delete`. |
| Magnetometer | **Reported experiment only** | \|B\| NO-GO; field anomaly WEAK; no positioning benefit. | H3/H4 report only. No "open magnetic map" claim. |
| Speed ridge (accelerometer) | **Roadmap; promising** | A 25.00 Hz cruise line on 5 eastbound section-runs; ridge 16 m vs L0 43.5 m on one hand-picked pair; locked on 4/12 sections, 0/6 westbound. The 1/3 sub-order claim was not confirmed. | Pre-register the settings, test on 24 Oct, then fuse as a speed observation. |

## 5. Verification and honesty

**Order of reporting.**
1. **H1–H4**: the tagged pre-registration (`prereg-24oct`, 10b3fad), run by `scripts/run_gonogo.py` with unchanged code and reported verbatim. Afterwards, confirm with `git diff` that the 23 Oct numbers are unchanged, and tag `prereg-results-24oct`.
2. **H5**: a descriptive addendum committed before the rides (a6dddfa, *after* the tag). Its analysis code is written after the rides, exactly as specified. Cite both hashes.
3. **Post-hoc v2** (WTT metres, stop gate, anything else built), labelled *"declared after pre-registration; tuned on 23 Oct only"*, with v1 in the same row.

**One sealed test.** Dev = the 23 Oct runs. Test = the 24 Oct runs.
- The Argon analyst session runs **on dev only, before** any 24 Oct positioning output is opened.
- The config it submits becomes v2. Commit and tag it `v2-posthoc`.
- Then score it **once** on 24 Oct. `harness.py` enforces a global test budget of 1 with a counter in the append-only log and in Atlas.
- `run_gonogo.py` prints `online_eval` and `forward_test` results to the console, so run it only **after** the v2 tag.
- If 24 Oct positioning output has already been seen, the "sealed" framing goes and v2 is plain post-hoc.

**Harness the agent cannot edit.**
- `agent/harness.py` is read-only, and its SHA-256 appears in every log line.
- Argon gets function declarations only (`list_experiments`, `run_experiment`, `get_calibration_report`, `submit_final`). It has no file-write tool.
- Each run is a subprocess with a 120 s timeout. One `online_eval` takes about 45 s, so live demo iterations are replayed from the log, not run live (§7).

**Append-only log.** `outputs_posthoc/attempts.jsonl` holds `{ts, attempt, config, harness_sha, metrics, prev_hash, hash}`. Each attempt is mirrored to Atlas `attempts` through a user whose role allows insert and find only. The demo shows the full log, including rejected attempts.

**Calibration (current state, v1).** Conservative on R1/R5 (coverage 1.00) and honest on R3 (0.89) on LST↔BTH. Over-confident on BTH→MLE (0.81–0.83). The report says so whether or not v2 changes it.

**Claims ledger.**

| Topic | We can say | We cannot say |
|---|---|---|
| Headline level | TfL's in-cab LAeq: LST→BTH 87 dB, BTH→MLE 83 dB, 24 Jun 2024 (FOI-1231-2425). | "92.8 dB is TfL's own data". Only "reported at 92.8 dB, second loudest (Brixton Buzz, 27 Jan 2026, from JRPass's review of TfL FOI data; primary not located)". |
| Grinding | Both sections were on TfL's list of sections ground for noise, Dec 2021–May 2022 ([MQ 2022/1791](https://www.london.gov.uk/who-we-are/what-london-assembly-does/questions-mayor/find-an-answer/tube-noise-2-22) attachment). TfL: grinding "must be repeated at regular intervals (usually 6 months – a year)" (TfL CSOPP Tube Noise presentation, 6 Dec 2022). | Which direction was ground; that nothing was ground since (only "no later public record found"); that noise "returned". |
| Transparency | TfL turned down most of Baker's data requests and plans grinding about 8 weeks ahead (MQT 16 Jul 2026, Q 2026/2156). The Central line is not in TfL's Aug 2024 or Aug 2025 noise hotspot updates. | That TfL "ignores" it. That her residential-noise concern is shown by in-car data. |
| Reconditioning | The Weir letter (9 Dec 2022) lists "Liverpool Street – Bethnal Green Eastbound Central" for deep Tube reconditioning in FY2023/24. Present it as a question, after checking the PDF by eye. | That it was done, or that Pandrol Vanguard is present. |
| H&C control | TfL cab LAeq LST–ALE–WCL 64–67 dB(A) ([FOI-3780-2324](https://foi.tfl.gov.uk/FOI-3780-2324/), 2020, S7 stock). Context only. | A like-for-like comparison with the 2024 Central data. |
| Our loudness | LST–BTH louder than BTH–MLE within the trip on 5/5 trips, +0.6 dB relative (WEAK). Same sign as TfL's ~4 dB cab gap. | Absolute dB from Voice Memos. That +0.6 dB reflects track condition: mean speed alone could explain it until speed-normalised. Anything at all if Voice Isolation was on. |
| Absolute dB (H5) | NIOSH-paired levels: "indicative, ±2 dB(A) or worse; above the 85 dB range validated by Huyan 2023". | Certified levels. ISO 3381 compliance; cite it as the method we do not meet. |
| Exposure | At TfL's **cab** level, one ~139 s LST→BTH traverse at 87 dB(A) is ~0.8% of the HSE 85 dB(A) 8-hour dose (~2.9% at the reported 92.8). Saloon level unknown. HSE values apply to workers: the stronger angle is drivers (RMT/ASLEF). | Hearing-damage claims for passengers. |
| Positioning | Online tracker median 2.2 m vs 11.2 m for the timetable, *as agreement with a yaw curve-edge proxy*. Timetable-level between curves. Mean 90% band coverage 0.92. | "2 m accuracy". Long straights. v1 metres are chord-scaled (−2.9% / −7.1%). |
| Noise placement | 23 Oct per-second noise is placed only to ≥100 m. The ±5 s sync bound is heuristic, and R1/R3 are off by ~7 s (~125 m). | Sub-100 m noise localisation from 23 Oct runs. Clap-sync precision on 24 Oct is to be measured. |
| Corrugation | Dominant tones behave as fixed-frequency resonances (9/9 runs). A weak speed-scaled component appears in the best-synced run. The roar-band profile repeats on R3/R5 at fixed offsets (r 0.70–0.86). | Any wavelength, or "corrugation measured". The scanned r 0.73–0.96 as a headline. |
| Magnetometer | \|B\| unusable (iOS recalibration jumps). Field anomaly weakly repeatable; no positioning benefit. | A magnetic map for quantum navigation. |
| Speed ridge / kNN | Exploratory results with their n (2 pairs; 3 runs per track). | General positioning gains. |
| Privacy | Only derived features go to Atlas; only ≤8 s windows would go to transcription, with `store=false`. ICO guidance calls recording conversations between members of the public "highly intrusive" (current CCTV guidance, under review). 1 s bands are coarser than the 125 ms frames Gontier et al. 2017 found almost unintelligible. | "Zero retention". "Speech is provably unrecoverable" against modern ML (to be measured). "No timestamps published": inventory.md on GitHub has them. |

## 6. Experiment improvements

### (a) Today's rides: checklist (pre-registered quota first, extras after)

| ☐ | Item | Why |
|---|---|---|
| ☐ | **Check TfL status and planned works** (Central, H&C) before leaving. | Weekend closures would break the plan; the fallback is in §7. |
| ☐ | Voice Memos: Control Center mic mode **Standard** (not Voice Isolation), Recording Mode **Mono**, Audio Quality **Lossless**, never Enhance. Screenshot the settings. Also find out what was active on 23 Oct. | Voice Isolation suppresses background sound. |
| ☐ | Voice Memos iCloud sync off; check whether the 23 Oct files are already in iCloud. | README says raw audio stays local. |
| ☐ | Do Not Disturb on, battery >50%, Low Power Mode off. | The 23 Oct battery alert paused phyphox. |
| ☐ | Street-level figure-of-eight `CAL_*.xlsx`, at the start and the end. | Required for H4. |
| ☐ | **One continuous recording per trip** (LST→MLE, LST→WCL); don't stop at BTH or ALE. | Within-trip contrasts are 3–4× more precise (sd 0.14 dB vs 0.56). |
| ☐ | Phone still on the platform ≥20 s. **Sharp clap near the mic, standing still, at the start and end.** | Arms the departure detector; gives two-point sync. Taps were silent in the audio. |
| ☐ | Same car every time (log car number and index from the front), mid-car, not over a bogie; same rigid placement. | Position along a metro car is worth about 1–5 dB(A) in the literature, bigger than our effect. |
| ☐ | Second phone running the NIOSH SLM app, A-weighted, time-history log on, including one platform dwell. | H5 absolute offset and limiter slope. |
| ☐ | Interleave Central and H&C blocks; alternate which direction starts each block. | Removes time-of-day confounds. |
| ☐ | Log every unplanned tunnel stop (time, duration) and whether the announcement was audible. | The only real test of the stop gate. |
| ☐ | `notes.csv` columns: car_no, car_index, position, placement, mic_mode, rec_mode, niosh_device, tunnel_stops, signal_seen, extra_run_flag. | The covariates the analyses need. |
| ☐ | **Don't open 24 Oct positioning outputs until `v2-posthoc` is tagged.** QC and inventory are fine. | Keeps the sealed test sealed. |
| ☐ | *Extras after the quota, flagged as deviations:* one run from the other end of the train per Central section; one real clip-start run (start phyphox 30–60 s after departure, keep the end clap). | Car-position control; real clip mode. |

### (b) Before submission: see the build plan (§7)

### (c) Post-hackathon roadmap

| Item | What it fixes |
|---|---|
| phyphox measurement-mode recorder: custom experiment with audio and IMU on one clock, on-device 100 ms band levels, no audio export (phyphox iOS uses `AVAudioSession` measurement mode with gain 1.0, verified in its source) | Voice Memos processing, sync, raw-audio privacy. Whether audio + IMU at 99 Hz works together is to be measured. |
| External mic (Dayton iMM-6 / MicW i436) + 94 dB calibrator, drift ≤0.5 dB | Indicative absolute dB above 85 dB |
| kHz floor channel: ESP32 + ADXL355 (4 kHz) or piezo contact mic | Sees 30–300 mm wavelengths at speed; barely hears speech |
| Camera ring-joint odometer (photometric luma only, no frames kept) | Independent speed between curves |
| Android contributors (`HIGH_SAMPLING_RATE_SENSORS`; rate is device-dependent) | Higher IMU rate; achieved rate checked in QC |
| Speed ridge fused into the HMM; gradient-aware Kalman filter with zero-velocity updates; pitch-rate (vertical curve) landmarks | Positioning between curves |
| Crowd calibration: hierarchical model with sections as shared reference points; publish at ≥N runs from ≥M devices | Device offsets, compression |
| Before/after-grinding design using the 8-week forward plan the Mayor said should be shared; EIR requests drafted by the agent (grinding per direction 2022–26, current roughness plots, the source of 92.8) | "Has noise returned?" answered properly |

## 7. Build plan to Sun 25 Oct, submit by 10:00

Map the hours onto the clock yourself; they are focused hours. They exclude sleep (budget ≥5 h), travel and meals. **If the spine doesn't fit in the hours you actually have, cut in the order below. Don't squeeze sleep.**

**Spine (above the hard cut line):**

| # | Task | h | Depends on | Output |
|---|---|---|---|---|
| T0 | Ride checklist (6a). `pip install "google-genai>=2.3.0"` (not installed in `.venv`). `client.models.list()` for exact IDs. Confirm the Gemini key is on the paid tier. Atlas: MongoDB ≥8.0, auto-scaling capped at **max M30**, oplog window ≥24 h. | 0.75 | — | IDs, versions |
| T1 | **Rides** (prereg quota) | 3.0 | T0 | `data/raw` |
| T2 | Corpus + `documents` (about 12 primary docs plus the Brixton Buzz secondary, chunked with pages). `docs_text` search index, `docs_auto` autoEmbed (voyage-4), `$rankFusion`. **Fallback inside Atlas**: client-side Voyage embeddings into a plain vector index, with `$rankFusion` kept. Python RRF only as the last resort, and if used, **don't claim Hybrid**. | 3.0 | T0 | 5 scored features |
| T3 | v2 on 23 Oct only: WTT lengths, stop gate, coverage + width report | 2.25 | — | `outputs_posthoc/` |
| T4 | Harness (global test budget 1) + recorded Argon analyst session on dev (4×4 grid + allow-list) → `submit_final` → commit and **tag `v2-posthoc`** | 3.0 | T3 | `attempts.jsonl`, tag |
| T5 | `run_gonogo.py` on 24 Oct (H1–H4) → `git diff` check → tag. H5 script. Then the single sealed v2 score. | 1.5 | T1, T4 | prereg + v2 tables |
| T6 | Investigator (`$rankFusion` top-k + Argon figure reading) + verifier + approval gate. Use v1 section stats (noise map v2 is C2). | 3.75 | T2, T5 | verified draft pack |
| T7 | Demo wrapper with the QC overlay; README (built components only, Gemini/Atlas/approval, data-source corrections); `docs/PRIVACY.md` with the deletion date | 2.25 | T5, T6 | repo |
| T8 | Timed 3:00 rehearsal ×2; backup screen recording; **3-minute submission video showing the Atlas indexes, the `$rankFusion` query and the approvals record**; form, models note, data sources | 1.5 | T7 | submission |
| | **Spine total** | **20.75** (17.75 excluding rides) | | |

That is more than a solo builder comfortably has after sleep. The first cuts, if needed, are ranked below.

**Hard cut line.** Below it, add only if you're ahead, in this order:

| # | Item | h |
|---|---|---|
| C1 | Fixed-lag smoother (5 s-delayed trail, labelled; BTH→MLE coverage 0.82, not calibrated) | 0.75 |
| C2 | Noise map v2 (soft bins, sync σ, speed-normalised contrast) | 1.5 |
| C3 | Blind roar location test (write the addendum first: ±100 m datum tolerance, statistic, WB control, 23 Oct result as the prior; a null is uninformative at this resolution) | 2.0 |
| C4 | `yaw_windows` exact kNN + clip prior (the 6th feature's sensor use) | 2.5 |
| C5 | Stream Processing replay → `live_view` read by the strip map (SP2, London, stop after the demo) | 2.5 |
| C6 | Transcriber | 2.0 |
| C7 | Noise typing contest (rules vs Argon, kappa) | 3.5 |

**If behind, cut from the spine in this order:**
1. Argon figure reading in T6. Keep text-only claims.
2. Shrink the T4 analyst session to the 4×4 grid plus Argon's written explanation.
3. The H5 script. Report the NIOSH table as raw numbers.
4. **Never cut:** the H1–H4 prereg report, the verifier, the approval gate, the corrected lead statistic, the video.

**Fallbacks.**
- **Rides fail** (closures, battery): demo on the 23 Oct R5 LST→BTH replay and drop the sealed-test framing.
- **Venue network:**
  - phone hotspot;
  - cached Atlas query results and a precomputed investigator output;
  - the verifier runs offline.
- **Morning pre-flight:** check the paid key and quota.
- **API failures:** bounded retries with timeouts; idempotent upserts keyed by `trip_id` / `claim_id`; an offline mode that replays the logs.

**Demo (3:00, no slides). Live vs precomputed is stated for every beat.**

| Time | Beat | Live / precomputed |
|---|---|---|
| 0:00–0:20 | 3 s of roar (real clip if retained, otherwise resynthesised from bands and labelled). "TfL's own in-cab measurement on Liverpool Street to Bethnal Green: 87 dB(A), June 2024. It was on TfL's list of sections ground for noise in 2021–22, and TfL says grinding usually has to be repeated every 6 months to a year. Press reports of TfL data put it at 92.8, second loudest." | Precomputed |
| 0:20–0:35 | Named user: Elly Baker AM. TfL turned down most of her data requests (MQT 16 Jul 2026). "This gives her an independent section ranking and a drafted data request: grinding per direction since 2022, current roughness plots, the source of 92.8." Secondary users: drivers (TfL's figures are cab LAeq), RMT/ASLEF. | Precomputed |
| 0:35–1:15 | Replay of the newest ride: the dot moves with its 90% band; noise is painted only when confident; QC overlay (R4 floor placement auto-excluded, phyphox pauses detected). If a real tunnel stop was logged, show the stop gate holding; otherwise label the stop as **synthetic**. "No GPS, no signal down there." | Live replay, local |
| 1:15–1:50 | **Verification loop.** Replay of the recorded Argon session from `attempts.jsonl` (timestamps, hashes): proposals, harness scores, at least one rejected change, the single sealed 24 Oct score next to the pre-registered v1 number. Ablation: "gyro helps; the magnetometer doesn't". | Replayed log; optionally 1 live Argon call with a timeout and cached fallback |
| 1:50–2:30 | Investigator. The `$rankFusion` query runs in Atlas. Precomputed Argon claims go through the **verifier live**, plus one **canary claim labelled on screen as seeded** (e.g. "92.8 dB is TfL's own data") to show a rejection. Corrugation shown as an open hypothesis. | Atlas query and verifier live; claims precomputed |
| 2:30–3:00 | **Human approval.** Claims with sources, evidence grade, uncertainty and the privacy checklist → Approve → signed draft plus an approvals record in Atlas. "Nothing is sent automatically." | Live, local + Atlas |

## 8. Open questions for Tobi (each changes a decision)

1. **Voice Memos mic mode on 23 Oct** (Standard / Voice Isolation / Wide Spectrum) and Recording Mode? If it was Voice Isolation, drop the 23 Oct noise results.
2. **Have you seen any 24 Oct positioning output yet?** If yes, the sealed-test framing goes.
3. **Atlas:** MongoDB version (≥8.0)? Auto-scaling capped at M30? Stream Processing enabled? This decides 5 vs 6 features.
4. **Gemini key** on the paid tier? Does `models.list()` show `gemini-4-argon` and `gemini-3.5-transcribe`?
5. **Demo start:** from the platform (depart mode, reliable) or mid-section (clip mode)? If from the platform, C4 loses its demo role.
6. **May the demo run the post-hoc v2 tracker** (stop gate), with v1 numbers shown first?
7. **Raw audio:** deletion date (e.g. 1 Nov 2026), and may a 3 s real clip be kept for the opener?
8. **A second phone for NIOSH today?** Without one, H5 loses the offset analysis.

## Sources (verified in this pass)

- TfL [FOI-1231-2425](https://tfl.gov.uk/corporate/transparency/freedom-of-information/foi-request-detail?referenceId=FOI-1231-2425), "Excessive Noise – Central Line" (8 Aug 2024)
- [MQ 2022/1791 "Tube Noise (2)"](https://www.london.gov.uk/who-we-are/what-london-assembly-does/questions-mayor/find-an-answer/tube-noise-2-22) (grinding list Dec 2021–May 2022); [MQ 2022/2799](https://www.london.gov.uk/who-we-are/what-london-assembly-does/questions-mayor/find-an-answer/outstanding-rail-grinding-works-tube-noise)
- MQT 16 Jul 2026, Q 2026/2156 "Tube Noise", [Minutes Appendix 2](https://www.london.gov.uk/about-us/londonassembly/meetings/documents/s123225/Minutes%20-%20Appendix%202%20-%20Questions%20to%20the%20Mayor.pdf)
- TfL FOI-1730-1819 "Rail Roughness Plots BEG-MIE January 2018" (published 22 Oct 2018); TfL reads elevated roughness at 720–880 m EB BTH→MLE
- TfL noise updates to the Assembly, [9 Aug 2024](https://www.london.gov.uk/about-us/londonassembly/meetings/documents/s112802/Appendix%207a%20-%20Tube%20Noise%20Update%20from%20the%20Chief%20Operating%20Officer%20TfL%20received%209%20August%202024.pdf) and 20 Aug 2025; Weir letter to the Environment Committee, 9 Dec 2022; TfL CSOPP "Tube Noise" presentation, 6 Dec 2022
- Central TTN 37/25 (FOI-3481-2425); Circle & H&C WTT 31 (FOI-1642-2021); H&C cab noise report RP-R2839 (FOI-3780-2324)
- Gemini: [transcribe](https://ai.google.dev/gemini-api/docs/transcribe), [terms](https://ai.google.dev/gemini-api/terms), [ZDR](https://ai.google.dev/gemini-api/docs/zdr), gemini-4-argon model page (2026-10-14)
- MongoDB docs: time series limitations, `autoEmbed` (voyage-4 family), `$rankFusion` (8.0+), `$vectorSearch` exact ENN (score 1/(1+d) for euclidean), Stream Processing `$source` / `$merge` / `$emit`
- ICO video surveillance guidance (under review); Gontier et al. 2017, Sensors 17(12):2758; Huyan et al. 2023 (PMC10686533); Kardous & Shaw 2014, JASA 135:EL186; ISO 3381:2021; SI 2005/1643
- Löffler & Bengtsson 2024 ([arXiv 2406.02339](https://arxiv.org/abs/2406.02339)); Dieckow et al. 2025 ([arXiv 2507.19327](https://arxiv.org/abs/2507.19327))
