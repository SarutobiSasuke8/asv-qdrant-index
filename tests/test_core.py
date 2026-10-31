import subprocess
from pathlib import Path

from asv_qdrant.core import blob_sha, chunk_file, select_paths, _split, MAX_CHARS


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


def test_blob_sha_matches_git(tmp_path):
    root = _repo(tmp_path, {"a.md": "hello\n"})
    expected = subprocess.run(
        ["git", "-C", str(root), "hash-object", "--no-filters", "a.md"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert blob_sha((root / "a.md").read_bytes()) == expected


def test_allowlist_and_exclusions(tmp_path):
    root = _repo(tmp_path, {
        "Workspace/Grok/Engineering/Design.md": "# D\nx",
        "Workspace/Grok/Engineering/Sub/Deep.md": "# Deep\nx",
        "Workspace/Messages/claude-code/2026-10-30-ticket.md": "# T\nx",
        "Workspace/Messages/claude-code/automation-schedule.md": "# S\nx",
        "Workspace/Messages/nezu-hermes/private.md": "# N\nx",
        "Context/Internal/secret.md": "# C\nx",
        "Workspace/Grok/Engineering/notes.txt": "x",
    })
    rels = {p.relative_to(root).as_posix() for p in select_paths(root)}
    assert rels == {
        "Workspace/Grok/Engineering/Design.md",
        "Workspace/Grok/Engineering/Sub/Deep.md",
        "Workspace/Messages/claude-code/2026-10-30-ticket.md",
    }


def test_untracked_files_are_ignored(tmp_path):
    root = _repo(tmp_path, {"Workspace/Grok/Engineering/A.md": "# A\nx"})
    (root / "Workspace/Grok/Engineering/Draft.md").write_text("# Draft\n", encoding="utf-8")
    assert [p.name for p in select_paths(root)] == ["A.md"]


def test_confidential_frontmatter_skipped(tmp_path):
    root = _repo(tmp_path, {
        "x.md": "---\ndisclosure: confidential\n---\n# X\nbody",
        "y.md": "---\nbad: yaml: here\nconfidential: true\n---\n# Y\nbody",
        "z.md": "---\nbad: yaml: here\n---\n# Z\nbody",
    })
    assert chunk_file(root / "x.md", root) == []
    assert chunk_file(root / "y.md", root) == []
    z = chunk_file(root / "z.md", root)
    assert z and z[0].title == "Z" and "bad:" not in z[0].text


def test_secret_chunks_dropped(tmp_path):
    root = _repo(tmp_path, {"s.md": "# S\n\n## Safe\nfine\n\n## Leak\napi_key = abcdefghijklmnopqrstuvwxyz123456\n"})
    chunks = chunk_file(root / "s.md", root)
    assert len(chunks) == 2  # title section and Safe
    assert all("abcdefghijklmnop" not in c.text for c in chunks)
    assert [c.chunk_index for c in chunks] == [0, 1]


def test_payload_and_ids(tmp_path):
    root = _repo(tmp_path, {"p.md": "---\nupdated: 2026-10-30\n---\n# Title\n\n## A\none\n\n## B\ntwo\n"})
    chunks = chunk_file(root / "p.md", root)
    payload = chunks[0].payload()
    assert {"path", "title", "updated", "source_sha", "chunk_index", "excerpt"} <= set(payload)
    assert {"kind", "sender", "date", "date_int", "schema"} <= set(payload)
    assert payload["updated"] == "2026-10-30" and payload["title"] == "Title"
    assert len({c.point_id for c in chunks}) == len(chunks)
    assert chunks[0].point_id == chunk_file(root / "p.md", root)[0].point_id


def test_long_sections_split_with_bound():
    body = "## Long\n" + "\n".join("line %d %s" % (i, "x" * 60) for i in range(200))
    parts = _split(body)
    assert len(parts) > 1
    assert all(len(p) <= MAX_CHARS for p in parts)


def test_crlf_broken_frontmatter_fails_closed(tmp_path):
    root = _repo(tmp_path, {})
    (root / "w.md").write_bytes(b"---\r\nbad: yaml: here\r\nconfidential: true\r\n---\r\n# W\r\nbody")
    assert chunk_file(root / "w.md", root) == []


def test_tracked_shas_match_index(tmp_path):
    from asv_qdrant.core import tracked_shas
    root = _repo(tmp_path, {"Workspace/Grok/Engineering/A B.md": "# A\r\nx\r\n"})
    expected = subprocess.run(
        ["git", "-C", str(root), "rev-parse", ":Workspace/Grok/Engineering/A B.md"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert tracked_shas(root)["Workspace/Grok/Engineering/A B.md"] == expected


def test_classify_kinds_sender_date():
    from asv_qdrant.core import classify
    m = "Workspace/Messages/claude-code/"
    assert classify("Workspace/Grok/Engineering/X.md", {}) [0] == "design-note"
    assert classify("Workspace/Grok/Chief of Staff/Portfolio/Portfolio Map.md", {})[0] == "portfolio"
    assert classify(m + "2026-10-30-2300-claude-code-ahm-49-restack-reply.md", {}) == ("reply", "", "2026-10-30")
    assert classify(m + "2026-10-29-0907-lingxi-engineer-grok-bot-drop-56.md", {"type": "ticket"})[0] == "ticket"
    assert classify(m + "2026-10-30-cos-runner-fyi.md", {"from": "Chief-Of-Staff-Grok-Bot"})[:2] == (
        "fyi", "chief-of-staff-grok-bot")
    assert classify(m + "ROLE.md", {"created": "2026-10-04"}) == ("message", "", "2026-10-04")


def test_noise_files_not_selected(tmp_path):
    root = _repo(tmp_path, {
        "Workspace/Messages/claude-code/2026-10-31-0115-claude-code-intake-log.md": "# L\nx",
        "Workspace/Messages/chief-of-staff-grok-bot/2026-10-31-daily-digest.md": "# D\nx",
        "Workspace/Grok/Engineering/MCP Download Stats/mcp-downloads-2026-10-29.md": "# S\nx",
        "Workspace/Grok/Engineering/MCP Download Stats/README.md": "# R\nx",
        "Workspace/Messages/claude-code/2026-10-31-0115-claude-code-qdrant-reply.md": "# Q\nx",
    })
    rels = sorted(p.relative_to(root).as_posix() for p in select_paths(root))
    assert rels == [
        "Workspace/Grok/Engineering/MCP Download Stats/README.md",
        "Workspace/Messages/claude-code/2026-10-31-0115-claude-code-qdrant-reply.md",
    ]
