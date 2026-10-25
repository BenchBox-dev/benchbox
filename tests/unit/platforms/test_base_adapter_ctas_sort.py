from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock, call

import pytest

from benchbox.core.tuning.applied_ledger import STATEMENT_FAILED, AppliedTuningLedger
from benchbox.platforms.base import PlatformAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _BaseCtasTestAdapter(PlatformAdapter):
    def __init__(self, ctas_sql: str | list[str] | None = "SELECT 1", **config: Any):
        super().__init__(**config)
        self._ctas_sql = ctas_sql

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

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        return {}, 0.0, None

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        return None

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        return {"query_id": query_id, "status": "SUCCESS", "execution_time_seconds": 0.0, "rows_returned": 0}

    def _build_ctas_sort_sql(self, table_name: str, sort_columns: list[Any]) -> str | list[str] | None:
        return self._ctas_sql


def _make_tuning_config(table_tunings: dict[str, Any]) -> Mock:
    config = Mock()
    config.table_tunings = table_tunings
    return config


def _make_table_tuning_with_sort(sort_columns: list[str]) -> Mock:
    from benchbox.core.tuning.interface import TuningType

    table_tuning = Mock()

    cols = []
    for i, col_name in enumerate(sort_columns, start=1):
        col = Mock()
        col.name = col_name
        col.order = i
        cols.append(col)

    def get_columns_by_type(tuning_type):
        if tuning_type == TuningType.SORTING:
            return cols
        return []

    table_tuning.get_columns_by_type.side_effect = get_columns_by_type
    return table_tuning


class TestPlatformAdapterApplyCtasSort:
    def setup_method(self):
        self.adapter = _BaseCtasTestAdapter(ctas_sql="CREATE OR REPLACE TABLE lineitem AS SELECT * FROM lineitem")
        self.connection = Mock()

    def test_returns_false_when_table_not_in_tuning_config(self):
        tuning_config = _make_tuning_config({})

        assert self.adapter.apply_ctas_sort("lineitem", tuning_config, self.connection) is False
        self.connection.execute.assert_not_called()

    def test_returns_false_when_no_sort_columns(self):
        table_tuning = Mock()
        table_tuning.get_columns_by_type.return_value = []
        tuning_config = _make_tuning_config({"lineitem": table_tuning})

        assert self.adapter.apply_ctas_sort("lineitem", tuning_config, self.connection) is False
        self.connection.execute.assert_not_called()

    def test_executes_platform_ctas_sql(self):
        table_tuning = _make_table_tuning_with_sort(["l_shipdate", "l_orderkey"])
        tuning_config = _make_tuning_config({"lineitem": table_tuning})

        assert self.adapter.apply_ctas_sort("lineitem", tuning_config, self.connection) is True
        self.connection.execute.assert_called_once_with("CREATE OR REPLACE TABLE lineitem AS SELECT * FROM lineitem")
        metadata = self.adapter.get_sorted_ingestion_metadata()
        assert metadata["applied_table_count"] == 1
        assert metadata["applied_tables"] == ["lineitem"]

    def test_case_insensitive_table_lookup(self):
        table_tuning = _make_table_tuning_with_sort(["l_shipdate"])
        tuning_config = _make_tuning_config({"LINEITEM": table_tuning})

        assert self.adapter.apply_ctas_sort("lineitem", tuning_config, self.connection) is True

    def test_dry_run_captures_sql_without_execution(self):
        table_tuning = _make_table_tuning_with_sort(["l_shipdate"])
        tuning_config = _make_tuning_config({"lineitem": table_tuning})
        self.adapter.enable_dry_run()

        assert self.adapter.apply_ctas_sort("lineitem", tuning_config, self.connection) is True
        self.connection.execute.assert_not_called()
        assert any(entry.get("operation_type") == "ctas_sort" for entry in self.adapter.captured_sql)
        metadata = self.adapter.get_sorted_ingestion_metadata()
        assert metadata["applied_table_count"] == 1

    def test_returns_false_when_platform_hook_returns_none(self):
        adapter = _BaseCtasTestAdapter(ctas_sql=None)
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        tuning_config = _make_tuning_config({"lineitem": _make_table_tuning_with_sort(["l_shipdate"])})

        assert adapter.apply_ctas_sort("lineitem", tuning_config, self.connection) is False
        self.connection.execute.assert_not_called()
        assert adapter._applied_tuning_ledger.dropped[0].intent == "sorted_ingestion lineitem ORDER BY l_shipdate"

    def test_executes_statements_via_cursor_when_connection_has_no_execute(self):
        adapter = _BaseCtasTestAdapter(ctas_sql=["SELECT 1", "SELECT 2"])
        tuning_config = _make_tuning_config({"lineitem": _make_table_tuning_with_sort(["l_shipdate"])})

        cursor = Mock()
        connection = Mock(spec=["cursor"])
        connection.cursor.return_value = cursor

        assert adapter.apply_ctas_sort("lineitem", tuning_config, connection) is True
        assert cursor.execute.call_count == 2
        cursor.execute.assert_any_call("SELECT 1")
        cursor.execute.assert_any_call("SELECT 2")
        assert cursor.close.call_count == 2


