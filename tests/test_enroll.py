from __future__ import annotations

import yaml
import pytest

from difflab import create_app
import difflab.enroll as enroll_mod

# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

FAKE_PUB_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyBlobForTesting difflab@container"
TOKEN = "s3cr3t-enroll-token"
VALID_BODY = {
    "token": TOKEN,
    "name": "devbox",
    "host": "192.0.2.10",
    "user": "alice",
    "roots": ["/home/alice/projects"],
}


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    ssh = d / "ssh"
    ssh.mkdir(parents=True)
    (ssh / "id_ed25519").write_text("FAKE PRIVATE KEY\n", encoding="utf-8")
    (ssh / "id_ed25519.pub").write_text(FAKE_PUB_KEY + "\n", encoding="utf-8")
    return d


@pytest.fixture
def tmp_config(tmp_path):
    cfg = {
        "targets": [
            {"name": "local-repo", "machine": "local", "repo": "/srv/repos/diff-lab"},
        ],
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return str(p)


@pytest.fixture
def enroll_app(tmp_config, data_dir, monkeypatch):
    monkeypatch.setenv("DIFFLAB_DATA", str(data_dir))
    monkeypatch.delenv("DIFFLAB_ENROLL_TOKEN", raising=False)
    application = create_app(config_path=tmp_config)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def enroll_client(enroll_app):
    return enroll_app.test_client()


# ──────────────────────────────────────────────
# /pubkey
# ──────────────────────────────────────────────

def test_pubkey_returns_restricted_line(enroll_client):
    resp = enroll_client.get("/pubkey")
    assert resp.status_code == 200
    assert resp.content_type.startswith("text/plain")
    line = resp.data.decode().strip()
    assert line.startswith(
        "no-pty,no-agent-forwarding,no-port-forwarding,no-X11-forwarding "
    )
    assert "ssh-ed25519" in line
    assert line.endswith("difflab")


def test_pubkey_contains_key_blob(enroll_client):
    resp = enroll_client.get("/pubkey")
    assert "AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyBlobForTesting" in resp.data.decode()


# ──────────────────────────────────────────────
# /register token checks
# ──────────────────────────────────────────────

def test_register_no_env_token_returns_503(enroll_client, monkeypatch):
    monkeypatch.delenv("DIFFLAB_ENROLL_TOKEN", raising=False)
    resp = enroll_client.post("/register", json={"token": "anything"})
    assert resp.status_code == 503
    assert "disabled" in resp.get_json()["error"]


def test_register_wrong_token_returns_401(enroll_client, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)
    body = {**VALID_BODY, "token": "wrongtoken"}
    resp = enroll_client.post("/register", json=body)
    assert resp.status_code == 401
    assert "unauthorized" in resp.get_json()["error"]


def test_register_missing_token_returns_401(enroll_client, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)
    body = {k: v for k, v in VALID_BODY.items() if k != "token"}
    resp = enroll_client.post("/register", json=body)
    assert resp.status_code == 401


# ──────────────────────────────────────────────
# /register validation
# ──────────────────────────────────────────────

def test_register_bad_name_returns_422(enroll_client, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)
    resp = enroll_client.post("/register", json={**VALID_BODY, "name": "bad name"})
    assert resp.status_code == 422
    assert "name" in resp.get_json()["error"]


def test_register_dash_host_returns_422(enroll_client, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)
    resp = enroll_client.post("/register", json={**VALID_BODY, "host": "-evil"})
    assert resp.status_code == 422
    assert "host" in resp.get_json()["error"]


def test_register_dash_user_returns_422(enroll_client, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)
    resp = enroll_client.post("/register", json={**VALID_BODY, "user": "-evil"})
    assert resp.status_code == 422
    assert "user" in resp.get_json()["error"]


def test_register_invalid_name_empty_returns_422(enroll_client, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)
    resp = enroll_client.post("/register", json={**VALID_BODY, "name": ""})
    assert resp.status_code == 422


# ──────────────────────────────────────────────
# /register happy path
# ──────────────────────────────────────────────

def _fake_run_ssh_find(argv):
    last = argv[-1]
    if "find" in last or "Get-ChildItem" in last:
        return "/home/alice/projects/myrepo/.git\n/home/alice/projects/other/.git\n", "", 0
    return "", "", 0


def test_register_happy_path(enroll_app, data_dir, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)
    monkeypatch.setattr(enroll_mod, "_run_ssh", _fake_run_ssh_find)

    with enroll_app.test_client() as c:
        resp = c.post("/register", json=VALID_BODY)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["machine"] == "devbox"
    assert "devbox-myrepo" in data["targets"]
    assert "devbox-other" in data["targets"]
    assert data["errors"] == []

    # Registry file was written
    reg_path = data_dir / "registry.yaml"
    assert reg_path.exists()
    reg = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
    names = [t["name"] for t in reg["targets"]]
    assert "devbox-myrepo" in names
    assert "devbox-other" in names


def test_register_persists_ssh_host_and_port(enroll_app, data_dir, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)
    monkeypatch.setattr(enroll_mod, "_run_ssh", _fake_run_ssh_find)
    body = {**VALID_BODY, "port": 2222}

    with enroll_app.test_client() as c:
        resp = c.post("/register", json=body)

    assert resp.status_code == 200
    reg = yaml.safe_load((data_dir / "registry.yaml").read_text(encoding="utf-8"))
    entry = next(t for t in reg["targets"] if t["name"] == "devbox-myrepo")
    assert entry["ssh_host"] == "alice@192.0.2.10"
    assert entry["port"] == 2222


def test_register_targets_appear_in_app_after_register(enroll_app, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)
    monkeypatch.setattr(enroll_mod, "_run_ssh", _fake_run_ssh_find)

    with enroll_app.test_client() as c:
        c.post("/register", json=VALID_BODY)

    with enroll_app.app_context():
        targets = enroll_app.config["DIFFLAB_TARGETS"]
    assert "devbox-myrepo" in targets
    assert "devbox-other" in targets


# ──────────────────────────────────────────────
# /register explicit repos list
# ──────────────────────────────────────────────

def test_register_explicit_repos(enroll_app, data_dir, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)

    def fake_git_verify(argv):
        return "true\n", "", 0

    monkeypatch.setattr(enroll_mod, "_run_ssh", fake_git_verify)

    body = {**VALID_BODY, "repos": ["/home/alice/myrepo"], "roots": []}
    with enroll_app.test_client() as c:
        resp = c.post("/register", json=body)

    assert resp.status_code == 200
    assert "devbox-myrepo" in resp.get_json()["targets"]


def test_register_explicit_repo_not_git(enroll_app, data_dir, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)

    def fake_not_git(argv):
        return "", "not a git repo", 128

    monkeypatch.setattr(enroll_mod, "_run_ssh", fake_not_git)

    body = {**VALID_BODY, "repos": ["/home/alice/notarepo"], "roots": []}
    with enroll_app.test_client() as c:
        resp = c.post("/register", json=body)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["targets"] == []
    assert any("not a git repository" in e for e in data["errors"])


# ──────────────────────────────────────────────
# /register SSH failure
# ──────────────────────────────────────────────

def test_register_ssh_failure_returns_502(enroll_client, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)

    def fake_ssh_fail(argv):
        return "", "Permission denied (publickey).", 255

    monkeypatch.setattr(enroll_mod, "_run_ssh", fake_ssh_fail)
    resp = enroll_client.post("/register", json=VALID_BODY)

    assert resp.status_code == 502
    error = resp.get_json()["error"]
    assert "authorized_keys" in error


# ──────────────────────────────────────────────
# Registry + config merge
# ──────────────────────────────────────────────

def test_registry_targets_loaded_at_startup(tmp_path, monkeypatch):
    cfg = {
        "targets": [{"name": "local-repo", "machine": "local", "repo": "/repos/local"}],
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")

    data = tmp_path / "data"
    ssh = data / "ssh"
    ssh.mkdir(parents=True)
    (ssh / "id_ed25519").write_text("FAKE KEY\n")
    (ssh / "id_ed25519.pub").write_text(FAKE_PUB_KEY + "\n")

    reg_path = data / "registry.yaml"
    reg_path.write_text(
        yaml.dump({
            "targets": [{
                "name": "reg-repo",
                "machine": "devbox",
                "repo": "/home/alice/myrepo",
                "ssh_host": "alice@192.0.2.10",
                "port": 22,
            }]
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv("DIFFLAB_DATA", str(data))
    app = create_app(config_path=str(cfg_path))

    with app.app_context():
        targets = app.config["DIFFLAB_TARGETS"]
    assert "local-repo" in targets
    assert "reg-repo" in targets


def test_registry_duplicate_name_does_not_override_config(tmp_path, monkeypatch):
    cfg = {
        "targets": [{"name": "myrepo", "machine": "local", "repo": "/original"}],
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")

    data = tmp_path / "data"
    ssh = data / "ssh"
    ssh.mkdir(parents=True)
    (ssh / "id_ed25519").write_text("FAKE KEY\n")
    (ssh / "id_ed25519.pub").write_text(FAKE_PUB_KEY + "\n")

    reg_path = data / "registry.yaml"
    reg_path.write_text(
        yaml.dump({
            "targets": [{
                "name": "myrepo",  # same name as config target
                "machine": "devbox",
                "repo": "/home/alice/different",
                "ssh_host": "alice@192.0.2.10",
                "port": 22,
            }]
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv("DIFFLAB_DATA", str(data))
    app = create_app(config_path=str(cfg_path))

    with app.app_context():
        targets = app.config["DIFFLAB_TARGETS"]
    # Config target wins; registry entry is silently dropped
    assert targets["myrepo"].repo == "/original"
    assert targets["myrepo"].ssh_host is None


def test_register_duplicate_name_gets_suffix(enroll_app, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)

    def fake_find(argv):
        return "/home/alice/local-repo/.git\n", "", 0

    monkeypatch.setattr(enroll_mod, "_run_ssh", fake_find)
    # "local-repo" is already in config
    with enroll_app.test_client() as c:
        resp = c.post("/register", json=VALID_BODY)

    data = resp.get_json()
    # Should be suffixed, not "local-repo"
    assert "devbox-local-repo" in data["targets"]


# ──────────────────────────────────────────────
# PowerShell fallback
# ──────────────────────────────────────────────

def test_register_falls_back_to_powershell(enroll_app, data_dir, monkeypatch):
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)

    call_count = {"n": 0}

    def fake_run(argv):
        call_count["n"] += 1
        last = argv[-1]
        if "find" in last:
            return "", "find: not found", 127
        if "Get-ChildItem" in last:
            return "C:\\Users\\alice\\myrepo\n", "", 0
        return "", "", 0

    monkeypatch.setattr(enroll_mod, "_run_ssh", fake_run)

    with enroll_app.test_client() as c:
        resp = c.post("/register", json=VALID_BODY)

    assert resp.status_code == 200
    assert "devbox-myrepo" in resp.get_json()["targets"]
    # find + powershell = at least 2 calls for that root
    assert call_count["n"] >= 2


# ──────────────────────────────────────────────
# Issue 2: dot-repo filtering
# ──────────────────────────────────────────────

def test_hidden_repos_excluded_from_posix_scan(enroll_app, data_dir, monkeypatch):
    """POSIX scan must skip repos whose path contains a dot-component."""
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)

    def fake_find(argv):
        # Returns a visible repo and two hidden repos
        return (
            "/home/alice/projects/myrepo/.git\n"
            "/root/.claude/.git\n"
            "/home/alice/.venv/.git\n"
        ), "", 0

    monkeypatch.setattr(enroll_mod, "_run_ssh", fake_find)

    with enroll_app.test_client() as c:
        resp = c.post("/register", json=VALID_BODY)

    data = resp.get_json()
    assert "devbox-myrepo" in data["targets"]
    # .claude and .venv paths must be silently excluded
    assert not any(".claude" in t for t in data["targets"])
    assert not any(".venv" in t for t in data["targets"])
    assert len(data["targets"]) == 1


def test_hidden_repos_excluded_from_powershell_scan(enroll_app, data_dir, monkeypatch):
    """PowerShell scan must also skip dot-component paths."""
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)

    def fake_run(argv):
        last = argv[-1]
        if "find" in last:
            return "", "find: not found", 127
        if "Get-ChildItem" in last:
            return (
                "C:\\Users\\alice\\myrepo\n"
                "C:\\Users\\alice\\.hidden\\repo\n"
            ), "", 0
        return "", "", 0

    monkeypatch.setattr(enroll_mod, "_run_ssh", fake_run)

    with enroll_app.test_client() as c:
        resp = c.post("/register", json=VALID_BODY)

    data = resp.get_json()
    assert "devbox-myrepo" in data["targets"]
    assert len(data["targets"]) == 1


def test_explicit_repos_bypass_dot_filter(enroll_app, data_dir, monkeypatch):
    """Explicit repos= in the payload must not be filtered even if dot-component."""
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)

    def fake_git_verify(argv):
        return "true\n", "", 0

    monkeypatch.setattr(enroll_mod, "_run_ssh", fake_git_verify)

    body = {**VALID_BODY, "repos": ["/home/alice/.myapp"], "roots": []}
    with enroll_app.test_client() as c:
        resp = c.post("/register", json=body)

    data = resp.get_json()
    assert "devbox-.myapp" in data["targets"] or any(".myapp" in t for t in data["targets"])


# ──────────────────────────────────────────────
# Issue 1: Windows shell — path with quotes rejected
# ──────────────────────────────────────────────

def test_register_explicit_repo_with_quote_skipped(enroll_app, data_dir, monkeypatch):
    """Repo paths containing a double-quote must be skipped with an error."""
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)
    monkeypatch.setattr(enroll_mod, "_run_ssh", lambda argv: ("true\n", "", 0))

    body = {**VALID_BODY, "repos": ['C:/bad"path'], "roots": []}
    with enroll_app.test_client() as c:
        resp = c.post("/register", json=body)

    data = resp.get_json()
    assert data["targets"] == []
    assert any('"' in e for e in data["errors"])


def test_register_windows_shell_persisted(enroll_app, data_dir, monkeypatch):
    """When PowerShell fallback fires, the registry entry must have shell=windows."""
    import yaml
    monkeypatch.setenv("DIFFLAB_ENROLL_TOKEN", TOKEN)

    def fake_run(argv):
        last = argv[-1]
        if "find" in last:
            return "", "not found", 127
        if "Get-ChildItem" in last:
            return "C:\\Users\\alice\\myrepo\n", "", 0
        return "", "", 0

    monkeypatch.setattr(enroll_mod, "_run_ssh", fake_run)

    with enroll_app.test_client() as c:
        resp = c.post("/register", json=VALID_BODY)

    assert resp.status_code == 200
    reg = yaml.safe_load((data_dir / "registry.yaml").read_text(encoding="utf-8"))
    entry = next(t for t in reg["targets"] if "myrepo" in t["name"])
    assert entry["shell"] == "windows"
    # Path should be normalized to forward slashes
    assert "\\" not in entry["repo"]
