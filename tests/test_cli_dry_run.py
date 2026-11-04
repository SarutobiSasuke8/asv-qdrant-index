import io
import json
import socket
import subprocess

from asv_qdrant import cli, index


def test_cli_dry_run_is_offline(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    note = tmp_path / "Workspace/Grok/Engineering/offline.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Offline plan\n\nLocal synthetic evidence.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=test",
                    "-c", "user.email=test@example.test", "commit", "-qm", "fixture"], check=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run attempted client, embedding or network access")

    for name in ("QDRANT_URL", "QDRANT_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "client_for", forbidden)
    monkeypatch.setattr(index, "embedder", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    output = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr("sys.stdout", output)
    assert cli.main(["ingest", str(tmp_path), "--dry-run"]) == 0
    output.flush()
    stats = json.loads(output.buffer.getvalue())
    assert stats["files"] == stats["changed"] == stats["chunks"] == 1
    assert stats["removed"] == 0
    assert stats["unchanged"] == 0
