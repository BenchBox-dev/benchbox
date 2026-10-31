from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from benchbox.utils.host_lock import EXIT_ADMISSION
from tests.utilities.paths import REPO_ROOT

pytestmark = [pytest.mark.fast, pytest.mark.unit]

WRAPPER = "scripts/linux_machine_run.sh"
WRAPPER_PATH = REPO_ROOT / WRAPPER
MACHINE_RUN = re.compile(r"\bcontainer machine run\b")
COMPOSE_UP = re.compile(r"(?:\bcompose\b|\$\(COMPOSE\))[^\n]*?\bup\b")
PYTHON_UP_ARGV = re.compile(r"""["']up["']\s*,\s*["']-d["']""")
PYTHON_UP_OWNER = "tests/uat/docker_assets.py"


def _logical_lines(text: str) -> list[str]:
    return [line for line in re.sub(r"\\\n\s*", " ", text).splitlines() if line.strip()]


def _is_prose(line: str) -> bool:
    stripped = line.lstrip("@ \t")
    return stripped.startswith(("echo ", "printf ", "#"))


def _shell_sources() -> dict[str, str]:
    paths = [REPO_ROOT / "Makefile", *sorted((REPO_ROOT / "make").glob("*.mk"))]
    paths += sorted((REPO_ROOT / "scripts").rglob("*.sh")) + sorted((REPO_ROOT / "_project" / "scripts").rglob("*.sh"))
    return {str(path.relative_to(REPO_ROOT)): path.read_text(encoding="utf-8") for path in paths}


def _python_sources() -> dict[str, str]:
    paths = sorted((REPO_ROOT / "scripts").rglob("*.py")) + sorted(
        path for path in (REPO_ROOT / "tests" / "uat").rglob("*.py") if not path.name.startswith("test_")
    )
    return {str(path.relative_to(REPO_ROOT)): path.read_text(encoding="utf-8") for path in paths}


def find_violations(shell: dict[str, str], python: dict[str, str]) -> list[str]:
    violations: list[str] = []
    for name, text in shell.items():
        for line in _logical_lines(text):
            if _is_prose(line):
                continue
            if MACHINE_RUN.search(line) and name != WRAPPER:
                violations.append(f"{name}: container machine run outside {WRAPPER}: {line.strip()}")
            if "linux_machine_run.sh" in line and name != WRAPPER:
                guarded = "host_lock" in line or "$(HOST_LOCK_CLI)" in line
                if not (guarded and " run " in line and "--guard" in line):
                    violations.append(f"{name}: {WRAPPER} not under host_lock run --guard: {line.strip()}")
            if COMPOSE_UP.search(line) and "$(COMPOSE_ADMIT)" not in line:
                violations.append(f"{name}: compose up without $(COMPOSE_ADMIT): {line.strip()}")
    for name, text in python.items():
        if name != PYTHON_UP_OWNER and PYTHON_UP_ARGV.search(text):
            violations.append(f"{name}: compose up argv built outside compose_up_command")
    return violations


def test_the_current_tree_has_no_unguarded_vm_runs_or_compose_bring_ups() -> None:
    assert find_violations(_shell_sources(), _python_sources()) == []


def test_the_wrapper_is_the_only_machine_run_call_and_is_referenced() -> None:
    shell = _shell_sources()
    callers = [name for name, text in shell.items() if name != WRAPPER and "linux_machine_run.sh" in text]
    assert {"Makefile", "scripts/linux_preflight_pytest.sh"} <= set(callers)


def test_the_compose_admission_variable_runs_host_admission() -> None:
    text = (REPO_ROOT / "make" / "platform-tests.mk").read_text(encoding="utf-8")
    definition = next(line for line in text.splitlines() if line.startswith("COMPOSE_ADMIT"))
    assert "$(HOST_LOCK_CLI) admit --need-gib" in definition


