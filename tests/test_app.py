from __future__ import annotations

import pytest

from difflab.config import Target
from difflab.gitops import ErrorKind, GitError, build_command
import difflab.gitops as gitops_mod
import difflab.status_service as ss_mod
from difflab.status_service import StatusResult


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


# ── Index: progressive rendering ─────────────────────────────────────────────

def test_index_renders_all_targets_pending(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.data.decode()
    assert 'data-state="pending"' in data
    assert "diff-lab" in data
    assert "remote-repo" in data


def test_index_has_machines_script(client):
    resp = client.get("/")
    data = resp.data.decode()
    assert "MACHINES" in data
    # Both machine names from conftest fixtures
    assert '"local"' in data
    assert '"diffhost"' in data


def test_index_does_not_probe_ssh(client, monkeypatch):
    called = []
    monkeypatch.setattr(gitops_mod, "_execute", lambda *a, **kw: called.append(a) or "")
    client.get("/")
    assert called == []


def test_index_hides_clean_target_as_pending(client):
    # With new arch, index renders all as pending — clean hiding is JS-side
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.data.decode()
    # Both targets visible as pending rows (not hidden server-side)
    assert 'data-state="pending"' in data
    assert "diff-lab" in data


def test_index_shows_errored_target_as_pending(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.data.decode()
    assert 'data-state="pending"' in data


def test_index_has_status_column(client):
    resp = client.get("/")
    data = resp.data.decode()
    assert "status-cell" in data
    assert "checking" in data


def test_index_sorted_by_machine_then_display_name(client, monkeypatch, app):
    """Initial render order reflects group_by_machine sort: machine ASC, display_name ASC."""
    app.config["DIFFLAB_TARGETS"] = {
        "zebra-Zoo": Target(name="zebra-Zoo", machine="zebra", repo="/a", ssh_host="u@z"),
        "alpha-Beta": Target(name="alpha-Beta", machine="alpha", repo="/b", ssh_host="u@a"),
        "alpha-Alpha": Target(name="alpha-Alpha", machine="alpha", repo="/c", ssh_host="u@a"),
    }
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.data.decode()
    # Machines are sorted: alpha < zebra; within alpha: Alpha < Beta
    assert data.index('data-name="alpha-Alpha"') < data.index('data-name="alpha-Beta"') < data.index('data-name="zebra-Zoo"')


def test_index_errored_sorted_by_machine_then_display_name(client, monkeypatch, app):
    """Pending rows rendered in (machine, display_name) order regardless of future state."""
    app.config["DIFFLAB_TARGETS"] = {
        "z-ZebraRepo": Target(name="z-ZebraRepo", machine="z", repo="/a", ssh_host="u@z"),
        "a-BetaApp": Target(name="a-BetaApp", machine="a", repo="/b", ssh_host="u@a"),
        "a-AlphaApp": Target(name="a-AlphaApp", machine="a", repo="/c", ssh_host="u@a"),
    }
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.data.decode()
    assert data.index('data-name="a-AlphaApp"') < data.index('data-name="a-BetaApp"') < data.index('data-name="z-ZebraRepo"')


# ── /api/status/<machine> ────────────────────────────────────────────────────

def test_api_status_unknown_machine_returns_404(client):
    resp = client.get("/api/status/nonexistent-machine")
    assert resp.status_code == 404


def test_api_status_dirty_result(client, monkeypatch, app):
    t = app.config["DIFFLAB_TARGETS"]["diff-lab"]
    dirty = StatusResult(target=t, status=" M file.py", error_kind=None,
                         error_message=None, error_detail=None)
    monkeypatch.setattr(ss_mod, "get_machine_status", lambda m, tgts, kp, **kw: [dirty])

    resp = client.get("/api/status/local")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["machine"] == "local"
    result = next(r for r in data["results"] if r["name"] == "diff-lab")
    assert result["state"] == "dirty"
    assert result["error"] is None


def test_api_status_clean_all(client, monkeypatch, app):
    results = [
        StatusResult(target=t, status="", error_kind=None, error_message=None, error_detail=None)
        for t in app.config["DIFFLAB_TARGETS"].values()
        if t.machine == "local"
    ]
    monkeypatch.setattr(ss_mod, "get_machine_status", lambda m, tgts, kp, **kw: results)
    resp = client.get("/api/status/local")
    data = resp.get_json()
    assert all(r["state"] == "clean" for r in data["results"])


def test_api_status_error_result_has_kind(client, monkeypatch, app):
    t = app.config["DIFFLAB_TARGETS"]["diff-lab"]
    err = StatusResult(target=t, status=None, error_kind="unreachable",
                       error_message="Machine unreachable", error_detail="ssh: connect to host")
    monkeypatch.setattr(ss_mod, "get_machine_status", lambda m, tgts, kp, **kw: [err])

    resp = client.get("/api/status/local")
    data = resp.get_json()
    result = next(r for r in data["results"] if r["name"] == "diff-lab")
    assert result["state"] == "error"
    assert result["error"]["kind"] == "unreachable"
    assert "unreachable" in result["error"]["message"].lower()
    assert "exit 255" not in result["error"]["message"]


def test_api_status_error_has_tooltip_detail(client, monkeypatch, app):
    t = app.config["DIFFLAB_TARGETS"]["diff-lab"]
    err = StatusResult(target=t, status=None, error_kind="unknown",
                       error_message="Can't read this repo", error_detail="fatal: some raw detail")
    monkeypatch.setattr(ss_mod, "get_machine_status", lambda m, tgts, kp, **kw: [err])

    resp = client.get("/api/status/local")
    data = resp.get_json()
    result = next(r for r in data["results"] if r["name"] == "diff-lab")
    assert result["error"]["detail"] == "fatal: some raw detail"


def test_api_status_dirty_row_has_name(client, monkeypatch, app):
    t = app.config["DIFFLAB_TARGETS"]["diff-lab"]
    dirty = StatusResult(target=t, status=" M file.py", error_kind=None,
                         error_message=None, error_detail=None)
    monkeypatch.setattr(ss_mod, "get_machine_status", lambda m, tgts, kp, **kw: [dirty])

    resp = client.get("/api/status/local")
    data = resp.get_json()
    assert any(r["name"] == "diff-lab" for r in data["results"])


def test_api_status_results_sorted_by_display_name(client, monkeypatch, app):
    """API results for a machine are sorted by display_name (case-insensitive)."""
    from difflab.status_service import group_by_machine
    app.config["DIFFLAB_TARGETS"] = {
        "dev-Zoo": Target(name="dev-Zoo", machine="dev", repo="/z", ssh_host="u@dev"),
        "dev-Apple": Target(name="dev-Apple", machine="dev", repo="/a", ssh_host="u@dev"),
        "dev-Banana": Target(name="dev-Banana", machine="dev", repo="/b", ssh_host="u@dev"),
    }

    def fake_get_status(machine, targets, key_path, **kw):
        return [
            StatusResult(target=t, status="", error_kind=None, error_message=None, error_detail=None)
            for t in targets
        ]

    monkeypatch.setattr(ss_mod, "get_machine_status", fake_get_status)
    resp = client.get("/api/status/dev")
    data = resp.get_json()
    names = [r["display_name"] for r in data["results"]]
    assert names == sorted(names, key=str.casefold)


# ── Canonical routing ────────────────────────────────────────────────────────

def test_canonical_diff_view_200(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    assert client.get("/d/local/diff-lab").status_code == 200


def test_canonical_raw_view_200(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    assert client.get("/raw/local/diff-lab").status_code == 200


def test_legacy_diff_redirects_to_canonical(client):
    resp = client.get("/d/diff-lab")
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/d/local/diff-lab")


def test_legacy_raw_redirects_to_canonical(client):
    resp = client.get("/raw/diff-lab")
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/raw/local/diff-lab")


def test_canonical_unknown_machine_404(client):
    assert client.get("/d/bogus/diff-lab").status_code == 404


def test_canonical_unknown_name_404(client):
    assert client.get("/d/local/bogus").status_code == 404


# ── /api/targets ──────────────────────────────────────────────────────────────

def test_api_targets_shape(client):
    resp = client.get("/api/targets")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) == 2
    machines = {t["machine"] for t in data}
    assert "local" in machines
    assert "diffhost" in machines
    for t in data:
        assert "machine" in t
        assert "name" in t
        assert "display_name" in t
        assert "repo" in t
        assert t["url"].startswith("/d/")
        assert t["machine"] in t["url"]


def test_api_targets_canonical_url(client):
    resp = client.get("/api/targets")
    data = resp.get_json()
    local = next(t for t in data if t["machine"] == "local")
    assert local["url"] == "/d/local/diff-lab"


def test_api_targets_no_store_cache(client):
    resp = client.get("/api/targets")
    assert resp.headers["Cache-Control"] == "no-store"


def test_api_targets_enriched_shape(client, monkeypatch):
    def fake_status(machine, targets, key_path, **kw):
        return [
            StatusResult(target=t, status=" M file.py", error_kind=None,
                         error_message=None, error_detail=None)
            for t in targets
        ]
    monkeypatch.setattr(ss_mod, "get_machine_status", fake_status)
    resp = client.get("/api/targets")
    data = resp.get_json()
    for t in data:
        assert "state" in t
        assert "file_count" in t
        assert "error" in t


def test_api_targets_includes_clean_targets(client, monkeypatch):
    def fake_status(machine, targets, key_path, **kw):
        return [
            StatusResult(target=t, status="", error_kind=None,
                         error_message=None, error_detail=None)
            for t in targets
        ]
    monkeypatch.setattr(ss_mod, "get_machine_status", fake_status)
    resp = client.get("/api/targets")
    data = resp.get_json()
    assert len(data) == 2
    assert all(t["state"] == "clean" for t in data)
    assert all(t["file_count"] == 0 for t in data)
    assert all(t["error"] is None for t in data)


def test_api_targets_includes_error_targets(client, monkeypatch):
    def fake_status(machine, targets, key_path, **kw):
        return [
            StatusResult(target=t, status=None, error_kind="unreachable",
                         error_message="Machine unreachable", error_detail="")
            for t in targets
        ]
    monkeypatch.setattr(ss_mod, "get_machine_status", fake_status)
    resp = client.get("/api/targets")
    data = resp.get_json()
    assert len(data) == 2
    for t in data:
        assert t["state"] == "error"
        assert t["file_count"] is None
        assert t["error"] == "Machine unreachable"


def test_api_targets_dirty_has_file_count(client, monkeypatch, app):
    local_t = app.config["DIFFLAB_TARGETS"]["diff-lab"]
    def fake_status(machine, targets, key_path, **kw):
        if machine == "local":
            return [StatusResult(target=local_t, status=" M a.py\n M b.py\n?? c.txt\n",
                                 error_kind=None, error_message=None, error_detail=None)]
        return [StatusResult(target=t, status="", error_kind=None,
                             error_message=None, error_detail=None)
                for t in targets]
    monkeypatch.setattr(ss_mod, "get_machine_status", fake_status)
    resp = client.get("/api/targets")
    data = resp.get_json()
    local = next(t for t in data if t["machine"] == "local")
    assert local["state"] == "dirty"
    assert local["file_count"] == 3


def test_api_targets_static_skips_status(client, monkeypatch):
    calls = []
    monkeypatch.setattr(ss_mod, "get_machine_status", lambda *a, **kw: calls.append(1) or [])
    resp = client.get("/api/targets?static=1")
    assert resp.status_code == 200
    assert calls == []
    data = resp.get_json()
    for t in data:
        assert "state" not in t
        assert "file_count" not in t
        assert "error" not in t


def test_api_targets_static_has_base_fields(client):
    resp = client.get("/api/targets?static=1")
    data = resp.get_json()
    for t in data:
        assert "machine" in t
        assert "name" in t
        assert "display_name" in t
        assert "repo" in t
        assert t["url"].startswith("/d/")


# ── /d/<machine>/<name> — canonical diff views ───────────────────────────────

def test_clean_target_accessible_via_canonical_url(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    assert client.get("/d/local/diff-lab").status_code == 200
    assert client.get("/raw/local/diff-lab").status_code == 200


def test_diff_view_shows_add(client, monkeypatch):
    monkeypatch.setattr(
        gitops_mod,
        "_execute",
        lambda argv, **kw: CANNED_DIFF if "--no-pager" in argv else " M example.py",
    )
    resp = client.get("/d/local/diff-lab")
    assert resp.status_code == 200
    assert b"diff-add" in resp.data


def test_diff_view_clean(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    resp = client.get("/d/local/diff-lab")
    assert resp.status_code == 200
    assert b"working tree clean" in resp.data.lower()


def test_unknown_target_404(client):
    assert client.get("/d/nope").status_code == 404
    assert client.get("/raw/nope").status_code == 404


def test_raw_returns_plain_text(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: CANNED_DIFF)
    resp = client.get("/raw/local/diff-lab")
    assert resp.status_code == 200
    assert "text/plain" in resp.content_type
    assert resp.data.decode() == CANNED_DIFF


def test_git_error_returns_502(client, monkeypatch):
    def boom(argv, **kw):
        raise GitError("repo not found", stderr="fatal: not a git repository")
    monkeypatch.setattr(gitops_mod, "_execute", boom)
    resp = client.get("/d/local/diff-lab")
    assert resp.status_code == 502
    assert b"repo not found" in resp.data
    assert b"Traceback" not in resp.data


def test_build_command_local():
    t = Target(name="x", machine="local", repo=LOCAL_REPO, ssh_host=None)
    cmd = build_command(t, "diff")
    assert cmd == ["git", "-C", LOCAL_REPO, "--no-pager", "diff", "HEAD"]


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


# ── New / deleted file handling ─────────────────────────────────────────────
# git diff HEAD includes both staged and unstaged changes, so staged new
# and deleted files (which git diff without HEAD would miss) are rendered.

STAGED_NEW_DIFF = """\
diff --git a/newfile.py b/newfile.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/newfile.py
@@ -0,0 +1,2 @@
+print("hello")
+print("world")
"""

STAGED_DELETED_DIFF = """\
diff --git a/oldfile.py b/oldfile.py
deleted file mode 100644
index abc1234..0000000
--- a/oldfile.py
+++ /dev/null
@@ -1,2 +0,0 @@
-print("goodbye")
-print("cruel world")
"""


def test_diff_view_renders_staged_new_file(client, monkeypatch):
    def fake_execute(argv, **kw):
        if "--numstat" in argv:
            return "2\t0\tnewfile.py\n"
        if "--no-pager" in argv:
            return STAGED_NEW_DIFF
        return "A  newfile.py\n"
    monkeypatch.setattr(gitops_mod, "_execute", fake_execute)
    resp = client.get("/d/local/diff-lab")
    assert resp.status_code == 200
    data = resp.data.decode()
    assert "newfile.py" in data
    assert "diff-add" in data
    assert "print(&#34;hello&#34;)" in data
    assert 'data-anchor="diff-' in data


def test_diff_view_renders_staged_deleted_file(client, monkeypatch):
    def fake_execute(argv, **kw):
        if "--numstat" in argv:
            return "0\t2\toldfile.py\n"
        if "--no-pager" in argv:
            return STAGED_DELETED_DIFF
        return "D  oldfile.py\n"
    monkeypatch.setattr(gitops_mod, "_execute", fake_execute)
    resp = client.get("/d/local/diff-lab")
    assert resp.status_code == 200
    data = resp.data.decode()
    assert "oldfile.py" in data
    assert "diff-del" in data
    assert "print(&#34;goodbye&#34;)" in data


def test_diff_view_status_heading_counts_staged_new_deleted(client, monkeypatch):
    def fake_execute(argv, **kw):
        if "--numstat" in argv:
            return "2\t0\tnewfile.py\n0\t2\toldfile.py\n"
        if "--no-pager" in argv:
            return STAGED_NEW_DIFF + "\n" + STAGED_DELETED_DIFF
        return "A  newfile.py\nD  oldfile.py\n"
    monkeypatch.setattr(gitops_mod, "_execute", fake_execute)
    resp = client.get("/d/local/diff-lab")
    assert resp.status_code == 200
    data = resp.data.decode()
    assert "2 files" in data
    assert "+2" in data
    assert "−2" in data


def test_diff_view_untracked_only_banner(client, monkeypatch):
    def fake_execute(argv, **kw):
        if "status" in argv:
            return "?? docker-compose.yml.bak\n"
        return ""
    monkeypatch.setattr(gitops_mod, "_execute", fake_execute)
    resp = client.get("/d/local/diff-lab")
    assert resp.status_code == 200
    data = resp.data.decode().lower()
    assert "no uncommitted changes to tracked files" in data
    assert "1 untracked file" in data
    assert "working tree clean" not in data


def test_diff_view_untracked_shows_badge_not_counts(client, monkeypatch):
    def fake_execute(argv, **kw):
        if "status" in argv:
            return "?? newfile.txt\n"
        return ""
    monkeypatch.setattr(gitops_mod, "_execute", fake_execute)
    resp = client.get("/d/local/diff-lab")
    assert resp.status_code == 200
    data = resp.data.decode()
    assert "change-counts--untracked" in data
    assert 'count-add">+0' not in data  # untracked rows must not show numeric counts


def test_diff_view_fully_clean_banner(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    resp = client.get("/d/local/diff-lab")
    assert resp.status_code == 200
    assert b"working tree clean" in resp.data.lower()
    assert b"untracked file" not in resp.data.lower()


def test_diff_view_untracked_plural(client, monkeypatch):
    def fake_execute(argv, **kw):
        if "status" in argv:
            return "?? a.bak\n?? b.bak\n"
        return ""
    monkeypatch.setattr(gitops_mod, "_execute", fake_execute)
    resp = client.get("/d/local/diff-lab")
    assert resp.status_code == 200
    assert b"2 untracked files" in resp.data


def test_diff_page_shows_display_name_not_slug(client, monkeypatch, app):
    t = Target(name="dev-MyApp", machine="dev", repo="/repos/MyApp", ssh_host="user@dev.example.com")
    app.config["DIFFLAB_TARGETS"] = {"dev-MyApp": t}
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    resp = client.get("/d/dev/MyApp")
    assert resp.status_code == 200
    data = resp.data.decode()
    assert "MyApp" in data
    assert "dev-MyApp" not in data.split("diff.lab")[1]


def test_diff_error_shows_collapsible_detail(client, monkeypatch):
    def boom(argv, **kw):
        raise GitError("Repository path not found", stderr="fatal: not a git repository")
    monkeypatch.setattr(gitops_mod, "_execute", boom)
    resp = client.get("/d/local/diff-lab")
    assert resp.status_code == 502
    data = resp.data.decode()
    assert "Repository path not found" in data
    assert "fatal: not a git repository" in data
    assert "<details" in data


def test_diff_page_shows_machine_name(client, monkeypatch, app):
    t = Target(name="prod-API", machine="prod", repo="/repos/API", ssh_host="u@prod.example.com")
    app.config["DIFFLAB_TARGETS"] = {"prod-API": t}
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    resp = client.get("/d/prod/API")
    assert resp.status_code == 200
    data = resp.data.decode()
    assert "diff-page-machine" in data
    assert ">prod<" in data


# ── Feature: Index Files column and column order ──────────────────────────────

def test_index_has_files_column_header(client):
    resp = client.get("/")
    data = resp.data.decode()
    assert "Files" in data
    assert 'data-sort-type="numeric"' in data


def test_index_rows_have_files_cell(client):
    resp = client.get("/")
    data = resp.data.decode()
    assert 'files-cell' in data


def test_index_files_column_before_status(client):
    resp = client.get("/")
    data = resp.data.decode()
    assert data.index(">Files<") < data.index(">Status<")


def test_api_status_dirty_has_file_count(client, monkeypatch, app):
    t = app.config["DIFFLAB_TARGETS"]["diff-lab"]
    dirty = StatusResult(target=t, status=" M a.py\n M b.py\n?? c.txt\n",
                         error_kind=None, error_message=None, error_detail=None)
    monkeypatch.setattr(ss_mod, "get_machine_status", lambda m, tgts, kp, **kw: [dirty])
    resp = client.get("/api/status/local")
    data = resp.get_json()
    result = next(r for r in data["results"] if r["name"] == "diff-lab")
    assert result["file_count"] == 3


def test_api_status_clean_has_zero_file_count(client, monkeypatch, app):
    t = app.config["DIFFLAB_TARGETS"]["diff-lab"]
    clean = StatusResult(target=t, status="", error_kind=None, error_message=None, error_detail=None)
    monkeypatch.setattr(ss_mod, "get_machine_status", lambda m, tgts, kp, **kw: [clean])
    resp = client.get("/api/status/local")
    data = resp.get_json()
    result = next(r for r in data["results"] if r["name"] == "diff-lab")
    assert result["file_count"] == 0


def test_api_status_error_has_null_file_count(client, monkeypatch, app):
    t = app.config["DIFFLAB_TARGETS"]["diff-lab"]
    err = StatusResult(target=t, status=None, error_kind="unreachable",
                       error_message="Machine unreachable", error_detail="")
    monkeypatch.setattr(ss_mod, "get_machine_status", lambda m, tgts, kp, **kw: [err])
    resp = client.get("/api/status/local")
    data = resp.get_json()
    result = next(r for r in data["results"] if r["name"] == "diff-lab")
    assert result["file_count"] is None


# ── Feature: Diff status heading with counts ──────────────────────────────────

def test_diff_status_heading_shows_file_count(client, monkeypatch):
    def fake_execute(argv, **kw):
        if "--numstat" in argv:
            return "3\t1\tfoo.py\n2\t0\tbar.py\n"
        if "--no-pager" in argv:
            return CANNED_DIFF
        return " M foo.py\n M bar.py\n"
    monkeypatch.setattr(gitops_mod, "_execute", fake_execute)
    resp = client.get("/d/local/diff-lab")
    data = resp.data.decode()
    assert "2 files" in data


def test_diff_status_heading_shows_added_deleted(client, monkeypatch):
    def fake_execute(argv, **kw):
        if "--numstat" in argv:
            return "3\t1\tfoo.py\n2\t0\tbar.py\n"
        if "--no-pager" in argv:
            return CANNED_DIFF
        return " M foo.py\n M bar.py\n"
    monkeypatch.setattr(gitops_mod, "_execute", fake_execute)
    resp = client.get("/d/local/diff-lab")
    data = resp.data.decode()
    assert "+5" in data  # 3+2 total added in heading
    assert "−1" in data  # 1+0 total deleted in heading


def test_diff_status_heading_notes_untracked(client, monkeypatch):
    def fake_execute(argv, **kw):
        if "--numstat" in argv:
            return "1\t0\tfoo.py\n"
        if "--no-pager" in argv:
            return CANNED_DIFF
        return " M foo.py\n?? orphan.txt\n"
    monkeypatch.setattr(gitops_mod, "_execute", fake_execute)
    resp = client.get("/d/local/diff-lab")
    data = resp.data.decode()
    assert "untracked" in data
    assert "2 files" in data  # 1 tracked + 1 untracked


def test_diff_status_heading_no_counts_when_clean(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    resp = client.get("/d/local/diff-lab")
    data = resp.data.decode()
    # When clean, heading is just "Status" with no count parens
    assert "Status" in data
    assert "+0" not in data


# ── Feature: Collapsible status section ──────────────────────────────────────

def test_diff_status_section_uses_details(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    resp = client.get("/d/local/diff-lab")
    data = resp.data.decode()
    assert '<details class="status-details" open>' in data


def test_diff_status_section_summary_contains_heading(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    resp = client.get("/d/local/diff-lab")
    data = resp.data.decode()
    assert '<summary class="status-heading">' in data
    assert "Status" in data


# ── Favicon ──────────────────────────────────────────────────────────────────

def test_favicon_ico_returns_svg(client):
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200
    assert "image/svg+xml" in resp.content_type


def test_index_has_favicon_link(client):
    resp = client.get("/")
    data = resp.data.decode()
    assert 'rel="icon"' in data
    assert "kingfisher_favicon.svg" in data


# ── Cache-Control headers ─────────────────────────────────────────────────────

def test_index_html_response_has_no_cache(client):
    resp = client.get("/")
    assert resp.headers["Cache-Control"] == "no-cache"


def test_diff_html_response_has_no_cache(client, monkeypatch):
    monkeypatch.setattr(gitops_mod, "_execute", lambda argv, **kw: "")
    resp = client.get("/d/local/diff-lab")
    assert resp.headers["Cache-Control"] == "no-cache"


def test_api_status_response_has_no_store(client, monkeypatch, app):
    t = app.config["DIFFLAB_TARGETS"]["diff-lab"]
    clean = StatusResult(target=t, status="", error_kind=None, error_message=None, error_detail=None)
    monkeypatch.setattr(ss_mod, "get_machine_status", lambda m, tgts, kp, **kw: [clean])
    resp = client.get("/api/status/local")
    assert resp.headers["Cache-Control"] == "no-store"
