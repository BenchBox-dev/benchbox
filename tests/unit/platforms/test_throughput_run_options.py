from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from benchbox.core.throughput.containment import await_quiescence
from benchbox.core.tpcds.streams import StreamQuery
from benchbox.core.tpcds.throughput_test import _apply_query_subset
from benchbox.platforms.base.connection_wrappers import StreamConnectionCapability
from benchbox.platforms.base.execution import (
    TestDriversMixin,
    _resolve_requested_stream_count,
    _throughput_config_options,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Driver(TestDriversMixin):
    stream_connection_capability = StreamConnectionCapability.SHARED_CURSOR
    platform_name = "stub"
    very_verbose = False

    def __init__(self, gate: threading.Event | None = None) -> None:
        self.calls: list[tuple[Any, bool]] = []
        self.gate = gate
        self._lock = threading.Lock()
        self._last_throughput_test_result = None

    def get_target_dialect(self) -> str | None:
        return None

    def new_stream_connection(self, connection: Any, *, benchmark_type: str | None = None) -> Any:
        return connection

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: Any,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self.calls.append((query_id, validate_row_count))
        if self.gate is not None:
            self.gate.wait(timeout=30)
        return {"query_id": query_id, "status": "SUCCESS", "rows_returned": 1, "execution_time_seconds": 0.0}


def _tpch_benchmark() -> Mock:
    benchmark = Mock()
    benchmark.get_query.return_value = "SELECT 1"
    return benchmark


def _run_tpch(driver: _Driver, **run_config: Any):
    config = {"scale_factor": 1.0, "num_streams": 2, **run_config}
    results = driver._execute_tpch_throughput_test(_tpch_benchmark(), Mock(), config)
    return driver._last_throughput_test_result, results


class TestStreamCountResolution:
    @pytest.mark.parametrize("key", ["num_streams", "streams"])
    @pytest.mark.parametrize("value", [0, 1, -1])
    def test_explicit_count_below_two_is_rejected(self, key, value):
        with pytest.raises(ValueError, match="at least 2"):
            _resolve_requested_stream_count({key: value})

    @pytest.mark.parametrize("value", [0, 1])
    def test_explicit_schema_count_below_two_is_rejected(self, value):
        with pytest.raises(ValueError, match="at least 2"):
            _resolve_requested_stream_count({"concurrent_streams": value})

    @pytest.mark.parametrize("run_config", [{}, {"concurrent_streams": None}])
    def test_unset_count_uses_the_default(self, run_config):
        assert _resolve_requested_stream_count(run_config) == 2

    def test_requested_count_passes_through(self):
        assert _resolve_requested_stream_count({"concurrent_streams": 5}) == 5
        assert _resolve_requested_stream_count({"num_streams": 3, "concurrent_streams": 8}) == 3

    def test_driver_reports_the_rejection_without_running_streams(self):
        driver = _Driver()

        result, rows = _run_tpch(driver, num_streams=1)

        assert result is None
        assert driver.calls == []
        assert rows[0]["query_id"] == "throughput_test_error"
        assert "at least 2" in rows[0]["error"]


class TestValidationMode:
    def test_disabled_turns_row_count_validation_off_for_every_stream(self):
        driver = _Driver()

        result, _rows = _run_tpch(driver, validation_mode="disabled")

        assert result.streams_executed == 2
        assert driver.calls
        assert {validate for _query_id, validate in driver.calls} == {False}

    @pytest.mark.parametrize("mode", [None, "exact", "loose"])
    def test_other_modes_keep_validation_on(self, mode):
        driver = _Driver()

        _run_tpch(driver, validation_mode=mode)

        assert {validate for _query_id, validate in driver.calls} == {True}


class TestQuerySubset:
    def test_tpch_streams_run_only_the_requested_queries(self):
        driver = _Driver()

        result, _rows = _run_tpch(driver, query_subset=["1", "Q6"])

        assert result.streams_executed == 2
        assert {query_id for query_id, _validate in driver.calls} == {1, 6}
        assert len(driver.calls) == 4
        assert all(len(stream.query_results) == 2 for stream in result.stream_results)

    def test_tpch_unknown_query_ids_fail_the_run_clearly(self):
        driver = _Driver()

        result, _rows = _run_tpch(driver, query_subset=["99"])

        assert result.success is False
        assert any("Invalid TPC-H query ids" in error for error in result.errors)
        assert driver.calls == []

    def test_tpch_without_a_subset_runs_all_queries(self):
        driver = _Driver()

        _run_tpch(driver)

        assert len(driver.calls) == 44

    def test_subset_throughput_runs_report_no_throughput_at_size(self):
        subset_result, _rows = _run_tpch(_Driver(), query_subset=["1", "6"])
        full_result, _rows = _run_tpch(_Driver())

        assert subset_result.success is True
        assert subset_result.throughput_at_size is None
        assert full_result.success is True
        assert full_result.throughput_at_size is not None

    def test_tpcds_subset_selects_by_id_and_variant(self):
        queries = [
            StreamQuery(stream_id=0, position=0, query_id=1),
            StreamQuery(stream_id=0, position=1, query_id=14, variant="a"),
            StreamQuery(stream_id=0, position=2, query_id=14, variant="b"),
            StreamQuery(stream_id=0, position=3, query_id=7),
        ]

        assert [q.position for q in _apply_query_subset(queries, ["Q14a", "7"])] == [1, 3]
        assert [q.position for q in _apply_query_subset(queries, ["14"])] == [1, 2]
        assert _apply_query_subset(queries, None) is queries

    def test_tpcds_unknown_query_ids_are_rejected(self):
        queries = [StreamQuery(stream_id=0, position=0, query_id=1)]

        with pytest.raises(ValueError, match="Invalid TPC-DS query ids"):
            _apply_query_subset(queries, ["5"])

    def test_tpcds_driver_hands_the_subset_to_the_throughput_config(self):
        driver = _Driver()
        captured: dict[str, Any] = {}

        def fake_run(self_, config=None):
            captured["config"] = config
            return SimpleNamespace(
                success=False,
                throughput_at_size=None,
                query_throughput=0.0,
                stream_results=[],
                errors=[],
                streams_executed=0,
                streams_successful=0,
                total_time=0.0,
            )

        with patch("benchbox.core.tpcds.throughput_test.TPCDSThroughputTest.run", fake_run):
            driver._execute_tpcds_throughput_test(
                Mock(),
                Mock(),
                {
                    "scale_factor": 1.0,
                    "num_streams": 2,
                    "query_subset": ["1", "14a"],
                    "stream_timeout_seconds": 90,
                    "cancel_on_timeout": True,
                },
            )

        config = captured["config"]
        assert config.query_subset == ["1", "14a"]
        assert config.stream_timeout == 90
        assert config.cancel_on_timeout is True


class TestStreamTimeoutOptions:
    def test_defaults_leave_the_driver_timeouts_in_place(self):
        options = _throughput_config_options({})

        assert "stream_timeout" not in options
        assert options["cancel_on_timeout"] is False

    def test_negative_timeout_is_rejected(self):
        with pytest.raises(ValueError, match="stream_timeout_seconds"):
            _throughput_config_options({"stream_timeout_seconds": -1})

    def test_zero_timeout_disables_the_deadline(self):
        assert _throughput_config_options({"stream_timeout_seconds": 0})["stream_timeout"] == 0

    @pytest.mark.parametrize(("cancel", "max_calls_per_stream"), [(True, 2), (False, 22)])
    def test_run_config_timeout_and_cancellation_reach_the_streams(self, cancel, max_calls_per_stream):
        gate = threading.Event()
        driver = _Driver(gate)

        try:
            result, _rows = _run_tpch(driver, stream_timeout_seconds=1, cancel_on_timeout=cancel)

            assert sorted(result.outstanding_stream_ids) == [1, 2]
            gate.set()
            assert await_quiescence(result, timeout=30.0) is True
            if cancel:
                assert len(driver.calls) <= 2 * max_calls_per_stream
            else:
                assert len(driver.calls) == 2 * max_calls_per_stream
        finally:
            gate.set()


class TestRunConfigPlumbing:
    def test_run_service_carries_stream_timeout_options_into_the_run_config(self, tmp_path):
        from benchbox.core.config import BenchmarkConfig
        from benchbox.core.run_service import resolve_run_config
        from benchbox.utils.verbosity import VerbositySettings

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=1.0,
            options={"stream_timeout_seconds": 120, "cancel_on_timeout": True},
        )

        run_config = resolve_run_config(config, database_path=tmp_path / "db", verbosity=VerbositySettings())

        assert run_config.stream_timeout_seconds == 120
        assert run_config.cancel_on_timeout is True

    def test_run_service_defaults_leave_timeouts_unset(self, tmp_path):
        from benchbox.core.config import BenchmarkConfig
        from benchbox.core.run_service import resolve_run_config
        from benchbox.utils.verbosity import VerbositySettings

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0)

        run_config = resolve_run_config(config, database_path=tmp_path / "db", verbosity=VerbositySettings())

        assert run_config.stream_timeout_seconds is None
        assert run_config.cancel_on_timeout is False


