# Tubesense go/no-go: rides of Fri 23 Oct 2026

Regenerate with `.venv/Scripts/python scripts/run_gonogo.py --clean` (~11.5 min). Numbers come from
`outputs/*.json`; plot paths are relative to `outputs/`. Every result below was built by one agent,
attacked by an independent skeptic agent, and the confirmed issues fixed before this run.

## Verdicts

| check | verdict | one line |
|---|---|---|
| (a) gyro yaw rate | **GO** | Curve signatures repeat run to run, far above chance. LST–BTH is the positioning showcase; BTH–MLE carries little positional detail. |
| (b) magnetometer | **\|B\| NO-GO; bias-free field anomaly WEAK** | \|B\| is spoiled by iOS recalibration jumps and the raw hard-iron bias. The size of the field's deviation from its section average repeats, and part of it survives removing the train's own traction field. |
| (c) loudness LST→BTH vs BTH→MLE | **WEAK** | LST–BTH is louder on 5/5 trips, but only by +0.6 dB, inside the Voice Memos processing uncertainty. |
| (e) positioning ablation | **gyro GO; every other sensor NO-GO at equal weight** | DTW on yaw places held-out curve edges to a median 6 m (CI 3–12), against 24 m for L0. That is curve fixes plus bridging the gaps between them, and it is no better than plain interpolation. No other sensor beats that, and the magnetometer field anomaly makes it worse. Long straights are untested. |
| (f) online tracker (live demo) | **GO on LST↔BTH; WEAK on BTH→MLE** | A causal HMM on gyro yaw tracks held-out rides at a median 2 m (LST↔BTH), against 11 m for a timetable, with honest 90 % bands. It snaps to curves; between curves it is no better than the timetable. |

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
  0.46–1.7 km, against about 1.5 and 2.2 km real. The DTW positioning step (L2) is tested in (e).
- Plots: `gonogo/gyro_EB_LST-BTH.png` (best single picture), `gyro_EB_BTH-MLE.png`,
  `gyro_WB_BTH-LST.png`, `gyro_WB_MLE-BTH.png`, `gyro_summary.png`, `gyro_matrix.png`.

## (b) Magnetometer: |B| NO-GO, bias-free field anomaly WEAK

### Follow-up: de-stepping and the field anomaly (`gonogo/mag_anomaly.json`, `mag_anomaly.png`)

`|B|` can't be compared across runs: the calibrated stream's offset keeps changing and the raw stream
carries a fixed bias. Two fixes:

- **De-stepping:** `features.mag_destep` measures each one-sample iOS offset step and subtracts it.
  It is applied to calibrated runs only, and removed R1.5 ×1, R2 ×4, R3 ×1, R4 ×5 and R6 ×6 steps.
  It does not remove the gradual re-convergence ramps, and it leaves one unknown bias per run.
- **The field anomaly:** `features.field_anomaly` takes |B⃗ − section mean B⃗|. Any constant bias
  cancels in the subtraction, so raw and calibrated runs become comparable. This assumes the phone
  doesn't rotate in the train during the section.

| feature (clean same-section pairs, L0 grid) | same-section mean r | pairs p < 0.05 vs surrogates | different-section null mean / p95 |
|---|---|---|---|
| de-stepped \|B\| (calibrated only) | 0.46 | 1/5 | 0.29 / 0.67 |
| field anomaly | 0.69 | 5/7 | 0.39 / 0.71 |
| field anomaly − linear driving model | 0.53 | 6/7 | 0.22 / 0.43 |
| same, mid-section only (fraction 0.25–0.85) | 0.59 | 4/7 | 0.24 / 0.58 |

- **The train's own field dominates.** Driving proxies (acceleration, traction, speed) explain a
  median 47% of the anomaly. The anomaly peaks while the train pulls away, which fits Royal Holloway
  2017.
- **A position-locked part remains.** The residual repeats above the different-section null, also in
  the middle of each section, away from the traction peaks.
- **The best case is LST→BTH.** Mid-section, the driving model barely repeats (r 0.20–0.35), but the
  anomaly repeats at 0.65–0.75. Raw R1 against calibrated R5 gives r 0.75, p 0.005.
- **De-stepping barely matters here.** The anomaly scores about the same without it (0.70), because
  the test sections contain few steps.
- **Verdict: WEAK.** There is a modest position-locked magnetic signal, strongest on LST→BTH. It can't
  yet be separated from non-linear traction effects that repeat because drivers run each section the
  same way. Add it to the DTW stack at low weight and let the ablation decide.
