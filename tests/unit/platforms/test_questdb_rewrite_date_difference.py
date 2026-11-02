from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]

QUESTDB_HTTP_URL = os.environ.get("BENCHBOX_QUESTDB_HTTP_URL")


def _scalar(sql: str) -> float:
    url = f"{QUESTDB_HTTP_URL}/exec?{urllib.parse.urlencode({'query': sql})}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)["dataset"][0][0]


def test_epoch_difference_does_not_use_absolute_datediff():
    rewritten = rewrite_query("SELECT EXTRACT(EPOCH FROM (dropoff - pickup)) FROM trips")
    assert "datediff" not in rewritten.lower()
    assert "CAST(CAST(dropoff AS TIMESTAMP) AS BIGINT) - CAST(CAST(pickup AS TIMESTAMP) AS BIGINT)" in rewritten


def test_day_difference_promotes_date_operands_to_timestamp_micros():
    rewritten = rewrite_query("SELECT EXTRACT(EPOCH FROM (CAST(a AS DATE) - CAST(b AS DATE))) / 86400 FROM t")
    assert "CAST(CAST(a AS DATE) AS TIMESTAMP)" in rewritten
    assert "86400000000.0" in rewritten


@pytest.mark.skipif(QUESTDB_HTTP_URL is None, reason="BENCHBOX_QUESTDB_HTTP_URL not set")
@pytest.mark.parametrize(
    ("pickup", "dropoff", "expected"),
    [
        ("'2024-01-01T00:00:10.000000Z'::timestamp", "'2024-01-01T00:00:00.000000Z'::timestamp", -10.0),
        ("'2024-01-01T00:00:00.000000Z'::timestamp", "'2024-01-01T00:00:10.500000Z'::timestamp", 10.5),
    ],
)
def test_rewritten_epoch_difference_keeps_sign_on_questdb(pickup, dropoff, expected):
    rewritten = rewrite_query(f"SELECT EXTRACT(EPOCH FROM ({dropoff} - {pickup}))")
    assert _scalar(rewritten) == expected


@pytest.mark.skipif(QUESTDB_HTTP_URL is None, reason="BENCHBOX_QUESTDB_HTTP_URL not set")
def test_rewritten_day_difference_keeps_sign_for_date_operands_on_questdb():
    rewritten = rewrite_query(
        "SELECT EXTRACT(EPOCH FROM (CAST('2024-01-01' AS DATE) - CAST('2024-01-03' AS DATE))) / 86400"
    )
    assert _scalar(rewritten) == -2.0
