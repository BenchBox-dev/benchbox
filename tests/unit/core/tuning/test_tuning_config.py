# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tuning import (
    ClusteringConfig,
    PartitioningConfig,
    SortKeyConfig,
    TuningColumn,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTuningColumnEnhancements:
    def test_default_values(self) -> None:

        col = TuningColumn(name="test_col", type="INTEGER", order=1)
        assert col.sort_order == "ASC"
        assert col.nulls_position == "DEFAULT"
        assert col.compression is None

    def test_sort_order_desc(self) -> None:

        col = TuningColumn(name="test_col", type="INTEGER", order=1, sort_order="DESC")
        assert col.sort_order == "DESC"

    def test_invalid_sort_order(self) -> None:

        with pytest.raises(ValueError, match="Invalid sort_order"):
            TuningColumn(name="test_col", type="INTEGER", order=1, sort_order="INVALID")

    def test_nulls_first(self) -> None:

        col = TuningColumn(name="test_col", type="INTEGER", order=1, nulls_position="FIRST")
        assert col.nulls_position == "FIRST"

    def test_nulls_last(self) -> None:

        col = TuningColumn(name="test_col", type="INTEGER", order=1, nulls_position="LAST")
        assert col.nulls_position == "LAST"

    def test_invalid_nulls_position(self) -> None:

        with pytest.raises(ValueError, match="Invalid nulls_position"):
            TuningColumn(name="test_col", type="INTEGER", order=1, nulls_position="NONE")

    def test_compression(self) -> None:

        col = TuningColumn(name="test_col", type="VARCHAR", order=1, compression="lzo")
        assert col.compression == "lzo"

    def test_to_dict_defaults_omitted(self) -> None:

        col = TuningColumn(name="test_col", type="INTEGER", order=1)
        result = col.to_dict()
        assert "sort_order" not in result
        assert "nulls_position" not in result
        assert "compression" not in result

    def test_to_dict_non_defaults_included(self) -> None:

        col = TuningColumn(
            name="test_col",
            type="INTEGER",
            order=1,
            sort_order="DESC",
            nulls_position="FIRST",
            compression="az64",
        )
        result = col.to_dict()
        assert result["sort_order"] == "DESC"
        assert result["nulls_position"] == "FIRST"
        assert result["compression"] == "az64"

    def test_from_dict_with_new_fields(self) -> None:

        data = {
            "name": "test_col",
            "type": "INTEGER",
            "order": 1,
            "sort_order": "DESC",
            "nulls_position": "LAST",
            "compression": "zstd",
        }
        col = TuningColumn.from_dict(data)
        assert col.sort_order == "DESC"
        assert col.nulls_position == "LAST"
        assert col.compression == "zstd"

    def test_from_dict_backward_compatible(self) -> None:

        data = {
            "name": "test_col",
            "type": "INTEGER",
            "order": 1,
        }
        col = TuningColumn.from_dict(data)
        assert col.sort_order == "ASC"
        assert col.nulls_position == "DEFAULT"
        assert col.compression is None


class TestPartitioningConfig:
    def test_basic_creation(self) -> None:

        columns = [TuningColumn(name="l_shipdate", type="DATE", order=1)]
        config = PartitioningConfig(columns=columns)
        assert config.strategy == "RANGE"
        assert config.granularity is None
        assert config.bucket_count is None

    def test_date_strategy_with_granularity(self) -> None:

        columns = [TuningColumn(name="l_shipdate", type="DATE", order=1)]
        config = PartitioningConfig(columns=columns, strategy="DATE", granularity="MONTHLY")
        assert config.strategy == "DATE"
        assert config.granularity == "MONTHLY"

    def test_hash_strategy_with_bucket_count(self) -> None:

        columns = [TuningColumn(name="l_orderkey", type="INTEGER", order=1)]
        config = PartitioningConfig(columns=columns, strategy="HASH", bucket_count=64)
        assert config.strategy == "HASH"
        assert config.bucket_count == 64

    def test_invalid_strategy(self) -> None:

        columns = [TuningColumn(name="test", type="INTEGER", order=1)]
        with pytest.raises(ValueError, match="Invalid strategy"):
            PartitioningConfig(columns=columns, strategy="INVALID")

    def test_invalid_bucket_count(self) -> None:

        columns = [TuningColumn(name="test", type="INTEGER", order=1)]
        with pytest.raises(ValueError, match="bucket_count must be a positive integer"):
            PartitioningConfig(columns=columns, strategy="HASH", bucket_count=-1)

    def test_to_dict_minimal(self) -> None:

        columns = [TuningColumn(name="l_shipdate", type="DATE", order=1)]
        config = PartitioningConfig(columns=columns)
        result = config.to_dict()
        assert "columns" in result
        assert "strategy" not in result

    def test_to_dict_full(self) -> None:

        columns = [TuningColumn(name="l_shipdate", type="DATE", order=1)]
        config = PartitioningConfig(columns=columns, strategy="DATE", granularity="DAILY")
        result = config.to_dict()
        assert result["strategy"] == "DATE"
        assert result["granularity"] == "DAILY"

    def test_from_dict(self) -> None:

        data = {
            "columns": [{"name": "l_shipdate", "type": "DATE", "order": 1}],
            "strategy": "DATE",
            "granularity": "MONTHLY",
        }
        config = PartitioningConfig.from_dict(data)
        assert config.strategy == "DATE"
        assert config.granularity == "MONTHLY"
        assert len(config.columns) == 1


