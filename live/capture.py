"""LIVE capture: read a phone running phyphox over the local network, run the live detectors, emit JSONL events and
(optionally) insert one derived document per second into Atlas tubesense.ingest_live (the Stream Processing source).

What: polls the phone ~5 Hz (one /get per poll, incremental threshold reads), feeds live.detect.LiveDetector and
prints one JSON object per line on stdout:
  hello   {source, url, experiment, sensors, device, rates_hz, atlas, note}       once, at the first second of data
  sample  {t, state, yaw_rate_dps, vib_rms, acc_h_rms, moving_score, handled, knock, placement_change, tilt_deg,
           screen, pressure_hpa, d_pressure_hpa, dpdt_hpa_s, b_abs_ut, rates_hz, gap, ...}  one per second
  knock   {t, n, pattern: single|double|triple|burst, times, gaps_s, peak_ms2, sync_marker}
  state   {t, state: stopped|moving, from, detected_t[, dwell_s, door_step_hpa]}   t is back-dated
  placement_change {t, initial, reorient_deg, screen, tilt_deg}
  info / warning / error {kind, message}            bye {reason, n_samples, n_inserted, duration_s}
Every event also has ride_id ('live-' + 8 hex, random per capture), ts (UTC ISO 8601) and source ('live'|'mock'). A server
that calls itself MOCK in /meta or /config (live.mock_phyphox used through --url) is detected and labelled mock.
After a phyphox session change (relaunch / screen lock) the detectors restart and `segment` increases; Atlas t keeps
counting (no duplicates). warning kind 'no_data' = measuring but no new accelerometer rows for > 3 s.
Times t are phyphox experiment seconds (they freeze while phyphox is paused).
What it is NOT: it does not position the train (no map matching runs here); it shows what the phone senses now.
Privacy: phyphox audio buffers are never requested; only the derived per-second values above leave the laptop (to
Atlas, with --atlas). Raw samples stay in memory; --save-raw (default off) appends them to the gitignored
data/raw/live/<ride_id>_<sensor>.csv.
Atlas document per second (inserted from a background thread so polling never waits on the network):
  {ride_id, t (s since the first captured second, continuous across segments), segment, phone_t, ts (UTC datetime),
   source: 'live'|'mock', live: true, replay: (source == 'mock'),
   yaw_rate_dps, vib_rms, acc_h_rms, state, stopped, handled, knock, pressure_hpa, b_abs_ut,
   section: 'live capture', s_m: null, level_rel_dbA: null, confident: false}
  -> accepted by the deployed atlas/stream/processor.json (it windows level / s_m / stopped per 10 s; level and s_m are
  null for live rows); atlas/stream/processor_live.json is the PROPOSED processor that also aggregates yaw, vib,
  knocks and stopped seconds (not deployed).

Run:  PYTHONPATH=. .venv/Scripts/python.exe -m live.capture --url http://192.168.1.23 [--start] [--atlas] [--jsonl]
      PYTHONPATH=. .venv/Scripts/python.exe -m live.capture --mock R9 --seconds 60 [--speed 4] [--atlas --cleanup]
API:  run_capture(url, on_event, mock=None, atlas=False, seconds=None, stop_event=None, ...) -> summary dict;
      on_event gets each event dict (JSON-safe). Never raises for network / phone problems: they become events.
"""
from __future__ import annotations

