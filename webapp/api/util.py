"""Shared helpers for the web app backend: repo paths, the API error type, JSON readers, module probes.

Light on purpose: this module is imported by the server process, which must not import pandas, scipy or
matplotlib (webapp/DESIGN.md section 1, rule 7).
"""
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
POSTHOC = ROOT / "outputs_posthoc"
OUT_DIR = Path(os.environ.get("TUBESENSE_POSTHOC_OUT", POSTHOC))   # approvals / drafts / asks / investigation


class ApiError(Exception):
    """An error with an HTTP status and the {"error", "message"} body of webapp/DESIGN.md section 6.1."""

    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def body(self) -> dict:
        return {"error": self.code, "message": self.message, **self.extra}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mtime_iso(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def read_json(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_jsonl(p: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def has_module(name: str) -> bool:
    """find_spec without importing the module (the parent package is imported)."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def have_env(name: str) -> bool:
    return bool(os.environ.get(name))


def exc_text(e: BaseException, n: int = 200) -> str:
    return f"{type(e).__name__}: {e}"[:n]
