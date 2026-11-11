from __future__ import annotations

from datetime import datetime

import pytest

from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import CANONICAL_KEY_ORDER, build_result_payload

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _result(**kwargs: object) -> BenchmarkResults:
    return BenchmarkResults(
        benchmark_name="tpch",
        platform="duckdb",
        scale_factor=1.0,
        execution_id="exec-1",
        timestamp=datetime(2026, 1, 1),
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        **kwargs,
    )


class TestProvenanceBlock:
    def test_provenance_in_canonical_key_order(self) -> None:
        assert "provenance" in CANONICAL_KEY_ORDER

    def test_absent_when_no_funding_or_source(self) -> None:
        payload = build_result_payload(_result())
        assert "provenance" not in payload

    def test_funding_only(self) -> None:
        payload = build_result_payload(_result(funding="personal"))
        assert payload["provenance"] == {"funding": "personal"}

    def test_source_only(self) -> None:
        payload = build_result_payload(_result(result_source="vendor"))
        assert payload["provenance"] == {"source": "vendor"}

    def test_funding_and_source(self) -> None:
        payload = build_result_payload(_result(funding="free-trial", result_source="community"))
        assert payload["provenance"] == {"funding": "free-trial", "source": "community"}

    def test_values_are_normalized(self) -> None:
        payload = build_result_payload(_result(funding="  PERSONAL ", result_source="Vendor"))
        assert payload["provenance"] == {"funding": "personal", "source": "vendor"}

    def test_unknown_funding_normalizes_to_unspecified(self) -> None:
        payload = build_result_payload(_result(funding="crowdfunded"))
        assert payload["provenance"] == {"funding": "unspecified"}

    def test_no_empty_block_emitted(self) -> None:

        payload = build_result_payload(_result(funding="", result_source=None))
        assert "provenance" not in payload


