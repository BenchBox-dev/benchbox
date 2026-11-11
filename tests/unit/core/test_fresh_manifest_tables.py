# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.runner.runner import _populate_tables_from_manifest
from benchbox.platforms.base.data_loading import normalize_table_paths
from benchbox.read_primitives import ReadPrimitives
from benchbox.utils.datagen_manifest import DataGenerationManifest, load_manifest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize("compressed", [False, True], ids=["uncompressed", "gzip"])
@pytest.mark.parametrize("facade", [False, True], ids=["implementation", "public-facade"])
def test_fresh_mapping_matches_manifest_reuse(tmp_path: Path, monkeypatch, compressed: bool, facade: bool) -> None:
    benchmark = (
        ReadPrimitives(scale_factor=0.01, output_dir=tmp_path)
        if facade
        else ReadPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    )
    impl = benchmark._impl if facade else benchmark
    layout: dict[str, Path | list[Path]] = {}
    payloads: dict[Path, bytes] = {}
    manifest = DataGenerationManifest(
        output_dir=tmp_path,
        benchmark="tpch",
        scale_factor=0.01,
        compression={"enabled": compressed, "type": "gzip" if compressed else None, "level": None},
        parallel=3,
    )
    for table, chunks in (("customer", 3), ("nation", 1)):
        paths = []
        for chunk in range(1, chunks + 1):
            suffix = f".tbl.{chunk}" if chunks > 1 else ".tbl"
            path = tmp_path / f"{table}{suffix}{'.gz' if compressed else ''}"
            payload = f"{chunk}|sample|\n".encode()
            path.write_bytes(gzip.compress(payload) if compressed else payload)
            payloads[path] = payload
            paths.append(path)
            manifest.add_entry(table, path, row_count=1)
        layout[table] = paths if chunks > 1 else paths[0]

    producer_manifest_bytes = None

    def generate() -> dict[str, Path | list[Path]]:
        nonlocal producer_manifest_bytes
        manifest.write()
        producer_manifest_bytes = manifest.manifest_path.read_bytes()
        return layout

    monkeypatch.setattr(impl.data_generator.tpch_generator, "generate", generate)
    fresh = benchmark.generate_data(tables=["customer", "nation"])
    assert fresh is impl.tables
    assert isinstance(layout["customer"], list)
    assert fresh == {
        "customer": [str(path) for path in layout["customer"]],
        "nation": str(layout["nation"]),
    }
    assert manifest.manifest_path.read_bytes() == producer_manifest_bytes
    assert load_manifest(manifest.manifest_path)["benchmark"] == "tpch"

    reused = ReadPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    summary = _populate_tables_from_manifest(reused)
    assert summary is not None
    assert summary["table_count"] == 2
    assert summary["file_count"] == 4
    for table, value in fresh.items():
        fresh_paths = normalize_table_paths(value)
        assert fresh_paths == normalize_table_paths(reused.tables[table])
        assert len(fresh_paths) == (3 if table == "customer" else 1)
        for path in fresh_paths:
            assert path.is_file()
            contents = gzip.decompress(path.read_bytes()) if compressed else path.read_bytes()
            assert contents == payloads[path]
    assert manifest.manifest_path.read_bytes() == producer_manifest_bytes


