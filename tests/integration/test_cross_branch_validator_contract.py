from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from benchbox.validation.bundle import format_summary, validate_bundles, validation_failed

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


def _run_validator(paths: list[Path]) -> tuple[int, str]:
    results = validate_bundles(paths)
    return (1 if validation_failed(results) else 0), format_summary(results)


def _fake_result() -> SimpleNamespace:
    return SimpleNamespace(
        benchmark_name="tpch",
        platform="duckdb",
        scale_factor=0.01,
        total_queries=22,
        duration_seconds=12.34,
    )


def _minimal_schema_v2_bundle() -> dict:
    return {
        "version": "2.0",
        "run": {
            "id": "test-run-id",
            "timestamp": "2026-04-29T00:00:00Z",
            "total_duration_ms": 12340,
        },
        "benchmark": {"id": "tpch", "scale_factor": 0.01},
        "platform": {"name": "duckdb"},
        "summary": {"validation": "passed", "queries": {"total": 22, "passed": 22, "failed": 0}},
        "phases": {"validation": {"status": "PASSED"}},
        "queries": [{"id": f"Q{i}", "ms": 100.0} for i in range(1, 23)],
    }


def test_writer_emits_hash_format_validator_accepts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sub = importlib.import_module("benchbox.cli.commands.submit")

    monkeypatch.setenv("BENCHBOX_MACHINE_ID_SALT", "integration-test-community-publish-salt")

    src = tmp_path / "tpch_duckdb.json"
    src.write_text(json.dumps(_minimal_schema_v2_bundle()), encoding="utf-8")

    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 0, f"benchbox submit failed: {result.output}"
    bundle_path = out_dir / "bundle" / src.name
    assert bundle_path.is_file()

    rc, output = _run_validator([bundle_path])

    assert rc == 0, (
        f"Vendored published-results validator rejected develop's bundle. "
        f"This is the same class of release-blocker as the 2026-04-29 dry-run. "
        f"Output:\n{output}"
    )
    assert "0 error(s), 0 warning(s), 0 override(s) required" in output, output


def test_writer_companion_hashes_validator_accepts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sub = importlib.import_module("benchbox.cli.commands.submit")

    monkeypatch.setenv("BENCHBOX_MACHINE_ID_SALT", "integration-test-community-publish-salt")

    src = tmp_path / "tpch_duckdb.json"
    src.write_text(json.dumps(_minimal_schema_v2_bundle()), encoding="utf-8")
    (tmp_path / "tpch_duckdb.plans.json").write_text('{"plans": []}', encoding="utf-8")
    (tmp_path / "tpch_duckdb.tuning.json").write_text('{"tuning": {}}', encoding="utf-8")

    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])
    assert result.exit_code == 0, result.output

    bundle_path = out_dir / "bundle" / src.name

    rc, output = _run_validator([bundle_path])

    assert rc == 0, f"Validator rejected bundle with companions. Output:\n{output}"
    assert "0 error(s)" in output, output


def test_validator_rejects_symlinked_bundle(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    real = tmp_path / "real_target.json"
    real.write_text(json.dumps(_minimal_schema_v2_bundle()), encoding="utf-8")

    bundle_filename = "tpch_duckdb.json"
    symlink_path = bundle_dir / bundle_filename
    symlink_path.symlink_to(real)

    real_hash = hashlib.sha256(real.read_bytes()).hexdigest()
    manifest = {
        "bundle_file": bundle_filename,
        "bundle_hash": real_hash,
        "companion_hashes": {},
    }
    (bundle_dir / "submission-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    rc, output = _run_validator([symlink_path])

    assert rc != 0, f"Validator wrongly accepted a symlinked bundle. Output:\n{output}"
    assert "symlink" in output.lower(), f"Expected a symlink-specific error. Output:\n{output}"


def test_validator_surfaces_both_missing_manifest_fields(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    bundle_path = bundle_dir / "tpch_duckdb.json"
    bundle_path.write_text(json.dumps(_minimal_schema_v2_bundle()), encoding="utf-8")
    (bundle_dir / "submission-manifest.json").write_text("{}", encoding="utf-8")

    _rc, output = _run_validator([bundle_path])

    assert "bundle_file" in output, output
    assert "bundle_hash" in output, output


def test_validator_prefers_per_bundle_manifest_over_legacy(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    bundle_filename = "tpch_duckdb.json"
    bundle_path = bundle_dir / bundle_filename
    bundle_path.write_text(json.dumps(_minimal_schema_v2_bundle()), encoding="utf-8")

    actual_hash = hashlib.sha256(bundle_path.read_bytes()).hexdigest()

    per_bundle_manifest = {
        "bundle_file": bundle_filename,
        "bundle_hash": actual_hash,
        "companion_hashes": {},
    }
    legacy_manifest = {
        "bundle_file": bundle_filename,
        "bundle_hash": "0" * 64,
        "companion_hashes": {},
    }
    (bundle_dir / "tpch_duckdb.manifest.json").write_text(json.dumps(per_bundle_manifest), encoding="utf-8")
    (bundle_dir / "submission-manifest.json").write_text(json.dumps(legacy_manifest), encoding="utf-8")

    rc, output = _run_validator([bundle_path])

    assert rc == 0, f"Validator must accept bundle when per-bundle manifest matches. Output:\n{output}"
    assert "Bundle hash mismatch" not in output, output


def test_validator_skips_per_bundle_manifest_during_discovery(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    bundle_filename = "tpch_duckdb.json"
    bundle_path = bundle_dir / bundle_filename
    bundle_path.write_text(json.dumps(_minimal_schema_v2_bundle()), encoding="utf-8")

    actual_hash = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    manifest = {
        "bundle_file": bundle_filename,
        "bundle_hash": actual_hash,
        "companion_hashes": {},
    }
    manifest_path = bundle_dir / "tpch_duckdb.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    rc, output = _run_validator([bundle_path, manifest_path])

    assert rc == 0, f"Validator must skip explicit-path *.manifest.json files. Output:\n{output}"
    assert "Validated 1 bundle(s)" in output or "1 bundle" in output, output