@pytest.mark.parametrize(
    ("shell", "python", "expected"),
    [
        ({"Makefile": "x:\n\t@container machine run -n m -- true\n"}, {}, "container machine run outside"),
        ({"scripts/a.sh": "container machine run -n m -- true\n"}, {}, "container machine run outside"),
        ({"Makefile": "x:\n\tbash scripts/linux_machine_run.sh m -- true\n"}, {}, "not under host_lock"),
        ({"make/a.mk": "x:\n\t$(COMPOSE) -p p -f f.yml up -d --wait\n"}, {}, "compose up without"),
        ({"scripts/a.sh": "docker compose -f f.yml up -d\n"}, {}, "compose up without"),
        ({}, {"scripts/x.py": 'argv = ["compose", "up", "-d"]\n'}, "outside compose_up_command"),
    ],
)
def test_the_choke_point_check_flags_a_direct_call(
    shell: dict[str, str], python: dict[str, str], expected: str
) -> None:
    violations = find_violations(shell, python)
    assert violations and expected in violations[0]


def test_the_choke_point_check_ignores_usage_lines_and_guarded_calls() -> None:
    shell = {
        "Makefile": (
            '\t@echo "  container machine run -n m"\n'
            "\t$(HOST_LOCK_CLI) run --guard -- bash scripts/linux_machine_run.sh m -- true\n"
            "\t$(COMPOSE_ADMIT) $(COMPOSE) -p p -f f.yml up -d --wait\n"
        )
    }
    assert find_violations(shell, {}) == []


STUB_CONTAINER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_LOG"
case "$1 $2" in
  "machine list") printf '[{"status":"%s","id":"m","default":true},{"status":"running","id":"other"}]\\n' "$STUB_STATUS" ;;
  "machine run") [ -n "${STUB_RUN_HOOK:-}" ] && bash -c "$STUB_RUN_HOOK"; exit "${STUB_RUN_EXIT:-0}" ;;
  "machine stop") exit "${STUB_STOP_EXIT:-0}" ;;
esac
"""


def _stub_env(tmp_path: Path, status: str, **extra: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "container"
    stub.write_text(STUB_CONTAINER, encoding="utf-8")
    stub.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        STUB_LOG=str(tmp_path / "calls.log"),
        STUB_STATUS=status,
        PYTHONPATH=str(REPO_ROOT),
        BENCHBOX_TEST_LOCK_DIR=str(tmp_path / "lock"),
        BENCHBOX_TEST_LOCK_WAIT_SECONDS="0",
        BENCHBOX_HEAVY_ADMIT="force",
        BENCHBOX_HEAVY_WATCHDOG="off",
        **extra,
    )
    for key in ("BENCHBOX_TEST_SESSION_OWNER", "BENCHBOX_SKIP_TEST_LOCK"):
        env.pop(key, None)
    return env


def _calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _wrapper(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/linux_machine_run.sh", "m", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
    )


def _locked_wrapper(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "benchbox.utils.host_lock",
            "run",
            "--guard",
            "--",
            "bash",
            str(WRAPPER_PATH),
            "m",
            *args,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
    )


def test_the_wrapper_refuses_to_boot_the_machine_outside_the_host_lock(tmp_path: Path) -> None:
    result = _wrapper(_stub_env(tmp_path, "stopped"), "--", "true")
    assert result.returncode == 2
    assert _calls(tmp_path) == []


def test_a_machine_this_run_started_is_stopped_afterwards_and_the_lock_is_held_meanwhile(tmp_path: Path) -> None:
    probe = tmp_path / "probe"
    check = "from benchbox.utils import host_lock as h; print(h.lock_is_held(h.lock_path()))"
    hook = f'{sys.executable} -c "{check}" > {probe}'
    result = _locked_wrapper(_stub_env(tmp_path, "stopped", STUB_RUN_HOOK=hook), "-e", "A=1", "--", "true")
    assert result.returncode == 0, result.stderr
    assert probe.read_text(encoding="utf-8").strip() == "True"
    calls = _calls(tmp_path)
    assert calls[-2:] == ["machine run -n m -e A=1 -- true", "machine stop m"]


def test_a_machine_that_was_already_running_is_left_running(tmp_path: Path) -> None:
    result = _locked_wrapper(_stub_env(tmp_path, "running"), "--", "true")
    assert result.returncode == 0, result.stderr
    assert not any(call.startswith("machine stop") for call in _calls(tmp_path))


def test_the_run_status_survives_the_stop(tmp_path: Path) -> None:
    result = _locked_wrapper(_stub_env(tmp_path, "stopped", STUB_RUN_EXIT="7", STUB_STOP_EXIT="1"), "--", "false")
    assert result.returncode == 7
    assert _calls(tmp_path)[-1] == "machine stop m"


def test_an_unknown_prior_state_is_never_stopped(tmp_path: Path) -> None:
    env = _stub_env(tmp_path, "unknown")
    result = _locked_wrapper(env, "--", "true")
    assert result.returncode == 0, result.stderr
    assert not any(call.startswith("machine stop") for call in _calls(tmp_path))


STUB_ENGINE = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_LOG"
exit 0
"""


