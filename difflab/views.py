from __future__ import annotations

import hmac
import os
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)

from difflab.config import Target
from difflab.enroll import EnrollError, SSHError, discover_repos, validate_body
from difflab.gitops import GitError, get_diff, get_numstat, get_status
from difflab.registry import load_registry, save_registry, upsert_target
from difflab.render import parse_numstat, parse_status_rows, render_diff_html
from difflab.sshkey import get_public_key_line
import difflab.status_service as status_service
from difflab.status_service import group_by_machine

bp = Blueprint("difflab", __name__)


@bp.get("/favicon.ico")
def favicon():
    return current_app.send_static_file("kingfisher_favicon.svg")


@bp.after_app_request
def set_cache_headers(response):
    if response.content_type.startswith("application/json"):
        response.headers["Cache-Control"] = "no-store"
    else:
        response.headers["Cache-Control"] = "no-cache"
    return response


def _get_target(name: str):
    targets = current_app.config["DIFFLAB_TARGETS"]
    if name not in targets:
        abort(404)
    return targets[name]


def _get_target_by_machine_name(machine: str, name: str):
    targets = current_app.config["DIFFLAB_TARGETS"]
    for t in targets.values():
        if t.machine == machine and t.display_name == name:
            return t
    abort(404)


@bp.get("/")
def index():
    targets = current_app.config["DIFFLAB_TARGETS"]
    machines = group_by_machine(targets)
    total_count = len(targets)
    return render_template("index.html", machines=machines, total_count=total_count)


@bp.get("/api/status/<machine>")
def machine_status(machine: str):
    targets_dict = current_app.config["DIFFLAB_TARGETS"]
    key_path = current_app.config.get("DIFFLAB_KEY_PATH")
    machines = group_by_machine(targets_dict)
    if machine not in machines:
        abort(404)
    machine_targets = machines[machine]
    results = status_service.get_machine_status(
        machine,
        machine_targets,
        key_path,
        ttl=current_app.config["DIFFLAB_CACHE_TTL"],
        host_concurrency=current_app.config["DIFFLAB_HOST_CONCURRENCY"],
    )
    return {
        "machine": machine,
        "results": [
            {
                "name": r.target.name,
                "display_name": r.target.display_name,
                "repo": r.target.repo,
                "state": r.state,
                "file_count": (
                    len([ln for ln in (r.status or "").splitlines() if ln])
                    if r.error_kind is None else None
                ),
                "error": (
                    {
                        "kind": r.error_kind,
                        "message": r.error_message,
                        "detail": r.error_detail,
                    }
                    if r.error_kind else None
                ),
            }
            for r in results
        ],
    }


@bp.get("/api/targets")
def api_targets():
    targets = current_app.config["DIFFLAB_TARGETS"]

    if request.args.get("static") == "1":
        return [
            {
                "machine": t.machine,
                "name": t.display_name,
                "display_name": t.display_name,
                "repo": t.repo,
                "url": f"/d/{t.machine}/{t.display_name}",
            }
            for t in targets.values()
        ]

    key_path = current_app.config.get("DIFFLAB_KEY_PATH")
    ttl = current_app.config["DIFFLAB_CACHE_TTL"]
    host_concurrency = current_app.config["DIFFLAB_HOST_CONCURRENCY"]
    machines = group_by_machine(targets)

    status_by_name: dict = {}
    for machine, machine_targets in machines.items():
        results = status_service.get_machine_status(
            machine, machine_targets, key_path,
            ttl=ttl, host_concurrency=host_concurrency,
        )
        for r in results:
            status_by_name[r.target.name] = r

    out = []
    for t in targets.values():
        r = status_by_name.get(t.name)
        if r is None:
            state, file_count, error = "error", None, "Status unavailable"
        else:
            state = r.state
            file_count = (
                len([ln for ln in (r.status or "").splitlines() if ln])
                if r.error_kind is None else None
            )
            error = r.error_message if r.error_kind else None
        out.append({
            "machine": t.machine,
            "name": t.display_name,
            "display_name": t.display_name,
            "repo": t.repo,
            "url": f"/d/{t.machine}/{t.display_name}",
            "state": state,
            "file_count": file_count,
            "error": error,
        })
    return out


