from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def test_dask_platform_has_metadata_capabilities():
    from benchbox.core.metadata_primitives import DASK_METADATA_CAPABILITIES, get_platform_capabilities

    caps = get_platform_capabilities("Dask")

    assert caps.platform_name == "dask-df"
    assert caps.notes == DASK_METADATA_CAPABILITIES.notes
    assert caps.supports_schema_introspection is True
    assert caps.supports_catalog is False


def test_dask_fixtures_and_schema_operations():
    pytest.importorskip("dask.dataframe")

    from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark
    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    benchmark = MetadataPrimitivesBenchmark()
    adapter = DaskDataFrameAdapter(execution_engine="threaded")
    tables = benchmark.build_complexity_dataframes(adapter, "wide_tables")
    manager = benchmark.get_dataframe_operations(adapter.platform_name)

    assert manager is not None
    wide = tables["stress_wide"]
    assert manager.execute_list_columns(wide).success
    assert manager.execute_get_dtypes(wide).success
    schema = manager.execute_get_schema(wide)
    assert schema.success
    assert all(isinstance(entry["nullable"], bool) for entry in schema.result_data)
    assert manager.execute_row_count(wide).result_data == 1
    assert manager.execute_wide_table_schema(wide).success
