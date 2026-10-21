# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

import benchbox.platforms.postgresql as postgresql_module
from benchbox.platforms.cedardb import CedarDBAdapter
from benchbox.platforms.postgresql import POSTGRES_DIALECT

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def cedardb_stubs(monkeypatch):

    mock_psycopg = Mock()
    mock_psycopg.__version__ = "3.1.0"

    monkeypatch.setattr(postgresql_module, "psycopg", mock_psycopg)

    with patch("benchbox.security.credentials.CredentialManager.get_platform_credentials", return_value={}):
        yield mock_psycopg


class TestCedarDBAdapter:
    def test_initialization_defaults(self, cedardb_stubs):

        adapter = CedarDBAdapter()

        assert adapter.platform_name == "CedarDB"
        assert adapter.get_target_dialect() == POSTGRES_DIALECT
        assert adapter.host == "localhost"
        assert adapter.port == 5432
        assert adapter.database == "benchbox"
        assert adapter.username == "postgres"
        assert adapter.schema == "public"

    def test_initialization_with_config(self, cedardb_stubs):

        adapter = CedarDBAdapter(
            host="cedardb.example.com",
            port=5433,
            database="analytics_db",
            username="cedar_user",
            password="secret",
            schema="analytics",
        )

        assert adapter.host == "cedardb.example.com"
        assert adapter.port == 5433
        assert adapter.database == "analytics_db"
        assert adapter.username == "cedar_user"
        assert adapter.password == "secret"
        assert adapter.schema == "analytics"

    def test_dialect_is_postgres(self, cedardb_stubs):

        adapter = CedarDBAdapter()

        assert adapter.get_target_dialect() == POSTGRES_DIALECT
        assert adapter.get_target_dialect() == "postgres"

    def test_from_config_basic(self, cedardb_stubs):

        config = {
            "host": "cedardb.local",
            "port": 5434,
            "database": "test_analytics",
        }

        adapter = CedarDBAdapter.from_config(config)

        assert adapter.host == "cedardb.local"
        assert adapter.port == 5434
        assert adapter.database == "test_analytics"

    def test_from_config_generates_database_name(self, cedardb_stubs):

        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }

        adapter = CedarDBAdapter.from_config(config)

        assert adapter.database == "benchbox_tpch_sf1"

    def test_from_config_uses_provided_database(self, cedardb_stubs):

        config = {
            "database": "explicit_db",
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }

        adapter = CedarDBAdapter.from_config(config)

        assert adapter.database == "explicit_db"

    def test_inherits_postgresql_connection_params(self, cedardb_stubs):

        adapter = CedarDBAdapter(
            host="cedardb.example.com",
            port=5434,
            database="testdb",
            username="testuser",
            password="testpass",
            sslmode="require",
            connect_timeout=15,
        )

        params = adapter._get_connection_params()

        assert params["host"] == "cedardb.example.com"
        assert params["port"] == 5434
        assert params["dbname"] == "testdb"
        assert params["user"] == "testuser"
        assert params["password"] == "testpass"
        assert params["sslmode"] == "require"
        assert params["connect_timeout"] == 15

    def test_get_platform_info_basic(self, cedardb_stubs):

        adapter = CedarDBAdapter()

        info = adapter.get_platform_info(connection=None)

        assert info["platform_type"] == "cedardb"
        assert info["platform_name"] == "CedarDB"

    def test_get_platform_info_with_connection(self, cedardb_stubs):

        adapter = CedarDBAdapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("CedarDB 1.0 (formerly Umbra)",)

        info = adapter.get_platform_info(connection=mock_conn)

        assert info["platform_type"] == "cedardb"
        assert "cedardb_version_string" in info
        assert "CedarDB" in info["cedardb_version_string"]


class TestCedarDBTuningTypes:
    def test_supports_tuning_type(self, cedardb_stubs):

        adapter = CedarDBAdapter()

        from benchbox.core.tuning.interface import TuningType

        assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True
        assert adapter.supports_tuning_type(TuningType.SORTING) is False
        assert adapter.supports_tuning_type(TuningType.DISTRIBUTION) is False
        assert adapter.supports_tuning_type(TuningType.CLUSTERING) is False
        assert adapter.supports_tuning_type(TuningType.PRIMARY_KEYS) is True
        assert adapter.supports_tuning_type(TuningType.FOREIGN_KEYS) is True


class TestCedarDBRegistration:
    def test_cedardb_in_platform_registry(self, cedardb_stubs):

        from benchbox.core.platform_registry import PlatformRegistry, auto_register_platforms

        auto_register_platforms()

        assert "cedardb" in PlatformRegistry._adapters
        assert PlatformRegistry._adapters["cedardb"] == CedarDBAdapter

    def test_cedardb_metadata(self, cedardb_stubs):

        from benchbox.core.platform_registry import PlatformRegistry

        metadata = PlatformRegistry._build_platform_metadata()

        assert "cedardb" in metadata
        assert metadata["cedardb"]["display_name"] == "CedarDB"
        assert metadata["cedardb"]["category"] == "olap"


