from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from benchbox.cli.commands.run import run
from benchbox.cli.config import ConfigManager
from benchbox.utils.runtime_env import DriverResolution

pytestmark = [pytest.mark.unit, pytest.mark.medium]


@pytest.fixture(autouse=True)
def _drivers_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "benchbox.cli.database.ensure_driver_version",
        lambda package_name, requested_version, auto_install=False, install_hint=None: DriverResolution(
            package=package_name or "",
            requested=requested_version,
            resolved=requested_version or "local-dev",
            actual=requested_version or "local-dev",
            auto_install_used=False,
            runtime_strategy="current-process",
            runtime_path=None,
            runtime_python_executable=sys.executable,
        ),
    )
    for module in ("redshift", "snowflake"):
        monkeypatch.setattr(f"benchbox.platforms.{module}.check_platform_dependencies", lambda *_args: (True, []))


def _save_credentials(**platforms: dict[str, Any]) -> None:
    path = Path.home() / ".benchbox" / "credentials.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(platforms), encoding="utf-8")


def _dry_run(tmp_path: Path, platform: str, *extra: str) -> Any:
    preview_dir = Path(tempfile.mkdtemp(prefix="preview-", dir=tmp_path))
    args = ["--platform", platform, "--benchmark", "tpch", "--scale", "0.01", "--non-interactive", *extra]
    return CliRunner().invoke(run, [*args, "--dry-run", str(preview_dir)], obj={"config": ConfigManager()})


@pytest.mark.parametrize(
    ("platform", "profile"),
    [
        ("snowflake", {"account": "acct", "username": "u", "password": "p"}),
        ("redshift", {"host": "cl.abc123.us-east-1.redshift.amazonaws.com", "username": "u", "password": "p"}),
    ],
)
def test_saved_profile_carrying_an_execution_engine_fails_closed(
    tmp_path: Path, platform: str, profile: dict[str, str]
) -> None:
    _save_credentials(**{platform: {**profile, "execution_engine": "photon"}})

    result = _dry_run(tmp_path, platform)

    output = " ".join(result.output.split())
    assert result.exit_code != 0, output
    assert "--execution-engine" in output
    assert "execution_engine" in output


@pytest.mark.parametrize(
    ("platform", "profile"),
    [
        ("snowflake", {"account": "acct", "username": "u", "password": "p"}),
        ("redshift", {"host": "cl.abc123.us-east-1.redshift.amazonaws.com", "username": "u", "password": "p"}),
    ],
)
def test_saved_profile_without_an_execution_engine_still_builds(
    tmp_path: Path, platform: str, profile: dict[str, str]
) -> None:
    _save_credentials(**{platform: profile})

    result = _dry_run(tmp_path, platform)

    assert result.exit_code == 0, result.output
