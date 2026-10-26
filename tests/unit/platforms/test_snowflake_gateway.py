from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.core.exceptions import ConfigurationError
from benchbox.core.gateway import INSECURE_GATEWAY_ENV, GatewayConfigurationError, UnsupportedGatewayError
from benchbox.platforms.snowflake import SnowflakeAdapter

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.cloud_import]

BASE = {"account": "acct", "username": "user", "password": "mock-password", "warehouse": "WH", "database": "DB"}


@pytest.fixture(autouse=True)
def snowflake_dependencies(monkeypatch):
    monkeypatch.delenv(INSECURE_GATEWAY_ENV, raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    with (
        patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])),
        patch("benchbox.platforms.snowflake.snowflake") as mock_snowflake,
    ):
        yield mock_snowflake


def _adapter(**extra) -> SnowflakeAdapter:
    return SnowflakeAdapter(**BASE, **extra)


def test_native_adapter_has_no_gateway_endpoint() -> None:
    adapter = _adapter()
    assert adapter.gateway_endpoint is None
    params = adapter._connect_params({}, include_database=True)
    assert not {"host", "port", "protocol", "insecure_mode", "ocsp_fail_open"} & set(params)
    assert "gateway" not in adapter.get_platform_info()


def test_explicit_native_gateway_is_native() -> None:
    assert _adapter(gateway="native").gateway_endpoint is None


@pytest.mark.parametrize("gateway", ["espresso", "greybeam", "custom"])
def test_gateway_requires_host(gateway: str) -> None:
    with pytest.raises(GatewayConfigurationError, match="requires gateway_host"):
        _adapter(gateway=gateway)


def test_unknown_gateway_is_rejected_with_allowed_values() -> None:
    with pytest.raises(UnsupportedGatewayError, match="Allowed: native, custom, espresso, greybeam"):
        _adapter(gateway="bogus", gateway_host="gw.example.com")


@pytest.mark.parametrize(
    "option",
    [
        {"gateway_host": "gw.example.com"},
        {"gateway_port": 8443},
        {"gateway_protocol": "http"},
        {"gateway_ca_bundle": "/tmp/ca.pem"},
        {"gateway_ocsp_fail_open": True},
        {"allow_insecure_gateway": True},
    ],
)
def test_gateway_options_without_gateway_are_rejected(option: dict) -> None:
    with pytest.raises(GatewayConfigurationError, match="require a gateway other than native"):
        _adapter(**option)


def test_default_https_protocol_without_gateway_is_accepted() -> None:
    assert _adapter(gateway_protocol="https").gateway_endpoint is None


def test_gateway_maps_connection_params_for_benchmark_and_admin_paths(snowflake_dependencies) -> None:
    connect = snowflake_dependencies.connector.connect
    connect.return_value = Mock()
    adapter = _adapter(gateway="espresso", gateway_host="gw.example.com", gateway_port=8443)

    adapter.create_connection()
    benchmark_kwargs = connect.call_args[1]
    connect.reset_mock()
    adapter._create_admin_connection()
    admin_kwargs = connect.call_args[1]

    for kwargs in (benchmark_kwargs, admin_kwargs):
        assert kwargs["host"] == "gw.example.com"
        assert kwargs["port"] == 8443
        assert kwargs["protocol"] == "https"
        assert kwargs["account"] == "acct"
        assert "ocsp_fail_open" not in kwargs
        assert "insecure_mode" not in kwargs
    assert "database" in benchmark_kwargs
    assert "database" not in admin_kwargs


def test_port_omitted_when_not_configured() -> None:
    params = _adapter(gateway="custom", gateway_host="gw.example.com")._connect_params({}, include_database=False)
    assert "port" not in params
    assert params["host"] == "gw.example.com"


