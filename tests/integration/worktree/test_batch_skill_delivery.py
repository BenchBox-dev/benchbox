from __future__ import annotations

import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]


DELIVERY_CATALOG_REV = "5c7ff93e8103ee5a4ac59ea2330625e85250b206"
SOURCE_REV = "aaad6c97632f0a36341cb670c9e1a7fe0b3a860b"
TODO_DB_VERSION = "0.9.1"
SCRIPTS_PROJECT = ROOT / "_project/scripts"
WHEEL = SCRIPTS_PROJECT / f"vendor/todo_db-{TODO_DB_VERSION}-py3-none-any.whl"
WHEEL_SHA256 = "760bc03241d25d8a5ebdefa3a64ca898dd9a63f093785afc06b5483d3b307637"
REQUIRED_BATCH_TOOLS = {"register_batch", "prepare", "bind_batch_pr", "abort_batch"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence() -> dict:
    path = ROOT / "_project/analysis/batch-rollout-evidence.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _rpc(proc: subprocess.Popen[str], method: str, params: dict | None = None, request_id: int = 1) -> dict:
    assert proc.stdin is not None and proc.stdout is not None
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        stderr = proc.stderr.read() if proc.stderr is not None else ""
        raise AssertionError(f"no response for {method}: {stderr}")
    return json.loads(line)


def test_pin_receipt_and_tracked_mirror_bind_catalog_tip() -> None:
    config = (ROOT / "skill-sync.conf").read_text(encoding="utf-8")
    receipt = (ROOT / ".claude/skills/skill-sync.receipt").read_text(encoding="utf-8")
    evidence = _evidence()

    pins = re.findall(r"^source = ~/Developer/skill-sync-skills\nrev\s+=\s+([0-9a-f]{40})$", config, re.MULTILINE)
    assert pins, "skill-sync.conf pins no catalog revision"
    for pin in pins:
        assert f"rev = {pin}" in receipt
    assert evidence["catalog"]["merge_revision"] == DELIVERY_CATALOG_REV
    assert evidence["source"]["merge_revision"] == SOURCE_REV
    assert evidence["benchbox_pin"]["after"] == DELIVERY_CATALOG_REV
    for relative, expected in evidence["mirrors"]["tracked_target"]["files"].items():
        assert _sha256(ROOT / ".claude/skills/todo" / relative) == expected


def test_feature_mode_is_capability_gated_and_serial_remains_default() -> None:
    docs = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "AGENTS.md",
            "docs/agent/batch-feature-delivery.md",
            "docs/agent/review-protocol.md",
            "docs/internal/dev-loop-worktrees.md",
        )
    )
    normalized = docs.lower()
    assert "serial mode is the default" in normalized
    assert "active todo-db mcp server" in normalized
    assert "one shared integration branch" in normalized
    assert "one integrator" in normalized
    assert "no feature-base prs" in normalized
    assert "no new ci skips" in normalized


def test_project_local_wheel_and_lock_are_exact_and_sibling_free() -> None:
    assert WHEEL.is_file()
    assert _sha256(WHEEL) == WHEEL_SHA256
    pyproject = (SCRIPTS_PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (SCRIPTS_PROJECT / "uv.lock").read_text(encoding="utf-8")
    expected_path = f"vendor/todo_db-{TODO_DB_VERSION}-py3-none-any.whl"
    assert f'path = "{expected_path}"' in pyproject
    assert f'path = "{expected_path}"' in lock
    assert f'name = "todo-db"\nversion = "{TODO_DB_VERSION}"' in lock
    assert "/Users/joe/Developer/todo-db" not in pyproject
    assert "/Users/joe/Developer/todo-db" not in lock
    with zipfile.ZipFile(WHEEL) as archive:
        names = set(archive.namelist())
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
    assert "Name: todo-db\n" in metadata
    assert f"Version: {TODO_DB_VERSION}\n" in metadata
    assert "todo_db/mcp/server.py" in names
    assert "todo_db/mcp/tools.py" in names
    assert "todo_db/database.py" not in names


def test_installed_runtime_handshake_exposes_registered_batch_tools(tmp_path: Path) -> None:
    remote = tmp_path / "state.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True, capture_output=True)
    subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(SCRIPTS_PROJECT),
            "--locked",
            "--",
            "todo-db",
            "bootstrap",
            "--state-remote",
            str(remote),
            "--state-branch",
            "todo-state",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "--project",
            str(SCRIPTS_PROJECT),
            "--locked",
            "--",
            "todo-db-mcp",
            "--state-remote",
            str(remote),
            "--state-branch",
            "todo-state",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--actor",
            "batch-runtime-test",
        ],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        initialized = _rpc(
            proc,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "batch-runtime-test", "version": "1"},
            },
        )
        assert initialized["result"]["serverInfo"] == {"name": "todo-db", "version": TODO_DB_VERSION}
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        listed = _rpc(proc, "tools/list", {}, request_id=2)
        tools = listed["result"]["tools"]
        names = {tool["name"] for tool in tools}
        assert len(tools) == 19
        assert names >= REQUIRED_BATCH_TOOLS
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_rollout_evidence_records_active_runtime_capability() -> None:
    evidence = _evidence()
    runtime = evidence["runtime"]
    assert runtime["dependency_metadata_version"] == TODO_DB_VERSION
    assert runtime["schema_version"] == 3
    assert runtime["tool_count"] == 19
    assert set(runtime["registered_batch_tools"]) == REQUIRED_BATCH_TOOLS
    assert evidence["verification"]["active_runtime_blocker"] is None
