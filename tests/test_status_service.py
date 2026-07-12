from __future__ import annotations

import threading
import time

import pytest

from difflab.config import Target
from difflab.gitops import ErrorKind, GitError
import difflab.gitops as gitops
import difflab.status_service as ss


def _target(name, machine, repo, ssh_host="user@host.example.com", port=22):
    return Target(name=name, machine=machine, repo=repo, ssh_host=ssh_host, port=port)


def _local_target(name, repo):
    return Target(name=name, machine="local", repo=repo, ssh_host=None)


@pytest.fixture(autouse=True)
def clear_state():
    ss.invalidate()
    yield
    ss.invalidate()


# ── 8. Batch success ─────────────────────────────────────────────────────────

def test_batch_success_maps_results(monkeypatch):
    targets = [
        _target("dev-App", "dev", "C:\\Users\\Alice\\App"),
        _target("dev-Other", "dev", "/home/alice/other"),
    ]

    def fake_batch(ssh_host, port, repos, key_path):
        # repos should be normalized
        assert "c:/Users/Alice/App" in repos
        assert "/home/alice/other" in repos
        return {
            "c:/Users/Alice/App": (0, " M file.py"),  # rc=0 with output = dirty
            "/home/alice/other": (0, ""),              # rc=0 empty = clean
        }

    monkeypatch.setattr(gitops, "get_batch_status", fake_batch)
    results = ss.get_machine_status("dev", targets, None, ttl=0)

    app_result = next(r for r in results if r.target.name == "dev-App")
    other_result = next(r for r in results if r.target.name == "dev-Other")
    assert app_result.state == "dirty"
    assert "M file.py" in (app_result.status or "")
    assert other_result.state == "clean"


# ── 9. GATE_REJECTED → fallback + memo ───────────────────────────────────────

def test_gate_rejected_falls_back_and_memos(monkeypatch):
    targets = [_target("dev-A", "dev", "/a"), _target("dev-B", "dev", "/b")]

    def fail_batch(*a, **kw):
        raise GitError("Remote gate refused command", kind=ErrorKind.GATE_REJECTED)

    get_status_calls = []

    def fake_status(t, key_path=None):
        get_status_calls.append(t.name)
        return " M file.py"

    monkeypatch.setattr(gitops, "get_batch_status", fail_batch)
    monkeypatch.setattr(gitops, "get_status", fake_status)

    results = ss.get_machine_status("dev", targets, None, ttl=0)
    assert len(get_status_calls) == 2
    assert all(r.state == "dirty" for r in results)

    # Second probe without invalidating: batch should NOT be tried (memo persists)
    get_status_calls.clear()
    batch_calls = []

    def track_batch(*a, **kw):
        batch_calls.append(1)
        return {}

    monkeypatch.setattr(gitops, "get_batch_status", track_batch)
    ss.get_machine_status("dev", targets, None, ttl=0)  # no invalidate
    assert not batch_calls
    assert get_status_calls  # fallback still used


# ── 10. UNREACHABLE → host-level error, no fallback ─────────────────────────

def test_unreachable_errors_all_targets_no_fallback(monkeypatch):
    targets = [_target("dev-A", "dev", "/a"), _target("dev-B", "dev", "/b")]

    def fail_batch(*a, **kw):
        raise GitError("Machine unreachable", kind=ErrorKind.UNREACHABLE)

    status_calls = []
    monkeypatch.setattr(gitops, "get_batch_status", fail_batch)
    monkeypatch.setattr(gitops, "get_status", lambda t, k=None: status_calls.append(1) or "")

    results = ss.get_machine_status("dev", targets, None, ttl=0)
    assert all(r.error_kind == "unreachable" for r in results)
    assert not status_calls  # no fallback for host-level errors


# ── 11. Fallback semaphore ────────────────────────────────────────────────────

def test_fallback_respects_host_concurrency(monkeypatch):
    N = 6
    concurrency = 2
    targets = [
        _target(f"dev-r{i}", "dev", f"/repo{i}", ssh_host="dev.example.com")
        for i in range(N)
    ]

    in_flight = []
    peak = [0]
    guard = threading.Lock()

    def slow_status(t, key_path=None):
        with guard:
            in_flight.append(1)
            peak[0] = max(peak[0], len(in_flight))
        time.sleep(0.04)
        with guard:
            in_flight.pop()
        return ""

    # Force fallback path
    ss._LEGACY_GATE["dev"] = time.monotonic()
    ss._FALLBACK_SEMS.pop("dev.example.com", None)

    monkeypatch.setattr(gitops, "get_status", slow_status)
    ss.get_machine_status("dev", targets, None, ttl=0, host_concurrency=concurrency)
    assert peak[0] <= concurrency


# ── 12. Cache TTL ─────────────────────────────────────────────────────────────

