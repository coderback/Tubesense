"""Live sensors: run live.capture as a heavy job (phone running phyphox, or a labelled MOCK replay) and probe a phone.

What:
- POST /api/live/start {url | mock, atlas, seconds, speed} starts `python -B -m live.capture (--url URL | --mock RUN
  --speed S) --seconds N --jsonl [--atlas]` as the heavy job kind `live`; its JSON lines (hello, sample, knock, state,
  placement_change, warning, info, error, bye) are relayed as SSE by the job manager. One heavy job at a time (409
  `busy` or `replace`), cancel = kill, orphan-cancelled after 60 s with no subscriber (like ride and stream).
- POST /api/live/stop cancels the running live job (a kill: no `bye` event, the job_end says cancelled).
- GET /api/live/probe?url=... asks the phone itself, in this process, with 2 s timeouts (live.phyphox.Phyphox): the
  experiment's sensor buffers, /meta, whether it is measuring, and the sample rates over ~1 s. On failure it returns
  the client's reason plus plain network advice (refused = phone there but phyphox not serving; timeout = not on the
  same network).
URL rule (the server fetches whatever URL it is given, so it is restricted): http only, no user info, path empty or
"/", no query or fragment, host an IP literal that is private / loopback / link-local (not unspecified, multicast or
reserved) or 'localhost', optional port. mock is a run id (R0-R15, R1.5) or 'synthetic', and excludes url.
Why: the page shows what the phone senses now; positioning runs on recorded rides (Ride page). No microphone is ever
read (live.capture never polls audio buffers). Raw samples stay in the capture process's memory; the per-second
summaries go to the browser and, only with atlas = true, to Atlas tubesense.ingest_live (egress logged).
Test mode (TUBESENSE_POSTHOC_OUT set): the events file goes under that folder and --atlas also gets --cleanup (the
capture deletes its own ingest_live rows at the end). The owner's normal server never adds --cleanup.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
import urllib.parse
from datetime import datetime, timezone

from . import egress
from .util import OUT_DIR, POSTHOC, ApiError, has_module

DEFAULT_URL = "http://172.20.10.1"
SECONDS_DEFAULT, SECONDS_MIN, SECONDS_MAX = 120, 5, 600
_MOCK = re.compile(r"^(R(1[0-5]|[0-9])|R1\.5|synthetic)$")
TEST_MODE = OUT_DIR != POSTHOC


def validate_url(url) -> str:
    """Return the normalised http://host[:port] or raise 400. See the module docstring for the rule."""
    if not isinstance(url, str) or not url.strip():
        raise ApiError(400, "bad_request", "url must be a non-empty string like http://172.20.10.1")
    url = url.strip()
    if len(url) > 100 or re.search(r"[\s\\]", url):
        raise ApiError(400, "bad_request", "url too long or contains spaces / backslashes")
    if "://" not in url:
        url = "http://" + url
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        raise ApiError(400, "bad_request", "url is not a valid http://host[:port] address") from None
    if u.scheme.lower() != "http":
        raise ApiError(400, "bad_request", "only http:// is allowed (phyphox serves plain http on the local network)")
    if u.username is not None or u.password is not None or "@" in u.netloc:
        raise ApiError(400, "bad_request", "url must not contain user info")
    if u.path not in ("", "/") or u.query or u.fragment:
        raise ApiError(400, "bad_request", "url must be just http://host[:port] (no path, query or fragment)")
    host = (u.hostname or "").lower()
    if host != "localhost":
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            raise ApiError(400, "bad_request", "host must be an IP address on the local network (or localhost), "
                           "not a domain name") from None
        if ip.is_unspecified or ip.is_multicast or ip.is_reserved or not (ip.is_private or ip.is_loopback or ip.is_link_local):
            raise ApiError(400, "bad_request", f"{host} is not a private / LAN address (phone hotspot 172.20.10.x, "
                           "home Wi-Fi 192.168.x.x or 10.x.x.x)")
        host = f"[{ip}]" if ip.version == 6 else str(ip)
    return f"http://{host}" + (f":{port}" if port is not None else "")


