from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from benchbox.core.data_organization.config import DataOrganizationConfig, SortColumn, SortOrder
from benchbox.core.data_organization.sorting import SortedParquetWriter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.resource_heavy,
]


DELTALAKE_AVAILABLE = importlib.util.find_spec("deltalake") is not None
PYICEBERG_AVAILABLE = importlib.util.find_spec("pyiceberg") is not None


def _write_tbl(path: Path, rows: list[tuple[object, ...]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write("|".join(str(v) for v in row) + "|\n")


def _count_descending_transitions(values: list[date]) -> int:
    return sum(1 for i in range(1, len(values)) if values[i - 1] > values[i])


def test_row_count_equivalence(tmp_path: Path):
    source = tmp_path / "lineitem.tbl"
    rows = [
        (4, "1992-01-04", 10),
        (2, "1992-01-02", 20),
        (5, "1992-01-05", 30),
        (1, "1992-01-01", 40),
        (3, "1992-01-03", 50),
    ]
    _write_tbl(source, rows)
    schema_registry = {"lineitem": {"columns": [{"name": "id"}, {"name": "l_shipdate"}, {"name": "qty"}]}}

    config = DataOrganizationConfig(
        sort_columns=[SortColumn(name="l_shipdate", order=SortOrder.ASC)],
        compression="none",
    )
    writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

    output = writer.write_sorted_parquet("lineitem", [source], tmp_path)
    sorted_table = pq.read_table(output)

    assert sorted_table.num_rows == len(rows)


def test_sort_key_monotonic(tmp_path: Path):
    source = tmp_path / "lineitem.tbl"
    _write_tbl(
        source,
        [
            (10, "1992-01-10"),
            (1, "1992-01-01"),
            (7, "1992-01-07"),
            (3, "1992-01-03"),
        ],
    )
    schema_registry = {"lineitem": {"columns": [{"name": "id"}, {"name": "l_shipdate"}]}}
    config = DataOrganizationConfig(
        sort_columns=[SortColumn(name="l_shipdate", order=SortOrder.ASC)], compression="none"
    )
    writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

    output = writer.write_sorted_parquet("lineitem", [source], tmp_path)
    shipdates = pq.read_table(output).column("l_shipdate").to_pylist()

    assert _count_descending_transitions(shipdates) == 0


@pytest.mark.requires_table_formats
@pytest.mark.skipif(not DELTALAKE_AVAILABLE, reason="deltalake package not installed")
def test_delta_sorted_output_writes_readable_delta_table(tmp_path: Path):
    from deltalake import DeltaTable

    source = tmp_path / "lineitem.tbl"
    _write_tbl(source, [(2, "1992-01-02"), (1, "1992-01-01")])
    schema_registry = {"lineitem": {"columns": [{"name": "id"}, {"name": "l_shipdate"}]}}
    config = DataOrganizationConfig(
        sort_columns=[SortColumn(name="l_shipdate", order=SortOrder.ASC)],
        output_format="delta",
        compression="none",
    )
    writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

    output = writer.write_sorted_parquet("lineitem", [source], tmp_path)

    assert output == tmp_path / "lineitem"
    assert (output / "_delta_log").exists()
    table = DeltaTable(str(output))
    assert table.to_pyarrow_table().num_rows == 2


@pytest.mark.requires_table_formats
@pytest.mark.skipif(not PYICEBERG_AVAILABLE, reason="pyiceberg package not installed")
@pytest.mark.skipif(sys.platform == "win32", reason="pyiceberg generates Unix-style paths incompatible with Windows")
def test_iceberg_sorted_output_writes_iceberg_layout(tmp_path: Path):
    source = tmp_path / "lineitem.tbl"
    _write_tbl(source, [(2, "1992-01-02"), (1, "1992-01-01")])
    schema_registry = {"lineitem": {"columns": [{"name": "id"}, {"name": "l_shipdate"}]}}
    config = DataOrganizationConfig(
        sort_columns=[SortColumn(name="l_shipdate", order=SortOrder.ASC)],
        output_format="iceberg",
        compression="none",
    )
    writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

    output = writer.write_sorted_parquet("lineitem", [source], tmp_path)

    assert output == tmp_path / "lineitem"
    assert (output / "metadata").exists()
    assert any((output / "metadata").glob("*.json"))


