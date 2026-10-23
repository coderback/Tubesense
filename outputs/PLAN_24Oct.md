# Pre-registered plan for the 24 Oct 2026 rides

Written and committed on Fri 23 Oct 2026, before the rides. Git tag `prereg-24oct` marks the frozen
state.

## What is frozen

Everything that turns recordings into numbers is frozen at the tag:

- **Code:** `tubesense/` and `scripts/`.
- **Settings:** all thresholds and tracker parameters, including the settings block in
  `outputs/online/online_eval.json`.
- **Frozen maps:** the demo maps built from the 23 Oct rides only, saved as
  `outputs/prereg/maps_23Oct.json`.

**After the rides,** the only changes allowed before the pre-registered numbers are reported are:

- naming and metadata for the new files;
- crash fixes that leave the numbers on the 23 Oct data unchanged, checked by rerunning and diffing
  the JSON outputs.

**Anything else is post-hoc.** Any analysis decided after seeing the 24 Oct data is labelled
post-hoc in `FINDINGS.md`.

## Purpose

1. **Generalisation:** does the method (gyro curve signatures and the online tracker) work on a
   second, different line? The test line is the Hammersmith & City, a shallow, cut-and-cover
   sub-surface line run with S7 trains. The method so far was built on the Central line, a deep bored
   tube with 1992 stock.
2. **Generalisation in time:** do maps built on 23 Oct still place rides from 24 Oct?
3. **Magnetism:** is the bias-free field anomaly as repeatable on the sub-surface line as on the deep
   tube?

## Collection protocol

- **Lines and runs.**
  - Central Liverpool Street ⇄ Mile End, via Bethnal Green: at least 3 runs each way.
  - Hammersmith & City Liverpool Street ⇄ Whitechapel, via Aldgate East: at least 3 runs each way.
  - On H&C, board trains **towards Barking**. Circle trains run eastbound via Aldgate, and
    Metropolitan trains usually terminate at Aldgate.
- **Magnetometer calibration.** Before going underground, and again at the end of the day:
  - Do a 30 s street-level figure-of-eight with the raw magnetometer: rotate the phone slowly through
    all orientations, in the open and away from cars and metal.
  - Save it as `data/raw/phyphox/CAL_<anything>.xlsx`.
- **Every run:**
  - raw magnetometer;
  - the same phone placement, written in `data/raw/notes.csv`;
  - the phone still on the platform for 20 s or more before departure;
  - a sharp knock or clap near the microphone at the start and the end, standing still;
  - battery above 50 %.
- **Naming:** `R<id>_<DIR>_<FROM>-<TO>`, with the numbers continuing from R7. Examples:
  `R7_EB_LST-MLE`, `R9_EB_LST-WCL`, `R10_WB_WCL-LST`.
- **No cherry-picking.** All recorded runs are processed. Exclusions come only from the existing
  automatic QC: phone handled, a section truncated, or a pause inside a section.

## Analysis (unchanged code)

`scripts/run_gonogo.py` runs the full chain. That includes `mag_compare` and `forward_test`.

## Hypotheses and success criteria

### H1. Gyro curve signatures repeat on the H&C

This applies the existing rule in `scripts/repeatability.py` (`verdict_gyro`) to the H&C groups,
using the different-section null pooled over both lines. It applies to each H&C section and
direction with at least 2 clean runs.

- **GO:** every clean same-section pair has best r within ±10 % above the null's maximum, **and**
  beats its phase surrogates at p < 0.05.
- **WEAK:** some pairs pass, not all.
- **NO-GO:** no pair passes.
- **Low information:** groups whose surrogate p99 is above 0.8 are reported as low information.
  That is the same flag as Central's Bethnal Green → Mile End.

### H2a. The online tracker generalises to the H&C

This is the leave-one-run-out test in `scripts/online_eval.py`: H&C maps are built from the other H&C
rides only.

- **GO:** the tracker's median-of-runs error against the independent truth is below the timetable
  baseline's, **and** the mean 90 % band coverage is at least 0.80.
- **WEAK:** only one of the two holds.
- **NO-GO:** neither holds.
- **Also reported:** the hidden-yaw test between curves, and clip-mode lock times.
- **Expectation, stated in advance:** the H&C sections are short (about 0.8 and 1.0 km straight-line)
  and may have few curves. A timetable-level result between curves is a legitimate outcome, not a
  failure of the pipeline.

### H2b. Forward-in-time test on the Central line

