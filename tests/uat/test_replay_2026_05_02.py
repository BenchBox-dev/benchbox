from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

CONFIG = Path(__file__).resolve().parent / "configs" / "uat-2026-05-02.yaml"
HEADER_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "uat-2026-05-02-matrix-summary-header.tsv"

pytestmark = pytest.mark.slow


def test_replay_dry_run_produces_expected_columns(tmp_path: Path):
    from tests.uat.config import load_config
    from tests.uat.orchestrator import run_sweep

    cfg = load_config(CONFIG)
    cfg = replace(cfg, dry_run=True)
    result = run_sweep(cfg, log_dir_override=tmp_path)
    expected_columns = HEADER_FIXTURE.read_text(encoding="utf-8").rstrip("\n").split("\t")
    from tests.uat.phases.report import REPORT_HEADER

    assert REPORT_HEADER.split("\t") == expected_columns
    assert result.exit_code() == 0


def test_replay_config_has_expected_shape():
    import yaml

    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert raw["name"] == "uat-2026-05-02"
    assert raw["scales"]["rungs"] == [0.01, 0.1, 1.0]
    assert raw["execute"]["per_cell_timeout_s"] == 600
    assert raw["execute"]["early_stop_after_s"] == 180
    assert raw["package"]["submit_terminal_state"] == "local-stage"
    assert raw["report"]["cross_scale_coverage_min_pairs"] is None


