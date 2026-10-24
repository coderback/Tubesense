# Demo run sheet (3:00 live, no slides) and the 3-minute video

This sheet is built from `docs/ARCHITECTURE.md` §7 ("Demo") and from what exists in the repo on 24 Oct.
**Every beat is labelled LIVE, REPLAY or PRECOMPUTED, and that label is said or shown on screen.**

The Gemini API key is over its spending cap, so **no Gemini call happens during the demo**. Everything
from Argon is a replay of a recorded log, and the screen says so.

Commands are PowerShell, run from the repo root. Set this once in each terminal:

```powershell
$env:PYTHONPATH = "."
```

In Git Bash, use `PYTHONPATH=. .venv/Scripts/python ...` instead.

> The flags below were checked against `--help` for `demo/ride_v2.py` and `demo/replay_analyst.py` on 24 Oct.
> Timing traps: `ride_v2.py` defaults to `--speed 1.0` (real time, about 3 min for one section plus the
> synthetic stop), so always pass `--speed 5`. `replay_analyst.py` auto-fits to about 30 s when `--speed` is
> left out. Its `--speed` is a compression factor, and `--speed 4` takes about 95 s. **Rehearse each beat once with a stopwatch.**

---

## Status on 24 Oct, and what blocks what

| Item | State | Effect on the demo |
|---|---|---|
| Atlas cluster, indexes, corpus | MongoDB 8.0.34. `docs_text`, `docs_auto` (autoEmbed voyage-4) and `yaw_vec` are all READY. 44 chunks. | 1:50 beat runs live (`demo/atlas_show.py`, tested) |
| Analyst session (Argon) | Recorded on 24 Oct 07:58–08:05 UTC (07:53 was the baseline plus a failed first session): 6 experiments, 1 accepted, 1 submitted. Hash chain intact. The Atlas `attempts` head matches the local head. | 1:15 beat is a REPLAY |
| Sealed 24 Oct test score | **Not scored yet.** `attempts.jsonl` has no `test` line, and `outputs/online/forward_test.json` has 0 sections. | Say "pending". Don't show a number. |
| Investigator output (`outputs_posthoc/investigation.json`) | **Missing.** The investigator is built but needs Gemini, and the key is capped. | Say "pending the API cap" at 1:50 |
| Approval page `agent/approve.py` | `/canary` works. **`GET /` drops the connection while `investigation.json` is missing** (FileNotFoundError; tested on a spare port). | The 2:30 beat needs the investigator run first. Fallback below. |
| Raw audio | Never played, never written to disk (privacy rule) | **No roar clip at 0:00.** Open on the number instead. |

---

## Pre-flight checklist (T-15 min)

- [ ] **Network:** phone hotspot on and the laptop joined to it. Don't rely on venue Wi-Fi.
- [ ] **.env present.** Check it without printing it:
      `Test-Path .env` should print `True`. Never `cat` it on screen.
- [ ] **Atlas reachable, and refresh the cached fallback in the same step:**
      `.venv\Scripts\python demo\atlas_show.py --snapshot`. The output should start with `LIVE from MongoDB Atlas`,
      all three indexes should show `READY True`, and it ends with `snapshot written: outputs_posthoc/atlas_snapshot.json`.
- [ ] **Offline fallback works:** `.venv\Scripts\python demo\atlas_show.py --offline` prints the `CACHED FALLBACK` banner.
- [ ] **Approval port free:** `Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue`
      should print nothing. Then start the server in its own terminal: `.venv\Scripts\python -m agent.approve`
      and open `http://127.0.0.1:8765/canary` in the browser.
- [ ] **Investigator output present?** `Test-Path outputs_posthoc\investigation.json`. If it prints `False`, use the 2:30 fallback.
- [ ] **Windows pre-opened, left to right, font ≥ 16 pt, dark terminal:**
  1. terminal A: ride replay command typed, not run;
  2. terminal B: `.venv\Scripts\python demo\replay_analyst.py` typed, no `--speed` (default ≈ 32 s; see 1:15);
  3. terminal C: `atlas_show` command typed;
  4. browser tab 1: `http://127.0.0.1:8765/canary`; tab 2: `http://127.0.0.1:8765/` (only if `investigation.json` exists);
  5. Atlas UI tab (for the video): cluster → `tubesense` → Search Indexes.
