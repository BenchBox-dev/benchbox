# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from dataclasses import dataclass

from benchbox.core.metadata_primitives.complexity import TypeComplexity
from benchbox.sql_compat.ddl_capabilities import get_metadata_ddl_capabilities


@dataclass
class ColumnDefinition:
    name: str
    data_type: str
    nullable: bool = True
    primary_key: bool = False


@dataclass
class TableDefinition:
    name: str
    columns: list[ColumnDefinition]
    schema_name: str | None = None


@dataclass
class ViewDefinition:
    name: str
    source_sql: str
    schema_name: str | None = None


TYPE_MAPPINGS: dict[str, dict[str, str]] = {
    "duckdb": {
        "integer": "INTEGER",
        "bigint": "BIGINT",
        "varchar": "VARCHAR(255)",
        "varchar_short": "VARCHAR(50)",
        "varchar_long": "VARCHAR(1000)",
        "decimal": "DECIMAL(18,4)",
        "decimal_small": "DECIMAL(10,2)",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
        "boolean": "BOOLEAN",
        "double": "DOUBLE",
        "array_int": "INTEGER[]",
        "array_varchar": "VARCHAR[]",
        "struct_simple": "STRUCT(key VARCHAR, value VARCHAR)",
        "struct_nested": "STRUCT(name VARCHAR, data STRUCT(x INTEGER, y INTEGER))",
        "map_simple": "MAP(VARCHAR, INTEGER)",
    },
    "snowflake": {
        "integer": "INTEGER",
        "bigint": "BIGINT",
        "varchar": "VARCHAR(255)",
        "varchar_short": "VARCHAR(50)",
        "varchar_long": "VARCHAR(1000)",
        "decimal": "NUMBER(18,4)",
        "decimal_small": "NUMBER(10,2)",
        "date": "DATE",
        "timestamp": "TIMESTAMP_NTZ",
        "boolean": "BOOLEAN",
        "double": "DOUBLE",
        "array_int": "ARRAY",
        "array_varchar": "ARRAY",
        "struct_simple": "OBJECT",
        "struct_nested": "OBJECT",
        "map_simple": "OBJECT",
    },
    "bigquery": {
        "integer": "INT64",
        "bigint": "INT64",
        "varchar": "STRING",
        "varchar_short": "STRING",
        "varchar_long": "STRING",
        "decimal": "NUMERIC",
        "decimal_small": "NUMERIC",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
        "boolean": "BOOL",
        "double": "FLOAT64",
        "array_int": "ARRAY<INT64>",
        "array_varchar": "ARRAY<STRING>",
        "struct_simple": "STRUCT<key STRING, value STRING>",
        "struct_nested": "STRUCT<name STRING, data STRUCT<x INT64, y INT64>>",
        "map_simple": "ARRAY<STRUCT<key STRING, value INT64>>",
    },
    "clickhouse": {
        "integer": "Int32",
        "bigint": "Int64",
        "varchar": "String",
        "varchar_short": "String",
        "varchar_long": "String",
        "decimal": "Decimal(18,4)",
        "decimal_small": "Decimal(10,2)",
        "date": "Date",
        "timestamp": "DateTime",
        "boolean": "Bool",
        "double": "Float64",
        "array_int": "Array(Int32)",
        "array_varchar": "Array(String)",
        "struct_simple": "Tuple(key String, value String)",
        "struct_nested": "Tuple(name String, data Tuple(x Int32, y Int32))",
        "map_simple": "Map(String, Int32)",
    },
    "databricks": {
        "integer": "INT",
        "bigint": "BIGINT",
        "varchar": "STRING",
        "varchar_short": "STRING",
        "varchar_long": "STRING",
        "decimal": "DECIMAL(18,4)",
        "decimal_small": "DECIMAL(10,2)",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
        "boolean": "BOOLEAN",
        "double": "DOUBLE",
        "array_int": "ARRAY<INT>",
        "array_varchar": "ARRAY<STRING>",
        "struct_simple": "STRUCT<key: STRING, value: STRING>",
        "struct_nested": "STRUCT<name: STRING, data: STRUCT<x: INT, y: INT>>",
        "map_simple": "MAP<STRING, INT>",
    },
    "postgres": {
        "integer": "INTEGER",
        "bigint": "BIGINT",
        "varchar": "VARCHAR(255)",
        "varchar_short": "VARCHAR(50)",
        "varchar_long": "VARCHAR(1000)",
        "decimal": "NUMERIC(18,4)",
        "decimal_small": "NUMERIC(10,2)",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
        "boolean": "BOOLEAN",
        "double": "DOUBLE PRECISION",
        "array_int": "INTEGER[]",
        "array_varchar": "VARCHAR[]",
        "struct_simple": "JSONB",
        "struct_nested": "JSONB",
        "map_simple": "JSONB",
    },
}

