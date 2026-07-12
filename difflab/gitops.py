from __future__ import annotations

import enum
import shlex
import subprocess
import time
from pathlib import Path

from difflab.config import Target


class ErrorKind(str, enum.Enum):
    TIMEOUT = "timeout"
    BUSY = "busy"
    UNREACHABLE = "unreachable"
    AUTH = "auth"
    GATE_REJECTED = "gate_rejected"
    REPO_MISSING = "repo_missing"
    REPO_DENIED = "repo_denied"
    UNKNOWN = "unknown"


class GitError(Exception):
    def __init__(self, message: str, stderr: str = "", kind: ErrorKind = ErrorKind.UNKNOWN) -> None:
        super().__init__(message)
        self.message = message
        self.stderr = stderr[:2000]
        self.kind = kind

    @property
    def retryable(self) -> bool:
        return self.kind is ErrorKind.BUSY


def classify_error(returncode: int, stderr: str) -> tuple[ErrorKind, str]:
    s = stderr.lower()

    # BUSY — check before UNREACHABLE; both can arrive with rc=255
    if ("kex_exchange_identification" in s
            or "connection reset by peer" in s
            or "connection closed by remote host" in s
            or "timeout during banner exchange" in s
            or "broken pipe" in s):
        return ErrorKind.BUSY, "Connection interrupted — host busy"

    # UNREACHABLE
    if ("ssh: connect to host" in s
            or "connection refused" in s
            or "no route to host" in s
            or "network is unreachable" in s
            or "could not resolve hostname" in s
            or "connection timed out" in s):
        return ErrorKind.UNREACHABLE, "Machine unreachable"

    # AUTH
    if ("permission denied (publickey" in s
            or "host key verification failed" in s
            or "no matching host key" in s):
        return ErrorKind.AUTH, "SSH access denied (check key)"

    # GATE_REJECTED
    if ("command not permitted" in s
            or "command not found" in s
            or "not recognized as an internal" in s
            or returncode == 127):
        return ErrorKind.GATE_REJECTED, "Remote gate refused command"

    # REPO_MISSING (rc != 255 required to distinguish from SSH-layer errors)
    if returncode != 255 and (
            "not a git repository" in s
            or "cannot chdir" in s
            or "no such file or directory" in s):
        return ErrorKind.REPO_MISSING, "Repository path not found"

    # REPO_DENIED (rc != 255 required)
    if returncode != 255 and (
            "permission denied" in s
            or "dubious ownership" in s
            or "safe.directory" in s):
        return ErrorKind.REPO_DENIED, "Permission or ownership refused"

    # UNKNOWN
    if returncode == 255:
        return ErrorKind.UNKNOWN, "SSH error"
    return ErrorKind.UNKNOWN, "Can't read this repo"


ALLOWED: dict[str, tuple[str, ...]] = {
    "diff": ("--no-pager", "diff"),
    "numstat": ("--no-pager", "diff", "--numstat"),
    "status": ("status", "--short"),
}

BATCH_OP = "difflab-batch-status"
BATCH_SEP = "\x1f"
BATCH_REC = "\x1e"
BATCH_LIMIT = 64


def _ssh_argv(ssh_host: str, port: int, key_path: "Path | None") -> list[str]:
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if key_path is not None:
        known_hosts = str(key_path.parent / "known_hosts")
        argv += [
            "-i", str(key_path),
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"UserKnownHostsFile={known_hosts}",
        ]
    if port != 22:
        argv += ["-p", str(port)]
    argv.append(ssh_host)
    return argv


def build_command(target: Target, op: str, key_path: "Path | None" = None) -> list[str]:
    if op not in ALLOWED:
        raise ValueError(f"Unknown git operation: {op!r}")

    args = ALLOWED[op]

    if target.ssh_host is None:
        return ["git", "-C", target.repo, *args]

    ssh = _ssh_argv(target.ssh_host, target.port, key_path)

    if target.shell == "windows":
        repo = target.repo.replace("\\", "/")
        remote_cmd = f'git -C "{repo}" ' + " ".join(args)
    else:
        remote_cmd = "git -C " + shlex.quote(target.repo) + " " + " ".join(args)
    return [*ssh, remote_cmd]


def build_batch_status_command(
    ssh_host: str, port: int, repos: list[str], key_path: "Path | None"
) -> list[str]:
    if not repos:
        raise ValueError("repos must not be empty")
    if len(repos) > BATCH_LIMIT:
        raise ValueError(f"Too many repos: {len(repos)} > {BATCH_LIMIT}")
    for repo in repos:
        for ch in (BATCH_SEP, BATCH_REC, "\n"):
            if ch in repo:
                raise ValueError(f"Repo path contains invalid character: {repo!r}")
        if repo.startswith("-"):
            raise ValueError(f"Repo path must not start with '-': {repo!r}")

    argv = _ssh_argv(ssh_host, port, key_path)
    batch_cmd = BATCH_OP + "".join(BATCH_SEP + r for r in repos)
    return [*argv, batch_cmd]


def parse_batch_status(stdout: str) -> dict[str, tuple[int, str]]:
    results: dict[str, tuple[int, str]] = {}
    current_path: str | None = None
    current_lines: list[str] = []

    REPO_PFX = BATCH_REC + "REPO "
    RC_PFX = BATCH_REC + "RC "

    for line in stdout.split("\n"):
        if line.startswith(REPO_PFX):
            # Missing trailing RC for previous section — drop it
            current_path = line[len(REPO_PFX):]
            current_lines = []
        elif line.startswith(RC_PFX):
            if current_path is not None:
                try:
                    rc = int(line[len(RC_PFX):].strip())
                except ValueError:
                    rc = 1
                text = "\n".join(current_lines).rstrip("\n")
                results[current_path] = (rc, text)
            current_path = None
            current_lines = []
        elif current_path is not None:
            current_lines.append(line)

    return results


def _execute(argv: list[str], timeout: int = 30, retries: int = 1) -> str:
    for attempt in range(retries + 1):
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
            raise GitError("Machine not responding (timed out)", kind=ErrorKind.TIMEOUT)

        if result.returncode == 0:
            return result.stdout

        kind, msg = classify_error(result.returncode, result.stderr)
        err = GitError(msg, stderr=result.stderr, kind=kind)
        if kind is ErrorKind.BUSY and attempt < retries:
            time.sleep(0.5)
            continue
        raise err

    raise GitError("Unexpected retry exhaustion", kind=ErrorKind.UNKNOWN)  # unreachable


def get_diff(target: Target, key_path: "Path | None" = None) -> str:
    return _execute(build_command(target, "diff", key_path=key_path))


def get_numstat(target: Target, key_path: "Path | None" = None) -> str:
    return _execute(build_command(target, "numstat", key_path=key_path))


def get_status(target: Target, key_path: "Path | None" = None) -> str:
    return _execute(build_command(target, "status", key_path=key_path), timeout=25)


def get_batch_status(
    ssh_host: str, port: int, repos: list[str], key_path: "Path | None"
) -> dict[str, tuple[int, str]]:
    argv = build_batch_status_command(ssh_host, port, repos, key_path)
    timeout = min(20 + 2 * len(repos), 120)
    stdout = _execute(argv, timeout=timeout)
    return parse_batch_status(stdout)
