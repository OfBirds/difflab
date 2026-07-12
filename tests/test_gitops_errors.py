from __future__ import annotations

import subprocess
import time

import pytest

from difflab.config import Target
from difflab.gitops import (
    BATCH_LIMIT,
    BATCH_REC,
    BATCH_SEP,
    ErrorKind,
    GitError,
    build_batch_status_command,
    classify_error,
    parse_batch_status,
)
import difflab.gitops as gitops_mod


# ── classify_error — one test per taxonomy row ───────────────────────────────

def test_classify_busy_kex():
    # The headline bug: kex + rc=255 must be BUSY, not UNREACHABLE
    kind, msg = classify_error(255, "kex_exchange_identification: read: Connection reset by peer")
    assert kind is ErrorKind.BUSY
    assert "busy" in msg.lower() or "interrupted" in msg.lower()


def test_classify_busy_connection_reset():
    kind, _ = classify_error(255, "Connection reset by peer")
    assert kind is ErrorKind.BUSY


def test_classify_busy_broken_pipe():
    kind, _ = classify_error(255, "Broken pipe")
    assert kind is ErrorKind.BUSY


def test_classify_unreachable_connect_to_host():
    kind, msg = classify_error(255, "ssh: connect to host bad.host port 22: Connection refused")
    assert kind is ErrorKind.UNREACHABLE
    assert "unreachable" in msg.lower()


def test_classify_unreachable_no_route():
    kind, _ = classify_error(1, "No route to host")
    assert kind is ErrorKind.UNREACHABLE


def test_classify_unreachable_dns():
    kind, _ = classify_error(255, "Could not resolve hostname bad.host")
    assert kind is ErrorKind.UNREACHABLE


def test_classify_auth_publickey():
    kind, msg = classify_error(255, "Permission denied (publickey,password).")
    assert kind is ErrorKind.AUTH
    assert "denied" in msg.lower() or "access" in msg.lower()


def test_classify_auth_host_key():
    kind, _ = classify_error(255, "Host key verification failed.")
    assert kind is ErrorKind.AUTH


def test_classify_gate_rejected_not_permitted():
    kind, _ = classify_error(1, "difflab: command not permitted")
    assert kind is ErrorKind.GATE_REJECTED


def test_classify_gate_rejected_rc127():
    kind, _ = classify_error(127, "bash: difflab-batch-status: command not found")
    assert kind is ErrorKind.GATE_REJECTED


def test_classify_repo_missing_not_git():
    kind, msg = classify_error(128, "fatal: not a git repository (or any of the parent directories)")
    assert kind is ErrorKind.REPO_MISSING
    assert "not found" in msg.lower() or "missing" in msg.lower() or "path" in msg.lower()


def test_classify_repo_denied_permission():
    kind, msg = classify_error(128, "fatal: detected dubious ownership in repository at '/x'\n\tsafe.directory /x")
    assert kind is ErrorKind.REPO_DENIED


def test_classify_repo_denied_generic():
    kind, _ = classify_error(128, "fatal: Permission denied")
    assert kind is ErrorKind.REPO_DENIED


def test_classify_unknown_rc255_no_pattern():
    # rc=255 with unmatched stderr → UNKNOWN "SSH error" (not "Machine unreachable")
    kind, msg = classify_error(255, "some random unrecognized error text")
    assert kind is ErrorKind.UNKNOWN
    assert msg == "SSH error"


def test_classify_unknown_other():
    kind, msg = classify_error(128, "some unknown git error xyzzy")
    assert kind is ErrorKind.UNKNOWN
    assert msg == "Can't read this repo"


def test_classify_rc255_disambiguates_repo_missing():
    # "not a git repository" with rc=255 should NOT be REPO_MISSING (could be SSH banner)
    kind, _ = classify_error(255, "not a git repository")
    assert kind is ErrorKind.UNKNOWN


def test_classify_permission_denied_rc255_not_repo_denied():
    # "permission denied" with rc=255 and without (publickey) → UNKNOWN (SSH-layer)
    kind, _ = classify_error(255, "Permission denied")
    assert kind is ErrorKind.UNKNOWN


def test_classify_auth_wins_over_repo_denied():
    # "Permission denied (publickey" should be AUTH, not REPO_DENIED
    kind, _ = classify_error(255, "Permission denied (publickey,keyboard-interactive).")
    assert kind is ErrorKind.AUTH


# ── _execute retry behaviour ─────────────────────────────────────────────────

def _make_result(returncode, stderr="", stdout=""):
    r = subprocess.CompletedProcess(args=[], returncode=returncode)
    r.stdout = stdout
    r.stderr = stderr
    return r


