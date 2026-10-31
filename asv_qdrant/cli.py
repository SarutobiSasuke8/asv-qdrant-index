"""asv-qdrant: create | ingest | search | status | eval | smoke.

Credentials come from QDRANT_URL and QDRANT_API_KEY only. `--local` runs
against an in-memory Qdrant (no credentials) for offline checks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from qdrant_client import QdrantClient

from .core import COLLECTION, DIM, MODEL, require_env
from .evals import DEFAULT_EVALS, run_evals
from .index import ensure_collection, ingest, search, status

SMOKE_QUERY = "Qdrant retrieval index over ASV engineering notes, not a second wiki"
SMOKE_EXPECT = "Workspace/Grok/Engineering/2026-10-30 Qdrant ASV Engineering Memory.md"
KINDS = ["design-note", "portfolio", "ticket", "reply", "fyi", "blocker", "claim", "message"]


def client_for(local: bool) -> QdrantClient:
    if local:
        return QdrantClient(":memory:")
    url, key = require_env()
    return QdrantClient(url=url, api_key=key, timeout=60)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="asv-qdrant", description=__doc__)
    p.add_argument("--local", action="store_true", help="in-memory Qdrant, no credentials")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create", help="create the collection and payload indexes if missing")
    ing = sub.add_parser("ingest", help="diff-ingest the ASV")
    ing.add_argument("asv_root", type=Path)
    ing.add_argument("--full", action="store_true", help="re-embed every file")
    ing.add_argument("--dry-run", action="store_true", help="select and chunk only")
    ing.add_argument("--only", help="limit to one path glob; skips deletion of other paths")
    s = sub.add_parser("search", help="top-k search")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--kind", action="append", choices=KINDS, help="repeatable")
    s.add_argument("--sender", help="e.g. lingxi-engineer-grok-bot")
    s.add_argument("--since", help="YYYY-MM-DD, by note date")
    sub.add_parser("status", help="point count and the ASV commits they came from")
    ev = sub.add_parser("eval", help="known-answer questions; reports hit rate in the top k")
    ev.add_argument("--file", type=Path, default=DEFAULT_EVALS)
    ev.add_argument("-k", type=int, default=5)
    sm = sub.add_parser("smoke", help="ingest then prove a known design note is found")
    sm.add_argument("asv_root", type=Path)
    sm.add_argument("--only", help="limit the ingest to one path glob, e.g. 'Workspace/Grok/Engineering/*'")
    args = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252

    def show(obj) -> None:
        print(json.dumps(obj, indent=2, ensure_ascii=False))

    if args.cmd == "ingest" and args.dry_run:
        show(ingest(None, args.asv_root.resolve(), dry_run=True, only=args.only))
        return 0

    client = client_for(args.local)
    if args.cmd == "create":
        created = ensure_collection(client)
        print(f"{COLLECTION}: {'created' if created else 'exists'} ({MODEL}, {DIM}-d, cosine)")
    elif args.cmd == "ingest":
        ensure_collection(client)
        show(ingest(client, args.asv_root.resolve(), full=args.full, only=args.only))
    elif args.cmd == "search":
        show(search(client, args.query, args.k, args.kind, args.sender, args.since))
    elif args.cmd == "status":
        show(status(client))
    elif args.cmd == "eval":
        result = run_evals(client, args.file, args.k)
        show(result)
        return 0 if result["passed"] == result["total"] else 1
    elif args.cmd == "smoke":
        ensure_collection(client)
        stats = ingest(client, args.asv_root.resolve(), only=args.only)
        hits = search(client, SMOKE_QUERY, 5)
        show({"ingest": stats, "query": SMOKE_QUERY, "hits": hits})
        ok = any(h["path"] == SMOKE_EXPECT for h in hits)
        print(f"SMOKE {'PASS' if ok else 'FAIL'}: expected {SMOKE_EXPECT} in top 5")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
