from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from benchbox.platforms.base.connection_lifecycle import ConnectionLifecycleMixin
from benchbox.platforms.influxdb.setup import InfluxDBSetupMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Adapter(InfluxDBSetupMixin, ConnectionLifecycleMixin):
    def __init__(self):
        self.logger = MagicMock()
        self.force_recreate = False
        self.dry_run = False
        self.dry_run_mode = False
        self.log_operation_start = MagicMock()
        self.log_verbose = MagicMock()
        self.log_very_verbose = MagicMock()
        self.host = "localhost"
        self.port = 8086
        self.token = None
        self.org = None
        self.database = "benchbox"
        self.ssl = True
        self.verify_ssl = True
        self.ca_cert_path = None
        self.mode = "cloud"


def test_setup_connection_params_defaults_and_overrides():
    adapter = _Adapter()
    adapter._setup_connection_params({"host": "influx.local", "token": "abc", "mode": "core", "ssl": False})

    assert adapter.host == "influx.local"
    assert adapter.token == "abc"
    assert adapter.mode == "core"
    assert adapter.ssl is False


def test_create_connection_success_and_close(monkeypatch):
    adapter = _Adapter()
    adapter.token = "abc"
    fake_conn = MagicMock()
    fake_conn.test_connection.return_value = True

    monkeypatch.setattr("benchbox.platforms.influxdb.setup.InfluxDBConnection", lambda **kwargs: fake_conn)

    conn = adapter.create_connection(host="localhost", port=8086)
    assert conn is fake_conn
    fake_conn.connect.assert_called_once_with()
    adapter.close_connection(conn)
    fake_conn.close.assert_called_once_with()


def test_create_connection_failure_paths(monkeypatch):
    adapter = _Adapter()
    adapter.token = "abc"
    fake_conn = MagicMock()
    fake_conn.test_connection.return_value = False
    monkeypatch.setattr("benchbox.platforms.influxdb.setup.InfluxDBConnection", lambda **kwargs: fake_conn)

    with pytest.raises(ConnectionError, match="Connection test failed"):
        adapter.create_connection()

    noisy_conn = MagicMock()
    noisy_conn.close.side_effect = RuntimeError("boom")
    adapter.close_connection(noisy_conn)
    adapter.logger.warning.assert_called()


def test_handle_existing_database_fails_closed_on_force_recreate():
    adapter = _Adapter()
    adapter.force_recreate = True

    with pytest.raises(RuntimeError, match="InfluxDB does not support --force-recreate") as excinfo:
        adapter.handle_existing_database()

    assert "manually" in str(excinfo.value).lower()
    assert adapter.get_database_path() is None


def test_handle_existing_database_ignores_force_recreate_on_dry_run():
    adapter = _Adapter()
    adapter.force_recreate = True
    adapter.dry_run = True

    adapter.handle_existing_database(database="benchbox_test")


def test_handle_existing_database_delegates_to_base_without_force_recreate():
    adapter = _Adapter()

    adapter.handle_existing_database(database="benchbox_test")


def test_check_benchmark_tables_exist_returns_true_when_all_tables_present(monkeypatch):
    adapter = _Adapter()
    adapter.benchmark = MagicMock()
    adapter.benchmark.tables = {"cpu": None, "mem": None}
    adapter.get_tables = MagicMock(return_value=["cpu", "mem"])
    fake_conn = MagicMock()
    monkeypatch.setattr("benchbox.platforms.influxdb.setup.InfluxDBConnection", lambda **kwargs: fake_conn)

    assert adapter.check_benchmark_tables_exist() is True

    fake_conn.connect.assert_called_once_with()
    adapter.get_tables.assert_called_once_with(fake_conn)
    fake_conn.close.assert_called_once_with()


def test_check_benchmark_tables_exist_uses_expected_table_resolver(monkeypatch):
    adapter = _Adapter()
    adapter.benchmark = SimpleNamespace(tables={})
    adapter._get_expected_tables = MagicMock(return_value=["cpu", "mem"])
    adapter.get_tables = MagicMock(return_value=["CPU", "mem"])
    fake_conn = MagicMock()
    monkeypatch.setattr("benchbox.platforms.influxdb.setup.InfluxDBConnection", lambda **kwargs: fake_conn)

    assert adapter.check_benchmark_tables_exist() is True

    adapter._get_expected_tables.assert_called_once_with(adapter.benchmark)
    adapter.get_tables.assert_called_once_with(fake_conn)
    fake_conn.close.assert_called_once_with()


