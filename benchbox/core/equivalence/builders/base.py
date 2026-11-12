from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CrossSurfaceData:
    connection: Any
    query_ids: Sequence[Any]
    reference_sql: Callable[[Any], str]
    dataframe_query: Callable[[Any], Any]
    benchmark: Any
    data_dir: Path
    query_parameters: dict[str, Any] = field(default_factory=dict)


def _assemble_simple_duckdb_cell(
    benchmark: Any,
    output_dir: Path,
    table_names: Sequence[str],
    *,
    label: str,
    dataframe_query: Callable[[Any], Any],
) -> CrossSurfaceData:
    output_dir = Path(output_dir)
    benchmark.generate_data()

    connection = _load_duckdb_cell(benchmark, output_dir, table_names, label=label)
    sql_queries = benchmark.get_queries()
    return CrossSurfaceData(
        connection=connection,
        query_ids=list(sql_queries.keys()),
        reference_sql=lambda query_id: sql_queries[query_id],
        dataframe_query=dataframe_query,
        benchmark=benchmark,
        data_dir=output_dir,
    )


def _load_duckdb_cell(benchmark: Any, output_dir: Path, table_names: Sequence[str], *, label: str) -> Any:
    import duckdb

    from benchbox.platforms.duckdb import DuckDBAdapter
    from benchbox.utils.duckdb_extensions import apply_single_arrow_lambda

    connection = duckdb.connect(":memory:")
    apply_single_arrow_lambda(connection, duckdb.__version__)
    try:
        for statement in benchmark.get_create_tables_sql(dialect="duckdb").strip().split(";"):
            if statement.strip():
                connection.execute(statement.strip())
        table_stats, _, _ = DuckDBAdapter(database=":memory:").load_data(benchmark, connection, Path(output_dir))

        stats_lower = {str(name).lower(): rows for name, rows in table_stats.items()}
        empty = [name for name in table_names if stats_lower.get(name.lower(), 0) <= 0]
        if empty:
            raise RuntimeError(f"{label} load failed - no rows in {empty} (stats={table_stats})")
    except Exception:
        connection.close()
        raise
    return connection
