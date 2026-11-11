import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from benchbox.core.loaded_tables import is_data_loading_skipped, require_loaded_tables

pytestmark = [pytest.mark.unit, pytest.mark.fast]

OBT = SimpleNamespace(REQUIRED_LOADED_TABLES=("tpcds_sales_returns_obt",))


def test_passes_when_required_table_has_rows():
    require_loaded_tables(OBT, {"TPCDS_SALES_RETURNS_OBT": 2_880_404})


def test_fails_when_the_source_tables_were_loaded_instead():
    with pytest.raises(RuntimeError, match="tpcds_sales_returns_obt was not loaded"):
        require_loaded_tables(OBT, {"CALL_CENTER": 6, "STORE_SALES": 2_880_404})


def test_fails_when_required_table_is_empty():
    with pytest.raises(RuntimeError, match="has 0 rows"):
        require_loaded_tables(OBT, {"tpcds_sales_returns_obt": 0})


@pytest.mark.parametrize("stats", [{}, {"a": 0}, {"a": -5}, {"a": True}, {"a": "n/a"}])
def test_unpinned_benchmarks_reject_loads_without_a_table_holding_rows(stats):
    with pytest.raises(RuntimeError, match="no rows were loaded for any table"):
        require_loaded_tables(SimpleNamespace(), stats)


def test_unpinned_benchmarks_pass_when_one_table_holds_rows_despite_error_sentinels():
    require_loaded_tables(SimpleNamespace(), {"loaded": 50, "failed": -100})


def test_benchmarks_without_requirements_reject_vacuous_loads():
    with pytest.raises(RuntimeError, match="no rows were loaded for any table"):
        require_loaded_tables(SimpleNamespace(), {})
    with pytest.raises(RuntimeError, match="no rows were loaded for any table"):
        require_loaded_tables(SimpleNamespace(), {"t": 0})
    require_loaded_tables(SimpleNamespace(), {"t": 3})


def test_benchmarks_skipping_data_loading_are_not_checked():
    skipped = SimpleNamespace(SKIP_DATA_LOADING=True)
    require_loaded_tables(skipped, {})
    require_loaded_tables(skipped, None)


def test_public_obt_wrapper_exposes_the_contract():
    from benchbox.tpcds_obt import TPCDSOBT

    assert TPCDSOBT.REQUIRED_LOADED_TABLES == ("tpcds_sales_returns_obt",)
    assert TPCDSOBT.GENERATES_OWN_OUTPUT is True
    with pytest.raises(RuntimeError, match="was not loaded"):
        require_loaded_tables(TPCDSOBT.__new__(TPCDSOBT), {"CALL_CENTER": 6})


def test_manifest_reuse_rejects_the_source_manifest_for_own_output_benchmarks():
    from benchbox.core.runner.runner import _resolve_manifest_allowed_names
    from benchbox.core.schemas import BenchmarkConfig

    obt = SimpleNamespace(GENERATES_OWN_OUTPUT=True, get_data_source_benchmark=lambda: "tpcds")
    sharer = SimpleNamespace(get_data_source_benchmark=lambda: "tpch")
    config = BenchmarkConfig(name="tpcds_obt", display_name="OBT", scale_factor=1.0)

    assert _resolve_manifest_allowed_names(obt, config) == {"tpcds_obt"}
    assert "tpch" in _resolve_manifest_allowed_names(sharer, config)


def test_duckdb_run_over_an_empty_manifest_fails_instead_of_validating_vacuously(tmp_path):
    import json

    pytest.importorskip("duckdb")
    from benchbox import TPCH
    from benchbox.platforms.duckdb import DuckDBAdapter
    from benchbox.utils.datagen_version import current_datagen_stamp

    (tmp_path / "customer.tbl.zst").write_bytes(b"\x28\xb5\x2f\xfd" + b"x" * 64)
    manifest = {"benchmark": "tpch", "scale_factor": 0.01, "tables": {}, **current_datagen_stamp("tpch")}
    (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    benchmark = TPCH(scale_factor=0.01, output_dir=tmp_path)
    adapter = DuckDBAdapter(database_path=":memory:")
    connection = adapter.create_connection()
    try:
        with pytest.raises((RuntimeError, ValueError), match="No data files found|no rows were loaded"):
            adapter._setup_fresh_database_phases(benchmark, connection, None)
    finally:
        adapter.close_connection(connection)


def test_none_requirements_reject_loads_without_rows():
    benchmark = SimpleNamespace(REQUIRED_LOADED_TABLES=None)
    with pytest.raises(RuntimeError, match="no rows were loaded for any table"):
        require_loaded_tables(benchmark, {})
    with pytest.raises(RuntimeError, match="no rows were loaded for any table"):
        require_loaded_tables(benchmark, {"t": 0})
    require_loaded_tables(benchmark, {"t": 3})


@pytest.mark.parametrize("required", ["lineitem", 42, {"lineitem": 1}])
def test_non_collection_requirements_reject_vacuous_loads(required):
    benchmark = SimpleNamespace(REQUIRED_LOADED_TABLES=required)
    with pytest.raises(RuntimeError, match="no rows were loaded for any table"):
        require_loaded_tables(benchmark, {"t": 0})


def test_partial_load_passes_but_warns_on_zero_row_tables(caplog):
    with caplog.at_level(logging.WARNING, logger="benchbox.core.loaded_tables"):
        require_loaded_tables(SimpleNamespace(), {"loaded": 50, "failed": 0})
    assert any("failed" in record.message for record in caplog.records)


def test_mock_benchmark_does_not_silently_skip_checks():
    benchmark = MagicMock()
    assert is_data_loading_skipped(benchmark) is False
    with pytest.raises(RuntimeError, match="no rows were loaded for any table"):
        require_loaded_tables(benchmark, {})


def test_skip_helper_reads_class_flag_first():
    class SchemaOnly:
        SKIP_DATA_LOADING = True

    class WithData:
        SKIP_DATA_LOADING = False

    assert is_data_loading_skipped(SchemaOnly()) is True
    assert is_data_loading_skipped(WithData()) is False


def test_skip_helper_honors_instance_flag_on_real_objects_only():
    assert is_data_loading_skipped(SimpleNamespace(SKIP_DATA_LOADING=True)) is True
    assert is_data_loading_skipped(SimpleNamespace()) is False


def test_all_load_paths_share_one_skip_helper():
    import benchbox.core.loaded_tables as loaded_tables
    import benchbox.platforms.base.adapter as adapter_module
    import benchbox.platforms.base.data_loading as data_loading_module
    import benchbox.platforms.base.spark_execution_mixin as spark_mixin

    assert data_loading_module.is_data_loading_skipped is loaded_tables.is_data_loading_skipped
    assert adapter_module.is_data_loading_skipped is loaded_tables.is_data_loading_skipped
    assert spark_mixin.is_data_loading_skipped is loaded_tables.is_data_loading_skipped


