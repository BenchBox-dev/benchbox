# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.tpcds.dataframe_queries.parameters import (
    TPCDS_DEFAULT_PARAMS,
    get_parameters,
    set_parameter_overrides as tpcds_set_overrides,
)
from benchbox.core.tpch.dataframe_queries import (
    TPCH_DEFAULT_PARAMS,
    get_tpch_parameters,
    set_parameter_overrides as tpch_set_overrides,
    set_scale_factor as tpch_set_scale_factor,
)
from benchbox.core.tpch.parameter_extractor import (
    clear_cache as tpch_clear_cache,
    extract_tpch_parameters,
    get_tpch_extracted_parameters,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestTPCHParameterOverrides:
    def setup_method(self):
        tpch_set_overrides(None)

    def teardown_method(self):
        tpch_set_overrides(None)

    def test_defaults_returned_when_no_overrides(self):
        params = get_tpch_parameters(1)
        assert params == TPCH_DEFAULT_PARAMS[1]

    def test_override_merges_on_top_of_defaults(self):
        tpch_set_overrides({6: {"start_date": date(1995, 1, 1)}})
        params = get_tpch_parameters(6)

        assert params["start_date"] == date(1995, 1, 1)
        assert params["discount_low"] == 0.05
        assert params["discount_high"] == 0.07
        assert params["quantity_limit"] == 24

    def test_override_does_not_affect_other_queries(self):
        tpch_set_overrides({1: {"cutoff_date": date(1998, 8, 17)}})
        params_q6 = get_tpch_parameters(6)
        assert params_q6 == TPCH_DEFAULT_PARAMS[6]

    def test_clear_overrides(self):
        tpch_set_overrides({1: {"cutoff_date": date(1998, 8, 17)}})
        assert get_tpch_parameters(1)["cutoff_date"] == date(1998, 8, 17)

        tpch_set_overrides(None)
        assert get_tpch_parameters(1)["cutoff_date"] == TPCH_DEFAULT_PARAMS[1]["cutoff_date"]

    def test_all_22_queries_have_defaults(self):
        for qid in range(1, 23):
            assert qid in TPCH_DEFAULT_PARAMS, f"Q{qid} missing from TPCH_DEFAULT_PARAMS"
            assert len(TPCH_DEFAULT_PARAMS[qid]) > 0, f"Q{qid} has empty defaults"

    def test_override_does_not_mutate_defaults(self):
        original_q1 = dict(TPCH_DEFAULT_PARAMS[1])
        tpch_set_overrides({1: {"cutoff_date": date(1998, 8, 17)}})
        assert TPCH_DEFAULT_PARAMS[1] == original_q1

    def test_default_params_symbol(self):
        assert isinstance(TPCH_DEFAULT_PARAMS, dict)
        assert len(TPCH_DEFAULT_PARAMS) == 22


class TestTPCDSParameterOverrides:
    def setup_method(self):
        tpcds_set_overrides(None)

    def teardown_method(self):
        tpcds_set_overrides(None)

    def test_defaults_returned_when_no_overrides(self):
        params = get_parameters(1)
        assert params.get("year") == TPCDS_DEFAULT_PARAMS[1]["year"]
        assert params.get("state") == TPCDS_DEFAULT_PARAMS[1]["state"]

    def test_override_merges_on_top_of_defaults(self):
        tpcds_set_overrides({1: {"year": 2001, "state": "CA"}})
        params = get_parameters(1)

        assert params.get("year") == 2001
        assert params.get("state") == "CA"
        assert params.get("agg_field") == TPCDS_DEFAULT_PARAMS[1]["agg_field"]

    def test_override_does_not_affect_other_queries(self):
        tpcds_set_overrides({1: {"year": 2001}})
        params_q2 = get_parameters(2)
        assert params_q2.get("year") == TPCDS_DEFAULT_PARAMS[2]["year"]

    def test_clear_overrides(self):
        tpcds_set_overrides({1: {"year": 2001}})
        assert get_parameters(1).get("year") == 2001

        tpcds_set_overrides(None)
        assert get_parameters(1).get("year") == TPCDS_DEFAULT_PARAMS[1]["year"]

    def test_override_does_not_mutate_defaults(self):
        original_q1 = dict(TPCDS_DEFAULT_PARAMS[1])
        tpcds_set_overrides({1: {"year": 2001}})
        assert TPCDS_DEFAULT_PARAMS[1] == original_q1


class TestTPCHParameterExtractor:
    def test_extractor_functions_are_callable(self):
        assert callable(extract_tpch_parameters)
        assert callable(get_tpch_extracted_parameters)
        assert callable(tpch_clear_cache)

    def test_cache_clear_does_not_raise(self):
        tpch_clear_cache()


class TestTPCDSParameterExtractor:
    def test_extractor_functions_are_callable(self):
        from benchbox.core.tpcds.parameter_extractor import (
            clear_cache,
            extract_tpcds_parameters,
            get_tpcds_extracted_parameters,
        )

        assert callable(extract_tpcds_parameters)
        assert callable(get_tpcds_extracted_parameters)
        assert callable(clear_cache)

    def test_cache_clear_does_not_raise(self):
        from benchbox.core.tpcds.parameter_extractor import clear_cache

        clear_cache()


class TestQueryFunctionsCentralized:
    def setup_method(self):
        tpch_set_overrides(None)

    def teardown_method(self):
        tpch_set_overrides(None)

    def test_get_tpch_parameters_for_all_queries(self):
        for qid in range(1, 23):
            params = get_tpch_parameters(qid)
            assert isinstance(params, dict), f"Q{qid} returned non-dict: {type(params)}"
            assert len(params) > 0, f"Q{qid} returned empty params"

    def test_tpcds_get_parameters_for_all_queries(self):

        for qid in TPCDS_DEFAULT_PARAMS:
            params = get_parameters(qid)
            assert params.query_id == qid
            assert len(params.params) > 0, f"TPC-DS Q{qid} returned empty params"

    def test_q7_dates_come_from_params(self):
        params = get_tpch_parameters(7)
        assert "start_date" in params, "Q7 missing start_date in TPCH_DEFAULT_PARAMS"
        assert "end_date" in params, "Q7 missing end_date in TPCH_DEFAULT_PARAMS"
        assert params["start_date"] == date(1995, 1, 1)
        assert params["end_date"] == date(1996, 12, 31)

    def test_q8_dates_come_from_params(self):
        params = get_tpch_parameters(8)
        assert "start_date" in params, "Q8 missing start_date in TPCH_DEFAULT_PARAMS"
        assert "end_date" in params, "Q8 missing end_date in TPCH_DEFAULT_PARAMS"
        assert params["start_date"] == date(1995, 1, 1)
        assert params["end_date"] == date(1996, 12, 31)

    def test_q7_date_overrides_propagate(self):
        tpch_set_overrides({7: {"start_date": date(1994, 1, 1), "end_date": date(1995, 12, 31)}})
        params = get_tpch_parameters(7)
        assert params["start_date"] == date(1994, 1, 1)
        assert params["end_date"] == date(1995, 12, 31)
        assert params["nation1"] == "FRANCE"
        assert params["nation2"] == "GERMANY"

    def test_q8_date_overrides_propagate(self):
        tpch_set_overrides({8: {"start_date": date(1994, 1, 1), "end_date": date(1995, 12, 31)}})
        params = get_tpch_parameters(8)
        assert params["start_date"] == date(1994, 1, 1)
        assert params["end_date"] == date(1995, 12, 31)
        assert params["target_nation"] == "BRAZIL"

    def test_tpcds_q96_override_propagates(self):
        tpcds_set_overrides({96: {"hour": 10}})
        params = get_parameters(96)
        assert params.get("hour") == 10

        tpcds_set_overrides(None)
        params = get_parameters(96)
        assert params.get("hour") == 8


class TestSeededOverridePrecedence:
    def setup_method(self):
        tpch_set_overrides(None)
        tpch_set_scale_factor(None)

    def teardown_method(self):
        tpch_set_overrides(None)
        tpch_set_scale_factor(None)

    def test_seeded_override_takes_precedence_over_scaled_default(self):
        tpch_set_scale_factor(0.1)
        tpch_set_overrides({11: {"fraction": 0.00002}})
        assert get_tpch_parameters(11)["fraction"] == pytest.approx(0.00002)


class TestMixinUnseededQ11ScaleFraction:
    def setup_method(self):
        tpch_set_overrides(None)
        tpch_set_scale_factor(None)

    def teardown_method(self):
        tpch_set_overrides(None)
        tpch_set_scale_factor(None)

    @staticmethod
    def _make_probe_adapter():
        from benchbox.platforms.dataframe.benchmark_mixin import BenchmarkExecutionMixin

        class _ScaleProbeAdapter(BenchmarkExecutionMixin):
            def __init__(self):
                self.observed_fraction = None

            def _run_query_iterations(self, *, ctx, benchmark_config, benchmark_instance, monitor, run_options=None):
                self.observed_fraction = get_tpch_parameters(11)["fraction"]
                return []

        return _ScaleProbeAdapter()

    @pytest.mark.parametrize("benchmark_name", ["tpch", "tpch_skew", "tpchavoc"])
    def test_mixin_scopes_q11_scale_for_tpch_family(self, benchmark_name):
        adapter = self._make_probe_adapter()
        config = MagicMock()
        config.name = benchmark_name
        config.scale_factor = 0.1

        adapter._execute_queries_phase(ctx=MagicMock(), benchmark_config=config, benchmark_instance=None, monitor=None)

        assert adapter.observed_fraction == pytest.approx(0.001)
        assert get_tpch_parameters(11)["fraction"] == pytest.approx(0.0001)

    def test_mixin_is_noop_for_non_tpch_family(self):
        adapter = self._make_probe_adapter()
        config = MagicMock()
        config.name = "tpcds"
        config.scale_factor = 0.1

        adapter._execute_queries_phase(ctx=MagicMock(), benchmark_config=config, benchmark_instance=None, monitor=None)

        assert adapter.observed_fraction == pytest.approx(0.0001)

    def test_mixin_resets_scale_on_exception(self):
        from benchbox.platforms.dataframe.benchmark_mixin import BenchmarkExecutionMixin

        class _BoomAdapter(BenchmarkExecutionMixin):
            def _run_query_iterations(self, **kwargs):
                raise RuntimeError("boom")

        config = MagicMock()
        config.name = "tpch"
        config.scale_factor = 0.1

        with pytest.raises(RuntimeError, match="boom"):
            _BoomAdapter()._execute_queries_phase(
                ctx=MagicMock(), benchmark_config=config, benchmark_instance=None, monitor=None
            )

        assert get_tpch_parameters(11)["fraction"] == pytest.approx(0.0001)


class TestSetScaleFactorForBenchmark:
    def setup_method(self):
        tpch_set_scale_factor(None)

    def teardown_method(self):
        tpch_set_scale_factor(None)

    @pytest.mark.parametrize("benchmark_id", ["tpch", "tpch_skew", "tpchavoc"])
    def test_applies_for_tpch_family(self, benchmark_id):
        from benchbox.core.tpch.dataframe_queries import set_scale_factor_for_benchmark

        set_scale_factor_for_benchmark(benchmark_id, 0.1)
        assert get_tpch_parameters(11)["fraction"] == pytest.approx(0.001)

    def test_noop_for_other_benchmarks(self):
        from benchbox.core.tpch.dataframe_queries import set_scale_factor_for_benchmark

        set_scale_factor_for_benchmark("tpcds", 0.1)
        assert get_tpch_parameters(11)["fraction"] == pytest.approx(0.0001)


