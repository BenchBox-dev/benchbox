# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
import yaml
from click.testing import CliRunner

from benchbox.cli.main import cli
from benchbox.cli.tuning_resolver import TuningMode, TuningResolution, TuningSource
from benchbox.core.tuning import modes as tuning_modes

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

__import__("benchbox.cli.commands.run")
_run_module = sys.modules["benchbox.cli.commands.run"]

VOCAB_PATH = Path(__file__).resolve().parent / "fixtures" / "tuning_mode_vocabulary.yaml"

EXPECTED_MODES = ["tuned", "tuned-fallback", "notuning", "auto", "custom"]
EXPECTED_NOT_RECORDED_SENTINEL = "not-recorded"


def _load_vocabulary_artifact() -> dict[str, Any]:
    return yaml.safe_load(VOCAB_PATH.read_text(encoding="utf-8"))


class TestSharedVocabularyArtifactMatchesADR2:
    def test_artifact_exists(self) -> None:
        assert VOCAB_PATH.exists(), (
            f"Shared vocabulary artifact missing at {VOCAB_PATH}. Keep it out of any 'data/' directory -- "
            "the repo .gitignore blanket-ignores those."
        )

    def test_modes_match_adr2_decided_set(self) -> None:
        spec = _load_vocabulary_artifact()
        assert spec["modes"] == EXPECTED_MODES

    def test_not_recorded_sentinel_matches_adr2(self) -> None:
        spec = _load_vocabulary_artifact()
        assert spec["not_recorded_sentinel"] == EXPECTED_NOT_RECORDED_SENTINEL

    def test_raw_file_paths_are_not_part_of_the_vocabulary(self) -> None:
        spec = _load_vocabulary_artifact()
        for mode in spec["modes"]:
            assert "/" not in mode
            assert "\\" not in mode
            assert not mode.endswith(".yaml")

    def test_balanced_is_not_part_of_the_vocabulary(self) -> None:
        spec = _load_vocabulary_artifact()
        assert "balanced" not in spec["modes"]


class TestExplorerContractCarriesTheVocabulary:
    def test_site_inputs_contract_publishes_the_fixture_modes(self, tmp_path: Path) -> None:
        import json

        from scripts.site_inputs import build_contract

        contract_path = tmp_path / "contract.json"
        build_contract(contract_path)
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        assert contract["tuning_vocabulary"] == _load_vocabulary_artifact()["modes"]


class TestProductionConstantsMatchTheSharedFixture:
    def test_modes_tuple_matches_fixture_order(self) -> None:
        spec = _load_vocabulary_artifact()
        assert list(tuning_modes.MODES) == spec["modes"] == EXPECTED_MODES

    def test_not_recorded_constant_matches_fixture(self) -> None:
        spec = _load_vocabulary_artifact()
        assert tuning_modes.NOT_RECORDED == spec["not_recorded_sentinel"] == EXPECTED_NOT_RECORDED_SENTINEL

    def test_named_constants_match_their_vocabulary_position(self) -> None:
        assert tuning_modes.TUNED == "tuned"
        assert tuning_modes.TUNED_FALLBACK == "tuned-fallback"
        assert tuning_modes.NOTUNING == "notuning"
        assert tuning_modes.AUTO == "auto"
        assert tuning_modes.CUSTOM == "custom"

    def test_is_canonical_mode_accepts_only_the_pinned_set(self) -> None:
        for mode in EXPECTED_MODES:
            assert tuning_modes.is_canonical_mode(mode)
        assert not tuning_modes.is_canonical_mode("balanced")
        assert not tuning_modes.is_canonical_mode("examples/tunings/duckdb/tpch_tuned.yaml")
        assert not tuning_modes.is_canonical_mode(tuning_modes.NOT_RECORDED)
        assert not tuning_modes.is_canonical_mode(None)


