from __future__ import annotations

import hashlib
import importlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from benchbox.core.results.anonymization import public_pseudonym_salt_path
from benchbox.core.results.canonical_json import canonical_json_bytes

sub = importlib.import_module("benchbox.cli.commands.submit")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def _community_publish_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHBOX_MACHINE_ID_SALT", "unit-test-community-publish-salt")


def _fake_result() -> SimpleNamespace:
    return SimpleNamespace(
        benchmark_name="tpch",
        platform="duckdb",
        scale_factor=0.01,
        total_queries=22,
        duration_seconds=12.34,
    )


def _valid_submission_bundle() -> dict:
    return {
        "version": "2.1",
        "run": {
            "id": "abc123",
            "timestamp": "2026-05-03T00:00:00",
            "total_duration_ms": 500,
        },
        "benchmark": {"id": "tpch", "name": "TPC-H", "scale_factor": 0.01},
        "platform": {"name": "duckdb", "version": "1.3.0"},
        "summary": {"validation": "passed", "queries": {"total": 22, "passed": 22, "failed": 0}},
        "phases": {"validation": {"status": "PASSED"}},
        "queries": [{"id": f"Q{i}", "ms": 123, "status": "SUCCESS"} for i in range(1, 23)],
    }


def _write_valid_submission_bundle(path: Path) -> None:
    path.write_text(json.dumps(_valid_submission_bundle()), encoding="utf-8")


def _write_unavailable_cost_total_bundle(path: Path) -> None:
    bundle = _valid_submission_bundle()
    bundle["normalized_cost"] = {
        "normalized_cost_usd": None,
        "cost_model_version": "2026.05.0",
        "cost_model_source": "benchbox.core.cost.pricing",
        "cost_scope": "compute_only",
        "cost_status": "unavailable",
        "billing_unit": "not_applicable",
        "pricing_region": "not_applicable",
        "deployment": {
            "cloud_provider": None,
            "cloud_region": None,
            "instance_type": None,
            "node_count": None,
        },
    }
    bundle["cost"] = {"total_usd": 0, "model": "estimated"}
    path.write_text(json.dumps(bundle), encoding="utf-8")


def _eof_fixed(data: bytes) -> bytes:
    return data.rstrip(b"\n") + b"\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_file_bytes(path: Path) -> bytes:
    return canonical_json_bytes(json.loads(path.read_text(encoding="utf-8")))


def _hostname_bundle(path: Path) -> None:
    raw_bundle = _valid_submission_bundle()
    raw_bundle["environment"] = {"hostname": "developer-macbook-pro.local"}
    path.write_text(json.dumps(raw_bundle), encoding="utf-8")


def _salted_token(salt: str, prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(f'{salt}|{prefix}|{value}'.encode()).hexdigest()[:12]}"


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("BENCHBOX_MACHINE_ID_SALT", raising=False)
    return public_pseudonym_salt_path()


def test_submit_generates_salt_once_and_keeps_it_out_of_all_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    salt_path = _clean_env(monkeypatch)
    src = tmp_path / "tpch_duckdb.json"
    _hostname_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))
    assert not salt_path.exists()

    first_dir = tmp_path / "first"
    first = CliRunner().invoke(sub.submit, [str(src), "--output", str(first_dir)])
    assert first.exit_code == 0, first.output
    salt = salt_path.read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[0-9a-f]{64}", salt)
    assert first.output.count("Created a private pseudonym salt") == 1
    assert "BENCHBOX_MACHINE_ID_SALT" in first.output

    second_dir = tmp_path / "second"
    second = CliRunner().invoke(sub.submit, [str(src), "--output", str(second_dir)])
    assert second.exit_code == 0, second.output
    assert "Created a private pseudonym salt" not in second.output
    assert salt_path.read_text(encoding="utf-8").strip() == salt

    for output_dir, text in ((first_dir, first.output), (second_dir, second.output)):
        assert salt not in text
        for produced in output_dir.rglob("*"):
            if produced.is_file():
                assert salt not in produced.read_text(encoding="utf-8")
        packaged = json.loads((output_dir / "bundle" / src.name).read_text(encoding="utf-8"))
        assert packaged["environment"]["hostname"] == _salted_token(salt, "host", "developer-macbook-pro.local")


