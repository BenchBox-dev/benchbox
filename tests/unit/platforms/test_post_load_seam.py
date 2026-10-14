from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.core.tuning.applied_ledger import (
    PHASE_POST_LOAD,
    STATEMENT_FAILED,
    AppliedTuningLedger,
)
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.core.tuning.introspection import MAINTENANCE, _classify
from benchbox.platforms.base import PlatformAdapter
from benchbox.platforms.base.tuning import TuningHooksMixin
from benchbox.platforms.databricks import DatabricksAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PLATFORMS_ROOT = Path(__file__).resolve().parents[3] / "benchbox" / "platforms"


class _Adapter(PlatformAdapter):
    def __init__(self, hook=None, **config: Any) -> None:
        super().__init__(**config)
        self._hook = hook

    def get_target_dialect(self) -> str:
        return "duckdb"

    @staticmethod
    def add_cli_arguments(parser) -> None:
        return None

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls(**config)

    def create_connection(self, **connection_config) -> Any:
        return Mock()

    def create_schema(self, benchmark, connection: Any) -> float:
        return 0.0

    def apply_platform_optimizations(self, platform_config, connection: Any) -> None:
        return None

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection: Any) -> None:
        return None

    def load_data(self, benchmark, connection: Any, data_dir: Path):
        return {}, 0.0, None

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        return None

    def execute_query(self, connection: Any, query: str, query_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"query_id": query_id, "status": "SUCCESS", "execution_time_seconds": 0.0, "rows_returned": 0}

    def apply_post_load_tunings(self, table_name: str, effective_config: Any, connection: Any) -> bool:
        if self._hook is None:
            return False
        self._hook(table_name, effective_config, connection)
        return True


class _Connection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str, *args, **kwargs):
        self.statements.append(statement)
        if statement.startswith("BROKEN"):
            raise RuntimeError("engine refused")
        return None


CONFIG = SimpleNamespace(table_tunings={})


class TestPostLoadRunner:
    def test_default_hook_does_nothing(self):
        connection = _Connection()

        assert TuningHooksMixin.apply_post_load_tunings(object(), "lineitem", CONFIG, connection) is False
        assert connection.statements == []

    def test_statements_are_recorded_in_the_post_load_phase(self):
        adapter = _Adapter(lambda table, config, connection: connection.execute(f"OPTIMIZE TABLE {table} FINAL"))
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        connection = _Connection()

        adapter.run_post_load_tunings("lineitem", CONFIG, connection)

        assert connection.statements == ["OPTIMIZE TABLE lineitem FINAL"]
        [recorded] = adapter._applied_tuning_ledger.statements
        assert (recorded.statement, recorded.phase) == ("OPTIMIZE TABLE lineitem FINAL", PHASE_POST_LOAD)

    def test_recorded_maintenance_statement_never_blocks_verification(self):
        adapter = _Adapter(lambda table, config, connection: connection.execute(f"OPTIMIZE TABLE {table} FINAL"))
        adapter._applied_tuning_ledger = AppliedTuningLedger()

        adapter.run_post_load_tunings("lineitem", CONFIG, _Connection())

        [recorded] = adapter._applied_tuning_ledger.statements
        assert _classify(recorded) == (MAINTENANCE, [])

    def test_failure_is_recorded_as_failed_and_never_breaks_the_run(self):
        adapter = _Adapter(lambda table, config, connection: connection.execute("BROKEN OPTIMIZE"))
        adapter._applied_tuning_ledger = AppliedTuningLedger()

        adapter.run_post_load_tunings("lineitem", CONFIG, _Connection())

        [recorded] = adapter._applied_tuning_ledger.statements
        assert recorded.status == STATEMENT_FAILED
        assert "engine refused" in recorded.error

    def test_a_hook_that_raises_outside_a_statement_never_breaks_the_run(self):
        def hook(table, config, connection):
            raise RuntimeError("boom")

        adapter = _Adapter(hook)

        adapter.run_post_load_tunings("lineitem", CONFIG, _Connection())

        assert adapter.get_post_load_maintenance_metadata()["applied_tables"] == ["lineitem"]

    def test_a_hook_that_performs_nothing_leaves_no_phase_and_no_metadata(self):
        adapter = _Adapter()

        adapter.run_post_load_tunings("lineitem", CONFIG, _Connection())

        assert adapter.build_post_load_maintenance_phase() is None
        assert adapter.get_post_load_maintenance_metadata() == {"total_apply_seconds": 0.0, "applied_tables": []}

    def test_the_phase_reports_success_after_clean_hooks(self):
        adapter = _Adapter(lambda *args: None)

        adapter.run_post_load_tunings("lineitem", CONFIG, _Connection())

        phase = adapter.build_post_load_maintenance_phase()
        assert (phase.status, phase.tables_processed) == ("SUCCESS", 1)

    def test_the_phase_reports_failure_when_a_hook_raised(self):
        def hook(table, config, connection):
            raise RuntimeError("boom")

        adapter = _Adapter(hook)

        adapter.run_post_load_tunings("lineitem", CONFIG, _Connection())

        phase = adapter.build_post_load_maintenance_phase()
        assert (phase.status, phase.tables_processed) == ("FAILED", 1)

    def test_no_effective_configuration_skips_the_hook(self):
        calls = []
        adapter = _Adapter(lambda *args: calls.append(args))

        adapter.run_post_load_tunings("lineitem", None, _Connection())

        assert calls == []

    def test_dry_run_skips_the_hook(self):
        calls = []
        adapter = _Adapter(lambda *args: calls.append(args))
        adapter.dry_run_mode = True

        adapter.run_post_load_tunings("lineitem", CONFIG, _Connection())

        assert calls == []

    def test_hook_runs_with_the_raw_connection_when_the_adapter_records_its_own_operations(self):
        seen = []
        adapter = _Adapter(lambda table, config, connection: seen.append(connection))
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        adapter.post_load_connection_recording = False
        connection = _Connection()

        adapter.run_post_load_tunings("lineitem", CONFIG, connection)

        assert seen == [connection]


