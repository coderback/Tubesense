"""Evidence pack endpoints: pack with claims verified now, history, sources, canary, approve, reject.

What: wraps agent.approve (load / checks / canary / records / approve / reject), agent.pack and agent.verify.
Approval is the human's action: it needs a POST from the browser's approval form, from the laptop (loopback)
unless the server was started with --lan-approve. This module never approves anything by itself.
Why: one implementation of the approval logic serves the standalone page (agent/approve.py, port 8765) and the web
app. The pack, the claims and their sources are what the file says; only `check` (verifier) and `status`
(approvals.jsonl) are computed now. TUBESENSE_POSTHOC_OUT is read when the server starts (agent.pack reads it at
import), so approval tests need a second server on another port pointing at a scratch folder.
"""
from __future__ import annotations

import threading
from pathlib import Path

from . import egress
from .util import POSTHOC, ApiError, mtime_iso, now_iso, read_json, rel

_lock = threading.Lock()


def _mods():
    from agent import approve as AP
    from agent import pack as P
    from agent import verify as V
    return AP, P, V


def _load():
    AP, P, V = _mods()
    if not P.INV.exists():
        raise ApiError(503, "unavailable", "Investigator output not present: the pack cannot be shown")
    try:
        return AP.load()
    except (OSError, ValueError, KeyError) as e:
        raise ApiError(503, "unavailable", f"Investigator output could not be read: {type(e).__name__}") from e


def _status(recs: list[dict]) -> dict:
    last = recs[-1] if recs else None
    if last is None:
        return {"state": "awaiting", "latest": None}
    return {"state": "approved" if last.get("decision") == "approved" else "rejected", "latest": last}


def pack() -> dict:
    AP, P, V = _mods()
    inv, md, pid = _load()
    chunks, stats = V.load_chunks(), V.load_stats()
    chk = AP.checks(inv)
    claims = []
    for c in inv["verified_claims"]:
        cites = []
        for x in c.get("citations") or []:
            ch = chunks.get(x["chunk_id"], {})
            cites.append({"chunk_id": x["chunk_id"], "quote": x.get("quote"), "title": ch.get("title"),
                          "publisher": ch.get("publisher"), "doc_date": ch.get("doc_date"), "page": ch.get("page"),
                          "url": ch.get("url")})
        st = []
        for k in c.get("stat_keys") or []:
            s = stats.get(k)
            st.append({"key": k, "value": s.get("value") if s else None, "unit": s.get("unit") if s else None,
                       "n": s.get("n") if s else None, "source": s.get("source") if s else None})
        claims.append({"id": c["id"], "section": c.get("section"), "section_title": P.TITLES.get(c.get("section"), c.get("section")),
                       "type": c["type"], "type_label": P.LABEL.get(c["type"], c["type"]), "text": c["text"],
                       "citations": cites, "stats": st, "formula": c.get("formula"),
                       "check": {"ok": chk[c["id"]]["ok"], "reasons": chk[c["id"]]["reasons"]}})
    recs = AP.records(pid)
    drafts = sorted(rel(p) for p in P.DRAFTS.glob("*.md")) if P.DRAFTS.exists() else []
    return {"label": "PRECOMPUTED", "generated": inv.get("ts"), "pack_id": pid, "model": inv.get("model"),
            "thinking": inv.get("thinking"), "corpus_hash": inv.get("corpus_hash"),
            "verifier_sha": inv.get("verifier_sha"), "verifier_sha_now": V.VERIFIER_SHA,
            "n_verified": len(claims), "n_dropped": len(inv.get("dropped_claims") or []),
            "headline_ids": inv.get("headline_claim_ids") or [], "claims": claims,
            "dropped": inv.get("dropped_claims") or [], "asks": inv.get("asks") or [], "checklist": AP.CHECKLIST, "md": md,
            "status": _status(recs), "approvals": recs,
            "posthoc_out": rel(P.OUTDIR), "posthoc_out_is_default": P.OUTDIR == POSTHOC, "mirror": AP.MIRROR, "drafts": drafts}


# --------------------------------------------------------------------------- history
def _steps_from_draft(label: str, f: Path) -> dict | None:
    d = read_json(f)
    if not d:
        return None
    rounds = [{"round": r["round"], "proposed": len(r.get("verification") or []),
               "rejected": sum(1 for v in (r.get("verification") or []) if not v.get("ok")),
               "seconds": r.get("seconds")} for r in d.get("rounds", [])]
    return {"kind": "draft", "label": label, "file": rel(f), "ts": d.get("ts"), "rounds": rounds,
            "verified": len(d.get("verified_claims") or []), "dropped": len(d.get("dropped_claims") or []),
            "revision": d.get("revision")}