def test_submit_env_override_creates_no_salt_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    salt_path = _clean_env(monkeypatch)
    monkeypatch.setenv("BENCHBOX_MACHINE_ID_SALT", "explicit-override-salt")
    src = tmp_path / "tpch_duckdb.json"
    _hostname_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(tmp_path / "out")])

    assert result.exit_code == 0, result.output
    assert not salt_path.exists()
    assert "Created a private pseudonym salt" not in result.output
    packaged = json.loads((tmp_path / "out" / "bundle" / src.name).read_text(encoding="utf-8"))
    assert packaged["environment"]["hostname"] == _salted_token(
        "explicit-override-salt", "host", "developer-macbook-pro.local"
    )


def test_submit_dry_run_never_creates_the_salt_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    salt_path = _clean_env(monkeypatch)
    src = tmp_path / "tpch_duckdb.json"
    _hostname_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    result = CliRunner().invoke(sub.submit, [str(src), "--dry-run", "--output", str(tmp_path / "out")])

    assert result.exit_code == 0, result.output
    assert not salt_path.exists()
    assert not salt_path.parent.exists()
    assert "no pseudonym salt exists yet" in result.output


def test_submit_dry_run_uses_the_existing_salt_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    salt_path = _clean_env(monkeypatch)
    salt_path.parent.mkdir(parents=True)
    salt_path.write_text("existing-file-salt", encoding="utf-8")
    src = tmp_path / "tpch_duckdb.json"
    _hostname_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    result = CliRunner().invoke(sub.submit, [str(src), "--dry-run", "--output", str(tmp_path / "out")])

    assert result.exit_code == 0, result.output
    assert "no pseudonym salt exists yet" not in result.output
    assert salt_path.read_text(encoding="utf-8") == "existing-file-salt"


def test_submit_refusals_happen_before_any_salt_file_is_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    salt_path = _clean_env(monkeypatch)
    src = tmp_path / "tpch_duckdb_partial.json"
    src.write_text('{"schema_version": "2.0"}', encoding="utf-8")
    partial = _fake_result()
    partial.total_queries = 2
    partial.successful_queries = 1
    partial.failed_queries = 1
    partial.validation_status = "PARTIAL"
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (partial, {}))

    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(tmp_path / "out")])

    assert result.exit_code == 1
    assert not salt_path.exists()
    assert not salt_path.parent.exists()


def test_submit_refuses_when_the_salt_store_is_unusable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    salt_path = _clean_env(monkeypatch)
    salt_path.parent.mkdir(parents=True)
    salt_path.write_text("", encoding="utf-8")
    src = tmp_path / "ok.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(tmp_path / "out")])

    assert result.exit_code == 1
    assert "Submission refused" in result.output
    assert "BENCHBOX_MACHINE_ID_SALT" in result.output
    assert "delete" in result.output
    assert not (tmp_path / "out").exists()


def test_submit_same_home_gives_the_same_hosted_bundle_hash_and_new_home_a_new_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    salt_path = _clean_env(monkeypatch)
    src = tmp_path / "tpch_duckdb.json"
    _hostname_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))
    monkeypatch.setattr(sub, "resolve_submission_token", lambda _url: SimpleNamespace(token="t", source="keyring"))
    hashes: list[str] = []

    def fake_upload(**kwargs):
        hashes.append(kwargs["bundle_hash"])
        return sub.HostedSubmitResult(status="published", idempotency_key="key")

    monkeypatch.setattr(sub, "submit_hosted_bundle", fake_upload)

    for _ in range(2):
        assert CliRunner().invoke(sub.submit, [str(src), "--service"]).exit_code == 0
    salt_path.unlink()
    assert CliRunner().invoke(sub.submit, [str(src), "--service"]).exit_code == 0

    assert hashes[0] == hashes[1]
    assert hashes[2] != hashes[0]


def test_submit_requires_file_or_last(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sub, "console", SimpleNamespace(print=lambda *a, **k: None))
    result = CliRunner().invoke(sub.submit, [])
    assert result.exit_code == 1


def test_submit_last_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sub, "find_latest_result", lambda *_a, **_k: None)
    result = CliRunner().invoke(sub.submit, ["--last"])
    assert result.exit_code == 1
    assert "No results found" in result.output


def test_submit_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)

    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(
        sub.submit,
        [str(src), "--dry-run", "--output", str(out_dir)],
    )

    assert result.exit_code == 0
    assert "Dry-run" in result.output or "dry-run" in result.output.lower()
    assert not out_dir.exists()


