from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from benchbox.platforms._spark_helpers import (
    SPARK_AQE_KEYS,
    SparkLikeAdapterMixin,
    default_spark_driver_memory,
    get_spark_query_plan,
    purge_orphaned_warehouse_directory,
    run_spark_schema_creation_loop,
    spark_aqe_conf_entries,
    spark_format_strips_constraints,
    spark_schema_statements,
    spark_session_tuning_conf_entries,
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


class TestSparkSchemaStatements:
    def _statements(self, sql: str, table_format: str = "parquet", strip: bool = True) -> list[str]:
        return spark_schema_statements(sql, table_format=table_format, strip_constraints=strip)

    @pytest.mark.parametrize("table_format", ["parquet", "orc", "delta", "iceberg"])
    def test_writes_using_clause_for_format(self, table_format: str) -> None:
        (result,) = self._statements("CREATE TABLE t (id INT)", table_format)
        assert result.endswith(f"USING {table_format.upper()}")

    def test_existing_using_clause_is_replaced(self) -> None:
        (result,) = self._statements("CREATE TABLE t (id INT) USING DELTA")
        assert "DELTA" not in result.upper()
        assert result.endswith("USING PARQUET")

    def test_using_precedes_partitioned_by(self) -> None:
        (result,) = self._statements("CREATE TABLE t (id INT, d DATE)\nPARTITIONED BY (d)", "delta", strip=False)
        assert result == "CREATE TABLE `t` (`id` INT, `d` DATE) USING DELTA PARTITIONED BY (`d`)"

    def test_using_precedes_cluster_by(self) -> None:
        (result,) = self._statements("CREATE TABLE t (id INT, k INT)\nCLUSTER BY (k)", "delta", strip=False)
        assert result == "CREATE TABLE `t` (`id` INT, `k` INT) USING DELTA CLUSTER BY (`k`)"

    def test_untuned_tables_next_to_partitioned_tables_keep_default_format(self) -> None:
        sql = "CREATE TABLE a (id INT, d DATE) PARTITIONED BY (d);\nCREATE TABLE b (id INT);"
        partitioned, plain = self._statements(sql, "parquet")
        assert partitioned.endswith("USING PARQUET PARTITIONED BY (`d`)")
        assert plain.endswith("USING PARQUET")

    def test_empty_format_defaults_to_parquet(self) -> None:
        (result,) = self._statements("CREATE TABLE t (id INT)", "")
        assert result.endswith("USING PARQUET")

    def test_non_create_statement_passes_through(self) -> None:
        assert self._statements("INSERT INTO t VALUES (1)") == ["INSERT INTO `t` VALUES (1)"]

    def test_comment_only_input_has_no_statements(self) -> None:
        assert self._statements("-- just a comment\n") == []
        assert self._statements("   \n  ") == []

    def test_multiple_statements_are_split(self) -> None:
        results = self._statements("CREATE TABLE a (x INT); CREATE TABLE b (y INT);")
        assert len(results) == 2
        assert all(result.endswith("USING PARQUET") for result in results)

    @pytest.mark.parametrize(
        "sql",
        [
            "CREATE TABLE t (id INT PRIMARY KEY, val STRING)",
            "CREATE TABLE t (id INT, val STRING, PRIMARY KEY (id))",
            "CREATE TABLE t (id INT, ref_id INT, FOREIGN KEY (ref_id) REFERENCES other(id))",
            "CREATE TABLE t (id INT, email STRING UNIQUE, UNIQUE (id))",
            "CREATE TABLE t (id INT, c INT, CONSTRAINT pk PRIMARY KEY (id))",
        ],
    )
    def test_v1_formats_strip_constraints(self, sql: str) -> None:
        (result,) = self._statements(sql)
        for keyword in ("PRIMARY KEY", "FOREIGN KEY", "REFERENCES", "UNIQUE", "CONSTRAINT"):
            assert keyword not in result.upper()
        assert result.count("(") == result.count(")")
        assert result.endswith("USING PARQUET")

    def test_smallint_is_upcast_when_stripping(self) -> None:
        (result,) = self._statements("CREATE TABLE t (id INT, c SMALLINT)")
        assert "SMALLINT" not in result.upper()

    def test_constraints_and_smallint_kept_when_not_stripping(self) -> None:
        (result,) = self._statements("CREATE TABLE t (id INT PRIMARY KEY, c SMALLINT)", "delta", strip=False)
        assert "PRIMARY KEY" in result.upper()
        assert "SMALLINT" in result.upper()
        assert result.endswith("USING DELTA")

    def test_fixed_size_array_becomes_array_type(self) -> None:
        (result,) = self._statements("CREATE TABLE vectors (id BIGINT, embedding FLOAT[128])")
        assert "ARRAY<" in result.upper()
        assert "[128]" not in result

    @pytest.mark.parametrize(
        ("table_format", "expected"),
        [("parquet", True), ("ORC", True), ("delta", False), ("iceberg", False), ("", True)],
    )
    def test_format_strips_constraints(self, table_format: str, expected: bool) -> None:
        assert spark_format_strips_constraints(table_format) is expected


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
            "CREATE TABLE orders (id INT) USING PARQUET",
            "CREATE TABLE customers (id INT) USING PARQUET",
        ]

        run_spark_schema_creation_loop(
            spark,
            statements,
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
                logger=logging.getLogger(__name__),
            )

    def test_raises_when_extracted_name_fails_validation(self) -> None:
        spark = MagicMock()
        spark.sql.side_effect = RuntimeError("Table already exists")

        with pytest.raises(RuntimeError, match="strict-ASCII identifier validation"):
            run_spark_schema_creation_loop(
                spark,
                ["CREATE TABLE my_schema.tbl (id INT)"],
                logger=logging.getLogger(__name__),
            )

    def test_raises_for_non_already_exists_errors(self) -> None:
        spark = MagicMock()
        spark.sql.side_effect = RuntimeError("syntax error near '('")

        with pytest.raises(RuntimeError, match="syntax error"):
            run_spark_schema_creation_loop(
                spark,
                ["CREATE TABLE t (id INT)"],
                logger=logging.getLogger(__name__),
            )

    def test_skips_empty_statements(self) -> None:
        spark = MagicMock()
        run_spark_schema_creation_loop(
            spark,
            ["", "  ", "CREATE TABLE t (id INT)"],
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


class _StoreConf:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple[str, str]] = []

    def set(self, key, value) -> None:
        self.set_calls.append((key, value))
        self.store[key] = str(value)

    def get(self, key, default=None):
        return self.store.get(key, default)

    def isModifiable(self, key) -> bool:
        return True


