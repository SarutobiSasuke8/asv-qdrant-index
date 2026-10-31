"""Qdrant operations: create the collection, diff-ingest, search."""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastembed import TextEmbedding
from qdrant_client import QdrantClient, models

from .core import COLLECTION, DIM, MODEL, chunk_file, select_paths, tracked_shas

# bge models are trained with this prefix on the query side only.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_model: TextEmbedding | None = None


def embedder() -> TextEmbedding:
    global _model
    if _model is None:
        _model = TextEmbedding(MODEL)
    return _model


def ensure_collection(client: QdrantClient) -> bool:
    """Create the collection if missing. Returns True when it was created."""
    if client.collection_exists(COLLECTION):
        info = client.get_collection(COLLECTION)
        size = info.config.params.vectors.size
        if size != DIM:
            raise SystemExit(f"{COLLECTION} has vector size {size}, expected {DIM} for {MODEL}")
        return False
    client.create_collection(
        COLLECTION,
        vectors_config=models.VectorParams(size=DIM, distance=models.Distance.COSINE),
    )
    client.create_payload_index(COLLECTION, "path", models.PayloadSchemaType.KEYWORD)
    return True


def indexed_shas(client: QdrantClient) -> dict[str, str]:
    """path -> source_sha for everything already in the collection."""
    seen: dict[str, str] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            COLLECTION, limit=512, offset=offset, with_payload=["path", "source_sha"], with_vectors=False
        )
        for p in points:
            seen[p.payload["path"]] = p.payload["source_sha"]
        if offset is None:
            return seen


def _delete_path(client: QdrantClient, path: str) -> None:
    client.delete(
        COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(must=[models.FieldCondition(key="path", match=models.MatchValue(value=path))])
        ),
    )


def ingest(client: QdrantClient, asv_root: Path, full: bool = False, dry_run: bool = False) -> dict:
    head = subprocess.run(
        ["git", "-C", str(asv_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    files = select_paths(asv_root)
    shas = tracked_shas(asv_root)
    existing = {} if dry_run else indexed_shas(client)
    stats = {"asv_head": head, "files": len(files), "changed": 0, "unchanged": 0, "removed": 0, "chunks": 0}

    current: set[str] = set()
    batch = []
    for f in files:
        chunks = chunk_file(f, asv_root, shas.get(f.relative_to(asv_root).as_posix()))
        if not chunks:
            continue
        path, sha = chunks[0].path, chunks[0].source_sha
        current.add(path)
        if not full and existing.get(path) == sha:
            stats["unchanged"] += 1
            continue
        stats["changed"] += 1
        stats["chunks"] += len(chunks)
        if not dry_run and path in existing:
            _delete_path(client, path)  # chunk count may have shrunk
        batch.extend(chunks)

    for gone in sorted(set(existing) - current):
        stats["removed"] += 1
        if not dry_run:
            _delete_path(client, gone)

    if dry_run or not batch:
        return stats
    vectors = embedder().embed([c.text for c in batch], batch_size=64)
    points = [
        models.PointStruct(id=c.point_id, vector=v.tolist(), payload={**c.payload(), "asv_head": head})
        for c, v in zip(batch, vectors)
    ]
    for i in range(0, len(points), 128):
        client.upsert(COLLECTION, points=points[i:i + 128])
    return stats


def search(client: QdrantClient, query: str, k: int = 5) -> list[dict]:
    vector = next(iter(embedder().embed([QUERY_PREFIX + query])))
    hits = client.query_points(COLLECTION, query=vector.tolist(), limit=k, with_payload=True).points
    return [
        {
            "score": round(h.score, 4),
            "path": h.payload["path"],
            "source_sha": h.payload["source_sha"],
            "chunk_index": h.payload["chunk_index"],
            "excerpt": h.payload["excerpt"],
        }
        for h in hits
    ]
