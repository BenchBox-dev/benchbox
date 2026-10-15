from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.core.tpch.official_benchmark import (
    TPCHOfficialBenchmark,
    TPCHOfficialBenchmarkConfig,
    TPCHOfficialBenchmarkResult,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class FakeConnection:
    connection_string = "duckdb://mem"

    def close(self):
        return None


class FakeBenchmark:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _power(value=100.0, *, success=True, executed=22, successful=22):
    return SimpleNamespace(
        power_at_size=value, success=success, queries_executed=executed, queries_successful=successful
    )


def _throughput(value=64.0, *, success=True, outstanding=()):
    return SimpleNamespace(
        throughput_at_size=value if success else None, success=success, outstanding_stream_ids=list(outstanding)
    )


def _maintenance(success=True):
    return SimpleNamespace(success=success, errors=[] if success else ["rf1 failed"])


@pytest.fixture
def phases(monkeypatch):
    module = "benchbox.core.tpch.official_benchmark"
    monkeypatch.setattr(f"{module}.TPCHBenchmark", FakeBenchmark)
    power = Mock()
    power.return_value.run.return_value = _power()
    adapter = Mock()
    adapter.supports_refresh_functions = True
    adapter._run_routed_throughput.return_value = _throughput()
    throughput = adapter._run_routed_throughput
    maintenance = Mock()
    maintenance.return_value.run_maintenance_test.return_value = _maintenance()
    monkeypatch.setattr(f"{module}.TPCHPowerTest", power)
    monkeypatch.setattr(f"{module}.TPCHMaintenanceTest", maintenance)
    return SimpleNamespace(power=power, throughput=throughput, maintenance=maintenance, adapter=adapter)


def _run(benchmark, phases, **kwargs):
    with pytest.warns(DeprecationWarning, match="TPCHOfficialBenchmark.run_official_benchmark is deprecated"):
        return benchmark.run_official_benchmark(
            connection_factory=FakeConnection, **{"adapter": phases.adapter, **kwargs}
        )


def test_run_official_benchmark_success(phases, tmp_path):
    benchmark = TPCHOfficialBenchmark(scale_factor=1.0, output_dir=tmp_path, verbose=False)

    result = _run(benchmark, phases)

    assert result.success is True
    assert result.power_at_size == 100.0
    assert result.throughput_at_size == 64.0
    assert not hasattr(result, "qphh_at_size")
    assert result.errors == []


def test_scale_factor_reaches_every_phase(phases, tmp_path):
    benchmark = TPCHOfficialBenchmark(scale_factor=0.5, output_dir=tmp_path, num_streams=3)

    _run(benchmark, phases)

    assert phases.power.call_args.kwargs["scale_factor"] == 0.5
    assert phases.throughput.call_args.args[2]["scale_factor"] == 0.5
    assert phases.throughput.call_args.args[2]["num_streams"] == 3
    assert phases.maintenance.call_args.kwargs["scale_factor"] == 0.5


def test_seed_reaches_power_and_throughput(phases, tmp_path):
    config = TPCHOfficialBenchmarkConfig(scale_factor=0.01, seed=7, output_dir=tmp_path)
    benchmark = TPCHOfficialBenchmark(scale_factor=0.01, output_dir=tmp_path)

    _run(benchmark, phases, config=config)

    assert phases.power.call_args.kwargs["seed"] == 7
    assert phases.throughput.call_args.args[2]["seed"] == 7


def test_run_official_benchmark_collects_phase_errors(phases, tmp_path):
    phases.power.return_value.run.side_effect = RuntimeError("power fail")
    phases.throughput.side_effect = RuntimeError("throughput fail")
    phases.maintenance.return_value.run_maintenance_test.side_effect = RuntimeError("maintenance fail")
    benchmark = TPCHOfficialBenchmark(scale_factor=1.0, output_dir=tmp_path, verbose=False)

    result = _run(benchmark, phases)

    assert result.success is False
    assert len(result.errors) == 3
    assert any("Power Test failed" in err for err in result.errors)
    assert not hasattr(result, "qphh_at_size")


@pytest.mark.parametrize(
    "power",
    [
        _power(success=False, executed=22, successful=22),
        _power(success=True, executed=22, successful=21),
        _power(success=True, executed=0, successful=0),
    ],
)
def test_failed_or_partial_power_phase_publishes_no_metric(phases, tmp_path, power):
    phases.power.return_value.run.return_value = power
    benchmark = TPCHOfficialBenchmark(scale_factor=1.0, output_dir=tmp_path)

    result = _run(benchmark, phases)

    assert result.success is False
    assert result.power_at_size == 0.0
    assert not hasattr(result, "qphh_at_size")
    assert any("Power@Size withheld" in err for err in result.errors)


def test_failed_throughput_phase_publishes_no_metric(phases, tmp_path):
    phases.throughput.return_value = _throughput(success=False)
    benchmark = TPCHOfficialBenchmark(scale_factor=1.0, output_dir=tmp_path)

    result = _run(benchmark, phases)

    assert result.success is False
    assert result.throughput_at_size == 0.0
    assert not hasattr(result, "qphh_at_size")
    assert any("Throughput@Size withheld" in err for err in result.errors)


def test_one_stream_is_refused_before_the_throughput_driver_runs(phases, tmp_path):
    benchmark = TPCHOfficialBenchmark(scale_factor=1.0, output_dir=tmp_path, num_streams=1)

    result = _run(benchmark, phases)

    phases.throughput.assert_not_called()
    assert result.success is False
    assert any("at least 2" in err for err in result.errors)


def test_failed_maintenance_marks_the_run_failed(phases, tmp_path):
    phases.maintenance.return_value.run_maintenance_test.return_value = _maintenance(success=False)
    benchmark = TPCHOfficialBenchmark(scale_factor=1.0, output_dir=tmp_path)

    result = _run(benchmark, phases)

    assert result.success is False
    assert any("Maintenance Test failed" in err for err in result.errors)


def test_maintenance_is_refused_while_throughput_work_is_outstanding(phases, tmp_path):
    phases.throughput.return_value = _throughput(success=False, outstanding=[1])
    benchmark = TPCHOfficialBenchmark(scale_factor=1.0, output_dir=tmp_path)

    result = _run(benchmark, phases)

    phases.maintenance.assert_not_called()
    assert any("Maintenance Test refused" in err for err in result.errors)


def test_adapter_route_runs_throughput_with_the_factory_connection_as_the_shared_connection(phases, tmp_path):
    adapter = Mock()
    adapter.supports_refresh_functions = True
    adapter._run_routed_throughput.return_value = _throughput(80.0)
    connection = Mock()
    benchmark = TPCHOfficialBenchmark(scale_factor=0.01, output_dir=tmp_path)

    with pytest.warns(DeprecationWarning):
        result = benchmark.run_official_benchmark(lambda: connection, adapter=adapter)

    adapter._run_routed_throughput.assert_called_once()
    args = adapter._run_routed_throughput.call_args.args
    assert args[1] is connection
    assert args[2]["scale_factor"] == 0.01
    assert args[2]["num_streams"] == 2
    assert result.throughput_at_size == 80.0


def test_power_and_shared_connections_are_closed(phases, tmp_path):
    connections = []

    def factory():
        connection = Mock()
        connections.append(connection)
        return connection

    benchmark = TPCHOfficialBenchmark(scale_factor=0.01, output_dir=tmp_path)

    with pytest.warns(DeprecationWarning):
        benchmark.run_official_benchmark(factory, adapter=phases.adapter)

    assert len(connections) >= 2
    assert connections[0].close.called
    assert connections[1].close.called


def test_missing_adapter_fails_closed_before_any_phase(phases, tmp_path):
    benchmark = TPCHOfficialBenchmark(scale_factor=0.01, output_dir=tmp_path)

    with (
        pytest.warns(DeprecationWarning),
        pytest.raises(TypeError, match=r"adapter=.*benchbox run --phases throughput"),
    ):
        benchmark.run_official_benchmark(FakeConnection)

    phases.power.assert_not_called()


def test_missing_adapter_is_allowed_when_throughput_is_disabled(phases, tmp_path):
    benchmark = TPCHOfficialBenchmark(scale_factor=0.01, output_dir=tmp_path)
    config = TPCHOfficialBenchmarkConfig(scale_factor=0.01, throughput_test_enabled=False, output_dir=tmp_path)

    with pytest.warns(DeprecationWarning):
        result = benchmark.run_official_benchmark(FakeConnection, config)

    assert result.power_at_size == 100.0
    phases.throughput.assert_not_called()


def test_adapter_gate_refusal_withholds_the_throughput_metric(phases, tmp_path):
    adapter = Mock()
    adapter.supports_refresh_functions = True
    adapter._run_routed_throughput.side_effect = RuntimeError("stream_connection_capability=UNSUPPORTED")
    benchmark = TPCHOfficialBenchmark(scale_factor=0.01, output_dir=tmp_path)

    with pytest.warns(DeprecationWarning):
        result = benchmark.run_official_benchmark(FakeConnection, adapter=adapter)

    assert result.success is False
    assert result.throughput_at_size == 0.0
    assert any("UNSUPPORTED" in err for err in result.errors)


def test_maintenance_refuses_unsupported_adapter_before_work(phases, tmp_path):
    benchmark = TPCHOfficialBenchmark(scale_factor=0.01, output_dir=tmp_path)
    config = TPCHOfficialBenchmarkConfig(
        scale_factor=0.01,
        power_test_enabled=False,
        throughput_test_enabled=False,
        maintenance_test_enabled=True,
    )
    phases.adapter.supports_refresh_functions = False

    with pytest.raises(NotImplementedError, match="refresh functions are not supported"):
        _run(benchmark, phases, config=config)

    phases.maintenance.assert_not_called()


def test_facade_builds_the_official_benchmark_from_its_scale_factor(tmp_path):
    from benchbox import TPCH

    facade = TPCH(scale_factor=0.01, output_dir=tmp_path)
    with patch("benchbox.core.tpch.official_benchmark.TPCHOfficialBenchmark") as official_cls:
        official_cls.return_value.run_official_benchmark.return_value = "ran"

        assert facade.run_official_benchmark(FakeConnection) == "ran"

    assert official_cls.call_args.kwargs["scale_factor"] == 0.01
    assert official_cls.call_args.args == ()


def test_validate_compliance_checks():
    benchmark = TPCHOfficialBenchmark.__new__(TPCHOfficialBenchmark)
    result = TPCHOfficialBenchmarkResult(
        config=TPCHOfficialBenchmarkConfig(scale_factor=1.0),
        start_time="s",
        end_time="e",
        total_time=1.0,
        power_test_result=None,
        throughput_test_result=None,
        maintenance_test_result=None,
        power_at_size=10.0,
        throughput_at_size=20.0,
        success=True,
        errors=[],
    )

    assert benchmark.validate_compliance(result) is True
    result.success = False
    assert benchmark.validate_compliance(result) is False
    result.success = True
    result.power_at_size = 0
    assert benchmark.validate_compliance(result) is False


def test_generate_audit_trail_writes_file(monkeypatch, tmp_path):
    monkeypatch.setattr("benchbox.core.tpch.official_benchmark.TPCHBenchmark", FakeBenchmark)
    benchmark = TPCHOfficialBenchmark(scale_factor=1.0, output_dir=tmp_path, verbose=False)

    result = TPCHOfficialBenchmarkResult(
        config=TPCHOfficialBenchmarkConfig(scale_factor=1.0, output_dir=tmp_path, num_streams=2),
        start_time="2026-01-01T00:00:00",
        end_time="2026-01-01T00:01:00",
        total_time=60.0,
        power_test_result=None,
        throughput_test_result=None,
        maintenance_test_result=None,
        power_at_size=10.0,
        throughput_at_size=20.0,
        success=True,
        errors=[],
    )

    path = benchmark.generate_audit_trail(result)

    assert path.exists()
    content = path.read_text()
    assert "TPC-H Official Benchmark Audit Trail" in content
    assert "Throughput@Size" in content
    assert "QphH" not in content
    assert "Power@Size: 10.00" in content


def test_refresh_update_sets_are_sequenced_across_power_throughput_and_maintenance(phases, monkeypatch, tmp_path):
    from benchbox.core.tpch.official_benchmark import TPCHOfficialBenchmarkConfig

    benchmark = TPCHOfficialBenchmark(scale_factor=1.0, output_dir=tmp_path)
    phases.adapter.supports_refresh_functions = True
    generated = []

    def generate(scale_factor, first_set, count, output_dir):
        generated.append((scale_factor, first_set, count))
        return {first_set: SimpleNamespace(set_id=first_set)}

    monkeypatch.setattr("benchbox.core.tpch.official_benchmark.require_refresh_support", lambda adapter: None)
    monkeypatch.setattr("benchbox.core.tpch.official_benchmark.generate_refresh_update_sets", generate)
    config = TPCHOfficialBenchmarkConfig(
        scale_factor=1.0,
        num_streams=2,
        output_dir=tmp_path,
        tpch_refresh_functions=True,
        tpch_refresh_first_set=8,
    )

    result = benchmark.run_official_benchmark(
        connection_factory=FakeConnection,
        config=config,
        adapter=phases.adapter,
        _warn_deprecated=False,
    )

    assert generated == [(1.0, 8, 1)]
    assert phases.power.call_args.kwargs["refresh_update_set"].set_id == 8
    assert phases.throughput.call_args.args[2]["tpch_refresh_first_set"] == 8
    assert phases.maintenance.call_args.kwargs["first_set"] == 11
    assert result.success is True