- **Unvalidated:** the step detector's thresholds were set on this data, and the 4-term driving model
  is linear.

### Original check (|B|, kept for the record)

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
- |B| gives no open magnetic map. The follow-up above is the better test.
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

## (e) Positioning ablation: which sensors help

Files: `positioning/ablation.json`, `ablation.png`, `gap_length.png`, `landmarks.png`,
`alignment_example.png`, `improvement_log.md`. Regenerate with `.venv/Scripts/python scripts/ablation.py`
(~2 min, deterministic).

**Metric: held-out landmark error.** There is no position truth in the tunnel.

- **Truth = curve edges found from yaw.** A curve is |yaw| > 0.6°/s for at least 4 s. Its edges are
  where yaw falls to half the curve's level. Edges cut off by a station or a pause are dropped.
- **Sample:** 27 physical curve edges, 114 scored rows. Every ordered pair of clean runs is used:
  6 pairs on EB LST→BTH, 6 on EB BTH→MLE and 2 on WB BTH→LST (a single run pair).
- **Hiding the landmark:** for each edge, the held-out run's yaw is hidden within ±0.08 of the
  section around it (a gap of about 350 m on LST–BTH). Heading is re-integrated across the gap so it
  cannot carry the hidden turn.
- **Alignment:** the held-out run is aligned to a reference run with multivariate DTW. Channels are
  z-normalised, weights are equal, and the ends are pinned at depart and arrive.
- **Error** = |warp(a) − b| × section length. CIs are 95% bootstrap over physical curve edges, with
  mirrored pairs and shared runs resampled together.

**Section lengths are straight lines:** LST–BTH 2,206 m and BTH–MLE 1,531 m, from the station
coordinates. The TfL Route/Sequence lineStrings, fetched 23 Oct, have no vertices between these
stations, so they only give the same chord. Integrating yaw suggests the real track is 5–6% longer on
LST–BTH and 13–14% longer on BTH–MLE, so the metre errors are low by about that much.

| step (median m, 95% CI) | EB LST→BTH | EB BTH→MLE | WB BTH→LST | overall |
|---|---|---|---|---|
| L0 time fraction | 37 (32–54) | 23 (17–58) | 18 (6–26) | 24 (18–35) |
| L0 + one offset from the other curve edges (no DTW) | 12 (6–14) | 15 (12–47) | 16 (9–30) | 12 (11–16) |
| L1 dead reckoning | 70 (42–82) | 26 (19–52) | 18 (9–32) | 40 (26–64) |
| Interpolate between the other curve edges (no DTW) | 3 (1–11) | 18 (3–33) | 6 (1–21) | 6 (2–16) |
| **L2 DTW yaw (headline)** | **3 (1–9)** | **18 (12–32)** | **6 (4–13)** | **6 (3–12)** |
| L2 DTW yaw, ends open ±5 s (added after seeing the data) | 2 (1–3) | 14 (12–32) | 6 (3–9) | 4 (2–7) |
| L2 DTW yaw + heading (no leak) | 12 (4–29) | 14 (11–23) | 12 (4–20) | 13 (10–18) |
| L2 DTW yaw + drive | 9 (2–40) | 11 (6–62) | 20 (11–32) | 12 (6–31) |
| L2 DTW all six, equal weights | 16 (4–114) | 13 (6–46) | 22 (11–92) | 16 (10–32) |
| Exploratory: all six, weights tuned on this data | 7 | 6 | 8 | 7 |

**What the numbers say**

- **The gyro does the positioning.**
  - DTW on yaw beats L0: −18 m overall (CI −29 to −9). It also wins on LST→BTH and WB BTH→LST.
  - On BTH→MLE there is no detectable effect (−5, CI −44 to +8). That section is essentially one long
    curve.
- **With yaw hidden, DTW is no better than interpolating.** Its error matches plain linear
  interpolation between the neighbouring curve edges with no DTW at all (+0.6 m, CI −5.8 to +4.9).
  - What we measured is curve fixes every 150–350 m, with the gap in between bridged.
  - Error grows with the gap. At about 350 / 530 / 1,060 m hidden on LST–BTH, DTW yaw gives
    6 / 12 / 14 m, interpolation 6 / 8 / 13 m, and L0 24 m throughout.
  - Positioning on long straights with no curves is untested.
- **L0's 37 m on LST→BTH comes mostly from boundary timing, not from differences in driving.**
  R3's departure is timed from the barometer only. L0 and DTW yaw per run pair:
  - R1–R5, the only pair with both boundaries observed: 13 → 3 m
  - R1–R3: 57 → 4 m
  - R3–R5: 48 → 3 m
  - The honest demo line is "13 m → 3 m on the clean pair", not "37 → 3".
