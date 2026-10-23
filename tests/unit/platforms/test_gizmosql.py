from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

import benchbox.platforms.gizmosql as gizmosql_module
from benchbox.core.platform_manifest import PLATFORM_MANIFEST, PLATFORM_MANIFEST_BY_KEY
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.platforms.base import DriverIsolationCapability, PlatformAdapter
from benchbox.platforms.gizmosql import (
    GizmoSQLAdapter,
    GizmoSQLConnectionError,
    GizmoSQLLoadError,
    GizmoSQLQueryTimeout,
    GizmoSQLReadOnlyError,
    GizmoSQLSettingScopeError,
    GizmoSQLUnsupportedServerError,
    extract_explain_json,
    parse_server_version,
    parse_session_settings,
    setting_literal,
)
from tests.utilities.gizmosql_fakes import FakeDriver, FakeGizmoSQLServer

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PASSWORD = "s3cret-Passw0rd!"
TOKEN = "tok-9f8e7d6c5b4a"
SCHEMA_SQL = "CREATE TABLE alpha (a INTEGER, b VARCHAR);\nCREATE TABLE beta (c INTEGER);\n"


class StubBenchmark:
    def __init__(self, name: str = "TPC-H Benchmark", sql: str = SCHEMA_SQL, tables: dict[str, Any] | None = None):
        self._name = name
        self.sql = sql
        self.tables = tables or {}

    def get_create_tables_sql(self, dialect: str = "standard", tuning_config: Any = None) -> str:
        return self.sql

    def get_schema(self) -> dict[str, dict[str, Any]]:
        return {name: {} for name in (self.tables or ("alpha", "beta"))}

    def get_table_loading_order(self, names: list[str]) -> list[str]:
        return sorted(names)


@pytest.fixture()
def server() -> FakeGizmoSQLServer:
    return FakeGizmoSQLServer()


@pytest.fixture()
def driver(server: FakeGizmoSQLServer, monkeypatch: pytest.MonkeyPatch) -> FakeDriver:
    fake = FakeDriver(server)
    monkeypatch.setattr(gizmosql_module, "load_gizmosql_driver", lambda: fake)
    return fake


def make_adapter(**overrides: Any) -> GizmoSQLAdapter:
    config: dict[str, Any] = {"host": "gizmo.example.com", "port": 31337, "username": "bench", "password": PASSWORD}
    config.update(overrides)
    return GizmoSQLAdapter(**config)


class TestManifestAndRegistry:
    def test_manifest_entry(self):
        entry = PLATFORM_MANIFEST_BY_KEY["gizmosql"]
        metadata = entry.metadata_dict()

        assert entry.support_status == "experimental"
        assert entry.capabilities["supports_sql"] is True
        assert entry.capabilities["supports_dataframe"] is False
        assert entry.capabilities["platform_family"] == "duckdb"
        assert entry.capabilities["inherits_from"] == "duckdb"
        assert metadata["driver_package"] == "adbc-driver-gizmosql"
        assert metadata["requirements"] == ["adbc-driver-gizmosql>=2.0.13,<3"]
        assert "benchbox[gizmosql]" in metadata["installation_command"]
        assert entry.adapter is not None
        assert entry.adapter.module == "benchbox.platforms.gizmosql"
        assert entry.adapter.class_name == "GizmoSQLAdapter"
        assert entry.adapter.stream_connection_capability == "independent_connection"

    def test_registration_order_is_contiguous(self):
        orders = sorted(entry.adapter.registration_order for entry in PLATFORM_MANIFEST if entry.adapter)

        assert orders == list(range(len(orders)))

    def test_single_self_hosted_deployment(self):
        modes = PLATFORM_MANIFEST_BY_KEY["gizmosql"].capabilities["deployment_modes"]

        assert list(modes) == ["self-hosted"]
        assert modes["self-hosted"]["mode"] == "self-hosted"
        assert modes["self-hosted"]["requires_credentials"] is True
        assert modes["self-hosted"]["requires_cloud_storage"] is False
        assert PlatformRegistry.get_default_deployment("gizmosql") == "self-hosted"

    def test_registry_loads_adapter_lazily(self):
        assert PlatformRegistry.get_adapter_class("gizmosql") is GizmoSQLAdapter

    def test_package_export(self):
        from benchbox import platforms

        assert platforms.GizmoSQLAdapter is GizmoSQLAdapter

    def test_derives_from_platform_adapter_not_duckdb_adapter(self):
        from benchbox.platforms.duckdb import DuckDBAdapter

        assert issubclass(GizmoSQLAdapter, PlatformAdapter)
        assert not issubclass(GizmoSQLAdapter, DuckDBAdapter)

    def test_driver_isolation_capability(self):
        assert GizmoSQLAdapter.driver_isolation_capability is DriverIsolationCapability.FEASIBLE_CLIENT_ONLY

    def test_dialect_mapping(self):
        from benchbox.utils.dialect_utils import PLATFORM_DIALECTS

        assert make_adapter_without_driver().get_target_dialect() == "duckdb"
        assert PLATFORM_DIALECTS["gizmosql"] == "duckdb"

    def test_query_plan_parser_registered(self):
        from benchbox.core.query_plans.parsers.duckdb import DuckDBQueryPlanParser
        from benchbox.core.query_plans.parsers.registry import get_parser_for_platform

        assert get_parser_for_platform("gizmosql") is not None
        assert isinstance(make_adapter_without_driver().get_query_plan_parser(), DuckDBQueryPlanParser)

    def test_option_specs_use_no_reserved_generic_names(self):
        from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

        names = set(PlatformHookRegistry.list_option_specs("gizmosql"))

        assert {
            "host",
            "port",
            "username",
            "password",
            "auth_token",
            "catalog",
            "schema",
            "tls",
            "tls_skip_verify",
            "tls_ca_cert",
            "allow_insecure_connection",
            "autocommit",
            "query_timeout",
            "ingest_batch_bytes",
            "session_settings",
        } <= names
        assert not names & {"engine", "mode", "backend", "runtime", "executor", "warehouse"}

    def test_cli_arguments_register(self):
        import argparse

        parser = argparse.ArgumentParser()
        GizmoSQLAdapter.add_cli_arguments(parser)

        namespace = parser.parse_args(["--gizmosql-host", "h", "--gizmosql-port", "5", "--gizmosql-schema", "s"])

        assert (namespace.host, namespace.port, namespace.schema) == ("h", 5, "s")


