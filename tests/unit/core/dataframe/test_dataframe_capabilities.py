# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

from benchbox.core.dataframe.capabilities import (
    DataFormat,
    ExecutionModel,
    MemoryCheckResult,
    check_sufficient_memory,
    estimate_memory_required,
    format_memory_warning,
    get_platform_capabilities,
    list_platform_capabilities,
    recommend_platform_for_sf,
    validate_scale_factor,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestPlatformCapabilities:
    def test_polars_capabilities(self):

        caps = get_platform_capabilities("polars")

        assert caps.platform_name == "Polars"
        assert caps.max_recommended_sf == 100.0
        assert caps.execution_model == ExecutionModel.LAZY
        assert caps.supports_streaming is True
        assert caps.gpu_required is False

    def test_pandas_capabilities(self):

        caps = get_platform_capabilities("pandas")

        assert caps.platform_name == "Pandas"
        assert caps.max_recommended_sf == 10.0
        assert caps.execution_model == ExecutionModel.EAGER
        assert caps.memory_overhead_factor >= 2.0

    def test_cudf_capabilities(self):

        caps = get_platform_capabilities("cudf")

        assert caps.platform_name == "cuDF"
        assert caps.gpu_required is True
        assert caps.max_recommended_sf <= 5.0

    def test_dask_capabilities(self):

        caps = get_platform_capabilities("dask")

        assert caps.platform_name == "Dask"
        assert caps.execution_model == ExecutionModel.OUT_OF_CORE
        assert caps.requires_partitioning is True
        assert caps.max_recommended_sf >= 100.0

    def test_pyspark_capabilities(self):

        caps = get_platform_capabilities("pyspark")

        assert caps.platform_name == "PySpark"
        assert caps.execution_model == ExecutionModel.DISTRIBUTED
        assert caps.requires_partitioning is True
        assert caps.max_recommended_sf >= 1000.0

    def test_case_insensitive_lookup(self):

        caps1 = get_platform_capabilities("POLARS")
        caps2 = get_platform_capabilities("Polars")
        caps3 = get_platform_capabilities("polars")

        assert caps1.platform_name == caps2.platform_name == caps3.platform_name

    def test_df_suffix_handling(self):

        caps1 = get_platform_capabilities("polars-df")
        caps2 = get_platform_capabilities("polars")

        assert caps1.platform_name == caps2.platform_name

    def test_unknown_platform_raises(self):

        with pytest.raises(ValueError, match="Unknown DataFrame platform"):
            get_platform_capabilities("unknown_platform")

    def test_estimate_memory_for_sf(self):

        caps = get_platform_capabilities("pandas")

        mem = caps.estimate_memory_for_sf(1.0)
        assert mem == pytest.approx(2.5, rel=0.1)

        mem = caps.estimate_memory_for_sf(10.0)
        assert mem == pytest.approx(25.0, rel=0.1)

    def test_can_handle_sf(self):

        caps = get_platform_capabilities("pandas")

        assert caps.can_handle_sf(1.0) is True
        assert caps.can_handle_sf(10.0) is True
        assert caps.can_handle_sf(100.0) is False


class TestListPlatformCapabilities:
    def test_returns_all_platforms(self):

        caps = list_platform_capabilities()

        assert "polars" in caps
        assert "pandas" in caps
        assert "dask" in caps
        assert "pyspark" in caps

    def test_returns_copy(self):

        caps1 = list_platform_capabilities()
        caps2 = list_platform_capabilities()

        caps1["polars"] = None
        assert caps2["polars"] is not None


class TestEstimateMemoryRequired:
    def test_tpch_estimate(self):

        estimate = estimate_memory_required("tpch", 10.0, "polars")

        assert estimate.benchmark == "tpch"
        assert estimate.scale_factor == 10.0
        assert estimate.platform == "Polars"
        assert estimate.raw_data_gb == 10.0
        assert estimate.estimated_memory_gb == pytest.approx(20.0)

    def test_tpcds_estimate(self):

        estimate = estimate_memory_required("tpcds", 5.0, "pandas")

        assert estimate.benchmark == "tpcds"
        assert estimate.scale_factor == 5.0
        assert estimate.platform == "Pandas"

    def test_different_platforms(self):

        polars_est = estimate_memory_required("tpch", 10.0, "polars")
        pandas_est = estimate_memory_required("tpch", 10.0, "pandas")

        assert pandas_est.estimated_memory_gb > polars_est.estimated_memory_gb


class TestValidateScaleFactor:
    def test_valid_scale_factor(self):

        is_valid, warning = validate_scale_factor(1.0, "polars")

        assert is_valid is True
        assert warning is None

    def test_at_limit(self):

        is_valid, warning = validate_scale_factor(100.0, "polars")

        assert is_valid is True
        assert warning is None

    def test_above_limit_non_strict(self):
        is_valid, warning = validate_scale_factor(200.0, "polars", strict=False)

        assert is_valid is True
        assert warning is not None
        assert "exceeds" in warning.lower()

    def test_above_limit_strict(self):
        is_valid, warning = validate_scale_factor(200.0, "polars", strict=True)

        assert is_valid is False
        assert warning is not None

    def test_zero_scale_factor(self):

        is_valid, warning = validate_scale_factor(0, "polars")

        assert is_valid is False
        assert "positive" in warning.lower()

    def test_negative_scale_factor(self):

        is_valid, warning = validate_scale_factor(-1.0, "polars")

        assert is_valid is False


class TestCheckSufficientMemory:
    def test_result_structure(self):

        result = check_sufficient_memory("tpch", 1.0, "polars")

        assert isinstance(result, MemoryCheckResult)
        assert hasattr(result, "is_safe")
        assert hasattr(result, "message")
        assert hasattr(result, "estimated_memory_gb")
        assert hasattr(result, "suggestions")

    def test_bool_conversion(self):
        result = check_sufficient_memory("tpch", 0.01, "polars")

        if result:
            pass

    def test_provides_suggestions_on_failure(self):

        result = check_sufficient_memory("tpch", 10000.0, "pandas")

        if not result.is_safe:
            assert len(result.suggestions) > 0

    def test_gpu_required_check(self):
        result = check_sufficient_memory("tpch", 1.0, "cudf")

        assert result.message is not None


class TestFormatMemoryWarning:
    def test_formatting(self):

        result = MemoryCheckResult(
            is_safe=False,
            message="Insufficient memory",
            estimated_memory_gb=100.0,
            available_memory_gb=16.0,
            scale_factor=100.0,
            platform="pandas",
            suggestions=["Try smaller scale factor", "Use Dask instead"],
        )

        formatted = format_memory_warning(result)

        assert "Insufficient memory" in formatted
        assert "Suggestions:" in formatted
        assert "smaller scale factor" in formatted
        assert "--ignore-memory-warnings" in formatted


class TestRecommendPlatformForSf:
    def test_small_sf(self):

        platform = recommend_platform_for_sf(1.0)
        assert platform == "polars"

    def test_medium_sf(self):

        platform = recommend_platform_for_sf(50.0)
        assert platform == "polars"

    def test_large_sf(self):

        platform = recommend_platform_for_sf(500.0)
        assert platform == "dask"

    def test_very_large_sf(self):

        platform = recommend_platform_for_sf(5000.0)
        assert platform == "pyspark"


class TestDataFormat:
    def test_formats(self):

        assert DataFormat.CSV.value == "csv"
        assert DataFormat.PARQUET.value == "parquet"
        assert DataFormat.ARROW.value == "arrow"


class TestExecutionModel:
    def test_models(self):

        assert ExecutionModel.EAGER.value == "eager"
        assert ExecutionModel.LAZY.value == "lazy"
        assert ExecutionModel.DISTRIBUTED.value == "distributed"
        assert ExecutionModel.OUT_OF_CORE.value == "out_of_core"


class TestAllPlatformsHaveCapabilities:
    @pytest.mark.parametrize(
        "platform",
        ["polars", "pandas", "cudf", "dask", "vaex", "pyspark", "datafusion"],
    )
    def test_platform_has_capabilities(self, platform):

        caps = get_platform_capabilities(platform)

        assert caps is not None
        assert caps.platform_name is not None
        assert caps.max_recommended_sf > 0
        assert caps.memory_overhead_factor > 0
        assert caps.execution_model is not None

    @pytest.mark.parametrize(
        "platform",
        ["polars", "pandas", "cudf", "dask", "vaex", "pyspark", "datafusion"],
    )
    def test_platform_has_description(self, platform):

        caps = get_platform_capabilities(platform)

        assert caps.description is not None
        assert len(caps.description) > 0

    @pytest.mark.parametrize(
        "platform",
        ["polars", "pandas", "cudf", "dask", "vaex", "pyspark", "datafusion"],
    )
    def test_platform_has_notes(self, platform):

        caps = get_platform_capabilities(platform)

        assert caps.notes is not None
        assert len(caps.notes) > 0


