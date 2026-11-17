# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import patch

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
from benchbox.core.dataframe.memory_model import (
    PLATFORM_BASELINE_GB,
    SUBSET_MODEL_MAX_SF,
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


def _guarded(estimate, available, margin=0.2):
    target = "benchbox.core.dataframe.capabilities"
    fake = SimpleNamespace(estimated_memory_gb=estimate, scope="full", tables=())
    with (
        patch(f"{target}.get_available_memory_gb", return_value=available),
        patch(f"{target}.estimate_memory_required", return_value=fake),
    ):
        return check_sufficient_memory("tpch", 10.0, "polars", safety_margin=margin)


def _printed(message):
    available = float(re.search(r"([\d.]+) GB available", message).group(1))
    threshold = float(re.search(r"at least ([\d.]+) GB", message).group(1))
    return available, threshold


class TestMemoryGuardMessage:
    @pytest.mark.parametrize("estimate", [0.05, 0.37, 1.0, 2.0, 3.0, 3.33, 7.77, 18.0, 20.0])
    @pytest.mark.parametrize("margin", [0.0, 0.2, 0.25])
    def test_printed_available_below_printed_threshold(self, estimate, margin):
        threshold = estimate * (1 + margin)
        for step in range(1, 1001):
            available = threshold * step / 1000 * 0.9999 if step < 1000 else threshold - 1e-9
            result = _guarded(estimate, available, margin)
            assert result.is_safe is False
            shown_available, shown_threshold = _printed(result.message)
            assert shown_available < shown_threshold

    @pytest.mark.parametrize(("estimate", "available"), [(3.0, 3.5), (2.0, 2.36), (3.0, 3.1)])
    def test_reported_cases_refused_with_consistent_message(self, estimate, available):
        result = _guarded(estimate, available)

        assert result.is_safe is False
        shown_available, shown_threshold = _printed(result.message)
        assert shown_available < shown_threshold
        assert f"~{estimate:.1f} GB estimated" in result.message
        assert "20% safety margin" in result.message
        assert "full-benchmark estimate" in result.message
        assert result.estimated_memory_gb == estimate
        assert result.available_memory_gb == available

    @pytest.mark.parametrize(
        ("estimate", "available", "safe"),
        [
            (3.0, 3.5, False),
            (2.0, 2.36, False),
            (3.0, 3.1, False),
            (3.0, 3.59, False),
            (3.0, 3.6, True),
            (3.0, 3.61, True),
            (2.0, 2.39, False),
            (2.0, 2.4, True),
            (3.0, 10.0, True),
        ],
    )
    def test_decision_unchanged(self, estimate, available, safe):
        assert _guarded(estimate, available).is_safe is safe

    def test_passing_message_states_margin_and_threshold(self):
        result = _guarded(3.0, 10.0)

        assert "~3.0 GB estimated" in result.message
        assert "20% safety margin" in result.message
        assert "3.6 GB" in result.message
        assert "full benchmark" in result.message


def _guarded_subset(benchmark, scale_factor, platform, queries, available):
    target = "benchbox.core.dataframe.capabilities"
    with patch(f"{target}.get_available_memory_gb", return_value=available):
        return check_sufficient_memory(benchmark, scale_factor, platform, queries)


class TestSubsetEstimate:
    def test_subset_counts_only_tables_the_queries_read(self):
        subset = estimate_memory_required("tpcds", 10.0, "polars", ["Q16", "Q94", "Q95"])
        full = estimate_memory_required("tpcds", 10.0, "polars")

        assert subset.scope == "subset"
        assert subset.tables == (
            "call_center",
            "catalog_returns",
            "catalog_sales",
            "customer_address",
            "date_dim",
            "web_returns",
            "web_sales",
            "web_site",
        )
        assert subset.estimated_memory_gb < full.estimated_memory_gb
        assert subset.estimated_memory_gb == pytest.approx(subset.raw_data_gb * 2.0, rel=1e-9)

    def test_subset_table_set_is_the_union_across_queries(self):
        single = estimate_memory_required("tpch", 1.0, "polars", ["Q6"])
        pair = estimate_memory_required("tpch", 1.0, "polars", ["q6", "11"])

        assert single.tables == ("lineitem",)
        assert pair.tables == ("lineitem", "nation", "partsupp", "supplier")
        assert pair.raw_data_gb > single.raw_data_gb

    def test_comma_separated_selection_is_split_into_query_ids(self):
        from_string = estimate_memory_required("tpch", 1.0, "polars", "Q6,Q11")
        from_list = estimate_memory_required("tpch", 1.0, "polars", ["Q6", "Q11"])

        assert from_string.tables == from_list.tables
        assert from_string.estimated_memory_gb == from_list.estimated_memory_gb

    def test_full_benchmark_estimate_is_unchanged(self):
        estimate = estimate_memory_required("tpcds", 10.0, "polars")

        assert estimate.scope == "full"
        assert estimate.tables == ()
        assert estimate.raw_data_gb == 10.0
        assert estimate.estimated_memory_gb == pytest.approx(20.0)

    @pytest.mark.parametrize("queries", [None, [], ()])
    def test_no_selection_is_the_full_estimate(self, queries):
        assert estimate_memory_required("tpch", 10.0, "polars", queries).scope == "full"

    @pytest.mark.parametrize("queries", [["Q999"], ["Q16", "Q999"], ["not-a-query"], ["Q0"]])
    def test_unknown_query_id_falls_back_to_full_estimate(self, queries):
        estimate = estimate_memory_required("tpcds", 10.0, "polars", queries)

        assert estimate.scope == "full"
        assert estimate.tables == ()
        assert estimate.estimated_memory_gb == pytest.approx(20.0)

    @pytest.mark.parametrize("benchmark_id", ["ssb", "clickbench", "joinorder"])
    def test_benchmark_without_table_map_falls_back_to_full_estimate(self, benchmark_id):
        assert estimate_memory_required(benchmark_id, 10.0, "polars", ["Q1"]).scope == "full"

    def test_scale_factor_above_validated_range_falls_back_to_full_estimate(self):
        above = SUBSET_MODEL_MAX_SF + 1

        estimate = estimate_memory_required("tpch", above, "polars", ["Q6"])

        assert estimate.scope == "full"
        assert estimate.estimated_memory_gb == pytest.approx(above * 2.0)

    def test_platform_without_measured_baseline_falls_back_to_full_estimate(self):
        assert estimate_memory_required("tpch", 10.0, "pandas", ["Q6"]).scope == "full"

    @pytest.mark.parametrize("platform", sorted(PLATFORM_BASELINE_GB))
    def test_estimate_never_below_platform_baseline(self, platform):
        estimate = estimate_memory_required("tpch", 0.01, platform, ["Q6"])

        assert estimate.scope == "subset"
        assert estimate.estimated_memory_gb == PLATFORM_BASELINE_GB[platform]

    def test_reported_motivating_case_is_no_longer_refused(self):
        full = _guarded_subset("tpcds", 10.0, "polars", None, 15.0)
        subset = _guarded_subset("tpcds", 10.0, "polars", ["Q16", "Q94", "Q95"], 15.0)

        assert full.is_safe is False
        assert subset.is_safe is True

    def test_subset_that_is_too_large_is_still_refused(self):
        result = _guarded_subset("tpch", 100.0, "polars", ["Q1"], 16.0)

        assert result.is_safe is False

    def test_refusal_message_names_subset_scope_and_tables(self):
        result = _guarded_subset("tpcds", 10.0, "polars", ["Q16", "Q94", "Q95"], 3.0)

        assert result.is_safe is False
        assert "subset estimate" in result.message
        assert "full-benchmark estimate" not in result.message
        assert "catalog_sales" in result.message
        assert "web_sales" in result.message
        assert "store_sales" not in result.message
        assert "20% safety margin" in result.message

    def test_refusal_message_names_full_scope_on_unknown_query(self):
        result = _guarded_subset("tpcds", 10.0, "polars", ["Q999"], 3.0)

        assert result.is_safe is False
        assert "full-benchmark estimate" in result.message
        assert "no table-level estimate applies" in result.message
        assert "subset estimate" not in result.message

    def test_refusal_message_for_full_run_has_no_subset_wording(self):
        result = _guarded_subset("tpch", 10.0, "polars", None, 3.0)

        assert result.is_safe is False
        assert "full-benchmark estimate" in result.message
        assert "no table-level estimate applies" not in result.message


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
