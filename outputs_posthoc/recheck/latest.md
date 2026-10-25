# Argon re-derivation of our headline numbers (20261025T085519Z)

Post-hoc check of **precomputed** outputs of the frozen pipeline. `gemini-4-argon` (thinking `medium`) got only derived tables and the definitions / frozen rule text, computed every value with its code-execution sandbox, and never saw our values. This script compared them under tolerances declared before the run.

**65/65 match**, 0 mismatch; numbers traced to sandbox output 55/55; total 216.3 s.

Tolerances: db 0.002, m 0.01, fraction 0.0005, p 1e-06, count 0.0, label None (+ half a unit in the last decimal of our published value).

## Central section mean levels (LAeq, placement-included runs)

28.4 s, attempts 1, code executed: True

| quantity | ours | Argon | match | traced | note |
|---|---|---|---|---|---|
| n:EB LST->BTH | 6 | 6 | yes | yes | |diff| 0 vs tol 1e-09 |
| mean_db:EB LST->BTH | -18.658 | -18.6577 | yes | yes | |diff| 0.000333 vs tol 0.0025 |
| sd_db:EB LST->BTH | 0.349 | 0.349212 | yes | yes | |diff| 0.000212 vs tol 0.0025 |
| n:EB BTH->MLE | 6 | 6 | yes | yes | |diff| 0 vs tol 1e-09 |
| mean_db:EB BTH->MLE | -19.419 | -19.4192 | yes | yes | |diff| 0.000167 vs tol 0.0025 |
| sd_db:EB BTH->MLE | 0.351 | 0.351241 | yes | yes | |diff| 0.000241 vs tol 0.0025 |
| n:WB MLE->BTH | 5 | 5 | yes | yes | |diff| 0 vs tol 1e-09 |
| mean_db:WB MLE->BTH | -19.075 | -19.0754 | yes | yes | |diff| 0.0004 vs tol 0.0025 |
| sd_db:WB MLE->BTH | 0.646 | 0.646439 | yes | yes | |diff| 0.000439 vs tol 0.0025 |
| n:WB BTH->LST | 5 | 5 | yes | yes | |diff| 0 vs tol 1e-09 |
| mean_db:WB BTH->LST | -18.799 | -18.7992 | yes | yes | |diff| 0.0002 vs tol 0.0025 |
| sd_db:WB BTH->LST | 0.477 | 0.47679 | yes | yes | |diff| 0.00021 vs tol 0.0025 |

Argon's definition notes: All definitions were unambiguous and followed literally: filtered rows by direction, section ('<from>-><to>'), and placement_excluded == False; computed row count n, arithmetic mean, and sample standard deviation (ddof=1) on the LAeq column.

<details><summary>Argon's executed code</summary>

```python
import pandas as pd
import numpy as np

df = pd.read_csv('input_file_0.csv')
df['section'] = df['from'] + '->' + df['to']

targets = [
    ('EB', 'LST->BTH'),
    ('EB', 'BTH->MLE'),
    ('WB', 'MLE->BTH'),
    ('WB', 'BTH->LST')
]

results = []
for direction, section in targets:
    sub = df[(df['direction'] == direction) & (df['section'] == section) & (df['placement_excluded'] == False)]
    n = int(len(sub))
    mean_db = float(sub['LAeq'].mean())
    sd_db = float(sub['LAeq'].std(ddof=1))
    print(f"n:{direction} {section} = {n}")
    print(f"mean_db:{direction} {section} = {mean_db!r}")
    print(f"sd_db:{direction} {section} = {sd_db!r}")
```

</details>

## H5 NIOSH arithmetic: above HSE 85 dB(A), offsets, EB-WB gap

18.6 s, attempts 2, code executed: True

