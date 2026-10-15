# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.utils.iceberg_layout import (
    is_iceberg_directory,
    local_iceberg_location,
    relocate_iceberg_table,
    resolve_iceberg_metadata_file,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_table(root, files):
    (root / "metadata").mkdir(parents=True)
    for name in files:
        (root / "metadata" / name).write_text("{}")
    return root


def test_rejects_non_directories_and_plain_dirs(tmp_path):
    assert is_iceberg_directory(tmp_path / "missing") is False
    plain = tmp_path / "plain"
    plain.mkdir()
    assert is_iceberg_directory(plain) is False
    assert resolve_iceberg_metadata_file(plain) is None


def test_detects_version_hint_and_metadata_json(tmp_path):
    hinted = _make_table(tmp_path / "hinted", [])
    (hinted / "metadata" / "version-hint.text").write_text("1")
    assert is_iceberg_directory(hinted) is True

    jsoned = _make_table(tmp_path / "jsoned", ["00000-aaa.metadata.json"])
    assert is_iceberg_directory(jsoned) is True


def test_resolves_newest_metadata_file(tmp_path):
    table = _make_table(tmp_path / "t", ["00001-bbb.metadata.json", "00000-aaa.metadata.json"])
    resolved = resolve_iceberg_metadata_file(table)
    assert resolved is not None
    assert resolved.name == "00001-bbb.metadata.json"


def test_resolve_honors_version_hint(tmp_path):
    table = _make_table(tmp_path / "hinted", ["v9.metadata.json", "v10.metadata.json"])
    (table / "metadata" / "version-hint.text").write_text("9")
    resolved = resolve_iceberg_metadata_file(table)
    assert resolved is not None
    assert resolved.name == "v9.metadata.json"


def test_resolve_orders_unpadded_versions_numerically(tmp_path):
    table = _make_table(tmp_path / "hadoop", ["v9.metadata.json", "v10.metadata.json"])
    resolved = resolve_iceberg_metadata_file(table)
    assert resolved is not None
    assert resolved.name == "v10.metadata.json"


def test_resolve_returns_none_without_metadata_files(tmp_path):
    table = tmp_path / "empty-meta"
    (table / "metadata").mkdir(parents=True)
    assert is_iceberg_directory(table) is False
    assert resolve_iceberg_metadata_file(table) is None


def test_relocate_rewrites_graph_for_new_location(tmp_path):

    pytest.importorskip("pyiceberg", reason="relocation needs pyiceberg")
    pa = pytest.importorskip("pyarrow", reason="relocation needs pyarrow")
    from pyiceberg.catalog.sql import SqlCatalog
    from pyiceberg.schema import Schema
    from pyiceberg.table import StaticTable
    from pyiceberg.types import LongType, NestedField, StringType

    src = tmp_path / "src"
    catalog = SqlCatalog("reloc", uri=f"sqlite:///{tmp_path}/cat.db", warehouse=str(tmp_path))
    catalog.create_namespace_if_not_exists("ns")
    table = catalog.create_table(
        "ns.t",
        schema=Schema(NestedField(1, "id", LongType()), NestedField(2, "name", StringType())),
        location=local_iceberg_location(src / "t"),
        properties={"format-version": "2"},
    )
    table.overwrite(pa.table({"id": [1, 2], "name": ["a", "b"]}))

    staging = tmp_path / "staging"
    dest = tmp_path / "dest" / "t"
    relocated = relocate_iceberg_table(src / "t", local_iceberg_location(dest), staging)

    dest.mkdir(parents=True)
    for rel in relocated.data_files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((src / "t" / rel).read_bytes())
    for rel, staged in relocated.graph_files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(staged.read_bytes())

    reloaded = StaticTable.from_metadata(relocated.metadata_location)
    assert reloaded.location() == local_iceberg_location(dest)
    result = reloaded.scan().to_arrow()
    assert result.num_rows == 2
    assert sorted(result.column("id").to_pylist()) == [1, 2]

    for staged in relocated.graph_files.values():
        if staged.suffix == ".json":
            assert local_iceberg_location(src / "t") not in staged.read_text(encoding="utf-8")


def test_relocate_rejects_non_table(tmp_path):
    with pytest.raises(ValueError, match="Not an Iceberg table directory"):
        relocate_iceberg_table(tmp_path / "missing", "s3://bucket/prefix", tmp_path / "staging")
