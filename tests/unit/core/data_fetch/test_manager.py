from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pytest

from benchbox.core.data_fetch import (
    ChecksumMismatchError,
    DataFetchError,
    ExtractionRequiredError,
    TableEntry,
    compute_manifest_hash,
    compute_manifest_identity_hash,
    fetch_data,
    load_manifest,
    verify_logical_content,
)
from benchbox.core.data_fetch.logical_hash import (
    logical_columns_from_schema,
    logical_table_hash_from_parquet,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


ALPHA_PAYLOAD = b"alpha-table-row-bytes"
BETA_PAYLOAD = b"beta-table-row-bytes"

ALPHA_SHA = hashlib.sha256(ALPHA_PAYLOAD).hexdigest()
BETA_SHA = hashlib.sha256(BETA_PAYLOAD).hexdigest()
ARCHIVE_SHA = "00" * 32
MANIFEST_HASH_PLACEHOLDER = "a" * 64


def _write_manifest(tmp: Path, archive_sha: str = ARCHIVE_SHA) -> Path:
    body = (
        f'dataset_version    = "test-v1"\n'
        f'manifest_hash      = "{MANIFEST_HASH_PLACEHOLDER}"\n'
        f'data_archive_hash  = "{archive_sha}"\n'
        f'url                = "https://example.com/test.tar.zst"\n'
        f'archive_sha256     = "{archive_sha}"\n'
        f'license_file       = "DATA-LICENSE.md"\n\n'
        f"[[tables]]\n"
        f'name      = "alpha"\n'
        f'file      = "alpha.parquet"\n'
        f'sha256    = "{ALPHA_SHA}"\n'
        f"row_count = {len(ALPHA_PAYLOAD)}\n\n"
        f"[[tables]]\n"
        f'name      = "beta"\n'
        f'file      = "beta.parquet"\n'
        f'sha256    = "{BETA_SHA}"\n'
        f"row_count = {len(BETA_PAYLOAD)}\n"
    )
    p = tmp / "data_manifest.toml"
    p.write_text(body)
    manifest_hash = compute_manifest_hash(p)
    p.write_text(body.replace(MANIFEST_HASH_PLACEHOLDER, manifest_hash))
    return p


def test_air_gapped_pre_populated_returns_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "alpha.parquet").write_bytes(ALPHA_PAYLOAD)
    (out_dir / "beta.parquet").write_bytes(BETA_PAYLOAD)
    manifest_path = _write_manifest(tmp_path)

    result = fetch_data("test", manifest_path, out_dir)
    assert result == out_dir


def test_pre_populated_with_bad_sha_short_circuits_before_download(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "alpha.parquet").write_bytes(b"corrupted contents")
    (out_dir / "beta.parquet").write_bytes(BETA_PAYLOAD)
    manifest_path = _write_manifest(tmp_path)

    download_calls: list[str] = []

    def fake_downloader(url, dest, expected_sha256=None):
        download_calls.append(url)
        return Path(dest)

    with pytest.raises(ChecksumMismatchError) as excinfo:
        fetch_data("test", manifest_path, out_dir, downloader=fake_downloader)
    assert "alpha.parquet" in excinfo.value.path
    assert excinfo.value.expected_sha256 == ALPHA_SHA
    assert download_calls == []


def test_empty_dir_downloads_then_raises_extraction_required(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    download_calls: list[tuple[str, Path]] = []

    def fake_downloader(url, dest, expected_sha256=None):
        download_calls.append((url, Path(dest)))
        Path(dest).write_bytes(b"pretend-archive-bytes")
        return Path(dest)

    with pytest.raises(ExtractionRequiredError) as excinfo:
        fetch_data("test", manifest_path, out_dir, downloader=fake_downloader)
    assert excinfo.value.output_dir == str(out_dir)
    assert "test.tar.zst" in excinfo.value.archive_path
    assert download_calls == [("https://example.com/test.tar.zst", out_dir / "test.tar.zst")]


def test_existing_verified_archive_raises_extraction_required_without_redownload(tmp_path: Path) -> None:
    archive_payload = b"complete-archive"
    archive_sha = hashlib.sha256(archive_payload).hexdigest()
    manifest_path = _write_manifest(tmp_path, archive_sha=archive_sha)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "test.tar.zst").write_bytes(archive_payload)

    def fail_downloader(url, dest, expected_sha256=None):
        raise AssertionError("verified archive cache should not be re-downloaded")

    with pytest.raises(ExtractionRequiredError) as excinfo:
        fetch_data("test", manifest_path, out_dir, downloader=fail_downloader)

    assert excinfo.value.archive_path == str(out_dir / "test.tar.zst")
    assert excinfo.value.output_dir == str(out_dir)


