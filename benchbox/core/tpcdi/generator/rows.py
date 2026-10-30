from __future__ import annotations

import csv
from collections.abc import Iterable
from decimal import Decimal
from typing import Any, TextIO


def format_cell(value: Any) -> Any:
    if type(value) is float and value == value and value not in (float("inf"), float("-inf")):
        return format(Decimal(repr(value)), "f")
    return value


class PipeRowWriter:
    def __init__(self, handle: TextIO) -> None:
        self._writer = csv.writer(handle, delimiter="|")

    def writerow(self, row: Iterable[Any]) -> None:
        self._writer.writerow([format_cell(value) for value in row])

    def writerows(self, rows: Iterable[Iterable[Any]]) -> None:
        for row in rows:
            self.writerow(row)
