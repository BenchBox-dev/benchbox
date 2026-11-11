from __future__ import annotations

import pytest

from benchbox.core.benchmark_registry import get_benchmark_metadata

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize(
    ("benchmark_name", "expected"),
    [
        ("read_primitives", True),
        ("write_primitives", True),
        ("tpcds_obt", True),
        ("tpcdi", True),
    ],
)
def test_supports_dataframe_flags(benchmark_name: str, expected: bool) -> None:
    metadata = get_benchmark_metadata(benchmark_name)
    assert metadata is not None
    assert metadata.get("supports_dataframe") is expected


