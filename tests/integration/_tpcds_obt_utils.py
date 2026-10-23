from __future__ import annotations

import datetime
import decimal
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_PARTS_AS_VARIANTS = {14, 23, 24}
SEED_CANDIDATES: tuple[int | None, ...] = (None, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
ABS_TOLERANCE = 0.011
REL_TOLERANCE = 1e-6
ROUNDING_DIGITS = 2

Row = tuple[Any, ...]


@dataclass(frozen=True)
class Comparison:
    equal: bool
    obt_parts: int
    base_parts: int
    obt_rows: tuple[int, ...]
    base_rows: tuple[int, ...]
    detail: str


def normalise(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (decimal.Decimal, float, int)):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, str):
        return value.rstrip()
    return value


def _sort_key(row: Row) -> tuple:
    return tuple(
        (1, "", 0.0)
        if value is None
        else (0, "", round(value, ROUNDING_DIGITS))
        if isinstance(value, float)
        else (0, str(value), 0.0)
        for value in row
    )


def _values_match(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right):
        return True
    if isinstance(left, float) and isinstance(right, float):
        return math.isclose(left, right, rel_tol=REL_TOLERANCE, abs_tol=ABS_TOLERANCE)
    return left == right


def rows_match(left: list[Row], right: list[Row]) -> tuple[bool, int]:
    if len(left) != len(right):
        return False, abs(len(left) - len(right))
    mismatched = 0
    for left_row, right_row in zip(sorted(left, key=_sort_key), sorted(right, key=_sort_key), strict=True):
        if len(left_row) != len(right_row) or not all(_values_match(a, b) for a, b in zip(left_row, right_row)):
            mismatched += 1
    return mismatched == 0, mismatched


class QueryTimeout(Exception):
    pass


def run_statements(connection, sql: str, timeout: float | None = None) -> list[list[Row]]:
    import duckdb

    parts: list[list[Row]] = []
    for statement in (part.strip() for part in sql.split(";")):
        if not statement:
            continue
        timer = threading.Timer(timeout, connection.interrupt) if timeout else None
        if timer:
            timer.start()
        try:
            cursor = connection.execute(statement)
            parts.append([tuple(normalise(value) for value in row) for row in cursor.fetchall()])
        except duckdb.InterruptException as error:
            raise QueryTimeout(f"no result within {timeout:g}s") from error
        finally:
            if timer:
                timer.cancel()
    return parts


def base_sql(benchmark, query_id: int, scale_factor: float, seed: int | None) -> str:
    variants = ("a", "b") if query_id in BASE_PARTS_AS_VARIANTS else (None,)
    return ";\n".join(
        benchmark.get_query(query_id, scale_factor=scale_factor, variant=variant, dialect="duckdb", seed=seed)
        for variant in variants
    )


def compare_parts(obt_parts: list[list[Row]], base_parts: list[list[Row]]) -> Comparison:
    problems = []
    if len(obt_parts) != len(base_parts):
        problems.append(f"{len(obt_parts)} OBT part(s) vs {len(base_parts)} base part(s)")
    for index, (obt_rows, base_rows) in enumerate(zip(obt_parts, base_parts), start=1):
        matched, mismatched = rows_match(obt_rows, base_rows)
        if not matched:
            problems.append(f"part {index}: {len(obt_rows)} vs {len(base_rows)} rows, {mismatched} differing")
    return Comparison(
        equal=not problems,
        obt_parts=len(obt_parts),
        base_parts=len(base_parts),
        obt_rows=tuple(len(rows) for rows in obt_parts),
        base_rows=tuple(len(rows) for rows in base_parts),
        detail="; ".join(problems),
    )


def choose_seed(base, benchmark, query_id: int, scale_factor: float) -> tuple[int | None, list[list[Row]]]:
    first: list[list[Row]] | None = None
    for seed in SEED_CANDIDATES:
        parts = run_statements(base, base_sql(benchmark, query_id, scale_factor, seed))
        if first is None:
            first = parts
        if parts and all(parts):
            return seed, parts
    assert first is not None
    return None, first


def build_engines(root: Path, scale_factor: float):
    import duckdb
    import pytest

    from benchbox.core.tpcds.generator.manager import TPCDSDataGenerator
    from benchbox.core.tpcds_obt.etl.transformer import TPCDSOBTTransformer
    from benchbox.core.tpcds_obt.queries import TPCDSOBTQueryManager
    from benchbox.platforms.duckdb import DuckDBAdapter
    from benchbox.tpcds import TPCDS

    generator = TPCDSDataGenerator(
        scale_factor=scale_factor, output_dir=root / "tpcds", compress_data=False, quiet=True
    )
    if not generator.dsdgen_available:
        pytest.skip("dsdgen is not available")
    generator.generate()

    outputs = TPCDSOBTTransformer().transform(root / "tpcds", root / "obt", scale_factor=scale_factor)
    obt = duckdb.connect()
    obt.execute(
        f"CREATE VIEW tpcds_sales_returns_obt AS SELECT * FROM read_parquet('{Path(outputs['table']).as_posix()}')"
    )

    benchmark = TPCDS(scale_factor=scale_factor, output_dir=root / "tpcds")
    adapter = DuckDBAdapter(database_path=":memory:")
    base = adapter.create_connection()
    adapter.create_schema(benchmark, base)
    adapter.load_data(benchmark, base, root / "tpcds")
    return obt, base, benchmark, TPCDSOBTQueryManager(), adapter
