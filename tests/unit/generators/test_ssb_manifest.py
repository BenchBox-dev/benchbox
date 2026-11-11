from pathlib import Path

import pytest

from benchbox.core.ssb.generator import SSBDataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_ssb_generator_writes_manifest_and_no_raw_when_compressed(tmp_path: Path):
    out = tmp_path / "ssb"
    out.mkdir()
    gen = SSBDataGenerator(scale_factor=0.001, output_dir=out, compress_data=True, compression_type="gzip")
    files = gen.generate_data(tables=["date"])

    assert "date" in files

    raw = list(out.glob("*.tbl"))
    assert not raw

    gz = list(out.glob("*.tbl.gz"))
    assert gz, "expected gzip-compressed output"

    mp = out / "_datagen_manifest.json"
    assert mp.exists()
    import json

    manifest = json.loads(mp.read_text())
    tables = manifest.get("tables") or {}
    assert "date" in tables
    first = tables["date"][0]
    assert first.get("path", "").endswith(".tbl.gz")
    assert int(first.get("size_bytes", 0)) > 0

    assert int(first.get("row_count", 0)) >= 1


