from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.uat.config import validate_config
from tests.uat.phases import package

pytestmark = pytest.mark.medium


def _fake_runner_factory(returncodes: list[int]):
    iterator = iter(returncodes)

    def runner(argv, check=False):
        rc = next(iterator)
        return Mock(returncode=rc, args=argv)

    return runner


def test_package_local_stage_invokes_output_mode(tmp_path: Path):
    cfg = validate_config({"name": "x", "package": {"submit_terminal_state": "local-stage"}})
    runner = _fake_runner_factory([0])
    result = package.run_package(
        cfg,
        result_paths=[tmp_path / "r.json"],
        submissions_dir=tmp_path / "subs",
        runner=runner,
    )
    assert result.terminal_state == "local-stage"
    assert result.success_count == 1
    assert result.invocations[0][:3] == ("benchbox", "publish", "run")
    assert "--target" in result.invocations[0]
    assert str(tmp_path / "subs") in result.invocations[0]
    assert "submit" not in result.invocations[0]


def test_package_cloud_uploaded_requires_service(tmp_path: Path):
    cfg = validate_config({"name": "x", "package": {"submit_terminal_state": "cloud-uploaded"}})
    result = package.run_package(
        cfg,
        result_paths=[tmp_path / "r.json"],
        submissions_dir=tmp_path / "subs",
        runner=_fake_runner_factory([]),
    )
    assert result.aborted is True
    assert result.exit_code() == 2
    assert "package.service" in (result.abort_reason or "")


def test_package_cloud_uploaded_invokes_service(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "x",
            "package": {
                "submit_terminal_state": "cloud-uploaded",
                "service": "https://results.example.com",
            },
        }
    )
    runner = _fake_runner_factory([0])
    result = package.run_package(
        cfg,
        result_paths=[tmp_path / "r.json"],
        submissions_dir=tmp_path / "subs",
        runner=runner,
    )
    argv = result.invocations[0]
    assert argv[:2] == ("benchbox", "submit")
    assert "--service" in argv
    assert "https://results.example.com" in argv


def test_package_missing_terminal_state_raises(tmp_path: Path):
    cfg = validate_config({"name": "x"})
    result = package.run_package(
        cfg,
        result_paths=[],
        submissions_dir=tmp_path,
        runner=_fake_runner_factory([]),
    )
    assert result.aborted is True
    assert result.exit_code() == 2
    assert "submit_terminal_state" in (result.abort_reason or "")


def test_package_invalid_terminal_state_raises(tmp_path: Path):
    cfg = validate_config({"name": "x", "package": {"submit_terminal_state": "merged-mainline"}})
    result = package.run_package(
        cfg,
        result_paths=[],
        submissions_dir=tmp_path,
        runner=_fake_runner_factory([]),
    )
    assert result.aborted is True
    assert result.exit_code() == 2
    assert "not in" in (result.abort_reason or "")


def test_package_counts_failures(tmp_path: Path):
    cfg = validate_config({"name": "x", "package": {"submit_terminal_state": "local-stage"}})
    runner = _fake_runner_factory([0, 1, 0])
    result = package.run_package(
        cfg,
        result_paths=[tmp_path / f"r{i}.json" for i in range(3)],
        submissions_dir=tmp_path / "subs",
        runner=runner,
        classify_results=False,
    )
    assert result.success_count == 2
    assert result.failure_count == 1
    assert result.exit_code() == 1


def _write_minimal_result(path: Path, *, validation: str = "passed", compliance_class: str | None = None) -> None:
    import json

    payload = {
        "version": "2.1",
        "run": {"id": "pkg", "timestamp": "2026-01-01T00:00:00", "total_duration_ms": 1},
        "benchmark": {"id": "tpch", "name": "TPC-H", "scale_factor": 0.01, "test_type": "power"},
        "platform": {"name": "DuckDB"},
        "summary": {
            "queries": {"total": 1, "passed": 1, "failed": 0},
            "validation": {"status": validation},
        },
        "queries": [{"id": "Q1", "status": "SUCCESS", "ms": 1}],
        "phases": {},
    }
    if compliance_class is not None:
        payload["benchmark"]["compliance_class"] = compliance_class
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_package_skips_unvalidated_without_counting_failure(tmp_path: Path):
    cfg = validate_config({"name": "x", "package": {"submit_terminal_state": "local-stage"}})
    clean = tmp_path / "clean.json"
    unvalidated = tmp_path / "unvalidated.json"
    _write_minimal_result(clean, validation="passed")
    _write_minimal_result(unvalidated, validation="not_validated")
    runner = _fake_runner_factory([0])
    warnings: list[str] = []
    result = package.run_package(
        cfg,
        result_paths=[clean, unvalidated],
        submissions_dir=tmp_path / "subs",
        runner=runner,
        warn=warnings.append,
        classify_results=True,
    )
    assert result.success_count == 1
    assert result.failure_count == 0
    assert result.exit_code() == 0
    assert len(result.invocations) == 1
    assert any("unvalidated" in w for w in warnings)