- [ ] **Backup MP4s open in a player, paused at 0:00:**
  - ride replay: `$env:USERPROFILE\Videos\tubesense\ride_v2_R5_LST-BTH.mp4`, rendered **outside the repo** so a
    `git add -A` can't pick it up (`*.mp4` is not gitignored). Run `New-Item -ItemType Directory -Force $env:USERPROFILE\Videos\tubesense`,
    then render it in advance with `--save`, see 0:35.
    The existing v1 render `outputs\online\demo_R5_LST-BTH.mp4` is the backup of the backup.
  - the full screen recording of a good rehearsal.
- [ ] Notifications off, battery > 50 %, charger in, phone silent.

---

## Beats

### 0:00–0:20 Opener: the number

| | |
|---|---|
| **Say** | "TfL's own in-cab measurement on Liverpool Street to Bethnal Green, eastbound: 87 dB(A), June 2024 (FOI-1231-2425). The Bethnal Green–Liverpool Street section appears on TfL's December 2021–May 2022 noise-grinding list, which doesn't state a direction. TfL says grinding usually has to be repeated every 6 months to a year. Press reports put it at 92.8 dB, second loudest on the network." |
| **Run** | Nothing. Show the FOI-1231-2425 hit line from the cached `atlas_show` output, or have the first screen of terminal C ready. |
| **Label** | PRECOMPUTED (public records) |
| **Fallback** | Say it without a screen. |
| **Never** | Never call 92.8 "TfL's data". It is a press report (Brixton Buzz, 27 Jan 2026) and the primary source isn't located. Never play raw audio. |

### 0:20–0:35 Named user

| | |
|---|---|
| **Say** | "This is for Elly Baker AM, who leads on transport for Labour at the London Assembly. On 16 July she said TfL had turned down most of her requests for noise data. She needs an independent, checkable section ranking and a precise data request. The people most exposed are drivers, and TfL's own figures are measured in the cab." |
| **Run** | Nothing. |
| **Label** | PRECOMPUTED (public record: MQT 2026/2156) |
| **Fallback** | None needed. |

### 0:35–1:15 Ride replay, no GPS

| | |
|---|---|
| **Say** | "A real ride, phone sensors only: no GPS, and no signal down there. The dot is the tracker's position with its 90 % band. Noise is painted only where it's confident. This tunnel stop is **synthetic**, inserted to show the stop gate holding the position." |
| **Run** | `.venv\Scripts\python demo\ride_v2.py R5 --track "EB LST->BTH" --tracker v2 --synthetic-stop 0.5 --stop-variant quiet --speed 5` (about 37 s measured: 141 s of ride plus the 30 s stop, at 5×, ending a few seconds after arrival) |
| **Label** | REPLAY of a recorded 23 Oct ride, LIVE local computation. Tracker **post-hoc v2**. Stop **SYNTHETIC 30 s** (must be on screen). |
| **Fallback** | Play `$env:USERPROFILE\Videos\tubesense\ride_v2_R5_LST-BTH.mp4`. Render it beforehand with the same command, including `--speed 5`, plus `--save $env:USERPROFILE\Videos\tubesense\ride_v2_R5_LST-BTH.mp4`. If that fails too, use `.venv\Scripts\python scripts\demo.py R5 --track "EB LST->BTH" --speed 6` (frozen v1, no stop). |
| **Honest numbers** | Errors are agreement with a yaw curve-edge proxy, not GPS truth. All are 23 Oct dev numbers, median end-of-stop error after a simulated 30 s stop. **Config on screen** (the Argon-submitted `final_config.json`, harness attempt 4): 12 m with platform-level quiet, 7 m for an idealised stop, 165 m when a real platform dwell is replayed. For comparison, frozen v1 has no stop state and gives 338 m; v2 *before* the analyst's tuning gave 23 / 11 / 165 m (`outputs_posthoc/eval_v2.json`, harness attempt 0). If you quote only one number, use the on-screen config's and name its source. |
| **QC line (optional)** | "The data is messy, and the pipeline flags that itself: a phone on the floor was excluded automatically, and two phyphox pauses were recovered from its event log" (`outputs/qc.json`). |

### 1:15–1:50 Verification loop

