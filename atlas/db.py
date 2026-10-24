"""Connection helper: reads MONGODB_URI from the repo's gitignored .env (never printed)."""
from __future__ import annotations

import os
from pathlib import Path

from pymongo import MongoClient
from pymongo.database import Database

ROOT = Path(__file__).resolve().parents[1]
DB_NAME = "tubesense"


def load_env(path: Path = ROOT / ".env") -> None:
    """Put KEY=value lines from .env into os.environ (existing variables win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def client(timeout_ms: int = 10_000) -> MongoClient:
    load_env()
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        raise SystemExit("MONGODB_URI is empty: paste the Atlas connection string into .env")
    if "<password>" in uri:
        raise SystemExit("MONGODB_URI still has the <password> placeholder: put the database user's password in .env")
    return MongoClient(uri, appname="tubesense", serverSelectionTimeoutMS=timeout_ms)


def db(c: MongoClient | None = None) -> Database:
    return (c or client())[DB_NAME]
