from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from benchbox.cli import billable_guard
from benchbox.cli.app import cli
from benchbox.cli.commands import run as run_module
from benchbox.cli.exit_codes import ExitCode
from benchbox.core import billable_platforms
from benchbox.core.billable_platforms import (
    ACKNOWLEDGEMENT_ENV_VAR,
    assess_platform,
    billable_platform_names,
)
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.schemas import ExecutionContext

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _expected_billable() -> dict[str, list[str]]:
    expected: dict[str, list[str]] = {}
    for name, metadata in PlatformRegistry.get_all_platform_metadata().items():
        capabilities = PlatformRegistry.get_platform_capabilities(name)
        managed = [
            mode_name
            for mode_name, mode in (capabilities.deployment_modes.items() if capabilities else [])
            if mode.mode == "managed"
        ]
        if metadata.get("category") == "cloud" or managed:
            expected[name] = managed
    return expected


EXPECTED = _expected_billable()
CLOUD_CATEGORY = sorted(
    name
    for name, metadata in PlatformRegistry.get_all_platform_metadata().items()
    if metadata.get("category") == "cloud"
)
MANAGED_MODES = sorted((name, mode) for name, modes in EXPECTED.items() for mode in modes)
DEFAULT_BILLABLE = sorted(name for name in EXPECTED if assess_platform(name).billable)
LOCAL_NAMES = sorted(set(PlatformRegistry.get_all_platform_metadata()) - set(EXPECTED))


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    monkeypatch.delenv(ACKNOWLEDGEMENT_ENV_VAR, raising=False)
    monkeypatch.delenv("BENCHBOX_NON_INTERACTIVE", raising=False)


class TestRegistryEnumeration:
    def test_billable_names_cover_every_cloud_category_and_managed_platform(self):
        assert set(billable_platform_names()) == set(EXPECTED)
        assert EXPECTED

    @pytest.mark.parametrize("name", CLOUD_CATEGORY)
    def test_every_cloud_category_platform_is_billable(self, name: str):
        assert assess_platform(name).billable

    @pytest.mark.parametrize(("name", "mode"), MANAGED_MODES)
    def test_every_managed_deployment_mode_is_billable(self, name: str, mode: str):
        assert assess_platform(name, deployment=mode).billable

    @pytest.mark.parametrize("name", LOCAL_NAMES)
    def test_other_platforms_are_not_billable(self, name: str):
        assert not assess_platform(name).billable

    def test_explicit_self_hosted_selection_is_not_billable_for_managed_default(self):
        assert assess_platform("influxdb").billable
        assert not assess_platform("influxdb", options={"mode": "core"}).billable


