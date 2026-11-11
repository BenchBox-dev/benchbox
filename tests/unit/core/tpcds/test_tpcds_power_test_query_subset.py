from __future__ import annotations

import pytest

from benchbox.core.tpcds.power_test import (
    TPCDSPowerTest,
    TPCDSPowerTestConfig,
    _parse_tpcds_query_id,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Q10", 10), ("q7", 7), ("42", 42), (99, 99), (" Q3 ", 3)],
)
def test_parse_accepts_bare_and_q_prefixed_ids(raw, expected):
    assert _parse_tpcds_query_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "Q", "Qx", "1.5", "query1"])
def test_parse_rejects_invalid_ids(raw):
    with pytest.raises(ValueError, match="Invalid TPC-DS query id"):
        _parse_tpcds_query_id(raw)


def test_build_queries_resolves_q_prefixed_subset():
    import logging

    test = TPCDSPowerTest.__new__(TPCDSPowerTest)
    test.config = TPCDSPowerTestConfig(query_subset=["Q10", "7"])
    test.logger = logging.getLogger(__name__)
    assert test._build_queries_to_execute([10]) == [(10, None), (7, None)]


