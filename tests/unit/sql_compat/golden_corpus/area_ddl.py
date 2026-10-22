from __future__ import annotations

import importlib
import logging
import re
from collections.abc import Callable
from typing import Any

from benchbox.core.tuning.interface import TableTuning, TuningColumn

from . import Case

_READ = "postgres"
_ENGINES = "benchbox._vendor.sqlglot.benchbox.ddl_engines"


_ARR = r"(float|double)_array"
_Check = Callable[[str, str], bool]


def _size(text: str) -> str:
    return re.sub(r"\[\d+\]", "", text)


def _split(statement: str) -> tuple[str, list[str], str] | None:
    start = statement.find("(")
    if start < 0:
        return None
    depth = 0
    columns: list[str] = []
    current = ""
    for index in range(start, len(statement)):
        char = statement[index]
        if char == "(":
            depth += 1
            if depth == 1:
                continue
        elif char == ")":
            depth -= 1
            if depth == 0:
                columns.append(current.strip())
                return statement[: start + 1], columns, statement[index:]
        elif char == "," and depth == 1:
            columns.append(current.strip())
            current = ""
            continue
        current += char
    return None


def _embedding(legacy: str, fork: str) -> tuple[str, str] | None:
    left, right = _split(legacy), _split(fork)
    if left is None or right is None or left[0] != right[0] or left[2] != right[2]:
        return None
    if len(left[1]) != len(right[1]):
        return None
    found = None
    for old, new in zip(left[1], right[1], strict=True):
        if old == new:
            continue
        if found is not None or not old.startswith("embedding ") or not new.startswith("embedding "):
            return None
        found = (old, new)
    return found


def _has_size(legacy: str, fork: str) -> bool:
    legacy = legacy.replace("`", "").replace(" TBLPROPERTIES()", "")
    found = _embedding(legacy, fork.replace("`", ""))
    return found is not None and bool(re.search(r"\[\d+\]", found[0])) and not re.search(r"\[\d+\]", found[1])


def _fork_raises(legacy: str, fork: str) -> bool:
    return bool(re.search(r"ARRAY<FLOAT>\[\d+\]\)?", legacy)) and fork == "<ValueError>"


def _singlestore_vector(element: str):
    def check(legacy: str, fork: str) -> bool:
        found = _embedding(legacy, fork)
        if found is None:
            return False
        size = re.search(r"\[(\d+)\]", found[0])
        return size is not None and found[1] == f"embedding VECTOR({size.group(1)}, {element})"

    return check


def _empty_tblproperties(legacy: str, fork: str) -> bool:
    return " TBLPROPERTIES()" in legacy and _size(legacy.replace(" TBLPROPERTIES()", "")) == _size(fork)


def _delta_using(legacy: str, fork: str) -> bool:
    return (
        "USING PARQUET" in legacy
        and legacy.replace(" TBLPROPERTIES()", "").replace("USING PARQUET", "USING DELTA") == fork
    )


def _no_backticks(legacy: str, fork: str) -> bool:
    return legacy.replace("`", "") == fork.replace("`", "")


_CLICKHOUSE_TYPES = (
    (r"\b(?:varchar|char|text|string)(?:\(\d+\))?(?=[ ,)])", "string"),
    (r"\bbigint\b", "int64"),
    (r"\bsmallint\b", "int16"),
    (r"\btinyint\b", "int8"),
    (r"\bint\b", "int32"),
    (r"\btimestamp\b", "datetime"),
    (r"\bboolean\b", "bool"),
    (r"\bdouble precision\b|\bdouble\b", "float64"),
)


def _clickhouse_spelling(legacy: str, fork: str) -> bool:
    def normal(text: str) -> str:
        text = text.lower()
        for pattern, replacement in _CLICKHOUSE_TYPES:
            text = re.sub(pattern, replacement, text)
        return text

    return normal(legacy) == normal(fork)


def _clickhouse_order_by(legacy: str, fork: str) -> bool:
    return "ORDER BY(CONSTRAINT" in legacy and "ORDER BY(CONSTRAINT" not in fork


def _drop_named_key(text: str) -> str:
    return re.sub(r",? ?CONSTRAINT pk( PRIMARY KEY\(a\))?", "", text)


def _dangling_constraint(legacy: str, fork: str) -> bool:
    return "CONSTRAINT" in legacy and _drop_named_key(legacy) == fork


def _key_removed(legacy: str, fork: str) -> bool:
    stripped = _drop_named_key(legacy).replace(" PRIMARY KEY", "")
    return stripped != legacy and stripped == fork


def _starrocks_duplicate_to_primary(legacy: str, fork: str) -> bool:
    normalised = _drop_named_key(legacy).replace(" PRIMARY KEY", "").replace("DUPLICATE KEY", "PRIMARY KEY")
    return "DUPLICATE KEY" in legacy and normalised == fork


def _doris_varchar(legacy: str, fork: str) -> bool:
    return legacy.replace("c VARCHAR)", "c VARCHAR(65533))") == fork


def _schema_qualified(legacy: str, fork: str) -> bool:
    return legacy.replace("[dbo].", "") == fork.replace("[dbo].", "")


def _schema_guard(legacy: str, fork: str) -> bool:
    guard = re.sub(r" AND TABLE_SCHEMA='dbo'", "", fork.replace("[dbo].", ""))
    guard = re.sub(r" ?WITH\(DISTRIBUTION=\w+\)", "", guard)
    return (
        "TABLE_SCHEMA" not in legacy
        and "TABLE_SCHEMA" in fork
        and guard == re.sub(r" ?WITH\(DISTRIBUTION=\w+\)", "", legacy)
    )


def _postgres_string(legacy: str, fork: str) -> bool:
    return legacy.replace("b STRING", "b TEXT") == fork


def _postgres_double(legacy: str, fork: str) -> bool:
    return "DOUBLE[" in legacy and fork == legacy.replace("DOUBLE[", "DOUBLE PRECISION[")


def _postgres_float(legacy: str, fork: str) -> bool:
    return fork == legacy.replace("FLOAT[", "DOUBLE PRECISION[")


def _same_lower(legacy: str, fork: str) -> bool:
    return legacy.lower() == fork.lower()


def _comment_prefix(legacy: str, fork: str) -> bool:
    return re.sub(r"^/\*.*?\*/ ", "", fork) == legacy and fork != legacy


def _same_tokens(legacy: str, fork: str) -> bool:
    def tokens(text: str) -> list[str]:
        return sorted(token for token in re.split(r"[\s,()]+", text) if token)

    return tokens(legacy) == tokens(fork)


def _unquoted_lower(legacy: str, fork: str) -> bool:
    return legacy.replace('"', "").lower() == fork.replace('"', "").lower()


