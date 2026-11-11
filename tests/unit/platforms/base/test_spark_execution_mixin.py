from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from benchbox.platforms.base.spark_execution_mixin import SparkDataLoadMixin, SparkQueryExecutionMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _DummySparkAdapter(SparkDataLoadMixin):
    def __init__(self) -> None:
        self.logger = MagicMock()
        self.platform_name = "spark"
        self.table_mode = "native"
        self.platform_config = {}
        self.requested_table_format = None

    def log_verbose(self, msg: str) -> None:
        return None

    def _normalize_and_validate_file_paths(self, file_paths):
        return [Path(path) for path in (file_paths if isinstance(file_paths, list) else [file_paths])]


def _make_dataframe(columns: list[str] | None = None) -> MagicMock:
    df = MagicMock()
    df.columns = columns or []
    df.cache.return_value = df
    df.count.return_value = 5
    df.write.mode.return_value = df.write
    return df


def _run_load(adapter: _DummySparkAdapter, spark: MagicMock, table_path: Path) -> None:
    benchmark = MagicMock()
    with patch("benchbox.platforms.base.data_loading.DataSourceResolver.resolve") as mock_resolve:
        mock_resolve.return_value = SimpleNamespace(source_type="benchmark_tables", tables={"orders": [table_path]})
        stats, _, _ = adapter._load_data_spark(benchmark, table_path.parent, spark)
    assert stats["orders"] == 5


def test_load_data_delegates_to_shared_spark_loader(tmp_path: Path) -> None:
    adapter = _DummySparkAdapter()
    adapter._load_data_spark = MagicMock(return_value=({"orders": 1}, 0.1, None))
    benchmark = MagicMock()
    connection = MagicMock()

    assert adapter.load_data(benchmark, connection, tmp_path) == ({"orders": 1}, 0.1, None)
    adapter._load_data_spark.assert_called_once_with(benchmark, tmp_path, connection)


def test_load_data_spark_uses_delta_reader_for_delta_directory(tmp_path: Path) -> None:
    adapter = _DummySparkAdapter()
    spark = MagicMock()
    delta_dir = tmp_path / "orders"
    (delta_dir / "_delta_log").mkdir(parents=True)
    delta_df = _make_dataframe(["id"])
    spark.read.format.return_value.load.return_value = delta_df

    _run_load(adapter, spark, delta_dir)

    spark.read.format.assert_called_once_with("delta")
    spark.read.format.return_value.load.assert_called_once_with(str(delta_dir))


def test_load_data_spark_uses_iceberg_reader_for_iceberg_directory(tmp_path: Path) -> None:
    adapter = _DummySparkAdapter()
    spark = MagicMock()
    iceberg_dir = tmp_path / "orders"
    (iceberg_dir / "metadata").mkdir(parents=True)
    iceberg_df = _make_dataframe(["id"])
    spark.read.format.return_value.load.return_value = iceberg_df

    _run_load(adapter, spark, iceberg_dir)

    spark.read.format.assert_called_once_with("iceberg")
    spark.read.format.return_value.load.assert_called_once_with(str(iceberg_dir))


def test_load_data_spark_uses_hudi_reader_for_hudi_directory(tmp_path: Path) -> None:
    adapter = _DummySparkAdapter()
    spark = MagicMock()
    hudi_dir = tmp_path / "orders"
    (hudi_dir / ".hoodie").mkdir(parents=True)
    hudi_df = _make_dataframe(["id"])
    spark.read.format.return_value.load.return_value = hudi_df

    _run_load(adapter, spark, hudi_dir)

    spark.read.format.assert_called_once_with("hudi")
    spark.read.format.return_value.load.assert_called_once_with(str(hudi_dir))


def test_load_data_spark_parquet_path_unchanged(tmp_path: Path) -> None:
    adapter = _DummySparkAdapter()
    spark = MagicMock()
    parquet_path = tmp_path / "orders.parquet"
    parquet_path.write_bytes(b"PAR1")
    parquet_df = _make_dataframe(["id"])
    spark.read.parquet.return_value = parquet_df

    _run_load(adapter, spark, parquet_path)

    spark.read.parquet.assert_called_once_with(str(parquet_path))
    spark.read.format.assert_not_called()


