# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tpch.compliance import (
    OFFICIAL_SCALE_POINTS,
    TpchComplianceClass,
    classify_tpch_run,
    validate_tpch_scale,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestClassifyTpchRun:
    def test_official_sf1_with_official_flag(self):
        assert classify_tpch_run(1.0, official=True) is TpchComplianceClass.OFFICIAL

    def test_official_sf10_with_official_flag(self):
        assert classify_tpch_run(10.0, official=True) is TpchComplianceClass.OFFICIAL

    def test_official_sf30_with_official_flag(self):
        assert classify_tpch_run(30.0, official=True) is TpchComplianceClass.OFFICIAL

    def test_all_official_scale_points_are_official(self):
        assert 30.0 in OFFICIAL_SCALE_POINTS
        for sf in OFFICIAL_SCALE_POINTS:
            result = classify_tpch_run(sf, official=True)
            assert result is TpchComplianceClass.OFFICIAL, f"SF={sf} should be OFFICIAL"

    def test_sf1_without_official_flag_is_nonstandard(self):
        assert classify_tpch_run(1.0, official=False) is TpchComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_sf30_without_official_flag_is_nonstandard(self):
        assert classify_tpch_run(30.0, official=False) is TpchComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_non_official_scale_point_with_official_flag_is_nonstandard(self):
        assert classify_tpch_run(2.0, official=True) is TpchComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_subscale_is_unofficial_subscale_regardless_of_official_flag(self):
        assert classify_tpch_run(0.5, official=False) is TpchComplianceClass.UNOFFICIAL_SUBSCALE
        assert classify_tpch_run(0.5, official=True) is TpchComplianceClass.UNOFFICIAL_SUBSCALE

    def test_subscale_just_below_one(self):
        assert classify_tpch_run(0.999) is TpchComplianceClass.UNOFFICIAL_SUBSCALE

    def test_dev_scale_factors_classify_subscale(self):
        assert classify_tpch_run(0.01) is TpchComplianceClass.UNOFFICIAL_SUBSCALE
        assert classify_tpch_run(0.1) is TpchComplianceClass.UNOFFICIAL_SUBSCALE

    def test_default_official_flag_is_false(self):
        assert classify_tpch_run(1.0) is TpchComplianceClass.UNOFFICIAL_NONSTANDARD
        assert classify_tpch_run(0.5) is TpchComplianceClass.UNOFFICIAL_SUBSCALE


class TestValidateTpchScale:
    def test_subscale_returns_subscale_class(self):
        assert validate_tpch_scale(0.5) is TpchComplianceClass.UNOFFICIAL_SUBSCALE

    def test_sf_above_one_returns_nonstandard_class(self):
        assert validate_tpch_scale(1.0) is TpchComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_zero_scale_raises(self):
        with pytest.raises(ValueError, match="must be positive"):
            validate_tpch_scale(0.0)

    def test_negative_scale_raises(self):
        with pytest.raises(ValueError, match="must be positive"):
            validate_tpch_scale(-1.0)

    def test_exceeds_maximum_raises(self):
        with pytest.raises(ValueError, match="exceeds maximum"):
            validate_tpch_scale(100001.0)

    def test_validate_forwards_official(self):
        assert validate_tpch_scale(1.0, official=True) is TpchComplianceClass.OFFICIAL
        assert validate_tpch_scale(30.0, official=True) is TpchComplianceClass.OFFICIAL
        assert validate_tpch_scale(2.0, official=True) is TpchComplianceClass.UNOFFICIAL_NONSTANDARD
        assert validate_tpch_scale(0.5, official=True) is TpchComplianceClass.UNOFFICIAL_SUBSCALE


class TestTpchComplianceClassEnum:
    def test_enum_values_are_strings(self):
        assert TpchComplianceClass.OFFICIAL == "official"
        assert TpchComplianceClass.UNOFFICIAL_NONSTANDARD == "unofficial_nonstandard"
        assert TpchComplianceClass.UNOFFICIAL_SUBSCALE == "unofficial_subscale"

    def test_enum_is_in_set_by_string_value(self):
        unofficial_classes = {"unofficial_nonstandard", "unofficial_subscale"}
        assert TpchComplianceClass.UNOFFICIAL_SUBSCALE in unofficial_classes
        assert TpchComplianceClass.UNOFFICIAL_NONSTANDARD in unofficial_classes
        assert TpchComplianceClass.OFFICIAL not in unofficial_classes

    def test_value_attribute_matches_string(self):
        assert TpchComplianceClass.OFFICIAL.value == "official"
        assert TpchComplianceClass.UNOFFICIAL_SUBSCALE.value == "unofficial_subscale"


class TestOfficialFlagReachesTheClassifier:
    def test_benchmark_classifies_an_official_run_as_official(self):
        from benchbox.core.tpch.benchmark import TPCHBenchmark

        assert TPCHBenchmark(scale_factor=1.0, official=True).compliance_class is TpchComplianceClass.OFFICIAL
        assert TPCHBenchmark(scale_factor=1.0).compliance_class is TpchComplianceClass.UNOFFICIAL_NONSTANDARD
        assert TPCHBenchmark(scale_factor=0.01).compliance_class is TpchComplianceClass.UNOFFICIAL_SUBSCALE

    def test_loader_forwards_official_from_the_benchmark_config(self):
        from benchbox.core.benchmark_loader import get_benchmark_instance
        from benchbox.core.schemas import BenchmarkConfig

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0, official=True)
        assert get_benchmark_instance(config, None).compliance_class is TpchComplianceClass.OFFICIAL

        default = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0)
        assert get_benchmark_instance(default, None).compliance_class is TpchComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_compliance_mode_kwargs_targets_tpch(self):
        from benchbox.core.benchmark_loader import compliance_mode_kwargs
        from benchbox.core.schemas import BenchmarkConfig

        gated = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0, official=True)
        assert compliance_mode_kwargs(gated) == {"official": True}

        ungated = BenchmarkConfig(name="tpcdi", display_name="TPC-DI", scale_factor=1.0, official=True)
        assert compliance_mode_kwargs(ungated) == {}

    def test_an_official_run_is_not_refused_by_submit_classification(self):
        from benchbox.validation.bundle import CLI_REFUSED_COMPLIANCE_CLASSES

        official = validate_tpch_scale(1.0, official=True)
        assert official.value not in CLI_REFUSED_COMPLIANCE_CLASSES
        assert validate_tpch_scale(1.0).value in CLI_REFUSED_COMPLIANCE_CLASSES
        assert validate_tpch_scale(0.01).value in CLI_REFUSED_COMPLIANCE_CLASSES

    def test_dataframe_fallback_classifies_tpch_from_config(self):
        from benchbox.core.schemas import BenchmarkConfig
        from benchbox.platforms.dataframe.benchmark_mixin import dataframe_compliance_class

        official = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=30.0, official=True)
        assert dataframe_compliance_class(None, official) == "official"

        casual = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0)
        assert dataframe_compliance_class(None, casual) == "unofficial_nonstandard"