- **Station ends are worse.**
  - Landmarks within 0.1 of a station (24 rows): DTW yaw 15 m, against 4 m mid-section. The pinned
    ends fight inaccurate boundaries.
  - Opening the ends by ±5 s gives 4 vs 3 m, and 3.8 m overall (−2.4 m, CI −7.2 to −0.1). This is a
    sensitivity check, not the headline.

**Which sensors help** (±0.08 gap, overall). "Worse" means worse than bridging the gap between curve
fixes: inside the gap the remaining channels take the full weight. The synthetic check shows this
mechanism directly. Mask a clean channel and leave one unrelated noise channel in, and the error
inside the window is 21 bins; without the noise channel it is 0.5 bins.

| channel | alone (m) | alone minus interpolation | added to yaw | verdict |
|---|---|---|---|---|
| yaw (gyro) | 6 | +0.6 (−5.8, +4.9) | – | **helps** vs L0; matches interpolation |
| heading (no leak) | 52 | +46 (16, 77) | +6.7 (0.8, 12.9) | slightly worse; nothing beyond yaw |
| drive (\|horizontal accel\|) | 12 | +6.1 (−4.9, 20.8) | +6.0 (−1.9, 25.6) | no detectable effect |
| vib (3–40 Hz energy) | 42 | +36 (13, 53) | +35.5 (12.5, 49.8) | worse |
| baro (in-section) | 148 | +142 (102, 175) | +102 (65, 140) | worse |
| mag (field anomaly) | 63 | +57 (27, 87) | +56 (25, 79) | worse |

- **Drive is the only competitive non-gyro channel.** It sees no yaw at all and still scores 12 m,
  with no detectable difference from L0 (−12.7, CI −26 to +2) or from interpolation.
  - It works because drivers run each section the same way; it is not locked to position.
  - Expect it to fail with unplanned tunnel stops or a different driver.
- **Vib and mag help only inside the mix.** Leaving one out of the six raises the error: vib +30.5
  (CI 0.7 to 62), mag +17.8 (CI 5.5 to 28).
  - They seem to offset the other noisy channels. On their own, both lose to interpolation.
  - Every other leave-one-out shows no detectable effect.
- **The magnetometer answer is no positioning benefit on these runs.**
  - Feature tested: the field anomaly, low-passed at 1 Hz and given equal weight. It is worse than
    interpolation, both alone and added to yaw.
  - That holds for raw+calibrated pairs (alone 47 m, 36 rows) and for calibrated pairs (70 m,
    78 rows).
  - Scope: this one feature, ±0.08 gaps, these runs. It was not tested on long straights.
- **Heading leaked in the first version.** Heading after the gap carried the hidden turn. The jump
  alone located the hidden edge to about 12 m, with no reference run and no DTW.
  - The earlier "best set, 5 m" (yaw+heading) was that leak: 4.8 m leaky, 13 m with the leak removed.
- **L1 dead reckoning is probably worse than L0:** +15.7 m (CI 0.1 to 36).

**Robustness and stress test**

- **Speed-independent truth:** taking curve edges at 15% and 85% of each curve's turn gives L0 22,
  interpolation 5, DTW yaw 6 and all six 19 m.
- **Distance-domain metric:** converting through the reference run's dead-reckoned distance gives
  yaw 6.1 m (vs 6.2) and L0 22.7 m (vs 24.4).
- **Normalisation** (scratch check, not in `ablation.json`): normalising the held-out run with the
  reference run's statistics moves yaw from 6.2 to 6.6 m overall (each section by about 1–2 m). All
  six goes from 16 to 47 m. Equal-weight multi-sensor sets are fragile.
- **Synthetic DTW check:** a known warp of up to 31 bins is recovered to a median of 0.27 bins
  (worst 2.6).
- **R4 stress test** (the excluded run against clean runs, 26 rows per role):
  - Unmasked, yaw still locks onto R4's curves (2 m).
  - Masked, R4 held out scores 11 m against L0's 12 m; as the reference it scores 16 m.
  - Pause, handheld and floor runs should not be used as references.
- **Exploratory, tuned on this same data:**
  - Tuned weights for all six (drive 4; heading, baro and mag 0.25) give 7 m. Picking the weights on
    two sections and scoring the third gives 7 / 6 / 8 m.
  - A step penalty of 0.5 gives all six 5 m; yaw alone at penalty 0.02–0.2 gives 3.4 m.
  - None of these beats yaw.

