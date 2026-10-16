from __future__ import annotations

import importlib
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from benchbox.cli.config import DirectoryManager
from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.config import BenchmarkConfig, DatabaseConfig
from benchbox.core.deployment import DEPLOYMENT_SELECTOR_OPTION, configured_deployment
from benchbox.core.deprecation_notices import run_notices, run_scope
from benchbox.core.dryrun import DryRunExecutor
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.run_service import resolve_lifecycle_phases
from benchbox.core.tuning.ddl_generator import get_ddl_generator
from benchbox.core.tuning.generators.timescaledb import TimescaleDBDDLGenerator

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _tpch(test_execution_type: str = "load_only") -> BenchmarkConfig:
    return BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        test_execution_type=test_execution_type,
        options={"enable_preflight_validation": False},
    )


class TestSelectorInType:
    def test_family_selector_resolves_to_base_platform_and_deployment(self) -> None:
        config = DatabaseConfig(type="timescaledb:cloud", name="db", options={"host": "h"})

        assert config.type == "timescaledb"
        assert config.options == {"host": "h", DEPLOYMENT_SELECTOR_OPTION: "cloud"}
        assert configured_deployment(config) == "cloud"

    def test_lookups_keyed_on_type_see_the_base_platform(self, tmp_path: Path) -> None:
        config = DatabaseConfig(type="timescaledb:cloud", name="db")

        assert PlatformRegistry.get_platform_capabilities(config.type) is not None
        assert isinstance(get_ddl_generator(config.type), TimescaleDBDDLGenerator)
        database_path = DirectoryManager(str(tmp_path)).get_database_path("tpch", 0.01, config.type)
        assert ":" not in database_path.name

    def test_dataframe_alias_selector_sets_execution_mode(self) -> None:
        config = DatabaseConfig(type="lakesail-df:distributed", name="db", options={"endpoint": "sc://h:1"})

        assert config.type == "lakesail"
        assert config.execution_mode == "dataframe"
        assert config.options[DEPLOYMENT_SELECTOR_OPTION] == "distributed"

    def test_explicit_execution_mode_is_kept(self) -> None:
        config = DatabaseConfig(type="lakesail-df:local", name="db", execution_mode="sql")

        assert config.execution_mode == "sql"

    @pytest.mark.parametrize("mode", ["local", "server", "cloud"])
    def test_legacy_clickhouse_selectors_are_left_to_the_adapter_factory(self, mode: str) -> None:
        config = DatabaseConfig(type=f"clickhouse:{mode}", name="db")

        assert config.type == f"clickhouse:{mode}"
        assert DEPLOYMENT_SELECTOR_OPTION not in config.options

    def test_default_selector_on_platform_without_deployment_options(self) -> None:
        config = DatabaseConfig(type="duckdb:local", name="db")

        assert config.type == "duckdb"
        assert DEPLOYMENT_SELECTOR_OPTION not in config.options

    def test_undeclared_mode_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="does not support deployment mode 'bogus'"):
            DatabaseConfig(type="timescaledb:bogus", name="db")

    def test_conflicting_internal_selector_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="conflicts with explicit deployment"):
            DatabaseConfig(type="timescaledb:cloud", name="db", options={DEPLOYMENT_SELECTOR_OPTION: "self-hosted"})

    def test_type_without_selector_is_unchanged(self) -> None:
        config = DatabaseConfig(type="lakesail-df", name="db", options={"deployment_mode": "local"})

        assert config.type == "lakesail-df"
        assert config.execution_mode is None
        assert config.options == {"deployment_mode": "local"}


class TestDryRunRecordsDeployment:
    def test_selector_form_records_mode_without_deprecation_notice(self, tmp_path: Path) -> None:
        config = DatabaseConfig(type="timescaledb:cloud", name="db", options={"host": "h", "password": "p"})

        with run_scope():
            result = DryRunExecutor(tmp_path).execute_dry_run(_tpch(), _profile(), config)
            notices = [notice.message for notice in run_notices()]

        assert result.deployment == "cloud"
        assert result.database_config["type"] == "timescaledb"
        assert not [message for message in notices if "deprecated" in message]

    def test_deprecated_option_still_selects_and_warns(self, tmp_path: Path) -> None:
        config = DatabaseConfig(
            type="timescaledb", name="db", options={"host": "h", "password": "p", "deployment_mode": "cloud"}
        )

        with run_scope():
            result = DryRunExecutor(tmp_path).execute_dry_run(_tpch(), _profile(), config)
            notices = [notice.message for notice in run_notices()]

        assert result.deployment == "cloud"
        assert any("'deployment_mode' for 'timescaledb' is deprecated" in message for message in notices)

    def test_unselected_run_records_the_manifest_default(self, tmp_path: Path) -> None:
        config = DatabaseConfig(type="timescaledb", name="db", options={"host": "h", "password": "p"})

        result = DryRunExecutor(tmp_path).execute_dry_run(_tpch(), _profile(), config)

        assert result.deployment == PlatformRegistry.get_default_deployment("timescaledb")

    def test_dataframe_alias_dry_run_does_not_build_a_sql_adapter(self, tmp_path: Path) -> None:
        config = DatabaseConfig(type="lakesail-df", name="db", options={"endpoint": "sc://h:1"})

        result = DryRunExecutor(tmp_path).execute_dry_run(_tpch(), _profile(), config)

        assert result.execution_mode == "dataframe"
        assert not [warning for warning in result.warnings if "Unsupported platform" in warning]

    def test_tsbs_devops_dry_run_previews_queries(self, tmp_path: Path) -> None:
        benchmark = BenchmarkConfig(name="tsbs_devops", display_name="TSBS DevOps", scale_factor=0.01)
        config = DatabaseConfig(type="influxdb:core", name="db", options={"token": "t"})

        result = DryRunExecutor(tmp_path).execute_dry_run(benchmark, _profile(), config)

        assert result.deployment == "core"
        assert result.queries
        assert not [warning for warning in result.warnings if "Unknown benchmark" in warning]