def make_adapter_without_driver(**overrides: Any) -> GizmoSQLAdapter:
    return make_adapter(dry_run=True, **overrides)


@pytest.mark.medium
class TestLazyImports:
    def test_package_import_does_not_load_gizmosql_or_adbc(self):
        code = """
import json, sys
import benchbox, benchbox.platforms
print(json.dumps(sorted(m for m in sys.modules if m.startswith(('adbc_driver', 'gizmosql')))))
"""
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)

        assert json.loads(completed.stdout) == []

    def test_adapter_module_import_does_not_load_adbc(self):
        code = """
import json, sys
import benchbox.platforms.gizmosql
print(json.dumps(sorted(m for m in sys.modules if m.startswith(('adbc_driver', 'gizmosql_')))))
"""
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)

        assert json.loads(completed.stdout) == []


class TestOptionalImport:
    def test_missing_driver_raises_install_hint(self, monkeypatch: pytest.MonkeyPatch):
        def fail(name: str, *args: Any, **kwargs: Any) -> Any:
            raise ImportError(f"No module named {name!r}")

        monkeypatch.setattr(gizmosql_module.importlib, "import_module", fail)

        with pytest.raises(ImportError, match=r"uv add 'benchbox\[gizmosql\]'"):
            make_adapter()

    def test_dry_run_does_not_need_the_driver(self, monkeypatch: pytest.MonkeyPatch):
        def fail(name: str, *args: Any, **kwargs: Any) -> Any:
            raise ImportError(name)

        monkeypatch.setattr(gizmosql_module.importlib, "import_module", fail)

        assert make_adapter(dry_run=True).platform_name == "GizmoSQL"


class TestConfiguration:
    def test_defaults(self, driver):
        adapter = GizmoSQLAdapter()

        assert (adapter.host, adapter.port) == ("localhost", 31337)
        assert adapter.tls is True
        assert adapter.tls_skip_verify is False
        assert adapter.autocommit is True
        assert adapter.query_timeout == 0
        assert adapter.skip_database_management is True
        assert adapter.uri == "grpc+tls://localhost:31337"

    def test_plaintext_uri_when_explicitly_allowed(self, driver):
        adapter = make_adapter(tls=False, allow_insecure_connection=True, host="::1")

        assert adapter.uri == "grpc+tcp://[::1]:31337"

    @pytest.mark.parametrize("overrides", [{"tls": False}, {"tls_skip_verify": True}, {"tls": "false"}])
    def test_insecure_modes_need_the_explicit_development_option(self, driver, overrides):
        with pytest.raises(ValueError, match="allow_insecure_connection"):
            make_adapter(**overrides)

    def test_password_and_token_are_exclusive(self, driver):
        with pytest.raises(ValueError, match="either password or auth_token"):
            make_adapter(auth_token=TOKEN)

    @pytest.mark.parametrize("value", [1024, 16 * 1024 * 1024, 64 * 1024 * 1024])
    def test_ingest_batch_bytes_stays_below_the_flight_limit(self, driver, value):
        with pytest.raises(ValueError, match="ingest_batch_bytes"):
            make_adapter(ingest_batch_bytes=value)

    def test_negative_timeout_rejected(self, driver):
        with pytest.raises(ValueError, match="query_timeout"):
            make_adapter(query_timeout=-1)

    def test_insecure_connection_warns(self, driver, caplog):
        adapter = make_adapter(tls=False, allow_insecure_connection=True)
        with caplog.at_level(logging.WARNING):
            adapter.create_connection().close()

        assert any("not fully secured" in record.message for record in caplog.records)

    def test_from_config_reads_environment_credentials(self, driver, monkeypatch):
        monkeypatch.setenv("GIZMOSQL_USERNAME", "env-user")
        monkeypatch.setenv("GIZMOSQL_PASSWORD", "env-pass")

        adapter = GizmoSQLAdapter.from_config({"host": "h", "benchmark": "tpch", "scale_factor": 0.01})

        assert adapter.username == "env-user"
        assert adapter.password == "env-pass"
        assert adapter.schema == "benchbox_tpch_sf001"

    def test_from_config_prefers_explicit_values_and_force(self, driver, monkeypatch):
        monkeypatch.setenv("GIZMOSQL_PASSWORD", "env-pass")

        adapter = GizmoSQLAdapter.from_config(
            {"password": "explicit", "schema": "mine", "force": True, "benchmark": "tpch", "scale_factor": 1}
        )

        assert adapter.password == "explicit"
        assert adapter.schema == "mine"
        assert adapter.force_recreate is True

    def test_session_settings_parsing(self):
        assert parse_session_settings("enable_profiling=query_tree, search_path = a;b=1") == {
            "enable_profiling": "query_tree",
            "search_path": "a",
            "b": "1",
        }
        assert parse_session_settings({"X": 1}) == {"x": "1"}
        assert parse_session_settings(None) == {}
        with pytest.raises(ValueError):
            parse_session_settings("no-equals")
        with pytest.raises(ValueError):
            parse_session_settings("bad name=1")

    def test_setting_literals_quote_text_only(self):
        assert setting_literal("4") == "4"
        assert setting_literal("TRUE") == "TRUE"
        assert setting_literal("a'b") == "'a''b'"

    def test_version_parsing(self):
        assert parse_server_version("v1.39.0") == (1, 39, 0)
        assert parse_server_version("1.37") == (1, 37, 0)
        assert parse_server_version("garbage") is None