**Caveats**

- **Small sample.** 2–3 runs per section, one phone, one morning. WB BTH→LST rests on one run pair,
  so its CI reflects only the spread across landmarks.
- **Thresholds set by eye.** The landmark thresholds were tuned on this data.
- **The truth comes from yaw itself.** So unmasked yaw (1.5 m) is close to circular. Only curve edges
  are scored, so the straights in between have no truth.
- **Offline alignment with both station ends known.** The live demo needs an online, open-end
  variant. Here only the ±5 s open-end version is tested.
- **Some design choices came after seeing the data.** Yaw was picked as the headline because it is
  the one position-locked channel, but only after the leaky "best set" had been seen. The open-end
  and heading-edge variants were also added after seeing the data.
- **The alignment example shows a boundary artefact.** The 60 m step in its first bins is R3's
  barometer-only departure time, not real motion.

## (f) Online tracker for the live demo: GO on LST↔BTH, WEAK on BTH→MLE

Files: `tubesense/online.py`, `scripts/online_eval.py` → `online/online_eval.json`, `online_eval.png`,
`clip_lock.png`, `track_<run>.png`, `maps.json`; `scripts/demo.py` (replay with a moving dot, see README).
Regenerate with `.venv/Scripts/python scripts/online_eval.py` (~45 s).

**What it is.** At each 0.25 s it uses only data up to that moment:

- **Attitude:** a gyro-propagated attitude filter.
- **Signals:** causal yaw, and horizontal acceleration for departure detection.
- **Tracker:** an HMM over (map position × speed level). It advances along the map at the map's
  typical speed profile, and the yaw match pulls it into place. The likelihood is tempered and has
  an outlier floor.
- **Maps:** one per section, built from the other clean runs. Each stores the mean yaw, the time
  per map bin, a dead-reckoned distance axis and the noise level per ~55 m bin.
- **Estimate shown:** the posterior MAP. The posterior mean trails the train at curve edges by
  11–15 m, because hypotheses already past an edge are cut off before the edge is seen. MAP was
  picked after seeing that, and both are reported.

**Truth.** The headline truth is independent: linear interpolation between the held-out ride's
curve edges, matched to the map's, with no DTW. Agreement with the offline-DTW proxy is also
reported, but it is optimistic: it shares the base run and the yaw signal.

**Metres.** Metres are dead-reckoned and scaled to the straight-line length (TfL's lineStrings give
no along-track length). Speed therefore starts from 0, not from a constant.

| held out | tracker vs truth, median / p90 (m) | timetable median / p90 (m) | between curves*: tracker MAP / mean / timetable (m) | 90 % band coverage |
|---|---|---|---|---|
| R1 LST→BTH | 1.9 / 4.6 | 1.5 / 4.1 | 11.6 / 9.0 / 2.2 | 1.00 |
| R3 LST→BTH ‡ | 2.2 / 15.5 | 114 / 125 | 7.5 / 7.2 / 117 | 0.89 |
| R5 LST→BTH | 1.6 / 3.8 | 4.4 / 8.1 | 10.8 / 4.0 / 3.4 | 1.00 |
| R1.5 BTH→MLE | 9.6 / 47 | 31.5 / 159 | 33.4 / 21.2 / 76.2 | 0.81 |
| R3 BTH→MLE | 8.2 / 64 | 9.2 / 102 | 66.9 / 31.3 / 16.2 | 0.83 |
| R5 BTH→MLE | 6.1 / 59 | 13.3 / 101 | 60.9 / 30.4 / 22.0 | 0.83 |
| R2 BTH→LST | 2.1 / 6.4 | 14.5 / 17.4 | 9.8 / 2.0 / 15.9 | 0.99 |
| R6 BTH→LST | 2.3 / 6.3 | 7.9 / 14.2 | 7.5 / 4.7 / 7.7 | 1.00 |
| **median of runs** | **2.2** | 11.2 | 11.2 / 8.1 / 16.1 | mean 0.92 |

\* The held-out ride's yaw is hidden within ±0.08 of the section around each curve edge, and the
estimate is read at that edge. As in (e), this is positioning between curves.
‡ The departure was missed online, because the recording starts as the train leaves. The offline
departure was used, and the run is flagged in the JSON.

**What the numbers say**

- **It snaps to curves.** It beats the timetable wherever runs differ in timing: R3, R2, and every
  p90.
