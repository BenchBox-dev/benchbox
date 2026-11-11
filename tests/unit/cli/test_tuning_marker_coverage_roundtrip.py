# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from benchbox.cli.tuning_resolver import TuningMode, TuningSource, resolve_tuning
from benchbox.core.tuning.coverage import BASIC_CONSTRAINTS, TUNED_TEMPLATE, UNTUNED, status_from_log_text

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def mock_console():
    return MagicMock(spec=Console)


def _resolution_log_text(resolution) -> str:

    return "\n".join(resolution.info_messages)


class TestResolverMarkerFeedsCoverageParser:
    def test_notuning_marker_classifies_as_untuned(self, mock_console) -> None:
        resolution = resolve_tuning(
            tuning_arg="notuning",
            platform=None,
            benchmark=None,
            config_manager=MagicMock(get=lambda *_a, **_kw: None),
            console=mock_console,
        )
        assert resolution.mode == TuningMode.NOTUNING

        status = status_from_log_text(_resolution_log_text(resolution))
        assert status == UNTUNED

    def test_fallback_no_template_found_classifies_as_basic_constraints(self, mock_console) -> None:
        mock_config = MagicMock()
        mock_config.get.return_value = None

        with patch.object(Path, "exists", return_value=False):
            resolution = resolve_tuning(
                tuning_arg="tuned",
                platform="nonexistent-platform",
                benchmark="nonexistent-benchmark",
                config_manager=mock_config,
                console=mock_console,
            )
        assert resolution.source == TuningSource.FALLBACK

        status = status_from_log_text(_resolution_log_text(resolution))
        assert status == BASIC_CONSTRAINTS

    def test_fallback_missing_platform_and_benchmark_classifies_as_basic_constraints(self, mock_console) -> None:
        mock_config = MagicMock()
        mock_config.get.return_value = None

        resolution = resolve_tuning(
            tuning_arg="tuned",
            platform=None,
            benchmark=None,
            config_manager=mock_config,
            console=mock_console,
        )
        assert resolution.source == TuningSource.FALLBACK

        status = status_from_log_text(_resolution_log_text(resolution))
        assert status == BASIC_CONSTRAINTS

    def test_auto_discovered_template_classifies_as_tuned_template(self, mock_console, tmp_path) -> None:
        platform_dir = tmp_path / "duckdb"
        platform_dir.mkdir()
        (platform_dir / "tpch_tuned.yaml").write_text("primary_keys:\n  enabled: true\n", encoding="utf-8")

        mock_config = MagicMock()
        mock_config.get.return_value = None

        with patch.dict(os.environ, {"BENCHBOX_TUNING_PATH": str(tmp_path)}):
            resolution = resolve_tuning(
                tuning_arg="tuned",
                platform="duckdb",
                benchmark="tpch",
                config_manager=mock_config,
                console=mock_console,
            )
        assert resolution.source == TuningSource.AUTO_DISCOVERED

        status = status_from_log_text(_resolution_log_text(resolution))
        assert status == TUNED_TEMPLATE

    def test_auto_mode_is_not_misclassified_as_any_coverage_status(self, mock_console) -> None:

        resolution = resolve_tuning(
            tuning_arg="auto",
            platform="duckdb",
            benchmark="tpch",
            config_manager=MagicMock(get=lambda *_a, **_kw: None),
            console=mock_console,
        )
        assert resolution.mode == TuningMode.AUTO

        status = status_from_log_text(_resolution_log_text(resolution))
        assert status is None

    def test_explicit_file_mode_is_not_misclassified_as_any_coverage_status(self, mock_console, tmp_path) -> None:
        custom_file = tmp_path / "custom_tuning.yaml"
        custom_file.write_text("primary_keys:\n  enabled: true\n", encoding="utf-8")

        resolution = resolve_tuning(
            tuning_arg=str(custom_file),
            platform="duckdb",
            benchmark="tpch",
            config_manager=MagicMock(get=lambda *_a, **_kw: None),
            console=mock_console,
        )
        assert resolution.mode == TuningMode.CUSTOM_FILE

        status = status_from_log_text(_resolution_log_text(resolution))
        assert status is None


