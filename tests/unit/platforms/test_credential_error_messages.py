from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from click.testing import CliRunner

import benchbox
import benchbox.platforms.azure_synapse as synapse_module
import benchbox.platforms.fabric_warehouse as fabric_module
from benchbox.cli.commands.run import run
from benchbox.cli.commands.setup import SUPPORTED_SETUP_PLATFORMS
from benchbox.cli.config import ConfigManager
from benchbox.core.exceptions import ConfigurationError
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.platforms.azure_synapse import AzureSynapseAdapter
from benchbox.platforms.databricks.adapter import DatabricksAdapter
from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter
from benchbox.utils.runtime_env import DriverResolution

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ENV_NAME = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
SETUP_COMMAND = re.compile(r"benchbox setup --platform (\w+)")
PLATFORM_OPTION = re.compile(r"--platform-option (\w+)=")
PACKAGE_ROOT = Path(benchbox.__file__).resolve().parent


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


@pytest.fixture()
def drivers_present(monkeypatch: pytest.MonkeyPatch) -> None:
    pyodbc = MagicMock()
    pyodbc.Error = Exception
    for module in (synapse_module, fabric_module):
        monkeypatch.setattr(module, "pyodbc", pyodbc)
        monkeypatch.setattr(module, "check_platform_dependencies", lambda *a, **kw: (True, []))
    monkeypatch.setattr("benchbox.platforms.databricks.adapter.databricks_sql", MagicMock())
    monkeypatch.setattr(
        "benchbox.platforms.databricks.adapter.check_platform_dependencies", lambda *a, **kw: (True, [])
    )


@pytest.fixture()
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    return home_dir


def _incomplete_configuration_messages() -> dict[str, list[str]]:
    cases: dict[str, list[tuple[type, dict[str, Any]]]] = {
        "synapse": [
            (AzureSynapseAdapter, {}),
            (AzureSynapseAdapter, {"auth_method": "aad_msi"}),
        ],
        "fabric_dw": [
            (FabricWarehouseAdapter, {}),
            (
                FabricWarehouseAdapter,
                {"workspace": "ws-guid", "database": "wh", "auth_method": "service_principal"},
            ),
        ],
        "databricks": [(DatabricksAdapter, {})],
    }
    messages: dict[str, list[str]] = {}
    for platform, attempts in cases.items():
        for adapter_class, kwargs in attempts:
            with pytest.raises(ConfigurationError) as raised:
                adapter_class(**kwargs)
            messages.setdefault(platform, []).append(str(raised.value))
    return messages


def _environment_reads() -> set[str]:
    read: set[str] = set()
    pattern = re.compile(r"""(?:getenv|environ)[^\n]*?["']([A-Z][A-Z0-9_]*)["']""")
    for path in PACKAGE_ROOT.rglob("*.py"):
        read.update(pattern.findall(path.read_text(encoding="utf-8")))
    return read


def test_incomplete_configuration_messages_name_no_environment_variable_that_is_never_read(
    drivers_present: None, home: Path
) -> None:
    messages = _incomplete_configuration_messages()
    assert set(messages) == {"synapse", "fabric_dw", "databricks"}
    read = _environment_reads()

    named = {
        (platform, name)
        for platform, entries in messages.items()
        for message in entries
        for name in ENV_NAME.findall(message)
    }

    assert {pair for pair in named if pair[1] not in read} == set()


def test_incomplete_configuration_messages_name_only_supported_ways_to_set_values(
    drivers_present: None, home: Path
) -> None:
    for platform, entries in _incomplete_configuration_messages().items():
        for message in entries:
            for target in SETUP_COMMAND.findall(message):
                assert target in SUPPORTED_SETUP_PLATFORMS, (platform, message)
            specs = PlatformHookRegistry.list_option_specs(platform)
            for option in PLATFORM_OPTION.findall(message):
                assert option in specs, (platform, option)


@pytest.mark.parametrize(
    ("platform", "section"),
    [("synapse", "synapse"), ("databricks", "databricks")],
)
def test_credentials_file_is_named_where_the_platform_reads_it(
    drivers_present: None, home: Path, platform: str, section: str
) -> None:
    message = _incomplete_configuration_messages()[platform][0]

    assert "~/.benchbox/credentials.yaml" in message
    assert f"'{section}:'" in message
    assert "${VAR}" in message


def test_exporting_the_formerly_named_variables_does_not_configure_synapse(
    monkeypatch: pytest.MonkeyPatch, drivers_present: None, home: Path
) -> None:
    monkeypatch.setenv("SYNAPSE_SERVER", "ws.sql.azuresynapse.net")
    monkeypatch.setenv("SYNAPSE_USER", "admin")
    monkeypatch.setenv("SYNAPSE_PASSWORD", "secret")

    with pytest.raises(ConfigurationError, match="Azure Synapse SQL authentication is incomplete"):
        AzureSynapseAdapter()


def _constructor_kwargs(
    monkeypatch: pytest.MonkeyPatch, preview_dir: Path, platform: str, *options: str
) -> dict[str, Any]:
    adapter_class = PlatformRegistry.get_adapter_class(platform)
    seen: list[dict[str, Any]] = []

    def spy(self: Any, **kwargs: Any) -> None:
        seen.append(kwargs)
        raise AdapterReached

    monkeypatch.setattr(adapter_class, "__init__", spy)
    args = ["--platform", platform, "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
    for option in options:
        args.extend(["--platform-option", option])
    result = CliRunner().invoke(run, [*args, "--dry-run", str(preview_dir)], obj={"config": ConfigManager()})
    assert result.exit_code == 0, result.output
    assert len(seen) == 1
    return seen[0]


def test_credentials_file_interpolates_environment_variables_for_synapse(
    monkeypatch: pytest.MonkeyPatch, home: Path, tmp_path: Path
) -> None:
    credentials = home / ".benchbox" / "credentials.yaml"
    credentials.parent.mkdir()
    credentials.write_text(
        yaml.safe_dump(
            {
                "synapse": {
                    "server": "${CI_SYNAPSE_SERVER}",
                    "username": "${CI_SYNAPSE_USER}",
                    "password": "${CI_SYNAPSE_PASSWORD}",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CI_SYNAPSE_SERVER", "ws.sql.azuresynapse.net")
    monkeypatch.setenv("CI_SYNAPSE_USER", "ci-admin")
    monkeypatch.setenv("CI_SYNAPSE_PASSWORD", "ci-secret")

    kwargs = _constructor_kwargs(monkeypatch, tmp_path, "synapse")

    assert kwargs["server"] == "ws.sql.azuresynapse.net"
    assert kwargs["username"] == "ci-admin"
    assert kwargs["password"] == "ci-secret"


def test_platform_option_beats_the_credentials_file_for_synapse(
    monkeypatch: pytest.MonkeyPatch, home: Path, tmp_path: Path
) -> None:
    credentials = home / ".benchbox" / "credentials.yaml"
    credentials.parent.mkdir()
    credentials.write_text(yaml.safe_dump({"synapse": {"server": "${CI_SYNAPSE_SERVER}"}}), encoding="utf-8")
    monkeypatch.setenv("CI_SYNAPSE_SERVER", "from-file.sql.azuresynapse.net")

    kwargs = _constructor_kwargs(monkeypatch, tmp_path, "synapse", "server=from-cli.sql.azuresynapse.net")

    assert kwargs["server"] == "from-cli.sql.azuresynapse.net"
