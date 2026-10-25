# H5: absolute levels from the NIOSH SLM app (indicative)

Pre-registered in the `outputs/PLAN_24Oct.md` addendum. **Deviations:** NIOSH exported one reading per trip (Liverpool Street <-> Mile End, incl. the Bethnal Green dwell), not per section; the offset to Voice Memos is across different rides on the same evening, not the same ride.

## 1. Trip levels

| Ride | Direction | LAeq dB(A) | Max dB(A) | LCpeak dB | vs HSE 85 dB(A) | TfL in-cab sections (FOI-1231-2425) |
|---|---|---|---|---|---|---|
| N1 | EB LST->MLE | 91.9 | 109.7 | 124.4 | +6.9 dB | LST-BTH 87, BTH-MLE 83 |
| N2 | WB MLE->LST | 87.3 | 105.3 | 120.2 | +2.3 dB | MLE-BTH 82, BTH-LST 86 |

The widely quoted 92.8 dB for Liverpool Street -> Bethnal Green is secondary (no primary FOI located). A per-trip level cannot be compared with a per-section figure directly; it includes quieter platform and dwell time.

## 2. Offset NIOSH minus Voice Memos (different rides, same evening)

| NIOSH ride | Voice Memos trips (dBFS(A)) | offset median [min, max] dB |
|---|---|---|
| N1 | R7 -19.6, R9 -20.4, R11 -20.0 | 111.9 [111.5, 112.3] (n=3) |
| N2 | R8 -20.2, R10 -19.1, R12 -19.4 | 106.7 [106.4, 107.5] (n=3) |

The slope of the offset against level (the Voice Memos limiter) needs per-section readings: not computed.

**Post-hoc observation (not in the plan):** eastbound minus westbound trip level is +4.6 dB on NIOSH but -0.5 dB on Voice Memos, so the offset is not one constant. Two readings on different trains cannot separate a train or direction effect from Voice Memos compressing loud trips. On the six same-evening Voice Memos trips the limiter was engaged in 34%-55% of seconds and in 94%-100% of the loudest 10 % of seconds. Do not convert Voice Memos levels to dB(A) with a single offset.

## 3. Within-trip LST->BTH minus BTH->MLE

not possible: NIOSH exported one reading per trip.

## Limits

- iPhone built-in mic validated to ~0.5 dB(A) only up to ~85 dB (Huyan et al. 2023); these trips are above that, so readings may be low
- not a certified meter: indicative
- n = 1 trip per direction; different trains
- NIOSH trip span includes platform waits and the Bethnal Green dwell
- our phone was on a seat in the 2nd car (passenger saloon); TfL's figures are in-cab
