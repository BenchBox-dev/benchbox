from __future__ import annotations

import argparse
import errno
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOCK_NAME = "test.lock"
LOCK_DIR_ENV = "BENCHBOX_TEST_LOCK_DIR"
WAIT_ENV = "BENCHBOX_TEST_LOCK_WAIT_SECONDS"
OWNER_ENV = "BENCHBOX_TEST_SESSION_OWNER"
DEFAULT_WAIT_SECONDS = 3600.0
LOCK_POLL_SECONDS = 0.25
WAIT_PROGRESS_SECONDS = 5.0
LOCK_OFFSET = 1 << 30
EXIT_REFUSED = 75
EXIT_OWNERSHIP = 78
LOG_PREFIX = "[host-lock]"

_HOLDER_OWNER = re.compile(r"^pid:\d+ owner:(\d+:\d+(?:\.\d+)?) ")


class LockUnavailable(TimeoutError):
    pass


class LockOwnershipError(LockUnavailable):
    pass


IS_WINDOWS = sys.platform == "win32"
_sleep = time.sleep


def _load_msvcrt() -> Any:
    import msvcrt

    return msvcrt


def _load_fcntl() -> Any:
    import fcntl

    return fcntl


def _now() -> float:
    return time.monotonic()


def _log(message: str) -> None:
    sys.stderr.write(f"{LOG_PREFIX} {message}\n")
    sys.stderr.flush()


def _psutil() -> Any:
    import psutil

    return psutil


def real_home() -> Path:
    if IS_WINDOWS:
        return Path.home()
    import pwd

    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return Path.home()


def default_lock_path() -> Path:
    return real_home() / ".benchbox" / LOCK_NAME


def lock_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    configured = env.get(LOCK_DIR_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return real_home() / ".benchbox"


def lock_path(environ: Mapping[str, str] | None = None) -> Path:
    return lock_dir(environ) / LOCK_NAME


def wait_seconds_from_env(environ: Mapping[str, str] | None = None) -> float:
    env = os.environ if environ is None else environ
    try:
        return max(0.0, float(env.get(WAIT_ENV) or DEFAULT_WAIT_SECONDS))
    except ValueError:
        return DEFAULT_WAIT_SECONDS


def seek_lock_byte(fd: int) -> None:
    os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)


def try_lock(fd: int) -> bool:
    if IS_WINDOWS:
        msvcrt = _load_msvcrt()

        try:
            seek_lock_byte(fd)
        except OSError:
            pass
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno != errno.EACCES:
                raise
            return False
        return True
    fcntl = _load_fcntl()

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def lock_exclusive(fd: int) -> None:
    if IS_WINDOWS:
        msvcrt = _load_msvcrt()

        try:
            seek_lock_byte(fd)
        except OSError:
            pass
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        return
    fcntl = _load_fcntl()

    fcntl.flock(fd, fcntl.LOCK_EX)


def unlock(fd: int) -> None:
    if IS_WINDOWS:
        msvcrt = _load_msvcrt()

        try:
            seek_lock_byte(fd)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    fcntl = _load_fcntl()

    fcntl.flock(fd, fcntl.LOCK_UN)


def _close_lock(fd: int) -> None:
    try:
        if IS_WINDOWS:
            unlock(fd)
    finally:
        os.close(fd)


def _open_lock_file(path: Path) -> int:
    return os.open(str(path), os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0), 0o644)


def _reopen(fd: int, path: Path) -> int:
    replacement = _open_lock_file(path)
    _close_lock(fd)
    return replacement


def _inode_matches(fd: int, path: Path) -> bool:
    try:
        path_inode = os.stat(path).st_ino
    except FileNotFoundError:
        return False
    return os.fstat(fd).st_ino == path_inode


def process_identity(process: Any = None) -> str:
    proc = _psutil().Process() if process is None else process
    return f"{proc.pid}:{proc.create_time():.2f}"


def _safe_identity() -> str | None:
    try:
        return process_identity()
    except Exception:
        return None


def read_holder(lock_path: Path) -> str:
    try:
        return lock_path.read_text(encoding="utf-8", errors="replace").strip() or "(empty lock file)"
    except OSError:
        return "(could not read lock file)"


