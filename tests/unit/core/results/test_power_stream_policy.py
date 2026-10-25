from types import SimpleNamespace

import pytest

from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.power_stream_policy import (
    POWER_STREAM_POLICY_FIXED_STREAM_0,
    POWER_STREAM_POLICY_PER_ITERATION,
    current_power_stream_policy,
    effective_power_stream_policy,
)
from benchbox.core.results.schema import build_result_payload
from benchbox.core.runner.runner import _attach_power_stream_policy
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GEN = {"data_generation_version": 1, "data_generation_hash": "a" * 64}


def _bundle(benchmark_id="tpch", timestamp="2026-10-12T10:00:00", policy=None, **extra):
    benchmark = {"id": benchmark_id, "name": benchmark_id, "scale_factor": 1.0, **GEN, **extra}
    if policy is not None:
        benchmark["power_stream_policy"] = policy
    return {"benchmark": benchmark, "run": {"timestamp": timestamp}}


@pytest.mark.parametrize(
    ("benchmark_id", "expected"),
    [("tpch", POWER_STREAM_POLICY_FIXED_STREAM_0), ("tpcds", POWER_STREAM_POLICY_PER_ITERATION)],
)
def test_tpc_results_carry_policy_through_payload_and_loader(benchmark_id, expected):
    result = _attach_power_stream_policy(make_benchmark_results(benchmark_id=benchmark_id, benchmark_name=benchmark_id))
    assert result.power_stream_policy == expected
    payload = build_result_payload(result)
    assert payload["benchmark"]["power_stream_policy"] == expected
    assert reconstruct_benchmark_results(payload).power_stream_policy == expected


class _FakePowerTest:
    stream_ids: list[int] = []

    def __init__(self, **kwargs):
        type(self).stream_ids.append(kwargs["stream_id"])
        self.config = SimpleNamespace(validation=False)

    def run(self):
        return SimpleNamespace(
            query_results=[],
            success=True,
            queries_successful=1,
            queries_executed=1,
            power_at_size=1.0,
            total_time=0.0,
            errors=[],
        )


def _harness_stream_ids(monkeypatch, module, execute, benchmark_id):
    fake = type("Fake", (_FakePowerTest,), {"stream_ids": []})
    monkeypatch.setattr(module, fake_name[benchmark_id], fake)
    adapter = SimpleNamespace(get_target_dialect=lambda: "standard")
    execute(
        adapter,
        SimpleNamespace(),
        object(),
        {"iterations": 3, "warm_up_iterations": 2, "stream_id": 0},
        make_connection_adapter=lambda *args: SimpleNamespace(),
        console=SimpleNamespace(print=lambda *args, **kwargs: None),
    )
    return fake.stream_ids


fake_name = {"tpch": "TPCHPowerTest", "tpcds": "TPCDSPowerTest"}


def test_marker_matches_what_each_power_harness_does(monkeypatch):
    from benchbox.core.tpcds import power_test as tpcds_power
    from benchbox.core.tpcds.platform_power import execute_tpcds_power_test
    from benchbox.core.tpch import power_test as tpch_power
    from benchbox.core.tpch.platform_power import execute_tpch_power_test

    tpch_ids = _harness_stream_ids(monkeypatch, tpch_power, execute_tpch_power_test, "tpch")
    tpcds_ids = _harness_stream_ids(monkeypatch, tpcds_power, execute_tpcds_power_test, "tpcds")
    assert len(set(tpch_ids)) == 1
    assert len(set(tpcds_ids)) == len(tpcds_ids) > 1
    assert current_power_stream_policy("tpch") == POWER_STREAM_POLICY_FIXED_STREAM_0
    assert current_power_stream_policy("tpcds") == POWER_STREAM_POLICY_PER_ITERATION


def test_compare_distinguishes_tpch_and_tpcds_style_policies():
    fixed = _bundle(policy=POWER_STREAM_POLICY_FIXED_STREAM_0)
    per_iteration = _bundle(policy=POWER_STREAM_POLICY_PER_ITERATION)
    assert ResultExporter._check_run_compatibility(fixed, per_iteration)["status"] == "incompatible"
    assert ResultExporter._check_run_compatibility(per_iteration, per_iteration)["status"] == "compatible"


def test_non_tpc_results_are_not_stamped():
    result = _attach_power_stream_policy(make_benchmark_results(benchmark_id="ssb", benchmark_name="SSB"))
    assert result.power_stream_policy is None
    assert "power_stream_policy" not in build_result_payload(result)["benchmark"]


def test_unmarked_tpch_policy_is_inferred_from_run_date():
    assert effective_power_stream_policy(_bundle(timestamp="2026-10-03T11:39:23")) == (
        POWER_STREAM_POLICY_PER_ITERATION,
        True,
    )
    assert effective_power_stream_policy(_bundle(timestamp="2026-10-12T17:21:55")) == (
        POWER_STREAM_POLICY_FIXED_STREAM_0,
        True,
    )
    assert effective_power_stream_policy(_bundle("tpcds", timestamp="2026-10-03T11:39:23")) == (None, False)


def test_compare_flags_old_and_new_tpch_power_policy():
    old = _bundle(timestamp="2026-10-03T11:39:23")
    new = _bundle(timestamp="2026-10-12T17:21:55", policy=POWER_STREAM_POLICY_FIXED_STREAM_0)
    outcome = ResultExporter._check_run_compatibility(old, new)
    assert outcome["status"] == "incompatible"
    assert outcome["compatible"] is False
    assert "per-iteration-stream" in outcome["warning"]
    assert outcome["power_stream"]["baseline"]["inferred"] is True


def test_compare_accepts_matching_policy():
    new = _bundle(policy=POWER_STREAM_POLICY_FIXED_STREAM_0)
    outcome = ResultExporter._check_run_compatibility(new, _bundle(policy=POWER_STREAM_POLICY_FIXED_STREAM_0))
    assert outcome["status"] == "compatible"
    assert outcome["warning"] is None


def test_compare_reports_unknown_when_one_tpcds_side_unmarked():
    outcome = ResultExporter._check_run_compatibility(
        _bundle("tpcds"), _bundle("tpcds", policy=POWER_STREAM_POLICY_FIXED_STREAM_0)
    )
    assert outcome["status"] == "unknown"
    assert outcome["compatible"] is None
    assert "power-stream policy" in outcome["warning"]


def test_power_stream_mismatch_keeps_generation_warning():
    old = _bundle(timestamp="2026-10-03T11:39:23")
    new = _bundle(timestamp="2026-10-12T17:21:55", data_generation_version=2)
    outcome = ResultExporter._check_run_compatibility(old, new)
    assert outcome["status"] == "incompatible"
    assert "different data generations" in outcome["warning"]
    assert "power-stream policies" in outcome["warning"]
