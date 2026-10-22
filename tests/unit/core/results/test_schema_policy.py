from __future__ import annotations

import pytest

from benchbox.core.results.schema_policy import (
    CURRENT_SCHEMA_VERSION,
    EXPLORER_INPUT_SCHEMA_POLICY,
    LEGACY_NORMALIZED_SCHEMA_VERSION,
    LOADER_SCHEMA_POLICY,
    NORMALIZER_SCHEMA_POLICY,
    PRODUCER_SCHEMA_POLICY,
    PUBLIC_SUBMISSION_SCHEMA_POLICY,
    RUNTIME_SCHEMA_POLICY,
    detect_normalizer_schema_version,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_producer_policy_only_accepts_current_schema_version() -> None:
    assert PRODUCER_SCHEMA_POLICY.evaluate(CURRENT_SCHEMA_VERSION).accepted

    older = PRODUCER_SCHEMA_POLICY.evaluate("2.0")
    assert not older.accepted
    assert "producer result schema policy" in older.error_message()


@pytest.mark.parametrize("version", ["2.0", "2.1", "2.2", "2.3", "2.4"])
def test_strict_runtime_policies_accept_known_v2_versions(version: str) -> None:
    assert RUNTIME_SCHEMA_POLICY.evaluate(version).accepted
    assert LOADER_SCHEMA_POLICY.evaluate(version).accepted
    assert EXPLORER_INPUT_SCHEMA_POLICY.evaluate(version).accepted


@pytest.mark.parametrize(
    ("version", "reason"),
    [
        ("2.99", "unknown-v2"),
        ("1.0", "legacy-v1"),
        (None, "missing"),
        ("", "missing"),
        ("2.x", "malformed"),
        (2.1, "malformed"),
    ],
)
def test_strict_runtime_policies_reject_unsupported_versions(version: object, reason: str) -> None:
    for policy in (RUNTIME_SCHEMA_POLICY, LOADER_SCHEMA_POLICY, EXPLORER_INPUT_SCHEMA_POLICY):
        decision = policy.evaluate(version)
        assert not decision.accepted
        assert decision.reason == reason
        assert "schema versions 2.0, 2.1, 2.2, 2.3, and 2.4" in decision.error_message()


@pytest.mark.parametrize("version", ["2.0", "2.1", "2.2", "2.99", "2.1.3"])
def test_public_submission_policy_forward_accepts_numeric_v2_family(version: str) -> None:
    assert PUBLIC_SUBMISSION_SCHEMA_POLICY.evaluate(version).accepted


@pytest.mark.parametrize("version", ["1.0", None, "", "2.x", object()])
def test_public_submission_policy_rejects_legacy_missing_and_malformed(version: object) -> None:
    decision = PUBLIC_SUBMISSION_SCHEMA_POLICY.evaluate(version)
    assert not decision.accepted
    assert "public submission schema policy" in decision.error_message()
    assert "numeric schema version family 2.x" in decision.error_message()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"version": "2.0"}, "2.0"),
        ({"version": "2.1"}, "2.1"),
        ({"version": "2.2"}, "2.2"),
        ({"version": "2.3"}, "2.3"),
        ({"schema_version": "2.0"}, "2.0"),
        ({"version": "2.99"}, LEGACY_NORMALIZED_SCHEMA_VERSION),
        ({"version": "1.0"}, LEGACY_NORMALIZED_SCHEMA_VERSION),
        ({}, LEGACY_NORMALIZED_SCHEMA_VERSION),
        ({"version": "2.x"}, LEGACY_NORMALIZED_SCHEMA_VERSION),
    ],
)
def test_normalizer_policy_preserves_legacy_fallback(data: dict, expected: str) -> None:
    assert detect_normalizer_schema_version(data) == expected


def test_normalizer_policy_names_fallback_reason_for_unknown_v2() -> None:
    decision = NORMALIZER_SCHEMA_POLICY.evaluate("2.99")

    assert decision.accepted
    assert decision.normalized_version == LEGACY_NORMALIZED_SCHEMA_VERSION
    assert decision.reason == "legacy-fallback-unknown-v2"


def test_producer_payload_boundary_redacts_platform_metadata_sentinels() -> None:

    import json
    from datetime import datetime

    from benchbox.core.results.models import BenchmarkResults
    from benchbox.core.results.schema import build_result_payload

    gates = (
        "RAW_CONFIG_GATE",
        "RAW_METADATA_GATE",
        "DEPLOYMENT_GATE",
        "CLOUD_GATE",
        "COMPUTE_GATE",
        "STORAGE_GATE",
    )
    result = BenchmarkResults(
        benchmark_name="synthetic",
        platform="synthetic",
        scale_factor=1.0,
        execution_id="schema-policy-gate",
        timestamp=datetime.now(),
        duration_seconds=0.1,
        total_queries=0,
        successful_queries=0,
        failed_queries=0,
        platform_info={"sort_key": "o_orderkey", "threads": 4},
        platform_raw_config={"password": "RAW_CONFIG_GATE"},
        platform_raw_metadata={"password": "RAW_METADATA_GATE"},
        platform_deployment={"token": "DEPLOYMENT_GATE"},
        platform_cloud={"access_key": "CLOUD_GATE"},
        platform_compute={"connection_string": "COMPUTE_GATE"},
        platform_storage={"secret": "STORAGE_GATE"},
    )
    text = json.dumps(build_result_payload(result), default=str)
    for gate in gates:
        assert gate not in text, f"producer payload leaked {gate}"
    assert "o_orderkey" in text
