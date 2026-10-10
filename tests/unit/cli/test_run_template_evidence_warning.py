from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli

pytestmark = [pytest.mark.unit, pytest.mark.fast]

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


def test_duckdb_tuned_run_warns_and_records_unmeasured_evidence(tmp_path, monkeypatch):
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
        ],
    )

    assert result.exit_code == 0, result.output
    assert WARNING in result.output
    payload = _tuned_run_payload(tmp_path)
    assert payload is not None, "no exported result carries platform.tuning.template_evidence"
    assert payload["platform"]["tuning"]["template_evidence"] == "unmeasured"


def test_duckdb_notuning_run_has_no_evidence_warning(tmp_path, monkeypatch):
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
        ],
    )

    assert result.exit_code == 0, result.output
    assert "has no measured benefit" not in result.output
    assert _tuned_run_payload(tmp_path) is None
