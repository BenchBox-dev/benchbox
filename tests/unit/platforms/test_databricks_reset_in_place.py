from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.databricks import DatabricksAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture
def adapter():
    with patch("benchbox.platforms.databricks.adapter.check_platform_dependencies", return_value=(True, [])):
        return DatabricksAdapter(
            server_hostname="h.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/x",
            access_token="t",
            catalog="cat",
            schema="sch",
        )


def _connection(tables, fail_on=None):
    cursor = MagicMock()
    executed = []

    def execute(sql):
        executed.append(sql)
        if fail_on and fail_on in sql:
            raise RuntimeError("boom")

    cursor.execute.side_effect = execute
    cursor.fetchall.return_value = tables
    connection = MagicMock()
    connection.cursor.return_value = cursor
    return connection, executed


def test_reset_truncates_every_table_and_never_drops(adapter):
    connection, executed = _connection([("sch", "orders", False), ("sch", "lineitem", False), ("sch", "tmp", True)])
    with patch.object(adapter, "_create_admin_connection", return_value=connection):
        assert adapter.reset_database_in_place(catalog="cat", schema="sch") is True

    assert executed == [
        "SHOW TABLES IN cat.sch",
        "TRUNCATE TABLE cat.sch.`orders`",
        "TRUNCATE TABLE cat.sch.`lineitem`",
    ]
    connection.close.assert_called_once()


def test_reset_raises_when_a_table_cannot_be_truncated(adapter):
    connection, executed = _connection([("sch", "v_orders", False)], fail_on="TRUNCATE")
    with patch.object(adapter, "_create_admin_connection", return_value=connection):
        with pytest.raises(RuntimeError, match="boom"):
            adapter.reset_database_in_place(catalog="cat", schema="sch")

    assert not any(sql.startswith("DROP") for sql in executed)


@pytest.mark.parametrize(
    ("tables", "fail_on"),
    [
        ([], "SHOW TABLES"),
        ([("sch", "orders", False), ("sch", "lineitem", False)], "`lineitem`"),
    ],
)
def test_remove_database_never_drops_after_reset_error(adapter, tables, fail_on):
    connection, executed = _connection(tables, fail_on=fail_on)
    adapter.drop_database = MagicMock()

    with (
        patch.object(adapter, "_create_admin_connection", return_value=connection),
        pytest.raises(RuntimeError, match="Could not remove existing database: boom"),
    ):
        adapter._remove_database(False, "", catalog="cat", schema="sch")

    adapter.drop_database.assert_not_called()
    assert not any(sql.startswith("DROP") for sql in executed)


def test_remove_database_never_drops_after_reset_connection_error(adapter):
    adapter.drop_database = MagicMock()

    with (
        patch.object(adapter, "_create_admin_connection", side_effect=RuntimeError("offline")),
        pytest.raises(RuntimeError, match="Could not remove existing database: offline"),
    ):
        adapter._remove_database(False, "", catalog="cat", schema="sch")

    adapter.drop_database.assert_not_called()


def test_handle_existing_database_preserves_known_absent_schema(adapter):
    adapter.check_database_exists = MagicMock(return_value=False)
    adapter.reset_database_in_place = MagicMock()
    adapter.drop_database = MagicMock()

    adapter.handle_existing_database(catalog="cat", schema="sch")

    adapter.reset_database_in_place.assert_not_called()
    adapter.drop_database.assert_not_called()


def test_if_not_exists_tables_are_replaced_only_after_reset(adapter):
    from benchbox.utils.dialect_utils import translate_ddl

    ddl = "CREATE TABLE IF NOT EXISTS flights (id INT)"

    def convert():
        return translate_ddl(ddl, "databricks", "standard", **adapter._schema_ddl_options(ddl))[0]

    assert "IF NOT EXISTS" in convert()

    adapter._schema_reset_in_place = True
    converted = convert()
    assert converted.startswith("CREATE OR REPLACE TABLE `flights`")
    assert "IF NOT EXISTS" not in converted


def test_reset_marks_schema_for_replacement(adapter):
    connection, _ = _connection([("sch", "orders", False)])
    with patch.object(adapter, "_create_admin_connection", return_value=connection):
        adapter.reset_database_in_place(catalog="cat", schema="sch")
    assert adapter._schema_reset_in_place is True
