"""Exercise the shell entrypoint without Git or service access."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh required")
def test_shell_dry_run_skips_refresh_and_status(tmp_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    for name, content in {
        "git": "#!/bin/sh\nexit 91\n",
        "asv-qdrant": "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
    }.items():
        executable = commands / name
        executable.write_text(content, newline="\n")
        executable.chmod(0o755)
    env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"])
    env.pop("QDRANT_URL", None)
    env.pop("QDRANT_API_KEY", None)
    mirror = tmp_path / "checkout with spaces"
    script = Path(__file__).resolve().parents[1] / "scripts/daily-update.sh"
    result = subprocess.run(["sh", str(script), "--dry-run", "--mirror", str(mirror)],
                            env=env, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == ["ingest", str(mirror), "--dry-run"]
    assert not mirror.exists()
