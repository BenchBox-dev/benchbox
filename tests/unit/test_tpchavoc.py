# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.sql_compat.rules.execution_filter.cloud_tpchavoc import CLOUD_TPCHAVOC_SKIPS
from benchbox.tpchavoc import TPCHavoc

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTPCHavocInit:
    def test_default_initialization(self, tmp_path):

        with patch("benchbox.tpchavoc.TPCHavocBenchmark") as mock_impl:
            havoc = TPCHavoc(scale_factor=1.0, output_dir=tmp_path)

            assert havoc.scale_factor == 1.0
            mock_impl.assert_called_once()

    def test_custom_scale_factor(self, tmp_path):

        with patch("benchbox.tpchavoc.TPCHavocBenchmark"):
            havoc = TPCHavoc(scale_factor=10.0, output_dir=tmp_path)

            assert havoc.scale_factor == 10.0

    def test_invalid_scale_factor_type(self, tmp_path):

        with pytest.raises(TypeError, match="scale_factor must be a number"):
            TPCHavoc(scale_factor="invalid", output_dir=tmp_path)

    def test_invalid_scale_factor_zero(self, tmp_path):

        with pytest.raises(ValueError, match="scale_factor must be positive"):
            TPCHavoc(scale_factor=0, output_dir=tmp_path)

    def test_invalid_scale_factor_negative(self, tmp_path):

        with pytest.raises(ValueError, match="scale_factor must be positive"):
            TPCHavoc(scale_factor=-1.0, output_dir=tmp_path)


class TestGetQuery:
    @pytest.fixture
    def havoc(self, tmp_path):

        with patch("benchbox.tpchavoc.TPCHavocBenchmark"):
            return TPCHavoc(scale_factor=1.0, output_dir=tmp_path)

    def test_get_query_valid_int(self, havoc):

        havoc._impl.get_query.return_value = "SELECT * FROM orders"

        result = havoc.get_query(1)

        assert result == "SELECT * FROM orders"
        havoc._impl.get_query.assert_called_once()

    def test_get_query_valid_string(self, havoc):

        havoc._impl.get_query.return_value = "SELECT * FROM orders"

        result = havoc.get_query("1_v1")

        assert result == "SELECT * FROM orders"

    def test_get_query_invalid_int_low(self, havoc):

        with pytest.raises(ValueError, match="Query ID must be 1-22"):
            havoc.get_query(0)

    def test_get_query_invalid_int_high(self, havoc):

        with pytest.raises(ValueError, match="Query ID must be 1-22"):
            havoc.get_query(23)

    def test_get_query_invalid_string_format(self, havoc):

        with pytest.raises(ValueError, match="must be in format"):
            havoc.get_query("invalid")

    def test_get_query_invalid_type(self, havoc):

        with pytest.raises(TypeError, match="query_id must be an integer or string"):
            havoc.get_query([1])

    def test_get_query_with_invalid_scale_factor_type(self, havoc):

        with pytest.raises(TypeError, match="scale_factor must be a number"):
            havoc.get_query(1, scale_factor="invalid")

    def test_get_query_with_invalid_scale_factor_value(self, havoc):

        with pytest.raises(ValueError, match="scale_factor must be positive"):
            havoc.get_query(1, scale_factor=-1.0)

    def test_get_query_with_invalid_seed_type(self, havoc):

        with pytest.raises(TypeError, match="seed must be an integer"):
            havoc.get_query(1, seed="invalid")

    def test_get_query_forwards_dialect(self, havoc):

        havoc._impl.get_query.return_value = "SELECT"

        havoc.get_query(1, dialect="bigquery", base_dialect="netezza")

        havoc._impl.get_query.assert_called_once_with(
            1, seed=None, scale_factor=None, dialect="bigquery", base_dialect="netezza"
        )

    def test_get_query_dialect_defaults_to_none(self, havoc):

        havoc._impl.get_query.return_value = "SELECT"

        havoc.get_query(1)

        havoc._impl.get_query.assert_called_once_with(1, seed=None, scale_factor=None, dialect=None, base_dialect=None)


