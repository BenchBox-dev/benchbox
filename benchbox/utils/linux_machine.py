from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Any

from benchbox.utils import heavy_run_events, resource_limits
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.vm_footprint import (
    DEFAULT_MACHINE as DEFAULT_MACHINE,
    MACHINE_ENV as MACHINE_ENV,
    STOP_KILL_STEP_SECONDS,
    argv_runs_wrapper as argv_runs_wrapper,
    basename,
    machine_name_from_env,
    stop_timeout_seconds,
)

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
STOP_OUTPUT_BYTES = 1024
STOP_POLL_SECONDS = 0.2
SIG_TERM = signal.Signals["SIGTERM"]
SIG_KILL = getattr(signal, "SIGKILL", SIG_TERM)


@dataclass(frozen=True)
class MachineState:
    status: str | None
    memory_gib: float | None
    is_default: bool


def machine_name(environ: Mapping[str, str] | None = None) -> str:
    return machine_name_from_env(os.environ if environ is None else environ)


def _subcommand(argv: Sequence[str]) -> tuple[str, Sequence[str]] | None:
    if not argv or basename(argv[0]) != "container":
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


def argv_matches(argv: Sequence[str], machine: str, machine_is_default: bool) -> bool:
    return argv_targets_machine(argv, machine, machine_is_default) or argv_runs_wrapper(argv, machine)


def read_machine_processes(
    machine: str,
    machine_is_default: bool,
    *,
    iterate: Callable[[], Iterable[Any]] | None = None,
) -> list[tuple[int, tuple[str, ...]]]:
    import psutil

    uid = os.getuid() if hasattr(os, "getuid") else None
    found: list[tuple[int, tuple[str, ...]]] = []
    for proc in (psutil.process_iter if iterate is None else iterate)():
        try:
            info = proc.as_dict(["pid", "name", "uids", "cmdline"], ad_value=None)
        except (psutil.Error, OSError, SystemError):
            continue
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


def lock_lost(environ: Mapping[str, str] | None = None) -> bool:
    from benchbox.utils import host_lock

    env = os.environ if environ is None else environ
    path = host_lock.lock_path(env)
    return not host_lock.lock_is_held(path) or host_lock.holder_owner(path) != env.get(host_lock.OWNER_ENV)


GUEST_POWEROFF_WAIT_ENV = "BENCHBOX_LINUX_POWEROFF_WAIT_SECONDS"
GUEST_POWEROFF_WAIT_SECONDS = 15
POWEROFF_REQUEST_TIMEOUT_SECONDS = 8.0
GUEST_POWEROFF_COMMAND = ("/bin/kill", "-s", "SIGRTMIN+4", "1")


def guest_poweroff_wait_seconds(environ: Mapping[str, str] | None = None) -> int:
    raw = (os.environ if environ is None else environ).get(GUEST_POWEROFF_WAIT_ENV, "")
    return int(raw) if raw.strip().isdigit() else GUEST_POWEROFF_WAIT_SECONDS


