from __future__ import annotations

import importlib.util
import json
import os
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "publication" / "check_explorer_compat.py"

SPEC = importlib.util.spec_from_file_location("check_explorer_compat", SCRIPT_PATH)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


@pytest.fixture
def mock_artifact_dir(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!DOCTYPE html><html><head><title>Results Explorer</title></head><body><div id='root'></div><script src='/assets/index.js'></script></body></html>",
        encoding="utf-8",
    )
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (assets / "index.js").write_text("console.log('explorer');", encoding="utf-8")
    (assets / "style.css").write_text("body { margin: 0; }", encoding="utf-8")
    return dist


def test_normalize_type() -> None:
    assert checker.normalize_type("varchar") == "VARCHAR"
    assert checker.normalize_type("TEXT") == "VARCHAR"
    assert checker.normalize_type("int4") == "INTEGER"
    assert checker.normalize_type("double") == "DOUBLE"
    assert checker.normalize_type("boolean") == "BOOLEAN"
    assert checker.normalize_type("CUSTOM_TYPE") == "CUSTOM_TYPE"


def test_schema_versions_definitions() -> None:
    assert checker.SUPPORTED_SCHEMA_VERSIONS == (15,)
    assert checker.CURRENT_SCHEMA_VERSION == 15
    from _project.scripts.explorer_pipeline.contract import EXPLORER_READ_MODEL_VERSION

    assert checker.CURRENT_SCHEMA_VERSION == EXPLORER_READ_MODEL_VERSION
    assert checker.SUPPORTED_SCHEMA_VERSIONS == (EXPLORER_READ_MODEL_VERSION,)

    cols = checker.get_table_columns_for_version(15)
    assert "results" in cols
    assert "metadata" in cols
    assert "result_environment" in cols
    assert "query_executions" in cols
    assert "benchmark_rankings" in cols
    assert "cohort_metadata" in cols
    assert "result_basis_availability" in cols
    assert "cpu_identity_provenance" in cols["result_environment"]
    assert "run_type" in cols["query_executions"]
    assert "override_rules" in cols["results"]
    assert "override_evidence" in cols["results"]
    assert "override_approver" in cols["results"]
    assert "override_expires" in cols["results"]
    assert "benchmark_support_status" in cols["results"]
    assert "throughput_at_size" in cols["results"]
    assert "stream_count" in cols["results"]
    assert cols["results"]["template_evidence"] == "VARCHAR"
    assert "throughput_at_size" in cols["benchmark_rankings"]
    for table in ("benchmark_rankings", "benchmark_matrix_cells", "cohort_metadata"):
        assert "stream_count" in cols[table]


def test_invalid_schema_version_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported schema version"):
        checker.get_table_columns_for_version(99)
    with pytest.raises(ValueError, match="Unsupported schema version"):
        checker.get_table_columns_for_version(7)
    with pytest.raises(ValueError, match="Unsupported schema version"):
        checker.get_table_columns_for_version(8)
    with pytest.raises(ValueError, match="Unsupported schema version"):
        checker.get_views_for_version(7)
    with pytest.raises(ValueError, match="Unsupported schema version"):
        checker.get_indexes_for_version(8)


def test_in_memory_schema_creation_and_validation() -> None:
    for version in checker.SUPPORTED_SCHEMA_VERSIONS:
        con = checker.create_in_memory_schema(version)
        try:
            errors = checker.validate_database_schema(con, expected_version=version)
            assert errors == [], f"Schema validation failed for v{version}: {errors}"

            query_errors = checker.validate_database_queries(con, version=version)
            assert query_errors == [], f"Query validation failed for v{version}: {query_errors}"
        finally:
            con.close()


def test_check_schema_compatibility_all_supported() -> None:
    results = checker.check_schema_compatibility()
    for version, errors in results.items():
        assert errors == [], f"Version v{version} had unexpected errors: {errors}"
    assert set(results.keys()) == set(checker.SUPPORTED_SCHEMA_VERSIONS)


