"""Gate script integration tests — run only when sh is available (CI/Linux)."""
from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from difflab.gitops import parse_batch_status

SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(
    SH is None or os.name != "posix",
    reason="POSIX sh required (skipped on Windows)",
)


@pytest.fixture
def gate_script():
    here = Path(__file__).parent.parent / "gate" / "git-gate.sh"
    assert here.exists(), f"gate script not found: {here}"
    return str(here)


@pytest.fixture
def stub_git(tmp_path):
    """Create a stub git script that echoes canned status output."""
    stub = tmp_path / "git"
    stub.write_text(textwrap.dedent("""\
        #!/bin/sh
        # Stub git: if "status --short", emit dirty marker; otherwise succeed silently.
        if [ "$*" = "-C /repo/a status --short" ] || [ "$1" = "-C" ] && echo "$*" | grep -q "status --short"; then
            printf " M stubfile.py\\n"
            exit 0
        fi
        exit 0
    """))
    stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(tmp_path) + ":" + env.get("PATH", "")
    return env


def _run_gate(gate, ssh_cmd, env=None):
    e = dict(os.environ) if env is None else dict(env)
    e["SSH_ORIGINAL_COMMAND"] = ssh_cmd
    result = subprocess.run(
        [SH, gate],
        capture_output=True,
        text=True,
        env=e,
        timeout=10,
    )
    return result


# ── 24. Gate tests ───────────────────────────────────────────────────────────

def test_gate_legacy_status_still_works(gate_script, tmp_path):
    """Legacy git -C <path> status --short still passes through."""
    # Create a real temp git repo so git -C works
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True)
    result = _run_gate(gate_script, f"git -C {repo} status --short")
    assert result.returncode == 0


def test_gate_batch_two_paths_parseable(gate_script, tmp_path):
    """Batch with 2 paths emits parseable REPO/RC records."""
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    for r in (repo_a, repo_b):
        r.mkdir()
        subprocess.run(["git", "init", str(r)], capture_output=True)

    cmd = "difflab-batch-status\x1f" + str(repo_a) + "\x1f" + str(repo_b)
    result = _run_gate(gate_script, cmd)
    assert result.returncode == 0

    parsed = parse_batch_status(result.stdout)
    assert str(repo_a) in parsed
    assert str(repo_b) in parsed


def test_gate_batch_rejects_bad_prefix(gate_script):
    result = _run_gate(gate_script, "evil-command /etc/passwd")
    assert result.returncode != 0
    assert "not permitted" in result.stderr


def test_gate_batch_rejects_too_many_paths(gate_script, tmp_path):
    paths = [f"/path/to/repo{i}" for i in range(65)]
    cmd = "difflab-batch-status\x1f" + "\x1f".join(paths)
    result = _run_gate(gate_script, cmd)
    assert result.returncode != 0


def test_gate_batch_rejects_leading_dash_path(gate_script):
    cmd = "difflab-batch-status\x1f-evil"
    result = _run_gate(gate_script, cmd)
    assert result.returncode != 0
    assert "not permitted" in result.stderr


def test_gate_batch_rejects_empty(gate_script):
    """difflab-batch-status with no paths is rejected."""
    result = _run_gate(gate_script, "difflab-batch-status")
    assert result.returncode != 0
