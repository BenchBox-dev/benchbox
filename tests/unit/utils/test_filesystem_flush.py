from __future__ import annotations

import logging
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.core.tpcds.generator.runner import DsdgenRunnerMixin
from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.utils import filesystem_flush
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.filesystem_flush import flush_filesystem

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell and a sync command"),
]

BLOCKING_SECONDS = 60
BOUND_SECONDS = 0.5
PROMPT_SECONDS = 10


def _script(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _fake_generator_run(monkeypatch, executable_name: str) -> list[list[str]]:
    generated: list[list[str]] = []
    real_run = subprocess.run

    def run(cmd, **kwargs):
        if Path(cmd[0]).name == executable_name:
            generated.append(list(cmd))
            return SimpleNamespace(returncode=0)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return generated


@pytest.fixture
def blocking_sync(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _script(bin_dir, "sync", f"exec sleep {BLOCKING_SECONDS}")
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setattr(filesystem_flush, "FLUSH_TIMEOUT_SECONDS", BOUND_SECONDS)
    return bin_dir


def test_a_flush_that_finishes_reports_success():
    assert flush_filesystem(command=[sys.executable, "-c", "pass"]) is True


def test_a_flush_that_fails_reports_failure():
    assert flush_filesystem(command=[sys.executable, "-c", "raise SystemExit(3)"]) is False


def test_a_missing_flush_command_is_not_an_error():
    assert flush_filesystem(command=["benchbox-no-such-flush-command"]) is False


def test_a_stalled_flush_is_abandoned_within_the_bound_and_warns(caplog):
    start = mono_time()
    with caplog.at_level(logging.WARNING):
        result = flush_filesystem(
            BOUND_SECONDS, command=[sys.executable, "-c", f"import time; time.sleep({BLOCKING_SECONDS})"]
        )

    assert result is False
    assert elapsed_seconds(start) < PROMPT_SECONDS
    assert "unflushed writes" in caplog.text
    assert "Skipped the filesystem flush" in caplog.text


def test_tpcds_generation_returns_while_the_flush_is_stalled(tmp_path, blocking_sync, monkeypatch):
    generated = _fake_generator_run(monkeypatch, "dsdgen")
    generator = SimpleNamespace(
        dsdgen_exe=tmp_path / "dsdgen",
        scale_factor=1,
        verbose=False,
        _copy_distribution_files=lambda output_dir: None,
    )

    start = mono_time()
    DsdgenRunnerMixin._run_file_based_dsdgen(generator, tmp_path)

    assert generated
    assert elapsed_seconds(start) < PROMPT_SECONDS


def test_tpch_generation_returns_while_the_flush_is_stalled(tmp_path, blocking_sync, monkeypatch, caplog):
    generated = _fake_generator_run(monkeypatch, "dbgen")
    dbgen_path = tmp_path / "dbgen"
    dbgen_path.mkdir()
    (dbgen_path / "dists.dss").write_text("", encoding="utf-8")
    generator = SimpleNamespace(
        output_dir=tmp_path / "out",
        should_use_compression=lambda: False,
        parallel=1,
        dbgen_path=dbgen_path,
        dbgen_exe=dbgen_path / "dbgen",
        scale_factor=1,
        logger=logging.getLogger("tpch-flush-test"),
        very_verbose=False,
    )

    start = mono_time()
    with caplog.at_level(logging.WARNING, logger="tpch-flush-test"):
        TPCHDataGenerator._run_dbgen_native(generator, tmp_path / "work")

    assert generated
    assert elapsed_seconds(start) < PROMPT_SECONDS
    assert "unflushed writes" in caplog.text
