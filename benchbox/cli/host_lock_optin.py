from __future__ import annotations

import functools
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar, cast

import click

HOST_LOCK_ENV = "BENCHBOX_HOST_LOCK"
HOST_LOCK_CONFIG_KEY = "host_lock"
PHASE = "benchbox-cli"
WATCHDOG_MESSAGE = "watchdog stopped the process tree after a breach"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})

F = TypeVar("F", bound=Callable[..., Any])


def home_config_path() -> Path:
    return Path.home() / ".benchbox" / "config.yaml"


def _config_enabled(path: Path) -> bool:
    import yaml

    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, ValueError, yaml.YAMLError):
        return False
    return isinstance(data, dict) and str(data.get(HOST_LOCK_CONFIG_KEY)).strip().lower() in _TRUE


def host_lock_enabled(environ: Mapping[str, str] | None = None, config_path: Path | None = None) -> bool:
    env = os.environ if environ is None else environ
    override = (env.get(HOST_LOCK_ENV) or "").strip().lower()
    if override in _TRUE:
        return True
    if override in _FALSE:
        return False
    return _config_enabled(home_config_path() if config_path is None else config_path)


def _refuse(code: int, message: str) -> SystemExit:
    click.echo(f"[host-lock] {message}", err=True)
    return SystemExit(code)


@contextmanager
def heavy_guard(command: str) -> Iterator[None]:
    if not host_lock_enabled():
        yield
        return

    from benchbox.utils import host_lock

    try:
        handle = host_lock.acquire(host_lock.lock_path(), phase=PHASE, gate=command, command=f"benchbox {command}")
        if not handle.inherited and handle.count == 1:
            try:
                host_lock.check_admission()
                handle.guard = host_lock.start_guard(handle)
            except BaseException:
                handle.release()
                raise
    except host_lock.LockOwnershipError as exc:
        raise _refuse(host_lock.EXIT_OWNERSHIP, str(exc)) from exc
    except host_lock.LockUnavailable as exc:
        raise _refuse(host_lock.EXIT_REFUSED, f"BLOCKED: {exc}") from exc
    except host_lock.AdmissionRefused as exc:
        raise _refuse(
            host_lock.EXIT_ADMISSION, f"REFUSED: host is not ready for heavy work; lock released\n{exc.report}"
        ) from exc

    watcher = handle.guard
    previous_owner = os.environ.get(host_lock.OWNER_ENV)
    if not handle.inherited:
        os.environ[host_lock.OWNER_ENV] = host_lock.process_identity()
    try:
        try:
            yield
        except BaseException as exc:
            if watcher is not None and watcher.breached:
                raise _refuse(host_lock.EXIT_WATCHDOG, WATCHDOG_MESSAGE) from exc
            raise
        if watcher is not None and watcher.breached:
            raise _refuse(host_lock.EXIT_WATCHDOG, WATCHDOG_MESSAGE)
    finally:
        if previous_owner is None:
            os.environ.pop(host_lock.OWNER_ENV, None)
        else:
            os.environ[host_lock.OWNER_ENV] = previous_owner
        handle.release()


def host_guarded(command: str, *, skip: Callable[[Mapping[str, Any]], bool] | None = None) -> Callable[[F], F]:
    def decorate(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if skip is not None and skip(kwargs):
                return func(*args, **kwargs)
            with heavy_guard(command):
                return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorate
