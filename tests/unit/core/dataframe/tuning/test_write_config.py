# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.dataframe.tuning.write_config import (
    DataFrameWriteConfiguration,
    DataFrameWriteTuningType,
    PartitionColumn,
    PartitionStrategy,
    SortColumn,
    get_platform_write_capabilities,
    validate_write_config_for_platform,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSortColumn:
    def test_default_order(self) -> None:

        col = SortColumn(name="l_shipdate")
        assert col.order == "asc"

    def test_explicit_order(self) -> None:

        col = SortColumn(name="l_orderkey", order="desc")
        assert col.order == "desc"

    def test_empty_name_raises(self) -> None:

        with pytest.raises(ValueError, match="cannot be empty"):
            SortColumn(name="")

    def test_invalid_order_raises(self) -> None:

        with pytest.raises(ValueError, match="Must be 'asc' or 'desc'"):
            SortColumn(name="col", order="ascending")

    def test_to_dict(self) -> None:

        col = SortColumn(name="l_shipdate", order="desc")
        assert col.to_dict() == {"name": "l_shipdate", "order": "desc"}

    def test_from_dict(self) -> None:

        data = {"name": "l_orderkey", "order": "asc"}
        col = SortColumn.from_dict(data)
        assert col.name == "l_orderkey"
        assert col.order == "asc"

    def test_from_dict_default_order(self) -> None:

        data = {"name": "l_shipdate"}
        col = SortColumn.from_dict(data)
        assert col.order == "asc"


class TestPartitionColumn:
    def test_default_strategy(self) -> None:

        col = PartitionColumn(name="o_orderdate")
        assert col.strategy == PartitionStrategy.VALUE

    def test_explicit_strategy(self) -> None:

        col = PartitionColumn(name="o_orderdate", strategy=PartitionStrategy.DATE_MONTH)
        assert col.strategy == PartitionStrategy.DATE_MONTH

    def test_empty_name_raises(self) -> None:

        with pytest.raises(ValueError, match="cannot be empty"):
            PartitionColumn(name="")

    def test_to_dict(self) -> None:

        col = PartitionColumn(name="o_orderdate", strategy=PartitionStrategy.DATE_YEAR)
        assert col.to_dict() == {"name": "o_orderdate", "strategy": "date_year"}

    def test_from_dict(self) -> None:

        data = {"name": "event_date", "strategy": "date_month"}
        col = PartitionColumn.from_dict(data)
        assert col.name == "event_date"
        assert col.strategy == PartitionStrategy.DATE_MONTH


class TestPartitionStrategy:
    def test_enum_values(self) -> None:

        assert PartitionStrategy.VALUE.value == "value"
        assert PartitionStrategy.DATE_YEAR.value == "date_year"
        assert PartitionStrategy.DATE_MONTH.value == "date_month"
        assert PartitionStrategy.DATE_DAY.value == "date_day"


class TestDataFrameWriteConfiguration:
    def test_default_configuration(self) -> None:

        config = DataFrameWriteConfiguration()
        assert config.partition_by == []
        assert config.sort_by == []
        assert config.row_group_size is None
        assert config.repartition_count is None
        assert config.compression == "zstd"
        assert config.compression_level is None
        assert config.data_page_version is None
        assert config.is_default()

    def test_with_sort_by(self) -> None:

        config = DataFrameWriteConfiguration(
            sort_by=[
                SortColumn(name="l_shipdate", order="asc"),
                SortColumn(name="l_orderkey", order="desc"),
            ]
        )
        assert len(config.sort_by) == 2
        assert config.sort_by[0].name == "l_shipdate"
        assert config.sort_by[1].order == "desc"
        assert not config.is_default()

    def test_with_partition_by(self) -> None:

        config = DataFrameWriteConfiguration(
            partition_by=[
                PartitionColumn(name="o_orderdate", strategy=PartitionStrategy.DATE_MONTH),
            ]
        )
        assert len(config.partition_by) == 1
        assert config.partition_by[0].strategy == PartitionStrategy.DATE_MONTH

    def test_row_group_size_validation(self) -> None:

        with pytest.raises(ValueError, match="row_group_size must be >= 1"):
            DataFrameWriteConfiguration(row_group_size=0)

    def test_repartition_count_validation(self) -> None:

        with pytest.raises(ValueError, match="repartition_count must be >= 1"):
            DataFrameWriteConfiguration(repartition_count=-1)

    def test_compression_level_validation_zstd(self) -> None:

        with pytest.raises(ValueError, match="out of range for zstd"):
            DataFrameWriteConfiguration(compression="zstd", compression_level=25)

        config = DataFrameWriteConfiguration(compression="zstd", compression_level=9)
        assert config.compression_level == 9

    def test_compression_level_validation_gzip(self) -> None:

        with pytest.raises(ValueError, match="out of range for gzip"):
            DataFrameWriteConfiguration(compression="gzip", compression_level=10)

        config = DataFrameWriteConfiguration(compression="gzip", compression_level=6)
        assert config.compression_level == 6

    def test_data_page_version(self) -> None:

        config_v1 = DataFrameWriteConfiguration(data_page_version="1.0")
        assert config_v1.data_page_version == "1.0"
        assert not config_v1.is_default()

        config_v2 = DataFrameWriteConfiguration(data_page_version="2.0")
        assert config_v2.data_page_version == "2.0"
        assert not config_v2.is_default()

        config_none = DataFrameWriteConfiguration(data_page_version=None)
        assert config_none.data_page_version is None
        assert config_none.is_default()


