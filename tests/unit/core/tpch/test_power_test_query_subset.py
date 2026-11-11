from __future__ import annotations

import pytest

from benchbox.core.tpch.power_test import _parse_tpch_query_id

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Q1", 1), ("q6", 6), ("14", 14), (22, 22), (" Q7 ", 7)],
)
def test_parse_accepts_bare_and_q_prefixed_ids(raw, expected):
    assert _parse_tpch_query_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "Q", "Qx", "1.5", "query1"])
def test_parse_rejects_invalid_ids(raw):
    with pytest.raises(ValueError, match="Invalid TPC-H query id"):
        _parse_tpch_query_id(raw)