class TestSortKeyConfig:
    def test_default_compound_style(self) -> None:

        columns = [TuningColumn(name="l_orderkey", type="INTEGER", order=1)]
        config = SortKeyConfig(columns=columns)
        assert config.style == "COMPOUND"

    def test_interleaved_style(self) -> None:

        columns = [TuningColumn(name="l_orderkey", type="INTEGER", order=1)]
        config = SortKeyConfig(columns=columns, style="INTERLEAVED")
        assert config.style == "INTERLEAVED"

    def test_auto_style(self) -> None:

        columns = [TuningColumn(name="l_orderkey", type="INTEGER", order=1)]
        config = SortKeyConfig(columns=columns, style="AUTO")
        assert config.style == "AUTO"

    def test_invalid_style(self) -> None:

        columns = [TuningColumn(name="test", type="INTEGER", order=1)]
        with pytest.raises(ValueError, match="Invalid style"):
            SortKeyConfig(columns=columns, style="INVALID")

    def test_to_dict_default_omitted(self) -> None:

        columns = [TuningColumn(name="l_orderkey", type="INTEGER", order=1)]
        config = SortKeyConfig(columns=columns)
        result = config.to_dict()
        assert "style" not in result

    def test_to_dict_non_default_included(self) -> None:

        columns = [TuningColumn(name="l_orderkey", type="INTEGER", order=1)]
        config = SortKeyConfig(columns=columns, style="INTERLEAVED")
        result = config.to_dict()
        assert result["style"] == "INTERLEAVED"

    def test_from_dict(self) -> None:

        data = {
            "columns": [{"name": "l_orderkey", "type": "INTEGER", "order": 1}],
            "style": "INTERLEAVED",
        }
        config = SortKeyConfig.from_dict(data)
        assert config.style == "INTERLEAVED"
        assert len(config.columns) == 1


class TestClusteringConfig:
    def test_basic_creation(self) -> None:

        columns = [TuningColumn(name="l_partkey", type="INTEGER", order=1)]
        config = ClusteringConfig(columns=columns)
        assert config.bucket_count is None

    def test_with_bucket_count(self) -> None:

        columns = [TuningColumn(name="l_partkey", type="INTEGER", order=1)]
        config = ClusteringConfig(columns=columns, bucket_count=64)
        assert config.bucket_count == 64

    def test_invalid_bucket_count(self) -> None:

        columns = [TuningColumn(name="test", type="INTEGER", order=1)]
        with pytest.raises(ValueError, match="bucket_count must be a positive integer"):
            ClusteringConfig(columns=columns, bucket_count=0)

    def test_to_dict_without_bucket_count(self) -> None:

        columns = [TuningColumn(name="l_partkey", type="INTEGER", order=1)]
        config = ClusteringConfig(columns=columns)
        result = config.to_dict()
        assert "bucket_count" not in result

    def test_to_dict_with_bucket_count(self) -> None:

        columns = [TuningColumn(name="l_partkey", type="INTEGER", order=1)]
        config = ClusteringConfig(columns=columns, bucket_count=32)
        result = config.to_dict()
        assert result["bucket_count"] == 32

    def test_from_dict(self) -> None:

        data = {
            "columns": [{"name": "l_partkey", "type": "INTEGER", "order": 1}],
            "bucket_count": 64,
        }
        config = ClusteringConfig.from_dict(data)
        assert config.bucket_count == 64
        assert len(config.columns) == 1