@bp.get("/d/<machine>/<name>")
def diff_view(machine: str, name: str):
    target = _get_target_by_machine_name(machine, name)
    key_path = current_app.config.get("DIFFLAB_KEY_PATH")
    try:
        status_text = get_status(target, key_path=key_path)
        diff_text = get_diff(target, key_path=key_path)
        numstat_text = get_numstat(target, key_path=key_path)
    except GitError as exc:
        current_app.logger.warning("GitError for %r/%r: %s", machine, name, exc.stderr)
        return render_template(
            "error.html",
            title="Git error",
            message=exc.message,
            detail=exc.stderr,
        ), 502

    counts_by_path = parse_numstat(numstat_text)
    status_rows = parse_status_rows(status_text, counts_by_path)
    rendered_diff = render_diff_html(diff_text, counts_by_path=counts_by_path)
    clean = not diff_text.strip()
    untracked_count = sum(1 for r in status_rows if r.get("untracked"))
    total_added = sum(int(c.get("added", 0)) for c in counts_by_path.values() if not c.get("binary"))
    total_deleted = sum(int(c.get("deleted", 0)) for c in counts_by_path.values() if not c.get("binary"))

    return render_template(
        "diff.html",
        target=target,
        status_text=status_text,
        status_rows=status_rows,
        rendered_diff=rendered_diff,
        clean=clean,
        untracked_count=untracked_count,
        total_added=total_added,
        total_deleted=total_deleted,
    )


@bp.get("/d/<name>")
def diff_view_legacy(name: str):
    target = _get_target(name)
    return redirect(
        url_for("difflab.diff_view", machine=target.machine, name=target.display_name),
        301,
    )


@bp.get("/raw/<machine>/<name>")
def raw_diff(machine: str, name: str):
    target = _get_target_by_machine_name(machine, name)
    key_path = current_app.config.get("DIFFLAB_KEY_PATH")
    try:
        diff_text = get_diff(target, key_path=key_path)
    except GitError as exc:
        current_app.logger.warning("GitError for %r/%r: %s", machine, name, exc.stderr)
        return render_template(
            "error.html",
            title="Git error",
            message=exc.message,
            detail=exc.stderr,
        ), 502

    return Response(diff_text, mimetype="text/plain; charset=utf-8")


@bp.get("/raw/<name>")
def raw_diff_legacy(name: str):
    target = _get_target(name)
    return redirect(
        url_for("difflab.raw_diff", machine=target.machine, name=target.display_name),
        301,
    )


@bp.get("/pubkey")
def pubkey():
    key_path = current_app.config.get("DIFFLAB_KEY_PATH")
    if key_path is None:
        return Response(
            "SSH keypair not initialized.\n",
            status=503,
            mimetype="text/plain; charset=utf-8",
        )
    target_os = request.args.get("os", "posix").lower()
    if target_os not in ("posix", "windows"):
        target_os = "posix"
    pub_line = get_public_key_line(key_path, target_os=target_os)
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

    reg_data = load_registry(data_dir)
    taken: set[str] = set(existing) | {t["name"] for t in reg_data.get("targets", [])}

    new_targets = []
    for repo in found_repos:
        entry = {
            "machine": name,
            "repo": repo,
            "ssh_host": ssh_host,
            "port": port,
            "shell": detected_shell,
        }
        final_name, _replaced = upsert_target(reg_data, entry, taken)
        taken.add(final_name)
        new_targets.append({**entry, "name": final_name})

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
    status_service.invalidate(name)

    return {
        "machine": name,
        "targets": [t["name"] for t in new_targets],
        "errors": disc_errors,
    }
