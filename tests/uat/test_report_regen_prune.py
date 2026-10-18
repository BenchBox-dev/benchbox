from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.uat import _cli as uat_cli, cells_io, orchestrator
from tests.uat.phases.enumerate import (
    REGISTRY_PRUNE_STATUS,
    CompatibilityPrunedCell,
    count_pruned_by_kind,
    is_registry_prune,
)
from tests.uat.runner import CellResult

pytestmark = pytest.mark.medium


def _source_info() -> orchestrator.RunSourceInfo:
    return orchestrator.RunSourceInfo(commit_sha="deadbeef", commit_short_sha="deadbee", dirty=False)


def _passed_cell(benchmark: str) -> CellResult:
    return CellResult(
        platform="duckdb",
        benchmark=benchmark,
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=Path(f"/tmp/{benchmark}.log"),
        result_path=Path(f"/tmp/{benchmark}.json"),
    )


def _registry_prune(benchmark: str, rule_id: str) -> CompatibilityPrunedCell:
    return CompatibilityPrunedCell("duckdb", benchmark, 0.01, rule_id, REGISTRY_PRUNE_STATUS, "not in registry", "")


def _compat_rule_prune(benchmark: str) -> CompatibilityPrunedCell:
    return CompatibilityPrunedCell("clickhouse-server", benchmark, 1.0, "some-rule", "blocked", "unsupported", "")


def test_is_registry_prune_distinguishes_registry_from_compatibility():
    assert is_registry_prune(_registry_prune("nope", "benchmark-not-in-registry")) is True
    assert is_registry_prune(_compat_rule_prune("tpch")) is False


def test_count_pruned_by_kind_splits_registry_from_compatibility():
    rows = [
        _registry_prune("nope", "benchmark-not-in-registry"),
        _registry_prune("nope-platform", "platform-not-in-registry"),
        _compat_rule_prune("tpch"),
    ]
    assert count_pruned_by_kind(rows) == (1, 2)
    assert count_pruned_by_kind(()) == (0, 0)
    assert count_pruned_by_kind(iter(rows)) == (1, 2)


def test_gate_summary_no_report_path_splits_registry_prunes():
    outcome = SimpleNamespace(
        results=[_passed_cell("tpch")],
        compatibility_pruned=[
            _registry_prune("nope", "benchmark-not-in-registry"),
            _registry_prune("nope-platform", "platform-not-in-registry"),
            _compat_rule_prune("ssb"),
        ],
        pruned=[],
        skipped_unreachable=[],
        startup_failed=[],
        aborted=False,
    )

    accounting = orchestrator._accounting_for_gate_summary(None, outcome)

    assert accounting.registry_pruned == 2
    assert accounting.compatibility_pruned == 1
    assert accounting.skipped == 3
    assert accounting.total_defined == (
        accounting.attempted + accounting.skipped + accounting.unreachable + accounting.startup_failed
    )


def test_regen_report_reconstructs_prune_counts_from_sidecar(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cells_jsonl = tmp_path / "cells.jsonl"
    cells_io.write_cells_jsonl(
        cells_jsonl,
        [_passed_cell("tpch"), _passed_cell("tpcds")],
        source_info=_source_info(),
        compatibility_pruned_count=2,
        early_stop_pruned_count=1,
        registry_pruned_count=3,
    )
    output_tsv = tmp_path / "matrix_summary.tsv"

    exit_code = uat_cli.main(["report", "--cells-jsonl", str(cells_jsonl), "--output-tsv", str(output_tsv)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_defined"] == 8
    assert payload["registry_pruned"] == 3
    text = output_tsv.read_text(encoding="utf-8")
    assert "compatibility_pruned=2" in text
    assert "registry_pruned=3" in text
    assert "early_stop_pruned=1" in text


def test_regen_without_prune_counts_is_unchanged(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cells_jsonl = tmp_path / "cells.jsonl"
    cells_io.write_cells_jsonl(cells_jsonl, [_passed_cell("tpch")], source_info=_source_info())
    output_tsv = tmp_path / "matrix_summary.tsv"

    exit_code = uat_cli.main(["report", "--cells-jsonl", str(cells_jsonl), "--output-tsv", str(output_tsv)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_defined"] == 1
    assert payload["registry_pruned"] == 0
