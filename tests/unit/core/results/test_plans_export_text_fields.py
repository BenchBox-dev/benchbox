from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from benchbox.core.results import exporter as exporter_module
from benchbox.core.results.anonymization import anonymize_plans_payload, find_public_path_leaks
from benchbox.core.results.exporter import ResultExporter
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

HOME = "/Users/synthetic-user"
OUT_DIR = "/srv/synthetic-out/results"
DATA_DIR = "/srv/synthetic-data/tpch_sf001"
FINGERPRINT = "f" * 64


def _text_plan() -> str:
    return f"Parquet SCAN [{HOME}/data/lineitem.parquet]\nParquet SCAN [{DATA_DIR}/orders.parquet]\n  PROJECT 2/16"


def _payload() -> dict:
    text = _text_plan()
    return {
        "version": "2.1",
        "run_id": "run-1",
        "plans_captured": 3,
        "queries": {
            "Q1": {
                "fingerprint": FINGERPRINT,
                "fingerprint_varied": True,
                "fingerprint_variants": [{"fingerprint": FINGERPRINT, "iteration": 1}],
                "plan_format": "text",
                "plan_text": text,
                "plan": {
                    "platform": "polars",
                    "plan_type": "optimized",
                    "plan_text": text,
                    "plan_data": {"optimized_plan": text, "logical_plan": f"FROM {OUT_DIR}/x.csv"},
                    "optimization_hints": [f"hint {HOME}/a"],
                    "raw_explain_output": text,
                },
            },
            "Q2": {"plan_format": "text", "plan": f"SCAN {HOME}/legacy.parquet"},
        },
        "errors": [{"query_id": "Q3", "error": "boom"}],
    }


def test_anonymize_plans_payload_sanitizes_text_fields_on_a_copy():
    payload = _payload()
    before = copy.deepcopy(payload)

    sanitized = anonymize_plans_payload(payload, roots=[OUT_DIR, DATA_DIR])

    assert payload == before
    assert find_public_path_leaks(sanitized, roots=[OUT_DIR, DATA_DIR, HOME]) == []
    q1 = sanitized["queries"]["Q1"]
    assert q1["fingerprint"] == FINGERPRINT
    assert q1["fingerprint_varied"] is True
    assert q1["fingerprint_variants"] == before["queries"]["Q1"]["fingerprint_variants"]
    assert q1["plan"]["plan_text"] == q1["plan_text"]
    assert "<redacted>" in q1["plan"]["plan_data"]["logical_plan"]
    assert "PROJECT 2/16" in q1["plan"]["plan_text"]
    assert list(q1["plan"]["plan_data"]) == ["optimized_plan", "logical_plan"]
    assert sanitized["queries"]["Q2"]["plan"] == "SCAN <redacted>"
    assert sanitized["errors"] == before["errors"]


def test_anonymize_plans_payload_leaves_clean_plan_text_unchanged():
    clean = "Seq Scan on public.lineitem\n  Filter: (l_shipdate < '1998-01-01')\nSCAN catalog.db.orders https://example.com/a"
    payload = {
        "queries": {"Q1": {"plan_text": clean, "plan": {"plan_text": clean, "plan_data": {"logical_plan": clean}}}}
    }

    assert anonymize_plans_payload(payload) == payload


def test_export_anonymize_text_companion_has_no_leaks(monkeypatch, tmp_path):
    monkeypatch.setattr(exporter_module, "build_plans_payload", lambda _result: _payload())
    result = make_benchmark_results(
        benchmark_id="tpch",
        benchmark_name="tpch",
        platform="polars-df",
        execution_id="r",
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        query_plans_captured=1,
        platform_info={"working_dir": DATA_DIR},
    )

    exported = ResultExporter(output_dir=tmp_path, anonymize=True).export_result(result, formats=["json"])
    text = (tmp_path / f"{exported['json'].stem}.plans.json").read_text(encoding="utf-8")

    assert find_public_path_leaks(json.loads(text), roots=[DATA_DIR, tmp_path]) == []
    assert HOME not in text
    assert DATA_DIR not in text
    assert Path(tmp_path).as_posix() not in text