WIDE_TABLE_TYPE_DISTRIBUTION: list[tuple[str, float]] = [
    ("integer", 0.30),
    ("varchar", 0.25),
    ("decimal", 0.15),
    ("bigint", 0.10),
    ("date", 0.08),
    ("timestamp", 0.05),
    ("boolean", 0.04),
    ("double", 0.03),
]


def get_type_mapping(dialect: str) -> dict[str, str]:
    normalized = dialect.lower().strip()
    if normalized not in TYPE_MAPPINGS:
        return TYPE_MAPPINGS["duckdb"]
    return TYPE_MAPPINGS[normalized]


def map_type(logical_type: str, dialect: str) -> str:
    mapping = get_type_mapping(dialect)
    return mapping.get(logical_type, mapping.get("varchar", "VARCHAR(255)"))


def generate_wide_table_columns(
    width: int,
    dialect: str,
    type_complexity: TypeComplexity = TypeComplexity.SCALAR,
) -> list[ColumnDefinition]:
    columns: list[ColumnDefinition] = []

    columns.append(
        ColumnDefinition(
            name="id",
            data_type=map_type("bigint", dialect),
            nullable=False,
            primary_key=True,
        )
    )

    remaining = width - 1
    type_counts: dict[str, int] = {}
    cumulative = 0.0

    for logical_type, percentage in WIDE_TABLE_TYPE_DISTRIBUTION:
        count = int(remaining * percentage)
        type_counts[logical_type] = count
        cumulative += count

    extra = remaining - int(cumulative)
    type_counts["varchar"] = type_counts.get("varchar", 0) + extra

    col_index = 1
    for logical_type, count in type_counts.items():
        sql_type = map_type(logical_type, dialect)
        for i in range(count):
            columns.append(
                ColumnDefinition(
                    name=f"col_{logical_type}_{col_index:04d}",
                    data_type=sql_type,
                    nullable=True,
                )
            )
            col_index += 1

    if type_complexity in (TypeComplexity.BASIC, TypeComplexity.NESTED):
        columns.append(
            ColumnDefinition(
                name="col_array_int",
                data_type=map_type("array_int", dialect),
                nullable=True,
            )
        )
        columns.append(
            ColumnDefinition(
                name="col_array_varchar",
                data_type=map_type("array_varchar", dialect),
                nullable=True,
            )
        )

    if type_complexity == TypeComplexity.NESTED:
        columns.append(
            ColumnDefinition(
                name="col_struct_simple",
                data_type=map_type("struct_simple", dialect),
                nullable=True,
            )
        )
        columns.append(
            ColumnDefinition(
                name="col_struct_nested",
                data_type=map_type("struct_nested", dialect),
                nullable=True,
            )
        )
        if get_metadata_ddl_capabilities(dialect).supports_map_columns:
            columns.append(
                ColumnDefinition(
                    name="col_map",
                    data_type=map_type("map_simple", dialect),
                    nullable=True,
                )
            )

    return columns


def generate_create_table_sql(
    table_def: TableDefinition,
    dialect: str,
    if_not_exists: bool = True,
) -> str:
    parts: list[str] = []

    if table_def.schema_name:
        full_name = f"{table_def.schema_name}.{table_def.name}"
    else:
        full_name = table_def.name

    if if_not_exists:
        parts.append(f"CREATE TABLE IF NOT EXISTS {full_name} (")
    else:
        parts.append(f"CREATE TABLE {full_name} (")

    col_defs: list[str] = []
    pk_columns: list[str] = []

    for col in table_def.columns:
        col_sql = f"    {col.name} {col.data_type}"
        if not col.nullable:
            col_sql += " NOT NULL"
        col_defs.append(col_sql)
        if col.primary_key:
            pk_columns.append(col.name)

    capabilities = get_metadata_ddl_capabilities(dialect)

    if pk_columns and capabilities.supports_primary_key_clause:
        pk_constraint = f"    PRIMARY KEY ({', '.join(pk_columns)})"
        col_defs.append(pk_constraint)

    parts.append(",\n".join(col_defs))
    parts.append(")")

    if capabilities.table_engine_clause_template:
        order_by = f"({', '.join(pk_columns)})" if pk_columns else capabilities.table_engine_empty_order_by
        parts.append(capabilities.table_engine_clause_template.format(order_by=order_by))

    return "\n".join(parts) + ";"


