"""In-memory log of what this server sent to network services during this session (never written to disk).

The Privacy & method screen shows it (GET /api/egress). Only the endpoints that call out add entries;
the server makes no other outbound calls. Entries describe what was sent in words, not the payload.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from .util import now_iso

MAX = 200
SINCE = now_iso()
_lock = threading.Lock()
_entries: deque[dict] = deque(maxlen=MAX)
_last: dict[str, float] = {}


def add(service: str, sent: str, endpoint: str) -> None:
    with _lock:
        _entries.appendleft({"ts": now_iso(), "service": service, "sent": sent, "endpoint": endpoint})


def add_throttled(service: str, sent: str, endpoint: str, every: float = 60.0) -> None:
    """Like add(), but at most once per `every` seconds for the same endpoint (polled endpoints)."""
    with _lock:
        last = _last.get(endpoint, -1e9)
        if time.monotonic() - last < every:
            return
        _last[endpoint] = time.monotonic()
    add(service, sent, endpoint)


def snapshot() -> dict:
    with _lock:
        return {"since": SINCE, "entries": list(_entries)}