import argparse
import json
import queue
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from live.detect import LiveDetector
from live.phyphox import Phyphox

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs_posthoc" / "live_capture"
RAW_DIR = ROOT / "data" / "raw" / "live"   # gitignored (data/raw/)
COLL = "ingest_live"
SECTION = "live capture"
NOTE = ("live sensing only: this does not position the train; rows are derived per-second values, no audio")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class _AtlasWriter(threading.Thread):
    """Background inserter: one document per sample row. A failed insert drops that row, warns once, and retries with
    backoff (5 s, doubling to 30 s) so a network outage does not end the Atlas feed; rows queued meanwhile are dropped
    beyond 300. Emits 'Atlas back' on recovery."""

    def __init__(self, emit):
        super().__init__(daemon=True)
        self.q: queue.Queue = queue.Queue()
        self.emit, self.n_ok, self.down, self.n_dropped = emit, 0, False, 0
        self.coll = None

    @property
    def failed(self) -> bool:
        return self.down

    def connect(self) -> bool:
        try:
            from atlas.db import db
            self.coll = db()[COLL]
            self.coll.database.client.admin.command("ping")
            return True
        except BaseException as e:  # atlas.db raises SystemExit for a missing URI
            self._warn(f"Atlas unavailable ({type(e).__name__}: {str(e)[:120]}); retrying in the background")
            return False

    def _warn(self, msg: str):
        if not self.down:
            self.emit({"type": "warning", "kind": "atlas", "message": msg})
        self.down = True

    def run(self):
        if self.connect():   # here, not in the caller: a slow / blocked Atlas must never delay the capture
            self.emit({"type": "info", "kind": "atlas", "message": f"Atlas connected: inserting into tubesense.{COLL}"})
        wait, next_try = 5.0, 0.0
        while True:
            doc = self.q.get()
            if doc is None:
                return
            if self.down and (time.time() < next_try or self.q.qsize() > 300):
                self.n_dropped += 1
                continue
            try:
                if self.coll is None and not self.connect():
                    raise ConnectionError("no connection")
                self.coll.insert_one(doc)
                self.n_ok += 1
                if self.down:
                    self.down, wait = False, 5.0
                    self.emit({"type": "info", "kind": "atlas",
                               "message": f"Atlas back ({self.n_dropped} rows dropped while down)"})
            except Exception as e:
                self._warn(f"Atlas insert failed ({type(e).__name__}); dropping rows and retrying")
                self.n_dropped += 1
                next_try, wait = time.time() + wait, min(wait * 2, 30.0)