def _bool(body: dict, key: str, default: bool) -> bool:
    v = body.get(key, default)
    if not isinstance(v, bool):
        raise ApiError(400, "bad_request", f"{key} must be true or false")
    return v


def validate_start(body: dict) -> dict:
    mock, url = body.get("mock"), body.get("url")
    if mock not in (None, "") and url not in (None, ""):
        raise ApiError(400, "bad_request", "give url (the phone) or mock (a recorded ride), not both")
    p: dict = {}
    if mock not in (None, ""):
        if not isinstance(mock, str) or not _MOCK.match(mock):
            raise ApiError(400, "bad_request", "mock must be a run id R0-R15 or R1.5, or 'synthetic'")
        speed = body.get("speed", 1)
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 1 <= speed <= 8:
            raise ApiError(400, "bad_request", "speed must be 1 to 8 (mock replay speed)")
        p.update(mock=mock, url=None, speed=speed)
    else:
        p.update(mock=None, url=validate_url(url if url not in (None, "") else DEFAULT_URL), speed=None)
    secs = body.get("seconds", SECONDS_DEFAULT)
    if isinstance(secs, bool) or not isinstance(secs, (int, float)) or not SECONDS_MIN <= secs <= SECONDS_MAX:
        raise ApiError(400, "bad_request", f"seconds must be {SECONDS_MIN} to {SECONDS_MAX}")
    p.update(seconds=int(secs), atlas=_bool(body, "atlas", False), replace=_bool(body, "replace", False))
    return p


def start(jobs, body: dict):
    p = validate_start(body)
    if not has_module("live.capture"):
        raise ApiError(503, "unavailable", "live.capture is not installed")
    args = (["--mock", p["mock"], "--speed", f"{p['speed']:g}"] if p["mock"] else ["--url", p["url"]]) + ["--seconds", str(p["seconds"])]
    if TEST_MODE:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        args += ["--jsonl", str(OUT_DIR / "live_capture" / f"web_{stamp}.jsonl")]
    else:
        args += ["--jsonl"]          # outputs_posthoc/live_capture/<ride_id>.jsonl (derived per-second events only)
    if p["atlas"]:
        args += ["--atlas"] + (["--cleanup"] if TEST_MODE else [])
    what = f"MOCK replay of {p['mock']} at {p['speed']:g}x" if p["mock"] else f"phone at {p['url']}"
    label = f"live sensors: {what}, {p['seconds']} s" + (" → Atlas ingest_live" if p["atlas"] else "")
    params = {k: p[k] for k in ("url", "mock", "speed", "seconds", "atlas")} | {"cleanup": bool(p["atlas"] and TEST_MODE)}
    job = jobs.start("live", "live.capture", args, params, label, replace=p["replace"])
    if p["atlas"]:
        egress.add("MongoDB Atlas `ingest_live`", "one document per second of derived motion values (yaw rate, vibration "
                   "RMS, stopped/moving, knock flag, pressure, |B|; section 'live capture', no position, no level); "
                   "no audio, no raw samples" + (" (test mode: deleted at the end)" if TEST_MODE else ""), "/api/live/start")
    return job


def stop(jobs) -> dict:
    j = jobs.live(("live",))
    if j is None:
        return {"stopped": False, "message": "no live capture is running"}
    return {"stopped": True, "job": jobs.cancel(j.id)}


