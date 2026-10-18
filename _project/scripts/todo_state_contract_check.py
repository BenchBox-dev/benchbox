#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

CLI_DESCRIPTION = "Fail closed when BenchBox still carries the retired database tracker."


class StateContractError(ValueError):
    pass


EXPECTED_REMOTE = "https://github.com/BenchBox-dev/BenchBox.git"
EXPECTED_BRANCH = "todo-state"
EXPECTED_VERSION = "0.10.1"
EXPECTED_WHEEL = f"todo_db-{EXPECTED_VERSION}-py3-none-any.whl"


def validate_contract(*, repo_root: Path) -> None:
    vendor = repo_root / "_project/scripts/vendor"
    wheels = sorted(vendor.glob("todo_db-*-py3-none-any.whl"))
    if [wheel.name for wheel in wheels] != [EXPECTED_WHEEL]:
        raise StateContractError(f"expected exactly {EXPECTED_WHEEL}, found {[wheel.name for wheel in wheels]!r}")

    pyproject = (repo_root / "_project/scripts/pyproject.toml").read_text(encoding="utf-8")
    if f"vendor/{EXPECTED_WHEEL}" not in pyproject:
        raise StateContractError(f"_project/scripts/pyproject.toml must pin vendor/{EXPECTED_WHEEL}")
    if re.search(r"(?i)libsql|turso|hosted", pyproject):
        raise StateContractError("_project/scripts/pyproject.toml still contains the retired hosted database runtime")

    config_path = repo_root / ".todo-db/config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateContractError(f"cannot read {config_path}: {exc}") from exc
    if config != {"state_branch": EXPECTED_BRANCH, "state_remote": EXPECTED_REMOTE}:
        raise StateContractError(f"{config_path} must identify the JSON/Git state branch")

    mcp_text = (repo_root / ".mcp.json").read_text(encoding="utf-8")
    if re.search(r"(?i)libsql|turso|allow-hosted|TODO_DB_(?:URL|AUTH_TOKEN|RO_AUTH_TOKEN)", mcp_text):
        raise StateContractError(".mcp.json still contains hosted database settings")

    try:
        with zipfile.ZipFile(wheels[0]) as archive:
            names = set(archive.namelist())
            required = {"todo_db/git_backend.py", "todo_db/cli.py", "todo_db/mcp/server.py", "todo_db/mcp/tools.py"}
            missing = sorted(required - names)
            if missing:
                raise StateContractError(f"{wheels[0]} is missing JSON/Git implementation files: {missing!r}")
            if "todo_db/database.py" in names or any(name.startswith("todo_db/migrations/") for name in names):
                raise StateContractError(f"{wheels[0]} still contains the retired SQLite implementation")
            try:
                metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
                metadata = archive.read(metadata_name).decode("utf-8")
            except (StopIteration, UnicodeDecodeError, KeyError) as exc:
                raise StateContractError(f"cannot read package metadata from {wheels[0]}") from exc
    except (OSError, zipfile.BadZipFile) as exc:
        raise StateContractError(f"cannot inspect {wheels[0]}: {exc}") from exc
    if "Name: todo-db\n" not in metadata or f"Version: {EXPECTED_VERSION}\n" not in metadata:
        raise StateContractError(f"{wheels[0]} does not identify the reviewed todo-db {EXPECTED_VERSION} runtime")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    validate_contract(repo_root=args.repo_root.resolve())
    print("TODO JSON/Git state contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
