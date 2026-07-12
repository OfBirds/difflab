from __future__ import annotations

import os
import re
from dataclasses import dataclass

import yaml

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Target:
    name: str
    machine: str
    repo: str
    ssh_host: str | None
    port: int = 22
    shell: str = "posix"

    @property
    def display_name(self) -> str:
        prefix = self.machine + "-"
        if self.name.startswith(prefix):
            return self.name[len(prefix):]
        return self.name


def load_config(path: str | None = None) -> dict[str, Target]:
    if path is None:
        path = os.environ.get("DIFFLAB_CONFIG", "config.yaml")

    try:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML parse error in {path}: {exc}")

    if not isinstance(raw, dict):
        raise ConfigError("Config file must be a YAML mapping at the top level.")

    machines_raw = raw.get("machines") or {}
    if not isinstance(machines_raw, dict):
        raise ConfigError("'machines' must be a YAML mapping (key: value pairs).")
    machines: dict[str, str] = machines_raw
    targets_raw = raw.get("targets")

    if not targets_raw:
        raise ConfigError("Config must contain a non-empty 'targets' list.")

    for host_name, dest in machines.items():
        if host_name == "local":
            raise ConfigError("'local' is a reserved machine name and cannot be used.")

        if not isinstance(dest, str):
            raise ConfigError(f"Machine '{host_name}' destination must be a string.")
        if dest.startswith("-"):
            raise ConfigError(
                f"Machine '{host_name}' destination must not start with '-'."
            )

    seen: dict[str, Target] = {}
    for item in targets_raw:
        if not isinstance(item, dict):
            raise ConfigError("Each target must be a YAML mapping.")

        name = item.get("name")
        repo = item.get("repo")

        if not isinstance(name, str) or not name:
            raise ConfigError("Every target must have a 'name' string.")
        if not isinstance(repo, str) or not repo:
            raise ConfigError(f"Target '{name}' must have a 'repo' string.")
        if not NAME_RE.match(name):
            raise ConfigError(
                f"Target name '{name}' is invalid. "
                "Must match ^[A-Za-z0-9][A-Za-z0-9._-]*$."
            )
        if repo.startswith("-"):
            raise ConfigError(f"Target '{name}' repo must not start with '-'.")
        if name in seen:
            raise ConfigError(f"Duplicate target name: '{name}'.")

        machine = item.get("machine", "local")
        if not isinstance(machine, str):
            raise ConfigError(f"Target '{name}' machine must be a string.")

        if machine == "local":
            ssh_host = None
        else:
            if machine not in machines:
                raise ConfigError(
                    f"Target '{name}' references unknown machine '{machine}'."
                )
            ssh_host = machines[machine]

        seen[name] = Target(name=name, machine=machine, repo=repo, ssh_host=ssh_host)

    return seen
