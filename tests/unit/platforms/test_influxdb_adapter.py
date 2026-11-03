# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import importlib
import os
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.influxdb import InfluxDBAdapter
from benchbox.platforms.influxdb._dependencies import INFLUXDB_AVAILABLE
from benchbox.platforms.influxdb.client import InfluxDBConnection

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestInfluxDBAdapterInitialization:
    def test_initialization_cloud_mode_with_token(self):
        try:
            adapter = InfluxDBAdapter(
                host="us-east-1-1.aws.cloud2.influxdata.com",
                token="test-token",
                org="test-org",
                database="benchmarks",
                mode="cloud",
            )
        except ImportError:
            pytest.skip("InfluxDB client not installed")

        assert adapter.mode == "cloud"
        assert adapter.host == "us-east-1-1.aws.cloud2.influxdata.com"
        assert adapter.token == "test-token"
        assert adapter.org == "test-org"
        assert adapter.database == "benchmarks"
        assert adapter.ssl is True
        assert adapter.platform_name == "InfluxDB (Cloud)"
        assert adapter.get_target_dialect() == "influxdb"

    def test_initialization_core_mode(self):
        try:
            adapter = InfluxDBAdapter(
                host="localhost",
                port=8086,
                token="test-token",
                database="benchmarks",
                mode="core",
                ssl=False,
            )
        except ImportError:
            pytest.skip("InfluxDB client not installed")

        assert adapter.mode == "core"
        assert adapter.host == "localhost"
        assert adapter.port == 8086
        assert adapter.ssl is False
        assert adapter.platform_name == "InfluxDB (Core)"

    def test_initialization_defaults(self):
        try:
            adapter = InfluxDBAdapter(token="test-token")
        except ImportError:
            pytest.skip("InfluxDB client not installed")

        assert adapter.host == "localhost"
        assert adapter.port == 8086
        assert adapter.database == "benchbox"
        assert adapter.mode == "cloud"
        assert adapter.ssl is True

    def test_initialization_token_from_env(self):
        original_token = os.environ.get("INFLUXDB_TOKEN")
        try:
            os.environ["INFLUXDB_TOKEN"] = "env-test-token"
            adapter = InfluxDBAdapter(host="localhost")
            assert adapter.token == "env-test-token"
        except ImportError:
            pytest.skip("InfluxDB client not installed")
        finally:
            if original_token:
                os.environ["INFLUXDB_TOKEN"] = original_token
            else:
                os.environ.pop("INFLUXDB_TOKEN", None)

    def test_initialization_invalid_mode(self):
        try:
            with pytest.raises(ValueError, match="does not support deployment mode"):
                InfluxDBAdapter(token="test", mode="invalid")
        except ImportError:
            pytest.skip("InfluxDB client not installed")


class TestInfluxDBAdapterMetadata:
    def test_platform_name_cloud(self):
        try:
            adapter = InfluxDBAdapter(token="test", mode="cloud")
            assert adapter.platform_name == "InfluxDB (Cloud)"
        except ImportError:
            pytest.skip("InfluxDB client not installed")

    def test_platform_name_core(self):
        try:
            adapter = InfluxDBAdapter(token="test", mode="core")
            assert adapter.platform_name == "InfluxDB (Core)"
        except ImportError:
            pytest.skip("InfluxDB client not installed")

    def test_get_target_dialect(self):
        try:
            adapter = InfluxDBAdapter(token="test")
            assert adapter.get_target_dialect() == "influxdb"
        except ImportError:
            pytest.skip("InfluxDB client not installed")

    def test_get_platform_info(self):
        try:
            adapter = InfluxDBAdapter(
                host="localhost",
                port=8086,
                token="test-token",
                database="test_db",
                mode="core",
            )
            info = adapter.get_platform_info()

            assert info["platform_type"] == "influxdb"
            assert "InfluxDB" in info["platform_name"]
            assert info["connection_mode"] == "core"
            assert "configuration" in info
            assert info["configuration"]["host"] == "localhost"
            assert info["configuration"]["port"] == 8086
            assert info["configuration"]["database"] == "test_db"
        except ImportError:
            pytest.skip("InfluxDB client not installed")