def _repeated_partition_column(legacy: str, fork: str) -> bool:
    deduplicated = legacy.replace("o_orderdate DATE, ", "", 1)
    return deduplicated != legacy and _same_tokens(deduplicated, fork)


def _sorted_without_cluster(legacy: str, fork: str) -> bool:
    without = re.sub(r" SORTED BY\([^)]*\)", "", legacy)
    return without != legacy and "CLUSTERED BY" not in legacy and _same_tokens(without, fork)


def _time_type(legacy: str, fork: str) -> bool:
    widened = re.sub(r"(?<= )TIME(?=[,)])", "TIMESTAMP", legacy)
    return widened != legacy and widened == fork


def _questdb_lengths(legacy: str, fork: str) -> bool:
    return re.sub(r"\b(?:VARCHAR|CHAR)\(\d+\)", "VARCHAR", legacy) == fork


def _questdb_missing_column(legacy: str, fork: str) -> bool:
    without_clause = re.sub(r" timestamp\(l_shipdate\)( PARTITION BY \w+)?", "", legacy)
    return (
        "timestamp(l_shipdate)" in legacy
        and "timestamp(l_shipdate)" not in fork
        and _questdb_lengths(without_clause, fork)
    )


_TSQL = r"(fabric|synapse_(replicate|round_robin))"

CLASSIFICATIONS: list[tuple[str, str, str, _Check]] = [
    (
        rf"(athena_(csv|other|parquet)|bigquery|databend|databricks_delta_auto_optimize|presto_(hive|iceberg)|"
        rf"trino_(hive|iceberg|memory|delta)|snowflake|starrocks|questdb_(default|day|none)):{_ARR}",
        "legacy-bug",
        "Legacy wrote a fixed-size array (ARRAY(DOUBLE)[N], DOUBLE[N]) that the target rejects (QuestDB verified on "
        "10.0.1: arrays do not have a fixed size); the fork writes the target's plain array type.",
        _has_size,
    ),
    (
        rf"databricks_delta_no_auto_optimize:{_ARR}",
        "legacy-bug",
        "Legacy wrote a fixed-size array and an empty TBLPROPERTIES() clause, which Databricks rejects.",
        _empty_tblproperties,
    ),
    (
        rf"{_TSQL}:{_ARR}",
        "legacy-bug",
        "T-SQL has no array column type, so legacy wrote invalid DDL; the fork raises a ValueError instead of "
        "writing it (VECTOR(n) is written only on a SQL Server or Azure SQL target with vector_type).",
        _fork_raises,
    ),
    (
        r"singlestore:float_array",
        "legacy-bug",
        "SingleStore has no ARRAY column type, so legacy wrote invalid DDL; the fork writes VECTOR(n, F32) for "
        "4-byte floats.",
        _singlestore_vector("F32"),
    ),
    (
        r"singlestore:double_array",
        "legacy-bug",
        "SingleStore has no ARRAY column type, so legacy wrote invalid DDL; the fork writes VECTOR(n, F64) for "
        "8-byte floats.",
        _singlestore_vector("F64"),
    ),
    (
        r"helper_strip_primary_keys:double_array",
        "legacy-bug",
        "DOUBLE is not a PostgreSQL type; the fork writes DOUBLE PRECISION[N].",
        _postgres_double,
    ),
    (
        r"helper_strip_primary_keys:float_array",
        "equivalent",
        "PostgreSQL FLOAT is double precision; the fork writes DOUBLE PRECISION[N].",
        _postgres_float,
    ),
    (
        r"athena_(csv|other|parquet):time_column",
        "legacy-bug",
        "Athena external tables have no TIME column type; the fork writes TIMESTAMP.",
        _time_type,
    ),
    (
        r"databricks_delta_(auto|no_auto)_optimize:using_present",
        "legacy-bug",
        "Legacy kept the statement's USING PARQUET although Delta was requested; the layout format replaces "
        "the statement's own USING (fork behaviour, a table that keeps its USING needs an empty layout).",
        _delta_using,
    ),
    (
        r"databricks_delta_no_auto_optimize:.*",
        "legacy-bug",
        "Legacy wrote an empty TBLPROPERTIES() clause, which Databricks rejects.",
        _empty_tblproperties,
    ),
    (
        r"bigquery:.*",
        "equivalent",
        "Legacy writes project.dataset.table as one backtick-quoted identifier; the fork writes project, dataset and "
        "table as separate identifiers. Both resolve to the same table; only the quoting differs.",
        _no_backticks,
    ),
    (
        r"clickhouse_default:pk_named",
        "legacy-bug",
        "Legacy writes ORDER BY(CONSTRAINT, a), taking the constraint keyword for a key column; the fork writes "
        "ORDER BY(a). Both keep the named constraint in the column list; no shipped DDL uses a named key.",
        _clickhouse_order_by,
    ),
    (
        r"clickhouse_(default|no_primary_keys|nullable|tuned):.*",
        "equivalent",
        "ClickHouse type spelling: Int32, String, Decimal, DateTime and Date name the same types as the SQL aliases "
        "INT, VARCHAR(n), CHAR(n), DECIMAL, TIMESTAMP and DATE legacy kept.",
        _clickhouse_spelling,
    ),
    (
        r"doris_(plain|partitioned):bare_varchar",
        "legacy-bug",
        "Legacy wrote a bare VARCHAR, whose default length is not the unbounded text the benchmark means; the fork "
        "writes the explicit VARCHAR(65533), the maximum, as it does for StarRocks.",
        _doris_varchar,
    ),
    (
        r"databend:pk_(inline|named)",
        "legacy-bug",
        "Legacy leaves an inline PRIMARY KEY or a dangling CONSTRAINT name that the adapter meant to strip.",
        _key_removed,
    ),
    (
        r"(doris_plain|doris_partitioned|lakesail_parquet|spark_orc|spark_parquet):pk_named",
        "legacy-bug",
        "Legacy leaves a dangling CONSTRAINT name after removing the PRIMARY KEY; the fork removes the whole constraint.",
        _dangling_constraint,
    ),
    (
        rf"{_TSQL}:if_not_exists",
        "legacy-bug",
        "Legacy IF NOT EXISTS guard checks the table name in every schema (and, for Synapse, drops the "
        "DISTRIBUTION clause); the fork also checks TABLE_SCHEMA and keeps the layout.",
        _schema_guard,
    ),
    (
        rf"{_TSQL}:.*",
        "equivalent",
        "The fork writes the adapter schema ([dbo].) on the table and its REFERENCES targets; legacy relied "
        "on the connection default schema, which is dbo.",
        _schema_qualified,
    ),
    (
        r"helper_strip_primary_keys:text_string_types",
        "legacy-bug",
        "STRING is not a PostgreSQL type; the fork writes TEXT.",
        _postgres_string,
    ),
    (
        r"helper_strip_primary_keys:with_properties",
        "equivalent",
        "Only the case of the property name differs (format vs FORMAT).",
        _same_lower,
    ),
    (
        r"(lakesail_parquet|spark_delta|spark_orc|spark_parquet):comment_prefix",
        "equivalent",
        "The fork keeps a leading /* comment */ on the statement; the statement is otherwise identical.",
        _comment_prefix,
    ),
    (
        r"snowflake:quoted_identifiers",
        "legacy-bug",
        "Legacy uppercases quoted identifiers, renaming the user's mixed-case names; the fork keeps them.",
        _unquoted_lower,
    ),
    (
        r"spark_mixin:delta_.*",
        "equivalent",
        "Only the order of the two delta.autoOptimize TBLPROPERTIES differs.",
        _same_tokens,
    ),
    (
        r"spark_mixin:hive_bucketed",
        "equivalent",
        "Only clause order differs; the fork follows the Hive grammar (CLUSTERED BY before STORED AS).",
        _same_tokens,
    ),
    (
        r"spark_mixin:hive_partition",
        "legacy-bug",
        "Legacy repeats the partition column in the column list, which Hive rejects.",
        _repeated_partition_column,
    ),
    (
        r"spark_mixin:iceberg_partition",
        "equivalent",
        "months(col) and MONTHS(col) are the same transform.",
        _same_lower,
    ),
    (
        r"spark_mixin:parquet_partition_sort",
        "legacy-bug",
        "Legacy wrote SORTED BY without CLUSTERED BY, which Spark rejects (verified on PySpark).",
        _sorted_without_cluster,
    ),
    (
        r"starrocks:(lineitem_codec|pk_inline|pk_middle|pk_trailing|pk_named)",
        "legacy-bug",
        "Legacy falls back to DUPLICATE KEY on single-line DDL because its pattern needs newline-indented columns "
        "(and leaves a dangling CONSTRAINT name); on the multi-line TPC DDL it writes PRIMARY KEY like the fork.",
        _starrocks_duplicate_to_primary,
    ),
    (
        r"questdb_(default|day|none):lineitem_codec",
        "legacy-bug",
        "Legacy wrote timestamp(l_shipdate) for a table without that column, and VARCHAR(n); QuestDB rejects both.",
        _questdb_missing_column,
    ),
    (
        r"questdb_(default|day|none):.*",
        "legacy-bug",
        "QuestDB rejects CHAR(n) and VARCHAR(n) (verified on QuestDB 10.0.1: ',' or ')' expected); the fork writes "
        "VARCHAR.",
        _questdb_lengths,
    ),
]


