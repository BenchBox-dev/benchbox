from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from benchbox.core.runner.runner import _ensure_data_generated
from benchbox.core.schemas import BenchmarkConfig
from benchbox.utils.cloud_storage import CloudStagingPath
from benchbox.utils.datagen_version import current_datagen_stamp

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def benchmark_config() -> BenchmarkConfig:
    return BenchmarkConfig(
        name="tpcds",
        display_name="TPC-DS",
        scale_factor=0.01,
        compress_data=True,
        compression_type="zstd",
        compression_level=None,
        options={},
    )


def _write_manifest(tmp_path: Path, *, table_names: list[str]) -> dict:
    tables: dict[str, dict] = {}
    file_count = 0
    for table in table_names:
        file_path = tmp_path / f"{table}.dat"
        file_path.write_text("1|sample\n")
        size = file_path.stat().st_size
        tables[table] = {
            "formats": {
                "dat": [
                    {
                        "path": file_path.name,
                        "size_bytes": size,
                        "row_count": 1,
                    }
                ]
            }
        }
        file_count += 1

    manifest = {
        "benchmark": "tpcds",
        "scale_factor": 0.01,
        **current_datagen_stamp("tpcds"),
        "compression": {"enabled": True, "type": "zstd", "level": None},
        "parallel": 1,
        "created_at": "2025-01-01T00:00:00Z",
        "generator_version": "test",
        "tables": tables,
    }

    with (tmp_path / "_datagen_manifest.json").open("w") as fh:
        json.dump(manifest, fh)

    return manifest


def test_manifest_reuse_populates_tables_and_logs(
    tmp_path: Path, benchmark_config: BenchmarkConfig, capsys: pytest.CaptureFixture[str]
):
    manifest = _write_manifest(tmp_path, table_names=["customer", "orders"])

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()

    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (False, True), "Expected reuse (not fresh) with manifest validated"
    dummy.generate_data.assert_not_called()
    assert isinstance(dummy.tables, dict)
    assert set(dummy.tables.keys()) == {"customer", "orders"}
    for path in dummy.tables.values():
        assert isinstance(path, Path)
        assert path.exists()

    out = capsys.readouterr().out
    assert "Reusing benchmark data" in out
    assert "2 tables" in out
    assert "2 files" in out
    assert manifest["created_at"] in out


def test_manifest_reuse_preserves_directory_format_preference(tmp_path: Path, benchmark_config: BenchmarkConfig):
    delta_dir = tmp_path / "lineitem"
    (delta_dir / "_delta_log").mkdir(parents=True)
    (delta_dir / "_delta_log" / "00000000000000000000.json").write_text("{}")
    (delta_dir / "part-000.parquet").write_bytes(b"delta")

    tbl_path = tmp_path / "lineitem.tbl"
    tbl_path.write_text("1|sample\n")

    delta_size = sum(path.stat().st_size for path in delta_dir.rglob("*") if path.is_file())
    manifest = {
        "version": 2,
        "benchmark": "tpcds",
        **current_datagen_stamp("tpcds"),
        "scale_factor": 0.01,
        "compression": {"enabled": True, "type": "zstd", "level": None},
        "parallel": 1,
        "created_at": "2025-01-01T00:00:00Z",
        "generator_version": "test",
        "format_preference": ["delta", "tbl"],
        "tables": {
            "lineitem": {
                "formats": {
                    "delta": [
                        {
                            "path": "lineitem",
                            "size_bytes": delta_size,
                            "row_count": 1,
                            "is_directory": True,
                        }
                    ],
                    "tbl": [
                        {
                            "path": "lineitem.tbl",
                            "size_bytes": tbl_path.stat().st_size,
                            "row_count": 1,
                        }
                    ],
                }
            }
        },
    }
    with (tmp_path / "_datagen_manifest.json").open("w") as fh:
        json.dump(manifest, fh)

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()

    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (False, True), "Expected reuse (not fresh) with manifest validated"
    dummy.generate_data.assert_not_called()
    assert dummy.tables == {"lineitem": delta_dir}