class TestInfluxDBAdapterFromConfig:
    def test_from_config_basic(self):
        config = {
            "host": "influx.example.com",
            "port": 443,
            "token": "config-token",
            "org": "my-org",
            "database": "config-db",
            "ssl": True,
            "mode": "cloud",
        }

        try:
            adapter = InfluxDBAdapter.from_config(config)
            assert adapter.host == "influx.example.com"
            assert adapter.port == 443
            assert adapter.token == "config-token"
            assert adapter.org == "my-org"
            assert adapter.database == "config-db"
            assert adapter.mode == "cloud"
        except ImportError:
            pytest.skip("InfluxDB client not installed")

    def test_from_config_with_defaults(self):
        config = {"token": "test-token"}

        try:
            adapter = InfluxDBAdapter.from_config(config)
            assert adapter.host == "localhost"
            assert adapter.port == 8086
            assert adapter.database == "benchbox"
            assert adapter.mode == "cloud"
        except ImportError:
            pytest.skip("InfluxDB client not installed")


class TestInfluxDBConnection:
    def test_connection_initialization(self):
        conn = InfluxDBConnection(
            host="localhost",
            token="test-token",
            database="test_db",
            port=8086,
            ssl=False,
            org="test-org",
        )

        assert conn.host == "localhost"
        assert conn.token == "test-token"
        assert conn.database == "test_db"
        assert conn.port == 8086
        assert conn.ssl is False
        assert conn.org == "test-org"
        assert conn._url == "http://localhost:8086"
        assert conn.is_connected is False

    def test_connection_url_https(self):
        conn = InfluxDBConnection(
            host="influx.example.com",
            token="test-token",
            database="test_db",
            port=443,
            ssl=True,
        )

        assert conn._url == "https://influx.example.com:443"

    def test_connection_not_connected_raises(self):
        conn = InfluxDBConnection(
            host="localhost",
            token="test-token",
            database="test_db",
        )

        with pytest.raises(RuntimeError, match="Not connected"):
            conn.execute("SELECT 1")

    def test_connection_no_client_available(self):
        conn = InfluxDBConnection(
            host="localhost",
            token="test-token",
            database="test_db",
        )

        with (
            patch("benchbox.platforms.influxdb.client.INFLUXDB3_AVAILABLE", False),
            patch("benchbox.platforms.influxdb.client.FLIGHTSQL_AVAILABLE", False),
            pytest.raises(ImportError, match="No InfluxDB client library available"),
        ):
            conn.connect()


class TestInfluxDBAdapterDependencies:
    def test_influxdb_available_flag(self):
        assert isinstance(INFLUXDB_AVAILABLE, bool)

    def test_adapter_import_without_deps(self):
        from benchbox.platforms.influxdb import InfluxDBAdapter

        assert InfluxDBAdapter is not None


class TestInfluxDBAdapterIntegrationPoints:
    def test_adapter_in_platform_registry(self):
        from benchbox.core.platform_registry import PlatformRegistry

        platforms = PlatformRegistry.get_available_platforms()
        assert isinstance(platforms, list)

    def test_adapter_metadata_in_registry(self):
        from benchbox.core.platform_registry import PlatformRegistry

        metadata = PlatformRegistry.get_all_platform_metadata()
        assert "influxdb" in metadata
        assert metadata["influxdb"]["display_name"] == "InfluxDB"
        assert metadata["influxdb"]["category"] == "timeseries"
        assert "flightsql" in metadata["influxdb"]["supports"]

    def test_influxdb_dialect_in_tsbs_schema(self):
        from benchbox.core.tsbs_devops.schema import _map_type_to_dialect

        assert _map_type_to_dialect("TIMESTAMP", "influxdb") == "TIMESTAMP"
        assert _map_type_to_dialect("DOUBLE", "influxdb") == "DOUBLE"
        assert _map_type_to_dialect("VARCHAR(255)", "influxdb") == "STRING"
        assert _map_type_to_dialect("BIGINT", "influxdb") == "BIGINT"