- **Between curves it is no better than the timetable** (median of runs 11 vs 16 m, mixed run by
  run). This is the ablation's finding again. On a clean run with steady driving (R1, R5) the
  timetable is already about 2–4 m, and nothing can beat that.
- **Between curves the posterior mean is better than the MAP**; at curves the MAP is better.
- **BTH→MLE is the weak section.** It is essentially one long curve, so the tracker locks late and
  its bands are optimistic: 0.81–0.83 coverage against a nominal 0.90.
- **The 90 % band is honest on LST↔BTH** (0.89–1.00) and slightly overconfident on BTH→MLE.
  Against the DTW proxy, coverage is 0.94.

**Clip mode** (the demo can start mid-section with no idea of position; uniform prior)

- **Lock time on LST↔BTH:** 0–33 s, depending on where the next distinctive curve is.
- **Lock time on BTH→MLE:** locks only about 25 s before arrival from any start up to 50 %. From
  10 %, that is about 73 s.
- **"Confident" display** = ≥ 50 % of the posterior within 50 m, a 90 % band under 200 m, held for 2 s.
  - It is more than 50 m wrong on 0–0.6 % of confident steps on LST↔BTH, and 8–19 % on BTH→MLE.
  - The cause is two similar curves on one section.
  - The demo paints live noise only when confident.

**Departure detection**

- **The detector:** a jump in horizontal acceleration. That jump is a magnitude, so braking and
  speed changes mid-section trigger it too.
- **When it is armed:** only while the train is known to be at a platform, that is from the start of
  a recording made on the platform, or after the tracker's own arrival.
- **Armed this way,** it matches the offline departure on 9 of 12 sections, at 1.5–8 s earlier
  (offline departures were partly timed from the barometer).
- **The 3 misses** are recordings that start moving or being handled at departure (R3 LST, R2 MLE, R4 MLE).
- **In this evaluation,** arming at the previous arrival uses the offline arrival as a stand-in for the
  tracker's own.
- **Confirmation delay:** about 1–4 s. The tracker then back-dates the start and catches up on the
  buffered data.

**Stress test (R4)**

- **BTH→LST,** with the 23 s phyphox pause: median 2.2 m, p90 7.3 m. The pause is bridged by the speed model.
- **MLE→BTH,** hand-held and moved: median 2.4 m, but p90 140 m and coverage 0.78.

**Caveats**

- **The metre axis** is dead reckoning, about ±100–200 m against the time axis, scaled to the chord
  length (real track ~5–14 % longer).
- **The noise overlay's sync** uses the whole recording.
- **Small sample:** 2–3 runs per map, and WB MLE→BTH has a one-run map.
- **The MAP was picked after seeing the data.** The truth DTW's step penalty came from the ablation on
  these same runs.

**For the demo:** use LST→BTH, starting on the platform. Say it in these terms: "locks onto the
curves, a few metres while curves are in view, timetable-level in between, honest uncertainty
band". Don't quote 2 m as "accuracy".

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

1. iOS recalibrates the magnetometer mid-ride, with jumps of 80–369 µT. A bias-free feature (the
   field anomaly) gets around it. Raw and calibrated runs then match each other (R1 raw vs R3
   calibrated, r 0.88).
2. The calibrated-magnetometer guess was reversed: 2 runs were raw, not 2 calibrated.
3. The "floor" run looks isolated rather than hard-coupled.
4. The eastbound tunnel vibrates 2–10× more than the westbound tunnel between the same stations.
   This could be a real difference in rail condition (corrugation?), but placement and the small
   number of runs could explain it. Unverified.
5. The triple taps are clear in the accelerometer but silent in the audio.
6. The Victoria hotspot is not especially loud in our ranking. Different line and rolling stock,
   one run, and the absolute levels are uncalibrated.
7. The LST–BTH gap is only +0.6 dB: consistent, but small.
8. The first positioning result was flattered by a leak: heading after a masked gap still carried the
   hidden turn. The fix took yaw+heading from 5 m to 13 m.

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
3. Prefer the raw magnetometer: its bias is fixed and the field anomaly handles it. Record more
   LST→BTH runs to separate location from traction.
4. Keep the phone's placement constant and note it in the filename. Record more LST→BTH runs and
   more WB runs.
5. Transcribe the announcements. Record runs that cover long straights before claiming anything
   between curves. The online tracker is built: see (f).
6. Start every recording on the platform, with the phone still for ≥ 20 s before departure. The
   online departure detector is only armed at a platform, and it missed the 3 recordings that began
   moving or being handled.
