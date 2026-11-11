from __future__ import annotations

import logging
import re
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def assert_manual_drop_error(excinfo: pytest.ExceptionInfo, platform_label: str) -> None:
    message = str(excinfo.value)
    assert platform_label in message
    assert "manually" in message.lower()


def _velox_adapter(monkeypatch: pytest.MonkeyPatch, tmp_path, **overrides):
    from benchbox.platforms import velox as velox_module
    from benchbox.platforms.velox import VeloxAdapter

    jar = tmp_path / "gluten.jar"
    jar.write_bytes(b"stub")
    session = MagicMock(name="spark")
    session.catalog.listDatabases.return_value = []
    builder = MagicMock(name="builder")
    builder.master.return_value = builder
    builder.config.return_value = builder
    builder.getOrCreate.return_value = session
    monkeypatch.setattr(velox_module, "SparkSession", MagicMock(builder=builder))
    config = {"deployment": "local", "gluten_jar_path": str(jar), "database": "benchdb"}
    config.update(overrides)
    return VeloxAdapter(**config), session


def test_velox_force_recreate_fails_closed(tmp_path, monkeypatch):
    adapter, _ = _velox_adapter(monkeypatch, tmp_path, force_recreate=True)

    with pytest.raises(RuntimeError, match="Velox does not support --force-recreate") as excinfo:
        adapter.create_connection()

    assert_manual_drop_error(excinfo, "Velox")


def test_velox_connects_normally_without_force_recreate(tmp_path, monkeypatch):
    adapter, session = _velox_adapter(monkeypatch, tmp_path)

    assert adapter.create_connection() is session


def _firebolt_adapter(**overrides):
    from benchbox.platforms.firebolt import FireboltAdapter

    adapter = FireboltAdapter.__new__(FireboltAdapter)
    adapter.deployment_mode = "core"
    adapter.database = "benchdb"
    adapter.force_recreate = False
    adapter.dry_run = False
    adapter.dry_run_mode = False
    adapter.logger = logging.getLogger("test-firebolt-force-recreate")
    adapter.log_verbose = MagicMock()
    adapter.log_very_verbose = MagicMock()
    for key, value in overrides.items():
        setattr(adapter, key, value)
    return adapter


def test_firebolt_core_force_recreate_fails_closed():
    adapter = _firebolt_adapter(force_recreate=True)

    with pytest.raises(RuntimeError, match=re.escape("Firebolt (Core) does not support --force-recreate")) as excinfo:
        adapter.drop_database(database="benchdb")

    assert_manual_drop_error(excinfo, "Firebolt (Core)")


def test_firebolt_core_drop_without_force_recreate_stays_implicit():
    adapter = _firebolt_adapter()

    assert adapter.drop_database(database="benchdb") is None


def test_firebolt_cloud_force_recreate_drops_database(monkeypatch):
    import benchbox.platforms.firebolt as firebolt_module

    executed: list[str] = []

    class _FakeCursor:
        def execute(self, sql):
            executed.append(sql)

        def close(self):
            pass

    class _FakeConnection:
        def cursor(self):
            return _FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr(firebolt_module, "firebolt_connect", lambda **kwargs: _FakeConnection())
    adapter = _firebolt_adapter(deployment_mode="cloud", force_recreate=True)
    adapter.check_server_database_exists = lambda **kwargs: True
    adapter._get_connection_params = dict

    adapter.drop_database(database="benchdb")

    assert any("DROP DATABASE" in sql for sql in executed)


@pytest.fixture()
def snowpark_session():
    mock_session = MagicMock()
    mock_session_builder = MagicMock()
    mock_session_builder.configs.return_value = mock_session_builder
    mock_session_builder.create.return_value = mock_session

    mock_snowpark_module = MagicMock()
    mock_snowpark_module.Session = MagicMock()
    mock_snowpark_module.Session.builder = mock_session_builder

    mock_exceptions = MagicMock()
    mock_exceptions.SnowparkSQLException = Exception

    with (
        patch.dict(
            "sys.modules",
            {
                "snowflake": MagicMock(),
                "snowflake.snowpark": mock_snowpark_module,
                "snowflake.snowpark.exceptions": mock_exceptions,
            },
        ),
        patch("benchbox.platforms.snowpark_connect.SNOWPARK_AVAILABLE", True),
        patch("benchbox.platforms.snowpark_connect.Session", mock_snowpark_module.Session),
    ):
        yield mock_session


def _snowpark_adapter(**overrides):
    from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

    config = {"account": "xy12345.us-east-1", "user": "test_user", "password": "test_password"}
    config.update(overrides)
    return SnowparkConnectAdapter(**config)


def test_snowpark_connect_force_recreate_fails_closed(snowpark_session):
    adapter = _snowpark_adapter(force_recreate=True)

    with pytest.raises(RuntimeError, match="Snowpark Connect does not support --force-recreate") as excinfo:
        adapter.create_connection()

    assert_manual_drop_error(excinfo, "Snowpark Connect")


def test_snowpark_connect_connects_normally_without_force_recreate(snowpark_session):
    adapter = _snowpark_adapter()

    assert adapter.create_connection() is snowpark_session


def test_motherduck_force_recreate_fails_closed():
    from benchbox.platforms.motherduck import MotherDuckAdapter

    adapter = MotherDuckAdapter(token="tok", database="benchdb", force_recreate=True)

    with pytest.raises(RuntimeError, match="MotherDuck does not support --force-recreate") as excinfo:
        adapter.create_connection()

    assert_manual_drop_error(excinfo, "MotherDuck")


def test_motherduck_connects_normally_without_force_recreate(monkeypatch):
    import benchbox.platforms.motherduck as motherduck_module
    from benchbox.platforms.motherduck import MotherDuckAdapter

    executed: list[str] = []

    class _FakeConnection:
        def execute(self, sql):
            executed.append(sql)
            return self

        def fetchone(self):
            return (1,)

        def close(self):
            pass

    monkeypatch.setattr(motherduck_module.duckdb, "connect", lambda *args, **kwargs: _FakeConnection())
    adapter = MotherDuckAdapter(token="tok", database="benchdb")

    assert isinstance(adapter.create_connection(), _FakeConnection)
    assert any("SELECT 1" in sql for sql in executed)


