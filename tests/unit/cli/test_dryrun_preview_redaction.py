from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from benchbox.cli.commands.run import run
from benchbox.cli.config import ConfigManager
from benchbox.core.results.platform_options import REDACTED_VALUE
from benchbox.utils.runtime_env import DriverResolution

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REDSHIFT_HOST = "wg.123456789012.us-east-1.redshift-serverless.amazonaws.com"


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


def _preview_files(tmp_path: Path, platform: str, *options: str) -> dict[Path, str]:
    preview_dir = tmp_path / "preview"
    args = ["--platform", platform, "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
    for option in options:
        args.extend(["--platform-option", option])
    result = CliRunner().invoke(run, [*args, "--dry-run", str(preview_dir)], obj={"config": ConfigManager()})
    assert result.exit_code == 0, result.output
    files = {path: path.read_text(encoding="utf-8") for path in preview_dir.rglob("*") if path.is_file()}
    assert any(path.suffix == ".json" for path in files), sorted(files)
    assert any(path.suffix == ".yaml" for path in files), sorted(files)
    return files


def _assert_redacted(files: dict[Path, str], secret: str, visible: str) -> None:
    leaks = sorted(path.name for path, text in files.items() if secret in text)
    assert leaks == [], f"{secret!r} written to {leaks}"
    for path, text in files.items():
        if path.suffix in (".json", ".yaml"):
            assert visible in text, path.name


def _platform_config(files: dict[Path, str]) -> dict[str, Any]:
    payload = next(json.loads(text) for path, text in files.items() if path.suffix == ".json")
    config = payload["platform_config"]
    return {**config, **(config.get("options") or {})}


def test_saved_quanton_api_key_is_redacted_from_every_preview_file(tmp_path: Path) -> None:
    _save_credentials(
        quanton={"api_key": "quanton-key-7f3a91", "s3_staging_dir": "s3://bucket/path", "cluster_size": "large"}
    )

    files = _preview_files(tmp_path, "quanton")

    _assert_redacted(files, "quanton-key-7f3a91", "large")
    config = _platform_config(files)
    assert config["api_key"] == REDACTED_VALUE
    assert config["compute_size"] == "large"


def test_saved_snowflake_password_is_redacted_from_every_preview_file(tmp_path: Path) -> None:
    _save_credentials(
        snowflake={
            "account": "acct",
            "username": "u",
            "password": "snowflake-pw-52c8e0",
            "warehouse": "COMPUTE_WH",
            "warehouse_size": "SMALL",
            "database": "BENCHBOX",
        }
    )

    files = _preview_files(tmp_path, "snowflake")

    _assert_redacted(files, "snowflake-pw-52c8e0", "COMPUTE_WH")
    assert _platform_config(files)["password"] == REDACTED_VALUE


def test_saved_redshift_password_is_redacted_from_every_preview_file(tmp_path: Path) -> None:
    _save_credentials(redshift={"host": REDSHIFT_HOST, "username": "u", "password": "redshift-pw-0d4b2e"})

    files = _preview_files(tmp_path, "redshift")

    _assert_redacted(files, "redshift-pw-0d4b2e", REDSHIFT_HOST)
    assert _platform_config(files)["password"] == REDACTED_VALUE


def test_gateway_host_userinfo_is_rejected_before_any_preview_file_is_written(tmp_path: Path) -> None:
    _save_credentials(snowflake={"account": "acct", "username": "u", "password": "p", "warehouse": "COMPUTE_WH"})
    preview_dir = tmp_path / "preview"
    args = ["--platform", "snowflake", "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
    args += ["--platform-option", "gateway_host=https://gwuser:gateway-pw-9e61aa@gw.example.com"]

    result = CliRunner().invoke(run, [*args, "--dry-run", str(preview_dir)], obj={"config": ConfigManager()})

    assert result.exit_code == 1
    assert "gateway_host must be a bare host name" in result.output
    assert "gateway-pw-9e61aa" not in result.output
    assert not preview_dir.exists() or not any(preview_dir.rglob("*"))
