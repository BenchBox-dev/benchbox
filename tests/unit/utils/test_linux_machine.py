from __future__ import annotations

import json

import pytest

from benchbox.utils import linux_machine as lm

pytestmark = [pytest.mark.unit, pytest.mark.fast]

C = "/opt/homebrew/bin/container"


@pytest.mark.parametrize(
    ("argv", "default", "expected"),
    [
        ([C, "machine", "run", "-n", "m", "--", "true"], False, True),
        ([C, "machine", "run", "--name", "m", "-e", "A=1", "--", "true"], False, True),
        ([C, "machine", "run", "--name=m", "true"], False, True),
        ([C, "machine", "run", "-nm", "true"], False, True),
        ([C, "machine", "run", "-e", "A=m", "-n", "m", "--", "x"], False, True),
        ([C, "machine", "run", "--", "true"], True, True),
        ([C, "machine", "run"], True, True),
        ([C, "machine", "run", "--", "true"], False, False),
        ([C, "machine", "run", "-d", "--", "sleep", "9"], True, True),
        ([C, "machine", "run", "-d", "-n", "m", "sleep", "9"], False, True),
        ([C, "machine", "run", "-n", "other", "--", "true"], True, False),
        ([C, "machine", "run", "-n", "m2", "--", "true"], False, False),
        ([C, "machine", "run", "-n", "mm", "--", "true"], True, False),
        ([C, "machine", "run", "echo", "-n", "m"], False, False),
        ([C, "machine", "run", "--", "container", "machine", "run", "-n", "m"], False, False),
        ([C, "machine", "create", "img", "--name", "m", "--memory", "6G"], False, True),
        ([C, "machine", "create", "-n", "m", "img"], False, True),
        ([C, "machine", "create", "img"], True, False),
        ([C, "machine", "create", "img", "--name", "other"], True, False),
        ([C, "--debug", "machine", "run", "-n", "m"], False, True),
        ([C, "machine", "stop", "m"], True, False),
        ([C, "machine", "list"], True, False),
        ([C, "list"], True, False),
        (["python", "machine", "run", "-n", "m"], True, False),
        ([], True, False),
    ],
)
def test_targets_machine_matches_real_argv_shapes(argv: list[str], default: bool, expected: bool) -> None:
    assert lm.argv_targets_machine(argv, "m", default) is expected


def test_wrapper_argv_needs_a_shell_running_the_script_with_the_machine() -> None:
    assert lm.argv_runs_wrapper(["/bin/bash", "/r/scripts/linux_machine_run.sh", "m", "--", "x"], "m")
    assert not lm.argv_runs_wrapper(["/bin/bash", "/r/scripts/linux_machine_run.sh", "other", "--"], "m")
    assert not lm.argv_runs_wrapper(["/bin/sh", "-c", "bash scripts/linux_machine_run.sh m -- x"], "m")
    assert not lm.argv_runs_wrapper(["python", "/r/scripts/linux_machine_run.sh", "m"], "m")
    assert not lm.argv_runs_wrapper(["bash", "/r/scripts/linux_machine_run.sh"], "m")


def test_machine_name_reads_the_environment() -> None:
    assert lm.machine_name({}) == lm.DEFAULT_MACHINE
    assert lm.machine_name({lm.MACHINE_ENV: " "}) == lm.DEFAULT_MACHINE
    assert lm.machine_name({lm.MACHINE_ENV: "x"}) == "x"


LIST_JSON = json.dumps(
    [
        {"status": "running", "default": True, "memory": 8 * 2**30, "id": "m"},
        {"status": "stopped", "default": False, "memory": 2 * 2**30, "id": "other"},
    ]
)


def test_read_machine_state_parses_status_memory_and_default() -> None:
    seen: list[tuple[list[str], float]] = []

    def runner(argv, timeout):
        seen.append((list(argv), timeout))
        return LIST_JSON

    state = lm.read_machine_state("m", runner, lambda name: "/bin/container")
    assert state == lm.MachineState("running", 8.0, True)
    assert seen == [(["container", "machine", "list", "--format", "json"], 5.0)]
    other = lm.read_machine_state("other", runner, lambda name: "/bin/container")
    assert other == lm.MachineState("stopped", 2.0, False)


def test_read_machine_state_is_none_when_unavailable() -> None:
    assert lm.read_machine_state("m", lambda a, t: LIST_JSON, lambda n: None) is None
    assert lm.read_machine_state("m", lambda a, t: None, lambda n: "/c") is None
    assert lm.read_machine_state("m", lambda a, t: "not json", lambda n: "/c") is None
    assert lm.read_machine_state("absent", lambda a, t: LIST_JSON, lambda n: "/c") is None


def test_foreign_pid_skips_the_wrapper_and_its_descendants() -> None:
    table = [
        (10, ("/bin/bash", "/r/scripts/linux_machine_run.sh", "m", "--")),
        (11, (C, "machine", "run", "-n", "m")),
        (20, (C, "machine", "run", "-n", "m")),
    ]
    ancestors = {11: frozenset({10, 1}), 20: frozenset({5, 1})}
    found = lm.foreign_machine_pid("m", False, 10, reader=lambda m, d: table, ancestors=lambda p: ancestors[p])
    assert found == 20
    assert (
        lm.foreign_machine_pid("m", False, 10, reader=lambda m, d: table[:2], ancestors=lambda p: ancestors[p]) is None
    )
