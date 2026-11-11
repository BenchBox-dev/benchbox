# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

from benchbox.core.connection import DatabaseConnection, DatabaseError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDatabaseConnectionInitialization:
    def test_initialization_with_execute_method(self, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db)

        assert db_conn.connection is duckdb_memory_db
        assert db_conn.dialect is None

    def test_initialization_with_dialect(self, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db, dialect="duckdb")

        assert db_conn.connection is duckdb_memory_db
        assert db_conn.dialect == "duckdb"

    def test_initialization_with_none_connection(self):

        with pytest.raises(TypeError, match="Connection object cannot be None"):
            DatabaseConnection(None)

    def test_initialization_with_invalid_connection(self):

        invalid_conn = object()

        with pytest.raises(
            ValueError,
            match="Connection object must have either 'cursor' or 'execute' method",
        ):
            DatabaseConnection(invalid_conn)


class TestDatabaseConnectionExecute:
    def test_execute_with_direct_execute_method(self, duckdb_memory_db):

        duckdb_memory_db.execute("CREATE TABLE test (id INTEGER, name VARCHAR)")
        duckdb_memory_db.execute("INSERT INTO test VALUES (1, 'test1'), (2, 'test2')")

        db_conn = DatabaseConnection(duckdb_memory_db)
        result = db_conn.execute("SELECT * FROM test")

        assert result is not None
        rows = result.fetchall()
        assert len(rows) == 2
        assert rows[0] == (1, "test1")
        assert rows[1] == (2, "test2")

    def test_execute_with_parameters_list(self, duckdb_memory_db):

        duckdb_memory_db.execute("CREATE TABLE test (id INTEGER, name VARCHAR, value DECIMAL)")

        db_conn = DatabaseConnection(duckdb_memory_db)
        params = [1, "test", 3.14]
        db_conn.execute("INSERT INTO test VALUES (?, ?, ?)", params)

        verify_result = duckdb_memory_db.execute("SELECT * FROM test WHERE id = 1")
        row = verify_result.fetchone()
        assert row[0] == 1
        assert row[1] == "test"
        assert float(row[2]) == 3.14

    def test_execute_with_parameters_dict(self, duckdb_memory_db):

        duckdb_memory_db.execute("CREATE TABLE test (id INTEGER, name VARCHAR)")

        db_conn = DatabaseConnection(duckdb_memory_db)
        params = {"id": 1, "name": "test"}
        db_conn.execute("INSERT INTO test VALUES ($id, $name)", params)

        verify_result = duckdb_memory_db.execute("SELECT * FROM test WHERE id = 1")
        row = verify_result.fetchone()
        assert row == (1, "test")

    def test_execute_with_parameters_tuple(self, duckdb_memory_db):

        duckdb_memory_db.execute("CREATE TABLE test (id INTEGER, name VARCHAR)")

        db_conn = DatabaseConnection(duckdb_memory_db)
        params = (1, "test")
        db_conn.execute("INSERT INTO test VALUES (?, ?)", params)

        verify_result = duckdb_memory_db.execute("SELECT * FROM test WHERE id = 1")
        row = verify_result.fetchone()
        assert row == (1, "test")

    def test_execute_with_empty_query(self, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db)

        with pytest.raises(ValueError, match="Query cannot be empty or None"):
            db_conn.execute("")

    def test_execute_with_none_query(self, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db)

        with pytest.raises(ValueError, match="Query cannot be empty or None"):
            db_conn.execute(None)

    def test_execute_with_whitespace_only_query(self, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db)

        with pytest.raises(ValueError, match="Query cannot be empty or None"):
            db_conn.execute("   \n\t   ")

    def test_execute_with_sql_error(self, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db, dialect="duckdb")

        with pytest.raises(DatabaseError, match="Error executing query \\(dialect: duckdb\\)"):
            db_conn.execute("SELECT * FROM nonexistent_table")

    @patch("benchbox.core.connection.logger")
    def test_execute_logging_direct_execute(self, mock_logger, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db)
        db_conn.execute("SELECT 1")

        mock_logger.debug.assert_called_once_with("Executing query with direct execute method: SELECT 1...")

    @patch("benchbox.core.connection.logger")
    def test_execute_logging_truncated_query(self, mock_logger, duckdb_memory_db):
        db_conn = DatabaseConnection(duckdb_memory_db)
        long_query = "SELECT 1 WHERE " + "1 = 1 AND " * 20 + "1 = 1"
        db_conn.execute(long_query)

        expected_log = f"Executing query with direct execute method: {long_query[:100]}..."
        mock_logger.debug.assert_called_once_with(expected_log)


