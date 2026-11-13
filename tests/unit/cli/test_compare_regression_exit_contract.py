from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli
from benchbox.cli.commands.compare import (
    _check_regression,
    _check_regression_threshold,
    _parse_duration_ms,
    _parse_threshold,
    _validate_min_regression_delta,
    _validate_regression_threshold,
)
from benchbox.cli.exit_codes import ExitCode
from tests.fixtures.result_dict_fixtures import make_v2_result_dict

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _comparison(query_change: float | None = None, metric_change: float | None = None) -> dict[str, Any]:
    comparison: dict[str, Any] = {"query_comparisons": [], "performance_changes": {}}
    if query_change is not None:
        comparison["query_comparisons"].append({"query_id": "1", "change_percent": query_change})
    if metric_change is not None:
        comparison["performance_changes"]["geometric_mean"] = {"change_percent": metric_change}
    return comparison


class TestRegressionExitCode:
    def test_regression_exits_nonzero(self):
        with pytest.raises(SystemExit) as exit_info:
            _check_regression_threshold(_comparison(query_change=25.0), 0.10)

        assert exit_info.value.code == ExitCode.REGRESSION

    def test_no_regression_does_not_exit(self):
        assert _check_regression_threshold(_comparison(query_change=2.0), 0.10) is None

    def test_improvement_does_not_exit(self):
        assert _check_regression_threshold(_comparison(query_change=-40.0), 0.10) is None

    def test_omitted_threshold_never_exits(self):
        assert _check_regression_threshold(_comparison(query_change=500.0), None) is None

    def test_metric_level_regression_also_exits(self):
        with pytest.raises(SystemExit) as exit_info:
            _check_regression_threshold(_comparison(metric_change=25.0), 0.10)

        assert exit_info.value.code == ExitCode.REGRESSION

    def test_exactly_at_the_threshold_does_not_exit(self):
        assert _check_regression_threshold(_comparison(query_change=10.0), 0.10) is None

    def test_just_over_the_threshold_exits(self):
        with pytest.raises(SystemExit) as exit_info:
            _check_regression_threshold(_comparison(query_change=10.5), 0.10)

        assert exit_info.value.code == ExitCode.REGRESSION


class TestThresholdParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("10%", 0.10), ("5.5%", 0.055), ("0.1", 0.1), ("0.05", 0.05), (" 10% ", 0.10)],
    )
    def test_accepted_threshold_spellings(self, raw: str, expected: float):
        assert _parse_threshold(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", ["abc", "%", "10%%", ""])
    def test_rejected_threshold_spellings(self, raw: str):
        assert _parse_threshold(raw) is None

    def test_invalid_threshold_exits_one(self):
        with pytest.raises(SystemExit) as exit_info:
            _validate_regression_threshold("not-a-threshold")

        assert exit_info.value.code == 1

    def test_absent_flag_yields_no_threshold(self):
        assert _validate_regression_threshold(None) is None
        assert _validate_regression_threshold("") is None


class TestFailOnRegressionUsesTheSharedPolicy:
    @pytest.mark.parametrize("change", [-50.0, -10.0, 0.0, 9.9, 10.0, 10.1, 50.0, 150.0])
    def test_predicate_matches_core(self, change: float):
        from benchbox.core.results.regression_policy import is_regression

        assert _check_regression(_comparison(query_change=change), 0.10) is is_regression(change, 10.0)

    def test_caller_threshold_overrides_the_default(self):
        assert _check_regression(_comparison(query_change=7.0), 0.05) is True
        assert _check_regression(_comparison(query_change=7.0), 0.10) is False

    def test_non_dict_metric_entries_are_skipped(self):
        comparison = {"query_comparisons": [], "performance_changes": {"geometric_mean": "not-a-dict"}}

        assert _check_regression(comparison, 0.10) is False


def _timed_query(baseline_ms: float, current_ms: float) -> dict[str, Any]:
    return {
        "query_id": "8",
        "baseline_time_ms": baseline_ms,
        "current_time_ms": current_ms,
        "change_percent": (current_ms - baseline_ms) / baseline_ms * 100.0,
    }


def _timed_comparison(*queries: dict[str, Any]) -> dict[str, Any]:
    return {"query_comparisons": list(queries), "performance_changes": {}}


class TestMinRegressionDelta:
    def test_exceeding_both_limits_is_a_regression(self):
        comparison = _timed_comparison(_timed_query(100.0, 120.0))

        assert _check_regression(comparison, 0.10, 5.0) is True

    def test_exceeding_only_the_percentage_is_not_a_regression(self):
        comparison = _timed_comparison(_timed_query(9.0, 11.6))

        assert _check_regression(comparison, 0.10, 5.0) is False
        assert _check_regression(comparison, 0.10) is True

    def test_exceeding_only_the_delta_is_not_a_regression(self):
        comparison = _timed_comparison(_timed_query(1000.0, 1060.0))

        assert _check_regression(comparison, 0.10, 5.0) is False

    def test_slowdown_equal_to_the_floor_is_not_a_regression(self):
        comparison = _timed_comparison(_timed_query(10.0, 15.0))

        assert _check_regression(comparison, 0.10, 5.0) is False

    def test_decimal_slowdown_equal_to_the_floor_is_not_a_regression(self):
        comparison = _timed_comparison(_timed_query(9.6, 16.6))

        assert _check_regression(comparison, 0.10, 7.0) is False

    def test_decimal_slowdown_just_above_the_floor_is_a_regression(self):
        comparison = _timed_comparison(_timed_query(9.6, 16.7))

        assert _check_regression(comparison, 0.10, 7.0) is True

    def test_large_slowdown_on_a_long_query_still_fails(self):
        comparison = _timed_comparison(_timed_query(5000.0, 5600.0))

        with pytest.raises(SystemExit) as exit_info:
            _check_regression_threshold(comparison, 0.10, 5.0)

        assert exit_info.value.code == ExitCode.REGRESSION

    def test_floor_does_not_apply_to_aggregate_metrics(self):
        comparison = {
            "query_comparisons": [],
            "performance_changes": {"total_execution_time": {"change_percent": 25.0}},
        }

        assert _check_regression(comparison, 0.10, 1_000_000.0) is True

    def test_one_query_over_both_limits_fails_among_jittery_ones(self):
        comparison = _timed_comparison(_timed_query(9.0, 11.6), _timed_query(10.0, 11.9), _timed_query(80.0, 100.0))

        assert _check_regression(comparison, 0.10, 5.0) is True

    def test_query_without_timings_falls_back_to_the_percentage(self):
        assert _check_regression(_comparison(query_change=25.0), 0.10, 5.0) is True

    def test_floor_is_reported_in_the_verdict(self, capsys: pytest.CaptureFixture[str]):
        _check_regression_threshold(_timed_comparison(_timed_query(9.0, 11.6)), 0.10, 5.0)

        assert "minimum query slowdown: 5 ms" in capsys.readouterr().out


def _timed_metric(baseline_s: float, current_s: float) -> dict[str, Any]:
    return {
        "baseline": baseline_s,
        "current": current_s,
        "change_percent": (current_s - baseline_s) / baseline_s * 100.0,
    }


def _metric_comparison(**metrics: dict[str, Any]) -> dict[str, Any]:
    return {"query_comparisons": [], "performance_changes": dict(metrics)}


class TestMinAggregateRegressionDelta:
    def test_exceeding_both_limits_is_a_regression(self):
        comparison = _metric_comparison(total_execution_time=_timed_metric(0.547, 0.700))

        assert _check_regression(comparison, 0.10, None, 82.0) is True

    def test_exceeding_only_the_percentage_is_not_a_regression(self):
        comparison = _metric_comparison(total_execution_time=_timed_metric(0.547, 0.605))

        assert _check_regression(comparison, 0.10, None, 82.0) is False
        assert _check_regression(comparison, 0.10) is True

    def test_exceeding_only_the_floor_is_not_a_regression(self):
        comparison = _metric_comparison(total_execution_time=_timed_metric(1.000, 1.060))

        assert _check_regression(comparison, 0.10, None, 50.0) is False

    def test_slowdown_equal_to_the_floor_is_not_a_regression(self):
        comparison = _metric_comparison(total_execution_time=_timed_metric(1.0, 2.0))

        assert _check_regression(comparison, 0.10, None, 1000.0) is False

    def test_decimal_slowdown_equal_to_the_floor_is_not_a_regression(self):
        comparison = _metric_comparison(total_execution_time=_timed_metric(0.600, 0.682))

        assert _check_regression(comparison, 0.10, None, 82.0) is False

    def test_decimal_slowdown_just_above_the_floor_is_a_regression(self):
        comparison = _metric_comparison(total_execution_time=_timed_metric(0.600, 0.683))

        assert _check_regression(comparison, 0.10, None, 82.0) is True

    def test_metric_without_timings_falls_back_to_the_percentage(self):
        comparison = _metric_comparison(total_execution_time={"change_percent": 25.0})

        assert _check_regression(comparison, 0.10, None, 82.0) is True

    def test_large_slowdown_still_fails(self):
        comparison = _metric_comparison(total_execution_time=_timed_metric(0.547, 1.200))

        with pytest.raises(SystemExit) as exit_info:
            _check_regression_threshold(comparison, 0.10, None, 82.0)

        assert exit_info.value.code == ExitCode.REGRESSION

    def test_floor_is_reported_in_the_verdict(self, capsys: pytest.CaptureFixture[str]):
        _check_regression_threshold(
            _metric_comparison(total_execution_time=_timed_metric(0.547, 0.605)), 0.10, None, 82.0
        )

        assert "minimum aggregate slowdown: 82" in capsys.readouterr().out


class TestDurationParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("5ms", 5.0), ("0.005s", 5.0), (" 6MS ", 6.0), ("2.5ms", 2.5), ("1s", 1000.0), ("0ms", 0.0)],
    )
    def test_accepted_spellings(self, raw: str, expected: float):
        assert _parse_duration_ms(raw) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "raw",
        [
            "5",
            "ms",
            "s",
            "-1ms",
            "+5ms",
            "fast",
            "5m",
            "nanms",
            "infs",
            "1_0ms",
            "1e3ms",
            "٥ms",
            "5 ms",
            "9" * 400 + "s",
        ],
    )
    def test_rejected_spellings(self, raw: str):
        assert _parse_duration_ms(raw) is None

    def test_invalid_duration_exits_one(self):
        with pytest.raises(SystemExit) as exit_info:
            _validate_min_regression_delta("soon")

        assert exit_info.value.code == 1

    def test_absent_option_yields_no_floor(self):
        assert _validate_min_regression_delta(None) is None

    def test_empty_value_exits_one(self):
        with pytest.raises(SystemExit) as exit_info:
            _validate_min_regression_delta("")

        assert exit_info.value.code == 1