def test_load_data_spark_preserves_mixed_case_table_names(tmp_path: Path) -> None:
    adapter = _DummySparkAdapter()
    spark = MagicMock()
    parquet_path = tmp_path / "DimCustomer.parquet"
    parquet_path.write_bytes(b"PAR1")
    spark.read.parquet.return_value = _make_dataframe(["id"])

    with patch("benchbox.platforms.base.data_loading.DataSourceResolver.resolve") as mock_resolve:
        mock_resolve.return_value = SimpleNamespace(
            source_type="benchmark_tables", tables={"DimCustomer": [parquet_path]}
        )
        stats, _, _ = adapter._load_data_spark(MagicMock(), tmp_path, spark)

    assert stats["DimCustomer"] == 5
    spark.table.assert_called_with("DimCustomer")
    spark.read.parquet.return_value.write.mode.return_value.insertInto.assert_called_once_with("DimCustomer")


def test_cast_dataframe_to_schema_parses_array_columns() -> None:
    adapter = _DummySparkAdapter()
    df = _make_dataframe(["id", "embedding"])
    fields = [
        SimpleNamespace(name="id", dataType=SimpleNamespace(typeName=lambda: "integer")),
        SimpleNamespace(
            name="embedding",
            dataType=SimpleNamespace(typeName=lambda: "array", simpleString=lambda: "array<float>"),
        ),
    ]
    schema = SimpleNamespace(fields=fields)

    with (
        patch("pyspark.sql.functions.col") as mock_col,
        patch("pyspark.sql.functions.from_json") as mock_from_json,
    ):
        id_expr = MagicMock()
        array_col = MagicMock()
        mock_col.side_effect = [id_expr, array_col]
        id_expr.cast.return_value.alias.return_value = "id_expr"
        array_col.cast.return_value = "array_as_string"
        mock_from_json.return_value.alias.return_value = "array_expr"

        adapter._cast_dataframe_to_schema(df, schema)

    mock_from_json.assert_called_once_with("array_as_string", "array<float>")
    df.select.assert_called_once_with("id_expr", "array_expr")


def test_load_data_spark_passes_platform_name_to_resolver(tmp_path: Path) -> None:

    adapter = _DummySparkAdapter()
    adapter.platform_name = "databricks"
    spark = MagicMock()
    parquet_path = tmp_path / "orders.parquet"
    parquet_path.write_bytes(b"PAR1")
    spark.read.parquet.return_value = _make_dataframe(["id"])

    with patch("benchbox.platforms.base.data_loading.DataSourceResolver", autospec=True) as mock_cls:
        mock_resolver = MagicMock()
        mock_cls.return_value = mock_resolver
        mock_resolver.resolve.return_value = SimpleNamespace(
            source_type="benchmark_tables", tables={"orders": [parquet_path]}
        )
        adapter._load_data_spark(MagicMock(), tmp_path, spark)

    _, kwargs = mock_cls.call_args
    assert kwargs.get("platform_name") == "databricks"


def test_load_data_spark_passes_config_to_resolver(tmp_path: Path) -> None:

    adapter = _DummySparkAdapter()
    adapter.platform_config = {"staging_root": "s3://bucket/prefix"}
    spark = MagicMock()
    parquet_path = tmp_path / "orders.parquet"
    parquet_path.write_bytes(b"PAR1")
    spark.read.parquet.return_value = _make_dataframe(["id"])

    with patch("benchbox.platforms.base.data_loading.DataSourceResolver", autospec=True) as mock_cls:
        mock_resolver = MagicMock()
        mock_cls.return_value = mock_resolver
        mock_resolver.resolve.return_value = SimpleNamespace(
            source_type="benchmark_tables", tables={"orders": [parquet_path]}
        )
        adapter._load_data_spark(MagicMock(), tmp_path, spark)

    _, kwargs = mock_cls.call_args
    assert kwargs.get("platform_config") == {"staging_root": "s3://bucket/prefix"}