def _tpch_statements() -> dict[str, str]:
    from benchbox.core.tpch.schema import get_create_all_tables_sql

    found: dict[str, str] = {}
    for statement in get_create_all_tables_sql(enable_primary_keys=True, enable_foreign_keys=True).split(";"):
        statement = statement.strip()
        if statement:
            found[f"tpch_{_name(statement)}"] = statement
    return found


def _tpcds_statements() -> dict[str, str]:
    from benchbox import TPCDS

    found: dict[str, str] = {}
    for statement in TPCDS(scale_factor=1).get_create_tables_sql().split(";"):
        statement = statement.strip()
        if statement and _name(statement) in {"store_sales", "call_center"}:
            found[f"tpcds_{_name(statement)}"] = statement
    return found


def _name(statement: str) -> str:
    match = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+)", statement, re.IGNORECASE)
    return match.group(1).lower() if match else ""


_MISC: dict[str, str] = {
    "bare_varchar": "CREATE TABLE t (c VARCHAR)",
    "char_with_length": "CREATE TABLE t (c CHAR(10))",
    "decimal_precision": "CREATE TABLE t (price DECIMAL(15, 2))",
    "pk_middle": "CREATE TABLE t (a INT, PRIMARY KEY (a), b TEXT)",
    "pk_trailing": "CREATE TABLE t (a INT, b TEXT, PRIMARY KEY (a))",
    "pk_inline": "CREATE TABLE t (a INT PRIMARY KEY, b TEXT)",
    "pk_named": "CREATE TABLE t (a INT, CONSTRAINT pk PRIMARY KEY (a))",
    "if_not_exists": "CREATE TABLE IF NOT EXISTS t (a INT, b VARCHAR(10))",
    "lineitem_codec": (
        "CREATE TABLE LineItem (l_orderkey BIGINT, l_comment VARCHAR(44), l_extendedprice DECIMAL(15, 2), "
        "PRIMARY KEY (l_orderkey))"
    ),
    "time_column": (
        "CREATE TABLE metrics (time TIMESTAMP, metric_time TIME, hostname VARCHAR(64), PRIMARY KEY (time, hostname))"
    ),
    "decimal_wide_scale": "CREATE TABLE t (lat DECIMAL(18, 14), amount DECIMAL(15, 2))",
    "float_array": "CREATE TABLE vectors (id BIGINT, embedding FLOAT[128])",
    "double_array": "CREATE TABLE vectors (id BIGINT, embedding DOUBLE[256])",
    "events_tuned": "CREATE TABLE events (event_id INTEGER, event_date DATE, payload VARCHAR(50))",
    "nullable_numeric": "CREATE TABLE t (amount DECIMAL(10,2), next_col INTEGER, tail VARCHAR(5))",
    "text_string_types": "CREATE TABLE t (a TEXT, b STRING, c SMALLINT, d TIMESTAMP, e BOOLEAN)",
    "non_create_insert": "INSERT INTO t VALUES (1)",
    "drop_table": "DROP TABLE IF EXISTS t",
}


def _inputs() -> dict[str, str]:
    return {**_tpch_statements(), **_tpcds_statements(), **_MISC}


def _bare(module: str, cls: str, **attributes: Any) -> Any:
    adapter = object.__new__(getattr(importlib.import_module(module), cls))
    adapter.logger = logging.getLogger("golden_corpus.ddl")
    for key, value in attributes.items():
        setattr(adapter, key, value)
    return adapter


