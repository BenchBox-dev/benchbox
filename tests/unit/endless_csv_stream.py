from __future__ import annotations

import io
import time


class _EndlessRaw(io.RawIOBase):
    def __init__(self, header: bytes, row: bytes) -> None:
        self._pending = header
        self._row = row

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        time.sleep(0.002)
        data = self._pending + self._row * 2000
        self._pending = b""
        size = min(len(buffer), len(data))
        buffer[:size] = data[:size]
        return size


class EndlessStream(io.BufferedReader):
    def __init__(self, header: bytes, row: bytes) -> None:
        super().__init__(_EndlessRaw(header, row), 1 << 16)
        self.reads_after_close = 0

    def _count(self) -> None:
        if self.closed:
            self.reads_after_close += 1

    def read(self, size: int | None = -1) -> bytes:
        self._count()
        return super().read(size)

    def read1(self, size: int = -1) -> bytes:
        self._count()
        return super().read1(size)

    def readinto(self, buffer) -> int:
        self._count()
        return super().readinto(buffer)

    def readinto1(self, buffer) -> int:
        self._count()
        return super().readinto1(buffer)

    def peek(self, size: int = 0) -> bytes:
        self._count()
        return super().peek(size)
