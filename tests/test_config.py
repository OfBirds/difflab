from __future__ import annotations

import pytest
import yaml

from difflab.config import ConfigError, load_config


def write_cfg(tmp_path, data):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return str(p)


def test_valid_config(tmp_path):
    cfg = {
        "machines": {"diffhost": "user@diffhost.example.com"},
        "targets": [
            {"name": "diff-lab", "machine": "local", "repo": "/srv/repos/diff-lab"},
            {"name": "remote-repo", "machine": "diffhost", "repo": "/srv/repos/remote-repo"},
        ],
    }
    targets = load_config(write_cfg(tmp_path, cfg))
    assert set(targets.keys()) == {"diff-lab", "remote-repo"}
    local = targets["diff-lab"]
    assert local.machine == "local"
    assert local.ssh_host is None
    remote = targets["remote-repo"]
    assert remote.ssh_host == "user@diffhost.example.com"


def test_missing_targets(tmp_path):
    cfg = {"machines": {}}
    with pytest.raises(ConfigError, match="targets"):
        load_config(write_cfg(tmp_path, cfg))


def test_bad_name_dots(tmp_path):
    cfg = {"targets": [{"name": "../evil", "repo": "/x"}]}
    with pytest.raises(ConfigError, match="invalid"):
        load_config(write_cfg(tmp_path, cfg))


def test_bad_name_space(tmp_path):
    cfg = {"targets": [{"name": "a b", "repo": "/x"}]}
    with pytest.raises(ConfigError, match="invalid"):
        load_config(write_cfg(tmp_path, cfg))


def test_unknown_machine(tmp_path):
    cfg = {"targets": [{"name": "foo", "machine": "ghost", "repo": "/x"}]}
    with pytest.raises(ConfigError, match="unknown machine"):
        load_config(write_cfg(tmp_path, cfg))


def test_dash_prefixed_host(tmp_path):
    cfg = {
        "machines": {"bad": "-evil-host"},
        "targets": [{"name": "foo", "machine": "bad", "repo": "/x"}],
    }
    with pytest.raises(ConfigError, match="must not start with"):
        load_config(write_cfg(tmp_path, cfg))


def test_dash_prefixed_repo(tmp_path):
    cfg = {"targets": [{"name": "foo", "repo": "-malicious"}]}
    with pytest.raises(ConfigError, match="must not start with"):
        load_config(write_cfg(tmp_path, cfg))


def test_duplicate_names(tmp_path):
    cfg = {
        "targets": [
            {"name": "foo", "repo": "/a"},
            {"name": "foo", "repo": "/b"},
        ]
    }
    with pytest.raises(ConfigError, match="Duplicate"):
        load_config(write_cfg(tmp_path, cfg))


def test_env_config_path(tmp_path, monkeypatch):
    cfg = {"targets": [{"name": "myrepo", "repo": "/repos/myrepo"}]}
    p = write_cfg(tmp_path, cfg)
    monkeypatch.setenv("DIFFLAB_CONFIG", p)
    targets = load_config()
    assert "myrepo" in targets


def test_toplevel_not_dict(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="top level"):
        load_config(str(p))


def test_machines_not_dict(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("machines:\n  - host1\n  - host2\ntargets:\n  - name: foo\n    repo: /x\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="machines"):
        load_config(str(p))


def test_local_reserved_machine_name(tmp_path):
    cfg = {
        "machines": {"local": "user@somehost.example.com"},
        "targets": [{"name": "foo", "repo": "/x"}],
    }
    with pytest.raises(ConfigError, match="reserved"):
        load_config(write_cfg(tmp_path, cfg))
