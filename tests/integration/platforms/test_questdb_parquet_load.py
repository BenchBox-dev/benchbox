# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import io

import pytest
import requests

from .conftest import skip_unless_docker_service

QUESTDB_HOST = "localhost"
QUESTDB_HTTP_PORT = 19000

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_questdb,
]

TABLE_NAME = "benchbox_imp_chunk_test"


@pytest.fixture(scope="module")
def questdb_http_url():
    skip_unless_docker_service(QUESTDB_HOST, QUESTDB_HTTP_PORT, platform="QuestDB")
    return f"http://{QUESTDB_HOST}:{QUESTDB_HTTP_PORT}"


def _exec(base_url: str, sql: str) -> dict:
    resp = requests.get(f"{base_url}/exec", params={"query": sql}, timeout=15)
    resp.raise_for_status()
    result = resp.json()
    if "error" in result:
        raise RuntimeError(f"QuestDB /exec error for {sql!r}: {result['error']}")
    return result


def _imp(base_url: str, csv_text: str, *, overwrite: str = "false", force_header: str = "true") -> str:
    params = {
        "name": TABLE_NAME,
        "overwrite": overwrite,
        "durable": "true",
        "delimiter": ",",
        "forceHeader": force_header,
    }
    buf = io.BytesIO(csv_text.encode())
    files = {"data": (f"{TABLE_NAME}.csv", buf, "text/csv")}
    resp = requests.post(f"{base_url}/imp", params=params, files=files, timeout=30)
    resp.raise_for_status()
    return resp.text


@pytest.fixture(autouse=True)
def drop_test_table(questdb_http_url):
    _exec(questdb_http_url, f"DROP TABLE IF EXISTS {TABLE_NAME}")
    yield
    _exec(questdb_http_url, f"DROP TABLE IF EXISTS {TABLE_NAME}")


class TestQuestDBImpTwoChunkImport:
    def test_two_chunks_with_force_header_correct_columns(self, questdb_http_url):
        chunk1 = "id,name,val\n1,alpha,1.1\n2,beta,2.2\n3,gamma,3.3\n"
        chunk2 = "id,name,val\n4,delta,4.4\n5,epsilon,5.5\n6,zeta,6.6\n"

        _imp(questdb_http_url, chunk1, overwrite="false", force_header="true")
        _imp(questdb_http_url, chunk2, overwrite="false", force_header="true")

        result = _exec(questdb_http_url, f"SELECT id, name, val FROM {TABLE_NAME} ORDER BY id")
        rows = result.get("dataset", [])

        assert len(rows) == 6, f"Expected 6 rows, got {len(rows)}: {rows}"

        assert rows[0][0] == 1 and rows[0][1] == "alpha"
        assert rows[1][0] == 2 and rows[1][1] == "beta"
        assert rows[2][0] == 3 and rows[2][1] == "gamma"

        assert rows[3][0] == 4 and rows[3][1] == "delta"
        assert rows[4][0] == 5 and rows[4][1] == "epsilon"
        assert rows[5][0] == 6 and rows[5][1] == "zeta"

    def test_val_column_not_shifted_to_id(self, questdb_http_url):
        chunk1 = "id,name,val\n10,first,9.9\n"
        chunk2 = "id,name,val\n20,second,8.8\n"

        _imp(questdb_http_url, chunk1, overwrite="false", force_header="true")
        _imp(questdb_http_url, chunk2, overwrite="false", force_header="true")

        result = _exec(questdb_http_url, f"SELECT id, name, val FROM {TABLE_NAME} ORDER BY id")
        rows = result.get("dataset", [])

        assert len(rows) == 2
        assert rows[0][0] == 10, f"id column shifted: got {rows[0][0]!r}, expected 10"
        assert rows[0][2] == pytest.approx(9.9, rel=1e-5)
        assert rows[1][0] == 20, f"id column shifted: got {rows[1][0]!r}, expected 20"
        assert rows[1][2] == pytest.approx(8.8, rel=1e-5)

    def test_three_chunks_all_columns_correct(self, questdb_http_url):
        chunk1 = "id,name,val\n1,alpha,1.1\n"
        chunk2 = "id,name,val\n2,beta,2.2\n"
        chunk3 = "id,name,val\n3,gamma,3.3\n"

        _imp(questdb_http_url, chunk1, overwrite="false", force_header="true")
        _imp(questdb_http_url, chunk2, overwrite="false", force_header="true")
        _imp(questdb_http_url, chunk3, overwrite="false", force_header="true")

        result = _exec(questdb_http_url, f"SELECT id, name, val FROM {TABLE_NAME} ORDER BY id")
        rows = result.get("dataset", [])

        assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}: {rows}"
        assert rows[0] == [1, "alpha", pytest.approx(1.1, rel=1e-5)]
        assert rows[1] == [2, "beta", pytest.approx(2.2, rel=1e-5)]
        assert rows[2] == [3, "gamma", pytest.approx(3.3, rel=1e-5)]


