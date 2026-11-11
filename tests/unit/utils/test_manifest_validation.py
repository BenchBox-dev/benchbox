import json
from pathlib import Path

import pytest

from benchbox.utils.data_validation import BenchmarkDataValidator
from benchbox.utils.datagen_manifest import compute_entry_size

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_validator_builds_manifest_from_scan(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    f = data_dir / "sample.tbl"
    f.write_text("1|a\n2|b\n3|c\n")

    v = BenchmarkDataValidator("ssb", scale_factor=0.01)
    res = v.validate_data_directory(data_dir)

    assert res.valid is True
    mp = data_dir / "_datagen_manifest.json"
    assert mp.exists()
    manifest = json.loads(mp.read_text())
    assert manifest.get("benchmark") == "ssb"
    assert "tables" in manifest and len(manifest["tables"]) >= 1

    res2 = v.validate_data_directory(data_dir)
    assert res2.valid is False
    assert any("stale" in issue for issue in res2.issues)


class TestComputeEntrySize:
    def test_regular_file(self, tmp_path: Path):
        f = tmp_path / "data.parquet"
        f.write_bytes(b"x" * 1234)
        assert compute_entry_size(f) == 1234
        assert compute_entry_size(f) == f.stat().st_size

    def test_directory_with_files(self, tmp_path: Path):
        d = tmp_path / "lineitem"
        d.mkdir()
        (d / "part-0.parquet").write_bytes(b"a" * 100)
        (d / "part-1.parquet").write_bytes(b"b" * 200)
        assert compute_entry_size(d) == 300

    def test_directory_with_nested_subdirectories(self, tmp_path: Path):
        d = tmp_path / "delta_table"
        d.mkdir()
        (d / "data.parquet").write_bytes(b"x" * 500)
        log_dir = d / "_delta_log"
        log_dir.mkdir()
        (log_dir / "00000.json").write_bytes(b"y" * 50)
        assert compute_entry_size(d) == 550

    def test_empty_directory(self, tmp_path: Path):
        d = tmp_path / "empty"
        d.mkdir()
        assert compute_entry_size(d) == 0

    def test_nonexistent_path_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            compute_entry_size(tmp_path / "does_not_exist")


class TestDataValidationDirectoryEntries:
    def _create_directory_manifest(self, data_dir: Path, table_name: str = "lineitem") -> dict:
        table_dir = data_dir / table_name
        table_dir.mkdir(parents=True)
        (table_dir / "part-0.parquet").write_bytes(b"a" * 100)
        (table_dir / "part-1.parquet").write_bytes(b"b" * 200)

        total_size = sum(f.stat().st_size for f in table_dir.rglob("*") if f.is_file())

        from benchbox.utils.datagen_version import current_datagen_stamp

        manifest = {
            "version": 2,
            "benchmark": "ssb",
            "scale_factor": 0.01,
            **current_datagen_stamp("ssb"),
            "format_preference": ["delta"],
            "tables": {
                table_name: {
                    "formats": {
                        "delta": [
                            {
                                "path": table_name,
                                "size_bytes": total_size,
                                "row_count": 1000,
                            }
                        ]
                    }
                }
            },
        }

        manifest_path = data_dir / "_datagen_manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        return manifest

    def test_directory_entry_validates(self, tmp_path: Path):
        data_dir = tmp_path / "data"
        self._create_directory_manifest(data_dir)

        v = BenchmarkDataValidator("ssb", scale_factor=0.01)
        res = v.validate_data_directory(data_dir)

        assert res.valid is True

    def test_directory_entry_detects_size_change(self, tmp_path: Path):
        data_dir = tmp_path / "data"
        self._create_directory_manifest(data_dir)

        (data_dir / "lineitem" / "extra.parquet").write_bytes(b"c" * 500)

        v = BenchmarkDataValidator("ssb", scale_factor=0.01)
        res = v.validate_data_directory(data_dir)

        assert res.valid is False


