from __future__ import annotations

import hmac
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    render_template,
    request,
)

from difflab.config import Target
from difflab.enroll import EnrollError, SSHError, discover_repos, make_target_name, validate_body
from difflab.gitops import GitError, get_diff, get_numstat, get_status
from difflab.registry import load_registry, save_registry
from difflab.render import parse_numstat, parse_status_rows, render_diff_html

bp = Blueprint("difflab", __name__)


@bp.after_app_request
def add_no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _get_target(name: str):
    targets = current_app.config["DIFFLAB_TARGETS"]
    if name not in targets:
        abort(404)
    return targets[name]


@bp.get("/")
def index():
    targets = current_app.config["DIFFLAB_TARGETS"]
    dirty = []
    errored = []
    clean_count = 0

    key_path = current_app.config.get("DIFFLAB_KEY_PATH")

    def _check(t):
        try:
            status_text = get_status(t, key_path=key_path)
            return t, status_text.strip(), None
        except GitError as exc:
            return t, None, exc

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(_check, t): t for t in targets.values()}
        for fut in as_completed(futures):
            t, status_stripped, err = fut.result()
            if err is not None:
                errored.append({"target": t, "message": err.message})
            elif status_stripped:
                dirty.append(t)
            else:
                clean_count += 1

    return render_template(
        "index.html",
        dirty=dirty,
        errored=errored,
        clean_count=clean_count,
    )


@bp.get("/d/<name>")
def diff_view(name: str):
    target = _get_target(name)
    key_path = current_app.config.get("DIFFLAB_KEY_PATH")
    try:
        status_text = get_status(target, key_path=key_path)
        diff_text = get_diff(target, key_path=key_path)
        numstat_text = get_numstat(target, key_path=key_path)
    except GitError as exc:
        current_app.logger.warning("GitError for %r: %s", name, exc.stderr)
        return render_template(
            "error.html",
            title="Git error",
            message=exc.message,
        ), 502

    counts_by_path = parse_numstat(numstat_text)
    status_rows = parse_status_rows(status_text, counts_by_path)
    rendered_diff = render_diff_html(diff_text, counts_by_path=counts_by_path)
    clean = not diff_text.strip()

    return render_template(
        "diff.html",
        target=target,
        status_text=status_text,
        status_rows=status_rows,
        rendered_diff=rendered_diff,
        clean=clean,
    )


@bp.get("/raw/<name>")
def raw_diff(name: str):
    target = _get_target(name)
    key_path = current_app.config.get("DIFFLAB_KEY_PATH")
    try:
        diff_text = get_diff(target, key_path=key_path)
    except GitError as exc:
        current_app.logger.warning("GitError for %r: %s", name, exc.stderr)
        return render_template(
            "error.html",
            title="Git error",
            message=exc.message,
        ), 502

    return Response(diff_text, mimetype="text/plain; charset=utf-8")


@bp.get("/pubkey")
def pubkey():
    pub_line = current_app.config.get("DIFFLAB_PUBKEY_LINE")
    if not pub_line:
        return Response(
            "SSH keypair not initialized.\n",
            status=503,
            mimetype="text/plain; charset=utf-8",
        )
    return Response(pub_line + "\n", mimetype="text/plain; charset=utf-8")


@bp.post("/register")
def register():
    token = os.environ.get("DIFFLAB_ENROLL_TOKEN")
    if not token:
        return {"error": "enrollment disabled"}, 503

    body = request.get_json(force=True, silent=True) or {}
    submitted = str(body.get("token") or "")
    if not hmac.compare_digest(submitted, token):
        return {"error": "unauthorized"}, 401

    try:
        name, host, user, port, roots, repos = validate_body(body)
    except EnrollError as exc:
        return {"error": exc.message}, exc.status

    key_path: Path | None = current_app.config.get("DIFFLAB_KEY_PATH")
    data_dir: Path = current_app.config["DIFFLAB_DATA_DIR"]

    if key_path is None:
        return {"error": "SSH keypair not initialized; enrollment unavailable."}, 503

    existing = current_app.config["DIFFLAB_TARGETS"]
    taken: set[str] = set(existing.keys())

    try:
        found_repos, disc_errors, detected_shell = discover_repos(
            host, user, port, key_path, roots, repos
        )
    except SSHError as exc:
        return {
            "error": (
                f"SSH connection failed: {exc}. "
                "Add the container's public key (GET /pubkey) to the host's authorized_keys."
            )
        }, 502

    ssh_host = f"{user}@{host}"
    new_targets = []
    for repo in found_repos:
        tname = make_target_name(name, repo, taken)
        taken.add(tname)
        new_targets.append({
            "name": tname,
            "machine": name,
            "repo": repo,
            "ssh_host": ssh_host,
            "port": port,
            "shell": detected_shell,
        })

    reg_data = load_registry(data_dir)
    existing_reg_names = {t["name"] for t in reg_data.get("targets", [])}
    for t in new_targets:
        if t["name"] not in existing_reg_names:
            reg_data.setdefault("targets", []).append(t)
    save_registry(data_dir, reg_data)

    for t in new_targets:
        existing[t["name"]] = Target(
            name=t["name"],
            machine=t["machine"],
            repo=t["repo"],
            ssh_host=t["ssh_host"],
            port=t["port"],
            shell=t.get("shell", "posix"),
        )
    current_app.config["DIFFLAB_TARGETS"] = existing

    return {
        "machine": name,
        "targets": [t["name"] for t in new_targets],
        "errors": disc_errors,
    }