class TestDataFrameWriteConfigurationSerialization:
    def test_to_dict_empty(self) -> None:

        config = DataFrameWriteConfiguration()
        assert config.to_dict() == {}

    def test_to_dict_with_sort(self) -> None:

        config = DataFrameWriteConfiguration(sort_by=[SortColumn(name="l_shipdate", order="asc")])
        result = config.to_dict()
        assert result == {"sort_by": [{"name": "l_shipdate", "order": "asc"}]}

    def test_to_dict_with_partition(self) -> None:

        config = DataFrameWriteConfiguration(
            partition_by=[PartitionColumn(name="o_orderdate", strategy=PartitionStrategy.DATE_MONTH)]
        )
        result = config.to_dict()
        assert result == {"partition_by": [{"name": "o_orderdate", "strategy": "date_month"}]}

    def test_to_dict_with_data_page_version(self) -> None:

        config = DataFrameWriteConfiguration(data_page_version="2.0")
        result = config.to_dict()
        assert result == {"data_page_version": "2.0"}

    def test_to_dict_full(self) -> None:

        config = DataFrameWriteConfiguration(
            partition_by=[PartitionColumn(name="year")],
            sort_by=[SortColumn(name="l_shipdate")],
            row_group_size=1000000,
            repartition_count=8,
            compression="gzip",
            compression_level=6,
            dictionary_columns=["l_comment"],
        )
        result = config.to_dict()

        assert "partition_by" in result
        assert "sort_by" in result
        assert result["row_group_size"] == 1000000
        assert result["repartition_count"] == 8
        assert result["compression"] == "gzip"
        assert result["compression_level"] == 6
        assert result["dictionary_columns"] == ["l_comment"]

    def test_from_dict_empty(self) -> None:

        config = DataFrameWriteConfiguration.from_dict({})
        assert config.is_default()

    def test_from_dict_with_string_columns(self) -> None:

        data = {
            "sort_by": ["l_shipdate", "l_orderkey"],
            "partition_by": ["o_orderdate"],
        }
        config = DataFrameWriteConfiguration.from_dict(data)

        assert len(config.sort_by) == 2
        assert config.sort_by[0].name == "l_shipdate"
        assert config.sort_by[0].order == "asc"

        assert len(config.partition_by) == 1
        assert config.partition_by[0].strategy == PartitionStrategy.VALUE

    def test_from_dict_full(self) -> None:

        data = {
            "partition_by": [{"name": "year", "strategy": "date_year"}],
            "sort_by": [{"name": "l_shipdate", "order": "desc"}],
            "row_group_size": 500000,
            "repartition_count": 4,
            "compression": "lz4",
            "compression_level": 5,
            "dictionary_columns": ["l_comment"],
            "skip_dictionary_columns": ["l_orderkey"],
            "data_page_version": "1.0",
        }
        config = DataFrameWriteConfiguration.from_dict(data)

        assert config.partition_by[0].name == "year"
        assert config.partition_by[0].strategy == PartitionStrategy.DATE_YEAR
        assert config.sort_by[0].order == "desc"
        assert config.row_group_size == 500000
        assert config.repartition_count == 4
        assert config.compression == "lz4"
        assert config.compression_level == 5
        assert config.dictionary_columns == ["l_comment"]
        assert config.skip_dictionary_columns == ["l_orderkey"]
        assert config.data_page_version == "1.0"


