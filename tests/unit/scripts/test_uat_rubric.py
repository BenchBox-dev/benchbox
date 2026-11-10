from __future__ import annotations

import json
from pathlib import Path

import pytest
import uat_rubric

from tests.uat.rubric_fixtures import PASS, TIMEOUT, write_history

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]

STAGE_CONFIG = """\
name: rubric-cli
platforms:
  include: [duckdb]
benchmarks:
  include: [tpch]
scales:
  rungs: [0.01, 0.1, 1.0]
"""


@pytest.fixture
def stage_config(tmp_path: Path) -> Path:
    path = tmp_path / "stage.yaml"
    path.write_text(STAGE_CONFIG, encoding="utf-8")
    return path


def test_cli_writes_proposals_and_signal_record_from_history(tmp_path, stage_config, capsys):
    history = tmp_path / "history"
    history.mkdir()
    write_history(
        history,
        {("duckdb", "tpch", 1.0): [PASS] * 6 + [TIMEOUT] * 4, ("duckdb", "tpch", 0.01): [PASS] * 10},
    )
    out = tmp_path / "out"

    code = uat_rubric.main(
        ["--history-dir", str(history), "--output-dir", str(out), "--stage-config", str(stage_config)]
    )

    assert code == 0
    proposals = json.loads((out / uat_rubric.PROPOSALS_FILENAME).read_text(encoding="utf-8"))
    assert [p["scale"] for p in proposals["proposals"]] == [1.0]
    assert proposals["proposals"][0]["tracker_item"]["title"].startswith("UAT envelope:")
    signals = json.loads((out / uat_rubric.SIGNAL_RECORD_FILENAME).read_text(encoding="utf-8"))
    assert [c["scale"] for c in signals["drop_list"]["candidates"]] == [1.0]
    assert "envelope proposals: 1" in capsys.readouterr().out


def test_cli_rejects_a_missing_history_directory(tmp_path, capsys):
    assert uat_rubric.main(["--history-dir", str(tmp_path / "absent"), "--output-dir", str(tmp_path)]) == 2
    assert "history directory not found" in capsys.readouterr().err


def test_default_stage_configs_define_gated_cells():
    gated = uat_rubric.gated_scales_from_configs([REPO_ROOT / name for name in uat_rubric.DEFAULT_STAGE_CONFIGS])

    assert gated
    assert all(scales for scales in gated.values())