def run_capture(url: str | None, on_event=None, *, mock: str | None = None, speed: float = 1.0, start_at: float = 0.0,
                atlas: bool = False, seconds: float | None = None, start: bool = False, poll_hz: float = 5.0,
                save_raw: bool = False, stop_event: threading.Event | None = None, ride_id: str | None = None) -> dict:
    """Capture until `seconds` of wall clock pass, stop_event is set, or Ctrl+C. Returns a summary dict."""
    ride_id = ride_id or "live-" + secrets.token_hex(4)
    src = "mock" if mock else "live"
    lock = threading.Lock()
    stats = {"n_samples": 0, "n_events": 0}

    def emit(ev: dict):
        ev = {**ev, "ride_id": ride_id, "source": src, "ts": ev.get("ts") or _iso(_now())}
        with lock:
            stats["n_events"] += 1
            if on_event is not None:
                try:
                    on_event(ev)
                except Exception as e:  # a broken consumer must not stop the capture
                    print(f"on_event failed: {type(e).__name__}: {e}", file=sys.stderr)

    srv = None
    if mock:
        from live.mock_phyphox import MockPhyphox
        m = MockPhyphox(mock, speed, start_at, autostart=True)
        url, srv = m.serve(0)
        label = m.label
    else:
        label = f"LIVE: phone at {url}"
    client = Phyphox(url or "")
    writer = None
    if atlas:
        writer = _AtlasWriter(emit)
        writer.start()

    t_wall0 = time.time()
    deadline = None if seconds is None else t_wall0 + seconds

    def done() -> bool:
        return (stop_event is not None and stop_event.is_set()) or (deadline is not None and time.time() >= deadline)

    reason, det, hello, first_t, last_err, meta = "seconds elapsed", LiveDetector(), None, None, None, {}
    was_measuring, fails, raw_files = None, 0, {}
    seg, t_off, last_rel, last_acc, stalled, pending = 0, 0, -1, time.time(), False, False
    try:
        # ---- connect + discover (retry: the phone may join the network late)
        while not done():
            sens = client.discover()
            if sens and not client.error:
                break
            if client.error != last_err:
                emit({"type": "error", "kind": "connect", "message": client.error})
                last_err = client.error
            time.sleep(2.0)
        else:
            reason = "stopped before the phone was reachable"
            raise StopIteration
        meta = client.meta() or {}
        cfg = client.config() or {}
        cfg_title = cfg.get("title")
        if not mock and (meta.get("mock") or str(cfg.get("category", "")).upper() == "MOCK"):
            src, label = "mock", str(meta.get("mock") or cfg_title)   # honest label before any event is emitted
        if start and not client.control("start"):
            emit({"type": "warning", "kind": "control", "message": client.error or "start refused"})
        client.prime()
        hello = {"type": "hello", "label": label, "url": client.url, "experiment": cfg_title,
                 "sensors": {k: [s.time_buf] + s.comps for k, s in client.sensors.items()},
                 "device": {k: meta.get(k) for k in ("deviceModel", "deviceBrand", "deviceRelease", "version", "build")
                            if k in meta},
                 "poll_hz": poll_hz, "atlas": writer is not None, "note": NOTE}
        hello_due = time.time() + 3.0
        period = 1.0 / poll_hz
        while not done():
            tp = time.perf_counter()
            p = client.poll()
            if p.error:
                fails += 1
                if fails in (1, 10) or fails % 50 == 0:
                    emit({"type": "warning" if fails < 10 else "error", "kind": "poll", "message": p.error,
                          "consecutive_failures": fails})
            else:
                if fails >= 10:
                    emit({"type": "warning", "kind": "poll", "message": f"phone reachable again after {fails} failed polls"})
                fails = 0
                meas = bool((p.status or {}).get("measuring"))
                if meas != was_measuring:
                    if not meas:
                        emit({"type": "warning", "kind": "not_measuring", "message":
                              "phyphox is not measuring: press play on the phone (or run with --start)"})
                    was_measuring = meas
                if p.reset:
                    emit({"type": "warning", "kind": "reset", "message": "phyphox buffers were cleared / the experiment "
                          "changed: detectors restarted"})
                    for ev in det.flush():
                        emit(ev)
                    det, first_t, seg, t_off, last_acc = LiveDetector(), None, seg + 1, last_rel + 1, time.time()
                    if not client.sensors:
                        client.discover()
                    if (p.status or {}).get("measuring") is False:
                        if start:
                            client.control("start")
                        emit({"type": "warning", "kind": "not_measuring", "message":
                              "phyphox stopped on a session change (screen lock / app switch / relaunch): press play"
                              + (" (--start re-sent)" if start else "")})
                if "acc" in p.data:
                    last_acc, stalled = time.time(), False
                elif meas and not stalled and time.time() - last_acc > 3.0:
                    stalled = True
                    emit({"type": "warning", "kind": "no_data", "message": "phyphox says measuring but no new "
                          "accelerometer samples for > 3 s (experiment time restarted, app in background, screen locked?)"})
                for key, a in p.data.items():
                    det.feed(key, a)
                    if save_raw:
                        RAW_DIR.mkdir(parents=True, exist_ok=True)
                        f = raw_files.setdefault(key, open(RAW_DIR / f"{ride_id}_{key}.csv", "a", encoding="utf-8"))
                        f.writelines(",".join(f"{v:.6f}" for v in row) + "\n" for row in a)
                for ev in det.step():
                    if ev["type"] == "sample":
                        if hello is not None:
                            emit({**hello, "rates_hz": ev.get("rates_hz")})
                            hello = None
                        first_t = ev["t"] if first_t is None else first_t
                        last_rel = ev["t"] - first_t + t_off
                        ev["segment"] = seg
                        stats["n_samples"] += 1
                        if writer is not None:
                            writer.q.put(_doc(ev, ride_id, last_rel, src))
                    emit(ev)
            if hello is not None and time.time() > hello_due:
                emit({**hello, "rates_hz": None})
                hello = None
            time.sleep(max(0.0, period - (time.perf_counter() - tp)))
        reason = "stop requested" if stop_event is not None and stop_event.is_set() else reason
    except StopIteration:
        pass
    except KeyboardInterrupt:
        reason = "interrupted"
    except Exception as e:  # never leak: report and end cleanly
        reason = f"internal error: {type(e).__name__}: {e}"
        emit({"type": "error", "kind": "internal", "message": reason})
    finally:
        for ev in det.flush():
            emit(ev)
        n_ins = 0
        if writer is not None:
            writer.q.put(None)
            writer.join(timeout=15 if not writer.down else 2)
            n_ins = writer.n_ok
            pending = writer.is_alive()
        for f in raw_files.values():
            f.close()
        if srv is not None:
            srv.shutdown()
        summary = {"type": "bye", "reason": reason, "n_samples": stats["n_samples"], "n_inserted": n_ins,
                   "duration_s": round(time.time() - t_wall0, 1), "label": label,
                   "atlas_pending": pending}
        emit(summary)
    return {**summary, "ride_id": ride_id}


