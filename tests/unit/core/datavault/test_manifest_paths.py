import json
from datetime import datetime
from pathlib import Path

import pytest

from benchbox.core.datavault.etl.transformer import DataVaultETLTransformer
from benchbox.utils.datagen_manifest import DataGenerationManifest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_datavault_manifest_uses_relative_table_paths_for_relative_output_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    output_dir = Path("out")
    output_dir.mkdir()
    table_path = output_dir / "hub_region.tbl"
    table_path.write_text("row\n", encoding="utf-8")

    transformer = DataVaultETLTransformer()
    transformer._write_manifest(
        output_dir=output_dir,
        table_paths={"hub_region": table_path},
        table_row_counts={"hub_region": 1},
        output_format="tbl",
        load_timestamp=datetime(2025, 1, 1, 0, 0, 0),
    )

    manifest = json.loads((output_dir / "_datagen_manifest.json").read_text(encoding="utf-8"))
    entry = manifest["tables"]["hub_region"]["formats"]["tbl"][0]
    assert entry["path"] == "hub_region.tbl"


def test_manifest_add_entry_with_relative_root_does_not_duplicate_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    output_dir = Path("out")
    output_dir.mkdir()
    data_file = output_dir / "sample.tbl"
    data_file.write_text("1|\n", encoding="utf-8")

    manifest = DataGenerationManifest(
        output_dir=output_dir,
        benchmark="datavault",
        scale_factor=0.01,
    )
    manifest.add_entry("sample", data_file, row_count=1, format="tbl")
    payload = manifest.to_dict()

    entry = payload["tables"]["sample"]["formats"]["tbl"][0]
    assert entry["path"] == "sample.tbl"


