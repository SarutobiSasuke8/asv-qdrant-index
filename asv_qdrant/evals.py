"""Known-answer questions: does the right ASV note come back in the top k?

Each case names a question and one or more acceptable paths. A case passes
when any acceptable path is in the top k. Re-run after scope or model changes.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from qdrant_client import QdrantClient

from .index import search

DEFAULT_EVALS = Path(__file__).resolve().parent.parent / "evals" / "questions.yaml"


def run_evals(client: QdrantClient, file: Path, k: int = 5) -> dict:
    cases = yaml.safe_load(file.read_text(encoding="utf-8"))
    results = []
    for case in cases:
        expect = case["expect"] if isinstance(case["expect"], list) else [case["expect"]]
        hits = search(client, case["question"], k, case.get("kind"))
        paths = [h["path"] for h in hits]
        rank = next((i + 1 for i, path in enumerate(paths) if path in expect), None)
        results.append({"question": case["question"], "rank": rank, "top": paths[0] if paths else None})
    passed = sum(r["rank"] is not None for r in results)
    return {"k": k, "passed": passed, "total": len(results), "cases": results}