class TestGetPlatformSkipQueries:
    @pytest.fixture
    def havoc(self, tmp_path):

        with patch("benchbox.tpchavoc.TPCHavocBenchmark"):
            return TPCHavoc(scale_factor=1.0, output_dir=tmp_path)

    def test_delegates_to_impl(self, havoc):

        havoc._impl.get_platform_skip_queries.return_value = ["1_v6"]

        result = havoc.get_platform_skip_queries("bigquery")

        assert result == ["1_v6"]
        havoc._impl.get_platform_skip_queries.assert_called_once_with("bigquery")

    def test_cloud_and_unknown_platforms_through_facade(self, tmp_path):

        havoc = TPCHavoc(scale_factor=0.1, output_dir=tmp_path)

        assert set(havoc.get_platform_skip_queries("bigquery")) == set(CLOUD_TPCHAVOC_SKIPS["bigquery"])
        assert havoc.get_platform_skip_queries("unknown-platform") == []


class TestGetQueryVariant:
    @pytest.fixture
    def havoc(self, tmp_path):

        with patch("benchbox.tpchavoc.TPCHavocBenchmark"):
            return TPCHavoc(scale_factor=1.0, output_dir=tmp_path)

    def test_get_query_variant_valid(self, havoc):

        havoc._impl.get_query_variant.return_value = "SELECT variant FROM orders"

        result = havoc.get_query_variant(1, 1)

        assert result == "SELECT variant FROM orders"
        havoc._impl.get_query_variant.assert_called_once_with(1, 1, None)

    def test_get_query_variant_with_params(self, havoc):

        havoc._impl.get_query_variant.return_value = "SELECT * FROM orders"
        params = {"date": "1998-01-01"}

        havoc.get_query_variant(1, 1, params=params)

        havoc._impl.get_query_variant.assert_called_once_with(1, 1, params)

    def test_get_query_variant_invalid_query_id_type(self, havoc):

        with pytest.raises(TypeError, match="query_id must be an integer"):
            havoc.get_query_variant("1", 1)

    def test_get_query_variant_invalid_variant_id_type(self, havoc):

        with pytest.raises(TypeError, match="variant_id must be an integer"):
            havoc.get_query_variant(1, "1")

    def test_get_query_variant_query_id_low(self, havoc):

        with pytest.raises(ValueError, match="Query ID must be 1-22"):
            havoc.get_query_variant(0, 1)

    def test_get_query_variant_query_id_high(self, havoc):

        with pytest.raises(ValueError, match="Query ID must be 1-22"):
            havoc.get_query_variant(23, 1)

    def test_get_query_variant_variant_id_low(self, havoc):

        with pytest.raises(ValueError, match="Variant ID must be 1-10"):
            havoc.get_query_variant(1, 0)

    def test_get_query_variant_variant_id_high(self, havoc):

        with pytest.raises(ValueError, match="Variant ID must be 1-10"):
            havoc.get_query_variant(1, 11)


class TestGetAllVariants:
    @pytest.fixture
    def havoc(self, tmp_path):

        with patch("benchbox.tpchavoc.TPCHavocBenchmark"):
            return TPCHavoc(scale_factor=1.0, output_dir=tmp_path)

    def test_get_all_variants_valid(self, havoc):

        havoc._impl.get_all_variants.return_value = {1: "SQL1", 2: "SQL2"}

        result = havoc.get_all_variants(1)

        assert result == {1: "SQL1", 2: "SQL2"}
        havoc._impl.get_all_variants.assert_called_once_with(1)

    def test_get_all_variants_invalid_type(self, havoc):

        with pytest.raises(TypeError, match="query_id must be an integer"):
            havoc.get_all_variants("1")

    def test_get_all_variants_query_id_low(self, havoc):

        with pytest.raises(ValueError, match="Query ID must be 1-22"):
            havoc.get_all_variants(0)

    def test_get_all_variants_query_id_high(self, havoc):

        with pytest.raises(ValueError, match="Query ID must be 1-22"):
            havoc.get_all_variants(23)