class TestLineProtocol:
    def test_to_line_protocol_basic(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        line = to_line_protocol(
            measurement="cpu",
            tags={"hostname": "host_0"},
            fields={"usage_user": 50.5, "usage_system": 10.2},
            timestamp=None,
        )
        assert line == "cpu,hostname=host_0 usage_user=50.5,usage_system=10.2"

    def test_to_line_protocol_with_timestamp(self):
        from datetime import datetime, timezone

        from benchbox.platforms.influxdb.client import to_line_protocol

        ts = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        line = to_line_protocol(
            measurement="cpu",
            tags={"hostname": "host_0"},
            fields={"usage_user": 50.5},
            timestamp=ts,
        )
        assert line.startswith("cpu,hostname=host_0 usage_user=50.5 ")
        assert line.split()[-1].isdigit()

    def test_to_line_protocol_integer_fields(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        line = to_line_protocol(
            measurement="mem",
            tags={"hostname": "host_0"},
            fields={"total": 16000000000, "used": 8000000000},
            timestamp=None,
        )
        assert "total=16000000000i" in line
        assert "used=8000000000i" in line

    def test_to_line_protocol_string_fields(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        line = to_line_protocol(
            measurement="events",
            tags={"hostname": "host_0"},
            fields={"message": "disk full"},
            timestamp=None,
        )
        assert 'message="disk full"' in line

    def test_to_line_protocol_escape_tag_values(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        line = to_line_protocol(
            measurement="cpu",
            tags={"hostname": "host with space,equals=test"},
            fields={"value": 1.0},
            timestamp=None,
        )
        assert "hostname=host\\ with\\ space\\,equals\\=test" in line

    def test_to_line_protocol_no_tags(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        line = to_line_protocol(
            measurement="cpu",
            tags={},
            fields={"usage": 50.0},
            timestamp=None,
        )
        assert line == "cpu usage=50.0"

    def test_to_line_protocol_empty_fields_raises(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        with pytest.raises(ValueError, match="At least one field is required"):
            to_line_protocol(
                measurement="cpu",
                tags={"hostname": "host_0"},
                fields={},
                timestamp=None,
            )

    def test_to_line_protocol_multiple_tags_sorted(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        line = to_line_protocol(
            measurement="disk",
            tags={"hostname": "host_0", "device": "sda"},
            fields={"reads": 100},
            timestamp=None,
        )
        assert line.startswith("disk,device=sda,hostname=host_0")


class TestInfluxDBConnectionWrite:
    def test_write_line_protocol_not_connected_raises(self):
        conn = InfluxDBConnection(
            host="localhost",
            token="test-token",
            database="test_db",
        )

        with pytest.raises(RuntimeError, match="Not connected"):
            conn.write_line_protocol(["cpu,host=a value=1.0"])

    def test_write_line_protocol_flightsql_raises(self):
        conn = InfluxDBConnection(
            host="localhost",
            token="test-token",
            database="test_db",
        )
        conn._client = object()
        conn._client_type = "flightsql"

        with pytest.raises(RuntimeError, match="Write operations require influxdb3-python"):
            conn.write_line_protocol(["cpu,host=a value=1.0"])


class TestInfluxDBTlsCertValidation:
    def test_connection_defaults_verify_ssl_true_no_ca_cert(self):
        conn = InfluxDBConnection(host="localhost", token="tok", database="db")
        assert conn.verify_ssl is True
        assert conn.ca_cert_path is None

    def test_connection_accepts_verify_ssl_false(self):
        conn = InfluxDBConnection(host="localhost", token="tok", database="db", verify_ssl=False)
        assert conn.verify_ssl is False

    def test_connection_accepts_ca_cert_path(self):
        conn = InfluxDBConnection(host="localhost", token="tok", database="db", ca_cert_path="/etc/ssl/ca.crt")
        assert conn.ca_cert_path == "/etc/ssl/ca.crt"

    def test_adapter_defaults_verify_ssl_true_no_ca_cert(self):
        try:
            from benchbox.platforms.influxdb import InfluxDBAdapter

            adapter = InfluxDBAdapter(token="test")
            assert adapter.verify_ssl is True
            assert adapter.ca_cert_path is None
        except ImportError:
            pytest.skip("InfluxDB client not installed")

    def test_adapter_accepts_verify_ssl_false(self):
        try:
            from benchbox.platforms.influxdb import InfluxDBAdapter

            adapter = InfluxDBAdapter(token="test", verify_ssl=False)
            assert adapter.verify_ssl is False
        except ImportError:
            pytest.skip("InfluxDB client not installed")

    def test_adapter_from_config_passes_tls_options(self):
        try:
            from benchbox.platforms.influxdb import InfluxDBAdapter

            config = {
                "token": "test-token",
                "host": "influxdb.example.com",
                "verify_ssl": False,
                "ca_cert_path": "/etc/ssl/my-ca.crt",
            }
            adapter = InfluxDBAdapter.from_config(config)
            assert adapter.verify_ssl is False
            assert adapter.ca_cert_path == "/etc/ssl/my-ca.crt"
        except ImportError:
            pytest.skip("InfluxDB client not installed")

    def test_influxdb3_client_receives_verify_ssl_false(self):
        conn = InfluxDBConnection(host="localhost", token="tok", database="db", verify_ssl=False)
        mock_client = Mock()
        with patch("benchbox.platforms.influxdb.client.InfluxDBClient3", mock_client):
            with patch("benchbox.platforms.influxdb.client.INFLUXDB3_AVAILABLE", True):
                conn._connect_influxdb3()
                _, kwargs = mock_client.call_args
                assert kwargs["verify_ssl"] is False

    def test_influxdb3_client_receives_ca_cert_path(self):
        conn = InfluxDBConnection(host="localhost", token="tok", database="db", ca_cert_path="/etc/ssl/ca.crt")
        mock_client = Mock()
        with patch("benchbox.platforms.influxdb.client.InfluxDBClient3", mock_client):
            with patch("benchbox.platforms.influxdb.client.INFLUXDB3_AVAILABLE", True):
                conn._connect_influxdb3()
                _, kwargs = mock_client.call_args
                assert kwargs["ssl_ca_cert"] == "/etc/ssl/ca.crt"

    def test_influxdb3_client_omits_tls_kwargs_when_defaults(self):
        conn = InfluxDBConnection(host="localhost", token="tok", database="db")
        mock_client = Mock()
        with patch("benchbox.platforms.influxdb.client.InfluxDBClient3", mock_client):
            with patch("benchbox.platforms.influxdb.client.INFLUXDB3_AVAILABLE", True):
                conn._connect_influxdb3()
                _, kwargs = mock_client.call_args
                assert "verify_ssl" not in kwargs
                assert "ssl_ca_cert" not in kwargs

    def test_flightsql_client_receives_disable_server_verification(self):
        conn = InfluxDBConnection(host="localhost", token="tok", database="db", verify_ssl=False)
        mock_client = Mock()
        with patch("benchbox.platforms.influxdb.client.FlightSQLClient", mock_client):
            with patch("benchbox.platforms.influxdb.client.INFLUXDB3_AVAILABLE", False):
                with patch("benchbox.platforms.influxdb.client.FLIGHTSQL_AVAILABLE", True):
                    conn._connect_flightsql()
                    _, kwargs = mock_client.call_args
                    assert kwargs["disable_server_verification"] is True


class TestInfluxDBPlatformOptionParsing:
    def test_ssl_and_port_options_are_typed(self):
        import benchbox.platforms
        from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

        parsed = PlatformHookRegistry.parse_options("influxdb", [("ssl", "false"), ("port", "8181")])
        assert parsed["ssl"] is False
        assert parsed["port"] == 8181

    def test_ssl_and_port_defaults_are_typed(self):
        import benchbox.platforms
        from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

        parsed = PlatformHookRegistry.parse_options("influxdb", [])
        assert parsed["ssl"] is True
        assert parsed["port"] == 8086
