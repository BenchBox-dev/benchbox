# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests as http_requests

from benchbox.core.exceptions import ConfigurationError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_MODULE = "benchbox.platforms.azure.synapse_spark_adapter"


def _make_adapter(**overrides):
    with (
        patch(f"{_MODULE}.AZURE_IDENTITY_AVAILABLE", True),
        patch(f"{_MODULE}.DefaultAzureCredential", MagicMock()),
        patch(f"{_MODULE}.CloudSparkStaging"),
    ):
        from benchbox.platforms.azure import SynapseSparkAdapter

        kwargs: dict[str, Any] = {
            "workspace_name": "ws",
            "spark_pool_name": "pool",
            "storage_account": "sa",
            "storage_container": "c",
        }
        kwargs.update(overrides)
        adapter = SynapseSparkAdapter(**kwargs)
        adapter._token_provider = MagicMock()
        adapter._token_provider.auth_headers.return_value = {
            "Authorization": "Bearer test",
            "Content-Type": "application/json",
        }
        return adapter


@contextlib.contextmanager
def _transport(*, get=None, post=None, delete=None):

    with (
        patch(f"{_MODULE}.requests.get") as mock_get,
        patch(f"{_MODULE}.requests.post") as mock_post,
        patch(f"{_MODULE}.requests.delete") as mock_delete,
    ):
        if get is not None:
            if isinstance(get, BaseException):
                mock_get.side_effect = get
            else:
                mock_get.return_value = get
        if post is not None:
            mock_post.return_value = post
        if delete is not None:
            if isinstance(delete, BaseException):
                mock_delete.side_effect = delete
            else:
                mock_delete.return_value = delete
        yield SimpleNamespace(get=mock_get, post=mock_post, delete=mock_delete)


