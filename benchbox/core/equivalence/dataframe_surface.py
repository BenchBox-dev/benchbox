# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

DATAFRAME_BACKENDS = ("expression", "pandas")

_MIDNIGHT = time(0, 0, 0)


@dataclass(frozen=True)
class SurfaceDivergence:
    query_id: Any
    cell: str
    detail: str

    @property
    def key(self) -> str:
        return f"{self.query_id}_{self.cell}" if self.cell else f"{self.query_id}"


def build_dataframe_contexts_from_specs(
    connection: Any,
    table_specs: Iterable[tuple[str, Iterable[tuple[str, str]]]],
) -> dict[str, Any]:
    import pandas as pd
    import polars as pl

    from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    expression_ctx = PolarsDataFrameAdapter().create_context()
    pandas_ctx = PandasDataFrameAdapter().create_context()
    for table_name, columns in table_specs:
        projections = [
            f"CAST({column} AS DOUBLE) AS {column}" if str(sql_type).upper().startswith("DECIMAL") else column
            for column, sql_type in columns
        ]
        arrow = connection.execute(f"SELECT {', '.join(projections)} FROM {table_name.lower()}").to_arrow_table()
        expression_ctx.register_table(table_name, pl.from_arrow(arrow).lazy())
        pandas_ctx.register_table(table_name, arrow.to_pandas(types_mapper=pd.ArrowDtype))
    return {"expression": expression_ctx, "pandas": pandas_ctx}


def build_dataframe_contexts(connection: Any, tables: Iterable[Any]) -> dict[str, Any]:
    specs = ((table.name, [(column.name, column.data_type.value) for column in table.columns]) for table in tables)
    return build_dataframe_contexts_from_specs(connection, specs)


def materialize_rows(result: Any) -> list[tuple[Any, ...]]:
    native = result if hasattr(result, "collect") else getattr(result, "native", result)
    if hasattr(native, "collect"):
        native = native.collect()
    if hasattr(native, "rows"):
        raw_rows = native.rows()
    elif hasattr(native, "itertuples"):
        raw_rows = native.itertuples(index=False, name=None)
    else:
        import pyarrow as pa

        if not isinstance(native, pa.Table):
            raise TypeError(f"cannot materialize result of type {type(native).__name__}")
        raw_rows = zip(*(column.to_pylist() for column in native.columns))
    return [tuple(_normalize_value(value) for value in row) for row in raw_rows]


def fetch_reference_rows(connection: Any, reference_sql: str) -> list[tuple[Any, ...]]:
    rows = connection.execute(reference_sql).fetchall()
    return [tuple(_normalize_value(value) for value in row) for row in rows]


def find_surface_divergences(
    query_ids: Iterable[Any],
    *,
    reference_rows: Callable[[Any], list[tuple[Any, ...]]],
    candidate_cells: Callable[[Any], Iterable[tuple[str, Callable[[list[tuple[Any, ...]]], None]]]],
    validation_error: type[BaseException],
    reference_failure_cell: str = "reference",
) -> list[SurfaceDivergence]:
    divergences: list[SurfaceDivergence] = []
    for query_id in query_ids:
        try:
            reference = reference_rows(query_id)
        except Exception as exc:
            divergences.append(SurfaceDivergence(query_id, reference_failure_cell, f"reference query failed: {exc}"))
            continue
        for cell, check in candidate_cells(query_id):
            try:
                check(reference)
            except validation_error as exc:
                divergences.append(SurfaceDivergence(query_id, cell, str(exc)))
            except Exception as exc:
                divergences.append(SurfaceDivergence(query_id, cell, f"error: {exc}"))
    return divergences


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.date() if value.time() == _MIDNIGHT else value
    if isinstance(value, float):
        return value if math.isnan(value) else float(value)
    if isinstance(value, (str, bytes, int, date)):
        return value
    if type(value).__name__ in ("NAType", "NaTType"):
        return None
    item = getattr(value, "item", None)
    if callable(item):
        return _normalize_value(item())
    return value