def _squash(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip().rstrip(";").strip()
    text = re.sub(r"\bINTEGER\b", "INT", re.sub(r"\s*,\s*", ", ", text), flags=re.IGNORECASE)
    text = re.sub(r"\s*=\s*", "=", text)
    text = re.sub(r"([\w`\"\]])\s+\(", r"\1(", text)
    text = re.sub(r"(PROPERTIES\()([^)]*)\)", lambda m: m.group(1) + m.group(2).replace('"', "'") + ")", text)
    text = re.sub(r'(LESS THAN\()"([^"]*)"\)', r"\1'\2')", text)
    text = re.sub(r"`(\w+)`", r"\1", text)
    text = re.sub(r'"([a-z_][a-z0-9_]*)"', r"\1", text)
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", text))


def _translated(sql: str, target: str, normalize_names: bool = False) -> str:
    from benchbox.core.sql_utils import normalize_table_name_in_sql
    from benchbox.utils.dialect_utils import SCHEMA_DDL_SCOPE, translate_sql_query

    translated = translate_sql_query(sql, target, source_dialect="standard", strict=False, scope=SCHEMA_DDL_SCOPE)
    return normalize_table_name_in_sql(translated) if normalize_names else translated


def _fork(sql: str, write: str, target: str | None = None, read: str = _READ, **options: Any) -> str:
    from benchbox.utils.dialect_utils import NO_IDENTIFY_DIALECTS

    ddl = importlib.import_module("benchbox._vendor.sqlglot.benchbox.ddl")
    identify = target is not None and target not in NO_IDENTIFY_DIALECTS
    statements = ddl.transpile_ddl(sql, read=read, write=write, options=ddl.DdlOptions(**options), identify=identify)
    return _squash(" ".join(statements))


def _layout(**fields: Any) -> Any:
    return importlib.import_module("benchbox._vendor.sqlglot.benchbox.layout").TableLayout(**fields)


def _family(
    name: str,
    legacy: Callable[[str], str],
    fork: Callable[[str], str],
    inputs: dict[str, str],
) -> list[Case]:
    return [
        Case(f"{name}:{key}", (lambda sql=sql: _squash(legacy(sql))), (lambda sql=sql: fork(sql)))
        for key, sql in inputs.items()
    ]


def _select(*keys: str) -> dict[str, str]:
    inputs = _inputs()
    return {key: inputs[key] for key in keys}


def _only(prefixes: tuple[str, ...], extra: dict[str, str] | None = None) -> dict[str, str]:
    inputs = {key: sql for key, sql in _inputs().items() if key.startswith(prefixes)}
    return {**inputs, **(extra or {})}


def _first_column(statement: str) -> str:
    match = re.search(r"\(\s*`?(\w+)`?", statement)
    return match.group(1) if match else ""


def _firebolt() -> list[Case]:
    adapter = _bare("benchbox.platforms.firebolt", "FireboltAdapter")
    return _family(
        "firebolt",
        lambda sql: adapter._optimize_table_definition(_translated(sql, "postgres", True)),
        lambda sql: _fork(
            sql,
            "postgres",
            "postgres",
            strip_primary_keys=True,
            strip_foreign_keys=True,
            type_map={"VARCHAR": "TEXT", "CHAR": "TEXT", "DECIMAL": "NUMERIC"},
            drop_type_parameters=["TEXT"],
            table_case="lower",
        ),
        _inputs(),
    )


def _redshift() -> list[Case]:
    adapter = _bare("benchbox.platforms.redshift", "RedshiftAdapter")
    inputs = {
        **_inputs(),
        "diststyle_present": "CREATE TABLE t (a INT) DISTSTYLE EVEN",
        "sortkey_present": "CREATE TABLE t (a INT) SORTKEY (a)",
    }
    return _family(
        "redshift",
        lambda sql: adapter._optimize_table_definition(_translated(sql, "redshift", True)),
        lambda sql: _fork(
            sql,
            "redshift",
            "redshift",
            table_case="lower",
            default_layout=_layout(distribution="auto", sort_auto=True),
        ),
        inputs,
    )


def _clickhouse_nullable(statement: str) -> set[str]:
    if "next_col" in statement:
        return {"amount", "next_col"}
    return {
        match.group(1)
        for match in re.finditer(r"^\s+(\w+)\s+[A-Za-z]+(?:\([^)]*\))?\s*,?\s*$", statement, re.MULTILINE)
    }


def _clickhouse() -> list[Case]:
    adapter = _bare("benchbox.platforms.clickhouse.adapter", "ClickHouseAdapter")
    cases = _family(
        "clickhouse_default",
        adapter._optimize_table_definition,
        lambda sql: _fork(
            sql,
            "clickhouse",
            read="duckdb",
            type_map={"TIME": "String"},
            default_layout=_layout(engine="MergeTree()", nullable_columns=[]),
        ),
        _inputs(),
    )
    cases += _family(
        "clickhouse_no_primary_keys",
        lambda sql: adapter._optimize_table_definition(sql, None, primary_keys_enabled=False),
        lambda sql: _fork(
            sql,
            "clickhouse",
            read="duckdb",
            type_map={"TIME": "String"},
            default_layout=_layout(engine="MergeTree()", nullable_columns=[]),
        ),
        _select("tpch_orders", "tpch_lineitem", "pk_middle", "pk_inline"),
    )

    nullable_inputs = _select("tpcds_store_sales", "tpcds_call_center", "nullable_numeric", "tpch_orders")
    cases += _family(
        "clickhouse_nullable",
        lambda sql: adapter._optimize_table_definition(sql, None, nullable_columns=_clickhouse_nullable(sql)),
        lambda sql: _fork(
            sql,
            "clickhouse",
            read="duckdb",
            type_map={"TIME": "String"},
            default_layout=_layout(engine="MergeTree()", nullable_columns=sorted(_clickhouse_nullable(sql))),
        ),
        nullable_inputs,
    )

    def tuned(sql: str, table_tunings: dict[str, Any]) -> tuple[Callable[[], str], Callable[[], str]]:
        def legacy() -> str:
            return _squash(adapter._optimize_table_definition(sql, table_tunings))

        def fork() -> str:
            keys = adapter._extract_primary_key_columns(sql)
            clauses = adapter._resolve_tuned_ddl_clauses(
                sql, table_tunings, primary_key_columns=keys if "ORDER BY" not in sql.upper() else None
            )
            layout = _layout(
                engine="MergeTree()",
                nullable_columns=[],
                partition_by=[clauses.partition_by] if clauses and clauses.partition_by else [],
                sort_by=[clauses.sort_by] if clauses and clauses.sort_by else [],
                primary_key=[clauses.primary_key] if clauses and clauses.primary_key else [],
            )
            return _fork(sql, "clickhouse", read="duckdb", type_map={"TIME": "String"}, default_layout=layout)

        return legacy, fork

    tunings = {
        "partition_and_sort": TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
            sorting=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)],
        ),
        "sort_only": TableTuning(
            table_name="lineitem",
            sorting=[
                TuningColumn(name="l_orderkey", type="INTEGER", order=1),
                TuningColumn(name="l_linenumber", type="INTEGER", order=2),
            ],
        ),
        "partition_only": TableTuning(
            table_name="lineitem", partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)]
        ),
    }
    statements = {
        "pk": _inputs()["tpch_lineitem"],
        "nopk": "CREATE TABLE lineitem (l_orderkey Int32, l_shipdate Date)",
    }
    for kind, statement in statements.items():
        for label, table_tuning in tunings.items():
            legacy, fork = tuned(statement, {"lineitem": table_tuning})
            cases.append(Case(f"clickhouse_tuned:{kind}_{label}", legacy, fork))
    return cases