class TestCedarDBConfigBuilder:
    def test_config_builder_basic(self, cedardb_stubs):

        from benchbox.platforms.cedardb import _build_cedardb_config

        options = {"host": "localhost", "port": 5432}
        overrides = {"scale_factor": 1.0}

        config = _build_cedardb_config("cedardb", options, overrides, None)

        assert config.host == "localhost"
        assert config.port == 5432
        assert config.type == "cedardb"

    def test_config_builder_defaults(self, cedardb_stubs):

        from benchbox.platforms.cedardb import _build_cedardb_config

        config = _build_cedardb_config("cedardb", {}, {}, None)

        assert config.host == "localhost"
        assert config.port == 5432
        assert config.username == "postgres"
        assert config.sslmode == "prefer"

    def test_config_builder_merges_overrides(self, cedardb_stubs):

        from benchbox.platforms.cedardb import _build_cedardb_config

        options = {"host": "cedardb.local"}
        overrides = {"database": "benchmark_db", "scale_factor": 10.0}

        config = _build_cedardb_config("cedardb", options, overrides, None)

        assert config.host == "cedardb.local"
        assert config.database == "benchmark_db"
        assert config.scale_factor == 10.0

    def test_config_builder_custom_port(self, cedardb_stubs):

        from benchbox.platforms.cedardb import _build_cedardb_config

        config = _build_cedardb_config("cedardb", {"port": 5434}, {}, None)

        assert config.port == 5434

    def test_config_builder_returns_database_config(self, cedardb_stubs):

        from benchbox.core.schemas import DatabaseConfig
        from benchbox.platforms.cedardb import _build_cedardb_config

        config = _build_cedardb_config("cedardb", {}, {}, None)

        assert isinstance(config, DatabaseConfig)
        assert config.type == "cedardb"
        assert config.name == "CedarDB"


class _GucRejectingCursor:
    rejected = ("random_page_cost",)

    def __init__(self, executed):
        self.executed = executed

    def execute(self, statement, *args, **kwargs):
        if any(name in statement for name in self.rejected):
            raise RuntimeError(f'cannot change configuration parameter "{self.rejected[0]}"')
        self.executed.append(statement)

    def close(self):
        return None


class _GucRejectingConnection:
    def __init__(self):
        self.executed = []

    def cursor(self):
        return _GucRejectingCursor(self.executed)

    def commit(self):
        return None

    def rollback(self):
        return None


def _session_ledger_after_configure(adapter, benchmark_type):
    from benchbox.core.tuning.applied_ledger import PHASE_SESSION, AppliedTuningLedger, recording_connection

    ledger = AppliedTuningLedger()
    connection = _GucRejectingConnection()
    adapter.configure_for_benchmark(recording_connection(connection, ledger, PHASE_SESSION), benchmark_type)
    return ledger, connection


def _validator_errors_for_ledger(tmp_path, ledger):
    from benchbox.core.results.exporter import ResultExporter
    from benchbox.validation.bundle import validate_bundles
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    result = make_benchmark_results(
        benchmark_name="TPC-H",
        platform="cedardb",
        execution_id="cedardb-baseline-exec",
        duration_seconds=3.0,
        total_queries=1,
        successful_queries=1,
        query_results=[{"query_id": "Q1", "execution_time": 1.0, "status": "SUCCESS", "rows_returned": 4}],
        validation_status="PASSED",
        validation_details={},
        applied_tuning_ledger=None if ledger.is_empty() else ledger.to_payload(status="not_applicable"),
    )
    local = ResultExporter(output_dir=tmp_path, anonymize=False).export_result(result, ["json"])["json"]
    return [error for result in validate_bundles([local]) for error in result.errors]


class TestCedarDBBaselineSessionLedger:
    @pytest.mark.parametrize("benchmark_type", ["olap", "oltp"])
    def test_baseline_records_no_session_statements(self, cedardb_stubs, benchmark_type):
        adapter = CedarDBAdapter()
        assert adapter.tuning_enabled is False

        ledger, connection = _session_ledger_after_configure(adapter, benchmark_type)

        assert ledger.statements == []
        assert connection.executed == []

    def test_baseline_result_passes_public_privacy_validation(self, cedardb_stubs, tmp_path):
        adapter = CedarDBAdapter()
        ledger, _ = _session_ledger_after_configure(adapter, "olap")

        errors = _validator_errors_for_ledger(tmp_path, ledger)

        assert not [error for error in errors if "unanonymized tuning" in error], errors

    def test_tuned_olap_session_pack_is_applied_and_recorded(self, cedardb_stubs):
        adapter = CedarDBAdapter(tuning_enabled=True)

        ledger, connection = _session_ledger_after_configure(adapter, "olap")

        assert connection.executed == [
            "SET enable_seqscan = on",
            "SET enable_hashjoin = on",
            "SET enable_mergejoin = on",
            "SET cpu_tuple_cost = 0.01",
        ]
        assert [entry.status for entry in ledger.statements] == ["executed"] * 4

    def test_tuned_session_pack_never_issues_rejected_random_page_cost(self, cedardb_stubs):
        for benchmark_type in ("olap", "oltp"):
            adapter = CedarDBAdapter(tuning_enabled=True)

            ledger, _ = _session_ledger_after_configure(adapter, benchmark_type)

            assert all("random_page_cost" not in entry.statement for entry in ledger.statements)
            assert all(entry.status == "executed" for entry in ledger.statements)
