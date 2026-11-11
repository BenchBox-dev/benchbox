from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

try:
    import pyspark

    PYSPARK_AVAILABLE = True
except ImportError:
    PYSPARK_AVAILABLE = False


pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(not PYSPARK_AVAILABLE, reason="PySpark not installed"),
]


if PYSPARK_AVAILABLE:
    from benchbox.platforms.pyspark import sql_adapter
    from benchbox.platforms.pyspark.sql_adapter import PySparkSQLAdapter
else:
    sql_adapter = None
    PySparkSQLAdapter = None


@pytest.fixture
def mock_pyspark_env(monkeypatch: pytest.MonkeyPatch):

    mock_session = MagicMock()
    mock_session.catalog.listDatabases.return_value = []
    mock_session.sql.return_value = MagicMock()

    mock_builder = MagicMock()
    mock_builder.master.return_value = mock_builder
    mock_builder.config.return_value = mock_builder
    mock_builder.enableHiveSupport.return_value = mock_builder
    mock_builder.getOrCreate.return_value = mock_session

    fake_spark_module = MagicMock()
    fake_spark_module.builder = mock_builder

    monkeypatch.setitem(
        sys.modules,
        "pyspark",
        MagicMock(sql=MagicMock(SparkSession=fake_spark_module)),
    )
    monkeypatch.setitem(sys.modules, "pyspark.sql", MagicMock(SparkSession=fake_spark_module))
    monkeypatch.setitem(sys.modules, "pyspark.sql.types", MagicMock())

    return mock_session


def test_platform_name(mock_pyspark_env) -> None:

    adapter = PySparkSQLAdapter(master="local[2]", database="benchbox_sql")

    assert adapter.platform_name == "PySpark SQL"
    assert adapter.get_target_dialect() == "spark"


def test_create_connection_uses_session_manager(monkeypatch: pytest.MonkeyPatch, mock_pyspark_env) -> None:

    captured_calls: list[dict] = []

    def fake_get_or_create(cls, **kwargs):
        captured_calls.append(kwargs.copy())
        return mock_pyspark_env

    release_calls = {"count": 0}

    def fake_release(cls):
        release_calls["count"] += 1

    monkeypatch.setattr(sql_adapter.SparkSessionManager, "get_or_create", classmethod(fake_get_or_create))
    monkeypatch.setattr(sql_adapter.SparkSessionManager, "release", classmethod(fake_release))

    adapter = PySparkSQLAdapter(master="local[4]", database="benchbox_sql")
    connection = adapter.create_connection()

    assert connection is mock_pyspark_env
    first_call = captured_calls[0]
    assert first_call["master"] == "local[4]"
    assert first_call["app_name"] == adapter.app_name
    assert first_call["shuffle_partitions"] == adapter.shuffle_partitions

    adapter_with_dir = PySparkSQLAdapter(master="local[2]", database="benchbox_sql", warehouse_dir=str(Path("/tmp")))
    adapter_with_dir.create_connection()
    second_call = captured_calls[1]
    assert second_call["extra_configs"].get("spark.sql.warehouse.dir") == str(Path("/tmp"))

    adapter.close()
    assert release_calls["count"] == 1


def test_close_clears_reference(monkeypatch: pytest.MonkeyPatch, mock_pyspark_env) -> None:

    released = {"value": False}

    monkeypatch.setattr(
        sql_adapter.SparkSessionManager,
        "get_or_create",
        classmethod(lambda cls, **_: mock_pyspark_env),
    )
    monkeypatch.setattr(
        sql_adapter.SparkSessionManager,
        "release",
        classmethod(lambda cls: released.__setitem__("value", True)),
    )

    adapter = PySparkSQLAdapter(master="local[*]", database="benchbox_sql")
    adapter.create_connection()
    adapter.close()

    assert released["value"] is True


