# Positioning improvement log

Held-out landmark error, median (95 % CI over curve edges) in metres. Truth = curve edges found from yaw. For each edge the held-out run's yaw is hidden +-0.08 of the section around it (~350 m gap on LST-BTH); heading is re-integrated so it cannot carry the hidden turn. All ordered pairs of clean runs; WB BTH->LST is a single run pair.

| step | EB LST->BTH | EB BTH->MLE | WB BTH->LST | overall |
|---|---|---|---|---|
| L0 time fraction | 37 (32-54) | 23 (17-58) | 18 (6-26) | 24 (18-35) |
| L0 + one offset fitted on the other curve edges (no DTW) | 12 (6-14) | 15 (12-47) | 16 (9-30) | 12 (11-16) |
| L1 dead reckoning | 70 (42-82) | 26 (19-52) | 18 (9-32) | 40 (26-64) |
| Interpolate between the other curve edges (no DTW) | 3 (1-11) | 18 (3-33) | 6 (1-21) | 6 (2-16) |
| **L2 DTW yaw (headline)** | 3 (1-9) | 18 (12-32) | 6 (4-13) | 6 (3-12) |
| L2 DTW yaw, open ends (+-5 s) | 2 (1-3) | 14 (12-32) | 6 (3-9) | 4 (2-7) |
| L2 DTW yaw + heading (heading re-integrated, no leak) | 12 (4-29) | 14 (11-23) | 12 (4-20) | 13 (10-18) |
| L2 DTW yaw + drive | 9 (2-40) | 11 (6-62) | 20 (11-32) | 12 (6-31) |
| L2 DTW all six, equal weights | 16 (4-114) | 13 (6-46) | 22 (11-92) | 16 (10-32) |
| EXPLORATORY: all six, weights tuned on this data | 7 | 6 | 8 | 7 |
| EXPLORATORY: all six, step penalty 0.5 | 4 | 4 | 9 | 5 |

**Per run pair, EB LST->BTH** (median m: L0 / L0+offset / interp / DTW yaw; * = a boundary timed from the barometer only):
- R1:LST-BTH vs R3:LST-BTH *: 57 / 12 / 1 / 4
- R1:LST-BTH vs R5:LST-BTH: 13 / 6 / 4 / 3
- R3:LST-BTH vs R5:LST-BTH *: 48 / 18 / 5 / 3

**Gap length** (overall median, mask half-width 0.04 / 0.08 / 0.12 / 0.16 / 0.24): DTW yaw 7 / 6 / 12 / 10 / 14 m; interpolation 2 / 6 / 8 / 10 / 13 m; L0 24 / 24 / 24 / 24 / 24 m.
**Near a station** (fa < 0.1 or > 0.9, n = 24) vs mid-section (n = 90): DTW yaw 15 vs 4 m; open ends 4 vs 3 m; L0 27 vs 21 m.
**DTW yaw vs interpolation (no DTW)**: +0.6 m [-5.8, 4.9], no detectable effect. **DTW yaw vs L0**: -18.2 m [-29.4, -8.9].
**Robustness, speed-independent heading edges as truth** (overall): L0 22, interp 5, DTW yaw 6, all six 19 m.

## Read this before quoting a number

- With yaw hidden, DTW yaw is bridging the gap between the neighbouring curve edges, so it does about as well as interpolating between them with no DTW. These numbers measure curve fixes every ~150-350 m plus bridging, not positioning on long straight track (unmeasured; see the gap-length line).
- Most of L0's error on LST->BTH is boundary timing (R3's departure is timed from the barometer only), not driving differences: compare the R1 vs R5 pair, whose boundaries are both observed.
- The truth is derived from the same yaw signal; unmasked yaw numbers are close to circular.
- 2-3 runs per section, one phone, one morning; the alignment is offline with both station ends known (the live demo needs an online, open-end variant). Exploratory rows are tuned on these same landmarks.
- Metres = L0 fraction differences x straight-line length (LST-BTH 2206 m, BTH-MLE 1531 m; TfL lineStrings are too coarse). The real track is ~5 % (LST-BTH) to ~13 % (BTH-MLE) longer.