def _review(label: str, f: Path) -> dict | None:
    d = read_json(f)
    if not isinstance(d, list):
        return None
    major = [x for x in d if x.get("severity") == "major"]
    return {"kind": "review", "label": label, "file": rel(f), "file_mtime": mtime_iso(f), "n": len(d), "major": len(major),
            "minor": sum(1 for x in d if x.get("severity") == "minor"),
            "reviewer": "Claude Code review agents run by the developer, against the full sources",
            "examples": [{"id": x.get("id"), "claim_id": x.get("claim_id"), "severity": x.get("severity"),
                          "phrase": x.get("phrase"), "problem": (x.get("problem") or "")[:400]} for x in major[:4]]}


def history() -> dict:
    steps = []
    for s in (_steps_from_draft("Draft 1", POSTHOC / "investigation_draft1.json"),
              _review("Independent review 1", POSTHOC / "pack_review.json"),
              _steps_from_draft("Draft 2 (revised from review 1)", POSTHOC / "investigation_draft2.json"),
              _review("Recheck", POSTHOC / "pack_review2.json")):
        if s:
            steps.append(s)
    fin = _steps_from_draft("Final (targeted revision)", POSTHOC / "investigation.json")
    if fin:
        fin["kind"] = "final"
        steps.append(fin)
    return {"steps": steps}


# --------------------------------------------------------------------------- sources, canary
def source(chunk_id: str, quote: str | None) -> dict:
    _, _, V = _mods()
    ch = V.load_chunks().get(chunk_id)
    if ch is None:
        raise ApiError(404, "not_found", f"no corpus chunk {chunk_id}")
    out = {k: ch.get(k) for k in ("chunk_id", "doc_id", "title", "publisher", "doc_date", "page", "page_end", "url",
                                  "reliability", "text")}
    out["chunk_id"] = out["chunk_id"] or chunk_id
    if quote:
        out["quote_found"] = V.norm(quote) in V.norm(ch.get("text") or "")
    return out


def canary() -> dict:
    AP, _, V = _mods()
    r = AP.canary()
    return {"label": "SEEDED", "claim": r["claim"], "ok": r["ok"], "reasons": r["reasons"], "checked_at": now_iso(),
            "verifier_sha": V.VERIFIER_SHA}


# --------------------------------------------------------------------------- approve / reject
def _check_loopback(client_ip: str, allow_lan: bool) -> None:
    if allow_lan or client_ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        return
    raise ApiError(403, "forbidden", "Approval is only allowed from the laptop running the server.")


def approve(body: dict, client_ip: str, allow_lan: bool) -> tuple[int, dict]:
    _check_loopback(client_ip, allow_lan)
    AP, P, _ = _mods()
    pid, who, checked = body.get("pack_id"), body.get("approver"), body.get("checked")
    if not isinstance(pid, str) or not isinstance(who, str):
        raise ApiError(400, "bad_request", "pack_id and approver are required strings")
    who = who.strip()
    if len(who) > 80:
        raise ApiError(400, "bad_request", "approver name is limited to 80 characters")
    if not isinstance(checked, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in checked):
        raise ApiError(400, "bad_request", "checked must be a list of checklist indices")
    with _lock:
        code, r = AP.approve(pid, who, set(checked))
    if code == 400:
        why = []
        if r["missing"]:
            why.append(f"{len(r['missing'])} checklist item(s) not ticked")
        if r["approver_missing"]:
            why.append("approver name missing")
        if r["pack_changed"]:
            why.append("the pack changed since the page was loaded")
        return 400, {"error": "incomplete", "message": "; ".join(why), **r}
    if code == 409:
        return 409, {"error": "verification_failed", "message": f"{len(r['fails'])} claims no longer verify", "fails": r["fails"]}
    rec = r["record"]
    atlas = ("not mirrored (test output dir)" if not AP.MIRROR else rec.get("atlas") or "mirrored")
    if AP.MIRROR and "atlas" not in rec:
        egress.add("MongoDB Atlas `approvals`", "approval record (name, pack id, signature)", "/api/pack/approve")
    return 200, {"decision": "approved", "record": rec, "signature": r["signature"], "draft": r["draft"], "atlas": atlas}


def reject(body: dict, client_ip: str, allow_lan: bool) -> dict:
    _check_loopback(client_ip, allow_lan)
    AP, _, _ = _mods()
    pid, why = body.get("pack_id"), body.get("reason")
    if not isinstance(pid, str) or not isinstance(why, str) or not why.strip():
        raise ApiError(400, "bad_request", "pack_id and a non-empty reason are required")
    cur = _load()[2]
    if pid != cur:
        raise ApiError(400, "incomplete", "the pack changed since the page was loaded", pack_changed=True)
    with _lock:
        rec = AP.reject(pid, why.strip()[:1000])
    if AP.MIRROR and "atlas" not in rec:
        egress.add("MongoDB Atlas `approvals`", "rejection record (pack id, reason)", "/api/pack/reject")
    return {"decision": "rejected", "record": rec}