`scripts/forward_test.py` tracks each clean 24 Oct Central section with the frozen 23 Oct maps.

- **GO:** median-of-runs error against the independent truth is 5 m or less on Liverpool Street ⇄
  Bethnal Green, **and** the mean 90 % band coverage there is at least 0.80.
- Bethnal Green → Mile End and Mile End → Bethnal Green (the second has a one-run map) are reported
  without a threshold.

### H3. Magnetism: deep tube vs sub-surface

This uses `scripts/mag_compare.py`.

- **Primary statistic:** the excess r of the field anomaly, with the driving model regressed out and
  the mid-section only (`resid_mid`), on **raw-raw** same-section pairs, per line.
  - Excess r = mean same-section r minus the mean r of that line's different-section null.
  - It has a bootstrap 95 % CI.
- **Comparison:** Central minus H&C, with its CI, tested two-sided. There is no directional
  prediction.
  - A CI that excludes 0 means **these two lines differ** in how repeatable the field anomaly is.
  - It does not mean "sub-surface tunnels differ". The lines differ in construction **and** in
    rolling stock and traction equipment, and this design cannot separate the two.
- **Secondary:** the same statistic for `anom`, `resid` and `anom_mid`, and for calibrated pairs if
  any.

### H4. Platform magnetic signatures (exploratory, no threshold)

- **Condition:** only if the `CAL_*` recording passes validation. The fitted field must be 35–65 µT
  and the relative misfit below 10 %.
- **What is reported:** |B − hard iron| at still platform dwells (raw runs), per line, station and
  platform direction. This includes the spread across visits against the spread across stations.

## Known limits, stated in advance

- **Small sample:** about 3 runs per section per direction, one phone, one day per line.
- **Shared runs:** pairs share runs, so the CIs from bootstrapping pairs are optimistic.
- **Metres:** dead-reckoned and scaled to straight-line section lengths. TfL's lineStrings have no
  vertices between stations on either line.
- **Truth:** the independent truth comes from yaw curve edges, so a section with no curves has no
  truth inside it.

## Addendum: H5, absolute levels from the NIOSH Sound Level Meter app

Added Sat 24 Oct 2026, 00:45 BST, before the rides. The rest of the plan is unchanged. No analysis
code for H5 exists yet. It will be written after the rides, and it will do exactly what is specified
here; anything beyond this is labelled post-hoc.

**Collection**

- **Preferred:** a second phone running the NIOSH SLM app, next to the main phone in the same
  placement, on the same rides. Start it at the start knock or clap. iOS generally won't let the app
  and Voice Memos record from one phone's microphone at the same time.
- **Fallback:** with only one phone, record one or two runs per section with NIOSH instead of Voice
  Memos, starting and stopping at the stations.
- **Settings:** A-weighted. Note the device model, and whether the app lists it as calibrated or an
  external measurement microphone is used.
- **Log** in `data/raw/niosh.csv`, one row per run and section (levels only; no audio is kept):
  `run_id, line, from, to, device, mic, LAeq_dBA, LAFmax_dBA, LCpeak_dB, start_time, end_time, notes`.

**H5 (descriptive, no threshold)**

1. **Absolute section level:** LAeq per line, section and direction, as mean ± sd over runs, labelled
   *indicative*. Report it against the HSE 85 dB(A) upper action level. Compare Liverpool Street →
   Bethnal Green with TfL's FOI figure of 92.8 dB for that section.
2. **Offset to our relative levels:** NIOSH LAeq minus the Voice Memos section LAeq (dBFS(A)) for the
   same section of the same ride, with both phones together.
   - Report the median offset and its spread across sections.
   - Report the slope of the offset against level. A slope above 0 at the loud end is the Voice
     Memos limiter's compression.
3. **The loudness question again:** the within-trip difference, Liverpool Street → Bethnal Green minus
   Bethnal Green → Mile End, using NIOSH levels. Compare it with the +0.6 dB found from Voice Memos
   on 23 Oct.

**Known limits, stated in advance**

- **Above 85 dB(A) the readings may be low.** The app on an iPhone's built-in microphone was validated
  to about 0.5 dB(A) only up to about 85 dB (Huyan et al. 2023, JMIR, PMC10686533). Tube sections
  near 90 dB are above that range and may be underestimated, unless an external measurement
  microphone is used.
- **It is not a certified meter.** All absolute levels are indicative.
- **Placement differs.** Two phones in slightly different positions add some offset noise.
