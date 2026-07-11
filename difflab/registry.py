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
