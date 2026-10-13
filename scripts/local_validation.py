#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import os
import shlex
import sys
import time
from pathlib import Path

LOCK_POLL_SECONDS = 0.25
WAIT_PROGRESS_SECONDS = 5.0
LOCK_OFFSET = 1 << 30


def seek_lock_byte(fd: int) -> None:
    os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)


def read_holder(lock_path: Path) -> str:
    try:
        return lock_path.read_text(encoding="utf-8", errors="replace").strip() or "(empty lock file)"
    except OSError:
        return "(could not read lock file)"


def write_holder(fd: int, lock_path: Path, *, phase: str, gate: str | None = None) -> None:
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    command = " ".join(shlex.quote(part) for part in sys.argv[:8])
    details = f" phase:{phase}"
    if gate:
        details += f" gate:{gate}"
    payload = f"pid:{os.getpid()} started:{started}{details} cmd:{command}\n".encode()
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload)
    except OSError:
        pass


def _close_lock(fd: int) -> None:
    if sys.platform == "win32":
        try:
            try:
                seek_lock_byte(fd)
            except OSError:
                pass
            import msvcrt

            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        finally:
            os.close(fd)
    else:
        os.close(fd)


def wait_on_fd(fd: int, lock_path: Path, timeout_seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    wait_started = time.monotonic()
    last_report = 0.0
    reported_wait = False
    holder = read_holder(lock_path)
    if sys.platform == "win32":
        import msvcrt

        while True:
            try:
                try:
                    seek_lock_byte(fd)
                except OSError:
                    pass
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                if reported_wait:
                    elapsed = time.monotonic() - wait_started
                    print(
                        f"[local-validation] acquired lock after waiting {elapsed:.1f}s: {lock_path}",
                        file=sys.stderr,
                    )
                return
            except OSError as exc:
                if exc.errno != errno.EACCES:
                    raise
                holder = read_holder(lock_path)
                now = time.monotonic()
                if not reported_wait or now - last_report >= WAIT_PROGRESS_SECONDS:
                    print(
                        f"[local-validation] waiting for lock {lock_path} (holder: {holder})",
                        file=sys.stderr,
                        flush=True,
                    )
                    reported_wait = True
                    last_report = now
                if now >= deadline:
                    raise TimeoutError(f"timed out waiting for {lock_path} (holder: {holder})") from None
                time.sleep(min(LOCK_POLL_SECONDS, max(0.0, deadline - now)))
    import fcntl

    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if reported_wait:
                elapsed = time.monotonic() - wait_started
                print(f"[local-validation] acquired lock after waiting {elapsed:.1f}s: {lock_path}", file=sys.stderr)
            return
        except BlockingIOError:
            holder = read_holder(lock_path)
            now = time.monotonic()
            if not reported_wait or now - last_report >= WAIT_PROGRESS_SECONDS:
                print(
                    f"[local-validation] waiting for lock {lock_path} (holder: {holder})", file=sys.stderr, flush=True
                )
                reported_wait = True
                last_report = now
            if now >= deadline:
                raise TimeoutError(f"timed out waiting for {lock_path} (holder: {holder})") from None
            time.sleep(min(LOCK_POLL_SECONDS, max(0.0, deadline - now)))


def wait_for_lock(lock_path: Path, timeout_seconds: float) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0), 0o644)
    try:
        wait_on_fd(fd, lock_path, timeout_seconds)
    except KeyboardInterrupt:
        print(f"[local-validation] lock wait cancelled: {lock_path}", file=sys.stderr, flush=True)
        os.close(fd)
        raise
    except BaseException:
        os.close(fd)
        raise
    return fd


def clear_inactive_lock(lock_path: Path) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0), 0o644)
    try:
        try:
            if sys.platform == "win32":
                import msvcrt

                seek_lock_byte(fd)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            print(f"[local-validation] active lock retained: {lock_path}", file=sys.stderr)
            return 1
        os.ftruncate(fd, 0)
        print(f"[local-validation] cleared inactive lock: {lock_path}")
        return 0
    finally:
        _close_lock(fd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    clear = sub.add_parser("clear-test-lock", help="clear an inactive shared test lock")
    clear.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return clear_inactive_lock(args.path)


if __name__ == "__main__":
    raise SystemExit(main())
