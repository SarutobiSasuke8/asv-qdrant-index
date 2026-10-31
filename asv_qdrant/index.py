"""Qdrant operations: create the collection, diff-ingest, search, status."""

from __future__ import annotations

import fnmatch
import subprocess
import warnings
from pathlib import Path

from fastembed import TextEmbedding
from qdrant_client import QdrantClient, models

from .core import COLLECTION, DIM, MODEL, SCHEMA, chunk_file, select_paths, tracked_shas

# bge models are trained with this prefix on the query side only.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

PAYLOAD_INDEXES = {
    "path": models.PayloadSchemaType.KEYWORD,
    "kind": models.PayloadSchemaType.KEYWORD,
    "sender": models.PayloadSchemaType.KEYWORD,
    "date_int": models.PayloadSchemaType.INTEGER,
}

UPSERT_BATCH = 128

_model: TextEmbedding | None = None


def embedder() -> TextEmbedding:
    global _model
    if _model is None:
        _model = TextEmbedding(MODEL)
    return _model


def ensure_collection(client: QdrantClient) -> bool:
    """Create the collection if missing and make sure the payload indexes exist.
    Returns True when the collection was created."""
    created = False
    if client.collection_exists(COLLECTION):
        info = client.get_collection(COLLECTION)
        size = info.config.params.vectors.size
        if size != DIM:
            raise SystemExit(f"{COLLECTION} has vector size {size}, expected {DIM} for {MODEL}")
        have = set(info.payload_schema or {})
    else:
        client.create_collection(
            COLLECTION,
            vectors_config=models.VectorParams(size=DIM, distance=models.Distance.COSINE),
        )
        created, have = True, set()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # local mode warns that indexes are no-ops
        for field, schema in PAYLOAD_INDEXES.items():
            if field not in have:
                client.create_payload_index(COLLECTION, field, schema)
    return created


def indexed_state(client: QdrantClient) -> dict[str, tuple[str, int]]:
    """path -> (source_sha, schema) for everything already in the collection."""
    seen: dict[str, tuple[str, int]] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            COLLECTION, limit=512, offset=offset, with_payload=["path", "source_sha", "schema"], with_vectors=False
        )
        for p in points:
            seen[p.payload["path"]] = (p.payload["source_sha"], p.payload.get("schema", 1))
        if offset is None:
            return seen


def _delete_path(client: QdrantClient, path: str) -> None:
    client.delete(
        COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(must=[models.FieldCondition(key="path", match=models.MatchValue(value=path))])
        ),
    )


def ingest(
    client: QdrantClient, asv_root: Path, full: bool = False, dry_run: bool = False, only: str | None = None
) -> dict:
    """Diff-ingest. `only` narrows to one glob and then never deletes other paths.

    A file is re-embedded when its blob sha or the payload schema changed.
    Points are written in batches as they are embedded, so an interrupted run
    keeps its progress and the next run picks up the rest."""
    head = subprocess.run(
        ["git", "-C", str(asv_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    files = select_paths(asv_root)
    if only:
        files = [f for f in files if fnmatch.fnmatch(f.relative_to(asv_root).as_posix(), only)]
    shas = tracked_shas(asv_root)
    existing = {} if dry_run else indexed_state(client)
    stats = {"asv_head": head, "files": len(files), "changed": 0, "unchanged": 0, "removed": 0, "chunks": 0}

    current: set[str] = set()
    todo = []
    for f in files:
        chunks = chunk_file(f, asv_root, shas.get(f.relative_to(asv_root).as_posix()))
        if not chunks:
            continue
        path, sha = chunks[0].path, chunks[0].source_sha
        current.add(path)
        if not full and existing.get(path) == (sha, SCHEMA):
            stats["unchanged"] += 1
            continue
        stats["changed"] += 1
        stats["chunks"] += len(chunks)
        todo.append((path, chunks))

    for gone in [] if only else sorted(set(existing) - current):
        stats["removed"] += 1
        if not dry_run:
            _delete_path(client, gone)

    if dry_run:
        return stats
    batch = []
    for path, chunks in todo:
        if path in existing:
            _delete_path(client, path)  # chunk count may have shrunk
        batch.extend(chunks)
        if len(batch) >= UPSERT_BATCH:
            _upsert(client, batch, head)
            batch = []
    if batch:
        _upsert(client, batch, head)
    return stats


def _upsert(client: QdrantClient, chunks: list, head: str) -> None:
    vectors = embedder().embed([c.text for c in chunks], batch_size=64)
    client.upsert(COLLECTION, points=[
        models.PointStruct(id=c.point_id, vector=v.tolist(), payload={**c.payload(), "asv_head": head})
        for c, v in zip(chunks, vectors)
    ])


def build_filter(
    kind: list[str] | None = None, sender: str | None = None, since: str | None = None
) -> models.Filter | None:
    must = []
    if kind:
        must.append(models.FieldCondition(key="kind", match=models.MatchAny(any=kind)))
    if sender:
        must.append(models.FieldCondition(key="sender", match=models.MatchValue(value=sender.lower())))
    if since:
        must.append(models.FieldCondition(key="date_int", range=models.Range(gte=int(since.replace("-", "")))))
    return models.Filter(must=must) if must else None


def search(
    client: QdrantClient,
    query: str,
    k: int = 5,
    kind: list[str] | None = None,
    sender: str | None = None,
    since: str | None = None,
) -> list[dict]:
    vector = next(iter(embedder().embed([QUERY_PREFIX + query])))
    hits = client.query_points(
        COLLECTION,
        query=vector.tolist(),
        query_filter=build_filter(kind, sender, since),
        limit=k,
        with_payload=True,
    ).points
    return [
        {
            "score": round(h.score, 4),
            "path": h.payload["path"],
            "title": h.payload.get("title", ""),
            "kind": h.payload.get("kind", ""),
            "sender": h.payload.get("sender", ""),
            "date": h.payload.get("date", ""),
            "source_sha": h.payload["source_sha"],
            "chunk_index": h.payload["chunk_index"],
            "excerpt": h.payload["excerpt"],
        }
        for h in hits
    ]


def status(client: QdrantClient) -> dict:
    info = client.get_collection(COLLECTION)
    heads: dict[str, int] = {}
    offset = None
    while True:
        points, offset = client.scroll(COLLECTION, limit=1024, offset=offset, with_payload=["asv_head"])
        for p in points:
            h = p.payload.get("asv_head", "")
            heads[h] = heads.get(h, 0) + 1
        if offset is None:
            break
    return {
        "collection": COLLECTION,
        "status": str(info.status),
        "points": client.count(COLLECTION).count,
        "model": MODEL,
        "asv_heads": heads,
    }
