from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "heavy_run_v1"
FILE_ENV = "BENCHBOX_HEAVY_RUNS_FILE"
RUN_ID_ENV = "BENCHBOX_HEAVY_RUN_ID"
LOCK_DIR_ENV = "BENCHBOX_TEST_LOCK_DIR"
DEFAULT_FILE_NAME = "heavy-runs.jsonl"
MAX_RECORD_BYTES = 3500
MAX_TEXT_CHARS = 200
MAX_DETAIL_ITEMS = 24


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def current_run_id(environ: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    return env.get(RUN_ID_ENV) or None


def _real_home() -> Path:
    if sys.platform == "win32":
        return Path.home()
    import pwd

    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return Path.home()


def events_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    explicit = env.get(FILE_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    lock_dir = env.get(LOCK_DIR_ENV, "").strip()
    if lock_dir:
        return Path(lock_dir).expanduser() / DEFAULT_FILE_NAME
    return _real_home() / ".benchbox" / DEFAULT_FILE_NAME


def _bounded(value: object) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return round(value, 6)
    return str(value)[:MAX_TEXT_CHARS]


def _encode(record: dict[str, object]) -> bytes:
    line = (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    if len(line) <= MAX_RECORD_BYTES:
        return line
    record["details"] = {"truncated": True}
    return (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def record_event(
    event: str,
    *,
    duration_seconds: float | None = None,
    outcome: str | None = None,
    exit_code: int | None = None,
    details: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
    run_id: str | None = None,
) -> None:
    try:
        env = os.environ if environ is None else environ
        record: dict[str, object] = {
            "schema": SCHEMA,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "run_id": run_id or env.get(RUN_ID_ENV) or "unknown",
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "event": event[:MAX_TEXT_CHARS],
        }
        if duration_seconds is not None:
            record["duration_seconds"] = round(max(0.0, duration_seconds), 6)
        if outcome is not None:
            record["outcome"] = outcome[:MAX_TEXT_CHARS]
        if exit_code is not None:
            record["exit_code"] = exit_code
        if details:
            record["details"] = {str(k)[:64]: _bounded(v) for k, v in list(details.items())[:MAX_DETAIL_ITEMS]}
        line = _encode(record)
        path = events_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            written = os.write(descriptor, line)
        finally:
            os.close(descriptor)
        if written != len(line):
            raise OSError(f"short write to {path}: {written} of {len(line)} bytes")
    except Exception as error:
        try:
            sys.stderr.write(f"[heavy-run-events] event log write failed: {type(error).__name__}: {error}\n")
        except Exception:
            pass
