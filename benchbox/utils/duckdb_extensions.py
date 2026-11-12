from __future__ import annotations

from typing import Any

DATASKETCHES_COMMUNITY_STATEMENTS: tuple[str, ...] = (
    "INSTALL datasketches FROM community",
    "LOAD datasketches",
)

DATASKETCHES_QUERY_FARM_STATEMENTS: tuple[str, ...] = (
    "SET allow_extension_repositories = 'allowed'",
    "CREATE EXTENSION REPOSITORY IF NOT EXISTS query_farm WITH PREFIX 'https://duckdb-extensions.query.farm'",
    "INSTALL datasketches FROM query_farm",
    "LOAD datasketches FROM query_farm",
)


def duckdb_major_version(version: str | None = None) -> int | None:
    if version is None:
        import duckdb

        version = getattr(duckdb, "__version__", None)
    head = (version or "").lstrip("v").split(".", 1)[0]
    return int(head) if head.isdigit() else None


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
