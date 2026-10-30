from __future__ import annotations

import re

import pytest

from .test_datavault_dataframe_queries import _materialize, _schema_faithful_tables

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _tables():
    import datetime

    tables = _schema_faithful_tables(8)
    tables["sat_order"]["o_orderdate"] = datetime.date(1993, 11, 1)
    tables["sat_lineitem"]["l_quantity"] = 150.0
    return tables


@pytest.fixture(scope="module")
def datafusion_context():
    pytest.importorskip("datafusion")
    import pyarrow as pa

    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    adapter = DataFusionDataFrameAdapter(target_partitions=2)
    ctx = adapter.create_context()
    for name, frame in _tables().items():
        adapter.session_ctx.register_record_batches(name, [pa.Table.from_pandas(frame).to_batches()])
        ctx.register_table(name, adapter.session_ctx.table(name))
    return ctx


@pytest.fixture(scope="module")
def polars_context():
    import polars as pl

    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    ctx = PolarsDataFrameAdapter().create_context()
    for name, frame in _tables().items():
        ctx.register_table(name, pl.from_pandas(frame).lazy())
    return ctx


def _query_frame(query_id, ctx):
    from benchbox.core.datavault.dataframe_queries import get_datavault_query

    return get_datavault_query(query_id).expression_impl(ctx)


def _plan(frame) -> str:
    return frame.native.optimized_logical_plan().display_indent()


def _scan_order(plan: str) -> list[str]:
    return re.findall(r"TableScan: (\w+)", plan)


def _sorted_rows(frame, *, arrow: bool):
    if arrow:
        table = frame.collect()
        columns = table.column_names
        rows = [tuple(row.values()) for row in table.to_pylist()]
    else:
        columns, rows = _materialize(frame)
    return columns, sorted(rows, key=repr)


@pytest.mark.parametrize(
    "query_id,expected_first_scan",
    [("Q10", "sat_order"), ("Q18", "link_lineitem")],
)
def test_selective_input_leads_the_join_chain(datafusion_context, query_id, expected_first_scan):
    scans = _scan_order(_plan(_query_frame(query_id, datafusion_context)))
    assert scans[0] == expected_first_scan


@pytest.mark.parametrize("query_id", ["Q10", "Q18"])
def test_datafusion_matches_polars_row_count_and_columns(datafusion_context, polars_context, query_id):
    datafusion_columns, datafusion_rows = _sorted_rows(_query_frame(query_id, datafusion_context), arrow=True)
    polars_columns, polars_rows = _sorted_rows(_query_frame(query_id, polars_context), arrow=False)
    assert datafusion_rows
    assert datafusion_columns == polars_columns
    assert len(datafusion_rows) == len(polars_rows)