def test_post_extraction_returns_dir(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def extracting_downloader(url, dest, expected_sha256=None):

        Path(dest).write_bytes(b"pretend-archive-bytes")
        (out_dir / "alpha.parquet").write_bytes(ALPHA_PAYLOAD)
        (out_dir / "beta.parquet").write_bytes(BETA_PAYLOAD)
        return Path(dest)

    result = fetch_data("test", manifest_path, out_dir, downloader=extracting_downloader)
    assert result == out_dir


def test_post_extraction_with_bad_sha_raises_checksum_mismatch(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def buggy_extracting_downloader(url, dest, expected_sha256=None):
        Path(dest).write_bytes(b"pretend-archive-bytes")

        (out_dir / "alpha.parquet").write_bytes(b"truncated")
        (out_dir / "beta.parquet").write_bytes(BETA_PAYLOAD)
        return Path(dest)

    with pytest.raises(ChecksumMismatchError) as excinfo:
        fetch_data("test", manifest_path, out_dir, downloader=buggy_extracting_downloader)
    assert "alpha.parquet" in excinfo.value.path
    assert excinfo.value.expected_sha256 == ALPHA_SHA


_LOGICAL_SCHEMA = {"id": "integer", "val": "character varying"}


def _write_parquet(con: object, path: Path, rows: list[tuple[int, str]]) -> None:
    values = ", ".join(f"({i}, '{v}')" for i, v in rows)
    con.execute(f"COPY (SELECT * FROM (VALUES {values}) t(id, val)) TO '{path}' (FORMAT PARQUET)")


def _write_logical_manifest(tmp: Path, con: object, tables: dict[str, list[tuple[int, str]]]) -> Path:
    data_dir = tmp / "data"
    data_dir.mkdir(exist_ok=True)
    cols = logical_columns_from_schema(_LOGICAL_SCHEMA)
    entries = []
    for name, rows in tables.items():
        parquet = data_dir / f"{name}.parquet"
        _write_parquet(con, parquet, rows)
        h = logical_table_hash_from_parquet(con=con, parquet_path=parquet, table=name, columns=cols)

        entries.append(
            TableEntry(
                name=name,
                file=f"{name}.parquet",
                sha256=hashlib.sha256(parquet.read_bytes()).hexdigest(),
                row_count=h.row_count,
                logical_sha256=h.sha256,
                schema=_LOGICAL_SCHEMA,
            )
        )
    manifest_hash = compute_manifest_identity_hash(
        dataset_version="logical-v1",
        data_archive_hash="cd" * 32,
        url="https://example.com/x.tar.zst",
        license_file="DATA-LICENSE.md",
        tables=entries,
    )
    lines = [
        'dataset_version = "logical-v1"',
        f'manifest_hash = "{manifest_hash}"',
        f'data_archive_hash = "{"cd" * 32}"',
        'url = "https://example.com/x.tar.zst"',
        f'archive_sha256 = "{"00" * 32}"',
        'license_file = "DATA-LICENSE.md"',
        "",
    ]
    for e in entries:
        lines += [
            "[[tables]]",
            f'name = "{e.name}"',
            f'file = "{e.file}"',
            f'sha256 = "{e.sha256}"',
            f'logical_sha256 = "{e.logical_sha256}"',
            f"row_count = {e.row_count}",
            'schema.id = "integer"',
            'schema.val = "character varying"',
            "",
        ]
    p = tmp / "data_manifest.toml"
    p.write_text("\n".join(lines))
    return p


def test_verify_logical_content_passes_on_matching_data(tmp_path: Path) -> None:
    con = duckdb.connect()
    manifest_path = _write_logical_manifest(tmp_path, con, {"t1": [(1, "a"), (2, "b")], "t2": [(1, "x")]})
    manifest = load_manifest(manifest_path)
    assert manifest.is_logical
    assert verify_logical_content(manifest, tmp_path / "data") == []


def test_verify_logical_content_survives_reencoded_parquet(tmp_path: Path) -> None:
    con = duckdb.connect()
    manifest_path = _write_logical_manifest(tmp_path, con, {"t1": [(2, "b"), (1, "a")]})
    manifest = load_manifest(manifest_path)

    con.execute("SET threads=1")
    _write_parquet(con, tmp_path / "data" / "t1.parquet", [(1, "a"), (2, "b")])
    assert verify_logical_content(manifest, tmp_path / "data") == []


def test_verify_logical_content_flags_content_change(tmp_path: Path) -> None:
    con = duckdb.connect()
    manifest_path = _write_logical_manifest(tmp_path, con, {"t1": [(1, "a"), (2, "b")]})
    manifest = load_manifest(manifest_path)
    _write_parquet(con, tmp_path / "data" / "t1.parquet", [(1, "a"), (2, "CHANGED")])
    mismatches = verify_logical_content(manifest, tmp_path / "data")
    assert [m.kind for m in mismatches] == ["hash_mismatch"]
    assert mismatches[0].table == "t1"


def test_verify_logical_content_flags_missing_file(tmp_path: Path) -> None:
    con = duckdb.connect()
    manifest_path = _write_logical_manifest(tmp_path, con, {"t1": [(1, "a")]})
    manifest = load_manifest(manifest_path)
    (tmp_path / "data" / "t1.parquet").unlink()
    mismatches = verify_logical_content(manifest, tmp_path / "data")
    assert [m.kind for m in mismatches] == ["missing"]


def test_verify_logical_content_requires_logical_manifest(tmp_path: Path) -> None:
    legacy_manifest = _write_manifest(tmp_path)
    manifest = load_manifest(legacy_manifest)
    with pytest.raises(DataFetchError, match="logical-mode manifest"):
        verify_logical_content(manifest, tmp_path)