class TestPostLoadTiming:
    def test_time_is_accumulated_per_run_and_reset_between_runs(self):
        adapter = _Adapter(lambda *args: None)
        adapter.run_post_load_tunings("lineitem", CONFIG, _Connection())
        adapter.run_post_load_tunings("orders", CONFIG, _Connection())

        metadata = adapter.get_post_load_maintenance_metadata()
        assert metadata["applied_tables"] == ["lineitem", "orders"]
        assert metadata["total_apply_seconds"] > 0

        adapter._reset_run_scoped_state()

        assert adapter.get_post_load_maintenance_metadata() == {"total_apply_seconds": 0.0, "applied_tables": []}

    def test_maintenance_time_is_excluded_from_the_reported_load_time(self):
        adapter = _Adapter()
        adapter._post_load_maintenance_seconds = 3.0
        adapter._post_load_maintenance_by_table = {"lineitem": 2.0, "orders": 1.0}
        per_table = {"LINEITEM": {"total_ms": 5000.0}, "orders": {"total_ms": 1500.0}, "region": {"total_ms": 10.0}}

        loading_time, adjusted = adapter.exclude_post_load_maintenance(10.0, per_table)

        assert loading_time == 7.0
        assert adjusted == {
            "LINEITEM": {"total_ms": 3000.0},
            "orders": {"total_ms": 500.0},
            "region": {"total_ms": 10.0},
        }

    def test_exclusion_never_goes_negative(self):
        adapter = _Adapter()
        adapter._post_load_maintenance_seconds = 3.0

        assert adapter.exclude_post_load_maintenance(1.0, None) == (0.0, None)

    def test_load_time_is_unchanged_when_there_was_no_maintenance(self):
        assert _Adapter().exclude_post_load_maintenance(4.5, {"t": {"total_ms": 100.0}}) == (
            4.5,
            {"t": {"total_ms": 100.0}},
        )

    def test_execution_metadata_carries_the_maintenance_block_separately_from_loading(self):
        adapter = _Adapter(lambda *args: None)
        adapter.run_post_load_tunings("lineitem", CONFIG, _Connection())

        metadata, _, _ = adapter._build_execution_metadata({"benchmark_name": "tpch"})

        assert metadata["post_load_maintenance"]["applied_tables"] == ["lineitem"]
        assert metadata["post_load_maintenance"]["total_apply_seconds"] > 0