class TestStreamCountEntryPoints:
    @pytest.mark.parametrize("streams", [0, 1, -3])
    def test_official_run_validation_rejects_counts_below_two(self, streams):
        from benchbox.core.run_service import validate_stream_count

        with pytest.raises(ValueError, match="must be >= 2"):
            validate_stream_count(streams, "throughput")

    def test_official_run_validation_accepts_two_and_unset(self):
        from benchbox.core.run_service import validate_stream_count

        validate_stream_count(2, "throughput")
        validate_stream_count(None, "power")

    @pytest.mark.parametrize("streams", ["0", "1"])
    def test_run_command_rejects_counts_below_two(self, streams):
        import click

        from benchbox.cli.commands.run import run

        with pytest.raises(click.BadParameter, match="x>=2"):
            run.make_context("run", ["--streams", streams])


class TestMaintenanceSessions:
    def test_tpch_maintenance_refuses_unsupported_adapter(self):
        driver = _Driver()
        driver.supports_refresh_functions = False
        driver._plan_capture_checkpoint = lambda connection: None

        rows = driver._execute_tpch_maintenance_test(Mock(), Mock(), {"scale_factor": 0.01})

        assert len(rows) == 1
        assert rows[0]["status"] == "FAILED"
        assert "refresh functions are not supported" in rows[0]["error"]

    @pytest.mark.parametrize(
        ("family", "patch_target"),
        [
            ("tpch", "benchbox.core.tpch.maintenance_test.TPCHMaintenanceTest"),
            ("tpcds", "benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest"),
        ],
    )
    @pytest.mark.parametrize(("run_config", "expected"), [({}, "olap"), ({"benchmark_type": "htap"}, "htap")])
    def test_maintenance_sessions_open_through_the_stream_hook_with_benchmark_type(
        self, family, patch_target, run_config, expected
    ):
        driver = _Driver()
        if family == "tpch":
            driver.supports_refresh_functions = True
        seen: list[str | None] = []

        def new_stream_connection(connection, *, benchmark_type=None):
            seen.append(benchmark_type)
            return connection

        driver.new_stream_connection = new_stream_connection
        driver._plan_capture_checkpoint = lambda connection: None
        method = getattr(driver, f"_execute_{family}_maintenance_test")

        with patch(patch_target) as maintenance:
            maintenance.return_value.run.side_effect = RuntimeError("stop after capturing the factory")
            maintenance.return_value.run_maintenance_test.side_effect = RuntimeError("stop")
            method(Mock(), Mock(), {"scale_factor": 1.0, **run_config})
            factory = maintenance.call_args.kwargs["connection_factory"]
            factory()

        assert seen == [expected]


