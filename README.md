# Tubesense

Tubesense turns ordinary London Underground rides, recorded on a phone, into a privacy-safe,
section-by-section noise map. It needs no GPS: phone sensors find the station stops and the curves,
which places the train between stations. The goal is a rail-corrugation map that an agent can check
against TfL's own noise and grinding records, so that it can draft a cited evidence pack for a
London Assembly member. A human approves the pack before it is sent. This repository currently holds
the first stage, a go/no-go analysis of one morning of rides (23 Oct 2026). It tests whether the
sensor signals repeat between rides and whether phone audio can rank sections by loudness. The
results are in [`outputs/FINDINGS.md`](outputs/FINDINGS.md).

All levels are relative (dB(A) re digital full scale, from an uncalibrated phone). Tubesense is not
a sound level meter, a safety system or a hearing-loss claim.

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
