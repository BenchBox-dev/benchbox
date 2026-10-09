from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import duckdb
import pytest

import benchbox.platforms.postgresql as postgresql_module
from benchbox.cli.config import ConfigManager
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tuning.applied_ledger import (
    APPLIED_UNVERIFIED,
    APPLIED_VERIFIED,
    NOOP,
    PHASE_DDL,
    PHASE_POST_LOAD,
    AppliedTuningLedger,
)
from benchbox.core.tuning.capability_registry import get_capability
from benchbox.core.tuning.interface import TableTuning, TuningColumn, TuningType, UnifiedTuningConfiguration
from benchbox.core.tuning.reconciliation import NOT_RENDERED_REASON, RECONCILIATION_FAILED_INTENT
from benchbox.platforms.base import PlatformAdapter, tuning_trust
from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter
from benchbox.platforms.datafusion import DataFusionAdapter
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.platforms.postgresql import PostgreSQLAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "tuning"
DUCKDB_SORT_ONLY = FIXTURES / "duckdb_sort_only.yaml"
CHDB_SORT_PARTITION = FIXTURES / "clickhouse_sort_partition.yaml"


class _Adapter(PlatformAdapter):
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


def _columns(*names: str) -> list[TuningColumn]:
    return [TuningColumn(name=name, type="INTEGER", order=order) for order, name in enumerate(names, start=1)]


def _layout_config(constraints: bool = False, **slots: list[TuningColumn]) -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    if not constraints:
        config.disable_all_constraints()
    config.table_tunings["LINEITEM"] = TableTuning(table_name="LINEITEM", **slots)
    return config


def _tuned(adapter: PlatformAdapter) -> PlatformAdapter:
    adapter.tuning_enabled = True
    adapter._applied_tuning_ledger = AppliedTuningLedger()
    adapter._applied_layout_operations = []
    return adapter


def _drops(adapter: PlatformAdapter) -> dict[str, str]:
    return {item.intent: item.reason for item in adapter._applied_tuning_ledger.dropped}


def _registry_notes(platform: str, tuning_type: TuningType) -> str:
    capability = get_capability(platform, tuning_type)
    assert capability is not None and capability.rendered_via == "none"
    return capability.notes


def _status(adapter: PlatformAdapter) -> str:
    return adapter._applied_tuning_ledger.overall_status(tuning_enabled=True, has_config=True)


class FakeMetadataManager:
    marker_save_failed = False

    def __init__(self, adapter, **_kwargs):
        pass

    def write_tuned_run_marker(self):
        return True

    def save_unified_tunings(self, config):
        return True


