# S0 Elizabeth line test ride (WOL -> LST, 24 Oct 2026)

**exploratory / post-hoc / n=1 / outside prereg-24oct.** One ride, one phone, recorded after the pre-registration and kept out of the frozen pipeline (the `S0` stem is not matched by `ingest.RUN_RE`). Stations are inferred, not observed: the three long dwells are taken to be CUH, CWF and WCL. Levels are dBFS(A), uncalibrated. Source: `posthoc/s0_elizabeth.py`. Every number is in `s0_elizabeth.json`, and `s0_elizabeth.png` shows the traces.

Recording: phyphox START 2026-10-24 14:50:59.334 UTC+01:00, events ['START', 'PAUSE'], IMU 0.029-900.418 s at 99.197 Hz (largest gap 0.01 s, so continuous); audio 910.507 s.

## 1. Marker test

**Result.** The snaps and the figure-8 do not work as sync markers in this file. All four snaps fall outside the IMU recording, and the figure-8 does not show in the audio above chance. The file does contain shared events, though: 3 hard jolts to the phone, around the start figure-8 and the handling just after it, are loud in both streams. They give a lag of **4.838 s** (spread 0.021 s), which is 0.04 s from `coarse_lag` (4.8 s) and 0.04 s from `fine_lag` (4.88 s). **Protocol change:** after starting both recorders, knock the phone case firmly 3 times. The accelerometer and the microphone both register a knock. Do this at both ends, while both recorders are running.

### Sync lag (audio_t = sensor_t + lag)

| method | lag (s) | quality |
|---|---|---|
| `sync.coarse_lag` (1 s envelopes, 9 band combos) | 4.8 | support 9/9, spread 0.5 s, peak margin 0.106 |
| `sync.fine_lag` (10 ms; whole / first third / last third) | 4.88 / 4.88 / 4.89 | z 5.815 / 5.987 / 2.937 (band [20, 60] Hz) |
| shared jolts: lag scan -20..+30 s, no prior (strong jolts, n=5) | 4.84 | score 12.446 dB; other lags: median 3.016, p99 6.782, max 11.076 (z 6.188) |
| shared jolts: per-event onset, audio peak >= 10 dB (n=3) | **4.838** | spread 0.021 s |

Drift: the first and last thirds of `fine_lag` differ by one 10 ms step (16.659 ppm, resolution 16.659 ppm). The last third is weak (z 2.937). All strong jolts fall in the first 36 s, so this file cannot test end-vs-start agreement with events. The only jolt near the end (891.627 s, 4.095 m/s^2) gives an audio onset of just 8.621 dB, which is too weak to count. Both clocks are on the same device, and drift below ~20 ppm is about 0.02 s over 15 min.

Shared events (jolt = 8 Hz high-passed |acc| from `sync.accel_spikes`; audio = 100-6000 Hz onset excess over a 1 s median, peak within +-0.1 s of sensor t + scan lag). Weak jolts are listed for completeness. Their offsets are forced near the lag by the +-0.1 s window, so only rows with an audio peak >= 10 dB count as evidence.

| sensor t (s) | jolt (m/s^2) | audio peak (dB) | offset (s) |
|---|---|---|---|
| 4.473 | 12.078 (strong) | 14.181 | 4.827 |
| 8.102 | 7.559 (strong) | 7.168 | 4.863 |
| 11.388 | 10.114 (strong) | 18.891 | 4.847 |
| 13.726 | 11.811 (strong) | 6.948 | 4.744 |
| 36.052 | 20.655 (strong) | 19.97 | 4.838 |
| 777.767 | 2.164 | 9.227 | 4.848 |
| 891.627 | 4.095 | 8.621 | 4.868 |

(19 jolts in total; rows with jolt < 5.0 m/s^2 and audio < 8 dB are omitted here and listed in the JSON.)

### Snaps (audio: 2-6 kHz excess over a 1 s running median)

| end | audio t (s) | excess (dB) | decay to -15 dB (ms) | sensor t at lag (s) | inside IMU recording |
|---|---|---|---|---|---|
| start | 0.87 | 36.868 | 5.0 | -3.968 | no |
| start | 1.805 | 37.935 | 10.0 | -3.033 | no |
| end | 908.625 | 38.687 | 5.0 | 903.787 | no |
| end | 909.18 | 35.501 | 5.0 | 904.342 | no |