| quantity | ours | Argon | match | traced | note |
|---|---|---|---|---|---|
| above_hse_upper_db:N1 | 6.9 | 6.9 | yes | yes | |diff| 5.33e-15 vs tol 0.052 |
| offset_median_db:N1 | 111.89 | 111.89 | yes | yes | |diff| 0 vs tol 0.007 |
| offset_min_db:N1 | 111.51 | 111.51 | yes | yes | |diff| 0 vs tol 0.007 |
| offset_max_db:N1 | 112.28 | 112.28 | yes | yes | |diff| 0 vs tol 0.007 |
| above_hse_upper_db:N2 | 2.3 | 2.3 | yes | yes | |diff| 2.66e-15 vs tol 0.052 |
| offset_median_db:N2 | 106.65 | 106.65 | yes | yes | |diff| 0 vs tol 0.007 |
| offset_min_db:N2 | 106.41 | 106.41 | yes | yes | |diff| 0 vs tol 0.007 |
| offset_max_db:N2 | 107.51 | 107.51 | yes | yes | |diff| 1.42e-14 vs tol 0.007 |
| niosh_eb_minus_wb_db | 4.6 | 4.6 | yes | yes | |diff| 8.88e-15 vs tol 0.052 |

Argon's definition notes: All definitions were unambiguous and followed literally using standard IEEE 754 floating-point arithmetic in pandas/numpy.

<details><summary>Argon's executed code</summary>

```python
import pandas as pd
import numpy as np

df = pd.read_csv('input_file_0.csv')
df['offset'] = df['niosh_LAeq_dBA'] - df['voice_memos_trip_LAeq_dBFS']

hse_upper = 85.0
results = []

for trip, group in df.groupby('niosh_trip', sort=False):
    niosh_laeq = group['niosh_LAeq_dBA'].iloc[0]
    above_hse = niosh_laeq - hse_upper
    offset_med = group['offset'].median()
    offset_min = group['offset'].min()
    offset_max = group['offset'].max()
    
    results.append((f"above_hse_upper_db:{trip}", float(above_hse)))
    results.append((f"offset_median_db:{trip}", float(offset_med)))
    results.append((f"offset_min_db:{trip}", float(offset_min)))
    results.append((f"offset_max_db:{trip}", float(offset_max)))

eb_laeq = df[df['direction'] == 'EB']['niosh_LAeq_dBA'].iloc[0]
wb_laeq = df[df['direction'] == 'WB']['niosh_LAeq_dBA'].iloc[0]
eb_minus_wb = eb_laeq - wb_laeq
results.append(("niosh_eb_minus_wb_db", float(eb_minus_wb)))

for k, v in results:
    print(f"{k} = {repr(v)}")
```

</details>

## Within-trip contrast LST-BTH minus BTH-MLE (LAeq, placement-ok trips)

29.0 s, attempts 2, code executed: True

