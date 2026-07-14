from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask, render_template

from difflab.config import load_config
from difflab.registry import dedupe_registry, load_registry, merge_targets, registry_to_targets, save_registry
from difflab.sshkey import ensure_keypair, get_public_key_line
from difflab.views import bp

_log = logging.getLogger(__name__)


def create_app(config_path: str | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates")

    targets = load_config(config_path)

    data_dir = Path(os.environ.get("DIFFLAB_DATA", "./data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    try:
        key_path = ensure_keypair(data_dir)
        pub_line = get_public_key_line(key_path)
    except Exception:
        _log.warning("SSH keypair unavailable; /pubkey and /register will return 503.")
        key_path = None
        pub_line = None

    reg_data = load_registry(data_dir)
    if dedupe_registry(reg_data):
        save_registry(data_dir, reg_data)
    reg_targets = registry_to_targets(reg_data)
    merged = merge_targets(targets, reg_targets)

    app.config["DIFFLAB_TARGETS"] = merged
    app.config["DIFFLAB_CONFIG_TARGETS"] = targets
    app.config["DIFFLAB_KEY_PATH"] = key_path
    app.config["DIFFLAB_PUBKEY_LINE"] = pub_line
    app.config["DIFFLAB_DATA_DIR"] = data_dir
    app.config["DIFFLAB_CACHE_TTL"] = float(os.environ.get("DIFFLAB_CACHE_TTL", "5"))
    app.config["DIFFLAB_HOST_CONCURRENCY"] = int(os.environ.get("DIFFLAB_HOST_CONCURRENCY", "3"))

    app.register_blueprint(bp)

    @app.errorhandler(404)
    def not_found(exc):
        return render_template("error.html", title="Not found", message="Page not found."), 404

    return app
