from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from difflab.config import NAME_RE


class EnrollError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class SSHError(Exception):
    pass


def validate_body(body: dict) -> tuple[str, str, str, int, list, list]:
    """Parse and validate a /register request body."""
    name = body.get("name", "")
    host = body.get("host", "")
    user = body.get("user", "")
    port_raw = body.get("port", 22)
    roots = body.get("roots") or []
    repos = body.get("repos") or []

    if not isinstance(name, str) or not NAME_RE.match(name):
        raise EnrollError("'name' must match ^[A-Za-z0-9][A-Za-z0-9._-]*$")
    if not isinstance(host, str) or not host:
        raise EnrollError("'host' is required")
    if host.startswith("-"):
        raise EnrollError("'host' must not start with '-'")
    if not isinstance(user, str) or not user:
        raise EnrollError("'user' is required")
    if user.startswith("-"):
        raise EnrollError("'user' must not start with '-'")
    try:
        port = int(port_raw)
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        raise EnrollError("'port' must be an integer between 1 and 65535")
    if not isinstance(roots, list) or not isinstance(repos, list):
        raise EnrollError("'roots' and 'repos' must be lists")

    return name, host, user, port, roots, repos


def _ssh_base(host: str, user: str, port: int, key_path: Path) -> list[str]:
    known_hosts = str(key_path.parent / "known_hosts")
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-i", str(key_path),
    ]
    if port != 22:
        cmd += ["-p", str(port)]
    cmd.append(f"{user}@{host}")
    return cmd


def _run_ssh(argv: list[str]) -> tuple[str, str, int]:
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        shell=False,
    )
    return result.stdout, result.stderr, result.returncode


def _is_hidden_path(path: str) -> bool:
    """Return True if any path component starts with '.'"""
    normalized = path.replace("\\", "/")
    return any(part.startswith(".") for part in normalized.split("/") if part)


def discover_repos(
    host: str,
    user: str,
    port: int,
    key_path: Path,
    roots: list[str],
    repos: list[str],
) -> tuple[list[str], list[str], str]:
    """SSH into the host and return (repo_paths, errors, shell).

    shell is 'windows' if PowerShell scan succeeded, 'posix' otherwise.
    Explicit repos bypass the hidden-path filter; auto-discovered paths skip
    any repo whose path contains a component starting with '.'.
    """
    discovered: list[str] = []
    errors: list[str] = []
    detected_shell = "posix"

    if repos:
        for repo in repos:
            if repo.startswith("-"):
                errors.append(f"Skipped {repo!r}: path must not start with '-'")
                continue
            if '"' in repo:
                errors.append(f"Skipped {repo!r}: path must not contain '\"'")
                continue
            argv = _ssh_base(host, user, port, key_path) + [
                f"git -C {shlex.quote(repo)} rev-parse --is-inside-work-tree"
            ]
            stdout, stderr, rc = _run_ssh(argv)
            if rc == 255:
                raise SSHError(stderr.strip() or "SSH connection failed")
            if rc == 0 and stdout.strip() == "true":
                discovered.append(repo)
            else:
                errors.append(f"{repo}: not a git repository")
    else:
        for root in roots:
            if root.startswith("-"):
                errors.append(f"Skipped root {root!r}: must not start with '-'")
                continue
            # Try POSIX find first
            argv_find = _ssh_base(host, user, port, key_path) + [
                f"find {shlex.quote(root)} -maxdepth 3 -type d -name .git"
            ]
            stdout, stderr, rc = _run_ssh(argv_find)
            if rc == 255:
                raise SSHError(stderr.strip() or "SSH connection failed")
            if rc != 0:
                # Likely a Windows host — retry with PowerShell
                quoted = shlex.quote(root)
                ps_cmd = (
                    f'powershell -NoProfile -Command '
                    f'"Get-ChildItem -Path {quoted} -Recurse -Depth 3 '
                    f'-Directory -Force -Filter .git '
                    f'| ForEach-Object {{ $_.Parent.FullName }}"'
                )
                argv_ps = _ssh_base(host, user, port, key_path) + [ps_cmd]
                stdout, stderr, rc = _run_ssh(argv_ps)
                if rc == 255:
                    raise SSHError(stderr.strip() or "SSH connection failed")
                if rc != 0:
                    errors.append(f"Could not scan {root!r}: {stderr[:200]}")
                    continue
                detected_shell = "windows"
                for line in stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    # Normalize backslashes; git on Windows accepts forward slashes
                    line = line.replace("\\", "/")
                    if _is_hidden_path(line):
                        continue
                    discovered.append(line)
            else:
                # POSIX find outputs .git directory paths; strip the /.git suffix
                for line in stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.endswith("/.git"):
                        line = line[:-5]
                    elif line.endswith("\\.git"):
                        line = line[:-5]
                    if _is_hidden_path(line):
                        continue
                    discovered.append(line)

    return discovered, errors, detected_shell


def make_target_name(machine: str, repo: str, taken: set[str]) -> str:
    """Generate a unique target name: <machine>-<repo-basename>, suffixed if needed."""
    basename = repo.rstrip("/\\").rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "repo"
    safe_base = re.sub(r"[^A-Za-z0-9._-]", "-", basename)
    candidate = f"{machine}-{safe_base}"
    if not re.match(r"^[A-Za-z0-9]", candidate):
        candidate = f"x{candidate}"
    if candidate not in taken:
        return candidate
    n = 2
    while f"{candidate}-{n}" in taken:
        n += 1
    return f"{candidate}-{n}"
