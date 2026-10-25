# Positioning improvement log

Held-out landmark error, median (95 % CI over curve edges) in metres. Truth = curve edges found from yaw. For each edge the held-out run's yaw is hidden +-0.08 of the section around it (~350 m gap on LST-BTH); heading is re-integrated so it cannot carry the hidden turn. All ordered pairs of clean runs; WB BTH->LST is a single run pair.

| step | EB LST->BTH | EB BTH->MLE | WB BTH->LST | WB MLE->BTH | EB LST->ALE | line central | line hammersmith-city | overall |
|---|---|---|---|---|---|---|---|---|
| L0 time fraction | 13 (9-15) | 56 (38-61) | 30 (23-37) | 27 (23-41) | 31 (26-65) | 23 (18-28) | 31 (26-65) | 23 (18-29) |
| L0 + one offset fitted on the other curve edges (no DTW) | 10 (5-13) | 28 (19-38) | 26 (20-38) | 41 (28-51) | 74 (60-93) | 16 (12-21) | 74 (60-93) | 16 (12-22) |
| L1 dead reckoning | 28 (21-35) | 56 (35-72) | 28 (21-39) | 29 (19-59) | 10 (5-32) | 29 (24-37) | 10 (5-32) | 29 (23-36) |
| Interpolate between the other curve edges (no DTW) | 1 (0-4) | 13 (2-31) | 10 (1-22) | 3 (1-14) | 48 (38-54) | 3 (1-7) | 48 (38-54) | 3 (1-9) |
| **L2 DTW yaw (headline)** | 3 (2-6) | 12 (8-25) | 10 (5-14) | 8 (2-14) | 21 (5-43) | 6 (4-9) | 21 (5-43) | 7 (4-9) |
| L2 DTW yaw, open ends (+-5 s) | 3 (2-4) | 12 (6-25) | 7 (4-13) | 8 (2-17) | 20 (5-30) | 5 (4-7) | 20 (5-30) | 5 (4-7) |
| L2 DTW yaw + heading (heading re-integrated, no leak) | 12 (3-20) | 16 (12-24) | 12 (7-17) | 16 (10-26) | 17 (12-81) | 12 (9-17) | 17 (12-81) | 12 (9-17) |
| L2 DTW yaw + drive | 11 (7-23) | 11 (6-20) | 24 (13-38) | 18 (7-24) | 9 (0-28) | 15 (10-21) | 9 (0-28) | 14 (10-21) |
| L2 DTW all six, equal weights | 16 (10-30) | 11 (5-20) | 16 (9-46) | 13 (6-25) | 23 (5-36) | 15 (11-22) | 23 (5-36) | 15 (11-22) |
| EXPLORATORY: all six, weights tuned on this data | 11 | 7 | 10 | 8 | 13 | 9 | 13 | 9 |
| EXPLORATORY: all six, step penalty 0.5 | 4 | 5 | 8 | 6 | 8 | 5 | 8 | 5 |

**Per run pair, EB LST->BTH** (median m: L0 / L0+offset / interp / DTW yaw; * = a boundary timed from the barometer only):
- R1:LST-BTH vs R3:LST-BTH *: 57 / 12 / 1 / 4
- R1:LST-BTH vs R5:LST-BTH: 13 / 6 / 4 / 3
- R1:LST-BTH vs R7:LST-BTH: 4 / 11 / 1 / 4
- R1:LST-BTH vs R9:LST-BTH: 7 / 8 / 1 / 2
- R11:LST-BTH vs R1:LST-BTH: 6 / 14 / 1 / 3
- R3:LST-BTH vs R5:LST-BTH *: 48 / 18 / 5 / 3
- R3:LST-BTH vs R7:LST-BTH *: 53 / 24 / 3 / 6
- R3:LST-BTH vs R9:LST-BTH *: 62 / 23 / 6 / 4
- R11:LST-BTH vs R3:LST-BTH *: 49 / 27 / 4 / 8
- R5:LST-BTH vs R7:LST-BTH: 5 / 6 / 2 / 2
- R5:LST-BTH vs R9:LST-BTH: 14 / 4 / 1 / 2
- R11:LST-BTH vs R5:LST-BTH: 7 / 11 / 1 / 2
- R7:LST-BTH vs R9:LST-BTH: 9 / 1 / 2 / 5
- R11:LST-BTH vs R7:LST-BTH: 3 / 3 / 2 / 2
- R11:LST-BTH vs R9:LST-BTH: 12 / 6 / 1 / 2

**Gap length** (overall median, mask half-width 0.04 / 0.08 / 0.12 / 0.16 / 0.24): DTW yaw 6 / 7 / 9 / 10 / 13 m; interpolation 1 / 3 / 5 / 8 / 11 m; L0 23 / 23 / 23 / 23 / 23 m.
**Near a station** (fa < 0.1 or > 0.9, n = 184) vs mid-section (n = 478): DTW yaw 12 vs 4 m; open ends 7 vs 4 m; L0 19 vs 27 m.
**DTW yaw vs interpolation (no DTW)**: +3.8 m [-0.4, 5.3], no detectable effect. **DTW yaw vs L0**: -16.2 m [-22.3, -11.5].
**Robustness, speed-independent heading edges as truth** (overall): L0 23, interp 5, DTW yaw 6, all six 16 m.

## Read this before quoting a number

- With yaw hidden, DTW yaw is bridging the gap between the neighbouring curve edges, so it does about as well as interpolating between them with no DTW. These numbers measure curve fixes every ~150-350 m plus bridging, not positioning on long straight track (unmeasured; see the gap-length line).
- Most of L0's error on LST->BTH is boundary timing (R3's departure is timed from the barometer only), not driving differences: compare the R1 vs R5 pair, whose boundaries are both observed.
- The truth is derived from the same yaw signal; unmasked yaw numbers are close to circular.
- 2-3 runs per section, one phone, one morning; the alignment is offline with both station ends known (the live demo needs an online, open-end variant). Exploratory rows are tuned on these same landmarks.
- Metres = L0 fraction differences x straight-line length (LST-ALE 791 m, LST-BTH 2206 m, BTH-MLE 1531 m, ALE-WCL 993 m; TfL lineStrings are too coarse). The real track is ~5 % (LST-BTH) to ~13 % (BTH-MLE) longer.
