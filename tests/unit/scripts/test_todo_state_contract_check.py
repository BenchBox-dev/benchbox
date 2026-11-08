from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "_project/scripts/todo_state_contract_check.py"
SPEC = importlib.util.spec_from_file_location("todo_state_contract_check", SCRIPT)
assert SPEC and SPEC.loader
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_json_git_state_contract_is_current() -> None:
    check.validate_contract(repo_root=REPO_ROOT)


def test_contract_rejects_database_runtime(tmp_path: Path) -> None:
    vendor = tmp_path / "_project/scripts/vendor"
    vendor.mkdir(parents=True)
    with zipfile.ZipFile(vendor / check.EXPECTED_WHEEL, "w") as archive:
        archive.writestr("tractable/database.py", "SCHEMA_VERSION = 7\n")
        archive.writestr("tractable/git_backend.py", "")
        archive.writestr("tractable/cli.py", "")
        archive.writestr("tractable/mcp/server.py", "")
        archive.writestr("tractable/mcp/tools.py", "")
        archive.writestr(
            f"tractable-{check.EXPECTED_VERSION}.dist-info/METADATA",
            f"Name: tractable\nVersion: {check.EXPECTED_VERSION}\n",
        )
    scripts = tmp_path / "_project/scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "pyproject.toml").write_text(
        f'[tool.uv.sources]\ntractable = {{ path = "vendor/{check.EXPECTED_WHEEL}" }}\n', encoding="utf-8"
    )
    config_dir = tmp_path / ".tractable"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"state_branch": check.EXPECTED_BRANCH, "state_remote": check.EXPECTED_REMOTE}), encoding="utf-8"
    )
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")

    with pytest.raises(check.StateContractError, match="retired SQLite"):
        check.validate_contract(repo_root=tmp_path)