def holder_owner(lock_path: Path) -> str | None:
    match = _HOLDER_OWNER.match(read_holder(lock_path))
    return match.group(1) if match else None


def write_holder(fd: int, lock_path: Path, *, phase: str, gate: str | None = None, command: str | None = None) -> None:
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    rendered = command if command is not None else " ".join(shlex.quote(part) for part in sys.argv[:8])
    owner = _safe_identity()
    owner_field = f" owner:{owner}" if owner else ""
    details = f" phase:{phase}"
    if gate:
        details += f" gate:{gate}"
    payload = f"pid:{os.getpid()}{owner_field} started:{started}{details} cmd:{rendered}\n".encode()
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload)
    except OSError:
        pass


def waiters_dir(lock_path: Path) -> Path:
    return lock_path.parent / f"{lock_path.name}.waiters"


def _register_waiter(lock_path: Path) -> Path | None:
    marker = waiters_dir(lock_path) / str(os.getpid())
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(_safe_identity() or "", encoding="utf-8")
    except OSError:
        return None
    return marker


def _unregister_waiter(marker: Path | None) -> None:
    if marker is None:
        return
    try:
        marker.unlink()
    except OSError:
        pass


def _waiter_is_live(pid: int, recorded_identity: str) -> bool:
    try:
        psutil = _psutil()
    except ImportError:
        return True
    try:
        if not recorded_identity:
            return bool(psutil.pid_exists(pid))
        return process_identity(psutil.Process(pid)) == recorded_identity
    except psutil.Error:
        return False


def live_waiter_count(lock_path: Path) -> int:
    try:
        entries = list(waiters_dir(lock_path).iterdir())
    except OSError:
        return 0
    count = 0
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            recorded = entry.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if _waiter_is_live(int(entry.name), recorded):
            count += 1
    return count


def wait_on_fd(fd: int, lock_path: Path, timeout_seconds: float) -> int:
    started = _now()
    deadline = started + max(0.0, timeout_seconds)
    last_report = 0.0
    reported_wait = False
    marker: Path | None = None
    try:
        while True:
            if not _inode_matches(fd, lock_path):
                fd = _reopen(fd, lock_path)
                continue
            if try_lock(fd):
                if _inode_matches(fd, lock_path):
                    if reported_wait:
                        _log(f"acquired lock after waiting {elapsed_since(started):.1f}s: {lock_path}")
                    return fd
                fd = _reopen(fd, lock_path)
                continue
            holder = read_holder(lock_path)
            if marker is None:
                marker = _register_waiter(lock_path)
            waiters = live_waiter_count(lock_path)
            now = _now()
            if not reported_wait or now - last_report >= WAIT_PROGRESS_SECONDS:
                _log(f"waiting for lock {lock_path} (holder: {holder}; live waiters: {waiters})")
                reported_wait = True
                last_report = now
            if now >= deadline:
                raise LockUnavailable(
                    f"timed out waiting for {lock_path} (holder: {holder}; live waiters: {waiters})"
                ) from None
            _sleep(min(LOCK_POLL_SECONDS, max(0.0, deadline - now)))
    except BaseException:
        _close_lock(fd)
        raise
    finally:
        _unregister_waiter(marker)


def elapsed_since(start: float) -> float:
    return _now() - start


def wait_for_lock(lock_path: Path, timeout_seconds: float) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = _open_lock_file(lock_path)
    try:
        return wait_on_fd(fd, lock_path, timeout_seconds)
    except KeyboardInterrupt:
        _log(f"lock wait cancelled: {lock_path}")
        raise


