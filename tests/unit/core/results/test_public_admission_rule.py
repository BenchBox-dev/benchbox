from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.publishing.admission import publish_admission
from benchbox.core.results.submit_classification import (
    SubmitTerminalState,
    classify_loaded_result,
    result_refused_at_submission,
)
from tests.unit.core.results.test_tpch_refresh_metrics import _result_payload

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _loaded(scale_factor: float, *, stamp: str | None = None, benchmark_name: str = "tpch") -> SimpleNamespace:
    result = SimpleNamespace(
        benchmark_name=benchmark_name,
        platform="platform-a",
        scale_factor=scale_factor,
        total_queries=22,
        duration_seconds=1.0,
        validation_status="PASSED",
        failed_queries=0,
    )
    if stamp is not None:
        result.compliance_class = stamp
    return result


@pytest.mark.parametrize("benchmark_name", ["tpch", "tpcds"])
def test_a_standard_scale_run_without_the_official_flag_is_admitted_publicly(benchmark_name: str) -> None:
    result = _loaded(1.0, benchmark_name=benchmark_name)

    assert publish_admission(result, "community").allowed
    assert classify_loaded_result(result) is SubmitTerminalState.submittable


@pytest.mark.parametrize("benchmark_name", ["tpch", "tpcds"])
@pytest.mark.parametrize("stamp", [None, "official"])
def test_a_sub_sf1_tpc_run_is_refused_publicly_whatever_it_is_stamped(benchmark_name: str, stamp: str | None) -> None:
    result = _loaded(0.01, stamp=stamp, benchmark_name=benchmark_name)

    decision = publish_admission(result, "community")
    assert (decision.allowed, decision.code) == (False, "unofficial_compliance")
    assert classify_loaded_result(result) is SubmitTerminalState.unofficial


@pytest.mark.parametrize("stamp", [None, "official", "unofficial_subscale", "unofficial_nonstandard"])
def test_the_stamp_never_decides_the_public_gate(stamp: str | None) -> None:
    assert result_refused_at_submission(_loaded(1.0, stamp=stamp)) is False
    assert result_refused_at_submission(_loaded(2.0, stamp=stamp)) is False
    assert result_refused_at_submission(_loaded(0.5, stamp=stamp)) is True


def test_a_non_standard_scale_run_is_admitted_unranked() -> None:
    assert publish_admission(_loaded(2.0), "community").allowed


def test_a_gateway_routed_run_is_refused_publicly() -> None:
    result = _loaded(1.0)
    result.platform_info = {"gateway": {"name": "proxy", "routed": True}}

    assert not publish_admission(result, "community").allowed


def test_a_written_power_only_standard_scale_result_is_admitted_publicly() -> None:
    from benchbox.core.results.loader import reconstruct_benchmark_results

    payload = _result_payload(1.0, None, power_refresh=False, evidenced=True)
    loaded = reconstruct_benchmark_results(payload)

    assert "compliance_class" not in payload["benchmark"]
    assert publish_admission(loaded, "community").allowed