class TestGetVariantDescription:
    @pytest.fixture
    def havoc(self, tmp_path):

        with patch("benchbox.tpchavoc.TPCHavocBenchmark"):
            return TPCHavoc(scale_factor=1.0, output_dir=tmp_path)

    def test_get_variant_description_valid(self, havoc):

        havoc._impl.get_variant_description.return_value = "Join order permutation"

        result = havoc.get_variant_description(1, 1)

        assert result == "Join order permutation"
        havoc._impl.get_variant_description.assert_called_once_with(1, 1)

    def test_get_variant_description_invalid_query_id_type(self, havoc):

        with pytest.raises(TypeError, match="query_id must be an integer"):
            havoc.get_variant_description("1", 1)

    def test_get_variant_description_invalid_variant_id_type(self, havoc):

        with pytest.raises(TypeError, match="variant_id must be an integer"):
            havoc.get_variant_description(1, "1")

    def test_get_variant_description_query_id_low(self, havoc):

        with pytest.raises(ValueError, match="Query ID must be 1-22"):
            havoc.get_variant_description(0, 1)

    def test_get_variant_description_variant_id_low(self, havoc):

        with pytest.raises(ValueError, match="Variant ID must be 1-10"):
            havoc.get_variant_description(1, 0)


class TestGetAllVariantsInfo:
    @pytest.fixture
    def havoc(self, tmp_path):

        with patch("benchbox.tpchavoc.TPCHavocBenchmark"):
            return TPCHavoc(scale_factor=1.0, output_dir=tmp_path)

    def test_get_all_variants_info_valid(self, havoc):

        havoc._impl.get_all_variants_info.return_value = {1: {"sql": "SELECT", "desc": "Test"}}

        result = havoc.get_all_variants_info(1)

        assert result == {1: {"sql": "SELECT", "desc": "Test"}}
        havoc._impl.get_all_variants_info.assert_called_once_with(1)

    def test_get_all_variants_info_invalid_type(self, havoc):

        with pytest.raises(TypeError, match="query_id must be an integer"):
            havoc.get_all_variants_info("1")

    def test_get_all_variants_info_query_id_low(self, havoc):

        with pytest.raises(ValueError, match="Query ID must be 1-22"):
            havoc.get_all_variants_info(0)


class TestRunQuery:
    @pytest.fixture
    def havoc(self, tmp_path):

        with patch("benchbox.tpchavoc.TPCHavocBenchmark"):
            return TPCHavoc(scale_factor=1.0, output_dir=tmp_path)

    def test_run_query_valid(self, havoc):

        havoc._impl.run_query.return_value = {"rows": 100, "time": 0.5}

        result = havoc.run_query(1, "duckdb:///:memory:")

        assert result == {"rows": 100, "time": 0.5}

    def test_run_query_invalid_query_id_type(self, havoc):

        with pytest.raises(TypeError, match="query_id must be an integer"):
            havoc.run_query("1", "duckdb:///:memory:")

    def test_run_query_invalid_query_id_range(self, havoc):

        with pytest.raises(ValueError, match="Query ID must be 1-22"):
            havoc.run_query(0, "duckdb:///:memory:")

    def test_run_query_invalid_connection_string_empty(self, havoc):

        with pytest.raises(ValueError, match="connection_string must be a non-empty string"):
            havoc.run_query(1, "")

    def test_run_query_invalid_connection_string_whitespace(self, havoc):

        with pytest.raises(ValueError, match="connection_string must be a non-empty string"):
            havoc.run_query(1, "   ")


