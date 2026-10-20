from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import benchbox.platforms.postgresql as postgresql_module
from benchbox.core.tuning.applied_ledger import PHASE_DDL, AppliedTuningLedger, recording_connection
from benchbox.core.tuning.ddl_generator import get_ddl_generator
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.platforms.base.sql_execution import execute_sql_query
from benchbox.platforms.cedardb import CedarDBAdapter
from benchbox.platforms.citus import CitusAdapter
from benchbox.platforms.pg_mooncake import PgMooncakeAdapter
from benchbox.platforms.postgresql import PARTITIONING_WITH_KEYS_REASON, PostgreSQLAdapter
from benchbox.platforms.timescaledb import TimescaleDBAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_CREATE = "CREATE TABLE lineitem (l_orderkey INTEGER NOT NULL, l_shipdate DATE NOT NULL)"
_TPCH = SimpleNamespace(_name="TPC-H")


def _config(*, keys: bool = False, column: str = "L_SHIPDATE", column_type: str = "DATE") -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    config.primary_keys.enabled = keys
    config.table_tunings["LINEITEM"] = TableTuning(
        table_name="LINEITEM", partitioning=[TuningColumn(name=column, type=column_type, order=1)]
    )
    return config


@pytest.fixture
def adapter(monkeypatch) -> PostgreSQLAdapter:
    monkeypatch.setattr(postgresql_module, "psycopg", Mock(__version__="3.1.0"))
    instance = PostgreSQLAdapter(tuning_enabled=True)
    instance._applied_tuning_ledger = AppliedTuningLedger()
    return instance


def test_date_partitioning_uses_the_benchmark_date_domain_and_a_default(adapter):
    adapter.unified_tuning_configuration = _config()

    statement, children = adapter.partitioned_create_statements(_CREATE, _TPCH)

    assert statement == f"{_CREATE}\nPARTITION BY RANGE (L_SHIPDATE)"
    assert (
        children[0]
        == "CREATE TABLE lineitem_1992 PARTITION OF lineitem FOR VALUES FROM ('1992-01-01') TO ('1993-01-01');"
    )
    assert (
        children[-2]
        == "CREATE TABLE lineitem_1998 PARTITION OF lineitem FOR VALUES FROM ('1998-01-01') TO ('1999-01-01');"
    )
    assert children[-1] == "CREATE TABLE lineitem_default PARTITION OF lineitem DEFAULT;"
    assert len(children) == 8


def test_adapter_output_equals_generator_output(adapter):
    adapter.unified_tuning_configuration = _config()
    generator = get_ddl_generator("postgresql")
    tuning = TableTuning(table_name="LINEITEM", partitioning=[TuningColumn(name="L_SHIPDATE", type="DATE", order=1)])
    clauses = generator.generate_tuning_clauses(tuning)

    statement, children = adapter.partitioned_create_statements(_CREATE, _TPCH)

    assert statement.endswith(clauses.partition_by)
    assert children == generator.generate_partition_children(
        "lineitem", [], clauses, table_tuning=tuning, year_range=(1992, 1999)
    )


def test_integer_partitioning_hashes_without_a_default(adapter):
    adapter.unified_tuning_configuration = _config(column="L_ORDERKEY", column_type="INTEGER")

    statement, children = adapter.partitioned_create_statements(_CREATE, _TPCH)

    assert statement.endswith("PARTITION BY HASH (L_ORDERKEY)")
    assert len(children) == 4 and all("FOR VALUES WITH (MODULUS 4" in child for child in children)


def test_unknown_benchmark_still_gets_a_default_partition(adapter):
    adapter.unified_tuning_configuration = _config()

    _statement, children = adapter.partitioned_create_statements(_CREATE, SimpleNamespace(_name="Custom"))

    assert children[-1] == "CREATE TABLE lineitem_default PARTITION OF lineitem DEFAULT;"


def test_requested_keys_skip_partitioning_and_record_the_drop(adapter):
    adapter.unified_tuning_configuration = _config(keys=True)

    statement, children = adapter.partitioned_create_statements(_CREATE, _TPCH)

    assert (statement, children) == (_CREATE, [])
    assert [(item.intent, item.reason) for item in adapter._applied_tuning_ledger.dropped] == [
        ("partitioning:LINEITEM (L_SHIPDATE)", PARTITIONING_WITH_KEYS_REASON)
    ]
    adapter._reconcile_requested_tuning(adapter.unified_tuning_configuration)
    partition_drops = [
        item for item in adapter._applied_tuning_ledger.dropped if item.intent.startswith("partitioning")
    ]
    assert len(partition_drops) == 1


def test_untuned_and_unpartitioned_tables_are_unchanged(adapter):
    adapter.unified_tuning_configuration = _config()
    assert adapter.partitioned_create_statements("CREATE TABLE orders (o_orderkey INTEGER)", _TPCH) == (
        "CREATE TABLE orders (o_orderkey INTEGER)",
        [],
    )
    adapter.tuning_enabled = False
    assert adapter.partitioned_create_statements(_CREATE, _TPCH) == (_CREATE, [])


class _FakeCursor:
    def __init__(self, executed: list[str]):
        self._executed = executed
        self._rows: list[tuple] = []

    def execute(self, statement, *args, **kwargs):
        text = str(statement)
        if "pg_get_partkeydef" in text:
            self._rows = [("RANGE (l_shipdate)",)]
            return
        self._executed.append(text)

    def fetchall(self):
        return self._rows

    def close(self):
        return None


class _FakeConnection:
    def __init__(self):
        self.executed: list[str] = []

    def cursor(self):
        return _FakeCursor(self.executed)

    def commit(self):
        return None

    def rollback(self):
        return None


def test_create_schema_executes_and_records_the_partitioned_table(adapter, monkeypatch):
    adapter.unified_tuning_configuration = _config()
    monkeypatch.setattr(adapter, "_create_schema_with_tuning", lambda benchmark, source_dialect: _CREATE + ";")
    connection = _FakeConnection()
    recorded = recording_connection(connection, adapter._applied_tuning_ledger, PHASE_DDL)

    adapter.create_schema(_TPCH, recorded)

    assert connection.executed[0] == f"{_CREATE}\nPARTITION BY RANGE (L_SHIPDATE)"
    assert connection.executed[-1] == "CREATE TABLE lineitem_default PARTITION OF lineitem DEFAULT;"
    adapter._reconcile_requested_tuning(adapter.unified_tuning_configuration)
    assert adapter._applied_tuning_ledger.dropped == []
    assert [item.intent for item in adapter._applied_tuning_ledger.satisfied] == ["partitioning:LINEITEM (L_SHIPDATE)"]


def test_adapters_that_do_not_partition_never_query_the_partition_catalog(adapter, monkeypatch):
    adapter.unified_tuning_configuration = _config()
    monkeypatch.setattr(adapter, "execution_partitioning_supported", False)
    connection = Mock()

    adapter._verify_partitioning_catalog(connection)

    connection.cursor.assert_not_called()
    assert adapter._applied_tuning_ledger.dropped == []


@pytest.mark.parametrize(
    "adapter_class",
    [CedarDBAdapter, CitusAdapter, PgMooncakeAdapter, TimescaleDBAdapter],
)
def test_family_variants_do_not_partition(adapter_class):
    assert adapter_class.execution_partitioning_supported is False


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
