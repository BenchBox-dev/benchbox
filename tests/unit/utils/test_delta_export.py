# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from benchbox.utils.delta_export import DeltaExportError, export_delta_to_parquet

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

deltalake = pytest.importorskip("deltalake")


def _write_delta(path: Path, batches: list[pa.Table], mode: str = "overwrite") -> None:
    from deltalake import write_deltalake

    for index, batch in enumerate(batches):
        write_deltalake(str(path), batch, mode=mode if index == 0 else "append")


@pytest.fixture
def delta_table(tmp_path: Path) -> Path:
    path = tmp_path / "orders"
    _write_delta(
        path,
        [
            pa.table({"id": [1, 2], "v": ["a", "b"]}),
            pa.table({"id": [3], "v": ["c"]}),
        ],
    )
    return path


class TestExportLatest:
    def test_exports_all_rows_with_schema(self, delta_table: Path, tmp_path: Path):
        out = tmp_path / "parquet"
        result = export_delta_to_parquet(delta_table, out)

        assert result.version == 1
        assert result.row_count == 3
        assert result.column_names == ["id", "v"]
        assert result.parquet_files == [out / "data.parquet"]
        assert all(path.suffix == ".parquet" for path in result.parquet_files)

    def test_parquet_readable_by_pyarrow(self, delta_table: Path, tmp_path: Path):
        out = tmp_path / "parquet"
        result = export_delta_to_parquet(delta_table, out)

        table = pq.read_table(result.parquet_files[0])
        assert table.num_rows == 3
        assert table.schema.names == ["id", "v"]
        assert sorted(table.column("id").to_pylist()) == [1, 2, 3]

    def test_creates_output_dir(self, delta_table: Path, tmp_path: Path):
        out = tmp_path / "nested" / "parquet"
        result = export_delta_to_parquet(delta_table, out)
        assert out.is_dir()
        assert result.parquet_files[0].exists()


class TestExportVersionPin:
    def test_pinned_version_exports_snapshot(self, delta_table: Path, tmp_path: Path):
        out = tmp_path / "parquet_v0"
        result = export_delta_to_parquet(delta_table, out, version=0)

        assert result.version == 0
        assert result.row_count == 2
        assert pq.read_table(result.parquet_files[0]).num_rows == 2


class TestExportErrors:
    def test_missing_delta_log_raises(self, tmp_path: Path):
        plain = tmp_path / "plain"
        plain.mkdir()
        with pytest.raises(DeltaExportError, match="_delta_log"):
            export_delta_to_parquet(plain, tmp_path / "out")

    def test_absent_path_raises(self, tmp_path: Path):
        with pytest.raises(DeltaExportError, match="_delta_log"):
            export_delta_to_parquet(tmp_path / "absent", tmp_path / "out")

    def test_unknown_version_raises(self, delta_table: Path, tmp_path: Path):
        with pytest.raises(DeltaExportError, match="Cannot open Delta table"):
            export_delta_to_parquet(delta_table, tmp_path / "out", version=99)

    def test_traversal_file_name_raises(self, delta_table: Path, tmp_path: Path):
        for bad_name in ("../escape.parquet", "sub/dir.parquet", "/abs.parquet", ".hidden.parquet", "data.csv"):
            with pytest.raises(DeltaExportError, match="Invalid file_name"):
                export_delta_to_parquet(delta_table, tmp_path / "out", file_name=bad_name)
        assert not (tmp_path / "escape.parquet").exists()

    def test_output_overlapping_table_raises(self, delta_table: Path):
        with pytest.raises(DeltaExportError, match="must not overlap"):
            export_delta_to_parquet(delta_table, delta_table / "nested-out")

    def test_output_containing_table_raises(self, delta_table: Path):
        with pytest.raises(DeltaExportError, match="must not overlap"):
            export_delta_to_parquet(delta_table, delta_table.parent)
        assert (delta_table / "_delta_log").is_dir()


class TestExportShapes:
    def test_empty_table_exports_no_files(self, tmp_path: Path):
        from deltalake import write_deltalake

        table = tmp_path / "empty"
        write_deltalake(
            str(table),
            pa.table({"id": pa.array([], type=pa.int64()), "v": pa.array([], type=pa.string())}),
            mode="overwrite",
        )
        result = export_delta_to_parquet(table, tmp_path / "out")

        assert result.row_count == 0
        assert result.parquet_files == []

    def test_reexport_overwrites(self, delta_table: Path, tmp_path: Path):
        out = tmp_path / "parquet"
        first = export_delta_to_parquet(delta_table, out)
        assert first.parquet_files == [out / "data.parquet"]

        second = export_delta_to_parquet(delta_table, out, version=0)
        assert second.row_count == 2
        assert second.parquet_files == [out / "data.parquet"]
        assert pq.read_table(out / "data.parquet").num_rows == 2

    def test_reexport_with_different_name_cleans_stale_files(self, delta_table: Path, tmp_path: Path):
        out = tmp_path / "parquet"
        export_delta_to_parquet(delta_table, out, version=0, file_name="v0.parquet")
        repeat = export_delta_to_parquet(delta_table, out, version=0, file_name="v0.parquet")
        assert repeat.parquet_files == [out / "v0.parquet"]

        latest = export_delta_to_parquet(delta_table, out)
        assert latest.parquet_files == [out / "data.parquet"]
        assert sorted(p.name for p in out.glob("*.parquet")) == ["data.parquet"]
        assert pq.read_table(out / "data.parquet").num_rows == 3

    def test_partitioned_table_keeps_hive_layout(self, tmp_path: Path):
        from deltalake import write_deltalake

        table = tmp_path / "parted"
        write_deltalake(
            str(table),
            pa.table({"id": [1, 2], "p": ["a", "b"]}),
            mode="overwrite",
            partition_by=["p"],
        )
        result = export_delta_to_parquet(table, tmp_path / "out")

        assert result.row_count == 2
        assert len(result.parquet_files) == 2
        assert {path.parent.name for path in result.parquet_files} == {"p=a", "p=b"}
        combined = pa.concat_tables([pq.read_table(path) for path in result.parquet_files])
        assert sorted(combined.column("id").to_pylist()) == [1, 2]

    def test_chunked_output_splits_files(self, delta_table: Path, tmp_path: Path):
        result = export_delta_to_parquet(delta_table, tmp_path / "out", max_rows_per_file=2)

        assert result.row_count == 3
        assert len(result.parquet_files) == 2
        combined = pa.concat_tables([pq.read_table(path) for path in result.parquet_files])
        assert sorted(combined.column("id").to_pylist()) == [1, 2, 3]