class TestDataFrameAdapterOptions:
    @pytest.fixture
    def captured(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        calls: dict[str, Any] = {}

        def fake_get_adapter(platform: str, **kwargs: Any) -> SimpleNamespace:
            calls["platform"] = platform
            calls.update(kwargs)
            return SimpleNamespace()

        monkeypatch.setattr("benchbox.cli.orchestrator.get_adapter", fake_get_adapter)
        return calls

    def _build(self, tmp_path: Path, config: DatabaseConfig) -> None:
        BenchmarkOrchestrator(base_dir=str(tmp_path))._build_platform_adapter(
            config, "dataframe", str(tmp_path), {}, {}, None, resolve_lifecycle_phases(["power"]), _tpch("power")
        )

    def test_selector_passes_endpoint_and_deployment(self, tmp_path: Path, captured: dict[str, Any]) -> None:
        self._build(
            tmp_path, DatabaseConfig(type="lakesail-df:distributed", name="db", options={"endpoint": "sc://h:1"})
        )

        assert captured["platform"] == "lakesail"
        assert captured["endpoint"] == "sc://h:1"
        assert captured["deployment"] == "distributed"

    def test_alias_type_keeps_registered_options(self, tmp_path: Path, captured: dict[str, Any]) -> None:
        config = DatabaseConfig(
            type="lakesail-df", name="db", options={"endpoint": "sc://h:1", "deployment_mode": "local"}
        )

        self._build(tmp_path, config)

        assert captured["endpoint"] == "sc://h:1"
        assert captured["deployment_mode"] == "local"
        assert captured["deployment"] is None


class TestLakeSailDataFrameDeployment:
    @pytest.fixture(autouse=True)
    def _pyspark(self) -> None:
        pytest.importorskip("pyspark")

    def test_records_selected_deployment_outside_spark_config(self) -> None:
        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        adapter = LakeSailDataFrameAdapter(endpoint="sc://h:1", deployment_mode="distributed")

        assert adapter.deployment_selected == "distributed"
        assert adapter.deployment_selected_class == "self-hosted"
        assert "deployment_mode" not in adapter._spark_config
        assert adapter.get_platform_info()["deployment_mode"] == "distributed"

    def test_defaults_to_local(self) -> None:
        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        assert LakeSailDataFrameAdapter().deployment_selected == "local"

    def test_get_adapter_passes_selector_to_dataframe_adapter(self) -> None:
        from benchbox.platforms import get_adapter

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            adapter = get_adapter("lakesail", mode="dataframe", deployment="distributed", endpoint="sc://h:1")

        assert adapter._endpoint == "sc://h:1"
        assert adapter.deployment_selected == "distributed"


class TestCliSelectorSplitting:
    def test_dataframe_alias_selector_keeps_deployment(self) -> None:
        from benchbox.cli.run_platform_resolution import _split_deployment_selector

        assert _split_deployment_selector("lakesail-df:distributed") == ("lakesail", "distributed")

    def test_dataframe_alias_selector_implies_dataframe_mode(self) -> None:
        from benchbox.cli.run_platform_resolution import _apply_dataframe_suffix_mode

        namespace = SimpleNamespace(mode=None, platform="lakesail-df:distributed")
        _apply_dataframe_suffix_mode(namespace)

        assert namespace.mode == "dataframe"


EXAMPLE_SELECTIONS = [
    ("examples.getting_started.sql.lakesail_tpch", "lakesail", "local"),
    ("examples.getting_started.dataframe.lakesail_df_tpch", "lakesail", "local"),
    ("examples.getting_started.cloud.tigerdata_tpch", "timescaledb", "cloud"),
]


@pytest.mark.parametrize(("module_name", "platform", "deployment"), EXAMPLE_SELECTIONS)
def test_examples_select_deployment_with_the_type_selector(
    monkeypatch: pytest.MonkeyPatch, module_name: str, platform: str, deployment: str
) -> None:
    monkeypatch.setenv("TIGERDATA_HOST", "h")
    monkeypatch.setenv("TIGERDATA_PASSWORD", "p")
    monkeypatch.delenv("SAIL_MODE", raising=False)
    module = importlib.import_module(module_name)

    _benchmark, database = module._build_configs(0.01)

    assert database.type == platform
    assert configured_deployment(database) == deployment
    assert "deployment_mode" not in database.options


def test_comparison_and_influxdb_examples_select_deployment_with_the_type_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INFLUXDB_TOKEN", "t")
    comparison = importlib.import_module("examples.getting_started.sql.lakesail_vs_spark_comparison")
    influxdb = importlib.import_module("examples.getting_started.cloud.influxdb_tsbs_devops")

    lakesail = comparison._build_lakesail_config()
    _benchmark, influx = influxdb._build_configs(0.01, "core", ssl=False)

    assert (lakesail.type, configured_deployment(lakesail)) == ("lakesail", "local")
    assert (influx.type, configured_deployment(influx)) == ("influxdb", "core")
    assert "deployment_mode" not in lakesail.options
    assert "deployment_mode" not in influx.options


def _profile():
    from benchbox.core.system import SystemProfiler

    return SystemProfiler().get_system_profile()
