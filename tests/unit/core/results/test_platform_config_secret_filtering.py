# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
from datetime import datetime

import pytest

from benchbox.core.results.database import ResultDatabase
from benchbox.core.results.environment import build_platform_metadata_payload
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.platform_options import REDACTED_VALUE
from benchbox.core.results.schema import _extract_platform_config, build_result_payload

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_RAW_CONFIG_GATE = "RAW_CONFIG_GATE"
_RAW_METADATA_GATE = "RAW_METADATA_GATE"
_DEPLOYMENT_GATE = "DEPLOYMENT_GATE"
_CLOUD_GATE = "CLOUD_GATE"
_COMPUTE_GATE = "COMPUTE_GATE"
_STORAGE_GATE = "STORAGE_GATE"
_ALL_GATES = (
    _RAW_CONFIG_GATE,
    _RAW_METADATA_GATE,
    _DEPLOYMENT_GATE,
    _CLOUD_GATE,
    _COMPUTE_GATE,
    _STORAGE_GATE,
)


def _multi_source_result(*, execution_id: str = "multi-source-gate") -> BenchmarkResults:
    return BenchmarkResults(
        benchmark_name="synthetic",
        platform="synthetic",
        scale_factor=1.0,
        execution_id=execution_id,
        timestamp=datetime.now(),
        duration_seconds=0.1,
        total_queries=0,
        successful_queries=0,
        failed_queries=0,
        platform_info={"sort_key": "o_orderkey", "threads": 4, "partition_key": "l_orderkey"},
        platform_raw_config={
            "password": _RAW_CONFIG_GATE,
            "threads": 4,
            "sort_key": "o_orderkey",
        },
        platform_raw_metadata={
            "password": _RAW_METADATA_GATE,
            "partition_key": "l_orderkey",
        },
        platform_deployment={"token": _DEPLOYMENT_GATE, "connection_mode": "embedded"},
        platform_cloud={"access_key": _CLOUD_GATE, "region": "us-east-1"},
        platform_compute={"connection_string": _COMPUTE_GATE, "warehouse": "BENCH_WH"},
        platform_storage={"secret": _STORAGE_GATE, "bucket": "bench-bucket"},
    )


class TestExtractPlatformConfigFiltering:
    def test_password_sentinel_is_filtered(self):
        out = _extract_platform_config({"configuration": {"password": "PW-SENTINEL"}})
        assert "PW-SENTINEL" not in json.dumps(out)
        assert out["password"] == REDACTED_VALUE

    def test_nested_secret_is_filtered(self):
        out = _extract_platform_config({"configuration": {"s3": {"secret_key": "SK-SENTINEL"}}})
        assert "SK-SENTINEL" not in json.dumps(out)

    def test_non_secret_config_keeps_real_values(self):
        out = _extract_platform_config({"configuration": {"memory_limit": "8GB", "threads": 4}})
        assert out["memory_limit"] == "8GB"
        assert out["threads"] == 4


