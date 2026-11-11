from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.tuning_evidence import __main__ as cli
from scripts.tuning_evidence.harness import ArmSpec

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def parse(*arguments: str) -> cli.argparse.Namespace:
    return cli.build_parser().parse_args(["run", "--platform", "duckdb", "--scale", "0.01", "--out", "x", *arguments])


def test_defaults_follow_the_decision_record() -> None:
    config = cli.config_from(parse())
    assert [arm.name for arm in config.arms] == ["N", "T"]
    assert config.baseline == "N"
    assert (config.rounds, config.timeout_seconds, config.load_ceiling, config.round_retries) == (9, 300.0, 8.0, 3)
    assert (config.cold, config.streams, config.resamples) == (False, 0, 2000)
    assert config.thresholds.min_rounds == 7
    assert config.calibration_arm is None


def test_aa_mode_builds_two_identical_arms_and_calibrates_from_the_second() -> None:
    config = cli.config_from(parse("--aa", "tuned", "--rounds", "7"))
    assert config.arms == (ArmSpec("A", load="tuned", session="tuned"), ArmSpec("B", load="tuned", session="tuned"))
    assert config.calibration_arm == "B"


def test_aa_mode_cannot_be_combined_with_arms() -> None:
    with pytest.raises(ValueError):
        cli.config_from(parse("--aa", "notuning", "--arm", "X=tuned"))


def test_chdb_throughput_is_refused() -> None:
    arguments = cli.build_parser().parse_args(
        ["run", "--platform", "clickhouse-local", "--scale", "1", "--out", "x", "--streams", "2"]
    )
    with pytest.raises(ValueError, match="chDB"):
        cli.config_from(arguments)


def test_main_reports_bad_arguments_without_a_traceback(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["run", "--platform", "duckdb", "--scale", "0.01", "--out", str(tmp_path), "--rounds", "3"])
    assert code == 1
    assert "rounds must be at least 7" in capsys.readouterr().err


def test_report_renders_a_saved_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = {
        "status": "completed",
        "harness": {"version": "1"},
        "config": {
            "platform": "duckdb",
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "cache_policy": "warm",
            "rounds": 7,
            "warmup_rounds": 1,
            "seed": 42,
            "resamples": 2000,
            "confidence": 0.95,
            "timeout_seconds": 300.0,
            "load_ceiling": 8.0,
            "round_retries": 3,
            "streams": 0,
        },
        "arms": {},
        "rounds": [],
        "results": {},
    }
    source = tmp_path / "harness.json"
    source.write_text(json.dumps(payload))
    assert cli.main(["report", str(source)]) == 0
    assert "# Tuning evidence: duckdb tpch SF0.01" in capsys.readouterr().out


