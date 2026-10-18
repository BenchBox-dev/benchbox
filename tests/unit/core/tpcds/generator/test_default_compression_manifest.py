from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.core.tpcds.constants import TPCDS_TABLE_NAMES
from benchbox.core.tpcds.generator import TPCDSDataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

EXPECTED_TABLES = sorted([*TPCDS_TABLE_NAMES, "dbgen_version"])
ROWS_PER_CHUNK = 7

PARENT_CHILDREN = {
    "catalog_sales": ["catalog_returns"],
    "store_sales": ["store_returns"],
    "web_sales": ["web_returns"],
}

FAKE_DSDGEN = """#!/usr/bin/env python3
import os
import sys

PARENTS = {
    "catalog_sales": ["catalog_returns"],
    "store_sales": ["store_returns"],
    "web_sales": ["web_returns"],
}
ROWS = 7


def _flag(name):
    args = sys.argv[1:]
    for key in ("-" + name, "/" + name):
        if key in args:
            idx = args.index(key)
            if idx + 1 < len(args):
                return args[idx + 1]
    return None


def main():
    table = _flag("TABLE")
    child = _flag("CHILD")
    parallel = _flag("PARALLEL") or "1"
    filter_flag = _flag("FILTER")
    tables = [table] + PARENTS.get(table, [])
    chunk = child if child is not None else "1"
    rendered = {}
    for name in tables:
        rendered[name] = [f"{name}|chunk{chunk}|row{i}|\\n" for i in range(ROWS)]
    if filter_flag is not None and filter_flag.upper() == "Y":
        sys.stdout.write("".join(rendered[table]))
    else:
        for name in tables:
            if child is None:
                filename = f"{name}.dat"
            else:
                filename = f"{name}_{child}_{parallel}.dat"
            with open(os.path.join(os.getcwd(), filename), "w", encoding="utf-8") as fh:
                fh.writelines(rendered[name])


main()
"""


@pytest.fixture
def fake_dsdgen(tmp_path: Path) -> Path:
    script = tmp_path / "fake_dsdgen.py"
    script.write_text(FAKE_DSDGEN, encoding="utf-8")
    os.chmod(script, 0o755)
    if sys.platform == "win32":
        launcher = tmp_path / "fake_dsdgen.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        return launcher
    return script


def _compressed_generator(tmp_path: Path, fake_dsdgen: Path, parallel: int) -> TPCDSDataGenerator:
    with patch.object(TPCDSDataGenerator, "_find_or_build_dsdgen", return_value=fake_dsdgen):
        return TPCDSDataGenerator(
            scale_factor=1.0,
            output_dir=tmp_path,
            verbose=False,
            parallel=parallel,
            force_regenerate=True,
            compress_data=True,
            compression_type="zstd",
        )


def _rebuild_manifest_from_scan(gen: TPCDSDataGenerator, output_dir: Path) -> tuple[dict[str, list[Path]], dict]:
    gen._manifest_entries.clear()
    table_paths = gen._gather_existing_table_files(output_dir)
    gen._write_manifest(output_dir, table_paths)
    manifest = json.loads((output_dir / "_datagen_manifest.json").read_text(encoding="utf-8"))
    return table_paths, manifest


def test_write_manifest_counts_zstd_rows_without_streaming_entries(tmp_path: Path) -> None:
    import zstandard as zstd

    gen = TPCDSDataGenerator.__new__(TPCDSDataGenerator)
    gen.scale_factor = 1.0
    gen.parallel = 1
    gen.compress_data = True
    gen.compression_type = "zstd"
    gen._manifest_entries = {}

    data_file = tmp_path / "item.dat.zst"
    data_file.write_bytes(zstd.ZstdCompressor().compress(b"1|a|\n2|b|\n3|c|\n"))

    gen._write_manifest(tmp_path, {"item": [data_file]})

    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text(encoding="utf-8"))
    assert manifest["tables"]["item"][0]["row_count"] == 3


def test_parallel_default_compression_records_every_chunk(tmp_path: Path, fake_dsdgen: Path) -> None:
    gen = _compressed_generator(tmp_path, fake_dsdgen, parallel=3)
    output_dir = tmp_path / "data"
    output_dir.mkdir()

    gen._run_parallel_streaming_dsdgen(output_dir)

    assert list(output_dir.glob("*.dat")) == []
    table_paths, manifest = _rebuild_manifest_from_scan(gen, output_dir)

    assert len(table_paths) == 25
    assert sorted(table_paths) == EXPECTED_TABLES
    assert sorted(manifest["tables"]) == EXPECTED_TABLES
    for table, entries in manifest["tables"].items():
        expected_chunks = 1 if table == "dbgen_version" else 3
        assert len(entries) == expected_chunks, table
        for entry in entries:
            assert entry["row_count"] == ROWS_PER_CHUNK, (table, entry)
            assert (output_dir / entry["path"]).exists()


def test_single_threaded_default_compression_records_every_table(tmp_path: Path, fake_dsdgen: Path) -> None:
    gen = _compressed_generator(tmp_path, fake_dsdgen, parallel=1)
    output_dir = tmp_path / "data"
    output_dir.mkdir()

    gen._run_streaming_dsdgen(output_dir)

    assert list(output_dir.glob("*.dat")) == []
    table_paths, manifest = _rebuild_manifest_from_scan(gen, output_dir)

    assert len(table_paths) == 25
    assert sorted(table_paths) == EXPECTED_TABLES
    assert sorted(manifest["tables"]) == EXPECTED_TABLES
    for table, entries in manifest["tables"].items():
        assert len(entries) == 1, table
        assert entries[0]["row_count"] == ROWS_PER_CHUNK, (table, entries[0])
        assert (output_dir / entries[0]["path"]).exists()
