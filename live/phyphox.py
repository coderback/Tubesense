"""Small client for the phyphox remote-interface REST API (the app used for every recorded ride).

What: discover the running experiment's sensor buffers (/config), read them incrementally (/get with the threshold
syntax), start/stop/clear (/control), read /meta and /time. No call raises to the caller: every method returns
None / {} / False and leaves a human-readable reason in `self.error`.

Protocol (official: https://phyphox.org/docs/remote-interface/ and its api-reference / openapi.yaml; checked against
phyphox-ios ExperimentWebServer.swift, which the recordings' phone (iPhone13,2, phyphox 1.2.1, iOS 26.1) runs):
- Enable: open the experiment, menu (top right) -> "Allow remote access", confirm the warning; the app then shows
  the address to use at the bottom of the screen. Use that address exactly. iOS default port 80 (falls back to
  8080..8180 if 80 is taken), Android default 8080.
- GET /get?acc_time=12.5&accX=12.5%7Cacc_time -> every value of acc_time > 12.5, and the values of accX at the
  indices where acc_time > 12.5. '|' MUST be sent as %7C. No value ('/get?accX') = last value only ("single");
  'full' = whole buffer. The server nudges the threshold up by one unit in the 8th significant digit so a value
  already delivered is not delivered again. Unknown buffer names are skipped, not reported. After a clear every
  requested buffer comes back as a 'full' update, whatever was asked.
- Response: {"buffer": {name: {"size": capacity, "updateMode": "partial"|"full"|"single", "buffer": [...]}},
             "status": {"session": id, "measuring": bool, "timedRun": bool, "countDown": ms}}.
  `session` changes when the user switches experiment (old data invalid). NaN/inf arrive as null.
- /control?cmd=start|stop|clear -> {"result": true|false} (HTTP 200 either way). /config -> {crc32, title, buffers,
  inputs: [{source: "accelerometer", outputs: [{"x": "accX"}, ..., {"t": "acc_time"}]}], export: [...]}.
- CORS: every response carries Access-Control-Allow-Origin: * (a browser page served over http can poll directly;
  an https page cannot reach http://phone). The bundled web interface serialises requests with a 10 ms gap.
Our recordings' export sets (Accelerometer / Gyroscope / Magnetometer / Barometer, 'Time (s)', 'X (m/s^2)', ...)
match the iOS "simple experiment" template (phyphox-ios SimpleExperimentTemplete.swift), whose buffers are
acc_time/accX/accY/accZ, gyro_time/gyroX.., mag_time/magX.., baro_time/baroX; those are the fallback names when
/config cannot be mapped.

Screen lock / backgrounding (phyphox-ios source): on resignActive (lock, Control Center, app switch, call) the app stops the
experiment AND tears down the web server; on return it relaunches the server with a NEW session id and the experiment stays
paused. poll() handles the new session (re-discover + re-prime); the user must press play again (or capture --start does).

Run (probe a phone): PYTHONPATH=. .venv/Scripts/python.exe -m live.phyphox http://192.168.1.23
"""
from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import quote

import numpy as np

# phyphox input source name -> our sensor key (the keys tubesense.ingest uses). phyphox-ios /config sends the Swift case
# name (input.sensorType.description, e.g. 'magneticField'), Android/the docs the raw name ('magnetic_field'): both listed.
SOURCES = {"accelerometer": "acc", "gyroscope": "gyr", "magnetic_field": "mag", "magneticfield": "mag", "magnetometer": "mag",
           "pressure": "baro", "barometer": "baro"}
# export set name (our .xlsx sheet names) -> sensor key, used when /config inputs cannot be mapped
EXPORT_SETS = {"Accelerometer": "acc", "Gyroscope": "gyr", "Magnetometer": "mag", "Barometer": "baro"}
# iOS simple-experiment buffer names: the experiment every recorded ride used
SIMPLE = {"acc": ("acc_time", ["accX", "accY", "accZ"]), "gyr": ("gyro_time", ["gyroX", "gyroY", "gyroZ"]),
          "mag": ("mag_time", ["magX", "magY", "magZ"]), "baro": ("baro_time", ["baroX"])}
