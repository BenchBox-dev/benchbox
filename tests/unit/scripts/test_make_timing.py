from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "make_timing.py"
SHELL_SHIM = ROOT / "scripts" / "make_timing_shell.sh"
INVENTORY_SCRIPT = ROOT / "make" / "check_makefile_inventory.py"
CONCURRENT_APPEND_SKIP = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "atomic O_APPEND whole-line writes and a 200-character cwd are POSIX behaviours; "
        "Windows emulates append non-atomically and limits cwd to MAX_PATH"
    ),
)
RECORD_FIELDS = {
    "schema",
    "target",
    "started_at",
    "ended_at",
    "duration_seconds",
    "exit_code",
    "cwd",
    "branch",
    "head",
    "host",
    "make_goals",
}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make_timing = _load("make_timing", SCRIPT)


def _exit_with(status: int) -> list[str]:
    return [sys.executable, "-c", f"import sys; sys.exit({status})"]


def _clean_environment(timings_file: Path, **extra: str) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("BENCHBOX_MAKE_TIMING", "GIT_"))
    }
    environment["BENCHBOX_MAKE_TIMINGS_FILE"] = str(timings_file)
    environment.update(extra)
    return environment


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_successful_command_appends_one_complete_record(tmp_path: Path) -> None:
    timings_file = tmp_path / "nested" / "timings.jsonl"
    environment = _clean_environment(timings_file, BENCHBOX_MAKE_TIMING_GOALS="pr-preflight test")

    status = make_timing.run_and_record(_exit_with(0), target="pr-preflight", environ=environment)

    assert status == 0
    (record,) = _lines(timings_file)
    assert set(record) == RECORD_FIELDS
    assert record["schema"] == "make_timing_v1"
    assert record["target"] == "pr-preflight"
    assert record["exit_code"] == 0
    assert record["make_goals"] == ["pr-preflight", "test"]
    assert record["cwd"] == os.getcwd()
    assert record["duration_seconds"] >= 0
    started = datetime.fromisoformat(record["started_at"].replace("Z", "+00:00"))
    ended = datetime.fromisoformat(record["ended_at"].replace("Z", "+00:00"))
    assert started.tzinfo is not None
    assert ended >= started


def test_failing_command_status_is_returned_and_recorded(tmp_path: Path) -> None:
    timings_file = tmp_path / "timings.jsonl"

    status = make_timing.run_and_record(_exit_with(7), target="pr-open", environ=_clean_environment(timings_file))

    assert status == 7
    (record,) = _lines(timings_file)
    assert record["exit_code"] == 7


def test_missing_command_returns_127_and_is_recorded(tmp_path: Path) -> None:
    timings_file = tmp_path / "timings.jsonl"

    status = make_timing.run_and_record(
        ["/nonexistent/benchbox-command"], target="pr-arm", environ=_clean_environment(timings_file)
    )

    assert status == 127
    assert _lines(timings_file)[0]["exit_code"] == 127


@pytest.mark.parametrize("command_status", [0, 3])
def test_unwritable_timings_file_never_changes_the_exit_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command_status: int
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed", encoding="utf-8")
    environment = _clean_environment(blocker / "timings.jsonl")

    status = make_timing.run_and_record(_exit_with(command_status), target="t", environ=environment)

    assert status == command_status
    assert "could not record timing" in capsys.readouterr().err


def test_directory_as_timings_file_never_changes_the_exit_status(tmp_path: Path) -> None:
    status = make_timing.run_and_record(_exit_with(0), target="t", environ=_clean_environment(tmp_path))

    assert status == 0


@pytest.mark.parametrize("value", ["0", "off", "false", "no", "OFF"])
def test_switch_disables_recording_but_still_runs_the_command(tmp_path: Path, value: str) -> None:
    timings_file = tmp_path / "timings.jsonl"
    marker = tmp_path / "ran"
    environment = _clean_environment(timings_file, BENCHBOX_MAKE_TIMINGS=value)

    status = make_timing.run_and_record(
        [sys.executable, "-c", f"import pathlib, sys; pathlib.Path({str(marker)!r}).touch(); sys.exit(4)"],
        target="t",
        environ=environment,
    )

    assert status == 4
    assert marker.exists()
    assert not timings_file.exists()


def test_default_location_is_in_the_benchbox_home_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("BENCHBOX_MAKE_TIMINGS_FILE", raising=False)

    assert make_timing.timings_path() == tmp_path / ".benchbox" / "make-timings.jsonl"


