from __future__ import annotations

import logging
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from benchbox.platforms._spark_helpers import (
    SPARK_AQE_KEYS,
    SPARK_CBO_KEYS,
    SparkLikeAdapterMixin,
    apply_spark_olap_runtime_conf,
    get_spark_query_plan,
    optimize_spark_table_definition,
    purge_orphaned_warehouse_directory,
    run_spark_schema_creation_loop,
    spark_aqe_conf_entries,
    validate_spark_identifier,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestNormalPlanCapture:
    def test_calls_explain_extended(self) -> None:
        spark = MagicMock()
        df = MagicMock()
        df.collect.return_value = []
        spark.sql.return_value = df
        get_spark_query_plan(spark, "SELECT 1")
        spark.sql.assert_called_once_with("EXPLAIN EXTENDED SELECT 1")

    def test_joins_rows_with_newlines(self) -> None:
        spark = MagicMock()
        df = MagicMock()
        rows = [MagicMock(), MagicMock(), MagicMock()]
        rows[0].__getitem__ = lambda self, k: "line A"
        rows[1].__getitem__ = lambda self, k: "line B"
        rows[2].__getitem__ = lambda self, k: "line C"
        df.collect.return_value = rows
        spark.sql.return_value = df
        result = get_spark_query_plan(spark, "SELECT 1")
        assert result == "line A\nline B\nline C"

    def test_single_row_plan(self) -> None:
        spark = MagicMock()
        df = MagicMock()
        row = MagicMock()
        row.__getitem__ = lambda self, k: "== Physical Plan ==\nScan"
        df.collect.return_value = [row]
        spark.sql.return_value = df
        result = get_spark_query_plan(spark, "SELECT 1")
        assert "== Physical Plan ==" in result

    def test_empty_plan_returns_empty_string(self) -> None:
        spark = MagicMock()
        df = MagicMock()
        df.collect.return_value = []
        spark.sql.return_value = df
        result = get_spark_query_plan(spark, "SELECT 1")
        assert result == ""


class TestErrorHandling:
    def test_returns_none_on_exception(self) -> None:
        spark = MagicMock()
        spark.sql.side_effect = RuntimeError("session closed")
        result = get_spark_query_plan(spark, "SELECT 1")
        assert result is None

    def test_returns_none_on_attribute_error(self) -> None:
        result = get_spark_query_plan(None, "SELECT 1")
        assert result is None

    def test_returns_none_on_collect_failure(self) -> None:
        spark = MagicMock()
        df = MagicMock()
        df.collect.side_effect = ConnectionError("lost connection")
        spark.sql.return_value = df
        result = get_spark_query_plan(spark, "SELECT 1")
        assert result is None

    def test_failure_logs_warning(self, caplog) -> None:
        import logging

        spark = MagicMock()
        spark.sql.side_effect = ValueError("bad query")
        with caplog.at_level(logging.WARNING):
            result = get_spark_query_plan(spark, "SELECT X")
        assert result is None
        assert any("Could not get query plan" in message for message in caplog.messages)

    def test_custom_logger_receives_warning(self) -> None:
        import logging

        spark = MagicMock()
        spark.sql.side_effect = ValueError("bad query")
        records: list = []

        class Sink(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        custom = logging.getLogger("test.qpc13.custom")
        custom.addHandler(Sink())
        custom.setLevel(logging.WARNING)
        try:
            assert get_spark_query_plan(spark, "SELECT X", logger=custom) is None
        finally:
            custom.handlers.clear()
        assert any("Could not get query plan" in message for message in records)


class TestValidateSparkIdentifier:
    def test_accepts_simple_names(self) -> None:
        assert validate_spark_identifier("orders") is True
        assert validate_spark_identifier("_private") is True
        assert validate_spark_identifier("Table123") is True

    def test_rejects_empty_and_non_string(self) -> None:
        assert validate_spark_identifier("") is False
        assert validate_spark_identifier(None) is False
        assert validate_spark_identifier(42) is False

    def test_rejects_injection_payloads(self) -> None:
        assert validate_spark_identifier("a-b") is False
        assert validate_spark_identifier("a; DROP TABLE t") is False
        assert validate_spark_identifier("123starts_with_digit") is False

    def test_enforces_128_char_limit(self) -> None:
        assert validate_spark_identifier("a" * 128) is True
        assert validate_spark_identifier("a" * 129) is False


class TestOptimizeSparkTableDefinition:
    def test_appends_using_parquet(self) -> None:
        result = optimize_spark_table_definition("CREATE TABLE t (id INT)", table_format="parquet")
        assert result.endswith("USING PARQUET")

    def test_appends_using_orc(self) -> None:
        result = optimize_spark_table_definition("CREATE TABLE t (id INT)", table_format="orc")
        assert result.endswith("USING ORC")

    def test_strips_existing_using_clause(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE t (id INT) USING DELTA",
            table_format="parquet",
        )
        assert "DELTA" not in result.upper()
        assert result.endswith("USING PARQUET")

    @pytest.mark.parametrize("clause", ["PARTITIONED BY (id)", "CLUSTER BY (id)"])
    def test_using_precedes_trailing_tuning_clause(self, clause: str) -> None:
        result = optimize_spark_table_definition(
            f"CREATE TABLE t (id INT, amount DECIMAL(10,2))\n{clause};",
            table_format="delta",
        )
        assert result == f"CREATE TABLE t (id INT, amount DECIMAL(10,2)) USING DELTA\n{clause}"

    @pytest.mark.parametrize(
        ("columns", "name"),
        [
            ("id INT, note STRING DEFAULT ' ) '", "t"),
            ("id INT, note STRING DEFAULT 'it''s ) here'", "t"),
            ("id INT, note STRING DEFAULT 'a\\' ) b'", "t"),
            ("id INT, `we ) ird` STRING", "t"),
            ("id INT, amount DECIMAL(10,2), tags MAP<STRING, ARRAY<INT>>", "`my table`"),
        ],
    )
    def test_using_stays_outside_column_list_with_quoted_parentheses(self, columns: str, name: str) -> None:
        result = optimize_spark_table_definition(
            f"CREATE TABLE {name} ({columns}) PARTITIONED BY (id);",
            table_format="delta",
        )
        assert f"({columns})" in result
        assert result.count("USING DELTA") == 1
        assert result.index(f"({columns})") + len(f"({columns})") < result.index("USING DELTA")
        assert result.index("USING DELTA") < result.index("PARTITIONED BY")

    @pytest.mark.parametrize(
        "columns",
        [
            "id INT /* ) */, amount INT",
            "id INT /* ( unbalanced */, amount INT",
            "id INT -- )\n, amount INT",
            "id INT -- ( unbalanced\n, amount INT",
        ],
    )
    def test_using_stays_outside_column_list_with_comment_parentheses(self, columns: str) -> None:
        result = optimize_spark_table_definition(
            f"CREATE TABLE t ({columns}) PARTITIONED BY (id);",
            table_format="delta",
        )
        assert f"({columns})" in result
        assert result.count("USING DELTA") == 1
        assert result.index(f"({columns})") + len(f"({columns})") < result.index("USING DELTA")
        assert result.index("USING DELTA") < result.index("PARTITIONED BY")

    def test_non_create_table_unchanged(self) -> None:
        sql = "INSERT INTO t VALUES (1)"
        assert optimize_spark_table_definition(sql, table_format="parquet") == sql

    def test_strip_v1_constraints_removes_inline_pk(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE t (id INT PRIMARY KEY, val STRING)",
            table_format="parquet",
        )
        assert "PRIMARY KEY" not in result.upper()
        assert "id INT" in result
        assert "val STRING" in result

    def test_strip_v1_constraints_removes_table_level_pk(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE t (id INT, val STRING, PRIMARY KEY (id))",
            table_format="parquet",
        )
        assert "PRIMARY KEY" not in result.upper()

    def test_strip_v1_constraints_removes_foreign_key(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE t (id INT, ref_id INT, FOREIGN KEY (ref_id) REFERENCES other(id))",
            table_format="parquet",
        )
        assert "FOREIGN KEY" not in result.upper()
        assert "REFERENCES" not in result.upper()

    def test_strip_v1_constraints_removes_unique_and_check(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE t (id INT, val INT, UNIQUE (id), CHECK (val > 0))",
            table_format="parquet",
        )
        assert "UNIQUE" not in result.upper()
        assert "CHECK" not in result.upper()

    def test_upcast_smallint_to_int(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE t (id INT, count SMALLINT)",
            table_format="parquet",
            upcast_smallint=True,
        )
        assert "SMALLINT" not in result.upper()
        assert "INT" in result.upper()

    def test_v2_format_preserves_constraints(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE t (id INT PRIMARY KEY)",
            table_format="delta",
            strip_v1_constraints=False,
        )
        assert "PRIMARY KEY" in result.upper()
        assert "USING DELTA" in result.upper()

    def test_v2_format_preserves_smallint(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE t (count SMALLINT)",
            table_format="iceberg",
            upcast_smallint=False,
        )
        assert "SMALLINT" in result.upper()

    def test_strip_constraints_with_nested_paren_check(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE t (a INT, b INT, CHECK ((a > 0) AND (b > 0)))",
            table_format="parquet",
        )
        assert "CHECK" not in result.upper()
        assert "AND" not in result.upper()
        assert result.count("(") == result.count(")")
        assert result.endswith("USING PARQUET")
        assert "a INT" in result
        assert "b INT" in result

    def test_strip_constraints_with_multi_column_foreign_key(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE t (a INT, b INT, c INT, FOREIGN KEY (a, b) REFERENCES other(c, d))",
            table_format="parquet",
        )
        assert "FOREIGN KEY" not in result.upper()
        assert "REFERENCES" not in result.upper()
        assert result.count("(") == result.count(")")
        assert "a INT" in result
        assert "c INT" in result

    def test_strip_v1_constraints_removes_inline_unique(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE t (id INT, email STRING UNIQUE)",
            table_format="parquet",
        )
        assert "UNIQUE" not in result.upper()
        assert "email STRING" in result

    def test_strip_v1_constraints_preserves_table_unique_clause_then_strips(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE t (id INT, email STRING UNIQUE, UNIQUE (id))",
            table_format="parquet",
        )
        assert "UNIQUE" not in result.upper()
        assert result.count("(") == result.count(")")
        assert "id INT" in result
        assert "email STRING" in result

    def test_repeated_space_collapse_preserves_newlines(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE t (\n    a INT,\n    b INT,\n    PRIMARY KEY (a)\n)",
            table_format="parquet",
        )
        assert "\n" in result
        assert "PRIMARY KEY" not in result.upper()

    def test_table_level_pk_after_trailing_comma_leaves_no_dangling_paren(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE taxi_zones (\n    location_id INTEGER,\n    zone VARCHAR,\n    PRIMARY KEY (location_id)\n)",
            table_format="parquet",
        )
        assert "PRIMARY KEY" not in result.upper()
        assert "(location_id))" not in result.replace(" ", "")
        assert result.count("(") == result.count(")")
        assert result.endswith("USING PARQUET")
        assert "location_id INTEGER" in result
        assert "zone VARCHAR" in result

    def test_strips_leading_line_comments_so_create_table_is_detected(self) -> None:
        result = optimize_spark_table_definition(
            "-- Benchmark schema\n-- second comment line\n\n"
            "CREATE TABLE region (\n    r_regionkey INTEGER NOT NULL,\n"
            "    r_name CHAR(25) NOT NULL,\n    PRIMARY KEY (r_regionkey)\n)",
            table_format="parquet",
        )
        assert result.upper().startswith("CREATE TABLE")
        assert "PRIMARY KEY" not in result.upper()
        assert "--" not in result
        assert result.endswith("USING PARQUET")

    def test_strips_leading_block_comments(self) -> None:
        result = optimize_spark_table_definition(
            "/* Benchmark Schema */ /* second banner */ "
            "CREATE TABLE `region` (`r_regionkey` INT NOT NULL, "
            "`r_comment` STRING, PRIMARY KEY (`r_regionkey`))",
            table_format="parquet",
        )
        assert result.upper().startswith("CREATE TABLE")
        assert "PRIMARY KEY" not in result.upper()
        assert "/*" not in result
        assert result.endswith("USING PARQUET")

    def test_comment_only_chunk_returns_empty(self) -> None:
        assert optimize_spark_table_definition("-- just a comment\n", table_format="parquet") == ""
        assert optimize_spark_table_definition("/* only a block comment */", table_format="parquet") == ""
        assert optimize_spark_table_definition("   \n  ", table_format="parquet") == ""

    def test_fixed_size_array_type_rewritten_to_array_element_type(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE vectors (id BIGINT, embedding FLOAT[128], doc_id VARCHAR(100))",
            table_format="parquet",
        )
        assert "ARRAY<FLOAT>" in result.upper()
        assert "[128]" not in result
        assert "embedding ARRAY<FLOAT>" in result

    def test_array_type_with_transpiled_fixed_size_suffix_normalised(self) -> None:
        result = optimize_spark_table_definition(
            "CREATE TABLE vectors (id BIGINT, embedding ARRAY<FLOAT>[128])",
            table_format="parquet",
        )
        assert "[128]" not in result
        assert "ARRAY<FLOAT>" in result.upper()
        assert result.endswith("USING PARQUET")


def _purge_stub(*, list_tables_return=None, list_tables_raises=None, current_db="default") -> MagicMock:
    spark = MagicMock()
    if list_tables_raises is not None:
        spark.catalog.listTables.side_effect = list_tables_raises
    else:
        spark.catalog.listTables.return_value = list_tables_return or []
    rows_mock = MagicMock()
    rows_mock.collect.return_value = [(current_db,)]
    spark.sql.return_value = rows_mock
    return spark


class TestPurgeOrphanedWarehouseDirectory:
    def test_noop_when_catalog_has_tables(self) -> None:
        existing_table = MagicMock()
        existing_table.name = "orders"
        spark = _purge_stub(list_tables_return=[existing_table])

        purge_orphaned_warehouse_directory(spark, logger=logging.getLogger(__name__))

        spark.catalog.listTables.assert_called_once()
        for call in spark.sql.call_args_list:
            sql_text = str(call.args[0]) if call.args else ""
            assert "DROP DATABASE" not in sql_text.upper()

    def test_drops_and_recreates_when_catalog_empty(self) -> None:
        spark = _purge_stub(list_tables_return=[], current_db="benchbox_run")

        purge_orphaned_warehouse_directory(spark, logger=logging.getLogger(__name__))

        executed = [str(call.args[0]) for call in spark.sql.call_args_list if call.args]
        assert any("DROP DATABASE IF EXISTS `benchbox_run` CASCADE" in s for s in executed)
        assert any("CREATE DATABASE IF NOT EXISTS `benchbox_run`" in s for s in executed)
        assert any("USE `benchbox_run`" in s for s in executed)

    def test_skips_when_catalog_probe_raises(self) -> None:
        spark = _purge_stub(list_tables_raises=RuntimeError("transient gRPC error"))

        purge_orphaned_warehouse_directory(spark, logger=logging.getLogger(__name__))

        for call in spark.sql.call_args_list:
            sql_text = str(call.args[0]) if call.args else ""
            assert "DROP DATABASE" not in sql_text.upper()

    def test_skips_when_current_database_invalid(self) -> None:
        spark = MagicMock()
        spark.catalog.listTables.return_value = []
        rows_mock = MagicMock()
        rows_mock.collect.return_value = [("bad; DROP TABLE foo",)]
        spark.sql.return_value = rows_mock

        purge_orphaned_warehouse_directory(spark, logger=logging.getLogger(__name__))

        for call in spark.sql.call_args_list:
            sql_text = str(call.args[0]) if call.args else ""
            assert "DROP DATABASE" not in sql_text.upper()

    def test_logs_at_info_when_purge_fires(self, caplog) -> None:
        spark = _purge_stub(list_tables_return=[], current_db="benchbox_run")
        with caplog.at_level(logging.INFO, logger=__name__):
            purge_orphaned_warehouse_directory(spark, logger=logging.getLogger(__name__))

        info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("Purged potentially-orphaned warehouse" in m for m in info_messages)

    def test_swallows_drop_failures(self) -> None:
        spark = MagicMock()
        spark.catalog.listTables.return_value = []
        rows_mock = MagicMock()
        rows_mock.collect.return_value = [("benchbox_run",)]

        def sql_side_effect(query):
            if "DROP DATABASE" in query.upper():
                raise RuntimeError("boom")
            return rows_mock

        spark.sql.side_effect = sql_side_effect

        purge_orphaned_warehouse_directory(spark, logger=logging.getLogger(__name__))


class TestRunSparkSchemaCreationLoop:
    def test_happy_path_executes_each_statement(self) -> None:
        spark = MagicMock()
        statements = [
            "CREATE TABLE orders (id INT)",
            "CREATE TABLE customers (id INT)",
        ]

        run_spark_schema_creation_loop(
            spark,
            statements,
            lambda s: optimize_spark_table_definition(s, table_format="parquet"),
            logger=logging.getLogger(__name__),
        )

        assert spark.sql.call_count == 2
        assert all("USING PARQUET" in str(call.args[0]).upper() for call in spark.sql.call_args_list)

    def test_pre_loop_hook_runs_once_before_statements(self) -> None:
        spark = MagicMock()
        order: list[str] = []
        spark.sql.side_effect = lambda q: (order.append("sql"), MagicMock())[1]

        def pre_loop(_s) -> None:
            order.append("pre_loop")

        run_spark_schema_creation_loop(
            spark,
            ["CREATE TABLE t (id INT)"],
            lambda s: s + " USING PARQUET",
            logger=logging.getLogger(__name__),
            on_pre_loop=pre_loop,
        )

        assert order == ["pre_loop", "sql"]

    def test_already_exists_triggers_drop_then_retry(self) -> None:
        spark = MagicMock()
        calls: list[str] = []

        def sql_side_effect(query):
            calls.append(query)
            if calls.count(query) == 1 and query.startswith("CREATE TABLE"):
                raise RuntimeError("Table or view already exists: orders")
            return MagicMock()

        spark.sql.side_effect = sql_side_effect

        run_spark_schema_creation_loop(
            spark,
            ["CREATE TABLE orders (id INT)"],
            lambda s: s,
            logger=logging.getLogger(__name__),
        )

        assert any(c.startswith("CREATE TABLE orders") for c in calls)
        assert any(c.startswith("DROP TABLE IF EXISTS") for c in calls)
        assert sum(1 for c in calls if c.startswith("CREATE TABLE orders")) == 2

    def test_already_exists_with_backtick_identifier_drops_unquoted_safe_name(self) -> None:
        spark = MagicMock()
        calls: list[str] = []

        def sql_side_effect(query):
            calls.append(query)
            if calls.count(query) == 1 and query.startswith("CREATE TABLE"):
                raise RuntimeError("[LOCATION_ALREADY_EXISTS] The location for table 'region' already exists.")
            return MagicMock()

        spark.sql.side_effect = sql_side_effect

        run_spark_schema_creation_loop(
            spark,
            ["CREATE TABLE `region` (`r_regionkey` INT)"],
            lambda s: s,
            logger=logging.getLogger(__name__),
        )

        assert "DROP TABLE IF EXISTS region" in calls
        assert sum(1 for c in calls if c.startswith("CREATE TABLE `region`")) == 2

    def test_backtick_quoted_dotted_identifier_still_fails_validation(self) -> None:
        spark = MagicMock()
        spark.sql.side_effect = RuntimeError("Table already exists")

        with pytest.raises(RuntimeError, match="strict-ASCII identifier validation"):
            run_spark_schema_creation_loop(
                spark,
                ["CREATE TABLE `my_schema.tbl` (id INT)"],
                lambda s: s,
                logger=logging.getLogger(__name__),
            )

    def test_location_collision_hook_fires_on_location_already_exists(self) -> None:
        spark = MagicMock()
        collision_calls: list[tuple] = []
        first_call = {"flag": True}

        def sql_side_effect(query):
            if first_call["flag"] and query.startswith("CREATE TABLE"):
                first_call["flag"] = False
                raise RuntimeError("[LOCATION_ALREADY_EXISTS] The location for table 'orders' already exists.")
            return MagicMock()

        spark.sql.side_effect = sql_side_effect

        def on_collision(s, table_name) -> None:
            collision_calls.append((s, table_name))

        run_spark_schema_creation_loop(
            spark,
            ["CREATE TABLE orders (id INT)"],
            lambda s: s,
            logger=logging.getLogger(__name__),
            on_location_collision=on_collision,
        )

        assert len(collision_calls) == 1
        assert collision_calls[0][0] is spark
        assert collision_calls[0][1] == "orders"

    def test_location_collision_hook_skipped_for_plain_already_exists(self) -> None:
        spark = MagicMock()
        collision_calls: list[tuple] = []
        first_call = {"flag": True}

        def sql_side_effect(query):
            if first_call["flag"] and query.startswith("CREATE TABLE"):
                first_call["flag"] = False
                raise RuntimeError("Table or view already exists: orders")
            return MagicMock()

        spark.sql.side_effect = sql_side_effect

        def on_collision(s, table_name) -> None:
            collision_calls.append((s, table_name))

        run_spark_schema_creation_loop(
            spark,
            ["CREATE TABLE orders (id INT)"],
            lambda s: s,
            logger=logging.getLogger(__name__),
            on_location_collision=on_collision,
        )

        assert collision_calls == []

    def test_raises_when_extract_returns_none(self) -> None:
        spark = MagicMock()
        spark.sql.side_effect = RuntimeError("View already exists")

        with pytest.raises(RuntimeError, match="strict-ASCII identifier validation"):
            run_spark_schema_creation_loop(
                spark,
                ["ALTER VIEW v AS SELECT 1"],
                lambda s: s,
                logger=logging.getLogger(__name__),
            )

    def test_raises_when_extracted_name_fails_validation(self) -> None:
        spark = MagicMock()
        spark.sql.side_effect = RuntimeError("Table already exists")

        with pytest.raises(RuntimeError, match="strict-ASCII identifier validation"):
            run_spark_schema_creation_loop(
                spark,
                ["CREATE TABLE my_schema.tbl (id INT)"],
                lambda s: s,
                logger=logging.getLogger(__name__),
            )

    def test_raises_for_non_already_exists_errors(self) -> None:
        spark = MagicMock()
        spark.sql.side_effect = RuntimeError("syntax error near '('")

        with pytest.raises(RuntimeError, match="syntax error"):
            run_spark_schema_creation_loop(
                spark,
                ["CREATE TABLE t (id INT)"],
                lambda s: s,
                logger=logging.getLogger(__name__),
            )

    def test_skips_empty_statements(self) -> None:
        spark = MagicMock()
        run_spark_schema_creation_loop(
            spark,
            ["", "  ", "CREATE TABLE t (id INT)"],
            lambda s: s,
            logger=logging.getLogger(__name__),
        )
        assert spark.sql.call_count == 1


@dataclass
class _DummyTuningRecord:
    enabled: bool


@dataclass
class _DummyPlatformConfig:
    spark: dict | None


class _DummyUnifiedConfig:
    def __init__(
        self,
        *,
        primary_keys=None,
        foreign_keys=None,
        platform_optimizations=None,
        table_tunings=None,
    ) -> None:
        self.primary_keys = primary_keys
        self.foreign_keys = foreign_keys
        self.platform_optimizations = platform_optimizations
        self.table_tunings = table_tunings or {}


class _StubAdapter(SparkLikeAdapterMixin):
    def __init__(self, name: str = "TestPlatform") -> None:
        self.platform_name = name
        self.logger = logging.getLogger(__name__)
        self.table_tuning_calls: list = []

    def apply_table_tunings(self, table_tuning, connection) -> None:
        self.table_tuning_calls.append((table_tuning, connection))


class TestSparkLikeAdapterMixin:
    def test_apply_constraint_configuration_logs_platform_name(self, caplog) -> None:
        adapter = _StubAdapter("LakeSail")
        with caplog.at_level(logging.INFO, logger=__name__):
            adapter.apply_constraint_configuration(_DummyTuningRecord(True), _DummyTuningRecord(True), connection=None)
        joined = "\n".join(r.message for r in caplog.records)
        assert "Primary key constraints enabled for LakeSail" in joined
        assert "Foreign key constraints enabled for LakeSail" in joined

    def test_apply_constraint_configuration_silent_when_disabled(self, caplog) -> None:
        adapter = _StubAdapter("Velox")
        with caplog.at_level(logging.INFO, logger=__name__):
            adapter.apply_constraint_configuration(
                _DummyTuningRecord(False), _DummyTuningRecord(False), connection=None
            )
        assert not any("constraints enabled" in r.message for r in caplog.records)

    def test_apply_platform_optimizations_forwards_to_spark_conf(self) -> None:
        adapter = _StubAdapter("Spark")
        spark = MagicMock()
        adapter.apply_platform_optimizations(
            _DummyPlatformConfig({"sql.adaptive.enabled": "true", "sql.cbo.enabled": "true"}),
            connection=spark,
        )
        spark.conf.set.assert_any_call("spark.sql.adaptive.enabled", "true")
        spark.conf.set.assert_any_call("spark.sql.cbo.enabled", "true")

    def test_apply_platform_optimizations_logs_warning_on_failure(self, caplog) -> None:
        adapter = _StubAdapter("Spark")
        spark = MagicMock()
        spark.conf.set.side_effect = RuntimeError("rejected")
        with caplog.at_level(logging.WARNING, logger=__name__):
            adapter.apply_platform_optimizations(_DummyPlatformConfig({"foo.bar": "baz"}), connection=spark)
        warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Failed to apply Spark config spark.foo.bar" in m for m in warnings)

    def test_platform_optimizations_recorded_in_applied_ledger(self) -> None:
        from benchbox.core.tuning.applied_ledger import AppliedTuningLedger

        adapter = _StubAdapter("Spark")
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        spark = MagicMock()
        adapter.apply_platform_optimizations(
            _DummyPlatformConfig({"sql.shuffle.partitions": "200", "sql.cbo.enabled": "true"}),
            connection=spark,
        )
        statements = adapter._applied_tuning_ledger.statements
        assert {s.statement for s in statements} == {
            "SET spark.sql.shuffle.partitions=200",
            "SET spark.sql.cbo.enabled=true",
        }
        assert all(s.phase == "session" and s.mechanism == "spark_session_config" for s in statements)
        assert (
            adapter._applied_tuning_ledger.overall_status(tuning_enabled=True, has_config=True) == "applied_unverified"
        )

    def test_failed_conf_set_recorded_as_failed_statement(self) -> None:
        from benchbox.core.tuning.applied_ledger import EXECUTED, STATEMENT_FAILED, AppliedTuningLedger

        adapter = _StubAdapter("Spark")
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        spark = MagicMock()

        def _set(key, _value):
            if key == "spark.sql.shuffle.partitions":
                raise RuntimeError("rejected")

        spark.conf.set.side_effect = _set
        adapter.apply_platform_optimizations(
            _DummyPlatformConfig({"sql.shuffle.partitions": "200", "sql.cbo.enabled": "true"}),
            connection=spark,
        )
        by_stmt = {s.statement: s.status for s in adapter._applied_tuning_ledger.statements}
        assert by_stmt["SET spark.sql.shuffle.partitions=200"] == STATEMENT_FAILED
        assert by_stmt["SET spark.sql.cbo.enabled=true"] == EXECUTED

    def test_platform_optimizations_without_ledger_does_not_raise(self) -> None:
        adapter = _StubAdapter("Spark")
        spark = MagicMock()
        adapter.apply_platform_optimizations(_DummyPlatformConfig({"sql.cbo.enabled": "true"}), connection=spark)
        spark.conf.set.assert_called_with("spark.sql.cbo.enabled", "true")

    def test_apply_unified_tuning_dispatches_in_fixed_order(self) -> None:
        adapter = _StubAdapter("LakeSail")
        spark = MagicMock()
        config = _DummyUnifiedConfig(
            primary_keys=_DummyTuningRecord(True),
            foreign_keys=_DummyTuningRecord(True),
            platform_optimizations=_DummyPlatformConfig({"sql.adaptive.enabled": "true"}),
            table_tunings={"orders": "TUNING_A", "customer": "TUNING_B"},
        )
        adapter.apply_unified_tuning(config, connection=spark)
        recorded = [tuning for tuning, _ in adapter.table_tuning_calls]
        assert "TUNING_A" in recorded
        assert "TUNING_B" in recorded
        spark.conf.set.assert_called_with("spark.sql.adaptive.enabled", "true")

    def test_apply_unified_tuning_returns_early_on_none(self) -> None:
        adapter = _StubAdapter("Velox")
        adapter.apply_unified_tuning(None, connection=MagicMock())
        assert adapter.table_tuning_calls == []


class TestSparkAqeSharedKeys:
    def test_aqe_keys_cover_all_three_toggles(self) -> None:
        assert set(SPARK_AQE_KEYS) == {
            "spark.sql.adaptive.enabled",
            "spark.sql.adaptive.coalescePartitions.enabled",
            "spark.sql.adaptive.skewJoin.enabled",
        }

    def test_cbo_keys_cover_both_toggles(self) -> None:
        assert set(SPARK_CBO_KEYS) == {
            "spark.sql.cbo.enabled",
            "spark.sql.cbo.joinReorder.enabled",
        }

    def test_entries_enabled_sets_all_true(self) -> None:
        entries = spark_aqe_conf_entries(True)
        assert entries == dict.fromkeys(SPARK_AQE_KEYS, "true")

    def test_entries_disabled_sets_all_false(self) -> None:
        entries = spark_aqe_conf_entries(False)
        assert entries == dict.fromkeys(SPARK_AQE_KEYS, "false")


class TestApplySparkOlapRuntimeConf:
    def test_disabled_sets_all_aqe_keys_false(self) -> None:
        spark = MagicMock()
        apply_spark_olap_runtime_conf(spark, "tpch", adaptive_enabled=False)

        for key in SPARK_AQE_KEYS:
            spark.conf.set.assert_any_call(key, "false")

    def test_enabled_sets_all_aqe_keys_true(self) -> None:
        spark = MagicMock()
        apply_spark_olap_runtime_conf(spark, "olap", adaptive_enabled=True)

        for key in SPARK_AQE_KEYS:
            spark.conf.set.assert_any_call(key, "true")

    def test_non_olap_type_sets_nothing(self) -> None:
        spark = MagicMock()
        apply_spark_olap_runtime_conf(spark, "ssb", adaptive_enabled=True)

        spark.conf.set.assert_not_called()

    def test_custom_benchmark_types_are_honored(self) -> None:
        spark = MagicMock()
        apply_spark_olap_runtime_conf(spark, "joinorder", ("joinorder",), adaptive_enabled=True)

        spark.conf.set.assert_any_call("spark.sql.adaptive.enabled", "true")

    def test_spark_config_override_wins(self) -> None:
        spark = MagicMock()
        apply_spark_olap_runtime_conf(
            spark,
            "tpch",
            adaptive_enabled=True,
            spark_config={"spark.sql.adaptive.enabled": "false", "spark.sql.cbo.enabled": "false"},
        )

        spark.conf.set.assert_any_call("spark.sql.adaptive.enabled", "false")
        spark.conf.set.assert_any_call("spark.sql.adaptive.skewJoin.enabled", "true")
        spark.conf.set.assert_any_call("spark.sql.cbo.enabled", "false")
        spark.conf.set.assert_any_call("spark.sql.cbo.joinReorder.enabled", "true")

    def test_conf_failure_is_logged_not_raised(self) -> None:
        spark = MagicMock()
        spark.conf.set.side_effect = RuntimeError("conf error")

        apply_spark_olap_runtime_conf(spark, "tpch", adaptive_enabled=True)


class TestAdaptiveEnabledFromConfig:
    def test_top_level_bool_wins(self) -> None:
        from benchbox.platforms._spark_helpers import adaptive_enabled_from_config

        assert adaptive_enabled_from_config({"adaptive_enabled": False}) is False
        assert adaptive_enabled_from_config({"adaptive_enabled": True}) is True

    def test_nested_options_fallback(self) -> None:
        from benchbox.platforms._spark_helpers import adaptive_enabled_from_config

        assert adaptive_enabled_from_config({"options": {"adaptive_enabled": False}}) is False
        assert adaptive_enabled_from_config({"options": {"adaptive_enabled": True}}) is True

    def test_top_level_beats_nested(self) -> None:
        from benchbox.platforms._spark_helpers import adaptive_enabled_from_config

        config = {"adaptive_enabled": True, "options": {"adaptive_enabled": False}}
        assert adaptive_enabled_from_config(config) is True

    def test_defaults_on(self) -> None:
        from benchbox.platforms._spark_helpers import adaptive_enabled_from_config

        assert adaptive_enabled_from_config({}) is True
        assert adaptive_enabled_from_config({"options": {}}) is True

    def test_option_parser_strings(self) -> None:
        from benchbox.platforms._spark_helpers import adaptive_enabled_from_config

        assert adaptive_enabled_from_config({"options": {"adaptive_enabled": "false"}}) is False
        assert adaptive_enabled_from_config({"options": {"adaptive_enabled": "0"}}) is False
        assert adaptive_enabled_from_config({"adaptive_enabled": "yes"}) is True
