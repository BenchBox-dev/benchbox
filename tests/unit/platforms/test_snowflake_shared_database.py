from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.base.validation import DatabaseValidator, SchemaValidator
from benchbox.platforms.snowflake import SnowflakeAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

DATABASE = "tpch_sf1_notuning_noconstraints"
TPCH_TABLES = ["customer", "lineitem", "nation", "orders", "part", "partsupp", "region", "supplier"]
STAGING_TABLES = ["write_ops_log", "txn_staging"]


class _Cursor:
    def __init__(self, statements: list[str], rows: list[Any] | None = None):
        self.statements = statements
        self.rows = rows or []

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> None:
        self.statements.append(sql)

    def fetchall(self) -> list[Any]:
        return self.rows

    def fetchone(self) -> Any:
        return self.rows[0] if self.rows else None

    def close(self) -> None:
        return None


class _Connection:
    def __init__(self, rows: list[Any] | None = None):
        self.statements: list[str] = []
        self.closed = False
        self._rows = rows

    def cursor(self) -> _Cursor:
        return _Cursor(self.statements, self._rows)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def adapter():
    with patch("benchbox.platforms.snowflake.snowflake"):
        instance = SnowflakeAdapter(
            account="acct", username="user", password="pw", warehouse="WH", database=DATABASE, schema="PUBLIC"
        )
    instance.benchmark_instance = SimpleNamespace(get_schema=lambda: {name: {} for name in TPCH_TABLES})
    return instance


def _admin(adapter: SnowflakeAdapter, connection: _Connection):
    return patch.object(adapter, "_create_admin_connection", return_value=connection)


def test_a_reset_drops_only_this_benchmarks_tables_and_keeps_the_database(adapter):
    connection = _Connection()
    with _admin(adapter, connection):
        assert adapter.reset_database_in_place(database=DATABASE, schema="PUBLIC") is True

    assert sorted(connection.statements) == sorted(
        [f"DROP TABLE IF EXISTS {DATABASE}.PUBLIC.{name.upper()}" for name in TPCH_TABLES]
        + [f"DROP TABLE IF EXISTS {DATABASE}.PUBLIC.BENCHBOX_TUNING_METADATA"]
    )
    assert not any("DATABASE" in statement.upper().replace(DATABASE.upper(), "") for statement in connection.statements)
    assert connection.closed


def test_a_reset_clears_the_tuning_metadata_so_a_later_run_cannot_trust_it(adapter):
    connection = _Connection()
    with _admin(adapter, connection):
        adapter.reset_database_in_place(database=DATABASE, schema="PUBLIC")

    assert f"DROP TABLE IF EXISTS {DATABASE}.PUBLIC.BENCHBOX_TUNING_METADATA" in connection.statements


def test_a_reset_drops_an_external_table_with_the_statement_snowflake_requires(adapter):
    class _ExternalTables(_Connection):
        def cursor(self) -> _Cursor:
            cursor = super().cursor()
            record = cursor.execute

            def execute(sql: str, *args: Any, **kwargs: Any) -> None:
                if sql == f"DROP TABLE IF EXISTS {DATABASE}.PUBLIC.LINEITEM":
                    record(sql)
                    raise RuntimeError("Object found is of type 'EXTERNAL_TABLE', not specified type 'TABLE'.")
                record(sql)

            cursor.execute = execute
            return cursor

    connection = _ExternalTables()
    with _admin(adapter, connection):
        assert adapter.reset_database_in_place(database=DATABASE, schema="PUBLIC") is True

    assert f"DROP EXTERNAL TABLE IF EXISTS {DATABASE}.PUBLIC.LINEITEM" in connection.statements
    assert not any(sql.startswith("DROP DATABASE") for sql in connection.statements)


def test_a_validation_mismatch_never_drops_the_database_other_benchmarks_share(adapter):
    connection = _Connection()
    with _admin(adapter, connection):
        adapter._remove_database(
            False,
            "",
            foreign_tables=tuple(STAGING_TABLES),
            database=DATABASE,
            schema="PUBLIC",
        )

    assert connection.statements
    assert not any(statement.upper().startswith("DROP DATABASE") for statement in connection.statements)
    dropped = {statement.rsplit(".", 1)[-1].lower() for statement in connection.statements}
    assert dropped == {*TPCH_TABLES, "benchbox_tuning_metadata"}
    assert dropped.isdisjoint(STAGING_TABLES)