@pytest.mark.parametrize("value", [True, False])
def test_ocsp_fail_open_forwarded_only_when_set(value: bool) -> None:
    adapter = _adapter(gateway="custom", gateway_host="gw.example.com", gateway_ocsp_fail_open=value)
    assert adapter._connect_params({}, include_database=False)["ocsp_fail_open"] is value


@pytest.mark.parametrize(
    "host", ["alice:secret@gw.example.com", "https://alice:secret@gw.example.com/path", "gw.example.com/path"]
)
def test_host_with_credentials_or_scheme_is_rejected_without_echoing_it(host: str) -> None:
    with pytest.raises(GatewayConfigurationError, match="bare host name") as excinfo:
        _adapter(gateway="custom", gateway_host=host)
    assert "secret" not in str(excinfo.value)


def test_host_whitespace_is_trimmed() -> None:
    adapter = _adapter(gateway="custom", gateway_host=" gw.example.com ")
    assert adapter.gateway_endpoint is not None
    assert adapter.gateway_endpoint.host == "gw.example.com"


def test_missing_ca_bundle_file_raises(tmp_path: Path) -> None:
    with pytest.raises(GatewayConfigurationError, match="gateway_ca_bundle"):
        _adapter(gateway="custom", gateway_host="gw.example.com", gateway_ca_bundle=str(tmp_path / "absent.pem"))


def test_ca_bundle_requires_matching_process_environment(tmp_path: Path, monkeypatch) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("mock-ca", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="REQUESTS_CA_BUNDLE"):
        _adapter(gateway="custom", gateway_host="gw.example.com", gateway_ca_bundle=str(bundle))

    other = tmp_path / "other.pem"
    other.write_text("mock-ca", encoding="utf-8")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(other))
    with pytest.raises(ConfigurationError, match="REQUESTS_CA_BUNDLE"):
        _adapter(gateway="custom", gateway_host="gw.example.com", gateway_ca_bundle=str(bundle))

    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bundle))
    adapter = _adapter(gateway="custom", gateway_host="gw.example.com", gateway_ca_bundle=str(bundle))
    assert adapter.gateway_endpoint is not None
    assert adapter.gateway_endpoint.ca_bundle == str(bundle)


def test_connect_never_modifies_the_process_environment(tmp_path: Path, snowflake_dependencies, monkeypatch) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("mock-ca", encoding="utf-8")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bundle))
    seen: list[str | None] = []
    snowflake_dependencies.connector.connect.side_effect = lambda **_: seen.append(os.environ.get("REQUESTS_CA_BUNDLE"))
    adapter = _adapter(gateway="custom", gateway_host="gw.example.com", gateway_ca_bundle=str(bundle))

    adapter._create_admin_connection()
    assert seen == [str(bundle)]
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(bundle)


def test_insecure_gateway_requires_environment_opt_in() -> None:
    with pytest.raises(GatewayConfigurationError, match=INSECURE_GATEWAY_ENV):
        _adapter(gateway="custom", gateway_host="gw.example.com", allow_insecure_gateway=True)


def test_insecure_gateway_maps_insecure_mode_and_warns(monkeypatch, caplog) -> None:
    monkeypatch.setenv(INSECURE_GATEWAY_ENV, "1")
    with caplog.at_level("WARNING", logger="benchbox.core.gateway"):
        adapter = _adapter(gateway="custom", gateway_host="gw.example.com", allow_insecure_gateway=True)
    assert adapter._connect_params({}, include_database=False)["insecure_mode"] is True
    assert any("without TLS verification" in record.message for record in caplog.records)