def _databricks() -> list[Case]:
    cases: list[Case] = []
    properties = {"delta.autoOptimize.optimizeWrite": "true", "delta.autoOptimize.autoCompact": "true"}
    inputs = {
        **_inputs(),
        "using_present": "CREATE TABLE t (a INT) USING PARQUET",
        "comment_prefix": "-- header\nCREATE TABLE t (a INT)",
    }
    delta = _bare("benchbox.platforms.databricks.adapter", "DatabricksAdapter", table_format="delta")
    for label, optimize in (("auto_optimize", True), ("no_auto_optimize", False)):
        cases += _family(
            f"databricks_delta_{label}",
            lambda sql, optimize=optimize: _with(delta, optimize, False, _translated(sql, "databricks")),
            lambda sql, optimize=optimize: _fork(
                sql,
                "databricks",
                "databricks",
                or_replace=True,
                default_layout=_layout(format="delta", properties=properties if optimize else {}),
            ),
            inputs,
        )
    cases += _family(
        "databricks_delta_reset_in_place",
        lambda sql: _with(delta, True, True, _translated(sql, "databricks")),
        lambda sql: _fork(
            sql,
            "databricks",
            "databricks",
            or_replace=True,
            replace_if_not_exists=True,
            default_layout=_layout(format="delta", properties=properties),
        ),
        _select("if_not_exists", "tpch_orders"),
    )

    hudi = _bare(
        "benchbox.platforms.databricks.adapter",
        "DatabricksAdapter",
        table_format="hudi",
        delta_auto_optimize=True,
        hudi_table_type="cow",
        hudi_primary_key="o_orderkey",
        hudi_precombine_field="o_orderdate",
        _schema_reset_in_place=False,
    )

    def hudi_fork(sql: str) -> str:
        rendered = " ".join(hudi._hudi_table_properties(_translated(sql, "databricks")))
        properties = dict(re.findall(r"'([^']+)' = '([^']+)'", rendered))
        layout = _layout(format="hudi", properties=properties)
        return _fork(sql, "databricks", "databricks", or_replace=True, default_layout=layout)

    cases += _family(
        "databricks_hudi",
        lambda sql: hudi._convert_to_delta_table(_translated(sql, "databricks")),
        hudi_fork,
        _select("tpch_orders", "tpch_nation", "bare_varchar"),
    )
    return cases


def _with(adapter: Any, auto_optimize: bool, reset: bool, sql: str) -> str:
    adapter.delta_auto_optimize = auto_optimize
    adapter._schema_reset_in_place = reset
    return adapter._convert_to_delta_table(sql)


def _athena() -> list[Case]:
    cases: list[Case] = []
    base = {"s3_bucket": "bucket", "s3_prefix": "prefix", "database": "db"}
    variants = {
        "parquet": ({"data_format": "parquet", "default_format": "PARQUET"}, "PARQUET", None, None),
        "csv": ({"data_format": "csv", "default_format": "CSV"}, "TEXTFILE", ",", None),
        "other": ({"data_format": "tbl", "default_format": "OTHER"}, "TEXTFILE", "|", {"skip.header.line.count": "0"}),
    }
    for label, (attributes, stored_as, delimiter, props) in variants.items():
        adapter = _bare("benchbox.platforms.athena", "AthenaAdapter", **base, **attributes)

        def fork(sql: str, stored_as=stored_as, delimiter=delimiter, props=props) -> str:
            table = _name(sql)
            return _fork(
                sql,
                "athena",
                "trino",
                strip_primary_keys=True,
                strip_foreign_keys=True,
                strip_not_null=True,
                if_not_exists=True,
                table_case="lower",
                type_map={"VARCHAR": "STRING", "CHAR": "STRING"},
                drop_type_parameters=["STRING"],
                default_layout=_layout(
                    external=True,
                    stored_as=stored_as,
                    location=f"s3://bucket/prefix/db/{table}/",
                    field_delimiter=delimiter,
                    line_delimiter="\n" if delimiter else None,
                    properties=props or {},
                ),
            )

        cases += _family(
            f"athena_{label}",
            lambda sql, adapter=adapter: adapter._convert_to_external_table(_translated(sql, "trino", True)),
            fork,
            _inputs(),
        )

    adapter = _bare(
        "benchbox.platforms.athena",
        "AthenaAdapter",
        **base,
        data_format="parquet",
        default_format="PARQUET",
    )

    def staging_fork(sql: str) -> str:
        table = _name(sql)
        return _fork(
            sql,
            "athena",
            "trino",
            strip_primary_keys=True,
            strip_foreign_keys=True,
            strip_not_null=True,
            if_not_exists=True,
            table_case="lower",
            table_suffix="_staging",
            type_map={"VARCHAR": "STRING", "CHAR": "STRING"},
            drop_type_parameters=["STRING"],
            default_layout=_layout(
                external=True,
                stored_as="TEXTFILE",
                location=f"s3://bucket/prefix/db_staging/{table}/",
                field_delimiter="|",
                line_delimiter="\n",
            ),
        )

    cases += _family(
        "athena_staging",
        lambda sql: adapter._convert_to_external_table(_translated(sql, "trino", True), is_staging=True),
        staging_fork,
        _only(("tpch_",), {"pk_middle": _MISC["pk_middle"], "non_create": _MISC["non_create_insert"]}),
    )
    return cases


def _databend() -> list[Case]:
    adapter = _bare("benchbox.platforms.databend.adapter", "DatabendAdapter")
    return _family(
        "databend",
        lambda sql: adapter._optimize_table_definition(_translated(sql, "snowflake")),
        lambda sql: _fork(
            sql,
            "snowflake",
            "snowflake",
            strip_primary_keys=True,
            strip_foreign_keys=True,
            type_map={"CHAR": "VARCHAR"},
        ),
        _inputs(),
    )


