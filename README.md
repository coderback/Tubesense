# Tubesense

Tubesense turns ordinary London Underground rides, recorded on a phone, into a privacy-safe,
section-by-section noise map. It needs no GPS: phone sensors find the station stops and the curves,
which places the train between stations. The goal is a rail-corrugation map that an agent can check
against TfL's own noise and grinding records, so that it can draft a cited evidence pack for a
London Assembly member. A human approves the pack before it is sent.

This repository holds the sensing half, built from one morning of rides on 23 Oct 2026:
- a go/no-go analysis of which phone signals repeat between rides;
- section loudness from phone audio;
- GPS-free positioning between stations;
- a live tracker for the demo.

The results are in [`outputs/FINDINGS.md`](outputs/FINDINGS.md).

All levels are relative (dB(A) re digital full scale, from an uncalibrated phone). Tubesense is not
a sound level meter, a safety system or a hearing-loss claim.

## Submission (Encode London Hackathon 2026)

**Problem.** Parts of the Tube exceed 90 dB. TfL's own FOI figures put Liverpool Street–Bethnal Green
at 92.8 dB, the second-loudest section. The main cause is rail corrugation, ripples worn into the
rail head that come back after grinding. The London Assembly has asked TfL to publish noise data
section by section since May 2023, and it is still not public.

**Named user.** Elly Baker AM, Labour transport spokesperson and Deputy Chair of the London Assembly
Transport Committee. Tubesense gives her section-level evidence gathered by riders, without waiting
for TfL to publish.

**Tracks entered:** Google DeepMind (Build with Gemini 4 Argon), MongoDB Atlas, The Real World, and
Agents That Ship.

**Demo video:** TODO: add the link to the 3-minute video.

**Built during the hackathon.** Every commit in this repo dates from Fri 23 Oct 2026, after kickoff.
The data is our own rides from the morning of 23 Oct.

### What works today, and the evidence

| result | verdict | evidence (in `outputs/`) |
|---|---|---|
| Phone gyro curve signatures repeat ride to ride | **GO**: same-section r 0.76–0.99 vs different-section 0.20 | `FINDINGS.md` (a), `gonogo/gyro_*.png` |
| Positioning between stations without GPS (offline) | DTW on yaw places held-out curve edges to a median 6 m, against 24 m for the time-fraction baseline | `FINDINGS.md` (e), `positioning/improvement_log.md` |
| Live tracker (causal, what the demo runs) | Median 2.2 m on held-out rides against an independent truth, vs 11.2 m for a timetable. Between curves it is no better than the timetable (11 vs 16 m). Its 90 % band covers the truth 92 % of the time | `FINDINGS.md` (f), `online/online_eval.json` |
| Section loudness from phone audio | **WEAK**: Liverpool Street→Bethnal Green is louder than Bethnal Green→Mile End on 5 of 5 trips, by only +0.6 dB (relative levels) | `FINDINGS.md` (c), `gonogo/loudness_sections.png` |
| Magnetometer as a position fingerprint | **NO-GO** for \|B\|; the bias-free field anomaly is **WEAK** and adds nothing to positioning | `FINDINGS.md` (b), (e) |

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

> **TODO before submission:** no Gemini model is called by the code in this repo yet. List here only
> what is actually wired in, with the files that call each model. The plan is:
> - `gemini-3.5-transcribe` to read station announcements;
> - `gemini-4-argon` as the investigator that reasons over TfL's grinding and noise records and
>   drafts the cited evidence pack.

### MongoDB Atlas

> **TODO before submission:** Atlas is not integrated yet. List the collections and features actually
> used, for example time series, Vector Search and Automated Embedding.

### Where the human approves

> **TODO before submission:** the evidence-pack approval step is not built yet. Describe where the
> human approves the pack before anything is sent, and what they see when they do.

### Data sources

- **Our own rides:** 9 recordings on 23 Oct 2026 (Victoria and Central lines), made on an iPhone 12 with
  phyphox 1.2.1 (accelerometer, gyroscope, magnetometer, barometer) and Voice Memos audio. Raw files
  are not published.
- **TfL Unified API:** the public `Line/central/Route/Sequence`, fetched for track geometry. Its
  lineStrings join stations with straight segments, so straight-line lengths are used and flagged.
  Station coordinates are in `tubesense/stops.py`.
- **TfL FOI noise figures** (reported March 2026), for context and ranking checks only. No TfL figure
  is used in a calculation.
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
| mag_anomaly | `tubesense/features.py` (`mag_destep`, `field_anomaly`) | `outputs/gonogo/mag_anomaly.json`, `mag_anomaly.png`: bias-free magnetometer follow-up |
| ablation | `tubesense/position.py` | `outputs/positioning/ablation.json`, `ablation.png`, `gap_length.png`, `improvement_log.md`: held-out positioning error per sensor set (L0, L1, DTW) |
| online_eval | `tubesense/online.py` | `outputs/online/online_eval.json`, `online_eval.png`, `clip_lock.png`, `track_<run>.png`: leave-one-run-out test of the live tracker; `maps.json`: the demo maps |
| loudness | `tubesense/loudness.py` | `outputs/gonogo/loudness.json`, `loudness_sections.csv/png`, `loudness_<stem>.png` |

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

## Privacy

- Raw recordings stay on this machine. `data/raw/`, `data/interim/` and every audio extension
  (`*.m4a`, `*.wav`, ...) are gitignored.
- Audio is decoded in memory and never written to disk, in any format or cache. The only audio
  products kept are 1-second A-weighted levels, third-octave bands and coarse indicators (a crude
  speech flag, limiter flags). Speech cannot be reconstructed from them.
- The intended production policy is to compute the levels and bands, then delete the raw audio, and to
  publish a section only when enough independent runs cover it. Recording conversations is
  "highly intrusive" (ICO), so the design keeps no audio at all.
- Station labels currently come from the stop sequence. Announcement transcription (next step) will
  keep only the announcement text.
