from __future__ import annotations

import pytest

from benchbox.core.tuning.interface import TableTuning, TuningColumn
from benchbox.platforms.clickhouse.workload import ClickHouseWorkloadMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_STATEMENT = "CREATE TABLE store_sales (ss_item_sk INTEGER, ss_sold_date_sk INTEGER, ss_quantity INTEGER)"


def _optimize(statement: str, table_tunings, nullable_columns: set[str], *, primary_keys_enabled: bool = True) -> str:
    mixin = ClickHouseWorkloadMixin()
    return mixin._translate_schema_statement(
        statement, table_tunings, nullable_columns=nullable_columns, primary_keys_enabled=primary_keys_enabled
    )


def _sorting_tuning(*column_names: str) -> dict[str, TableTuning]:
    return {
        "store_sales": TableTuning(
            table_name="STORE_SALES",
            sorting=[
                TuningColumn(name=name, type="INTEGER", order=order) for order, name in enumerate(column_names, start=1)
            ],
        )
    }


def test_tuned_sort_on_nullable_column_raises_actionable_error() -> None:
    tunings = _sorting_tuning("ss_sold_date_sk")
    with pytest.raises(ValueError, match="ss_sold_date_sk") as excinfo:
        _optimize(_STATEMENT, tunings, {"ss_sold_date_sk"})
    message = str(excinfo.value)
    assert "store_sales" in message
    assert "ss_sold_date_sk" in message


def test_tuned_partition_on_nullable_column_raises() -> None:
    tunings = {
        "store_sales": TableTuning(
            table_name="STORE_SALES",
            partitioning=[TuningColumn(name="ss_sold_date_sk", type="INTEGER", order=1)],
        )
    }
    with pytest.raises(ValueError, match="ss_sold_date_sk") as excinfo:
        _optimize(_STATEMENT, tunings, {"ss_sold_date_sk"})
    assert "cannot be Nullable" in str(excinfo.value)


def test_tuned_sort_on_non_nullable_column_renders_and_wraps_others() -> None:
    tunings = _sorting_tuning("ss_item_sk")
    rendered = _optimize(_STATEMENT, tunings, {"ss_quantity"})
    assert "ORDER BY (ss_item_sk)" in rendered
    assert "ss_quantity Nullable(Int32)" in rendered
    assert "ss_item_sk Nullable(" not in rendered


def test_untuned_nullable_columns_never_raise() -> None:
    rendered = _optimize(_STATEMENT, None, {"ss_sold_date_sk", "ss_quantity"})
    assert "Nullable(Int32)" in rendered


@pytest.mark.usefixtures("chdb_probe_satisfied")
def test_registry_tunings_reject_nullable_keys_on_the_real_tpcds_schema(tmp_path) -> None:
    from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark
    from benchbox.core.tpcds.schema.registry import get_tunings
    from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

    benchmark = TPCDSBenchmark(scale_factor=0.01, output_dir=tmp_path / "tpcds")
    adapter = ClickHouseAdapter(deployment_mode="local")
    nullable_by_table = adapter._get_nullable_columns_by_table(benchmark)
    tpcds_tunings = get_tunings().table_tunings
    statements = [
        statement.strip()
        for statement in benchmark.get_create_tables_sql(dialect="duckdb").split(";")
        if statement.strip()
    ]
    rejected: set[str] = set()
    for statement in statements:
        table_name = adapter._extract_table_name(statement)
        assert table_name is not None
        nullable_columns = nullable_by_table.get(table_name.lower(), set())
        try:
            adapter._translate_schema_statement(statement, tpcds_tunings, nullable_columns=nullable_columns)
        except ValueError as exc:
            assert "cannot be Nullable" in str(exc)
            rejected.add(table_name)
    assert rejected == {
        "store_sales",
        "store_returns",
        "catalog_sales",
        "catalog_returns",
        "web_sales",
        "web_returns",
        "inventory",
    }


def test_statement_inline_pk_on_nullable_column_keeps_existing_rendering() -> None:
    statement = "CREATE TABLE t (a INTEGER, b INTEGER, PRIMARY KEY (b))"
    tunings = {
        "t": TableTuning(
            table_name="T",
            sorting=[TuningColumn(name="a", type="INTEGER", order=1)],
        )
    }
    rendered = _optimize(statement, tunings, {"b"}, primary_keys_enabled=False)
    assert "ORDER BY (a)" in rendered
    assert "Nullable(" not in rendered


class _Benchmark:
    def __init__(self, ddl: str, schema=None) -> None:
        self._ddl = ddl
        self._schema = schema

    def get_create_tables_sql(self, dialect="standard", tuning_config=None):
        return self._ddl

    def get_schema(self):
        return self._schema


def _created_ddl(benchmark: _Benchmark) -> str:
    from unittest.mock import Mock

    mixin = ClickHouseWorkloadMixin()
    mixin.logger = Mock()
    mixin.tuning_enabled = False
    mixin.get_effective_tuning_configuration = lambda: None
    mixin._get_constraint_configuration = lambda: (True, False)
    mixin._log_constraint_configuration = lambda *args: None
    mixin._record_tuned_sort_key_op = lambda *args: None
    mixin._record_primary_key_order_op = lambda *args: None
    executed: list[str] = []
    connection = Mock()
    connection.execute = executed.append
    mixin.create_schema(benchmark, connection)
    return "\n".join(executed)


def test_schema_without_structure_derives_nullability_from_the_ddl() -> None:
    ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT NOT NULL, note VARCHAR(5), amount INTEGER);"
    rendered = _created_ddl(_Benchmark(ddl, schema="CREATE TABLE t (...)"))
    assert "name String" in rendered and "name Nullable" not in rendered
    assert "note Nullable(String)" in rendered
    assert "amount Nullable(Int32)" in rendered
    assert "id Nullable" not in rendered


def test_keyed_schema_columns_defer_to_ddl_not_null() -> None:
    ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY, op VARCHAR(10) NOT NULL, note VARCHAR(5));"
    schema = {
        "t": {"columns": {"id": {"type": "INTEGER"}, "op": {"type": "VARCHAR(10)"}, "note": {"type": "VARCHAR(5)"}}}
    }
    rendered = _created_ddl(_Benchmark(ddl, schema))
    assert "op String" in rendered and "op Nullable" not in rendered
    assert "note Nullable(String)" in rendered


def test_structured_schema_without_nullable_columns_stays_non_nullable() -> None:
    ddl = "CREATE TABLE t (name STRING);"
    schema = {"t": {"columns": {"name": {"type": "STRING", "nullable": False}}}}
    assert "Nullable" not in _created_ddl(_Benchmark(ddl, schema))


def test_ddl_not_null_columns_ignore_constraints_and_quotes() -> None:
    statement = 'CREATE TABLE t (a INTEGER NOT NULL, "b" TEXT, c DECIMAL(10, 2) NOT NULL, PRIMARY KEY (a))'
    assert ClickHouseWorkloadMixin._ddl_not_null_columns(statement) == {"a", "c"}
