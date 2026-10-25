"""Read the stream processor's 10 s windows back from tubesense.live_view, for the web app.

What: latest(ride_id=None, limit=30) returns the newest windows the Atlas stream processor live_windows
(atlas/stream/processor.json) $merged into live_view, renamed to the web app's contract:
  {"available", "reason", "processor", "processor_state", "age_s", "windows": [{ride_id, window_start,
   window_end, n, level_mean, level_max, s_m_min, s_m_max, section, n_confident, n_stopped, replay}],
   "history": [same shape]}
A window is 10 s of replay WALL CLOCK at insert time (the processor's event time is the insert time), so it
holds about 10 x replay-speed recorded seconds (x4: ~40 s); n is the number of recorded seconds in it, not a
sample rate. Label windows that way.
processor is the processor name ("live_windows"); processor_state is its Atlas state ("STARTED",
"NOT_CREATED", ...) or null when unknown. n_stopped counts seconds with the stop gate engaged.
Only documents the processor wrote are returned (they carry processor: "live_windows"); nothing here
computes a window. available means LIVE and is False, with the reason, when Atlas is unreachable, when
live_view has no processor windows (for that ride), when the processor is known not to be running, or when
the newest window ended more than FRESH_S s ago (age_s), whatever the processor state. "windows" holds the
windows only when available is True (otherwise it is empty); older windows are returned under "history",
never as live: render them labelled "history, not live". The processor state comes from one Atlas CLI call (timeout
CLI_TIMEOUT s, one at a time, cached for STATE_TTL s); if the CLI is missing or logged out it is unknown.
Levels are relative phone levels, dBFS(A), uncalibrated; windows come from a REPLAY when replay is true.

Run:  PYTHONPATH=. .venv/Scripts/python.exe -m atlas.live_view [ride_id] [--limit 30]
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from datetime import datetime, timezone

PROC = "live_windows"
STATE_TTL = 20.0                         # s between CLI state checks (each call takes ~1-3 s)
CLI_TIMEOUT = 8                          # s; a hung atlas.exe must not pile up behind the web app's 2 s poll
FRESH_S = 90.0                           # a window older than this (window_end vs now, UTC) is history, not live
_state = {"t": -1e9, "value": None, "why": None}
_lock = threading.Lock()


def processor_state() -> tuple[str | None, str | None]:
    """(state, None) e.g. ('STARTED', None), or (None, why) when it cannot be checked cheaply."""
    if os.environ.get("TUBESENSE_SP_STATE_CHECK", "1") == "0":
        return None, "processor state unknown (check disabled)"
    if time.monotonic() - _state["t"] < STATE_TTL:
        return _state["value"], _state["why"] or (None if _state["value"] else "processor state unknown (check in progress)")
    if not _lock.acquire(blocking=False):           # a check is already in flight: answer from the cache
        return _state["value"], _state["why"] or "processor state unknown (check in progress)"
    try:
        _state["t"] = time.monotonic()                # stamp first: a slow or failing call is not retried per poll
        from atlas import stream_setup as SS
        try:
            st, why = SS.proc_state(timeout=CLI_TIMEOUT), None
            if st is None:
                st = "NOT_CREATED"
        except Exception as e:                        # CliError, timeout, missing CLI
            st, why = None, f"processor state unknown ({str(e)[:120]})"
        _state.update(t=time.monotonic(), value=st, why=why)
        return st, why
    finally:
        _lock.release()


def _iso(d):
    return d.isoformat().replace("+00:00", "") + "Z" if d is not None and hasattr(d, "isoformat") else d


def _window(d: dict) -> dict:
    secs = [s for s in (d.get("sections") or []) if s]
    return {"ride_id": d.get("ride_id"), "window_start": _iso(d.get("window_start")),
            "window_end": _iso(d.get("window_end")), "n": d.get("n"),
            "level_mean": None if d.get("mean_dbA") is None else round(d["mean_dbA"], 2),
            "level_max": d.get("max_dbA"), "s_m_min": d.get("s_min"), "s_m_max": d.get("s_max"),
            "section": " + ".join(sorted(secs)) if secs else None,
            "n_confident": d.get("n_confident"), "n_stopped": d.get("n_stopped"), "replay": d.get("replay")}


def _age_s(docs) -> float | None:
    """Seconds since the newest window ended (UTC wall clock), or None if no window has a window_end."""
    ends = [d["window_end"] for d in docs if isinstance(d.get("window_end"), datetime)]
    if not ends:
        return None
    end = max(ends)
    end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end
    return (datetime.now(timezone.utc) - end).total_seconds()


def latest(ride_id: str | None = None, limit: int = 30) -> dict:
    out = {"available": False, "reason": None, "processor": PROC, "processor_state": None, "age_s": None,
           "windows": [], "history": []}
    try:
        from atlas.db import client
        c = client(timeout_ms=4000)
        q = {"processor": PROC, **({"ride_id": ride_id} if ride_id else {})}
        docs = list(c["tubesense"]["live_view"].find(q).sort("window_start", -1).limit(int(limit)))
    except BaseException as e:            # SystemExit from atlas.db when .env is missing, network errors
        if isinstance(e, KeyboardInterrupt):
            raise
        out["reason"] = f"Atlas unreachable ({type(e).__name__})"
        return out
    wins = [_window(d) for d in docs]
    state, why = processor_state()
    out["processor_state"] = state
    age = _age_s(docs)
    out["age_s"] = None if age is None else round(age, 1)
    if state is not None and state != "STARTED":
        out["reason"] = f"processor {PROC} is {state}" + (": the windows in history are from an earlier run" if docs else "")
    elif not docs:
        out["reason"] = ("no processor windows in live_view" + (f" for {ride_id}" if ride_id else "")
                         + (" yet (a window closes 10 s after it opens, the last one ~10 s after the replay ends)"
                            if state == "STARTED" else "") + (f"; {why}" if why else ""))
    elif age is None or age > FRESH_S:
        known = f"{PROC} is {state}" if state else (why or "processor state unknown")
        howold = "of unknown age" if age is None else (f"{age / 60:.0f} min old" if age >= 120 else f"{age:.0f} s old")
        out["reason"] = f"latest processor window is {howold} ({known}): shown as history, not live"
    else:
        out["available"], out["reason"] = True, why
    out["windows" if out["available"] else "history"] = wins
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ride_id", nargs="?", default=None)
    ap.add_argument("--limit", type=int, default=30)
    a = ap.parse_args()
    print(json.dumps(latest(a.ride_id, a.limit), indent=1, default=str))


if __name__ == "__main__":
    main()