def test_submit_creates_output_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)

    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(
        sub.submit,
        [str(src), "--output", str(out_dir)],
    )

    assert result.exit_code == 0
    assert (out_dir / "bundle" / src.name).exists()
    assert (out_dir / "tpch_duckdb.manifest.json").exists()
    assert (out_dir / "CONTRIBUTING.md").exists()


def test_submit_refuses_partial_query_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb_partial.json"
    src.write_text('{"schema_version": "2.0"}', encoding="utf-8")
    partial = _fake_result()
    partial.total_queries = 2
    partial.successful_queries = 1
    partial.failed_queries = 1
    partial.validation_status = "PARTIAL"
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (partial, {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 1
    assert "not a clean pass" in result.output
    assert "1 failed query" in result.output
    assert not out_dir.exists()


def test_submit_refuses_unvalidated_with_distinct_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb_unvalidated.json"
    src.write_text('{"schema_version": "2.0"}', encoding="utf-8")
    unvalidated = _fake_result()
    unvalidated.validation_status = "not_validated"
    unvalidated.failed_queries = 0
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (unvalidated, {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 1
    assert "unvalidated" in result.output
    assert "validation_status=not_validated" in result.output
    assert "schema violation" not in result.output.lower()
    assert not out_dir.exists()


def test_submit_contributing_md_includes_canonical_required_items(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)

    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 0
    contributing = (out_dir / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for required in (
        "published-results",
        "generate_corpus_inventory",
        "validate_submission",
        "benchbox.dev/docs/contributing-results.html",
        "<result>.manifest.json",
    ):
        assert required in contributing, f"missing {required!r} in packaged CONTRIBUTING.md"
    assert "submission-manifest.json" not in contributing


def test_submit_prints_next_steps_with_pr_target_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)

    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 0
    for required in (
        "published-results",
        "generate_corpus_inventory.py --write",
        "validate_submission.py",
        "results: tpch duckdb sf0.01",
        "results-data/bundles/tpch_duckdb.json",
    ):
        assert required in result.output, f"missing {required!r} in submit next-steps"


def test_submit_dry_run_matches_real_run_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)

    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--dry-run", "--output", str(out_dir)])

    assert result.exit_code == 0
    assert not out_dir.exists(), "dry-run must not write files"
    for required in (
        "Dry-run preview",
        "published-results",
        "generate_corpus_inventory.py --write",
        "results: tpch duckdb sf0.01",
        "(dry run; no files written)",
    ):
        assert required in result.output, f"missing {required!r} in dry-run output"


def test_submit_dry_run_validation_rejects_pr_package_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb_invalid_cost.json"
    _write_unavailable_cost_total_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--dry-run", "--output", str(out_dir)])

    assert result.exit_code == 1
    assert "Submission validation failed" in result.output
    assert "cannot accompany cost_status 'unavailable'" in result.output
    assert "Dry-run preview" not in result.output
    assert not out_dir.exists()


def _write_flat_plateau_bundle(path: Path) -> None:
    queries = [{"id": f"Q{i}", "ms": 4400.0 + (i % 5) * 15.0, "status": "SUCCESS"} for i in range(1, 23)]
    bundle = {
        "version": "2.1",
        "run": {"id": "plateau", "timestamp": "2026-05-03T00:00:00", "total_duration_ms": 100000},
        "benchmark": {"id": "tpch", "name": "TPC-H", "scale_factor": 1.0},
        "platform": {"name": "duckdb", "version": "1.3.0"},
        "summary": {"validation": "passed", "queries": {"total": 22, "passed": 22, "failed": 0}},
        "phases": {"validation": {"status": "PASSED"}},
        "queries": queries,
    }
    path.write_text(json.dumps(bundle), encoding="utf-8")


def _write_sibling_override_artifact(bundle_path: Path) -> None:
    bundle_path.with_name(f"{bundle_path.stem}.override.json").write_text(
        json.dumps(
            {
                "rules": [{"rule": "timing-plateau", "rule_version": "1"}],
                "reason": "verified fixed-overhead regime against profiler trace",
                "evidence": "https://example.com/trace",
                "expires": "single-batch",
                "approver": "reviewer2",
            }
        ),
        encoding="utf-8",
    )


def test_submit_dry_run_refuses_unsatisfied_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb_plateau.json"
    _write_flat_plateau_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--dry-run", "--output", str(out_dir)])

    assert result.exit_code == 1
    assert "Submission validation failed" in result.output
    assert "requires overrides" in result.output
    assert "timing-plateau" in result.output
    assert "Dry-run preview" not in result.output
    assert not out_dir.exists()


def test_submit_dry_run_accepts_satisfied_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb_plateau.json"
    _write_flat_plateau_bundle(src)
    _write_sibling_override_artifact(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--dry-run", "--output", str(out_dir)])

    assert result.exit_code == 0, result.output
    assert "Dry-run preview" in result.output


def test_submit_real_run_refuses_short_coverage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    bundle = _valid_submission_bundle()
    bundle["queries"] = [{"id": "Q1", "ms": 123, "status": "SUCCESS"}]
    bundle["summary"]["queries"] = {"total": 1, "passed": 1, "failed": 0}
    src.write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 1
    assert "Submission validation failed" in result.output
    assert "canonical queries" in result.output
    assert not out_dir.exists()


def test_submit_dry_run_validator_is_packaged_library() -> None:
    import inspect
    import re

    assert not hasattr(sub, "_load_submission_validator_module")
    source = inspect.getsource(sub._validate_submission_bundle)
    forbidden_patterns = [
        r"importlib",
        r"\bPath\.cwd\b",
        r"\bos\.getcwd\b",
        r"""os\.environ\[\s*['"]PWD['"]\s*\]""",
        r"""os\.getenv\(\s*['"]PWD['"]""",
        r"""Path\(\s*['"]['"]\s*\)""",
        r"validate_submission\.py",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, source), f"validator loader regressed — matched /{pattern}/"


def test_submit_manifest_contains_bundle_hash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)

    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    manifest = json.loads((out_dir / "tpch_duckdb.manifest.json").read_text(encoding="utf-8"))
    assert "bundle_hash" in manifest
    assert len(manifest["bundle_hash"]) == 64
    assert "submitted_by" in manifest


def test_submit_manifest_provenance_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    CliRunner().invoke(
        sub.submit,
        [str(src), "--output", str(out_dir), "--funding", "free-trial", "--notes", "ran on a laptop"],
    )

    manifest = json.loads((out_dir / "tpch_duckdb.manifest.json").read_text(encoding="utf-8"))
    assert manifest["result_source"] == "community"
    assert manifest["funding"] == "free-trial"
    assert manifest["submission_notes"] == "ran on a laptop"


def test_submit_manifest_funding_defaults_to_unspecified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    manifest = json.loads((out_dir / "tpch_duckdb.manifest.json").read_text(encoding="utf-8"))
    assert manifest["funding"] == "unspecified"
    assert "submission_notes" not in manifest


def test_submit_load_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "bad.json"
    src.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        sub,
        "load_result_file",
        lambda *_a, **_k: (_ for _ in ()).throw(sub.ResultLoadError("bad data")),
    )

    result = CliRunner().invoke(sub.submit, [str(src)])
    assert result.exit_code == 1
    assert "Error loading result file" in result.output


def test_submit_copies_companion_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)

    plans = tmp_path / "tpch_duckdb.plans.json"
    plans.write_text('{"plans": []}', encoding="utf-8")

    tuning = tmp_path / "tpch_duckdb.tuning.json"
    tuning.write_text('{"tuning": {}}', encoding="utf-8")

    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(
        sub.submit,
        [str(src), "--output", str(out_dir)],
    )

    assert result.exit_code == 0
    assert (out_dir / "bundle" / "tpch_duckdb.plans.json").exists()
    assert (out_dir / "bundle" / "tpch_duckdb.tuning.json").exists()


def test_submit_canonical_manifest_hashes_primary_and_companions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    source_raw_hash = _sha256(src.read_bytes())

    plans = tmp_path / "tpch_duckdb.plans.json"
    plans.write_text('{"z":1,"alpha":{"b":2}}', encoding="utf-8")
    tuning = tmp_path / "tpch_duckdb.tuning.json"
    tuning.write_text('{"z":2,"alpha":1}', encoding="utf-8")

    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 0, result.output
    packaged_source = out_dir / "bundle" / src.name
    packaged_plans = out_dir / "bundle" / plans.name
    packaged_tuning = out_dir / "bundle" / tuning.name
    manifest = json.loads((out_dir / "tpch_duckdb.manifest.json").read_text(encoding="utf-8"))

    packaged_payload = json.loads(packaged_source.read_text(encoding="utf-8"))
    assert packaged_payload.get("export", {}).get("anonymized") is True
    assert packaged_plans.read_bytes() == _canonical_file_bytes(plans)
    assert packaged_tuning.read_bytes() == _canonical_file_bytes(tuning)
    assert _eof_fixed(packaged_source.read_bytes()) == packaged_source.read_bytes()
    assert _eof_fixed(packaged_plans.read_bytes()) == packaged_plans.read_bytes()
    assert _eof_fixed(packaged_tuning.read_bytes()) == packaged_tuning.read_bytes()
    assert manifest["bundle_hash"] == _sha256(packaged_source.read_bytes())
    assert manifest["bundle_hash"] != source_raw_hash
    assert manifest["companion_hashes"][plans.name] == _sha256(packaged_plans.read_bytes())
    assert manifest["companion_hashes"][tuning.name] == _sha256(packaged_tuning.read_bytes())


def test_submit_last_passes_filters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_find(results_dir, *, benchmark=None, platform=None):
        captured["benchmark"] = benchmark
        captured["platform"] = platform
        return None

    monkeypatch.setattr(sub, "find_latest_result", fake_find)

    CliRunner().invoke(sub.submit, ["--last", "--benchmark", "tpch", "--platform", "duckdb"])

    assert captured["benchmark"] == "tpch"
    assert captured["platform"] == "duckdb"


def test_submit_unsupported_schema_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "old_result.json"
    src.write_text("{}", encoding="utf-8")

    def _raise(*_a, **_k):
        raise sub.UnsupportedSchemaError("Unsupported schema version: 1.0")

    monkeypatch.setattr(sub, "load_result_file", _raise)

    result = CliRunner().invoke(sub.submit, [str(src)])
    assert result.exit_code == 1
    assert "Unsupported schema version" in result.output


def test_submit_file_not_found_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "missing.json"
    src.write_text("{}", encoding="utf-8")

    def _raise(*_a, **_k):
        raise FileNotFoundError("Result file not found: missing.json")

    monkeypatch.setattr(sub, "load_result_file", _raise)

    result = CliRunner().invoke(sub.submit, [str(src)])
    assert result.exit_code == 1
    assert "Result file not found" in result.output


def test_submit_generic_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "weird.json"
    src.write_text("{}", encoding="utf-8")

    def _raise(*_a, **_k):
        raise Exception("something broke")

    monkeypatch.setattr(sub, "load_result_file", _raise)

    result = CliRunner().invoke(sub.submit, [str(src)])
    assert result.exit_code == 1
    assert "Unexpected error" in result.output


def test_submit_service_dry_run_no_creds_required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))
    monkeypatch.setattr(
        sub,
        "resolve_submission_token",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("dry-run must not resolve auth")),
    )

    result = CliRunner().invoke(sub.submit, [str(src), "--service", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Dry-run - would upload" in result.output
    assert "Service URL:" in result.output
    assert "Visibility:" in result.output
    assert not (tmp_path / "submission").exists()


def test_submit_service_dry_run_validation_rejects_bundle_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "tpch_duckdb_invalid_cost.json"
    _write_unavailable_cost_total_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))
    monkeypatch.setattr(
        sub,
        "resolve_submission_token",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("dry-run must not resolve auth")),
    )

    result = CliRunner().invoke(sub.submit, [str(src), "--service", "--dry-run"])

    assert result.exit_code == 1
    assert "Submission validation failed" in result.output
    assert "cannot accompany cost_status 'unavailable'" in result.output
    assert "Dry-run - would upload" not in result.output
    assert not (tmp_path / "submission").exists()