def generate_create_view_sql(
    view_def: ViewDefinition,
    dialect: str,
    or_replace: bool = True,
) -> str:
    if view_def.schema_name:
        full_name = f"{view_def.schema_name}.{view_def.name}"
    else:
        full_name = view_def.name

    capabilities = get_metadata_ddl_capabilities(dialect)
    prefix = capabilities.create_or_replace_view_prefix if or_replace else capabilities.create_view_if_not_exists_prefix
    return f"{prefix} {full_name} AS\n{view_def.source_sql};"


def generate_drop_table_sql(
    table_name: str,
    dialect: str,
    schema_name: str | None = None,
    if_exists: bool = True,
) -> str:
    if schema_name:
        full_name = f"{schema_name}.{table_name}"
    else:
        full_name = table_name

    if if_exists:
        return f"DROP TABLE IF EXISTS {full_name};"
    return f"DROP TABLE {full_name};"


def generate_drop_view_sql(
    view_name: str,
    dialect: str,
    schema_name: str | None = None,
    if_exists: bool = True,
) -> str:
    if schema_name:
        full_name = f"{schema_name}.{view_name}"
    else:
        full_name = view_name

    if if_exists:
        return f"DROP VIEW IF EXISTS {full_name};"
    return f"DROP VIEW {full_name};"


def generate_simple_table_columns(
    column_count: int,
    dialect: str,
) -> list[ColumnDefinition]:
    columns: list[ColumnDefinition] = [
        ColumnDefinition(
            name="id",
            data_type=map_type("bigint", dialect),
            nullable=False,
            primary_key=True,
        ),
        ColumnDefinition(
            name="name",
            data_type=map_type("varchar", dialect),
            nullable=True,
        ),
        ColumnDefinition(
            name="created_at",
            data_type=map_type("timestamp", dialect),
            nullable=True,
        ),
        ColumnDefinition(
            name="amount",
            data_type=map_type("decimal", dialect),
            nullable=True,
        ),
        ColumnDefinition(
            name="is_active",
            data_type=map_type("boolean", dialect),
            nullable=True,
        ),
    ]

    for i in range(5, column_count):
        columns.append(
            ColumnDefinition(
                name=f"field_{i:02d}",
                data_type=map_type("varchar", dialect),
                nullable=True,
            )
        )

    return columns


def supports_complex_types(dialect: str) -> bool:
    return get_metadata_ddl_capabilities(dialect).supports_complex_types


def supports_views(dialect: str) -> bool:
    return get_metadata_ddl_capabilities(dialect).supports_views


def supports_foreign_keys(dialect: str) -> bool:
    return get_metadata_ddl_capabilities(dialect).supports_foreign_keys


def supports_acl(dialect: str) -> bool:
    no_acl = {"sqlite", "datafusion", "spark", "polars", "duckdb"}
    return dialect.lower() not in no_acl


def supports_acl_introspection(dialect: str) -> bool:
    introspection_platforms = {
        "postgresql",
        "postgres",
        "redshift",
        "firebolt",
        "synapse",
        "fabric",
        "databricks",
        "clickhouse",
    }
    return dialect.lower() in introspection_platforms


def supports_role_hierarchy(dialect: str) -> bool:
    hierarchy_platforms = {
        "postgresql",
        "postgres",
        "redshift",
        "clickhouse",
        "snowflake",
        "databricks",
        "synapse",
        "fabric",
        "firebolt",
    }
    return dialect.lower() in hierarchy_platforms


def supports_column_grants(dialect: str) -> bool:
    column_grant_platforms = {
        "postgresql",
        "postgres",
        "redshift",
        "synapse",
        "fabric",
        "snowflake",
        "databricks",
    }
    return dialect.lower() in column_grant_platforms


def generate_create_role_sql(
    role_name: str,
    dialect: str,
    if_not_exists: bool = True,
) -> str:
    d = dialect.lower()

    if d in ("synapse", "fabric"):
        return f"CREATE ROLE [{role_name}];"
    elif d == "bigquery":
        return "-- BigQuery: Role creation not supported via SQL (IAM-based)"
    elif d == "snowflake":
        if if_not_exists:
            return f"CREATE ROLE IF NOT EXISTS {role_name};"
        return f"CREATE ROLE {role_name};"
    elif d in ("postgresql", "postgres", "redshift"):
        return f"CREATE ROLE {role_name};"
    elif d == "clickhouse" or d == "databricks":
        if if_not_exists:
            return f"CREATE ROLE IF NOT EXISTS {role_name};"
        return f"CREATE ROLE {role_name};"
    elif d == "duckdb":
        return f"CREATE ROLE {role_name};"
    else:
        return f"CREATE ROLE {role_name};"


