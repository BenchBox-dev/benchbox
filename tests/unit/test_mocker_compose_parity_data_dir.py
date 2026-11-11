from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PARITY_SCRIPT = REPO_ROOT / "_project" / "scripts" / "mocker_compose_parity.sh"

_FAKE_MAKE_SCRIPT = """#!/usr/bin/env bash
# Fake `make` for mocker_compose_parity.sh DATA_DIR-wiring tests. Never
# invokes a real container engine: logs the argv it receives (one line per
# invocation) and exits 0 unconditionally, as if `up -d --wait` / teardown
# always succeeded. Deliberately never writes a <plat>.project tracking
# file, so the parity script's own fresh-state checks take their SKIP path
# instead of shelling out to a real `docker`/`mocker` CLI.
printf '%s\\n' "$*" >> "$FAKE_MAKE_LOG"
exit 0
"""


def _write_fake_make(bin_dir: Path) -> None:
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "make"
    stub.write_text(_FAKE_MAKE_SCRIPT)
    mode = stub.stat().st_mode
    stub.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_parity_script(
    tmp_path: Path,
    *,
    platform: str,
    benchmark_data_dir: str | None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "fake-bin"
    _write_fake_make(bin_dir)
    log_file = tmp_path / "make.log"
    state_dir = tmp_path / "docker-projects"
    state_dir.mkdir()

    env = dict(os.environ)
    env.pop("BENCHBOX_DATA_DIR", None)
    if benchmark_data_dir is not None:
        env["BENCHBOX_DATA_DIR"] = benchmark_data_dir
    env["FAKE_MAKE_LOG"] = str(log_file)
    env["DOCKER_TEST_STATE_DIR"] = str(state_dir)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"

    result = subprocess.run(
        ["bash", str(PARITY_SCRIPT), "docker", platform],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    return result, log_file


def _data_dir_arg(log_line: str) -> str | None:
    for token in log_line.split():
        if token.startswith("BENCHBOX_DATA_DIR="):
            return token[len("BENCHBOX_DATA_DIR=") :]
    return None


def test_parity_script_passes_default_absolute_data_dir_to_up_and_down(tmp_path):
    result, log_file = _run_parity_script(tmp_path, platform="lakesail", benchmark_data_dir=None)

    assert log_file.exists(), f"expected the fake `make` to be invoked at all; stderr={result.stderr!r}"
    log_lines = log_file.read_text().splitlines()
    up_calls = [line for line in log_lines if "test-docker-up-lakesail" in line]
    down_calls = [line for line in log_lines if "test-docker-down-lakesail" in line]
    assert up_calls, f"expected a `make test-docker-up-lakesail` call; got: {log_lines}"
    assert down_calls, f"expected a `make test-docker-down-lakesail` call; got: {log_lines}"

    expected_default = str(REPO_ROOT / "benchmark_runs")
    for call in up_calls + down_calls:
        data_dir = _data_dir_arg(call)
        assert data_dir is not None, f"BENCHBOX_DATA_DIR not passed through: {call!r}"
        assert Path(data_dir).is_absolute(), f"BENCHBOX_DATA_DIR must be absolute, got {data_dir!r}"
        assert data_dir == expected_default, (
            f"expected the script's own default ({expected_default!r}) when "
            f"BENCHBOX_DATA_DIR is unset, got {data_dir!r}"
        )


def test_parity_script_forwards_an_explicit_absolute_data_dir_unchanged(tmp_path):
    custom_data_dir = str(tmp_path / "custom-benchbox-data")
    result, log_file = _run_parity_script(tmp_path, platform="lakesail", benchmark_data_dir=custom_data_dir)

    assert log_file.exists(), f"expected the fake `make` to be invoked at all; stderr={result.stderr!r}"
    log_lines = log_file.read_text().splitlines()
    up_calls = [line for line in log_lines if "test-docker-up-lakesail" in line]
    down_calls = [line for line in log_lines if "test-docker-down-lakesail" in line]
    assert up_calls
    assert down_calls

    for call in up_calls + down_calls:
        assert _data_dir_arg(call) == custom_data_dir, (
            f"expected explicit BENCHBOX_DATA_DIR forwarded verbatim: {call!r}"
        )


def test_parity_script_passes_data_dir_for_velox_too(tmp_path):
    custom_data_dir = str(tmp_path / "custom-velox-data")
    result, log_file = _run_parity_script(tmp_path, platform="velox", benchmark_data_dir=custom_data_dir)

    assert log_file.exists(), f"expected the fake `make` to be invoked at all; stderr={result.stderr!r}"
    log_lines = log_file.read_text().splitlines()
    up_calls = [line for line in log_lines if "test-docker-up-velox" in line]
    assert up_calls
    for call in up_calls:
        assert _data_dir_arg(call) == custom_data_dir