def test_submit_service_default_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    result = CliRunner().invoke(sub.submit, [str(src), "--service", "--dry-run"])
    assert result.exit_code == 0
    assert sub._DEFAULT_SERVICE_URL in result.output


def test_submit_service_custom_url_passed_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    custom = "https://staging.benchbox.dev/v1"
    result = CliRunner().invoke(sub.submit, [str(src), "--service", custom, "--dry-run"])
    assert result.exit_code == 0
    assert custom in result.output


def test_submit_service_visibility_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    result = CliRunner().invoke(sub.submit, [str(src), "--service", "--dry-run", "--visibility", "unlisted"])
    assert result.exit_code == 0
    assert "unlisted" in result.output


def test_submit_service_visibility_rejects_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    src.write_text('{"schema_version": "2.0"}', encoding="utf-8")
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    result = CliRunner().invoke(sub.submit, [str(src), "--service", "--dry-run", "--visibility", "secret"])
    assert result.exit_code == 2
    assert "Invalid value" in result.output or "secret" in result.output


def test_submit_service_idempotency_key_passthrough(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    key = "11111111-2222-3333-4444-555555555555"
    result = CliRunner().invoke(sub.submit, [str(src), "--service", "--dry-run", "--idempotency-key", key])
    assert result.exit_code == 0
    assert key in result.output


def test_submit_service_real_upload_calls_transport(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))
    auth_calls: list[str] = []
    upload_calls: list[dict] = []

    def fake_resolve(service_url: str):
        auth_calls.append(service_url)
        return SimpleNamespace(token="secret-token", source="keyring")

    def fake_upload(**kwargs):
        upload_calls.append(kwargs)
        return sub.HostedSubmitResult(
            status="published",
            submission_id="sub1",
            public_result_id="r1",
            public_url="https://benchbox.dev/results/r/r1",
            idempotency_key=kwargs["idempotency_key"] or "generated-key",
            status_history=("pending", "published"),
        )

    monkeypatch.setattr(sub, "resolve_submission_token", fake_resolve)
    monkeypatch.setattr(sub, "submit_hosted_bundle", fake_upload)

    result = CliRunner().invoke(sub.submit, [str(src), "--service"])
    assert result.exit_code == 0
    assert "Hosted submission complete" in result.output
    assert "https://benchbox.dev/results/r/r1" in result.output
    assert "secret-token" not in result.output
    assert auth_calls == [sub._DEFAULT_SERVICE_URL]
    assert upload_calls[0]["token"] == "secret-token"
    assert upload_calls[0]["manifest"]["submission_path"] == "hosted-service"
    history = json.loads((tmp_path / "tpch_duckdb.generated-key.submission.json").read_text(encoding="utf-8"))
    assert history["public_url"] == "https://benchbox.dev/results/r/r1"
    assert history["status"] == "published"
    assert "secret-token" not in json.dumps(history)