def test_load_data_spark_propagates_resolver_error_without_cleanup_failure(tmp_path: Path) -> None:

    adapter = _DummySparkAdapter()
    spark = MagicMock()

    with patch("benchbox.platforms.base.data_loading.DataSourceResolver.resolve") as mock_resolve:
        mock_resolve.side_effect = RuntimeError("resolver failed")

        with pytest.raises(RuntimeError, match="resolver failed"):
            adapter._load_data_spark(MagicMock(), tmp_path, spark)


def test_load_data_spark_skips_declared_no_data_benchmark(tmp_path: Path) -> None:
    adapter = _DummySparkAdapter()

    class SchemaOnlyBenchmark:
        SKIP_DATA_LOADING = True

    benchmark = SchemaOnlyBenchmark()

    with patch("benchbox.platforms.base.data_loading.DataSourceResolver.resolve") as mock_resolve:
        mock_resolve.return_value = SimpleNamespace(source_type="benchmark_tables", tables={})
        stats, _, timings = adapter._load_data_spark(benchmark, tmp_path, MagicMock())

    assert stats == {}
    assert timings == {}
    adapter.logger.info.assert_called_with("Benchmark is schema-only; skipping Spark data load")


def test_load_data_spark_rejects_missing_files_for_own_data_benchmark(tmp_path: Path) -> None:
    adapter = _DummySparkAdapter()
    benchmark = MagicMock()
    benchmark.get_data_source_benchmark.return_value = None

    with patch("benchbox.platforms.base.data_loading.DataSourceResolver.resolve") as mock_resolve:
        mock_resolve.return_value = SimpleNamespace(source_type="benchmark_tables", tables={})
        with pytest.raises(ValueError, match="No data files found"):
            adapter._load_data_spark(benchmark, tmp_path, MagicMock())


class _NoCacheAdapter(_DummySparkAdapter):
    _df_caching_supported: bool = False

    def __init__(self) -> None:
        super().__init__()
        self.log_verbose = MagicMock()


def test_load_data_spark_no_cache_writes_before_count(tmp_path: Path) -> None:

    adapter = _NoCacheAdapter()
    spark = MagicMock()
    parquet_path = tmp_path / "orders.parquet"
    parquet_path.write_bytes(b"PAR1")
    parquet_df = _make_dataframe(["id"])
    spark.read.parquet.return_value = parquet_df
    count_dfs = [MagicMock(), MagicMock()]
    count_dfs[0].collect.return_value = [(0,)]
    count_dfs[1].collect.return_value = [(7,)]
    spark.sql.side_effect = count_dfs

    with patch("benchbox.platforms.base.data_loading.DataSourceResolver.resolve") as mock_resolve:
        mock_resolve.return_value = SimpleNamespace(source_type="benchmark_tables", tables={"orders": [parquet_path]})
        stats, _, _ = adapter._load_data_spark(MagicMock(), tmp_path, spark)

    parquet_df.count.assert_not_called()

    parquet_df.write.mode.assert_called_once_with("append")
    assert spark.sql.call_count == 2
    assert all("COUNT(*)" in call.args[0] for call in spark.sql.call_args_list)
    assert stats["orders"] == 7


def test_load_data_spark_no_cache_counts_chunked_table_once(tmp_path: Path) -> None:

    adapter = _NoCacheAdapter()
    spark = MagicMock()
    parquet_paths = [tmp_path / "orders_1.parquet", tmp_path / "orders_2.parquet"]
    for parquet_path in parquet_paths:
        parquet_path.write_bytes(b"PAR1")

    first_df = _make_dataframe(["id"])
    second_df = _make_dataframe(["id"])
    spark.read.parquet.side_effect = [first_df, second_df]

    pre_count_df = MagicMock()
    pre_count_df.collect.return_value = [(3,)]
    post_count_df = MagicMock()
    post_count_df.collect.return_value = [(15,)]
    spark.sql.side_effect = [pre_count_df, post_count_df]

    with patch("benchbox.platforms.base.data_loading.DataSourceResolver.resolve") as mock_resolve:
        mock_resolve.return_value = SimpleNamespace(source_type="benchmark_tables", tables={"orders": parquet_paths})
        stats, _, _ = adapter._load_data_spark(MagicMock(), tmp_path, spark)

    first_df.count.assert_not_called()
    second_df.count.assert_not_called()
    first_df.write.mode.assert_called_once_with("append")
    second_df.write.mode.assert_called_once_with("append")
    assert spark.sql.call_count == 2
    assert stats["orders"] == 12

    verbose_msgs = [c.args[0] for c in adapter.log_verbose.call_args_list]
    assert any("Wrote chunk 1/2 for orders" in m for m in verbose_msgs)
    assert any("Wrote chunk 2/2 for orders" in m for m in verbose_msgs)


