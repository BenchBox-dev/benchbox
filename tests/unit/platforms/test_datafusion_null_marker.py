from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
duckdb = pytest.importorskip("duckdb")

_ROWS = "1|__NULL__|10\n2||20\n3|hello|__NULL__\n4||__NULL__\n5|__NULL__|50\n"
_COLUMNS = [
    {"name": "id", "type": "BIGINT"},
    {"name": "searchphrase", "type": "VARCHAR"},
    {"name": "counter", "type": "BIGINT"},
]


def _load_with_adapter(tmp_path, data_path, benchmark):
    from benchbox.platforms.datafusion import DataFusionAdapter

    with patch("benchbox.platforms.datafusion.SessionContext"):
        adapter = DataFusionAdapter(working_dir=str(tmp_path / "wd"), data_format="parquet")
    adapter._table_schemas = {"hits": {"name": "hits", "columns": _COLUMNS}}
    rows = adapter._convert_and_register_parquet(Mock(), "hits", [data_path], pa, pq, benchmark=benchmark)
    return rows, pq.read_table(adapter.working_dir / "hits.parquet").to_pylist()


def test_clickbench_null_marker_distinguishes_empty_string_from_null(tmp_path):
    data_path = tmp_path / "hits.csv"
    data_path.write_text(_ROWS)
    benchmark = SimpleNamespace(csv_delimiter="|", csv_null_marker="__NULL__", csv_has_header=False)

    row_count, loaded = _load_with_adapter(tmp_path, data_path, benchmark)

    con = duckdb.connect()
    expected = con.execute(
        "SELECT * FROM read_csv(?, delim='|', header=false, nullstr='__NULL__', "
        "columns={'id': 'BIGINT', 'searchphrase': 'VARCHAR', 'counter': 'BIGINT'}) ORDER BY id",
        [str(data_path)],
    ).fetchall()

    assert row_count == len(expected)
    assert [(r["id"], r["searchphrase"], r["counter"]) for r in sorted(loaded, key=lambda r: r["id"])] == expected
    assert [r["searchphrase"] for r in sorted(loaded, key=lambda r: r["id"])] == [None, "", "hello", "", None]
