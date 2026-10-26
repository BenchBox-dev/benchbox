from __future__ import annotations

import types
from unittest.mock import Mock

import pytest

from benchbox.core.exceptions import ConfigurationError
from benchbox.core.gateway import GREYBEAM_LOAD_ENV, enforce_greybeam_lifecycle
from benchbox.core.runner.runner import LifecyclePhases, _enforce_gateway_lifecycle
from benchbox.platforms.base.execution import TestDriversMixin

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(GREYBEAM_LOAD_ENV, raising=False)


@pytest.mark.parametrize("phase", ["generate", "load"])
def test_inline_phase_raises(phase: str) -> None:
    with pytest.raises(ConfigurationError, match="--allow-greybeam-inline-load"):
        enforce_greybeam_lifecycle("greybeam", generate=phase == "generate", load=phase == "load")


def test_flag_without_env_raises() -> None:
    with pytest.raises(ConfigurationError, match=GREYBEAM_LOAD_ENV):
        enforce_greybeam_lifecycle("greybeam", generate=True, load=True, allow_inline_load=True)


def test_env_without_flag_raises(monkeypatch) -> None:
    monkeypatch.setenv(GREYBEAM_LOAD_ENV, "1")
    with pytest.raises(ConfigurationError, match="--allow-greybeam-inline-load"):
        enforce_greybeam_lifecycle("greybeam", generate=True, load=True)


def test_flag_and_env_allow_with_warning(monkeypatch, caplog) -> None:
    monkeypatch.setenv(GREYBEAM_LOAD_ENV, "1")
    with caplog.at_level("WARNING"):
        enforce_greybeam_lifecycle("greybeam", generate=True, load=True, allow_inline_load=True)
    assert "invalid data" in caplog.text


def test_query_only_phases_pass() -> None:
    enforce_greybeam_lifecycle("greybeam", generate=False, load=False)


@pytest.mark.parametrize("gateway", [None, "native", "espresso", "custom"])
def test_other_gateways_unaffected(gateway) -> None:
    enforce_greybeam_lifecycle(gateway, generate=True, load=True, maintenance_requested=True)


def test_explicit_maintenance_fails_even_with_escape_hatch(monkeypatch) -> None:
    monkeypatch.setenv(GREYBEAM_LOAD_ENV, "1")
    with pytest.raises(ConfigurationError, match="maintenance"):
        enforce_greybeam_lifecycle(
            "greybeam", generate=True, load=True, maintenance_requested=True, allow_inline_load=True
        )
    with pytest.raises(ConfigurationError, match="maintenance"):
        enforce_greybeam_lifecycle("greybeam", generate=False, load=False, maintenance_requested=True)


def _config(**options):
    return types.SimpleNamespace(options=options, test_execution_type=options.pop("test_type", "standard"))


def test_lifecycle_default_phases_rejected() -> None:
    db = types.SimpleNamespace(gateway="greybeam")
    with pytest.raises(ConfigurationError):
        _enforce_gateway_lifecycle(_config(), db, None, LifecyclePhases())


def test_lifecycle_query_phases_accepted() -> None:
    db = types.SimpleNamespace(gateway="greybeam")
    phases = LifecyclePhases(generate=False, load=False, execute=True)
    _enforce_gateway_lifecycle(_config(requested_phases=["power"]), db, None, phases)


def test_lifecycle_explicit_maintenance_rejected() -> None:
    db = types.SimpleNamespace(gateway="greybeam")
    phases = LifecyclePhases(generate=False, load=False, execute=True)
    with pytest.raises(ConfigurationError, match="maintenance"):
        _enforce_gateway_lifecycle(_config(requested_phases=["power", "maintenance"]), db, None, phases)


def test_lifecycle_data_only_and_load_only_rejected() -> None:
    db = types.SimpleNamespace(gateway="greybeam")
    phases = LifecyclePhases(generate=False, load=False, execute=False)
    for test_type in ("data_only", "load_only"):
        with pytest.raises(ConfigurationError):
            _enforce_gateway_lifecycle(_config(test_type=test_type), db, None, phases)


def test_lifecycle_reads_gateway_from_adapter() -> None:
    adapter = types.SimpleNamespace(gateway_endpoint=types.SimpleNamespace(name="greybeam"))
    with pytest.raises(ConfigurationError):
        _enforce_gateway_lifecycle(_config(), None, adapter, LifecyclePhases())


def test_lifecycle_native_unaffected() -> None:
    _enforce_gateway_lifecycle(_config(), types.SimpleNamespace(gateway="native"), None, LifecyclePhases())


class _Driver(TestDriversMixin):
    def __init__(self, gateway: str | None) -> None:
        self.gateway_endpoint = types.SimpleNamespace(name=gateway) if gateway else None
        self.logger = Mock()
        self.power = Mock(return_value=[{"query_id": "q1", "status": "SUCCESS"}])
        self.maintenance = Mock(return_value=[{"query_id": "rf1", "status": "SUCCESS"}])
        self._last_throughput_test_result = None

    def _resolve_benchmark_slug(self, benchmark, run_config):
        return "tpch"