class TestConnectionAndRedaction:
    def test_connect_passes_secure_defaults(self, driver):
        make_adapter().create_connection().close()

        call = driver.connect_calls[0]
        assert call["uri"] == "grpc+tls://gizmo.example.com:31337"
        assert call["tls_skip_verify"] is False
        assert call["username"] == "bench"
        assert call["password"] == PASSWORD
        assert call["autocommit"] is True

    def test_token_authentication_uses_bearer_header(self, driver):
        make_adapter(password=None, auth_token=TOKEN).create_connection().close()

        call = driver.connect_calls[0]
        assert call["db_kwargs"]["adbc.flight.sql.authorization_header"] == f"Bearer {TOKEN}"
        assert call["password"] is None

    def test_ca_certificate_is_read_from_file(self, driver, tmp_path):
        pem = tmp_path / "ca.pem"
        pem.write_text("-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n")

        make_adapter(tls_ca_cert=str(pem)).create_connection().close()

        assert (
            "BEGIN CERTIFICATE" in driver.connect_calls[0]["db_kwargs"]["adbc.flight.sql.client_option.tls_root_certs"]
        )

    def test_connection_errors_redact_credentials(self, server, monkeypatch):
        error = RuntimeError(
            f"auth failed for grpc+tls://bench:{PASSWORD}@gizmo.example.com password={PASSWORD} {TOKEN}"
        )
        monkeypatch.setattr(gizmosql_module, "load_gizmosql_driver", lambda: FakeDriver(server, connect_error=error))
        adapter = make_adapter(password=None, auth_token=TOKEN)
        adapter.password = PASSWORD

        with pytest.raises(GizmoSQLConnectionError) as raised:
            adapter.create_connection()

        message = str(raised.value)
        assert PASSWORD not in message
        assert TOKEN not in message
        assert "****" in message
        assert raised.value.__cause__ is None
        assert raised.value.__suppress_context__ is True

    def test_platform_info_never_contains_secrets(self, driver):
        adapter = make_adapter(password=None, auth_token=TOKEN, catalog="main", schema="bench")
        connection = adapter.create_connection()

        info = adapter.get_platform_info(connection)
        serialized = json.dumps(info, default=str) + json.dumps(adapter._get_platform_metadata(connection), default=str)

        assert PASSWORD not in serialized
        assert TOKEN not in serialized
        assert info["configuration"]["auth_token"] == "<redacted>"
        assert info["configuration"]["catalog"] == "main"
        assert info["platform_type"] == "gizmosql"
        assert info["platform_version"] == "1.39.0"
        assert info["engine_version"] == "1.5.5"
        assert info["edition"] == "Core"
        assert info["backend"] == "duckdb"
        assert info["timing_basis"] == "client_observed_flight_sql"

    def test_normalized_result_metadata_redacts_secrets(self, driver):
        adapter = make_adapter()
        connection = adapter.create_connection()

        metadata = adapter.get_normalized_result_metadata(
            connection=connection, platform_info=adapter.get_platform_info(connection)
        )

        assert PASSWORD not in json.dumps(metadata, default=str)

    def test_query_failure_text_is_redacted(self, driver, server):
        adapter = make_adapter()
        connection = adapter.create_connection()
        server.failures["boom"] = RuntimeError(f"cannot run, password={PASSWORD} at grpc+tls://u:{PASSWORD}@h:1")

        result = adapter.execute_query(connection, "SELECT boom", "Q1")

        assert result["status"] == "FAILED"
        assert PASSWORD not in result["error"]


