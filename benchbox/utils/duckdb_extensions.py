from __future__ import annotations

from typing import Any

THIRD_PARTY_EXTENSION_OPTION = "allow_third_party_extensions"
QUERY_FARM_HOST = "duckdb-extensions.query.farm"
THIRD_PARTY_ENABLE_HINT = f"set --platform-option {THIRD_PARTY_EXTENSION_OPTION}=true to allow BenchBox to contact that host and load the extension"
DATAFRAME_ENABLE_HINT = f"DataFrame platforms have no {THIRD_PARTY_EXTENSION_OPTION} option to enable it"
SINGLE_ARROW_LAMBDA_STATEMENT = "SET lambda_syntax = 'ENABLE_SINGLE_ARROW'"
SINGLE_ARROW_LAMBDA_SETTING = "lambda_syntax=ENABLE_SINGLE_ARROW"
SINGLE_ARROW_REJECTED_FROM_MAJOR = 2
RELATION_ARROW_FROM_MAJOR = 2

DATASKETCHES_COMMUNITY_STATEMENTS: tuple[str, ...] = (
    "INSTALL datasketches FROM community",
    "LOAD datasketches",
)

DATASKETCHES_QUERY_FARM_STATEMENTS: tuple[str, ...] = (
    "SET allow_extension_repositories = 'allowed'",
    f"CREATE EXTENSION REPOSITORY IF NOT EXISTS query_farm WITH PREFIX 'https://{QUERY_FARM_HOST}'",
    "INSTALL datasketches FROM query_farm",
    "LOAD datasketches FROM query_farm",
)


def duckdb_major_version(version: str | None = None) -> int | None:
    if version is None:
        import duckdb

        version = getattr(duckdb, "__version__", None)
    head = (version or "").lstrip("v").split(".", 1)[0]
    return int(head) if head.isdigit() else None


def duckdb_relation_arrow_supported(version: str | None = None) -> bool:
    major = duckdb_major_version(version)
    return major is not None and major >= RELATION_ARROW_FROM_MAJOR


def finish_unfinished_result(connection: Any) -> None:
    complete = getattr(connection, "complete", None)
    if callable(complete):
        complete()


def fetch_duckdb_arrow_table(connection: Any, sql: str, version: str | None = None) -> Any:
    if duckdb_relation_arrow_supported(version):
        finish_unfinished_result(connection)
        return connection.sql(sql).to_arrow_table()
    return connection.execute(sql).fetch_arrow_table()


def datasketches_install_statements(version: str | None = None) -> tuple[str, ...]:
    major = duckdb_major_version(version)
    if major is not None and major >= 2:
        return DATASKETCHES_QUERY_FARM_STATEMENTS
    return DATASKETCHES_COMMUNITY_STATEMENTS


def install_datasketches(connection: Any, version: str | None = None) -> None:
    for statement in datasketches_install_statements(version):
        connection.execute(statement)


def rewrite_datasketches_install_sql(sql: str, version: str | None = None) -> str:
    community = ";\n".join(DATASKETCHES_COMMUNITY_STATEMENTS) + ";"
    if community not in sql:
        return sql
    statements = datasketches_install_statements(version)
    if statements is DATASKETCHES_COMMUNITY_STATEMENTS:
        return sql
    return sql.replace(community, ";\n".join(statements) + ";")


def datasketches_skip_reason(sql: str, allow_third_party: bool, version: str | None = None) -> str | None:
    community = ";\n".join(DATASKETCHES_COMMUNITY_STATEMENTS) + ";"
    if allow_third_party or community not in sql:
        return None
    if datasketches_install_statements(version) is DATASKETCHES_COMMUNITY_STATEMENTS:
        return None
    return (
        "the datasketches extension for DuckDB 2.x is only available from the third-party Query.Farm repository "
        f"(https://{QUERY_FARM_HOST}); {THIRD_PARTY_ENABLE_HINT}"
    )


def single_arrow_lambda_statement(version: str | None = None) -> str | None:
    major = duckdb_major_version(version)
    if major is not None and major >= SINGLE_ARROW_REJECTED_FROM_MAJOR:
        return SINGLE_ARROW_LAMBDA_STATEMENT
    return None


def apply_single_arrow_lambda(connection: Any, version: str | None = None) -> str | None:
    statement = single_arrow_lambda_statement(version)
    if statement is not None:
        connection.execute(statement)
        return SINGLE_ARROW_LAMBDA_SETTING
    return None