class TestFamilies:
    def test_postgres_no_op_apply_drops_layout_with_registry_notes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(postgresql_module, "psycopg", Mock(__version__="3.1.0"))
        monkeypatch.setattr("benchbox.core.tuning.metadata.TuningMetadataManager", FakeMetadataManager)
        config = _layout_config(partitioning=_columns("L_SHIPDATE"), clustering=_columns("L_ORDERKEY"))
        adapter = _tuned(PostgreSQLAdapter(tuning_enabled=True, unified_tuning_configuration=config))
        adapter.create_schema = Mock(return_value=0.0)
        adapter.load_data = Mock(return_value=({"lineitem": 1}, 0.0, {}))
        adapter._create_enhanced_schema_creation_phase = Mock()
        adapter._create_enhanced_data_loading_phase = Mock()
        benchmark = Mock(output_dir=tmp_path)

        adapter._setup_fresh_database_phases(benchmark, Mock(), config)
        assert adapter._applied_tuning_ledger.dropped == []
        adapter._reconcile_requested_tuning(config)

        assert _drops(adapter) == {
            "partitioning:LINEITEM (L_SHIPDATE)": _registry_notes("postgresql", TuningType.PARTITIONING),
            "clustering:LINEITEM (L_ORDERKEY)": _registry_notes("postgresql", TuningType.CLUSTERING),
        }
        assert _status(adapter) == NOOP

    @pytest.mark.parametrize("platform", ["cedardb", "citus", "paradedb", "pg-duckdb", "pg-mooncake", "timescaledb"])
    def test_postgres_family_variants_drop_every_layout_intent(self, platform):
        adapter = _tuned(_Adapter(type=platform))

        adapter._reconcile_requested_tuning(_layout_config(partitioning=_columns("L_SHIPDATE")))

        [reason] = _drops(adapter).values()
        capability = get_capability(platform, TuningType.PARTITIONING)
        assert reason == (capability.notes if capability is not None else NOT_RENDERED_REASON)

    def test_postgres_constraints_rendered_in_create_table_are_satisfied(self):
        adapter = _tuned(_Adapter(type="postgresql"))
        adapter._applied_tuning_ledger.record(
            "CREATE TABLE nation (n_nationkey INTEGER, PRIMARY KEY (n_nationkey))", PHASE_DDL
        )
        config = UnifiedTuningConfiguration()
        config.disable_all_constraints()
        config.primary_keys.enabled = True
        config.foreign_keys.enabled = True

        adapter._reconcile_requested_tuning(config)

        assert [(item.intent, item.satisfied_by) for item in adapter._applied_tuning_ledger.satisfied] == [
            ("primary_keys", 0)
        ]
        assert _drops(adapter) == {"foreign_keys": NOT_RENDERED_REASON}

    def test_bigquery_inspect_only_drops_partitioning_and_clustering_with_registry_notes(self):
        adapter = _tuned(_Adapter(type="bigquery"))

        adapter._reconcile_requested_tuning(
            _layout_config(partitioning=_columns("L_SHIPDATE"), clustering=_columns("L_ORDERKEY"))
        )

        assert _drops(adapter) == {
            "partitioning:LINEITEM (L_SHIPDATE)": _registry_notes("bigquery", TuningType.PARTITIONING),
            "clustering:LINEITEM (L_ORDERKEY)": _registry_notes("bigquery", TuningType.CLUSTERING),
        }

    def test_redshift_analyze_only_drops_distribution_and_unrendered_sort(self):
        adapter = _tuned(_Adapter(type="redshift"))
        ledger = adapter._applied_tuning_ledger
        ledger.record("ANALYZE lineitem", PHASE_POST_LOAD)
        ledger.record("VACUUM lineitem", PHASE_POST_LOAD)

        adapter._reconcile_requested_tuning(
            _layout_config(distribution=_columns("L_ORDERKEY"), sorting=_columns("L_SHIPDATE"))
        )

        assert _drops(adapter) == {
            "distribution:LINEITEM (L_ORDERKEY)": _registry_notes("redshift", TuningType.DISTRIBUTION),
            "sorting:LINEITEM (L_SHIPDATE)": NOT_RENDERED_REASON,
        }
        assert _status(adapter) == APPLIED_UNVERIFIED

    @pytest.mark.parametrize(
        "platform",
        ["emr-serverless", "dataproc", "dataproc-serverless", "glue", "athena-spark", "fabric-spark", "synapse-spark"],
    )
    def test_managed_spark_drops_every_intent_as_not_rendered(self, platform):
        adapter = _tuned(_Adapter(type=platform))

        adapter._reconcile_requested_tuning(
            _layout_config(partitioning=_columns("L_SHIPDATE"), sorting=_columns("L_ORDERKEY"))
        )

        assert _drops(adapter) == {
            "partitioning:LINEITEM (L_SHIPDATE)": NOT_RENDERED_REASON,
            "sorting:LINEITEM (L_ORDERKEY)": NOT_RENDERED_REASON,
        }

    def test_datafusion_requested_constraints_are_dropped_not_noop_with_empty_drops(self, tmp_path):
        adapter = _tuned(DataFusionAdapter(type="datafusion"))
        config = UnifiedTuningConfiguration()
        adapter.unified_tuning_configuration = config
        adapter.benchmark = TPCHBenchmark(scale_factor=0.01, output_dir=tmp_path)
        connection = adapter.create_connection()
        adapter.create_schema(adapter.benchmark, connection)
        adapter.apply_unified_tuning(config, connection)

        adapter._reconcile_requested_tuning(config)

        assert _drops(adapter) == {
            "primary_keys": NOT_RENDERED_REASON,
            "foreign_keys": NOT_RENDERED_REASON,
        }
        assert adapter._applied_tuning_ledger.statements == []
        assert _status(adapter) == NOOP


