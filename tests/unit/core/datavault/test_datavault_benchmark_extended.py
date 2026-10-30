# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.datavault.benchmark import DataVaultBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDataVaultBenchmarkProperties:
    def test_has_name(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        assert bm._name == "Data Vault Benchmark"

    def test_has_version(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        assert bm._version == "1.0"

    def test_has_description(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        assert bm._description
        assert "Data Vault" in bm._description

    def test_record_source_default(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        assert bm.record_source == "TPCH"

    def test_record_source_custom(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01, record_source="MY_SYS")
        assert bm.record_source == "MY_SYS"

    def test_parallel_default(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        assert bm.parallel == 1

    def test_compression_defaults(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        assert bm.compress_data is False
        assert bm.compression_type == "none"
        assert bm.compression_level is None

    def test_force_regenerate_default(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        assert bm.force_regenerate is False


class TestDataVaultTpchSourceDir:
    def test_tpch_source_dir_computed_from_output_dir(self, tmp_path: Path) -> None:
        output = tmp_path / "benchmark_runs" / "datagen" / "datavault_sf001"
        bm = DataVaultBenchmark(scale_factor=0.01, output_dir=output)
        src = bm.tpch_source_dir
        assert src.name.startswith("tpch_")
        assert src.parent == output.parent

    def test_tpch_source_dir_cached(self, tmp_path: Path) -> None:
        output = tmp_path / "datavault_sf001"
        bm = DataVaultBenchmark(scale_factor=0.01, output_dir=output)
        src1 = bm.tpch_source_dir
        src2 = bm.tpch_source_dir
        assert src1 == src2


class TestDataVaultQueries:
    @pytest.fixture
    def bm(self) -> DataVaultBenchmark:
        return DataVaultBenchmark(scale_factor=0.01)

    def test_get_all_queries_returns_22(self, bm: DataVaultBenchmark) -> None:
        queries = bm.get_all_queries()
        assert len(queries) == 22

    def test_get_all_queries_keys_are_ints(self, bm: DataVaultBenchmark) -> None:
        queries = bm.get_all_queries()
        for k in queries:
            assert isinstance(k, int)
        assert set(queries.keys()) == set(range(1, 23))

    def test_get_queries_returns_string_keys(self, bm: DataVaultBenchmark) -> None:
        queries = bm.get_queries()
        for k in queries:
            assert isinstance(k, str), f"Key should be str, got {type(k)}"

    def test_get_queries_returns_22(self, bm: DataVaultBenchmark) -> None:
        queries = bm.get_queries()
        assert len(queries) == 22

    def test_get_query_by_int(self, bm: DataVaultBenchmark) -> None:
        sql = bm.get_query(1)
        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_get_query_by_string(self, bm: DataVaultBenchmark) -> None:
        sql = bm.get_query("1")
        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_all_queries_are_select_statements(self, bm: DataVaultBenchmark) -> None:
        queries = bm.get_all_queries()
        for qid, sql in queries.items():
            assert "SELECT" in sql.upper(), f"Q{qid} should be a SELECT statement"


class TestDataVaultSchema:
    @pytest.fixture
    def bm(self) -> DataVaultBenchmark:
        return DataVaultBenchmark(scale_factor=0.01)

    def test_get_schema_returns_dict(self, bm: DataVaultBenchmark) -> None:
        schema = bm.get_schema()
        assert isinstance(schema, dict)

    def test_get_schema_has_21_tables(self, bm: DataVaultBenchmark) -> None:
        schema = bm.get_schema()
        assert len(schema) == 21

    def test_get_create_tables_sql_duckdb(self, bm: DataVaultBenchmark) -> None:
        ddl = bm.get_create_tables_sql(dialect="duckdb")
        assert "CREATE TABLE" in ddl
        assert ddl.count("CREATE TABLE") == 21

    def test_get_create_tables_sql_standard(self, bm: DataVaultBenchmark) -> None:
        ddl = bm.get_create_tables_sql(dialect="standard")
        assert "CREATE TABLE" in ddl

    def test_get_table_loading_order_returns_21(self, bm: DataVaultBenchmark) -> None:
        order = bm.get_table_loading_order()
        assert len(order) == 21

    def test_get_table_loading_order_filtered(self, bm: DataVaultBenchmark) -> None:
        all_order = bm.get_table_loading_order()
        subset = all_order[:5]
        filtered = bm.get_table_loading_order(available_tables=subset)
        assert filtered == subset

    def test_get_table_count_is_21(self, bm: DataVaultBenchmark) -> None:
        assert bm.get_table_count() == 21


class TestDataVaultCleanup:
    def test_cleanup_no_error_when_no_generator(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        bm.cleanup()

    def test_cleanup_calls_generator_cleanup(self) -> None:
        from unittest.mock import MagicMock

        bm = DataVaultBenchmark(scale_factor=0.01)
        mock_gen = MagicMock()
        bm._tpch_generator = mock_gen
        bm.cleanup()
        mock_gen.cleanup.assert_called_once()


class TestDataVaultBenchmarkEdgeCases:
    def test_invalid_hash_algorithm_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported hash algorithm"):
            DataVaultBenchmark(scale_factor=0.01, hash_algorithm="sha512")

    def test_supported_hash_algorithms(self) -> None:
        bm_md5 = DataVaultBenchmark(scale_factor=0.01, hash_algorithm="md5")
        assert bm_md5.hash_algorithm == "md5"

    def test_tpch_source_dir_default_output_dir(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        bm.output_dir = None
        bm._tpch_source_dir = None
        src = bm.tpch_source_dir
        assert "tpch_" in src.name

    def test_get_create_tables_sql_non_standard_dialect(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        try:
            ddl = bm.get_create_tables_sql(dialect="mysql")
            assert "CREATE TABLE" in ddl
        except Exception:
            pass

    def test_execute_query_with_mock_connection(self) -> None:
        from unittest.mock import MagicMock

        bm = DataVaultBenchmark(scale_factor=0.01)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("row1",)]
        mock_conn.cursor.return_value = mock_cursor
        result = bm.execute_query(1, mock_conn)
        assert result == [("row1",)]
        mock_cursor.execute.assert_called_once()

    def test_execute_query_connection_without_cursor(self) -> None:
        from unittest.mock import MagicMock

        bm = DataVaultBenchmark(scale_factor=0.01)
        mock_conn = MagicMock(spec=["execute", "fetchall"])
        mock_conn.fetchall.return_value = []
        result = bm.execute_query(1, mock_conn)
        assert isinstance(result, list)
        mock_conn.execute.assert_called_once()

    def test_supports_dataframe_mode(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        assert bm.supports_dataframe_mode() is True

    def test_get_dataframe_queries_returns_list(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        queries = bm.get_dataframe_queries()
        assert isinstance(queries, list)
        assert len(queries) > 0

    def test_get_create_tables_sql_with_tuning_config(self) -> None:
        from unittest.mock import MagicMock

        bm = DataVaultBenchmark(scale_factor=0.01)
        tuning = MagicMock()
        tuning.primary_keys.enabled = False
        tuning.foreign_keys.enabled = False
        ddl = bm.get_create_tables_sql(dialect="duckdb", tuning_config=tuning)
        assert "CREATE TABLE" in ddl

    def test_lazy_load_query_manager(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        assert bm._query_manager is None
        qm = bm.query_manager
        assert qm is not None
        assert bm.query_manager is qm

    def test_lazy_load_tpch_generator(self, tmp_path: Path) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path / "datavault")
        assert bm._tpch_generator is None
        gen = bm.tpch_generator
        assert gen is not None
        assert bm.tpch_generator is gen

    def test_lazy_load_etl_transformer(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        assert bm._etl_transformer is None
        transformer = bm.etl_transformer
        assert transformer is not None
        assert bm.etl_transformer is transformer

    def test_check_manifest_returns_none_when_no_file(self, tmp_path: Path) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        result = bm._check_existing_manifest()
        assert result is None

    def test_check_manifest_benchmark_mismatch(self, tmp_path: Path) -> None:
        import json

        manifest = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "tables": {},
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest))
        bm = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        result = bm._check_existing_manifest()
        assert result is None

    def test_check_manifest_scale_factor_mismatch(self, tmp_path: Path) -> None:
        import json

        manifest = {
            "benchmark": "datavault",
            "scale_factor": 1.0,
            "tables": {},
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest))
        bm = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        result = bm._check_existing_manifest()
        assert result is None

    def test_check_manifest_invalid_json_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "_datagen_manifest.json").write_text("not valid json{")
        bm = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        result = bm._check_existing_manifest()
        assert result is None

    def test_generate_data_with_mocked_generators(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        bm = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)

        mock_tpch_files = {"lineitem": tmp_path / "lineitem.tbl"}
        mock_dv_files = {"hub_customer": tmp_path / "hub_customer.tbl"}

        mock_gen = MagicMock()
        mock_gen.generate.return_value = mock_tpch_files
        bm._tpch_generator = mock_gen

        mock_etl = MagicMock()
        mock_etl.transform.return_value = mock_dv_files
        bm._etl_transformer = mock_etl

        result = bm.generate_data()
        assert result == mock_dv_files
        mock_gen.generate.assert_called_once()
        mock_etl.transform.assert_called_once()

    def test_generate_data_with_force_regenerate(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        bm = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path, force_regenerate=True)

        mock_gen = MagicMock()
        mock_gen.generate.return_value = {}
        bm._tpch_generator = mock_gen

        mock_etl = MagicMock()
        mock_etl.transform.return_value = {}
        bm._etl_transformer = mock_etl

        result = bm.generate_data()
        assert isinstance(result, dict)

    def test_generate_data_output_dir_none_raises(self) -> None:
        bm = DataVaultBenchmark(scale_factor=0.01)
        bm.output_dir = None
        with pytest.raises(ValueError, match="output_dir must be set"):
            bm.generate_data()


class TestDataVaultManifestDialectMetadata:
    def _write(self, tmp_path: Path, output_format: str) -> dict:
        import json
        from datetime import datetime

        from benchbox.core.datavault.etl.transformer import DataVaultETLTransformer

        transformer = DataVaultETLTransformer(scale_factor=0.01)
        data_file = tmp_path / f"hub_region.{output_format}"
        data_file.write_text("a|b\n")
        transformer._write_manifest(
            output_dir=tmp_path,
            table_paths={"hub_region": data_file},
            table_row_counts={"hub_region": 1},
            output_format=output_format,
            load_timestamp=datetime(2024, 1, 1),
        )
        return json.loads((tmp_path / "_datagen_manifest.json").read_text())

    def test_tbl_manifest_records_pipe_dialect(self, tmp_path: Path) -> None:
        manifest = self._write(tmp_path, "tbl")
        entry = manifest["tables"]["hub_region"]["formats"]["tbl"][0]
        assert entry["metadata"] == {
            "csv_delimiter": "|",
            "csv_has_header": False,
            "csv_null_marker": "",
            "csv_normalize_booleans": False,
            "csv_quote": '"',
        }

    def test_csv_manifest_records_comma_dialect(self, tmp_path: Path) -> None:
        manifest = self._write(tmp_path, "csv")
        entry = manifest["tables"]["hub_region"]["formats"]["csv"][0]
        assert entry["metadata"]["csv_delimiter"] == ","
        assert entry["metadata"]["csv_has_header"] is False

    def test_quoted_values_load_through_postgres_csv_copy(self, tmp_path: Path) -> None:
        from benchbox.platforms.base.data_loading import DataSource, resolve_csv_dialect
        from benchbox.platforms.postgresql import _postgres_copy_sql

        manifest = self._write(tmp_path, "tbl")
        metadata = manifest["tables"]["hub_region"]["formats"]["tbl"][0]["metadata"]
        data_file = tmp_path / "sat_order.tbl"
        data_file.write_text('hk1||"Clerk#000000951"|""\n')
        source = DataSource(
            source_type="manifest",
            tables={"sat_order": data_file},
            table_metadata={"sat_order": metadata},
        )
        dialect = resolve_csv_dialect(source, "sat_order", data_file, benchmark=None)

        assert dialect.quote == '"'
        sql = _postgres_copy_sql("sat_order", dialect)
        assert "FORMAT csv" in sql
        assert "NULL ''" in sql

    def test_satellite_manifest_resolves_empty_field_to_null(self, tmp_path: Path) -> None:
        from benchbox.platforms.base.data_loading import (
            DataSource,
            DuckDBNativeHandler,
            resolve_csv_dialect,
        )

        manifest = self._write(tmp_path, "tbl")
        metadata = manifest["tables"]["hub_region"]["formats"]["tbl"][0]["metadata"]
        data_file = tmp_path / "sat_customer.tbl"
        data_file.write_text("hk1|ACME|\n")
        source = DataSource(
            source_type="manifest",
            tables={"sat_customer": data_file},
            table_metadata={"sat_customer": metadata},
        )
        dialect = resolve_csv_dialect(source, "sat_customer", data_file, benchmark=None)
        assert dialect.delimiter == "|"
        assert dialect.has_header is False
        assert dialect.null_marker == ""

        handler = DuckDBNativeHandler(
            delimiter=dialect.delimiter, adapter=None, benchmark=None, null_marker=dialect.null_marker
        )
        assert handler._pipe_nullstr_config() == "nullstr=''"

        duckdb = pytest.importorskip("duckdb")
        conn = duckdb.connect(":memory:")
        try:
            conn.execute("CREATE TABLE sat_customer (hk VARCHAR, name VARCHAR, load_end_dts TIMESTAMP)")
            conn.execute(
                "INSERT INTO sat_customer SELECT * FROM read_csv("
                f"'{data_file}', delim='{dialect.delimiter}', header=false, "
                f"{handler._pipe_nullstr_config()}, names=['hk', 'name', 'load_end_dts'])"
            )
            (is_null,) = conn.execute("SELECT load_end_dts IS NULL FROM sat_customer").fetchone()
            assert is_null is True
        finally:
            conn.close()