The snaps are clean audio markers: excess of 35-39 dB with a decay of 10 ms or less. Each pair sits before phyphox START (audio 4.838 s) or after the IMU end (audio 905.26 s). The accelerometer therefore cannot show them in this file, and nothing can be checked on it. A snap also never moves the phone. Two of the start-window clicks, at audio 9.30 and 16.27 s, have the same 2-6 kHz signature but come with 10-12 m/s^2 jolts. They are the phone being knocked, not snaps (they appear in the shared-event table at sensor 4.47 and 11.39 s).

### Figure-8 (gyro)

| t (s) | dur (s) | peak / max 1 s RMS (rad/s) | axis RMS share x/y/z | min/max axis | dominant Hz x/y/z | figure-8 | raw mag sphere fit |
|---|---|---|---|---|---|---|---|
| 0.049-16.049 | 16.0 | 8.589 / 2.822 | 0.299/0.346/0.356 | 0.84 | 0.375/1.312/0.687 | yes | radius 32.408 uT, misfit 0.35 |
| 30.049-31.049 | 1.0 | 2.047 / 1.115 | 0.286/0.369/0.345 | 0.774 | 3.006/1.002/2.004 | no (handling) |  |
| 34.049-37.049 | 3.0 | 11.749 / 2.157 | 0.233/0.501/0.267 | 0.464 | 0.334/4.008/0.334 | no (handling) |  |
| 40.049-41.049 | 1.0 | 4.32 / 1.739 | 0.052/0.059/0.89 | 0.058 | 3.006/4.008/1.002 | no (handling) |  |
| 891.049-899.049 | 8.0 | 4.549 / 3.318 | 0.366/0.311/0.323 | 0.85 | 0.625/1.124/1.124 | yes | radius 32.977 uT, misfit 0.219 |

Both figure-8s show in the gyro, balanced across the three axes (min/max axis RMS 0.84-0.85). As a magnetometer calibration they fail: the sphere-fit misfit is 22-35 % of the radius, and the ~33 uT radius is well below the Earth field (~49 uT in London). Both waves were presumably made on a platform (start: before boarding at Woolwich; end: after alighting at Liverpool Street), where the field is distorted.

Figure-8 in the audio: Pearson r between gyro |w| and the 20-300 Hz envelope (0.1 s bins). The null is r at 2000 random lags.

| figure-8 | r at shared-event lag | percentile of that r in the null | best lag in +-10 s (r) | null p95 / p99 |
|---|---|---|---|---|
| start [0.049, 16.049] | 0.124 | 84.35 | 6.34 (0.298) | 0.19 / 0.251 |
| end [891.049, 899.049] | -0.287 | 11.05 | 0.74 (0.554) | 0.361 / 0.475 |

At the true lag the correlation is at chance level (start) or negative (end). The 'best' lags (6.3 s and 0.7 s) disagree with each other and with the jolts. The wave leaves no usable handling-noise signature in the microphone.

**End of recording.** The IMU stops at 900.418 s (PAUSE event 900.43 s after START, a round 15 min, which suggests a phyphox timer but is not confirmed). In the last 2 s, RMS |w| per 0.25 s is [1.006, 2.244, 2.011, 0.837, 0.917, 0.392, 0.45, 0.338] rad/s. That is still above the 0.5 rad/s handled threshold until about 0.75 s before the end, and far above platform rest (0.002 rad/s). The end figure-8 lasts 8.0 s against 16.0 s at the start. It was dying down, but the phone was still moving when recording stopped, so its tail **was probably cut**. The audio runs 5.25 s past the IMU end, and the end snaps fall in that stretch.
 There is also a weaker 2-6 kHz click at audio 905.44 s (excess 25.694 dB, decay 5.0 ms), which is sensor ~900.60 s at the shared-event lag, +0.18 s from the IMU end. It may be the screen tap that stopped phyphox, but its cause is not determined, so the timer reading stays unconfirmed either way. It is not used as a sync or drift event.


## 2. Stop gate on real stops (gate only)