class TestEveryLoadPathRunsTheSeam:
    def test_each_ctas_sort_call_is_followed_by_the_post_load_call_with_the_same_arguments(self):
        offenders = []
        checked = 0
        for path in sorted(PLATFORMS_ROOT.rglob("*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for number, line in enumerate(lines):
                match = re.match(r"^\s*(?:self|self\.adapter)\.apply_ctas_sort\((?P<args>.*)\)\s*$", line)
                if not match:
                    continue
                checked += 1
                following = lines[number + 1].strip() if number + 1 < len(lines) else ""
                receiver = "self.adapter" if "self.adapter." in line else "self"
                if following != f"{receiver}.run_post_load_tunings({match.group('args')})":
                    offenders.append(f"{path.relative_to(PLATFORMS_ROOT)}:{number + 1}")
        assert checked == 13
        assert offenders == []


@pytest.fixture
def databricks_adapter():
    with patch("benchbox.platforms.databricks.adapter.databricks_sql"):
        yield DatabricksAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
        )


def _table_tuning() -> TableTuning:
    return TableTuning(
        table_name="LINEITEM",
        clustering=[TuningColumn(name="L_ORDERKEY", type="INTEGER", order=1)],
    )


def _databricks_connection(provider: str = "delta"):
    cursor = Mock()
    cursor.fetchall.return_value = [("Provider", provider)]
    connection = Mock()
    connection.cursor.return_value = cursor
    return connection, cursor


class TestDatabricksPostLoad:
    def test_apply_table_tunings_runs_no_delta_maintenance(self, databricks_adapter):
        connection, cursor = _databricks_connection()

        databricks_adapter.apply_table_tunings(_table_tuning(), connection)

        executed = [str(call.args[0]) for call in cursor.execute.call_args_list]
        assert not any(
            sql.startswith(("OPTIMIZE lineitem\n", "ANALYZE")) or sql == "OPTIMIZE lineitem" for sql in executed
        )
        assert "optimize" not in [op["mechanism"] for op in databricks_adapter._applied_layout_operations]
        assert "analyze" not in [op["mechanism"] for op in databricks_adapter._applied_layout_operations]

    def test_post_load_runs_optimize_and_analyze_on_the_physical_table(self, databricks_adapter):
        connection, cursor = _databricks_connection()
        config = UnifiedTuningConfiguration()
        config.table_tunings["LINEITEM"] = _table_tuning()

        databricks_adapter.run_post_load_tunings("LINEITEM", config, connection)

        executed = [str(call.args[0]) for call in cursor.execute.call_args_list]
        assert "OPTIMIZE lineitem" in executed
        assert "ANALYZE TABLE lineitem COMPUTE STATISTICS" in executed
        operations = databricks_adapter._applied_layout_operations
        assert [(op["mechanism"], op["phase"]) for op in operations] == [
            ("optimize", "post_load"),
            ("analyze", "post_load"),
        ]

    def test_post_load_is_skipped_for_a_non_delta_table(self, databricks_adapter):
        connection, cursor = _databricks_connection(provider="parquet")
        config = UnifiedTuningConfiguration()
        config.table_tunings["LINEITEM"] = _table_tuning()

        databricks_adapter.run_post_load_tunings("LINEITEM", config, connection)

        executed = [str(call.args[0]) for call in cursor.execute.call_args_list]
        assert not any(sql.startswith(("OPTIMIZE", "ANALYZE")) for sql in executed)

    def test_post_load_is_skipped_for_an_untuned_table(self, databricks_adapter):
        connection, cursor = _databricks_connection()

        databricks_adapter.run_post_load_tunings("ORDERS", UnifiedTuningConfiguration(), connection)

        cursor.execute.assert_not_called()


class TestDatabricksLoadPathOptimizesOnce:
    def _load(self, adapter, *, tuned: bool):
        from tests.unit.platforms.csv_dialect_test_helpers import resolver_data_source

        config = UnifiedTuningConfiguration()
        if tuned:
            config.table_tunings["LINEITEM"] = _table_tuning()
        adapter.catalog = "main"
        adapter.schema = "bench"
        adapter.get_effective_tuning_configuration = Mock(return_value=config)
        cursor = Mock()
        cursor.fetchone.return_value = (10,)
        cursor.fetchall.return_value = [("Provider", "delta")]
        connection = Mock()
        connection.cursor.return_value = cursor
        file_path = Path("lineitem.csv")
        adapter._load_single_table(
            cursor=cursor,
            connection=connection,
            benchmark=None,
            table_name="lineitem",
            file_path=file_path,
            stage_root="dbfs:/Volumes/main/bench",
            existing_tables={"lineitem"},
            data_source=resolver_data_source(
                "lineitem", file_path, {"csv_delimiter": ",", "csv_has_header": False, "csv_null_marker": ""}
            ),
        )
        return [str(call.args[0]) for call in cursor.execute.call_args_list]

    def test_a_tuned_table_is_optimized_once_after_load(self, databricks_adapter):
        executed = self._load(databricks_adapter, tuned=True)

        assert [sql for sql in executed if sql.upper().startswith("OPTIMIZE")] == ["OPTIMIZE lineitem"]
        assert [op["mechanism"] for op in databricks_adapter._applied_layout_operations] == ["optimize", "analyze"]

    def test_the_skip_marker_does_not_outlive_the_load(self, databricks_adapter):
        self._load(databricks_adapter, tuned=True)
        executed = self._load(databricks_adapter, tuned=False)

        assert [sql for sql in executed if sql.upper().startswith("OPTIMIZE")] == ["OPTIMIZE LINEITEM"]

    def test_an_untuned_table_keeps_the_load_time_optimize(self, databricks_adapter):
        executed = self._load(databricks_adapter, tuned=False)

        assert [sql for sql in executed if sql.upper().startswith("OPTIMIZE")] == ["OPTIMIZE LINEITEM"]


class TestCaughtMaintenanceErrorsFailThePhase:
    def test_databricks_optimize_error(self, databricks_adapter):
        connection, cursor = _databricks_connection()

        def fail_optimize(statement, *args, **kwargs):
            if str(statement).startswith("OPTIMIZE"):
                raise RuntimeError("optimize refused")

        cursor.execute.side_effect = fail_optimize
        config = UnifiedTuningConfiguration()
        config.table_tunings["LINEITEM"] = _table_tuning()

        databricks_adapter.run_post_load_tunings("LINEITEM", config, connection)

        assert databricks_adapter.build_post_load_maintenance_phase().status == "FAILED"

    @pytest.mark.usefixtures("chdb_probe_satisfied")
    def test_clickhouse_optimize_error(self):
        from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter

        with patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])):
            adapter = ClickHouseLocalAdapter.from_config({"optimize_after_load": True})
        adapter.tuning_enabled = True

        class Refusing(_Connection):
            def execute(self, statement: str, *args, **kwargs):
                if statement.startswith("OPTIMIZE"):
                    raise RuntimeError("engine refused OPTIMIZE")
                return super().execute(statement, *args, **kwargs)

        adapter.run_post_load_tunings("lineitem", CONFIG, Refusing())

        phase = adapter.build_post_load_maintenance_phase()
        assert phase.status == "FAILED"
        assert phase.tables_processed == 1

    def test_the_phase_stays_successful_when_every_statement_works(self, databricks_adapter):
        connection, _ = _databricks_connection()
        config = UnifiedTuningConfiguration()
        config.table_tunings["LINEITEM"] = _table_tuning()

        databricks_adapter.run_post_load_tunings("LINEITEM", config, connection)

        assert databricks_adapter.build_post_load_maintenance_phase().status == "SUCCESS"