def _make_up(tmp_path: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    engine = tmp_path / "engine"
    engine.write_text(STUB_ENGINE, encoding="utf-8")
    engine.chmod(0o755)
    env = dict(
        os.environ,
        STUB_LOG=str(tmp_path / "calls.log"),
        PYTHONPATH=str(REPO_ROOT),
        **extra,
    )
    env.pop("BENCHBOX_DATA_DIR", None)
    env.setdefault("BENCHBOX_HEAVY_ADMIT", "")
    return subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-s",
            "test-docker-up-postgresql",
            f"CONTAINER_ENGINE={engine}",
            f"DOCKER_TEST_STATE_DIR={tmp_path / 'state'}",
            f"HOST_LOCK_CLI={sys.executable} -m benchbox.utils.host_lock",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_compose_bring_up_refuses_before_invoking_the_engine_when_admission_fails(tmp_path: Path) -> None:
    result = _make_up(tmp_path, BENCHBOX_HEAVY_ADMIT_MIN_GIB="1000000")
    assert result.returncode != 0
    assert f"Error {EXIT_ADMISSION}" in result.stderr + result.stdout, result.stdout + result.stderr
    assert not any(" up " in call for call in _calls(tmp_path))


def test_the_compose_bring_up_invokes_the_engine_unchanged_when_admission_passes(tmp_path: Path) -> None:
    result = _make_up(tmp_path, BENCHBOX_HEAVY_ADMIT="force")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(tmp_path)
    assert len(calls) == 1
    assert calls[0].startswith("compose -p benchbox-postgresql-test-")
    assert calls[0].endswith("-f docker/postgresql/docker-compose.yml up -d --wait")


def _load_bring_up():
    import importlib.util

    path = REPO_ROOT / "scripts" / "uat-bring-up" / "uat_bring_up.py"
    spec = importlib.util.spec_from_file_location("uat_bring_up_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bring_up_harness(monkeypatch: pytest.MonkeyPatch, module, admission):
    from benchbox.utils import host_lock
    from tests.uat import matrix

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        from tests.uat import docker_assets

        return docker_assets.DockerCommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.docker_assets, "resolve_container_cli", lambda: "docker")
    monkeypatch.setattr(module.docker_assets, "run_docker_command", fake_run)
    monkeypatch.setattr(host_lock, "check_admission", admission)
    monkeypatch.setattr(matrix, "platform_is_reachable", lambda platform: True)
    return calls


def test_uat_bring_up_refuses_before_invoking_the_engine_when_admission_fails(monkeypatch, capsys) -> None:
    from benchbox.utils import host_lock

    module = _load_bring_up()
    needs: list[float | None] = []

    def refuse(*, need_gib=None, environ=None):
        needs.append(need_gib)
        raise host_lock.AdmissionRefused("free memory 0.1 GiB below floor")

    calls = _bring_up_harness(monkeypatch, module, refuse)
    assert module.main(["--platform", "postgresql"]) == EXIT_ADMISSION
    assert calls == []
    assert "free memory 0.1 GiB below floor" in capsys.readouterr().err
    assert needs and (needs[0] is None or needs[0] > 0)


def test_uat_bring_up_invokes_the_engine_unchanged_when_admission_passes(monkeypatch) -> None:
    module = _load_bring_up()
    calls = _bring_up_harness(monkeypatch, module, lambda *, need_gib=None, environ=None: "admitted")
    assert module.main(["--platform", "postgresql"]) == 0
    assert len(calls) == 1 and "up" in calls[0]


def test_uat_bring_up_dry_run_skips_admission(monkeypatch) -> None:
    module = _load_bring_up()

    def refuse(*, need_gib=None, environ=None):
        raise AssertionError("dry run must not run admission")

    _bring_up_harness(monkeypatch, module, refuse)
    assert module.main(["--platform", "postgresql", "--dry-run"]) == 0
