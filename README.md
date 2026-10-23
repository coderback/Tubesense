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
| loudness | `tubesense/loudness.py` | `outputs/gonogo/loudness.json`, `loudness_sections.csv/png`, `loudness_<stem>.png` |

Time conventions: sensor time is phyphox experiment time, which stops while phyphox is paused.
`Run.to_wall()` maps it to wall-clock time, and `sync.sensor_to_audio()` maps it to seconds in the
audio file.

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