class TestPlatformMetadataBoundaryFiltering:
    def test_all_sources_filtered_at_payload_builder(self):
        payload = build_platform_metadata_payload(
            platform_info={"sort_key": "o_orderkey", "threads": 4},
            platform_config=None,
            deployment={"token": _DEPLOYMENT_GATE, "connection_mode": "embedded"},
            cloud={"access_key": _CLOUD_GATE, "region": "us-east-1"},
            compute={"connection_string": _COMPUTE_GATE, "warehouse": "BENCH_WH"},
            storage={"secret": _STORAGE_GATE, "bucket": "bench-bucket"},
            raw_config={"password": _RAW_CONFIG_GATE, "threads": 4, "sort_key": "o_orderkey"},
            raw_metadata={"password": _RAW_METADATA_GATE, "partition_key": "l_orderkey"},
        )
        serialized = json.dumps(payload, default=str)
        for gate in _ALL_GATES:
            assert gate not in serialized, f"builder leaked {gate}"

        assert payload["raw_config"]["password"] == REDACTED_VALUE
        assert payload["raw_config"]["threads"] == 4
        assert payload["raw_config"]["sort_key"] == "o_orderkey"
        assert payload["raw_metadata"]["password"] == REDACTED_VALUE
        assert payload["raw_metadata"]["partition_key"] == "l_orderkey"
        assert payload["deployment"]["token"] == REDACTED_VALUE
        assert payload["deployment"]["connection_mode"] == "embedded"
        assert payload["cloud"]["access_key"] == REDACTED_VALUE
        assert payload["cloud"]["region"] == "us-east-1"
        assert payload["compute"]["connection_string"] == REDACTED_VALUE
        assert payload["compute"]["warehouse"] == "BENCH_WH"
        assert payload["storage"]["secret"] == REDACTED_VALUE
        assert payload["storage"]["bucket"] == "bench-bucket"

    def test_sanitize_flag_cannot_disable_any_block(self):
        payload = build_platform_metadata_payload(
            platform_info=None,
            platform_config=None,
            deployment={"token": _DEPLOYMENT_GATE},
            cloud={"access_key": _CLOUD_GATE},
            compute={"connection_string": _COMPUTE_GATE},
            storage={"secret": _STORAGE_GATE},
            raw_config={"password": _RAW_CONFIG_GATE},
            raw_metadata={"password": _RAW_METADATA_GATE},
            sanitize_raw_config=False,
        )
        serialized = json.dumps(payload, default=str)
        for gate in _ALL_GATES:
            assert gate not in serialized, f"opt-out path leaked {gate}"

    def test_public_result_payload_redacts_all_sources(self):
        text = json.dumps(build_result_payload(_multi_source_result()), default=str)
        for gate in _ALL_GATES:
            assert gate not in text, f"public payload leaked {gate}"
        assert "o_orderkey" in text
        assert "threads" in text

    def test_private_export_redacts_all_sources(self, tmp_path):
        exporter = ResultExporter(output_dir=tmp_path / "export", anonymize=False)
        exported = exporter.export_result(_multi_source_result(execution_id="private-export"), formats=["json"])
        text = exported["json"].read_text(encoding="utf-8")
        for gate in _ALL_GATES:
            assert gate not in text, f"private export leaked {gate}"
        assert "BENCH_WH" in text
        assert "bench-bucket" in text

    def test_results_db_never_persists_source_sentinels(self, tmp_path):
        db = ResultDatabase(db_path=tmp_path / "results.db")
        db.store_result(_multi_source_result(execution_id="db-multi-source"))
        raw = (tmp_path / "results.db").read_bytes()
        for gate in _ALL_GATES:
            assert gate.encode() not in raw, f"results.db leaked {gate}"

        stored = db.get_result("db-multi-source")
        assert stored is not None
        platform = stored.metadata.get("platform") or {}
        assert platform.get("raw_config", {}).get("password") == REDACTED_VALUE
        assert platform.get("raw_metadata", {}).get("password") == REDACTED_VALUE
        assert platform.get("deployment", {}).get("token") == REDACTED_VALUE
        assert platform.get("cloud", {}).get("access_key") == REDACTED_VALUE
        assert platform.get("compute", {}).get("connection_string") == REDACTED_VALUE
        assert platform.get("storage", {}).get("secret") == REDACTED_VALUE
        assert platform.get("raw_config", {}).get("sort_key") == "o_orderkey"
        assert platform.get("raw_config", {}).get("threads") == 4
        assert platform.get("compute", {}).get("warehouse") == "BENCH_WH"
        assert platform.get("storage", {}).get("bucket") == "bench-bucket"


class TestResultsDbMetadataFiltering:
    def test_explicit_raw_config_is_filtered_at_metadata_boundary(self):
        payload = build_platform_metadata_payload(
            platform_info=None,
            platform_config=None,
            deployment=None,
            cloud=None,
            compute=None,
            storage=None,
            raw_config={"password": "RAW-PASSWORD-SENTINEL", "threads": 4},
            raw_metadata=None,
        )

        assert payload["raw_config"]["password"] == REDACTED_VALUE
        assert payload["raw_config"]["threads"] == 4

    def test_explicit_raw_config_cannot_disable_boundary_filtering(self):
        payload = build_platform_metadata_payload(
            platform_info=None,
            platform_config=None,
            deployment=None,
            cloud=None,
            compute=None,
            storage=None,
            raw_config={"password": "RAW-PASSWORD-SENTINEL", "threads": 4},
            raw_metadata=None,
            sanitize_raw_config=False,
        )

        assert payload["raw_config"]["password"] == REDACTED_VALUE
        assert payload["raw_config"]["threads"] == 4

    def test_platform_info_secret_never_reaches_metadata_json(self, tmp_path):
        result = BenchmarkResults(
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=0.01,
            execution_id="exec-filter-test",
            timestamp=datetime.now(),
            duration_seconds=1.0,
            total_queries=1,
            successful_queries=1,
            failed_queries=0,
            platform_info={"name": "duckdb", "api_key": "DBKEY-SENTINEL"},
        )
        db = ResultDatabase(db_path=tmp_path / "results.db")
        db.store_result(result)
        raw = (tmp_path / "results.db").read_bytes()
        assert b"DBKEY-SENTINEL" not in raw