@CONCURRENT_APPEND_SKIP
def test_concurrent_runs_append_whole_lines(tmp_path: Path) -> None:
    timings_file = tmp_path / "timings.jsonl"
    environment = _clean_environment(timings_file)
    runs = 24
    processes = [
        subprocess.Popen(
            [sys.executable, str(SCRIPT), "run", "--target", f"target-{index % 3}", "--", *_exit_with(0)],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for index in range(runs)
    ]
    for process in processes:
        process.communicate()
        assert process.returncode == 0

    records = _lines(timings_file)
    assert len(records) == runs
    assert {record["target"] for record in records} == {"target-0", "target-1", "target-2"}


@CONCURRENT_APPEND_SKIP
def test_concurrent_appends_of_large_records_do_not_interleave(tmp_path: Path) -> None:
    timings_file = tmp_path / "timings.jsonl"
    environment = _clean_environment(timings_file)
    long_directory = tmp_path / ("d" * 200)
    long_directory.mkdir()
    processes = [
        subprocess.Popen(
            [sys.executable, str(SCRIPT), "run", "--target", "wide", "--", *_exit_with(0)],
            env=environment,
            cwd=long_directory,
        )
        for _ in range(16)
    ]
    for process in processes:
        assert process.wait() == 0

    records = _lines(timings_file)
    assert len(records) == 16
    assert all(record["cwd"].endswith("d" * 200) for record in records)


def test_target_comes_from_the_make_environment_when_not_given(tmp_path: Path) -> None:
    timings_file = tmp_path / "timings.jsonl"
    environment = _clean_environment(timings_file, BENCHBOX_MAKE_TIMING_TARGET="from-make")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "run",
            "--",
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(5)",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 5
    assert completed.stdout == "out\n"
    assert completed.stderr == "err\n"
    assert _lines(timings_file)[0]["target"] == "from-make"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_signal_death_is_recorded_and_reraised(tmp_path: Path) -> None:
    timings_file = tmp_path / "timings.jsonl"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "run", "--target", "killed", "--", "sh", "-c", "kill -TERM $$"],
        env=_clean_environment(timings_file),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == -signal.SIGTERM
    assert _lines(timings_file)[0]["exit_code"] == 128 + signal.SIGTERM


def test_run_without_a_command_is_a_usage_error(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "run", "--target", "t", "--"],
        env=_clean_environment(tmp_path / "timings.jsonl"),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2


def _record(target: str, seconds: float, *, started: datetime, exit_code: int = 0) -> dict:
    return {
        "schema": "make_timing_v1",
        "target": target,
        "started_at": started.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "ended_at": started.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "duration_seconds": seconds,
        "exit_code": exit_code,
        "cwd": "/work",
        "branch": "feat/x",
        "head": "abc1234",
        "host": "h",
        "make_goals": [target],
    }


def test_report_prints_count_median_p90_and_max_per_target() -> None:
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    recent = now - timedelta(hours=1)
    records = [_record("pr-preflight", seconds, started=recent) for seconds in range(1, 11)]
    records.append(_record("pr-preflight", 600, started=recent, exit_code=1))
    records.append(_record("pr-open", 2, started=recent))
    records.append(_record("pr-open", 100, started=now - timedelta(days=60)))

    report = make_timing.render_report(records, now=now, days=30, slowest=2)

    rows = {line.split()[0]: line.split() for line in report.splitlines() if line.startswith("pr-")}
    assert rows["pr-preflight"][1:3] == ["11", "1"]
    assert rows["pr-preflight"][3:] == ["6.0s", "10.0s", "10.0m"]
    assert rows["pr-open"][1:3] == ["1", "0"]
    assert rows["pr-open"][5] == "2.0s"
    assert "Slowest 2 recent runs" in report
    slowest_section = report.split("Slowest 2 recent runs")[1].splitlines()
    assert "10.0m" in slowest_section[1]
    assert "exit=1" in slowest_section[1]


def test_percentile_uses_nearest_rank() -> None:
    values = [float(number) for number in range(1, 11)]

    assert make_timing.percentile(values, 0.9) == 9.0
    assert make_timing.percentile(values, 1.0) == 10.0
    assert make_timing.percentile([4.0], 0.9) == 4.0


def test_read_records_skips_damaged_and_foreign_lines(tmp_path: Path) -> None:
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    good = _record("pr-open", 1.5, started=now)
    timings_file = tmp_path / "timings.jsonl"
    timings_file.write_text(
        "\n".join(
            [
                json.dumps(good),
                "{not json",
                json.dumps({**good, "schema": "other_v9"}),
                json.dumps({"schema": "make_timing_v1"}),
                "",
            ]
        ),
        encoding="utf-8",
    )

    records, skipped = make_timing.read_records(timings_file)

    assert records == [good]
    assert skipped == 3


