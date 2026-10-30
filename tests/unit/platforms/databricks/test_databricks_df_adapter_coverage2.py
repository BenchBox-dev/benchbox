# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.expected_results.models import ValidationMode
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


def _new_adapter(**overrides) -> mod.DatabricksDataFrameAdapter:
    config = {
        "server_hostname": overrides.get("server_hostname", "host.cloud.databricks.com"),
        "http_path": overrides.get("http_path", "/sql/path"),
        "access_token": overrides.get("access_token", "dapi_token"),
        "catalog": overrides.get("catalog", "main"),
        "schema": overrides.get("schema", "bench"),
        "cluster_id": overrides.get("cluster_id"),
        "execution_mode": overrides.get("execution_mode", "dataframe"),
    }
    with (
        patch.object(mod.DatabricksAdapter, "__init__", _fake_parent_init),
        patch.object(mod, "DATABRICKS_CONNECT_AVAILABLE", True),
    ):
        return mod.DatabricksDataFrameAdapter(**config)


def _make_fake_spark(registered: list | None = None):

    class _FakeCatalog:
        def setCurrentCatalog(self, _c):
            pass

        def setCurrentDatabase(self, _d):
            pass

    class _FakeSpark:
        catalog = _FakeCatalog()

        def table(self, name):
            if registered is not None:
                registered.append(name)
            return f"tbl:{name}"

    return _FakeSpark()


def _make_result_df(rows=()):

    class _FakeResultDF:
        def collect(self):
            return list(rows)

    return _FakeResultDF()


def _mock_validator(*, warning=None, is_valid=True, error=None):
    result = MagicMock()
    result.expected_row_count = 2
    result.validation_mode = ValidationMode.EXACT
    result.warning_message = warning
    result.is_valid = is_valid
    result.error_message = error
    validator = MagicMock()
    validator.validate_query_result.return_value = result
    return validator, result


class TestInitWithConnectError:
    def test_falls_back_when_connect_error_message_set(self, monkeypatch: pytest.MonkeyPatch):
        _patch_parent_init(monkeypatch)
        monkeypatch.setattr(mod, "DATABRICKS_CONNECT_AVAILABLE", False)
        monkeypatch.setattr(mod, "_databricks_connect_error", "SparkSession.Hook missing")

        adapter = mod.DatabricksDataFrameAdapter(
            server_hostname="host",
            http_path="/sql/path",
            access_token="token",
            execution_mode="dataframe",
        )

        assert adapter.execution_mode == "sql"

    def test_dataframe_mode_preserved_when_connect_available(self, monkeypatch: pytest.MonkeyPatch):
        _patch_parent_init(monkeypatch)
        monkeypatch.setattr(mod, "DATABRICKS_CONNECT_AVAILABLE", True)

        adapter = mod.DatabricksDataFrameAdapter(
            server_hostname="host",
            http_path="/sql/path",
            access_token="token",
            execution_mode="dataframe",
            cluster_id="cl-1",
        )

        assert adapter.execution_mode == "dataframe"
        assert adapter.cluster_id == "cl-1"