BACKFILL_S = 1.0  # on the first poll, start this far before the phone's latest sample (not the whole buffer)

UNREACHABLE = ("phone not reachable at {url}: same Wi-Fi? venue Wi-Fi may isolate clients: use the phone hotspot "
               "(laptop joins the phone's hotspot). Is phyphox open in the foreground with 'Allow remote access' on, "
               "and is the address exactly the one phyphox shows (iOS default port 80, Android 8080)? ({why})")


@dataclass
class Sensor:
    key: str             # acc / gyr / mag / baro
    time_buf: str        # e.g. acc_time
    comps: list[str]     # e.g. [accX, accY, accZ]
    last_t: float | None = None  # threshold for the next incremental read


@dataclass
class Poll:
    data: dict[str, np.ndarray] = field(default_factory=dict)  # key -> (n, 1 + len(comps)): t, values
    status: dict | None = None
    error: str | None = None
    reset: bool = False       # buffers were cleared / the experiment restarted / the session changed


def normalise_url(url: str) -> str:
    url = url.strip().rstrip("/")
    return url if url.startswith(("http://", "https://")) else "http://" + url


class Phyphox:
    def __init__(self, url: str, timeout: float = 2.0):
        self.url = normalise_url(url)
        self.timeout = timeout
        self.error: str | None = None
        self.sensors: dict[str, Sensor] = {}
        self.session: str | None = None

    # ------------------------------------------------------------------ http
    def _get(self, path: str) -> dict | list | None:
        self.error = None
        try:
            with urllib.request.urlopen(self.url + path, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            self.error = f"phyphox answered HTTP {e.code} for {path.split('?')[0]}: {body}"
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            why = getattr(e, "reason", e)
            self.error = UNREACHABLE.format(url=self.url, why=f"{type(e).__name__}: {why}")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.error = f"{self.url} answered, but not with phyphox JSON ({e}); is this the phyphox address?"
        except Exception as e:  # never leak
            self.error = f"unexpected error talking to {self.url}: {type(e).__name__}: {e}"
        return None

    # ------------------------------------------------------------------ endpoints
    def config(self) -> dict | None:
        c = self._get("/config")
        if c is not None and not isinstance(c, dict):
            self.error, c = "/config did not return an object", None
        return c

    def meta(self) -> dict | None:
        m = self._get("/meta")
        return m if isinstance(m, dict) else None

    def time_events(self) -> list | None:
        t = self._get("/time")
        return t if isinstance(t, list) else None

    def control(self, cmd: str) -> bool:
        """cmd: start | stop | clear. True only if phyphox says {"result": true}."""
        r = self._get(f"/control?cmd={quote(cmd)}")
        ok = isinstance(r, dict) and r.get("result") is True
        if r is not None and not ok:
            self.error = f"phyphox refused /control?cmd={cmd}"
        return ok

    def status(self) -> dict | None:
        r = self._get("/get")
        return r.get("status") if isinstance(r, dict) else None

    def discover(self) -> dict[str, Sensor]:
        """Map the running experiment's buffers to acc/gyr/mag/baro via /config (inputs, then export sets, then the
        iOS simple-experiment names). Returns {} (and sets self.error) if the phone is unreachable or has no IMU."""
        cfg = self.config()
        if cfg is None:
            return {}
        names = {b.get("name") for b in cfg.get("buffers", []) if isinstance(b, dict)}
        found: dict[str, Sensor] = {}
        for inp in cfg.get("inputs", []) or []:
            key = SOURCES.get(str(inp.get("source", "")).lower())
            if not key or key in found:
                continue
            outs = {k: v for o in inp.get("outputs", []) or [] for k, v in o.items()}
            comps = [outs[c] for c in ("x", "y", "z") if c in outs] if key != "baro" else [outs.get("x") or outs.get("abs")]
            if outs.get("t") and comps and all(comps):
                found[key] = Sensor(key, outs["t"], comps)
        for s in cfg.get("export", []) or []:
            key = EXPORT_SETS.get(s.get("set"))
            src = s.get("sources", []) or []
            if key and key not in found and len(src) >= 2 and str(src[0].get("label", "")).startswith("Time"):
                found[key] = Sensor(key, src[0]["buffer"], [x["buffer"] for x in src[1: 2 if key == "baro" else 4]])
        for key, (tb, comps) in SIMPLE.items():
            if key not in found and tb in names and all(c in names for c in comps):
                found[key] = Sensor(key, tb, list(comps))
        if "acc" not in found or "gyr" not in found:
            self.error = (f"experiment '{cfg.get('title')}' has no accelerometer + gyroscope buffers (found "
                          f"{sorted(found)}); on the phone use the experiment the rides used: a simple experiment "
                          "with Accelerometer, Gyroscope, Magnetometer, Pressure")
        self.sensors = found
        return found

    def prime(self) -> bool:
        """Start incremental reads BACKFILL_S before each sensor's latest sample (avoids downloading a long buffer)."""
        if not self.sensors:
            return False
        q = "&".join(quote(s.time_buf) for s in self.sensors.values())
        r = self._get("/get?" + q)
        if not isinstance(r, dict):
            return False
        self.session = (r.get("status") or {}).get("session")
        for s in self.sensors.values():
            v = ((r.get("buffer") or {}).get(s.time_buf) or {}).get("buffer") or [None]
            s.last_t = (float(v[-1]) - BACKFILL_S) if v[-1] is not None else None
        return True

    def poll(self) -> Poll:
        """One incremental read of every discovered sensor (all in one /get, one consistent snapshot on iOS)."""
        out = Poll()
        if not self.sensors:
            out.error = self.error or "no sensors discovered"
            return out
        parts = []
        for s in self.sensors.values():
            thr = "full" if s.last_t is None else repr(float(s.last_t))
            parts.append(f"{quote(s.time_buf)}={thr}")
            parts += [f"{quote(c)}={thr}%7C{quote(s.time_buf)}" if s.last_t is not None else f"{quote(c)}=full"
                      for c in s.comps]
        r = self._get("/get?" + "&".join(parts))
        if not isinstance(r, dict):
            out.error = self.error or "bad /get response"
            return out
        out.status = r.get("status") or {}
        sess = out.status.get("session")
        if self.session is not None and sess is not None and sess != self.session:
            # New session (app relaunched / screen lock tore the server down / experiment switched): old thresholds are
            # wrong because experiment time may restart from 0. Re-discover and re-prime now (no data this poll).
            out.reset = True
            old = self.sensors
            if self.discover() and "acc" in self.sensors and "gyr" in self.sensors:
                self.session = sess
                self.prime()
            else:
                self.sensors = old
                for s in old.values():
                    s.last_t = None  # unknown: read from the start of the new buffers
                self.session = sess
            return out
        self.session = sess if sess is not None else self.session
        bufs = r.get("buffer") or {}
        for s in self.sensors.values():
            cols = [bufs.get(b) or {} for b in [s.time_buf] + s.comps]
            if not cols[0]:
                continue
            arrs = [np.array([np.nan if v is None else v for v in (c.get("buffer") or [])], float) for c in cols]
            n = min(len(a) for a in arrs)  # a component may be one sample behind its time buffer on some builds
            if n == 0:
                if any(c.get("updateMode") == "full" for c in cols) and s.last_t is not None:
                    s.last_t, out.reset = None, True  # cleared and still empty: read from the start next time
                continue
            a = np.column_stack([x[:n] for x in arrs])
            a = a[np.isfinite(a[:, 0])]
            full = any(c.get("updateMode") == "full" for c in cols)
            if full and s.last_t is not None and len(a) and a[0, 0] < s.last_t - 0.5:
                out.reset = True  # cleared and restarted: experiment time began again
            elif full and s.last_t is not None:
                a = a[a[:, 0] > s.last_t]
            if len(a):
                s.last_t = float(a[-1, 0])
                out.data[s.key] = a
        return out


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
    p = Phyphox(url)
    sens = p.discover()
    print(json.dumps({"url": p.url, "sensors": {k: [s.time_buf] + s.comps for k, s in sens.items()},
                      "error": p.error, "meta": p.meta(), "status": p.status()}, indent=1))


if __name__ == "__main__":
    main()