class _StoreSession:
    def __init__(self) -> None:
        self.conf = _StoreConf()


def _real_spark_config(spark_map: dict):
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

    config = UnifiedTuningConfiguration.from_dict({"platform_optimizations": {"spark": dict(spark_map)}})
    return config


class _StubAdapter(SparkLikeAdapterMixin):
    def __init__(self, name: str = "TestPlatform") -> None:
        self.platform_name = name
        self.logger = logging.getLogger(__name__)
        self.table_tuning_calls: list = []
        self.unified_tuning_configuration = None

    def get_effective_tuning_configuration(self):
        return self.unified_tuning_configuration

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
        from benchbox.platforms._spark_helpers import apply_tuned_spark_session_config

        adapter = _StubAdapter("Spark")
        adapter.tuning_enabled = True
        adapter.unified_tuning_configuration = _real_spark_config(
            {"sql.adaptive.enabled": "true", "sql.cbo.enabled": "true"}
        )
        session = _StoreSession()
        apply_tuned_spark_session_config(adapter, session)
        assert session.conf.set_calls == [
            ("spark.sql.adaptive.enabled", "true"),
            ("spark.sql.cbo.enabled", "true"),
        ]

    def test_apply_platform_optimizations_logs_warning_on_failure(self, caplog) -> None:
        from benchbox.platforms._spark_helpers import apply_tuned_spark_session_config

        adapter = _StubAdapter("Spark")
        adapter.tuning_enabled = True
        adapter.unified_tuning_configuration = _real_spark_config({"foo.bar": "baz"})
        session = _StoreSession()
        session.conf.set = MagicMock(side_effect=RuntimeError("rejected"))
        with caplog.at_level(logging.WARNING, logger=__name__):
            apply_tuned_spark_session_config(adapter, session)
        warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("failed to apply" in m for m in warnings)

    def test_platform_optimizations_recorded_in_applied_ledger(self) -> None:
        from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
        from benchbox.platforms._spark_helpers import apply_tuned_spark_session_config

        adapter = _StubAdapter("Spark")
        adapter.tuning_enabled = True
        adapter.unified_tuning_configuration = _real_spark_config(
            {"sql.shuffle.partitions": "200", "sql.cbo.enabled": "true"}
        )
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        apply_tuned_spark_session_config(adapter, _StoreSession())
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
        from benchbox.platforms._spark_helpers import apply_tuned_spark_session_config

        adapter = _StubAdapter("Spark")
        adapter.tuning_enabled = True
        adapter.unified_tuning_configuration = _real_spark_config(
            {"sql.shuffle.partitions": "200", "sql.cbo.enabled": "true"}
        )
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        session = _StoreSession()

        def _set(key, _value):
            if key == "spark.sql.shuffle.partitions":
                raise RuntimeError("rejected")
            session.conf.store[key] = str(_value)

        session.conf.set = _set
        apply_tuned_spark_session_config(adapter, session)
        by_stmt = {s.statement: s.status for s in adapter._applied_tuning_ledger.statements}
        assert by_stmt["SET spark.sql.shuffle.partitions=200"] == STATEMENT_FAILED
        assert by_stmt["SET spark.sql.cbo.enabled=true"] == EXECUTED

    def test_platform_optimizations_without_ledger_does_not_raise(self) -> None:
        from benchbox.platforms._spark_helpers import apply_tuned_spark_session_config

        adapter = _StubAdapter("Spark")
        adapter.tuning_enabled = True
        adapter.unified_tuning_configuration = _real_spark_config({"sql.cbo.enabled": "true"})
        session = _StoreSession()
        apply_tuned_spark_session_config(adapter, session)
        assert session.conf.set_calls == [("spark.sql.cbo.enabled", "true")]

    def test_apply_unified_tuning_does_not_set_spark_conf(self) -> None:
        from benchbox.platforms._spark_helpers import apply_tuned_spark_session_config

        adapter = _StubAdapter("LakeSail")
        adapter.tuning_enabled = True
        adapter.table_tuning_calls = []
        session = _StoreSession()
        config = _real_spark_config({"sql.adaptive.enabled": "true"})
        config.table_tunings["orders"] = "TUNING_A"
        adapter.unified_tuning_configuration = config
        adapter.apply_unified_tuning(config, connection=session)
        assert session.conf.set_calls == []
        apply_tuned_spark_session_config(adapter, session)
        assert session.conf.set_calls == [("spark.sql.adaptive.enabled", "true")]

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

    def test_entries_enabled_sets_all_true(self) -> None:
        entries = spark_aqe_conf_entries(True)
        assert entries == dict.fromkeys(SPARK_AQE_KEYS, "true")

    def test_entries_disabled_sets_all_false(self) -> None:
        entries = spark_aqe_conf_entries(False)
        assert entries == dict.fromkeys(SPARK_AQE_KEYS, "false")


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


