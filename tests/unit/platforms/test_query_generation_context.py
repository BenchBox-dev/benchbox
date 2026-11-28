from __future__ import annotations

from typing import Any

import pytest

from benchbox.core.expected_results.models import row_count_validation_record
from benchbox.core.validation.query_validation import QueryValidator
from benchbox.core.validation.stream_checks import QueryGeneration, current_query_generation
from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _ValidatingAdapter:
    def __init__(self) -> None:
        self.seen: list[QueryGeneration | None] = []

    def get_target_dialect(self) -> str:
        return "duckdb"

    def execute_query(self, connection: Any, query: str, query_id: str, **kwargs: Any) -> dict[str, Any]:
        self.seen.append(current_query_generation())
        result = QueryValidator().validate_query_result(
            kwargs["benchmark_type"], query_id, 5, kwargs["scale_factor"], kwargs["stream_id"]
        )
        return {
            "query_id": query_id,
            "status": "SUCCESS",
            "rows_returned": 5,
            "row_count_validation": row_count_validation_record(result, 5),
        }


def test_the_generation_set_for_a_query_reaches_the_validator_and_the_evidence() -> None:
    adapter = _ValidatingAdapter()
    connection = PlatformAdapterConnection(object(), adapter)
    connection.benchmark_type = "tpcds"
    connection.scale_factor = 1.0

    connection.set_query_context("31", stream_id=1, generation=QueryGeneration(seed=42, streams_index=1))
    first = connection.execute("SELECT 1").platform_result["row_count_validation"]
    connection.set_query_context("31", stream_id=1)
    second = connection.execute("SELECT 1").platform_result["row_count_validation"]

    assert adapter.seen == [QueryGeneration(seed=42, streams_index=1), None]
    assert (first["seed"], first["streams_index"]) == (42, 1)
    assert (second["seed"], second["streams_index"]) == (None, None)
    assert current_query_generation() is None
