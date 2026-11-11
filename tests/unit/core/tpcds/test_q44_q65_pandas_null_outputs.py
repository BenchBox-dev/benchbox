from __future__ import annotations

import math
from datetime import date

import duckdb
import pytest

from benchbox.core.equivalence.cross_surface import build_production_contexts
from benchbox.core.equivalence.dataframe_surface import fetch_reference_rows, materialize_rows
from benchbox.core.tpcds.dataframe_queries import queries
from benchbox.core.tpcds.dataframe_queries.parameters import parameter_overrides
from benchbox.core.tpcds.qualification.runner import qualification_parameters
from benchbox.core.tpcds.schema.registry import TABLES
from benchbox.core.tpchavoc.validation import ResultValidator, ValidationError
from benchbox.tpcds import TPCDS

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]


def _cell(tmp_path, monkeypatch, query, tables, file_format, *, backend="pandas"):
    monkeypatch.setenv("BENCHBOX_CACHE_DIR", str(tmp_path / "cache"))
    benchmark = TPCDS(scale_factor=1.0, output_dir=tmp_path)
    values = qualification_parameters()["values"][str(query)]
    raw = benchmark._impl.query_manager.dsqgen.generate_with_parameters(query, values, scale_factor=1.0)
    sql = benchmark._impl._apply_target_dialect_overrides(
        query, benchmark._impl.translate_query_text(raw, "netezza", "duckdb"), "duckdb"
    )
    paths = {}
    with duckdb.connect() as connection:
        for name, rows in tables.items():
            table = next(table for table in TABLES if table.name == name)
            connection.execute(table.get_create_table_sql(enable_primary_keys=True, enable_foreign_keys=False))
            for row in rows:
                names = list(row)
                connection.execute(
                    f"INSERT INTO {name} ({','.join(names)}) VALUES ({','.join('?' for _ in names)})",
                    list(row.values()),
                )
            path = tmp_path / f"{name}.{'dat' if file_format == 'csv' else 'parquet'}"
            source = name
            if file_format == "parquet":
                projection = ",".join(
                    f"CAST({column.name} AS DOUBLE) AS {column.name}"
                    if column.data_type.value.startswith("DECIMAL")
                    else column.name
                    for column in table.columns
                )
                source = f"(SELECT {projection} FROM {name})"
            options = (
                "FORMAT CSV, DELIMITER '|', HEADER false, NULL '', QUOTE ''"
                if file_format == "csv"
                else "FORMAT PARQUET"
            )
            connection.execute(f"COPY {source} TO '{path}' ({options})")
            paths[name] = [path]
        expected = fetch_reference_rows(connection, sql)
    benchmark.tables = paths
    context = build_production_contexts(benchmark, tmp_path, backends=(backend,), scale_factor=1.0)[backend]
    return context, expected


def _assert_rows(expected, actual, query, key):
    validator = ResultValidator()
    validator.validate_results_exact(expected, actual, query, 0, order_aware=True, order_by=key, tie_aware=True)
    null_position = next((i, j) for i, row in enumerate(expected) for j, value in enumerate(row) if value is None)
    i, j = null_position
    mutated = list(actual)
    row = list(mutated[i])
    row[j] = math.nan
    mutated[i] = tuple(row)
    with pytest.raises(ValidationError):
        validator.validate_results_exact(expected, mutated, query, 0, order_aware=True, order_by=key, tie_aware=True)


@pytest.mark.parametrize("file_format", ["csv", "parquet"])
def test_q44_nullable_names_and_rank_ties_match_generated_sql(tmp_path, monkeypatch, file_format):
    literal = "NaN" if file_format == "parquet" else "Shared"
    tables = {
        "item": [
            {"i_item_sk": key, "i_item_id": f"Item{key}", "i_product_name": name}
            for key, name in ((1, None), (2, literal), (3, "Other"), (4, "High"), (5, "No-profit"), (6, "Other-store"))
        ],
        "store_sales": [
            {
                "ss_ticket_number": ticket,
                "ss_item_sk": key,
                "ss_store_sk": store,
                "ss_addr_sk": address,
                "ss_net_profit": profit,
            }
            for ticket, key, store, address, profit in (
                (1, 1, 4, None, "10"),
                (2, 2, 4, 1, "20"),
                (3, 3, 4, 1, "20"),
                (4, 4, 4, 1, "30"),
                (5, 5, 4, 1, None),
                (6, 6, 5, None, "1000"),
            )
        ],
    }
    context, expected = _cell(tmp_path, monkeypatch, 44, tables, file_format)
    assert len(expected) == 6 and sum(row[0] == 2 for row in expected) == 4
    assert (1, None, "High") in expected and (4, "High", None) in expected
    with parameter_overrides({44: {"store_sk": 4, "null_col": "ss_addr_sk"}}):
        actual = materialize_rows(queries.q44_pandas_impl(context))
    _assert_rows(expected, actual, 44, [0])
    if file_format == "parquet":
        assert any(literal in row for row in actual)


