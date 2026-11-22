from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class StartedRemoteWork:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Any] = {}

    def add(self, key: str, handle: Any = None) -> None:
        with self._lock:
            self._items[key] = handle

    def discard(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._items)

    def cancel_all(self, platform: str, cancel: Callable[[str, Any], None]) -> None:
        with self._lock:
            started = list(self._items.items())
        failures: list[str] = []
        for key, handle in started:
            try:
                cancel(key, handle)
            except Exception as exc:
                failures.append(f"{key}: {type(exc).__name__}: {exc}")
            else:
                self.discard(key)
        if failures:
            raise RuntimeError(f"Could not cancel {platform} work started by this run: " + "; ".join(failures))