S0 has no map, so this tests the gate and not the tracker. `GateParams()` defaults (attempt D, tuned on the Central line 23 Oct only): {'z_on': -0.8, 'z_off': -0.6, 'engage_s': 3.0, 'release_s': 1.0, 'pm_stop': 0.02, 'ref_s': 60.0, 'ref_min_s': 10.0, 'rewind': True, 'cues': ['vib', 'body']}. The gate runs causally from the inferred Woolwich departure (68.279 s) to the end of the recording: 3329 steps of 0.25 s, with valid/handled passed as `GatedTracker.step` does. Gyro bias comes from the three long dwells.

**Result: the gate never engaged** (0 engaged steps in the whole ride). That covers the three platform dwells of 53-64 s each. Its lowest z was -0.768 at 261.779 s, which is about 4 s into the CUH dwell and just short of z_on = -0.8. On the 23 Oct Central runs the same gate flagged 69-85 % of dwell interiors on R2-R6 (`attempts_manual.jsonl` D). So on this line **recall is 0/3 platform dwells**, and the zero false engagements while running say nothing, because the gate was never on.

| interval | dur (s) | interior flagged | engage delay (s) | z median / min (at s) | moving ref vib at start -> end | drop vs section running median (non-causal) | vib / body median | abs yaw median / max (deg/s) | LAeq_1s median |
|---|---|---|---|---|---|---|---|---|---|
| CUH | 53.0 | 0.0 | never | -0.454 / -0.768 (4.0) | -0.971 -> -1.786 | -0.729 | -1.85 / -1.874 | 0.012 / 0.124 | -33.688 |
| CWF | 64.0 | 0.0 | never | -0.396 / -0.596 (28.0) | -1.298 -> -2.088 | -0.874 | -2.071 / -2.205 | 0.009 / 0.059 | -32.968 |
| WCL | 63.5 | 0.0 | never | -0.354 / -0.507 (28.25) | -1.262 -> -2.088 | -0.86 | -2.07 / -2.145 | 0.009 / 0.048 | -34.059 |
| short 456.3-459.8 | 3.5 | None | never | -0.457 / -0.529 (3.25) | -1.225 -> -1.236 | -0.613 | -1.639 / -1.787 | 0.007 / 0.028 | -34.738 |
| short 685.8-696.3 | 10.5 | 0.0 | never | -0.473 / -0.545 (5.25) | -1.176 -> -1.241 | -0.529 | -1.66 / -1.85 | 0.011 / 0.024 | -33.131 |
| short 872.3-878.3 | 6.0 | None | never | -0.365 / -0.491 (2.75) | -1.18 -> -1.21 | -0.373 | -1.508 / -1.771 | 0.013 / 0.034 | -32.841 |
| *ref: platform dwell interiors* | | | | -0.378 | | | -2.08 / -2.19 | 0.009 / 0.07 | -33.688 |
| *ref: running* | | | | 0.073 (p5 -0.222, p1 -0.4) | | | -1.135 / -1.223 (p5 -1.398 / -1.491) | 0.93 / 4.242 | -24.317 |

**Why it missed (diagnosis, not a fix).** Measured against the running median of the preceding section, each dwell sits -0.874 to -0.729 log10 below running (non-causal column). That is around z_on = -0.8, and smaller than the causal dwell z medians of -0.93 to -1.10 that the Central runs reached. Two things combine. (a) On this train and placement the dwell/running contrast is smaller to begin with. (b) The causal reference makes it worse. One possible reason (not measured; n=1, no speed reference) is a longer, smoother braking approach, which would put slow approach steps into the 60 s 'moving' reference before the stop. Either way, the best z in any dwell is only -0.507 to -0.768. Because z stays above z_on, the quiet counter stays at 0, and the dwell steps themselves keep being appended to the reference (`StopGate.update` appends whenever `quiet == 0`). The reference then sinks into the dwell: the vib reference falls by about 0.8 log10 during each dwell (column 'moving ref'), and z drifts back towards 0. Attempt D only stopped pre-engage steps with z < z_on from polluting the reference. Steps that are quiet but above z_on still get in. Any fix (for example freezing the reference while z < z_off, or using a slower reference) would be a new post-hoc attempt, and it must not be tuned on this one ride.

