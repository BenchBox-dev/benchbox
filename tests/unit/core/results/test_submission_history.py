from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.core.results.submission_history import (
    list_hosted_submissions,
    record_hosted_submission,
    submission_history_path,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_record_hosted_submission_writes_sidecar_without_token(tmp_path: Path) -> None:
    result_file = tmp_path / "tpch_duckdb.json"
    result_file.write_text('{"version": "2.1"}', encoding="utf-8")

    sidecar = record_hosted_submission(
        result_file=result_file,
        service_url="https://api.benchbox.dev/v1",
        manifest={
            "bundle_file": result_file.name,
            "bundle_hash": "a" * 64,
            "benchmark": "tpch",
            "platform": "duckdb",
            "scale_factor": 0.01,
        },
        status="published",
        idempotency_key="fixed-key",
        submission_id="sub1",
        public_result_id="r1",
        public_url="https://benchbox.dev/results/r/r1",
    )

    assert sidecar == submission_history_path(result_file, idempotency_key="fixed-key")
    raw = sidecar.read_text(encoding="utf-8")
    assert "secret-token" not in raw
    data = json.loads(raw)
    assert data["result_file"] == result_file.name
    assert data["public_url"] == "https://benchbox.dev/results/r/r1"
    assert data["idempotency_key"] == "fixed-key"


def test_list_hosted_submissions_sorts_newest_first(tmp_path: Path) -> None:
    older = tmp_path / "older.submission.json"
    older.write_text(
        json.dumps({"submitted_at": "2026-01-01T00:00:00+00:00", "submission_id": "old"}),
        encoding="utf-8",
    )
    newer = tmp_path / "newer.submission.json"
    newer.write_text(
        json.dumps({"submitted_at": "2026-02-01T00:00:00+00:00", "submission_id": "new"}),
        encoding="utf-8",
    )

    records = list_hosted_submissions(tmp_path)

    assert [record["submission_id"] for record in records] == ["new", "old"]


def test_record_hosted_submission_keeps_distinct_idempotency_records(tmp_path: Path) -> None:
    result_file = tmp_path / "tpch_duckdb.json"
    result_file.write_text('{"version": "2.1"}', encoding="utf-8")
    manifest = {
        "bundle_file": result_file.name,
        "bundle_hash": "a" * 64,
        "benchmark": "tpch",
        "platform": "duckdb",
        "scale_factor": 0.01,
    }

    first = record_hosted_submission(
        result_file=result_file,
        service_url="https://api.benchbox.dev/v1",
        manifest=manifest,
        status="published",
        idempotency_key="prod/key",
        submission_id="sub1",
        public_result_id="r1",
        public_url="https://benchbox.dev/results/r/r1",
    )
    second = record_hosted_submission(
        result_file=result_file,
        service_url="https://staging.benchbox.dev/v1",
        manifest=manifest,
        status="published",
        idempotency_key="staging-key",
        submission_id="sub2",
        public_result_id="r2",
        public_url="https://benchbox.dev/results/r/r2",
    )

    assert first != second
    assert first.name == "tpch_duckdb.prod-key.submission.json"
    assert len(list_hosted_submissions(tmp_path)) == 2