class TestServerValidation:
    def test_accepts_supported_server_and_records_versions(self, driver):
        adapter = make_adapter()
        adapter.create_connection().close()

        assert adapter.server_info == {
            "gizmosql_version": "1.39.0",
            "edition": "Core",
            "backend": "duckdb",
            "duckdb_version": "1.5.5",
            "read_only": False,
            "server_settings": {"access_mode": "automatic", "memory_limit": "8 GiB", "threads": "4"},
        }

    def test_rejects_sqlite_backend(self, server, driver):
        server.vendor_version = "sqlite 3.45.0"

        with pytest.raises(GizmoSQLUnsupportedServerError, match="'sqlite' is not supported"):
            make_adapter().create_connection()

    def test_rejects_unknown_backend(self, server, driver):
        server.vendor_version = ""

        with pytest.raises(GizmoSQLUnsupportedServerError, match="unknown"):
            make_adapter().create_connection()

    def test_rejects_old_server(self, server, driver):
        server.version = "v1.36.9"

        with pytest.raises(GizmoSQLUnsupportedServerError, match="1.37"):
            make_adapter().create_connection()

    def test_rejects_unknown_edition(self, server, driver):
        server.edition = "Community"

        with pytest.raises(GizmoSQLUnsupportedServerError, match="edition"):
            make_adapter().create_connection()

    def test_accepts_enterprise_with_warning(self, server, driver, caplog):
        server.edition = "Enterprise"

        with caplog.at_level(logging.WARNING):
            make_adapter().create_connection().close()

        assert any("Enterprise" in record.message for record in caplog.records)

    def test_rejects_endpoint_without_gizmosql_functions(self, server, driver):
        server.failures["gizmosql_version()"] = RuntimeError("Scalar Function does not exist")

        with pytest.raises(GizmoSQLUnsupportedServerError, match="not a supported GizmoSQL server"):
            make_adapter().create_connection()

    def test_failed_validation_closes_the_connection(self, server, driver):
        server.edition = "Community"

        with pytest.raises(GizmoSQLUnsupportedServerError):
            make_adapter().create_connection()

        assert server.connections[0].closed is True

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"access_mode": "read_only"}, True),
            ({"database_readonly": True}, True),
            ({}, False),
        ],
    )
    def test_read_only_detection(self, kwargs, expected):
        server = FakeGizmoSQLServer(**kwargs)
        driver = FakeDriver(server)
        adapter = make_adapter()
        adapter._driver = driver
        server.tables = {"alpha": 1, "beta": 1}

        adapter.benchmark = StubBenchmark()
        adapter.create_connection().close()

        assert adapter.server_read_only is expected


class TestReadOnlyServer:
    @pytest.fixture()
    def read_only(self, monkeypatch):
        server = FakeGizmoSQLServer(access_mode="read_only", tables={"alpha": 5, "beta": 6})
        monkeypatch.setattr(gizmosql_module, "load_gizmosql_driver", lambda: FakeDriver(server))
        return server

    def test_query_only_runs_reuse_preloaded_data(self, read_only):
        adapter = make_adapter()
        adapter.benchmark = StubBenchmark()
        connection = adapter.create_connection()

        assert adapter.database_was_reused is True
        assert adapter.execute_query(connection, "SELECT 1", "Q1")["status"] == "SUCCESS"

    def test_missing_tables_fail_with_actionable_error(self, read_only):
        read_only.tables = {"alpha": 5}
        adapter = make_adapter()
        adapter.benchmark = StubBenchmark()

        with pytest.raises(GizmoSQLReadOnlyError, match="does not hold populated tables"):
            adapter.create_connection()

    def test_schema_creation_fails_before_any_mutation(self, read_only):
        adapter = make_adapter()
        connection = adapter.create_connection()
        before = len(read_only.statements)

        with pytest.raises(GizmoSQLReadOnlyError, match="read-only"):
            adapter.create_schema(StubBenchmark(), connection)

        assert len(read_only.statements) == before

    def test_load_fails_before_any_mutation(self, read_only, tmp_path):
        adapter = make_adapter()
        connection = adapter.create_connection()
        before = len(read_only.statements)

        with pytest.raises(GizmoSQLReadOnlyError, match="load benchmark data"):
            adapter.load_data(StubBenchmark(tables={"alpha": [tmp_path / "a.tbl"]}), connection, tmp_path)

        assert len(read_only.statements) == before
        assert read_only.ingested == {}

    def test_statistics_fail_before_any_mutation(self, read_only):
        adapter = make_adapter()
        connection = adapter.create_connection()
        before = len(read_only.statements)

        with pytest.raises(GizmoSQLReadOnlyError, match="statistics"):
            adapter.gather_statistics(connection, ["alpha"])

        assert len(read_only.statements) == before

    @pytest.mark.parametrize(
        "statement",
        [
            "INSERT INTO alpha VALUES (1, 'x')",
            "  -- note\nDELETE FROM alpha",
            "/* c */ UPDATE alpha SET a = 1",
            "CREATE TABLE z (a INT)",
            "drop table alpha",
            "TRUNCATE alpha",
            "ANALYZE alpha",
            "MERGE INTO alpha USING beta ON true WHEN MATCHED THEN DELETE",
        ],
    )
    def test_maintenance_statements_are_blocked_client_side(self, read_only, statement):
        adapter = make_adapter()
        connection = adapter.create_connection()
        before = len(read_only.statements)

        with pytest.raises(GizmoSQLReadOnlyError):
            connection.execute(statement)

        assert len(read_only.statements) == before

    def test_reads_and_session_statements_are_allowed(self, read_only):
        adapter = make_adapter()
        connection = adapter.create_connection()

        connection.execute("SELECT 1")
        connection.execute("SET enable_profiling = 'query_tree'")

    def test_force_recreate_is_rejected(self, read_only):
        with pytest.raises(GizmoSQLReadOnlyError, match="force_recreate"):
            make_adapter(force_recreate=True).create_connection()

    def test_missing_schema_on_read_only_server_names_the_fix(self, read_only):
        with pytest.raises(GizmoSQLReadOnlyError, match="set the schema option|Set the schema option"):
            make_adapter(schema="absent").create_connection()
        assert not any(sql.startswith("CREATE SCHEMA") for sql in read_only.sql_log())