class TestDatabaseConnectionFetchMethods:
    def test_fetchall_success(self, duckdb_memory_db):

        duckdb_memory_db.execute("CREATE TABLE test (id INTEGER, name VARCHAR)")
        duckdb_memory_db.execute("INSERT INTO test VALUES (1, 'row1'), (2, 'row2'), (3, 'row3')")

        db_conn = DatabaseConnection(duckdb_memory_db)
        cursor = db_conn.execute("SELECT * FROM test ORDER BY id")

        result = db_conn.fetchall(cursor)

        assert result == [(1, "row1"), (2, "row2"), (3, "row3")]

    def test_fetchone_success(self, duckdb_memory_db):

        duckdb_memory_db.execute("CREATE TABLE test (id INTEGER, name VARCHAR)")
        duckdb_memory_db.execute("INSERT INTO test VALUES (1, 'single_row')")

        db_conn = DatabaseConnection(duckdb_memory_db)
        cursor = db_conn.execute("SELECT * FROM test")

        result = db_conn.fetchone(cursor)

        assert result == (1, "single_row")

    def test_fetchone_no_more_rows(self, duckdb_memory_db):

        duckdb_memory_db.execute("CREATE TABLE test (id INTEGER, name VARCHAR)")

        db_conn = DatabaseConnection(duckdb_memory_db)
        cursor = db_conn.execute("SELECT * FROM test")

        result = db_conn.fetchone(cursor)

        assert result is None


class TestDatabaseConnectionTransactionMethods:
    def test_commit_success(self, duckdb_memory_db):
        db_conn = DatabaseConnection(duckdb_memory_db)

        db_conn.commit()

    def test_rollback_success(self, duckdb_memory_db):
        db_conn = DatabaseConnection(duckdb_memory_db)

        duckdb_memory_db.execute("BEGIN")

        db_conn.rollback()

    @patch("benchbox.core.connection.logger")
    def test_commit_logging(self, mock_logger, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db)
        db_conn.commit()

        mock_logger.debug.assert_called_once_with("Transaction committed")

    @patch("benchbox.core.connection.logger")
    def test_rollback_logging(self, mock_logger, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db)

        duckdb_memory_db.execute("BEGIN")

        db_conn.rollback()

        mock_logger.debug.assert_called_once_with("Transaction rolled back")


class TestDatabaseConnectionClose:
    def test_close_success(self, duckdb_memory_db):
        db_conn = DatabaseConnection(duckdb_memory_db)

        db_conn.close()

    def test_close_multiple_calls(self, duckdb_memory_db):
        db_conn = DatabaseConnection(duckdb_memory_db)
        db_conn.close()
        db_conn.close()

    @patch("benchbox.core.connection.logger")
    def test_close_logging(self, mock_logger, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db)
        db_conn.close()

        mock_logger.debug.assert_called_once_with("Database connection closed")


class TestDatabaseConnectionContextManager:
    def test_context_manager_enter(self, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db)

        with db_conn as conn:
            assert conn is db_conn

    def test_context_manager_exit_calls_close(self, duckdb_memory_db):
        db_conn = DatabaseConnection(duckdb_memory_db)

        with db_conn:
            pass

    def test_context_manager_with_exception(self, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db)

        with pytest.raises(ValueError, match="Test exception"), db_conn:
            raise ValueError("Test exception")


class TestDatabaseConnectionRepresentation:
    def test_repr_without_dialect(self, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db)

        repr_str = repr(db_conn)
        assert "DatabaseConnection(" in repr_str
        assert "dialect=" not in repr_str

    def test_repr_with_dialect(self, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db, dialect="duckdb")

        repr_str = repr(db_conn)
        assert "DatabaseConnection(" in repr_str
        assert "dialect=duckdb" in repr_str


