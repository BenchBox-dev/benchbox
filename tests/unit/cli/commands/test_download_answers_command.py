from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.download_answers import download_answers

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.parametrize(
    ("benchmark_name", "expected_path"),
    [
        ("all", "/cache/answers"),
        ("tpch", "/cache/answers/tpch"),
        ("tpcds", "/cache/answers/tpcds"),
    ],
)
def test_show_cache_dir_prints_requested_cache_path(
    runner: CliRunner,
    benchmark_name: str,
    expected_path: str,
) -> None:
    with (
        patch("benchbox.core.expected_results.download.get_cache_dir", return_value=Path("/cache/answers")),
        patch("benchbox.core.expected_results.download.get_tpch_cache_dir", return_value=Path("/cache/answers/tpch")),
        patch("benchbox.core.expected_results.download.get_tpcds_cache_dir", return_value=Path("/cache/answers/tpcds")),
    ):
        result = runner.invoke(download_answers, ["--benchmark", benchmark_name, "--show-cache-dir"])

    assert result.exit_code == 0
    assert expected_path in result.output.replace("\\", "/")


def test_disabled_download_short_circuits_before_fetching(runner: CliRunner) -> None:
    with (
        patch("benchbox.core.expected_results.download.is_download_disabled", return_value=True),
        patch("benchbox.core.expected_results.download.get_answers_base_url") as get_url,
    ):
        result = runner.invoke(download_answers, [])

    assert result.exit_code == 0
    assert "downloads are disabled" in result.output.lower()
    get_url.assert_not_called()


def test_download_all_exits_nonzero_when_any_download_fails(runner: CliRunner) -> None:
    with (
        patch("benchbox.core.expected_results.download.is_download_disabled", return_value=False),
        patch("benchbox.core.expected_results.download.get_answers_base_url", return_value="https://example.com"),
        patch(
            "benchbox.core.expected_results.download.download_all_answers",
            return_value={"tpch": Path("/cache/tpch"), "tpcds": None},
        ),
    ):
        result = runner.invoke(download_answers, ["--benchmark", "all", "--force"])

    assert result.exit_code == 1
    assert "Source URL: https://example.com" in result.output
    assert "TPC-H answers cached" in result.output
    assert "TPC-DS answer download failed" in result.output


def test_download_tpch_success_reports_cache_location(runner: CliRunner) -> None:
    with (
        patch("benchbox.core.expected_results.download.is_download_disabled", return_value=False),
        patch("benchbox.core.expected_results.download.get_answers_base_url", return_value="https://example.com"),
        patch("benchbox.core.expected_results.download.download_tpch_answers", return_value=Path("/cache/tpch")),
    ):
        result = runner.invoke(download_answers, ["--benchmark", "tpch"])

    assert result.exit_code == 0
    assert "Downloading TPC-H answer files" in result.output
    assert "/cache/tpch" in result.output.replace("\\", "/")


def test_download_tpch_failure_returns_exit_code_one(runner: CliRunner) -> None:
    with (
        patch("benchbox.core.expected_results.download.is_download_disabled", return_value=False),
        patch("benchbox.core.expected_results.download.get_answers_base_url", return_value="https://example.com"),
        patch("benchbox.core.expected_results.download.download_tpch_answers", return_value=None),
    ):
        result = runner.invoke(download_answers, ["--benchmark", "tpch"])

    assert result.exit_code == 1
    assert "TPC-H answer download failed" in result.output


def test_download_tpcds_success_reports_cache_location(runner: CliRunner) -> None:
    with (
        patch("benchbox.core.expected_results.download.is_download_disabled", return_value=False),
        patch("benchbox.core.expected_results.download.get_answers_base_url", return_value="https://example.com"),
        patch("benchbox.core.expected_results.download.download_tpcds_answers", return_value=Path("/cache/tpcds")),
    ):
        result = runner.invoke(download_answers, ["--benchmark", "tpcds"])

    assert result.exit_code == 0
    assert "Downloading TPC-DS answer files" in result.output
    assert "/cache/tpcds" in result.output.replace("\\", "/")


def test_download_tpcds_failure_returns_exit_code_one(runner: CliRunner) -> None:
    with (
        patch("benchbox.core.expected_results.download.is_download_disabled", return_value=False),
        patch("benchbox.core.expected_results.download.get_answers_base_url", return_value="https://example.com"),
        patch("benchbox.core.expected_results.download.download_tpcds_answers", return_value=None),
    ):
        result = runner.invoke(download_answers, ["--benchmark", "tpcds"])

    assert result.exit_code == 1
    assert "TPC-DS answer download failed" in result.output