class TestNamespace:
    def test_creates_schema_and_uses_it_on_writable_server(self, server, driver):
        adapter = make_adapter(schema="bench", catalog="warehouse")
        adapter.create_connection().close()

        log = server.sql_log()
        assert 'CREATE SCHEMA IF NOT EXISTS "warehouse"."bench"' in log
        assert 'USE "warehouse"."bench"' in log

    def test_existing_schema_is_not_recreated(self, server, driver):
        server.schemas.add("bench")
        make_adapter(schema="bench").create_connection().close()

        assert not any(sql.startswith("CREATE SCHEMA") for sql in server.sql_log())
        assert 'USE "bench"' in server.sql_log()

    def test_catalog_only_uses_catalog(self, server, driver):
        make_adapter(catalog="warehouse").create_connection().close()

        assert 'USE "warehouse"' in server.sql_log()

    def test_identifiers_are_quoted(self, server, driver):
        make_adapter(schema='we"ird').create_connection().close()

        assert 'USE "we""ird"' in server.sql_log()


class TestSessionSettings:
    def test_global_settings_are_refused_before_any_set(self, server, driver):
        adapter = make_adapter(session_settings="threads=1")

        with pytest.raises(GizmoSQLSettingScopeError, match="every session"):
            adapter.create_connection()

        assert not any(sql.upper().startswith("SET ") for sql in server.sql_log())

    def test_unknown_settings_are_refused(self, server, driver):
        with pytest.raises(GizmoSQLSettingScopeError, match="not a DuckDB setting"):
            make_adapter(session_settings="nonsense=1").create_connection()

    def test_session_scoped_settings_are_replayed_and_recorded(self, server, driver):
        adapter = make_adapter(session_settings="enable_profiling=query_tree")
        main = adapter.create_connection()
        stream = adapter.new_stream_connection(main, benchmark_type="olap")

        for connection in server.connections:
            assert "SET enable_profiling = 'query_tree'" in server.sql_log(connection.connection_id)
        assert adapter.effective_session_settings == {"enable_profiling": "query_tree"}
        assert adapter._get_platform_metadata(main)["session_settings_effective"] == {"enable_profiling": "query_tree"}
        assert adapter._get_platform_metadata(main)["session_settings_requested"] == {"enable_profiling": "query_tree"}
        stream.close()
        main.close()

    def test_no_settings_means_no_set_statements_on_any_session(self, server, driver):
        adapter = make_adapter()
        main = adapter.create_connection()
        stream = adapter.new_stream_connection(main, benchmark_type="olap")

        assert not any(sql.upper().startswith("SET ") for sql in server.sql_log())
        stream.close()
        main.close()

    def test_configure_for_benchmark_changes_nothing_server_wide(self, server, driver):
        adapter = make_adapter()
        connection = adapter.create_connection()
        before = len(server.statements)

        adapter.configure_for_benchmark(connection, "transaction_primitives")

        assert len(server.statements) == before


class TestSchemaCreation:
    def test_creates_missing_tables_without_replacing_existing_ones(self, server, driver):
        adapter = make_adapter()
        connection = adapter.create_connection()

        adapter.create_schema(StubBenchmark(), connection)

        creates = [sql for sql in server.sql_log() if "create table" in sql.lower()]
        assert len(creates) == 2
        assert all("if not exists" in sql.lower() for sql in creates)
        assert not any(sql.lower().startswith("drop") for sql in server.sql_log())

    def test_force_recreate_drops_only_benchmark_tables(self, server, driver):
        adapter = make_adapter(force_recreate=True)
        connection = adapter.create_connection()

        adapter.create_schema(StubBenchmark(), connection)

        drops = [sql for sql in server.sql_log() if sql.lower().startswith("drop")]
        assert drops == ['DROP TABLE IF EXISTS "alpha"', 'DROP TABLE IF EXISTS "beta"']

    def test_tpcds_foreign_keys_are_stripped(self, server, driver):
        sql = "CREATE TABLE alpha (a INTEGER, b INTEGER, FOREIGN KEY (b) REFERENCES beta (c));"
        adapter = make_adapter()
        connection = adapter.create_connection()

        adapter.create_schema(StubBenchmark(name="TPC-DS Benchmark", sql=sql), connection)

        assert not any("foreign key" in statement.lower() for statement in server.sql_log())

    def test_failure_names_the_table_and_redacts_secrets(self, server, driver):
        server.failures['create table if not exists "alpha"'] = RuntimeError(f"nope password={PASSWORD}")
        adapter = make_adapter()
        connection = adapter.create_connection()

        with pytest.raises(gizmosql_module.GizmoSQLError) as raised:
            adapter.create_schema(StubBenchmark(), connection)

        assert "alpha" in str(raised.value)
        assert PASSWORD not in str(raised.value)

    def test_manual_commit_sessions_commit_the_ddl(self, server, driver):
        adapter = make_adapter(autocommit=False)
        connection = adapter.create_connection()

        adapter.create_schema(StubBenchmark(), connection)

        assert server.connections[0].commits >= 1

    def test_dry_run_captures_statements_without_a_server(self, server):
        adapter = make_adapter(dry_run=True)
        adapter.enable_dry_run()
        connection = adapter.create_connection()

        adapter.create_schema(StubBenchmark(), connection)
        result = adapter.execute_query(connection, "SELECT 1", "Q1")

        assert server.connections == []
        assert result["status"] == "DRY_RUN"
        assert [entry["operation_type"] for entry in adapter.captured_sql] == ["create_table", "create_table", "query"]


