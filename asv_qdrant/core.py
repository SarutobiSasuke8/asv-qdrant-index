"""Select, chunk, embed and index ASV engineering notes in Qdrant.

The ASV is the source of truth. Every point is a pointer to an ASV path at a
given blob sha; agents must open the file before acting on a hit.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import frontmatter

COLLECTION = "asv-engineering"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384
NAMESPACE = uuid.UUID("6f1d3c52-6d0e-4b8a-9f5e-2a4c1b7e9d30")

INCLUDE = [
    "Workspace/Grok/Engineering/**/*.md",
    "Workspace/Grok/Chief of Staff/Portfolio/Portfolio Map.md",
    "Workspace/Messages/lingxi-engineer-grok-bot/*.md",
    "Workspace/Messages/claude-code/*.md",
    "Workspace/Messages/codex/*.md",
    "Workspace/Messages/chief-of-staff-grok-bot/*.md",
]

# Hard exclusions win over INCLUDE, whatever a future glob change adds.
EXCLUDE = [
    "Context/**",
    "**/gryps-brain/**",
    "**/Brand Confidential/**",
    "**/*confidential*/**",
    "**/automation-schedule.md",
]

# Frontmatter values that keep a note out of the index.
BLOCKED_DISCLOSURE = {"confidential", "brand-confidential", "internal-only", "private"}

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.]{16,}"),
    re.compile(r"\b(sk|pk|ghp|gho|github_pat|xox[abp])_[A-Za-z0-9_]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

MAX_CHARS = 1500
OVERLAP = 200
EXCERPT_CHARS = 300


@dataclass
class Chunk:
    path: str
    title: str
    updated: str
    source_sha: str
    chunk_index: int
    text: str

    @property
    def point_id(self) -> str:
        return str(uuid.uuid5(NAMESPACE, f"{self.path}#{self.chunk_index}"))

    def payload(self) -> dict:
        return {
            "path": self.path,
            "title": self.title,
            "updated": self.updated,
            "source_sha": self.source_sha,
            "chunk_index": self.chunk_index,
            "excerpt": self.text[:EXCERPT_CHARS],
        }


def require_env() -> tuple[str, str]:
    url = os.environ.get("QDRANT_URL", "").strip()
    key = os.environ.get("QDRANT_API_KEY", "").strip()
    if not url:
        raise SystemExit("QDRANT_URL is not set")
    if not key:
        raise SystemExit("QDRANT_API_KEY is not set")
    return url, key


def blob_sha(data: bytes) -> str:
    """Git blob sha, so a hit can be checked with `git cat-file -p <sha>`."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _match(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, p.replace("**/", "")) for p in patterns)


def select_paths(asv_root: Path) -> list[Path]:
    """Tracked files only, so untracked drafts and clippings never leak in."""
    out = subprocess.run(
        ["git", "-C", str(asv_root), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode("utf-8")
    rels = [r for r in out.split("\0") if r.endswith(".md")]
    return [asv_root / r for r in sorted(rels) if _match(r, INCLUDE) and not _match(r, EXCLUDE)]


def tracked_shas(asv_root: Path) -> dict[str, str]:
    """Blob sha in the git index per path. Unlike hashing the working file, this
    survives core.autocrlf and matches `git cat-file -p <sha>`."""
    out = subprocess.run(
        ["git", "-C", str(asv_root), "ls-files", "-s", "-z"], capture_output=True, check=True
    ).stdout.decode("utf-8")
    shas = {}
    for entry in filter(None, out.split("\0")):
        meta, rel = entry.split("\t", 1)
        shas[rel] = meta.split()[1]
    return shas


def _split(body: str) -> list[str]:
    sections = re.split(r"(?m)^(?=#{1,3} )", body)
    chunks: list[str] = []
    for section in (s.strip() for s in sections):
        if not section:
            continue
        while len(section) > MAX_CHARS:
            cut = section.rfind("\n", 0, MAX_CHARS)
            cut = cut if cut > MAX_CHARS // 2 else MAX_CHARS
            chunks.append(section[:cut].strip())
            section = section[max(cut - OVERLAP, 0):]
        if section.strip():
            chunks.append(section.strip())
    return chunks


def chunk_file(path: Path, asv_root: Path, sha: str | None = None) -> list[Chunk]:
    raw = path.read_bytes()
    rel = path.relative_to(asv_root).as_posix()
    text = raw.decode("utf-8", errors="replace")
    try:
        post = frontmatter.loads(text)
        meta, body = post.metadata, post.content
    except Exception:
        # Broken YAML: fail closed if the block mentions a restriction at all.
        block = re.match(r"(?s)^---\r?\n(.*?)\r?\n---\r?\n?", text)
        if block and re.search(r"(?i)disclosure|confidential", block.group(1)):
            return []
        meta, body = {}, text[block.end():] if block else text
    disclosure = str(meta.get("disclosure", "")).lower()
    if disclosure in BLOCKED_DISCLOSURE or meta.get("confidential") is True:
        return []
    h1 = re.search(r"(?m)^# (.+)$", body)
    title = str(meta.get("title") or (h1.group(1) if h1 else path.stem)).strip()
    updated = str(meta.get("updated") or meta.get("created") or "")
    sha = sha or blob_sha(raw)
    chunks = []
    for text in _split(body):
        if any(p.search(text) for p in SECRET_PATTERNS):
            continue
        chunks.append(Chunk(rel, title, updated, sha, len(chunks), f"{title}\n\n{text}"))
    return chunks
