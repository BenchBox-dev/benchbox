from __future__ import annotations

from unittest.mock import Mock

import pytest

import benchbox.platforms.postgresql as postgresql_module
from benchbox.core.tuning.applied_ledger import PHASE_POST_LOAD, AppliedTuningLedger
from benchbox.core.tuning.ddl_generator import get_ddl_generator
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.platforms.base.sql_execution import execute_sql_query
from benchbox.platforms.cedardb import CedarDBAdapter
from benchbox.platforms.citus import CitusAdapter
from benchbox.platforms.paradedb import ParadeDBAdapter
from benchbox.platforms.pg_duckdb import PgDuckDBAdapter
from benchbox.platforms.pg_mooncake import PgMooncakeAdapter
from benchbox.platforms.postgresql import PostgreSQLAdapter
from benchbox.platforms.timescaledb import TimescaleDBAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _columns(*names: str) -> list[TuningColumn]:
    return [TuningColumn(name=name, type="DATE", order=order) for order, name in enumerate(names, start=1)]


def _config(**slots: list[TuningColumn]) -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    config.table_tunings["LINEITEM"] = TableTuning(table_name="LINEITEM", **slots)
    return config


@pytest.fixture
def adapter(monkeypatch) -> PostgreSQLAdapter:
    monkeypatch.setattr(postgresql_module, "psycopg", Mock(__version__="3.1.0"))
    instance = PostgreSQLAdapter(tuning_enabled=True)
    instance._applied_tuning_ledger = AppliedTuningLedger()
    return instance


class _FakeCursor:
    def __init__(self, executed: list[str]):
        self._executed = executed

    def execute(self, statement, *args, **kwargs):
        self._executed.append(str(statement))

    def close(self):
        return None


class _FakeConnection:
    def __init__(self):
        self.executed: list[str] = []
        self.commits = 0

    def cursor(self):
        return _FakeCursor(self.executed)

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None


def test_adapter_statements_equal_generator_output(adapter):
    tuning = _config(clustering=_columns("L_SHIPDATE"), sorting=_columns("L_ORDERKEY")).table_tunings["LINEITEM"]
    generator = get_ddl_generator("postgresql")

    expected = generator.get_post_load_statements("lineitem", generator.generate_tuning_clauses(tuning))

    assert adapter.postgres_layout_statements("lineitem", tuning) == expected
    assert expected == [
        "CREATE INDEX IF NOT EXISTS idx_lineitem_cluster ON lineitem (L_SHIPDATE, L_ORDERKEY)",
        "CLUSTER lineitem USING idx_lineitem_cluster",
    ]


def test_non_public_schema_is_qualified(adapter):
    adapter.schema = "bench"
    tuning = _config(sorting=_columns("L_SHIPDATE")).table_tunings["LINEITEM"]

    statements = adapter.postgres_layout_statements("lineitem", tuning)

    assert statements[0].endswith("ON bench.lineitem (L_SHIPDATE)")
    assert statements[1] == "CLUSTER bench.lineitem USING idx_lineitem_cluster"


def test_partitioning_and_untuned_tables_add_no_statements(adapter):
    assert adapter.postgres_layout_statements("lineitem", None) == []
    partitioned = _config(partitioning=_columns("L_SHIPDATE")).table_tunings["LINEITEM"]
    assert adapter.postgres_layout_statements("lineitem", partitioned) == []


def test_post_load_executes_and_records_the_statements(adapter):
    config = _config(clustering=_columns("L_SHIPDATE"))
    connection = _FakeConnection()

    adapter.run_post_load_tunings("lineitem", config, connection)

    assert connection.executed == [
        "CREATE INDEX IF NOT EXISTS idx_lineitem_cluster ON lineitem (L_SHIPDATE)",
        "CLUSTER lineitem USING idx_lineitem_cluster",
    ]
    assert connection.commits == 1
    recorded = [(item.statement, item.phase, item.status) for item in adapter._applied_tuning_ledger.statements]
    assert recorded == [
        ("CREATE INDEX IF NOT EXISTS idx_lineitem_cluster ON lineitem (L_SHIPDATE)", PHASE_POST_LOAD, "executed"),
        ("CLUSTER lineitem USING idx_lineitem_cluster", PHASE_POST_LOAD, "executed"),
    ]


def test_recorded_index_satisfies_reconciliation(adapter):
    config = _config(clustering=_columns("L_SHIPDATE"))
    adapter.run_post_load_tunings("lineitem", config, _FakeConnection())

    adapter._reconcile_requested_tuning(config)

    assert adapter._applied_tuning_ledger.dropped == []


def test_clustered_sort_skips_the_sorted_ingestion_drop(adapter):
    config = _config(sorting=_columns("L_SHIPDATE"))

    assert adapter.apply_ctas_sort("lineitem", config, _FakeConnection()) is False
    assert adapter._applied_tuning_ledger.dropped == []


@pytest.mark.parametrize(
    "adapter_class",
    [CedarDBAdapter, CitusAdapter, ParadeDBAdapter, PgDuckDBAdapter, PgMooncakeAdapter, TimescaleDBAdapter],
)
def test_family_variants_do_not_cluster(adapter_class):
    tuning = _config(clustering=_columns("L_SHIPDATE")).table_tunings["LINEITEM"]

    assert adapter_class.post_load_index_cluster_supported is False
    assert adapter_class.postgres_layout_statements(adapter_class.__new__(adapter_class), "lineitem", tuning) == []


def test_statements_without_rows_return_no_rows():
    cursor = Mock(description=None)
    connection = Mock()
    connection.cursor.return_value = cursor
    build = Mock(return_value={"status": "SUCCESS"})

    execute_sql_query(
        connection, "CREATE TABLE t (x INT)", "metadata", log_verbose=Mock(), build_query_result_with_validation=build
    )

    cursor.fetchall.assert_not_called()
    assert build.call_args.kwargs["actual_row_count"] == 0
