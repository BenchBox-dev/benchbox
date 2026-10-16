from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from benchbox.core.results.anonymization import anonymize_applied_tuning_payload
from benchbox.core.tuning.applied_ledger import PHASE_DDL, AppliedTuningLedger
from benchbox.core.tuning.interface import (
    PlatformOptimizationConfiguration,
    TableTuning,
    TuningColumn,
    UnifiedTuningConfiguration,
)
from benchbox.core.tuning.introspection import CORROBORATED, IntrospectedObject, IntrospectedState, corroborate
from benchbox.platforms.base import PlatformAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CLICKHOUSE_DDL = (
    "CREATE TABLE lineitem (l_orderkey Int32, l_linenumber Int32, l_shipdate Date) "
    "ENGINE = MergeTree() PARTITION BY (toYYYYMM(l_shipdate)) ORDER BY (l_orderkey, l_linenumber)"
)
CLICKHOUSE_FALLBACK_DDL = (
    "CREATE TABLE lineitem (l_orderkey Int32, l_linenumber Int32) ENGINE = MergeTree() ORDER BY tuple()"
)


class _Adapter(PlatformAdapter):
    def __init__(self, platform_type: str, sorted_ingestion_mode: str = "off") -> None:
        super().__init__(type=platform_type)
        self.unified_tuning_configuration = UnifiedTuningConfiguration(
            platform_optimizations=PlatformOptimizationConfiguration(sorted_ingestion_mode=sorted_ingestion_mode)
        )
        self._applied_tuning_ledger = AppliedTuningLedger()

    @property
    def platform_name(self) -> str:
        return self.canonical_platform_type

    def get_target_dialect(self) -> str:
        return "duckdb"

    @staticmethod
    def add_cli_arguments(parser) -> None:
        return None

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls(config["type"])

    def create_connection(self, **connection_config) -> Any:
        return Mock()

    def create_schema(self, benchmark, connection: Any) -> float:
        return 0.0

    def apply_platform_optimizations(self, platform_config, connection: Any) -> None:
        return None

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection: Any) -> None:
        return None

    def load_data(self, benchmark, connection: Any, data_dir: Path):
        return {}, 0.0, None

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        return None

    def execute_query(self, connection: Any, query: str, query_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"query_id": query_id, "status": "SUCCESS", "execution_time_seconds": 0.0, "rows_returned": 0}


def _config(*columns: str) -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.table_tunings["lineitem"] = TableTuning(
        table_name="lineitem",
        sorting=[TuningColumn(name=name, type="INTEGER", order=order) for order, name in enumerate(columns, start=1)],
    )
    return config


def _sort(adapter: _Adapter, *columns: str) -> bool:
    return adapter.apply_ctas_sort("lineitem", _config(*columns), Mock())


@pytest.mark.parametrize("platform_type", ["clickhouse-local", "clickhouse-server", "clickhouse-cloud", "starrocks"])
def test_sort_realized_by_executed_ddl_is_satisfied_not_dropped(platform_type):
    adapter = _Adapter(platform_type)
    ledger = adapter._applied_tuning_ledger
    ledger.record("SET max_threads = 4", PHASE_DDL)
    ledger.record(CLICKHOUSE_DDL, PHASE_DDL)

    assert _sort(adapter, "l_orderkey", "l_linenumber") is False

    assert ledger.dropped == []
    [satisfied] = ledger.satisfied
    assert satisfied.satisfied_by == 1
    assert satisfied.intent == "sorted_ingestion lineitem ORDER BY l_orderkey, l_linenumber"
    assert ledger.statements[satisfied.satisfied_by].statement == CLICKHOUSE_DDL


def test_satisfied_entry_serializes_next_to_dropped_and_stays_out_of_the_hash():
    adapter = _Adapter("clickhouse-local")
    ledger = adapter._applied_tuning_ledger
    ledger.record(CLICKHOUSE_DDL, PHASE_DDL)
    hash_before = ledger.applied_ledger_hash()

    _sort(adapter, "l_orderkey", "l_linenumber")

    payload = ledger.to_payload(status="applied_unverified")
    assert payload["dropped"] == []
    assert payload["satisfied"] == [
        {
            "intent": "sorted_ingestion lineitem ORDER BY l_orderkey, l_linenumber",
            "satisfied_by": 0,
            "reason": "sort realized by ORDER BY in CREATE TABLE",
        }
    ]
    assert ledger.applied_ledger_hash() == hash_before
    assert "satisfied" not in AppliedTuningLedger().to_payload(status="noop")


