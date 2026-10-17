from __future__ import annotations

import duckdb
import pytest

from benchbox.core.tuning.applied_ledger import PHASE_DDL, AppliedStatement, AppliedTuningLedger
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.core.tuning.introspection import statement_table
from benchbox.platforms.clickhouse.introspection import (
    ClickHouseTuningIntrospector,
    _clickhouse_string_literal_body,
)
from benchbox.platforms.duckdb import DuckDBAdapter, _duckdb_sort_index_sql

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_alter_table_statements_still_resolve_their_table():
    statement = AppliedStatement(statement="ALTER TABLE lineitem CLUSTER BY (l_shipdate)", phase=PHASE_DDL)

    assert statement_table(statement) == "lineitem"


def _sorted_config() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    config.table_tunings["lineitem"] = TableTuning(
        table_name="lineitem", sorting=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)]
    )
    return config


def _adapter_with_table() -> tuple[DuckDBAdapter, duckdb.DuckDBPyConnection, str]:
    adapter = DuckDBAdapter(database_path=":memory:")
    adapter._applied_tuning_ledger = AppliedTuningLedger()
    adapter._applied_layout_operations = []
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE lineitem (l_orderkey INTEGER)")
    return adapter, connection, _duckdb_sort_index_sql("lineitem", ["l_orderkey"])


def test_in_place_sort_records_the_index_re_create_as_satisfied():
    adapter, connection, index_sql = _adapter_with_table()
    adapter._applied_tuning_ledger.record(index_sql, PHASE_DDL)

    adapter._recreate_sort_index_after_ctas("lineitem", _sorted_config(), connection)

    satisfied = adapter._applied_tuning_ledger.satisfied
    assert [(entry.intent, entry.satisfied_by) for entry in satisfied] == [("sort_index:lineitem", 0)]
    assert adapter._applied_layout_operations == []


def test_re_create_without_an_earlier_index_stays_executed():
    adapter, connection, index_sql = _adapter_with_table()

    adapter._recreate_sort_index_after_ctas("lineitem", _sorted_config(), connection)

    assert adapter._applied_tuning_ledger.satisfied == []
    assert [(op["statement"], op["status"]) for op in adapter._applied_layout_operations] == [(index_sql, "applied")]


def test_replace_path_keeps_the_re_create_executed(monkeypatch):
    adapter, connection, index_sql = _adapter_with_table()
    adapter._applied_tuning_ledger.record(index_sql, PHASE_DDL)
    monkeypatch.setattr(DuckDBAdapter, "_requires_constraint_preserving_ctas_sort", property(lambda self: False))

    adapter._recreate_sort_index_after_ctas("lineitem", _sorted_config(), connection)

    assert adapter._applied_tuning_ledger.satisfied == []
    assert [op["status"] for op in adapter._applied_layout_operations] == ["applied"]


def test_clickhouse_table_filter_escapes_backslash_and_quote():
    assert _clickhouse_string_literal_body("a\\b'c") == "a\\\\b''c"

    class _Connection:
        def __init__(self):
            self.queries: list[str] = []

        def execute(self, query, *args, **kwargs):
            self.queries.append(str(query))
            return []

    ledger = AppliedTuningLedger()
    ledger.record("OPTIMIZE TABLE a\\b'c", PHASE_DDL)
    connection = _Connection()
    ClickHouseTuningIntrospector().introspect(connection, ledger)

    assert "IN ('a\\\\b''c')" in connection.queries[0]