def test_plain_http_to_remote_host_requires_environment_opt_in(monkeypatch, caplog) -> None:
    with pytest.raises(GatewayConfigurationError, match=INSECURE_GATEWAY_ENV):
        _adapter(gateway="custom", gateway_host="gw.example.com", gateway_protocol="http")

    monkeypatch.setenv(INSECURE_GATEWAY_ENV, "1")
    with caplog.at_level("WARNING", logger="benchbox.core.gateway"):
        adapter = _adapter(gateway="custom", gateway_host="gw.example.com", gateway_protocol="http")
    params = adapter._connect_params({}, include_database=False)
    assert params["protocol"] == "http"
    assert "insecure_mode" not in params
    assert caplog.records


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_plain_http_to_loopback_needs_no_opt_in(host: str) -> None:
    adapter = _adapter(gateway="custom", gateway_host=host, gateway_protocol="http")
    assert adapter._connect_params({}, include_database=False)["protocol"] == "http"


def test_from_config_forwards_gateway_fields() -> None:
    adapter = SnowflakeAdapter.from_config(
        {
            **BASE,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "gateway": "greybeam",
            "gateway_host": "gw.example.com",
            "gateway_port": 443,
            "gateway_protocol": "https",
            "gateway_ocsp_fail_open": False,
        }
    )
    endpoint = adapter.gateway_endpoint
    assert endpoint is not None
    assert (endpoint.name, endpoint.host, endpoint.port, endpoint.ocsp_fail_open) == (
        "greybeam",
        "gw.example.com",
        443,
        False,
    )


def test_from_config_native_default_has_no_endpoint() -> None:
    adapter = SnowflakeAdapter.from_config({**BASE, "benchmark": "tpch", "scale_factor": 0.01, "gateway": None})
    assert adapter.gateway_endpoint is None


def test_routed_platform_info_records_gateway() -> None:
    info = _adapter(gateway="greybeam", gateway_host="gw.example.com").get_platform_info()
    assert info["gateway"] == {"name": "greybeam", "routed": True}
    assert "gw.example.com" not in repr(info)


def test_connector_accepts_every_gateway_parameter(monkeypatch) -> None:
    connection = pytest.importorskip("snowflake.connector.connection")
    monkeypatch.setenv(INSECURE_GATEWAY_ENV, "1")
    adapter = _adapter(
        gateway="custom",
        gateway_host="gw.example.com",
        gateway_port=8443,
        gateway_ocsp_fail_open=False,
        allow_insecure_gateway=True,
    )
    params = adapter._connect_params({}, include_database=True)
    assert set(params) <= set(connection.DEFAULT_CONFIGURATION)


def test_explicit_gateway_options_reach_the_adapter_config() -> None:
    from benchbox.core.platform_config import get_platform_config
    from benchbox.core.schemas import DatabaseConfig

    database_config = DatabaseConfig(
        type="snowflake",
        name="Snowflake",
        gateway="espresso",
        options={"_explicit_platform_options": {"gateway_host": "gw.example.com", "gateway_port": 8443}},
    )
    platform_config = get_platform_config(database_config, None)
    assert platform_config["gateway"] == "espresso"
    assert platform_config["gateway_host"] == "gw.example.com"
    assert platform_config["gateway_port"] == 8443
    adapter = SnowflakeAdapter(**BASE, **{k: v for k, v in platform_config.items() if k.startswith("gateway")})
    assert adapter.gateway_endpoint is not None
    assert adapter.gateway_endpoint.port == 8443


def _gateway_adapter() -> SnowflakeAdapter:
    return _adapter(gateway="custom", gateway_host="gw.example.com")


def test_native_query_tag_is_legacy_format() -> None:
    adapter = _adapter()
    assert adapter._format_query_tag("Q1") == "BenchBox_Q1"


def test_gateway_query_tag_carries_stable_run_suffix() -> None:
    adapter = _gateway_adapter()
    suffix = adapter.gateway_run_suffix
    assert len(suffix) == 8
    assert adapter._format_query_tag("Q1") == f"BenchBox_Q1_gw-{suffix}"
    assert adapter._format_query_tag("Q2") == f"BenchBox_Q2_gw-{suffix}"
    assert _gateway_adapter().gateway_run_suffix != suffix