class TestCanonicalModeMapsResolutionsOntoTheSharedVocabulary:
    def test_tuned_via_auto_discovered_template_is_tuned(self) -> None:
        resolution = TuningResolution(mode=TuningMode.TUNED, source=TuningSource.AUTO_DISCOVERED, enabled=True)
        assert resolution.canonical_mode == tuning_modes.TUNED

    def test_tuned_via_fallback_is_tuned_fallback(self) -> None:
        resolution = TuningResolution(mode=TuningMode.TUNED, source=TuningSource.FALLBACK, enabled=True)
        assert resolution.canonical_mode == tuning_modes.TUNED_FALLBACK

    def test_tuned_via_wizard_is_tuned_not_tuned_fallback(self) -> None:
        resolution = TuningResolution(mode=TuningMode.TUNED, source=TuningSource.INTERACTIVE_WIZARD, enabled=True)
        assert resolution.canonical_mode == tuning_modes.TUNED

    def test_tuned_via_explicit_config_file_default_is_tuned(self) -> None:
        resolution = TuningResolution(mode=TuningMode.TUNED, source=TuningSource.EXPLICIT_FILE, enabled=True)
        assert resolution.canonical_mode == tuning_modes.TUNED

    def test_tuned_via_packaged_resource_is_tuned_not_tuned_fallback(self) -> None:
        resolution = TuningResolution(mode=TuningMode.TUNED, source=TuningSource.PACKAGED_RESOURCE, enabled=True)
        assert resolution.canonical_mode == tuning_modes.TUNED

    def test_custom_file_is_custom_never_the_raw_path(self) -> None:
        resolution = TuningResolution(
            mode=TuningMode.CUSTOM_FILE,
            source=TuningSource.EXPLICIT_FILE,
            enabled=True,
            config_file=Path("/home/user/my-tuning.yaml"),
        )
        assert resolution.canonical_mode == tuning_modes.CUSTOM
        assert "/" not in resolution.canonical_mode

    def test_notuning_is_notuning(self) -> None:
        resolution = TuningResolution(mode=TuningMode.NOTUNING, source=TuningSource.BASELINE, enabled=False)
        assert resolution.canonical_mode == tuning_modes.NOTUNING

    def test_auto_is_auto(self) -> None:
        resolution = TuningResolution(mode=TuningMode.AUTO, source=TuningSource.SMART_DEFAULTS, enabled=True)
        assert resolution.canonical_mode == tuning_modes.AUTO

    def test_canonical_mode_is_always_a_pinned_vocabulary_value(self) -> None:
        for mode in TuningMode:
            for source in TuningSource:
                resolution = TuningResolution(mode=mode, source=source, enabled=True)
                assert tuning_modes.is_canonical_mode(resolution.canonical_mode)


class TestFallbackLabelingEndToEnd:
    def _invoke(self, tmp_path, monkeypatch, *, official: bool = False, scale: str = "0.01"):
        monkeypatch.setenv("BENCHBOX_TUNING_PATH", str(tmp_path / "no-such-tuning-dir"))
        monkeypatch.chdir(tmp_path)

        with ExitStack() as stack:
            bench_mgr = stack.enter_context(patch.object(_run_module, "BenchmarkManager"))
            profiler = stack.enter_context(patch.object(_run_module, "SystemProfiler"))
            orchestrator = stack.enter_context(patch.object(_run_module, "BenchmarkOrchestrator"))
            exporter = stack.enter_context(patch.object(_run_module, "ResultExporter"))
            db_mgr = stack.enter_context(patch.object(_run_module, "DatabaseManager"))

            bench_mgr.return_value.benchmarks = {
                "coffeeshop": {"display_name": "CoffeeShop", "estimated_time_range": (2, 10)}
            }
            profiler.return_value.get_system_profile.return_value = Mock()

            mock_db_cfg = Mock()
            mock_db_cfg.type = "duckdb"
            mock_db_cfg.options = {}
            mock_db_cfg.driver_version_actual = "1.4.3"
            mock_db_cfg.driver_version_resolved = "1.4.3"
            db_mgr.return_value.create_config.return_value = mock_db_cfg

            mock_result = Mock()
            mock_result.validation_status = "PASSED"
            mock_result.validation_details = {"stages": []}
            orchestrator.return_value.execute_benchmark.return_value = mock_result
            exporter.return_value.export_result.return_value = {"json": str(tmp_path / "result.json")}

            args = [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "coffeeshop",
                "--scale",
                scale,
                "--tuning",
                "tuned",
                "--non-interactive",
                "--output",
                str(tmp_path / "out"),
            ]
            if official:
                args.append("--official")
            result = CliRunner().invoke(cli, args)
            return result, orchestrator

    def test_duckdb_coffeeshop_has_no_template_anywhere(self) -> None:
        from benchbox.core.tuning.packaged_templates import packaged_template_path

        assert not Path("examples/tunings/duckdb/coffeeshop_tuned.yaml").exists()
        assert not packaged_template_path("duckdb", "coffeeshop").exists()

    def test_tuned_with_no_template_records_tuned_fallback(self, tmp_path, monkeypatch) -> None:
        result, orchestrator = self._invoke(tmp_path, monkeypatch, official=False)

        assert result.exit_code == 0, result.output
        orchestrator.return_value.execute_benchmark.assert_called_once()
        execution_context = orchestrator.return_value.execute_benchmark.call_args.kwargs["execution_context"]
        assert execution_context.tuning_mode == tuning_modes.TUNED_FALLBACK

    def test_official_refuses_tuned_fallback(self, tmp_path, monkeypatch) -> None:
        result, orchestrator = self._invoke(tmp_path, monkeypatch, official=True, scale="1")

        assert result.exit_code != 0
        assert "tuned-fallback" in result.output
        orchestrator.return_value.execute_benchmark.assert_not_called()
