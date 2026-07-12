from __future__ import annotations

import yaml
import pytest

from difflab import create_app
from difflab.registry import (
    dedupe_registry,
    find_by_repo,
    normalize_repo_path,
    upsert_target,
)


FAKE_PUB_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyBlobForTesting difflab@container"


# ── 20. normalize_repo_path ───────────────────────────────────────────────────

def test_normalize_backslashes():
    assert normalize_repo_path("C:\\Users\\alice\\repo") == "c:/Users/alice/repo"


def test_normalize_trailing_slash():
    assert normalize_repo_path("/home/alice/repo/") == "/home/alice/repo"


def test_normalize_drive_letter_case():
    assert normalize_repo_path("C:/Users/alice") == "c:/Users/alice"
    assert normalize_repo_path("D:\\projects") == "d:/projects"


def test_normalize_double_slash():
    assert normalize_repo_path("/home//alice//repo") == "/home/alice/repo"


def test_normalize_bare_slash():
    assert normalize_repo_path("/") == "/"


def test_normalize_windows_path_matches_posix_normalized():
    a = normalize_repo_path("C:\\Users\\Alice\\App")
    b = normalize_repo_path("c:/Users/Alice/App/")
    assert a == b


def test_normalize_mixed_slashes():
    assert normalize_repo_path("C:/Users\\alice/repo") == "c:/Users/alice/repo"


# ── find_by_repo ──────────────────────────────────────────────────────────────

def test_find_by_repo_exact_match():
    reg = {"targets": [{"name": "dev-App", "machine": "dev", "repo": "/app"}]}
    result = find_by_repo(reg, "dev", "/app")
    assert result is not None
    assert result["name"] == "dev-App"


def test_find_by_repo_normalized_match():
    reg = {"targets": [{"name": "dev-App", "machine": "dev", "repo": "C:\\Users\\alice\\App"}]}
    result = find_by_repo(reg, "dev", "c:/Users/alice/App/")
    assert result is not None
    assert result["name"] == "dev-App"


def test_find_by_repo_no_match_different_machine():
    reg = {"targets": [{"name": "dev-App", "machine": "dev", "repo": "/app"}]}
    result = find_by_repo(reg, "prod", "/app")
    assert result is None


def test_find_by_repo_no_match_different_repo():
    reg = {"targets": [{"name": "dev-App", "machine": "dev", "repo": "/app"}]}
    result = find_by_repo(reg, "dev", "/other")
    assert result is None


# ── 21. upsert_target — same basename, different path → suffix ────────────────

def test_upsert_different_path_same_basename_gets_suffix():
    reg = {"targets": [{"name": "dev-myrepo", "machine": "dev",
                        "repo": "/home/alice/myrepo", "ssh_host": "alice@dev", "port": 22, "shell": "posix"}]}
    entry = {"machine": "dev", "repo": "/home/bob/myrepo",
             "ssh_host": "alice@dev", "port": 22, "shell": "posix"}
    taken = {"dev-myrepo"}
    name, replaced = upsert_target(reg, entry, taken)
    assert name != "dev-myrepo"
    assert "myrepo" in name
    assert not replaced


def test_upsert_same_machine_same_repo_updates_in_place():
    reg = {"targets": [{"name": "dev-App", "machine": "dev",
                        "repo": "/app", "ssh_host": "old@dev", "port": 22, "shell": "posix"}]}
    entry = {"machine": "dev", "repo": "/app",
             "ssh_host": "new@dev", "port": 2222, "shell": "posix"}
    name, replaced = upsert_target(reg, entry, set())
    assert name == "dev-App"
    assert replaced is True
    assert reg["targets"][0]["ssh_host"] == "new@dev"
    assert reg["targets"][0]["port"] == 2222


# ── 22. dedupe_registry ───────────────────────────────────────────────────────

def test_dedupe_keeps_shortest_name():
    reg = {
        "targets": [
            {"name": "dev-App", "machine": "dev", "repo": "/app"},
            {"name": "dev-App-2", "machine": "dev", "repo": "/app"},
        ]
    }
    modified = dedupe_registry(reg)
    assert modified is True
    names = [t["name"] for t in reg["targets"]]
    assert names == ["dev-App"]
    assert "dev-App-2" not in names


def test_dedupe_no_dupes_unchanged():
    reg = {
        "targets": [
            {"name": "dev-App", "machine": "dev", "repo": "/app"},
            {"name": "dev-Other", "machine": "dev", "repo": "/other"},
        ]
    }
    original_targets = list(reg["targets"])
    modified = dedupe_registry(reg)
    assert modified is False
    assert reg["targets"] == original_targets


def test_dedupe_windows_path_matches_backslash():
    reg = {
        "targets": [
            {"name": "dev-App", "machine": "dev", "repo": "c:/Users/alice/App"},
            {"name": "dev-App-2", "machine": "dev", "repo": "C:\\Users\\alice\\App"},
        ]
    }
    modified = dedupe_registry(reg)
    assert modified is True
    assert len(reg["targets"]) == 1
    assert reg["targets"][0]["name"] == "dev-App"


def test_dedupe_different_machines_not_deduped():
    reg = {
        "targets": [
            {"name": "dev-App", "machine": "dev", "repo": "/app"},
            {"name": "prod-App", "machine": "prod", "repo": "/app"},
        ]
    }
    modified = dedupe_registry(reg)
    assert modified is False
    assert len(reg["targets"]) == 2


def test_create_app_heals_registry_duplicates(tmp_path, monkeypatch):
    """create_app runs dedupe_registry automatically and rewrites the file."""
    cfg = {"targets": [{"name": "local-repo", "machine": "local", "repo": "/repos/local"}]}
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
            "targets": [
                {"name": "dev-App", "machine": "dev", "repo": "/app",
                 "ssh_host": "alice@dev", "port": 22, "shell": "posix"},
                {"name": "dev-App-2", "machine": "dev", "repo": "/app",
                 "ssh_host": "alice@dev", "port": 22, "shell": "posix"},
            ]
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv("DIFFLAB_DATA", str(data))
    app = create_app(config_path=str(cfg_path))

    with app.app_context():
        targets = app.config["DIFFLAB_TARGETS"]

    # Only dev-App should survive
    assert "dev-App" in targets
    assert "dev-App-2" not in targets

    # Registry file should be rewritten without the duplicate
    reg = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
    names = [t["name"] for t in reg["targets"]]
    assert "dev-App" in names
    assert "dev-App-2" not in names
