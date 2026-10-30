from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from benchbox.platforms.databricks import dataframe_adapter as mod

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]


def _fake_parent_init(self, **config):
    self.server_hostname = config.get("server_hostname")
    self.http_path = config.get("http_path")
    self.access_token = config.get("access_token")
    self.catalog = config.get("catalog", "main")
    self.schema = config.get("schema", "default")
    self.logger = logging.getLogger("test.databricks.parent")
    self.log_verbose = lambda *_a, **_k: None
    self.log_very_verbose = lambda *_a, **_k: None
    self.capture_plans = False
    self.normalize_plan_literals = False


def _patch_parent_init(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.DatabricksAdapter, "__init__", _fake_parent_init)


def _new_adapter() -> mod.DatabricksDataFrameAdapter:
    with (
        patch.object(mod.DatabricksAdapter, "__init__", _fake_parent_init),
        patch.object(mod, "DATABRICKS_CONNECT_AVAILABLE", True),
    ):
        return mod.DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/path",
            access_token="token",
            catalog="main",
            schema="bench",
            cluster_id="cluster-1",
            execution_mode="dataframe",
        )


def test_init_falls_back_to_sql_when_connect_missing(monkeypatch: pytest.MonkeyPatch):
    _patch_parent_init(monkeypatch)
    monkeypatch.setattr(mod, "DATABRICKS_CONNECT_AVAILABLE", False)
    monkeypatch.setattr(mod, "_databricks_connect_error", None)

    adapter = mod.DatabricksDataFrameAdapter(
        server_hostname="host",
        http_path="/sql/path",
        access_token="token",
        execution_mode="dataframe",
    )

    assert adapter.execution_mode == "sql"
    assert adapter.cluster_id is None


def test_platform_name_and_from_config(monkeypatch: pytest.MonkeyPatch):
    _patch_parent_init(monkeypatch)
    monkeypatch.setattr(mod, "DATABRICKS_CONNECT_AVAILABLE", True)

    adapter = mod.DatabricksDataFrameAdapter(
        server_hostname="host",
        http_path="/sql/path",
        access_token="token",
        execution_mode="dataframe",
        cluster_id="cid",
    )
    assert adapter.platform_name == "Databricks-df"

    built = mod.DatabricksDataFrameAdapter.from_config(
        {
            "server_hostname": "host",
            "http_path": "/sql/path",
            "access_token": "token",
            "execution_mode": "sql",
            "cluster_id": "cid2",
        }
    )
    assert built.execution_mode == "sql"
    assert built.cluster_id == "cid2"


def test_get_or_create_spark_session_success_and_errors(monkeypatch: pytest.MonkeyPatch):
    adapter = _new_adapter()

    monkeypatch.setattr(mod, "DATABRICKS_CONNECT_AVAILABLE", False)
    with pytest.raises(ImportError):
        adapter._get_or_create_spark_session()

    class _Builder:
        def host(self, _h):
            return self

        def token(self, _t):
            return self

        def clusterId(self, _c):
            return self

        def getOrCreate(self):
            return SimpleNamespace(version="15.0")

    monkeypatch.setattr(mod, "DATABRICKS_CONNECT_AVAILABLE", True)
    monkeypatch.setattr(mod, "DatabricksSession", SimpleNamespace(builder=_Builder()))
    spark = adapter._get_or_create_spark_session()
    assert spark.version == "15.0"
    assert adapter._spark_initialized is True
    assert adapter.spark is spark

    broken = _new_adapter()

    class _BrokenBuilder:
        def host(self, _h):
            raise RuntimeError("boom")

    monkeypatch.setattr(mod, "DatabricksSession", SimpleNamespace(builder=_BrokenBuilder()))
    with pytest.raises(RuntimeError, match="Failed to create Databricks Connect session"):
        broken._get_or_create_spark_session()


def test_get_platform_info_and_spark_version_failure(monkeypatch: pytest.MonkeyPatch):
    adapter = _new_adapter()

    monkeypatch.setattr(mod.DatabricksAdapter, "get_platform_info", lambda self, connection=None: {"base": True})
    info = adapter.get_platform_info()
    assert info["execution_mode"] == "dataframe"
    assert info["cluster_id"] == "cluster-1"

    class _BadSpark:
        @property
        def version(self):
            raise RuntimeError("bad")

    adapter._spark = _BadSpark()
    adapter._spark_initialized = True
    info_bad = adapter.get_platform_info()
    assert info_bad["spark_version"] is None


