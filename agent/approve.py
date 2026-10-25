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
The approval logic lives in load / checks / canary / records / approve / reject, so this page and the web app
(webapp/api/pack_api.py) share one implementation; the page's HTML and the files written are unchanged.
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


def load() -> tuple[dict, str, str]:
    """(investigation, rendered pack markdown, pack_id) from the current investigation.json."""
    inv = json.loads(P.INV.read_text(encoding="utf-8"))
    md, pid = P.render(inv)
    return inv, md, pid


_load = load


def checks(inv: dict) -> dict[str, dict]:
    """{claim_id: {"id", "ok", "reasons"}}: the verifier run now on the pack's verified claims."""
    return {c["id"]: c for c in V.verify_all(P.for_verifier(inv["verified_claims"]))}


def canary() -> dict:
    """The seeded false claim and the verifier's verdict on it, run now."""
    return {**V.verify_all([V.CANARY])[0], "claim": V.CANARY}


def records(pack_id: str | None = None) -> list[dict]:
    """Lines of approvals.jsonl (all, or those for one pack); [] when the file does not exist."""
    if not RECORDS.exists():
        return []
    rows = [json.loads(x) for x in RECORDS.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [r for r in rows if pack_id is None or r.get("pack_id") == pack_id]


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


def approve(pid: str | None, who: str, checked: list[int] | set[int]) -> tuple[int, dict]:
    """The approval step. 400 {"missing", "approver_missing", "pack_changed"}, 409 {"fails"} or 200
    {"record", "signature", "draft", "md"} (after writing the draft and the record)."""
    inv, md, cur = load()
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    missing = [t for i, t in enumerate(CHECKLIST) if i not in set(checked)]
    who = (who or "").strip()
    if missing or not who or pid != cur:
        return 400, {"missing": missing, "approver_missing": not who, "pack_changed": pid != cur}
    fails = [c for c in checks(inv).values() if not c["ok"]]
    if fails:
        return 409, {"fails": [{"id": c["id"], "reasons": c["reasons"]} for c in fails]}
    sig = hashlib.sha256((md + who + ts + V.VERIFIER_SHA).encode()).hexdigest()
    out = P.DRAFTS / f"APPROVED_{cur}.md"
    P.DRAFTS.mkdir(parents=True, exist_ok=True)
    out.write_text(md + f"\n_Approved as a DRAFT by {who} at {ts}. Signature {sig}. Not sent._\n", encoding="utf-8")
    rec = _record({"ts": ts, "decision": "approved", "pack_id": cur, "approver": who, "signature": sig,
                   "verifier_sha": V.VERIFIER_SHA, "checklist": CHECKLIST, "draft": (out.relative_to(V.ROOT) if out.is_relative_to(V.ROOT) else out).as_posix(),
                   "n_claims": len(inv["verified_claims"])})
    return 200, {"record": rec, "signature": sig, "draft": rec["draft"], "md": md}


def reject(pid: str, why: str) -> dict:
    """Record a rejection with its reason (no draft)."""
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return _record({"ts": ts, "decision": "rejected", "pack_id": pid, "reason": why})


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/canary"):
            r = canary()
            self._send(_page(f"<h2>Seeded test claim (deliberately false, for the demo)</h2><p>{html.escape(V.CANARY['text'])}</p>"
                             f"<p class=bad><b>Verifier: {'accepted' if r['ok'] else 'REJECTED'}</b></p><ul>" +
                             "".join(f"<li>{html.escape(x)}</li>" for x in r["reasons"]) + "</ul><a href=/>back</a>"))
            return
        inv, md, pid = load()
        chk = checks(inv)
        rows = "".join(
            f"<tr><td>{html.escape(c['id'])}</td><td>{c['type']}</td><td class={'ok' if chk[c['id']]['ok'] else 'bad'}>"
            f"{'verified' if chk[c['id']]['ok'] else 'FAILS: ' + html.escape('; '.join(chk[c['id']]['reasons']))}</td>"
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
        if self.path == "/reject":
            reject(load()[2], form.get("why", ""))
            self._send(_page(f"<h2>Rejected</h2><p>Recorded, no draft created.</p><a href=/>back</a>"))
            return
        code, r = approve(form.get("pid"), form.get("who", ""), {i for i in range(len(CHECKLIST)) if f"c{i}" in form})
        if code == 400:
            why = ["the pack changed since the page was loaded"] if r["pack_changed"] else []
            self._send(_page("<h2>Not approved</h2><ul>" + "".join(f"<li>unchecked: {html.escape(m)}</li>" for m in r["missing"]) +
                             ("<li>approver name missing</li>" if r["approver_missing"] else "") +
                             "".join(f"<li>{w}</li>" for w in why) + "</ul><a href=/>back</a>"), 400)
        elif code == 409:
            self._send(_page(f"<h2>Not approved</h2><p>{len(r['fails'])} claims no longer verify.</p><a href=/>back</a>"), 409)
        else:
            self._send(_page(f"<h2>Approved as a draft</h2><p>Signature <code>{r['signature']}</code></p><p>Draft: {r['draft']}</p>"
                             f"<p>Nothing was sent.</p>"))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    print(f"approval page: http://127.0.0.1:{a.port}/  (local only; Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