@pytest.mark.parametrize("file_format", ["csv", "parquet"])
def test_q65_nullable_decimal_outputs_and_null_order_ties_match_generated_sql(tmp_path, monkeypatch, file_format):
    literal = "NaN" if file_format == "parquet" else "Brand"
    tables = {
        "store": [{"s_store_sk": 4, "s_store_id": "Store4", "s_store_name": None}],
        "item": [
            {
                "i_item_sk": 1,
                "i_item_id": "Item1",
                "i_item_desc": None,
                "i_current_price": None,
                "i_wholesale_cost": "10",
                "i_brand": None,
            },
            {
                "i_item_sk": 2,
                "i_item_id": "Item2",
                "i_item_desc": "Excluded",
                "i_current_price": "2",
                "i_wholesale_cost": "1",
                "i_brand": "High",
            },
            {
                "i_item_sk": 3,
                "i_item_id": "Item3",
                "i_item_desc": None,
                "i_current_price": "3",
                "i_wholesale_cost": None,
                "i_brand": literal,
            },
            {
                "i_item_sk": 4,
                "i_item_id": "Item4",
                "i_item_desc": "Outside",
                "i_current_price": "4",
                "i_wholesale_cost": "2",
                "i_brand": "Outside",
            },
        ],
        "date_dim": [
            {"d_date_sk": key, "d_date_id": f"Date{key}", "d_date": day, "d_month_seq": month}
            for key, month, day in (
                (1, 1176, date(1998, 1, 1)),
                (2, 1187, date(1998, 12, 1)),
                (3, 1175, date(1997, 12, 1)),
                (4, 1188, date(1999, 1, 1)),
            )
        ],
        "store_sales": [
            {
                "ss_ticket_number": ticket,
                "ss_item_sk": key,
                "ss_store_sk": 4,
                "ss_sold_date_sk": date,
                "ss_sales_price": price,
            }
            for ticket, key, date, price in (
                (1, 1, 1, "1"),
                (2, 2, 2, "99"),
                (3, 3, 1, "0"),
                (4, 4, 3, "-100"),
                (5, 4, 4, "-100"),
            )
        ],
    }
    context, expected = _cell(tmp_path, monkeypatch, 65, tables, file_format)
    assert len(expected) == 2
    assert (None, None, 1.0, None, 10.0, None) in expected
    assert (None, None, 0.0, 3.0, None, literal) in expected
    with parameter_overrides({65: {"dms": 1176}}):
        actual = materialize_rows(queries.q65_pandas_impl(context))
    _assert_rows(expected, actual, 65, [0, 1])


@pytest.mark.parametrize("backend", ["expression", "pandas", "datafusion"])
def test_q65_all_null_revenue_is_excluded_while_mixed_zero_and_negative_sums_remain(tmp_path, monkeypatch, backend):
    tables = {
        "store": [{"s_store_sk": 4, "s_store_id": "Store4", "s_store_name": "Store"}],
        "item": [
            {
                "i_item_sk": key,
                "i_item_id": f"Item{key}",
                "i_item_desc": description,
                "i_current_price": "3",
                "i_wholesale_cost": "1",
                "i_brand": "Brand",
            }
            for key, description in ((1, "Mixed"), (2, "High"), (3, "Zero"), (4, "Null-only"), (5, "Negative"))
        ],
        "date_dim": [{"d_date_sk": 1, "d_date_id": "Date1", "d_date": date(1998, 1, 1), "d_month_seq": 1176}],
        "store_sales": [
            {
                "ss_ticket_number": ticket,
                "ss_item_sk": key,
                "ss_store_sk": 4,
                "ss_sold_date_sk": 1,
                "ss_sales_price": price,
            }
            for ticket, key, price in (
                (1, 1, "1"),
                (2, 1, None),
                (3, 2, "99"),
                (4, 3, "0"),
                (5, 4, None),
                (6, 4, None),
                (7, 5, "-1"),
            )
        ],
    }
    context, expected = _cell(tmp_path, monkeypatch, 65, tables, "csv", backend=backend)
    assert expected == [
        ("Store", "Mixed", 1.0, 3.0, 1.0, "Brand"),
        ("Store", "Negative", -1.0, 3.0, 1.0, "Brand"),
        ("Store", "Zero", 0.0, 3.0, 1.0, "Brand"),
    ]
    implementation = queries.q65_pandas_impl if backend == "pandas" else queries.q65_expression_impl
    with parameter_overrides({65: {"dms": 1176}}):
        actual = materialize_rows(implementation(context))
    validator = ResultValidator()
    validator.validate_results_exact(expected, actual, 65, 0, order_aware=True, order_by=[0, 1])
    mutated = [(*actual[0][:2], actual[0][2] + 1.0, *actual[0][3:]), *actual[1:]]
    with pytest.raises(ValidationError):
        validator.validate_results_exact(expected, mutated, 65, 0, order_aware=True, order_by=[0, 1])


