from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

from benchbox.utils.arrow_rows import ArrowRowWriter


def format_cell(value: Any) -> Any:
    if type(value) is float and value == value and value not in (float("inf"), float("-inf")):
        return format(Decimal(repr(value)), "f")
    return value


class PipeRowWriter:
    def __init__(self, path: Path | str) -> None:
        self._handle = open(path, "wb")
        self._writer = ArrowRowWriter(self._handle, "|")

    def writerow(self, row: Iterable[Any]) -> None:
        self._writer.writerow([format_cell(value) for value in row])

    def writerows(self, rows: Iterable[Iterable[Any]]) -> None:
        for row in rows:
            self.writerow(row)

    def __enter__(self) -> PipeRowWriter:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if exc_type is None:
                self._writer.flush()
        finally:
            self._handle.close()
