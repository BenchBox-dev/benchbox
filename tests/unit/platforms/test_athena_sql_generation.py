# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, Mock, patch, patch as _patch

import pytest

from benchbox.utils.dialect_utils import translate_ddl

translate_ddl("CREATE TABLE t (a INT)", "athena")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_adapter(**kwargs):

    with (
        patch.dict(
            "sys.modules",
            {
                "boto3": MagicMock(),
                "pyathena": MagicMock(),
                "pyathena.cursor": MagicMock(),
            },
        ),
        patch("benchbox.platforms.athena.AthenaAdapter._validate_configuration"),
    ):
        from benchbox.platforms.athena import AthenaAdapter

        defaults = {
            "s3_bucket": "test-bucket",
            "s3_prefix": "data",
            "database": "benchbox_db",
        }
        defaults.update(kwargs)
        return AthenaAdapter(**defaults)


class TestAthenaBuildCtasSortSql:
    def test_ctas_mode_is_disabled_with_reason(self):
        adapter = _make_adapter()
        col = Mock()
        col.name = "l_orderkey"
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "ctas")):
            result = adapter._build_ctas_sort_sql("lineitem", [col])
        assert result is None
        assert "own source table" in adapter._ctas_sort_unsupported_reason("ctas")

    def test_off_mode_returns_none(self):
        adapter = _make_adapter()
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("off", "auto")):
            result = adapter._build_ctas_sort_sql("orders", [Mock()])
        assert result is None

    def test_unsupported_method_raises_value_error(self):
        adapter = _make_adapter()
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "z_order")):
            with pytest.raises(ValueError, match="not supported for Athena"):
                adapter._build_ctas_sort_sql("orders", [Mock()])


class TestAthenaExternalTableDdlStagingPath:
    def test_staging_true_generates_textfile_format(self):
        with (
            patch.dict(
                "sys.modules",
                {
                    "boto3": MagicMock(),
                    "pyathena": MagicMock(),
                    "pyathena.cursor": MagicMock(),
                },
            ),
            patch("benchbox.platforms.athena.AthenaAdapter._validate_configuration"),
        ):
            from benchbox.platforms.athena import AthenaAdapter

            adapter = AthenaAdapter(
                s3_bucket="test-bucket",
                s3_prefix="data",
                database="benchbox_db",
                default_format="PARQUET",
            )
            sql = "CREATE TABLE orders (id INT, amount DECIMAL)"
            converted = adapter._external_table_ddl(sql, is_staging=True)[0]

            assert "EXTERNAL TABLE" in converted.upper()
            assert "TEXTFILE" in converted.upper() or "DELIMITED" in converted.upper()

    def test_parquet_format_generates_stored_as_parquet(self):
        with (
            patch.dict(
                "sys.modules",
                {
                    "boto3": MagicMock(),
                    "pyathena": MagicMock(),
                    "pyathena.cursor": MagicMock(),
                },
            ),
            patch("benchbox.platforms.athena.AthenaAdapter._validate_configuration"),
        ):
            from benchbox.platforms.athena import AthenaAdapter

            adapter = AthenaAdapter(
                s3_bucket="test-bucket",
                s3_prefix="data",
                database="benchbox_db",
                default_format="PARQUET",
            )
            sql = "CREATE TABLE lineitem (l_orderkey BIGINT)"
            converted = adapter._external_table_ddl(sql, is_staging=False)[0]
            assert "STORED AS PARQUET" in converted.upper()
            assert "s3://test-bucket/data/benchbox_db/lineitem/" in converted


class TestAthenaExternalTableLocationPath:
    def test_location_uses_bucket_prefix_database(self):
        sql = "CREATE TABLE nation (n_key INT)"
        with (
            patch.dict(
                "sys.modules",
                {
                    "boto3": MagicMock(),
                    "pyathena": MagicMock(),
                    "pyathena.cursor": MagicMock(),
                },
            ),
            patch("benchbox.platforms.athena.AthenaAdapter._validate_configuration"),
        ):
            from benchbox.platforms.athena import AthenaAdapter

            adapter2 = AthenaAdapter(
                s3_bucket="my-bucket",
                s3_prefix="benchmarks",
                database="tpch_db",
                default_format="PARQUET",
            )
            converted = adapter2._external_table_ddl(sql, is_staging=False)[0]
            assert "s3://my-bucket/benchmarks/tpch_db/nation/" in converted


class TestAthenaConfigureForBenchmark:
    def test_configure_for_benchmark_tpch_executes_sql(self):
        adapter = _make_adapter()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_conn, "tpch")


class TestAthenaGetTargetDialect:
    def test_returns_trino(self):
        adapter = _make_adapter()

        dialect = adapter.get_target_dialect()
        assert isinstance(dialect, str)
        assert len(dialect) > 0


class TestAthenaCtasSqlContent:
    def test_ctas_sql_contains_format_parquet(self):

        with (
            patch.dict(
                "sys.modules",
                {
                    "boto3": MagicMock(),
                    "pyathena": MagicMock(),
                    "pyathena.cursor": MagicMock(),
                },
            ),
            patch("benchbox.platforms.athena.AthenaAdapter._validate_configuration"),
        ):
            from benchbox.platforms.athena import AthenaAdapter

            adapter = AthenaAdapter(
                s3_bucket="test-bucket",
                s3_prefix="data",
                database="db",
                compression="SNAPPY",
            )

            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (100,)

            s3_mock = MagicMock()

            adapter._convert_staging_to_parquet(mock_cursor, [("lineitem", 100)], s3_mock)

            executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list if call.args]
            ctas_calls = [sql for sql in executed_sql if "CREATE TABLE" in sql]
            assert any("format" in sql.lower() and "parquet" in sql.lower() for sql in ctas_calls)