def test_validate_database_schema_detects_missing_table() -> None:
    con = checker.create_in_memory_schema(15)
    try:
        con.execute("DROP TABLE cohort_metadata")
        errors = checker.validate_database_schema(con, expected_version=15)
        assert any("missing required tables for v15: cohort_metadata" in err for err in errors)
    finally:
        con.close()


def test_validate_database_schema_detects_missing_column() -> None:
    con = checker.create_in_memory_schema(15)
    try:
        con.execute("ALTER TABLE results DROP COLUMN funding")
        errors = checker.validate_database_schema(con, expected_version=15)
        assert any("table 'results' missing required columns: funding" in err for err in errors)
    finally:
        con.close()


def test_validate_database_schema_detects_missing_template_evidence() -> None:
    con = checker.create_in_memory_schema(15)
    try:
        con.execute("ALTER TABLE results DROP COLUMN template_evidence")
        errors = checker.validate_database_schema(con, expected_version=15)
        assert any("table 'results' missing required columns: template_evidence" in err for err in errors)
    finally:
        con.close()


def test_validate_database_schema_detects_missing_view() -> None:
    con = checker.create_in_memory_schema(15)
    try:
        con.execute("DROP VIEW result_detail_metrics")
        errors = checker.validate_database_schema(con, expected_version=15)
        assert any("missing required views for v15: result_detail_metrics" in err for err in errors)
    finally:
        con.close()


def test_validate_database_schema_detects_missing_view_column() -> None:
    con = checker.create_in_memory_schema(15)
    try:
        con.execute("CREATE OR REPLACE VIEW result_detail_metrics AS SELECT result_id FROM results")
        errors = checker.validate_database_schema(con, expected_version=15)
        assert any(
            "view 'result_detail_metrics' missing required columns:" in err and "override_rules" in err
            for err in errors
        )
        assert any(
            "view 'result_detail_metrics' missing required columns:" in err and "benchmark_support_status" in err
            for err in errors
        )
        assert any(
            "view 'result_detail_metrics' missing required columns:" in err
            and "throughput_at_size" in err
            and "stream_count" in err
            for err in errors
        )
        assert any(
            "view 'result_detail_metrics' missing required columns:" in err and "template_evidence" in err
            for err in errors
        )
    finally:
        con.close()


def test_validate_database_schema_version_mismatch() -> None:
    con = checker.create_in_memory_schema(15)
    try:
        con.execute("UPDATE metadata SET read_model_version = 8")
        errors = checker.validate_database_schema(con, expected_version=15)
        assert any("read_model_version mismatch: expected 15, got 8" in err for err in errors)
    finally:
        con.close()


def test_compute_file_and_directory_checksums(mock_artifact_dir: Path) -> None:
    checksums = checker.compute_directory_checksums(mock_artifact_dir)
    assert "index.html" in checksums
    assert "assets/index.js" in checksums
    assert "assets/style.css" in checksums
    assert len(checksums) == 3

    addr = checker.compute_content_address(checksums)
    assert isinstance(addr, str) and len(addr) == 64


def test_compute_directory_checksums_anchored_exclude(mock_artifact_dir: Path) -> None:
    nested = mock_artifact_dir / "assets" / "manifest.json"
    nested.write_text('{"fake": true}', encoding="utf-8")
    checksums = checker.compute_directory_checksums(mock_artifact_dir)
    assert "assets/manifest.json" in checksums
    assert "manifest.json" not in checksums
    nested_sums = mock_artifact_dir / "assets" / "SHA256SUMS"
    nested_sums.write_text("fake", encoding="utf-8")
    checksums2 = checker.compute_directory_checksums(mock_artifact_dir)
    assert "assets/SHA256SUMS" in checksums2
    assert "SHA256SUMS" not in checksums2


def test_generate_artifact_manifest(mock_artifact_dir: Path) -> None:
    manifest = checker.generate_artifact_manifest(mock_artifact_dir, write=True)
    assert manifest["bundle"] == "explorer_app"
    assert manifest["file_count"] == 3
    assert (mock_artifact_dir / "manifest.json").is_file()
    assert (mock_artifact_dir / "SHA256SUMS").is_file()
    assert manifest["read_model_version"] == checker.CURRENT_SCHEMA_VERSION
    assert manifest["supported_versions"] == list(checker.SUPPORTED_SCHEMA_VERSIONS)
    assert "github_sha" in manifest
    assert "contract_version" in manifest

    errors, info = checker.validate_artifact_bundle(mock_artifact_dir)
    assert errors == []
    assert info is not None
    assert info["content_address"] == manifest["content_address"]


