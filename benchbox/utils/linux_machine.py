from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from benchbox.utils import resource_limits

MACHINE_ENV = "BENCHBOX_HEAVY_LINUX_MACHINE"
DEFAULT_MACHINE = "benchbox-agent"
WRAPPER_NAME = "linux_machine_run.sh"
SHELL_NAMES = frozenset({"bash", "sh", "zsh", "dash"})
VALUE_OPTIONS = frozenset(
    {
        "-e",
        "--env",
        "--env-file",
        "--gid",
        "-u",
        "--user",
        "--uid",
        "-w",
        "--workdir",
        "--cwd",
        "--ulimit",
        "-a",
        "--arch",
        "--os",
        "--platform",
        "--scheme",
        "--progress",
        "--max-concurrent-downloads",
        "--cpus",
        "--memory",
        "--home-mount",
        "--kernel",
    }
)
NAME_OPTIONS = frozenset({"-n", "--name"})
PROCESS_TIMEOUT_SECONDS = resource_limits.EXTERNAL_TIMEOUT_SECONDS
EXIT_BUSY = 0
EXIT_IDLE = 1
EXIT_UNKNOWN = 2


@dataclass(frozen=True)
class MachineState:
    status: str | None
    memory_gib: float | None
    is_default: bool


def machine_name(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    return (env.get(MACHINE_ENV) or "").strip() or DEFAULT_MACHINE


def _basename(value: str) -> str:
    return os.path.basename(value.rstrip("/"))


def _subcommand(argv: Sequence[str]) -> tuple[str, Sequence[str]] | None:
    if not argv or _basename(argv[0]) != "container":
        return None
    rest = list(argv[1:])
    index = 0
    while index < len(rest) and rest[index].startswith("-"):
        index += 1
    if index + 1 >= len(rest) or rest[index] != "machine":
        return None
    return rest[index + 1], rest[index + 2 :]


def _named(tokens: Sequence[str], stop_at_positional: bool) -> tuple[bool, str | None]:
    name: str | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            break
        if token in NAME_OPTIONS:
            name = tokens[index + 1] if index + 1 < len(tokens) else None
            index += 2
            continue
        if token.startswith("--name="):
            name = token.split("=", 1)[1]
        elif token.startswith("-n") and not token.startswith("--") and len(token) > 2:
            name = token[2:]
        elif token in VALUE_OPTIONS:
            index += 2
            continue
        elif not token.startswith("-") and stop_at_positional:
            break
        index += 1
    return name is not None, name


def argv_targets_machine(argv: Sequence[str], machine: str, machine_is_default: bool) -> bool:
    parsed = _subcommand(argv)
    if parsed is None:
        return False
    command, tokens = parsed
    if command not in {"run", "create"}:
        return False
    named, name = _named(tokens, stop_at_positional=command == "run")
    if named:
        return name == machine
    return command == "run" and machine_is_default


def argv_runs_wrapper(argv: Sequence[str], machine: str) -> bool:
    if not argv or _basename(argv[0]) not in SHELL_NAMES:
        return False
    for index, token in enumerate(argv[1:-1], start=1):
        if _basename(token) == WRAPPER_NAME:
            return argv[index + 1] == machine
    return False


def argv_matches(argv: Sequence[str], machine: str, machine_is_default: bool) -> bool:
    return argv_targets_machine(argv, machine, machine_is_default) or argv_runs_wrapper(argv, machine)


def read_machine_processes(machine: str, machine_is_default: bool) -> list[tuple[int, tuple[str, ...]]]:
    import psutil

    uid = os.getuid() if hasattr(os, "getuid") else None
    found: list[tuple[int, tuple[str, ...]]] = []
    for proc in psutil.process_iter(["pid", "name", "uids", "cmdline"]):
        info = proc.info
        uids = info.get("uids")
        if uid is not None and uids is not None and uids.real != uid:
            continue
        argv = tuple(info.get("cmdline") or ())
        if info.get("pid") is None or not argv:
            continue
        if argv_matches(argv, machine, machine_is_default):
            found.append((info["pid"], argv))
    return found


def ancestor_pids(pid: int) -> frozenset[int]:
    import psutil

    try:
        return frozenset(parent.pid for parent in psutil.Process(pid).parents())
    except (psutil.Error, OSError):
        return frozenset()


def foreign_machine_pid(
    machine: str,
    machine_is_default: bool,
    own_pid: int,
    *,
    reader: Callable[[str, bool], list[tuple[int, tuple[str, ...]]]] = read_machine_processes,
    ancestors: Callable[[int], frozenset[int]] = ancestor_pids,
) -> int | None:
    for pid, _argv in sorted(reader(machine, machine_is_default)):
        if pid == own_pid or pid == os.getpid() or own_pid in ancestors(pid):
            continue
        return pid
    return None


def parse_machine_state(text: str, machine: str) -> MachineState | None:
    try:
        items = json.loads(text)
    except ValueError:
        return None
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict) or item.get("id") != machine:
            continue
        status = item.get("status")
        return MachineState(
            status=status if isinstance(status, str) else None,
            memory_gib=resource_limits._bytes_to_gib(item.get("memory")),
            is_default=item.get("default") is True,
        )
    return None


def read_machine_state(
    machine: str,
    runner: resource_limits.Runner = resource_limits.run_text,
    which: Callable[[str], str | None] = shutil.which,
) -> MachineState | None:
    if which("container") is None:
        return None
    text = runner(["container", "machine", "list", "--format", "json"], PROCESS_TIMEOUT_SECONDS)
    return None if text is None else parse_machine_state(text, machine)


@dataclass(frozen=True)
class VmAssessment:
    machine: str
    state: MachineState
    protected: bool
    active: bool
    unguarded: tuple[int, ...]

    @property
    def running(self) -> bool:
        return self.state.status == "running"

    @property
    def idle(self) -> bool:
        return self.running and not self.protected and not self.active


def assess(
    machine: str,
    state: MachineState,
    processes: Sequence[tuple[int, tuple[str, ...]]],
    held: frozenset[int],
) -> VmAssessment:
    unguarded = tuple(
        sorted(
            pid for pid, argv in processes if pid not in held and argv_targets_machine(argv, machine, state.is_default)
        )
    )
    return VmAssessment(
        machine=machine,
        state=state,
        protected=any(pid in held for pid, _argv in processes),
        active=bool(processes),
        unguarded=unguarded,
    )


def busy_main(machine: str, own_pid: int) -> int:
    try:
        state = read_machine_state(machine)
        pid = foreign_machine_pid(machine, state is not None and state.is_default, own_pid)
    except Exception:
        return EXIT_UNKNOWN
    if pid is None:
        return EXIT_IDLE
    print(pid)
    return EXIT_BUSY


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchbox.utils.linux_machine")
    sub = parser.add_subparsers(dest="command", required=True)
    busy = sub.add_parser("busy")
    busy.add_argument("machine")
    busy.add_argument("--own-pid", type=int, required=True)
    args = parser.parse_args(argv)
    return busy_main(args.machine, args.own_pid)


if __name__ == "__main__":
    sys.exit(main())