class TestRunBenchmark:
    @pytest.fixture
    def havoc(self, tmp_path):

        with patch("benchbox.tpchavoc.TPCHavocBenchmark"):
            return TPCHavoc(scale_factor=1.0, output_dir=tmp_path)

    def test_run_benchmark_valid(self, havoc):

        havoc._impl.run_benchmark.return_value = {"total_time": 10.0}

        result = havoc.run_benchmark("duckdb:///:memory:")

        assert result == {"total_time": 10.0}

    def test_run_benchmark_invalid_connection_string(self, havoc):

        with pytest.raises(ValueError, match="connection_string must be a non-empty string"):
            havoc.run_benchmark("")

    def test_run_benchmark_invalid_iterations_type(self, havoc):

        with pytest.raises(TypeError, match="iterations must be an integer"):
            havoc.run_benchmark("duckdb:///:memory:", iterations="5")

    def test_run_benchmark_invalid_iterations_value(self, havoc):

        with pytest.raises(ValueError, match="iterations must be positive"):
            havoc.run_benchmark("duckdb:///:memory:", iterations=0)

    def test_run_benchmark_invalid_queries_type(self, havoc):

        with pytest.raises(TypeError, match="queries must be a list"):
            havoc.run_benchmark("duckdb:///:memory:", queries=1)

    def test_run_benchmark_invalid_query_id_in_list(self, havoc):

        with pytest.raises(TypeError, match="query_id must be an integer"):
            havoc.run_benchmark("duckdb:///:memory:", queries=["1"])

    def test_run_benchmark_invalid_query_id_range_in_list(self, havoc):

        with pytest.raises(ValueError, match="Query ID must be 1-22"):
            havoc.run_benchmark("duckdb:///:memory:", queries=[23])


class TestDelegationMethods:
    @pytest.fixture
    def havoc(self, tmp_path):

        with patch("benchbox.tpchavoc.TPCHavocBenchmark"):
            return TPCHavoc(scale_factor=1.0, output_dir=tmp_path)

    def test_generate_data(self, havoc):

        havoc._impl.generate_data.return_value = [Path("data.csv")]

        result = havoc.generate_data()

        assert result == [Path("data.csv")]
        havoc._impl.generate_data.assert_called_once()

    def test_get_queries(self, havoc):

        havoc._impl.get_queries.return_value = {"1": "SELECT"}

        result = havoc.get_queries()

        assert result == {"1": "SELECT"}
        havoc._impl.get_queries.assert_called_once_with(dialect=None)

    def test_get_queries_with_dialect(self, havoc):

        havoc._impl.get_queries.return_value = {"1": "SELECT"}

        havoc.get_queries(dialect="duckdb")

        havoc._impl.get_queries.assert_called_once_with(dialect="duckdb")

    def test_get_implemented_queries(self, havoc):

        havoc._impl.get_implemented_queries.return_value = [1, 3, 6]

        result = havoc.get_implemented_queries()

        assert result == [1, 3, 6]
        havoc._impl.get_implemented_queries.assert_called_once()

    def test_get_schema(self, havoc):

        havoc._impl.get_schema.return_value = {"orders": {"columns": []}}

        result = havoc.get_schema()

        assert result == {"orders": {"columns": []}}
        havoc._impl.get_schema.assert_called_once()

    def test_get_create_tables_sql(self, havoc):

        havoc._impl.get_create_tables_sql.return_value = "CREATE TABLE orders"

        result = havoc.get_create_tables_sql()

        assert result == "CREATE TABLE orders"
        havoc._impl.get_create_tables_sql.assert_called_once()

    def test_get_benchmark_info(self, havoc):

        havoc._impl.get_benchmark_info.return_value = {"name": "TPC-Havoc"}

        result = havoc.get_benchmark_info()

        assert result == {"name": "TPC-Havoc"}
        havoc._impl.get_benchmark_info.assert_called_once()

    def test_export_variant_queries(self, havoc):

        havoc._impl.export_variant_queries.return_value = {"1_v1": Path("q1_v1.sql")}

        result = havoc.export_variant_queries()

        assert result == {"1_v1": Path("q1_v1.sql")}
        havoc._impl.export_variant_queries.assert_called_once()

    def test_load_data_to_database(self, havoc):

        havoc.load_data_to_database("duckdb:///:memory:")

        havoc._impl.load_data_to_database.assert_called_once()


