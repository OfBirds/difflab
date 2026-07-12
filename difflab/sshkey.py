from __future__ import annotations

import subprocess
from pathlib import Path

# Standard install paths for the gate scripts (see gate/ in the repo root).
# new enrollments should install the gate at these paths before authorizing the key.
_POSIX_GATE = "/usr/local/lib/difflab/git-gate.sh"
_WINDOWS_GATE_INVOKE = (
    "powershell -NoProfile -ExecutionPolicy Bypass"
    " -File C:/ProgramData/difflab/git-gate.ps1"
)
_RESTRICTIONS = "no-pty,no-agent-forwarding,no-port-forwarding,no-X11-forwarding"


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


def get_public_key_line(key_path: Path, target_os: str = "posix") -> str:
    """Return a complete authorized_keys line with forced-command gate prefix.

    target_os: "posix" (default) emits the sh gate path;
               "windows" emits the powershell invocation.
    """
    pub_path = key_path.with_suffix(".pub")
    parts = pub_path.read_text(encoding="utf-8").strip().split()
    keytype, blob = parts[0], parts[1]
    gate_cmd = _WINDOWS_GATE_INVOKE if target_os == "windows" else _POSIX_GATE
    return (
        f'command="{gate_cmd}",{_RESTRICTIONS} '
        f"{keytype} {blob} difflab"
    )
