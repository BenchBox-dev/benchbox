from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVELOP_VALIDATOR = REPO_ROOT / "scripts" / "validate_submission.py"


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
            "timestamp": "2026-05-02T00:00:00Z",
            "total_duration_ms": 12340,
        },
        "benchmark": {"id": "tpch", "scale_factor": 0.01},
        "platform": {"name": "duckdb"},
        "summary": {"validation": "passed", "queries": {"total": 22, "passed": 22, "failed": 0}},
        "queries": [{"id": f"Q{i}", "ms": 100.0, "status": "SUCCESS"} for i in range(1, 23)],
    }


def _hosted_bundle_layout(tmp_path: Path) -> tuple[Path, Path, dict]:
    sub = importlib.import_module("benchbox.cli.commands.submit")

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    source_path = bundle_dir / "tpch_duckdb.json"
    source_path.write_text(json.dumps(_minimal_schema_v2_bundle()), encoding="utf-8")
    source_path.write_bytes(sub._canonical_submission_file_bytes(source_path))

    manifest = sub._build_submission_manifest(
        source_path=source_path,
        companions=[],
        result=_fake_result(),
        submitted_by="contract-test@example.invalid",
        submission_path="hosted-service",
    )

    manifest_path = bundle_dir / f"{source_path.stem}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return source_path, manifest_path, manifest


def _run_validator(bundle_path: Path, cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(DEVELOP_VALIDATOR), str(bundle_path)],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_hosted_bundle_passes_develop_validator(tmp_path: Path) -> None:
    bundle_path, _manifest_path, _manifest = _hosted_bundle_layout(tmp_path)

    rc, output = _run_validator(bundle_path, cwd=tmp_path)

    assert rc == 0, (
        "develop validator rejected hosted-submit bundle. "
        "This is hosted-submit / develop drift — the same class as the "
        "2026-04-29 publishing release-blocker. Fix the divergence "
        "(submit.py manifest builder vs scripts/validate_submission.py "
        "schema) before landing.\n\n"
        f"Output:\n{output}"
    )
    assert "0 error(s)" in output, output


