from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from benchbox.core.tpcds.power_test import TPCDSPowerTest
from benchbox.core.tpcds.throughput_test import TPCDSThroughputTest, TPCDSThroughputTestConfig
from benchbox.core.tpch.platform_power import _power_query_result
from benchbox.core.tpch.throughput_test import TPCHThroughputTest, TPCHThroughputTestConfig
from benchbox.platforms.base.connection_wrappers import PlatformAdapterCursor

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PASSED = {"status": "PASSED", "expected": 3, "actual": 3}
FAILED = {"status": "FAILED", "expected": 3, "actual": 4, "error": "row count mismatch"}


class _EvidenceConnection:
    def __init__(self, evidence: dict[str, Any]) -> None:
        self.evidence = evidence

    def execute(self, _query: str) -> PlatformAdapterCursor:
        result: dict[str, Any] = {
            "status": "FAILED" if self.evidence["status"] == "FAILED" else "SUCCESS",
            "rows_returned": self.evidence["actual"],
            "first_row": (1,),
            "row_count_validation": dict(self.evidence),
        }
        if self.evidence["status"] == "FAILED":
            result["error"] = self.evidence["error"]
        return PlatformAdapterCursor(result)

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None


@dataclass
class _StreamQuery:
    query_id: int
    variant: str | None = None


def _tpcds_power_rows(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    benchmark = Mock()
    benchmark.get_query = Mock(return_value="SELECT 1")
    power_test = TPCDSPowerTest(
        benchmark=benchmark,
        connection_factory=lambda: _EvidenceConnection(evidence),
        scale_factor=1.0,
        warm_up=False,
        query_subset=["3"],
    )
    power_test._preflight_validate_generation = lambda _ids: None
    return power_test.run().query_results


@pytest.mark.parametrize("evidence", [PASSED, FAILED], ids=["passed", "failed"])
def test_tpcds_power_query_result_keeps_platform_row_count_evidence(evidence: dict[str, Any]) -> None:
    (query_result,) = _tpcds_power_rows(evidence)

    assert query_result["success"] is (evidence["status"] == "PASSED")
    assert query_result["row_count_validation"] == evidence
    platform_row = _power_query_result(query_result, stream_id=0, iteration=1, run_type="measurement")
    assert platform_row["row_count_validation"] == evidence


@pytest.mark.parametrize("evidence", [PASSED, FAILED], ids=["passed", "failed"])
def test_tpcds_throughput_stream_query_keeps_platform_row_count_evidence(evidence: dict[str, Any]) -> None:
    test = TPCDSThroughputTest(
        benchmark=SimpleNamespace(get_query=lambda *_args, **_kwargs: "SELECT 1"),
        connection_factory=lambda: _EvidenceConnection(evidence),
        num_streams=1,
    )
    test._pregenerated_queries = {0: [(_StreamQuery(query_id=3), "SELECT 1")]}

    stream = test._execute_stream(0, 42, TPCDSThroughputTestConfig(num_streams=1))

    (query_result,) = stream.query_results
    assert query_result["success"] is (evidence["status"] == "PASSED")
    assert query_result["row_count_validation"] == evidence


@pytest.mark.parametrize("evidence", [PASSED, FAILED], ids=["passed", "failed"])
def test_tpch_throughput_stream_query_keeps_platform_row_count_evidence(evidence: dict[str, Any]) -> None:
    test = TPCHThroughputTest(
        benchmark=SimpleNamespace(get_query=lambda query_id, **_kwargs: f"SELECT {query_id}"),
        connection_factory=lambda: _EvidenceConnection(evidence),
        num_streams=1,
    )

    stream = test._execute_stream(0, 42, TPCHThroughputTestConfig(num_streams=1))

    assert len(stream.query_results) == 22
    assert all(query_result["row_count_validation"] == evidence for query_result in stream.query_results)