class TestUnsetStreamCount:
    def test_benchmark_config_defaults_to_unset(self):
        from benchbox.core.config import BenchmarkConfig

        assert BenchmarkConfig(name="tpch", display_name="TPC-H").concurrency is None

    def test_saved_single_stream_value_still_loads_for_non_throughput_runs(self):
        from benchbox.core.config import BenchmarkConfig

        config = BenchmarkConfig.model_validate({"name": "tpch", "display_name": "TPC-H", "concurrency": 1})

        assert config.concurrency == 1

    @pytest.mark.parametrize("test_execution_type", ["throughput", "combined"])
    def test_single_stream_is_rejected_when_throughput_will_run(self, test_execution_type):
        from benchbox.core.config import BenchmarkConfig

        with pytest.raises(ValueError, match="at least 2"):
            BenchmarkConfig(name="tpch", display_name="TPC-H", concurrency=1, test_execution_type=test_execution_type)

    def test_combined_run_without_throughput_accepts_one_stream(self):
        from benchbox.core.config import BenchmarkConfig

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            concurrency=1,
            test_execution_type="combined",
            options={"requested_phases": ["power", "maintenance"]},
        )

        assert config.concurrency == 1

    def test_run_config_rejects_single_stream_throughput(self):
        from benchbox.core.config import RunConfig

        with pytest.raises(ValueError, match="at least 2"):
            RunConfig(concurrent_streams=1, test_execution_type="throughput")
        assert RunConfig(test_execution_type="throughput").concurrent_streams is None

    def test_run_service_rejects_single_stream_before_building_an_adapter(self):
        from benchbox.core.config import BenchmarkConfig
        from benchbox.core.run_service import execute_run

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", concurrency=1)
        adapter_factory = Mock()

        with pytest.raises(ValueError, match="at least 2"):
            execute_run(
                config=config,
                benchmark_instance=Mock(),
                database_config=Mock(),
                system_profile=Mock(),
                platform_config=None,
                output_root=None,
                phases_to_run=["load", "throughput"],
                adapter_factory=adapter_factory,
                verbosity=Mock(),
            )
        adapter_factory.assert_not_called()

    def _state(self, **overrides):
        from benchbox.cli.composite_params import CompressionConfig

        state = SimpleNamespace(
            platform="duckdb",
            benchmark="tpch",
            scale=1.0,
            phases="load,power,throughput",
            queries=None,
            tuning="tuned",
            table_mode="native",
            output=None,
            mode=None,
            seed=None,
            comp_config=CompressionConfig(),
            compression=CompressionConfig(),
            iterations=None,
            concurrency=None,
            non_replayable_options=(),
        )
        for key, value in overrides.items():
            setattr(state, key, value)
        return state

    def test_cli_request_rejects_single_stream_with_throughput(self):
        from benchbox.cli.run_resolution import current_run_request

        with pytest.raises(ValueError, match="at least 2"):
            current_run_request(self._state(concurrency=1))

    def test_cli_request_keeps_unset_count_unset(self):
        from benchbox.cli.run_resolution import current_run_request

        assert current_run_request(self._state()).concurrency is None
        assert current_run_request(self._state(concurrency=4)).concurrency == 4
        assert current_run_request(self._state(concurrency=1, phases="load,power")).concurrency == 1

    @pytest.mark.parametrize(
        ("saved", "expected"),
        [({}, None), ({"concurrency": None}, None), ({"concurrency": 1}, None), ({"concurrency": 3}, 3)],
    )
    def test_saved_runs_replay_with_the_unset_count(self, saved, expected):
        from benchbox.cli.run_resolution import RunRequest, _saved_concurrency

        current = RunRequest(
            platform="duckdb",
            benchmark="tpch",
            scale=1.0,
            phases=("load", "throughput"),
            queries=None,
            tuning="tuned",
            table_mode="native",
            output=None,
            mode=None,
            seed=None,
            compression_enabled=False,
            compression_type="zstd",
            compression_level=None,
        )

        assert _saved_concurrency(current, saved, frozenset()) == expected

    def test_wizard_requires_two_streams_and_suggests_at_least_two(self):
        from benchbox.cli.benchmarks import BenchmarkManager

        manager = object.__new__(BenchmarkManager)
        asked: list[int] = []
        answers = iter([1, 0, 3])

        def fake_ask(prompt, default):
            asked.append(default)
            return next(answers)

        with (
            patch("benchbox.cli.benchmarks.Confirm.ask", return_value=True),
            patch("benchbox.cli.benchmarks.IntPrompt.ask", side_effect=fake_ask),
        ):
            result = manager._prompt_concurrency({"supports_streams": True}, {"cpu_cores": 2})

        assert result == 3
        assert asked == [2, 2, 2]

    def test_wizard_leaves_the_count_unset_when_concurrency_is_declined(self):
        from benchbox.cli.benchmarks import BenchmarkManager

        manager = object.__new__(BenchmarkManager)

        with patch("benchbox.cli.benchmarks.Confirm.ask", return_value=False):
            assert manager._prompt_concurrency({"supports_streams": True}, {"cpu_cores": 8}) is None
        assert manager._prompt_concurrency({"supports_streams": False}, {"cpu_cores": 8}) is None