def _say(message: str) -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def _request_guest_poweroff(machine: str, timeout: float) -> None:
    argv = ["container", "machine", "run", "-n", machine, "--uid", "0", "--", *GUEST_POWEROFF_COMMAND]
    try:
        subprocess.run(argv, stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        _say(f"guest poweroff request timed out after {timeout:g}s; falling back to 'container machine stop'")
    except OSError as exc:
        _say(f"guest poweroff request failed ({exc}); falling back to 'container machine stop'")


def _machine_stop(machine: str) -> int:
    try:
        return subprocess.call(["container", "machine", "stop", machine], stdin=subprocess.DEVNULL)
    except OSError as exc:
        _say(f"could not run 'container machine stop {machine}': {exc}")
        return 127


def stop_sequence(
    machine: str,
    wait_seconds: float,
    *,
    state_reader: Callable[[str], MachineState | None] = read_machine_state,
    request_poweroff: Callable[[str, float], None] = _request_guest_poweroff,
    machine_stop: Callable[[str], int] = _machine_stop,
    clock: Callable[[], float] = mono_time,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    state = state_reader(machine)
    if state is None:
        _say(f"state of '{machine}' unreadable; skipping guest poweroff")
    elif state.status == "stopped":
        return 0
    elif state.status == "running":
        request_poweroff(machine, POWEROFF_REQUEST_TIMEOUT_SECONDS)
        started = clock()
        while True:
            state = state_reader(machine)
            if state is not None and state.status == "stopped":
                return 0
            if elapsed_seconds(started, clock()) >= wait_seconds:
                break
            sleeper(1.0)
        _say(f"guest did not power off within {wait_seconds:g}s; using 'container machine stop'")
    return machine_stop(machine)


def stop_command(machine: str, environ: Mapping[str, str] | None = None) -> list[str]:
    wait = str(guest_poweroff_wait_seconds(environ))
    return [sys.executable, "-m", "benchbox.utils.linux_machine", "stop-sequence", machine, "--wait", wait]


class MachineStop:
    def __init__(self, machine: str) -> None:
        self._sink: IO[bytes] | None = tempfile.TemporaryFile()
        try:
            self._proc = subprocess.Popen(
                stop_command(machine),
                stdin=subprocess.DEVNULL,
                stdout=self._sink,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError:
            self.close()
            raise

    def poll(self) -> int | None:
        return self._proc.poll()

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(self._proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def kill(self) -> None:
        for sig in (SIG_TERM, SIG_KILL):
            self._signal_group(sig)
            try:
                self._proc.wait(timeout=STOP_KILL_STEP_SECONDS)
            except subprocess.TimeoutExpired:
                continue

    def close(self) -> None:
        sink, self._sink = self._sink, None
        if sink is not None:
            sink.close()

    def output(self) -> str:
        sink = self._sink
        if sink is None:
            return ""
        try:
            size = sink.seek(0, os.SEEK_END)
            sink.seek(max(0, size - STOP_OUTPUT_BYTES))
            text = sink.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            text = ""
        finally:
            self.close()
        return " | ".join(line.strip() for line in text.splitlines() if line.strip())[-STOP_OUTPUT_BYTES:]


def classify_stop(state: MachineState | None, kind: str | None) -> str:
    status = None if state is None else state.status
    if status == "running":
        return "still_running"
    if status != "stopped":
        return "unverified"
    return {None: "stopped", "timeout": "stopped_after_timeout"}.get(kind, "stopped_after_error")


@dataclass(frozen=True)
class CleanupDeps:
    state_reader: Callable[[str], MachineState | None] = read_machine_state
    process_reader: Callable[[str, bool], list[tuple[int, tuple[str, ...]]]] = read_machine_processes
    lock_checker: Callable[[], bool] = lock_lost
    stopper: Callable[[str], Any] = MachineStop
    recorder: Callable[..., None] = heavy_run_events.record_event
    clock: Callable[[], float] = mono_time
    sleeper: Callable[[float], None] = time.sleep


def _stop_and_reconcile(
    machine: str, stop_timeout: float, deps: CleanupDeps, emit: Callable[[str], None]
) -> tuple[str, str | None]:
    try:
        handle = deps.stopper(machine)
    except OSError as exc:
        emit(f"ERROR: could not run 'container machine stop {machine}': {exc}")
        return "spawn_failed", None
    started = deps.clock()
    try:
        while handle.poll() is None and elapsed_seconds(started, deps.clock()) < stop_timeout:
            deps.sleeper(STOP_POLL_SECONDS)
        code = handle.poll()
        kind = "timeout" if code is None else (None if code == 0 else "error")
        failure = f"timed out after {stop_timeout:g}s" if code is None else (None if code == 0 else f"exit {code}")
        handle.kill()
        output = handle.output()
        try:
            state = deps.state_reader(machine)
        except Exception:
            state = None
    finally:
        handle.kill()
        handle.close()
    outcome = classify_stop(state, kind)
    status = "unreadable" if state is None else f"'{state.status}'"
    if output:
        emit(f"stop output: {output}")
    if outcome == "still_running":
        emit(
            f"ERROR: linux machine '{machine}' is still running after 'container machine stop' "
            f"({failure or 'exit 0'}); the container CLI has no force-stop, so it is left for the sentinel; "
            f"diagnose with: container machine logs -n 50 {machine}"
        )
    elif outcome == "unverified":
        emit(
            f"WARNING: could not confirm linux machine '{machine}' stopped (state {status}; "
            f"stop {failure or 'exit 0'}); the sentinel re-checks it"
        )
    elif failure is not None:
        emit(f"WARNING: stop of linux machine '{machine}' {failure} but the machine is now 'stopped'")
    return outcome, failure


def cleanup_machine(
    machine: str,
    own_pid: int,
    *,
    stop_timeout: float,
    emit: Callable[[str], None],
    deps: CleanupDeps | None = None,
) -> str:
    deps = CleanupDeps() if deps is None else deps
    started = deps.clock()
    deps.recorder("vm_cleanup_started", details={"machine": machine, "source": "wrapper"})
    failure: str | None = None
    try:
        if deps.lock_checker():
            outcome = "lock_lost"
            emit(
                f"==> linux machine '{machine}' left running: this run no longer holds the host lock; "
                "the sentinel reclaims it when idle"
            )
        else:
            state = deps.state_reader(machine)
            other = foreign_machine_pid(
                machine,
                state is None or state.is_default,
                own_pid,
                reader=deps.process_reader,
            )
            if other is not None:
                outcome = "left_running"
                emit(f"==> linux machine '{machine}' left running: in use by pid {other}")
            else:
                emit(f"==> stopping linux machine '{machine}'")
                outcome, failure = _stop_and_reconcile(machine, stop_timeout, deps, emit)
    except BaseException:
        deps.recorder(
            "vm_cleanup_finished",
            duration_seconds=elapsed_seconds(started, deps.clock()),
            outcome="interrupted",
            details={"machine": machine, "source": "wrapper"},
        )
        raise
    deps.recorder(
        "vm_cleanup_finished",
        duration_seconds=elapsed_seconds(started, deps.clock()),
        outcome=outcome,
        details={"machine": machine, "source": "wrapper", "failure": failure},
    )
    return outcome


def cleanup_main(machine: str, own_pid: int, stop_timeout: float | None) -> int:
    timeout = stop_timeout_seconds(os.environ) if stop_timeout is None else stop_timeout

    def emit(message: str) -> None:
        sys.stderr.write(message + "\n")
        sys.stderr.flush()

    cleanup_machine(machine, own_pid, stop_timeout=timeout, emit=emit)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchbox.utils.linux_machine")
    sub = parser.add_subparsers(dest="command", required=True)
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("machine")
    cleanup.add_argument("--own-pid", type=int, required=True)
    cleanup.add_argument("--stop-timeout", type=float)
    sequence = sub.add_parser("stop-sequence")
    sequence.add_argument("machine")
    sequence.add_argument("--wait", type=float, default=GUEST_POWEROFF_WAIT_SECONDS)
    args = parser.parse_args(argv)
    if args.command == "stop-sequence":
        return stop_sequence(args.machine, args.wait)
    return cleanup_main(args.machine, args.own_pid, args.stop_timeout)


if __name__ == "__main__":
    sys.exit(main())