def test_load_data_spark_no_cache_tolerates_missing_preload_count(tmp_path: Path, caplog) -> None:

    import logging

    adapter = _NoCacheAdapter()
    spark = MagicMock()
    parquet_path = tmp_path / "orders.parquet"
    parquet_path.write_bytes(b"PAR1")
    parquet_df = _make_dataframe(["id"])
    spark.read.parquet.return_value = parquet_df

    post_count_df = MagicMock()
    post_count_df.collect.return_value = [(7,)]
    spark.sql.side_effect = [RuntimeError("table not found"), post_count_df]

    with (
        caplog.at_level(logging.DEBUG, logger="benchbox.platforms.base.spark_execution_mixin"),
        patch("benchbox.platforms.base.data_loading.DataSourceResolver.resolve") as mock_resolve,
    ):
        mock_resolve.return_value = SimpleNamespace(source_type="benchmark_tables", tables={"orders": [parquet_path]})
        stats, _, _ = adapter._load_data_spark(MagicMock(), tmp_path, spark)

    parquet_df.count.assert_not_called()
    parquet_df.write.mode.assert_called_once_with("append")
    assert spark.sql.call_count == 2
    assert stats["orders"] == 7
    assert "Row count unavailable for table 'orders'; assuming 0" in caplog.text


def test_load_data_spark_no_cache_postload_count_failure_marks_table_failed(tmp_path: Path) -> None:

    adapter = _NoCacheAdapter()
    spark = MagicMock()
    parquet_path = tmp_path / "orders.parquet"
    parquet_path.write_bytes(b"PAR1")
    parquet_df = _make_dataframe(["id"])
    spark.read.parquet.return_value = parquet_df

    pre_count_df = MagicMock()
    pre_count_df.collect.return_value = [(3,)]
    spark.sql.side_effect = [pre_count_df, RuntimeError("session closed")]

    with patch("benchbox.platforms.base.data_loading.DataSourceResolver.resolve") as mock_resolve:
        mock_resolve.return_value = SimpleNamespace(source_type="benchmark_tables", tables={"orders": [parquet_path]})
        stats, _, _ = adapter._load_data_spark(MagicMock(), tmp_path, spark)

    parquet_df.count.assert_not_called()
    parquet_df.write.mode.assert_called_once_with("append")
    assert spark.sql.call_count == 2
    assert stats["orders"] == 0
    adapter.logger.error.assert_any_call("Failed to load orders: session closed")


def test_load_data_spark_no_cache_negative_row_delta_warns(tmp_path: Path) -> None:

    adapter = _NoCacheAdapter()
    spark = MagicMock()
    parquet_path = tmp_path / "orders.parquet"
    parquet_path.write_bytes(b"PAR1")
    parquet_df = _make_dataframe(["id"])
    spark.read.parquet.return_value = parquet_df

    pre_count_df = MagicMock()
    pre_count_df.collect.return_value = [(10,)]
    post_count_df = MagicMock()
    post_count_df.collect.return_value = [(3,)]
    spark.sql.side_effect = [pre_count_df, post_count_df]

    with patch("benchbox.platforms.base.data_loading.DataSourceResolver.resolve") as mock_resolve:
        mock_resolve.return_value = SimpleNamespace(source_type="benchmark_tables", tables={"orders": [parquet_path]})
        stats, _, _ = adapter._load_data_spark(MagicMock(), tmp_path, spark)

    assert stats["orders"] == 0
    adapter.logger.warning.assert_called_once_with(
        "Negative row delta for %s (%d -> %d); reporting 0",
        "orders",
        10,
        3,
    )


