from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.core.upload_validation import (
    ManifestComparisonResult,
    RemoteManifestValidator,
    UploadValidationEngine,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def make_manifest(
    *,
    benchmark: str = "tpch",
    scale_factor: float | int = 1.0,
    tables: dict | None = None,
    compression: dict | None = None,
    **extras: object,
) -> dict:
    manifest = {
        "version": 2,
        "benchmark": benchmark,
        "scale_factor": scale_factor,
        "compression": compression or {"enabled": False, "type": None, "level": None},
        "tables": tables or {"a": {"formats": {"tbl": [{}]}}},
        "formats": ["tbl"],
    }
    manifest.update(extras)
    return manifest


class FakeRemoteFS:
    def __init__(self):
        self._store: dict[str, bytes] = {}

    def file_exists(self, remote_path: str) -> bool:
        return remote_path in self._store

    def read_file(self, remote_path: str) -> bytes:
        if remote_path not in self._store:
            raise FileNotFoundError(remote_path)
        return self._store[remote_path]

    def write_file(self, remote_path: str, content: bytes) -> None:
        self._store[remote_path] = content

    def list_files(self, remote_path: str, pattern: str = "*") -> list[str]:
        return [k for k in self._store if k.startswith(remote_path)]


@pytest.fixture()
def local_manifest(tmp_path: Path) -> Path:
    manifest = make_manifest(
        tables={
            "customer": {
                "formats": {
                    "tbl": [
                        {"path": "customer.tbl", "size_bytes": 10, "row_count": 150000},
                    ]
                }
            },
            "orders": {
                "formats": {
                    "tbl": [
                        {"path": "orders.tbl.1.zst", "size_bytes": 5, "row_count": 100},
                        {"path": "orders.tbl.2.zst", "size_bytes": 5, "row_count": 100},
                    ]
                }
            },
        },
        format_preference=["tbl"],
    )
    path = tmp_path / "_datagen_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "customer.tbl").write_bytes(b"x" * 10)
    (tmp_path / "orders.tbl.1.zst").write_bytes(b"x" * 5)
    (tmp_path / "orders.tbl.2.zst").write_bytes(b"x" * 5)
    return path


def test_manifest_comparison_matches():
    local = make_manifest(tables={"a": [{}], "b": [{}]}, formats=[])
    remote = json.loads(json.dumps(local))
    comp = RemoteManifestValidator().compare_manifests(local, remote)
    assert isinstance(comp, ManifestComparisonResult)
    assert comp.manifests_match is True
    assert not comp.differences


def test_should_upload_false_when_remote_matches(local_manifest: Path, monkeypatch):
    remote_root = "dbfs:/Volumes/workspace/schema/vol"
    fs = FakeRemoteFS()

    manifest = json.loads(local_manifest.read_text(encoding="utf-8"))
    fs.write_file(f"{remote_root}/_datagen_manifest.json", json.dumps(manifest).encode("utf-8"))
    for _table, table_data in (manifest.get("tables") or {}).items():
        for e in table_data["formats"]["tbl"]:
            fs.write_file(f"{remote_root}/{e['path']}", b"x" * int(e.get("size_bytes", 0)))

    engine = UploadValidationEngine(fs)
    should_upload, result = engine.should_upload_data(remote_root, local_manifest)
    assert should_upload is False
    assert result.is_valid is True


def test_should_upload_true_when_remote_missing_manifest(local_manifest: Path):
    remote_root = "dbfs:/Volumes/workspace/schema/vol"
    engine = UploadValidationEngine(FakeRemoteFS())
    should_upload, result = engine.should_upload_data(remote_root, local_manifest)
    assert should_upload is True
    assert result.is_valid is False


def test_validate_remote_files_exist_reports_missing(monkeypatch):
    fs = FakeRemoteFS()
    v = RemoteManifestValidator(fs)
    m = {"version": 2, "tables": {"x": {"formats": {"tbl": [{"path": "x.tbl", "size_bytes": 10}]}}}}
    res = v.validate_remote_files_exist("dbfs:/Volumes/workspace/x", m)
    assert not res.is_valid
    assert any("missing" in e for e in res.errors)


def test_manifest_comparison_scale_factor_mismatch():

    local = make_manifest()
    remote = json.loads(json.dumps(local))
    remote["scale_factor"] = 10.0

    comp = RemoteManifestValidator().compare_manifests(local, remote)
    assert comp.manifests_match is False
    assert comp.scale_factor_match is False
    assert any("Scale factor mismatch" in d for d in comp.differences)


def test_manifest_comparison_benchmark_mismatch():

    local = make_manifest()
    remote = json.loads(json.dumps(local))
    remote["benchmark"] = "tpcds"

    comp = RemoteManifestValidator().compare_manifests(local, remote)
    assert comp.manifests_match is False
    assert comp.benchmark_match is False
    assert any("Benchmark mismatch" in d for d in comp.differences)


def test_manifest_comparison_compression_mismatch():

    local = make_manifest()
    remote = json.loads(json.dumps(local))
    remote["compression"] = {"enabled": True, "type": "zstd", "level": 3}

    comp = RemoteManifestValidator().compare_manifests(local, remote)
    assert comp.manifests_match is False
    assert comp.compression_match is False
    assert any("Compression mismatch" in d for d in comp.differences)


def test_manifest_comparison_table_count_mismatch():

    local = make_manifest(
        tables={
            "a": {"formats": {"tbl": [{}]}},
            "b": {"formats": {"tbl": [{}]}},
        }
    )
    remote = json.loads(json.dumps(local))
    remote["tables"] = {"a": {"formats": {"tbl": [{}]}}}

    comp = RemoteManifestValidator().compare_manifests(local, remote)
    assert comp.manifests_match is False
    assert comp.table_count_match is False
    assert any("Table count mismatch" in d for d in comp.differences)


def test_manifest_comparison_file_count_mismatch():
    local = make_manifest(tables={"a": {"formats": {"tbl": [{}, {}]}}})
    remote = json.loads(json.dumps(local))
    remote["tables"] = {"a": {"formats": {"tbl": [{}]}}}

    comp = RemoteManifestValidator().compare_manifests(local, remote)
    assert comp.manifests_match is False
    assert comp.file_count_match is False
    assert any("File count mismatch" in d for d in comp.differences)


def test_manifest_comparison_int_float_tolerance():

    local = make_manifest(scale_factor=1)
    remote = json.loads(json.dumps(local))
    remote["scale_factor"] = 1.0

    comp = RemoteManifestValidator().compare_manifests(local, remote)
    assert comp.manifests_match is True
    assert comp.scale_factor_match is True


def test_force_upload_always_uploads():

    remote_root = "dbfs:/Volumes/workspace/schema/vol"
    fs = FakeRemoteFS()

    manifest = make_manifest(tables={"a": {"formats": {"tbl": [{"path": "a.tbl", "size_bytes": 10}]}}})
    fs.write_file(f"{remote_root}/_datagen_manifest.json", json.dumps(manifest).encode("utf-8"))
    fs.write_file(f"{remote_root}/a.tbl", b"x" * 10)

    tmp = Path(__file__).parent.parent.parent / "_project"
    tmp.mkdir(exist_ok=True)
    local_manifest_path = tmp / "test_manifest_force.json"
    local_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        engine = UploadValidationEngine(fs)

        should_upload_no_force, _ = engine.should_upload_data(remote_root, local_manifest_path, force_upload=False)

        should_upload_force, result = engine.should_upload_data(remote_root, local_manifest_path, force_upload=True)

        assert should_upload_no_force is False
        assert should_upload_force is True
        assert any("Force upload" in w for w in result.warnings)
    finally:
        if local_manifest_path.exists():
            local_manifest_path.unlink()


def test_should_upload_when_manifest_corrupted():

    remote_root = "dbfs:/Volumes/workspace/schema/vol"
    fs = FakeRemoteFS()

    fs.write_file(f"{remote_root}/_datagen_manifest.json", b"corrupted json {{{")

    manifest = make_manifest(tables={"a": {"formats": {"tbl": [{"path": "a.tbl", "size_bytes": 10}]}}})
    tmp = Path(__file__).parent.parent.parent / "_project"
    tmp.mkdir(exist_ok=True)
    local_manifest_path = tmp / "test_manifest_corrupted.json"
    local_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        engine = UploadValidationEngine(fs)
        should_upload, result = engine.should_upload_data(remote_root, local_manifest_path)

        assert should_upload is True
        assert result.is_valid is False
    finally:
        if local_manifest_path.exists():
            local_manifest_path.unlink()


def test_remote_manifest_attached_to_validation_result():

    remote_root = "dbfs:/Volumes/workspace/schema/vol"
    fs = FakeRemoteFS()

    manifest = make_manifest(tables={"a": {"formats": {"tbl": [{"path": "a.tbl", "size_bytes": 10}]}}})
    fs.write_file(f"{remote_root}/_datagen_manifest.json", json.dumps(manifest).encode("utf-8"))
    fs.write_file(f"{remote_root}/a.tbl", b"x" * 10)

    tmp = Path(__file__).parent.parent.parent / "_project"
    tmp.mkdir(exist_ok=True)
    local_manifest_path = tmp / "test_manifest_attached.json"
    local_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        engine = UploadValidationEngine(fs)
        should_upload, result = engine.should_upload_data(remote_root, local_manifest_path)

        assert should_upload is False
        assert result.is_valid is True

        assert result.remote_manifest is not None
        assert result.remote_manifest["benchmark"] == "tpch"
        assert result.remote_manifest["scale_factor"] == 1.0
    finally:
        if local_manifest_path.exists():
            local_manifest_path.unlink()


def test_print_validation_report_with_valid_manifest(caplog):

    import logging

    from benchbox.core.validation.engines import ValidationResult

    caplog.set_level(logging.INFO, logger="benchbox.core.upload_validation")

    validation_result = ValidationResult(
        is_valid=True,
        errors=[],
        warnings=[],
        remote_manifest=make_manifest(
            tables={
                "customer": {"formats": {"tbl": [{}]}},
                "orders": {"formats": {"tbl": [{}]}},
            }
        ),
    )

    engine = UploadValidationEngine()
    engine.print_validation_report(validation_result, verbose=False)

    assert "✅ Valid TPCH data found for scale factor 1.0" in caplog.text
    assert "✅ Data validation PASSED (2 tables)" in caplog.text
    assert "Total files:" not in caplog.text


def test_print_validation_report_verbose_mode(caplog):

    import logging

    from benchbox.core.validation.engines import ValidationResult

    caplog.set_level(logging.INFO, logger="benchbox.core.upload_validation")

    validation_result = ValidationResult(
        is_valid=True,
        errors=[],
        warnings=[],
        remote_manifest=make_manifest(
            benchmark="tpcds",
            scale_factor=10.0,
            tables={
                "catalog_sales": {
                    "formats": {"zst": [{"path": "catalog_sales.dat.1.zst"}, {"path": "catalog_sales.dat.2.zst"}]}
                },
                "store_sales": {"formats": {"zst": [{"path": "store_sales.dat.1.zst"}]}},
            },
            compression={"enabled": True, "type": "zstd", "level": 3},
        ),
    )

    engine = UploadValidationEngine()
    engine.print_validation_report(validation_result, verbose=True)

    assert "✅ Valid TPCDS data found for scale factor 10.0" in caplog.text
    assert "✅ Data validation PASSED (2 tables)" in caplog.text

    assert "Total files: 3" in caplog.text
    assert "Compression: zstd (level 3)" in caplog.text
    assert "catalog_sales" in caplog.text
    assert "store_sales" in caplog.text


def test_print_validation_report_skips_invalid_results():

    from benchbox.core.validation.engines import ValidationResult

    validation_result = ValidationResult(
        is_valid=False,
        errors=["Some error"],
        warnings=[],
    )

    engine = UploadValidationEngine()

    engine.print_validation_report(validation_result, verbose=False)


def test_print_validation_report_skips_missing_manifest(caplog):

    import logging

    from benchbox.core.validation.engines import ValidationResult

    caplog.set_level(logging.INFO)

    validation_result = ValidationResult(
        is_valid=True,
        errors=[],
        warnings=[],
    )

    engine = UploadValidationEngine()
    engine.print_validation_report(validation_result, verbose=False)

    assert "✅" not in caplog.text


def test_should_upload_data_logs_validation_messages(caplog):

    import logging

    caplog.set_level(logging.INFO, logger="benchbox.core.upload_validation")

    remote_root = "dbfs:/Volumes/workspace/schema/vol"
    fs = FakeRemoteFS()

    manifest = make_manifest(
        tables={"customer": [{"path": "customer.tbl", "size_bytes": 10}]},
        formats=[],
    )
    fs.write_file(f"{remote_root}/_datagen_manifest.json", json.dumps(manifest).encode("utf-8"))
    fs.write_file(f"{remote_root}/customer.tbl", b"x" * 10)

    tmp = Path(__file__).parent.parent.parent / "_project"
    tmp.mkdir(exist_ok=True)
    local_manifest_path = tmp / "test_manifest_logging.json"
    local_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        engine = UploadValidationEngine(fs)
        should_upload, result = engine.should_upload_data(remote_root, local_manifest_path, verbose=False)

        assert should_upload is False
        assert result.is_valid is True

        assert "✅ Valid TPCH data found for scale factor 1.0" in caplog.text
        assert "✅ Data validation PASSED (1 tables)" in caplog.text
        assert "Skipping upload (existing data is valid)" in caplog.text
    finally:
        if local_manifest_path.exists():
            local_manifest_path.unlink()


def test_print_validation_report_zero_tables_edge_case(caplog):

    import logging

    from benchbox.core.validation.engines import ValidationResult

    caplog.set_level(logging.WARNING, logger="benchbox.core.upload_validation")

    validation_result = ValidationResult(
        is_valid=True,
        errors=[],
        warnings=[],
        remote_manifest={
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "tables": {},
            "compression": {"enabled": False, "type": None, "level": None},
        },
    )

    engine = UploadValidationEngine()
    engine.print_validation_report(validation_result, verbose=False)

    assert "Remote manifest has zero tables" in caplog.text
    assert "validation passed but data may be incomplete" in caplog.text


def test_print_validation_report_missing_scale_factor(caplog):

    import logging

    from benchbox.core.validation.engines import ValidationResult

    caplog.set_level(logging.INFO, logger="benchbox.core.upload_validation")

    manifest = make_manifest(tables={"customer": [{}]}, formats=[])
    manifest.pop("scale_factor")

    validation_result = ValidationResult(
        is_valid=True,
        errors=[],
        warnings=[],
        remote_manifest=manifest,
    )

    engine = UploadValidationEngine()
    engine.print_validation_report(validation_result, verbose=False)

    assert "✅ Valid TPCH data found for scale factor unknown" in caplog.text


def test_print_validation_report_malformed_compression(caplog):

    import logging

    from benchbox.core.validation.engines import ValidationResult

    caplog.set_level(logging.INFO, logger="benchbox.core.upload_validation")

    manifest = make_manifest(tables={"customer": [{}]}, formats=[])
    manifest["compression"] = None

    validation_result = ValidationResult(
        is_valid=True,
        errors=[],
        warnings=[],
        remote_manifest=manifest,
    )

    engine = UploadValidationEngine()
    engine.print_validation_report(validation_result, verbose=True)

    assert "✅ Valid TPCH data found for scale factor 1.0" in caplog.text
    assert "Compression:" not in caplog.text


def test_databricks_adapter_passes_verbose_flag():
    remote_root = "dbfs:/Volumes/workspace/schema/vol"
    fs = FakeRemoteFS()

    manifest = make_manifest(
        tables={"customer": [{"path": "customer.tbl", "size_bytes": 10}]},
        formats=[],
    )
    fs.write_file(f"{remote_root}/_datagen_manifest.json", json.dumps(manifest).encode("utf-8"))
    fs.write_file(f"{remote_root}/customer.tbl", b"x" * 10)

    tmp = Path(__file__).parent.parent.parent / "_project"
    tmp.mkdir(exist_ok=True)
    local_manifest_path = tmp / "test_manifest_verbose.json"
    local_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        engine = UploadValidationEngine(fs)

        should_upload_no_verbose, result_no_verbose = engine.should_upload_data(
            remote_root, local_manifest_path, verbose=False
        )
        assert should_upload_no_verbose is False
        assert result_no_verbose.is_valid is True

        should_upload_verbose, result_verbose = engine.should_upload_data(
            remote_root, local_manifest_path, verbose=True
        )
        assert should_upload_verbose is False
        assert result_verbose.is_valid is True

        assert result_no_verbose.is_valid == result_verbose.is_valid
    finally:
        if local_manifest_path.exists():
            local_manifest_path.unlink()