def test_submit_service_upload_uses_canonical_manifest_hashes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    plans = tmp_path / "tpch_duckdb.plans.json"
    plans.write_text('{"z":1,"alpha":{"b":2}}', encoding="utf-8")
    tuning = tmp_path / "tpch_duckdb.tuning.json"
    tuning.write_text('{"z":2,"alpha":1}', encoding="utf-8")

    captured: dict = {}

    def fake_upload(**kwargs):
        captured["source_bytes"] = kwargs["source_path"].read_bytes()
        captured["companion_bytes"] = {comp.name: comp.read_bytes() for comp in kwargs["companions"]}
        captured["manifest"] = dict(kwargs["manifest"])
        captured["bundle_hash"] = kwargs["bundle_hash"]
        return sub.HostedSubmitResult(status="published", idempotency_key="generated-key")

    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))
    monkeypatch.setattr(sub, "resolve_submission_token", lambda _service_url: SimpleNamespace(token="secret-token"))
    monkeypatch.setattr(sub, "submit_hosted_bundle", fake_upload)
    monkeypatch.setattr(sub, "record_hosted_submission", lambda **_kwargs: tmp_path / "history.json")

    result = CliRunner().invoke(sub.submit, [str(src), "--service", "--no-wait"])

    assert result.exit_code == 0, result.output
    uploaded_payload = json.loads(captured["source_bytes"].decode("utf-8"))
    assert uploaded_payload.get("export", {}).get("anonymized") is True
    assert captured["companion_bytes"][plans.name] == _canonical_file_bytes(plans)
    assert captured["companion_bytes"][tuning.name] == _canonical_file_bytes(tuning)
    assert _eof_fixed(captured["source_bytes"]) == captured["source_bytes"]
    assert _eof_fixed(captured["companion_bytes"][plans.name]) == captured["companion_bytes"][plans.name]
    assert _eof_fixed(captured["companion_bytes"][tuning.name]) == captured["companion_bytes"][tuning.name]
    assert captured["manifest"]["bundle_hash"] == captured["bundle_hash"] == _sha256(captured["source_bytes"])
    assert captured["manifest"]["companion_hashes"][plans.name] == _sha256(captured["companion_bytes"][plans.name])
    assert captured["manifest"]["companion_hashes"][tuning.name] == _sha256(captured["companion_bytes"][tuning.name])