def test_load_data_spark_csv_compat_temp_dir_lives_under_data_dir(tmp_path: Path) -> None:

    adapter = _CsvExtAdapter()
    spark = MagicMock()
    spark.table.side_effect = RuntimeError("table not found")
    source_path = tmp_path / "orders.tbl.zst"
    source_path.write_bytes(b"x")
    csv_df = _make_dataframe(["id"])
    spark.read.option.return_value = spark.read

    def _csv(path: str):
        csv_path = Path(path)
        assert tmp_path in csv_path.parents
        assert csv_path.name == "orders.csv.zst"
        assert csv_path.is_dir()
        compat_file = csv_path / "orders.csv.zst"
        assert compat_file.exists()
        assert compat_file.samefile(source_path)
        return csv_df

    spark.read.csv.side_effect = _csv

    with patch("benchbox.platforms.base.data_loading.DataSourceResolver.resolve") as mock_resolve:
        mock_resolve.return_value = SimpleNamespace(
            source_type="benchmark_tables",
            tables={"orders": [source_path]},
            table_metadata={},
        )
        stats, _, _ = adapter._load_data_spark(MagicMock(), tmp_path, spark)

    assert stats["orders"] == 5


def test_row_count_escapes_backticks_in_table_name() -> None:

    spark = MagicMock()
    spark.sql.return_value.collect.return_value = [(42,)]

    result = SparkDataLoadMixin._row_count(spark, "my`table")

    assert result == 42
    sql_arg = spark.sql.call_args[0][0]
    assert "my``table" in sql_arg
    assert sql_arg == "SELECT COUNT(*) FROM `my``table`"


class _CsvExtAdapter(_DummySparkAdapter):
    _requires_csv_extension: bool = True


def test_csv_compat_path_noop_when_flag_false(tmp_path: Path) -> None:

    adapter = _DummySparkAdapter()
    dat = tmp_path / "orders.dat.zst"
    dat.write_bytes(b"x")
    assert adapter._csv_compat_path(dat, tmp_path) == dat


def test_csv_compat_path_symlinks_dat_zst_to_csv_zst(tmp_path: Path) -> None:
    adapter = _CsvExtAdapter()
    dat = tmp_path / "orders.dat.zst"
    dat.write_bytes(b"x")
    link_dir = tmp_path / "links"
    link_dir.mkdir()

    result = adapter._csv_compat_path(dat, link_dir)

    assert result.name == "orders.csv.zst"
    assert result.is_dir()
    assert (result / "orders.csv.zst").samefile(dat)


def test_csv_compat_path_symlinks_tbl_to_csv(tmp_path: Path) -> None:
    adapter = _CsvExtAdapter()
    tbl = tmp_path / "customer.tbl"
    tbl.write_bytes(b"x")
    link_dir = tmp_path / "links"
    link_dir.mkdir()

    result = adapter._csv_compat_path(tbl, link_dir)

    assert result.name == "customer.csv"
    assert result.is_dir()
    assert (result / "customer.csv").samefile(tbl)


def test_csv_compat_path_preserves_csv_extension(tmp_path: Path) -> None:

    adapter = _CsvExtAdapter()
    csv = tmp_path / "orders.csv"
    csv.write_bytes(b"x")
    link_dir = tmp_path / "links"
    link_dir.mkdir()

    result = adapter._csv_compat_path(csv, link_dir)

    assert result.name == "orders.csv"
    assert result.is_dir()
    assert (result / "orders.csv").samefile(csv)


def test_csv_compat_path_preserves_csv_zst_extension(tmp_path: Path) -> None:

    adapter = _CsvExtAdapter()
    csv_zst = tmp_path / "orders.csv.zst"
    csv_zst.write_bytes(b"x")
    link_dir = tmp_path / "links"
    link_dir.mkdir()

    result = adapter._csv_compat_path(csv_zst, link_dir)

    assert result.name == "orders.csv.zst"
    assert result.is_dir()
    assert (result / "orders.csv.zst").samefile(csv_zst)


def test_csv_compat_path_xz_extension(tmp_path: Path) -> None:

    adapter = _CsvExtAdapter()
    dat_xz = tmp_path / "orders.dat.xz"
    dat_xz.write_bytes(b"x")
    link_dir = tmp_path / "links"
    link_dir.mkdir()

    result = adapter._csv_compat_path(dat_xz, link_dir)

    assert result.name == "orders.csv.xz"
    assert result.is_dir()
    assert (result / "orders.csv.xz").samefile(dat_xz)


