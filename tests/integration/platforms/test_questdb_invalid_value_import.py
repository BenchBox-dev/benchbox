import pytest
import requests

from benchbox.platforms.base.data_loading import CsvDialect
from benchbox.platforms.questdb import QuestDBAdapter

from .conftest import skip_unless_docker_service

QUESTDB_HOST = "localhost"
QUESTDB_HTTP_PORT = 19000

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_questdb,
]

TABLE_NAME = "benchbox_invalid_value_test"
DIALECT = CsvDialect(",", True, None, False, None)


@pytest.fixture(scope="module")
def base_url():
    skip_unless_docker_service(QUESTDB_HOST, QUESTDB_HTTP_PORT, platform="QuestDB")
    return f"http://{QUESTDB_HOST}:{QUESTDB_HTTP_PORT}"


def _exec(base_url: str, sql: str) -> dict:
    resp = requests.get(f"{base_url}/exec", params={"query": sql}, timeout=15)
    resp.raise_for_status()
    result = resp.json()
    if "error" in result:
        raise RuntimeError(f"QuestDB /exec error for {sql!r}: {result['error']}")
    return result


@pytest.fixture(autouse=True)
def table(base_url):
    _exec(base_url, f"DROP TABLE IF EXISTS {TABLE_NAME}")
    _exec(
        base_url,
        f"CREATE TABLE {TABLE_NAME} (i INT, l LONG, d DOUBLE, dt DATE, ts TIMESTAMP, s SYMBOL, name VARCHAR)",
    )
    yield
    _exec(base_url, f"DROP TABLE IF EXISTS {TABLE_NAME}")


def _adapter() -> QuestDBAdapter:
    return QuestDBAdapter(host=QUESTDB_HOST, http_port=QUESTDB_HTTP_PORT)


def _load(tmp_path, body: str) -> int:
    data_file = tmp_path / f"{TABLE_NAME}.csv"
    data_file.write_text("i,l,d,dt,ts,s,name\n" + body, encoding="utf-8")
    return _adapter()._load_table_via_rest_api(TABLE_NAME, data_file, DIALECT)


def test_valid_rows_load_unchanged(tmp_path, base_url):
    rows = _load(
        tmp_path,
        "1,10,1.5,2020-01-01,2020-01-01,a,x\n2,20,2.5,2020-01-02,2020-01-02,b,y\n",
    )

    assert rows == 2
    result = _exec(base_url, f"SELECT count(), count(i), count(d), count(dt) FROM {TABLE_NAME}")
    assert result["dataset"] == [[2, 2, 2, 2]]


@pytest.mark.parametrize(
    ("bad_row", "column", "type_name", "value"),
    [
        ("abc,10,1.5,2020-01-01,2020-01-01,a,x", "i", "INT", "abc"),
        ("99999999999,10,1.5,2020-01-01,2020-01-01,a,x", "i", "INT", "99999999999"),
        ("1,abc,1.5,2020-01-01,2020-01-01,a,x", "l", "LONG", "abc"),
        ("1,10,abc,2020-01-01,2020-01-01,a,x", "d", "DOUBLE", "abc"),
        ("1,10,1.5,notadate,2020-01-01,a,x", "dt", "DATE", "notadate"),
        ("1,10,1.5,2020-01-01,notadate,a,x", "ts", "TIMESTAMP", "notadate"),
    ],
)
def test_invalid_value_fails_naming_row_and_column(tmp_path, bad_row, column, type_name, value):
    good = "1,10,1.5,2020-01-01,2020-01-01,a,x\n"

    with pytest.raises(RuntimeError) as excinfo:
        _load(tmp_path, good + bad_row + "\n" + good)

    message = str(excinfo.value)
    assert f"column '{column}' ({type_name})" in message
    assert "row 2" in message
    assert repr(value) in message


def test_empty_values_remain_valid_nulls(tmp_path, base_url):
    rows = _load(tmp_path, "1,10,1.5,2020-01-01,2020-01-01,a,x\n,,,,,,\n")

    assert rows == 2
    result = _exec(base_url, f"SELECT count(i), count(d) FROM {TABLE_NAME}")
    assert result["dataset"] == [[1, 1]]


