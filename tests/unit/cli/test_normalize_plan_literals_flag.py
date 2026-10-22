# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License.

from __future__ import annotations

import glob
import json

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.run import run
from benchbox.cli.config import ConfigManager
from benchbox.cli.dryrun import generate_cli_command

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def _load_plans_companion(results_dir):

    matches = glob.glob(str(results_dir / "*.plans.json"))
    assert matches, f"No plans companion file written in {results_dir}"
    with open(matches[0], encoding="utf-8") as handle:
        return json.load(handle)


class TestNormalizePlanLiteralsFlagAccepted:
    def test_flag_is_a_recognized_option(self):
        param_names = {p.name for p in run.params}
        assert "normalize_plan_literals" in param_names

    def test_dry_run_command_generator_reproduces_flag(self):
        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.01,
            capture_plans=True,
            normalize_plan_literals=True,
        )
        assert "--normalize-plan-literals" in cmd

    def test_dry_run_command_generator_omits_flag_when_unset(self):
        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.01,
            capture_plans=True,
            normalize_plan_literals=False,
        )
        assert "--normalize-plan-literals" not in cmd


class TestNormalizePlanLiteralsFingerprintInResults:
    def _run(self, monkeypatch, tmp_path, tpch_sf001_data, extra_args):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--phases",
                "power",
                "--queries",
                "1",
                "--capture-plans",
                "--non-interactive",
                "--quiet",
                "--output",
                tpch_sf001_data(tmp_path / "data"),
                *extra_args,
            ],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        return tmp_path / "benchmark_runs" / "results"

    def test_normalized_fingerprint_present_when_flag_set(self, monkeypatch, tmp_path, tpch_sf001_data):
        results_dir = self._run(monkeypatch, tmp_path, tpch_sf001_data, ["--normalize-plan-literals"])
        payload = _load_plans_companion(results_dir)

        plans = payload["queries"]
        assert plans, "Expected at least one captured plan"
        for entry in plans.values():
            assert "fingerprint" in entry
            assert "fingerprint_normalized" in entry

            assert len(entry["fingerprint_normalized"]) == 64

    def test_normalized_fingerprint_absent_without_flag(self, monkeypatch, tmp_path, tpch_sf001_data):
        results_dir = self._run(monkeypatch, tmp_path, tpch_sf001_data, [])
        payload = _load_plans_companion(results_dir)

        plans = payload["queries"]
        assert plans, "Expected at least one captured plan"
        for entry in plans.values():
            assert "fingerprint" in entry
            assert "fingerprint_normalized" not in entry
