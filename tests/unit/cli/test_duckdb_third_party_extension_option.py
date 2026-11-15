from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.run import run
from benchbox.cli.config import ConfigManager
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.utils.runtime_env import DriverResolution

pytestmark = [pytest.mark.unit, pytest.mark.medium]

OPTION = "allow_third_party_extensions"


@pytest.fixture(autouse=True)
def _drivers_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "benchbox.cli.database.ensure_driver_version",
        lambda package_name, requested_version, auto_install=False, install_hint=None, allow_install=True: (
            DriverResolution(
                package=package_name or "",
                requested=requested_version,
                resolved=requested_version or "local-dev",
                actual=requested_version or "local-dev",
                auto_install_used=False,
                runtime_strategy="current-process",
                runtime_path=None,
                runtime_python_executable=sys.executable,
            )
        ),
    )


def _dry_run(tmp_path: Path, *options: str) -> dict[str, Any]:
    preview_dir = Path(tempfile.mkdtemp(prefix="preview-", dir=tmp_path))
    args = ["--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
    for option in options:
        args.extend(["--platform-option", option])
    result = CliRunner().invoke(run, [*args, "--dry-run", str(preview_dir)], obj={"config": ConfigManager()})
    assert result.exit_code == 0, result.output
    (summary,) = preview_dir.glob("tpch_*.json")
    return json.loads(summary.read_text(encoding="utf-8"))


def test_dry_run_shows_option_when_set(tmp_path: Path) -> None:
    payload = _dry_run(tmp_path, f"{OPTION}=true")

    assert f'"{OPTION}": true' in json.dumps(payload)


def test_dry_run_omits_option_when_unset(tmp_path: Path) -> None:
    payload = _dry_run(tmp_path)

    assert f'"{OPTION}": true' not in json.dumps(payload)


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, False), (False, False), ("false", False), ("true", True), (True, True)]
)
def test_adapter_parses_option(raw, expected) -> None:
    config: dict[str, Any] = {"benchmark": "tpch", "scale_factor": 0.01, "database_path": ":memory:"}
    if raw is not None:
        config[OPTION] = raw

    adapter = DuckDBAdapter.from_config(config)

    assert adapter.allow_third_party_extensions is expected
    assert (OPTION in adapter.get_platform_info()["configuration"]) is expected
