"""MOCK phyphox: a local server with the phyphox remote-interface endpoints, fed by a recorded ride or synthetic motion.

What: serves /get (single / full / threshold and threshold|reference-buffer reads, with the iOS server's threshold
nudge and the full update after a clear), /control?cmd=start|stop|clear, /config, /meta, /time, with the buffer
names of the iOS "simple experiment" our rides used (acc_time/accX.., gyro_time/gyroX.., mag_time/magX..,
baro_time/baroX). A recorded ride's samples become visible as experiment time advances (x speed), so the live
detectors, the capture CLI and the web app can be tested without the phone.
Why: everything must be testable off-stage, and the demo needs an honest fallback if the venue network fails. It is
always labelled: /config title and /meta say "MOCK: replaying recorded <run> through a phyphox-compatible interface".
How: recorded ride = tubesense.ingest.load_run (the phyphox .xlsx export, parquet-cached), times shifted so the
replay starts at experiment time 0 (from --start-at s into the ride). Synthetic = gravity + platform/tunnel
vibration, two stops, a curve, a double knock at 3 s and a phone flip at 75 s.

Run:  PYTHONPATH=. .venv/Scripts/python.exe -m live.mock_phyphox --run R9 --port 8080 [--speed 1] [--start-at 0]
      PYTHONPATH=. .venv/Scripts/python.exe -m live.mock_phyphox --run synthetic --port 8080
API:  MockPhyphox(run="R9", speed=1.0, start_at=0.0, autostart=True).serve(port=0) -> (url, server); server.shutdown()
"""
from __future__ import annotations

import argparse
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

import numpy as np

LAYOUT = {"acc": ("acc_time", ["accX", "accY", "accZ"], "accelerometer", "Accelerometer", ["X (m/s^2)", "Y (m/s^2)", "Z (m/s^2)"]),
          "gyr": ("gyro_time", ["gyroX", "gyroY", "gyroZ"], "gyroscope", "Gyroscope", ["X (rad/s)", "Y (rad/s)", "Z (rad/s)"]),
          "mag": ("mag_time", ["magX", "magY", "magZ"], "magnetic_field", "Magnetometer", ["X (µT)", "Y (µT)", "Z (µT)"]),
          "baro": ("baro_time", ["baroX"], "pressure", "Barometer", ["X (hPa)"])}