def test_submit_service_real_upload_requires_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    def fake_resolve(_service_url: str):
        raise sub.SubmissionAuthError("No hosted-submit token found.")

    monkeypatch.setattr(sub, "resolve_submission_token", fake_resolve)

    result = CliRunner().invoke(sub.submit, [str(src), "--service"])
    assert result.exit_code == 1
    assert "Authentication required" in result.output
    assert "Uploading submission bundle" not in result.output


def test_submit_service_reauthenticates_once_on_401(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))
    tokens: list[str] = []

    monkeypatch.setattr(sub, "resolve_submission_token", lambda _service_url: SimpleNamespace(token="stale-token"))
    monkeypatch.setattr(sub, "refresh_submission_token", lambda _service_url: SimpleNamespace(token="fresh-token"))

    def fake_upload(**kwargs):
        tokens.append(kwargs["token"])
        if kwargs["token"] == "stale-token":
            raise sub.HostedSubmitUnauthorized("bad token")
        return sub.HostedSubmitResult(
            status="published",
            submission_id="sub1",
            public_result_id="r1",
            public_url="https://benchbox.dev/results/r/r1",
            idempotency_key="generated-key",
            status_history=("pending", "published"),
        )

    monkeypatch.setattr(sub, "submit_hosted_bundle", fake_upload)

    result = CliRunner().invoke(sub.submit, [str(src), "--service"])

    assert result.exit_code == 0
    assert tokens == ["stale-token", "fresh-token"]
    assert "fresh-token" not in result.output