def test_cache_within_ttl_returns_same_result(monkeypatch):
    targets = [_target("dev-A", "dev", "/a")]
    call_count = [0]

    def fake_batch(*a, **kw):
        call_count[0] += 1
        return {"/a": (0, "")}

    monkeypatch.setattr(gitops, "get_batch_status", fake_batch)
    ss.get_machine_status("dev", targets, None, ttl=60)
    ss.get_machine_status("dev", targets, None, ttl=60)
    assert call_count[0] == 1


def test_invalidate_forces_reproble(monkeypatch):
    targets = [_target("dev-A", "dev", "/a")]
    call_count = [0]

    def fake_batch(*a, **kw):
        call_count[0] += 1
        return {"/a": (0, "")}

    monkeypatch.setattr(gitops, "get_batch_status", fake_batch)
    ss.get_machine_status("dev", targets, None, ttl=60)
    ss.invalidate("dev")
    ss.get_machine_status("dev", targets, None, ttl=60)
    assert call_count[0] == 2


def test_ttl_zero_disables_cache(monkeypatch):
    targets = [_target("dev-A", "dev", "/a")]
    call_count = [0]

    def fake_batch(*a, **kw):
        call_count[0] += 1
        return {"/a": (0, "")}

    monkeypatch.setattr(gitops, "get_batch_status", fake_batch)
    ss.get_machine_status("dev", targets, None, ttl=0)
    ss.get_machine_status("dev", targets, None, ttl=0)
    assert call_count[0] == 2


# ── 13. Single-flight ─────────────────────────────────────────────────────────

def test_single_flight_concurrent_requests(monkeypatch):
    targets = [_target("dev-A", "dev", "/a")]
    probe_count = [0]
    results_collected = []

    def slow_batch(*a, **kw):
        probe_count[0] += 1
        time.sleep(0.1)
        return {"/a": (0, "")}

    monkeypatch.setattr(gitops, "get_batch_status", slow_batch)

    def call():
        r = ss.get_machine_status("dev", targets, None, ttl=30)
        results_collected.append(r)

    t1 = threading.Thread(target=call)
    t2 = threading.Thread(target=call)
    t1.start()
    time.sleep(0.01)
    t2.start()
    t1.join()
    t2.join()

    assert probe_count[0] == 1
    assert len(results_collected) == 2
    assert results_collected[0] is results_collected[1]


# ── 14. Local machine ─────────────────────────────────────────────────────────

def test_local_machine_uses_get_status_not_batch(monkeypatch):
    targets = [_local_target("local-A", "/app")]
    batch_calls = []
    status_calls = []

    monkeypatch.setattr(gitops, "get_batch_status", lambda *a, **kw: batch_calls.append(1) or {})
    monkeypatch.setattr(gitops, "get_status", lambda t, k=None: status_calls.append(t.name) or " M f.py")

    results = ss.get_machine_status("local", targets, None, ttl=0)
    assert not batch_calls
    assert "local-A" in status_calls
    assert results[0].state == "dirty"


# ── 15. Missing path in batch output ─────────────────────────────────────────

def test_batch_missing_path_gives_incomplete_error(monkeypatch):
    targets = [_target("dev-A", "dev", "/a"), _target("dev-B", "dev", "/b")]

    monkeypatch.setattr(gitops, "get_batch_status", lambda *a, **kw: {"/a": (0, "")})

    results = ss.get_machine_status("dev", targets, None, ttl=0)
    a = next(r for r in results if r.target.name == "dev-A")
    b = next(r for r in results if r.target.name == "dev-B")
    assert a.state == "clean"
    assert b.error_kind == "unknown"
    assert "incomplete" in (b.error_message or "").lower()


# ── Busy + Auth also errors all targets (host-level) ─────────────────────────

def test_timeout_errors_all_targets(monkeypatch):
    targets = [_target("dev-A", "dev", "/a"), _target("dev-B", "dev", "/b")]
    monkeypatch.setattr(gitops, "get_batch_status",
                        lambda *a, **kw: (_ for _ in ()).throw(GitError("timed out", kind=ErrorKind.TIMEOUT)))
    results = ss.get_machine_status("dev", targets, None, ttl=0)
    assert all(r.error_kind == "timeout" for r in results)


def test_auth_errors_all_targets_no_fallback(monkeypatch):
    targets = [_target("dev-A", "dev", "/a")]
    status_calls = []
    monkeypatch.setattr(gitops, "get_batch_status",
                        lambda *a, **kw: (_ for _ in ()).throw(GitError("denied", kind=ErrorKind.AUTH)))
    monkeypatch.setattr(gitops, "get_status", lambda t, k=None: status_calls.append(1) or "")
    results = ss.get_machine_status("dev", targets, None, ttl=0)
    assert results[0].error_kind == "auth"
    assert not status_calls