class TestDatabaseReuse:
    def make(self, server, driver, **overrides):
        adapter = make_adapter(**overrides)
        adapter.benchmark = StubBenchmark()
        adapter.create_connection().close()
        return adapter

    def test_populated_tables_are_reused(self, server, driver):
        server.tables = {"alpha": 3, "beta": 4}

        assert self.make(server, driver).database_was_reused is True

    def test_empty_table_means_partial_load(self, server, driver):
        server.tables = {"alpha": 3, "beta": 0}

        assert self.make(server, driver).database_was_reused is False

    def test_missing_table_means_fresh(self, server, driver):
        server.tables = {"alpha": 3}

        assert self.make(server, driver).database_was_reused is False

    def test_force_recreate_never_reuses(self, server, driver):
        server.tables = {"alpha": 3, "beta": 4}

        assert self.make(server, driver, force_recreate=True).database_was_reused is False

    def test_decision_is_made_once_per_run(self, server, driver):
        server.tables = {"alpha": 3, "beta": 4}
        adapter = self.make(server, driver)
        server.tables = {}

        adapter.create_connection().close()

        assert adapter.database_was_reused is True


class TestQueryExecution:
    def test_success_returns_rows_and_counts(self, server, driver):
        adapter = make_adapter()
        connection = adapter.create_connection()

        result = adapter.execute_query(connection, "SELECT 1", "Q1")

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 1
        assert result["execution_time_seconds"] >= 0

    def test_failure_is_reported_not_raised(self, server, driver):
        adapter = make_adapter()
        connection = adapter.create_connection()
        server.failures["bad"] = RuntimeError("syntax error")

        result = adapter.execute_query(connection, "SELECT bad", "Q1")

        assert result["status"] == "FAILED"
        assert "syntax error" in result["error"]

    def test_query_timeout_cancels_and_reports_failure(self, server, driver):
        adapter = make_adapter(query_timeout=1)
        connection = adapter.create_connection()
        connection.query_timeout = 0.05
        server.delay_until_cancel = "slow_query"

        result = adapter.execute_query(connection, "SELECT slow_query", "Q9")

        assert result["status"] == "FAILED"
        assert "query_timeout" in result["error"]
        assert result["error_type"] == GizmoSQLQueryTimeout.__name__
        assert any(cursor.cancelled for cursor in server.connections[0].cursors)

    def test_timeout_does_not_leak_into_the_next_query(self, server, driver):
        adapter = make_adapter(query_timeout=1)
        connection = adapter.create_connection()
        connection.query_timeout = 0.05
        server.delay_until_cancel = "slow_query"
        adapter.execute_query(connection, "SELECT slow_query", "Q9")

        result = adapter.execute_query(connection, "SELECT 1", "Q1")

        assert result["status"] == "SUCCESS"

    def test_queries_without_a_timeout_arm_no_timer(self, server, driver):
        adapter = make_adapter()
        connection = adapter.create_connection()

        cursor = connection.cursor()
        cursor.execute("SELECT 1")

        assert cursor._timer is None
        cursor.close()

    def test_close_releases_every_open_cursor_first(self, server, driver):
        adapter = make_adapter()
        connection = adapter.create_connection()
        connection.execute("SELECT 1")
        held = connection.cursor()

        connection.close()

        assert server.connections[0].closed is True
        assert held._raw.closed is True

    def test_connection_execute_returns_a_fetchable_result(self, server, driver):
        connection = make_adapter().create_connection()

        assert connection.execute("SELECT 1").fetchall() == [(1,)]


class TestTransactions:
    def test_autocommit_default_makes_commit_and_rollback_noops(self, server, driver):
        connection = make_adapter().create_connection()

        connection.commit()
        connection.rollback()

        assert (server.connections[0].commits, server.connections[0].rollbacks) == (0, 0)
        assert server.connections[0].kwargs["autocommit"] is True

    def test_manual_commit_uses_adbc_transactions(self, server, driver):
        connection = make_adapter(autocommit=False).create_connection()

        connection.commit()
        connection.rollback()

        assert (server.connections[0].commits, server.connections[0].rollbacks) == (1, 1)
        assert server.connections[0].kwargs["autocommit"] is False

    def test_autocommit_accepts_string_values(self, server, driver):
        assert make_adapter(autocommit="false").autocommit is False

    def test_failed_query_rolls_back_only_manual_sessions(self, server, driver):
        adapter = make_adapter(autocommit=False)
        connection = adapter.create_connection()
        server.failures["bad"] = RuntimeError("x")

        adapter.execute_query(connection, "SELECT bad", "Q1")

        assert server.connections[0].rollbacks == 1