class TestDatabaseConnectionIntegration:
    def test_sqlite_like_connection(self):
        mock_conn = Mock()
        mock_conn.execute = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall = Mock(return_value=[("result",)])
        mock_conn.execute.return_value = mock_cursor
        mock_conn.commit = Mock()
        mock_conn.rollback = Mock()
        mock_conn.close = Mock()

        db_conn = DatabaseConnection(mock_conn, dialect="sqlite")

        cursor = db_conn.execute("SELECT * FROM test")
        assert cursor is mock_cursor

        results = db_conn.fetchall(cursor)
        assert results == [("result",)]

        db_conn.commit()
        db_conn.rollback()

        db_conn.close()

        mock_conn.execute.assert_called_once()
        mock_cursor.fetchall.assert_called_once()
        mock_conn.commit.assert_called_once()
        mock_conn.rollback.assert_called_once()
        mock_conn.close.assert_called_once()

    def test_postgres_like_connection(self):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.execute = Mock()
        mock_cursor.fetchall = Mock(return_value=[("result",)])
        mock_cursor.fetchone = Mock(return_value=("result",))
        mock_conn.cursor = Mock(return_value=mock_cursor)
        mock_conn.commit = Mock()
        mock_conn.rollback = Mock()
        mock_conn.close = Mock()

        del mock_conn.execute

        db_conn = DatabaseConnection(mock_conn, dialect="postgresql")

        cursor = db_conn.execute("SELECT * FROM test", [1, "param"])
        assert cursor is mock_cursor

        results = db_conn.fetchall(cursor)
        assert results == [("result",)]

        result = db_conn.fetchone(cursor)
        assert result == ("result",)

        db_conn.commit()
        db_conn.rollback()

        db_conn.close()

        mock_conn.cursor.assert_called_once()
        mock_cursor.execute.assert_called_once_with("SELECT * FROM test", [1, "param"])
        mock_cursor.fetchall.assert_called_once()
        mock_cursor.fetchone.assert_called_once()
        mock_conn.commit.assert_called_once()
        mock_conn.rollback.assert_called_once()
        mock_conn.close.assert_called_once()

    def test_full_workflow_with_context_manager(self):

        mock_conn = Mock()
        mock_conn.execute = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall = Mock(return_value=[("row1",), ("row2",)])
        mock_conn.execute.return_value = mock_cursor
        mock_conn.commit = Mock()
        mock_conn.close = Mock()

        with DatabaseConnection(mock_conn, dialect="duckdb") as db_conn:
            cursor = db_conn.execute("SELECT * FROM test")

            results = db_conn.fetchall(cursor)
            assert len(results) == 2

            db_conn.commit()

        mock_conn.close.assert_called_once()


class TestDatabaseError:
    def test_database_error_creation(self):

        error = DatabaseError("Test error message")
        assert str(error) == "Test error message"
        assert isinstance(error, Exception)

    def test_database_error_inheritance(self):

        error = DatabaseError("Test error")
        assert isinstance(error, Exception)

    def test_database_error_with_cause(self):

        original_error = ValueError("Original error")

        try:
            raise DatabaseError("Wrapped error") from original_error
        except DatabaseError as e:
            assert str(e) == "Wrapped error"
            assert e.__cause__ is original_error


@pytest.mark.unit
class TestDatabaseConnectionEdgeCases:
    def test_execute_with_very_long_query(self, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db)

        long_query = "SELECT 1 WHERE " + "1 = 1 AND " * 20 + "1 = 1"

        with patch("benchbox.core.connection.logger") as mock_logger:
            db_conn.execute(long_query)

            logged_message = mock_logger.debug.call_args[0][0]
            assert "..." in logged_message
            assert len(logged_message) <= 150

    def test_multiple_parameter_types(self, duckdb_memory_db):
        duckdb_memory_db.execute("CREATE TABLE test (id INTEGER, name VARCHAR, value DECIMAL)")

        db_conn = DatabaseConnection(duckdb_memory_db)

        test_cases = [
            ([], "SELECT 1"),
            ([1, "test", 3.14], "INSERT INTO test VALUES (?, ?, ?)"),
            ((2, "test2"), "INSERT INTO test VALUES (?, ?, 2.5)"),
        ]

        for params, query in test_cases:
            try:
                db_conn.execute(query, params)
            except Exception:
                pass

    def test_connection_attribute_access(self, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db, dialect="duckdb")

        assert db_conn.connection is duckdb_memory_db
        assert db_conn.dialect == "duckdb"

    def test_error_message_formatting(self, duckdb_memory_db):

        db_conn = DatabaseConnection(duckdb_memory_db)
        with pytest.raises(DatabaseError) as exc_info:
            db_conn.execute("SELECT * FROM nonexistent_table")
        assert "Error executing query:" in str(exc_info.value)
        assert "dialect:" not in str(exc_info.value)

        db_conn = DatabaseConnection(duckdb_memory_db, dialect="duckdb")
        with pytest.raises(DatabaseError) as exc_info:
            db_conn.execute("SELECT * FROM nonexistent_table")
        assert "Error executing query (dialect: duckdb):" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main(["-v", __file__])