# --------------------------------------------------------------------------- probe
def _laptop_ip(host: str) -> str | None:
    """The local address the OS would use to reach `host` (a UDP connect sends no packet)."""
    try:
        h = host.strip("[]")
        fam = socket.AF_INET6 if ":" in h else socket.AF_INET
        with socket.socket(fam, socket.SOCK_DGRAM) as s:
            s.connect((h if h != "localhost" else "127.0.0.1", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def _advice(err: str, host: str, laptop: str | None) -> list[str]:
    e = (err or "").lower()
    out = []
    if "refused" in e or "10061" in e:
        out.append("The address answered but nothing is serving there: open phyphox, turn on Allow remote access "
                   "(menu, top right) and keep the screen awake; use the exact address phyphox shows (iOS port 80, Android 8080).")
    elif "timed out" in e or "timeout" in e or "unreachable" in e or "no route" in e or "10060" in e or "10065" in e:
        out.append("No answer from that address: the laptop and the phone are probably not on the same network. "
                   "Venue Wi-Fi often isolates devices: put the laptop on the phone's hotspot.")
    elif "phyphox json" in e or "http " in e:
        out.append("Something answered, but it is not phyphox: check the address phyphox shows at the bottom of its screen.")
    elif "accelerometer + gyroscope" in e:
        out.append("phyphox is reachable but the open experiment has no accelerometer and gyroscope: open the experiment "
                   "the rides used (Accelerometer, Gyroscope, Magnetometer, Pressure).")
    if laptop and host.count(".") == 3 and laptop.count(".") == 3 and laptop.rsplit(".", 1)[0] != host.rsplit(".", 1)[0]:
        out.append(f"This laptop's address towards {host} is {laptop}: a different subnet, so they are not on the same local network.")
    out.append("Keep phyphox in the foreground: on iPhone, locking the screen or switching apps stops the remote server.")
    return out


def probe(url) -> dict:
    url = validate_url(url if url not in (None, "") else DEFAULT_URL)
    if not has_module("live.phyphox"):
        raise ApiError(503, "unavailable", "live.phyphox is not installed")
    from live.phyphox import Phyphox      # numpy only (allowed in the server process)
    host = urllib.parse.urlsplit(url).hostname or ""
    laptop = _laptop_ip(host)
    t0 = time.monotonic()
    ph = Phyphox(url, timeout=2.0)
    sens = ph.discover()
    base = {"url": url, "laptop_ip": laptop, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if not sens or ph.error:
        err = ph.error or "no sensors found"
        return {**base, "ok": False, "reachable": bool(sens) or "not phyphox" in err or "accelerometer" in err,
                "error": err, "advice": _advice(err, host, laptop),
                "sensors": {k: [s.time_buf] + s.comps for k, s in sens.items()}, "seconds": round(time.monotonic() - t0, 2)}
    meta = ph.meta() or {}
    cfg_title = None
    is_mock = bool(meta.get("mock"))
    rates, measuring, session = {}, None, None
    if ph.prime():
        session = ph.session
        t1 = time.monotonic()
        time.sleep(1.0)
        p = ph.poll()
        span = time.monotonic() - t1
        measuring = (p.status or {}).get("measuring") if not p.error else None
        for k, a in p.data.items():
            if len(a) >= 2:
                rates[k] = round((len(a) - 1) / max(1e-6, float(a[-1, 0] - a[0, 0])), 1)
            else:
                rates[k] = round(len(a) / span, 1)
        for k in ph.sensors:
            rates.setdefault(k, 0.0)
    try:
        cfg_title = (ph.config() or {}).get("title")
    except Exception:  # noqa: BLE001  (never raises by design; belt and braces)
        cfg_title = None
    warn = []
    if measuring is False:
        warn.append("phyphox is not measuring: press play on the phone before starting.")
    elif measuring and not any(rates.get(k) for k in ("acc", "gyr")):
        warn.append("phyphox says measuring but sent no new motion samples in 1 s (screen locked or app in background?).")
    return {**base, "ok": True, "reachable": True, "error": None, "mock": is_mock, "experiment": cfg_title,
            "sensors": {k: [s.time_buf] + s.comps for k, s in ph.sensors.items()},
            "device": {k: meta.get(k) for k in ("deviceModel", "deviceBrand", "deviceRelease", "version", "build") if k in meta},
            "measuring": measuring, "session": session, "rates_hz": rates, "warnings": warn,
            "advice": [] if not warn else ["Keep phyphox in the foreground with the screen awake."],
            "seconds": round(time.monotonic() - t0, 2)}
