from __future__ import annotations

import pytest
import yaml

from difflab.config import Target
from difflab import create_app


LOCAL_TARGET = Target(
    name="diff-lab",
    machine="local",
    repo="/srv/repos/diff-lab",
    ssh_host=None,
)

REMOTE_TARGET = Target(
    name="remote-repo",
    machine="diffhost",
    repo="/srv/repos/remote-repo",
    ssh_host="user@diffhost.example.com",
)


@pytest.fixture
def targets_fixture():
    return {
        LOCAL_TARGET.name: LOCAL_TARGET,
        REMOTE_TARGET.name: REMOTE_TARGET,
    }


@pytest.fixture
def tmp_config(tmp_path):
    cfg = {
        "machines": {"diffhost": "user@diffhost.example.com"},
        "targets": [
            {"name": "diff-lab", "machine": "local", "repo": "/srv/repos/diff-lab"},
            {"name": "remote-repo", "machine": "diffhost", "repo": "/srv/repos/remote-repo"},
        ],
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return str(p)


@pytest.fixture
def app(tmp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("DIFFLAB_DATA", str(tmp_path / "data"))
    application = create_app(config_path=tmp_config)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()
