from unittest.mock import MagicMock

import pytest

from benchbox.utils.config_helpers import (
    ConcurrentQueriesSettings,
    ExecutionConfigHelper,
    PowerRunSettings,
    create_sample_execution_config,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_config_manager(overrides: dict | None = None):
    defaults = {
        "execution.power_run.iterations": 4,
        "execution.power_run.warm_up_iterations": 0,
        "execution.power_run.timeout_per_iteration_minutes": 60,
        "execution.power_run.fail_fast": False,
        "execution.power_run.collect_metrics": True,
        "execution.concurrent_queries.enabled": False,
        "execution.concurrent_queries.max_concurrent": 2,
        "execution.concurrent_queries.query_timeout_seconds": 300,
        "execution.concurrent_queries.retry_failed_queries": True,
        "execution.concurrent_queries.max_retries": 3,
        "execution.max_workers": 4,
        "execution.memory_limit_gb": 0,
        "execution.parallel_queries": False,
    }
    if overrides:
        defaults.update(overrides)

    store: dict = {}
    cm = MagicMock()
    cm.get.side_effect = lambda key, default=None: store.get(key, defaults.get(key, default))
    cm.set.side_effect = lambda key, value: store.__setitem__(key, value)
    cm.save_config.return_value = None
    cm.validate_config.return_value = True
    return cm


def _stream_timeout_writes(cm):
    return [
        call for call in cm.set.call_args_list if call.args[0] == "execution.concurrent_queries.stream_timeout_seconds"
    ]


class TestPowerRunSettings:
    def test_from_config_manager_defaults(self):
        cm = _make_config_manager()
        s = PowerRunSettings.from_config_manager(cm)
        assert s.iterations == 4
        assert s.warm_up_iterations == 0
        assert s.timeout_per_iteration_minutes == 60
        assert s.fail_fast is False
        assert s.collect_metrics is True

    def test_to_dict(self):
        s = PowerRunSettings(
            iterations=3, warm_up_iterations=1, timeout_per_iteration_minutes=90, fail_fast=True, collect_metrics=False
        )
        d = s.to_dict()
        assert d == {
            "iterations": 3,
            "warm_up_iterations": 1,
            "timeout_per_iteration_minutes": 90,
            "fail_fast": True,
            "collect_metrics": False,
        }

    def test_apply_to_config_manager(self):
        cm = _make_config_manager()
        s = PowerRunSettings(
            iterations=5, warm_up_iterations=2, timeout_per_iteration_minutes=120, fail_fast=True, collect_metrics=True
        )
        s.apply_to_config_manager(cm)

        assert cm.set.call_count == 5
        cm.set.assert_any_call("execution.power_run.iterations", 5)
        cm.set.assert_any_call("execution.power_run.warm_up_iterations", 2)
        cm.set.assert_any_call("execution.power_run.timeout_per_iteration_minutes", 120)
        cm.set.assert_any_call("execution.power_run.fail_fast", True)
        cm.set.assert_any_call("execution.power_run.collect_metrics", True)


class TestConcurrentQueriesSettings:
    def test_from_config_manager_defaults(self):
        cm = _make_config_manager()
        s = ConcurrentQueriesSettings.from_config_manager(cm)
        assert s.enabled is False
        assert s.max_concurrent == 2
        assert s.query_timeout_seconds == 300
        assert s.stream_timeout_seconds is None
        assert s.cancel_on_timeout is False
        assert s.retry_failed_queries is True
        assert s.max_retries == 3

    def test_to_dict(self):
        s = ConcurrentQueriesSettings(
            enabled=True,
            max_concurrent=8,
            query_timeout_seconds=600,
            stream_timeout_seconds=7200,
            retry_failed_queries=False,
            max_retries=0,
        )
        d = s.to_dict()
        assert d["enabled"] is True
        assert d["max_concurrent"] == 8
        assert d["max_retries"] == 0

    def test_apply_to_config_manager(self):
        cm = _make_config_manager()
        s = ConcurrentQueriesSettings(
            enabled=True,
            max_concurrent=4,
            query_timeout_seconds=600,
            stream_timeout_seconds=7200,
            retry_failed_queries=False,
            max_retries=5,
        )
        s.apply_to_config_manager(cm)
        assert cm.set.call_count == 7
        cm.set.assert_any_call("execution.concurrent_queries.stream_timeout_seconds", 7200)
        cm.set.assert_any_call("execution.concurrent_queries.enabled", True)
        cm.set.assert_any_call("execution.concurrent_queries.max_concurrent", 4)


class TestExecutionConfigHelper:
    def test_init_with_custom_config_manager(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        assert helper.config_manager is cm

    def test_get_power_run_settings(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        s = helper.get_power_run_settings()
        assert isinstance(s, PowerRunSettings)
        assert s.iterations == 4

    def test_get_concurrent_queries_settings(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        s = helper.get_concurrent_queries_settings()
        assert isinstance(s, ConcurrentQueriesSettings)
        assert s.max_concurrent == 2

    def test_update_power_run_settings(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        s = PowerRunSettings(
            iterations=7, warm_up_iterations=1, timeout_per_iteration_minutes=30, fail_fast=False, collect_metrics=True
        )
        helper.update_power_run_settings(s)
        cm.set.assert_any_call("execution.power_run.iterations", 7)

    def test_update_concurrent_queries_settings(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        s = ConcurrentQueriesSettings(
            enabled=True,
            max_concurrent=6,
            query_timeout_seconds=120,
            stream_timeout_seconds=1800,
            retry_failed_queries=True,
            max_retries=2,
        )
        helper.update_concurrent_queries_settings(s)
        cm.set.assert_any_call("execution.concurrent_queries.max_concurrent", 6)

    def test_enable_power_run_iterations(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        helper.enable_power_run_iterations(iterations=5, warm_up_iterations=2)
        cm.set.assert_any_call("execution.power_run.iterations", 5)
        cm.set.assert_any_call("execution.power_run.warm_up_iterations", 2)

    def test_enable_concurrent_queries(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        helper.enable_concurrent_queries(max_concurrent=4)
        cm.set.assert_any_call("execution.concurrent_queries.enabled", True)
        cm.set.assert_any_call("execution.concurrent_queries.max_concurrent", 4)

    def test_disable_concurrent_queries(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        helper.disable_concurrent_queries()
        cm.set.assert_any_call("execution.concurrent_queries.enabled", False)

    def test_optimize_for_system_low_memory(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        helper.optimize_for_system(cpu_cores=4, memory_gb=4.0)
        cm.set.assert_any_call("execution.power_run.timeout_per_iteration_minutes", 120)
        cm.set.assert_any_call("execution.concurrent_queries.query_timeout_seconds", 600)
        assert not _stream_timeout_writes(cm)

    def test_optimize_for_system_high_memory(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        helper.optimize_for_system(cpu_cores=32, memory_gb=64.0)
        cm.set.assert_any_call("execution.power_run.timeout_per_iteration_minutes", 45)
        cm.set.assert_any_call("execution.concurrent_queries.query_timeout_seconds", 180)
        cm.set.assert_any_call("execution.concurrent_queries.max_concurrent", 8)

    def test_optimize_for_system_mid_memory(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        helper.optimize_for_system(cpu_cores=8, memory_gb=12.0)
        cm.set.assert_any_call("execution.concurrent_queries.max_concurrent", 2)

    def test_create_performance_profile_quick(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        profile = helper.create_performance_profile("quick")
        assert profile["name"] == "quick"
        assert profile["power_run"]["iterations"] == 1
        assert profile["power_run"]["warm_up_iterations"] == 0
        assert profile["concurrent_queries"]["enabled"] is False

    def test_create_performance_profile_standard(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        profile = helper.create_performance_profile("standard")
        assert profile["name"] == "standard"
        assert profile["power_run"]["iterations"] == 3
        assert profile["concurrent_queries"]["enabled"] is True

    def test_create_performance_profile_thorough(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        profile = helper.create_performance_profile("thorough")
        assert profile["name"] == "thorough"
        assert profile["power_run"]["iterations"] == 5
        assert profile["concurrent_queries"]["max_concurrent"] == 4

    def test_create_performance_profile_stress(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        profile = helper.create_performance_profile("stress")
        assert profile["name"] == "stress"
        assert profile["power_run"]["iterations"] == 10
        assert profile["concurrent_queries"]["max_concurrent"] == 8

    def test_create_performance_profile_unknown_raises(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        with pytest.raises(ValueError, match="Unknown performance profile"):
            helper.create_performance_profile("nonexistent")

    def test_apply_performance_profile(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        helper.apply_performance_profile("quick")
        cm.set.assert_any_call("execution.power_run.iterations", 1)
        cm.set.assert_any_call("execution.concurrent_queries.enabled", False)

    def test_save_config(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        helper.save_config()
        cm.save_config.assert_called_once()

    def test_validate_execution_config(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        assert helper.validate_execution_config() is True
        cm.validate_config.assert_called_once()

    def test_get_execution_summary(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)
        summary = helper.get_execution_summary()

        assert "power_run" in summary
        assert "concurrent_queries" in summary
        assert "general" in summary

        assert summary["power_run"]["enabled"] is True
        assert summary["power_run"]["total_iterations"] == 4
        assert summary["power_run"]["estimated_duration_minutes"] == 4 * 60

        assert summary["concurrent_queries"]["enabled"] is False
        assert summary["concurrent_queries"]["max_streams"] == 2

        assert summary["general"]["max_workers"] == 4

    def test_get_execution_summary_disabled_power_run(self):
        cm = _make_config_manager(
            overrides={
                "execution.power_run.iterations": 1,
                "execution.power_run.warm_up_iterations": 0,
            }
        )
        helper = ExecutionConfigHelper(config_manager=cm)
        summary = helper.get_execution_summary()
        assert summary["power_run"]["enabled"] is False


class TestCreateSampleExecutionConfig:
    def test_creates_yaml_file(self, tmp_path):
        output = tmp_path / "sample_config.yaml"
        create_sample_execution_config(output)
        assert output.exists()

        import yaml

        data = yaml.safe_load(output.read_text())
        assert "execution" in data
        assert "power_run" in data["execution"]
        assert "concurrent_queries" in data["execution"]
        assert data["execution"]["power_run"]["iterations"] == 3
        assert data["execution"]["concurrent_queries"]["enabled"] is True

    def test_creates_yaml_file_with_string_path(self, tmp_path):
        output = str(tmp_path / "sample_config2.yaml")
        create_sample_execution_config(output)
        from pathlib import Path

        assert Path(output).exists()


class TestStreamTimeoutIsOnlyWrittenWhenSet:
    def test_unset_timeout_is_not_written(self):
        cm = _make_config_manager()
        settings = ConcurrentQueriesSettings.from_config_manager(cm)

        settings.apply_to_config_manager(cm)

        assert settings.stream_timeout_seconds is None
        assert not _stream_timeout_writes(cm)

    def test_explicit_timeout_survives_a_round_trip(self):
        cm = _make_config_manager({"execution.concurrent_queries.stream_timeout_seconds": 5400})

        settings = ConcurrentQueriesSettings.from_config_manager(cm)
        settings.apply_to_config_manager(cm)

        assert settings.stream_timeout_seconds == 5400
        assert [call.args[1] for call in _stream_timeout_writes(cm)] == [5400]

    @pytest.mark.parametrize("profile", ["quick", "standard", "thorough", "stress"])
    def test_profiles_never_write_a_stream_timeout(self, profile):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)

        assert helper.create_performance_profile(profile)["concurrent_queries"]["stream_timeout_seconds"] is None
        helper.apply_performance_profile(profile)

        assert not _stream_timeout_writes(cm)

    @pytest.mark.parametrize("memory_gb", [4.0, 12.0, 64.0])
    def test_system_optimization_never_writes_a_stream_timeout(self, memory_gb):
        cm = _make_config_manager()

        ExecutionConfigHelper(config_manager=cm).optimize_for_system(cpu_cores=8, memory_gb=memory_gb)

        assert not _stream_timeout_writes(cm)

    def test_unrelated_helper_calls_do_not_write_a_stream_timeout(self):
        cm = _make_config_manager()
        helper = ExecutionConfigHelper(config_manager=cm)

        helper.disable_concurrent_queries()
        helper.enable_concurrent_queries(max_concurrent=4)

        assert not _stream_timeout_writes(cm)

    def test_cancel_on_timeout_round_trips(self):
        cm = _make_config_manager({"execution.concurrent_queries.cancel_on_timeout": True})

        settings = ConcurrentQueriesSettings.from_config_manager(cm)
        settings.apply_to_config_manager(cm)

        assert settings.cancel_on_timeout is True
        cm.set.assert_any_call("execution.concurrent_queries.cancel_on_timeout", True)
        assert settings.to_dict()["cancel_on_timeout"] is True

    @pytest.mark.parametrize(("timeout", "expected"), [(None, None), (0, None), (5400, 90)])
    def test_execution_summary_estimates_streams_only_from_a_configured_timeout(self, timeout, expected):
        overrides = {} if timeout is None else {"execution.concurrent_queries.stream_timeout_seconds": timeout}
        summary = ExecutionConfigHelper(config_manager=_make_config_manager(overrides)).get_execution_summary()

        assert summary["concurrent_queries"]["estimated_stream_duration_minutes"] == expected


