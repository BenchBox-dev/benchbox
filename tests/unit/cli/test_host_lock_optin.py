from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.cli.host_lock_optin import HOST_LOCK_ENV, host_lock_enabled
from benchbox.cli.main import cli
from benchbox.utils import host_lock as hl
from tests.unit.cli.test_cli_data_load_modes import _mock_run_command_components
from tests.utilities.paths import REPO_ROOT

pytestmark = [pytest.mark.unit, pytest.mark.fast]

HOLDER_PROGRAM = (
    "import importlib.util, sys, time\n"
    "spec = importlib.util.spec_from_file_location('hl_holder', sys.argv[1])\n"
    "hl = importlib.util.module_from_spec(spec)\n"
    "sys.modules[spec.name] = hl\n"
    "spec.loader.exec_module(hl)\n"
    "hl.acquire(hl.lock_path(), wait_seconds=0, phase='test', command='holder-cmd --flag')\n"
    "print('ready', flush=True)\n"
    "time.sleep(120)\n"
)

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process semantics")


@pytest.fixture
def lock_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "lock"
    monkeypatch.setenv(hl.LOCK_DIR_ENV, str(directory))
    monkeypatch.setenv(hl.WAIT_ENV, "0")
    monkeypatch.setenv(hl.ADMIT_ENV, "force")
    monkeypatch.setenv(hl.WATCHDOG_MODE_ENV, "off")
    monkeypatch.delenv(hl.OWNER_ENV, raising=False)
    monkeypatch.delenv(HOST_LOCK_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return directory / hl.LOCK_NAME


@pytest.fixture
def holder(lock_file: Path) -> Iterator[subprocess.Popen]:
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", HOLDER_PROGRAM, str(REPO_ROOT / "benchbox" / "utils" / "host_lock.py")],
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready", process.stderr.read() if process.stderr else ""
    yield process
    process.kill()
    process.wait(timeout=10)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _write_home_config(home: Path, text: str) -> None:
    config = home / ".benchbox" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(text, encoding="utf-8")


def test_default_is_off(lock_file: Path) -> None:
    assert host_lock_enabled() is False


@pytest.mark.parametrize(("raw", "expected"), [("1", True), ("true", True), ("0", False), ("off", False)])
def test_env_override(lock_file: Path, monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv(HOST_LOCK_ENV, raw)
    assert host_lock_enabled() is expected


def test_home_config_enables_and_env_overrides(
    lock_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_home_config(tmp_path / "home", "host_lock: true\n")
    assert host_lock_enabled() is True
    monkeypatch.setenv(HOST_LOCK_ENV, "0")
    assert host_lock_enabled() is False


def test_working_directory_config_cannot_toggle(
    lock_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "benchbox.yaml").write_text("host_lock: true\n", encoding="utf-8")
    monkeypatch.chdir(workdir)
    assert host_lock_enabled() is False
    _write_home_config(tmp_path / "home", "host_lock: true\n")
    (workdir / "benchbox.yaml").write_text("host_lock: false\n", encoding="utf-8")
    assert host_lock_enabled() is True


def test_setting_off_leaves_no_holder_record(lock_file: Path) -> None:
    with _mock_run_command_components():
        result = CliRunner().invoke(
            cli, ["run", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
        )
    assert result.exit_code == 0, result.output
    assert not lock_file.exists() or hl.read_holder(lock_file).strip() == ""


@POSIX_ONLY
@pytest.mark.parametrize(
    "argv",
    [
        ["run", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"],
        ["run-official", "tpch", "--platform", "duckdb", "--scale", "1", "--phases", "power"],
        ["datagen", "--benchmark", "tpch", "--scale", "0.01"],
        ["convert", "--input", ".", "--format", "parquet"],
        ["compare", "-p", "duckdb", "-p", "sqlite"],
        ["compare-dataframes", "--platforms", "polars-df"],
    ],
)
def test_enabled_commands_are_refused_naming_the_holder(
    lock_file: Path, holder: subprocess.Popen, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    monkeypatch.setenv(HOST_LOCK_ENV, "1")
    result = CliRunner().invoke(cli, argv)
    assert result.exit_code == hl.EXIT_REFUSED, result.output
    assert "holder-cmd --flag" in result.output


@POSIX_ONLY
def test_dry_run_and_read_only_commands_are_not_refused(
    lock_file: Path, holder: subprocess.Popen, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(HOST_LOCK_ENV, "1")
    runner = CliRunner()
    dry = runner.invoke(
        cli,
        [
            "run",
            "--dry-run",
            str(tmp_path / "dry"),
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--non-interactive",
        ],
    )
    assert dry.exit_code != hl.EXIT_REFUSED, dry.output
    assert "holder-cmd" not in dry.output
    listing = runner.invoke(cli, ["compare", "--list-platforms"])
    assert listing.exit_code != hl.EXIT_REFUSED, listing.output
    assert "holder-cmd" not in listing.output
    missing = runner.invoke(cli, ["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")])
    assert "holder-cmd" not in missing.output
    results = runner.invoke(cli, ["results", "--help"])
    assert results.exit_code == 0


def test_in_process_reentry_inside_a_held_session_does_not_wait(
    lock_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(HOST_LOCK_ENV, "1")
    session = hl.acquire(lock_file, wait_seconds=0, phase="pytest-session", gate="hermetic")
    try:
        with _mock_run_command_components():
            result = CliRunner().invoke(
                cli, ["run", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
            )
        assert result.exit_code == 0, result.output
        assert session.count == 1
        assert session.guard is None
    finally:
        session.release()


def test_owner_env_is_exported_during_the_command_and_restored(
    lock_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchbox.cli.host_lock_optin import heavy_guard

    monkeypatch.setenv(HOST_LOCK_ENV, "1")
    with heavy_guard("run"):
        assert os.environ[hl.OWNER_ENV] == hl.process_identity()
        assert "phase:benchbox-cli" in hl.read_holder(lock_file)
    assert hl.OWNER_ENV not in os.environ
    assert not hl.lock_is_held(lock_file)


def test_admission_refusal_exits_with_the_admission_code(lock_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from benchbox.cli import host_lock_optin

    def refuse(**_kwargs: object) -> str:
        raise hl.AdmissionRefused("report-text")

    monkeypatch.setenv(HOST_LOCK_ENV, "1")
    monkeypatch.setattr(hl, "check_admission", refuse)
    with pytest.raises(SystemExit) as raised, host_lock_optin.heavy_guard("run"):
        pass
    assert raised.value.code == hl.EXIT_ADMISSION
    assert not hl.lock_is_held(lock_file)


class _BreachedGuard:
    breached = True

    def stop(self) -> None:
        return None


@pytest.mark.parametrize("body", ["exit", "error", "normal"])
def test_a_watchdog_breach_exits_79_on_every_path(lock_file: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    import click

    from benchbox.cli.host_lock_optin import heavy_guard

    monkeypatch.setenv(HOST_LOCK_ENV, "1")
    monkeypatch.setattr(hl, "check_admission", lambda **_kwargs: "")
    monkeypatch.setattr(hl, "start_guard", lambda handle, **_kwargs: _BreachedGuard())
    with pytest.raises(SystemExit) as raised, heavy_guard("run"):
        if body == "exit":
            raise click.exceptions.Exit(1)
        if body == "error":
            raise RuntimeError("engine died")
    assert raised.value.code == hl.EXIT_WATCHDOG
    if body != "normal":
        assert raised.value.__cause__ is not None
    assert hl.OWNER_ENV not in os.environ
    assert not hl.lock_is_held(lock_file)


@pytest.mark.parametrize(
    ("text", "expected"), [("host_lock: yes\n", True), ("host_lock: 1\n", True), ("host_lock: off\n", False)]
)
def test_home_config_accepts_the_env_truthy_set(lock_file: Path, tmp_path: Path, text: str, expected: bool) -> None:
    _write_home_config(tmp_path / "home", text)
    assert host_lock_enabled() is expected


@POSIX_ONLY
def test_non_interactive_compare_without_platforms_is_not_locked(
    lock_file: Path, holder: subprocess.Popen, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(HOST_LOCK_ENV, "1")
    result = CliRunner().invoke(cli, ["compare", "--non-interactive"])
    assert result.exit_code != hl.EXIT_REFUSED
    assert "holder-cmd" not in result.output
