from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import benchbox.platforms.postgresql as postgresql_module
from benchbox.core.tuning.applied_ledger import PHASE_DDL, AppliedTuningLedger, recording_connection
from benchbox.core.tuning.ddl_generator import get_ddl_generator
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.platforms.cedardb import CedarDBAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_CREATE = "CREATE TABLE lineitem (l_orderkey INTEGER NOT NULL, l_shipdate DATE NOT NULL)"
_TPCH = SimpleNamespace(_name="TPC-H")


def _config(*columns: tuple[str, str], keys: bool = False) -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    config.primary_keys.enabled = keys
    config.table_tunings["LINEITEM"] = TableTuning(
        table_name="LINEITEM",
        partitioning=[TuningColumn(name=name, type=kind, order=index) for index, (name, kind) in enumerate(columns, 1)],
    )
    return config


@pytest.fixture
def adapter(monkeypatch) -> CedarDBAdapter:
    monkeypatch.setattr(postgresql_module, "psycopg", Mock(__version__="3.1.0"))
    instance = CedarDBAdapter(tuning_enabled=True)
    instance._applied_tuning_ledger = AppliedTuningLedger()
    return instance


def test_a_date_column_is_partitioned_by_hash_without_explicit_partitions(adapter):
    adapter.unified_tuning_configuration = _config(("L_SHIPDATE", "DATE"))

    statement, children = adapter.partitioned_create_statements(_CREATE, _TPCH)

    assert statement == f"{_CREATE}\nPARTITION BY HASH (L_SHIPDATE)"
    assert children == []


def test_several_columns_form_one_hash_key(adapter):
    adapter.unified_tuning_configuration = _config(("L_ORDERKEY", "INTEGER"), ("L_SHIPDATE", "DATE"))

    statement, _children = adapter.partitioned_create_statements(_CREATE, _TPCH)

    assert statement.endswith("PARTITION BY HASH (L_ORDERKEY, L_SHIPDATE)")


def test_primary_keys_do_not_prevent_partitioning(adapter):
    adapter.unified_tuning_configuration = _config(("L_SHIPDATE", "DATE"), keys=True)

    statement, _children = adapter.partitioned_create_statements(_CREATE, _TPCH)

    assert statement.endswith("PARTITION BY HASH (L_SHIPDATE)")
    assert adapter._applied_tuning_ledger.dropped == []


def test_adapter_output_equals_generator_output(adapter):
    adapter.unified_tuning_configuration = _config(("L_SHIPDATE", "DATE"))
    tuning = TableTuning(table_name="LINEITEM", partitioning=[TuningColumn(name="L_SHIPDATE", type="DATE", order=1)])
    clauses = get_ddl_generator("cedardb").generate_tuning_clauses(tuning)

    statement, _children = adapter.partitioned_create_statements(_CREATE, _TPCH)

    assert statement.endswith(clauses.partition_by)


class _FakeCursor:
    def __init__(self, definition: str | None):
        self._definition = definition
        self._rows: list[tuple] = []

    def execute(self, statement, params=None, *args, **kwargs):
        text = str(statement)
        if "pg_get_partkeydef" in text:
            matches = "c.relkind = 'p'" in text and params == ("public", "lineitem")
            self._rows = [(self._definition,)] if matches and self._definition is not None else []

    def fetchall(self):
        return self._rows

    def close(self):
        return None


class _FakeConnection:
    def __init__(self, definition: str | None):
        self.executed: list[str] = []
        self._definition = definition

    def cursor(self):
        return _FakeCursor(self._definition)

    def execute(self, statement, *args, **kwargs):
        self.executed.append(str(statement))

    def commit(self):
        return None

    def rollback(self):
        return None


def _run_schema(
    adapter, monkeypatch, definition: str | None, *columns: tuple[str, str], keys: bool = False, unique: bool = False
) -> _FakeConnection:
    config = _config(*columns, keys=keys)
    config.unique_constraints.enabled = unique
    adapter.unified_tuning_configuration = config
    monkeypatch.setattr(adapter, "_create_schema_with_tuning", lambda benchmark, source_dialect: _CREATE + ";")
    connection = _FakeConnection(definition)
    cursor_connection = recording_connection(connection, adapter._applied_tuning_ledger, PHASE_DDL)
    adapter.create_schema(_TPCH, cursor_connection)
    adapter._reconcile_requested_tuning(adapter.unified_tuning_configuration)
    return connection


def test_a_hash_partition_key_read_back_from_the_catalog_is_satisfied(adapter, monkeypatch):
    _run_schema(adapter, monkeypatch, "HASH (l_shipdate)", ("L_SHIPDATE", "DATE"))

    assert adapter._applied_tuning_ledger.dropped == []
    assert [item.intent for item in adapter._applied_tuning_ledger.satisfied] == ["partitioning:LINEITEM (L_SHIPDATE)"]


@pytest.mark.parametrize("keys, unique", [(True, False), (False, True), (True, True)])
def test_requested_keys_still_verify_and_satisfy_the_partitioning(adapter, monkeypatch, keys, unique):
    _run_schema(adapter, monkeypatch, "HASH (l_shipdate)", ("L_SHIPDATE", "DATE"), keys=keys, unique=unique)

    assert [item for item in adapter._applied_tuning_ledger.dropped if "partitioning" in item.intent] == []
    assert [item.reason for item in adapter._applied_tuning_ledger.satisfied if "partitioning" in item.intent] == [
        "pg_get_partkeydef catalog readback: HASH (l_shipdate)"
    ]


@pytest.mark.parametrize(
    "definition, reason",
    [
        ("RANGE (l_shipdate)", "expected HASH ['l_shipdate'], observed RANGE (l_shipdate)"),
        ("HASH (l_orderkey)", "expected HASH ['l_shipdate'], observed HASH (l_orderkey)"),
        (None, "table is not a partitioned table"),
    ],
)
def test_any_other_partition_key_is_not_claimed(adapter, monkeypatch, definition, reason):
    _run_schema(adapter, monkeypatch, definition, ("L_SHIPDATE", "DATE"))

    assert adapter._applied_tuning_ledger.satisfied == []
    assert reason in adapter._applied_tuning_ledger.dropped[0].reason