def _trino() -> list[Case]:
    cases: list[Case] = []
    variants = (
        ("trino_hive", "benchbox.platforms.trino", "TrinoAdapter", "hive", "trino", "hive"),
        ("trino_iceberg", "benchbox.platforms.trino", "TrinoAdapter", "iceberg", "trino", "iceberg"),
        ("trino_memory", "benchbox.platforms.trino", "TrinoAdapter", "memory", "trino", "memory"),
        ("trino_delta", "benchbox.platforms.trino", "TrinoAdapter", "delta", "trino", "delta"),
        ("presto_hive", "benchbox.platforms.presto", "PrestoAdapter", "hive", "presto", "hive"),
        ("presto_iceberg", "benchbox.platforms.presto", "PrestoAdapter", "iceberg", "presto", "iceberg"),
    )
    for label, module, cls, table_format, write, _ in variants:
        adapter = _bare(module, cls, table_format=table_format, catalog=table_format)
        memory = table_format in adapter.ddl_memory_table_formats
        with_format = table_format in adapter.ddl_format_property_formats

        def fork(sql: str, write=write, memory=memory, with_format=with_format) -> str:
            return _fork(
                sql,
                write,
                write,
                strip_primary_keys=True,
                strip_not_null=memory,
                table_case="lower",
                default_layout=_layout(format="PARQUET") if with_format else None,
            )

        cases += _family(
            label,
            lambda sql, adapter=adapter, write=write: adapter._optimize_table_definition(_translated(sql, write, True)),
            fork,
            _inputs(),
        )
    return cases


def _starrocks_key_model(sql: str) -> str:
    sqlglot = importlib.import_module("benchbox._vendor.sqlglot")
    exp = sqlglot.exp
    tree = sqlglot.parse_one(sql, read=_READ)
    columns = [column.name.lower() for column in tree.find_all(exp.ColumnDef)]
    key = [
        identifier.name.lower()
        for primary in tree.find_all(exp.PrimaryKey)
        for identifier in primary.find_all(exp.Identifier)
    ]
    key += [
        column.name.lower()
        for column in tree.find_all(exp.ColumnDef)
        if any(
            isinstance(c.args.get("kind"), exp.PrimaryKeyColumnConstraint) for c in column.args.get("constraints") or []
        )
    ]
    leading = bool(key) and set(key) == set(columns[: len(set(key))])
    return "primary" if leading else "duplicate"


def _starrocks() -> list[Case]:
    adapter = _bare("benchbox.platforms.starrocks.adapter", "StarRocksAdapter")
    engines = importlib.import_module(_ENGINES)

    def fork(sql: str) -> str:
        first = _first_column(sql)
        model = _starrocks_key_model(sql)
        return _fork(
            sql,
            "starrocks",
            read="postgres",
            strip_foreign_keys=True,
            float_as_real=True,
            type_map=engines.STARROCKS_TYPE_MAP,
            default_layout=_layout(
                key_model=model,
                key_columns=[] if model == "primary" else [first],
                distribution="hash",
                distribute_by=[first],
                buckets=8,
            ),
        )

    return _family("starrocks", adapter._optimize_table_definition, fork, _inputs())


def _doris() -> list[Case]:
    cases: list[Case] = []
    engines = importlib.import_module(_ENGINES)
    doris = importlib.import_module("benchbox.platforms.doris")
    for label, partitioning in (("plain", False), ("partitioned", True)):
        adapter = _bare(
            "benchbox.platforms.doris",
            "DorisAdapter",
            table_model="duplicate",
            default_buckets=doris._DEFAULT_BUCKETS,
            enable_partitioning=partitioning,
            replication_num=1,
        )

        def fork(sql: str, adapter=adapter) -> str:
            table = _name(sql)
            layout = _layout(
                key_model="duplicate",
                key_columns=doris._TPCH_TABLE_KEYS.get(table, []),
                partition_clause=adapter.get_partition_clause(table) or None,
                distribution="hash",
                distribute_by=[doris._TPCH_DISTRIBUTION_KEYS[table]] if table in doris._TPCH_DISTRIBUTION_KEYS else [],
                buckets=adapter._compute_bucket_count(table, None),
                properties={"replication_num": "1", "storage_medium": "HDD"},
            )
            return _fork(
                sql,
                "doris",
                "doris",
                strip_foreign_keys=True,
                strip_primary_keys=True,
                type_map=engines.DORIS_TYPE_MAP,
                default_layout=layout,
            )

        cases += _family(
            f"doris_{label}",
            lambda sql, adapter=adapter: adapter._inject_doris_ddl_clauses(_translated(sql, "doris")),
            fork,
            _inputs(),
        )
    return cases


def _tsql() -> list[Case]:
    cases: list[Case] = []
    for distribution in ("ROUND_ROBIN", "REPLICATE"):
        adapter = _bare(
            "benchbox.platforms.azure_synapse",
            "AzureSynapseAdapter",
            schema="dbo",
            distribution_default=distribution,
        )
        cases += _family(
            f"synapse_{distribution.lower()}",
            lambda sql, adapter=adapter: adapter._optimize_table_definition(_translated(sql, "tsql")),
            lambda sql, distribution=distribution: _fork(
                sql, "tsql", "tsql", schema="dbo", default_layout=_layout(distribution=distribution.lower())
            ),
            _inputs(),
        )
    fabric = _bare("benchbox.platforms.fabric_warehouse", "FabricWarehouseAdapter", schema="dbo")
    cases += _family(
        "fabric",
        lambda sql: fabric._optimize_table_definition(_translated(sql, "tsql")),
        lambda sql: _fork(sql, "tsql", "tsql", schema="dbo"),
        _inputs(),
    )
    return cases


def _snowflake() -> list[Case]:
    adapter = _bare("benchbox.platforms.snowflake", "SnowflakeAdapter")
    inputs = {
        **_inputs(),
        "quoted_identifiers": (
            'CREATE TABLE "hits" ("WatchID" BIGINT NOT NULL, "note" VARCHAR DEFAULT \'keep "me" lower\')'
        ),
        "lowercase_create": "create table orders (id INT)",
        "comment_prefix": "-- Generated staging load tables\nCREATE TABLE orders_stage (id INT)",
    }
    return _family(
        "snowflake",
        lambda sql: adapter._optimize_table_definition(_translated(sql, "snowflake")),
        lambda sql: _fork(sql, "snowflake", "snowflake", or_replace=True),
        inputs,
    )


def _bigquery() -> list[Case]:
    adapter = _bare(
        "benchbox.platforms.bigquery",
        "BigQueryAdapter",
        project_id="proj",
        dataset_id="ds",
        partitioning_field=None,
        clustering_fields=[],
    )
    return _family(
        "bigquery",
        lambda sql: adapter._convert_to_bigquery_table(_translated(sql, "bigquery")),
        lambda sql: _fork(sql, "bigquery", "bigquery", or_replace=True, schema="proj.ds", table_case="upper"),
        _inputs(),
    )


