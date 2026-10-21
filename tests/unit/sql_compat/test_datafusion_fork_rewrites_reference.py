from __future__ import annotations

from decimal import Decimal

import pytest

from benchbox.core.expected_results.loader import load_tpch_subscale_row_counts
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpch.schema import TABLES
from benchbox.utils.dialect_utils import apply_rewrite

datafusion = pytest.importorskip("datafusion")
duckdb = pytest.importorskip("duckdb")
pa = pytest.importorskip("pyarrow")

pytestmark = [pytest.mark.unit, pytest.mark.medium]

SCALE_FACTORS = (0.01, 0.1)
QUERY_IDS = (11, 16, 18, 20)
SCHEMA = {table.name: {column.name: column.get_sql_type() for column in table.columns} for table in TABLES}


def _arrow_type(sql_type: str):
    if sql_type == "INTEGER":
        return pa.int32()
    if sql_type == "DATE":
        return pa.date32()
    if sql_type.startswith("DECIMAL"):
        return pa.decimal128(15, 2)
    return pa.string()


def _fork_rewrite(query_id: int, sql: str) -> str:
    if query_id in (11, 20):
        return apply_rewrite("decorrelate_subqueries", sql, "datafusion", schema=SCHEMA)
    if query_id == 16:
        return apply_rewrite(
            "not_in_subquery_to_not_exists", sql, "datafusion", non_null_columns=["ps_suppkey", "s_suppkey"]
        )
    return apply_rewrite("in_aggregate_subquery_to_exists", sql, "datafusion", alias="t")


def _comparable(rows) -> list[tuple]:
    return [
        tuple(round(float(cell), 2) if isinstance(cell, (Decimal, float)) else cell for cell in row) for row in rows
    ]


@pytest.fixture(scope="module", params=SCALE_FACTORS, ids=lambda sf: f"sf{sf:g}")
def engines(request, tmp_path_factory):
    scale_factor = request.param
    data_dir = tmp_path_factory.mktemp(f"tpch_sf{scale_factor:g}")
    TPCHBenchmark(scale_factor=scale_factor, output_dir=data_dir).generate_data()
    context = datafusion.SessionContext()
    reference = duckdb.connect()
    for table in TABLES:
        path = data_dir / f"{table.name}.tbl"
        schema = pa.schema([pa.field(column.name, _arrow_type(column.get_sql_type())) for column in table.columns])
        context.register_csv(
            table.name, str(path), schema=schema, has_header=False, delimiter="|", file_extension=".tbl"
        )
        columns = ", ".join(f"'{column.name}': '{column.get_sql_type()}'" for column in table.columns)
        reference.execute(
            f"CREATE TABLE {table.name} AS SELECT * FROM read_csv('{path}', delim='|', header=false, "
            f"columns={{{columns}}})"
        )
    return scale_factor, TPCHBenchmark(scale_factor=scale_factor), context, reference


@pytest.mark.parametrize("query_id", QUERY_IDS)
def test_fork_rewrites_match_the_tpch_reference_row_counts_and_the_unrewritten_query(engines, query_id: int):
    scale_factor, benchmark, context, reference = engines
    rewritten = _fork_rewrite(query_id, benchmark.get_query(query_id, dialect="datafusion"))
    batches = context.sql(rewritten).collect()
    actual = [
        tuple(column[index].as_py() for column in batch.columns) for batch in batches for index in range(batch.num_rows)
    ]
    expected_count = load_tpch_subscale_row_counts(scale_factor).row_counts[str(query_id)]
    assert len(actual) == expected_count
    expected = reference.execute(benchmark.get_query(query_id, dialect="duckdb")).fetchall()
    assert _comparable(actual) == _comparable(expected)
