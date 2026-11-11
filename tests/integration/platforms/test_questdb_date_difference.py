from __future__ import annotations

import pytest
import requests

from benchbox.platforms.questdb_rewrites import rewrite_query

from .conftest import get_env_or_skip

pytestmark = [pytest.mark.integration, pytest.mark.live_integration, pytest.mark.live_questdb]


@pytest.fixture(scope="module")
def questdb_http_url():
    return get_env_or_skip("BENCHBOX_QUESTDB_HTTP_URL", "QuestDB")


def _scalar(base_url: str, sql: str) -> float:
    response = requests.get(f"{base_url}/exec", params={"query": sql}, timeout=30)
    response.raise_for_status()
    return response.json()["dataset"][0][0]


@pytest.mark.parametrize(
    ("pickup", "dropoff", "expected"),
    [
        ("'2024-01-01T00:00:10.000000Z'::timestamp", "'2024-01-01T00:00:00.000000Z'::timestamp", -10.0),
        ("'2024-01-01T00:00:00.000000Z'::timestamp", "'2024-01-01T00:00:10.500000Z'::timestamp", 10.5),
        ("'2024-01-01T00:00:00.000000Z'::timestamp", "'2024-01-01T00:00:00.000001Z'::timestamp", 0.000001),
        ("'2024-01-01T00:00:00.000001Z'::timestamp", "'2024-01-01T00:00:00.000000Z'::timestamp", -0.000001),
        ("'2024-01-01T00:00:00.000000Z'::timestamp", "'2024-01-01T00:00:00.000000Z'::timestamp", 0.0),
    ],
)
def test_rewritten_epoch_difference_keeps_sign_on_questdb(questdb_http_url, pickup, dropoff, expected):
    rewritten = rewrite_query(f"SELECT EXTRACT(EPOCH FROM ({dropoff} - {pickup}))")
    assert _scalar(questdb_http_url, rewritten) == expected


def test_rewritten_day_difference_keeps_sign_for_date_operands_on_questdb(questdb_http_url):
    rewritten = rewrite_query(
        "SELECT EXTRACT(EPOCH FROM (CAST('2024-01-01' AS DATE) - CAST('2024-01-03' AS DATE))) / 86400"
    )
    assert _scalar(questdb_http_url, rewritten) == -2.0


