from unittest.mock import patch

import pytest

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.databricks import DatabricksAdapter
from benchbox.platforms.databricks.adapter import DATABRICKS_OAUTH_ENV_VARS

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]

GATEWAY_HOSTNAME = "workspace.espressocomputing.com"


@pytest.fixture(autouse=True)
def databricks_dependencies(monkeypatch):
    for name in (*DATABRICKS_OAUTH_ENV_VARS, "DATABRICKS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with (
        patch("benchbox.platforms.databricks.adapter.check_platform_dependencies", return_value=(True, [])),
        patch("benchbox.platforms.databricks.adapter.databricks_sql"),
    ):
        yield


def _config(**overrides):
    return {
        "server_hostname": GATEWAY_HOSTNAME,
        "http_path": "/sql/1.0/warehouses/test",
        "access_token": "mock-token",
        **overrides,
    }


def test_gateway_defaults_to_native():
    assert DatabricksAdapter(**_config()).gateway == "native"


@pytest.mark.parametrize("gateway", ["native", "espresso", "custom"])
def test_gateway_is_stored(gateway):
    assert DatabricksAdapter(**_config(gateway=gateway)).gateway == gateway


@pytest.mark.parametrize("gateway", ["espresso", "custom"])
def test_from_config_forwards_gateway_and_hostname_unchanged(gateway):
    adapter = DatabricksAdapter.from_config(_config(gateway=gateway))

    assert adapter.gateway == gateway
    assert adapter.server_hostname == GATEWAY_HOSTNAME
    assert adapter.http_path == "/sql/1.0/warehouses/test"
    assert adapter._get_connection_params()["server_hostname"] == GATEWAY_HOSTNAME


def test_from_config_without_gateway_is_native():
    assert DatabricksAdapter.from_config(_config()).gateway == "native"


def test_unknown_gateway_is_rejected():
    with pytest.raises(ConfigurationError, match="does not support gateway 'greybeam'"):
        DatabricksAdapter(**_config(gateway="greybeam"))


@pytest.mark.parametrize("env_var", DATABRICKS_OAUTH_ENV_VARS)
@pytest.mark.parametrize("gateway", ["espresso", "custom"])
def test_oauth_environment_with_gateway_is_rejected(monkeypatch, env_var, gateway):
    monkeypatch.setenv(env_var, "mock-value")

    with pytest.raises(ConfigurationError, match=f"personal access token.*{env_var}"):
        DatabricksAdapter(**_config(gateway=gateway))


def test_oauth_environment_with_gateway_is_rejected_from_config(monkeypatch):
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "mock-client")

    with pytest.raises(ConfigurationError, match="DATABRICKS_CLIENT_ID"):
        DatabricksAdapter.from_config(_config(gateway="espresso"))


@pytest.mark.parametrize("env_var", DATABRICKS_OAUTH_ENV_VARS)
def test_oauth_environment_with_native_is_allowed(monkeypatch, env_var):
    monkeypatch.setenv(env_var, "mock-value")

    assert DatabricksAdapter(**_config(gateway="native")).gateway == "native"
    assert DatabricksAdapter(**_config()).gateway == "native"


def _platform_info(adapter):
    with (
        patch.object(DatabricksAdapter, "_warehouse_id_from_http_path", return_value=None),
        patch.object(DatabricksAdapter, "_detect_databricks_region", return_value=None),
    ):
        return adapter.get_platform_info()


def _payload(adapter):
    from benchbox.core.results.models import QueryExecution
    from benchbox.core.results.schema import build_result_payload
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    result = make_benchmark_results(
        platform="databricks",
        platform_info=_platform_info(adapter),
        query_results=[QueryExecution(query_id="1", execution_time_ms=10, rows_returned=1)],
    )
    result.tpc_compliant = True
    return build_result_payload(result)


@pytest.mark.parametrize("gateway", ["espresso", "custom"])
def test_routed_run_is_recorded_with_gateway_name_and_variant(gateway):
    adapter = DatabricksAdapter(**_config(gateway=gateway))

    assert _platform_info(adapter)["gateway"] == {"name": gateway, "routed": True}
    payload = _payload(adapter)
    assert payload["platform"]["gateway"] == {"name": gateway, "routed": True}
    assert payload["platform"]["variant"].endswith(f"@{gateway}")
    assert payload["tpc_compliant"] is False


def test_native_run_emits_no_gateway():
    adapter = DatabricksAdapter(**_config())

    assert "gateway" not in _platform_info(adapter)
    payload = _payload(adapter)
    assert "gateway" not in payload["platform"]
    assert "@" not in payload["platform"]["variant"]


@pytest.mark.parametrize("gateway", ["espresso", "custom"])
@pytest.mark.parametrize("missing", ["server_hostname", "access_token"])
def test_from_config_with_gateway_requires_explicit_credentials(gateway, missing):
    config = _config(gateway=gateway)
    del config[missing]

    with (
        patch.object(DatabricksAdapter, "_auto_detect_databricks_config") as auto_detect,
        pytest.raises(ConfigurationError, match=f"explicit {missing}"),
    ):
        DatabricksAdapter.from_config(config)

    auto_detect.assert_not_called()


def test_from_config_with_gateway_rejects_placeholder_hostname():
    config = _config(gateway="espresso", server_hostname="your-workspace.cloud.databricks.com")

    with (
        patch.object(DatabricksAdapter, "_auto_detect_databricks_config") as auto_detect,
        pytest.raises(ConfigurationError, match="explicit server_hostname"),
    ):
        DatabricksAdapter.from_config(config)

    auto_detect.assert_not_called()