class TestConfigFileStreamOptions:
    def _config_manager(self, tmp_path, body):
        from benchbox.cli.config import ConfigManager

        path = tmp_path / "benchbox.yaml"
        path.write_text(body)
        return ConfigManager(config_path=path)

    def _driver_config(self, run_config):
        driver = _Driver()
        captured: dict[str, Any] = {}

        def fake_run(self_, config=None):
            captured["config"] = config
            raise RuntimeError("stop after capturing the config")

        with patch("benchbox.core.tpch.throughput_test.TPCHThroughputTest.run", fake_run):
            driver._execute_tpch_throughput_test(Mock(), Mock(), run_config)
        return captured["config"]

    def _run_config(self, tmp_path, options):
        from benchbox.core.config import BenchmarkConfig
        from benchbox.core.run_service import resolve_run_config
        from benchbox.utils.verbosity import VerbositySettings

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0, options=options)
        run_config = resolve_run_config(config, database_path=tmp_path / "db", verbosity=VerbositySettings())
        return {key: value for key, value in run_config.__dict__.items() if key != "benchmark"}

    def test_config_file_stream_timeout_and_cancellation_reach_the_throughput_driver(self, tmp_path):
        from benchbox.cli.commands.run import _stream_timeout_config_entries

        manager = self._config_manager(
            tmp_path,
            "execution:\n  concurrent_queries:\n    stream_timeout_seconds: 90\n    cancel_on_timeout: true\n",
        )

        options = _stream_timeout_config_entries(SimpleNamespace(config=manager))
        cfg = self._driver_config(self._run_config(tmp_path, options))

        assert cfg.stream_timeout == 90
        assert cfg.cancel_on_timeout is True

    def test_unset_config_keeps_the_benchmark_default_timeout(self, tmp_path):
        from benchbox.cli.commands.run import _stream_timeout_config_entries
        from benchbox.core.tpch.throughput_test import TPCHThroughputTestConfig

        manager = self._config_manager(tmp_path, "execution:\n  concurrent_queries:\n    enabled: false\n")
        options = _stream_timeout_config_entries(SimpleNamespace(config=manager))
        cfg = self._driver_config(self._run_config(tmp_path, options))

        assert options == {}
        assert cfg.stream_timeout == TPCHThroughputTestConfig().stream_timeout
        assert cfg.cancel_on_timeout is False

    def test_default_configuration_does_not_set_a_stream_timeout(self, tmp_path):
        from benchbox.cli.commands.run import _stream_timeout_config_entries
        from benchbox.cli.config import ConfigManager

        manager = ConfigManager(config_path=tmp_path / "missing.yaml")

        assert _stream_timeout_config_entries(SimpleNamespace(config=manager)) == {}