def _response(status=200, payload=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload if payload is not None else {}
    resp.text = "err"
    return resp


@pytest.fixture
def adapter():
    return _make_adapter()


class TestInitGuards:
    def test_identity_missing_guard(self, monkeypatch):
        monkeypatch.setattr(f"{_MODULE}.AZURE_IDENTITY_AVAILABLE", False)
        monkeypatch.setattr(
            f"{_MODULE}.check_platform_dependencies",
            lambda *a, **k: (False, ["azure-identity"]),
        )
        from benchbox.platforms.azure import SynapseSparkAdapter

        with pytest.raises(ConfigurationError):
            SynapseSparkAdapter(
                workspace_name="ws",
                spark_pool_name="pool",
                storage_account="sa",
                storage_container="c",
            )

    def test_staging_init_failure_warns(self, caplog):
        with (
            patch(f"{_MODULE}.AZURE_IDENTITY_AVAILABLE", True),
            patch(f"{_MODULE}.DefaultAzureCredential", MagicMock()),
            patch(f"{_MODULE}.CloudSparkStaging") as staging,
        ):
            staging.from_uri.side_effect = RuntimeError("bad uri")
            from benchbox.platforms.azure import SynapseSparkAdapter

            with caplog.at_level("WARNING", logger="benchbox.platforms.azure.synapse_spark_adapter"):
                fresh = SynapseSparkAdapter(
                    workspace_name="ws",
                    spark_pool_name="pool",
                    storage_account="sa",
                    storage_container="c",
                )
        assert fresh._staging is None
        assert any("Failed to initialize ADLS staging" in r.getMessage() for r in caplog.records)


class TestSessionLifecycle:
    def test_create_session_without_requests_raises(self, adapter, monkeypatch):
        monkeypatch.setattr(f"{_MODULE}.REQUESTS_AVAILABLE", False)
        with pytest.raises(ConfigurationError, match="requests package"):
            adapter._create_session()

    def test_create_session_failure_status_raises(self, adapter):
        with _transport(post=_response(status=500)):
            with pytest.raises(ConfigurationError, match="Failed to create Livy session"):
                adapter._create_session()

    def test_create_session_returns_id(self, adapter):
        with (
            _transport(post=_response(status=201, payload={"id": 4})) as http,
            patch.object(adapter, "_wait_for_session_state", return_value="idle"),
        ):
            assert adapter._create_session() == 4
            http.post.assert_called_once()

    def test_wait_target_hit(self, adapter):
        with _transport(get=_response(payload={"state": "idle"})):
            assert adapter._wait_for_session_state(7, ["idle"]) == "idle"

    def test_wait_non_200_raises(self, adapter):
        with _transport(get=_response(status=503)):
            with pytest.raises(ConfigurationError, match="Failed to get session status"):
                adapter._wait_for_session_state(7, ["idle"])

    def test_wait_error_state_raises(self, adapter):
        with _transport(get=_response(payload={"state": "error"})):
            with pytest.raises(ConfigurationError, match="Session is in error state"):
                adapter._wait_for_session_state(7, ["idle"])

    def test_wait_timeout_raises(self, adapter):

        with _transport(), pytest.raises(ConfigurationError, match="Timeout waiting"):
            adapter._wait_for_session_state(7, ["idle"], timeout_seconds=0)

    def test_ensure_idle_returns_same_id(self, adapter):
        adapter._session_id = 3
        with _transport(get=_response(payload={"state": "idle"})):
            assert adapter._ensure_session() == 3

    def test_ensure_busy_waits(self, adapter):
        adapter._session_id = 3
        with (
            _transport(get=_response(payload={"state": "busy"})),
            patch.object(adapter, "_wait_for_session_state", return_value="idle") as waiter,
        ):
            assert adapter._ensure_session() == 3
        waiter.assert_called_once()

    def test_ensure_dead_closes_and_recreates(self, adapter):
        adapter._session_id = 3
        with (
            _transport(get=_response(payload={"state": "dead"})) as http,
            patch.object(adapter, "_create_session", return_value=9) as create,
        ):
            assert adapter._ensure_session() == 9
        http.delete.assert_called_once()
        create.assert_called_once()

    def test_ensure_invalid_get_recovers(self, adapter):
        adapter._session_id = 3
        with (
            _transport(get=RuntimeError("gone")) as http,
            patch.object(adapter, "_create_session", return_value=9),
        ):
            assert adapter._ensure_session() == 9
        http.delete.assert_called_once()


class TestConnectionMapping:
    def test_connected(self, adapter):
        with _transport(get=_response(payload={"sparkVersion": "3.4"})):
            result = adapter.create_connection()
        assert result["status"] == "connected"
        assert result["spark_version"] == "3.4"

    def test_transport_error(self, adapter):
        with _transport(get=http_requests.exceptions.ConnectionError("down")):
            with pytest.raises(ConfigurationError, match="Failed to connect to Synapse"):
                adapter.create_connection()


class TestExecuteAndClose:
    def test_execute_failure_envelope(self, adapter):
        with patch.object(adapter, "_execute_statement_measured", side_effect=RuntimeError("livy down")):
            result = adapter.execute_query(None, "SELECT 1", "q1")
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert result["error"] == "livy down"
        assert result["error_type"] == "RuntimeError"

    def test_close_skips_foreign_session(self, adapter):
        adapter._session_id = 5
        adapter._session_created_by_us = False
        with _transport() as http:
            adapter.close()
        http.delete.assert_not_called()
        assert adapter._session_id == 5

    def test_configure_ssb_and_unknown(self, adapter):
        adapter.configure_for_benchmark("ssb")
        assert adapter._benchmark_type == "ssb"
        assert adapter._spark_config
        adapter.configure_for_benchmark("weird-bench")
        assert adapter._benchmark_type == "weird-bench"
        assert adapter._spark_config


class TestWaitPollAndDeleteFailures:
    def test_poll_then_idle(self, adapter):
        states = [{"state": "running"}, {"state": "idle"}]
        with _transport() as http:
            http.get.side_effect = [_response(payload=s) for s in states]
            with patch("benchbox.platforms.azure.synapse_spark_adapter.time.sleep") as nap:
                assert adapter._wait_for_session_state(7, ["idle"]) == "idle"
        nap.assert_called_once()

    def test_dead_delete_failure_still_recreates(self, adapter):
        adapter._session_id = 3
        with (
            _transport(
                get=_response(payload={"state": "dead"}),
                delete=RuntimeError("denied"),
            ),
            patch.object(adapter, "_create_session", return_value=9),
        ):
            assert adapter._ensure_session() == 9

    def test_invalid_delete_failure_still_recreates(self, adapter):
        adapter._session_id = 3
        with (
            _transport(
                get=RuntimeError("gone"),
                delete=RuntimeError("denied"),
            ),
            patch.object(adapter, "_create_session", return_value=9),
        ):
            assert adapter._ensure_session() == 9


class TestCloseSuccess:
    def test_close_deletes_owned_session(self, adapter):
        adapter._session_id = 5
        adapter._session_created_by_us = True
        with _transport(delete=_response(payload={})) as http:
            adapter.close()
        http.delete.assert_called_once()
        assert adapter._session_id is None


class TestTuningNoOps:
    def test_apply_platform_tuning_merges(self, adapter):
        config = SimpleNamespace(spark_config={"spark.key": "v"})
        adapter._spark_config = {}
        adapter.apply_platform_tuning(config)
        assert adapter._spark_config == {"spark.key": "v"}

    def test_constraint_configuration_noops(self, adapter, caplog):
        with caplog.at_level("DEBUG", logger="benchbox.platforms.azure.synapse_spark_adapter"):
            adapter.apply_constraint_configuration(primary_keys=[SimpleNamespace()], foreign_keys=[SimpleNamespace()])
        messages = [r.getMessage() for r in caplog.records]
        assert any("Ignoring 1 primary key constraints" in m for m in messages)
        assert any("Ignoring 1 foreign key constraints" in m for m in messages)

    def test_from_config_tuning_passthrough(self):
        from benchbox.platforms.azure import SynapseSparkAdapter

        with (
            patch(f"{_MODULE}.AZURE_IDENTITY_AVAILABLE", True),
            patch(f"{_MODULE}.DefaultAzureCredential", MagicMock()),
            patch(f"{_MODULE}.CloudSparkStaging"),
        ):
            adapter = SynapseSparkAdapter.from_config(
                {
                    "workspace_name": "ws",
                    "spark_pool_name": "pool",
                    "storage_account": "sa",
                    "storage_container": "c",
                    "tuning_enabled": True,
                    "tuning_source": "cli",
                }
            )
        assert adapter.tuning_enabled is True
        assert adapter.tuning_source == "cli"


class TestImportFallback:
    def _reload(self):
        import importlib

        import benchbox.platforms.azure.synapse_spark_adapter as mod

        return importlib.reload(mod)

    def test_reload_with_identity_present(self):
        import sys
        import types

        identity = types.ModuleType("azure.identity")
        identity.DefaultAzureCredential = MagicMock(name="DefaultAzureCredential")
        saved = {k: sys.modules[k] for k in ("azure.identity",) if k in sys.modules}
        sys.modules["azure.identity"] = identity
        try:
            mod = self._reload()
            assert mod.AZURE_IDENTITY_AVAILABLE is True
        finally:
            sys.modules.pop("azure.identity", None)
            sys.modules.update(saved)
            self._reload()

    def test_reload_without_requests(self):
        import sys

        import benchbox.platforms.azure.synapse_spark_adapter as mod

        saved = {k: sys.modules[k] for k in ("requests",) if k in sys.modules}
        sys.modules["requests"] = None
        try:
            mod = self._reload()
            assert mod.REQUESTS_AVAILABLE is False
            assert mod.requests is None
        finally:
            sys.modules.pop("requests", None)
            sys.modules.update(saved)
            self._reload()
