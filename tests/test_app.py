from __future__ import annotations

import time

import pytest

from difflab.config import Target
from difflab.gitops import GitError, build_command
import difflab.gitops as gitops_mod


CANNED_DIFF = """\
diff --git a/example.py b/example.py
index abc..def 100644
--- a/example.py
+++ b/example.py
@@ -1,2 +1,2 @@
-old line
+new line
"""

LOCAL_REPO = "/srv/repos/diff-lab"
REMOTE_HOST = "user@diffhost.example.com"
REMOTE_REPO = "/srv/repos/remote-repo"


def test_index_lists_targets(client, monkeypatch):
    # diff-lab is dirty; the remote repo raises GitError (SSH unreachable)
    def fake_execute(argv, **kw):
        if LOCAL_REPO in argv:
            return " M views.py\n"
        raise GitError("repo not found", stderr="fatal: not a git repo")
    monkeypatch.setattr(gitops_mod, "_execute", fake_execute)
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"
    data = resp.data.decode()
    assert "diff-lab" in data
    assert "/d/diff-lab" in data
    assert "Refresh" in data


def test_index_hides_clean_target(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.data.decode()
    assert "diff-lab" not in data
    assert "2 clean hidden" in data


def test_clean_target_accessible_via_direct_url(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    assert client.get("/d/diff-lab").status_code == 200
    assert client.get("/raw/diff-lab").status_code == 200


def test_index_shows_errored_target(client, monkeypatch):
    def boom(argv, **kw):
        raise GitError("repo not found", stderr="fatal: not a git repository")
    monkeypatch.setattr(gitops_mod, "_execute", boom)
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.data.decode()
    assert "diff-lab" in data
    assert "[ERROR]" in data


def test_diff_view_shows_add(client, monkeypatch):
    def fake_execute(argv, **kw):
        if "--numstat" in argv:
            return "1\t1\texample.py\n"
        if "--no-pager" in argv:
            return CANNED_DIFF
        return " M example.py"

    monkeypatch.setattr(gitops_mod, "_execute", fake_execute)
    resp = client.get("/d/diff-lab")
    assert resp.status_code == 200
    assert b"diff-add" in resp.data
    data = resp.data.decode()
    assert 'class="status-row"' in data
    assert 'data-copy-path="example.py"' in data
    assert '<span class="count-add">+1</span>' in data
    assert '<span class="count-del">-1</span>' in data


def test_diff_view_clean(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    resp = client.get("/d/diff-lab")
    assert resp.status_code == 200
    assert b"working tree clean" in resp.data.lower()


def test_unknown_target_404(client):
    assert client.get("/d/nope").status_code == 404
    assert client.get("/raw/nope").status_code == 404


def test_raw_returns_plain_text(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: CANNED_DIFF)
    resp = client.get("/raw/diff-lab")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"
    assert "text/plain" in resp.content_type
    assert resp.data.decode() == CANNED_DIFF


def test_git_error_returns_502(client, monkeypatch):
    def boom(argv, **kw):
        raise GitError("repo not found", stderr="fatal: not a git repository")
    monkeypatch.setattr(gitops_mod, "_execute", boom)
    resp = client.get("/d/diff-lab")
    assert resp.status_code == 502
    assert b"repo not found" in resp.data
    assert b"Traceback" not in resp.data


def test_build_command_local():
    t = Target(name="x", machine="local", repo=LOCAL_REPO, ssh_host=None)
    cmd = build_command(t, "diff")
    assert cmd == ["git", "-C", LOCAL_REPO, "--no-pager", "diff"]


def test_build_command_numstat_local():
    t = Target(name="x", machine="local", repo=LOCAL_REPO, ssh_host=None)
    cmd = build_command(t, "numstat")
    assert cmd == ["git", "-C", LOCAL_REPO, "--no-pager", "diff", "--numstat"]


def test_build_command_remote():
    t = Target(name="x", machine="diffhost", repo=REMOTE_REPO, ssh_host=REMOTE_HOST)
    cmd = build_command(t, "diff")
    assert cmd[0] == "ssh"
    assert cmd[1:3] == ["-o", "BatchMode=yes"]
    assert REMOTE_HOST in cmd
    import shlex
    assert shlex.quote(REMOTE_REPO) in cmd[-1]


def test_build_command_unknown_op_raises():
    t = Target(name="x", machine="local", repo=LOCAL_REPO, ssh_host=None)
    with pytest.raises(ValueError, match="Unknown"):
        build_command(t, "push")


# ── Issue 1: Windows shell quoting ──────────────────────────────────────────

def test_build_command_windows_uses_double_quotes():
    t = Target(
        name="x", machine="winbox", repo="C:/projects/myrepo",
        ssh_host="user@winbox.example.com", shell="windows",
    )
    cmd = build_command(t, "diff")
    remote = cmd[-1]
    assert '"C:/projects/myrepo"' in remote
    assert "'" not in remote


def test_build_command_windows_normalizes_backslashes():
    t = Target(
        name="x", machine="winbox", repo="C:\\projects\\myrepo",
        ssh_host="user@winbox.example.com", shell="windows",
    )
    cmd = build_command(t, "diff")
    remote = cmd[-1]
    assert '"C:/projects/myrepo"' in remote
    assert "\\" not in remote


def test_build_command_posix_uses_shlex_quote():
    import shlex
    t = Target(
        name="x", machine="linbox", repo="/home/alice/my repo",
        ssh_host="alice@linbox.example.com", shell="posix",
    )
    cmd = build_command(t, "diff")
    remote = cmd[-1]
    assert shlex.quote("/home/alice/my repo") in remote


# ── Issue 3: parallel index buckets correctly ────────────────────────────────

def test_index_parallel_buckets_dirty_errored_clean(client, monkeypatch):
    """Parallel execution must still correctly classify dirty / errored / clean."""
    calls = []

    def fake_execute(argv, **kw):
        calls.append(argv)
        if LOCAL_REPO in argv:
            return " M views.py\n"
        raise GitError("ssh unreachable", stderr="")

    monkeypatch.setattr(gitops_mod, "_execute", fake_execute)
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.data.decode()
    assert "diff-lab" in data
    assert "[ERROR]" in data
    # Both targets were checked
    assert len(calls) == 2


def test_index_parallel_concurrency(client, monkeypatch):
    """Status checks must run concurrently, not sequentially."""
    import threading

    in_flight = []
    lock = threading.Lock()
    peak = [0]

    def slow_execute(argv, **kw):
        with lock:
            in_flight.append(1)
            peak[0] = max(peak[0], len(in_flight))
        time.sleep(0.05)
        with lock:
            in_flight.pop()
        return ""

    monkeypatch.setattr(gitops_mod, "_execute", slow_execute)
    resp = client.get("/")
    assert resp.status_code == 200
    # Both targets ran; with concurrency peak should be 2
    assert peak[0] == 2