def test_csv_compat_path_multipart_tpch_chunks_get_unique_names(tmp_path: Path) -> None:

    adapter = _CsvExtAdapter()
    link_dir = tmp_path / "links"
    link_dir.mkdir()

    chunk1 = tmp_path / "customer.tbl.1.zst"
    chunk2 = tmp_path / "customer.tbl.2.zst"
    chunk1.write_bytes(b"a")
    chunk2.write_bytes(b"b")

    result1 = adapter._csv_compat_path(chunk1, link_dir)
    result2 = adapter._csv_compat_path(chunk2, link_dir)

    assert result1.name == "customer.1.csv.zst"
    assert result2.name == "customer.2.csv.zst"
    assert result1.name != result2.name, "chunk symlinks must be distinct"
    assert result1.is_dir() and (result1 / "customer.1.csv.zst").samefile(chunk1)
    assert result2.is_dir() and (result2 / "customer.2.csv.zst").samefile(chunk2)


def test_csv_compat_path_rejects_unknown_compression(tmp_path: Path) -> None:

    adapter = _CsvExtAdapter()
    dat = tmp_path / "orders.dat"
    dat.write_bytes(b"x")
    link_dir = tmp_path / "links"
    link_dir.mkdir()

    with patch("benchbox.platforms.base.spark_execution_mixin.detect_compression", return_value="brotli"):
        with pytest.raises(ValueError, match="Unsupported CSV compression type 'brotli'"):
            adapter._csv_compat_path(dat, link_dir)


def test_csv_compat_path_warns_on_symlink_collision(tmp_path: Path, caplog) -> None:

    import logging

    adapter = _CsvExtAdapter()
    link_dir = tmp_path / "links"
    link_dir.mkdir()

    dat1 = tmp_path / "orders.dat"
    dat1.write_bytes(b"first")
    compat_dir = link_dir / "orders.csv"
    compat_dir.mkdir()
    link = compat_dir / "orders.csv"
    link.hardlink_to(dat1.resolve())

    dat2_dir = tmp_path / "other"
    dat2_dir.mkdir()
    dat2 = dat2_dir / "orders.dat"
    dat2.write_bytes(b"second")

    with caplog.at_level(logging.WARNING, logger="benchbox.platforms.base.spark_execution_mixin"):
        result = adapter._csv_compat_path(dat2, link_dir)

    assert result == compat_dir
    assert "CSV compatibility path collision" in caplog.text


def test_csv_compat_path_wraps_plain_csv_for_sail_directory_scan(tmp_path: Path) -> None:

    adapter = _CsvExtAdapter()
    csv = tmp_path / "customer.csv"
    csv.write_bytes(b"x")
    link_dir = tmp_path / "links"
    link_dir.mkdir()

    result = adapter._csv_compat_path(csv, link_dir)

    assert result.is_dir()
    assert result.name == "customer.csv"
    assert (result / "customer.csv").samefile(csv)


class _DummyQueryAdapter(SparkQueryExecutionMixin):
    def __init__(self) -> None:
        self.logger = MagicMock()

    def log_verbose(self, msg: str) -> None:
        pass


def test_execute_query_delegates_to_shared_spark_executor() -> None:
    adapter = _DummyQueryAdapter()
    adapter._execute_query_spark = MagicMock(return_value={"status": "ok"})
    connection = MagicMock()

    assert adapter.execute_query(connection, "select 1", "Q1", benchmark_type="tpch", scale_factor=0.01) == {
        "status": "ok"
    }
    adapter._execute_query_spark.assert_called_once_with(
        connection=connection,
        query="select 1",
        query_id="Q1",
        benchmark_type="tpch",
        scale_factor=0.01,
        validate_row_count=True,
        stream_id=None,
    )


