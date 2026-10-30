from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _frame():
    import pandas as pd

    return pd.DataFrame(
        {
            "id": [1, 2, 3],
            "amount": [1.5, None, 3.5],
            "label": ["a", "b", "c"],
            "tags": [[1], [2], [3]],
        }
    )


def _managers():
    pytest.importorskip("dask.dataframe")
    import dask
    import dask.dataframe as dd

    from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark

    benchmark = MetadataPrimitivesBenchmark()
    pandas_frame = _frame()
    with dask.config.set({"dataframe.convert-string": False}):
        dask_frame = dd.from_pandas(pandas_frame, npartitions=2)
    return (
        benchmark.get_dataframe_operations("pandas-df"),
        benchmark.get_dataframe_operations("dask-df"),
        pandas_frame,
        dask_frame,
    )


def test_dask_platform_has_metadata_capabilities():
    from benchbox.core.metadata_primitives import DASK_METADATA_CAPABILITIES, get_platform_capabilities

    caps = get_platform_capabilities("Dask")

    assert caps.platform_name == "dask-df"
    assert caps.notes == DASK_METADATA_CAPABILITIES.notes
    assert caps.supports_schema_introspection is True
    assert caps.supports_catalog is False


@pytest.mark.parametrize(
    "operation",
    [
        "execute_list_columns",
        "execute_get_dtypes",
        "execute_get_schema",
        "execute_column_count",
        "execute_wide_table_schema",
    ],
)
def test_dask_schema_operations_match_pandas(operation):
    pandas_manager, dask_manager, pandas_frame, dask_frame = _managers()

    expected = getattr(pandas_manager, operation)(pandas_frame)
    actual = getattr(dask_manager, operation)(dask_frame)

    assert expected.success and actual.success
    assert actual.result_count == expected.result_count
    assert actual.result_data == expected.result_data
    assert actual.metrics == expected.metrics


def test_dask_row_count_and_describe_stats_match_pandas():
    pandas_manager, dask_manager, pandas_frame, dask_frame = _managers()

    rows = dask_manager.execute_row_count(dask_frame)
    stats = dask_manager.execute_describe_stats(dask_frame)
    expected = pandas_manager.execute_describe_stats(pandas_frame)

    assert rows.success and rows.result_data == 3
    assert stats.success
    assert stats.result_count == expected.result_count
    assert set(stats.result_data) == set(expected.result_data)


def test_dask_dataframe_benchmark_runs_every_operation_without_failures():
    pytest.importorskip("dask.dataframe")
    from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark
    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    benchmark = MetadataPrimitivesBenchmark()
    adapter = DaskDataFrameAdapter(execution_engine="threaded")
    tables = benchmark.build_complexity_dataframes(adapter, "wide_tables")

    result = benchmark.run_dataframe_benchmark(adapter.platform_name, tables)

    assert result.total_queries > 0
    assert result.failed_queries == 0
    wide = benchmark.get_dataframe_operations(adapter.platform_name).execute_wide_table_schema(tables["stress_wide"])
    assert len(wide.result_data) == wide.metrics["column_count"] >= 100


def test_dask_reports_complex_type_introspection_as_unsupported():
    from benchbox.core.metadata_primitives.dataframe_operations import UnsupportedOperationError

    _, dask_manager, _, dask_frame = _managers()

    with pytest.raises(UnsupportedOperationError):
        dask_manager.execute_complex_type_introspection(dask_frame)
