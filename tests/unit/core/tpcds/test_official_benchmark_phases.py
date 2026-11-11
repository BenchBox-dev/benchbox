from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.core.tpcds.official_benchmark import TPCDSOfficialBenchmark, TPCDSOfficialBenchmarkConfig

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _power(*, value=100.0, success=True, executed=99, successful=99):
    return SimpleNamespace(
        power_at_size=value,
        success=success,
        queries_executed=executed,
        queries_successful=successful,
        config=SimpleNamespace(scale_factor=0.01),
    )


def _throughput(value=64.0):
    return SimpleNamespace(throughput_at_size=value, success=True, outstanding_stream_ids=[])


@pytest.fixture
def phases():
    with (
        patch("benchbox.core.tpcds.power_test.TPCDSPowerTest") as power,
        patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest") as maintenance,
    ):
        power.return_value.run.return_value = _power()
        adapter = Mock()
        adapter._run_routed_throughput.return_value = _throughput()
        maintenance.return_value.run.return_value = {"success": True}
        yield SimpleNamespace(
            power=power, throughput=adapter._run_routed_throughput, maintenance=maintenance, adapter=adapter
        )


def _official(tmp_path, scale_factor=0.01):
    return TPCDSOfficialBenchmark(scale_factor=scale_factor, output_dir=tmp_path, verbose=False)


def _run(official, phases, factory=None, **kwargs):
    with pytest.warns(DeprecationWarning, match="TPCDSOfficialBenchmark.run_official_benchmark is deprecated"):
        return official.run_official_benchmark(factory or (lambda: Mock()), adapter=phases.adapter, **kwargs)


class TestScaleFactor:
    def test_power_and_throughput_receive_the_configured_scale_factor(self, tmp_path, phases):
        official = _official(tmp_path, scale_factor=0.01)

        result = _run(official, phases)

        assert phases.power.call_args.kwargs["scale_factor"] == 0.01
        assert phases.throughput.call_args.args[2]["scale_factor"] == 0.01
        assert result.success is True

    def test_explicit_config_scale_factor_wins(self, tmp_path, phases):
        official = _official(tmp_path, scale_factor=1.0)
        config = TPCDSOfficialBenchmarkConfig(scale_factor=10.0, num_streams=3, output_dir=tmp_path)

        _run(official, phases, config=config)

        assert phases.power.call_args.kwargs["scale_factor"] == 10.0
        assert phases.throughput.call_args.args[2]["scale_factor"] == 10.0
        assert phases.throughput.call_args.args[2]["num_streams"] == 3


class TestPowerPhaseOutcome:
    @pytest.mark.parametrize(
        "power",
        [
            _power(success=True, executed=99, successful=70),
            _power(success=True, executed=99, successful=98),
            _power(success=False, executed=99, successful=99),
        ],
    )
    def test_partial_or_failed_power_phase_publishes_no_metric(self, tmp_path, phases, power):
        phases.power.return_value.run.return_value = power
        official = _official(tmp_path)

        result = _run(official, phases)

        assert result.success is False
        assert result.power_at_size == 0.0
        assert not hasattr(result, "qphds_at_size")
        assert any("Power@Size withheld" in error for error in result.errors)

    def test_complete_power_phase_publishes_the_metric(self, tmp_path, phases):
        official = _official(tmp_path)

        result = _run(official, phases)

        assert result.power_at_size == 100.0
        assert not hasattr(result, "qphds_at_size")
        assert result.errors == []


class TestStreamMinimum:
    def test_one_stream_is_refused_before_the_throughput_driver_is_built(self, tmp_path, phases):
        official = _official(tmp_path)
        config = TPCDSOfficialBenchmarkConfig(scale_factor=0.01, num_streams=1, output_dir=tmp_path)

        result = _run(official, phases, config=config)

        phases.throughput.assert_not_called()
        assert result.success is False
        assert result.throughput_at_size == 0.0
        assert any("at least 2" in error for error in result.errors)


class TestAdapterGate:
    def test_missing_adapter_fails_closed_before_any_phase(self, tmp_path, phases):
        official = _official(tmp_path)

        with (
            pytest.warns(DeprecationWarning),
            pytest.raises(TypeError, match=r"adapter=.*benchbox run --phases throughput"),
        ):
            official.run_official_benchmark(lambda: Mock())

        phases.power.assert_not_called()

    def test_missing_adapter_is_allowed_when_throughput_is_disabled(self, tmp_path, phases):
        official = _official(tmp_path)
        config = TPCDSOfficialBenchmarkConfig(
            scale_factor=0.01, throughput_test_enabled=False, maintenance_test_enabled=False, output_dir=tmp_path
        )

        with pytest.warns(DeprecationWarning):
            result = official.run_official_benchmark(lambda: Mock(), config)

        assert result.power_at_size == 100.0

    def test_gate_refusal_withholds_the_throughput_metric(self, tmp_path, phases):
        phases.throughput.side_effect = RuntimeError("stream_connection_capability=UNSUPPORTED")

        result = _run(_official(tmp_path), phases)

        assert result.success is False
        assert result.throughput_at_size == 0.0
        assert any("UNSUPPORTED" in error for error in result.errors)

    def test_shared_connection_is_passed_to_the_adapter_and_closed(self, tmp_path, phases):
        connections = []

        def factory():
            connection = Mock()
            connections.append(connection)
            return connection

        _run(_official(tmp_path), phases, factory)

        assert phases.throughput.call_args.args[1] in connections
        assert phases.throughput.call_args.args[1].close.called


class TestMaintenanceSeed:
    def test_configured_seed_reaches_the_maintenance_run(self, tmp_path, phases):
        official = TPCDSOfficialBenchmark(scale_factor=0.01, output_dir=tmp_path, verbose=False, seed=777)

        _run(official, phases)

        assert phases.maintenance.return_value.run.call_args.args[0].seed == 777

    def test_unset_seed_leaves_the_kit_default(self, tmp_path, phases):
        _run(_official(tmp_path), phases)

        assert phases.maintenance.return_value.run.call_args.args[0].seed is None


