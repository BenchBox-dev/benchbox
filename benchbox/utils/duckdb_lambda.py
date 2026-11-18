from __future__ import annotations

from typing import Any

from benchbox.utils.dialect_utils import duckdb_arrow_lambda_replacements

PYTHON_LAMBDA_FROM_VERSION = (1, 3)


def duckdb_python_lambda_supported(version: str | None = None) -> bool:
    if version is None:
        import duckdb

        version = getattr(duckdb, "__version__", None)
    parts = (version or "").lstrip("v").split(".")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return False
    return (int(parts[0]), int(parts[1])) >= PYTHON_LAMBDA_FROM_VERSION


def rewrite_arrow_lambdas(sql: str) -> str:
    if "->" not in sql:
        return sql
    replacements = duckdb_arrow_lambda_replacements(sql)
    for start, end, text in reversed(replacements):
        sql = sql[:start] + text + sql[end:]
    return sql


def duckdb_query_text(sql: str, version: str | None = None) -> str:
    if duckdb_python_lambda_supported(version):
        return rewrite_arrow_lambdas(sql)
    return sql


class PythonLambdaConnection:
    def __init__(self, connection: Any, version: str | None = None) -> None:
        self._connection = connection
        self._version = version

    def execute(self, query: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(query, str):
            query = duckdb_query_text(query, self._version)
        return self._connection.execute(query, *args, **kwargs)

    def sql(self, query: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(query, str):
            query = duckdb_query_text(query, self._version)
        return self._connection.sql(query, *args, **kwargs)

    def __enter__(self) -> PythonLambdaConnection:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._connection.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)