def synthetic(dur: float = 180.0, fs: float = 99.2, seed: int = 7) -> dict[str, np.ndarray]:
    """Phone flat on a seat: dwell 0-20 s, run 20-80 s (curve 40-55 s), dwell 80-110 s, run 110-180 s.
    Double knock at 3.0/3.3 s; the phone is flipped screen-down at 75 s (placement change)."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, dur, 1 / fs)
    moving = ((t > 20) & (t < 80)) | (t > 110)
    g = np.where(t[:, None] < 75, [0.0, 0.0, 9.81], [0.0, 0.0, -9.81])
    flip = (t > 74) & (t < 76)
    vib = np.where(moving, 0.35, 0.02)[:, None] * rng.standard_normal((len(t), 3))
    body = np.where(moving, 0.25, 0.0)[:, None] * np.sin(2 * np.pi * 1.3 * t[:, None] + [0, 1, 2])
    acc = g + vib + body
    for tk in (3.0, 3.3):
        k = int(tk * fs)
        acc[k: k + 3] += [[0, 0, 30], [0, 0, -18], [0, 0, 6]]
    acc[flip] += 3 * rng.standard_normal((flip.sum(), 3))
    yaw = np.where((t > 40) & (t < 55), np.radians(3.0), 0.0)
    gyr = 0.002 * rng.standard_normal((len(t), 3)) + np.where(moving, 0.01, 0.0)[:, None] * rng.standard_normal((len(t), 3))
    gyr[:, 2] += np.where(t < 75, yaw, -yaw)
    gyr[flip] += 2.0 * rng.standard_normal((flip.sum(), 3))
    mag = np.array([20.0, 0.0, -42.0]) + np.where(moving, 6.0, 1.0)[:, None] * np.sin(0.3 * t[:, None] + [0, 2, 4])
    tb = np.arange(0, dur, 1.08)
    p = 1012.0 + np.where(((tb > 20) & (tb < 80)) | (tb > 110), 0.25 * np.sin(0.4 * tb), 0.0)
    p += np.where(tb > 95, 0.4, 0.0)  # door step at the second dwell
    return {"acc": np.column_stack([t, acc]), "gyr": np.column_stack([t, gyr]),
            "mag": np.column_stack([t, mag]), "baro": np.column_stack([tb, p])}


def recorded(run_id: str, start_at: float = 0.0) -> dict[str, np.ndarray]:
    from tubesense.ingest import load_run
    r = load_run(run_id)
    out = {}
    for k, df in r.sensors.items():
        a = df.to_numpy(float)
        a = a[a[:, 0] >= start_at]
        a[:, 0] -= start_at
        out[k] = a
    return out


class MockPhyphox:
    def __init__(self, run: str = "R9", speed: float = 1.0, start_at: float = 0.0, autostart: bool = True):
        self.run, self.speed = run, speed
        self.data = synthetic() if run == "synthetic" else recorded(run, start_at)
        self.label = ("MOCK: synthetic motion through a phyphox-compatible interface" if run == "synthetic" else
                      f"MOCK: replaying recorded {run} (from {start_at:g} s, x{speed:g}) through a phyphox-compatible interface")
        self.end_t = max(float(a[-1, 0]) for a in self.data.values())
        self.lock = threading.Lock()
        self.session = secrets.token_hex(3)
        self.exp_t0 = 0.0          # experiment time accumulated before the current start
        self.wall_start = None     # wall clock of the current start (None = not measuring)
        self.cleared_at = 0.0      # experiment time origin after a clear (data before it is dropped)
        self.force_full = False
        self.events: list[dict] = []
        if autostart:
            self.start()

    # ---------------------------------------------------------------- experiment clock
    def now_t(self) -> float:
        t = self.exp_t0 + (0.0 if self.wall_start is None else (time.time() - self.wall_start) * self.speed)
        return min(t, self.end_t - self.cleared_at)

    @property
    def measuring(self) -> bool:
        if self.wall_start is not None and self.now_t() >= self.end_t - self.cleared_at:
            self.stop()  # recording exhausted: behave like a stopped experiment
        return self.wall_start is not None

    def start(self) -> bool:
        if self.wall_start is None and self.now_t() < self.end_t - self.cleared_at:
            self.wall_start = time.time()
            self.events.append({"event": "START", "experimentTime": self.now_t(), "systemTime": time.time()})
        return True

    def stop(self) -> bool:
        if self.wall_start is not None:
            self.exp_t0 = self.now_t()
            self.wall_start = None
            self.events.append({"event": "PAUSE", "experimentTime": self.exp_t0, "systemTime": time.time()})
        return True

    def clear(self) -> bool:
        # phyphox: clear empties the buffers and stops; a new start counts from 0. Here the replay moves on.
        self.cleared_at += self.now_t()
        self.stop()
        self.exp_t0, self.events, self.force_full = 0.0, [], True
        return True

    def buffers(self) -> dict[str, np.ndarray]:
        """Visible content of every buffer now."""
        tn, out = self.now_t(), {}
        for k, a in self.data.items():
            tb, comps = LAYOUT[k][0], LAYOUT[k][1]
            lo = np.searchsorted(a[:, 0], self.cleared_at, side="left")
            hi = np.searchsorted(a[:, 0], self.cleared_at + tn, side="right")
            seg = a[lo:hi]
            out[tb] = seg[:, 0] - self.cleared_at
            for i, c in enumerate(comps):
                out[c] = seg[:, 1 + i]
        return out

    # ---------------------------------------------------------------- endpoints
    def get(self, query: list[tuple[str, str]]) -> tuple[int, dict]:
        with self.lock:
            bufs, force, self.force_full = self.buffers(), self.force_full, False
            measuring = self.measuring
        res = {}
        for name, value in query:
            if name not in bufs:
                continue  # unknown buffers are skipped, as phyphox does
            raw = bufs[name]
            d = {"size": 0}
            if value == "full" or (value and force):
                d["updateMode"], vals = "full", raw
            elif value:
                parts = value.split("|")
                try:
                    thr = float(parts[0])
                except ValueError:
                    return 400, {"error": "Invalid threshold."}
                if np.isfinite(thr) and thr != 0:  # iOS: nudge by one unit in the 8th digit
                    thr += 10.0 ** np.floor(np.log10(abs(thr) / 1e7))
                if len(parts) > 1:
                    if parts[-1] not in bufs:
                        return 400, {"error": "Unknown reference buffer."}
                    ref = bufs[parts[-1]][: len(raw)]
                    vals = raw[: len(ref)][ref > thr]
                else:
                    vals = raw[raw > thr]
                d["updateMode"] = "partial"
            else:
                d["updateMode"], vals = "single", raw[-1:] if len(raw) else np.array([np.nan])
            d["buffer"] = [float(v) if np.isfinite(v) else None for v in vals]
            res[name] = d
        return 200, {"buffer": res, "status": {"session": self.session, "measuring": measuring,
                                               "timedRun": False, "countDown": 0}}

    def config(self) -> dict:
        names = [n for k in self.data for n in [LAYOUT[k][0]] + LAYOUT[k][1]]
        return {"crc32": "0", "title": self.label, "localTitle": self.label, "category": "MOCK", "localCategory": "MOCK",
                "buffers": [{"name": n, "size": 0} for n in names],
                "inputs": [{"source": LAYOUT[k][2], "outputs": [{a: b} for a, b in zip("xyz", LAYOUT[k][1])]
                            + [{"t": LAYOUT[k][0]}]} for k in self.data],
                "export": [{"set": LAYOUT[k][3], "sources": [{"label": "Time (s)", "buffer": LAYOUT[k][0]}]
                            + [{"label": lab, "buffer": b} for lab, b in zip(LAYOUT[k][4], LAYOUT[k][1])]} for k in self.data]}

    def meta(self) -> dict:
        return {"version": "mock", "build": "0", "fileFormat": "1.20", "deviceModel": "MOCK", "deviceBrand": "MOCK",
                "mock": self.label}

    def control(self, cmd: str) -> dict:
        with self.lock:
            fn = {"start": self.start, "stop": self.stop, "clear": self.clear}.get(cmd)
            return {"result": bool(fn and fn())}

    # ---------------------------------------------------------------- http
    def handler(self):
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urlsplit(self.path)
                q = parse_qsl(u.query, keep_blank_values=True)
                code, body = 200, None
                if u.path == "/get":
                    code, body = mock.get(q)
                elif u.path == "/control":
                    body = mock.control(dict(q).get("cmd", ""))
                elif u.path == "/config":
                    body = mock.config()
                elif u.path == "/meta":
                    body = mock.meta()
                elif u.path == "/time":
                    body = mock.events
                elif u.path in ("/", "/index.html"):
                    body = {"mock": mock.label, "endpoints": ["/get", "/control", "/config", "/meta", "/time"]}
                else:
                    code, body = 404, {"error": "Not found."}
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        return H

    def serve(self, port: int = 0, host: str = "127.0.0.1") -> tuple[str, ThreadingHTTPServer]:
        """Start serving in a daemon thread; port 0 = any free port. Returns (url, server)."""
        srv = ThreadingHTTPServer((host, port), self.handler())
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return f"http://{host}:{srv.server_address[1]}", srv


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="R9", help="recorded run id (e.g. R9, R5) or 'synthetic'")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to expose on the LAN (not needed for the demo)")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--start-at", type=float, default=0.0, help="start the replay this many s into the ride")
    ap.add_argument("--no-autostart", action="store_true", help="wait for /control?cmd=start, like a phone")
    a = ap.parse_args()
    m = MockPhyphox(a.run, a.speed, a.start_at, autostart=not a.no_autostart)
    url, srv = m.serve(a.port, a.host)
    print(f"{m.label} at {url} (ride ends at experiment time {m.end_t:.0f} s); Ctrl+C to stop", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
