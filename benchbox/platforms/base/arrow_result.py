from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from benchbox.core.results.fetch_mode import FETCH_MODE_ARROW, FETCH_MODE_ROWS
from benchbox.utils.clock import elapsed_seconds, mono_time

Row = tuple[Any, ...]
RowSource = Callable[[], Sequence[Sequence[Any]]]
ArrowSource = Callable[[], Any]
CellConverter = Callable[[Any], Any]


def receive_arrow(source: Any) -> Any:
    import pyarrow as pa

    if isinstance(source, pa.Table):
        return source
    if isinstance(source, pa.RecordBatchReader):
        return source.read_all()
    if isinstance(source, pa.RecordBatch):
        return pa.Table.from_batches([source])
    batches = list(source)
    if not batches:
        raise ValueError("an Arrow result needs at least one record batch to carry its schema")
    return pa.Table.from_batches(batches)


_ACTIVE_CLOCK: ContextVar[ConversionClock | None] = ContextVar("arrow_conversion_clock", default=None)


class ConversionClock:
    def __init__(self) -> None:
        self.seconds = 0.0
        self._token: Token[ConversionClock | None] | None = None

    def start(self) -> ConversionClock:
        self._token = _ACTIVE_CLOCK.set(self)
        return self

    def stop(self) -> None:
        if self._token is not None:
            _ACTIVE_CLOCK.reset(self._token)
            self._token = None

    def finish(self) -> float:
        self.stop()
        return self.seconds


class ArrowRows(Sequence):
    def __init__(self, table: Any) -> None:
        self._table = table
        self._rows: list[Row] | None = None
        self._converters: dict[int, CellConverter] = {}

    @property
    def table(self) -> Any:
        return self._table

    def __len__(self) -> int:
        return self._table.num_rows

    def convert_columns(self, converters: Mapping[int, CellConverter]) -> None:
        self._converters = dict(converters)
        self._rows = None

    def _convert(self, index: int, value: Any) -> Any:
        convert = self._converters.get(index)
        return value if convert is None or value is None else convert(value)

    def _materialize(self) -> list[Row]:
        if self._rows is None:
            started = mono_time()
            columns = [
                [self._convert(index, value) for value in column.to_pylist()]
                if index in self._converters
                else column.to_pylist()
                for index, column in enumerate(self._table.columns)
            ]
            self._rows = list(zip(*columns)) if columns else [()] * self._table.num_rows
            clock = _ACTIVE_CLOCK.get()
            if clock is not None:
                clock.seconds += elapsed_seconds(started)
        return self._rows

    def __getitem__(self, index: Any) -> Any:
        return self._materialize()[index]

    def __iter__(self) -> Iterator[Row]:
        return iter(self._materialize())

    def first(self) -> Row | None:
        if self._table.num_rows == 0:
            return None
        if self._rows is not None:
            return self._rows[0]
        return tuple(self._convert(index, column[0].as_py()) for index, column in enumerate(self._table.columns))


@dataclass(frozen=True)
class FetchedResult:
    fetch_mode: str
    rows: Sequence[Sequence[Any]]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def first_row(self) -> Sequence[Any] | None:
        if isinstance(self.rows, ArrowRows):
            return self.rows.first()
        return self.rows[0] if len(self.rows) else None


def fetch_result(fetch_mode: str, *, fetch_rows: RowSource, fetch_arrow: ArrowSource | None = None) -> FetchedResult:
    if fetch_mode == FETCH_MODE_ARROW and fetch_arrow is not None:
        return FetchedResult(FETCH_MODE_ARROW, ArrowRows(receive_arrow(fetch_arrow())))
    return FetchedResult(FETCH_MODE_ROWS, fetch_rows())
