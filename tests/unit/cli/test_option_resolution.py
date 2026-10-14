from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.run import run
from benchbox.cli.config import ConfigManager
from benchbox.cli.database import DatabaseManager
from benchbox.cli.platform import normalize_platform_name
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.security.credentials import CredentialManager
from benchbox.utils.runtime_env import DriverResolution

pytestmark = [pytest.mark.unit, pytest.mark.medium]


class AdapterReached(Exception):
    pass


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


def _invoke(tmp_path: Path, platform: str, *options: str) -> Any:
    preview_dir = Path(tempfile.mkdtemp(prefix="preview-", dir=tmp_path))
    args = ["--platform", platform, "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
    for option in options:
        args.extend(["--platform-option", option])
    return CliRunner().invoke(run, [*args, "--dry-run", str(preview_dir)], obj={"config": ConfigManager()})


def _constructor_kwargs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, canonical: str, *options: str
) -> dict[str, Any]:
    seen: list[dict[str, Any]] = []

    def spy(self: Any, **kwargs: Any) -> None:
        seen.append(kwargs)
        raise AdapterReached

    monkeypatch.setattr(PlatformRegistry.get_adapter_class(canonical), "__init__", spy)
    result = _invoke(tmp_path, platform, *options)
    assert result.exit_code == 0, result.output
    assert len(seen) == 1, f"{platform} adapter was not constructed"
    return seen[0]


def _save_credentials(monkeypatch: pytest.MonkeyPatch, platform: str, saved: dict[str, Any]) -> None:
    original = CredentialManager.get_platform_credentials

    def fake(self: CredentialManager, name: str, *args: Any, **kwargs: Any) -> Any:
        if name == platform:
            return dict(saved)
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(CredentialManager, "get_platform_credentials", fake)


@pytest.mark.parametrize(
    ("platform", "option", "default_value", "saved_value"),
    [
        ("postgresql", "host", "localhost", "prod.example.com"),
        ("starrocks", "host", "localhost", "prod.example.com"),
        ("trino", "table_format", "memory", "hive"),
        ("presto", "table_format", "memory", "hive"),
    ],
)
def test_cli_value_equal_to_the_default_overrides_the_saved_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, option: str, default_value: str, saved_value: str
) -> None:
    assert PlatformHookRegistry.get_default_options(platform)[option] == default_value
    _save_credentials(monkeypatch, platform, {option: saved_value})

    saved = _constructor_kwargs(monkeypatch, tmp_path, platform, platform)
    explicit = _constructor_kwargs(monkeypatch, tmp_path, platform, platform, f"{option}={default_value}")

    assert saved[option] == saved_value
    assert explicit[option] == default_value


def test_cli_value_that_differs_from_the_default_still_overrides_the_saved_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _save_credentials(monkeypatch, "postgresql", {"host": "prod.example.com"})

    kwargs = _constructor_kwargs(monkeypatch, tmp_path, "postgresql", "postgresql", "host=staging.example.com")

    assert kwargs["host"] == "staging.example.com"


def test_programmatic_option_without_explicit_keys_overrides_the_saved_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_credentials(monkeypatch, "postgresql", {"host": "prod.example.com"})

    config = DatabaseManager().create_config("postgresql", {"host": "staging.example.com"})

    assert config.options["host"] == "staging.example.com"


def test_programmatic_option_equal_to_the_default_keeps_the_saved_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_credentials(monkeypatch, "postgresql", {"host": "prod.example.com"})

    config = DatabaseManager().create_config("postgresql", {"host": "localhost"})

    assert config.options["host"] == "prod.example.com"


ALIAS_CASES = [
    ("sqlite3", "sqlite", "timeout=7.5", "timeout", 7.5),
    ("azure_synapse", "synapse", "resource_class=staticrc40", "resource_class", "staticrc40"),
    ("pg_duckdb", "pg-duckdb", "work_mem=512MB", "work_mem", "512MB"),
    ("fabric_lakehouse", "fabric-lakehouse", "plan_max_depth=5", "plan_max_depth", 5),
]


@pytest.mark.parametrize(("alias", "canonical", "option", "kwarg", "expected"), ALIAS_CASES)
def test_registry_alias_accepts_the_options_of_its_canonical_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, alias: str, canonical: str, option: str, kwarg: str, expected: Any
) -> None:
    assert normalize_platform_name(alias) == canonical

    kwargs = _constructor_kwargs(monkeypatch, tmp_path, alias, canonical, option)

    assert kwargs[kwarg] == expected


def test_alias_with_an_unknown_option_reports_the_canonical_options(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "sqlite3", "no_such_option=1")

    assert result.exit_code != 0
    assert "does not accept platform-specific options" not in result.output
    assert "Unknown platform option 'no_such_option' for platform 'sqlite'" in result.output


def test_sqlite_alias_dry_run_records_the_canonical_platform(tmp_path: Path) -> None:
    preview_dir = Path(tempfile.mkdtemp(prefix="preview-", dir=tmp_path))
    result = CliRunner().invoke(
        run,
        [
            "--platform",
            "sqlite3",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--non-interactive",
            "--platform-option",
            "timeout=7.5",
            "--dry-run",
            str(preview_dir),
        ],
        obj={"config": ConfigManager()},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(next(preview_dir.glob("tpch_*.json")).read_text(encoding="utf-8"))
    assert payload["platform_config"]["timeout"] == 7.5


FRESH_PROCESS_CHECK = """
import sys
import benchbox.platforms
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
specs = PlatformHookRegistry.list_option_specs(sys.argv[1])
assert specs, "no option specs registered"
assert "benchbox.cli" not in sys.modules
assert "driver_version" in specs
"""


@pytest.mark.parametrize("platform", ["clickhouse", "duckdb", "sqlite", "postgresql"])
def test_option_specs_register_when_only_the_platforms_package_is_imported(platform: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", FRESH_PROCESS_CHECK, platform], capture_output=True, text=True, check=False
    )

    assert completed.returncode == 0, completed.stderr


def test_supplied_keys_resolve_aliases_and_ignore_defaults() -> None:
    keys = PlatformHookRegistry.supplied_option_keys("clickhouse", [("USER", "alice"), ("port", "9000")])

    assert {"username", "port"} <= keys
    assert "host" not in keys


def test_explicit_layer_keeps_only_supplied_keys() -> None:
    layer = PlatformHookRegistry.explicit_option_layer({"host": "localhost", "port": 9000}, {"host"})

    assert layer == {"host": "localhost"}
    assert PlatformHookRegistry.explicit_option_layer({"host": "localhost"}) == {}