def test_submit_service_no_wait_reports_accepted_not_complete(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))
    monkeypatch.setattr(sub, "resolve_submission_token", lambda _service_url: SimpleNamespace(token="secret-token"))
    monkeypatch.setattr(
        sub,
        "submit_hosted_bundle",
        lambda **_kwargs: sub.HostedSubmitResult(
            status="pending",
            submission_id="sub1",
            idempotency_key="generated-key",
            status_history=("pending",),
        ),
    )

    result = CliRunner().invoke(sub.submit, [str(src), "--service", "--no-wait"])

    assert result.exit_code == 0
    assert "Hosted submission accepted" in result.output
    assert "Hosted submission complete" not in result.output


def test_submit_default_pr_path_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])
    assert result.exit_code == 0
    assert out_dir.is_dir()
    assert (out_dir / "tpch_duckdb.manifest.json").is_file()


def test_submit_manifest_filename_inherits_bundle_stem(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_sf001_duckdb_20260403_093653_9c0925d1.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 0
    assert (out_dir / "tpch_sf001_duckdb_20260403_093653_9c0925d1.manifest.json").is_file()
    assert not (out_dir / "submission-manifest.json").exists()


def test_submit_two_consecutive_submits_do_not_collide(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src_a = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src_a)
    src_b = tmp_path / "tpch_clickhouse.json"
    _write_valid_submission_bundle(src_b)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    runner = CliRunner()
    result_a = runner.invoke(sub.submit, [str(src_a), "--output", str(out_dir)])
    result_b = runner.invoke(sub.submit, [str(src_b), "--output", str(out_dir)])

    assert result_a.exit_code == 0
    assert result_b.exit_code == 0
    assert (out_dir / "tpch_duckdb.manifest.json").is_file()
    assert (out_dir / "tpch_clickhouse.manifest.json").is_file()


def test_submit_submitted_by_flag_overrides_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))
    monkeypatch.setattr(sub, "_get_git_username", lambda: "git-config-name")

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(
        sub.submit,
        [str(src), "--output", str(out_dir), "--submitted-by", "Explicit Name"],
    )

    assert result.exit_code == 0
    manifest = json.loads((out_dir / "tpch_duckdb.manifest.json").read_text(encoding="utf-8"))
    assert manifest["submitted_by"] == "Explicit Name"


def test_submit_submitted_by_falls_back_to_git_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))
    monkeypatch.setattr(sub, "_get_git_username", lambda: "git-config-name")

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 0
    manifest = json.loads((out_dir / "tpch_duckdb.manifest.json").read_text(encoding="utf-8"))
    assert manifest["submitted_by"] == "git-config-name"


def test_submit_submitted_by_warns_when_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))
    monkeypatch.setattr(sub, "_get_git_username", lambda: "")

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 0
    assert "submitted_by is empty" in result.output
    assert "--submitted-by" in result.output
    manifest = json.loads((out_dir / "tpch_duckdb.manifest.json").read_text(encoding="utf-8"))
    assert manifest["submitted_by"] == ""