class TestDataLoaderTuningConfig:
    def test_apply_ctas_sort_not_called_without_tuning_config(self, tmp_path):
        from benchbox.platforms.base.data_loading import DataLoader

        csv_file = tmp_path / "orders.csv"
        csv_file.write_text("1,Alice\n2,Bob\n")

        adapter = Mock()
        adapter.verbose_enabled = False
        adapter.logger = Mock()
        adapter.dry_run_mode = False
        adapter.apply_ctas_sort = Mock()

        benchmark = Mock()
        del benchmark.get_table_loading_order

        loader = DataLoader(
            adapter=adapter,
            benchmark=benchmark,
            connection=Mock(),
            data_dir=tmp_path,
            tuning_config=None,
        )
        loader._load_single_file = Mock(return_value=2)

        loader._load_file_based_data({"orders": [csv_file]})

        adapter.apply_ctas_sort.assert_not_called()

    def test_apply_ctas_sort_called_for_each_table_with_tuning_config(self, tmp_path):
        from benchbox.platforms.base.data_loading import DataLoader

        adapter = Mock()
        adapter.verbose_enabled = False
        adapter.logger = Mock()
        adapter.dry_run_mode = False
        adapter.apply_ctas_sort = Mock(return_value=True)

        mock_config = Mock()
        benchmark = Mock()
        del benchmark.get_table_loading_order

        loader = DataLoader(
            adapter=adapter,
            benchmark=benchmark,
            connection=Mock(),
            data_dir=tmp_path,
            tuning_config=mock_config,
        )
        loader._load_single_file = Mock(return_value=5)

        (tmp_path / "lineitem.csv").write_text("1|data\n")
        (tmp_path / "orders.csv").write_text("1|data\n")
        files = {
            "lineitem": [tmp_path / "lineitem.csv"],
            "orders": [tmp_path / "orders.csv"],
        }
        loader._load_file_based_data(files)

        assert adapter.apply_ctas_sort.call_count == 2


class TestMotherDuckSqlHook:
    def test_motherduck_builds_duckdb_compatible_ctas_sql(self):
        from benchbox.core.tuning.interface import TuningColumn
        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter.__new__(MotherDuckAdapter)

        col1 = TuningColumn(name="l_shipdate", type="DATE", order=1)
        col2 = TuningColumn(name="l_orderkey", type="INTEGER", order=2)

        sql = adapter._build_ctas_sort_sql("lineitem", [col1, col2])

        assert sql == ("CREATE OR REPLACE TABLE lineitem AS SELECT * FROM lineitem ORDER BY l_shipdate, l_orderkey;")


class _SortedIngestionStrategyAdapter(_BaseCtasTestAdapter):
    def __init__(self, platform_name: str, **config: Any):
        super().__init__(**config)
        self._platform_name = platform_name

    @property
    def platform_name(self) -> str:
        return self._platform_name


