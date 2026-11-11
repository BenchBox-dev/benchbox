# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox import TPCHSkew
from benchbox.core.tpch_skew.benchmark import TPCHSkewBenchmark
from benchbox.core.tpch_skew.skew_config import SkewConfiguration

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestTPCHSkewBenchmark:
    def test_initialization_default(self):

        benchmark = TPCHSkewBenchmark(scale_factor=0.01)
        assert benchmark.skew_preset == "moderate"
        assert benchmark.skew_config.skew_factor == 0.5

    def test_initialization_with_preset(self):

        benchmark = TPCHSkewBenchmark(scale_factor=0.01, skew_preset="heavy")
        assert benchmark.skew_preset == "heavy"
        assert benchmark.skew_config.skew_factor == 0.8

    def test_initialization_with_custom_config(self):

        config = SkewConfiguration(skew_factor=0.75, distribution_type="normal")
        benchmark = TPCHSkewBenchmark(scale_factor=0.01, skew_config=config)
        assert benchmark.skew_preset == "custom"
        assert benchmark.skew_config.skew_factor == 0.75
        assert benchmark.skew_config.distribution_type == "normal"

    def test_invalid_preset(self):

        with pytest.raises(ValueError, match="Invalid skew preset"):
            TPCHSkewBenchmark(scale_factor=0.01, skew_preset="invalid")

    def test_scale_factor_validation(self):
        with pytest.raises(ValueError):
            TPCHSkewBenchmark(scale_factor=0)
        with pytest.raises(ValueError):
            TPCHSkewBenchmark(scale_factor=-1)

    def test_get_skew_info(self):

        benchmark = TPCHSkewBenchmark(scale_factor=0.01, skew_preset="light")
        info = benchmark.get_skew_info()

        assert "preset" in info
        assert "skew_factor" in info
        assert "distribution_type" in info
        assert "attribute_skew_enabled" in info
        assert "join_skew_enabled" in info
        assert "temporal_skew_enabled" in info
        assert info["preset"] == "light"
        assert info["skew_factor"] == 0.2

    def test_get_benchmark_info(self):

        benchmark = TPCHSkewBenchmark(scale_factor=1.0, skew_preset="moderate")
        info = benchmark.get_benchmark_info()

        assert info["name"] == "TPC-H Skew Benchmark"
        assert info["num_queries"] == 22
        assert "skew_info" in info
        assert info["skew_info"]["preset"] == "moderate"

    def test_get_available_presets(self):

        presets = TPCHSkewBenchmark.get_available_presets()
        expected = ["none", "light", "moderate", "heavy", "extreme", "realistic"]
        assert presets == expected

    def test_get_preset_description(self):

        desc = TPCHSkewBenchmark.get_preset_description("moderate")
        assert "Moderate skew" in desc
        assert "0.5" in desc

    def test_get_preset_description_invalid(self):

        with pytest.raises(ValueError, match="Unknown preset"):
            TPCHSkewBenchmark.get_preset_description("invalid")

    def test_inherits_tpch_queries(self):

        benchmark = TPCHSkewBenchmark(scale_factor=0.01)
        queries = benchmark.get_queries()
        assert len(queries) == 22
        assert "1" in queries

    def test_get_single_query(self):

        benchmark = TPCHSkewBenchmark(scale_factor=0.01)
        query = benchmark.get_query(1)
        assert isinstance(query, str)
        assert "SELECT" in query.upper()

    def test_get_schema(self):

        benchmark = TPCHSkewBenchmark(scale_factor=0.01)
        schema = benchmark.get_schema()
        assert "lineitem" in schema
        assert "orders" in schema
        assert "customer" in schema


