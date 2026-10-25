"""Create the Tubesense collections and search indexes on Atlas. Idempotent: safe to re-run.

Checks the server is MongoDB >= 8.0 ($rankFusion), creates the collections, then the three
search indexes that carry the scored features:
  documents.docs_text  Atlas Search (full text)
  documents.docs_auto  Automated Embedding (autoEmbed, Voyage voyage-4): also Voyage AI
  yaw_windows.yaw_vec  Vector Search over sensor fingerprints (64-d, euclidean, exact ENN)
Hybrid Search is a $rankFusion query over docs_text + docs_auto (atlas/search.py).
Stream Processing is set up by atlas/stream_setup.py (bodies in atlas/stream/), not here; it writes
ingest_live -> live_view (atlas/stream_replay.py feeds it, atlas/live_view.py reads it).

Run:   .venv/Scripts/python -m atlas.setup            (create + wait until queryable)
       .venv/Scripts/python -m atlas.setup --status   (report only)
"""
from __future__ import annotations

import argparse
import time

from pymongo.errors import CollectionInvalid, OperationFailure
from pymongo.operations import SearchIndexModel

from atlas.db import client, db

REGULAR = ["documents", "section_stats", "yaw_windows", "attempts", "claims", "approvals",
           "ingest_live", "live_view"]
TIMESERIES = {"noise_1s": {"timeField": "t", "metaField": "meta", "granularity": "seconds"}}

INDEXES = {
    "documents": [
        SearchIndexModel(name="docs_text", type="search", definition={
            "mappings": {"dynamic": False, "fields": {
                "text": {"type": "string"},
                "title": {"type": "string"},
                "doc_type": {"type": "token"},
                "sections": {"type": "token"},
                "publisher": {"type": "token"},
            }}}),
        SearchIndexModel(name="docs_auto", type="vectorSearch", definition={
            "fields": [
                {"type": "autoEmbed", "modality": "text", "path": "text", "model": "voyage-4"},
                {"type": "filter", "path": "doc_type"},
                {"type": "filter", "path": "sections"},
            ]}),
    ],
    "yaw_windows": [
        SearchIndexModel(name="yaw_vec", type="vectorSearch", definition={
            "fields": [
                {"type": "vector", "path": "v", "numDimensions": 64, "similarity": "euclidean"},
                {"type": "filter", "path": "track"},
                {"type": "filter", "path": "direction"},
                {"type": "filter", "path": "run_id"},
                {"type": "filter", "path": "W"},
            ]}),
    ],
}


def server_version(d) -> tuple[int, ...]:
    v = d.command("buildInfo")["version"]
    return tuple(int(x) for x in v.split(".")[:2] if x.isdigit())


def ensure_collections(d) -> None:
    have = set(d.list_collection_names())
    for name in REGULAR:
        if name not in have:
            d.create_collection(name)
            print(f"created collection {name}")
    for name, ts in TIMESERIES.items():
        if name not in have:
            try:
                d.create_collection(name, timeseries=ts)
                print(f"created time series collection {name} (not a scored feature)")
            except CollectionInvalid:
                pass


def ensure_indexes(d) -> list[str]:
    failed = []
    for coll, models in INDEXES.items():
        have = {i["name"] for i in d[coll].list_search_indexes()}
        for m in models:
            name = m.document["name"]
            if name in have:
                continue
            try:
                d[coll].create_search_index(m)
                print(f"requested {coll}.{name}")
            except OperationFailure as e:
                failed.append(f"{coll}.{name}: {e.details.get('errmsg', e) if e.details else e}")
    return failed


def status(d) -> dict[str, str]:
    out = {}
    for coll in INDEXES:
        for i in d[coll].list_search_indexes():
            out[f"{coll}.{i['name']}"] = f"{i.get('status')} queryable={i.get('queryable')}"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="report only, change nothing")
    ap.add_argument("--wait", type=float, default=300, help="seconds to wait for indexes to be queryable")
    args = ap.parse_args()

    c = client()
    c.admin.command("ping")
    d = db(c)
    ver = server_version(d)
    print(f"connected: MongoDB {'.'.join(map(str, ver))}, database '{d.name}'")
    if ver < (8, 0):
        print("WARNING: $rankFusion (Hybrid Search) needs MongoDB >= 8.0: upgrade the cluster version")

    if not args.status:
        ensure_collections(d)
        failed = ensure_indexes(d)
        for f in failed:
            print("FAILED", f)
        if any("docs_auto" in f for f in failed):
            print("autoEmbed failed: check cluster auto-scaling is on (cap max at M30). Fallback: embed "
                  "the public chunks client-side with a Voyage model into a plain 'vector' index.")
        t0 = time.time()
        while time.time() - t0 < args.wait:
            st = status(d)
            if st and all("queryable=True" in s for s in st.values()):
                break
            time.sleep(10)

    for k, s in sorted(status(d).items()):
        print(f"{k:28s} {s}")


if __name__ == "__main__":
    main()
