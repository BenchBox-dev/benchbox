# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tpcds.compliance import (
    OFFICIAL_SCALE_POINTS,
    TPCDS_MIN_SUBSCALE,
    TpcdsComplianceClass,
    classify_tpcds_run,
    validate_tpcds_scale,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestClassifyTpcdsRun:
    def test_official_sf1_with_official_flag(self):
        assert classify_tpcds_run(1.0, official=True) is TpcdsComplianceClass.OFFICIAL

    def test_official_sf10_with_official_flag(self):
        assert classify_tpcds_run(10.0, official=True) is TpcdsComplianceClass.OFFICIAL

    def test_official_sf100_with_official_flag(self):
        assert classify_tpcds_run(100.0, official=True) is TpcdsComplianceClass.OFFICIAL

    def test_all_official_scale_points_are_official(self):
        for sf in OFFICIAL_SCALE_POINTS:
            result = classify_tpcds_run(sf, official=True)
            assert result is TpcdsComplianceClass.OFFICIAL, f"SF={sf} should be OFFICIAL"

    def test_sf1_without_official_flag_is_nonstandard(self):
        assert classify_tpcds_run(1.0, official=False) is TpcdsComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_sf10_without_official_flag_is_nonstandard(self):
        assert classify_tpcds_run(10.0, official=False) is TpcdsComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_non_official_scale_point_with_official_flag_is_nonstandard(self):
        assert classify_tpcds_run(2.0, official=True) is TpcdsComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_subscale_is_unofficial_subscale_regardless_of_official_flag(self):
        assert classify_tpcds_run(0.5, official=False) is TpcdsComplianceClass.UNOFFICIAL_SUBSCALE
        assert classify_tpcds_run(0.5, official=True) is TpcdsComplianceClass.UNOFFICIAL_SUBSCALE

    def test_subscale_just_below_one(self):
        assert classify_tpcds_run(0.999) is TpcdsComplianceClass.UNOFFICIAL_SUBSCALE

    def test_minimum_subscale(self):
        assert classify_tpcds_run(TPCDS_MIN_SUBSCALE) is TpcdsComplianceClass.UNOFFICIAL_SUBSCALE

    def test_default_official_flag_is_false(self):
        assert classify_tpcds_run(1.0) is TpcdsComplianceClass.UNOFFICIAL_NONSTANDARD
        assert classify_tpcds_run(0.5) is TpcdsComplianceClass.UNOFFICIAL_SUBSCALE


class TestValidateTpcdsScale:
    def test_subscale_returns_subscale_class(self):
        result = validate_tpcds_scale(0.5)
        assert result is TpcdsComplianceClass.UNOFFICIAL_SUBSCALE

    def test_sf_above_one_returns_nonstandard_class(self):
        result = validate_tpcds_scale(1.0)
        assert result is TpcdsComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_minimum_subscale_accepted(self):
        result = validate_tpcds_scale(TPCDS_MIN_SUBSCALE)
        assert result is TpcdsComplianceClass.UNOFFICIAL_SUBSCALE

    def test_below_minimum_subscale_raises(self):
        below_min = TPCDS_MIN_SUBSCALE / 10.0
        with pytest.raises(ValueError, match="minimum subscale"):
            validate_tpcds_scale(below_min)

    def test_zero_scale_raises(self):
        with pytest.raises(ValueError, match="must be positive"):
            validate_tpcds_scale(0.0)

    def test_negative_scale_raises(self):
        with pytest.raises(ValueError, match="must be positive"):
            validate_tpcds_scale(-1.0)

    def test_exceeds_maximum_raises(self):
        with pytest.raises(ValueError, match="exceeds maximum"):
            validate_tpcds_scale(100001.0)

    def test_official_scale_point_returns_nonstandard_without_official_flag(self):
        result = validate_tpcds_scale(1.0)
        assert result is TpcdsComplianceClass.UNOFFICIAL_NONSTANDARD


