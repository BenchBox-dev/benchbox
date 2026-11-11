from __future__ import annotations

import pytest

from benchbox.cli.commands.run import (
    _build_data_organization_from_tuning,
    _resolve_data_organization_payload,
)
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_build_data_organization_from_tuning_with_sort_partition_cluster():
    cfg = UnifiedTuningConfiguration()
    cfg.platform_optimizations.sorted_ingestion_method = "hilbert"
    cfg.table_tunings["lineitem"] = TableTuning(
        table_name="lineitem",
        sorting=[
            TuningColumn(name="l_shipdate", type="DATE", order=2, sort_order="DESC"),
            TuningColumn(name="l_orderkey", type="BIGINT", order=1, sort_order="ASC"),
        ],
        partitioning=[
            TuningColumn(name="l_returnflag", type="VARCHAR", order=1),
        ],
        clustering=[
            TuningColumn(name="l_partkey", type="BIGINT", order=1),
            TuningColumn(name="l_suppkey", type="BIGINT", order=2),
        ],
    )

    payload = _build_data_organization_from_tuning(cfg)

    assert payload is not None
    assert payload["table_configs"]["lineitem"] == [
        {"name": "l_orderkey", "order": "asc"},
        {"name": "l_shipdate", "order": "desc"},
    ]
    assert payload["table_partition_configs"]["lineitem"] == ["l_returnflag"]
    assert payload["table_cluster_configs"]["lineitem"] == ["l_partkey", "l_suppkey"]
    assert payload["clustering_method"] == "hilbert"


def test_build_data_organization_from_tuning_empty_returns_none():
    cfg = UnifiedTuningConfiguration()
    assert _build_data_organization_from_tuning(cfg) is None


def test_resolve_payload_uses_tpch_default_for_parquet_sorted():
    payload = _resolve_data_organization_payload("tpch", "parquet-sorted", None)
    assert payload == {
        "table_configs": {"lineitem": [{"name": "l_shipdate", "order": "asc"}]},
        "output_format": "parquet",
    }


def test_resolve_payload_rejects_unsupported_benchmark():
    with pytest.raises(ValueError, match="sorted modes are currently supported"):
        _resolve_data_organization_payload("ssb", "parquet-sorted", None)


def test_resolve_payload_uses_delta_output_format():
    payload = _resolve_data_organization_payload("tpch", "delta-sorted", None)
    assert payload is not None
    assert payload["output_format"] == "delta"


def test_resolve_payload_uses_iceberg_output_format():
    payload = _resolve_data_organization_payload("tpch", "iceberg-sorted", None)
    assert payload is not None
    assert payload["output_format"] == "iceberg"


