from __future__ import annotations

import json
import os

import pytest

from tests.uat import docker_assets, engine_evidence

pytestmark = pytest.mark.medium


@pytest.fixture
def capture(tmp_path):
    spec = docker_assets.docker_platform_spec("starrocks")
    calls = []

    def run(runner):
        return engine_evidence.capture_engine_evidence(
            "starrocks", spec, "evidence-test", log_dir=tmp_path, runner=runner, env={}
        )

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        output = "abc123" if "-q" in argv else "blacklist event password=hidden-value token=private-value"
        return docker_assets.DockerCommandResult(tuple(argv), 0, output, "")

    return run, runner, calls, tmp_path / "starrocks-evidence"


def test_capture_writes_redacted_bounded_artifacts_and_allowlisted_metadata(capture, monkeypatch):
    run, runner, calls, destination = capture
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "never-export-this")
    monkeypatch.setenv("ImageVersion", "image-build")
    assert run(runner) == ()
    assert "blacklist event" in (destination / "fe.log").read_text(encoding="utf-8")
    contents = "".join(path.read_text(encoding="utf-8") for path in destination.iterdir())
    assert "hidden-value" not in contents
    assert "private-value" not in contents
    assert "never-export-this" not in contents
    assert (
        json.loads((destination / "host.json").read_text(encoding="utf-8"))["runner"]["ImageVersion"] == "image-build"
    )
    assert all(0 < kwargs["timeout_s"] <= 10 for _, kwargs in calls)
    assert all(".Config.Env" not in " ".join(argv) for argv, _ in calls)
    assert any("SHOW BACKENDS\\G SHOW BACKEND BLACKLIST\\G" in argv for argv, _ in calls)
    assert any("^mem_limit=" in " ".join(argv) for argv, _ in calls)
    assert all(
        record["status"] == "ok"
        for record in json.loads((destination / "manifest.json").read_text(encoding="utf-8"))["commands"]
    )


def test_capture_records_command_failure_and_continues(capture):
    run, runner, calls, destination = capture

    def failing_runner(argv, **kwargs):
        if "inspect" in argv:
            raise OSError("sensitive exception detail")
        if "SHOW BACKENDS\\G SHOW BACKEND BLACKLIST\\G" in argv:
            return docker_assets.DockerCommandResult(tuple(argv), 124, "partial blacklist", "", timed_out=True)
        return runner(argv, **kwargs)

    failures = run(failing_runner)
    assert any("OSError" in failure for failure in failures)
    assert any("timed_out=True" in failure for failure in failures)
    assert (destination / "be.INFO").exists()
    manifest = (destination / "manifest.json").read_text(encoding="utf-8")
    assert "sensitive exception detail" not in manifest
    assert "partial blacklist" in (destination / "backends.txt").read_text(encoding="utf-8")
    assert calls


def test_capture_deadline_stops_commands_and_marks_incomplete(capture, monkeypatch):
    run, runner, calls, destination = capture
    monkeypatch.setattr(engine_evidence, "elapsed_seconds", lambda _: 61)
    assert run(runner)
    assert not calls
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["commands"][0]["status"] == "deadline-exhausted"


def test_capture_file_size_is_bounded_and_truncation_visible(capture, monkeypatch):
    run, runner, _, destination = capture
    monkeypatch.setattr(engine_evidence, "_FILE_BYTES", 8192)

    def oversized(argv, **kwargs):
        if "-q" in argv:
            return runner(argv, **kwargs)
        return docker_assets.DockerCommandResult(
            tuple(argv), 0, "header\n" + "x" * 16384 + "\nlatest backend failure", ""
        )

    run(oversized)
    assert (destination / "fe.log").stat().st_size == 8192
    saved_log = (destination / "fe.log").read_text(encoding="utf-8")
    assert saved_log.startswith("header\n")
    assert "latest backend failure" in saved_log
    assert all(path.stat().st_size <= 8192 for path in destination.iterdir())
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert any(record.get("truncated") for record in manifest["commands"])


def test_log_script_preserves_bounded_tail_and_marks_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_evidence, "_LOG_ROOT", str(tmp_path))
    (tmp_path / "engine.log").write_text("first blacklist event\n" + "x" * 64, encoding="utf-8")
    result = docker_assets.run_docker_command(
        ["sh", "-c", engine_evidence._log_script("engine.log", limit=32)],
        timeout_s=5,
    )
    assert result.returncode == 0
    assert "SOURCE" in result.stdout and result.stdout.split("BYTES", 1)[1].split()[0] == "86"
    assert "TRUNCATED=true" in result.stdout
    assert result.stdout.endswith("x" * 32)
    missing = docker_assets.run_docker_command(
        ["sh", "-c", engine_evidence._log_script("missing.log", limit=32)],
        timeout_s=5,
    )
    assert missing.returncode == 1


def test_gc_file_limit_retains_newest_files_and_reports_omissions(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_evidence, "_LOG_ROOT", str(tmp_path))
    for index in range(10):
        path = tmp_path / f"gc.log.{index}"
        path.write_text(f"GC event {index}\n", encoding="utf-8")
        os.utime(path, (index + 1, index + 1))
    result = docker_assets.run_docker_command(
        ["sh", "-c", engine_evidence._log_script("gc.log.*", limit=32, count=8)],
        timeout_s=5,
    )
    assert result.succeeded
    assert "TRUNCATED=true" in result.stdout
    assert "gc.log.0 BYTES" not in result.stdout
    assert "gc.log.1 BYTES" not in result.stdout
    assert "gc.log.2 BYTES" in result.stdout
    assert "gc.log.9 BYTES" in result.stdout


def test_cedardb_capture_collects_logs_events_state_and_memory_redacted(tmp_path):
    spec = docker_assets.docker_platform_spec("cedardb")
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        output = "abc123" if "-q" in argv else "server terminated password=hidden-value"
        return docker_assets.DockerCommandResult(tuple(argv), 0, output, "")

    failures = engine_evidence.capture_engine_evidence(
        "cedardb", spec, "evidence-test", log_dir=tmp_path, runner=runner, env={}
    )
    destination = tmp_path / "cedardb-evidence"
    assert failures == ()
    assert {path.name for path in destination.iterdir()} == {
        "host.json",
        "containers.txt",
        "container-state.json",
        "container.log",
        "events.txt",
        "memory.txt",
        "manifest.json",
    }
    assert "terminated" in (destination / "container.log").read_text(encoding="utf-8")
    assert "hidden-value" not in "".join(path.read_text(encoding="utf-8") for path in destination.iterdir())
    assert any("logs" in argv and "--timestamps" in argv for argv in calls)
    assert any("events" in argv and "container=abc123" in argv for argv in calls)
    assert any(".State" in " ".join(argv) and "inspect" in argv for argv in calls)
