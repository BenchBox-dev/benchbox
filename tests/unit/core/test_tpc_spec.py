from __future__ import annotations

import pytest

from benchbox.core.run_service import execute_run, validate_stream_count
from benchbox.core.schemas import BenchmarkConfig
from benchbox.core.tpc_spec import (
    TPCDS_MIN_STREAMS,
    TPCH_MIN_STREAMS,
    full_spec_violations,
    min_streams_for_scale,
    refresh_functions_enabled,
    require_full_spec,
)
from benchbox.core.tpcds.compliance import TpcdsComplianceClass, classify_tpcds_run
from benchbox.core.tpch.compliance import TpchComplianceClass, classify_tpch_run

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TPCH_PHASES = ("load", "power", "throughput")
TPCDS_PHASES = ("load", "power", "throughput", "maintenance")


def _tpch(**overrides):
    kwargs = {
        "seed": 42,
        "phases": TPCH_PHASES,
        "streams": 5,
        "query_subset": None,
        "refresh_functions": True,
    }
    kwargs.update(overrides)
    return full_spec_violations("tpch", 100.0, **kwargs)


def _tpcds(**overrides):
    kwargs = {"seed": 42, "phases": TPCDS_PHASES, "streams": 4, "query_subset": None}
    kwargs.update(overrides)
    return full_spec_violations("tpcds", 100.0, **kwargs)


class TestMinimumStreams:
    @pytest.mark.parametrize(("scale", "expected"), sorted(TPCH_MIN_STREAMS.items()))
    def test_tpch_table(self, scale, expected):
        assert min_streams_for_scale("tpch", scale) == expected

    @pytest.mark.parametrize("scale", sorted(TPCDS_MIN_STREAMS))
    def test_tpcds_minimum_is_even_and_covers_table(self, scale):
        minimum = min_streams_for_scale("tpcds", scale)
        assert minimum >= TPCDS_MIN_STREAMS[scale]
        assert minimum % 2 == 0
        assert minimum >= 4

    def test_tpcds_small_scale_uses_floor(self):
        assert min_streams_for_scale("tpcds", 1.0) == 4

    def test_other_benchmark_has_no_minimum(self):
        assert min_streams_for_scale("ssb", 1.0) is None

    def test_validate_stream_count_enforces_per_scale_minimum(self):
        with pytest.raises(ValueError, match="at least 5|>= 5"):
            validate_stream_count(3, "power,throughput", benchmark="tpch", scale_factor=100.0)
        validate_stream_count(5, "power,throughput", benchmark="tpch", scale_factor=100.0)

    def test_validate_stream_count_without_benchmark_keeps_flat_minimum(self):
        validate_stream_count(2, "throughput")
        with pytest.raises(ValueError):
            validate_stream_count(1, "throughput")


class TestFullSpecViolations:
    def test_compliant_tpch_run_has_no_violations(self):
        assert _tpch() == []

    def test_compliant_tpcds_run_has_no_violations(self):
        assert _tpcds() == []

    def test_non_tpc_benchmark_is_not_checked(self):
        assert full_spec_violations("ssb", 1.0, seed=None, phases=(), streams=None, query_subset=["Q1"]) == []

    @pytest.mark.parametrize("builder", [_tpch, _tpcds])
    def test_missing_seed_refused(self, builder):
        assert any("seed" in item for item in builder(seed=None))

    @pytest.mark.parametrize("builder", [_tpch, _tpcds])
    def test_query_subset_refused(self, builder):
        assert any("query subsets" in item for item in builder(query_subset=["1", "2"]))

    def test_tpch_missing_throughput_phase_refused(self):
        assert any("throughput" in item for item in _tpch(phases=("load", "power")))

    def test_tpch_missing_power_phase_refused(self):
        assert any("power" in item for item in _tpch(phases=("load", "throughput")))

    def test_tpcds_missing_maintenance_phase_refused(self):
        assert any("maintenance" in item for item in _tpcds(phases=("load", "power", "throughput")))

    def test_tpch_stream_minimum_per_scale(self):
        assert any("at least 5" in item for item in _tpch(streams=4))

    def test_streams_required(self):
        assert any("--streams is required" in item for item in _tpch(streams=None))

    def test_tpcds_odd_stream_count_refused(self):
        assert any("even" in item for item in _tpcds(streams=5))

    def test_tpcds_stream_minimum(self):
        assert any("at least 4" in item for item in _tpcds(streams=2))

    def test_tpch_refresh_functions_required(self):
        assert any("RF1/RF2" in item for item in _tpch(refresh_functions=False))
        assert any("RF1/RF2" in item for item in _tpch(refresh_functions=None))

    def test_require_full_spec_lists_every_violation(self):
        with pytest.raises(ValueError) as exc:
            require_full_spec(
                "tpch", 100.0, seed=None, phases=("power",), streams=2, query_subset=["1"], refresh_functions=False
            )
        message = str(exc.value)
        for fragment in ("seed", "query subsets", "partial runs", "at least 5", "RF1/RF2"):
            assert fragment in message


class TestRefreshFunctionsEnabled:
    def test_reads_benchmark_options(self):
        assert refresh_functions_enabled({"benchmark_options": {"tpch_refresh_functions": True}}) is True
        assert refresh_functions_enabled({"benchmark_options": {"tpch_refresh_functions": False}}) is False

    def test_falls_back_to_instance_attribute(self):
        class Instance:
            tpch_refresh_functions = True

        assert refresh_functions_enabled({}, Instance()) is True
        assert refresh_functions_enabled({}, object()) is False


class TestClassificationDependsOnConditions:
    def test_tpch_violation_downgrades_official(self):
        assert classify_tpch_run(100.0, official=True) is TpchComplianceClass.OFFICIAL
        downgraded = classify_tpch_run(100.0, official=True, spec_violations=["a --seed is required"])
        assert downgraded is TpchComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_tpcds_violation_downgrades_official(self):
        assert classify_tpcds_run(100.0, official=True) is TpcdsComplianceClass.OFFICIAL
        downgraded = classify_tpcds_run(100.0, official=True, spec_violations=["partial runs"])
        assert downgraded is TpcdsComplianceClass.UNOFFICIAL_NONSTANDARD

    def test_subscale_stays_subscale(self):
        assert classify_tpch_run(0.1, official=True, spec_violations=["x"]) is TpchComplianceClass.UNOFFICIAL_SUBSCALE


class TestExecuteRunGate:
    def _config(self, **overrides):
        kwargs = {
            "name": "tpch",
            "display_name": "TPC-H",
            "scale_factor": 100.0,
            "concurrency": 5,
            "official": True,
            "options": {"seed": 1, "benchmark_options": {"tpch_refresh_functions": True}},
        }
        kwargs.update(overrides)
        return BenchmarkConfig(**kwargs)

    def _run(self, config, phases):
        return execute_run(
            config=config,
            benchmark_instance=object(),
            database_config=None,
            system_profile=None,
            platform_config=None,
            output_root=None,
            phases_to_run=phases,
            adapter_factory=lambda **_k: None,
            verbosity=None,
        )

    def test_official_partial_run_refused(self):
        with pytest.raises(ValueError, match="full TPC-H specification"):
            self._run(self._config(), ["load", "power"])

    def test_official_query_subset_refused(self):
        with pytest.raises(ValueError, match="query subsets"):
            self._run(self._config(queries=["1"]), ["load", "power", "throughput"])

    def test_official_missing_seed_refused(self):
        with pytest.raises(ValueError, match="seed"):
            self._run(self._config(options={}), ["load", "power", "throughput"])
