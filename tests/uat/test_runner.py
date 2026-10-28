from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.uat import runner
from tests.uat.artifact_hygiene import LocalArtifactGrowthError
from tests.uat.timeouts import TimeoutResult

pytestmark = pytest.mark.medium


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _write_result_json(
    path: Path,
    *,
    failed: int = 0,
    compliance_class: str | None = None,
    validation: str | None = None,
    translation_status: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    passed = 1 if failed == 0 else 0
    if validation is None:
        validation = "passed" if failed == 0 else "failed"
    payload = {
        "version": "2.1",
        "run": {
            "id": "uat-test",
            "timestamp": "2026-01-01T00:00:00",
            "total_duration_ms": 1,
            "query_time_ms": 1,
            "iterations": 1,
            "streams": 1,
        },
        "benchmark": {
            "id": "tpch",
            "name": "TPC-H",
            "scale_factor": 0.01,
            "test_type": "power",
        },
        "platform": {"name": "DuckDB"},
        "summary": {
            "queries": {"total": 1, "passed": passed, "failed": failed},
            "validation": {"status": validation},
        },
        "queries": [{"id": "Q1", "status": "SUCCESS" if failed == 0 else "ERROR", "ms": 1}],
        "phases": {},
    }
    if compliance_class is not None:
        payload["benchmark"]["compliance_class"] = compliance_class
    if translation_status is not None:
        payload["execution"] = {"translation": {"status": translation_status}}
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_last_nonempty_output_line_picks_last_subprocess_line():
    log = """\
# uv run -- benchbox run --quiet
# BENCHBOX_OUTPUT_DIR=/tmp/shared-runs
[info] running...
[trace] retry...
/Users/joe/Developer/benchmark_runs/results/duckdb_tpch_20260502_FINAL.json
"""
    out = runner.last_nonempty_output_line(log)
    assert out == "/Users/joe/Developer/benchmark_runs/results/duckdb_tpch_20260502_FINAL.json"


def test_last_nonempty_output_line_accepts_custom_runs_root():
    log = "/tmp/shared-runs/results/duckdb_tpch_custom_root.json\n"
    assert runner.last_nonempty_output_line(log) == "/tmp/shared-runs/results/duckdb_tpch_custom_root.json"


def test_last_nonempty_output_line_returns_none_when_only_uat_comments():
    log = """\
# uv run -- benchbox run --quiet
# BENCHBOX_OUTPUT_DIR=/tmp/shared-runs
"""
    assert runner.last_nonempty_output_line(log) is None


def test_last_nonempty_output_line_returns_none_when_missing():
    assert runner.last_nonempty_output_line("") is None


def test_run_cell_writes_log_and_returns_result(tmp_path: Path):
    result_path = tmp_path / "benchmark_runs" / "results" / "duckdb_tpch_smoke.json"
    _write_result_json(result_path)
    fake_argv = [
        sys.executable,
        "-c",
        f"print({str(result_path)!r})",
    ]
    with patch.object(runner, "benchbox_run_argv", return_value=fake_argv):
        result = runner.run_cell(
            "duckdb",
            "tpch",
            0.01,
            timeout_s=10,
            log_dir=tmp_path,
            now=_dt.datetime(2026, 5, 5, 12, 0, 0),
        )

    assert result.status == "passed"
    assert result.exit_code == 0
    assert result.result_path is not None
    assert str(result.result_path).endswith("duckdb_tpch_smoke.json")
    assert result.log_path.exists()
    log_text = result.log_path.read_text(encoding="utf-8")
    assert str(result_path.parent) in log_text


def test_run_cell_sets_benchbox_output_dir_for_subprocess(tmp_path: Path):
    result_path = tmp_path / "shared-runs" / "results" / "duckdb_tpch_env.json"
    _write_result_json(result_path)
    fake_argv = [sys.executable, "-c", "print('unused')"]
    captured = {}

    def fake_run_with_timeout(argv, timeout_s, *, stdout=None, stderr=None, env=None, cwd=None):
        captured["env"] = env
        captured["argv"] = argv
        return TimeoutResult(
            exit_code=0,
            timed_out=False,
            elapsed_s=0.1,
            stdout=f"{result_path}\n".encode(),
            stderr=b"[warning] ignored stderr after quiet stdout\n",
        )

    def fake_benchbox_run_argv(*args, extra_args=(), **kwargs):
        return [*fake_argv, *extra_args]

    with (
        patch.object(runner, "benchbox_run_argv", side_effect=fake_benchbox_run_argv),
        patch.object(runner, "run_with_timeout", side_effect=fake_run_with_timeout),
    ):
        result = runner.run_cell(
            "duckdb",
            "tpch",
            0.01,
            timeout_s=10,
            log_dir=tmp_path,
            benchmark_runs_dir=tmp_path / "shared-runs",
            now=_dt.datetime(2026, 5, 5, 12, 0, 0),
        )

    assert result.status == "passed"
    assert result.result_path == result_path
    assert captured["env"]["BENCHBOX_OUTPUT_DIR"] == str(tmp_path / "shared-runs")
    assert captured["argv"][-2:] == ["--output", str(tmp_path / "shared-runs" / "datagen")]
    log_text = result.log_path.read_text()
    assert "BENCHBOX_OUTPUT_DIR=" in log_text
    assert "ignored stderr" in log_text


@pytest.mark.parametrize("local_managed_platform", [False, True])
def test_run_cell_forwards_local_managed_platform_scope(local_managed_platform: bool, tmp_path: Path):
    captured = {}

    def fake_benchbox_run_argv(*args, **kwargs):
        captured["local_managed_platform"] = kwargs["local_managed_platform"]
        return [sys.executable, "-c", "print('unused')"]

    with patch.object(runner, "benchbox_run_argv", side_effect=fake_benchbox_run_argv):
        runner.run_cell(
            "postgresql",
            "tpch",
            0.01,
            timeout_s=10,
            log_dir=tmp_path,
            local_managed_platform=local_managed_platform,
        )

    assert captured["local_managed_platform"] is local_managed_platform


def test_run_cell_defaults_benchbox_output_dir_to_shared(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)
    fake_argv = [sys.executable, "-c", "print('unused')"]
    captured = {}

    def fake_run_with_timeout(argv, timeout_s, *, stdout=None, stderr=None, env=None, cwd=None):
        captured["env"] = env
        return TimeoutResult(exit_code=0, timed_out=False, elapsed_s=0.1, stdout=b"")

    with (
        patch.object(runner, "benchbox_run_argv", return_value=fake_argv),
        patch.object(runner, "run_with_timeout", side_effect=fake_run_with_timeout),
    ):
        runner.run_cell(
            "duckdb",
            "tpch",
            0.01,
            timeout_s=10,
            log_dir=tmp_path,
            now=_dt.datetime(2026, 5, 5, 12, 0, 0),
        )

    assert captured["env"]["BENCHBOX_OUTPUT_DIR"] == str(Path.home() / "Developer" / "benchmark_runs")


def test_run_cell_marks_failure(tmp_path: Path):
    fake_argv = [sys.executable, "-c", "import sys; sys.exit(2)"]
    with patch.object(runner, "benchbox_run_argv", return_value=fake_argv):
        result = runner.run_cell("duckdb", "tpch", 0.01, timeout_s=10, log_dir=tmp_path)
    assert result.status == "failed"
    assert result.exit_code == 2
    assert result.result_path is None


def test_run_cell_preserves_result_path_when_failed_child_exports_json(tmp_path: Path):
    result_path = tmp_path / "benchmark_runs" / "results" / "failed-query.json"
    _write_result_json(result_path, failed=1)
    fake_argv = [sys.executable, "-c", f"print({str(result_path)!r}); raise SystemExit(2)"]

    with patch.object(runner, "benchbox_run_argv", return_value=fake_argv):
        result = runner.run_cell("clickhouse-server", "read_primitives", 0.01, timeout_s=10, log_dir=tmp_path)

    assert result.status == "failed"
    assert result.exit_code == 2
    assert result.result_path == result_path
    assert result.submit_terminal_state == "query_failure"


def test_run_cell_writes_clickhouse_load_failure_sidecar(tmp_path: Path):
    payload = {
        "table": "lineitem",
        "source_files": ["/tmp/lineitem.tbl"],
        "rows_attempted": 65536,
        "memory_settings": {"max_memory_usage": "8GB", "insert_block_size": 65536},
        "driver_timeout_s": 300,
        "exception": {"type": "RuntimeError", "message": "memory limit exceeded"},
        "result_json": None,
    }
    fake_argv = [
        sys.executable,
        "-c",
        f"print({(runner.LOAD_FAILURE_MARKER + json.dumps(payload, separators=(',', ':')))!r}); raise SystemExit(1)",
    ]

    with patch.object(runner, "benchbox_run_argv", return_value=fake_argv):
        result = runner.run_cell(
            "clickhouse-server",
            "tpch",
            1.0,
            timeout_s=10,
            log_dir=tmp_path,
            now=_dt.datetime(2026, 8, 12, 12, 0, 0),
        )

    assert result.status == "failed"
    assert result.result_path is None
    assert result.load_failure_path is not None
    assert result.load_failure_path == result.log_path.with_suffix(".load_failure.json")
    artifact = json.loads(result.load_failure_path.read_text(encoding="utf-8"))
    assert artifact["table"] == "lineitem"
    assert artifact["rows_attempted"] == 65536
    assert artifact["result_json"] is None
    assert artifact["log_path"] == str(result.log_path)


def test_load_failure_sidecar_scans_large_log_incrementally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    log_path = tmp_path / "cell.log"
    payload = {"table": "lineitem", "rows_attempted": 1, "result_json": None}
    log_path.write_text("noise\n" * 10_000 + runner.LOAD_FAILURE_MARKER + json.dumps(payload) + "\n", encoding="utf-8")

    def fail_full_read(*_args, **_kwargs):
        raise AssertionError("load-failure extraction must not read the complete log")

    monkeypatch.setattr(Path, "read_text", fail_full_read)
    _result_path, sidecar = runner._materialize_load_failure_sidecar(
        log_path=log_path,
        platform="clickhouse-server",
        benchmark="tpch",
        scale=1.0,
        runs_dir=tmp_path,
        result_path=None,
    )

    assert sidecar is not None
    monkeypatch.undo()
    assert json.loads(sidecar.read_text(encoding="utf-8"))["table"] == "lineitem"


def test_run_cell_diagnostic_rerun_fires_for_empty_stdout_failure(tmp_path: Path):
    captured_quiet = []

    def fake_benchbox_run_argv(*args, quiet=True, **kwargs):
        captured_quiet.append(quiet)
        return [sys.executable, "-c", "unused"]

    outcomes = [
        TimeoutResult(exit_code=2, timed_out=False, elapsed_s=0.1, stdout=b"", stderr=b""),
        TimeoutResult(exit_code=2, timed_out=False, elapsed_s=0.1, stdout=b"diagnostic detail\n", stderr=None),
    ]

    def fake_run_with_timeout(argv, timeout_s, *, stdout=None, stderr=None, env=None, cwd=None):
        return outcomes.pop(0)

    with (
        patch.object(runner, "benchbox_run_argv", side_effect=fake_benchbox_run_argv),
        patch.object(runner, "run_with_timeout", side_effect=fake_run_with_timeout),
    ):
        result = runner.run_cell("duckdb", "tpch", 0.01, timeout_s=10, log_dir=tmp_path)

    assert result.status == "failed"
    assert result.exit_code == 2
    assert captured_quiet == [True, False]
    log_text = result.log_path.read_text()
    assert "verbose diagnostic re-run" in log_text
    assert "diagnostic detail" in log_text


def test_run_cell_skips_diagnostic_rerun_when_failure_has_stderr(tmp_path: Path):
    captured_quiet = []

    def fake_benchbox_run_argv(*args, quiet=True, **kwargs):
        captured_quiet.append(quiet)
        return [sys.executable, "-c", "unused"]

    def fake_run_with_timeout(argv, timeout_s, *, stdout=None, stderr=None, env=None, cwd=None):
        if stderr is not None:
            stderr.write("uv run warning on stderr\n")
        return TimeoutResult(
            exit_code=2,
            timed_out=False,
            elapsed_s=0.1,
            stdout=b"",
            stderr=None,
        )

    with (
        patch.object(runner, "benchbox_run_argv", side_effect=fake_benchbox_run_argv),
        patch.object(runner, "run_with_timeout", side_effect=fake_run_with_timeout),
    ):
        result = runner.run_cell("duckdb", "tpch", 0.01, timeout_s=10, log_dir=tmp_path)

    assert result.status == "failed"
    assert captured_quiet == [True]
    log_text = result.log_path.read_text()
    assert "uv run warning on stderr" in log_text
    assert "verbose diagnostic re-run" not in log_text


def test_run_cell_skips_diagnostic_rerun_for_real_stderr_only_failure(tmp_path: Path):
    captured_quiet = []

    def fake_benchbox_run_argv(*args, quiet=True, **kwargs):
        captured_quiet.append(quiet)
        return [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('stderr diagnostic\\n'); sys.exit(2)",
        ]

    with patch.object(runner, "benchbox_run_argv", side_effect=fake_benchbox_run_argv):
        result = runner.run_cell("duckdb", "tpch", 0.01, timeout_s=10, log_dir=tmp_path)

    assert result.status == "failed"
    assert result.exit_code == 2
    assert captured_quiet == [True]
    log_text = result.log_path.read_text()
    assert "stderr diagnostic" in log_text
    assert "verbose diagnostic re-run" not in log_text


def test_run_cell_diagnostic_rerun_fires_when_failure_has_only_whitespace_stderr(tmp_path: Path):
    captured_quiet = []

    def fake_benchbox_run_argv(*args, quiet=True, **kwargs):
        captured_quiet.append(quiet)
        return [sys.executable, "-c", "unused"]

    outcomes = [
        TimeoutResult(exit_code=2, timed_out=False, elapsed_s=0.1, stdout=b"", stderr=b"\n"),
        TimeoutResult(exit_code=2, timed_out=False, elapsed_s=0.1, stdout=b"diagnostic detail\n", stderr=None),
    ]

    def fake_run_with_timeout(argv, timeout_s, *, stdout=None, stderr=None, env=None, cwd=None):
        return outcomes.pop(0)

    with (
        patch.object(runner, "benchbox_run_argv", side_effect=fake_benchbox_run_argv),
        patch.object(runner, "run_with_timeout", side_effect=fake_run_with_timeout),
    ):
        result = runner.run_cell("duckdb", "tpch", 0.01, timeout_s=10, log_dir=tmp_path)

    assert result.status == "failed"
    assert captured_quiet == [True, False]
    assert "diagnostic detail" in result.log_path.read_text()


def test_run_cell_skips_diagnostic_rerun_when_failure_has_stdout(tmp_path: Path):
    captured_quiet = []

    def fake_benchbox_run_argv(*args, quiet=True, **kwargs):
        captured_quiet.append(quiet)
        return [sys.executable, "-c", "unused"]

    def fake_run_with_timeout(argv, timeout_s, *, stdout=None, stderr=None, env=None, cwd=None):
        return TimeoutResult(
            exit_code=2,
            timed_out=False,
            elapsed_s=0.1,
            stdout=b"already diagnostic stdout\n",
            stderr=b"incidental stderr\n",
        )

    with (
        patch.object(runner, "benchbox_run_argv", side_effect=fake_benchbox_run_argv),
        patch.object(runner, "run_with_timeout", side_effect=fake_run_with_timeout),
    ):
        result = runner.run_cell("duckdb", "tpch", 0.01, timeout_s=10, log_dir=tmp_path)

    assert result.status == "failed"
    assert captured_quiet == [True]
    log_text = result.log_path.read_text()
    assert "already diagnostic stdout" in log_text
    assert "incidental stderr" in log_text
    assert "verbose diagnostic re-run" not in log_text


def test_run_cell_diagnostic_rerun_error_does_not_mask_original_failure(tmp_path: Path):
    def fake_benchbox_run_argv(*args, quiet=True, **kwargs):
        return [sys.executable, "-c", "unused"]

    calls = 0

    def fake_run_with_timeout(argv, timeout_s, *, stdout=None, stderr=None, env=None, cwd=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return TimeoutResult(exit_code=2, timed_out=False, elapsed_s=0.1, stdout=b"", stderr=b"")
        raise RuntimeError("diagnostic boom")

    with (
        patch.object(runner, "benchbox_run_argv", side_effect=fake_benchbox_run_argv),
        patch.object(runner, "run_with_timeout", side_effect=fake_run_with_timeout),
    ):
        result = runner.run_cell("duckdb", "tpch", 0.01, timeout_s=10, log_dir=tmp_path)

    assert result.status == "failed"
    assert result.exit_code == 2
    assert calls == 2
    assert "diagnostic re-run error RuntimeError: diagnostic boom" in result.log_path.read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX timeout semantics")
def test_run_cell_marks_timeout(tmp_path: Path):
    fake_argv = [sys.executable, "-c", "import time; time.sleep(3)"]
    with patch.object(runner, "benchbox_run_argv", return_value=fake_argv):
        result = runner.run_cell("duckdb", "tpch", 0.01, timeout_s=1, log_dir=tmp_path)
    assert result.status == "timed-out"
    assert result.exit_code == 124
    assert "UAT_TIMEOUT timeout_s=1 exit_code=124" in result.log_path.read_text()


def test_run_cell_flags_local_datagen_leak_under_external_root(monkeypatch, tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    external = tmp_path / "ext"
    external.mkdir()
    monkeypatch.chdir(worktree)

    leaked_dir = worktree / "benchmark_runs" / "datagen" / "tpch_sf001"

    def fake_run_with_timeout(argv, timeout_s, *, stdout=None, stderr=None, env=None, cwd=None):
        leaked_dir.mkdir(parents=True, exist_ok=True)
        (leaked_dir / "lineitem.tbl").write_bytes(b"x" * 4096)
        return TimeoutResult(exit_code=0, timed_out=False, elapsed_s=0.1)

    fake_argv = [sys.executable, "-c", "print('unused')"]
    with (
        patch.object(runner, "benchbox_run_argv", return_value=fake_argv),
        patch.object(runner, "run_with_timeout", side_effect=fake_run_with_timeout),
        pytest.raises(LocalArtifactGrowthError) as excinfo,
    ):
        runner.run_cell(
            "duckdb",
            "tpch",
            0.01,
            timeout_s=10,
            log_dir=tmp_path / "logs",
            benchmark_runs_dir=external,
            now=_dt.datetime(2026, 6, 1, 12, 0, 0),
        )

    message = str(excinfo.value)
    assert str(external) in message
    assert str(worktree / "benchmark_runs") in message
    assert (leaked_dir / "lineitem.tbl").exists()


def test_run_cell_local_root_run_is_not_guarded(monkeypatch, tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.chdir(worktree)
    local_runs = worktree / "benchmark_runs"

    def fake_run_with_timeout(argv, timeout_s, *, stdout=None, stderr=None, env=None, cwd=None):
        target = local_runs / "datagen" / "tpch_sf001"
        target.mkdir(parents=True, exist_ok=True)
        (target / "lineitem.tbl").write_bytes(b"x" * 4096)
        return TimeoutResult(exit_code=0, timed_out=False, elapsed_s=0.1)

    fake_argv = [sys.executable, "-c", "print('unused')"]
    with (
        patch.object(runner, "benchbox_run_argv", return_value=fake_argv),
        patch.object(runner, "run_with_timeout", side_effect=fake_run_with_timeout),
    ):
        result = runner.run_cell(
            "duckdb",
            "tpch",
            0.01,
            timeout_s=10,
            log_dir=tmp_path / "logs",
            benchmark_runs_dir=local_runs,
            now=_dt.datetime(2026, 6, 1, 12, 0, 0),
        )
    assert isinstance(result, runner.CellResult)
    assert (local_runs / "datagen" / "tpch_sf001" / "lineitem.tbl").exists()


def test_default_log_dir_honours_benchbox_output_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path))
    out = runner._default_log_dir(_dt.datetime(2026, 5, 5))
    assert out == tmp_path / "logs" / "uat_20260505"


def test_classify_for_submit_vocabulary_and_clean_result(tmp_path: Path):
    result_path = tmp_path / "benchmark_runs" / "results" / "clean.json"
    _write_result_json(result_path)

    assert runner.classify_for_submit(result_path) is runner.SubmitTerminalState.submittable
    assert set(runner.SubmitTerminalState.__members__) == {
        "submittable",
        "unofficial",
        "query_failure",
        "schema_violation",
        "bundle_load_error",
        "unvalidated",
        "missing_manifest",
    }


def test_submit_state_is_cell_failure_excludes_unvalidated() -> None:
    assert not runner.submit_state_is_cell_failure("unvalidated")
    assert not runner.submit_state_is_cell_failure(runner.SubmitTerminalState.unvalidated)
    assert runner.submit_state_is_cell_failure("schema_violation")
    assert runner.submit_state_is_cell_failure("bundle_load_error")
    assert runner.submit_state_is_cell_failure("query_failure")
    assert runner.submit_state_is_cell_failure("missing_manifest")


def test_classify_for_submit_marks_query_failure_and_run_cell_failed(tmp_path: Path):
    result_path = tmp_path / "benchmark_runs" / "results" / "failed-query.json"
    _write_result_json(result_path, failed=1)
    fake_argv = [sys.executable, "-c", f"print({str(result_path)!r})"]

    with patch.object(runner, "benchbox_run_argv", return_value=fake_argv):
        result = runner.run_cell("duckdb", "tpch", 0.01, timeout_s=10, log_dir=tmp_path)

    assert runner.classify_for_submit(result_path) is runner.SubmitTerminalState.query_failure
    assert result.status == "failed"
    assert result.exit_code == 1
    assert result.submit_terminal_state == "query_failure"


def test_classify_for_submit_keeps_unofficial_as_passed_cell(tmp_path: Path):
    result_path = tmp_path / "benchmark_runs" / "results" / "unofficial.json"
    _write_result_json(result_path, compliance_class="unofficial_subscale")
    fake_argv = [sys.executable, "-c", f"print({str(result_path)!r})"]

    with patch.object(runner, "benchbox_run_argv", return_value=fake_argv):
        result = runner.run_cell("duckdb", "tpcds", 0.01, timeout_s=10, log_dir=tmp_path)

    assert runner.classify_for_submit(result_path) is runner.SubmitTerminalState.unofficial
    assert result.status == "passed"
    assert result.submit_terminal_state == "unofficial"


def test_classify_for_submit_keeps_unvalidated_as_passed_cell(tmp_path: Path):
    result_path = tmp_path / "benchmark_runs" / "results" / "unvalidated.json"
    _write_result_json(result_path, validation="not_validated")
    fake_argv = [sys.executable, "-c", f"print({str(result_path)!r})"]

    with patch.object(runner, "benchbox_run_argv", return_value=fake_argv):
        result = runner.run_cell("duckdb", "tpch", 0.01, timeout_s=10, log_dir=tmp_path)

    assert runner.classify_for_submit(result_path) is runner.SubmitTerminalState.unvalidated
    assert result.status == "passed"
    assert result.exit_code == 0
    assert result.submit_terminal_state == "unvalidated"


def test_classify_for_submit_prefers_unofficial_over_unvalidated(tmp_path: Path):
    result_path = tmp_path / "benchmark_runs" / "results" / "unofficial-unvalidated.json"
    _write_result_json(result_path, compliance_class="unofficial_subscale", validation="not_validated")
    assert runner.classify_for_submit(result_path) is runner.SubmitTerminalState.unofficial


def test_classify_for_submit_keeps_uncertain_with_translation_fallback_as_passed_cell(tmp_path: Path):
    result_path = tmp_path / "benchmark_runs" / "results" / "uncertain-fallback.json"
    _write_result_json(result_path, validation="uncertain", translation_status="fallback")
    fake_argv = [sys.executable, "-c", f"print({str(result_path)!r})"]

    with patch.object(runner, "benchbox_run_argv", return_value=fake_argv):
        result = runner.run_cell("duckdb", "tpch", 0.01, timeout_s=10, log_dir=tmp_path)

    assert runner.classify_for_submit(result_path) is runner.SubmitTerminalState.unvalidated
    assert not runner.submit_state_is_cell_failure(runner.SubmitTerminalState.unvalidated)
    assert result.status == "passed"
    assert result.exit_code == 0
    assert result.submit_terminal_state == "unvalidated"


def test_classify_for_submit_marks_translation_fallback_as_cell_failure(tmp_path: Path):
    result_path = tmp_path / "benchmark_runs" / "results" / "translation-fallback.json"
    _write_result_json(result_path, validation="passed", translation_status="fallback")
    fake_argv = [sys.executable, "-c", f"print({str(result_path)!r})"]

    with patch.object(runner, "benchbox_run_argv", return_value=fake_argv):
        result = runner.run_cell("duckdb", "tpch", 0.01, timeout_s=10, log_dir=tmp_path)

    assert runner.classify_for_submit(result_path) is runner.SubmitTerminalState.schema_violation
    assert runner.submit_state_is_cell_failure(runner.SubmitTerminalState.schema_violation)
    assert result.status == "failed"
    assert result.submit_terminal_state == "schema_violation"


def _fake_run_with_timeout_factory(*, exit_code: int, timed_out: bool):

    def fake_run_with_timeout(argv, timeout_s, *, stdout=None, stderr=None, env=None, cwd=None):
        return TimeoutResult(exit_code=exit_code, timed_out=timed_out, elapsed_s=0.1, stdout=b"", stderr=b"")

    return fake_run_with_timeout


def test_run_cell_official_timeout_preserves_timed_out_status(tmp_path: Path):
    fake_argv = [sys.executable, "-c", "print('unused')"]
    with (
        patch.object(runner, "benchbox_run_official_argv", return_value=fake_argv),
        patch.object(
            runner, "run_with_timeout", side_effect=_fake_run_with_timeout_factory(exit_code=124, timed_out=True)
        ),
        patch.object(runner, "resolve_official_result_path", return_value=None),
    ):
        result = runner.run_cell(
            "duckdb",
            "tpch",
            0.01,
            timeout_s=1,
            log_dir=tmp_path,
            official=True,
            streams=3,
        )

    assert result.status == "timed-out"
    assert result.exit_code == 124
    assert result.result_path is None
    assert result.throughput_check == "not validated: cell timed out"


def test_run_cell_official_missing_result_downgrades_passed_cell(tmp_path: Path):
    fake_argv = [sys.executable, "-c", "print('unused')"]
    with (
        patch.object(runner, "benchbox_run_official_argv", return_value=fake_argv),
        patch.object(
            runner, "run_with_timeout", side_effect=_fake_run_with_timeout_factory(exit_code=0, timed_out=False)
        ),
        patch.object(runner, "resolve_official_result_path", return_value=None),
    ):
        result = runner.run_cell(
            "duckdb",
            "tpch",
            0.01,
            timeout_s=10,
            log_dir=tmp_path,
            official=True,
            streams=3,
        )

    assert result.status == "failed"
    assert result.exit_code == 1
    assert result.throughput_check == "no result JSON resolved for throughput validation"


def test_run_cell_official_failed_exit_with_missing_result_keeps_original_exit_code(tmp_path: Path):
    fake_argv = [sys.executable, "-c", "print('unused')"]
    with (
        patch.object(runner, "benchbox_run_official_argv", return_value=fake_argv),
        patch.object(
            runner, "run_with_timeout", side_effect=_fake_run_with_timeout_factory(exit_code=2, timed_out=False)
        ),
        patch.object(runner, "resolve_official_result_path", return_value=None),
    ):
        result = runner.run_cell(
            "duckdb",
            "tpch",
            0.01,
            timeout_s=10,
            log_dir=tmp_path,
            official=True,
            streams=3,
        )

    assert result.status == "failed"
    assert result.exit_code == 2
    assert result.throughput_check == "no result JSON resolved for throughput validation"


def test_run_cell_official_downgrades_passed_cell_on_stream_count_mismatch(tmp_path: Path):
    result_path = tmp_path / "benchmark_runs" / "results" / "official.json"
    _write_result_json(result_path)
    fake_argv = [sys.executable, "-c", "pass"]
    with (
        patch.object(runner, "benchbox_run_official_argv", return_value=fake_argv),
        patch.object(runner, "resolve_official_result_path", return_value=result_path),
    ):
        result = runner.run_cell(
            "duckdb",
            "tpch",
            0.01,
            timeout_s=10,
            log_dir=tmp_path,
            official=True,
            streams=3,
        )

    assert result.status == "failed"
    assert result.exit_code == 1
    assert "throughput stream count mismatch" in (result.throughput_check or "")


def test_run_cell_official_threads_emitted_path_into_result_resolution(tmp_path: Path):
    result_path = tmp_path / "benchmark_runs" / "results" / "official.json"
    _write_result_json(result_path)
    fake_argv = [sys.executable, "-c", "pass"]
    timeout_result = TimeoutResult(
        exit_code=0,
        timed_out=False,
        elapsed_s=0.1,
        stdout=f"{result_path}\n".encode(),
        stderr=b"",
    )
    with (
        patch.object(runner, "benchbox_run_official_argv", return_value=fake_argv),
        patch.object(runner, "run_with_timeout", return_value=timeout_result),
        patch.object(runner, "resolve_official_result_path", return_value=result_path) as mock_resolve,
    ):
        runner.run_cell(
            "duckdb",
            "tpch",
            1,
            timeout_s=10,
            log_dir=tmp_path,
            official=True,
            streams=3,
        )

    mock_resolve.assert_called_once()
    assert mock_resolve.call_args.kwargs["emitted_path"] == str(result_path)
    assert set(mock_resolve.call_args.kwargs) == {"emitted_path"}


def _throughput_payload(*, platform_name: str = "DuckDB", scale_factor: float = 1.0) -> dict:
    from benchbox.core.results.metrics import TPCMetricsCalculator

    queries = [
        {"id": str(i), "stream": stream, "status": "SUCCESS", "run_type": "measurement", "test_type": "throughput"}
        for stream in (1, 2, 3)
        for i in range(1, 23)
    ]
    return {
        "platform": {"name": platform_name},
        "benchmark": {"id": "tpch", "scale_factor": scale_factor},
        "phases": {"throughput_test": {"duration_ms": 1_000_000}},
        "queries": queries,
        "summary": {
            "tpc_metrics": {
                "throughput_at_size": TPCMetricsCalculator.calculate_throughput_at_size(
                    total_queries=66, total_time_seconds=1000.0, scale_factor=scale_factor, num_streams=3
                )
            }
        },
    }


def _run_official_cell_with_result(tmp_path: Path, result_path: Path):
    timeout_result = TimeoutResult(
        exit_code=0, timed_out=False, elapsed_s=0.1, stdout=f"{result_path}\n".encode(), stderr=b""
    )
    with (
        patch.object(runner, "benchbox_run_official_argv", return_value=[sys.executable, "-c", "pass"]),
        patch.object(runner, "run_with_timeout", return_value=timeout_result),
        patch.object(runner, "classify_for_submit", return_value=runner.SubmitTerminalState.submittable),
    ):
        return runner.run_cell("duckdb", "tpch", 1, timeout_s=10, log_dir=tmp_path, official=True, streams=3)


def test_run_cell_official_passes_when_result_matches_cell_and_streams(tmp_path: Path):
    result_path = tmp_path / "results" / "official.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps(_throughput_payload()), encoding="utf-8")

    result = _run_official_cell_with_result(tmp_path, result_path)

    assert result.status == "passed"
    assert result.throughput_check == "ok"


def test_run_cell_official_fails_when_result_json_is_unreadable(tmp_path: Path):
    result_path = tmp_path / "results" / "official.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text("{not json", encoding="utf-8")

    result = _run_official_cell_with_result(tmp_path, result_path)

    assert result.status == "failed"
    assert result.exit_code == 1
    assert "could not read result JSON" in (result.throughput_check or "")


def test_run_cell_official_fails_when_result_json_belongs_to_another_platform(tmp_path: Path):
    result_path = tmp_path / "results" / "official.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps(_throughput_payload(platform_name="PostgreSQL")), encoding="utf-8")

    result = _run_official_cell_with_result(tmp_path, result_path)

    assert result.status == "failed"
    assert "does not match requested 'duckdb'" in (result.throughput_check or "")


def test_run_cell_official_fails_when_result_json_has_another_scale_factor(tmp_path: Path):
    result_path = tmp_path / "results" / "official.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps(_throughput_payload(scale_factor=10.0)), encoding="utf-8")

    result = _run_official_cell_with_result(tmp_path, result_path)

    assert result.status == "failed"
    assert "scale factor" in (result.throughput_check or "")


def _write_obt_clickhouse_result(
    path: Path,
    failed_ids: list[str],
    driver_version: str = "4.1.6",
    failed_status: str = "FAILED",
    failed_extra: dict | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"id": qid, "status": failed_status, "ms": 1, **(failed_extra or {})} for qid in failed_ids] + [
        {"id": "1", "status": "SUCCESS", "ms": 1},
        {"id": "2", "status": "SUCCESS", "ms": 1},
    ]
    payload = {
        "version": "2.2",
        "run": {
            "id": "uat-test",
            "timestamp": "2026-01-01T00:00:00",
            "total_duration_ms": 1,
            "query_time_ms": 1,
            "iterations": 1,
            "streams": 1,
        },
        "benchmark": {"id": "tpcds_obt", "name": "TPC-DS OBT", "scale_factor": 1.0, "test_type": "power"},
        "platform": {"name": "ClickHouse Local"},
        "execution": {"driver_version_actual": driver_version},
        "summary": {
            "queries": {"total": len(rows), "passed": 2, "failed": len(failed_ids)},
            "validation": {"status": "failed"},
        },
        "queries": rows,
        "phases": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    ("failed_ids", "driver_version", "failed_status", "failed_extra", "expected_status"),
    [
        (["41", "44"], "4.1.6", "FAILED", None, "passed"),
        (["44"], "4.1.6", "FAILED", None, "passed"),
        (["41", "44", "7"], "4.1.6", "FAILED", None, "failed"),
        (["41", "44"], "4.2.0", "FAILED", None, "failed"),
        (["41", "44"], "4.1.6", "VALIDATION_FAILED", None, "failed"),
        (
            ["41"],
            "4.1.6",
            "FAILED",
            {"rows": 2, "row_count_validation": {"status": "FAILED", "expected": 3, "actual": 2}},
            "failed",
        ),
    ],
)
def test_run_cell_honours_documented_clickhouse_obt_exclusions(
    tmp_path: Path,
    failed_ids: list[str],
    driver_version: str,
    failed_status: str,
    failed_extra: dict | None,
    expected_status: str,
):
    result_path = tmp_path / "benchmark_runs" / "results" / "obt.json"
    _write_obt_clickhouse_result(result_path, failed_ids, driver_version, failed_status, failed_extra)
    fake_argv = [sys.executable, "-c", f"print({str(result_path)!r}); raise SystemExit(1)"]

    with patch.object(runner, "benchbox_run_argv", return_value=fake_argv):
        result = runner.run_cell("clickhouse-local", "tpcds_obt", 1.0, timeout_s=10, log_dir=tmp_path)

    assert result.status == expected_status
    assert result.submit_terminal_state == "query_failure"
    assert (result.exit_code == 0) == (expected_status == "passed")


def test_run_cell_rejects_documented_exclusion_when_a_warmup_row_failed_row_count_validation(tmp_path: Path):
    result_path = tmp_path / "benchmark_runs" / "results" / "obt.json"
    _write_obt_clickhouse_result(result_path, ["41"])
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["queries"].append(
        {
            "id": "2",
            "iter": 0,
            "ms": 1,
            "rows": 2,
            "run_type": "warmup",
            "status": "SUCCESS",
            "stream": 0,
            "row_count_validation": {"status": "FAILED", "expected": 3, "actual": 2},
        }
    )
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    fake_argv = [sys.executable, "-c", f"print({str(result_path)!r}); raise SystemExit(1)"]

    with patch.object(runner, "benchbox_run_argv", return_value=fake_argv):
        result = runner.run_cell("clickhouse-local", "tpcds_obt", 1.0, timeout_s=10, log_dir=tmp_path)

    assert result.status == "failed"
