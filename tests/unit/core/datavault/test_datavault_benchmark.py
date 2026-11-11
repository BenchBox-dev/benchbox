import json

import pytest

from benchbox.core.datavault.benchmark import DataVaultBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_default_output_dir_uses_datavault_name(tmp_path, monkeypatch):

    monkeypatch.chdir(tmp_path)

    benchmark = DataVaultBenchmark(scale_factor=0.01)

    assert benchmark.output_dir is not None
    assert benchmark.output_dir.name.endswith("datavault_sf001")
    assert "benchmark_runs" in benchmark.output_dir.as_posix()


def test_benchmark_name_override():
    benchmark = DataVaultBenchmark(scale_factor=0.1)
    assert benchmark._get_benchmark_name() == "datavault"


def test_get_create_tables_sql_translation():
    benchmark = DataVaultBenchmark(scale_factor=0.01)
    ddl = benchmark.get_create_tables_sql(dialect="snowflake")

    assert ddl.count("CREATE TABLE") == 21

    assert '"hub_region"' not in ddl
    assert "hub_region" in ddl


def test_query_translation_via_base():
    from benchbox.datavault import DataVault

    benchmark = DataVault(scale_factor=0.01)
    translated = benchmark.translate_query(1, dialect="snowflake")

    assert "SELECT" in translated.upper()


class TestDataFrameMode:
    def test_supports_dataframe_mode(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        assert benchmark.supports_dataframe_mode() is True

    def test_get_dataframe_queries_returns_all_22(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        queries = benchmark.get_dataframe_queries()
        assert len(queries) == 22

    def test_dataframe_queries_have_both_implementations(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        for query in benchmark.get_dataframe_queries():
            assert query.expression_impl is not None, f"{query.query_id} missing expression_impl"
            assert query.pandas_impl is not None, f"{query.query_id} missing pandas_impl"

    def test_dataframe_query_ids_match_sql_queries(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        df_ids = sorted(q.query_id for q in benchmark.get_dataframe_queries())
        expected = sorted(f"Q{i}" for i in range(1, 23))
        assert df_ids == expected


class TestHashAlgorithmValidation:
    def test_md5_algorithm_accepted(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01, hash_algorithm="md5")
        assert benchmark.hash_algorithm == "md5"

    def test_sha1_algorithm_rejected(self):
        with pytest.raises(ValueError) as exc_info:
            DataVaultBenchmark(scale_factor=0.01, hash_algorithm="sha1")

        assert "sha1" in str(exc_info.value).lower()
        assert "md5" in str(exc_info.value).lower()

    def test_sha256_algorithm_accepted(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01, hash_algorithm="sha256")
        assert benchmark.hash_algorithm == "sha256"

    def test_sha256_produces_64char_key(self):
        from benchbox.core.datavault.etl.hash_functions import generate_hash_key

        result = generate_hash_key(1, algorithm="sha256")
        assert len(result) == 64

    def test_invalid_algorithm_rejected(self):
        with pytest.raises(ValueError):
            DataVaultBenchmark(scale_factor=0.01, hash_algorithm="invalid")


class TestManifestChecking:
    def test_check_existing_manifest_returns_none_when_no_manifest(self, tmp_path):
        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        result = benchmark._check_existing_manifest()
        assert result is None

    def test_check_existing_manifest_returns_none_on_benchmark_mismatch(self, tmp_path):
        manifest = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "tables": {},
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest))

        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        result = benchmark._check_existing_manifest()
        assert result is None

    def test_check_existing_manifest_returns_none_on_scale_mismatch(self, tmp_path):
        manifest = {
            "version": 2,
            "benchmark": "datavault",
            "scale_factor": 1.0,
            "tables": {},
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest))

        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        result = benchmark._check_existing_manifest()
        assert result is None

    def test_check_existing_manifest_returns_none_on_missing_files(self, tmp_path):
        manifest = {
            "version": 2,
            "benchmark": "datavault",
            "scale_factor": 0.01,
            "tables": {
                "hub_region": {"formats": {"tbl": [{"path": "hub_region.tbl", "row_count": 5, "size_bytes": 100}]}}
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest))

        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        result = benchmark._check_existing_manifest()

        assert result is None

    def test_force_regenerate_bypasses_manifest_check(self, tmp_path, monkeypatch):

        manifest = {
            "version": 2,
            "benchmark": "datavault",
            "scale_factor": 0.01,
            "tables": {},
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest))

        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path, force_regenerate=True)

        assert benchmark.force_regenerate is True


