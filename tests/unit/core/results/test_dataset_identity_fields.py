from __future__ import annotations

import pytest

from benchbox.core.data_fetch import load_manifest
from benchbox.core.results.builder import BenchmarkInfoInput, PlatformInfoInput, ResultBuilder
from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.schema import build_result_payload

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _result(benchmark_id: str):
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(
            name=benchmark_id,
            display_name=benchmark_id,
            scale_factor=1.0,
            test_type="standard",
            benchmark_id=benchmark_id,
        ),
        platform=PlatformInfoInput(name="duckdb"),
    )
    builder.add_query_result(
        normalize_query_result(
            {
                "query_id": "1a",
                "execution_time_seconds": 0.001,
                "rows_returned": 1,
                "status": "SUCCESS",
                "iteration": 1,
                "stream_id": 0,
                "run_type": "measurement",
            }
        )
    )
    return builder.build()


def test_joinorder_result_payload_records_dataset_identity() -> None:
    payload = build_result_payload(_result("joinorder"))
    manifest = load_manifest("benchbox/core/joinorder/data_manifest.toml")

    assert payload["benchmark"]["dataset_version"] == manifest.dataset_version
    assert payload["benchmark"]["manifest_hash"] == manifest.manifest_hash
    assert payload["benchmark"]["data_archive_hash"] == manifest.data_archive_hash


def test_generated_benchmark_payload_omits_dataset_identity() -> None:
    payload = build_result_payload(_result("tpch"))

    assert "dataset_version" not in payload["benchmark"]
    assert "manifest_hash" not in payload["benchmark"]
    assert "data_archive_hash" not in payload["benchmark"]


def test_reexport_preserves_captured_dataset_identity() -> None:
    payload = build_result_payload(_result("joinorder"))
    payload["benchmark"]["dataset_version"] = "joinorder-imdb-2013-v0"
    payload["benchmark"]["manifest_hash"] = "old-manifest-hash"
    payload["benchmark"]["data_archive_hash"] = "old-archive-hash"

    reconstructed = reconstruct_benchmark_results(payload)
    reexported = build_result_payload(reconstructed)

    assert reexported["benchmark"]["dataset_version"] == "joinorder-imdb-2013-v0"
    assert reexported["benchmark"]["manifest_hash"] == "old-manifest-hash"
    assert reexported["benchmark"]["data_archive_hash"] == "old-archive-hash"