def lock_is_held(lock_path: Path) -> bool:
    try:
        fd = os.open(str(lock_path), os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return False
    try:
        if try_lock(fd):
            unlock(fd)
            return False
        return True
    finally:
        os.close(fd)


def clear_inactive_lock(lock_path: Path) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = _open_lock_file(lock_path)
    try:
        try:
            acquired = try_lock(fd)
        except OSError:
            acquired = False
        if not acquired:
            _log(f"active lock retained: {lock_path}")
            return 1
        os.ftruncate(fd, 0)
        sys.stdout.write(f"{LOG_PREFIX} cleared inactive lock: {lock_path}\n")
        return 0
    finally:
        _close_lock(fd)


@dataclass
class HostLock:
    path: Path
    fd: int | None
    pid: int
    inherited: bool = False
    count: int = 1

    def release(self) -> None:
        _release(self)


_registry: dict[str, HostLock] = {}
_registry_lock = threading.RLock()


def _registry_key(path: Path) -> str:
    return str(path.resolve())


def _release(handle: HostLock) -> None:
    if handle.pid != os.getpid():
        return
    with _registry_lock:
        if handle.count <= 0:
            return
        handle.count -= 1
        if handle.count > 0:
            return
        key = _registry_key(handle.path)
        if _registry.get(key) is handle:
            del _registry[key]
        fd, handle.fd = handle.fd, None
    if fd is not None:
        _close_lock(fd)


def held_inode_matches(handle: HostLock) -> bool:
    if handle.fd is None:
        return handle.inherited
    return _inode_matches(handle.fd, handle.path)


def owner_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env[OWNER_ENV] = process_identity()
    return env


def _ancestry_identities() -> set[str]:
    psutil = _psutil()
    current = psutil.Process()
    identities: set[str] = set()
    for process in (current, *current.parents()):
        try:
            identities.add(process_identity(process))
        except psutil.Error:
            continue
    return identities


def _inherits_lock(path: Path, environ: Mapping[str, str]) -> bool:
    try:
        psutil = _psutil()
    except ImportError:
        return False
    owner = holder_owner(path)
    if owner is None:
        return False
    try:
        ancestry = _ancestry_identities()
    except psutil.Error:
        return False
    if owner not in ancestry or not lock_is_held(path):
        return False
    declared = (environ.get(OWNER_ENV) or "").strip()
    if declared == owner:
        return True
    state = "is missing" if not declared else f"is {declared!r}"
    raise LockOwnershipError(
        f"{path} is held by {owner}, this process or one of its ancestors, but {OWNER_ENV} {state}; "
        f"the environment was scrubbed between the lock holder and this process. Preserve {OWNER_ENV} "
        "for descendants instead of waiting on an ancestor"
    )


def acquire(
    lock_path: Path | None = None,
    *,
    wait_seconds: float | None = None,
    phase: str,
    gate: str | None = None,
    command: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> HostLock:
    path = default_lock_path() if lock_path is None else lock_path
    path.parent.mkdir(parents=True, exist_ok=True)
    key = _registry_key(path)
    pid = os.getpid()
    with _registry_lock:
        existing = _registry.get(key)
        if existing is not None and existing.pid == pid and existing.count > 0:
            existing.count += 1
            return existing
    env = os.environ if environ is None else environ
    if _inherits_lock(path, env):
        handle = HostLock(path, None, pid, inherited=True)
    else:
        timeout = wait_seconds_from_env(env) if wait_seconds is None else wait_seconds
        fd = wait_for_lock(path, timeout)
        write_holder(fd, path, phase=phase, gate=gate, command=command)
        handle = HostLock(path, fd, pid)
    with _registry_lock:
        _registry[key] = handle
    return handle


@contextmanager
def hold(lock_path: Path | None = None, **kwargs: Any) -> Iterator[HostLock]:
    handle = acquire(lock_path, **kwargs)
    try:
        yield handle
    finally:
        handle.release()


_FORWARDED_SIGNALS = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name))


def _stdin_foreground_state() -> tuple[bool, bool]:
    try:
        if not os.isatty(0):
            return False, False
        return True, os.tcgetpgrp(0) == os.getpgrp()
    except OSError:
        return True, False


def _prepare_child(take_foreground: bool) -> None:
    os.setpgid(0, 0)
    if take_foreground:
        signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        os.tcsetpgrp(0, os.getpgrp())
        signal.signal(signal.SIGTTOU, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, _FORWARDED_SIGNALS)


def _exit_code(returncode: int) -> int:
    return 128 - returncode if returncode < 0 else returncode