def test_satisfied_never_counts_as_corroboration():
    adapter = _Adapter("clickhouse-local")
    ledger = adapter._applied_tuning_ledger
    ledger.record(CLICKHOUSE_DDL, PHASE_DDL)
    state = IntrospectedState(
        platform="clickhouse",
        objects=[
            IntrospectedObject(kind="sort_key", table="lineitem", columns=("l_orderkey", "l_linenumber")),
            IntrospectedObject(kind="partition_key", table="lineitem", columns=("toyyyymm(l_shipdate)",)),
        ],
    )
    without = corroborate(ledger, state)
    _sort(adapter, "l_orderkey", "l_linenumber")
    with_satisfied = corroborate(ledger, state)

    assert [entry.verdict for entry in with_satisfied.entries] == [entry.verdict for entry in without.entries]
    assert with_satisfied.corroborated is without.corroborated is True
    assert {entry.verdict for entry in with_satisfied.entries} == {CORROBORATED}


def test_satisfied_without_a_corroborated_statement_does_not_upgrade():
    adapter = _Adapter("clickhouse-local")
    ledger = adapter._applied_tuning_ledger
    ledger.record(CLICKHOUSE_DDL, PHASE_DDL)
    _sort(adapter, "l_orderkey", "l_linenumber")

    receipt = corroborate(ledger, IntrospectedState(platform="clickhouse", objects=[]))

    assert receipt.corroborated is False
    assert {entry.verdict for entry in receipt.entries} == {"absent"}


def test_ddl_fallback_order_by_does_not_satisfy_a_requested_sort():
    adapter = _Adapter("clickhouse-local")
    adapter._applied_tuning_ledger.record(CLICKHOUSE_FALLBACK_DDL, PHASE_DDL)

    _sort(adapter, "l_orderkey", "l_linenumber")

    ledger = adapter._applied_tuning_ledger
    assert ledger.satisfied == []
    [dropped] = ledger.dropped
    assert "no executed CREATE TABLE" in dropped.reason


def test_ddl_order_by_for_other_columns_does_not_satisfy_a_requested_sort():
    adapter = _Adapter("clickhouse-local")
    adapter._applied_tuning_ledger.record(CLICKHOUSE_DDL, PHASE_DDL)

    _sort(adapter, "l_shipdate")

    assert adapter._applied_tuning_ledger.satisfied == []
    assert len(adapter._applied_tuning_ledger.dropped) == 1


def test_sort_realized_by_ddl_without_a_recorded_statement_is_dropped():
    adapter = _Adapter("clickhouse-local")

    _sort(adapter, "l_orderkey", "l_linenumber")

    assert adapter._applied_tuning_ledger.satisfied == []
    assert len(adapter._applied_tuning_ledger.dropped) == 1


def test_platform_with_neither_ddl_nor_ctas_still_records_a_drop():
    adapter = _Adapter("postgresql", sorted_ingestion_mode="force")

    _sort(adapter, "l_orderkey")

    ledger = adapter._applied_tuning_ledger
    assert ledger.satisfied == []
    [dropped] = ledger.dropped
    assert dropped.reason.endswith("does not support CTAS sort")


@pytest.mark.parametrize("platform_type", ["snowflake", "athena", "azure-synapse", "databricks"])
def test_mode_off_is_the_recorded_reason_not_missing_ctas_support(platform_type):
    adapter = _Adapter(platform_type, sorted_ingestion_mode="off")

    _sort(adapter, "l_orderkey")

    [dropped] = adapter._applied_tuning_ledger.dropped
    assert dropped.reason == "sorted_ingestion_mode=off"


def test_anonymized_export_redacts_satisfied_entries():
    adapter = _Adapter("clickhouse-local")
    adapter._applied_tuning_ledger.record(CLICKHOUSE_DDL, PHASE_DDL)
    _sort(adapter, "l_orderkey", "l_linenumber")
    payload = adapter._applied_tuning_ledger.to_payload(status="applied_unverified")

    redacted = anonymize_applied_tuning_payload(payload)
    assert redacted["satisfied"] == [{"redacted": True}]
    assert payload["satisfied"][0]["intent"].startswith("sorted_ingestion lineitem")
