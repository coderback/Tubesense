"""Endpoint checks against a running web app server (webapp/DESIGN.md tests B1, B3, B12-B15, B21, B23, B4, B16).

What: read-only GETs (health has only booleans for secrets, the ride catalogue, the recorded analyst session, the
pack, a source quote, the canary, stream status, compare, method, egress), one ride replay at 8x unless --no-ride,
and, only with --scratch-approve AND a server whose health says its output dir is not the real outputs_posthoc,
the approval path (400 / 409 / 200). It prints PASS or FAIL per check and exits 1 if any fails.
Why: a one-command check that the backend still honours its contract before a demo.
Run (repo root): PYTHONPATH=. .venv/Scripts/python.exe -m webapp.api.selftest --base http://127.0.0.1:8770 [--no-ride] [--scratch-approve]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

RESULTS: list[tuple[str, bool, str]] = []


def call(base: str, path: str, body: dict | None = None, method: str | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method or ("POST" if data is not None else "GET"),
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


def secret_values() -> list[str]:
    """Values of .env (never printed) with length >= 8, to prove no response contains them."""
    from .util import ROOT
    out = []
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if len(v) >= 8:
                    out.append(v)
    except OSError:
        pass
    return out


def follow(base: str, job: dict, limit_s: float = 120.0) -> list[dict]:
    ev, t0 = [], time.time()
    with urllib.request.urlopen(base + job["events_url"], timeout=limit_s) as r:
        for raw in r:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("data: "):
                ev.append(json.loads(line[6:]))
            if time.time() - t0 > limit_s:
                break
    return ev


def run(base: str, no_ride: bool, scratch_approve: bool) -> None:
    code, h = call(base, "/api/health")
    check("B1 health ok, env values are booleans, lan false", code == 200 and h.get("ok") and not h.get("lan")
          and all(isinstance(v, bool) for v in h["env"].values()), json.dumps(h.get("env")))
    secrets = secret_values()
    leaks = []
    for p in ("/api/health", "/api/rides", "/api/pack", "/api/analyst/session", "/api/method", "/api/egress"):
        body = urllib.request.urlopen(base + p, timeout=60).read().decode("utf-8", "replace")
        leaks += [p for s in secrets if s in body]
    check("B1 no secret value appears in responses", not leaks, f"{len(secrets)} values checked")

    _, r = call(base, "/api/rides")
    r9 = next((x for x in r["rides"] if x["run_id"] == "R9" and x["track"] == "EB LST->BTH"), {})
    r9b = next((x for x in r["rides"] if x["run_id"] == "R9" and x["track"] == "EB BTH->MLE"), {})
    check("B3 R9 EB LST->BTH available and recommended", r9.get("available") and r9.get("recommended"))
    check("B3 R9 EB BTH->MLE unavailable (section_truncated)", r9b.get("available") is False and "section_truncated" in (r9b.get("reason") or ""))
    check("B3 three Central stations", sorted(r["stations"]) == ["BTH", "LST", "MLE"])

    code, s = call(base, "/api/analyst/session?atlas=0")
    ex = [e for e in s.get("events", []) if e.get("name") == "run_experiment"]
    acc = [e for e in ex if e["verdict"] and e["verdict"]["accepted"]]
    check("B12 hash chain intact, 9 lines", s["integrity"]["chain_ok"] and s["integrity"]["lines"] == 9)
    check("B12 6 experiments, exactly attempt 4 accepted, every recheck same",
          len(ex) == 6 and [e["attempt"] for e in acc] == [4] and all(e["verdict"]["recheck_same"] for e in ex if e["verdict"]))
    check("B12 sealed v2 median 1.8 m", s["sealed"] and s["sealed"]["v2"]["median_error_m"] == 1.8)

    code, p = call(base, "/api/pack")
    check("B13 pack 16 claims all verified now, 5 checklist items",
          code == 200 and len(p["claims"]) == 16 and all(c["check"]["ok"] for c in p["claims"]) and len(p["checklist"]) == 5,
          f"pack {p.get('pack_id')}")
    code, q = call(base, "/api/source/MQ-2022-1791%23001?quote=Bethnal%20Green%20to%20Liverpool%20Street")
    check("B14 source quote found, url present", code == 200 and q.get("quote_found") is True and q.get("url"))
    code, c = call(base, "/api/canary")
    check("B15 canary rejected, reasons mention the press figure", code == 200 and c["ok"] is False
          and any("92.8" in x for x in c["reasons"]))
    code, st = call(base, "/api/stream/status")
    check("B21 stream status is honest (no invented windows)", code == 200 and (st["available"] or st["windows"] == []),
          st.get("reason") or f"{len(st['windows'])} windows")
    code, cm = call(base, "/api/compare")
    code2, me = call(base, "/api/method")
    code3, eg = call(base, "/api/egress")
    check("B23 compare/method/egress answer", code == code2 == code3 == 200 and me["prereg"]["frozen_unchanged"] in (True, None)
          and "entries" in eg, f"frozen_unchanged={me['prereg']['frozen_unchanged']}")

    if not no_ride:
        code, job = call(base, "/api/ride/start", {"run": "R9", "track": "EB LST->BTH", "tracker": "v2", "speed": 8})
        t0 = time.time()
        ev = follow(base, job) if code == 202 else []
        end = next((e for e in ev if e["type"] == "end"), None)
        ticks = [e for e in ev if e["type"] == "tick"]
        check("B4 ride at 8x finishes within 35 s with ticks, paints and a departure", code == 202 and end is not None
              and time.time() - t0 <= 35 and sum(1 for t in ticks if t["paint"]) >= 20 and any(t["departure"] for t in ticks),
              f"{time.time() - t0:.1f} s, {len(ticks)} ticks")
        check("B4 end near the section length, reference median < 10 m",
              end and ticks[-1]["s_m"] > 0.97 * 2270 and (end["summary"]["vs_truth_median_m"] or 99) < 10,
              f"last {ticks[-1]['s_m'] if ticks else None} m, median {end and end['summary']['vs_truth_median_m']} m")

    if scratch_approve:
        if h["posthoc_out_is_default"] or h["approval_mirror"]:
            check("B16 approval tests", False, "REFUSED: this server writes to the real outputs_posthoc")
        else:
            pid = p["pack_id"]
            ok = {"pack_id": pid, "approver": "Selftest", "checked": [0, 1, 2, 3, 4]}
            c1, b1 = call(base, "/api/pack/approve", {**ok, "checked": [0, 1, 2, 3]})
            c2, b2 = call(base, "/api/pack/approve", {**ok, "approver": " "})
            c3, b3 = call(base, "/api/pack/approve", {**ok, "pack_id": "wrong"})
            c4, b4 = call(base, "/api/pack/approve", ok)
            check("B16 4 boxes 400, no name 400, wrong pack 400, full 200 not mirrored",
                  c1 == 400 and b1["missing"] and c2 == 400 and b2["approver_missing"] and c3 == 400 and b3["pack_changed"]
                  and c4 == 200 and b4["atlas"] == "not mirrored (test output dir)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="http://127.0.0.1:8770")
    ap.add_argument("--no-ride", action="store_true", help="skip the 8x ride replay")
    ap.add_argument("--scratch-approve", action="store_true",
                    help="also test approval; only against a server started with TUBESENSE_POSTHOC_OUT=<scratch dir>")
    a = ap.parse_args()
    try:
        run(a.base, a.no_ride, a.scratch_approve)
    except (urllib.error.URLError, OSError) as e:
        print(f"FAIL  server not reachable at {a.base}: {type(e).__name__}")
        sys.exit(1)
    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed" + (f"; FAILED: {', '.join(bad)}" if bad else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
