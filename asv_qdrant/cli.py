"""asv-qdrant: create | ingest | search | smoke.

Credentials come from QDRANT_URL and QDRANT_API_KEY only. `--local` runs
against an in-memory Qdrant (no credentials) for offline checks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qdrant_client import QdrantClient

from .core import COLLECTION, DIM, MODEL, require_env
from .index import ensure_collection, ingest, search

SMOKE_QUERY = "Qdrant retrieval index over ASV engineering notes, not a second wiki"
SMOKE_EXPECT = "Workspace/Grok/Engineering/2026-10-30 Qdrant ASV Engineering Memory.md"


def client_for(args) -> QdrantClient:
    if args.local:
        return QdrantClient(":memory:")
    url, key = require_env()
    return QdrantClient(url=url, api_key=key, timeout=60)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="asv-qdrant", description=__doc__)
    p.add_argument("--local", action="store_true", help="in-memory Qdrant, no credentials")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create", help="create the collection if missing")
    ing = sub.add_parser("ingest", help="diff-ingest the ASV")
    ing.add_argument("asv_root", type=Path)
    ing.add_argument("--full", action="store_true", help="re-embed every file")
    ing.add_argument("--dry-run", action="store_true", help="select and chunk only")
    s = sub.add_parser("search", help="top-k search")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=5)
    sm = sub.add_parser("smoke", help="ingest then prove a known design note is found")
    sm.add_argument("asv_root", type=Path)
    args = p.parse_args(argv)

    if args.cmd == "ingest" and args.dry_run:
        print(json.dumps(ingest(None, args.asv_root.resolve(), dry_run=True), indent=2))
        return 0

    client = client_for(args)
    if args.cmd == "create":
        created = ensure_collection(client)
        print(f"{COLLECTION}: {'created' if created else 'exists'} ({MODEL}, {DIM}-d, cosine)")
    elif args.cmd == "ingest":
        ensure_collection(client)
        print(json.dumps(ingest(client, args.asv_root.resolve(), full=args.full), indent=2))
    elif args.cmd == "search":
        print(json.dumps(search(client, args.query, args.k), indent=2, ensure_ascii=False))
    elif args.cmd == "smoke":
        ensure_collection(client)
        stats = ingest(client, args.asv_root.resolve())
        hits = search(client, SMOKE_QUERY, 5)
        print(json.dumps({"ingest": stats, "query": SMOKE_QUERY, "hits": hits}, indent=2, ensure_ascii=False))
        ok = any(h["path"] == SMOKE_EXPECT for h in hits)
        print(f"SMOKE {'PASS' if ok else 'FAIL'}: expected {SMOKE_EXPECT} in top 5")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
