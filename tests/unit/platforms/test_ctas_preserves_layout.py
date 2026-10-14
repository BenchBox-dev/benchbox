from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.platforms.athena import AthenaAdapter
from benchbox.platforms.azure_synapse import AzureSynapseAdapter
from benchbox.platforms.redshift import RedshiftAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]

_DISABLED_CTAS = [
    (AthenaAdapter, "own source table"),
    (AzureSynapseAdapter, "RENAME OBJECT"),
    (RedshiftAdapter, "SORTKEY"),
]


def _sorted_config(mode: str = "force", method: str = "ctas") -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    config.platform_optimizations.sorted_ingestion_mode = mode
    config.platform_optimizations.sorted_ingestion_method = method
    config.table_tunings["lineitem"] = TableTuning(
        table_name="lineitem",
        sorting=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
    )
    return config


def _adapter(adapter_cls: type, config: UnifiedTuningConfiguration):
    adapter = adapter_cls.__new__(adapter_cls)
    adapter.logger = logging.getLogger("test-ctas-preserves-layout")
    adapter.dry_run_mode = False
    adapter.capture_sql = Mock()
    adapter.log_verbose = Mock()
    adapter.schema = "dbo"
    adapter.tuning_enabled = True
    adapter.unified_tuning_configuration = config
    adapter.get_effective_tuning_configuration = lambda: config
    adapter._applied_tuning_ledger = AppliedTuningLedger()
    return adapter


@pytest.mark.parametrize("adapter_cls,reason_fragment", _DISABLED_CTAS)
def test_disabled_ctas_runs_no_sql_and_records_an_explicit_drop(adapter_cls, reason_fragment):
    config = _sorted_config()
    adapter = _adapter(adapter_cls, config)
    connection = Mock()

    assert adapter.apply_ctas_sort("lineitem", config, connection) is False

    connection.execute.assert_not_called()
    connection.cursor.assert_not_called()
    assert getattr(adapter, "_sorted_ingestion_applied_tables", []) == []
    [dropped] = adapter._applied_tuning_ledger.dropped
    assert reason_fragment in dropped.reason
    assert adapter._applied_tuning_ledger.statements == []


@pytest.mark.parametrize("adapter_cls,_reason", _DISABLED_CTAS)
def test_disabled_ctas_reports_no_applied_tables(adapter_cls, _reason):
    config = _sorted_config()
    adapter = _adapter(adapter_cls, config)
    adapter.apply_ctas_sort("lineitem", config, Mock())

    metadata = adapter.get_sorted_ingestion_metadata()

    assert metadata["applied_tables"] == []
    assert metadata["applied_table_count"] == 0


def test_redshift_vacuum_sort_stays_in_place():
    config = _sorted_config(method="vacuum_sort")
    adapter = _adapter(RedshiftAdapter, config)

    sql = adapter._build_ctas_sort_sql("lineitem", [TuningColumn(name="l_shipdate", type="DATE", order=1)])

    assert sql == "VACUUM SORT ONLY dbo.lineitem"
    assert adapter._ctas_sort_unsupported_reason("vacuum_sort") is None


@pytest.mark.parametrize("platform", ["snowflake", "databricks"])
def test_replace_style_ctas_builders_stay_unreachable(platform):
    errors, _warnings = _sorted_config().validate_for_platform_detailed(platform)

    assert any("sorting" in error for error in errors), (
        f"the validator now accepts sorting on {platform}; its CREATE OR REPLACE ... ORDER BY CTAS "
        "builder would discard clustering, properties and constraints, so make it preserve them first"
    )