def test_check_benchmark_tables_exist_returns_false_when_tables_missing(monkeypatch):
    adapter = _Adapter()
    adapter.benchmark = MagicMock()
    adapter.benchmark.tables = {"cpu": None, "mem": None}
    adapter.get_tables = MagicMock(return_value=["cpu"])
    fake_conn = MagicMock()
    monkeypatch.setattr("benchbox.platforms.influxdb.setup.InfluxDBConnection", lambda **kwargs: fake_conn)

    assert adapter.check_benchmark_tables_exist() is False

    fake_conn.connect.assert_called_once_with()
    adapter.get_tables.assert_called_once_with(fake_conn)
    fake_conn.close.assert_called_once_with()


def test_check_benchmark_tables_exist_returns_false_without_benchmark(monkeypatch):
    adapter = _Adapter()
    connection_factory = MagicMock()
    monkeypatch.setattr("benchbox.platforms.influxdb.setup.InfluxDBConnection", connection_factory)

    assert adapter.check_benchmark_tables_exist() is False

    connection_factory.assert_not_called()


def test_check_benchmark_tables_exist_returns_false_without_expected_tables(monkeypatch):
    adapter = _Adapter()
    adapter.benchmark = SimpleNamespace(tables={})
    connection_factory = MagicMock()
    monkeypatch.setattr("benchbox.platforms.influxdb.setup.InfluxDBConnection", connection_factory)

    assert adapter.check_benchmark_tables_exist() is False

    connection_factory.assert_not_called()


def test_check_benchmark_tables_exist_returns_false_for_force_recreate(monkeypatch):
    adapter = _Adapter()
    adapter.force_recreate = True
    adapter.benchmark = MagicMock()
    adapter.benchmark.tables = {"cpu": None}
    connection_factory = MagicMock()
    monkeypatch.setattr("benchbox.platforms.influxdb.setup.InfluxDBConnection", connection_factory)

    assert adapter.check_benchmark_tables_exist() is False

    connection_factory.assert_not_called()


def test_check_benchmark_tables_exist_returns_false_when_connection_creation_fails(monkeypatch):
    adapter = _Adapter()
    adapter.benchmark = MagicMock()
    adapter.benchmark.tables = {"cpu": None}
    connection_factory = MagicMock(side_effect=RuntimeError("cannot create client"))
    monkeypatch.setattr("benchbox.platforms.influxdb.setup.InfluxDBConnection", connection_factory)

    assert adapter.check_benchmark_tables_exist() is False

    adapter.logger.debug.assert_called_once()


def test_core_mode_creates_the_database_before_the_connection_test(monkeypatch):
    adapter = _Adapter()
    adapter.mode = "core"
    fake_conn = MagicMock()
    order = []
    fake_conn.ensure_database.side_effect = lambda: order.append("ensure")
    fake_conn.test_connection.side_effect = lambda: order.append("test") or True
    monkeypatch.setattr("benchbox.platforms.influxdb.setup.InfluxDBConnection", lambda **kwargs: fake_conn)

    adapter.create_connection(host="localhost", port=8181)

    assert order == ["ensure", "test"]


def test_cloud_mode_never_creates_the_database(monkeypatch):
    adapter = _Adapter()
    fake_conn = MagicMock()
    fake_conn.test_connection.return_value = True
    monkeypatch.setattr("benchbox.platforms.influxdb.setup.InfluxDBConnection", lambda **kwargs: fake_conn)

    adapter.create_connection()

    fake_conn.ensure_database.assert_not_called()


@pytest.fixture
def loopback_server():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}
    status = {"code": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            seen["body"] = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            seen["auth"] = self.headers.get("Authorization")
            self.send_response(status["code"])
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], seen, status
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _connection(port):
    from benchbox.platforms.influxdb.client import InfluxDBConnection

    return InfluxDBConnection(host="127.0.0.1", token=None, database="bench", port=port, ssl=False)


def test_ensure_database_posts_the_database_name(loopback_server):
    port, seen, _ = loopback_server
    _connection(port).ensure_database()

    assert seen == {
        "path": "/api/v3/configure/database",
        "body": b'{"db": "bench"}',
        "auth": None,
    }


def test_ensure_database_accepts_an_existing_database(loopback_server):
    port, _, status = loopback_server
    status["code"] = 409
    _connection(port).ensure_database()


def test_ensure_database_surfaces_other_http_errors(loopback_server):
    port, _, status = loopback_server
    status["code"] = 401
    with pytest.raises(ConnectionError, match="HTTP 401"):
        _connection(port).ensure_database()