def generate_drop_role_sql(
    role_name: str,
    dialect: str,
    if_exists: bool = True,
) -> str:
    d = dialect.lower()

    if d in ("synapse", "fabric"):
        return f"DROP ROLE IF EXISTS [{role_name}];"
    elif d == "bigquery":
        return "-- BigQuery: Role drop not supported via SQL (IAM-based)"
    elif d in ("postgresql", "postgres", "redshift"):
        if if_exists:
            return f"DROP ROLE IF EXISTS {role_name};"
        return f"DROP ROLE {role_name};"
    else:
        if if_exists:
            return f"DROP ROLE IF EXISTS {role_name};"
        return f"DROP ROLE {role_name};"


def generate_grant_sql(
    grantee: str,
    object_name: str,
    privileges: list[str],
    dialect: str,
    object_type: str = "TABLE",
    with_grant_option: bool = False,
    column_name: str | None = None,
) -> str:
    d = dialect.lower()
    priv_str = ", ".join(privileges)

    if column_name:
        if d in ("synapse", "fabric"):
            grant_target = f"{priv_str} ({column_name}) ON {object_type} [{object_name}]"
        else:
            grant_target = f"{priv_str} ({column_name}) ON {object_type} {object_name}"
    else:
        if d in ("synapse", "fabric"):
            grant_target = f"{priv_str} ON {object_type} [{object_name}]"
        else:
            grant_target = f"{priv_str} ON {object_type} {object_name}"

    if d in ("synapse", "fabric"):
        grantee_ref = f"[{grantee}]"
    else:
        grantee_ref = grantee

    grant_option = ""
    if with_grant_option:
        if d in ("synapse", "fabric"):
            grant_option = " WITH GRANT OPTION"
        else:
            grant_option = " WITH GRANT OPTION"

    return f"GRANT {grant_target} TO {grantee_ref}{grant_option};"


def generate_revoke_sql(
    grantee: str,
    object_name: str,
    privileges: list[str],
    dialect: str,
    object_type: str = "TABLE",
    column_name: str | None = None,
) -> str:
    d = dialect.lower()
    priv_str = ", ".join(privileges)

    if column_name:
        if d in ("synapse", "fabric"):
            revoke_target = f"{priv_str} ({column_name}) ON {object_type} [{object_name}]"
        else:
            revoke_target = f"{priv_str} ({column_name}) ON {object_type} {object_name}"
    else:
        if d in ("synapse", "fabric"):
            revoke_target = f"{priv_str} ON {object_type} [{object_name}]"
        else:
            revoke_target = f"{priv_str} ON {object_type} {object_name}"

    if d in ("synapse", "fabric"):
        grantee_ref = f"[{grantee}]"
    else:
        grantee_ref = grantee

    return f"REVOKE {revoke_target} FROM {grantee_ref};"


def generate_grant_role_sql(
    parent_role: str,
    child_role: str,
    dialect: str,
) -> str:
    d = dialect.lower()

    if d in ("synapse", "fabric"):
        return f"ALTER ROLE [{parent_role}] ADD MEMBER [{child_role}];"
    elif d == "bigquery":
        return "-- BigQuery: Role grants not supported via SQL"
    elif d == "clickhouse":
        return f"GRANT {parent_role} TO {child_role};"
    else:
        return f"GRANT {parent_role} TO {child_role};"


def generate_revoke_role_sql(
    parent_role: str,
    child_role: str,
    dialect: str,
) -> str:
    d = dialect.lower()

    if d in ("synapse", "fabric"):
        return f"ALTER ROLE [{parent_role}] DROP MEMBER [{child_role}];"
    elif d == "bigquery":
        return "-- BigQuery: Role revokes not supported via SQL"
    elif d == "clickhouse":
        return f"REVOKE {parent_role} FROM {child_role};"
    else:
        return f"REVOKE {parent_role} FROM {child_role};"


__all__ = [
    "ColumnDefinition",
    "TableDefinition",
    "ViewDefinition",
    "TYPE_MAPPINGS",
    "WIDE_TABLE_TYPE_DISTRIBUTION",
    "generate_create_table_sql",
    "generate_create_view_sql",
    "generate_drop_table_sql",
    "generate_drop_view_sql",
    "generate_simple_table_columns",
    "generate_wide_table_columns",
    "get_type_mapping",
    "map_type",
    "supports_complex_types",
    "supports_foreign_keys",
    "supports_views",
    "supports_acl",
    "supports_acl_introspection",
    "supports_role_hierarchy",
    "supports_column_grants",
    "generate_create_role_sql",
    "generate_drop_role_sql",
    "generate_grant_sql",
    "generate_revoke_sql",
    "generate_grant_role_sql",
    "generate_revoke_role_sql",
]