class TestGuards:
    def test_duckdb_sort_only_custom_config_still_reaches_applied_verified(self, tmp_path):
        connection = duckdb.connect(":memory:")
        adapter = _tuned(DuckDBAdapter(type="duckdb"))
        adapter.create_schema(TPCHBenchmark(scale_factor=0.01, output_dir=tmp_path), connection)
        config = ConfigManager().load_unified_tuning_config(DUCKDB_SORT_ONLY, platform="duckdb")
        adapter.unified_tuning_configuration = config

        adapter.apply_unified_tuning(config, connection)
        for table in ("lineitem", "orders"):
            adapter.apply_ctas_sort(table, config, connection)
        adapter._fold_layout_operations_into_ledger()
        adapter._reconcile_requested_tuning(config)
        status, receipt = adapter._corroborate_applied_ledger(connection, _status(adapter))

        assert adapter._applied_tuning_ledger.dropped == []
        assert adapter._applied_tuning_ledger.satisfied == []
        assert status == APPLIED_VERIFIED
        assert receipt is not None and receipt["corroborated"] is True

    def test_chdb_custom_config_gains_no_drops_and_still_verifies(self, tmp_path):
        with patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])):
            adapter = _tuned(ClickHouseLocalAdapter(type="chdb"))
        adapter.unified_tuning_configuration = ConfigManager().load_unified_tuning_config(
            CHDB_SORT_PARTITION, platform="chdb"
        )
        config = adapter.get_effective_tuning_configuration()
        client = _RecordingClient()

        adapter.create_schema(TPCHBenchmark(scale_factor=0.01, output_dir=tmp_path), client)
        for table in ("lineitem", "orders"):
            adapter.apply_ctas_sort(table, config, client)
        adapter._fold_layout_operations_into_ledger()
        adapter._reconcile_requested_tuning(config)
        catalog = _RecordingClient(
            [
                ("lineitem", "l_orderkey, l_linenumber", "toYYYYMM(l_shipdate)"),
                ("orders", "o_orderkey, o_orderdate", ""),
            ]
        )
        status, receipt = adapter._corroborate_applied_ledger(catalog, _status(adapter))

        ledger = adapter._applied_tuning_ledger
        assert ledger.dropped == []
        assert {item.intent for item in ledger.satisfied} >= {"partitioning:LINEITEM (L_SHIPDATE)"}
        assert status == APPLIED_VERIFIED
        assert receipt is not None and receipt["corroborated"] is True


class _RecordingClient:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.rows = rows or []
        self.statements: list[str] = []

    def execute(self, statement: str, *args: Any, **kwargs: Any) -> list[tuple]:
        self.statements.append(statement)
        return self.rows


class TestSeam:
    def _adapter(self, **config: Any) -> PlatformAdapter:
        return _tuned(_Adapter(type="datafusion", **config))

    def test_reused_database_is_not_reconciled(self):
        adapter = self._adapter()
        adapter.database_was_reused = True

        adapter._reconcile_requested_tuning(_layout_config(sorting=_columns("L_ORDERKEY")))

        assert adapter._applied_tuning_ledger.dropped == []

    def test_dry_run_is_not_reconciled(self):
        adapter = self._adapter()
        adapter.dry_run_mode = True

        adapter._reconcile_requested_tuning(_layout_config(sorting=_columns("L_ORDERKEY")))

        assert adapter._applied_tuning_ledger.dropped == []

    def test_untuned_run_is_not_reconciled(self):
        adapter = self._adapter()
        adapter.tuning_enabled = False

        adapter._reconcile_requested_tuning(_layout_config(sorting=_columns("L_ORDERKEY")))

        assert adapter._applied_tuning_ledger.dropped == []

    def test_constraint_toggles_follow_what_the_run_benchmark_declares(self, tmp_path):
        from benchbox.core.ssb.benchmark import SSBBenchmark

        adapter = self._adapter()
        adapter.benchmark = SSBBenchmark(scale_factor=0.01, output_dir=tmp_path)

        adapter._reconcile_requested_tuning(UnifiedTuningConfiguration())

        assert _drops(adapter) == {"primary_keys": NOT_RENDERED_REASON}

    def test_reconciliation_error_fails_closed_with_a_blocking_drop(self, monkeypatch):
        adapter = self._adapter()

        def explode(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("registry unavailable")

        monkeypatch.setattr(tuning_trust, "reconcile_requested_intents", explode)
        adapter._reconcile_requested_tuning(_layout_config(sorting=_columns("L_ORDERKEY")))

        assert _drops(adapter) == {RECONCILIATION_FAILED_INTENT: "reconciliation failed: registry unavailable"}

    def test_each_new_drop_is_logged_as_a_warning(self, caplog):
        adapter = self._adapter()
        adapter._applied_tuning_ledger.record_dropped("partitioning:ORDERS", "earlier")

        with caplog.at_level(logging.WARNING):
            adapter._reconcile_requested_tuning(_layout_config(sorting=_columns("L_ORDERKEY")))

        messages = [record.getMessage() for record in caplog.records if "not applied" in record.getMessage()]
        assert messages == [
            f"Requested tuning intent not applied: sorting:LINEITEM (L_ORDERKEY) ({NOT_RENDERED_REASON})"
        ]
