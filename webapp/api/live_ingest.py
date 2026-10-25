"""Rows from the Tubesense mobile app (prototype, mobile/): one JSON object per second, kept in memory for the Live sensors page.

What:
- POST /api/live/ingest: the app posts one row per second. Accepted only from a loopback or private-LAN client address (the
  phone reaches the server only when it runs with --lan; the hotspot is 172.20.10.0/28). No Origin is required (React
  Native sends none); a browser Origin, when present, must still be this server's own (server._read_body). The row must
  match the schema below exactly: every key present, no other key, each value of its type and range; anything else is 400.
  Schema: {source: "tubesense-mobile", session: [A-Za-z0-9_-]{1,64}, t: number s (0..172800), ts: ISO 8601 string,
  yaw_rate_dps: number, acc_h_rms: number, vib_rms: number, b_abs_ut: number|null, pressure_hpa: number|null,
  d_pressure_10s: number|null, state: "moving"|"stopped", knock: bool, rates: {motion, mag, baro: number|null}}.
- The last 300 rows per session are kept (at most 12 sessions; the least recently seen is dropped). Nothing is written to
  disk. GET /api/live/app lists the sessions; GET /api/live/app/rows?session=S&after=N returns that session's rows with
  seq > N (the page polls it once a second).
- Atlas (optional): POST /api/live/app/atlas {on: true|false} from the page (its Atlas toggle) opens a 20 s lease that the
  page renews while it follows the app; while the lease holds, each accepted row is inserted (background thread) into
  tubesense.ingest_live as {ride_id: 'app-' + 8 random hex per session (never the session string or a device name), t,
  phone_t, ts, source: 'tubesense-mobile', live: true, replay: false, yaw_rate_dps, vib_rms, acc_h_rms, state, stopped,
  knock, pressure_hpa, b_abs_ut, section: 'live capture', s_m: null, level_rel_dbA: null, confident: false} (the same
  shape live.capture writes). Egress is logged (throttled).
Why: a second way to get live phone summaries onto the page without phyphox. The app sends derived per-second values
only; there is no audio field and nothing outside the schema is stored.
"""
from __future__ import annotations

import ipaddress
import math
import queue
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone

from . import egress
from .util import ApiError, exc_text, now_iso

SOURCE = "tubesense-mobile"
KEEP_ROWS = 300
MAX_SESSIONS = 12
LEASE_S = 20.0
_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NUM = {  # key -> (min, max, nullable)
    "t": (0.0, 172800.0, False), "yaw_rate_dps": (-3000.0, 3000.0, False), "acc_h_rms": (0.0, 500.0, False),
    "vib_rms": (0.0, 500.0, False), "b_abs_ut": (0.0, 10000.0, True), "pressure_hpa": (200.0, 1200.0, True),
    "d_pressure_10s": (-100.0, 100.0, True),
}
_RATES = ("motion", "mag", "baro")
KEYS = {"source", "session", "ts", "state", "knock", "rates", *_NUM}

_lock = threading.Lock()
_sessions: "OrderedDict[str, dict]" = OrderedDict()
_seq = 0
_atlas = {"until": 0.0, "inserted": 0, "failed": 0, "last_error": None, "writer": None}


def client_ok(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return a.is_loopback or (a.is_private and not a.is_unspecified and not a.is_multicast)


def _num(body: dict, k: str) -> float | None:
    lo, hi, nullable = _NUM[k]
    v = body[k]
    if v is None and nullable:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ApiError(400, "bad_request", f"{k} must be a number from {lo:g} to {hi:g}" + (" or null" if nullable else ""))
    return float(v)


def validate(body: dict) -> dict:
    if set(body) != KEYS:
        missing, extra = sorted(KEYS - set(body)), sorted(set(body) - KEYS)
        raise ApiError(400, "bad_request", "row does not match the schema" + (f"; missing {missing}" if missing else "")
                       + (f"; not allowed {extra[:5]}" if extra else ""))
    if body["source"] != SOURCE:
        raise ApiError(400, "bad_request", f"source must be {SOURCE!r}")
    if not isinstance(body["session"], str) or not _SESSION.match(body["session"]):
        raise ApiError(400, "bad_request", "session must be 1-64 characters from A-Z a-z 0-9 _ -")
    ts = body["ts"]
    if not isinstance(ts, str) or len(ts) > 40:
        raise ApiError(400, "bad_request", "ts must be an ISO 8601 string")
    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(400, "bad_request", "ts must be an ISO 8601 string") from None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    if body["state"] not in ("moving", "stopped"):
        raise ApiError(400, "bad_request", "state must be 'moving' or 'stopped'")
    if not isinstance(body["knock"], bool):
        raise ApiError(400, "bad_request", "knock must be true or false")
    r = body["rates"]
    if not isinstance(r, dict) or set(r) != set(_RATES):
        raise ApiError(400, "bad_request", "rates must be {motion, mag, baro}")
    rates = {}
    for k in _RATES:
        v = r[k]
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 2000):
            raise ApiError(400, "bad_request", f"rates.{k} must be a number from 0 to 2000 or null")
        rates[k] = None if v is None else round(float(v), 1)
    row = {k: _num(body, k) for k in _NUM}
    row.update(session=body["session"], ts=when.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
               state=body["state"], knock=body["knock"], rates=rates)
    return row