def test_manifest_reuse_prints_when_no_logger(
    tmp_path: Path, benchmark_config: BenchmarkConfig, capsys: pytest.CaptureFixture[str]
):
    _write_manifest(tmp_path, table_names=["lineitem"])

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()

    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (False, True), "Expected reuse (not fresh) with manifest validated"
    dummy.generate_data.assert_not_called()
    out = capsys.readouterr().out
    assert "Reusing benchmark data" in out
    assert "1 table" in out


def test_missing_manifest_triggers_generation(tmp_path: Path, benchmark_config: BenchmarkConfig):
    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()

    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (True, False), "Expected fresh generation without manifest reuse"
    dummy.generate_data.assert_called_once()


def test_no_regenerate_with_missing_manifest_raises(tmp_path: Path, benchmark_config: BenchmarkConfig):
    benchmark_config.options = {"no_regenerate": True}

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()

    with pytest.raises(RuntimeError):
        _ensure_data_generated(dummy, benchmark_config)

    dummy.generate_data.assert_not_called()


def test_force_regenerate_ignores_manifest(tmp_path: Path, benchmark_config: BenchmarkConfig):
    _write_manifest(tmp_path, table_names=["customer"])
    benchmark_config.options = {"force_regenerate": True}

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()

    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (True, False), "Expected fresh generation without manifest reuse"
    dummy.generate_data.assert_called_once()


def test_force_regenerate_overrides_populated_tables(tmp_path: Path, benchmark_config: BenchmarkConfig):
    _write_manifest(tmp_path, table_names=["customer"])
    benchmark_config.options = {"force_regenerate": True}

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = {"customer": ["customer.dat"]}
            self.generate_data = Mock()

    dummy = DummyBenchmark()

    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (True, False), "Expected fresh generation without manifest reuse"
    dummy.generate_data.assert_called_once()


def test_populated_missing_tables_trigger_regeneration(tmp_path: Path, benchmark_config: BenchmarkConfig):

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = {"orders": tmp_path / "missing.dat"}
            self.generate_data = Mock()

    dummy = DummyBenchmark()

    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (True, False)
    dummy.generate_data.assert_called_once()


def test_populated_tables_must_match_manifest_before_reuse(tmp_path: Path, benchmark_config: BenchmarkConfig):
    _write_manifest(tmp_path, table_names=["customer"])
    stale_path = tmp_path / "orders.dat"
    stale_path.write_text("stale\n")

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = {"orders": stale_path}
            self.generate_data = Mock()

    dummy = DummyBenchmark()

    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (True, False)
    dummy.generate_data.assert_called_once()


def test_external_populated_tables_without_manifest_are_reused(tmp_path: Path, benchmark_config: BenchmarkConfig):
    benchmark_config.options["table_mode"] = "external"

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = {"orders": "s3://bucket/benchbox/orders"}
            self.generate_data = Mock()

    dummy = DummyBenchmark()

    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (False, False)
    dummy.generate_data.assert_not_called()


def test_invalid_manifest_regenerates(tmp_path: Path, benchmark_config: BenchmarkConfig):
    manifest = _write_manifest(tmp_path, table_names=["orders"])

    manifest["tables"]["orders"]["formats"]["dat"][0]["size_bytes"] += 100
    with (tmp_path / "_datagen_manifest.json").open("w") as fh:
        json.dump(manifest, fh)

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

        def log_verbose(self, message: str) -> None:
            self.last_message = message

    dummy = DummyBenchmark()

    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (True, False), "Expected fresh generation without manifest reuse"
    dummy.generate_data.assert_called_once()


def test_no_regenerate_with_invalid_manifest_raises(tmp_path: Path, benchmark_config: BenchmarkConfig):
    manifest = _write_manifest(tmp_path, table_names=["orders"])
    manifest["tables"]["orders"]["formats"]["dat"][0]["size_bytes"] += 50
    with (tmp_path / "_datagen_manifest.json").open("w") as fh:
        json.dump(manifest, fh)

    benchmark_config.options = {"no_regenerate": True}

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()

    with pytest.raises(RuntimeError):
        _ensure_data_generated(dummy, benchmark_config)

    dummy.generate_data.assert_not_called()


