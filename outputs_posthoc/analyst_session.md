# Argon analyst session (post-hoc, 23 Oct dev split only)

- Model `gemini-4-argon`, thinking `medium`, experiment budget 10, harness SHA-256 `ad1b570e086b2e08…`

**run_experiment** `{"GAMMA": 0.25, "YAW_SD_FLOOR": 0.35, "P_SPEED": 0.01, "stop_gate": true}` (2026-10-24T07:59:30+00:00)
> Test whether reducing P_SPEED from 0.03 to 0.01 tightens the position band width across straight sections by assuming smoother speed trajectories, without degrading EB BTH->MLE coverage or median error.
- median error 2.3 m, coverage 0.877, band 61.2 m; vs baseline {"error_delta_ci_m": [0.06, 0.51, 1.05], "width_change": -0.171, "track_coverage_drop": {"EB LST->BTH": 0.004, "EB BTH->MLE": 0.107, "WB BTH->LST": -0.001}, "coverage_ok": false, "accepted": false}

**run_experiment** `{"GAMMA": 0.25, "YAW_SD_FLOOR": 0.35, "P_SPEED": 0.1, "stop_gate": true}` (2026-10-24T08:00:16+00:00)
> Since P_SPEED=0.01 degraded error and coverage on EB BTH->MLE, test whether increasing P_SPEED to 0.10 allows faster speed adaptation, reducing median error (error_delta_ci_m < 0) and improving EB BTH->MLE coverage.
- median error 2.6 m, coverage 0.941, band 87.0 m; vs baseline {"error_delta_ci_m": [-0.78, 0.41, 1.55], "width_change": 0.179, "track_coverage_drop": {"EB LST->BTH": -0.02, "EB BTH->MLE": -0.042, "WB BTH->LST": 0.004}, "coverage_ok": true, "accepted": false}

**run_experiment** `{"GAMMA": 0.25, "YAW_SD_FLOOR": 0.2, "P_SPEED": 0.03, "stop_gate": true}` (2026-10-24T08:01:04+00:00)
> Test whether reducing YAW_SD_FLOOR from 0.35 to 0.20 sharpens curve/turn localization and narrows the 90% band width while maintaining coverage_ok and error_delta_ci_m within slack.
- median error 2.25 m, coverage 0.897, band 52.0 m; vs baseline {"error_delta_ci_m": [-1.61, -0.8, -0.14], "width_change": -0.295, "track_coverage_drop": {"EB LST->BTH": -0.002, "EB BTH->MLE": 0.058, "WB BTH->LST": -0.001}, "coverage_ok": false, "accepted": false}

**run_experiment** `{"GAMMA": 0.25, "YAW_SD_FLOOR": 0.2, "P_SPEED": 0.1, "stop_gate": true}` (2026-10-24T08:02:02+00:00)
> YAW_SD_FLOOR=0.20 (Exp 3) moved the error CI entirely below 0 [-1.61, -0.14] and narrowed width by 29.5%, but failed coverage_ok due to a 0.058 drop on EB BTH->MLE. Since P_SPEED=0.10 (Exp 2) increased EB BTH->MLE coverage by 0.042, test whether combining YAW_SD_FLOOR=0.20 and P_SPEED=0.10 preserves the error improvement while satisfying coverage_ok.
- median error 2.4 m, coverage 0.92, band 62.1 m; vs baseline {"error_delta_ci_m": [-1.94, -0.81, -0.05], "width_change": -0.159, "track_coverage_drop": {"EB LST->BTH": -0.019, "EB BTH->MLE": 0.012, "WB BTH->LST": 0.004}, "coverage_ok": true, "accepted": true}

**run_experiment** `{"GAMMA": 0.1, "YAW_SD_FLOOR": 0.2, "P_SPEED": 0.03, "stop_gate": true}` (2026-10-24T08:02:52+00:00)
> Test whether compensating the sharper floor (YAW_SD_FLOOR=0.20) via stronger likelihood tempering (GAMMA=0.10, keeping P_SPEED=0.03) also restores EB BTH->MLE coverage while preserving the error improvement from YAW_SD_FLOOR=0.20.
- median error 2.3 m, coverage 0.942, band 79.8 m; vs baseline {"error_delta_ci_m": [-0.23, -0.1, 0.01], "width_change": 0.081, "track_coverage_drop": {"EB LST->BTH": -0.009, "EB BTH->MLE": -0.051, "WB BTH->LST": -0.004}, "coverage_ok": true, "accepted": false}

