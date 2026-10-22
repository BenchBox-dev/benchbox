from __future__ import annotations

import types
from pathlib import Path

import pytest

from benchbox.core.results.schema import build_applied_ledger_payload
from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.platforms.sqlite import SQLiteAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _tuning_config() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.table_tunings["orders"] = TableTuning(
        table_name="orders",
        sorting=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)],
    )
    return config


def _adapter(tmp_path: Path, canonical: str, *, tuned: bool = True) -> SQLiteAdapter:
    adapter_type = type("_LabelledSQLiteAdapter", (SQLiteAdapter,), {"canonical_platform_type": canonical})
    database = str(tmp_path / "store.db")
    if tuned:
        adapter = adapter_type(database_path=database, tuning_enabled=True, tuning_config=_tuning_config())
    else:
        adapter = adapter_type(database_path=database)
    adapter._applied_tuning_ledger = AppliedTuningLedger()
    return adapter


def _run_fresh_setup(adapter: SQLiteAdapter, tmp_path: Path) -> list[bool]:
    connection = adapter.create_connection()
    adapter.create_schema = lambda benchmark, conn: 0.0
    adapter.load_data = lambda benchmark, conn, data_dir: ({"orders": 5}, 0.0, None)
    applied: list[bool] = []
    adapter.apply_unified_tuning = lambda config, conn: applied.append(True)
    try:
        adapter._setup_fresh_database_phases(
            types.SimpleNamespace(output_dir=str(tmp_path)), connection, adapter.get_effective_tuning_configuration()
        )
    finally:
        adapter.close_connection(connection)
    return applied


def test_a_tuned_run_without_a_store_proceeds_and_records_why_it_has_no_marker(tmp_path):
    adapter = _adapter(tmp_path, "athena")

    applied = _run_fresh_setup(adapter, tmp_path)

    assert applied == [True]
    ledger = adapter._applied_tuning_ledger
    assert ledger.metadata_store_unsupported is True
    assert not ledger.is_empty()
    assert ledger.to_payload(status="applied_unverified")["metadata_store"] == "unsupported"


def test_the_recorded_exemption_reaches_the_exported_ledger(tmp_path):
    adapter = _adapter(tmp_path, "athena")
    _run_fresh_setup(adapter, tmp_path)

    result = types.SimpleNamespace(
        applied_tuning_ledger=adapter._applied_tuning_ledger.to_payload(status="applied_unverified")
    )

    assert build_applied_ledger_payload(result)["metadata_store"] == "unsupported"


def test_a_run_with_a_store_records_no_exemption(tmp_path):
    adapter = _adapter(tmp_path, "sqlite")

    applied = _run_fresh_setup(adapter, tmp_path)

    assert applied == [True]
    assert adapter._applied_tuning_ledger.metadata_store_unsupported is False
    assert "metadata_store" not in adapter._applied_tuning_ledger.to_payload(status="applied_unverified")


@pytest.mark.parametrize("tuned", [True, False])
def test_a_database_without_a_store_is_refused_for_reuse(tmp_path, tuned):
    adapter = _adapter(tmp_path, "athena", tuned=tuned)

    result = adapter._validate_database_tunings(database_path=str(tmp_path / "store.db"))

    assert result.is_valid is False
    assert "recreated instead of reused" in result.errors[0]
    assert "Athena" in result.errors[0]


def test_the_failure_to_write_a_marker_where_a_store_exists_still_aborts_the_run(tmp_path):
    adapter = _adapter(tmp_path, "sqlite")
    adapter.ensure_tuned_run_marker = lambda connection: False

    with pytest.raises(RuntimeError, match="tuned-run marker"):
        _run_fresh_setup(adapter, tmp_path)