def _write_directory_manifest(tmp_path: Path, *, table_name: str = "lineitem") -> dict:
    table_dir = tmp_path / table_name
    table_dir.mkdir()
    (table_dir / "part-0.parquet").write_bytes(b"x" * 100)
    (table_dir / "part-1.parquet").write_bytes(b"y" * 200)
    log_dir = table_dir / "_delta_log"
    log_dir.mkdir()
    (log_dir / "00000.json").write_bytes(b"z" * 50)

    total_size = sum(f.stat().st_size for f in table_dir.rglob("*") if f.is_file())

    manifest = {
        "benchmark": "tpcds",
        **current_datagen_stamp("tpcds"),
        "scale_factor": 0.01,
        "compression": {"enabled": True, "type": "zstd", "level": None},
        "parallel": 1,
        "created_at": "2025-01-01T00:00:00Z",
        "generator_version": "test",
        "format_preference": ["delta"],
        "tables": {
            table_name: {
                "formats": {
                    "delta": [
                        {
                            "path": table_name,
                            "size_bytes": total_size,
                            "row_count": 1000,
                            "is_directory": True,
                        }
                    ]
                }
            }
        },
    }

    with (tmp_path / "_datagen_manifest.json").open("w") as fh:
        json.dump(manifest, fh)

    return manifest


def test_directory_entry_validates_successfully(
    tmp_path: Path, benchmark_config: BenchmarkConfig, capsys: pytest.CaptureFixture[str]
):
    _write_directory_manifest(tmp_path)

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()
    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (False, True), "Directory entry should validate and reuse data"
    dummy.generate_data.assert_not_called()
    out = capsys.readouterr().out
    assert "Reusing benchmark data" in out


def test_directory_entry_size_mismatch_detected(tmp_path: Path, benchmark_config: BenchmarkConfig):
    _write_directory_manifest(tmp_path)

    (tmp_path / "lineitem" / "extra.parquet").write_bytes(b"w" * 999)

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()
    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (True, False), "Size mismatch in directory should trigger regeneration"
    dummy.generate_data.assert_called_once()


def test_table_name_directory_collision_invalidates_manifest(tmp_path: Path, benchmark_config: BenchmarkConfig):
    _write_manifest(tmp_path, table_names=["customer", "orders"])

    stale_dir = tmp_path / "customer"
    stale_dir.mkdir()
    (stale_dir / "data").mkdir()
    (stale_dir / "metadata").mkdir()
    (stale_dir / "data" / "part-0.parquet").write_bytes(b"stale")

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()
    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (True, False), "Table-name directory collision should trigger regeneration"
    dummy.generate_data.assert_called_once()


def test_cloud_staging_path_checks_local_directory_collisions(tmp_path: Path, benchmark_config: BenchmarkConfig):
    _write_manifest(tmp_path, table_names=["customer"])
    stale_dir = tmp_path / "customer"
    stale_dir.mkdir()
    (stale_dir / "metadata.json").write_text("stale")

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = CloudStagingPath(tmp_path, "abfss://c@a.dfs.core.windows.net/benchbox")
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()
    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (True, False)
    dummy.generate_data.assert_called_once()


def test_tracked_directory_entry_still_validates(
    tmp_path: Path, benchmark_config: BenchmarkConfig, capsys: pytest.CaptureFixture[str]
):
    _write_directory_manifest(tmp_path, table_name="lineitem")

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()
    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (False, True), "Tracked directory entry should validate and reuse"
    dummy.generate_data.assert_not_called()
    out = capsys.readouterr().out
    assert "Reusing benchmark data" in out


def test_file_entry_resolving_to_directory_invalidates(tmp_path: Path, benchmark_config: BenchmarkConfig):

    file_path = tmp_path / "orders.dat"
    file_path.write_text("1|sample\n")
    size = file_path.stat().st_size

    manifest = {
        "benchmark": "tpcds",
        **current_datagen_stamp("tpcds"),
        "scale_factor": 0.01,
        "compression": {"enabled": True, "type": "zstd", "level": None},
        "parallel": 1,
        "created_at": "2025-01-01T00:00:00Z",
        "generator_version": "test",
        "tables": {"orders": {"formats": {"dat": [{"path": "orders.dat", "size_bytes": size, "row_count": 1}]}}},
    }

    with (tmp_path / "_datagen_manifest.json").open("w") as fh:
        json.dump(manifest, fh)

    file_path.unlink()
    file_path.mkdir()
    (file_path / "data.parquet").write_bytes(b"x" * size)

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()
    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (True, False), "File entry resolving to directory should trigger regeneration"
    dummy.generate_data.assert_called_once()