def test_gateway_execute_and_stats_lookup_use_same_tag() -> None:
    adapter = _gateway_adapter()
    tag = adapter._format_query_tag("Q1")
    connection = Mock()
    cursor = connection.cursor.return_value
    cursor.fetchone.return_value = ("sf-id", 10, 9, 1, 100, 5, 0.1, "MEDIUM", 1)

    cursor.fetchall.return_value = [(1,)]
    result = adapter.execute_query(connection, "SELECT 1", "Q1")
    assert result["status"] == "SUCCESS"
    executed = [call.args[0] for call in cursor.execute.call_args_list]
    assert f"ALTER SESSION SET QUERY_TAG = '{tag}'" in executed
    lookup = [sql for sql in executed if "QUERY_HISTORY" in sql]
    assert lookup
    assert all(f"WHERE QUERY_TAG = '{tag}'" in sql for sql in lookup)


def test_gateway_stats_lookup_returns_rows_under_gateway_tag() -> None:
    adapter = _gateway_adapter()
    connection = Mock()
    cursor = connection.cursor.return_value
    tag = adapter._format_query_tag("Q7")

    def execute(sql, *args, **kwargs):
        cursor.fetchone.return_value = (
            ("sf-id", 10, 9, 1, 100, 5, 0.1, "MEDIUM", 1) if f"QUERY_TAG = '{tag}'" in sql else None
        )

    cursor.execute.side_effect = execute
    stats = adapter._get_query_statistics(connection, "Q7")
    assert stats["snowflake_query_id"] == "sf-id"
    assert stats["execution_time_ms"] == 9
    assert stats["credits_used_cloud_services"] == 0.1


def test_native_stats_lookup_uses_legacy_tag() -> None:
    adapter = _adapter()
    connection = Mock()
    cursor = connection.cursor.return_value
    cursor.fetchone.return_value = None
    adapter._get_query_statistics(connection, "Q7")
    assert "WHERE QUERY_TAG = 'BenchBox_Q7'" in cursor.execute.call_args.args[0]


@pytest.mark.parametrize("force_recreate", [False, True])
def test_greybeam_query_only_missing_database_never_writes(snowflake_dependencies, force_recreate) -> None:
    adapter = _adapter(gateway="greybeam", gateway_host="gw.example.com", force_recreate=force_recreate)
    with (
        patch.object(adapter, "check_database_exists", return_value=False),
        patch.object(adapter, "_create_admin_connection") as admin,
        patch.object(adapter, "create_schema") as schema,
        patch.object(adapter, "load_data") as load,
        pytest.raises(ConfigurationError, match="replica"),
    ):
        adapter.run_benchmark(Mock(), test_execution_type="power", options={"requested_phases": ["power"]})
    admin.assert_not_called()
    schema.assert_not_called()
    load.assert_not_called()
    snowflake_dependencies.connector.connect.assert_not_called()


@pytest.mark.parametrize("reason", ["Missing tables: LINEITEM", "Row count mismatch", "Database empty"])
def test_greybeam_incompatible_database_never_resets(reason, snowflake_dependencies) -> None:
    adapter = _adapter(gateway="greybeam", gateway_host="gw.example.com")
    validation = Mock(
        is_valid=False, can_reuse=False, warnings=[], issues=[reason], database_empty=True, foreign_tables=()
    )
    with (
        patch.object(adapter, "check_database_exists", return_value=True),
        patch.object(adapter, "_validate_database_compatibility", return_value=validation),
        patch.object(adapter, "reset_database_in_place") as reset,
        patch.object(adapter, "drop_database") as drop,
        pytest.raises(ConfigurationError, match="replica"),
    ):
        adapter.create_connection()
    reset.assert_not_called()
    drop.assert_not_called()
    snowflake_dependencies.connector.connect.assert_not_called()