class TestSessionTuningConfEntries:
    def test_baseline_with_no_options_leaves_engine_defaults(self) -> None:
        assert spark_session_tuning_conf_entries(None, None, tuning_enabled=False) == {}

    def test_tuned_run_falls_back_to_curated_values(self) -> None:
        entries = spark_session_tuning_conf_entries(None, None, tuning_enabled=True)
        assert entries["spark.sql.shuffle.partitions"] == "200"
        assert all(entries[key] == "true" for key in SPARK_AQE_KEYS)

    @pytest.mark.parametrize("tuning_enabled", [False, True])
    def test_explicit_options_win_in_every_mode(self, tuning_enabled: bool) -> None:
        entries = spark_session_tuning_conf_entries(64, False, tuning_enabled=tuning_enabled)
        assert entries["spark.sql.shuffle.partitions"] == "64"
        assert all(entries[key] == "false" for key in SPARK_AQE_KEYS)


class TestSchemaDdlReadDialect:
    def test_target_dialect_ddl_with_backticks_is_read_as_target(self) -> None:
        sql = "CREATE TABLE `hub_x` (`id` BIGINT, `n` STRING, PRIMARY KEY (`id`))"
        (result,) = spark_schema_statements(sql, table_format="delta", strip_constraints=False)
        assert result.startswith("CREATE TABLE `hub_x`")
        assert result.endswith("USING DELTA")

    def test_standard_ddl_is_read_as_standard(self) -> None:
        from benchbox.utils.dialect_utils import schema_ddl_read_dialect

        assert schema_ddl_read_dialect("CREATE TABLE t (a INT)", "spark") == "standard"
        assert schema_ddl_read_dialect("CREATE TABLE `t` (`a` INT)", "spark") == "spark"


