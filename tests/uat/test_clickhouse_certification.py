from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.uat.clickhouse_certification import (
    CertificationArtifactError,
    manifest_table_rows,
    result_table_rows,
    validate_exact_manifest_rows,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _write_artifacts(tmp_path: Path, *, lineitem_manifest: int = 3, lineitem_result: int = 3) -> tuple[Path, Path]:
    manifest = tmp_path / "_datagen_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 2,
                "benchmark": "tpch",
                "scale_factor": 1.0,
                "format_preference": ["tbl"],
                "tables": {
                    "customer": {
                        "tbl": [{"path": "customer.tbl", "row_count": 2}],
                        "parquet": [{"path": "customer.parquet", "row_count": 4}],
                    },
                    "lineitem": {
                        "tbl": [{"path": "lineitem.tbl", "row_count": lineitem_manifest}],
                        "parquet": [{"path": "lineitem.parquet", "row_count": lineitem_manifest + 1}],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    result = tmp_path / "result.json"
    result.write_text(
        json.dumps(
            {
                "benchmark": {"id": "tpch", "scale_factor": 1.0},
                "platform": {"name": "ClickHouse (Server)"},
                "tables": {"customer": {"rows": 2}, "lineitem": {"rows": lineitem_result}},
            }
        ),
        encoding="utf-8",
    )
    return manifest, result


def test_exact_row_gate_compares_each_table(tmp_path: Path):
    manifest, result = _write_artifacts(tmp_path)
    validation = validate_exact_manifest_rows(manifest, result, "tbl")
    assert validation.passed
    assert validation.expected == {"customer": 2, "lineitem": 3}


def test_exact_row_gate_rejects_table_mismatch(tmp_path: Path):
    manifest, result = _write_artifacts(tmp_path, lineitem_result=4)
    with pytest.raises(CertificationArtifactError, match="lineitem: expected=3 actual=4"):
        validate_exact_manifest_rows(manifest, result, "tbl")


def test_exact_row_gate_rejects_missing_or_extra_table(tmp_path: Path):
    manifest, result = _write_artifacts(tmp_path)
    payload = json.loads(result.read_text(encoding="utf-8"))
    del payload["tables"]["customer"]
    payload["tables"]["supplier"] = {"rows": 1}
    result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CertificationArtifactError, match="customer: expected=2 actual=None"):
        validate_exact_manifest_rows(manifest, result, "tbl")


def test_artifact_readers_fail_closed_on_missing_counts(tmp_path: Path):
    manifest, result = _write_artifacts(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    del payload["tables"]["lineitem"]["tbl"][0]["row_count"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CertificationArtifactError, match="lacks row_count"):
        manifest_table_rows(manifest, "tbl")

    result.write_text(json.dumps({"tables": {"lineitem": {}}}), encoding="utf-8")
    with pytest.raises(CertificationArtifactError, match="lacks a table row count"):
        result_table_rows(result)


def test_certification_uses_explicit_loaded_format(tmp_path: Path):
    manifest, result = _write_artifacts(tmp_path, lineitem_manifest=4, lineitem_result=5)
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["tables"]["customer"]["rows"] = 4
    result.write_text(json.dumps(payload), encoding="utf-8")
    assert validate_exact_manifest_rows(manifest, result, "parquet").expected == {"customer": 4, "lineitem": 5}


def test_certification_rejects_a_result_from_another_platform(tmp_path: Path):
    manifest, result = _write_artifacts(tmp_path)
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["platform"]["name"] = "DuckDB"
    result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CertificationArtifactError, match="platform must be ClickHouse Server"):
        validate_exact_manifest_rows(manifest, result, "tbl")
