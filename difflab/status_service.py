from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from difflab.config import Target
import difflab.gitops as gitops
from difflab.gitops import ErrorKind, GitError
from difflab.registry import normalize_repo_path

_HOST_LEVEL_ERRORS = {ErrorKind.TIMEOUT, ErrorKind.BUSY, ErrorKind.UNREACHABLE, ErrorKind.AUTH}
_LEGACY_GATE_TTL = 3600.0

# Module-level state: cache, single-flight locks, legacy-gate memo, fallback semaphores
_CACHE: dict[str, tuple[float, list[StatusResult]]] = {}
_MACHINE_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_LEGACY_GATE: dict[str, float] = {}
_FALLBACK_SEMS: dict[str, threading.BoundedSemaphore] = {}
_SEMS_GUARD = threading.Lock()


@dataclass(frozen=True)
class StatusResult:
    target: Target
    status: str | None
    error_kind: str | None
    error_message: str | None
    error_detail: str | None

    @property
    def state(self) -> str:
        if self.error_kind is not None:
            return "error"
        if self.status:
            return "dirty"
        return "clean"


def group_by_machine(targets: dict[str, Target]) -> dict[str, list[Target]]:
    machines: dict[str, list[Target]] = {}
    for t in targets.values():
        machines.setdefault(t.machine, []).append(t)
    return {
        m: sorted(machines[m], key=lambda t: t.display_name.casefold())
        for m in sorted(machines.keys())
    }


def _get_machine_lock(machine: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if machine not in _MACHINE_LOCKS:
            _MACHINE_LOCKS[machine] = threading.Lock()
        return _MACHINE_LOCKS[machine]


def _get_fallback_sem(ssh_host: str, concurrency: int) -> threading.BoundedSemaphore:
    with _SEMS_GUARD:
        if ssh_host not in _FALLBACK_SEMS:
            _FALLBACK_SEMS[ssh_host] = threading.BoundedSemaphore(concurrency)
        return _FALLBACK_SEMS[ssh_host]


def get_machine_status(
    machine: str,
    targets: list[Target],
    key_path: Path | None,
    *,
    ttl: float = 5.0,
    host_concurrency: int = 3,
) -> list[StatusResult]:
    lock = _get_machine_lock(machine)
    with lock:
        now = time.monotonic()
        cached = _CACHE.get(machine)
        if cached is not None and (now - cached[0]) < ttl:
            return cached[1]
        results = _check_machine(machine, targets, key_path, host_concurrency)
        _CACHE[machine] = (now, results)
        return results


def _check_machine(
    machine: str,
    targets: list[Target],
    key_path: Path | None,
    host_concurrency: int,
) -> list[StatusResult]:
    if not targets:
        return []

    first = targets[0]
    if first.ssh_host is None:
        # Local machine — per-repo status, no SSH
        def _local_one(t: Target) -> StatusResult:
            try:
                text = gitops.get_status(t, key_path)
                return StatusResult(target=t, status=text.strip(),
                                    error_kind=None, error_message=None, error_detail=None)
            except GitError as exc:
                return StatusResult(target=t, status=None,
                                    error_kind=exc.kind.value, error_message=exc.message,
                                    error_detail=exc.stderr)

        with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
            futures = [pool.submit(_local_one, t) for t in targets]
            return [f.result() for f in futures]

    ssh_host = first.ssh_host
    port = first.port

    # Check legacy-gate memo (keyed by machine name)
    legacy_ts = _LEGACY_GATE.get(machine)
    if legacy_ts is not None and (time.monotonic() - legacy_ts) < _LEGACY_GATE_TTL:
        return _fallback_per_repo(targets, key_path, host_concurrency)

    # Try batch
    repos = [normalize_repo_path(t.repo) for t in targets]
    try:
        batch_results = gitops.get_batch_status(ssh_host, port, repos, key_path)
    except GitError as exc:
        if exc.kind in _HOST_LEVEL_ERRORS:
            return [
                StatusResult(target=t, status=None,
                             error_kind=exc.kind.value, error_message=exc.message,
                             error_detail=exc.stderr)
                for t in targets
            ]
        # Gate rejected or unknown — fall back permanently for this host
        _LEGACY_GATE[machine] = time.monotonic()
        return _fallback_per_repo(targets, key_path, host_concurrency)

    # Map batch results back to targets
    results: list[StatusResult] = []
    for t in targets:
        repo_key = normalize_repo_path(t.repo)
        if repo_key not in batch_results:
            results.append(StatusResult(
                target=t, status=None,
                error_kind=ErrorKind.UNKNOWN.value,
                error_message="Batch output incomplete",
                error_detail="",
            ))
            continue
        rc, text = batch_results[repo_key]
        if rc == 0:
            results.append(StatusResult(target=t, status=text.strip(),
                                        error_kind=None, error_message=None, error_detail=None))
        else:
            kind, msg = gitops.classify_error(rc, text)
            results.append(StatusResult(
                target=t, status=None,
                error_kind=kind.value, error_message=msg,
                error_detail=text[:2000],
            ))
    return results


def _fallback_per_repo(
    targets: list[Target],
    key_path: Path | None,
    host_concurrency: int,
) -> list[StatusResult]:
    if not targets:
        return []

    ssh_host = targets[0].ssh_host
    sem = _get_fallback_sem(ssh_host, host_concurrency) if ssh_host else None

    def _one(t: Target) -> StatusResult:
        if sem:
            sem.acquire()
        try:
            text = gitops.get_status(t, key_path)
            return StatusResult(target=t, status=text.strip(),
                                error_kind=None, error_message=None, error_detail=None)
        except GitError as exc:
            return StatusResult(target=t, status=None,
                                error_kind=exc.kind.value, error_message=exc.message,
                                error_detail=exc.stderr)
        finally:
            if sem:
                sem.release()

    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futures = [pool.submit(_one, t) for t in targets]
        return [f.result() for f in futures]


def invalidate(machine: str | None = None) -> None:
    if machine is None:
        _CACHE.clear()
        _LEGACY_GATE.clear()
        _FALLBACK_SEMS.clear()
    else:
        _CACHE.pop(machine, None)
        _LEGACY_GATE.pop(machine, None)