# --------------------------------------------------------------------------- Atlas forwarding
class _Writer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="app-ingest-atlas")
        self.q: queue.Queue = queue.Queue(maxsize=600)
        self.coll = None

    def run(self):
        while True:
            doc = self.q.get()
            try:
                if self.coll is None:
                    from atlas.db import db
                    self.coll = db()["ingest_live"]
                self.coll.insert_one(doc)
                with _lock:
                    _atlas["inserted"] += 1
                egress.add_throttled("MongoDB Atlas `ingest_live`", "Tubesense mobile app rows: one document per second of "
                                     "derived motion values (yaw rate, vibration, stopped/moving, knock, pressure, |B|); "
                                     "random ride_id per session; no audio, no device name", "/api/live/ingest", every=60)
            except BaseException as e:  # noqa: BLE001  (SystemExit from atlas.db when MONGODB_URI is missing)
                self.coll = None
                with _lock:
                    _atlas["failed"] += 1
                    _atlas["last_error"] = exc_text(e, 160)


def _forward(sess: dict, row: dict) -> None:
    w = _atlas["writer"]
    if w is None:
        w = _atlas["writer"] = _Writer()
        w.start()
    doc = {"ride_id": sess["ride_id"], "t": round(row["t"] - sess["t0"], 3), "segment": 0, "phone_t": row["t"],
           "ts": datetime.now(timezone.utc), "source": SOURCE, "live": True, "replay": False,
           "yaw_rate_dps": row["yaw_rate_dps"], "vib_rms": row["vib_rms"], "acc_h_rms": row["acc_h_rms"],
           "state": row["state"], "stopped": row["state"] == "stopped", "handled": None, "knock": row["knock"],
           "pressure_hpa": row["pressure_hpa"], "b_abs_ut": row["b_abs_ut"], "section": "live capture", "s_m": None,
           "level_rel_dbA": None, "confident": False}
    try:
        w.q.put_nowait(doc)
    except queue.Full:
        with _lock:
            _atlas["failed"] += 1
            _atlas["last_error"] = "queue full (Atlas slow or unreachable): row dropped"


def set_atlas(body: dict) -> dict:
    on = body.get("on")
    if not isinstance(on, bool):
        raise ApiError(400, "bad_request", "on must be true or false")
    with _lock:
        _atlas["until"] = time.monotonic() + LEASE_S if on else 0.0
    return atlas_state()


def atlas_state() -> dict:
    with _lock:
        left = _atlas["until"] - time.monotonic()
        return {"on": left > 0, "lease_s": round(max(0.0, left), 1), "inserted": _atlas["inserted"], "failed": _atlas["failed"],
                "last_error": _atlas["last_error"]}


# --------------------------------------------------------------------------- endpoints
def ingest(body: dict, ip: str) -> dict:
    global _seq
    if not client_ok(ip):
        raise ApiError(403, "forbidden", "rows are accepted only from this machine or the local network")
    row = validate(body)
    sid = row.pop("session")
    with _lock:
        sess = _sessions.get(sid)
        if sess is None:
            while len(_sessions) >= MAX_SESSIONS:
                _sessions.popitem(last=False)
            sess = _sessions[sid] = {"session": sid, "ride_id": "app-" + secrets.token_hex(4), "rows": deque(maxlen=KEEP_ROWS),
                                     "n": 0, "first_seen": now_iso(), "t0": row["t"], "client": ip}
        _sessions.move_to_end(sid)
        _seq += 1
        row["seq"] = _seq
        sess["rows"].append(row)
        sess["n"] += 1
        sess["last_mono"] = time.monotonic()
        sess["last_seen"] = now_iso()
        forward = _atlas["until"] > time.monotonic()
    if forward:
        _forward(sess, row)
    return {"ok": True, "seq": row["seq"], "kept": len(sess["rows"]), "atlas": forward}


def _summary(s: dict) -> dict:
    last = s["rows"][-1] if s["rows"] else {}
    return {"session": s["session"], "n": s["n"], "kept": len(s["rows"]), "first_seen": s["first_seen"], "last_seen": s.get("last_seen"),
            "age_s": round(time.monotonic() - s.get("last_mono", 0.0), 1), "last_seq": last.get("seq"), "rates": last.get("rates"),
            "client_local": s["client"] in ("127.0.0.1", "::1")}


def sessions() -> dict:
    with _lock:
        items = [_summary(s) for s in reversed(_sessions.values())]
    return {"sessions": items, "keep_rows": KEEP_ROWS, "atlas": atlas_state(), "source": SOURCE}


def rows(session: str | None, after: str | None) -> dict:
    if not session or not _SESSION.match(session):
        raise ApiError(400, "bad_request", "session is required")
    try:
        a = int(after or 0)
    except ValueError:
        raise ApiError(400, "bad_request", "after must be a number") from None
    with _lock:
        s = _sessions.get(session)
        if s is None:
            raise ApiError(404, "not_found", f"no app session {session} (rows are kept in memory only)")
        out = [dict(r) for r in s["rows"] if r["seq"] > a]
        summ = _summary(s)
    return {**summ, "rows": out, "atlas": atlas_state()}