def test_force_recreate_does_not_drop_the_shared_database_either(adapter):
    adapter.force_recreate = True
    adapter.check_database_exists = Mock(return_value=True)
    adapter.get_database_path = Mock(return_value=None)
    connection = _Connection()
    with _admin(adapter, connection):
        adapter.handle_existing_database(database=DATABASE, schema="PUBLIC")

    assert connection.statements
    assert not any(statement.upper().startswith("DROP DATABASE") for statement in connection.statements)


def test_a_reset_that_cannot_tell_which_tables_are_its_own_removes_nothing(adapter):
    adapter.benchmark_instance = None
    connection = _Connection()
    with _admin(adapter, connection), pytest.raises(RuntimeError, match="Could not remove existing database"):
        adapter._remove_database(False, "", database=DATABASE, schema="PUBLIC")

    assert connection.statements == []


def test_a_failed_drop_fails_the_reset_instead_of_continuing(adapter):
    class _Failing(_Connection):
        def cursor(self) -> _Cursor:
            cursor = super().cursor()

            def execute(sql: str, *args: Any, **kwargs: Any) -> None:
                raise RuntimeError("insufficient privileges")

            cursor.execute = execute
            return cursor

    with _admin(adapter, _Failing()), pytest.raises(RuntimeError, match="Failed to reset Snowflake database"):
        adapter.reset_database_in_place(database=DATABASE, schema="PUBLIC")


@pytest.mark.parametrize("name", ['x"; DROP DATABASE y; --', "a b", "a.b", ""])
def test_an_unsafe_database_name_is_rejected_before_any_connection(adapter, name):
    connection = Mock()
    with patch.object(adapter, "_create_admin_connection", return_value=connection) as admin:
        with pytest.raises(ValueError, match="Invalid Snowflake database identifier"):
            adapter.drop_database(database=name)
        with pytest.raises(ValueError, match="Invalid Snowflake database identifier"):
            adapter.reset_database_in_place(database=name, schema="PUBLIC")
    admin.assert_not_called()


def test_the_drop_names_the_database_that_create_schema_made(adapter):
    created = _Connection()
    adapter.database = DATABASE
    with patch.object(adapter, "_should_skip_schema_creation", return_value=True):
        adapter.create_schema(adapter.benchmark_instance, created)
    create = next(sql for sql in created.statements if sql.startswith("CREATE DATABASE"))

    dropped = _Connection()
    with _admin(adapter, dropped):
        adapter.drop_database(database=DATABASE)

    assert create == f"CREATE DATABASE IF NOT EXISTS {DATABASE}"
    assert dropped.statements == [f"DROP DATABASE IF EXISTS {DATABASE}"]


def test_the_use_fallbacks_name_the_database_and_schema_unquoted(adapter):
    connection = _Connection(rows=[["x", "OTHER_DB"]])
    with _admin(adapter, connection):
        adapter.check_server_database_exists(database=DATABASE, schema="PUBLIC")

    assert f"USE DATABASE {DATABASE}" in connection.statements
    assert not any('"' in statement for statement in connection.statements)


class _ExistingTablesAdapter:
    def __init__(self, existing: list[str], expected: list[str]):
        self.benchmark_instance = SimpleNamespace(get_schema=lambda: {name: {} for name in expected})
        self._existing = existing
        self.scale_factor = None

    def _get_existing_tables(self, connection: Any) -> list[str]:
        return self._existing

    def log_operation_start(self, *args: Any, **kwargs: Any) -> None: ...

    def log_operation_complete(self, *args: Any, **kwargs: Any) -> None: ...

    def log_very_verbose(self, *args: Any, **kwargs: Any) -> None: ...


def test_validation_reports_the_tables_that_belong_to_no_part_of_this_benchmark():
    benchmark_adapter = _ExistingTablesAdapter(
        existing=[*TPCH_TABLES, *STAGING_TABLES, "benchbox_tuning_metadata", "sqlite_sequence"],
        expected=TPCH_TABLES,
    )

    result = SchemaValidator(benchmark_adapter, {}).validate(Mock())

    assert result.is_valid
    assert result.foreign_tables == set(STAGING_TABLES)


def test_validation_carries_the_foreign_tables_into_the_database_result():
    benchmark_adapter = _ExistingTablesAdapter(existing=[*TPCH_TABLES[:-1], *STAGING_TABLES], expected=TPCH_TABLES)
    validator = DatabaseValidator(benchmark_adapter, {})

    with (
        patch.object(validator.connection_validator, "create_temporary_connection") as connect,
        patch.object(
            validator.tuning_validator, "validate", return_value=SimpleNamespace(is_valid=True, errors=[], warnings=[])
        ),
    ):
        connect.return_value.__enter__.return_value = Mock()
        result = validator.validate()

    assert not result.is_valid
    assert result.foreign_tables == tuple(sorted(STAGING_TABLES))
