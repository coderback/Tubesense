"""Tubesense web app server: static files, JSON API and server-sent events, standard library only.

Run (repo root):
  PYTHONPATH=. .venv/Scripts/python.exe -m webapp.server [--port 8770] [--lan] [--lan-approve]
  -> Tubesense web app: http://127.0.0.1:8770/  (local only; --lan to open to the network)

What: serves webapp/static, answers light requests itself (pack and approval, recorded analyst session, Atlas
search, method, egress log) and runs heavy work as separate Python processes (ride replay, live analyst, stream
replay, Argon ask) whose JSON-lines output it relays as SSE (webapp/api/jobs.py). One heavy job and one ask at a
time. This process imports no pandas, scipy, matplotlib or tubesense.* (it warns at start if one slipped in).
Why: the trackers set module globals so v1 and v2 cannot share a process, a job's memory returns to the OS when it
ends, and cancel is a kill. The API is specified in webapp/DESIGN.md section 6; where this file decided something
the design left open, it is listed below.
Decisions beyond the design:
- Host header must be loopback (127.0.0.1, localhost, [::1]); with --lan also an IP literal or this machine's own name, never a
  domain name (DNS-rebinding guard, host_ok); approve/reject also refuse a foreign Origin; POSTs with an
  Origin header need Origin host == Host.
- A POST with an empty body counts as {}; a non-empty body must be application/json, at most 16 KB.
- HTTP/1.1 keep-alive for files and JSON; SSE responses and refused POSTs close the connection.
- POST /api/stream/start also accepts "dry_run": true (tracker only, no Atlas writes) for tests.
- /api/analyst/session: `checked_at` is when the checks were really computed (cached up to 30 s, `cached_age_s`);
  ride events carry `seq` in the JSON as well as in the SSE `id:` line.
- Ride `init` adds qc.this_ride_items [{text, severity}] next to the plain lines.
- GET /api/ask/cached/{name} returns agent.ask.replay(name) (CACHED, re-verified and re-gated now), never the raw file.
- "Argon at work" (webapp/api/work_api.py): GET /api/work/roughness (+ /file/<allow-listed png>), POST .../roughness/recheck
  (agent.roughness --cached, no Gemini); GET /api/work/recheck, POST .../recheck/start (agent.recheck --jsonl, live) and
  .../recheck/canary (--canary, plain text relayed as log events); GET /api/work/reports, /api/work/reports/<run>, POST
  .../reports/start (agent.ride_report <run> --jsonl, whose `event` key is relayed as `type`). These jobs are heavy
  (one at a time with rides, analyst and stream); in test mode (TUBESENSE_POSTHOC_OUT set) they get --no-atlas.
  No ride-report approval in the web app (CLI only).
- Live sensors (webapp/api/live_api.py): POST /api/live/start {url | mock, atlas, seconds, speed} runs `live.capture ... --jsonl`
  as the heavy job kind `live` (orphan-cancelled like ride and stream); POST /api/live/stop kills it; GET /api/live/probe?url=
  asks the phone in this process (2 s timeouts). The phone URL must be http://<private/LAN IP literal or localhost>[:port]
  with no path. In test mode the events file goes under TUBESENSE_POSTHOC_OUT and --atlas also gets --cleanup.
- Mobile app rows (webapp/api/live_ingest.py): POST /api/live/ingest accepts one schema-checked row per second from a loopback or
  private-LAN client only (no Origin needed: React Native sends none; a foreign browser Origin is still refused); kept in
  memory (300 rows per session); GET /api/live/app, GET /api/live/app/rows?session=&after=; POST /api/live/app/atlas {on}
  opens a 20 s lease during which rows are also inserted into Atlas ingest_live with a random ride_id per session.
Approval: only POST /api/pack/approve from a loopback client (or any client with --lan-approve) and only with a body
{pack_id, approver, checked[]}. Nothing in this server approves by itself.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True      # frozen packages must never get .pyc files from this process

import argparse
import atexit
import ipaddress
import json
import mimetypes
import re
import socket
import sys
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .api import egress
from .api.jobs import JobManager
from .api.util import OUT_DIR, POSTHOC, ApiError, exc_text, has_module, have_env, now_iso, rel

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 16 * 1024
LOOPBACK = ("127.0.0.1", "localhost", "::1")
MIME = {".webmanifest": "application/manifest+json", ".js": "text/javascript", ".mjs": "text/javascript",
        ".jsonl": "application/x-ndjson", ".json": "application/json", ".svg": "image/svg+xml", ".css": "text/css",
        ".html": "text/html; charset=utf-8", ".webp": "image/webp", ".woff2": "font/woff2"}
STATE = {"port": 8770, "lan": False, "lan_approve": False, "verbose": False}
JM = JobManager()


def host_ok(netloc: str) -> bool:
    """Is this Host (or Origin) header value one of ours? Loopback by default; with --lan also IP literals and this machine's
    own name (`name`, `name.local`). A DNS-rebinding page reaches the server under its own domain name, which is never accepted."""
    try:
        u = urllib.parse.urlsplit("//" + (netloc or "").strip().lower())
        name, port = u.hostname, u.port
    except ValueError:
        return False
    if not name or (port is not None and port != STATE["port"]):
        return False
    if name in LOOPBACK:
        return True
    if not STATE["lan"]:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        me = socket.gethostname().lower()
        return name in (me, me + ".local")


# --------------------------------------------------------------------------- response types
class Raw:
    def __init__(self, body: bytes, ctype: str, status: int = 200):
        self.body, self.ctype, self.status = body, ctype, status


class FileResp:
    def __init__(self, path: Path, ctype: str | None = None, cache: str = "no-cache"):
        self.path, self.ctype, self.cache = path, ctype, cache


class Sse:
    def __init__(self, job, after: int):
        self.job, self.after = job, after


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    return o


def dumps(obj) -> bytes:
    try:
        return json.dumps(obj, ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")
    except ValueError:
        return json.dumps(_clean(obj), ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")


# --------------------------------------------------------------------------- endpoints
class Ctx:
    def __init__(self, h: "Handler", m: re.Match, query: dict, body: dict):
        self.h, self.m, self.q, self.body = h, m, query, body

    @property
    def ip(self) -> str:
        return self.h.client_address[0]


def health(c: Ctx):
    return {"ok": True, "time": now_iso(), "server": "tubesense-webapp", "port": STATE["port"], "lan": STATE["lan"],
            "lan_approve": STATE["lan_approve"], "posthoc_out": rel(OUT_DIR), "posthoc_out_is_default": OUT_DIR == POSTHOC,
            "approval_mirror": OUT_DIR == POSTHOC,
            "env": {"gemini_api_key": have_env("GEMINI_API_KEY"), "mongodb_uri": have_env("MONGODB_URI")},
            "modules": {m: has_module(m) for m in ("agent.ask", "agent.live_analyst", "atlas.live_view", "atlas.stream_replay",
                                                    "agent.roughness", "agent.recheck", "agent.ride_report",
                                                    "live.capture", "live.phyphox")},
            "files": {"investigation": (OUT_DIR / "investigation.json").exists(),
                      "atlas_snapshot": (POSTHOC / "atlas_snapshot.json").exists(),
                      "model_comparison_json": (POSTHOC / "model_comparison.json").exists(),
                      "model_comparison_md": (POSTHOC / "model_comparison.md").exists(),
                      "analyst_session": (POSTHOC / "analyst_session.jsonl").exists()},
            "heavy_job": JM.heavy_running()}


def egress_log(c: Ctx):
    return egress.snapshot()


def method(c: Ctx):
    from .api import method_api
    return method_api.method()


def rides(c: Ctx):
    from .api import rides as R
    return R.catalogue(refresh=c.q.get("refresh") == "1")


def ride_start(c: Ctx):
    from .api import rides as R
    p = R.validate_start(c.body)
    job = JM.start("ride", "webapp.api.ride_worker", R.worker_args(p), p, R.ride_label(p), control=True, replace=p["replace"])
    return 202, job.to_dict()


def ride_precomputed(c: Ctx):
    from .api import rides as R
    name = c.q.get("file")
    if name is None:
        return R.precomputed_list()
    return FileResp(R.precomputed_file(name), "application/x-ndjson")


def jobs_list(c: Ctx):
    return {"jobs": JM.list()}


def job_get(c: Ctx):
    return JM.get(c.m["id"]).to_dict()


def job_events(c: Ctx):
    job = JM.get(c.m["id"])
    last = c.h.headers.get("Last-Event-ID") or c.q.get("after") or "0"
    return Sse(job, int(last) if last.isdigit() else 0)


def job_go(c: Ctx):
    return JM.go(c.m["id"])


def job_control(c: Ctx):
    return JM.control(c.m["id"], c.body)


def job_cancel(c: Ctx):
    return JM.cancel(c.m["id"])


def analyst_session(c: Ctx):
    from .api import analyst_api
    return analyst_api.session(atlas=c.q.get("atlas") == "1")


def analyst_live_start(c: Ctx):
    from .api import analyst_api
    return 202, analyst_api.live_start(JM, c.body).to_dict()


def ask_status(c: Ctx):
    from .api import ask_api
    return ask_api.status()


def ask_start(c: Ctx):
    from .api import ask_api
    return 202, ask_api.start(JM, c.body).to_dict()


def ask_cached(c: Ctx):
    from .api import ask_api
    return ask_api.cached_list()


def ask_cached_file(c: Ctx):
    from .api import ask_api
    return ask_api.cached_file(c.m["name"])


def pack_get(c: Ctx):
    from .api import pack_api
    return pack_api.pack()


def pack_history(c: Ctx):
    from .api import pack_api
    return pack_api.history()


def source(c: Ctx):
    from .api import pack_api
    return pack_api.source(urllib.parse.unquote(c.m["chunk"]), c.q.get("quote"))


def canary(c: Ctx):
    from .api import pack_api
    return pack_api.canary()


def _own_origin(c: Ctx) -> None:
    """Approving or rejecting is the human's action: a browser Origin, when one is sent, must be this server's own."""
    o = c.h.headers.get("Origin")
    if o is not None and not host_ok(urllib.parse.urlsplit(o).netloc):
        raise ApiError(403, "forbidden", "approval from another origin refused")


def pack_approve(c: Ctx):
    from .api import pack_api
    _own_origin(c)
    return pack_api.approve(c.body, c.ip, STATE["lan_approve"])


def pack_reject(c: Ctx):
    from .api import pack_api
    _own_origin(c)
    return pack_api.reject(c.body, c.ip, STATE["lan_approve"])


def atlas_search(c: Ctx):
    from .api import atlas_api
    k = c.q.get("k", "3")
    if not k.isdigit():
        raise ApiError(400, "bad_request", "k must be a number from 1 to 5")
    return atlas_api.search(c.q.get("q"), int(k), c.q.get("mode", "live"))


def stream_status(c: Ctx):
    from .api import atlas_api
    return atlas_api.stream_status(c.q.get("ride_id"))


def stream_start(c: Ctx):
    from .api import atlas_api
    return 202, atlas_api.stream_start(JM, c.body).to_dict()


def compare(c: Ctx):
    from .api import compare_api
    return compare_api.compare()


def work_roughness(c: Ctx):
    from .api import work_api
    return work_api.roughness()


def work_roughness_file(c: Ctx):
    from .api import work_api
    return FileResp(work_api.roughness_file(c.m["name"]), "image/png", "no-cache")


def work_roughness_recheck(c: Ctx):
    from .api import work_api
    return 202, work_api.roughness_recheck(JM, c.body).to_dict()


def work_recheck(c: Ctx):
    from .api import work_api
    return work_api.recheck()


def work_recheck_start(c: Ctx):
    from .api import work_api
    return 202, work_api.recheck_start(JM, c.body).to_dict()


def work_recheck_canary(c: Ctx):
    from .api import work_api
    return 202, work_api.recheck_canary(JM, c.body).to_dict()


def work_reports(c: Ctx):
    from .api import work_api
    return work_api.reports()


def work_report(c: Ctx):
    from .api import work_api
    return work_api.report(c.m["run"])


def work_report_start(c: Ctx):
    from .api import work_api
    return 202, work_api.report_start(JM, c.body).to_dict()


def live_start(c: Ctx):
    from .api import live_api
    return 202, live_api.start(JM, c.body).to_dict()


def live_stop(c: Ctx):
    from .api import live_api
    return live_api.stop(JM)


def live_probe(c: Ctx):
    from .api import live_api
    return live_api.probe(c.q.get("url"))


def live_ingest(c: Ctx):
    from .api import live_ingest as LI
    return LI.ingest(c.body, c.ip)


def live_app(c: Ctx):
    from .api import live_ingest as LI
    return LI.sessions()


def live_app_rows(c: Ctx):
    from .api import live_ingest as LI
    return LI.rows(c.q.get("session"), c.q.get("after"))


def live_app_atlas(c: Ctx):
    from .api import live_ingest as LI
    return LI.set_atlas(c.body)


ROUTES = [
    ("GET", r"/api/health", health), ("GET", r"/api/egress", egress_log), ("GET", r"/api/method", method),
    ("GET", r"/api/rides", rides), ("POST", r"/api/ride/start", ride_start),
    ("GET", r"/api/ride/precomputed", ride_precomputed),
    ("GET", r"/api/jobs", jobs_list), ("GET", r"/api/jobs/(?P<id>[\w]+)", job_get),
    ("GET", r"/api/jobs/(?P<id>[\w]+)/events", job_events), ("POST", r"/api/jobs/(?P<id>[\w]+)/go", job_go),
    ("POST", r"/api/jobs/(?P<id>[\w]+)/control", job_control), ("POST", r"/api/jobs/(?P<id>[\w]+)/cancel", job_cancel),
    ("GET", r"/api/analyst/session", analyst_session), ("POST", r"/api/analyst/live/start", analyst_live_start),
    ("GET", r"/api/ask/status", ask_status), ("POST", r"/api/ask/start", ask_start),
    ("GET", r"/api/ask/cached", ask_cached), ("GET", r"/api/ask/cached/(?P<name>[^/]+)", ask_cached_file),
    ("GET", r"/api/pack", pack_get), ("GET", r"/api/pack/history", pack_history),
    ("GET", r"/api/source/(?P<chunk>[^/]+)", source), ("GET", r"/api/canary", canary),
    ("POST", r"/api/pack/approve", pack_approve), ("POST", r"/api/pack/reject", pack_reject),
    ("GET", r"/api/atlas/search", atlas_search), ("GET", r"/api/stream/status", stream_status),
    ("POST", r"/api/stream/start", stream_start), ("GET", r"/api/compare", compare),
    ("GET", r"/api/work/roughness", work_roughness), ("GET", r"/api/work/roughness/file/(?P<name>[\w.\-]+)", work_roughness_file),
    ("POST", r"/api/work/roughness/recheck", work_roughness_recheck),
    ("GET", r"/api/work/recheck", work_recheck), ("POST", r"/api/work/recheck/start", work_recheck_start),
    ("POST", r"/api/work/recheck/canary", work_recheck_canary),
    ("GET", r"/api/work/reports", work_reports), ("GET", r"/api/work/reports/(?P<run>[\w.]+)", work_report),
    ("POST", r"/api/work/reports/start", work_report_start),
    ("POST", r"/api/live/start", live_start), ("POST", r"/api/live/stop", live_stop), ("GET", r"/api/live/probe", live_probe),
    ("POST", r"/api/live/ingest", live_ingest), ("GET", r"/api/live/app", live_app), ("GET", r"/api/live/app/rows", live_app_rows),
    ("POST", r"/api/live/app/atlas", live_app_atlas),
]
ROUTES = [(m, re.compile(p + r"/?$"), f) for m, p, f in ROUTES]


# --------------------------------------------------------------------------- HTTP handler
class Handler(BaseHTTPRequestHandler):
    server_version = "tubesense-webapp"
    protocol_version = "HTTP/1.1"       # keep-alive for assets; SSE and refused POST bodies close the connection
    timeout = 60

    def log_message(self, *a):          # quiet; errors are logged where they happen
        if STATE["verbose"]:
            sys.stderr.write("[web] " + (a[0] % a[1:] if len(a) > 1 else str(a[0])).split("?")[0] + "\n")

    # ---- dispatch
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_HEAD(self):
        self._dispatch("GET", head=True)

    def _dispatch(self, method: str, head: bool = False):
        try:
            parts = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parts.path)
            self._check_host()
            if path.startswith("/api/"):
                self._api(method, path, dict(urllib.parse.parse_qsl(parts.query)), head)
            elif method == "GET":
                self._static(path, head)
            else:
                raise ApiError(404, "not_found", "no such path")
        except ApiError as e:
            if method == "POST":
                self.close_connection = True        # the body may be unread
            self._json(e.status, e.body(), head)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:  # noqa: BLE001
            self.close_connection = True
            sys.stderr.write(f"[web] 500 {self.command} {self.path.split('?')[0]}: {exc_text(e)}\n{traceback.format_exc()}")
            try:
                self._json(500, {"error": "internal", "message": f"{type(e).__name__} (see the server log)"}, head)
            except OSError:
                pass

    def _check_host(self):
        if not host_ok(self.headers.get("Host") or ""):
            raise ApiError(403, "forbidden", "unexpected Host header (this server answers to 127.0.0.1/localhost"
                           + (", IP addresses and this machine's own name" if STATE["lan"] else "; use --lan for other devices") + ")")

    def _read_body(self) -> dict:
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != (self.headers.get("Host") or ""):
            raise ApiError(403, "forbidden", "cross-origin request refused")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "bad_request", "bad Content-Length") from None
        if n > MAX_BODY:
            raise ApiError(400, "bad_request", f"body larger than {MAX_BODY // 1024} KB")
        if n == 0:
            return {}
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            raise ApiError(400, "bad_request", "POST bodies must be application/json")
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "bad_request", "body is not valid JSON") from None
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request", "body must be a JSON object")
        return body

    def _api(self, method: str, path: str, query: dict, head: bool):
        allowed = []
        for m, rx, fn in ROUTES:
            mt = rx.match(path)
            if not mt:
                continue
            if m != method:
                allowed.append(m)
                continue
            body = self._read_body() if method == "POST" else {}
            self._respond(fn(Ctx(self, mt, query, body)), head)
            return
        if allowed:
            raise ApiError(405, "method_not_allowed", f"use {' or '.join(allowed)} for {path}")
        raise ApiError(404, "not_found", f"no such endpoint {path}")

    def _respond(self, r, head: bool = False):
        if isinstance(r, tuple):
            self._json(r[0], r[1], head)
        elif isinstance(r, Sse):
            self._sse(r.job, r.after)
        elif isinstance(r, FileResp):
            self._file(r.path, r.ctype, r.cache, head)
        elif isinstance(r, Raw):
            self._send(r.status, r.body, r.ctype, head)
        else:
            self._json(200, r, head)

    # ---- writers
    def _send(self, status: int, body: bytes, ctype: str, head: bool = False, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _json(self, status: int, obj, head: bool = False):
        self._send(status, dumps(obj), "application/json; charset=utf-8", head, {"Cache-Control": "no-store"})

    def _file(self, p: Path, ctype: str | None, cache: str, head: bool = False):
        ctype = ctype or MIME.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") and "charset" not in ctype:
            ctype += "; charset=utf-8"
        size = p.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if head:
            return
        with open(p, "rb") as fh:
            while chunk := fh.read(65536):
                self.wfile.write(chunk)

    def _static(self, path: str, head: bool):
        rel_p = path.lstrip("/") or "index.html"
        if "\\" in rel_p or any(part in ("..", "") for part in rel_p.split("/")[:-1]) or rel_p.endswith("/.."):
            raise ApiError(404, "not_found", "not found")
        try:
            p = (STATIC / rel_p).resolve()
            p.relative_to(STATIC.resolve())
        except (ValueError, OSError):
            raise ApiError(404, "not_found", "not found") from None
        if p.is_dir():
            p = p / "index.html"
        if not p.is_file():
            if rel_p == "index.html":
                msg = (b"<!doctype html><meta charset=utf-8><title>Tubesense</title><body style='background:#070B14;color:#E9F1FF;"
                       b"font:16px system-ui;padding:24px'><h1>Tubesense web app</h1><p>The API is running "
                       b"(<a style='color:#F2B33D' href=/api/health>/api/health</a>); webapp/static/index.html is not built yet.</p>")
                return self._send(200, msg, "text/html; charset=utf-8", head)
            raise ApiError(404, "not_found", "not found")
        self._file(p, None, "no-cache", head)

    def _sse(self, job, after: int):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        w = self.wfile
        JM.subscribe(job)
        try:
            w.write(b"retry: 3600000\n\n")
            w.flush()
            last, t_write = after, time.monotonic()
            while True:
                events, done = job.wait_events(last, 5.0)
                for e in events:
                    w.write(b"id: %d\ndata: " % e["seq"] + dumps(e) + b"\n\n")
                    last = e["seq"]
                if events:
                    w.flush()
                    t_write = time.monotonic()
                if any(e.get("type") == "job_end" for e in events) or (done and not events):
                    break
                if time.monotonic() - t_write >= 15:
                    w.write(b": ping\n\n")
                    w.flush()
                    t_write = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            JM.unsubscribe(job)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


# --------------------------------------------------------------------------- start
def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))        # no packet is sent; picks the interface
            return s.getsockname()[0]
    except OSError:
        return "<this machine's LAN IP>"


def warm() -> None:
    """Load .env (values stay in the environment, never printed) and import the light modules once."""
    try:
        from atlas.db import load_env
        load_env()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[web] .env not loaded: {exc_text(e)}\n")
    for mod in ("agent.verify", "agent.pack", "agent.approve", "demo.replay_analyst", "demo.atlas_show"):
        try:
            __import__(mod)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[web] could not import {mod}: {exc_text(e)} (its endpoints will answer 500/503)\n")
    heavy = [m for m in ("pandas", "scipy", "matplotlib", "tubesense.stops", "tubesense.online") if m in sys.modules]
    if heavy:
        sys.stderr.write(f"[web] WARNING: heavy modules are loaded in the server process: {', '.join(heavy)}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--lan", action="store_true", help="listen on all interfaces (anyone on the network can use the app)")
    ap.add_argument("--lan-approve", action="store_true", help="also allow evidence-pack approval from non-loopback clients")
    ap.add_argument("--verbose", action="store_true", help="log one line per request (path only)")
    a = ap.parse_args()
    STATE.update(port=a.port, lan=a.lan, lan_approve=a.lan_approve, verbose=a.verbose)
    warm()
    srv = Server(("0.0.0.0" if a.lan else "127.0.0.1", a.port), Handler)
    atexit.register(JM.shutdown)
    if a.lan:
        print(f"Tubesense web app: http://{lan_ip()}:{a.port}/  (LAN: anyone on this network can start Gemini and Atlas jobs; "
              "use a phone hotspot, not venue Wi-Fi)", flush=True)
        print(f"  approval from other devices: {'ALLOWED (--lan-approve)' if a.lan_approve else 'laptop only'}", flush=True)
    else:
        print(f"Tubesense web app: http://127.0.0.1:{a.port}/  (local only; --lan to open to the network)", flush=True)
    if OUT_DIR != POSTHOC:
        print(f"  TEST MODE: approvals and asks go to {OUT_DIR} and are not mirrored to Atlas", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        JM.shutdown()
        srv.server_close()


if __name__ == "__main__":
    main()