class TestTPCHSkewTopLevel:
    def test_initialization(self):

        benchmark = TPCHSkew(scale_factor=0.01)
        assert benchmark.scale_factor == 0.01

    def test_preset_access(self):

        benchmark = TPCHSkew(scale_factor=0.01, skew_preset="light")
        assert benchmark.skew_preset == "light"

    def test_skew_config_access(self):

        benchmark = TPCHSkew(scale_factor=0.01, skew_preset="heavy")
        assert benchmark.skew_config.skew_factor == 0.8

    def test_get_queries(self):

        benchmark = TPCHSkew(scale_factor=0.01)
        queries = benchmark.get_queries()
        assert len(queries) == 22

    def test_get_query_validation(self):

        benchmark = TPCHSkew(scale_factor=0.01)

        query = benchmark.get_query(1)
        assert isinstance(query, str)

        with pytest.raises(ValueError, match="1-22"):
            benchmark.get_query(0)
        with pytest.raises(ValueError, match="1-22"):
            benchmark.get_query(23)

        with pytest.raises(TypeError, match="integer"):
            benchmark.get_query("1")

    def test_get_skew_info(self):

        benchmark = TPCHSkew(scale_factor=0.01, skew_preset="realistic")
        info = benchmark.get_skew_info()
        assert info["preset"] == "realistic"
        assert info["skew_factor"] == 0.6

    def test_static_methods(self):

        presets = TPCHSkew.get_available_presets()
        assert "moderate" in presets

        desc = TPCHSkew.get_preset_description("extreme")
        assert "Extreme" in desc


class TestTPCHSkewPresetConfigurations:
    @pytest.mark.parametrize("preset", ["none", "light", "moderate", "heavy", "extreme", "realistic"])
    def test_preset_creates_valid_benchmark(self, preset):

        benchmark = TPCHSkew(scale_factor=0.01, skew_preset=preset)
        assert benchmark.skew_preset == preset
        queries = benchmark.get_queries()
        assert len(queries) == 22

    def test_none_preset_is_uniform(self):

        benchmark = TPCHSkew(scale_factor=0.01, skew_preset="none")
        config = benchmark.skew_config
        assert config.skew_factor == 0.0
        assert config.enable_attribute_skew is False
        assert config.enable_join_skew is False
        assert config.enable_temporal_skew is False

    def test_extreme_has_max_skew(self):

        benchmark = TPCHSkew(scale_factor=0.01, skew_preset="extreme")
        config = benchmark.skew_config
        assert config.skew_factor == 1.0
        assert config.attribute_skew.part_brand_skew == 1.0
        assert config.join_skew.part_popularity_skew == 1.0

    def test_realistic_has_temporal_skew(self):

        benchmark = TPCHSkew(scale_factor=0.01, skew_preset="realistic")
        config = benchmark.skew_config
        assert config.enable_temporal_skew is True
        assert config.temporal_skew.enable_hot_periods is True


class TestTPCHSkewDialectSupport:
    def test_query_dialect_translation(self):

        benchmark = TPCHSkew(scale_factor=0.01)

        query_default = benchmark.get_query(1)
        assert isinstance(query_default, str)

        query_duckdb = benchmark.get_query(1, dialect="duckdb")
        assert isinstance(query_duckdb, str)

    def test_get_all_queries_with_dialect(self):

        benchmark = TPCHSkew(scale_factor=0.01)
        queries = benchmark.get_queries(dialect="duckdb")
        assert len(queries) == 22
        for _query_id, query in queries.items():
            assert isinstance(query, str)


class TestTPCHSkewScaleFactor:
    def test_fractional_scale_factor(self):

        benchmark = TPCHSkew(scale_factor=0.01)
        assert benchmark.scale_factor == 0.01

    def test_integer_scale_factor(self):

        benchmark = TPCHSkew(scale_factor=1)
        assert benchmark.scale_factor == 1

    def test_scale_factor_in_benchmark_info(self):

        benchmark = TPCHSkew(scale_factor=10)
        info = benchmark.get_benchmark_info()
        assert info["scale_factor"] == 10


