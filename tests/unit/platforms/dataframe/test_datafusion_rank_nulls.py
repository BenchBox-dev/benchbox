from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]


@pytest.fixture(scope="module")
def tpcds_benchmark(tmp_path_factory):
    from benchbox.tpcds import TPCDS

    return TPCDS(scale_factor=0.01, output_dir=tmp_path_factory.mktemp("rank-sql"))


@pytest.mark.parametrize("method,sql_function", [("min", "rank"), ("dense", "dense_rank")])
@pytest.mark.parametrize("descending", [False, True])
@pytest.mark.parametrize("joined", [False, True])
def test_datafusion_partitioned_ranks_match_translated_sql_with_nulls_and_ties(
    method, sql_function, descending, joined, tpcds_benchmark
):
    pytest.importorskip("datafusion")
    import duckdb
    import pyarrow as pa

    from benchbox._vendor import sqlglot
    from benchbox._vendor.sqlglot import exp
    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    tables = {
        "facts": pa.table(
            {
                "id": list(range(1, 13)),
                "grp": ["a"] * 5 + ["b"] * 5 + ["c"] * 2,
                "value": [None, 1.0, 1.0, 3.0, None, 4.0, None, 2.0, 2.0, None, None, None],
            }
        )
    }
    if joined:
        tables["metrics"] = tables["facts"].select(["id", "value"])
        tables["facts"] = tables["facts"].set_column(2, "value", pa.array([-99.0] * 12))
    value = "m.value" if joined else "f.value"
    source = "facts f LEFT JOIN metrics m ON f.id=m.id" if joined else "facts f"
    direction = "DESC" if descending else "ASC"
    rendered = tpcds_benchmark.get_query(86 if descending else 36, dialect="duckdb")
    window = next(sqlglot.parse_one(rendered, read="duckdb").find_all(exp.Window))
    ordering = window.args["order"].expressions[0]
    assert ordering.args["desc"] == descending
    nulls_first = ordering.args["nulls_first"]
    assert nulls_first == descending
    null_order = "NULLS FIRST" if nulls_first else "NULLS LAST"
    with duckdb.connect() as connection:
        for name, table in tables.items():
            connection.register(name, table)
        expected = connection.execute(
            f"SELECT f.id, {value}, {sql_function}() OVER (PARTITION BY f.grp ORDER BY {value} {direction} "
            f"{null_order}) AS ranking FROM {source} ORDER BY f.id"
        ).fetchall()
    adapter = DataFusionDataFrameAdapter(target_partitions=4)
    ctx = adapter.create_context()
    for name, table in tables.items():
        adapter.session_ctx.register_record_batches(name, [table.to_batches()])
        ctx.register_table(name, adapter.session_ctx.table(name))
    frame = ctx.get_table("facts")
    column = "value"
    if joined:
        frame = frame.join(ctx.get_table("metrics"), on="id", how="left", suffix="_metric")
        column = "value_metric"
    ranking = ctx.col(column).rank(method=method, descending=descending).over("grp")
    table = frame.with_columns(ranking.alias("ranking")).select("id", column, "ranking").sort("id").collect()
    actual = list(zip(*(column.to_pylist() for column in table.columns)))
    assert actual == expected
