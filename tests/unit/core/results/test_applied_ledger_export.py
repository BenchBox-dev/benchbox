# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from benchbox.core.results.anonymization import anonymize_applied_tuning_payload
from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.result_factory import build_enhanced_benchmark_result
from benchbox.core.results.schema import build_result_payload

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _applied_payload() -> dict:
    return {
        "status": "applied_unverified",
        "applied_ledger_hash": "a1b2c3" * 10 + "d4d4",
        "statements": [
            {
                "statement": "CREATE INDEX IF NOT EXISTS idx ON LINEITEM (l_orderkey)",
                "phase": "ddl",
                "status": "executed",
            }
        ],
        "dropped": [{"intent": "partitioning:LINEITEM", "reason": "load-time only"}],
    }


def test_factory_threads_applied_ledger_onto_result() -> None:
    benchmark = SimpleNamespace(benchmark_name="tpch", scale_factor=0.01, compliance_class=None)
    applied = _applied_payload()

    result = build_enhanced_benchmark_result(
        benchmark=benchmark,
        platform="duckdb",
        query_results=[],
        tunings_applied={"configuration": {"tuning_mode": "tuned"}},
        tuning_config_hash="requested-hash",
        tuning_validation_status="applied_unverified",
        tuning_metadata_saved=True,
        applied_tuning_ledger=applied,
        applied_ledger_hash=applied["applied_ledger_hash"],
    )

    assert result.applied_tuning_ledger == applied
    assert result.applied_ledger_hash == applied["applied_ledger_hash"]
    assert result.tuning_validation_status == "applied_unverified"
    assert result.tuning_metadata_saved is True
    assert result.tuning_config_hash == "requested-hash"
    assert result.applied_ledger_hash != result.tuning_config_hash


def test_factory_without_ledger_leaves_fields_none() -> None:
    benchmark = SimpleNamespace(benchmark_name="tpch", scale_factor=0.01, compliance_class=None)

    result = build_enhanced_benchmark_result(benchmark=benchmark, platform="duckdb", query_results=[])

    assert result.applied_tuning_ledger is None
    assert result.applied_ledger_hash is None
    assert result.tuning_validation_status == "not_validated"


def test_loader_reconstructs_applied_ledger_from_companion() -> None:
    benchmark = SimpleNamespace(benchmark_name="tpch", scale_factor=0.01, compliance_class=None)
    applied = _applied_payload()
    result = build_enhanced_benchmark_result(
        benchmark=benchmark,
        platform="duckdb",
        query_results=[],
        tunings_applied={"configuration": {"tuning_mode": "tuned"}},
        tuning_config_hash="requested-hash",
        tuning_validation_status="applied_unverified",
        applied_tuning_ledger=applied,
        applied_ledger_hash=applied["applied_ledger_hash"],
    )
    payload = build_result_payload(result)

    reconstructed = reconstruct_benchmark_results(payload, applied_data=applied)

    assert reconstructed.applied_tuning_ledger == applied
    assert reconstructed.applied_ledger_hash == applied["applied_ledger_hash"]


def test_loader_without_applied_companion_leaves_fields_none() -> None:
    benchmark = SimpleNamespace(benchmark_name="tpch", scale_factor=0.01, compliance_class=None)
    result = build_enhanced_benchmark_result(benchmark=benchmark, platform="duckdb", query_results=[])
    payload = build_result_payload(result)

    reconstructed = reconstruct_benchmark_results(payload)

    assert reconstructed.applied_tuning_ledger is None
    assert reconstructed.applied_ledger_hash is None


def test_anonymized_export_drops_raw_statement_text(tmp_path) -> None:
    payload = {
        "status": "applied_unverified",
        "applied_ledger_hash": "f" * 64,
        "statements": [
            {
                "statement": "SET spark.sql.warehouse.dir=/Users/secret/abs/path",
                "phase": "session",
                "status": "executed",
                "mechanism": "spark_session_config",
                "table": None,
            },
            {
                "statement": "CREATE INDEX idx ON LINEITEM (l_orderkey)",
                "phase": "ddl",
                "status": "failed",
                "error": "disk full at /Users/secret/tmp",
            },
        ],
        "dropped": [{"intent": "partitioning:LINEITEM", "reason": "load-time only"}],
    }

    sanitized = anonymize_applied_tuning_payload(payload)

    dumped = str(sanitized)
    assert "/Users/secret" not in dumped
    assert "warehouse.dir" not in dumped
    for entry in sanitized["statements"]:
        assert "statement" not in entry
        assert "error" not in entry
        assert "table" not in entry
        assert entry["statement_redacted"] is True
    assert sanitized["applied_ledger_hash"] == "f" * 64
    assert sanitized["status"] == "applied_unverified"
    assert sanitized["statements"][0]["phase"] == "session"
    assert sanitized["statements"][0]["mechanism"] == "spark_session_config"
    assert sanitized["dropped"] == [{"redacted": True}]
    assert payload["statements"][0]["statement"].startswith("SET spark")


def test_anonymized_export_scrubs_receipt_and_dropped_free_text(tmp_path) -> None:
    payload = {
        "status": "applied_unverified",
        "statements": [],
        "dropped": [
            {
                "intent": "partitioning:private_catalog.private_schema.orders",
                "reason": "driver failed at secret.internal/private/path",
            }
        ],
        "receipt": {
            "platform": "snowflake",
            "corroborated": False,
            "summary": {"unverifiable": 1, "absent": 1, "verifiable_total": 2},
            "error": "connection to secret.internal failed for /private/cert.pem",
            "dropped": [
                {
                    "intent": "partitioning:private_catalog.private_schema.orders",
                    "reason": "nested driver failure at secret.internal",
                }
            ],
            "entries": [
                {
                    "statement": "CREATE INDEX private_idx ON private_catalog.private_schema.orders (id)",
                    "phase": "ddl",
                    "verdict": "unverifiable",
                    "reason": "introspection degraded: connection to secret.internal failed",
                    "detail": "raw driver detail at /private/cert.pem",
                },
                {
                    "statement": "CREATE INDEX private_idx ON private_catalog.private_schema.orders (id)",
                    "phase": "ddl",
                    "verdict": "absent",
                    "reason": "no index on private_catalog.private_schema.orders found in catalog",
                },
            ],
        },
    }

    sanitized = anonymize_applied_tuning_payload(payload)
    serialized = json.dumps(sanitized, sort_keys=True)

    for secret in ("secret.internal", "/private", "private_catalog", "private_schema"):
        assert secret not in serialized
    assert sanitized["receipt"]["error_redacted"] is True
    assert all(entry["reason_redacted"] is True for entry in sanitized["receipt"]["entries"])
    assert sanitized["dropped"] == [{"redacted": True}]
    assert sanitized["receipt"]["dropped"] == [{"redacted": True}]
    assert sanitized["receipt"]["corroborated"] is False
    assert sanitized["receipt"]["summary"]["verifiable_total"] == 2