def test_multi_format_tracked_directory_not_flagged_as_collision(
    tmp_path: Path, benchmark_config: BenchmarkConfig, capsys: pytest.CaptureFixture[str]
):

    tbl_file = tmp_path / "lineitem.tbl"
    tbl_file.write_text("1|data\n")
    tbl_size = tbl_file.stat().st_size

    delta_dir = tmp_path / "lineitem"
    delta_dir.mkdir()
    (delta_dir / "part-0.parquet").write_bytes(b"x" * 100)
    delta_log = delta_dir / "_delta_log"
    delta_log.mkdir()
    (delta_log / "00000.json").write_bytes(b"z" * 50)
    delta_size = sum(f.stat().st_size for f in delta_dir.rglob("*") if f.is_file())

    manifest = {
        "benchmark": "tpcds",
        **current_datagen_stamp("tpcds"),
        "scale_factor": 0.01,
        "compression": {"enabled": True, "type": "zstd", "level": None},
        "parallel": 1,
        "created_at": "2025-01-01T00:00:00Z",
        "generator_version": "test",
        "format_preference": ["tbl", "delta"],
        "tables": {
            "lineitem": {
                "formats": {
                    "tbl": [{"path": "lineitem.tbl", "size_bytes": tbl_size, "row_count": 1}],
                    "delta": [
                        {
                            "path": "lineitem",
                            "size_bytes": delta_size,
                            "row_count": 1,
                            "is_directory": True,
                        }
                    ],
                }
            }
        },
    }

    with (tmp_path / "_datagen_manifest.json").open("w") as fh:
        json.dump(manifest, fh)

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()
    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (False, True), "Multi-format tracked directory should validate and reuse"
    dummy.generate_data.assert_not_called()
    out = capsys.readouterr().out
    assert "Reusing benchmark data" in out


def test_shards_inside_table_named_directory_not_flagged_as_collision(
    tmp_path: Path, benchmark_config: BenchmarkConfig, capsys: pytest.CaptureFixture[str]
):
    shard_dir = tmp_path / "lineitem"
    shard_dir.mkdir()
    shard = shard_dir / "part-0001.csv"
    shard.write_text("1,sample\n")

    manifest = {
        "benchmark": "tpcds",
        **current_datagen_stamp("tpcds"),
        "scale_factor": 0.01,
        "compression": {"enabled": False, "type": None, "level": None},
        "parallel": 1,
        "created_at": "2025-01-01T00:00:00Z",
        "generator_version": "test",
        "format_preference": ["csv"],
        "tables": {
            "lineitem": {
                "formats": {
                    "csv": [
                        {
                            "path": "lineitem/part-0001.csv",
                            "size_bytes": shard.stat().st_size,
                            "row_count": 1,
                        }
                    ]
                }
            }
        },
    }
    with (tmp_path / "_datagen_manifest.json").open("w") as fh:
        json.dump(manifest, fh)

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    dummy = DummyBenchmark()
    result = _ensure_data_generated(dummy, benchmark_config)

    assert result == (False, True), "Shards under a table directory should validate and reuse"
    dummy.generate_data.assert_not_called()
    assert dummy.tables == {"lineitem": shard}
    out = capsys.readouterr().out
    assert "Reusing benchmark data" in out


def test_populated_tables_matching_current_manifest_reuse_manifest(tmp_path: Path, benchmark_config: BenchmarkConfig):
    _write_manifest(tmp_path, table_names=["customer", "orders"])

    class DummyBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = None
            self.generate_data = Mock()

    populated = DummyBenchmark()
    assert _ensure_data_generated(populated, benchmark_config) == (False, True)

    class CallerBenchmark:
        def __init__(self) -> None:
            self.output_dir = tmp_path
            self.tables = dict(populated.tables)
            self.generate_data = Mock()

    caller = CallerBenchmark()
    result = _ensure_data_generated(caller, benchmark_config)

    assert result == (False, True)
    caller.generate_data.assert_not_called()


