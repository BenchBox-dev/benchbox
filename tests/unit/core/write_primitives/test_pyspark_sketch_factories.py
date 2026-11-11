from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from benchbox.core.write_primitives.dataframe_operations import (
    make_pyspark_hll_merge_extract,
    make_pyspark_hll_persist_builder,
    make_pyspark_topk_merge_extract,
    make_pyspark_topk_persist_builder,
    pyspark_supports_approx_top_k,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _FakeRow:
    def __init__(self, values):
        self._values = list(values)

    def __getitem__(self, key):
        return self._values[key]


@pytest.fixture()
def fake_pyspark_functions(monkeypatch):
    fake_F = MagicMock(name="pyspark.sql.functions")
    fake_pyspark = types.ModuleType("pyspark")
    fake_sql = types.ModuleType("pyspark.sql")
    monkeypatch.setitem(sys.modules, "pyspark", fake_pyspark)
    monkeypatch.setitem(sys.modules, "pyspark.sql", fake_sql)
    monkeypatch.setitem(sys.modules, "pyspark.sql.functions", fake_F)
    return fake_F


class TestPysparkSupportsApproxTopK:
    def test_none_session_returns_false(self):
        assert pyspark_supports_approx_top_k(None) is False

    def test_old_spark_returns_false(self, fake_pyspark_functions):
        spark = MagicMock(version="3.5.1")
        assert pyspark_supports_approx_top_k(spark) is False

    def test_spark_4_0_returns_false(self, fake_pyspark_functions):
        spark = MagicMock(version="4.0.0")
        assert pyspark_supports_approx_top_k(spark) is False

    def test_spark_4_1_returns_true_when_function_exists(self, fake_pyspark_functions):
        spark = MagicMock(version="4.1.0")
        assert pyspark_supports_approx_top_k(spark) is True

    def test_spark_4_2_returns_true(self, fake_pyspark_functions):
        spark = MagicMock(version="4.2.0")
        assert pyspark_supports_approx_top_k(spark) is True

    def test_unparseable_version_returns_false(self):
        spark = MagicMock(version="not-a-version")
        assert pyspark_supports_approx_top_k(spark) is False


class TestPysparkHllPersistBuilder:
    def test_builder_reads_source_and_groups_with_hll_sketch_agg(self, fake_pyspark_functions, tmp_path: Path):
        spark = MagicMock(name="spark")
        chain = MagicMock(name="df_chain")
        spark.read.parquet.return_value = chain
        chain.groupBy.return_value = chain

        builder = make_pyspark_hll_persist_builder(spark, tmp_path / "src", ["activity_date", "region"], "l_orderkey")
        builder()

        spark.read.parquet.assert_called_once_with(str(tmp_path / "src"))
        chain.groupBy.assert_called_once_with("activity_date", "region")
        fake_pyspark_functions.hll_sketch_agg.assert_called_once_with("l_orderkey")
        fake_pyspark_functions.hll_sketch_agg.return_value.alias.assert_called_once_with("sketch")
        chain.agg.assert_called_once_with(fake_pyspark_functions.hll_sketch_agg.return_value.alias.return_value)

    def test_custom_alias_passed_through(self, fake_pyspark_functions, tmp_path: Path):
        spark = MagicMock()
        chain = MagicMock()
        spark.read.parquet.return_value = chain
        chain.groupBy.return_value = chain

        builder = make_pyspark_hll_persist_builder(spark, tmp_path, ["g"], "v", sketch_alias="user_sketch")
        builder()
        fake_pyspark_functions.hll_sketch_agg.return_value.alias.assert_called_once_with("user_sketch")


class TestPysparkHllMergeExtract:
    def test_extractor_reads_state_and_chains_union_then_estimate(self, fake_pyspark_functions, tmp_path: Path):
        spark = MagicMock()
        state = MagicMock(name="state_df")
        spark.read.parquet.return_value = state
        row = _FakeRow([14836.5])
        state.agg.return_value.collect.return_value = [row]

        extractor = make_pyspark_hll_merge_extract(spark, sketch_col="user_sketch")
        result = extractor(tmp_path / "state")

        spark.read.parquet.assert_called_once_with(str(tmp_path / "state"))
        fake_pyspark_functions.hll_union_agg.assert_called_once_with("user_sketch")
        fake_pyspark_functions.hll_sketch_estimate.assert_called_once_with(
            fake_pyspark_functions.hll_union_agg.return_value
        )

        assert result == 14836.5
        assert isinstance(result, float)


class TestPysparkTopkFactories:
    def test_persist_builder_calls_approx_top_k_accumulate(self, fake_pyspark_functions, tmp_path: Path):
        spark = MagicMock()
        chain = MagicMock()
        spark.read.parquet.return_value = chain
        chain.groupBy.return_value = chain

        builder = make_pyspark_topk_persist_builder(spark, tmp_path / "src", ["shard_id"], "l_shipmode")
        builder()

        fake_pyspark_functions.approx_top_k_accumulate.assert_called_once_with("l_shipmode")
        chain.groupBy.assert_called_once_with("shard_id")

    def test_merge_extract_calls_combine_then_estimate_then_size(self, fake_pyspark_functions, tmp_path: Path):
        spark = MagicMock()
        state = MagicMock()
        spark.read.parquet.return_value = state
        row = _FakeRow([7])
        state.agg.return_value.collect.return_value = [row]

        extractor = make_pyspark_topk_merge_extract(spark, sketch_col="topk_sketch")
        result = extractor(tmp_path / "state")

        fake_pyspark_functions.approx_top_k_combine.assert_called_once_with("topk_sketch")
        fake_pyspark_functions.approx_top_k_estimate.assert_called_once_with(
            fake_pyspark_functions.approx_top_k_combine.return_value
        )
        fake_pyspark_functions.size.assert_called_once_with(fake_pyspark_functions.approx_top_k_estimate.return_value)

        assert result == 7.0


