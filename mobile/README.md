# Tubesense capture (optional phone companion)

A one-screen Expo app that reads the iPhone's motion, magnetometer and barometer, reduces them to
**1-second summaries on the phone**, and POSTs each summary to the Tubesense web app on the laptop.
phyphox stays the main live input; this is a bonus.

**No microphone.** The app does not import or request any audio API. Raw samples never leave the phone.

## Run it on the iPhone (Expo Go, no account needed)

1. Install **Expo Go** from the App Store (needs the version for Expo SDK 58, the current one).
2. Turn on the iPhone's **Personal Hotspot** and join it from the laptop. The laptop's IP on the hotspot is
   usually `172.20.10.3` (check with `ipconfig`, "Wireless LAN adapter Wi-Fi", IPv4 Address).
3. Start the web app with `--lan` so the phone's POSTs can reach it (see the web app's own instructions).
4. In a terminal on the laptop:
   ```
   cd mobile
   npm install        # first time only
   npx expo start --lan
   ```
   If Windows Firewall asks whether to allow **Node.js** on private networks, click **Allow**.
5. Scan the QR code with the **iPhone camera** and tap the banner to open it in Expo Go.
6. Press **Start**. When iOS asks for **Motion & Fitness**, allow it (if you missed it: Settings >
   Privacy & Security > Motion & Fitness > Expo Go).
7. Check the laptop address (default `172.20.10.3`; it is remembered). The connection card shows sent / failed / queued.
8. Knock the phone case twice at the start and end of the ride (the "SYNC KNOCK" bar flashes).
9. Stop Metro with Ctrl+C when done (it uses RAM).

Keep Expo Go in the foreground with the screen on: the app holds the screen awake while capturing,
but iOS pauses JavaScript when the app is backgrounded or locked.

## What it sends

One JSON row per second to `http://<laptop>:8770/api/live/ingest`; see [CONTRACT.md](CONTRACT.md).
Up to 60 rows are buffered while the laptop is unreachable.

## Code

- `src/metrics.ts`: pure maths (yaw about gravity, horizontal and vibration RMS, stop state machine,
  double-knock detector, pressure change, outbox), with thresholds cited to `tubesense/stops.py`,
  `tubesense/sync.py` and `live/detect.py`.
- `App.tsx`: sensors, the per-second tick, sending, UI (design v2: a full-screen map of the section, a bottom sheet,
  sensor values in a details sheet, day and tunnel looks).
- `src/route.ts`: sections (TfL WTT lengths), the time-based position guess, the optional tracker position from the reply,
  and the generated map drawing. `src/RideMap.tsx`: the map. The buildings are generated, not real; the line is schematic.
- `test/route.test.mts`: the time guess, reply parsing and map drawing helpers.
- `test/metrics.test.mts`: synthetic checks (rotation about gravity with the phone flat, tilted and face down;
  a double knock; a still period). Run `npm test`. Type check: `npx tsc --noEmit`.

## Limits (honest)

- `vib_rms` is RMS of acceleration minus its 1 s mean, not the pipeline's 3-40 Hz band; the 0.17 m/s^2 stop
  threshold was read off phyphox runs R5/R11/R12, not tuned on this app. MOVING/STOPPED is indicative.
- iOS magnetometer values are the calibrated CoreMotion field, unlike the raw phyphox Central runs.
- DeviceMotion on iOS tops out near 100 Hz; the knock threshold (10 m/s^2) was measured on phyphox data.
