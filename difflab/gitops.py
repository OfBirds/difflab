from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from difflab.config import Target


class GitError(Exception):
    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.stderr = stderr[:2000]


ALLOWED: dict[str, tuple[str, ...]] = {
    "diff": ("--no-pager", "diff"),
    "numstat": ("--no-pager", "diff", "--numstat"),
    "status": ("status", "--short"),
}


def build_command(target: Target, op: str, key_path: "Path | None" = None) -> list[str]:
    if op not in ALLOWED:
        raise ValueError(f"Unknown git operation: {op!r}")

    args = ALLOWED[op]

    if target.ssh_host is None:
        return ["git", "-C", target.repo, *args]

    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if key_path is not None:
        known_hosts = str(key_path.parent / "known_hosts")
        ssh += [
            "-i", str(key_path),
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"UserKnownHostsFile={known_hosts}",
        ]
    if target.port != 22:
        ssh += ["-p", str(target.port)]
    ssh.append(target.ssh_host)

    if target.shell == "windows":
        # cmd.exe treats single quotes literally; normalize separators and double-quote
        repo = target.repo.replace("\\", "/")
        remote_cmd = f'git -C "{repo}" ' + " ".join(args)
    else:
        remote_cmd = "git -C " + shlex.quote(target.repo) + " " + " ".join(args)
    return [*ssh, remote_cmd]


def _execute(argv: list[str], timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise GitError(f"Git command timed out after {timeout} seconds.")

    if result.returncode != 0:
        raise GitError(
            f"Git command failed (exit {result.returncode}).",
            stderr=result.stderr,
        )

    return result.stdout


def get_diff(target: Target, key_path: Path | None = None) -> str:
    return _execute(build_command(target, "diff", key_path=key_path))


def get_numstat(target: Target, key_path: Path | None = None) -> str:
    return _execute(build_command(target, "numstat", key_path=key_path))


def get_status(target: Target, key_path: Path | None = None) -> str:
    return _execute(build_command(target, "status", key_path=key_path), timeout=25)