**False engagements while running:** 0 (0.0 s over 561.25 s of running, defined as sections minus 5 s at each edge, minus the short stops +-2 s). This is not evidence of specificity, because the gate never engaged anywhere.

### The short stationary intervals

| short stop | then | gap to dwell (s) | gap vib / body | gap abs yaw median | gap LAeq_1s median |
|---|---|---|---|---|---|
| 456.3-459.8 | CWF dwell | 15.5 | -1.77 / -1.51 | 0.008 | -32.761 |
| 685.8-696.3 | WCL dwell | 9.0 | -1.758 / -1.587 | 0.009 | -31.411 |
| 872.3-878.3 | LST arrival (terminal, rider alights) | - | - | - | - |

**685.8-696.3 s (10.5 s, about 9 s before the inferred WCL platform stop).** The speed evidence points to a standstill, or a crawl too slow to make rolling noise. The audio level is -33.131 dBFS(A), at platform-dwell level (-33.688) and about 9 dB below running (-24.317). Yaw is 0.011 deg/s, the same as at a dwell (running median 0.93), so the train is not curving. Vib / body are -1.66 / -1.85, below the running p5 (-1.398 / -1.491) but about 0.4 log10 above the platform dwells (-2.08 / -2.19). The barometer is flat (`stops.stationary_intervals` dpdt median ~0.01 hPa/s). The gate did not engage: z median -0.473, min -0.545. With no position or speed reference in the tunnel, a stop-short / creep cannot be told apart from a signal check here. The 456.3-459.8 s interval before CWF shows the same pattern.

Between each short stop and its platform dwell (the 'gap' columns above), the audio stays near dwell level (-32.761, -31.411), while car-body motion rises (-1.51, -1.587 log10, against -2.19 at a dwell). That pattern fits a stop short of the platform followed by a slow creep onto the stopping mark. If so, the CWF and WCL arrival edges used here (`stops.stationary_intervals`) mark the end of the creep and could be up to 15.5 s after the first standstill. That is a reading, not an observation.

## 3. Cross-line levels and limiter (same level statistics on both lines)

The section and dwell edges come from different detectors on the two lines: S0 uses raw `stops.stationary_intervals` edges; Central uses `outputs/sections.json` (refined segment edges mapped through `outputs/sync.json`). Over multi-minute sections this should matter little, but it is not identical.

Level statistics, identical for S0 and Central: 1 s A-weighted LAeq (phonometry `weighting_filter`, 48 kHz) over seconds whose centre falls in the section's audio window. LAeq = energy mean. L10 / L90 = 90th / 10th percentile of the 1 s levels. Peak > -1 dBFS = share of seconds whose sample peak exceeds -1 dBFS. Limited = share within 1 dB of the recording's own ceiling (`noise.LIMIT_DB`). Dwell = non-truncated platform dwells minus 3 s at each end. Central figures are recomputed from `outputs/noise/*_levels.csv` with the windows in `outputs/gonogo/loudness_sections.csv` (built from `outputs/sections.json` and `outputs/sync.json`). They reproduce that CSV to within 0.001 dB.

| S0 section (stations inferred) | dur (s) | LAeq | L10 | L90 | peak > -1 dBFS | limited | LAeq from 16 kHz decode (diff) |
|---|---|---|---|---|---|---|---|
| WOL->CUH | 189.5 | -22.913 | -19.355 | -30.948 | 0.311 | 0.353 | -22.905 (+0.009) |
| CUH->CWF | 164.5 | -24.014 | -20.826 | -32.235 | 0.165 | 0.195 | -24.007 (+0.007) |
| CWF->WCL | 166.0 | -24.196 | -21.729 | -30.693 | 0.108 | 0.151 | -24.188 (+0.008) |
| WCL->LST | 103.5 | -23.519 | -20.702 | -29.477 | 0.184 | 0.252 | -23.512 (+0.007) |

S0 moving -23.612 vs platform dwell -31.53, a difference of **+7.918 dB**. Moving: peak > -1 dBFS 0.197, limited 0.241. Recording ceiling -0.45 dBFS. Whole file: peak > -1 dBFS 0.142.

Central 23 Oct (18 section-runs; 16 not placement-excluded):