class TestStreamTimeoutReporting:
    def _printed(self, run_config, runner):
        driver = _Driver()
        console = Mock()
        with patch("benchbox.platforms.base.execution.quiet_console", console):
            runner(driver, run_config)
        return " ".join(str(call.args[0]) for call in console.print.call_args_list)

    def _tpch(self, driver, run_config):
        _run_tpch(driver, **run_config)

    def _tpcds(self, driver, run_config):
        with patch("benchbox.core.tpcds.throughput_test.TPCDSThroughputTest.run", side_effect=RuntimeError("stop")):
            driver._execute_tpcds_throughput_test(Mock(), Mock(), {"scale_factor": 1.0, "num_streams": 2, **run_config})

    def test_benchmark_defaults_are_reported_per_benchmark(self):
        assert "Stream timeout: 3600s (benchmark default)" in self._printed({}, self._tpch)
        assert "Stream timeout: 7200s (benchmark default)" in self._printed({}, self._tpcds)

    def test_config_file_value_is_reported_with_its_source(self):
        printed = self._printed({"stream_timeout_seconds": 1800, "stream_timeout_source": "config file"}, self._tpch)

        assert "Stream timeout: 1800s (config file)" in printed

    def test_run_option_is_reported_as_a_run_option(self):
        assert "Stream timeout: 90s (run option)" in self._printed({"stream_timeout_seconds": 90}, self._tpcds)

    def test_zero_is_reported_as_no_timeout(self):
        assert "Stream timeout: no timeout (run option)" in self._printed({"stream_timeout_seconds": 0}, self._tpch)

    def test_config_file_source_travels_from_the_cli_into_the_run_config(self, tmp_path):
        from benchbox.cli.commands.run import _stream_timeout_config_entries
        from benchbox.core.config import BenchmarkConfig
        from benchbox.core.run_service import resolve_run_config
        from benchbox.utils.verbosity import VerbositySettings

        manager = TestConfigFileStreamOptions()._config_manager(
            tmp_path, "execution:\n  concurrent_queries:\n    stream_timeout_seconds: 1800\n"
        )
        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=1.0,
            options=_stream_timeout_config_entries(SimpleNamespace(config=manager)),
        )

        run_config = resolve_run_config(config, database_path=tmp_path / "db", verbosity=VerbositySettings())

        assert run_config.stream_timeout_seconds == 1800
        assert run_config.stream_timeout_source == "config file"