def test_report_on_a_missing_file_says_there_is_nothing_to_show(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "report", "--file", str(tmp_path / "absent.jsonl")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "No timing records in this window." in completed.stdout


def _makefile_text() -> str:
    return (ROOT / "Makefile").read_text(encoding="utf-8")


def _timed_targets() -> list[str]:
    match = re.search(r"^MAKE_TIMED_TARGETS := ((?:.*\\\n)*.*)$", _makefile_text(), re.MULTILINE)
    assert match is not None
    return match.group(1).replace("\\\n", " ").split()


def _logical_recipe_lines(recipe: str) -> int:
    count = 0
    continued = False
    for line in recipe.splitlines():
        if not line.strip():
            continue
        if not continued:
            count += 1
        continued = line.endswith("\\")
    return count


def test_timed_targets_are_prerequisite_free_single_command_recipes() -> None:
    inventory_module = _load("check_makefile_inventory_for_timing", INVENTORY_SCRIPT)
    rules = inventory_module.build_inventory(ROOT)["rules"]
    targets = _timed_targets()

    assert len(targets) == len(set(targets))
    for target in targets:
        recipes = [rule for rule in rules[target] if rule["recipe"].strip()]
        assert len(recipes) == 1, target
        assert recipes[0]["header"] == f"{target}:", target
        assert _logical_recipe_lines(recipes[0]["recipe"]) == 1, target


def test_makefile_routes_the_timed_targets_through_the_shell_shim() -> None:
    text = _makefile_text()

    assert "$(MAKE_TIMED_TARGETS): SHELL := $(MAKE_TIMING_SHELL)" in text
    assert "$(MAKE_TIMED_TARGETS): export BENCHBOX_MAKE_TIMING_TARGET = $@" in text
    assert "$(MAKE_TIMED_TARGETS): export BENCHBOX_MAKE_TIMING_GOALS = $(MAKECMDGOALS)" in text
    assert os.access(SHELL_SHIM, os.X_OK)


@pytest.mark.skipif(shutil.which("make") is None or sys.platform == "win32", reason="POSIX make is required")
def test_make_recipe_runs_through_the_shim_with_unchanged_output_and_status(tmp_path: Path) -> None:
    timings_file = tmp_path / "timings.jsonl"
    wiring = [
        line
        for line in _makefile_text().splitlines()
        if line.startswith("$(MAKE_TIMED_TARGETS):") or line.startswith("MAKE_TIMING_SHELL :=")
    ]
    (tmp_path / "Makefile").write_text(
        "\n".join(
            [
                f"BENCHBOX_MAKEFILE_ROOT := {ROOT}/",
                "MAKE_TIMED_TARGETS := probe-ok probe-fail",
                *wiring,
                ".PHONY: probe-ok probe-fail",
                "probe-ok:",
                "\t@echo probe-output $(WORD)",
                "probe-fail:",
                "\t@echo before-failure; exit 9",
                "",
            ]
        ),
        encoding="utf-8",
    )
    environment = _clean_environment(timings_file)

    ok = subprocess.run(
        ["make", "-s", "-C", str(tmp_path), "probe-ok", "WORD=forwarded"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    failed = subprocess.run(
        ["make", "-s", "-C", str(tmp_path), "probe-fail"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert ok.returncode == 0
    assert ok.stdout == "probe-output forwarded\n"
    assert failed.returncode == 2
    assert failed.stdout == "before-failure\n"
    first, second = _lines(timings_file)
    assert (first["target"], first["exit_code"], first["make_goals"]) == ("probe-ok", 0, ["probe-ok"])
    assert (second["target"], second["exit_code"]) == ("probe-fail", 9)


@pytest.mark.skipif(shutil.which("make") is None or sys.platform == "win32", reason="POSIX make is required")
def test_make_switch_skips_recording_and_keeps_the_recipe_running(tmp_path: Path) -> None:
    timings_file = tmp_path / "timings.jsonl"
    wiring = [
        line
        for line in _makefile_text().splitlines()
        if line.startswith("$(MAKE_TIMED_TARGETS):") or line.startswith("MAKE_TIMING_SHELL :=")
    ]
    (tmp_path / "Makefile").write_text(
        "\n".join(
            [
                f"BENCHBOX_MAKEFILE_ROOT := {ROOT}/",
                "MAKE_TIMED_TARGETS := probe",
                *wiring,
                ".PHONY: probe",
                "probe:",
                "\t@echo ran",
                "",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["make", "-s", "-C", str(tmp_path), "probe"],
        env=_clean_environment(timings_file, BENCHBOX_MAKE_TIMINGS="0"),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == "ran\n"
    assert not timings_file.exists()


@pytest.mark.skipif(shutil.which("make") is None or sys.platform == "win32", reason="POSIX make is required")
def test_parallel_make_jobserver_reaches_a_nested_make_through_the_shim(tmp_path: Path) -> None:
    timings_file = tmp_path / "timings.jsonl"
    wiring = [
        line
        for line in _makefile_text().splitlines()
        if line.startswith("$(MAKE_TIMED_TARGETS):") or line.startswith("MAKE_TIMING_SHELL :=")
    ]
    (tmp_path / "Makefile").write_text(
        "\n".join(
            [
                f"BENCHBOX_MAKEFILE_ROOT := {ROOT}/",
                "MAKE_TIMED_TARGETS := outer",
                *wiring,
                ".PHONY: outer inner",
                "outer:",
                "\t@$(MAKE) -s inner",
                "inner:",
                "\t@echo nested-ran",
                "",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["make", "-j2", "-s", "-C", str(tmp_path), "outer"],
        env=_clean_environment(timings_file),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == "nested-ran\n"
    assert "jobserver" not in completed.stderr
    assert [record["target"] for record in _lines(timings_file)] == ["outer"]
