from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from benchbox.core.write_primitives.catalog import loader as wp_loader
from benchbox.core.write_primitives.catalog.loader import (
    AggregateStateSpec,
    WritePrimitivesCatalogError,
    load_write_primitives_catalog,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _patch_catalog(monkeypatch: pytest.MonkeyPatch, yaml_text: str) -> None:
    class DummyResource:
        def open(self, mode: str = "r", encoding: str | None = None):
            return io.StringIO(yaml_text)

    monkeypatch.setattr(
        wp_loader,
        "resources",
        SimpleNamespace(files=lambda package: SimpleNamespace(joinpath=lambda filename: DummyResource())),
    )


def test_loader_parses_aggregate_state_block(monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_text = """
version: 1
operations:
  - id: sketch_df_hll_demo
    description: demo aggregate-state op
    write_sql: ""
    aggregate_state:
      sketch_type: hll
      source_table: lineitem
      target_subdir: write_primitives/sketch_df_hll_demo
      group_cols: [l_shipdate, l_returnflag]
      value_col: l_orderkey
      sketch_alias: user_sketch
      supported_platforms: [pyspark]
    validation_queries:
      - id: distinct_in_range
        sql: "SELECT 1"
        expected_value_min: 14250
        expected_value_max: 15750
"""
    _patch_catalog(monkeypatch, yaml_text)
    cat = load_write_primitives_catalog()
    op = cat.operations["sketch_df_hll_demo"]
    assert isinstance(op.aggregate_state, AggregateStateSpec)
    assert op.aggregate_state.sketch_type == "hll"
    assert op.aggregate_state.source_table == "lineitem"
    assert op.aggregate_state.group_cols == ["l_shipdate", "l_returnflag"]
    assert op.aggregate_state.value_col == "l_orderkey"
    assert op.aggregate_state.sketch_alias == "user_sketch"
    assert op.aggregate_state.supported_platforms == ["pyspark"]


def test_loader_allows_empty_write_sql_when_aggregate_state_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    yaml_text = """
version: 1
operations:
  - id: sketch_df_topk_demo
    description: demo topk op without SQL body
    write_sql: ""
    aggregate_state:
      sketch_type: topk
      source_table: lineitem
      target_subdir: write_primitives/sketch_df_topk_demo
      group_cols: [l_shipmode]
      value_col: l_shipmode
      sketch_alias: topk_sketch
      supported_platforms: [pyspark]
"""
    _patch_catalog(monkeypatch, yaml_text)
    cat = load_write_primitives_catalog()
    assert cat.operations["sketch_df_topk_demo"].write_sql == ""


def test_loader_still_requires_write_sql_for_sql_ops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    yaml_text = """
version: 1
operations:
  - id: sketch_query_no_sql
    description: missing write_sql, no aggregate_state
    write_sql: ""
"""
    _patch_catalog(monkeypatch, yaml_text)
    with pytest.raises(WritePrimitivesCatalogError, match="write_sql"):
        load_write_primitives_catalog()


def test_loader_rejects_invalid_sketch_type(monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_text = """
version: 1
operations:
  - id: sketch_df_bad
    description: bad sketch type
    write_sql: ""
    aggregate_state:
      sketch_type: theta
      source_table: lineitem
      target_subdir: write_primitives/bad
      group_cols: [a]
      value_col: x
"""
    _patch_catalog(monkeypatch, yaml_text)
    with pytest.raises(WritePrimitivesCatalogError, match="sketch_type"):
        load_write_primitives_catalog()


def test_loader_rejects_non_string_group_cols(monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_text = """
version: 1
operations:
  - id: sketch_df_bad_groups
    description: bad groups
    write_sql: ""
    aggregate_state:
      sketch_type: hll
      source_table: lineitem
      target_subdir: write_primitives/bad
      group_cols: [1, 2]
      value_col: x
"""
    _patch_catalog(monkeypatch, yaml_text)
    with pytest.raises(WritePrimitivesCatalogError, match="group_cols"):
        load_write_primitives_catalog()


def test_loader_rejects_missing_value_col(monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_text = """
version: 1
operations:
  - id: sketch_df_no_value
    description: missing value_col
    write_sql: ""
    aggregate_state:
      sketch_type: hll
      source_table: lineitem
      target_subdir: write_primitives/bad
      group_cols: [a]
"""
    _patch_catalog(monkeypatch, yaml_text)
    with pytest.raises(WritePrimitivesCatalogError, match="value_col"):
        load_write_primitives_catalog()


def test_real_catalog_exposes_two_aggregate_state_ops() -> None:

    cat = load_write_primitives_catalog()
    ids = {op_id for op_id, op in cat.operations.items() if op.aggregate_state is not None}
    assert {"sketch_df_hll_persist_merge", "sketch_df_topk_persist_merge"} <= ids


