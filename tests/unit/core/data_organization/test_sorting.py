from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest

from benchbox.core.data_organization.config import (
    DataOrganizationConfig,
    SortColumn,
    SortOrder,
)
from benchbox.core.data_organization.sorting import SortedParquetWriter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSortOrder:
    def test_from_string_asc(self):
        assert SortOrder.from_string("asc") == SortOrder.ASC
        assert SortOrder.from_string("ASC") == SortOrder.ASC

    def test_from_string_desc(self):
        assert SortOrder.from_string("desc") == SortOrder.DESC

    def test_from_string_invalid(self):
        with pytest.raises(ValueError, match="Invalid sort order"):
            SortOrder.from_string("random")


class TestSortColumn:
    def test_defaults_to_asc(self):
        sc = SortColumn(name="l_shipdate")
        assert sc.order == SortOrder.ASC

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            SortColumn(name="")

    def test_roundtrip_dict(self):
        sc = SortColumn(name="o_orderdate", order=SortOrder.DESC)
        d = sc.to_dict()
        assert d == {"name": "o_orderdate", "order": "desc"}
        restored = SortColumn.from_dict(d)
        assert restored == sc

    def test_from_dict_missing_name(self):
        with pytest.raises(ValueError, match="requires 'name'"):
            SortColumn.from_dict({"order": "asc"})

    def test_frozen(self):
        sc = SortColumn(name="x")
        with pytest.raises(AttributeError):
            sc.name = "y"


class TestDataOrganizationConfig:
    def test_defaults(self):
        cfg = DataOrganizationConfig()
        assert cfg.sort_columns == []
        assert cfg.partition_by == []
        assert cfg.cluster_by == []
        assert cfg.clustering_method == "z_order"
        assert cfg.output_format == "parquet"
        assert cfg.row_group_size == 128 * 1024 * 1024
        assert cfg.compression == "zstd"
        assert not cfg.has_sorting

    def test_has_sorting_global(self):
        cfg = DataOrganizationConfig(sort_columns=[SortColumn(name="x")])
        assert cfg.has_sorting

    def test_has_sorting_table_override(self):
        cfg = DataOrganizationConfig(table_configs={"lineitem": [SortColumn(name="l_shipdate")]})
        assert cfg.has_sorting

    def test_table_override_takes_priority(self):
        global_cols = [SortColumn(name="x")]
        override_cols = [SortColumn(name="y")]
        cfg = DataOrganizationConfig(sort_columns=global_cols, table_configs={"lineitem": override_cols})
        assert cfg.get_sort_columns_for_table("lineitem") == override_cols
        assert cfg.get_sort_columns_for_table("orders") == global_cols

    def test_invalid_row_group_size(self):
        with pytest.raises(ValueError, match="row_group_size must be positive"):
            DataOrganizationConfig(row_group_size=0)

    def test_invalid_compression(self):
        with pytest.raises(ValueError, match="Invalid compression"):
            DataOrganizationConfig(compression="brotli")

    def test_roundtrip_dict(self):
        cfg = DataOrganizationConfig(
            sort_columns=[SortColumn("a"), SortColumn("b", SortOrder.DESC)],
            partition_by=["p"],
            cluster_by=["c1", "c2"],
            clustering_method="hilbert",
            output_format="delta",
            row_group_size=64 * 1024 * 1024,
            compression="snappy",
            table_configs={"lineitem": [SortColumn("l_shipdate")]},
            table_partition_configs={"lineitem": ["l_returnflag"]},
            table_cluster_configs={"lineitem": ["l_partkey", "l_suppkey"]},
            table_clustering_methods={"lineitem": "z_order"},
        )
        d = cfg.to_dict()
        restored = DataOrganizationConfig.from_dict(d)
        assert restored.sort_columns == cfg.sort_columns
        assert restored.partition_by == cfg.partition_by
        assert restored.cluster_by == cfg.cluster_by
        assert restored.clustering_method == cfg.clustering_method
        assert restored.output_format == cfg.output_format
        assert restored.row_group_size == cfg.row_group_size
        assert restored.compression == cfg.compression
        assert restored.table_configs == cfg.table_configs
        assert restored.table_partition_configs == cfg.table_partition_configs
        assert restored.table_cluster_configs == cfg.table_cluster_configs
        assert restored.table_clustering_methods == cfg.table_clustering_methods

    def test_from_dict_defaults(self):
        cfg = DataOrganizationConfig.from_dict({})
        assert cfg.sort_columns == []
        assert cfg.partition_by == []
        assert cfg.cluster_by == []
        assert cfg.clustering_method == "z_order"
        assert cfg.output_format == "parquet"
        assert cfg.compression == "zstd"


