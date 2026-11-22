from __future__ import annotations

import pytest

from benchbox.core.dataframe.data_loader import ConversionStatus, FormatConverter
from benchbox.core.equivalence.dataframe_surface import materialize_rows
from benchbox.core.tpcds.dataframe_queries import TPCDS_DATAFRAME_QUERIES
from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS, bind_parameters
from benchbox.core.tpcds.dataframe_queries.parameters import parameter_overrides
from benchbox.core.tpcds.power_test import power_parameter_seed

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]

ITEM_COLUMNS = [
    "i_item_sk",
    "i_manufact_id",
    "i_manufact",
    "i_category",
    "i_color",
    "i_units",
    "i_size",
    "i_product_name",
]
ITEM_TYPES = {"i_item_sk": "int64", "i_manufact_id": "int64"}
STREAM = 1


@pytest.fixture(scope="module")
def tpcds_benchmark(tmp_path_factory):
    from benchbox.tpcds import TPCDS

    return TPCDS(scale_factor=10.0, output_dir=tmp_path_factory.mktemp("q41-sql"))


@pytest.fixture(scope="module")
def stream_binding():
    return bind_parameters(41, scale_factor=10.0, seed=power_parameter_seed(None, STREAM), stream_id=0)


def _item_rows(parameters):
    manufact = parameters["manufact"]
    colors, units, sizes = parameters["colors"], parameters["units"], parameters["sizes"]
    return [
        (1, manufact, "anchor", "Women", colors[0], units[0], sizes[0], "matches through the first draw"),
        (2, manufact + 1, "anchor", "Women", "other", "other", "other", "shares the matching manufacturer"),
        (3, manufact + 2, "outlier", "Women", "other", "other", "other", "has no matching manufacturer"),
    ]


def _write_item(path, rows):
    path.write_text("".join("|".join(str(value) for value in row) + "|\n" for row in rows))


def _reference_rows(benchmark, logged, rows):
    import duckdb
    import pandas as pd

    impl = benchmark._impl
    raw = impl.query_manager.dsqgen.generate_with_parameters("41", logged, scale_factor=10.0)
    sql = impl._apply_target_dialect_overrides(41, impl.translate_query_text(raw, "netezza", "duckdb"), "duckdb")
    with duckdb.connect() as connection:
        connection.register("item", pd.DataFrame(rows, columns=ITEM_COLUMNS))
        return connection.execute(sql).fetchall()


def _expression_rows(parquet_path, parameters):
    pl = pytest.importorskip("polars")
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    ctx = PolarsDataFrameAdapter().create_context()
    ctx.register_table("item", pl.scan_parquet(parquet_path))
    with parameter_overrides({41: parameters}):
        implementation = TPCDS_DATAFRAME_QUERIES.get_or_raise("Q41").get_impl_for_family("expression")
        return materialize_rows(implementation(ctx))


def test_q41_expression_keeps_sentinel_looking_text_from_converted_data(tmp_path, tpcds_benchmark, stream_binding):
    parameters = ADAPTERS[41](stream_binding.logged)
    assert parameters["sizes"][0] == "N/A"
    rows = _item_rows(parameters)
    tbl = tmp_path / "item.dat"
    _write_item(tbl, rows)
    parquet = tmp_path / "item.parquet"

    status, row_count = FormatConverter.convert_csv_to_parquet(
        source_path=tbl,
        target_path=parquet,
        column_names=ITEM_COLUMNS,
        delimiter="|",
        column_types=ITEM_TYPES,
    )

    assert status == ConversionStatus.SUCCESS
    assert row_count == len(rows)
    expected = _reference_rows(tpcds_benchmark, stream_binding.logged, rows)
    assert expected
    assert _expression_rows(parquet, parameters) == expected
