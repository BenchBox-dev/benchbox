from pathlib import Path
from unittest.mock import Mock

import pytest

from benchbox.platforms import singlestore
from benchbox.platforms.singlestore import SingleStoreAdapter, _parquet_load_chunks

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def parquet_path(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({"id": list(range(1, 6)), "name": ["a", "b", "c", "d", "e"]}), path)
    return path


def test_parquet_is_split_into_bounded_row_chunks(parquet_path, monkeypatch):
    monkeypatch.setattr(singlestore, "_PARQUET_LOAD_CHUNK_ROWS", 2)

    texts = [path.read_text(encoding="utf-8") for path in _parquet_load_chunks(parquet_path)]

    assert texts == ["1\ta\n2\tb\n", "3\tc\n4\td\n", "5\te\n"]
    assert list(parquet_path.parent.glob("*.tsv")) == []


def test_each_chunk_is_its_own_load_data_statement_and_rows_are_summed(parquet_path, monkeypatch):
    monkeypatch.setattr(singlestore, "_PARQUET_LOAD_CHUNK_ROWS", 2)
    connection = Mock()
    cursor = connection.cursor.return_value
    cursor.fetchone.side_effect = [(0,), None, (2,), (2,), None, (4,), (4,), None, (5,)]
    loaded_files: list[Path] = []

    def capture(sql):
        if sql.startswith("LOAD DATA"):
            path = Path(sql.split("'")[1])
            assert path.exists()
            loaded_files.append(path)

    cursor.execute.side_effect = capture

    rows = SingleStoreAdapter.__new__(SingleStoreAdapter)._load_parquet_infile(connection, "t", parquet_path)

    assert rows == 5
    assert len(loaded_files) == 3
    assert len(set(loaded_files)) == 3
    assert all(not path.exists() for path in loaded_files)


def test_empty_parquet_loads_nothing(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path = tmp_path / "empty.parquet"
    pq.write_table(pa.table({"id": pa.array([], pa.int64())}), path)
    connection = Mock()

    assert SingleStoreAdapter.__new__(SingleStoreAdapter)._load_parquet_infile(connection, "t", path) == 0
    connection.cursor.assert_not_called()
