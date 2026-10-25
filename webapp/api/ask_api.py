"""Ask Argon endpoints: availability, start (job), cached answers.

What: `agent.ask` (built separately) is run as a subprocess (`python -m agent.ask "<q>" --jsonl [--cached]`) and
its events (start, retrieval, draft, verdicts, review, final, error) are relayed verbatim as SSE by the job
manager. The cached list calls agent.ask.list_cached() in process (imported lazily per request); a cached answer is
served through agent.ask.replay(name), which re-verifies and re-gates it now and labels it CACHED.
Why: the web app never calls Gemini itself. Only the typed question, public document text and summary statistics
leave the laptop, inside agent.ask. If agent.ask is missing every endpoint answers with an honest not-available state.
"""
from __future__ import annotations

import re

from . import egress
from .util import OUT_DIR, ApiError, exc_text, has_module, have_env

THINKING, MODEL = "medium", "gemini-4-argon"
_NAME = re.compile(r"^[\w.\-]+\.json$")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
ASKS = OUT_DIR / "asks"


def status() -> dict:
    ok = has_module("agent.ask")
    key = have_env("GEMINI_API_KEY")
    return {"available": ok, "reason": None if ok else "agent.ask not installed yet", "gemini_api_key": key,
            "model": MODEL, "thinking": THINKING}


def cached_list() -> dict:
    if not has_module("agent.ask"):
        return {"available": False, "reason": "agent.ask not installed yet", "items": []}
    try:
        from agent import ask as A
        items = [{**it, "name": it["file"].replace("\\", "/").rsplit("/", 1)[-1]} for it in A.list_cached()]
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": exc_text(e), "items": []}
    return {"available": True, "items": items}


def cached_file(name: str) -> dict:
    """One saved answer, served through agent.ask.replay(name): labelled CACHED with its original ts, the verifier
    re-run on its claims now and the answer line re-gated now (fields cached, label, exact_match, superseded_by,
    reverified {ts, verifier_sha, same_verifier, same_corpus, all_ok, results}). Never the raw file: it says cached false."""
    if not _NAME.match(name):
        raise ApiError(404, "not_found", "no such cached answer")
    p = ASKS / name
    try:
        p.resolve().relative_to(ASKS.resolve())
    except ValueError:
        raise ApiError(404, "not_found", "no such cached answer") from None
    if not p.is_file():
        raise ApiError(404, "not_found", "no such cached answer")
    if not has_module("agent.ask"):
        raise ApiError(503, "unavailable", "agent.ask not installed yet")
    from agent import ask as A
    try:
        return A.replay(name)
    except LookupError:
        raise ApiError(404, "not_found", "no such cached answer") from None


def start(jobs, body: dict):
    q = body.get("question")
    if not isinstance(q, str):
        raise ApiError(400, "bad_request", "question must be a string")
    q = _CTRL.sub(" ", q).strip()
    if not 5 <= len(q) <= 300:
        raise ApiError(400, "bad_request", "question must be 5 to 300 characters")
    cached = body.get("cached", False)
    if not isinstance(cached, bool):
        raise ApiError(400, "bad_request", "cached must be true or false")
    if not has_module("agent.ask"):
        raise ApiError(503, "unavailable", "agent.ask not installed yet")
    if not cached and not have_env("GEMINI_API_KEY"):
        raise ApiError(503, "unavailable", "GEMINI_API_KEY is not set (.env): a live ask needs Gemini")
    args = [q, "--jsonl"] + (["--cached"] if cached else [])
    job = jobs.start("ask", "agent.ask", args, {"question": q, "cached": cached},
                     ("cached answer: " if cached else "ask Argon: ") + q[:80])
    if not cached:
        egress.add("Google Gemini API (gemini-4-argon)", "your question, public document chunks, summary statistics",
                   "/api/ask/start")
        egress.add("MongoDB Atlas", "your question (hybrid search over public documents)", "/api/ask/start")
    return job