| Central track (included runs) | n | LAeq (mean of runs) | peak > -1 dBFS | limited |
|---|---|---|---|---|
| EB BTH->MLE | 3 | -19.311 | 0.434 | 0.48 |
| EB LST->BTH | 3 | -18.55 | 0.348 | 0.384 |
| WB BTH->LST | 2 | -19.069 | 0.482 | 0.535 |
| WB MLE->BTH | 2 | -19.433 | 0.349 | 0.389 |

All included Central section-runs, including R0.5 OXC->LST: LAeq -23.512 to -18.215, peak > -1 dBFS 0.178-0.632, limited 0.2-0.684.

| Central run | moving LAeq | dwell LAeq (n s) | moving - dwell | moving peak > -1 dBFS | moving limited | sync (`outputs/sync.json`) |
|---|---|---|---|---|---|---|
| R0.5_EB_OXC-LST | -20.855 | -22.637 (71) | 1.782 | 0.426 | 0.461 | medium (+-0.1 s) |
| R1_EB_LST-BTH | -18.215 | -39.071 (7) | 20.856 | 0.317 | 0.381 | low (+-5.0 s) |
| R1.5_EB_BTH-MLE | -19.426 | None (0) | - | 0.483 | 0.534 | low (+-0.5 s) |
| R2_WB_MLE-LST | -19.72 | -27.678 (39) | 7.957 | 0.413 | 0.475 | low (+-5.0 s) |
| R3_EB_LST-MLE | -18.56 | -26.624 (37) | 8.063 | 0.364 | 0.405 | low (+-5.0 s) |
| R4_WB_MLE-LST | -19.57 | -26.976 (30) | 7.406 | 0.387 | 0.423 | low (+-5.0 s) |
| R5_EB_LST-MLE | -19.321 | -28.511 (26) | 9.19 | 0.402 | 0.423 | low (+-5.0 s) |
| R6_WB_MLE-LST | -18.704 | -25.333 (29) | 6.628 | 0.445 | 0.478 | low (+-5.0 s) |

Decode rate: R5 EB LST->BTH gives -19.099 from a 16 kHz decode against -19.111 in the 48 kHz CSV (difference +0.012 dB). Our streamed 48 kHz decode reproduces the CSV to 0.005 dB per second. On S0 the 16 kHz minus 48 kHz difference is below 0.01 dB per section. S0 is reported at 48 kHz, so decode rate adds no bias.

### Reading (ranking direction only)

- Each S0 section (-24.196 to -22.913 dBFS(A)) is **quieter in this phone's recording** than every LST-BTH-MLE track on the Central line (-19.433 to -18.55), by 3.5-5.6 dB. The ranking runs in the expected direction. Some included Central section-runs are quieter than S0's loudest section: R0.5 TCR->HOL -23.337, R0.5 HOL->CYL -23.512.

- The limiter is pinned on the Central line far more often. Central LST-BTH-MLE seconds within 1 dB of the ceiling: 0.384-0.535. S0 sections: 0.151-0.353. The limiter compresses the loud end, so the Central levels are biased low and the true gap is, if anything, larger than measured.

- Gain is a caveat. S0's platform dwells (-31.53) are also 3-6 dB quieter than the Central R2-R6 dwells (-25.3 to -28.5; R0.5's busy central-London dwells and R1's 7 s of dwell are outliers), yet S0's moving-minus-dwell contrast (+7.918 dB) falls inside the Central R2-R6 range (+6.6 to +9.2 dB). Part of the absolute gap may therefore come from different stations or a different recording gain (the mic mode is unknown), not from the train alone.

- Not like-for-like: same phone and app (Voice Memos), but the mic mode is unknown, the car and stock differ, the placement differs, and S0 is one run against 2-3 per Central track. Central sync is mostly low confidence (+-5 s), while S0 sync is ~0.05 s from the jolts. With multi-minute sections that matters little.

- TfL context only, not a comparison: in-cab LAeq on the Central line is 82-87 dB(A) (FOI-1231-2425, 24 Jun 2024), and on the Hammersmith & City line LST-ALE-WCL it is 64-67 dB(A) (FOI-3780-2324, 2020, S7 stock). Neither covers the Elizabeth line, and the investigator corpus has no TfL Elizabeth line noise figure. None of these dBFS numbers converts to dB(A).