def _make_spark(table_rows: dict[str, list] | None = None) -> MagicMock:

    spark = MagicMock()
    table_rows = table_rows or {}

    def _sql(query: str):
        result_df = MagicMock()
        for name, rows in table_rows.items():
            if name in query:
                result_df.collect.return_value = rows
                return result_df

        result_df.collect.side_effect = Exception(f"Table not found in: {query}")
        return result_df

    spark.sql.side_effect = _sql
    return spark


def test_validate_data_integrity_all_accessible() -> None:
    adapter = _DummyQueryAdapter()
    spark = _make_spark({"orders": [[1]], "lineitem": [[1]]})
    table_stats = {"orders": 10, "lineitem": 50}

    status, details = adapter._validate_data_integrity(None, spark, table_stats)

    assert status == "PASSED"
    assert set(details["accessible_tables"]) == {"orders", "lineitem"}
    assert details["constraints_enabled"] is True
    assert "inaccessible_tables" not in details


def test_validate_data_integrity_some_inaccessible() -> None:
    adapter = _DummyQueryAdapter()

    spark = _make_spark({"orders": [[1]]})
    table_stats = {"orders": 10, "lineitem": 50}

    status, details = adapter._validate_data_integrity(None, spark, table_stats)

    assert status == "FAILED"
    assert "lineitem" in details["inaccessible_tables"]
    assert details["constraints_enabled"] is False


def test_validate_data_integrity_all_inaccessible() -> None:
    adapter = _DummyQueryAdapter()
    spark = _make_spark()
    table_stats = {"orders": 0, "lineitem": 0}

    status, details = adapter._validate_data_integrity(None, spark, table_stats)

    assert status == "FAILED"
    assert set(details["inaccessible_tables"]) == {"orders", "lineitem"}


def test_validate_data_integrity_empty_table_stats() -> None:
    adapter = _DummyQueryAdapter()
    spark = _make_spark()

    status, details = adapter._validate_data_integrity(None, spark, {})

    assert status == "PASSED"
    assert details["accessible_tables"] == []


def test_validate_data_integrity_logs_inaccessible_table(caplog) -> None:
    import logging

    adapter = _DummyQueryAdapter()
    spark = _make_spark()
    table_stats = {"orders": 0}

    with caplog.at_level(logging.DEBUG):
        adapter._validate_data_integrity(None, spark, table_stats)

    assert True


def test_validate_data_integrity_spark_sql_exception_marks_table_inaccessible() -> None:

    adapter = _DummyQueryAdapter()
    broken_spark = MagicMock()
    broken_spark.sql.side_effect = RuntimeError("session closed")
    table_stats = {"orders": 10}

    status, details = adapter._validate_data_integrity(None, broken_spark, table_stats)

    assert status == "FAILED"
    assert "orders" in details["inaccessible_tables"]


def test_get_table_row_count_returns_count() -> None:
    adapter = _DummyQueryAdapter()
    spark = MagicMock()
    spark.sql.return_value.collect.return_value = [[42]]

    assert adapter.get_table_row_count(spark, "orders") == 42


def test_get_table_row_count_empty_result() -> None:
    adapter = _DummyQueryAdapter()
    spark = MagicMock()
    spark.sql.return_value.collect.return_value = []

    assert adapter.get_table_row_count(spark, "orders") == 0


def test_get_table_row_count_exception_returns_zero() -> None:
    adapter = _DummyQueryAdapter()
    spark = MagicMock()
    spark.sql.side_effect = Exception("table not found")

    assert adapter.get_table_row_count(spark, "missing_table") == 0


def test_get_table_row_count_escapes_backticks() -> None:

    adapter = _DummyQueryAdapter()
    spark = MagicMock()
    spark.sql.return_value.collect.return_value = [[99]]

    adapter.get_table_row_count(spark, "my`table")

    sql_arg = spark.sql.call_args[0][0]
    assert sql_arg == "SELECT COUNT(*) FROM `my``table`"


def test_get_table_row_count_uses_spark_sql_not_cursor() -> None:

    adapter = _DummyQueryAdapter()
    spark = MagicMock()
    spark.sql.return_value.collect.return_value = [[99]]

    adapter.get_table_row_count(spark, "orders")

    spark.sql.assert_called_once()
    assert "orders" in spark.sql.call_args[0][0]
    spark.cursor.assert_not_called()