| quantity | ours | Argon | match | traced | note |
|---|---|---|---|---|---|
| n_trips | 11 | 11 | yes | yes | |diff| 0 vs tol 1e-09 |
| n_positive | 10 | 10 | yes | yes | |diff| 0 vs tol 1e-09 |
| mean_db | 0.540986 | 0.540909 | yes | yes | |diff| 7.66e-05 vs tol 0.002 |
| sd_db | 0.356517 | 0.356597 | yes | yes | |diff| 7.99e-05 vs tol 0.002 |
| min_db | -0.133424 | -0.134 | yes | yes | |diff| 0.000576 vs tol 0.002 |
| max_db | 1.21034 | 1.21 | yes | yes | |diff| 0.000338 vs tol 0.002 |
| sign_test_p_one_sided | 0.00585938 | 0.00585938 | yes | yes | |diff| 0 vs tol 1e-06 |
| diff_db:R1+R1.5_EB_LST-MLE | 1.21034 | 1.21 | yes | yes | |diff| 0.000338 vs tol 0.002 |
| diff_db:R10_WB_MLE-LST | -0.133424 | -0.134 | yes | yes | |diff| 0.000576 vs tol 0.002 |
| diff_db:R11_EB_LST-MLE | 0.672852 | 0.673 | yes | yes | |diff| 0.000148 vs tol 0.002 |
| diff_db:R12_WB_MLE-LST | 0.488312 | 0.488 | yes | yes | |diff| 0.000312 vs tol 0.002 |
| diff_db:R2_WB_MLE-LST | 0.476323 | 0.476 | yes | yes | |diff| 0.000323 vs tol 0.002 |
| diff_db:R3_EB_LST-MLE | 0.559872 | 0.56 | yes | yes | |diff| 0.000128 vs tol 0.002 |
| diff_db:R4_WB_MLE-LST | 0.514618 | 0.515 | yes | yes | |diff| 0.000382 vs tol 0.002 |
| diff_db:R5_EB_LST-MLE | 0.511841 | 0.512 | yes | yes | |diff| 0.000159 vs tol 0.002 |
| diff_db:R6_WB_MLE-LST | 0.251971 | 0.252 | yes | yes | |diff| 2.9e-05 vs tol 0.002 |
| diff_db:R7_EB_LST-MLE | 0.634285 | 0.635 | yes | yes | |diff| 0.000715 vs tol 0.002 |
| diff_db:R8_WB_MLE-LST | 0.299317 | 0.299 | yes | yes | |diff| 0.000317 vs tol 0.002 |
| diff_db:R9_EB_LST-MLE | 0.979154 | 0.979 | yes | yes | |diff| 0.000154 vs tol 0.002 |
| placement_excluded_trips | R4_WB_MLE-LST | R4_WB_MLE-LST | yes | - | exact label |

Argon's definition notes: All definitions were followed literally with no ambiguity found.

<details><summary>Argon's executed code</summary>

```python
import pandas as pd
import numpy as np
from scipy.stats import binomtest

df = pd.read_csv('input_file_0.csv')

paired_trips = []
for trip, group in df.groupby('trip', sort=False):
    dirs = group['direction'].unique()
    if len(dirs) != 1:
        continue
    d = dirs[0]
    if d == 'EB':
        r1 = group[(group['from'] == 'LST') & (group['to'] == 'BTH')]
        r2 = group[(group['from'] == 'BTH') & (group['to'] == 'MLE')]
    elif d == 'WB':
        r1 = group[(group['from'] == 'BTH') & (group['to'] == 'LST')]
        r2 = group[(group['from'] == 'MLE') & (group['to'] == 'BTH')]
    else:
        continue
    
    if len(r1) == 1 and len(r2) == 1:
        diff = float(r1['LAeq'].iloc[0] - r2['LAeq'].iloc[0])
        excl = bool(r1['placement_excluded'].iloc[0] or r2['placement_excluded'].iloc[0])
        paired_trips.append((trip, diff, excl))

for t, diff, excl in paired_trips:
    print(f"diff_db:{t} = {diff!r} (excl={excl})")

excl_trips = sorted([t for t, _, excl in paired_trips if excl])
print("placement_excluded_trips =", ",".join(excl_trips))

ok_diffs = [diff for _, diff, excl in paired_trips if not excl]
n_trips = len(ok_diffs)
n_positive = sum(1 for d in ok_diffs if d > 0)
mean_db = float(np.mean(ok_diffs))
sd_db = float(np.std(ok_diffs, ddof=1))
min_db = float(np.min(ok_diffs))
max_db = float(np.max(ok_diffs))
p_val = float(binomtest(n_positive, n_trips, 0.5, alternative="greater").pvalue)

print(f"n_trips = {n_trips}")
print(f"n_positive = {n_positive}")
print(f"mean_db = {mean_db!r}")
print(f"sd_db = {sd_db!r}")
print(f"min_db = {min_db!r}")
print(f"max_db = {max_db!r}")
print(f"sign_test_p_one_sided = {p_val!r}")
```

</details>