class TestSparkSessionCredentialPaths:
    def _make_builder(self):
        calls: list[tuple[str, str]] = []

        class _Builder:
            def host(self, h):
                calls.append(("host", h))
                return self

            def token(self, t):
                calls.append(("token", t))
                return self

            def clusterId(self, c):
                calls.append(("clusterId", c))
                return self

            def getOrCreate(self):
                return SimpleNamespace(version="14.0", calls=calls)

        return _Builder(), calls

    def test_session_without_hostname(self, monkeypatch: pytest.MonkeyPatch):
        adapter = _new_adapter(server_hostname=None, cluster_id=None)
        builder, calls = self._make_builder()
        monkeypatch.setattr(mod, "DATABRICKS_CONNECT_AVAILABLE", True)
        monkeypatch.setattr(mod, "DatabricksSession", SimpleNamespace(builder=builder))

        adapter._get_or_create_spark_session()

        assert not any(k == "host" for k, _ in calls)

    def test_session_without_token(self, monkeypatch: pytest.MonkeyPatch):
        adapter = _new_adapter(access_token=None, cluster_id=None)
        builder, calls = self._make_builder()
        monkeypatch.setattr(mod, "DATABRICKS_CONNECT_AVAILABLE", True)
        monkeypatch.setattr(mod, "DatabricksSession", SimpleNamespace(builder=builder))

        adapter._get_or_create_spark_session()

        assert not any(k == "token" for k, _ in calls)

    def test_session_without_cluster_id(self, monkeypatch: pytest.MonkeyPatch):
        adapter = _new_adapter(cluster_id=None)
        builder, calls = self._make_builder()
        monkeypatch.setattr(mod, "DATABRICKS_CONNECT_AVAILABLE", True)
        monkeypatch.setattr(mod, "DatabricksSession", SimpleNamespace(builder=builder))

        adapter._get_or_create_spark_session()

        assert not any(k == "clusterId" for k, _ in calls)

    def test_session_with_all_credentials(self, monkeypatch: pytest.MonkeyPatch):
        adapter = _new_adapter(cluster_id="cl-42")
        builder, calls = self._make_builder()
        monkeypatch.setattr(mod, "DATABRICKS_CONNECT_AVAILABLE", True)
        monkeypatch.setattr(mod, "DatabricksSession", SimpleNamespace(builder=builder))

        spark = adapter._get_or_create_spark_session()

        assert spark.version == "14.0"
        assert ("host", "https://host.cloud.databricks.com") in calls
        assert ("token", "dapi_token") in calls
        assert ("clusterId", "cl-42") in calls

    def test_session_returns_cached(self, monkeypatch: pytest.MonkeyPatch):
        adapter = _new_adapter()
        existing = SimpleNamespace(version="13.0")
        adapter._spark = existing

        assert adapter._get_or_create_spark_session() is existing


class TestExecuteDataFrameQueryValidation:
    def _run(
        self,
        adapter,
        rows,
        *,
        query_id="Q1",
        validate=False,
        benchmark_type=None,
        scale_factor=None,
        stream_id=None,
        tables=None,
        validator_mock=None,
    ):
        registered: list[str] = []
        spark = _make_fake_spark(registered=registered if tables else None)
        result_df = _make_result_df(rows)

        kwargs = {
            "connection": None,
            "query_builder": lambda _s, _t: result_df,
            "query_id": query_id,
            "validate_row_count": validate,
            "benchmark_type": benchmark_type,
            "scale_factor": scale_factor,
            "stream_id": stream_id,
            "tables": tables,
        }

        with patch.object(adapter, "_get_or_create_spark_session", return_value=spark):
            if validator_mock is not None:
                with patch("benchbox.core.validation.query_validation.QueryValidator", return_value=validator_mock):
                    out = adapter.execute_dataframe_query(**kwargs)
            else:
                out = adapter.execute_dataframe_query(**kwargs)

        return out, registered

    def test_validation_called_with_correct_args(self):
        adapter = _new_adapter()
        validator, result = _mock_validator()

        out, _ = self._run(
            adapter,
            [(1, "a"), (2, "b")],
            validate=True,
            benchmark_type="tpch",
            scale_factor=1.0,
            validator_mock=validator,
        )

        assert out["status"] == "SUCCESS"
        assert out["rows_returned"] == 2
        validator.validate_query_result.assert_called_once_with(
            benchmark_type="tpch",
            query_id="Q1",
            actual_row_count=2,
            scale_factor=1.0,
            stream_id=None,
        )

    def test_validation_warning_is_logged(self):
        adapter = _new_adapter()
        logged: list[str] = []
        adapter.log_verbose = lambda msg, *_a, **_k: logged.append(msg)

        validator, _ = _mock_validator(warning="Row count slightly off")
        self._run(adapter, [(99,)], validate=True, benchmark_type="tpch", validator_mock=validator)

        assert any("Row count" in m for m in logged)

    def test_validation_failure_is_logged(self):
        adapter = _new_adapter()
        logged: list[str] = []
        adapter.log_verbose = lambda msg, *_a, **_k: logged.append(msg)

        validator, _ = _mock_validator(is_valid=False, error="Expected 1 row, got 0")
        self._run(adapter, [], validate=True, benchmark_type="tpch", validator_mock=validator)

        assert any("FAILED" in m for m in logged)

    def test_empty_result_sets_first_row_none(self):
        adapter = _new_adapter()
        out, _ = self._run(adapter, [], validate=False)
        assert out["rows_returned"] == 0
        assert out["first_row"] is None

    def test_tables_dict_registers_each_table(self):
        adapter = _new_adapter()
        out, registered = self._run(
            adapter,
            [(1,)],
            validate=False,
            tables={"lineitem": "/p/lineitem", "orders": "/p/orders"},
        )
        assert out["status"] == "SUCCESS"
        assert set(registered) == {"lineitem", "orders"}

    def test_stream_id_forwarded_to_validator(self):
        adapter = _new_adapter()
        validator, _ = _mock_validator()
        self._run(
            adapter,
            [(1,)],
            validate=True,
            benchmark_type="tpch",
            scale_factor=1.0,
            stream_id=3,
            validator_mock=validator,
        )

        call_kwargs = validator.validate_query_result.call_args.kwargs
        assert call_kwargs["stream_id"] == 3

    def test_result_has_execution_mode_and_resource_usage(self):
        adapter = _new_adapter()
        out, _ = self._run(adapter, [(42, "foo")], validate=False)
        assert out["execution_mode"] == "dataframe"
        assert "resource_usage" in out
        assert "execution_time_seconds" in out["resource_usage"]


