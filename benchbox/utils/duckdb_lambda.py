from __future__ import annotations

from typing import Any

PYTHON_LAMBDA_FROM_VERSION = (1, 3)


def duckdb_python_lambda_supported(version: str | None = None) -> bool:
    if version is None:
        import duckdb

        version = getattr(duckdb, "__version__", None)
    parts = (version or "").lstrip("v").split(".")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return False
    return (int(parts[0]), int(parts[1])) >= PYTHON_LAMBDA_FROM_VERSION


def _uses_parameters(lambda_node: Any) -> bool:
    from benchbox._vendor.sqlglot import exp

    names = {parameter.name.lower() for parameter in lambda_node.expressions}
    return any(column.name.lower() in names for column in lambda_node.this.find_all(exp.Column, exp.Identifier))


def rewrite_arrow_lambdas(sql: str) -> str:
    if "->" not in sql:
        return sql

    from benchbox._vendor.sqlglot import exp, parse
    from benchbox._vendor.sqlglot.dialects.duckdb import DuckDB
    from benchbox._vendor.sqlglot.errors import SqlglotError
    from benchbox._vendor.sqlglot.tokens import TokenType

    try:
        tokens = DuckDB().tokenize(sql)
        statements = parse(sql, read="duckdb")
    except SqlglotError:
        return sql

    first_parameter_offsets = {
        lambda_node.expressions[0].meta["start"]
        for statement in statements
        if statement is not None
        for lambda_node in statement.find_all(exp.Lambda)
        if lambda_node.expressions and "start" in lambda_node.expressions[0].meta and _uses_parameters(lambda_node)
    }
    if not first_parameter_offsets:
        return sql

    replacements: list[tuple[int, int, str]] = []
    for index, token in enumerate(tokens):
        if token.token_type != TokenType.ARROW or index == 0:
            continue
        previous = tokens[index - 1]
        if previous.token_type == TokenType.R_PAREN:
            opener = index - 2
            depth = 1
            while opener >= 0:
                kind = tokens[opener].token_type
                depth += (kind == TokenType.R_PAREN) - (kind == TokenType.L_PAREN)
                if depth == 0:
                    break
                opener -= 1
            if opener < 0 or opener + 1 >= index - 1:
                continue
            start = tokens[opener].start
            first = tokens[opener + 1].start
            parameters = sql[tokens[opener].end + 1 : previous.start].strip()
        else:
            start = first = previous.start
            parameters = sql[previous.start : previous.end + 1]
        if first not in first_parameter_offsets:
            continue
        replacements.append((start, token.end + 1, f"lambda {parameters}:"))

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