| | |
|---|---|
| **Say** | "Argon tuned the tracker, but it can't mark its own homework. It proposes settings from an allow-list, and a fixed harness scores them on the 23 Oct rides with a rule written before the session. Here it is: six experiments, five rejected, including one with lower error that broke calibration on one section. One is accepted, and every call is hash-chained. The one allowed score on the 24 Oct rides is still pending." |
| **Run** | `.venv\Scripts\python demo\replay_analyst.py` (default speed fits the 375 s session into ~30 s, about 32 s wall time with the Atlas check). Add `--no-atlas` if the network is slow (the Atlas check waits up to 4 s); use `--speed 20` (~19 s) if the beat is running short. **Never `--speed 4`: that takes ~94 s.** `--step` advances by keypress; `--summary` prints the one-screen table. |
| **Label** | **REPLAY of recorded session** (24 Oct 07:58–08:05 UTC, `outputs_posthoc/attempts.jsonl`). No model is called. |
| **Fallback** | `.venv\Scripts\python demo\replay_analyst.py --summary`. If that fails, `.venv\Scripts\python -m agent.harness --verify` (prints `{'lines': 8, 'broken': []}`) and scroll `outputs_posthoc\analyst_session.md`. |
| **Honest numbers** | The accepted config has a band 15.9 % narrower, and its mean per-run error changes by −0.81 m (95 % CI −1.94 to −0.05). These are 23 Oct dev numbers. Ablation: "gyro carries the position; the magnetometer adds nothing" (`outputs/positioning/ablation.json`). |

### 1:50–2:30 Investigator and MongoDB Atlas

| | |
|---|---|
| **Say** | "The investigator's evidence lives in MongoDB Atlas: 14 public sources, TfL and Assembly records plus the press report behind the 92.8 figure. One question, three ways: full-text Atlas Search, Vector Search where Atlas embeds the query itself with Voyage, and Hybrid, a `$rankFusion` that fuses both. All three put Mayor's Question 2022/1791 first: Bethnal Green to Liverpool Street is on the December 2021 to May 2022 grinding list, with no direction stated. The analyst's hash-chained log is mirrored here too, and its head matches the local chain." Then the canary: "Every claim goes through a deterministic verifier. This one is **seeded**, deliberately false: '92.8 dB is TfL's own data'. Rejected." |
| **Run** | 1. `.venv\Scripts\python demo\atlas_show.py` (about 6 s live, under 1 s with `--offline`; add `--pause 2` for the video).<br>2. Browser tab `http://127.0.0.1:8765/canary`, or in a terminal: `.venv\Scripts\python -m agent.verify --canary` |
| **Label** | Atlas queries **LIVE** (read-only). Canary verifier **LIVE**, claim **SEEDED** (labelled on the page). Investigator claims: **pending the API cap**. Say so. |
| **Fallback** | `.venv\Scripts\python demo\atlas_show.py --offline`, which prints the `CACHED FALLBACK` banner (say "cached from the live cluster this morning"). A live run that can't reach Atlas falls back to the cache by itself, with the same banner. For the canary, use the terminal `agent.verify --canary` (no network needed). |
| **If `investigation.json` exists** | Add: "Argon read the whole corpus and returned typed claims. N passed the verifier and M were dropped, listed with reasons." Take N and M from the approval page header. |
| **Optional extra query** | `.venv\Scripts\python demo\atlas_show.py "did TfL refuse to share noise data with Assembly Members"` |

### 2:30–3:00 Human approval

| | |
|---|---|
| **Say** | "Nothing goes out without a human. Every box, a name, and the verifier re-runs on approval. That creates a signed **draft** and an approvals record in Atlas. Nothing is sent automatically." |
| **Run** | Browser `http://127.0.0.1:8765/`: tick the 5 boxes, type the approver name, click **Approve as draft**. |
| **Label** | LIVE, local server plus one Atlas write (`approvals`) |
| **Precondition** | `outputs_posthoc\investigation.json` exists. Without it, `GET /` fails. |
| **Fallback (no investigation yet)** | Stay on `/canary` and say: "The approval gate is built: the checklist, the verifier re-run and a SHA-256 signature over pack, approver, time and verifier version. It renders the pack once the investigator run completes, which is pending the API cap." **Don't fake an approval.** |
| **Fallback (server down)** | Restart `.venv\Scripts\python -m agent.approve`. If the port is taken: `--port 8766`. |

