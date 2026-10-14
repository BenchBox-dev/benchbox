from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from benchbox.core.results.anonymization import AnonymizationManager
from benchbox.core.results.models import QueryExecution
from benchbox.core.results.schema import SchemaV2Validator, build_result_payload
from benchbox.core.runner.runner import _enrich_normalized_runtime_metadata
from benchbox.platforms import get_adapter
from benchbox.utils.runtime_env import DriverResolution
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(autouse=True)
def _dependencies_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "benchbox.platforms.ensure_driver_version",
        lambda package_name, requested_version, auto_install=False, install_hint=None: DriverResolution(
            package=package_name or "",
            requested=requested_version,
            resolved="local-dev",
            actual="local-dev",
            auto_install_used=False,
            runtime_strategy="current-process",
            runtime_path=None,
            runtime_python_executable=sys.executable,
        ),
    )
    monkeypatch.setattr("benchbox.platforms.databricks.adapter.check_platform_dependencies", lambda *_: (True, []))


def _install_sdk(monkeypatch: pytest.MonkeyPatch, **warehouse_fields: Any) -> None:
    warehouse = types.SimpleNamespace(
        id="abc123",
        name="bench",
        cluster_size="Small",
        warehouse_type=types.SimpleNamespace(value="CLASSIC"),
        enable_serverless_compute=False,
        spot_instance_policy=None,
        channel=None,
        state=types.SimpleNamespace(value="RUNNING"),
        **warehouse_fields,
    )

    class _Workspace:
        def __init__(self, host: str | None = None, token: str | None = None) -> None:
            self.warehouses = types.SimpleNamespace(get=lambda warehouse_id: warehouse)
            self.metastores = types.SimpleNamespace(summary=lambda: types.SimpleNamespace(region="us-east-1"))

    sdk = types.ModuleType("databricks.sdk")
    sdk.WorkspaceClient = _Workspace
    monkeypatch.setitem(sys.modules, "databricks.sdk", sdk)


def _install_unreadable_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Workspace:
        def __init__(self, host: str | None = None, token: str | None = None) -> None:
            def forbidden(warehouse_id: str) -> Any:
                raise PermissionError("forbidden")

            self.warehouses = types.SimpleNamespace(get=forbidden)
            self.metastores = types.SimpleNamespace(summary=forbidden)

    sdk = types.ModuleType("databricks.sdk")
    sdk.WorkspaceClient = _Workspace
    monkeypatch.setitem(sys.modules, "databricks.sdk", sdk)


def _bundle() -> dict[str, Any]:
    adapter = get_adapter(
        "databricks",
        server_hostname="dbc-1.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/abc123",
        access_token="dapi-test",
        benchmark="tpch",
        scale_factor=0.01,
    )
    result = make_benchmark_results(
        platform="databricks",
        platform_info=adapter.get_platform_info(None),
        query_results=[QueryExecution(query_id="1", execution_time_ms=10, rows_returned=1)],
    )
    payload = build_result_payload(_enrich_normalized_runtime_metadata(result, adapter=adapter))
    SchemaV2Validator().validate(payload)
    return payload


def _present(receipt: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in receipt.items() if value is not None}


def _receipt(applied: str | None, applied_class: str | None, applied_native: dict[str, Any] | None) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "requested": "default",
        "applied": applied,
        "applied_class": applied_class,
        "resolution": "platform_default",
        "observed": "not_captured",
        "observed_source": "none",
    }
    if applied_native is not None:
        receipt["applied_native"] = applied_native
    return receipt


@pytest.mark.parametrize(
    ("enable_photon", "expected"),
    [
        (True, _receipt("photon", "native-vectorized", {"enable_photon": True})),
        (False, _receipt("standard", "standard", {"enable_photon": False})),
        (None, _receipt(None, None, None)),
    ],
    ids=["photon-on", "photon-off", "setting-empty"],
)
def test_bundle_records_the_warehouse_setting_as_applied_and_never_as_observed(
    monkeypatch: pytest.MonkeyPatch, enable_photon: bool | None, expected: dict[str, Any]
) -> None:
    _install_sdk(monkeypatch, enable_photon=enable_photon)

    payload = _bundle()

    assert _present(payload["platform"]["execution_engine"]) == _present(expected)
    assert payload["platform"]["variant"] == "databricks"
    public = AnonymizationManager().anonymize_result_payload(json.loads(json.dumps(payload)))
    assert _present(public["platform"]["execution_engine"]) == _present(expected)


def test_bundle_leaves_the_applied_engine_empty_when_the_warehouse_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_unreadable_sdk(monkeypatch)

    assert _present(_bundle()["platform"]["execution_engine"]) == _present(_receipt(None, None, None))