class TestStreams:
    def test_each_stream_is_an_independent_session(self, server, driver):
        adapter = make_adapter()
        main = adapter.create_connection()

        first = adapter.new_stream_connection(main, benchmark_type="olap")
        second = adapter.new_stream_connection(main, benchmark_type="olap")

        raws = {id(main.raw), id(first.raw), id(second.raw)}
        assert len(raws) == 3
        assert len(driver.connect_calls) == 3
        assert first is not second

    def test_closing_a_stream_leaves_the_others_open(self, server, driver):
        adapter = make_adapter()
        main = adapter.create_connection()
        first = adapter.new_stream_connection(main)
        second = adapter.new_stream_connection(main)

        first.execute("SELECT 1")
        first.close()

        assert first.raw.closed is True
        assert second.raw.closed is False
        assert main.raw.closed is False
        assert second.execute("SELECT 1").fetchall()

    def test_streams_keep_namespace_and_read_only_state(self, server, driver):
        server.schemas.add("bench")
        adapter = make_adapter(schema="bench")
        main = adapter.create_connection()

        stream = adapter.new_stream_connection(main)

        assert 'USE "bench"' in server.sql_log(stream.raw.connection_id)
        assert stream.read_only is False

    def test_stream_failure_closes_the_new_session(self, server, driver):
        server.schemas.add("bench")
        adapter = make_adapter(schema="bench")
        main = adapter.create_connection()
        server.failures['use "bench"'] = RuntimeError("catalog gone")

        with pytest.raises(RuntimeError, match="catalog gone"):
            adapter.new_stream_connection(main)

        assert server.connections[-1].closed is True
        assert main.raw.closed is False

    def test_streams_inherit_manual_commit_mode(self, server, driver):
        adapter = make_adapter(autocommit=False)
        main = adapter.create_connection()

        stream = adapter.new_stream_connection(main)

        assert stream.raw.kwargs["autocommit"] is False

    def test_dry_run_streams_need_no_server(self, server):
        adapter = make_adapter(dry_run=True)
        adapter.enable_dry_run()

        stream = adapter.new_stream_connection(adapter.create_connection())

        assert server.connections == []
        stream.close()


class TestPlanCapture:
    def test_extracts_json_from_the_second_column(self):
        rows = [("physical_plan", '{"name": "SCAN"}')]

        assert extract_explain_json(rows) == '{"name": "SCAN"}'

    def test_multi_row_responses_use_the_first_json_value(self):
        rows = [("logical", "not json"), ("physical_plan", "[1]"), ("other", "[2]")]

        assert extract_explain_json(rows) == "[1]"

    @pytest.mark.parametrize("rows", [[], None, [("only-one-column",)], [("p", "{broken")], [(None, None)]])
    def test_empty_or_malformed_responses_return_none(self, rows):
        assert extract_explain_json(rows) is None

    def test_structured_values_are_serialised(self):
        assert json.loads(extract_explain_json([("p", {"a": 1})])) == {"a": 1}

    def test_default_plan_capture_does_not_execute(self, server, driver):
        adapter = make_adapter()
        connection = adapter.create_connection()

        plan = adapter.get_query_plan(connection, "SELECT 1")

        assert json.loads(plan)["name"] == "PROJECTION"
        assert "EXPLAIN (FORMAT JSON) SELECT 1" in server.sql_log()
        assert not any("analyze" in sql.lower() for sql in server.sql_log())

    def test_analyze_is_opt_in_and_skips_dml(self, server, driver):
        adapter = make_adapter(analyze_plans=True)
        connection = adapter.create_connection()

        adapter.get_query_plan(connection, "SELECT 1")
        adapter.get_query_plan(connection, "INSERT INTO alpha VALUES (1, 'x')")

        log = server.sql_log()
        assert "EXPLAIN (ANALYZE, FORMAT JSON) SELECT 1" in log
        assert "EXPLAIN (FORMAT JSON) INSERT INTO alpha VALUES (1, 'x')" in log

    def test_explain_failure_returns_none(self, server, driver):
        adapter = make_adapter()
        connection = adapter.create_connection()
        server.failures["explain"] = RuntimeError("no plan")

        assert adapter.get_query_plan(connection, "SELECT 1") is None

    def test_plan_capture_attaches_parsed_plan(self, server, driver):
        adapter = make_adapter(capture_plans=True)
        connection = adapter.create_connection()

        result = adapter.execute_query(connection, "SELECT 1", "Q1")

        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is not None