def test_hosted_bundle_with_corrupted_hash_is_rejected(tmp_path: Path) -> None:
    bundle_path, manifest_path, manifest = _hosted_bundle_layout(tmp_path)

    manifest["bundle_hash"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    rc, output = _run_validator(bundle_path, cwd=tmp_path)

    assert rc != 0, "validator must reject a hosted bundle with a wrong hash"
    assert "hash" in output.lower(), f"expected hash error, got:\n{output}"


def test_hosted_partial_bundle_is_rejected_even_with_a_valid_manifest(tmp_path: Path) -> None:
    sub = importlib.import_module("benchbox.cli.commands.submit")
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    source_path = bundle_dir / "tpch_duckdb.json"
    payload = _minimal_schema_v2_bundle()
    payload["queries"].pop()
    payload["summary"]["queries"] = {"total": 21, "passed": 21, "failed": 0}
    source_path.write_text(json.dumps(payload), encoding="utf-8")
    source_path.write_bytes(sub._canonical_submission_file_bytes(source_path))
    result = _fake_result()
    result.total_queries = 21
    manifest = sub._build_submission_manifest(
        source_path=source_path,
        companions=[],
        result=result,
        submitted_by="contract-test@example.invalid",
        submission_path="hosted-service",
    )
    (bundle_dir / f"{source_path.stem}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    rc, output = _run_validator(source_path, cwd=tmp_path)

    assert rc != 0
    assert "covers 21 of 22 canonical queries" in output


def test_hosted_bundle_missing_required_schema_key_is_rejected(tmp_path: Path) -> None:
    sub = importlib.import_module("benchbox.cli.commands.submit")

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    source_path = bundle_dir / "tpch_duckdb.json"

    bad = _minimal_schema_v2_bundle()
    del bad["queries"]
    source_path.write_text(json.dumps(bad), encoding="utf-8")
    source_path.write_bytes(sub._canonical_submission_file_bytes(source_path))

    manifest = sub._build_submission_manifest(
        source_path=source_path,
        companions=[],
        result=_fake_result(),
        submitted_by="contract-test@example.invalid",
        submission_path="hosted-service",
    )
    (bundle_dir / f"{source_path.stem}.manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    rc, output = _run_validator(source_path, cwd=tmp_path)

    assert rc != 0, "validator must reject a bundle missing required schema-v2 keys"
    assert "queries" in output.lower(), f"expected `queries` error, got:\n{output}"


def test_hosted_manifest_schema_matches_pr_flow_for_required_keys(tmp_path: Path) -> None:
    sub = importlib.import_module("benchbox.cli.commands.submit")

    src = tmp_path / "tpch_duckdb.json"
    src.write_text(json.dumps(_minimal_schema_v2_bundle()), encoding="utf-8")

    pr_manifest = sub._build_submission_manifest(
        source_path=src,
        companions=[],
        result=_fake_result(),
        submitted_by="x@example.invalid",
        submission_path="PR-based",
    )
    hosted_manifest = sub._build_submission_manifest(
        source_path=src,
        companions=[],
        result=_fake_result(),
        submitted_by="x@example.invalid",
        submission_path="hosted-service",
    )

    assert set(pr_manifest.keys()) == set(hosted_manifest.keys()), (
        f"hosted vs PR manifest key drift. PR={set(pr_manifest)}, hosted={set(hosted_manifest)}"
    )
    assert pr_manifest["submission_path"] == "PR-based"
    assert hosted_manifest["submission_path"] == "hosted-service"
    for key in ("bundle_file", "bundle_hash", "benchmark", "platform", "scale_factor"):
        assert pr_manifest[key] == hosted_manifest[key], f"{key} drift"


def test_hosted_and_pr_manifest_built_via_dispatch_call_sites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner

    sub = importlib.import_module("benchbox.cli.commands.submit")

    monkeypatch.setenv("BENCHBOX_MACHINE_ID_SALT", "integration-test-community-publish-salt")
    monkeypatch.setenv("BENCHBOX_ACKNOWLEDGE_HOSTED_SUBMISSION", "1")

    src = tmp_path / "tpch_duckdb.json"
    src.write_text(json.dumps(_minimal_schema_v2_bundle()), encoding="utf-8")

    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    pr_result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])
    assert pr_result.exit_code == 0, pr_result.output
    pr_manifest_path = out_dir / "tpch_duckdb.manifest.json"
    assert pr_manifest_path.exists(), pr_result.output
    pr_manifest = json.loads(pr_manifest_path.read_text(encoding="utf-8"))

    captured_manifest: dict[str, dict] = {}

    def fake_submit_hosted_bundle(*, manifest: dict, bundle_hash: str, **_kwargs):
        captured_manifest["payload"] = manifest
        from benchbox.cli.submit_service import HostedSubmitResult, make_idempotency_key

        return HostedSubmitResult(
            status="accepted",
            idempotency_key=make_idempotency_key("https://hosted.invalid", bundle_hash),
            submission_id="fake-id",
        )

    monkeypatch.setattr(sub, "submit_hosted_bundle", fake_submit_hosted_bundle)
    monkeypatch.setattr(
        sub,
        "resolve_submission_token",
        lambda _service_url: SimpleNamespace(token="fake-token"),
    )
    monkeypatch.setattr(
        sub,
        "record_hosted_submission",
        lambda **_kwargs: tmp_path / "fake_history.json",
    )

    hosted_result = CliRunner().invoke(
        sub.submit,
        [
            str(src),
            "--service",
            "https://hosted.invalid",
            "--no-wait",
            "--visibility",
            "public",
        ],
    )
    assert hosted_result.exit_code == 0, hosted_result.output
    assert "payload" in captured_manifest, "hosted dispatch never called submit_hosted_bundle"
    hosted_manifest = captured_manifest["payload"]

    assert set(pr_manifest.keys()) == set(hosted_manifest.keys()), (
        f"hosted vs PR manifest key drift via dispatch call sites. PR={set(pr_manifest)}, hosted={set(hosted_manifest)}"
    )
    assert pr_manifest["submission_path"] == "PR-based"
    assert hosted_manifest["submission_path"] == "hosted-service"
    for key in ("bundle_file", "bundle_hash", "benchmark", "platform", "scale_factor"):
        assert pr_manifest[key] == hosted_manifest[key], f"{key} drift via dispatch call sites"