class TestVectorSearchSchema:
    @pytest.mark.parametrize("dialect", ["spark", "databricks"])
    def test_embedding_is_four_byte_float(self, dialect: str) -> None:
        from benchbox import VectorSearch

        sql = VectorSearch(scale_factor=0.01).get_create_tables_sql(dialect=dialect)
        statements = spark_schema_statements(sql, table_format="parquet", strip_constraints=True)
        (vectors,) = [s for s in statements if "embedding" in s.lower()]
        assert "ARRAY<FLOAT>" in vectors.upper()
        assert "DOUBLE" not in vectors.upper()


class TestDefaultSparkDriverMemory:
    GIB = 1024**3

    @pytest.mark.parametrize(
        ("total_gib", "expected"),
        [(2, "4g"), (7, "4g"), (16, "9g"), (32, "19g"), (64, "31g"), (512, "31g")],
    )
    def test_local_master_scales_with_system_memory(self, total_gib: int, expected: str) -> None:
        assert default_spark_driver_memory("local[*]", total_gib * self.GIB) == expected

    @pytest.mark.parametrize("master", ["yarn", "spark://host:7077", "k8s://https://host"])
    def test_cluster_masters_keep_the_fixed_default(self, master: str) -> None:
        assert default_spark_driver_memory(master, 64 * self.GIB) == "4g"

    def test_sixteen_gib_release_runner_gets_a_nine_gib_heap(self) -> None:
        runner_total_bytes = int(15.6 * self.GIB)

        assert default_spark_driver_memory("local[*]", runner_total_bytes) == "9g"

    def test_runner_heap_plus_jvm_overhead_fits_the_stage_one_memory_guard(self) -> None:
        jvm_non_heap_allowance_gib = 2
        python_driver_allowance_gib = 1
        stage_one = Path(__file__).resolve().parents[3] / "tests/uat/configs/release-gate-01-native-dataframe.yaml"
        guard_gib = yaml.safe_load(stage_one.read_text(encoding="utf-8"))["execute"]["per_cell_memory_limit_gib"]
        heap_gib = int(default_spark_driver_memory("local[*]", int(15.6 * self.GIB)).removesuffix("g"))

        assert heap_gib + jvm_non_heap_allowance_gib + python_driver_allowance_gib <= guard_gib