def _singlestore() -> list[Case]:
    from benchbox.platforms import singlestore

    adapter = _bare("benchbox.platforms.singlestore", "SingleStoreAdapter")
    ddl = importlib.import_module("benchbox._vendor.sqlglot.benchbox.ddl")
    engines = importlib.import_module(_ENGINES)

    def fork(sql: str) -> str:
        keys = ddl.SingleStoreKeys(
            shard_keys=singlestore._TPCH_SHARD_KEYS,
            sort_keys=singlestore._TPCH_SORT_KEYS,
            reference_tables=singlestore._REFERENCE_TABLES,
        )
        return _fork(
            sql,
            "singlestore",
            "mysql",
            strip_foreign_keys=True,
            table_case="lower",
            float_as_real=True,
            type_map=engines.SINGLESTORE_TYPE_MAP,
            singlestore=keys,
        )

    def legacy(sql: str) -> str:
        return adapter._transform_create_statement(_translated(sql, "mysql"))

    return _family("singlestore", legacy, fork, _inputs())


def _mooncake() -> list[Case]:
    adapter = _bare("benchbox.platforms.pg_mooncake", "PgMooncakeAdapter")
    cases = _family(
        "mooncake_access_method",
        lambda sql: adapter._add_columnstore_access_method(_translated(sql, "postgres")),
        lambda sql: _fork(sql, "postgres", "postgres", default_layout=_layout(format="mooncake")),
        _inputs(),
    )

    def promote(schema: str, table: str) -> str:
        from unittest.mock import MagicMock

        adapter = _bare("benchbox.platforms.pg_mooncake", "PgMooncakeAdapter", schema=schema)
        adapter._validate_identifier = lambda name: True
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = (1,)
        adapter._promote_loaded_tables_to_mooncake(connection, [table], {table: 1})
        rendered = []
        for call in cursor.execute.call_args_list:
            statement, *params = call.args
            rendered.append(statement % tuple(f"'{value}'" for value in params[0]) if params else statement)
        return _squash("\n".join(rendered[:2]))

    def mirror(schema: str, table: str) -> str:
        engines = importlib.import_module(_ENGINES)
        adapter = _bare("benchbox.platforms.pg_mooncake", "PgMooncakeAdapter", schema=schema)
        staging = adapter._mooncake_staging_table_name(table)
        mirror_schema = None if schema == "public" else schema
        return _squash("\n".join(engines.mooncake_mirror_statements(table, staging, mirror_schema)))

    for schema in ("public", "bench"):
        for table in ("lineitem", "orders"):
            cases.append(
                Case(
                    f"mooncake_mirror:{schema}.{table}",
                    lambda schema=schema, table=table: promote(schema, table),
                    lambda schema=schema, table=table: mirror(schema, table),
                )
            )
    return cases


def _questdb() -> list[Case]:
    cases: list[Case] = []
    questdb = importlib.import_module("benchbox.platforms.questdb")
    for label, partition in (("default", None), ("day", "DAY"), ("none", "NONE")):
        adapter = _bare("benchbox.platforms.questdb", "QuestDBAdapter", partition_by=partition)

        def legacy(sql: str, adapter=adapter) -> str:
            from benchbox.platforms.base.ddl_helpers import strip_foreign_keys

            if "FOREIGN KEY" in sql.upper() or "REFERENCES" in sql.upper():
                sql = strip_foreign_keys(sql)
            if "PRIMARY KEY" in sql.upper():
                sql = adapter._strip_pk_constraints(sql)
            return adapter._apply_questdb_schema_enhancements(sql)

        def legacy_translated(sql: str, legacy=legacy) -> str:
            return legacy(_translated(sql, "postgres"))

        def fork(sql: str, adapter=adapter) -> str:
            table = _name(sql)
            designated = questdb.TPCH_TIMESTAMP_COLUMNS.get(table)
            if designated and not re.search(rf"\b{designated}\b", sql, re.IGNORECASE):
                designated = None
            layout = _layout(
                symbol_columns=questdb.TPCH_SYMBOL_COLUMNS.get(table, []),
                timestamp_columns=questdb.TPCH_DATE_COLUMNS.get(table, []),
                designated_timestamp=designated,
                time_partition=adapter._get_partition_for_table(table) if designated else None,
            )
            return _fork(
                sql,
                "questdb",
                "postgres",
                strip_primary_keys=True,
                strip_foreign_keys=True,
                default_layout=layout,
            )

        cases += _family(f"questdb_{label}", legacy_translated, fork, _inputs())
    return cases


def _strip_helpers() -> list[Case]:
    from benchbox.platforms.base.ddl_helpers import strip_foreign_keys, strip_primary_keys, strip_with_properties

    ducklake = _bare("benchbox.platforms.ducklake", "DuckLakeAdapter")
    inputs = {
        **_inputs(),
        "with_properties": "CREATE TABLE t (a INT) WITH (format = 'PARQUET')",
        "fk_actions": (
            "CREATE TABLE child (id INT, pid INT, FOREIGN KEY (pid) REFERENCES parent(id) "
            "ON DELETE CASCADE ON UPDATE NO ACTION)"
        ),
        "fk_named": "CREATE TABLE child (id INT, pid INT, CONSTRAINT fk FOREIGN KEY (pid) REFERENCES parent(id))",
        "inline_references": "CREATE TABLE child (id INT, pid INT REFERENCES parent(id))",
    }
    cases = _family(
        "postgresql_strip_foreign_keys",
        lambda sql: strip_foreign_keys(_translated(sql, "postgres")),
        lambda sql: _fork(sql, "postgres", "postgres", strip_foreign_keys=True),
        inputs,
    )
    cases += _family(
        "ducklake_strip_primary_keys",
        lambda sql: ducklake.ducklake_strip_primary_keys(_translated(sql, "duckdb")),
        lambda sql: _fork(sql, "duckdb", "duckdb", strip_primary_keys=True),
        inputs,
    )
    cases += _family(
        "helper_strip_primary_keys",
        strip_primary_keys,
        lambda sql: _fork(sql, "postgres", strip_primary_keys=True),
        inputs,
    )
    cases += _family(
        "helper_strip_with_properties",
        strip_with_properties,
        lambda sql: _fork(sql, "postgres", strip_with_properties=True),
        {"with_properties": inputs["with_properties"]},
    )
    return cases