def test_greybeam_force_recreate_refused_before_database_checks(snowflake_dependencies) -> None:
    adapter = _adapter(gateway="greybeam", gateway_host="gw.example.com", force_recreate=True)
    with (
        patch.object(adapter, "check_database_exists") as exists,
        pytest.raises(ConfigurationError, match="replica"),
    ):
        adapter.create_connection()
    exists.assert_not_called()
    snowflake_dependencies.connector.connect.assert_not_called()


@pytest.mark.parametrize(
    "operation", ["drop_database", "reset_database_in_place", "create_schema", "load_data", "fresh"]
)
def test_greybeam_mutation_entrypoints_fail_before_sql(operation) -> None:
    adapter = _adapter(gateway="greybeam", gateway_host="gw.example.com")
    connection = Mock()
    with patch.object(adapter, "_create_admin_connection") as admin, pytest.raises(ConfigurationError):
        if operation == "create_schema":
            adapter.create_schema(Mock(), connection)
        elif operation == "load_data":
            adapter.load_data(Mock(), connection, Path("/tmp/data"))
        elif operation == "fresh":
            adapter._setup_fresh_database_phases(Mock(), connection, None)
        else:
            getattr(adapter, operation)()
    admin.assert_not_called()
    connection.cursor.assert_not_called()


def test_greybeam_compatible_database_can_connect(snowflake_dependencies) -> None:
    adapter = _adapter(gateway="greybeam", gateway_host="gw.example.com")
    validation = Mock(is_valid=True, warnings=[], issues=[])
    with (
        patch.object(adapter, "check_database_exists", return_value=True),
        patch.object(adapter, "_validate_database_compatibility", return_value=validation),
    ):
        adapter.create_connection()
    assert adapter.database_was_reused is True
    snowflake_dependencies.connector.connect.assert_called_once()


@pytest.mark.parametrize("flag,env,allowed", [(False, "1", False), (True, "", False), (True, "1", True)])
def test_greybeam_runtime_write_guard_requires_both_gates(monkeypatch, flag, env, allowed) -> None:
    monkeypatch.setenv("BENCHBOX_I_UNDERSTAND_GREYBEAM_LOAD", env)
    adapter = _adapter(gateway="greybeam", gateway_host="gw.example.com", allow_greybeam_inline_load=flag)
    if allowed:
        adapter._guard_greybeam_database_write()
    else:
        with pytest.raises(ConfigurationError):
            adapter._guard_greybeam_database_write()


def test_greybeam_double_gated_override_allows_missing_database_connection(monkeypatch, snowflake_dependencies) -> None:
    monkeypatch.setenv("BENCHBOX_I_UNDERSTAND_GREYBEAM_LOAD", "1")
    adapter = _adapter(gateway="greybeam", gateway_host="gw.example.com", allow_greybeam_inline_load=True)
    with patch.object(adapter, "check_database_exists", return_value=False):
        adapter.create_connection()
    assert adapter.database_was_reused is False
    snowflake_dependencies.connector.connect.assert_called_once()


def test_lifecycle_propagates_and_resets_inline_load_permission() -> None:
    from benchbox.core.runner.runner import ValidationOptions, _configure_lifecycle_adapter
    from benchbox.core.schemas import BenchmarkConfig
    from benchbox.utils.verbosity import VerbositySettings

    adapter = _adapter(gateway="greybeam", gateway_host="gw.example.com")
    config = BenchmarkConfig(
        name="tpch", display_name="TPC-H", scale_factor=1, options={"allow_greybeam_inline_load": True}
    )
    _configure_lifecycle_adapter(
        adapter, None, None, Mock(), config, ValidationOptions(), VerbositySettings(), "native"
    )
    assert adapter.allow_greybeam_inline_load is True
    config.options.clear()
    _configure_lifecycle_adapter(
        adapter, None, None, Mock(), config, ValidationOptions(), VerbositySettings(), "native"
    )
    assert adapter.allow_greybeam_inline_load is False