def _supervise_windows(command: Sequence[str], env: Mapping[str, str]) -> int:
    child = subprocess.Popen(list(command), env=dict(env))
    try:
        return child.wait()
    except KeyboardInterrupt:
        child.terminate()
        child.wait()
        return 130


def _suspend_with_child(child_pid: int) -> None:
    try:
        os.tcsetpgrp(0, os.getpgrp())
    except OSError:
        pass
    previous = signal.signal(signal.SIGTSTP, signal.SIG_DFL)
    try:
        os.killpg(os.getpgrp(), signal.SIGTSTP)
    finally:
        signal.signal(signal.SIGTSTP, previous)
    if _stdin_foreground_state() == (True, True):
        try:
            os.tcsetpgrp(0, child_pid)
        except OSError:
            pass
    try:
        os.killpg(child_pid, signal.SIGCONT)
    except OSError:
        pass


def _wait_for_child(child: subprocess.Popen[bytes], foreground: bool) -> int:
    while True:
        _, status = os.waitpid(child.pid, os.WUNTRACED)
        if os.WIFSTOPPED(status):
            if foreground:
                _suspend_with_child(child.pid)
            continue
        code = os.waitstatus_to_exitcode(status)
        child.returncode = code
        return _exit_code(code)


def _supervise_posix(command: Sequence[str], env: Mapping[str, str]) -> int:
    stdin_is_tty, foreground = _stdin_foreground_state()
    stdin = subprocess.DEVNULL if stdin_is_tty and not foreground else None
    child: subprocess.Popen[bytes] | None = None

    def forward(signum: int, _frame: object) -> None:
        if child is None:
            raise SystemExit(128 + signum)
        try:
            os.killpg(child.pid, signum)
        except OSError:
            pass

    previous = {
        sig: signal.signal(sig, forward) for sig in _FORWARDED_SIGNALS if signal.getsignal(sig) != signal.SIG_IGN
    }
    previous_ttou = signal.signal(signal.SIGTTOU, signal.SIG_IGN) if foreground else None
    try:
        signal.pthread_sigmask(signal.SIG_BLOCK, _FORWARDED_SIGNALS)
        try:
            child = subprocess.Popen(
                list(command),
                env=dict(env),
                stdin=stdin,
                preexec_fn=lambda: _prepare_child(foreground),
            )
        finally:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, _FORWARDED_SIGNALS)
        return _wait_for_child(child, foreground)
    finally:
        if foreground:
            try:
                os.tcsetpgrp(0, os.getpgrp())
            except OSError:
                pass
            if previous_ttou is not None:
                signal.signal(signal.SIGTTOU, previous_ttou)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run_command(command: Sequence[str], *, phase: str = "run", gate: str | None = None) -> int:
    path = lock_path()
    rendered = " ".join(shlex.quote(part) for part in command)
    try:
        handle = acquire(path, phase=phase, gate=gate, command=rendered)
    except LockOwnershipError as exc:
        _log(str(exc))
        return EXIT_OWNERSHIP
    except LockUnavailable as exc:
        _log(f"BLOCKED: {exc}")
        return EXIT_REFUSED
    except KeyboardInterrupt:
        return 128 + signal.SIGINT
    env = dict(os.environ) if handle.inherited else owner_environment()
    try:
        if IS_WINDOWS:
            return _supervise_windows(command, env)
        return _supervise_posix(command, env)
    except FileNotFoundError as exc:
        _log(f"command not found: {exc.filename or command[0]}")
        return 127
    except PermissionError as exc:
        _log(f"command not executable: {exc.filename or command[0]}")
        return 126
    finally:
        handle.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m benchbox.utils.host_lock")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="hold the host lock for the lifetime of a command")
    run.add_argument("--phase", default="run")
    run.add_argument("--gate")
    run.add_argument("argv", nargs=argparse.REMAINDER)
    clear = sub.add_parser("clear", help="clear an inactive shared test lock")
    clear.add_argument("path", nargs="?", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "clear":
        return clear_inactive_lock(args.path if args.path is not None else lock_path())
    command = list(args.argv)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("run requires a command after --")
    return run_command(command, phase=args.phase, gate=args.gate)


if __name__ == "__main__":
    raise SystemExit(main())
