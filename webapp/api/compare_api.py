"""GET /api/compare: the Why Argon model comparison files, as they are on disk.

What: returns outputs_posthoc/model_comparison.json (parsed) and model_comparison.md (text) with their mtimes;
`available` is true when either exists. Files only: nothing is computed or invented here.
Why: the JSON layout is owned by agent/compare_models.py; the screen renders the Markdown when the JSON shape is
unknown or missing.
"""
from __future__ import annotations

import json

from .util import POSTHOC, mtime_iso

JSON, MD = POSTHOC / "model_comparison.json", POSTHOC / "model_comparison.md"


def compare() -> dict:
    out = {"available": False, "json": None, "md": None, "json_mtime": None, "md_mtime": None}
    if JSON.exists():
        try:
            out["json"], out["json_mtime"] = json.loads(JSON.read_text(encoding="utf-8")), mtime_iso(JSON)
        except (OSError, ValueError):
            out["json_error"] = "model_comparison.json exists but could not be parsed"
    if MD.exists():
        try:
            out["md"], out["md_mtime"] = MD.read_text(encoding="utf-8"), mtime_iso(MD)
        except OSError:
            pass
    out["available"] = out["json"] is not None or out["md"] is not None
    return out
