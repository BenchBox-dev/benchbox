from unittest.mock import Mock

import pytest

import benchbox.platforms.cedardb as cedardb_module
import benchbox.platforms.pg_duckdb as pg_duckdb_module
import benchbox.platforms.postgresql as postgresql_module
import benchbox.platforms.timescaledb as timescaledb_module
from benchbox.core.tuning.applied_ledger import PHASE_SESSION, AppliedTuningLedger, recording_connection
from benchbox.platforms.cedardb import CedarDBAdapter
from benchbox.platforms.citus import CitusAdapter
from benchbox.platforms.paradedb import ParadeDBAdapter
from benchbox.platforms.pg_duckdb import PgDuckDBAdapter
from benchbox.platforms.pg_mooncake import PgMooncakeAdapter
from benchbox.platforms.postgresql import PostgreSQLAdapter
from benchbox.platforms.timescaledb import TimescaleDBAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PG_FAMILY = [
    PostgreSQLAdapter,
    TimescaleDBAdapter,
    PgDuckDBAdapter,
    CedarDBAdapter,
    PgMooncakeAdapter,
    CitusAdapter,
    ParadeDBAdapter,
]
BENCHMARK_TYPES = ["olap", "oltp", "timeseries"]


@pytest.fixture()
def psycopg_stubs(monkeypatch):
    mock_psycopg = Mock()
    mock_psycopg.__version__ = "3.1.0"
    for module in (postgresql_module, pg_duckdb_module, timescaledb_module, cedardb_module):
        monkeypatch.setattr(module, "psycopg", mock_psycopg, raising=False)
    return mock_psycopg


class _Cursor:
    def __init__(self, executed):
        self.executed = executed

    def execute(self, statement, *args, **kwargs):
        self.executed.append(statement)

    def close(self):
        return None


class _Connection:
    def __init__(self):
        self.executed = []

    def cursor(self):
        return _Cursor(self.executed)

    def commit(self):
        return None

    def rollback(self):
        return None


def _configure(adapter, benchmark_type):
    ledger = AppliedTuningLedger()
    connection = _Connection()
    adapter.configure_for_benchmark(recording_connection(connection, ledger, PHASE_SESSION), benchmark_type)
    return ledger, connection


@pytest.mark.parametrize("adapter_cls", PG_FAMILY, ids=lambda cls: cls.__name__)
@pytest.mark.parametrize("benchmark_type", BENCHMARK_TYPES)
def test_baseline_records_no_session_statements(psycopg_stubs, adapter_cls, benchmark_type):
    adapter = adapter_cls()
    assert adapter.tuning_enabled is False

    ledger, connection = _configure(adapter, benchmark_type)

    assert ledger.statements == []
    assert connection.executed == []


@pytest.mark.parametrize(
    "adapter_cls", [cls for cls in PG_FAMILY if cls is not CedarDBAdapter], ids=lambda c: c.__name__
)
def test_tuned_olap_session_pack_is_applied_and_recorded(psycopg_stubs, adapter_cls):
    adapter = adapter_cls(tuning_enabled=True)

    ledger, connection = _configure(adapter, "olap")

    assert "SET enable_hashjoin = on" in connection.executed
    assert "SET random_page_cost = 1.1" in connection.executed
    assert [entry.statement for entry in ledger.statements] == connection.executed
    assert all(entry.status == "executed" for entry in ledger.statements)


def test_tuned_timescaledb_enables_chunk_skipping(psycopg_stubs):
    ledger, connection = _configure(TimescaleDBAdapter(tuning_enabled=True), "timeseries")

    assert connection.executed == ["SET timescaledb.enable_chunk_skipping = on"]
    assert [entry.statement for entry in ledger.statements] == connection.executed


def test_tuned_pg_duckdb_olap_applies_force_execution(psycopg_stubs):
    ledger, connection = _configure(PgDuckDBAdapter(tuning_enabled=True), "olap")

    assert connection.executed[-1] == "SET duckdb.force_execution = true"
    assert [entry.statement for entry in ledger.statements] == connection.executed


def test_baseline_stream_session_replays_no_planner_settings(psycopg_stubs):
    stream_cursor = Mock()
    stream_connection = Mock()
    stream_connection.cursor.return_value = stream_cursor
    psycopg_stubs.connect.return_value = stream_connection

    PostgreSQLAdapter().new_stream_connection(Mock(), benchmark_type="olap")

    executed = " ".join(str(call) for call in stream_cursor.execute.call_args_list)
    assert "random_page_cost" not in executed
    assert "enable_hashjoin" not in executed
    assert "work_mem" in executed
