# Tubesense go/no-go: rides of Fri 23 Oct 2026

Regenerate with `.venv/Scripts/python scripts/run_gonogo.py --clean` (~9.5 min). Numbers come from
`outputs/*.json`; plot paths are relative to `outputs/`. Every result below was built by one agent,
attacked by an independent skeptic agent, and the confirmed issues fixed before this run.

## Verdicts

| check | verdict | one line |
|---|---|---|
| (a) gyro yaw rate | **GO** | Curve signatures repeat run to run, far above chance. LST–BTH is the positioning showcase; BTH–MLE carries little positional detail. |
| (b) magnetometer \|B\| | **NO-GO** | No position-locked map. iOS recalibration jumps hit 5 of the 7 calibrated runs. |
| (c) loudness LST→BTH vs BTH→MLE | **WEAK** | LST–BTH is louder on 5/5 trips, but only by +0.6 dB, inside the Voice Memos processing uncertainty. |

## Inventory (`inventory.md`)

One iPhone 12 (phyphox 1.2.1 plus Voice Memos). Every run has acc, gyr, mag and baro at 99.2 Hz;
the magnetometer runs at 98.8 Hz on R0.5 and R1. Barometer 0.93 Hz; audio ALAC 48 kHz mono.
No timestamp gaps in any file, because phyphox experiment time hides pauses.

| run | route | sensors / audio (s) | notes |
|---|---|---|---|
| R0 | Victoria NB VIC→GPK→OXC | 215 / 304 | magnetometer known calibrated |
| R0.5 | Central EB OXC→LST, 6 sections | 583 / 671 | raw magnetometer; best-synced run |
| R1 | EB LST→BTH | 170 / 206 | raw magnetometer; first half of split trip |
| R1.5 | EB BTH→MLE | 143 / 255 | second half of split trip; slow; rattling placement |
| R2 | WB MLE→LST | 311 / 402 | MLE departure not recorded |
| R3 | EB LST→MLE | 294 / 415 | hard-contact placement |
| R4 | WB MLE→LST | 267 sensor / 303 wall / 358 audio | false start, 23 s pause, floor-like placement |
| R5 | EB LST→MLE | 299 / 351 | clean reference run |
| R6 | WB MLE→LST | 300 / 376 | clean placement; 5 magnetometer jumps |

**Sections** (`sections.json`, `segment/*.png`): all 20 found, no tunnel stops. Times agree across
runs: LST→BTH 137–141 s, BTH→LST 148–154 s, MLE→BTH 97–106 s, BTH→MLE 105 s. The exception is
R1.5 at 116.5 s, flagged as slow running. Boundaries are good to about ±5 s.

## (a) Gyro yaw rate: GO

Yaw rate is the gyro projected onto the accelerometer's up direction, so it doesn't depend on how
the phone sits. Each clean pair of runs is scored against 200 spectrum-matched random signals
(surrogates).

| section | clean pairs, best r within ±10% shift (L1 distance grid) | surrogate p | surrogate p99 |
|---|---|---|---|
| EB LST→BTH | 0.76, 0.91, 0.82 (L0 time grid: 0.95) | ≤0.015 | 0.73 |
| EB BTH→MLE | 0.90, 0.91, 0.99 (L0 0.92) | ≤0.005 | 0.84, low information |
| WB BTH→LST | 0.87 (L0 0.89) | 0.005 | 0.71 |
| WB MLE→BTH | only R6 clean, not scored | – | – |

- **Null:** pairs from different sections score a mean r of 0.20 (max 0.67, 123 pairs).
- **Total heading change** agrees across runs: LST→BTH −56.7 ± 1.4°, BTH→MLE +28.0 ± 0.4°,
  BTH→LST +48.8 ± 1.3°. R4's battery pause shows up as about 18° of a curve missing.
- **Opposite direction:** LST–BTH matches only after a −5% shift (plain r 0.36, best 0.81,
  p ≤0.01). BTH–MLE is not distinguishable from surrogates. The EB and WB tunnels are separate and
  their geometry differs near LST.
- **Which section to demo:** LST–BTH has five distinct curves, so it is the positioning showcase.
  BTH–MLE is essentially one long curve.
- **L1 dead reckoning does not beat L0** (time fraction). Its dead-reckoned lengths come out at
  0.46–1.7 km, against about 1.5 and 2.2 km real. The DTW positioning step (L2) is the next thing
  to try.
- Plots: `gonogo/gyro_EB_LST-BTH.png` (best single picture), `gyro_EB_BTH-MLE.png`,
  `gyro_WB_BTH-LST.png`, `gyro_WB_MLE-BTH.png`, `gyro_summary.png`, `gyro_matrix.png`.

## (b) Magnetometer: NO-GO (raw and calibrated kept separate)

- **Detected from the data:**
  - Raw: R0.5 and R1. They have their own 98.8 Hz clock and a fixed hard-iron bias of about
    [−111, 37, −133] µT.
  - Calibrated: R0, R1.5 and R2–R6, which share the gyro's sample stream.
  - So about 2 runs were *raw*, not 2 calibrated.
- **iOS re-estimated the calibration mid-ride:** one-sample jumps of 80–369 µT while the phone was
  still. Counts: R1.5 ×2, R2 ×3, R3 ×1, R4 ×3, R6 ×5; none on R0, R5 or the raw runs. These are
  masked from 15 s before to 25 s after each jump.
- **Only one calibrated pair is jump-free** (R3 vs R5 on LST→BTH): r 0.24, surrogate p 0.62. That
  is below the calibrated null (mean 0.49, n = 2), and with only two pairs that null is weak too.