class TestDataVaultBenchmarkExtraOps:
    def test_get_query_returns_sql(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        sql = benchmark.get_query(1)
        assert isinstance(sql, str)
        assert "SELECT" in sql.upper()

    def test_get_all_queries_returns_22(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        queries = benchmark.get_all_queries()
        assert len(queries) == 22

    def test_get_queries_dialect_none(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        queries = benchmark.get_queries()

        assert "1" in queries
        assert "22" in queries

    @pytest.mark.parametrize("dialect", ["snowflake", "bigquery", "databricks"])
    def test_get_queries_translates_duckdb_syntax_for_cloud_dialects(self, dialect):
        queries = DataVaultBenchmark(scale_factor=0.01).get_queries(dialect=dialect)

        assert len(queries) == 22
        for sql in queries.values():
            assert "FROM 1 FOR" not in sql.upper()
        assert "SUBSTRING(" in queries["22"].upper()
        if dialect == "bigquery":
            assert "INTERVAL '90 DAYS'" not in queries["1"].upper()
            assert "INTERVAL '90' DAY" in queries["1"].upper()

    @pytest.mark.parametrize("dialect", [None, "duckdb"])
    def test_get_queries_keeps_duckdb_source(self, dialect):
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        source = {str(k): v for k, v in benchmark.get_all_queries().items()}

        assert benchmark.get_queries(dialect=dialect) == source

    def test_get_query_translates_for_dialect(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)

        assert "FROM 1 FOR" in benchmark.get_query(22).upper()
        assert "FROM 1 FOR" not in benchmark.get_query(22, dialect="snowflake").upper()

    def test_wrapper_passes_dialect_and_keeps_keys(self):
        from benchbox.datavault import DataVault

        benchmark = DataVault(scale_factor=0.01)
        translated = benchmark.get_queries(dialect="bigquery")

        assert translated.keys() == benchmark.get_queries().keys()
        assert "FROM 1 FOR" not in translated[22].upper()
        assert "FROM 1 FOR" not in benchmark.get_query(22, dialect="bigquery").upper()

    def test_get_schema_returns_tables_dict(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        schema = benchmark.get_schema()
        assert isinstance(schema, dict)
        assert len(schema) > 0

    def test_get_table_count_is_21(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        assert benchmark.get_table_count() == 21

    def test_get_table_loading_order_full(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        order = benchmark.get_table_loading_order()
        assert len(order) == 21

    def test_get_table_loading_order_filtered(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        available = ["hub_customer", "hub_supplier"]
        order = benchmark.get_table_loading_order(available_tables=available)
        assert set(order) == set(available)

    def test_tpch_source_dir_property(self, tmp_path):
        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        src_dir = benchmark.tpch_source_dir
        assert src_dir is not None
        assert "tpch" in str(src_dir)

    def test_tpch_generator_lazy_load(self, tmp_path):
        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        gen = benchmark.tpch_generator
        assert gen is not None

        assert benchmark.tpch_generator is gen

    def test_etl_transformer_lazy_load(self, tmp_path):
        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        transformer = benchmark.etl_transformer
        assert transformer is not None
        assert benchmark.etl_transformer is transformer

    def test_query_manager_lazy_load(self, tmp_path):
        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        qm = benchmark.query_manager
        assert qm is not None
        assert benchmark.query_manager is qm

    def test_get_create_tables_sql_with_tuning_config(self, tmp_path):
        from types import SimpleNamespace

        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        tuning = SimpleNamespace(
            primary_keys=SimpleNamespace(enabled=False),
            foreign_keys=SimpleNamespace(enabled=False),
        )
        ddl = benchmark.get_create_tables_sql(dialect="duckdb", tuning_config=tuning)
        assert isinstance(ddl, str)
        assert "CREATE TABLE" in ddl

    def test_execute_query_uses_cursor(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.fetchall.return_value = [(1, "test")]
        result = benchmark.execute_query(1, conn)
        assert result == [(1, "test")]
        cursor.execute.assert_called_once()

    def test_execute_query_connectionless_cursor(self):
        benchmark = DataVaultBenchmark(scale_factor=0.01)

        conn = MagicMock()
        del conn.cursor
        conn.fetchall.return_value = []
        benchmark.execute_query(1, conn)
        conn.execute.assert_called_once()

    def test_generate_data_uses_existing_manifest(self, tmp_path, monkeypatch):
        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)

        fake_files = {f"hub_{i}": tmp_path / f"hub_{i}.tbl" for i in range(21)}
        for path in fake_files.values():
            path.touch()

        monkeypatch.setattr(benchmark, "_check_existing_manifest", lambda: fake_files)

        result = benchmark.generate_data()
        assert len(result) == 21

    def test_cleanup_with_generator(self, tmp_path):
        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        mock_gen = MagicMock()
        benchmark._tpch_generator = mock_gen
        benchmark.cleanup()
        mock_gen.cleanup.assert_called_once()

    def test_generate_data_full_path(self, tmp_path, monkeypatch):
        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path, force_regenerate=True)
        mock_tpch_gen = MagicMock()
        mock_tpch_gen.generate.return_value = {"customer": tmp_path / "customer.tbl"}

        mock_etl = MagicMock()
        dv_files = {f"hub_{i}": tmp_path / f"hub_{i}.tbl" for i in range(21)}
        mock_etl.transform.return_value = dv_files

        benchmark._tpch_generator = mock_tpch_gen
        benchmark._etl_transformer = mock_etl

        result = benchmark.generate_data()
        assert len(result) == 21
        mock_tpch_gen.generate.assert_called_once()
        mock_etl.transform.assert_called_once()

    def test_default_output_dir_without_explicit_dir(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        benchmark = DataVaultBenchmark(scale_factor=0.01)
        assert benchmark.output_dir is not None
        assert "datavault" in str(benchmark.output_dir)

    def test_check_manifest_with_few_tables(self, tmp_path):
        from benchbox.utils.datagen_manifest import MANIFEST_FILENAME

        manifest = {
            "version": 2,
            "benchmark": "datavault",
            "scale_factor": 0.01,
            "tables": {
                "hub_customer": {"formats": {"tbl": [{"path": "hub_customer.tbl", "row_count": 5, "size_bytes": 100}]}},
            },
        }
        (tmp_path / MANIFEST_FILENAME).write_text(__import__("json").dumps(manifest))

        (tmp_path / "hub_customer.tbl").touch()

        benchmark = DataVaultBenchmark(scale_factor=0.01, output_dir=tmp_path)
        result = benchmark._check_existing_manifest()

        assert result is None


from unittest.mock import MagicMock


