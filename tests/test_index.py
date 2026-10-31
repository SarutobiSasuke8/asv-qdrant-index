import subprocess
import warnings

import numpy as np
from qdrant_client import QdrantClient

from asv_qdrant import index
from asv_qdrant.core import COLLECTION, DIM


class FakeEmbedder:
    def embed(self, texts, batch_size=64):
        for t in texts:
            v = np.zeros(DIM, dtype=np.float32)
            v[hash(t) % DIM] = 1.0
            v[0] += 0.1
            yield v


def _commit(root, files):
    for rel, text in files.items():
        p = root / rel
        if text is None:
            p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode())
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x", "--allow-empty"],
        check=True,
    )


def test_diff_ingest(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_model", FakeEmbedder())
    warnings.simplefilter("ignore")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    eng = "Workspace/Grok/Engineering/"
    _commit(tmp_path, {eng + "a.md": "# A\n\n## one\nx\n\n## two\ny\n", eng + "b.md": "# B\nz\n"})
    client = QdrantClient(":memory:")
    assert index.ensure_collection(client) is True
    assert index.ensure_collection(client) is False

    s = index.ingest(client, tmp_path)
    assert (s["changed"], s["chunks"]) == (2, 4)
    assert client.count(COLLECTION).count == 4

    s = index.ingest(client, tmp_path)
    assert (s["changed"], s["unchanged"]) == (0, 2)

    _commit(tmp_path, {eng + "a.md": "# A\nshorter\n", eng + "b.md": None})
    s = index.ingest(client, tmp_path)
    assert (s["changed"], s["removed"]) == (1, 1)
    paths = [p.payload["path"] for p in client.scroll(COLLECTION, limit=50)[0]]
    assert paths == [eng + "a.md"]  # old chunks of a.md and all of b.md gone


def test_schema_bump_reembeds_and_filters(tmp_path, monkeypatch):
    from asv_qdrant import core
    monkeypatch.setattr(index, "_model", FakeEmbedder())
    warnings.simplefilter("ignore")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _commit(tmp_path, {
        "Workspace/Grok/Engineering/d.md": "---\ncreated: 2026-10-01\n---\n# D\nx\n",
        "Workspace/Messages/codex/2026-10-30-1200-claude-code-x-reply.md": "---\nfrom: claude-code\n---\n# R\ny\n",
    })
    client = QdrantClient(":memory:")
    index.ensure_collection(client)
    assert index.ingest(client, tmp_path)["changed"] == 2
    monkeypatch.setattr(core, "SCHEMA", core.SCHEMA + 1)
    monkeypatch.setattr(index, "SCHEMA", core.SCHEMA)
    assert index.ingest(client, tmp_path)["changed"] == 2  # schema bump forces a rewrite

    def paths(**kw):
        return [h["path"] for h in index.search(client, "anything", 10, **kw)]
    assert paths(kind=["reply"]) == ["Workspace/Messages/codex/2026-10-30-1200-claude-code-x-reply.md"]
    assert paths(sender="claude-code") == ["Workspace/Messages/codex/2026-10-30-1200-claude-code-x-reply.md"]
    assert paths(since="2026-10-15") == ["Workspace/Messages/codex/2026-10-30-1200-claude-code-x-reply.md"]
    assert len(paths()) == 2