class TestGetEnabledTypes:
    def test_empty_config(self) -> None:

        config = DataFrameWriteConfiguration()
        assert config.get_enabled_types() == set()

    def test_with_sort(self) -> None:

        config = DataFrameWriteConfiguration(sort_by=[SortColumn(name="col")])
        types = config.get_enabled_types()
        assert DataFrameWriteTuningType.SORT_BY in types

    def test_with_partition(self) -> None:

        config = DataFrameWriteConfiguration(partition_by=[PartitionColumn(name="col")])
        types = config.get_enabled_types()
        assert DataFrameWriteTuningType.PARTITION_BY in types

    def test_with_row_group_size(self) -> None:

        config = DataFrameWriteConfiguration(row_group_size=1000000)
        types = config.get_enabled_types()
        assert DataFrameWriteTuningType.ROW_GROUP_SIZE in types

    def test_with_compression_level(self) -> None:

        config = DataFrameWriteConfiguration(compression_level=6)
        types = config.get_enabled_types()
        assert DataFrameWriteTuningType.COMPRESSION in types

    def test_with_data_page_version(self) -> None:

        config = DataFrameWriteConfiguration(data_page_version="2.0")
        types = config.get_enabled_types()
        assert DataFrameWriteTuningType.DATA_PAGE_VERSION in types


class TestPlatformCapabilities:
    def test_polars_capabilities(self) -> None:

        caps = get_platform_write_capabilities("polars")
        assert caps["sort_by"] is True
        assert caps["partition_by"] is False
        assert caps["row_group_size"] is True
        assert caps["repartition_count"] is False

    def test_dask_capabilities(self) -> None:

        caps = get_platform_write_capabilities("dask")
        assert caps["sort_by"] is False
        assert caps["partition_by"] is True
        assert caps["repartition_count"] is True

    def test_pyspark_capabilities(self) -> None:

        caps = get_platform_write_capabilities("pyspark")
        assert caps["sort_by"] is True
        assert caps["partition_by"] is True
        assert caps["repartition_count"] is True

    def test_unknown_platform_defaults(self) -> None:

        caps = get_platform_write_capabilities("unknown_platform")
        assert caps["sort_by"] is True
        assert caps["partition_by"] is False


class TestValidateWriteConfigForPlatform:
    def test_valid_polars_config(self) -> None:

        config = DataFrameWriteConfiguration(
            sort_by=[SortColumn(name="l_shipdate")],
            row_group_size=1000000,
        )
        warnings = validate_write_config_for_platform(config, "polars")
        assert len(warnings) == 0

    def test_partition_warning_for_polars(self) -> None:

        config = DataFrameWriteConfiguration(partition_by=[PartitionColumn(name="o_orderdate")])
        warnings = validate_write_config_for_platform(config, "polars")
        assert len(warnings) == 1
        assert "does not support partitioned writes" in warnings[0]

    def test_repartition_warning_for_pandas(self) -> None:

        config = DataFrameWriteConfiguration(repartition_count=8)
        warnings = validate_write_config_for_platform(config, "pandas")
        assert len(warnings) == 1
        assert "does not support repartitioning" in warnings[0]

    def test_sort_warning_for_dask(self) -> None:

        config = DataFrameWriteConfiguration(sort_by=[SortColumn(name="l_shipdate")])
        warnings = validate_write_config_for_platform(config, "dask")
        assert len(warnings) == 1
        assert "limited sort support" in warnings[0]

    def test_valid_pyspark_config(self) -> None:

        config = DataFrameWriteConfiguration(
            partition_by=[PartitionColumn(name="year")],
            sort_by=[SortColumn(name="l_shipdate")],
            repartition_count=8,
        )
        warnings = validate_write_config_for_platform(config, "pyspark")
        assert len(warnings) == 0


class TestDataFrameWriteTuningType:
    def test_enum_values(self) -> None:

        assert DataFrameWriteTuningType.PARTITION_BY.value == "partition_by"
        assert DataFrameWriteTuningType.SORT_BY.value == "sort_by"
        assert DataFrameWriteTuningType.ROW_GROUP_SIZE.value == "row_group_size"
        assert DataFrameWriteTuningType.REPARTITION.value == "repartition"
        assert DataFrameWriteTuningType.COMPRESSION.value == "compression"
        assert DataFrameWriteTuningType.DICTIONARY_ENCODING.value == "dictionary_encoding"
        assert DataFrameWriteTuningType.DATA_PAGE_VERSION.value == "data_page_version"


class TestConversionOptionsDataPageVersionValidation:
    def test_valid_data_page_versions(self):
        from benchbox.utils.format_converters.base import ConversionOptions

        opts_1 = ConversionOptions(data_page_version="1.0")
        assert opts_1.data_page_version == "1.0"

        opts_2 = ConversionOptions(data_page_version="2.0")
        assert opts_2.data_page_version == "2.0"

    def test_none_is_valid(self):
        from benchbox.utils.format_converters.base import ConversionOptions

        opts = ConversionOptions(data_page_version=None)
        assert opts.data_page_version is None

    def test_invalid_data_page_version_raises(self):
        from benchbox.utils.format_converters.base import ConversionOptions

        with pytest.raises(ValueError, match="Invalid data_page_version"):
            ConversionOptions(data_page_version="3.0")


