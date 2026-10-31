from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def interprocess_lock(target_path: str | Path) -> Iterator[Path]:
    from benchbox.utils.host_lock import lock_exclusive, unlock

    target = Path(target_path)
    lock_path = target.parent / f"{target.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0), 0o644)
    try:
        lock_exclusive(fd)
        try:
            yield lock_path
        finally:
            unlock(fd)
    finally:
        os.close(fd)


archive_lock = interprocess_lock