class TestRunRefusesBeforeAnyConnection:
    @pytest.fixture
    def tripwires(self, monkeypatch):
        calls: list[str] = []

        def trip(name: str):
            def raiser(*_args, **_kwargs):
                calls.append(name)
                raise AssertionError(f"{name} must not be reached")

            return raiser

        from benchbox.cli.database import DatabaseManager
        from benchbox.cli.orchestrator import BenchmarkOrchestrator
        from benchbox.security.credentials import CredentialManager

        monkeypatch.setattr(DatabaseManager, "create_config", trip("create_config"))
        monkeypatch.setattr(BenchmarkOrchestrator, "execute_benchmark", trip("execute_benchmark"))
        monkeypatch.setattr(CredentialManager, "get_platform_credentials", trip("get_platform_credentials"))
        monkeypatch.setattr(CredentialManager, "has_credentials", trip("has_credentials"))
        monkeypatch.setattr("benchbox.platforms.get_platform_adapter", trip("get_platform_adapter"))
        return calls

    @pytest.mark.parametrize("name", [*DEFAULT_BILLABLE, "timescaledb:cloud"])
    def test_billable_platform_is_refused_with_code_six(self, name: str, tripwires):
        result = CliRunner().invoke(
            cli, ["run", "--platform", name, "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
        )

        assert result.exit_code == ExitCode.ACKNOWLEDGEMENT_REQUIRED, result.output
        assert tripwires == []
        assert ACKNOWLEDGEMENT_ENV_VAR in result.output

    def test_compare_refuses_a_billable_platform_before_running_anything(self, tripwires):
        result = CliRunner().invoke(cli, ["compare", "-p", "duckdb", "-p", "snowflake", "--non-interactive"])

        assert result.exit_code == 6, result.output
        assert tripwires == []

    def test_compare_of_local_platforms_is_not_refused(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            "benchbox.cli.commands.compare._build_platform_runner",
            lambda *args, **kwargs: called.append(args) or MagicMock(side_effect=RuntimeError("stop")),
        )

        result = CliRunner().invoke(cli, ["compare", "-p", "duckdb", "-p", "sqlite", "--non-interactive"])

        assert result.exit_code != 6
        assert called


class TestAcknowledgement:
    @pytest.fixture
    def direct(self, monkeypatch):
        seen = {}

        def capture(s):
            seen["acknowledgement"] = s.billable_acknowledgement

        monkeypatch.setattr(run_module, "_run_direct", capture)

        def resolved(s):
            s.resolved_mode = "sql"
            s.resolved_execution_engine = "default"

        monkeypatch.setattr(run_module, "_resolve_platform_mode", resolved)
        return seen

    def _run(self, platform: str, *extra: str):
        return CliRunner().invoke(
            cli, ["run", "--platform", platform, "--benchmark", "tpch", "--non-interactive", *extra]
        )

    def test_flag_lets_the_run_proceed_and_records_its_source(self, direct):
        result = self._run("snowflake", "--acknowledge-billable-platform")

        assert result.exit_code == 0, result.output
        assert direct["acknowledgement"] == {"acknowledged": True, "source": "cli_flag"}

    def test_environment_variable_lets_the_run_proceed_and_records_its_source(self, direct, monkeypatch):
        monkeypatch.setenv(ACKNOWLEDGEMENT_ENV_VAR, "1")

        result = self._run("bigquery")

        assert result.exit_code == 0, result.output
        assert direct["acknowledgement"] == {"acknowledged": True, "source": "environment"}

    @pytest.mark.parametrize("name", ["duckdb", "sqlite", "clickhouse-server", "postgresql"])
    def test_local_and_self_hosted_platforms_are_unchanged(self, direct, name: str):
        result = self._run(name)

        assert result.exit_code == 0, result.output
        assert direct["acknowledgement"] is None

    def test_dry_run_is_exempt(self, monkeypatch):
        reached = []
        monkeypatch.setattr(run_module, "_run_dry_run", lambda s: reached.append(s.platform))

        result = CliRunner().invoke(
            cli, ["run", "--platform", "snowflake", "--benchmark", "tpch", "--non-interactive", "--dry-run", "/tmp/x"]
        )

        assert result.exit_code == 0, result.output
        assert reached == ["snowflake"]

    def test_interactive_terminal_session_is_not_refused(self, monkeypatch):
        monkeypatch.setattr(billable_platforms, "is_non_interactive_session", lambda flag=False: False)
        monkeypatch.setattr(billable_guard, "is_non_interactive_session", lambda flag=False: False)

        assert billable_guard.enforce_billable_acknowledgement("snowflake") is None

    def test_terminal_detection(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
        monkeypatch.setattr("sys.stdout", MagicMock(isatty=lambda: True))

        assert not billable_platforms.is_non_interactive_session()
        assert billable_platforms.is_non_interactive_session(True)
        monkeypatch.setenv("BENCHBOX_NON_INTERACTIVE", "true")
        assert billable_platforms.is_non_interactive_session()


class TestBundleRecordsAcknowledgement:
    def test_execution_block_carries_acknowledgement_and_source(self):
        from benchbox.core.results.schema import _build_execution_from_context

        context = ExecutionContext(
            entry_point="cli", billable_platform_acknowledgement={"acknowledged": True, "source": "cli_flag"}
        )
        result = MagicMock()
        result.execution_context = context.model_dump(mode="json")
        result.execution_metadata = {}
        result.platform_info = {}

        block = _build_execution_from_context(result, {})

        assert block["billable_platform_acknowledgement"] == {"acknowledged": True, "source": "cli_flag"}