def test_from_config_with_gateway_uses_explicit_credentials_without_auto_detect():
    with patch.object(DatabricksAdapter, "_auto_detect_databricks_config") as auto_detect:
        adapter = DatabricksAdapter.from_config(_config(gateway="espresso"))

    auto_detect.assert_not_called()
    assert adapter.server_hostname == GATEWAY_HOSTNAME
    assert adapter.access_token == "mock-token"


def test_from_config_native_still_auto_detects_missing_credentials():
    detected = {
        "server_hostname": "native.cloud.databricks.com",
        "http_path": "/sql/1.0/warehouses/native",
        "access_token": "mock-native-token",
    }
    with patch.object(DatabricksAdapter, "_auto_detect_databricks_config", return_value=detected) as auto_detect:
        adapter = DatabricksAdapter.from_config({"catalog": "main"})

    auto_detect.assert_called_once()
    assert adapter.gateway == "native"
    assert adapter.server_hostname == "native.cloud.databricks.com"


def _full_info(adapter):
    info = _platform_info(adapter)
    info.update({"platform_type": "databricks", "region": "us-east-1"})
    return info


@pytest.mark.parametrize("gateway", ["espresso", "custom"])
def test_gateway_run_is_excluded_from_native_baseline_set(gateway):
    from benchbox.core.results.execution_variant import platform_info_bundle, variants_comparable

    native = platform_info_bundle("Databricks", _full_info(DatabricksAdapter(**_config())))
    routed = platform_info_bundle("Databricks", _full_info(DatabricksAdapter(**_config(gateway=gateway))))

    assert variants_comparable(native, native)
    assert variants_comparable(routed, routed)
    assert not variants_comparable(native, routed)
    assert not variants_comparable(routed, native)


def test_gateway_run_is_excluded_from_regression_baseline(tmp_path):
    import json

    from benchbox.core.results.analytics import _load_regression_runs
    from benchbox.core.results.execution_variant import platform_info_bundle

    def write(name, adapter, stamp):
        info = _full_info(adapter)
        bundle = platform_info_bundle("Databricks", info)
        bundle["platform"]["gateway"] = info.get("gateway", {})
        bundle["run"] = {"timestamp": stamp}
        bundle["benchmark"] = {"id": "tpch", "scale_factor": 0.01}
        bundle["queries"] = [{"run_type": "measurement", "id": "Q1", "ms": 100.0}]
        path = tmp_path / name
        path.write_text(json.dumps(bundle))
        return path

    paths = [
        write("a_native.json", DatabricksAdapter(**_config()), "2026-10-10T10:00:00Z"),
        write("b_gateway.json", DatabricksAdapter(**_config(gateway="espresso")), "2026-10-11T10:00:00Z"),
        write("c_native.json", DatabricksAdapter(**_config()), "2026-10-12T10:00:00Z"),
    ]
    runs, other = _load_regression_runs(paths, None, None, 5)

    assert [run["file"] for run in runs] == ["a_native.json", "c_native.json"]
    assert other == ["b_gateway.json"]


@pytest.mark.parametrize("gateway", ["espresso", "custom"])
def test_gateway_cost_is_not_comparable(monkeypatch, gateway):
    from benchbox.core.cost.integration import add_cost_estimation_to_results
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    monkeypatch.setattr("benchbox.core.cost.calculator.get_pricing_age_days", lambda table=None: 1)

    def priced(adapter):
        results = make_benchmark_results(
            benchmark_name="TPC-H",
            platform="databricks",
            scale_factor=1,
            platform_info=_full_info(adapter),
            query_results=[{"query_id": "Q1", "execution_time": 1.5, "resource_usage": {"dbu_used": 0.5}}],
        )
        return add_cost_estimation_to_results(results)

    routed = priced(DatabricksAdapter(**_config(gateway=gateway)))
    normalized = routed.cost_summary["normalized_cost"]
    assert normalized["cost_status"] == "not_comparable_gateway"
    assert normalized["normalized_cost_usd"] is None
    assert routed.query_results[0].get("cost") is None

    native = priced(DatabricksAdapter(**_config()))
    assert native.cost_summary["normalized_cost"]["cost_status"] != "not_comparable_gateway"


def test_gateway_platform_info_records_unverified_gateway_cache():
    info = _platform_info(DatabricksAdapter(**_config(gateway="espresso")))
    cache = info["configuration"]["cache_control"]
    assert cache["gateway_cache"] == "unverified"
    assert cache["warm_run_metrics"] == "cache_unverified"
    assert "cache_control" not in _platform_info(DatabricksAdapter(**_config()))["configuration"]


def test_gateway_normalized_metadata_reports_unverified_gateway_cache():
    adapter = DatabricksAdapter(**_config(gateway="espresso"))
    adapter._cache_control_receipt = {
        "validated": True,
        "cache_disabled": True,
        "settings": {"use_cached_result": "false"},
        "warnings": [],
        "errors": [],
    }
    with (
        patch.object(DatabricksAdapter, "_warehouse_id_from_http_path", return_value=None),
        patch.object(DatabricksAdapter, "_detect_databricks_region", return_value=None),
    ):
        cache = adapter.get_normalized_result_metadata()["platform_compute"]["cache_control"]
    assert cache["validated"] is True
    assert cache["cache_disabled"] is True
    assert cache["gateway_cache"] == "unverified"


def test_gateway_query_result_defaults_engine_to_unknown():
    from unittest.mock import MagicMock

    for gateway, expected in (("espresso", "unknown"), ("native", None)):
        adapter = DatabricksAdapter(**_config(gateway=gateway))
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [(1,)]
        with (
            patch.object(DatabricksAdapter, "_ensure_session_cache_disabled"),
            patch.object(DatabricksAdapter, "_merge_plan_capture_into_result"),
        ):
            result = adapter.execute_query(connection, "SELECT 1", "Q1", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert result.get("execution_engine") == expected