class TestClickHouseOptionIsDeclared:
    @pytest.mark.parametrize("platform", ["clickhouse-local", "clickhouse-server", "clickhouse-cloud"])
    def test_optimize_after_load_defaults_off_and_can_be_enabled(self, platform):
        assert PlatformHookRegistry.parse_options(platform, [])["optimize_after_load"] is False
        assert (
            PlatformHookRegistry.parse_options(platform, [("optimize_after_load", "true")])["optimize_after_load"]
            is True
        )


class TestClickHouseOptionReachesTheAdapter:
    @pytest.mark.usefixtures("chdb_probe_satisfied")
    @pytest.mark.parametrize("adapter_name", ["ClickHouseLocalAdapter", "ClickHouseServerAdapter"])
    def test_from_config_carries_optimize_after_load(self, adapter_name):
        import importlib

        module = importlib.import_module(
            "benchbox.platforms.clickhouse_local" if "Local" in adapter_name else "benchbox.platforms.clickhouse_server"
        )
        adapter_cls = getattr(module, adapter_name)
        with patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])):
            enabled = adapter_cls.from_config({"benchmark": "tpch", "scale_factor": 0.01, "optimize_after_load": True})
            default = adapter_cls.from_config({"benchmark": "tpch", "scale_factor": 0.01})

        assert enabled._optimize_after_load_enabled() is True
        assert default._optimize_after_load_enabled() is False

    def test_cloud_from_config_carries_optimize_after_load(self):
        from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

        with patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])):
            base = {"host": "h", "password": "p"}
            enabled = ClickHouseCloudAdapter.from_config({**base, "optimize_after_load": True})
            default = ClickHouseCloudAdapter.from_config(base)

        assert enabled._optimize_after_load_enabled() is True
        assert default._optimize_after_load_enabled() is False

    @pytest.mark.usefixtures("chdb_probe_satisfied")
    @pytest.mark.parametrize("adapter_name", ["ClickHouseLocalAdapter", "ClickHouseCloudAdapter"])
    def test_optimize_after_load_does_nothing_when_tuning_is_off(self, adapter_name):
        import importlib

        module = importlib.import_module(
            "benchbox.platforms.clickhouse_local" if "Local" in adapter_name else "benchbox.platforms.clickhouse_cloud"
        )
        config = {"host": "h", "password": "p"} if "Cloud" in adapter_name else {}
        with patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])):
            adapter = getattr(module, adapter_name).from_config({**config, "optimize_after_load": True})
        connection = _Connection()

        assert adapter.apply_post_load_tunings("lineitem", CONFIG, connection) is False
        assert connection.statements == []

        adapter.tuning_enabled = True
        assert adapter.apply_post_load_tunings("lineitem", CONFIG, connection) is True
        assert [s for s in connection.statements if "system." not in s] == ["OPTIMIZE TABLE lineitem FINAL"]
