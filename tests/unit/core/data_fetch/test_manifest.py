from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.data_fetch import (
    DataManifest,
    ManifestValidationError,
    TableEntry,
    compute_manifest_hash,
    compute_manifest_identity_hash,
    load_manifest,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


_HEX_B = "b" * 64
_HEX_C = "c" * 64
_HEX_D = "d" * 64
_HASH_PLACEHOLDER = "0" * 64

_MINIMAL = f"""\
dataset_version    = "test-v1"
manifest_hash      = "{_HASH_PLACEHOLDER}"
data_archive_hash  = "{_HEX_B}"
url                = "https://example.com/test.tar.zst"
archive_sha256     = "{_HEX_C}"
license_file       = "DATA-LICENSE.md"

[[tables]]
name      = "t1"
file      = "t1.parquet"
sha256    = "33"
row_count = 100

[[tables]]
name      = "t2"
file      = "t2.parquet"
sha256    = "44"
row_count = 200

[provenance]
source_doi          = "10.7910/DVN/2QYZBT"
retrieval_timestamp = "2026-05-10T14:00:00Z"
"""


def _write(tmp: Path, body: str) -> Path:
    p = tmp / "data_manifest.toml"
    p.write_text(body)
    manifest_hash = compute_manifest_hash(p)
    p.write_text(body.replace(_HASH_PLACEHOLDER, manifest_hash))
    return p


def test_load_minimal_manifest(tmp_path: Path) -> None:
    p = _write(tmp_path, _MINIMAL)
    m = load_manifest(p)
    assert isinstance(m, DataManifest)
    assert m.dataset_version == "test-v1"
    assert m.manifest_hash == compute_manifest_hash(p)
    assert len(m.tables) == 2
    assert m.tables[0].name == "t1"
    assert m.tables[1].row_count == 200
    assert m.provenance["source_doi"] == "10.7910/DVN/2QYZBT"


def test_table_lookup_by_name(tmp_path: Path) -> None:
    p = _write(tmp_path, _MINIMAL)
    m = load_manifest(p)
    assert m.table("t2").file == "t2.parquet"
    with pytest.raises(KeyError):
        m.table("nope")


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ManifestValidationError, match="not found"):
        load_manifest(tmp_path / "does-not-exist.toml")


def test_malformed_toml_raises(tmp_path: Path) -> None:
    p = tmp_path / "data_manifest.toml"
    p.write_text("not valid toml = = =\n")
    with pytest.raises(ManifestValidationError, match="not valid TOML"):
        load_manifest(p)


def test_missing_required_top_key_raises(tmp_path: Path) -> None:
    body = _MINIMAL.replace('dataset_version    = "test-v1"\n', "")
    p = _write(tmp_path, body)
    with pytest.raises(ManifestValidationError, match="missing required keys"):
        load_manifest(p)


def test_manifest_hash_mismatch_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, _MINIMAL)
    p.write_text(
        p.read_text().replace(
            'url                = "https://example.com/test.tar.zst"',
            'url = "https://evil.test/archive.tar.zst"',
        )
    )
    with pytest.raises(ManifestValidationError, match="manifest_hash mismatch"):
        load_manifest(p)


def test_manifest_hash_ignores_archive_sha256_transport_checksum(tmp_path: Path) -> None:
    p = _write(tmp_path, _MINIMAL)
    original_hash = compute_manifest_hash(p)
    p.write_text(p.read_text().replace(f'archive_sha256     = "{_HEX_C}"', f'archive_sha256     = "{_HEX_D}"'))

    assert compute_manifest_hash(p) == original_hash
    assert load_manifest(p).archive_sha256 == _HEX_D


def test_missing_table_field_raises(tmp_path: Path) -> None:
    bad = _MINIMAL.replace('file      = "t1.parquet"\n', "")
    p = _write(tmp_path, bad)
    with pytest.raises(ManifestValidationError, match="missing field"):
        load_manifest(p)


def test_tables_must_be_array(tmp_path: Path) -> None:
    body = (
        f'dataset_version="x"\nmanifest_hash="{_HASH_PLACEHOLDER}"\n'
        'data_archive_hash="x"\nurl="x"\n'
        'archive_sha256="x"\nlicense_file="x"\ntables = "this should be a list"\n'
    )
    p = _write(tmp_path, body)
    with pytest.raises(ManifestValidationError, match="must be an array"):
        load_manifest(p)