def _write_tbl(path: Path, rows: list[tuple[object, ...]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write("|".join(str(v) for v in row) + "|\n")


def _write_tbl_clean(path: Path, rows: list[tuple[object, ...]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write("|".join(str(v) for v in row) + "\n")


def _write_tbl_crlf(path: Path, rows: list[tuple[object, ...]], *, trailing_delimiter: bool = True) -> None:
    suffix = "|\r\n" if trailing_delimiter else "\r\n"
    payload = b"".join(("|".join(str(v) for v in row) + suffix).encode("utf-8") for row in rows)
    path.write_bytes(payload)


class TestSortedParquetWriterSingleColumn:
    def test_single_column_sorting_writes_sorted_parquet(self, tmp_path: Path):
        source = tmp_path / "lineitem.tbl"
        _write_tbl(
            source,
            [
                (3, "1998-03-01"),
                (1, "1992-01-02"),
                (2, "1996-05-17"),
            ],
        )
        schema_registry = {"lineitem": {"columns": [{"name": "id"}, {"name": "l_shipdate"}]}}
        config = DataOrganizationConfig(sort_columns=[SortColumn(name="l_shipdate")], compression="none")
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        output = writer.write_sorted_parquet("lineitem", [source], tmp_path)

        table = pq.read_table(output)
        assert table.column("l_shipdate").to_pylist() == [date(1992, 1, 2), date(1996, 5, 17), date(1998, 3, 1)]
        assert table.column("id").to_pylist() == [1, 2, 3]

    def test_single_column_sorting_rejects_unknown_column(self, tmp_path: Path):
        source = tmp_path / "orders.tbl"
        _write_tbl(source, [(1, "2024-01-01")])
        schema_registry = {"orders": {"columns": [{"name": "o_orderkey"}, {"name": "o_orderdate"}]}}
        config = DataOrganizationConfig(sort_columns=[SortColumn(name="missing_col")], compression="none")
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        with pytest.raises(ValueError, match="Sort column 'missing_col' not found"):
            writer.write_sorted_parquet("orders", [source], tmp_path)

    def test_reads_normalized_tbl_with_no_trailing_delimiter(self, tmp_path: Path):
        source = tmp_path / "lineitem.tbl"
        _write_tbl_clean(
            source,
            [
                (3, "1998-03-01"),
                (1, "1992-01-02"),
                (2, "1996-05-17"),
            ],
        )
        schema_registry = {"lineitem": {"columns": [{"name": "id"}, {"name": "l_shipdate"}]}}
        config = DataOrganizationConfig(sort_columns=[SortColumn(name="l_shipdate")], compression="none")
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        output = writer.write_sorted_parquet("lineitem", [source], tmp_path)

        table = pq.read_table(output)
        assert table.column_names == ["id", "l_shipdate"]
        assert table.column("l_shipdate").to_pylist() == [date(1992, 1, 2), date(1996, 5, 17), date(1998, 3, 1)]
        assert table.column("id").to_pylist() == [1, 2, 3]

    def test_reads_trailing_delimiter_tbl_with_crlf_line_endings(self, tmp_path: Path):
        source = tmp_path / "lineitem.tbl"
        _write_tbl_crlf(
            source,
            [
                (3, "1998-03-01"),
                (1, "1992-01-02"),
                (2, "1996-05-17"),
            ],
        )
        schema_registry = {"lineitem": {"columns": [{"name": "id"}, {"name": "l_shipdate"}]}}
        config = DataOrganizationConfig(sort_columns=[SortColumn(name="l_shipdate")], compression="none")
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        output = writer.write_sorted_parquet("lineitem", [source], tmp_path)

        table = pq.read_table(output)
        assert table.column_names == ["id", "l_shipdate"]
        assert table.column("l_shipdate").to_pylist() == [date(1992, 1, 2), date(1996, 5, 17), date(1998, 3, 1)]
        assert table.column("id").to_pylist() == [1, 2, 3]

    def test_crlf_source_still_reports_unknown_sort_column(self, tmp_path: Path):
        source = tmp_path / "orders.tbl"
        _write_tbl_crlf(source, [(1, "2024-01-01")])
        schema_registry = {"orders": {"columns": [{"name": "o_orderkey"}, {"name": "o_orderdate"}]}}
        config = DataOrganizationConfig(sort_columns=[SortColumn(name="missing_col")], compression="none")
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        with pytest.raises(ValueError, match="Sort column 'missing_col' not found"):
            writer.write_sorted_parquet("orders", [source], tmp_path)

    def test_reads_clean_tbl_with_crlf_line_endings(self, tmp_path: Path):
        source = tmp_path / "lineitem.tbl"
        _write_tbl_crlf(
            source,
            [
                (3, "1998-03-01"),
                (1, "1992-01-02"),
                (2, "1996-05-17"),
            ],
            trailing_delimiter=False,
        )
        schema_registry = {"lineitem": {"columns": [{"name": "id"}, {"name": "l_shipdate"}]}}
        config = DataOrganizationConfig(sort_columns=[SortColumn(name="l_shipdate")], compression="none")
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        output = writer.write_sorted_parquet("lineitem", [source], tmp_path)

        table = pq.read_table(output)
        assert table.column_names == ["id", "l_shipdate"]
        assert table.column("l_shipdate").to_pylist() == [date(1992, 1, 2), date(1996, 5, 17), date(1998, 3, 1)]
        assert table.column("id").to_pylist() == [1, 2, 3]


class TestSortedParquetWriterMultiColumn:
    def test_multi_column_sorting_with_mixed_order(self, tmp_path: Path):
        source = tmp_path / "orders.tbl"
        _write_tbl(
            source,
            [
                ("A", "2024-01-01", 100),
                ("A", "2024-01-03", 50),
                ("B", "2024-01-02", 70),
                ("B", "2024-01-04", 40),
                ("A", "2024-01-02", 75),
            ],
        )
        schema_registry = {
            "orders": {
                "columns": [
                    {"name": "category"},
                    {"name": "order_date"},
                    {"name": "amount"},
                ]
            }
        }
        config = DataOrganizationConfig(
            sort_columns=[
                SortColumn(name="category", order=SortOrder.ASC),
                SortColumn(name="order_date", order=SortOrder.DESC),
            ],
            compression="none",
        )
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        output = writer.write_sorted_parquet("orders", [source], tmp_path)
        table = pq.read_table(output)

        assert table.column("category").to_pylist() == ["A", "A", "A", "B", "B"]
        assert table.column("order_date").to_pylist() == [
            date(2024, 1, 3),
            date(2024, 1, 2),
            date(2024, 1, 1),
            date(2024, 1, 4),
            date(2024, 1, 2),
        ]

    def test_multi_column_sorting_with_table_override(self, tmp_path: Path):
        source = tmp_path / "lineitem.tbl"
        _write_tbl(
            source,
            [
                (1, "2024-01-01", 10),
                (2, "2024-01-01", 30),
                (3, "2024-01-01", 20),
            ],
        )
        schema_registry = {
            "lineitem": {
                "columns": [
                    {"name": "id"},
                    {"name": "ship_date"},
                    {"name": "qty"},
                ]
            }
        }
        config = DataOrganizationConfig(
            sort_columns=[SortColumn(name="id", order=SortOrder.DESC)],
            table_configs={
                "lineitem": [
                    SortColumn(name="ship_date", order=SortOrder.ASC),
                    SortColumn(name="qty", order=SortOrder.DESC),
                ]
            },
            compression="none",
        )
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        output = writer.write_sorted_parquet("lineitem", [source], tmp_path)
        table = pq.read_table(output)
        assert table.column("qty").to_pylist() == [30, 20, 10]


class TestSortedParquetWriterRowGroupSize:
    def test_row_group_size_is_applied_to_parquet_writer(self, tmp_path: Path):
        source = tmp_path / "lineitem.tbl"
        _write_tbl(
            source,
            [
                (1, "1992-01-01"),
                (2, "1992-01-02"),
                (3, "1992-01-03"),
                (4, "1992-01-04"),
                (5, "1992-01-05"),
            ],
        )
        schema_registry = {"lineitem": {"columns": [{"name": "id"}, {"name": "l_shipdate"}]}}
        config = DataOrganizationConfig(
            sort_columns=[SortColumn(name="l_shipdate", order=SortOrder.ASC)],
            row_group_size=2,
            compression="none",
        )
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        output = writer.write_sorted_parquet("lineitem", [source], tmp_path)
        metadata = pq.ParquetFile(output).metadata

        assert metadata is not None
        assert metadata.num_row_groups == 3


class TestSortedParquetWriterPartitionAware:
    def test_partition_columns_are_applied_before_sort_columns(self, tmp_path: Path):
        source = tmp_path / "lineitem.tbl"
        _write_tbl(
            source,
            [
                ("west", "1992-01-03", 3),
                ("east", "1992-01-02", 2),
                ("west", "1992-01-01", 1),
                ("east", "1992-01-04", 4),
            ],
        )
        schema_registry = {
            "lineitem": {
                "columns": [
                    {"name": "region"},
                    {"name": "l_shipdate"},
                    {"name": "id"},
                ]
            }
        }
        config = DataOrganizationConfig(
            sort_columns=[SortColumn(name="l_shipdate", order=SortOrder.ASC)],
            partition_by=["region"],
            compression="none",
        )
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        output = writer.write_sorted_parquet("lineitem", [source], tmp_path)
        table = pq.read_table(output)

        assert table.column("region").to_pylist() == ["east", "east", "west", "west"]
        assert table.column("l_shipdate").to_pylist() == [
            date(1992, 1, 2),
            date(1992, 1, 4),
            date(1992, 1, 1),
            date(1992, 1, 3),
        ]

    def test_partition_column_validation(self, tmp_path: Path):
        source = tmp_path / "lineitem.tbl"
        _write_tbl(source, [("east", "1992-01-01")])
        schema_registry = {"lineitem": {"columns": [{"name": "region"}, {"name": "l_shipdate"}]}}
        config = DataOrganizationConfig(
            sort_columns=[SortColumn(name="l_shipdate", order=SortOrder.ASC)],
            partition_by=["missing_partition"],
            compression="none",
        )
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        with pytest.raises(ValueError, match="Partition column 'missing_partition' not found"):
            writer.write_sorted_parquet("lineitem", [source], tmp_path)


class TestSortedParquetWriterClustering:
    def test_z_order_clustering_applied_after_sort(self, tmp_path: Path):
        source = tmp_path / "lineitem.tbl"
        _write_tbl(
            source,
            [
                (1, 1, 40),
                (0, 1, 30),
                (1, 0, 20),
                (0, 0, 10),
            ],
        )
        schema_registry = {"lineitem": {"columns": [{"name": "x"}, {"name": "y"}, {"name": "payload"}]}}
        config = DataOrganizationConfig(cluster_by=["x", "y"], clustering_method="z_order", compression="none")
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        output = writer.write_sorted_parquet("lineitem", [source], tmp_path)
        table = pq.read_table(output)

        assert table.column("x").to_pylist() == [0, 1, 0, 1]
        assert table.column("y").to_pylist() == [0, 0, 1, 1]
        assert table.column("payload").to_pylist() == [10, 20, 30, 40]


class TestSortedParquetWriterSafetyLimit:
    def test_rejects_large_reordering_input(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        source = tmp_path / "lineitem.tbl"
        _write_tbl(source, [(1, "1992-01-01")])
        schema_registry = {"lineitem": {"columns": [{"name": "id"}, {"name": "l_shipdate"}]}}
        config = DataOrganizationConfig(sort_columns=[SortColumn(name="l_shipdate")], compression="none")
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        monkeypatch.setenv("BENCHBOX_DATA_ORG_MAX_IN_MEMORY_BYTES", "1")
        with pytest.raises(RuntimeError, match="exceeds safety limit"):
            writer.write_sorted_parquet("lineitem", [source], tmp_path)


class TestSortedParquetWriterOpenTableFormatOptions:
    def test_iceberg_conversion_enables_row_count_validation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        source = tmp_path / "lineitem.tbl"
        _write_tbl(source, [(2, "1992-01-02"), (1, "1992-01-01")])
        schema_registry = {"lineitem": {"columns": [{"name": "id"}, {"name": "l_shipdate"}]}}
        config = DataOrganizationConfig(
            sort_columns=[SortColumn(name="l_shipdate", order=SortOrder.ASC)],
            output_format="iceberg",
            compression="none",
        )
        writer = SortedParquetWriter(config=config, schema_registry=schema_registry)

        captured: dict[str, bool] = {}

        class _FakeOptions:
            def __init__(self, **kwargs):
                captured["validate_row_count"] = kwargs["validate_row_count"]
                self.output_dir = kwargs["output_dir"]

        class _FakeConverter:
            def convert(self, source_files, table_name, schema, options):
                output = options.output_dir / table_name
                output.mkdir(parents=True, exist_ok=True)
                return SimpleNamespace(output_files=[output])

        monkeypatch.setattr("benchbox.utils.format_converters.ConversionOptions", _FakeOptions)
        monkeypatch.setattr("benchbox.utils.format_converters.IcebergConverter", _FakeConverter)

        writer.write_sorted_parquet("lineitem", [source], tmp_path)
        assert captured["validate_row_count"] is True