- **Changes in |B| with jumps masked:** 3 of 7 pairs reach p < 0.05 (r 0.21–0.48), but the signs
  are inconsistent.
- **The driving repeats, the field doesn't.** Along-track acceleration repeats at r 0.96 for the
  same section, but also scores 0.60 between different sections. So this test can't show whether
  the train's traction current drives |B|.
- No open magnetic map from this data. The next experiment should be raw-only.
- Plots: `gonogo/mag_*.png`, `mag_summary.png`, `mag_traction_test.png`, `qc/magnetometer_mode.png`.

## (c) Loudness: WEAK

Levels are uncalibrated dBFS(A): relative only, never comparable with TfL's 92.8 dB.

- **Within each trip, LST–BTH minus BTH–MLE is +0.60 dB.** It is positive on 5/5 trips (range
  +0.25 to +1.21, sign test p = 0.031). Trips recorded in one file give +0.45 dB, 4/4.
- **The variants agree on direction, 5/5 each:** core +0.31, loudest 25% of seconds +0.58,
  L10 +0.53, speech-flagged seconds dropped +0.58.
- **The limiter upper bound is the exception:** it agrees on only 2/5 trips and flips sign on every
  eastbound trip.
- **Robust to sync error:** shifting the sync by ±8 s gives +0.60 to +0.64.
- **Section means, dBFS(A):**
  - EB LST→BTH −18.55 ± 0.49
  - EB BTH→MLE −19.31 ± 0.38
  - WB BTH→LST −19.07 ± 0.65
  - WB MLE→BTH −19.43 ± 0.81
- R4 is excluded automatically as the floor run. Its own numbers (−19.35 / −19.87, +0.51) have the
  same sign.
- **Ranking:** the top three section-runs are all LST–BTH.
- **Victoria:** VIC→GPK is −19.4 dB (mid-table) and +0.96 dB above GPK→OXC (n = 1). That is
  consistent in direction with TfL's FOI figure, but within the uncertainty.
- **R0.5 bonus:** TCR→HOL and HOL→CYL are about 2.5 dB below that trip's mean; BNK→LST is +1.1 and
  CYL→STP +0.9 above it.
- Plots: `gonogo/loudness_sections.png`, `loudness_<stem>.png`.

## Messy inputs, detected automatically (`qc.json`)

- **R4 is floor-like.**
  - Hand-held from 0 to 37 s while the train was moving.
  - Then level: tilt 0.6–1.0°, against 3.4–10.7° on every other run.
  - Its 20–50 Hz vertical vibration is 0.02× that of the other runs on the same section.
  - It was isolated rather than hard-coupled, which suggests a soft surface (bag or coat?) rather
    than the bare floor. **Please confirm.**
- **R3** shows hard contact (3.2× peers); **R1.5** shows rattling (5.6× peers).
- **R4's two pauses**, both found from the event log (timestamps show no gap):
  - a false start: 1.17 s recorded, then a 12.2 s pause;
  - the battery pause: 23.35 s at t = 192.90 s, inside BTH→LST. The 24 s of audio that fall
    inside it are dropped from loudness.
- **Split trip:** R1 + R1.5, 853 s apart. R0.5 continues into R1 after 109 s.
- **Other flags:** R2's MLE departure wasn't recorded; R1.5 ran slow.
- **Sync:**
  - The triple taps aren't detectable in the audio, so sync comes from envelope correlation.
  - R0.5 is good to ±0.1 s, R1.5 to ±0.5 s, the rest to about ±5 s. An independent check
    reproduced R0 and R2 to about 0.2 s.
  - That's fine for per-second noise, not for sub-second corrugation work.

## What surprised us

1. iOS recalibrates the magnetometer mid-ride, with jumps of 80–369 µT. Any magnetic work needs the
   raw stream.
2. The calibrated-magnetometer guess was reversed: 2 runs were raw, not 2 calibrated.
3. The "floor" run looks isolated rather than hard-coupled.
4. The eastbound tunnel vibrates 2–10× more than the westbound tunnel between the same stations.
   This could be a real difference in rail condition (corrugation?), but placement and the small
   number of runs could explain it. Unverified.
5. The triple taps are clear in the accelerometer but silent in the audio.
6. The Victoria hotspot is not especially loud in our ranking. Different line and rolling stock,
   one run, and the absolute levels are uncalibrated.
7. The LST–BTH gap is only +0.6 dB: consistent, but small.

## Caveats

- Uncalibrated dBFS(A). The Voice Memos limiter compresses the loud end by about 1–3 dB, and
  whether it also applies automatic gain control is unresolved.
- No in-tunnel ground truth for position.
- Station labels come from the stop sequence, not announcements; `gemini-3.5-transcribe` is next.
- Only 2–3 runs per section, one phone, one morning. Thresholds (placement, sync uniqueness,
  magnetometer-jump detector, yaw veto) were tuned on this same data, so treat these as screening
  results.

## Next steps for tomorrow

1. Replace the taps with a sharp knock or clap, near the microphone, at both ends, while standing
   still.
2. Record audio without AGC or the limiter (e.g. phyphox audio or a raw recorder app), plus one
   calibration reference.
3. Use the raw magnetometer only.
4. Keep the phone's placement constant and note it in the filename. Record more LST→BTH runs and
   more WB runs.
5. Transcribe the announcements, then run L2 DTW positioning on yaw rate, with LST–BTH as the demo
   section.