## H2b forward test: median-of-runs error, coverage, verdict

32.6 s, attempts 2, code executed: True

| quantity | ours | Argon | match | traced | note |
|---|---|---|---|---|---|
| n_sections | 10 | 10 | yes | yes | |diff| 0 vs tol 1e-09 |
| overall_tracker_median_m | 1.8 | 1.75 | yes | yes | |diff| 0.05 vs tol 0.06 |
| overall_timetable_median_m | 11.2 | 11.2 | yes | yes | |diff| 0 vs tol 0.06 |
| overall_band90_coverage_mean | 0.951 | 0.9507 | yes | yes | |diff| 0.0003 vs tol 0.001 |
| lstbth_n_sections | 6 | 6 | yes | yes | |diff| 0 vs tol 1e-09 |
| lstbth_tracker_median_m | 1.95 | 1.95 | yes | yes | |diff| 2.22e-16 vs tol 0.015 |
| lstbth_band90_coverage_mean | 0.963 | 0.963 | yes | yes | |diff| 0 vs tol 0.001 |
| lstbth_timetable_median_m | 8.2 | 8.2 | yes | yes | |diff| 0 vs tol 0.06 |
| h2b_verdict | GO | GO | yes | - | exact label |

Argon's definition notes: All definitions were followed literally using standard pandas median() and mean() over the specified rows.

<details><summary>Argon's executed code</summary>

```python
import pandas as pd
import numpy as np

df = pd.read_csv('input_file_0.csv')

n_sections = len(df)
overall_tracker_median_m = float(df['tracker_median_error_m'].median())
overall_timetable_median_m = float(df['timetable_median_error_m'].median())
overall_band90_coverage_mean = float(df['band90_coverage'].mean())

lstbth_df = df[df['track'].isin(['EB LST->BTH', 'WB BTH->LST'])]
lstbth_n_sections = len(lstbth_df)
lstbth_tracker_median_m = float(lstbth_df['tracker_median_error_m'].median())
lstbth_timetable_median_m = float(lstbth_df['timetable_median_error_m'].median())
lstbth_band90_coverage_mean = float(lstbth_df['band90_coverage'].mean())

h2b_verdict = "GO" if (lstbth_tracker_median_m <= 5.0 and lstbth_band90_coverage_mean >= 0.80) else "NO-GO"

print(f"n_sections: {n_sections}")
print(f"overall_tracker_median_m: {repr(overall_tracker_median_m)}")
print(f"overall_timetable_median_m: {repr(overall_timetable_median_m)}")
print(f"overall_band90_coverage_mean: {repr(overall_band90_coverage_mean)}")
print(f"lstbth_n_sections: {lstbth_n_sections}")
print(f"lstbth_tracker_median_m: {repr(lstbth_tracker_median_m)}")
print(f"lstbth_timetable_median_m: {repr(lstbth_timetable_median_m)}")
print(f"lstbth_band90_coverage_mean: {repr(lstbth_band90_coverage_mean)}")
print(f"h2b_verdict: {h2b_verdict}")
```

</details>

## Loudness verdict under the frozen rule (GO / WEAK / NO-GO)

35.9 s, attempts 2, code executed: True