_T1_LOGICAL = "1a" * 32
_T2_LOGICAL = "2b" * 32
_LOGICAL_TABLES = [
    TableEntry(
        name="t1",
        file="t1.parquet",
        sha256=_HEX_B,
        row_count=100,
        logical_sha256=_T1_LOGICAL,
        schema={"id": "integer", "name": "character varying"},
    ),
    TableEntry(
        name="t2",
        file="t2.parquet",
        sha256=_HEX_C,
        row_count=200,
        logical_sha256=_T2_LOGICAL,
        schema={"id": "integer"},
    ),
]
_LOGICAL_URL = "https://example.com/logical.tar.zst"
_LOGICAL_DAH = "cd" * 32


def _logical_body(tables: list[TableEntry], *, data_archive_hash: str = _LOGICAL_DAH) -> str:
    manifest_hash = compute_manifest_identity_hash(
        dataset_version="test-logical-v1",
        data_archive_hash=data_archive_hash,
        url=_LOGICAL_URL,
        license_file="DATA-LICENSE.md",
        tables=tables,
    )
    lines = [
        'dataset_version = "test-logical-v1"',
        f'manifest_hash = "{manifest_hash}"',
        f'data_archive_hash = "{data_archive_hash}"',
        f'url = "{_LOGICAL_URL}"',
        f'archive_sha256 = "{_HEX_D}"',
        'license_file = "DATA-LICENSE.md"',
        "",
    ]
    for t in tables:
        lines += ["[[tables]]", f'name = "{t.name}"', f'file = "{t.file}"', f'sha256 = "{t.sha256}"']
        if t.logical_sha256 is not None:
            lines.append(f'logical_sha256 = "{t.logical_sha256}"')
        lines.append(f"row_count = {t.row_count}")
        for col, col_type in t.schema.items():
            lines.append(f'schema.{col} = "{col_type}"')
        lines.append("")
    return "\n".join(lines)


def _write_raw(tmp: Path, body: str) -> Path:
    p = tmp / "data_manifest.toml"
    p.write_text(body)
    return p


def test_load_logical_manifest(tmp_path: Path) -> None:
    p = _write_raw(tmp_path, _logical_body(_LOGICAL_TABLES))
    m = load_manifest(p)
    assert m.is_logical
    assert m.table("t1").logical_sha256 == _T1_LOGICAL
    assert m.table("t1").schema == {"id": "integer", "name": "character varying"}
    assert m.data_archive_hash == _LOGICAL_DAH


def test_legacy_manifest_is_not_logical(tmp_path: Path) -> None:
    p = _write(tmp_path, _MINIMAL)
    assert load_manifest(p).is_logical is False


def test_logical_manifest_hash_stable_across_transport_byte_change(tmp_path: Path) -> None:
    p = _write_raw(tmp_path, _logical_body(_LOGICAL_TABLES))
    original = load_manifest(p)
    rebuilt = p.read_text()

    rebuilt = rebuilt.replace(f'\nsha256 = "{_HEX_B}"', f'\nsha256 = "{"e" * 64}"')
    rebuilt = rebuilt.replace(f'archive_sha256 = "{_HEX_D}"', f'archive_sha256 = "{"f" * 64}"')
    p.write_text(rebuilt)
    reloaded = load_manifest(p)
    assert reloaded.manifest_hash == original.manifest_hash
    assert reloaded.table("t1").sha256 == "e" * 64
    assert reloaded.table("t1").logical_sha256 == _T1_LOGICAL


def test_logical_manifest_tamper_on_logical_hash_detected(tmp_path: Path) -> None:
    p = _write_raw(tmp_path, _logical_body(_LOGICAL_TABLES))
    p.write_text(p.read_text().replace(f'logical_sha256 = "{_T1_LOGICAL}"', f'logical_sha256 = "{"ff" * 32}"'))
    with pytest.raises(ManifestValidationError, match="manifest_hash mismatch"):
        load_manifest(p)


def test_logical_manifest_tamper_on_schema_detected(tmp_path: Path) -> None:
    p = _write_raw(tmp_path, _logical_body(_LOGICAL_TABLES))
    p.write_text(p.read_text().replace('schema.name = "character varying"', 'schema.name = "integer"'))
    with pytest.raises(ManifestValidationError, match="manifest_hash mismatch"):
        load_manifest(p)


def test_mixed_logical_mode_rejected(tmp_path: Path) -> None:
    partial = [
        _LOGICAL_TABLES[0],
        TableEntry(name="t2", file="t2.parquet", sha256=_HEX_C, row_count=200, schema={"id": "integer"}),
    ]

    body = _logical_body(_LOGICAL_TABLES).replace(f'logical_sha256 = "{_T2_LOGICAL}"\n', "")
    p = _write_raw(tmp_path, body)
    with pytest.raises(ManifestValidationError, match="present on some tables but missing"):
        load_manifest(p)
    assert partial