def _spark_helper() -> list[Case]:
    from benchbox.platforms._spark_helpers import optimize_spark_table_definition

    inputs = {
        **_inputs(),
        "using_present": "CREATE TABLE t (a INT) USING PARQUET",
        "unique_inline": "CREATE TABLE t (a INT UNIQUE, b TEXT, UNIQUE (b))",
        "comment_prefix": "-- Generated\nCREATE TABLE t (a INT)",
    }
    cases: list[Case] = []
    variants = (
        ("spark_parquet", "parquet", True, True),
        ("spark_orc", "orc", True, True),
        ("spark_delta", "delta", False, False),
        ("lakesail_parquet", "parquet", True, True),
    )
    for label, table_format, v1_table, _ in variants:

        def legacy(sql: str, table_format=table_format, v1_table=v1_table) -> str:
            return optimize_spark_table_definition(
                _translated(sql, "spark"),
                table_format=table_format,
                strip_v1_constraints=v1_table,
                upcast_smallint=v1_table,
            )

        def fork(sql: str, table_format=table_format, v1_table=v1_table) -> str:
            options: dict[str, Any] = {"default_layout": _layout(format=table_format)}
            if v1_table:
                options.update(
                    strip_primary_keys=True,
                    strip_foreign_keys=True,
                    strip_unique=True,
                    type_map={"SMALLINT": "INT"},
                )
            return _fork(sql, "spark", "spark", **options)

        cases += _family(label, legacy, fork, inputs)
    return cases


def _spark_mixin() -> list[Case]:
    from benchbox.core.tuning.ddl_generator import ColumnDefinition
    from benchbox.platforms.base.cloud_spark.mixins import SparkDDLGeneratorMixin, SparkTableFormat

    def adapter_for(table_format: SparkTableFormat) -> Any:
        return type("MixinAdapter", (SparkDDLGeneratorMixin,), {"table_format": table_format})()

    columns = [
        ColumnDefinition("o_orderkey", "BIGINT"),
        ColumnDefinition("o_orderdate", "DATE"),
        ColumnDefinition("o_comment", "VARCHAR(79)"),
    ]
    source = "CREATE TABLE orders (o_orderkey BIGINT, o_orderdate DATE, o_comment VARCHAR(79))"
    lineitem_columns = [ColumnDefinition("l_orderkey", "BIGINT"), ColumnDefinition("l_shipdate", "DATE")]
    lineitem_source = "CREATE TABLE lineitem (l_orderkey BIGINT, l_shipdate DATE)"
    optimize = {"delta.autoOptimize.optimizeWrite": "true", "delta.autoOptimize.autoCompact": "true"}

    def tuning(**groups: list[tuple[str, str]]) -> TableTuning:
        return TableTuning(
            table_name="orders",
            **{
                key: [TuningColumn(name=name, type=kind, order=index) for index, (name, kind) in enumerate(cols, 1)]
                for key, cols in groups.items()
            },
        )

    specs = [
        ("delta_untuned", SparkTableFormat.DELTA, None, "spark", {"format": "delta", "properties": optimize}),
        (
            "delta_partition",
            SparkTableFormat.DELTA,
            tuning(partitioning=[("o_orderdate", "DATE")]),
            "spark",
            {"format": "delta", "partition_by": ["o_orderdate"], "properties": optimize},
        ),
        (
            "delta_cluster",
            SparkTableFormat.DELTA,
            tuning(clustering=[("o_orderkey", "BIGINT")]),
            "databricks",
            {"format": "delta", "cluster_by": ["o_orderkey"], "properties": optimize},
        ),
        (
            "delta_distribution_zorder",
            SparkTableFormat.DELTA,
            tuning(distribution=[("o_orderkey", "BIGINT")]),
            "databricks",
            {"format": "delta", "properties": optimize},
        ),
        (
            "iceberg_partition",
            SparkTableFormat.ICEBERG,
            tuning(partitioning=[("o_orderdate", "DATE")]),
            "spark",
            {"format": "iceberg", "partition_by": ["months(o_orderdate)"]},
        ),
        (
            "iceberg_sorted",
            SparkTableFormat.ICEBERG,
            tuning(sorting=[("o_orderkey", "BIGINT")]),
            "spark",
            {"format": "iceberg", "properties": {"write.sort-order": "o_orderkey ASC"}},
        ),
        (
            "parquet_untuned",
            SparkTableFormat.PARQUET,
            None,
            "spark",
            {"format": "parquet"},
        ),
        (
            "parquet_distribution",
            SparkTableFormat.PARQUET,
            tuning(distribution=[("o_orderkey", "BIGINT")]),
            "spark",
            {"format": "parquet", "distribute_by": ["o_orderkey"], "buckets": 32},
        ),
        (
            "parquet_partition_sort",
            SparkTableFormat.PARQUET,
            tuning(partitioning=[("o_orderdate", "DATE")], sorting=[("o_orderkey", "BIGINT")]),
            "spark",
            {"format": "parquet", "partition_by": ["o_orderdate"]},
        ),
        (
            "hive_untuned",
            SparkTableFormat.HIVE,
            None,
            "hive",
            {"stored_as": "PARQUET"},
        ),
        (
            "hive_partition",
            SparkTableFormat.HIVE,
            tuning(partitioning=[("o_orderdate", "DATE")]),
            "hive",
            {"stored_as": "PARQUET", "partition_by": ["o_orderdate"]},
        ),
        (
            "hive_bucketed",
            SparkTableFormat.HIVE,
            tuning(distribution=[("o_orderkey", "BIGINT")], sorting=[("o_orderdate", "DATE")]),
            "hive",
            {"stored_as": "PARQUET", "distribute_by": ["o_orderkey"], "sort_by": ["o_orderdate"], "buckets": 32},
        ),
    ]
    cases = []
    for label, table_format, table_tuning, write, layout in specs:
        adapter = adapter_for(table_format)

        def legacy(adapter=adapter, table_tuning=table_tuning) -> str:
            return _squash(
                adapter.generate_create_table_ddl("orders", columns, adapter.generate_tuning_clauses(table_tuning))
            )

        def fork(write=write, layout=layout) -> str:
            return _fork(source, write, default_layout=_layout(**layout))

        cases.append(Case(f"spark_mixin:{label}", legacy, fork))

    adapter = adapter_for(SparkTableFormat.DELTA)
    cases.append(
        Case(
            "spark_mixin:if_not_exists_schema",
            lambda: _squash(
                adapter.generate_create_table_ddl("lineitem", lineitem_columns, None, if_not_exists=True, schema="tpch")
            ),
            lambda: _fork(lineitem_source, "spark", if_not_exists=True, schema="tpch"),
        )
    )
    return cases


def cases() -> list[Case]:
    found: list[Case] = []
    for build in (
        _firebolt,
        _redshift,
        _clickhouse,
        _databricks,
        _athena,
        _databend,
        _trino,
        _starrocks,
        _doris,
        _tsql,
        _snowflake,
        _bigquery,
        _singlestore,
        _mooncake,
        _questdb,
        _strip_helpers,
        _spark_helper,
        _spark_mixin,
    ):
        found.extend(build())
    return found