**run_experiment** `{"GAMMA": 0.25, "YAW_SD_FLOOR": 0.5, "P_SPEED": 0.03, "stop_gate": true}` (2026-10-24T08:03:54+00:00)
> Verify whether increasing YAW_SD_FLOOR from 0.35 to 0.50 moves metrics in the opposite direction of 0.20 (wider bands and worse localization contrast on EB BTH->MLE).
- median error 2.55 m, coverage 0.942, band 86.9 m; vs baseline {"error_delta_ci_m": [0.27, 0.72, 1.26], "width_change": 0.178, "track_coverage_drop": {"EB LST->BTH": -0.008, "EB BTH->MLE": -0.056, "WB BTH->LST": 0.001}, "coverage_ok": true, "accepted": false}

**submit_final** `{"GAMMA": 0.25, "YAW_SD_FLOOR": 0.2, "P_SPEED": 0.1, "stop_gate": true}` (2026-10-24T08:04:05+00:00)
> On the 23 Oct dev split, (GAMMA=0.25, YAW_SD_FLOOR=0.2, P_SPEED=0.1, stop_gate=True) is accepted under the pre-declared rule: mean coverage is 0.920 (vs 0.918 baseline; EB BTH->MLE drop is only 0.012, 0.809 vs 0.821), the error delta CI is entirely below zero [-1.94, -0.81, -0.05] m (EB BTH->MLE median error drops from 8.8 m to 5.9 m), and mean 90% band width narrows by 15.9% (62.1 m vs 73.8 m). Lowering YAW_SD_FLOOR to 0.20 preserves low-amplitude yaw features on EB BTH->MLE, while increasing P_SPEED to 0.10 prevents speed-transition over-confidence between curves.

## Argon's report

### Experiment Report (23 Oct Dev Split)

**What we tried and why**
Starting from the baseline (`GAMMA=0.25, YAW_SD_FLOOR=0.35, P_SPEED=0.03, stop_gate=True`; median yaw-landmark proxy error `2.3 m`, mean 90% band width `73.8 m`, mean coverage `0.918`, `EB BTH->MLE` coverage `0.821` and median error `8.8 m`), we first tested one-factor sensitivities of the speed transition probability (`P_SPEED`) and observation noise floor (`YAW_SD_FLOOR`), then tested their interaction and likelihood tempering (`GAMMA`).

**What the numbers say**
1. **`P_SPEED` alone:** Lowering `P_SPEED` to `0.01` narrowed bands (`61.2 m`, `-17.1%`) but caused `EB BTH->MLE` coverage to drop by `0.107` (to `0.714`) and worsened error (`CI [+0.06, +1.05] m`). Raising `P_SPEED` to `0.10` improved `EB BTH->MLE` coverage to `0.863` (`-0.042` drop) and reduced its median error to `8.5 m`, but widened mean bands by `+17.9%` (`87.0 m`).
2. **`YAW_SD_FLOOR` alone:** Raising `YAW_SD_FLOOR` to `0.50` washed out subtle yaw features on `EB BTH->MLE`, increasing error (`CI [+0.27, +1.26] m`, track median `10.7 m`) and width (`86.9 m`). Lowering `YAW_SD_FLOOR` to `0.20` sharpened feature contrast, shifting the error delta CI entirely below zero (`[-1.61, -0.80, -0.14] m`, `EB BTH->MLE` median `7.1 m`) and narrowing bands by `29.5%` (`52.0 m`), but failed `coverage_ok` (`EB BTH->MLE` drop `0.058` to `0.763`).
3. **Interaction vs. Tempering:** Compensating `YAW_SD_FLOOR=0.20` with `GAMMA=0.10` widened bands (`79.8 m`) and erased the error gain (`CI [-0.23, +0.01] m`). In contrast, pairing `YAW_SD_FLOOR=0.20` with `P_SPEED=0.10` (`GAMMA=0.25, stop_gate=True`) satisfied all acceptance criteria.

**Submission**
We submitted `GAMMA=0.25, YAW_SD_FLOOR=0.20, P_SPEED=0.10, stop_gate=True`: it achieved `accepted=True`, `coverage_ok=True` (mean coverage `0.920`, `EB BTH->MLE` coverage `0.809`, drop `0.012`), an error delta CI entirely below zero (`[-1.94, -0.81, -0.05] m`, reducing `EB BTH->MLE` median proxy error from `8.8 m` to `5.9 m`), and a `15.9%` narrower band (`62.1 m`).

**Next structural hypothesis**
Replace the uniform `P_SPEED` random walk with a **position-dependent speed transition prior** (higher acceleration/braking variance near stations and speed-limit transitions, lower variance in mid-tunnel cruise) or fuse longitudinal accelerometer cues into the speed transition kernel.

_374.4 s, tokens {'prompt': 8309, 'output': 852}_