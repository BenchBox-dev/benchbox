from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_FAKE_ENGINE_SCRIPT = """#!/usr/bin/env bash
# Fake CONTAINER_ENGINE for Makefile docker-guard tests. Never touches a
# real container engine: logs every invocation it receives and exits 0
# unconditionally, as if `compose up -d --wait` / `compose down -v` always
# succeeded.
printf '%s\\n' "$*" >> "$FAKE_ENGINE_LOG"
exit 0
"""


def _write_fake_engine(tmp_path: Path) -> Path:
    engine = tmp_path / "fake-container-engine"
    engine.write_text(_FAKE_ENGINE_SCRIPT)
    mode = engine.stat().st_mode
    engine.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return engine


def _run_make(
    target: str,
    tmp_path: Path,
    *,
    state_dir: Path,
    engine: Path,
    log_file: Path,
    data_dir: str | None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("BENCHBOX_DATA_DIR", None)
    if data_dir is not None:
        env["BENCHBOX_DATA_DIR"] = data_dir
    env["FAKE_ENGINE_LOG"] = str(log_file)

    return subprocess.run(
        [
            "make",
            "-C",
            str(REPO_ROOT),
            target,
            f"CONTAINER_ENGINE={engine}",
            f"DOCKER_TEST_STATE_DIR={state_dir}",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


@pytest.mark.parametrize(
    ("target", "platform"),
    [
        ("test-docker-up-lakesail", "lakesail"),
        ("test-docker-up-velox", "velox"),
        ("test-docker-up-trino", "trino"),
        ("test-docker-up-presto", "presto"),
    ],
)
def test_guard_rejects_missing_data_dir_without_touching_compose(tmp_path, target, platform):
    state_dir = tmp_path / "docker-projects"
    state_dir.mkdir()
    log_file = tmp_path / "engine.log"
    engine = _write_fake_engine(tmp_path)

    result = _run_make(target, tmp_path, state_dir=state_dir, engine=engine, log_file=log_file, data_dir=None)

    assert result.returncode != 0, result.stderr
    assert "BENCHBOX_DATA_DIR must be exported" in result.stderr
    assert f"docker/{platform}" in result.stderr
    assert not log_file.exists(), (
        "guard rejection must never reach the container engine -- "
        f"fake engine log unexpectedly created: {log_file.read_text() if log_file.exists() else ''}"
    )


@pytest.mark.parametrize(
    ("target", "platform"),
    [
        ("test-docker-up-lakesail", "lakesail"),
        ("test-docker-up-velox", "velox"),
        ("test-docker-up-trino", "trino"),
        ("test-docker-up-presto", "presto"),
    ],
)
def test_guard_rejects_relative_data_dir_without_touching_compose(tmp_path, target, platform):
    state_dir = tmp_path / "docker-projects"
    state_dir.mkdir()
    log_file = tmp_path / "engine.log"
    engine = _write_fake_engine(tmp_path)

    result = _run_make(
        target, tmp_path, state_dir=state_dir, engine=engine, log_file=log_file, data_dir="relative/data-dir"
    )

    assert result.returncode != 0, result.stderr
    assert "must be an ABSOLUTE path" in result.stderr
    assert f"docker/{platform}" in result.stderr
    assert not log_file.exists()


@pytest.mark.parametrize("data_dir", [None, "relative/data-dir"], ids=["missing", "relative"])
def test_docker_velox_guard_rejects_and_never_calls_compose_up(tmp_path, data_dir):
    state_dir = tmp_path / "docker-projects"
    state_dir.mkdir()
    log_file = tmp_path / "engine.log"
    engine = _write_fake_engine(tmp_path)

    result = _run_make(
        "test-docker-velox", tmp_path, state_dir=state_dir, engine=engine, log_file=log_file, data_dir=data_dir
    )

    assert result.returncode != 0, result.stderr
    assert "docker/velox" in result.stderr
    if log_file.exists():
        log_text = log_file.read_text()
        assert "up" not in log_text.split(), f"guard rejection must never reach `compose up`: {log_text!r}"


def test_up_questdb_is_not_guarded(tmp_path):
    state_dir = tmp_path / "docker-projects"
    state_dir.mkdir()
    log_file = tmp_path / "engine.log"
    engine = _write_fake_engine(tmp_path)

    result = _run_make(
        "test-docker-up-questdb", tmp_path, state_dir=state_dir, engine=engine, log_file=log_file, data_dir=None
    )

    assert result.returncode == 0, result.stderr
    assert log_file.exists(), "expected the (fake) container engine to be invoked for a non-mirroring platform"
    assert "up" in log_file.read_text()
    assert (state_dir / "questdb.project").exists()


def test_guard_rejection_leaves_a_tracked_stack_untouched(tmp_path):
    state_dir = tmp_path / "docker-projects"
    state_dir.mkdir()
    project_file = state_dir / "lakesail.project"
    live_project_name = "benchbox-lakesail-test-LIVE-FROM-SHELL-A"
    project_file.write_text(live_project_name + "\n")
    log_file = tmp_path / "engine.log"
    engine = _write_fake_engine(tmp_path)

    result = _run_make(
        "test-docker-up-lakesail", tmp_path, state_dir=state_dir, engine=engine, log_file=log_file, data_dir=None
    )

    assert result.returncode != 0, result.stderr
    assert "BENCHBOX_DATA_DIR must be exported" in result.stderr
    assert project_file.exists(), "guard rejection must not delete the tracking file for an already-running stack"
    assert project_file.read_text() == live_project_name + "\n", (
        "guard rejection must not rewrite the tracking file for an already-running stack"
    )
    assert not log_file.exists(), (
        "guard rejection must never invoke `compose down -v` against an already-running stack -- "
        f"fake engine log unexpectedly created: {log_file.read_text() if log_file.exists() else ''}"
    )


