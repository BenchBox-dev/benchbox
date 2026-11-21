from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

GENERATION_IN_PROGRESS_MARKER = ".benchbox-generation-in-progress"

_lock = threading.Lock()
_active: dict[Path, tuple[int, bool]] = {}


def _local_directory(data_dir: Any) -> Path | None:
    if not isinstance(data_dir, Path):
        return None
    return Path(os.path.abspath(data_dir))


def generation_in_progress(data_dir: Any) -> bool:
    directory = _local_directory(data_dir)
    if directory is None or not (directory / GENERATION_IN_PROGRESS_MARKER).exists():
        return False
    with _lock:
        active = _active.get(directory)
    return active is None or active[1]


@contextmanager
def track_generation(data_dir: Any) -> Iterator[None]:
    directory = _local_directory(data_dir)
    if directory is None:
        yield
        return
    with _lock:
        active = _active.get(directory)
        if active is not None:
            _active[directory] = (active[0] + 1, active[1])
            outermost = False
        else:
            marker = directory / GENERATION_IN_PROGRESS_MARKER
            _active[directory] = (1, marker.exists())
            outermost = True
    try:
        if outermost:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / GENERATION_IN_PROGRESS_MARKER).touch()
        yield
    except BaseException:
        _leave(directory)
        raise
    finished = _leave(directory)
    if finished:
        (directory / GENERATION_IN_PROGRESS_MARKER).unlink(missing_ok=True)


def _leave(directory: Path) -> bool:
    with _lock:
        depth, stale = _active[directory]
        if depth > 1:
            _active[directory] = (depth - 1, stale)
            return False
        del _active[directory]
        return True