def test_generate_manifest_embeds_github_sha(mock_artifact_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SHA", "abc123def456")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/develop")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    manifest = checker.generate_artifact_manifest(mock_artifact_dir, write=False)
    assert manifest["github_sha"] == "abc123def456"
    assert manifest["github_ref"] == "refs/heads/develop"
    assert manifest["github_event_name"] == "push"


def test_validate_artifact_bundle_valid_directory(mock_artifact_dir: Path) -> None:
    errors, info = checker.validate_artifact_bundle(mock_artifact_dir)
    assert errors == []
    assert info is not None
    assert info["file_count"] == 3
    assert info["js_bundles"] == 1
    assert info["css_bundles"] == 1


def test_validate_artifact_bundle_require_manifest_missing(mock_artifact_dir: Path) -> None:
    errors_ok, _ = checker.validate_artifact_bundle(mock_artifact_dir, require_manifest=False)
    assert errors_ok == []
    errors_req, _ = checker.validate_artifact_bundle(mock_artifact_dir, require_manifest=True)
    assert any("manifest.json is missing" in err for err in errors_req)
    assert any("SHA256SUMS is missing" in err for err in errors_req)


def test_validate_artifact_bundle_nonexistent_path(tmp_path: Path) -> None:
    non_existent = tmp_path / "does_not_exist"
    errors, info = checker.validate_artifact_bundle(non_existent)
    assert any("does not exist" in err for err in errors)
    assert info is None


def test_validate_artifact_bundle_missing_index(tmp_path: Path) -> None:
    dist = tmp_path / "no_index"
    dist.mkdir()
    assets = dist / "assets"
    assets.mkdir()
    (assets / "index.js").write_text("console.log('hi');", encoding="utf-8")
    (assets / "index.css").write_text("body {}", encoding="utf-8")

    errors, _ = checker.validate_artifact_bundle(dist)
    assert any("missing 'index.html'" in err for err in errors)


def test_validate_artifact_bundle_empty_file(mock_artifact_dir: Path) -> None:
    empty_file = mock_artifact_dir / "empty.txt"
    empty_file.touch()

    errors, _ = checker.validate_artifact_bundle(mock_artifact_dir)
    assert any("empty file in artifact bundle: empty.txt" in err for err in errors)


def test_validate_artifact_bundle_manifest_checksum_mismatch(mock_artifact_dir: Path) -> None:
    checker.generate_artifact_manifest(mock_artifact_dir, write=True)

    (mock_artifact_dir / "index.html").write_text("TAMPERED", encoding="utf-8")

    errors, _ = checker.validate_artifact_bundle(mock_artifact_dir)
    assert any("manifest content_address mismatch" in err or "file checksum mismatch" in err for err in errors)


def test_validate_artifact_bundle_malformed_sha256sums(mock_artifact_dir: Path) -> None:
    checker.generate_artifact_manifest(mock_artifact_dir, write=True)
    (mock_artifact_dir / "SHA256SUMS").write_text("garbage_token\n", encoding="utf-8")
    errors, _ = checker.validate_artifact_bundle(mock_artifact_dir)
    assert any("malformed SHA256SUMS" in err for err in errors)