def _doc(ev: dict, ride_id: str, t_rel: int, src: str = "live") -> dict:
    """The Atlas ingest_live document for one sample row (derived values only)."""
    return {"ride_id": ride_id, "t": t_rel, "segment": ev.get("segment", 0), "phone_t": ev["t"], "ts": _now(),
            "source": src, "live": True, "replay": src == "mock", "yaw_rate_dps": ev.get("yaw_rate_dps"), "vib_rms": ev.get("vib_rms"),
            "acc_h_rms": ev.get("acc_h_rms"), "state": ev.get("state"), "stopped": ev.get("state") == "stopped",
            "handled": ev.get("handled"), "knock": bool(ev.get("knock")), "pressure_hpa": ev.get("pressure_hpa"),
            "b_abs_ut": ev.get("b_abs_ut"), "section": SECTION, "s_m": None, "level_rel_dbA": None,
            "confident": False}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="the address phyphox shows after 'Allow remote access', e.g. http://192.168.1.23")
    ap.add_argument("--mock", metavar="RUN", help="no phone: replay a recorded ride (e.g. R9) or 'synthetic' through "
                                                  "a local phyphox-compatible server (labelled MOCK)")
    ap.add_argument("--speed", type=float, default=1.0, help="mock replay speed")
    ap.add_argument("--start-at", type=float, default=0.0, help="mock: start this many s into the ride")
    ap.add_argument("--start", action="store_true", help="send /control?cmd=start (else press play on the phone)")
    ap.add_argument("--atlas", action="store_true", help="insert each per-second row into tubesense.ingest_live")
    ap.add_argument("--cleanup", action="store_true", help="with --atlas: delete this ride_id's documents at the end")
    ap.add_argument("--jsonl", nargs="?", const="", default=None,
                    help="also write the events to this file (default outputs_posthoc/live_capture/<ride_id>.jsonl)")
    ap.add_argument("--seconds", type=float, default=None, help="stop after this many s of wall clock")
    ap.add_argument("--poll-hz", type=float, default=5.0)
    ap.add_argument("--save-raw", action="store_true", help="append raw samples to gitignored data/raw/live/ (off)")
    ap.add_argument("--quiet", action="store_true", help="stdout: only non-sample events (samples still go to --jsonl)")
    a = ap.parse_args()
    if not a.url and not a.mock:
        ap.error("give --url (the phone) or --mock RUN")
    ride_id = "live-" + secrets.token_hex(4)
    fh = None
    if a.jsonl is not None:
        path = Path(a.jsonl) if a.jsonl else OUT / f"{ride_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "w", encoding="utf-8")
        print(f"writing events to {path}", file=sys.stderr)

    def show(ev):
        line = json.dumps(ev, ensure_ascii=False)
        if not (a.quiet and ev["type"] == "sample"):
            print(line, flush=True)
        if fh:
            fh.write(line + "\n")
            fh.flush()

    s = run_capture(a.url, show, mock=a.mock, speed=a.speed, start_at=a.start_at, atlas=a.atlas, seconds=a.seconds,
                    start=a.start, poll_hz=a.poll_hz, save_raw=a.save_raw, ride_id=ride_id)
    if fh:
        fh.close()
    if a.atlas and a.cleanup:
        try:
            from atlas.db import db
            n = db()[COLL].delete_many({"ride_id": s["ride_id"]}).deleted_count
            for _ in range(3 if s.get("atlas_pending") else 0):   # writer still draining: delete again after a wait
                time.sleep(5)
                n += db()[COLL].delete_many({"ride_id": s["ride_id"]}).deleted_count
            print(f"cleanup: deleted {n} documents with ride_id {s['ride_id']} from {COLL}", file=sys.stderr)
        except BaseException as e:
            print(f"cleanup failed: {type(e).__name__}", file=sys.stderr)


if __name__ == "__main__":
    main()