def test_submit_submitted_by_in_help_output() -> None:
    result = CliRunner().invoke(sub.submit, ["--help"])
    assert result.exit_code == 0
    assert "--submitted-by" in result.output


def test_submit_help_mentions_results_paths_affordance() -> None:
    result = CliRunner().invoke(sub.submit, ["--help"])
    assert result.exit_code == 0
    assert "benchbox results --paths" in result.output


def test_submit_pseudonymizes_retained_identifiers_and_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb.json"
    raw_bundle = _valid_submission_bundle()
    raw_bundle["environment"] = {
        "cwd": "/Users/developer/secret-project/BenchBox",
        "hostname": "developer-macbook-pro.local",
        "user": "developer",
    }
    src.write_text(json.dumps(raw_bundle), encoding="utf-8")

    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 0, result.output
    packaged_source = out_dir / "bundle" / src.name
    packaged_text = packaged_source.read_text(encoding="utf-8")
    packaged_data = json.loads(packaged_text)

    assert "/Users/developer" not in packaged_text
    assert "developer-macbook-pro.local" not in packaged_text
    assert packaged_data.get("export", {}).get("anonymized") is True
    assert packaged_data["environment"]["cwd"].startswith("path_")
    assert packaged_data["environment"]["hostname"].startswith("host_")

    validation_results = sub.validate_bundles([packaged_source])
    assert validation_results and all(v.ok for v in validation_results)


def test_submit_redacts_raw_plans_and_applied_ledger_companions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "tpch_duckdb.json"
    _write_valid_submission_bundle(src)
    plans = {
        "queries": {
            "q1": {
                "plan": {
                    "raw_explain_output": "Seq Scan on /Users/developer/data/lineitem.parquet",
                    "logical_root": {
                        "physical_operator": {"platform_metadata": {"path": "/Users/developer/data"}},
                        "children": [{"physical_operator": {"platform_metadata": {"host": "developer-macbook-pro"}}}],
                    },
                }
            }
        }
    }
    applied = {
        "status": "applied_verified",
        "statements": [{"statement": "CREATE INDEX i ON /Users/developer/db.t(c)", "phase": "ddl"}],
    }
    (tmp_path / "tpch_duckdb.plans.json").write_text(json.dumps(plans), encoding="utf-8")
    (tmp_path / "tpch_duckdb.applied.json").write_text(json.dumps(applied), encoding="utf-8")
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 0, result.output
    packaged_plans = (out_dir / "bundle" / "tpch_duckdb.plans.json").read_text(encoding="utf-8")
    packaged_applied = (out_dir / "bundle" / "tpch_duckdb.applied.json").read_text(encoding="utf-8")
    assert "/Users/developer" not in packaged_plans + packaged_applied
    assert "developer-macbook-pro" not in packaged_plans
    assert json.loads(packaged_plans)["queries"]["q1"]["plan"]["raw_explain_output"] is None
    assert json.loads(packaged_applied)["statements"][0]["statement_redacted"] is True


def test_submit_pseudonymizes_inline_requested_tuning_identifiers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "tpch_duckdb.json"
    raw_bundle = _valid_submission_bundle()
    raw_bundle["platform"]["tuning"] = {
        "requested": {
            "table_tunings": {"secret_orders_tbl": {"clustering": ["secret_customer_col"]}},
            "constraints": {"primary_keys": [{"table": "secret_orders_tbl", "columns": ["secret_customer_col"]}]},
        }
    }
    src.write_text(json.dumps(raw_bundle), encoding="utf-8")
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 0, result.output
    packaged_text = (out_dir / "bundle" / src.name).read_text(encoding="utf-8")
    assert "secret_orders_tbl" not in packaged_text
    assert "secret_customer_col" not in packaged_text


def test_submit_real_run_packages_override_companion_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_duckdb_plateau.json"
    _write_flat_plateau_bundle(src)
    _write_sibling_override_artifact(src)
    monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))

    out_dir = tmp_path / "submission"
    result = CliRunner().invoke(sub.submit, [str(src), "--output", str(out_dir)])

    assert result.exit_code == 0, result.output
    original = json.loads(src.with_name(f"{src.stem}.override.json").read_text(encoding="utf-8"))
    packaged = json.loads((out_dir / "bundle" / f"{src.stem}.override.json").read_text(encoding="utf-8"))
    assert packaged == original