class TestSortedIngestionCapabilityAndGuardrails:
    def test_capability_matrix_for_cloud_platform(self):
        adapter = _SortedIngestionStrategyAdapter("Databricks")
        capability = adapter.get_sorted_ingestion_capability()

        assert capability["is_cloud_platform"] is True
        assert capability["supports_sorted_ingestion"] is True
        assert "liquid_clustering" in capability["supported_methods"]

    def test_capability_matrix_for_clickhouse_cloud_reports_unsupported(self):
        adapter = _SortedIngestionStrategyAdapter("ClickHouse Cloud")
        capability = adapter.get_sorted_ingestion_capability()

        assert capability["is_cloud_platform"] is True
        assert capability["supports_sorted_ingestion"] is False
        assert capability["supported_methods"] == []

    def test_resolve_sorted_ingestion_off_mode_short_circuits(self):
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration

        adapter = _SortedIngestionStrategyAdapter("Snowflake")
        cfg = UnifiedTuningConfiguration()
        cfg.platform_optimizations.sorted_ingestion_mode = "off"
        cfg.platform_optimizations.sorted_ingestion_method = "auto"
        adapter.unified_tuning_configuration = cfg

        assert adapter.resolve_sorted_ingestion_strategy() == ("off", "auto")

    def test_resolve_sorted_ingestion_auto_on_unsupported_cloud_platform_returns_skip(self):
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration

        adapter = _SortedIngestionStrategyAdapter("ClickHouse Cloud")
        cfg = UnifiedTuningConfiguration()
        cfg.platform_optimizations.sorted_ingestion_mode = "auto"
        cfg.platform_optimizations.sorted_ingestion_method = "auto"
        adapter.unified_tuning_configuration = cfg

        assert adapter.resolve_sorted_ingestion_strategy() == ("off", "none")

    def test_resolve_sorted_ingestion_force_on_unsupported_platform_raises(self):
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration

        adapter = _SortedIngestionStrategyAdapter("ClickHouse Cloud")
        cfg = UnifiedTuningConfiguration()
        cfg.platform_optimizations.sorted_ingestion_mode = "force"
        cfg.platform_optimizations.sorted_ingestion_method = "auto"
        adapter.unified_tuning_configuration = cfg

        with pytest.raises(ValueError, match="mode 'force' is not supported"):
            adapter.resolve_sorted_ingestion_strategy()

    def test_resolve_sorted_ingestion_rejects_unsupported_method(self):
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration

        adapter = _SortedIngestionStrategyAdapter("Snowflake")
        cfg = UnifiedTuningConfiguration()
        cfg.platform_optimizations.sorted_ingestion_mode = "force"
        cfg.platform_optimizations.sorted_ingestion_method = "liquid_clustering"
        adapter.unified_tuning_configuration = cfg

        with pytest.raises(ValueError, match="method 'liquid_clustering' is not supported"):
            adapter.resolve_sorted_ingestion_strategy()