def test_package_skips_unofficial_without_counting_failure(tmp_path: Path):
    cfg = validate_config({"name": "x", "package": {"submit_terminal_state": "local-stage"}})
    unofficial = tmp_path / "unofficial.json"
    _write_minimal_result(unofficial, compliance_class="unofficial_subscale")
    runner = _fake_runner_factory([])
    warnings: list[str] = []
    result = package.run_package(
        cfg,
        result_paths=[unofficial],
        submissions_dir=tmp_path / "subs",
        runner=runner,
        warn=warnings.append,
        classify_results=True,
    )
    assert result.success_count == 0
    assert result.failure_count == 0
    assert result.invocations == ()
    assert any("unofficial" in w for w in warnings)


def test_package_counts_schema_violation_as_failure(tmp_path: Path):
    cfg = validate_config({"name": "x", "package": {"submit_terminal_state": "local-stage"}})
    bad = tmp_path / "schema.json"
    _write_minimal_result(bad, validation="failed")
    runner = _fake_runner_factory([])
    warnings: list[str] = []
    result = package.run_package(
        cfg,
        result_paths=[bad],
        submissions_dir=tmp_path / "subs",
        runner=runner,
        warn=warnings.append,
        classify_results=True,
    )
    assert result.failure_count == 1
    assert result.invocations == ()
    assert any("schema_violation" in w for w in warnings)


def test_package_counts_bundle_load_error_as_failure(tmp_path: Path):
    cfg = validate_config({"name": "x", "package": {"submit_terminal_state": "local-stage"}})
    bad = tmp_path / "malformed.json"
    bad.write_text("{not valid json", encoding="utf-8")
    runner = _fake_runner_factory([])
    warnings: list[str] = []
    result = package.run_package(
        cfg,
        result_paths=[bad],
        submissions_dir=tmp_path / "subs",
        runner=runner,
        warn=warnings.append,
        classify_results=True,
    )
    assert result.failure_count == 1
    assert result.invocations == ()
    assert any("bundle_load_error" in w for w in warnings)


@pytest.mark.parametrize("state", sorted(package.PR_STUB_TERMINAL_STATES))
def test_package_warns_for_pr_stub_states(tmp_path: Path, state: str):
    cfg = validate_config({"name": "x", "package": {"submit_terminal_state": state}})
    runner = _fake_runner_factory([0])
    warnings: list[str] = []
    package.run_package(
        cfg,
        result_paths=[tmp_path / "r.json"],
        submissions_dir=tmp_path / "subs",
        runner=runner,
        warn=warnings.append,
    )
    assert warnings, f"no warning emitted for stub state {state!r}"
    assert state in warnings[0]
    assert "stub" in warnings[0].lower()


def test_package_local_stage_does_not_warn(tmp_path: Path):
    cfg = validate_config({"name": "x", "package": {"submit_terminal_state": "local-stage"}})
    runner = _fake_runner_factory([0])
    warnings: list[str] = []
    package.run_package(
        cfg,
        result_paths=[tmp_path / "r.json"],
        submissions_dir=tmp_path / "subs",
        runner=runner,
        warn=warnings.append,
    )
    assert warnings == []


def test_package_cloud_uploaded_does_not_warn(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "x",
            "package": {
                "submit_terminal_state": "cloud-uploaded",
                "service": "https://results.example.com",
            },
        }
    )
    runner = _fake_runner_factory([0])
    warnings: list[str] = []
    package.run_package(
        cfg,
        result_paths=[tmp_path / "r.json"],
        submissions_dir=tmp_path / "subs",
        runner=runner,
        warn=warnings.append,
    )
    assert warnings == []


@pytest.mark.parametrize("state", ["local-stage", "draft-pr", "merged-to-published-results"])
def test_package_local_states_stage_through_the_maintainer_publish_command(tmp_path: Path, state: str):
    cfg = validate_config({"name": "x", "package": {"submit_terminal_state": state}})
    result = package.run_package(
        cfg,
        result_paths=[tmp_path / "r.json"],
        submissions_dir=tmp_path / "subs",
        runner=_fake_runner_factory([0]),
        warn=lambda _msg: None,
    )
    assert result.invocations[0][:3] == ("benchbox", "publish", "run")
