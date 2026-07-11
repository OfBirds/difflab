from __future__ import annotations

import subprocess
from pathlib import Path


def ensure_keypair(data_dir: Path) -> Path:
    key_path = data_dir / "ssh" / "id_ed25519"
    if not key_path.exists():
        key_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path)],
            check=True,
            capture_output=True,
        )
    return key_path


def get_public_key_line(key_path: Path) -> str:
    pub_path = key_path.with_suffix(".pub")
    parts = pub_path.read_text(encoding="utf-8").strip().split()
    keytype, blob = parts[0], parts[1]
    return (
        "no-pty,no-agent-forwarding,no-port-forwarding,no-X11-forwarding "
        f"{keytype} {blob} difflab"
    )
