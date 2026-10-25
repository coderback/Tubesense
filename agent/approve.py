"""Human approval gate for the evidence pack: a local page (127.0.0.1 only). Approval creates a signed
DRAFT and a record; nothing is emailed, posted or sent anywhere.

GET  /         the pack, every claim with its type, verification status and sources, a privacy and
               honesty checklist, and the approver's name
POST /approve  requires every checklist box and a name; re-runs the verifier on the claims (they must
               all still pass), then writes outputs_posthoc/drafts/APPROVED_<pack_id>.md and appends
               the record to outputs_posthoc/approvals.jsonl and Atlas `approvals`.
               signature = SHA-256(pack markdown + approver + timestamp + verifier SHA)
POST /reject   records a rejection with the reason (no draft)
GET  /canary   the seeded false claim and the verifier's rejection (labelled as seeded, for the demo)
Run: PYTHONPATH=. .venv/Scripts/python -m agent.approve [--port 8765]
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import pack as P
from . import verify as V

RECORDS = P.OUTDIR / "approvals.jsonl"
MIRROR = P.OUTDIR == V.ROOT / "outputs_posthoc"   # no Atlas writes from test runs
CHECKLIST = [
    "I have read every claim and opened its sources.",
    "Our own numbers are labelled relative / indicative, never as certified dB.",
    "The limits section says what the evidence cannot show (direction ground, re-grinding, the 92.8 dB source).",
    "No personal data, speech or raw audio is included.",
    "I understand approval creates a draft only: nothing is sent automatically.",
]
CSS = """body{font:15px/1.5 system-ui,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#1b1b1b}
pre{white-space:pre-wrap;background:#f6f6f4;padding:16px;border-radius:8px}table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #ddd;padding:6px;text-align:left;vertical-align:top;font-size:13px}
.ok{color:#17663a}.bad{color:#a11}button{font-size:15px;padding:8px 18px;margin-right:8px}"""


def _load():
    inv = json.loads(P.INV.read_text(encoding="utf-8"))
    md, pid = P.render(inv)
    return inv, md, pid


def _page(body: str) -> bytes:
    return f"<!doctype html><meta charset=utf-8><title>Tubesense approval</title><style>{CSS}</style>{body}".encode()


def _record(rec: dict) -> dict:
    with open(RECORDS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    if not MIRROR:
        return rec
    try:
        from atlas.db import db
        db().approvals.insert_one({**rec, "_id": rec["signature"] if rec.get("signature") else rec["ts"]})
    except Exception as e:  # noqa: BLE001
        rec["atlas"] = f"skipped: {type(e).__name__}"
    return rec


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/canary"):
            r = V.verify_all([V.CANARY])[0]
            self._send(_page(f"<h2>Seeded test claim (deliberately false, for the demo)</h2><p>{html.escape(V.CANARY['text'])}</p>"
                             f"<p class=bad><b>Verifier: {'accepted' if r['ok'] else 'REJECTED'}</b></p><ul>" +
                             "".join(f"<li>{html.escape(x)}</li>" for x in r["reasons"]) + "</ul><a href=/>back</a>"))
            return
        inv, md, pid = _load()
        checks = {c["id"]: c for c in V.verify_all(P.for_verifier(inv["verified_claims"]))}
        rows = "".join(
            f"<tr><td>{html.escape(c['id'])}</td><td>{c['type']}</td><td class={'ok' if checks[c['id']]['ok'] else 'bad'}>"
            f"{'verified' if checks[c['id']]['ok'] else 'FAILS: ' + html.escape('; '.join(checks[c['id']]['reasons']))}</td>"
            f"<td>{html.escape(c['text'])}</td><td>{html.escape(', '.join(x['chunk_id'] for x in c.get('citations') or []))}"
            f"{html.escape(', '.join(c.get('stat_keys') or []))}</td></tr>" for c in inv["verified_claims"])
        boxes = "".join(f"<label><input type=checkbox name=c{i}> {html.escape(t)}</label><br>" for i, t in enumerate(CHECKLIST))
        self._send(_page(
            f"<h1>Approve evidence pack <code>{pid}</code></h1><p>{len(inv['verified_claims'])} verified claims, "
            f"{len(inv['dropped_claims'])} dropped by the verifier. Nothing is sent: approval creates a signed draft.</p>"
            f"<form method=post action=/approve><h3>Checklist</h3>{boxes}<p>Approver name: <input name=who required></p>"
            f"<input type=hidden name=pid value={pid}><button>Approve as draft</button></form>"
            f"<form method=post action=/reject><p>Or reject, reason: <input name=why size=60> <button>Reject</button></p></form>"
            f"<p><a href=/canary>Show the verifier rejecting a seeded false claim</a></p>"
            f"<h3>Claims</h3><table><tr><th>id</th><th>type</th><th>check</th><th>claim</th><th>sources / stats</th></tr>{rows}</table>"
            f"<h3>Pack</h3><pre>{html.escape(md)}</pre>"))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode()).items()}
        inv, md, pid = _load()
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if self.path == "/reject":
            _record({"ts": ts, "decision": "rejected", "pack_id": pid, "reason": form.get("why", "")})
            self._send(_page(f"<h2>Rejected</h2><p>Recorded, no draft created.</p><a href=/>back</a>"))
            return
        missing = [t for i, t in enumerate(CHECKLIST) if f"c{i}" not in form]
        who = form.get("who", "").strip()
        if missing or not who or form.get("pid") != pid:
            why = ["the pack changed since the page was loaded"] if form.get("pid") != pid else []
            self._send(_page("<h2>Not approved</h2><ul>" + "".join(f"<li>unchecked: {html.escape(m)}</li>" for m in missing) +
                             ("<li>approver name missing</li>" if not who else "") +
                             "".join(f"<li>{w}</li>" for w in why) + "</ul><a href=/>back</a>"), 400)
            return
        fails = [c for c in V.verify_all(P.for_verifier(inv["verified_claims"])) if not c["ok"]]
        if fails:
            self._send(_page(f"<h2>Not approved</h2><p>{len(fails)} claims no longer verify.</p><a href=/>back</a>"), 409)
            return
        sig = hashlib.sha256((md + who + ts + V.VERIFIER_SHA).encode()).hexdigest()
        out = P.DRAFTS / f"APPROVED_{pid}.md"
        P.DRAFTS.mkdir(parents=True, exist_ok=True)
        out.write_text(md + f"\n_Approved as a DRAFT by {who} at {ts}. Signature {sig}. Not sent._\n", encoding="utf-8")
        rec = _record({"ts": ts, "decision": "approved", "pack_id": pid, "approver": who, "signature": sig,
                       "verifier_sha": V.VERIFIER_SHA, "checklist": CHECKLIST, "draft": (out.relative_to(V.ROOT) if out.is_relative_to(V.ROOT) else out).as_posix(),
                       "n_claims": len(inv["verified_claims"])})
        self._send(_page(f"<h2>Approved as a draft</h2><p>Signature <code>{sig}</code></p><p>Draft: {rec['draft']}</p>"
                         f"<p>Nothing was sent.</p>"))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    print(f"approval page: http://127.0.0.1:{a.port}/  (local only; Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