def test_validate_artifact_bundle_malformed_sha256sums_line_continuation(mock_artifact_dir: Path) -> None:
    checker.generate_artifact_manifest(mock_artifact_dir, write=True)
    (mock_artifact_dir / "SHA256SUMS").write_text(
        "not-a-hash  index.html\n" + (mock_artifact_dir / "SHA256SUMS").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    errors, _ = checker.validate_artifact_bundle(mock_artifact_dir)
    assert any("malformed SHA256SUMS hash" in err for err in errors)


def test_validate_artifact_bundle_zip_archive(mock_artifact_dir: Path, tmp_path: Path) -> None:
    zip_path = tmp_path / "explorer_app.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for p in mock_artifact_dir.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(mock_artifact_dir))

    errors, info = checker.validate_artifact_bundle(zip_path)
    assert errors == []
    assert info is not None
    assert info["file_count"] == 3


def test_validate_artifact_bundle_tar_archive(mock_artifact_dir: Path, tmp_path: Path) -> None:
    tar_path = tmp_path / "explorer_app.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for p in mock_artifact_dir.rglob("*"):
            if p.is_file():
                tf.add(p, arcname=str(p.relative_to(mock_artifact_dir)))

    errors, info = checker.validate_artifact_bundle(tar_path)
    assert errors == []
    assert info is not None
    assert info["file_count"] == 3


def test_cli_no_args_requires_schema_only_or_inputs(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = checker.main([])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "--schema-only" in captured.err


def test_cli_default_schema_checks(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = checker.main(["--schema-only"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Results Explorer Compatibility" in captured.out
    assert "Schema v15" in captured.out
    assert "Schema v12" not in captured.out
    assert "Schema v11" not in captured.out
    assert "Schema v10" not in captured.out
    assert "Schema v9" not in captured.out
    assert "All Results Explorer compatibility checks PASSED" in captured.out


def test_cli_json_mode(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = checker.main(["--schema-only", "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "passed"
    assert data["current_version"] == 15
    assert "v15" in data["schema_checks"]
    assert "v12" not in data["schema_checks"]
    assert "v11" not in data["schema_checks"]
    assert "v10" not in data["schema_checks"]


def test_cli_specific_schema_version(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = checker.main(["--schema-only", "--schema-versions", "15"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Schema v15" in captured.out
    assert "Schema v12" not in captured.out
    assert "Schema v11" not in captured.out
    assert "Schema v10" not in captured.out


def test_cli_invalid_schema_version(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = checker.main(["--schema-only", "--schema-versions", "99"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "Unsupported schema version 99" in captured.err


def test_cli_invalid_schema_version_7(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = checker.main(["--schema-only", "--schema-versions", "7"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "Unsupported schema version 7" in captured.err


def test_cli_empty_schema_versions(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = checker.main(["--schema-only", "--schema-versions", ""])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "Invalid --schema-versions format" in captured.err


def test_cli_artifact_check(mock_artifact_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = checker.main(["--artifact", str(mock_artifact_dir), "--generate-manifest"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Explorer Application Artifact" in captured.out
    assert "Content address" in captured.out
    assert "All Results Explorer compatibility checks PASSED" in captured.out


def test_cli_artifact_verify_requires_manifest(mock_artifact_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = checker.main(["--artifact", str(mock_artifact_dir), "--require-manifest"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "FAILED" in captured.err or "manifest.json is missing" in captured.out or "FAILED" in captured.out

    checker.generate_artifact_manifest(mock_artifact_dir, write=True)
    exit_code2 = checker.main(["--artifact", str(mock_artifact_dir), "--require-manifest"])
    assert exit_code2 == 0


def test_cli_generate_and_require_mutually_exclusive(
    mock_artifact_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = checker.main(["--artifact", str(mock_artifact_dir), "--generate-manifest", "--require-manifest"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "mutually exclusive" in captured.err


def test_cli_artifact_not_found(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = checker.main(["--artifact", "/tmp/non_existent_explorer_dir_12345"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "Artifact path not found" in captured.err


def test_cli_db_path_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import duckdb

    db_file = tmp_path / "test.duckdb"
    with duckdb.connect(str(db_file)) as con:
        for stmt in checker.generate_schema_ddl(15):
            con.execute(stmt)
        con.execute("INSERT INTO metadata VALUES (15)")

    exit_code = checker.main(["--db-path", str(db_file)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Database Snapshot" in captured.out
    assert "All Results Explorer compatibility checks PASSED" in captured.out


def test_cli_db_path_not_found(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = checker.main(["--db-path", "/tmp/non_existent_db_12345.duckdb"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "DuckDB database file not found" in captured.err