class TestGetPlatformInfoSQLMode:
    def test_sql_mode_skips_spark_version(self, monkeypatch: pytest.MonkeyPatch):
        adapter = _new_adapter(execution_mode="sql")
        monkeypatch.setattr(
            mod.DatabricksAdapter,
            "get_platform_info",
            lambda self, connection=None: {"base": True},
        )

        info = adapter.get_platform_info()

        assert info["execution_mode"] == "sql"
        assert "spark_version" not in info
        assert info["databricks_connect_available"] == mod.DATABRICKS_CONNECT_AVAILABLE


class TestCloseConnectionNotInitialized:
    def test_close_without_spark(self, monkeypatch: pytest.MonkeyPatch):
        adapter = _new_adapter()
        close_calls: list = []
        monkeypatch.setattr(
            mod.DatabricksAdapter,
            "close_connection",
            lambda self, conn: close_calls.append(conn),
        )

        conn = MagicMock()
        adapter.close_connection(conn)

        assert close_calls == [conn]
        assert adapter._spark is None

    def test_close_with_spark_stops_session(self, monkeypatch: pytest.MonkeyPatch):
        adapter = _new_adapter()
        stopped: list = []
        adapter._spark = SimpleNamespace(stop=lambda: stopped.append(True))
        adapter._spark_initialized = True
        monkeypatch.setattr(mod.DatabricksAdapter, "close_connection", lambda self, conn: None)

        adapter.close_connection(MagicMock())

        assert stopped == [True]
        assert adapter._spark is None
        assert adapter._spark_initialized is False


class TestPlatformNameModes:
    def test_dataframe_mode_suffix(self, monkeypatch: pytest.MonkeyPatch):
        _patch_parent_init(monkeypatch)
        monkeypatch.setattr(mod, "DATABRICKS_CONNECT_AVAILABLE", True)
        adapter = mod.DatabricksDataFrameAdapter(
            server_hostname="host",
            http_path="/sql/path",
            access_token="token",
            execution_mode="dataframe",
        )
        assert adapter.platform_name == "Databricks-df"

    def test_sql_mode_no_suffix(self, monkeypatch: pytest.MonkeyPatch):
        _patch_parent_init(monkeypatch)
        monkeypatch.setattr(mod, "DATABRICKS_CONNECT_AVAILABLE", True)
        adapter = mod.DatabricksDataFrameAdapter(
            server_hostname="host",
            http_path="/sql/path",
            access_token="token",
            execution_mode="sql",
        )
        assert adapter.platform_name == "Databricks"


class TestExecuteQuerySQLDispatch:
    def test_kwargs_forwarded_to_parent(self, monkeypatch: pytest.MonkeyPatch):
        adapter = _new_adapter()
        received: dict = {}

        def _fake_parent(self, connection, query, query_id, **kwargs):
            received.update({"query": query, "query_id": query_id, **kwargs})
            return {"query_id": query_id, "status": "OK"}

        monkeypatch.setattr(mod.DatabricksAdapter, "execute_query", _fake_parent)

        result = adapter.execute_query(
            connection=MagicMock(),
            query="SELECT 1",
            query_id="TSQL",
            benchmark_type="tpch",
            scale_factor=0.1,
            validate_row_count=False,
            stream_id=7,
        )

        assert result["query_id"] == "TSQL"
        assert received["query"] == "SELECT 1"
        assert received["benchmark_type"] == "tpch"
        assert received["scale_factor"] == 0.1
        assert received["stream_id"] == 7