@pytest.mark.parametrize("context_flag", ["_validating_database", "dry_run", "dry_run_mode"])
@pytest.mark.parametrize("force_recreate", [False, True])
def test_greybeam_read_only_context_does_not_decide_reuse(context_flag, force_recreate) -> None:
    adapter = _adapter(gateway="greybeam", gateway_host="gw.example.com", force_recreate=force_recreate)
    setattr(adapter, context_flag, True)
    with patch.object(adapter, "check_database_exists") as exists:
        adapter.handle_existing_database()
    exists.assert_not_called()
    assert adapter.database_was_reused is False
    assert not getattr(adapter, "_existing_db_decided", False)
    with pytest.raises(ConfigurationError):
        adapter._setup_fresh_database_phases(Mock(), Mock(), None)
    with pytest.raises(ConfigurationError):
        adapter._remove_database(False, "")


class _TPCHSmokeBenchmark:
    def get_schema(self):
        return {"orders": {"columns": []}}


@pytest.mark.parametrize("allow_inline_load,row_count", [(False, 1_500_000), (True, 1_500_000), (False, 0)])
def test_greybeam_query_only_real_validation_smoke(
    snowflake_dependencies, monkeypatch, allow_inline_load, row_count
) -> None:
    monkeypatch.setenv("BENCHBOX_I_UNDERSTAND_GREYBEAM_LOAD", "1" if allow_inline_load else "")
    adapter = _adapter(gateway="greybeam", gateway_host="gw.example.com", allow_greybeam_inline_load=allow_inline_load)
    adapter.benchmark_instance = _TPCHSmokeBenchmark()
    adapter.scale_factor = 1
    statements = []
    validation_connections = []

    def connect(**kwargs):
        validation_connections.append(getattr(adapter, "_validating_database", False))
        connection = Mock(spec=["cursor", "close"])
        cursor = Mock(spec=["execute", "fetchone", "fetchall", "close"])
        connection.cursor.return_value = cursor

        def execute(sql, *args):
            normalized = " ".join(sql.upper().split())
            statements.append(normalized)
            if "BENCHBOX_TUNING_METADATA" in normalized:
                raise RuntimeError("BENCHBOX_TUNING_METADATA does not exist")
            if normalized == "SHOW DATABASES":
                rows = [(None, "DB")]
            elif normalized == "SHOW TABLES":
                rows = [(None, "ORDERS")]
            elif normalized == "SELECT CURRENT_VERSION()":
                rows = [("mock-version",)]
            elif normalized == "SELECT COUNT(*) FROM ORDERS":
                rows = [(row_count,)]
            elif normalized.startswith("ALTER SESSION SET QUERY_TAG") or "QUERY_HISTORY" in normalized:
                rows = []
            else:
                raise AssertionError(f"Unexpected SQL in query-only smoke: {sql}")
            cursor.fetchone.return_value = rows[0] if rows else None
            cursor.fetchall.return_value = rows
            return cursor

        cursor.execute.side_effect = execute
        return connection

    snowflake_dependencies.connector.connect.side_effect = connect
    with patch.object(adapter, "reset_database_in_place") as reset, patch.object(adapter, "drop_database") as drop:
        if row_count:
            connection = adapter.create_connection()
            assert adapter.database_was_reused is True
            result = adapter.execute_query(connection, "SELECT COUNT(*) FROM orders", "smoke")
            assert result["status"] == "SUCCESS"
            assert result["first_row"] == (row_count,)
            adapter.close_connection(connection)
        else:
            with pytest.raises(ConfigurationError, match="replica"):
                adapter.create_connection()
        reset.assert_not_called()
        drop.assert_not_called()
    assert validation_connections.count(True) == 2
    assert "SHOW TABLES" in statements
    assert "SELECT COUNT(*) FROM ORDERS" in statements
    assert not any(
        sql.startswith(("CREATE ", "DROP ", "TRUNCATE ", "INSERT ", "UPDATE ", "DELETE ")) for sql in statements
    )