@pytest.fixture
def harness(monkeypatch):
    combined = types.SimpleNamespace(
        label="TPC-H", power_method="power", throughput_method="throughput_m", maintenance_method="maintenance"
    )
    monkeypatch.setattr("benchbox.platforms.base.execution.resolve_combined_harness", lambda _id: combined)
    monkeypatch.setattr(
        "benchbox.platforms.base.execution.check_phase_boundary", lambda _r: types.SimpleNamespace(proceed=True)
    )


def test_implicit_maintenance_is_skipped_loudly(harness) -> None:
    driver = _Driver("greybeam")
    driver.throughput_m = Mock(return_value=[])
    rows = driver._execute_combined_test(object(), object(), {"options": {}})
    driver.maintenance.assert_not_called()
    skipped = [r for r in rows if r.get("maintenance_skipped")]
    assert len(skipped) == 1 and skipped[0]["status"] == "SKIPPED"
    driver.logger.warning.assert_called_once()


def test_explicit_maintenance_fails_fast(harness) -> None:
    driver = _Driver("greybeam")
    with pytest.raises(ConfigurationError):
        driver._execute_combined_test(object(), object(), {"options": {"requested_phases": ["power", "maintenance"]}})
    driver.power.assert_not_called()


def test_native_runs_maintenance(harness) -> None:
    driver = _Driver(None)
    driver.throughput_m = Mock(return_value=[])
    rows = driver._execute_combined_test(object(), object(), {"options": {}})
    driver.maintenance.assert_called_once()
    assert not any(r.get("maintenance_skipped") for r in rows)


def test_standalone_maintenance_fails_fast() -> None:
    with pytest.raises(ConfigurationError):
        _Driver("greybeam")._execute_maintenance_test(object(), object(), {})


def _greybeam_adapter():
    return types.SimpleNamespace(
        platform_name="snowflake",
        supports_refresh_functions=True,
        gateway_endpoint=types.SimpleNamespace(name="greybeam"),
        get_target_dialect=lambda: "snowflake",
    )


def test_refresh_support_check_rejects_greybeam() -> None:
    from benchbox.core.tpch.refresh import refresh_unsupported_reason, require_refresh_support

    adapter = _greybeam_adapter()
    assert "greybeam" in refresh_unsupported_reason(adapter)
    with pytest.raises(ConfigurationError, match="tpch_refresh_functions"):
        require_refresh_support(adapter)
    adapter.gateway_endpoint = None
    assert refresh_unsupported_reason(adapter) is None
    adapter.gateway_endpoint = types.SimpleNamespace(name="espresso")
    assert refresh_unsupported_reason(adapter) is None


def test_power_phase_refresh_fails_before_any_refresh_executes(monkeypatch) -> None:
    from benchbox.core.tpch import power_test as power_test_module, refresh as refresh_module
    from benchbox.core.tpch.platform_power import execute_tpch_power_test

    generated = Mock()
    power = Mock()
    rf = Mock()
    monkeypatch.setattr(refresh_module, "generate_refresh_update_sets", generated)
    monkeypatch.setattr(power_test_module, "TPCHPowerTest", power)
    monkeypatch.setattr(power_test_module, "apply_rf1", rf)
    monkeypatch.setattr(power_test_module, "apply_rf2", rf)
    factory = Mock()

    rows = execute_tpch_power_test(
        _greybeam_adapter(),
        Mock(),
        object(),
        {"scale_factor": 0.01, "tpch_refresh_functions": True, "iterations": 1, "warm_up_iterations": 0},
        make_connection_adapter=Mock(),
        make_refresh_connection_adapter=factory,
        console=types.SimpleNamespace(print=lambda *a, **k: None),
    )
    assert rows[0]["status"] == "FAILED"
    assert "greybeam" in rows[0]["error"]
    generated.assert_not_called()
    power.assert_not_called()
    rf.assert_not_called()
    factory.assert_not_called()


def test_throughput_phase_refresh_fails_before_any_refresh_executes(monkeypatch) -> None:
    from benchbox.core.tpch import refresh as refresh_module, throughput_test as throughput_module
    from benchbox.platforms.base import execution as execution_module

    generated = Mock()
    runner = Mock()
    rf = Mock()
    monkeypatch.setattr(refresh_module, "generate_refresh_update_sets", generated)
    monkeypatch.setattr(throughput_module, "TPCHThroughputTest", runner)
    monkeypatch.setattr(throughput_module, "apply_rf1", rf)
    monkeypatch.setattr(throughput_module, "apply_rf2", rf)
    monkeypatch.setattr(execution_module, "require_throughput_stream_capability", lambda *a, **k: None)

    class Driver(TestDriversMixin):
        platform_name = "snowflake"
        supports_refresh_functions = True
        gateway_endpoint = types.SimpleNamespace(name="greybeam")

    rows = Driver()._execute_tpch_throughput_test(
        Mock(), object(), {"scale_factor": 0.01, "tpch_refresh_functions": True, "streams": 2}
    )
    assert rows[0]["status"] == "FAILED"
    assert "greybeam" in rows[0]["error"]
    generated.assert_not_called()
    runner.assert_not_called()
    rf.assert_not_called()