class TestCompareWithUniform:
    def test_compare_with_uniform_validation_none_adapter(self):

        benchmark = TPCHSkewBenchmark(scale_factor=0.01)
        with pytest.raises(ValueError, match="adapter cannot be None"):
            benchmark.compare_with_uniform(None)

    def test_compare_with_uniform_validation_invalid_queries(self):

        benchmark = TPCHSkewBenchmark(scale_factor=0.01)

        class MockAdapter:
            pass

        mock_adapter = MockAdapter()

        with pytest.raises(ValueError, match="Invalid query IDs"):
            benchmark.compare_with_uniform(mock_adapter, queries=[0, 1, 2])

        with pytest.raises(ValueError, match="Invalid query IDs"):
            benchmark.compare_with_uniform(mock_adapter, queries=[1, 23])

        with pytest.raises(ValueError, match="Invalid query IDs"):
            benchmark.compare_with_uniform(mock_adapter, queries=["1", 2])

    def test_compare_with_uniform_validation_invalid_iterations(self):

        benchmark = TPCHSkewBenchmark(scale_factor=0.01)

        class MockAdapter:
            pass

        mock_adapter = MockAdapter()

        with pytest.raises(ValueError, match="iterations must be >= 1"):
            benchmark.compare_with_uniform(mock_adapter, queries=[1], iterations=0)

        with pytest.raises(ValueError, match="iterations must be >= 1"):
            benchmark.compare_with_uniform(mock_adapter, queries=[1], iterations=-1)

    def test_geometric_mean_helper(self):

        result = TPCHSkewBenchmark._geometric_mean([1.0, 1.0, 1.0])
        assert result == pytest.approx(1.0)

        result = TPCHSkewBenchmark._geometric_mean([2.0, 8.0])
        assert result == pytest.approx(4.0)

        result = TPCHSkewBenchmark._geometric_mean([8.0])
        assert result == pytest.approx(8.0)

        result = TPCHSkewBenchmark._geometric_mean([])
        assert result == 0.0

    def test_compare_default_queries(self):
        benchmark = TPCHSkewBenchmark(scale_factor=0.01)

        assert benchmark.scale_factor == 0.01
        queries = benchmark.get_queries()
        assert len(queries) == 22


class TestTPCHSkewCompareWithUniformWrapper:
    def test_compare_with_uniform_exists_on_tpch_skew(self):

        benchmark = TPCHSkew(scale_factor=0.01)
        assert hasattr(benchmark, "compare_with_uniform")
        assert callable(benchmark.compare_with_uniform)

    def test_compare_with_uniform_delegates_to_impl(self):

        benchmark = TPCHSkew(scale_factor=0.01)

        with pytest.raises(ValueError, match="adapter cannot be None"):
            benchmark.compare_with_uniform(None)

    def test_compare_with_uniform_validates_queries(self):

        benchmark = TPCHSkew(scale_factor=0.01, skew_preset="moderate")

        class MockAdapter:
            pass

        mock_adapter = MockAdapter()

        with pytest.raises(ValueError, match="Invalid query IDs"):
            benchmark.compare_with_uniform(mock_adapter, queries=[0, 1, 2])

        with pytest.raises(ValueError, match="Invalid query IDs"):
            benchmark.compare_with_uniform(mock_adapter, queries=[1, 23])

    def test_compare_with_uniform_validates_iterations(self):

        benchmark = TPCHSkew(scale_factor=0.01)

        class MockAdapter:
            pass

        mock_adapter = MockAdapter()

        with pytest.raises(ValueError, match="iterations must be >= 1"):
            benchmark.compare_with_uniform(mock_adapter, queries=[1], iterations=0)

    def test_compare_signature_matches_implementation(self):

        import inspect

        wrapper_sig = inspect.signature(TPCHSkew.compare_with_uniform)
        impl_sig = inspect.signature(TPCHSkewBenchmark.compare_with_uniform)

        wrapper_params = [p for p in wrapper_sig.parameters if p != "self"]
        impl_params = [p for p in impl_sig.parameters if p != "self"]
        assert wrapper_params == impl_params