**Close (if time):** "Ordinary rides, no GPS, TfL's own records, every claim checked, a human signs. Raw
audio never leaves the laptop."

---

## Live vs precomputed (one table)

| Beat | What is on screen | Live? | Source |
|---|---|---|---|
| 0:00 | 87 dB(A) in-cab figure, 92.8 "reported" | PRECOMPUTED | FOI-1231-2425; Brixton Buzz 27 Jan 2026 |
| 0:20 | Named user | PRECOMPUTED | MQT 2026/2156 |
| 0:35 | Strip map, dot, 90 % band, noise paint | LIVE compute on a REPLAY of a 23 Oct ride | `demo/ride_v2.py`, `data/raw` (read-only) |
| 0:35 | Tunnel stop | **SYNTHETIC** | `--synthetic-stop` |
| 0:35 | Tracker settings | **post-hoc v2** | `posthoc/tracker_v2.py`, `outputs_posthoc/final_config.json` |
| 1:15 | Argon proposals, harness verdicts, hashes | **REPLAY of recorded session** | `outputs_posthoc/attempts.jsonl`, `analyst_session.md` |
| 1:15 | Sealed 24 Oct score | **PENDING** (not scored) | none |
| 1:50 | Cluster, indexes, three searches, `$rankFusion` JSON, attempts mirror | LIVE Atlas (read-only); CACHED FALLBACK if offline | `demo/atlas_show.py` |
| 1:50 | Verifier rejecting the canary | LIVE verifier, **SEEDED** claim | `agent/verify.py`, `/canary` |
| 1:50 | Investigator claims | **PENDING the API cap** (or PRECOMPUTED once run) | `outputs_posthoc/investigation.json` |
| 2:30 | Approval, signature, Atlas `approvals` record | LIVE (only if the investigation exists) | `agent/approve.py` |

---

## Honest one-liners (say these, not stronger versions)

- **Levels:** "Relative levels from an uncalibrated phone, in dBFS(A). They rank sections; they are not certified dB." Liverpool Street→Bethnal Green is louder than Bethnal Green→Mile End on 5 of 5 trips, **by only +0.6 dB**. Verdict **WEAK**.
- **Positioning:** "Errors are agreement with a yaw curve-edge proxy. There is no GPS truth in a tunnel." Between curves the tracker is no better than the timetable (11 vs 16 m). Say so if asked.
- **92.8 dB:** "Reported in the press, citing TfL data. We couldn't find the primary source. TfL's own published in-cab figure is 87."
- **Stop gate:** "That stop is synthetic. We haven't logged a real tunnel stop yet."
- **Argon:** "This is a replay of a session recorded this morning. The model isn't called live: our key hit its cap, and each call takes 30–60 s anyway."
- **v2:** "Post-hoc, tuned on 23 Oct only. Its single 24 Oct score is pending."
- **Magnetometer:** "It doesn't help positioning. It's NO-GO as a fingerprint."
- **Corrugation:** "An open hypothesis, not a measurement."
- **Privacy:** "Recording conversations is 'highly intrusive' (ICO). So we keep no audio at all: 1-second levels and bands only, raw audio deleted on 1 Nov. Gemini sees public documents and summary statistics, nothing else."
- **Scope:** "Not a safety system, not a sound level meter, not a hearing-loss claim."

---

## The 3-minute video (MongoDB judges must see Atlas in use)

Record the same beats. Add these shots, each 3–5 s, during 1:50–2:30:

1. **Atlas UI → `tubesense` → Search Indexes:** `docs_text`, `docs_auto` (autoEmbed voyage-4) and `yaw_vec`, all READY.
2. **Terminal:** `.venv\Scripts\python demo\atlas_show.py --pause 2`. Hold on the `$rankFusion` JSON and the three ranked lists with `ranks text=#1 semantic=#1`.
3. **Atlas UI → Collections → `attempts`:** one document with `hash` / `prev_hash`.
4. If an approval happened, **Atlas UI → `approvals`:** the new record with its signature.

The video must show the same labels as the live demo: REPLAY, SYNTHETIC, post-hoc v2, CACHED FALLBACK if used.
Keep the backup MP4s and the video outside the repo (`$env:USERPROFILE\Videos\tubesense\`). `*.mp4` is not gitignored.