def test_execute_dataframe_query_success_and_failure(monkeypatch: pytest.MonkeyPatch):
    adapter = _new_adapter()

    class _FakeCatalog:
        def setCurrentCatalog(self, _c):
            return None

        def setCurrentDatabase(self, _d):
            return None

    class _FakeSpark:
        def __init__(self):
            self.catalog = _FakeCatalog()

        def table(self, name):
            return f"table:{name}"

    spark = _FakeSpark()
    monkeypatch.setattr(adapter, "_get_or_create_spark_session", lambda: spark)

    class _FakeResultDF:
        def collect(self):
            return [(1, "a"), (2, "b")]

    out = adapter.execute_dataframe_query(
        connection=None,
        query_builder=lambda _spark, tables: _FakeResultDF(),
        query_id="Q1",
        tables={"lineitem": "unused"},
        validate_row_count=False,
    )
    assert out["status"] == "SUCCESS"
    assert out["rows_returned"] == 2
    assert out["execution_mode"] == "dataframe"

    fail = adapter.execute_dataframe_query(
        connection=None,
        query_builder=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("query boom")),
        query_id="Q2",
    )
    assert fail["status"] == "FAILED"
    assert fail["error_type"] == "RuntimeError"


def test_execute_query_dispatch_and_close_connection(monkeypatch: pytest.MonkeyPatch):
    adapter = _new_adapter()

    monkeypatch.setattr(adapter, "execute_dataframe_query", lambda **kwargs: {"mode": "df", **kwargs})
    monkeypatch.setattr(mod.DatabricksAdapter, "execute_query", lambda self, **kwargs: {"mode": "sql", **kwargs})
    monkeypatch.setattr(mod.DatabricksAdapter, "close_connection", lambda self, connection: connection.append("closed"))

    df_result = adapter.execute_query(connection=[], query=lambda *_a: None, query_id="Q1")
    sql_result = adapter.execute_query(connection=[], query="select 1", query_id="Q2")
    assert df_result["mode"] == "df"
    assert sql_result["mode"] == "sql"

    conn = []
    adapter._spark_initialized = True
    adapter._spark = SimpleNamespace(stop=lambda: conn.append("stopped"))
    adapter.close_connection(conn)
    assert conn == ["closed", "stopped"]
    assert adapter._spark is None and adapter._spark_initialized is False

    adapter._spark_initialized = True
    adapter._spark = SimpleNamespace(stop=lambda: (_ for _ in ()).throw(RuntimeError("stop error")))
    adapter.close_connection(conn)


def test_expression_helpers(monkeypatch: pytest.MonkeyPatch):
    adapter = _new_adapter()
    monkeypatch.setattr(mod, "PYSPARK_AVAILABLE", False)

    with pytest.raises(ImportError):
        adapter.col("x")
    with pytest.raises(ImportError):
        adapter.lit(1)
    with pytest.raises(ImportError):
        adapter.sum_col("x")
    with pytest.raises(ImportError):
        adapter.avg_col("x")
    with pytest.raises(ImportError):
        adapter.count_col()
    with pytest.raises(ImportError):
        adapter.min_col("x")
    with pytest.raises(ImportError):
        adapter.max_col("x")

    monkeypatch.setattr(mod, "PYSPARK_AVAILABLE", True)
    fake_f = SimpleNamespace(
        col=lambda n: ("col", n),
        lit=lambda v: ("lit", v),
        sum=lambda x: ("sum", x),
        avg=lambda x: ("avg", x),
        count=lambda x: ("count", x),
        min=lambda x: ("min", x),
        max=lambda x: ("max", x),
    )
    monkeypatch.setattr(mod, "F", fake_f)

    assert adapter.col("c") == ("col", "c")
    assert adapter.lit(7) == ("lit", 7)
    assert adapter.sum_col("c") == ("sum", ("col", "c"))
    assert adapter.avg_col("c") == ("avg", ("col", "c"))
    assert adapter.count_col("c") == ("count", ("col", "c"))
    assert adapter.count_col(None) == ("count", ("lit", 1))
    assert adapter.min_col("c") == ("min", ("col", "c"))
    assert adapter.max_col("c") == ("max", ("col", "c"))