class TestConfigFileValidationAndCompatibility:
    def test_zero_stream_timeout_is_a_valid_config_value(self, tmp_path):
        from benchbox.cli.config import ConfigManager

        path = tmp_path / "benchbox.yaml"
        path.write_text("execution:\n  concurrent_queries:\n    stream_timeout_seconds: 0\n")

        assert ConfigManager(config_path=path).validate_config() is True

    def test_negative_stream_timeout_is_still_invalid(self, tmp_path):
        from benchbox.cli.config import ConfigManager

        path = tmp_path / "benchbox.yaml"
        path.write_text("execution:\n  concurrent_queries:\n    stream_timeout_seconds: -5\n")

        assert ConfigManager(config_path=path).validate_config() is False

    def test_official_run_minimum_applies_only_when_throughput_runs(self):
        from benchbox.core.run_service import validate_stream_count

        validate_stream_count(1, "power")
        validate_stream_count(0, "load,power")
        with pytest.raises(ValueError, match="must be >= 2"):
            validate_stream_count(1, "power,throughput")

    def test_legacy_saved_single_stream_is_noted_when_treated_as_unset(self):
        from benchbox.cli.run_resolution import RunRequest, merge_quick_restart_request

        current = RunRequest(
            platform="duckdb",
            benchmark="tpch",
            scale=1.0,
            phases=("load", "power"),
            queries=None,
            tuning="tuned",
            table_mode="native",
            output=None,
            mode=None,
            seed=None,
            compression_enabled=False,
            compression_type="zstd",
            compression_level=None,
        )
        saved = {"database": "duckdb", "benchmark": "tpch", "scale": 1.0, "concurrency": 1}

        merged = merge_quick_restart_request(current, saved, explicit_fields=frozenset())

        assert merged.concurrency is None
        assert "saved run recorded 1 stream, the former default; treated as not set" in merged.compatibility_notes