| quantity | ours | Argon | match | traced | note |
|---|---|---|---|---|---|
| verdict | NO-GO | NO-GO | yes | - | exact label |
| n_positive:LAeq | 10 | 10 | yes | yes | |diff| 0 vs tol 1e-09 |
| all_trips_positive:LAeq | false | false | yes | - | exact label |
| n_positive:nospeech_LAeq | 10 | 10 | yes | yes | |diff| 0 vs tol 1e-09 |
| all_trips_positive:nospeech_LAeq | false | false | yes | - | exact label |
| n_positive:core_LAeq | 10 | 10 | yes | yes | |diff| 0 vs tol 1e-09 |
| all_trips_positive:core_LAeq | false | false | yes | - | exact label |
| n_positive:top25_LAeq | 11 | 11 | yes | yes | |diff| 0 vs tol 1e-09 |
| all_trips_positive:top25_LAeq | true | true | yes | - | exact label |
| n_positive:L10 | 11 | 11 | yes | yes | |diff| 0 vs tol 1e-09 |
| all_trips_positive:L10 | true | true | yes | - | exact label |
| n_positive:LAeq_1s_mean | 9 | 9 | yes | yes | |diff| 0 vs tol 1e-09 |
| all_trips_positive:LAeq_1s_mean | false | false | yes | - | exact label |
| n_positive:LAeq_limiter_ub | 7 | 7 | yes | yes | |diff| 0 vs tol 1e-09 |
| all_trips_positive:LAeq_limiter_ub | false | false | yes | - | exact label |

Argon's definition notes: All definitions were unambiguous and applied literally. There are 12 paired LST-BTH / BTH-MLE trips (R1+R1.5 through R12), of which 1 (R4) is placement-excluded, leaving n = 11 placement_ok trips. Since n_positive for LAeq is 10 (trip R10 has diff < 0), consistent is False and the decision rule evaluates to NO-GO.

<details><summary>Argon's executed code</summary>

```python
import pandas as pd
import numpy as np
from scipy.stats import binomtest

df = pd.read_csv('input_file_0.csv')

VARIANTS = ["LAeq", "nospeech_LAeq", "core_LAeq", "top25_LAeq", "L10", "LAeq_1s_mean", "LAeq_limiter_ub"]
MIN_DB = 1.0

# Pair trips
paired_trips = []
for trip, group in df.groupby('trip', sort=False):
    dirs = group['direction'].unique()
    if len(dirs) != 1:
        continue
    d = dirs[0]
    if d == 'EB':
        r1 = group[(group['from'] == 'LST') & (group['to'] == 'BTH')]
        r2 = group[(group['from'] == 'BTH') & (group['to'] == 'MLE')]
    elif d == 'WB':
        r1 = group[(group['from'] == 'BTH') & (group['to'] == 'LST')]
        r2 = group[(group['from'] == 'MLE') & (group['to'] == 'BTH')]
    else:
        continue
    
    if len(r1) == 1 and len(r2) == 1:
        r1_row = r1.iloc[0]
        r2_row = r2.iloc[0]
        excl = bool(r1_row['placement_excluded']) or bool(r2_row['placement_excluded'])
        diffs = {v: r1_row[v] - r2_row[v] for v in VARIANTS}
        diffs['trip'] = trip
        diffs['placement_excluded'] = excl
        paired_trips.append(diffs)

paired_df = pd.DataFrame(paired_trips)
placement_ok = paired_df[~paired_df['placement_excluded']]

n = len(placement_ok)
s = {"n": n}
for v in VARIANTS:
    vals = placement_ok[v]
    n_pos = int((vals > 0).sum())
    s[v] = {
        "n_positive": n_pos,
        "mean_db": float(vals.mean()),
        "min_db": float(vals.min()),
        "max_db": float(vals.max()),
        "sign_test_p_one_sided": float(binomtest(n_pos, n, 0.5, alternative="greater").pvalue)
    }

P = {"summary": {"placement_ok": s}}

# Run verdict logic
la = s["LAeq"]
consistent = la["n_positive"] == n
robust = {k: s[k]["n_positive"] == n for k in VARIANTS}
if consistent and la["mean_db"] >= MIN_DB and all(robust.values()):
    v_str = "GO"
elif consistent:
    v_str = "WEAK"
else:
    v_str = "NO-GO"

print(f"n = {n}")
print(f"verdict = {v_str}")
for k in VARIANTS:
    print(f"n_positive:{k} = {s[k]['n_positive']}")
    print(f"all_trips_positive:{k} = {'true' if robust[k] else 'false'}")
```

</details>
