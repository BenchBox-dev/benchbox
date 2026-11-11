from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]


@pytest.fixture
def q77_cell(tmp_path, monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from benchbox.tpcds import TPCDS

    monkeypatch.setenv("BENCHBOX_CACHE_DIR", str(tmp_path / "cache"))
    specifications = {
        "date_dim": (
            "d_date_sk INTEGER, d_date DATE",
            [(1, date(2000, 8, 23)), (2, date(2000, 9, 22)), (3, date(2000, 9, 23))],
        ),
        "store": ("s_store_sk INTEGER", [(1,), (2,)]),
        "web_page": ("wp_web_page_sk INTEGER", [(1,), (2,)]),
        "store_sales": (
            "ss_sold_date_sk INTEGER, ss_store_sk INTEGER, ss_ext_sales_price DECIMAL(7,2), ss_net_profit DECIMAL(7,2)",
            [(1, 1, None, None), (1, 2, "0", "0"), (3, 1, "999", "999")],
        ),
        "store_returns": (
            "sr_returned_date_sk INTEGER, sr_store_sk INTEGER, sr_return_amt DECIMAL(7,2), sr_net_loss DECIMAL(7,2)",
            [(1, 1, None, None)],
        ),
        "catalog_sales": (
            "cs_sold_date_sk INTEGER, cs_call_center_sk INTEGER, "
            "cs_ext_sales_price DECIMAL(7,2), cs_net_profit DECIMAL(7,2)",
            [(1, None, "2", "1"), (2, None, "2.5", "1"), (1, 1, "11.5", "5"), (3, 1, "999", "999")],
        ),
        "catalog_returns": (
            "cr_returned_date_sk INTEGER, cr_call_center_sk INTEGER, "
            "cr_return_amount DECIMAL(7,2), cr_net_loss DECIMAL(7,2)",
            [(1, 1, "1", ".5"), (1, 2, "2", "1"), (3, 3, "999", "999")],
        ),
        "web_sales": (
            "ws_sold_date_sk INTEGER, ws_web_page_sk INTEGER, "
            "ws_ext_sales_price DECIMAL(7,2), ws_net_profit DECIMAL(7,2)",
            [(1, 1, "3", "1"), (1, 2, None, None)],
        ),
        "web_returns": (
            "wr_returned_date_sk INTEGER, wr_web_page_sk INTEGER, wr_return_amt DECIMAL(7,2), wr_net_loss DECIMAL(7,2)",
            [(1, 1, None, None)],
        ),
    }
    benchmark = TPCDS(scale_factor=1.0, output_dir=tmp_path)
    raw = benchmark._impl.query_manager.dsqgen.generate_with_parameters(
        77, {"SALES_DATE.01": "2000-08-23"}, scale_factor=1.0
    )
    sql = benchmark._impl._apply_target_dialect_overrides(
        77, benchmark._impl.translate_query_text(raw, "netezza", "duckdb"), "duckdb"
    )
    tables = {}
    with duckdb.connect() as connection:
        for name, (columns, rows) in specifications.items():
            connection.execute(f"CREATE TABLE {name} ({columns})")
            connection.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' for _ in rows[0])})", rows)
            table = connection.execute(f"SELECT * FROM {name}").to_arrow_table()
            table = table.cast(
                pa.schema(
                    [
                        pa.field(field.name, pa.float64() if pa.types.is_decimal(field.type) else field.type)
                        for field in table.schema
                    ]
                )
            )
            tables[name] = tmp_path / f"{name}.parquet"
            pq.write_table(table, tables[name])
        cursor = connection.execute(sql)
        types = [str(column[1]) for column in cursor.description]
        expected = cursor.fetchall()
    return sql, types, expected, SimpleNamespace(name="numeric_ties", scale_factor=1.0, tables=tables), tmp_path


@pytest.mark.parametrize("backend", ["expression", "pandas", "datafusion"])
def test_qualification_numeric_ties_match_generated_q77_and_reject_wrong_amounts_and_order(q77_cell, backend):
    pytest.importorskip({"expression": "polars", "pandas": "pandas", "datafusion": "datafusion"}[backend])
    from benchbox.core.equivalence.cross_surface import build_production_contexts
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries
    from benchbox.core.tpcds.dataframe_queries.parameters import parameter_overrides
    from benchbox.core.tpcds.qualification.runner import compare_rows, convert_rows

    sql, types, expected, benchmark, directory = q77_cell
    context = build_production_contexts(benchmark, directory, backends=(backend,), scale_factor=1.0)[backend]
    implementation = queries.q77_pandas_impl if backend == "pandas" else queries.q77_expression_impl
    with parameter_overrides({77: {"sales_date": "2000-08-23"}}):
        candidate = materialize_rows(implementation(context))
    assert len(expected) == len(candidate) == 10
    tied = [row for row in expected if row[:2] == ("catalog channel", None)]
    assert sorted(row[2:] for row in tied) == [
        (Decimal("9.00"), Decimal("3.00"), Decimal("2.50")),
        (Decimal("32.00"), Decimal("6.00"), Decimal("11.00")),
    ]
    printed = {
        "columns": ["channel", "id", "sales", "returns", "profit"],
        "rows": [[None if value is None else str(value) for value in row] for row in expected],
        "null_tokens": [],
    }
    official = convert_rows(printed, types)
    for reference in (expected, official):
        assert compare_rows(reference, candidate, "77", sql)["status"] == "match"
        mutated = [*candidate]
        mutated[0] = (*mutated[0][:2], mutated[0][2] + 1.0, *mutated[0][3:])
        assert compare_rows(reference, mutated, "77", sql)["status"] == "mismatch"
        reversed_order = compare_rows(reference, list(reversed(candidate)), "77", sql)
        assert reversed_order["status"] == "mismatch"
        assert "ORDER BY" in reversed_order["detail"]