def test_execute_retries_once_on_busy(monkeypatch):
    calls = []
    results = [
        _make_result(255, "kex_exchange_identification: read: Connection reset by peer"),
        _make_result(0, stdout=" M file.py"),
    ]

    def fake_run(*a, **kw):
        calls.append(1)
        return results.pop(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    out = gitops_mod._execute(["ssh", "host", "cmd"])
    assert out == " M file.py"
    assert len(calls) == 2


def test_execute_does_not_retry_unreachable(monkeypatch):
    calls = []

    def fake_run(*a, **kw):
        calls.append(1)
        return _make_result(255, "ssh: connect to host bad port 22: Connection refused")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GitError) as exc_info:
        gitops_mod._execute(["ssh", "host", "cmd"])
    assert exc_info.value.kind is ErrorKind.UNREACHABLE
    assert len(calls) == 1


def test_execute_does_not_retry_twice_on_double_busy(monkeypatch):
    calls = []

    def fake_run(*a, **kw):
        calls.append(1)
        return _make_result(255, "kex_exchange_identification: read: Connection reset by peer")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    with pytest.raises(GitError) as exc_info:
        gitops_mod._execute(["ssh", "host", "cmd"])
    assert exc_info.value.kind is ErrorKind.BUSY
    assert len(calls) == 2  # one attempt + one retry, then raises


# ── build_batch_status_command ───────────────────────────────────────────────

def test_build_batch_command_basic():
    argv = build_batch_status_command("user@host", 22, ["/a", "/b c"], None)
    assert argv[0] == "ssh"
    assert argv[-1] == "difflab-batch-status\x1f/a\x1f/b c"
    assert ["-o", "BatchMode=yes"] == argv[1:3]


def test_build_batch_command_includes_key_opts(tmp_path):
    key = tmp_path / "ssh" / "id_ed25519"
    key.parent.mkdir()
    key.write_text("FAKE")
    argv = build_batch_status_command("user@host", 22, ["/a"], key)
    assert "-i" in argv
    assert str(key) in argv


def test_build_batch_command_port_flag():
    argv = build_batch_status_command("user@host", 2222, ["/a"], None)
    assert "-p" in argv
    assert "2222" in argv


def test_build_batch_command_no_port_flag_for_22():
    argv = build_batch_status_command("user@host", 22, ["/a"], None)
    assert "-p" not in argv


def test_build_batch_command_raises_on_sep_in_path():
    with pytest.raises(ValueError):
        build_batch_status_command("host", 22, ["/a\x1fb"], None)


def test_build_batch_command_raises_on_newline_in_path():
    with pytest.raises(ValueError):
        build_batch_status_command("host", 22, ["/a\nb"], None)


def test_build_batch_command_raises_on_leading_dash():
    with pytest.raises(ValueError):
        build_batch_status_command("host", 22, ["-evil"], None)


def test_build_batch_command_raises_on_too_many():
    with pytest.raises(ValueError, match="Too many"):
        build_batch_status_command("host", 22, [f"/p{i}" for i in range(BATCH_LIMIT + 1)], None)


# ── parse_batch_status ───────────────────────────────────────────────────────

def test_parse_batch_status_two_repos():
    stdout = "\x1eREPO /a\n M file.py\n\x1eRC 1\n\x1eREPO /b\n\x1eRC 0\n"
    result = parse_batch_status(stdout)
    assert result["/a"] == (1, " M file.py")
    assert result["/b"] == (0, "")


def test_parse_batch_status_empty_section():
    stdout = "\x1eREPO /a\n\x1eRC 0\n"
    result = parse_batch_status(stdout)
    assert result["/a"] == (0, "")


def test_parse_batch_status_missing_final_rc():
    stdout = "\x1eREPO /a\n M file\n\x1eRC 0\n\x1eREPO /b\n M other\n"
    result = parse_batch_status(stdout)
    assert "/a" in result
    assert "/b" not in result  # missing RC → dropped


def test_parse_batch_status_garbage_before_first_repo():
    stdout = "some banner text\nmore garbage\n\x1eREPO /a\n\x1eRC 0\n"
    result = parse_batch_status(stdout)
    assert "/a" in result
    assert result["/a"][0] == 0


def test_parse_batch_status_multiline_output():
    stdout = "\x1eREPO /a\n M file1.py\n M file2.py\n\x1eRC 1\n"
    result = parse_batch_status(stdout)
    assert " M file1.py" in result["/a"][1]
    assert " M file2.py" in result["/a"][1]
