from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from typing import Any, BinaryIO

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv

DEFAULT_BATCH_ROWS = 65536
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_NONE_TYPE = type(None)


def _column_to_arrow(values: Sequence[Any]) -> pa.Array:
    kinds = set(map(type, values))
    if kinds <= {str, _NONE_TYPE}:
        array = pa.array(values, type=pa.string())
    elif kinds <= {int, _NONE_TYPE} and all(_INT64_MIN <= v <= _INT64_MAX for v in values if v is not None):
        return pa.array(values, type=pa.int64())
    else:
        array = pa.array([None if v is None else str(v) for v in values], type=pa.string())
    empty = pc.equal(array, "")
    if pc.any(empty).as_py():
        array = pc.if_else(empty, pa.scalar(None, pa.string()), array)
    return array


class ArrowRowWriter:
    def __init__(
        self,
        handle: BinaryIO,
        delimiter: str = "|",
        batch_rows: int = DEFAULT_BATCH_ROWS,
    ) -> None:
        self._handle = handle
        self._delimiter = delimiter
        self._batch_rows = batch_rows
        self._rows: list[Sequence[Any]] = []
        self._options = pacsv.WriteOptions(include_header=False, delimiter=delimiter, quoting_style="none")

    def writerow(self, row: Sequence[Any]) -> None:
        self._rows.append(row)
        if len(self._rows) >= self._batch_rows:
            self.flush()

    def writerows(self, rows: Iterable[Sequence[Any]]) -> None:
        for row in rows:
            self.writerow(row)

    def flush(self) -> None:
        rows = self._rows
        if not rows:
            return
        self._rows = []
        self._handle.write(self._encode(rows))

    def _encode(self, rows: list[Sequence[Any]]) -> bytes:
        width = len(rows[0])
        if width > 1:
            try:
                table = pa.Table.from_arrays(
                    [_column_to_arrow(column) for column in zip(*rows, strict=True)],
                    names=[str(index) for index in range(width)],
                )
                sink = pa.BufferOutputStream()
                pacsv.write_csv(table, sink, self._options)
                return sink.getvalue().to_pybytes().replace(b"\n", b"\r\n")
            except (ValueError, TypeError, pa.ArrowException):
                pass
        buffer = io.StringIO(newline="")
        csv.writer(buffer, delimiter=self._delimiter).writerows(rows)
        return buffer.getvalue().encode("utf-8")

    def __enter__(self) -> ArrowRowWriter:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.flush()
