# Tubesense capture -> web app ingest contract

The phone app POSTs one JSON object per second (one row per request, oldest first when catching up).

```
POST http://<laptop-ip>:8770/api/live/ingest
Content-Type: application/json
```

## Body

```json
{
  "source": "tubesense-mobile",
  "session": "m-k3x9q2a7bd",
  "t": 42,
  "ts": "2026-10-25T09:41:07.512Z",
  "yaw_rate_dps": -0.412,
  "acc_h_rms": 0.1834,
  "vib_rms": 0.3121,
  "b_abs_ut": 48.27,
  "pressure_hpa": 1009.412,
  "d_pressure_10s": -0.031,
  "state": "moving",
  "knock": false,
  "rates": { "motion": 100, "mag": 20, "baro": 1 }
}
```

| field | type | meaning |
|---|---|---|
| `source` | `"tubesense-mobile"` | constant |
| `session` | string | random ID made at each Start (`m-` + 10 chars); not tied to the device or user |
| `t` | integer | seconds since Start (1, 2, 3, ...); one row per value |
| `ts` | string | ISO 8601 UTC time the second was summarised (phone clock) |
| `yaw_rate_dps` | number | mean over the second of rotation rate projected on the up vector, deg/s; **+ = left turn** (anticlockwise seen from above), as in `tubesense/features.py` |
| `acc_h_rms` | number | RMS over the second of 0.3 Hz low-passed horizontal (gravity removed) acceleration, m/s^2 |
| `vib_rms` | number | RMS over the second of \|a - 1 s mean\| (gravity-free acceleration, all axes), m/s^2. Simpler than the pipeline's 3-40 Hz band: indicative |
| `b_abs_ut` | number \| null | mean \|B\| over the second, uT. iOS gives the **calibrated** CoreMotion field (phyphox Central runs used raw). null if no samples / not yet calibrated |
| `pressure_hpa` | number \| null | latest barometer reading, hPa (iOS delivers ~1 Hz, a few s late). null if none in the last 10 s |
| `d_pressure_10s` | number \| null | latest pressure minus the reading >= 10 s older, hPa. null until 10 s of history |
| `state` | `"moving"` \| `"stopped"` | stopped = `vib_rms` < 0.17 m/s^2 for >= 3 s (min_len 3 s from `tubesense/stops.py`); handled seconds (mean \|gyro\| > 0.25 rad/s) keep the previous state; \|yaw\| >= 0.3 deg/s for >= 2 s forces moving |
| `knock` | boolean | true in the second a **double knock** group closed: two \|a\| spikes > 10 m/s^2 (gravity-free; `KNOCK_MIN` in `live/detect.py`), spikes < 0.12 s apart merged, gap 0.12-1.0 s. The group closes 1 s after its last spike, so `knock` arrives ~1-2 s after the knocks |
| `rates` | object | samples received in that second per sensor (Hz): `motion` (DeviceMotion, requested 100 Hz), `mag` (requested 20 Hz), `baro` |

Numbers are rounded (yaw 3 dp, acc/vib 4 dp, \|B\| 2 dp, pressure 3 dp). There is no audio field: the app never uses the microphone.

## Reply (optional position for the phone map)

The phone shows a map of the current section. If the 2xx reply's JSON body has a `position` object, the map uses it as the
tracker's estimate; otherwise it shows a rough time-based guess (seconds moving ÷ the section's usual run time) and says so.
The server does not send `position` today: live rides are not positioned (positioning runs on recorded rides).

```json
{ "ok": true, "seq": 42, "position": { "section": "EB LST->BTH", "s_m": 881.0, "length_m": 2270.0, "band_lo": 849.0, "band_hi": 881.0 } }
```

| field | type | meaning |
|---|---|---|
| `position.s_m` | number | metres from the section's start station (required) |
| `position.length_m` | number | section length, m (default: the app's TfL WTT length) |
| `position.band_lo`, `band_hi` | number \| null | 90 % band, m |
| `position.section` | string | `EB LST->BTH`, `EB BTH->MLE`, `WB MLE->BTH` or `WB BTH->LST`; ignored if it is not the section chosen on the phone |

## Server expectations

- Reply 2xx for an accepted row. Any non-2xx or no reply within 2.5 s counts as failed; the row stays queued and is retried next second.
- The phone keeps at most 60 unsent rows (drops the oldest), so after an outage up to 60 rows arrive back to back, in `t` order. The server may receive a duplicate if a reply is lost after the row was stored; dedupe on (`session`, `t`).
- No CORS needed: React Native `fetch` is not a browser and sends no preflight.
- No auth. Plain HTTP on the hotspot LAN only.
- **The web app must listen on the LAN** (start it with `--lan`, i.e. bind 0.0.0.0, port 8770), otherwise the phone cannot reach it. Windows may ask to allow Python/node on private networks: allow it.
