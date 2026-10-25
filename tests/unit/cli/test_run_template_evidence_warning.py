from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli
from benchbox.core.system import SystemProfiler
from benchbox.core.tuning import template_evidence
from benchbox.utils import config_interface
from tests.utilities.owned_cli_state import owned_cli_state

pytestmark = [pytest.mark.unit, pytest.mark.medium]

WARNING = (
    "Tuned template duckdb/tpch has no measured benefit: the result shows "
    "the template was applied, not that it is faster than notuning."
)


def _result_payloads(root: Path) -> list[dict]:
    payloads = []
    for path in root.rglob("*.json"):
        try:
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return payloads


def _tuned_run_payload(tmp_path: Path) -> dict | None:
    for payload in _result_payloads(tmp_path):
        tuning = payload.get("platform", {}).get("tuning", {}) if isinstance(payload, dict) else {}
        if isinstance(tuning, dict) and "template_evidence" in tuning:
            return payload
    return None


DATA_ORGANIZATION_ENV = "BENCHBOX_DATA_ORGANIZATION_CONFIG_JSON"


@pytest.fixture(scope="module")
def process_state_before_run():
    return {
        "provider": config_interface._config_provider,
        "data_organization": os.environ.get(DATA_ORGANIZATION_ENV),
    }


@pytest.fixture(scope="module")
def duckdb_tuned_run(tmp_path_factory, process_state_before_run):
    output_dir = tmp_path_factory.mktemp("tuned_run")
    with owned_cli_state(), pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(output_dir))
        result = CliRunner().invoke(
            cli,
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--tuning",
                "tuned",
                "--scale",
                "0.01",
                "--queries",
                "1",
                "--non-interactive",
            ],
        )
    return result, output_dir


def test_duckdb_tuned_run_warns_and_records_unmeasured_evidence(duckdb_tuned_run):
    result, output_dir = duckdb_tuned_run

    assert result.exit_code == 0, result.output
    assert WARNING in result.output
    payload = _tuned_run_payload(output_dir)
    assert payload is not None, "no exported result carries platform.tuning.template_evidence"
    assert payload["platform"]["tuning"]["template_evidence"] == "unmeasured"


def test_duckdb_tuned_run_prints_tuning_verification_summary(duckdb_tuned_run):
    result, _ = duckdb_tuned_run

    assert result.exit_code == 0, result.output
    assert "Tuning verification: applied_verified" in result.output
    assert "Verdicts:" in result.output
    assert "Top reasons:" in result.output
    assert "Dropped intents" in result.output


def test_module_scoped_run_leaves_process_state_untouched(duckdb_tuned_run, process_state_before_run):
    assert config_interface._config_provider is process_state_before_run["provider"]
    assert os.environ.get(DATA_ORGANIZATION_ENV) == process_state_before_run["data_organization"]


def test_duckdb_notuning_run_has_no_evidence_warning(tmp_path, monkeypatch, tpch_sf001_data):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path))

    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--tuning",
            "notuning",
            "--scale",
            "0.01",
            "--queries",
            "1",
            "--non-interactive",
            "--output",
            tpch_sf001_data(tmp_path / "data"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "has no measured benefit" not in result.output
    assert _tuned_run_payload(tmp_path) is None


def test_duckdb_tuned_run_without_engine_metadata_stays_unmeasured(tmp_path, monkeypatch, tpch_sf001_data):
    profile = SystemProfiler().get_system_profile()
    monkeypatch.setattr(
        template_evidence,
        "_REGISTRY",
        {
            "duckdb": {
                "tpch": {
                    "measurements": [
                        {
                            "state": "measured-benefit",
                            "scale_factor": 0.01,
                            "memory": {"basis": "host_total_gb", "value": profile.memory_total_gb},
                            "engine_version": duckdb.__version__,
                            "measured_on": "2026-10-11",
                            "evidence": "test fixture",
                        }
                    ]
                }
            }
        },
    )
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path))

    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--tuning",
            "tuned",
            "--scale",
            "0.01",
            "--queries",
            "1",
            "--non-interactive",
            "--output",
            tpch_sf001_data(tmp_path / "data"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert WARNING in result.output
    payload = _tuned_run_payload(tmp_path)
    assert payload is not None
    assert payload["platform"]["tuning"]["template_evidence"] == "unmeasured"


@pytest.mark.parametrize(("warehouse_size", "warned"), (("X-Small", False), ("Small", True)))
def test_snowflake_warning_follows_recorded_warehouse_size(warehouse_size, warned, monkeypatch):
    from types import SimpleNamespace

    from benchbox.cli.commands import run as run_command

    template_evidence.load_registry(refresh=True)
    printed: list[str] = []
    monkeypatch.setattr(run_command.console, "print", lambda message, **_: printed.append(message))
    result = SimpleNamespace(
        platform="snowflake",
        benchmark_name="tpcds",
        tuning_source_file="examples/tunings/snowflake/tpcds_tuned.yaml",
        tunings_applied={"table_tunings": {}},
        scale_factor=100.0,
        execution_environment={"platform_runtime": {"runtime_type": "managed_cloud"}},
        system_profile=None,
        platform_info={"engine_version": "10.37.102"},
        platform_compute={
            "warehouse_size": warehouse_size,
            "warehouse_size_source": "observed",
            "multi_cluster_warehouse": False,
            "source": "observed",
        },
    )

    run_command._print_template_evidence_warning(result, quiet=False)

    assert bool(printed) is warned