class TestLoading:
    def write_tbl(self, directory: Path, name: str, rows: int, start: int = 0) -> Path:
        path = directory / f"{name}.tbl"
        path.write_text("".join(f"{start + i}|text-{start + i}|\n" for i in range(rows)))
        return path

    def adapter_with_tables(self, server, driver, **overrides):
        adapter = make_adapter(**overrides)
        adapter.create_connection().close()
        return adapter

    def test_loads_files_through_bounded_arrow_ingest(self, server, driver, tmp_path):
        adapter = make_adapter()
        connection = adapter.create_connection()
        benchmark = StubBenchmark(tables={"alpha": [self.write_tbl(tmp_path, "alpha", 5)]})

        stats, seconds, timings = adapter.load_data(benchmark, connection, tmp_path)

        assert stats == {"alpha": 5}
        assert seconds >= 0
        assert "alpha" in timings
        batch = server.ingested["alpha"][0]
        assert batch.schema.names == ["a", "b"]
        assert batch.column(0).to_pylist() == [0, 1, 2, 3, 4]
        assert server.sql_log().count("INGEST append alpha") == 1

    def test_load_uses_a_separate_manual_commit_session_and_commits_per_table(self, server, driver, tmp_path):
        adapter = make_adapter()
        connection = adapter.create_connection()
        benchmark = StubBenchmark(
            tables={"alpha": [self.write_tbl(tmp_path, "alpha", 2)], "beta": [self.write_tbl(tmp_path, "beta", 3)]}
        )

        adapter.load_data(benchmark, connection, tmp_path)

        loader = server.connections[1]
        assert loader.kwargs["autocommit"] is False
        assert loader.commits == 2
        assert loader.closed is True
        assert connection.raw.closed is False

    def test_sharded_tables_are_loaded_in_one_ingest(self, server, driver, tmp_path):
        adapter = make_adapter()
        connection = adapter.create_connection()
        shards = [self.write_tbl(tmp_path, "alpha_1", 2), self.write_tbl(tmp_path, "alpha_2", 3, start=10)]
        benchmark = StubBenchmark(tables={"alpha": shards})

        stats, _, _ = adapter.load_data(benchmark, connection, tmp_path)

        assert stats == {"alpha": 5}
        assert server.sql_log().count("INGEST append alpha") == 1

    def test_rerun_does_not_duplicate_rows(self, server, driver, tmp_path):
        adapter = make_adapter()
        connection = adapter.create_connection()
        benchmark = StubBenchmark(tables={"alpha": [self.write_tbl(tmp_path, "alpha", 4)]})

        adapter.load_data(benchmark, connection, tmp_path)
        stats, _, _ = adapter.load_data(benchmark, connection, tmp_path)

        assert stats == {"alpha": 4}
        assert server.sql_log().count("INGEST append alpha") == 1
        assert not any(sql.upper().startswith(("DELETE", "TRUNCATE", "DROP")) for sql in server.sql_log())

    def test_populated_tables_are_kept_and_empty_ones_are_loaded(self, server, driver, tmp_path):
        server.tables = {"alpha": 7}
        adapter = make_adapter()
        connection = adapter.create_connection()
        benchmark = StubBenchmark(
            tables={"alpha": [self.write_tbl(tmp_path, "alpha", 4)], "beta": [self.write_tbl(tmp_path, "beta", 3)]}
        )

        stats, _, _ = adapter.load_data(benchmark, connection, tmp_path)

        assert stats == {"alpha": 7, "beta": 3}
        assert "alpha" not in server.ingested

    def test_failure_rolls_back_names_the_table_and_redacts(self, server, driver, tmp_path):
        server.ingest_error = RuntimeError(f"stream broke password={PASSWORD}")
        adapter = make_adapter()
        connection = adapter.create_connection()
        benchmark = StubBenchmark(tables={"alpha": [self.write_tbl(tmp_path, "alpha", 2)]})

        with pytest.raises(GizmoSQLLoadError) as raised:
            adapter.load_data(benchmark, connection, tmp_path)

        assert "alpha" in str(raised.value)
        assert "previous contents were kept" in str(raised.value)
        assert PASSWORD not in str(raised.value)
        loader = server.connections[1]
        assert loader.rollbacks == 1
        assert loader.commits == 0
        assert loader.closed is True

    def test_ingest_count_mismatch_is_an_error(self, server, driver, tmp_path):
        server.ingest_reports = 99
        adapter = make_adapter()
        connection = adapter.create_connection()
        benchmark = StubBenchmark(tables={"alpha": [self.write_tbl(tmp_path, "alpha", 2)]})

        with pytest.raises(GizmoSQLLoadError, match="ingested 99 rows"):
            adapter.load_data(benchmark, connection, tmp_path)

    def test_malformed_data_fails_loudly_instead_of_loading_zero_rows(self, server, driver, tmp_path):
        bad = tmp_path / "alpha.tbl"
        bad.write_text("not-an-int|x|\n")
        adapter = make_adapter()
        connection = adapter.create_connection()

        with pytest.raises(GizmoSQLLoadError, match="alpha"):
            adapter.load_data(StubBenchmark(tables={"alpha": [bad]}), connection, tmp_path)

    def test_missing_data_files_are_skipped_with_zero_rows(self, server, driver, tmp_path):
        adapter = make_adapter()
        connection = adapter.create_connection()

        stats, _, _ = adapter.load_data(StubBenchmark(tables={"alpha": [tmp_path / "gone.tbl"]}), connection, tmp_path)

        assert stats == {"alpha": 0}
        assert server.ingested == {}

    def test_dry_run_load_captures_without_ingesting(self, server, tmp_path):
        adapter = make_adapter(dry_run=True)
        adapter.enable_dry_run()
        connection = adapter.create_connection()
        benchmark = StubBenchmark(tables={"alpha": [self.write_tbl(tmp_path, "alpha", 2)]})

        stats, _, _ = adapter.load_data(benchmark, connection, tmp_path)

        assert stats == {"alpha": 1000}
        assert server.connections == []

    def test_statistics_analyze_each_table_on_writable_servers(self, server, driver):
        adapter = make_adapter()
        connection = adapter.create_connection()

        assert adapter.gather_statistics(connection, ["alpha", "beta"]) == ("explicit", 2)
        assert 'ANALYZE "alpha"' in server.sql_log()
        assert 'ANALYZE "beta"' in server.sql_log()


class TestHealthAndCapabilities:
    def test_connection_health(self, server, driver):
        adapter = make_adapter()
        connection = adapter.create_connection()

        result = adapter.validate_connection_health(connection)

        assert result.is_valid
        assert result.details["basic_query_test"] == "passed"

    def test_capability_validation_warns_on_relaxed_tls(self, server, driver):
        adapter = make_adapter(tls=False, allow_insecure_connection=True)

        result = adapter.validate_platform_capabilities("olap")

        assert result.is_valid
        assert any("TLS" in warning for warning in result.warnings)

    def test_execution_engine_resolves_to_platform_default(self, driver):
        receipt = make_adapter().resolve_execution_engine("default")

        assert receipt.requested == "default"