class TestConstraintPreservingCtasSort:
    def _make_adapter_and_config(self):
        adapter = _SortedIngestionStrategyAdapter(
            "duckdb", ctas_sql="CREATE OR REPLACE TABLE lineitem AS SELECT * FROM lineitem"
        )
        config = _make_tuning_config({"lineitem": _make_table_tuning_with_sort(["l_orderkey"])})
        config.foreign_keys = Mock(enabled=False)
        return adapter, config

    def test_duckdb_uses_in_place_rewrite_without_config_constraint_gate(self):
        adapter, tuning_config = self._make_adapter_and_config()
        connection = Mock()
        metadata = Mock()
        metadata.fetchall.return_value = []
        connection.execute.return_value = metadata

        assert adapter.apply_ctas_sort("lineitem", tuning_config, connection) is True

        assert connection.execute.call_args_list[0] == call("BEGIN TRANSACTION;")
        assert connection.execute.call_args_list[2:] == [
            call('CREATE TEMP TABLE "lineitem__ctas_sort" AS SELECT * FROM "lineitem" ORDER BY "l_orderkey";'),
            call('DELETE FROM "lineitem";'),
            call('INSERT INTO "lineitem" SELECT * FROM "lineitem__ctas_sort";'),
            call('DROP TABLE "lineitem__ctas_sort";'),
        ]
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()

    def test_rewrite_rolls_back_and_records_failure_after_statement_error(self):
        adapter, tuning_config = self._make_adapter_and_config()
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        connection = Mock()
        metadata = Mock()
        metadata.fetchall.return_value = []
        connection.execute.side_effect = [None, metadata, None, None, RuntimeError("insert failed")]

        assert adapter.apply_ctas_sort("lineitem", tuning_config, connection) is False

        connection.rollback.assert_called_once_with()
        connection.commit.assert_not_called()
        statement = adapter._applied_tuning_ledger.statements[0]
        assert statement.status == STATEMENT_FAILED
        assert statement.mechanism == "sorted_ingestion"
        assert statement.table == "lineitem"
        assert statement.error == "insert failed"

    def test_rewrite_raises_when_rollback_cannot_restore_safety(self):
        adapter, tuning_config = self._make_adapter_and_config()
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        connection = Mock()
        metadata = Mock()
        metadata.fetchall.return_value = []
        connection.execute.side_effect = [None, metadata, None, None, RuntimeError("insert failed")]
        connection.rollback.side_effect = RuntimeError("rollback failed")

        with pytest.raises(RuntimeError, match="could not be rolled back"):
            adapter.apply_ctas_sort("lineitem", tuning_config, connection)

        statement = adapter._applied_tuning_ledger.statements[0]
        assert statement.status == STATEMENT_FAILED
        assert "rollback failed" in statement.error

    def test_active_caller_transaction_is_not_committed_or_rolled_back(self):
        adapter, tuning_config = self._make_adapter_and_config()
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        connection = Mock()
        connection.transaction_active = True

        assert adapter.apply_ctas_sort("lineitem", tuning_config, connection) is False

        connection.execute.assert_not_called()
        connection.commit.assert_not_called()
        connection.rollback.assert_not_called()
        assert len(adapter._applied_tuning_ledger.dropped) == 1
        assert "active transaction" in adapter._applied_tuning_ledger.dropped[0].reason

    def test_duckdb_1_0_constraint_shape_ignores_nullable_and_unrelated_rows(self):
        adapter, _tuning_config = self._make_adapter_and_config()
        connection = Mock()
        metadata = Mock()
        metadata.fetchall.return_value = [
            ("child", ["parent_id"], "FOREIGN KEY (parent_id) REFERENCES lineitem(l_orderkey)"),
            ("lineitem", ["parent_id"], ""),
            ("other_child", ["other_id"], "FOREIGN KEY (other_id) REFERENCES other(id)"),
        ]
        nullable_child = Mock()
        nullable_child.fetchone.return_value = None
        nullable_self_reference = Mock()
        nullable_self_reference.fetchone.return_value = None
        connection.execute.side_effect = [metadata, nullable_child, nullable_self_reference]

        assert adapter._has_populated_fk_dependents(connection, "lineitem") is False

        metadata_query = connection.execute.call_args_list[0].args[0]
        assert "constraint_text" in metadata_query
        assert "referenced_table" not in metadata_query
        assert "referenced_column_names" not in metadata_query
        assert connection.execute.call_args_list[1] == call(
            'SELECT 1 FROM "child" WHERE "parent_id" IS NOT NULL LIMIT 1;'
        )
        assert connection.execute.call_args_list[2] == call(
            'SELECT 1 FROM "lineitem" WHERE "parent_id" IS NOT NULL LIMIT 1;'
        )

    def test_populated_non_null_fk_dependent_skips_and_records_intent(self):
        adapter, tuning_config = self._make_adapter_and_config()
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        connection = Mock()
        metadata = Mock()
        metadata.fetchall.return_value = [
            ("orders", ["lineitem_id"], "FOREIGN KEY (lineitem_id) REFERENCES lineitem(l_orderkey)")
        ]
        dependent = Mock()
        dependent.fetchone.return_value = (1,)
        connection.execute.side_effect = [None, metadata, dependent]

        assert adapter.apply_ctas_sort("lineitem", tuning_config, connection) is False

        connection.rollback.assert_called_once_with()
        connection.commit.assert_not_called()
        assert len(adapter._applied_tuning_ledger.dropped) == 1
        assert "foreign-key dependents" in adapter._applied_tuning_ledger.dropped[0].reason
