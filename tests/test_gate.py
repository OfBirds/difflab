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


def _init_repo_with_commit(path):
    """Init a git repo with one commit so HEAD resolves (git diff HEAD needs it)."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", str(path)], capture_output=True)
    (Path(path) / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "-C", str(path), "add", "seed.txt"], capture_output=True, env=env)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "seed"], capture_output=True, env=env)


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


def test_gate_diff_head_accepted(gate_script, tmp_path):
    """git -C <path> --no-pager diff HEAD passes through (post diff-HEAD change)."""
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    result = _run_gate(gate_script, f"git -C {repo} --no-pager diff HEAD")
    assert result.returncode == 0, result.stderr
    assert "not permitted" not in result.stderr


def test_gate_numstat_head_accepted(gate_script, tmp_path):
    """git -C <path> --no-pager diff --numstat HEAD passes through."""
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    result = _run_gate(gate_script, f"git -C {repo} --no-pager diff --numstat HEAD")
    assert result.returncode == 0, result.stderr
    assert "not permitted" not in result.stderr


def test_gate_legacy_diff_without_head_still_accepted(gate_script, tmp_path):
    """git -C <path> --no-pager diff (no HEAD) still passes for older app builds."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True)
    result = _run_gate(gate_script, f"git -C {repo} --no-pager diff")
    assert result.returncode == 0, result.stderr
    assert "not permitted" not in result.stderr


def test_gate_diff_head_passes_ref_through_to_git(gate_script, tmp_path):
    """The HEAD ref must reach git: a staged-only change shows under `diff HEAD`
    but not under plain `diff`. This guards the staged-files feature end to end."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
        )

    git("init")
    (repo / "seed.txt").write_text("seed\n")
    git("add", "seed.txt")
    git("commit", "-m", "seed")
    # Stage a brand-new file (staged, not committed) — invisible to plain `git diff`.
    (repo / "staged.txt").write_text("new staged content\n")
    git("add", "staged.txt")

    plain = _run_gate(gate_script, f"git -C {repo} --no-pager diff")
    head = _run_gate(gate_script, f"git -C {repo} --no-pager diff HEAD")
    assert plain.returncode == 0 and head.returncode == 0
    assert "staged.txt" not in plain.stdout
    assert "staged.txt" in head.stdout


def test_gate_rejects_diff_with_arbitrary_ref(gate_script, tmp_path):
    """Only HEAD is allowed as the diff ref — arbitrary refs are refused."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True)
    result = _run_gate(gate_script, f"git -C {repo} --no-pager diff origin/main")
    assert result.returncode != 0
    assert "not permitted" in result.stderr


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