class TestTpcdsComplianceClassEnum:
    def test_enum_values_are_strings(self):
        assert TpcdsComplianceClass.OFFICIAL == "official"
        assert TpcdsComplianceClass.UNOFFICIAL_NONSTANDARD == "unofficial_nonstandard"
        assert TpcdsComplianceClass.UNOFFICIAL_SUBSCALE == "unofficial_subscale"

    def test_enum_is_in_set_by_string_value(self):
        unofficial_classes = {"unofficial_nonstandard", "unofficial_subscale"}
        assert TpcdsComplianceClass.UNOFFICIAL_SUBSCALE in unofficial_classes
        assert TpcdsComplianceClass.UNOFFICIAL_NONSTANDARD in unofficial_classes
        assert TpcdsComplianceClass.OFFICIAL not in unofficial_classes

    def test_value_attribute_matches_string(self):
        assert TpcdsComplianceClass.OFFICIAL.value == "official"
        assert TpcdsComplianceClass.UNOFFICIAL_SUBSCALE.value == "unofficial_subscale"


class TestOfficialFlagReachesTheClassifier:
    def test_validate_tpcds_scale_forwards_official(self):
        assert validate_tpcds_scale(1.0, official=True) is TpcdsComplianceClass.OFFICIAL
        assert validate_tpcds_scale(1.0) is TpcdsComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_official_only_applies_at_official_scale_points(self):
        assert validate_tpcds_scale(2.0, official=True) is TpcdsComplianceClass.UNOFFICIAL_NONSTANDARD
        assert validate_tpcds_scale(0.5, official=True) is TpcdsComplianceClass.UNOFFICIAL_SUBSCALE

    def test_benchmark_runner_classifies_an_official_run_as_official(self):
        from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

        assert TPCDSBenchmark(scale_factor=1.0, official=True).compliance_class is TpcdsComplianceClass.OFFICIAL
        assert TPCDSBenchmark(scale_factor=1.0).compliance_class is TpcdsComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_loader_forwards_official_from_the_benchmark_config(self):
        from benchbox.core.benchmark_loader import get_benchmark_instance
        from benchbox.core.schemas import BenchmarkConfig

        config = BenchmarkConfig(name="tpcds", display_name="TPC-DS", scale_factor=1.0, official=True)
        assert get_benchmark_instance(config, None).compliance_class is TpcdsComplianceClass.OFFICIAL

        default = BenchmarkConfig(name="tpcds", display_name="TPC-DS", scale_factor=1.0)
        assert get_benchmark_instance(default, None).compliance_class is TpcdsComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_an_official_run_is_not_refused_by_submit_classification(self):
        from benchbox.validation.bundle import CLI_REFUSED_COMPLIANCE_CLASSES

        official = validate_tpcds_scale(1.0, official=True)
        assert official.value not in CLI_REFUSED_COMPLIANCE_CLASSES
        assert validate_tpcds_scale(1.0).value in CLI_REFUSED_COMPLIANCE_CLASSES

    def test_both_construction_paths_carry_compliance_mode(self):
        import inspect

        from benchbox.cli import orchestrator
        from benchbox.core import benchmark_loader

        assert "compliance_mode_kwargs(config)" in inspect.getsource(benchmark_loader.get_benchmark_instance), (
            "get_benchmark_instance does not apply compliance_mode_kwargs; CLI runs would be unsubmittable"
        )
        import ast

        tree = ast.parse(inspect.getsource(orchestrator))
        called = {
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        assert "get_benchmark_instance" in called
        assert not called & {"benchmark_class", "instantiate_benchmark_class"}, (
            "benchbox.cli.orchestrator builds benchmark instances outside get_benchmark_instance; "
            "CLI runs would not carry compliance_mode_kwargs"
        )

    def test_compliance_mode_kwargs_only_targets_gated_benchmarks(self):
        from benchbox.core.benchmark_loader import compliance_mode_kwargs
        from benchbox.core.schemas import BenchmarkConfig

        for name, display_name in (("tpcds", "TPC-DS"), ("tpch", "TPC-H")):
            gated = BenchmarkConfig(name=name, display_name=display_name, scale_factor=1.0, official=True)
            assert compliance_mode_kwargs(gated) == {"official": True}

        ungated = BenchmarkConfig(name="tpcdi", display_name="TPC-DI", scale_factor=1.0, official=True)
        assert compliance_mode_kwargs(ungated) == {}

    def test_official_benchmark_wrapper_defaults_to_official(self):
        from benchbox.core.tpcds.compliance import TpcdsComplianceClass
        from benchbox.core.tpcds.official_benchmark import TPCDSOfficialBenchmark

        wrapper = TPCDSOfficialBenchmark(scale_factor=1.0)
        assert wrapper.benchmark.compliance_class is TpcdsComplianceClass.OFFICIAL


