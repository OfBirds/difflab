from __future__ import annotations

from pathlib import Path

import yaml

from difflab.config import Target


def load_registry(data_dir: Path) -> dict:
    reg_path = data_dir / "registry.yaml"
    if not reg_path.exists():
        return {"targets": []}
    with open(reg_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {"targets": []}


def save_registry(data_dir: Path, data: dict) -> None:
    reg_path = data_dir / "registry.yaml"
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(reg_path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, allow_unicode=True)


def normalize_repo_path(repo: str) -> str:
    path = repo.replace("\\", "/")
    # Lowercase drive letter (Windows paths)
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        path = path[0].lower() + path[1:]
    # Collapse double slashes
    while "//" in path:
        path = path.replace("//", "/")
    # Strip trailing slash, but keep bare "/"
    if path != "/":
        path = path.rstrip("/")
    return path


def find_by_repo(reg_data: dict, machine: str, repo: str) -> dict | None:
    normalized = normalize_repo_path(repo)
    for entry in reg_data.get("targets", []):
        if (entry.get("machine") == machine
                and normalize_repo_path(entry.get("repo", "")) == normalized):
            return entry
    return None


def upsert_target(reg_data: dict, entry: dict, taken: set[str]) -> tuple[str, bool]:
    machine = entry["machine"]
    repo = entry["repo"]
    match = find_by_repo(reg_data, machine, repo)
    if match:
        match["ssh_host"] = entry.get("ssh_host")
        match["port"] = entry.get("port", 22)
        match["shell"] = entry.get("shell", "posix")
        match["repo"] = entry.get("repo", match["repo"])
        return match["name"], True
    # New entry — generate a unique name
    from difflab.enroll import make_target_name
    name = make_target_name(machine, repo, taken)
    reg_data.setdefault("targets", []).append({**entry, "name": name})
    return name, False


def dedupe_registry(reg_data: dict) -> bool:
    targets = reg_data.get("targets", [])
    if not targets:
        return False

    from collections import defaultdict
    groups: dict[tuple, list] = defaultdict(list)
    for t in targets:
        key = (t.get("machine", ""), normalize_repo_path(t.get("repo", "")))
        groups[key].append(t)

    modified = False
    keep = []
    for group in groups.values():
        if len(group) == 1:
            keep.append(group[0])
        else:
            # Keep shortest name; ties broken by original list order
            winner = min(group, key=lambda t: (len(t.get("name", "")), targets.index(t)))
            keep.append(winner)
            modified = True

    if modified:
        reg_data["targets"] = keep
    return modified


def registry_to_targets(reg_data: dict) -> dict[str, Target]:
    result: dict[str, Target] = {}
    for t in reg_data.get("targets", []):
        name = t.get("name")
        if not name:
            continue
        result[name] = Target(
            name=name,
            machine=t.get("machine", ""),
            repo=t.get("repo", ""),
            ssh_host=t.get("ssh_host"),
            port=t.get("port", 22),
            shell=t.get("shell", "posix"),
        )
    return result


def merge_targets(
    config_targets: dict[str, Target],
    registry_targets: dict[str, Target],
) -> dict[str, Target]:
    merged = dict(config_targets)
    for name, target in registry_targets.items():
        if name not in merged:
            merged[name] = target
    return merged