def _write_result(path: Path, query_ms: dict[str, float]) -> str:
    queries = [
        {"id": query_id, "ms": ms, "rows": 1, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"}
        for query_id, ms in query_ms.items()
    ]
    path.write_text(json.dumps(make_v2_result_dict(queries=queries, total_ms=1000)), encoding="utf-8")
    return str(path)


class TestMinRegressionDeltaCommand:
    @pytest.fixture
    def placeholder_files(self, tmp_path: Path) -> tuple[str, str]:
        baseline = tmp_path / "baseline.json"
        current = tmp_path / "current.json"
        baseline.write_text("{}")
        current.write_text("{}")
        return str(baseline), str(current)

    def _compare(self, tmp_path: Path, baseline_ms: dict[str, float], current_ms: dict[str, float], *options: str):
        baseline = _write_result(tmp_path / "baseline.json", baseline_ms)
        current = _write_result(tmp_path / "current.json", current_ms)
        return CliRunner().invoke(cli, ["compare", baseline, current, "--fail-on-regression", "10%", *options])

    def test_without_fail_on_regression_is_a_usage_error(self, placeholder_files: tuple[str, str]):
        result = CliRunner().invoke(cli, ["compare", *placeholder_files, "--min-regression-delta", "5ms"])

        assert result.exit_code == 2
        assert "--min-regression-delta requires --fail-on-regression" in result.output

    def test_empty_value_is_rejected_instead_of_disabling_the_floor(self, placeholder_files: tuple[str, str]):
        result = CliRunner().invoke(
            cli, ["compare", *placeholder_files, "--fail-on-regression", "10%", "--min-regression-delta", ""]
        )

        assert result.exit_code == 1
        assert "Invalid duration" in result.output

    def test_platform_runs_reject_the_option(self):
        result = CliRunner().invoke(
            cli,
            ["compare", "-p", "duckdb", "-p", "sqlite", "--fail-on-regression", "10%", "--min-regression-delta", "5ms"],
        )

        assert result.exit_code == 2
        assert "result file comparison" in result.output

    def test_list_platforms_ignores_the_option_check(self):
        result = CliRunner().invoke(cli, ["compare", "--list-platforms", "--min-regression-delta", "5ms"])

        assert result.exit_code == 0

    def test_help_documents_the_option(self):
        result = CliRunner().invoke(cli, ["compare", "--help"])

        assert "--min-regression-delta" in result.output
        assert "--fail-on-regression" in result.output

    def test_jitter_on_a_short_query_passes_with_the_floor(self, tmp_path: Path):
        result = self._compare(
            tmp_path, {"8": 9.0, "20": 10.0}, {"8": 11.6, "20": 11.9}, "--min-regression-delta", "5ms"
        )

        assert result.exit_code == 0, result.output
        assert "No performance regression" in result.output

    def test_jitter_on_a_short_query_fails_without_the_floor(self, tmp_path: Path):
        result = self._compare(tmp_path, {"8": 9.0, "20": 10.0}, {"8": 11.6, "20": 11.9})

        assert result.exit_code == ExitCode.REGRESSION

    def test_large_slowdown_on_a_long_query_fails_with_the_floor(self, tmp_path: Path):
        result = self._compare(
            tmp_path, {"8": 9.0, "9": 5000.0}, {"8": 9.0, "9": 5600.0}, "--min-regression-delta", "5ms"
        )

        assert result.exit_code == ExitCode.REGRESSION
        assert "Performance regression detected" in result.output

    def test_seconds_spelling_is_equivalent(self, tmp_path: Path):
        result = self._compare(tmp_path, {"8": 9.0}, {"8": 11.6}, "--min-regression-delta", "0.005s")

        assert result.exit_code == 0, result.output

    def test_interactive_file_comparison_forwards_the_floor(self):
        from benchbox.cli.commands import compare as compare_module

        with (
            patch.object(compare_module, "_direct_file_selection", return_value=(Path("a.json"), Path("b.json"))),
            patch.object(compare_module.Prompt, "ask", return_value="2"),
            patch.object(compare_module.Confirm, "ask", return_value=True),
            patch.object(compare_module, "_run_file_comparison") as run_file_comparison,
        ):
            compare_module._interactive_file_comparison(
                output_format="text",
                output_file=None,
                fail_on_regression="10%",
                show_all_queries=False,
                min_regression_delta="5ms",
            )

        assert run_file_comparison.call_args.kwargs["min_regression_delta"] == "5ms"

    def test_interactive_platform_comparison_rejects_the_floor(self):
        from benchbox.cli.commands import compare as compare_module

        with (
            patch.object(compare_module.Prompt, "ask", return_value="1"),
            patch.object(compare_module, "_interactive_platform_comparison") as platform_comparison,
            pytest.raises(compare_module.click.UsageError, match="result file comparison"),
        ):
            compare_module._run_interactive_wizard(
                output_format="text",
                output_file=None,
                generate_charts=False,
                theme="light",
                fail_on_regression="10%",
                show_all_queries=False,
                min_regression_delta="5ms",
            )

        platform_comparison.assert_not_called()
