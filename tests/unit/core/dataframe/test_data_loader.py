# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.dataframe.capabilities import DataFormat
from benchbox.core.dataframe.data_loader import (
    DATAFRAME_CACHE_VERSION,
    CacheManifest,
    ConversionStatus,
    DataCache,
    DataFrameDataLoader,
    DataLoadResult,
    FormatConverter,
    LoadedTable,
    SchemaMapper,
    _compute_source_hash,
    get_tpch_column_names,
)
from benchbox.core.dataframe.tuning.write_config import (
    DataFrameWriteConfiguration,
    SortColumn,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSchemaMapper:
    def test_get_column_names(self):

        try:
            from benchbox.core.tpch.schema import CUSTOMER

            names = SchemaMapper.get_column_names(CUSTOMER)

            assert len(names) == 8
            assert names[0] == "c_custkey"
            assert "c_name" in names
            assert "c_mktsegment" in names
        except ImportError:
            pytest.skip("TPC-H schema not available")

    def test_get_polars_schema(self):

        try:
            from benchbox.core.tpch.schema import LINEITEM

            schema = SchemaMapper.get_polars_schema(LINEITEM)

            assert schema["l_orderkey"] == "Int64"
            assert schema["l_quantity"] == "Float64"
            assert schema["l_returnflag"] == "Utf8"
            assert schema["l_shipdate"] == "Date"
        except ImportError:
            pytest.skip("TPC-H schema not available")

    def test_get_pandas_schema(self):

        try:
            from benchbox.core.tpch.schema import ORDERS

            schema = SchemaMapper.get_pandas_schema(ORDERS)

            assert schema["o_orderkey"] == "int64"
            assert schema["o_totalprice"] == "float64"
            assert schema["o_orderstatus"] == "object"
            assert schema["o_orderdate"] == "datetime64[ns]"
        except ImportError:
            pytest.skip("TPC-H schema not available")

    def test_get_pyarrow_schema(self):

        try:
            from benchbox.core.tpch.schema import SUPPLIER

            schema = SchemaMapper.get_pyarrow_schema(SUPPLIER)

            assert schema["s_suppkey"] == "int64"
            assert schema["s_acctbal"] == "float64"
            assert schema["s_name"] == "string"
        except ImportError:
            pytest.skip("TPC-H schema not available")


class TestFormatConverter:
    def test_convert_csv_to_parquet_basic(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            csv_path = tmpdir / "test.csv"
            csv_path.write_text("a,b,c\n1,2,3\n4,5,6\n")

            parquet_path = tmpdir / "test.parquet"

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=csv_path,
                target_path=parquet_path,
                delimiter=",",
            )

            assert status == ConversionStatus.SUCCESS
            assert row_count == 2
            assert parquet_path.exists()

    def test_convert_declared_string_columns_survive_production_load_path(self):
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            tbl_path = tmpdir / "codes.tbl"
            tbl_path.write_text("007|\n010|\n100|\n")
            parquet_path = tmpdir / "codes.parquet"

            class _DeclaredStringBenchmark:
                name = "declared_string_fixture"

                def get_schema(self):
                    return {
                        "codes": {
                            "columns": [
                                {"name": "code", "type": "VARCHAR"},
                                {"name": "note", "type": "TEXT"},
                            ]
                        }
                    }

            benchmark = _DeclaredStringBenchmark()
            loader = DataFrameDataLoader(platform="polars")

            pyarrow_types = loader._get_pyarrow_types(benchmark)
            derived_types = pyarrow_types["codes"]
            assert derived_types["code"] == "string", (
                f"production loader must derive a string Arrow type for the declared "
                f"VARCHAR column; got {derived_types.get('code')!r}"
            )
            assert derived_types["note"] == "string", (
                f"production loader must derive a string Arrow type for the declared "
                f"TEXT column; got {derived_types.get('note')!r}"
            )

            null_markers = loader._get_null_markers(benchmark, {"codes": tbl_path})
            assert null_markers["codes"] == "", (
                f"a .tbl source should resolve to the empty->NULL marker; got {null_markers['codes']!r}"
            )

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=tbl_path,
                target_path=parquet_path,
                column_names=["code", "note"],
                delimiter="|",
                column_types=derived_types,
                null_marker=null_markers["codes"],
            )

            assert status == ConversionStatus.SUCCESS
            assert row_count == 3
            table = pq.read_table(parquet_path)
            assert table.column("code").to_pylist() == ["007", "010", "100"]
            assert str(table.schema.field("note").type) in {"string", "large_string"}

    def test_convert_tbl_file(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            tbl_path = tmpdir / "test.tbl"
            tbl_path.write_text("1|Alice|100.00|\n2|Bob|200.00|\n")

            parquet_path = tmpdir / "test.parquet"
            column_names = ["id", "name", "amount"]

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=tbl_path,
                target_path=parquet_path,
                column_names=column_names,
                delimiter="|",
            )

            assert status == ConversionStatus.SUCCESS
            assert row_count == 2

    def test_convert_tbl_file_without_trailing_delimiter(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            tbl_path = tmpdir / "test.tbl"
            tbl_path.write_text("1|Alice|100.00\n2|Bob|200.00\n")

            parquet_path = tmpdir / "test.parquet"
            column_names = ["id", "name", "amount"]

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=tbl_path,
                target_path=parquet_path,
                column_names=column_names,
                delimiter="|",
            )

            assert status == ConversionStatus.SUCCESS
            assert row_count == 2

    def test_convert_compressed_tbl_file(self):

        zstd = pytest.importorskip("zstandard")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            tbl_path = tmpdir / "test.tbl"
            tbl_path.write_text("1|Alice|100.00|\n2|Bob|200.00|\n")

            compressed_path = tmpdir / "test.tbl.zst"
            compressor = zstd.ZstdCompressor(level=3)
            with open(tbl_path, "rb") as f_in, open(compressed_path, "wb") as f_out:
                compressor.copy_stream(f_in, f_out)

            parquet_path = tmpdir / "test.parquet"
            column_names = ["id", "name", "amount"]

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=compressed_path,
                target_path=parquet_path,
                column_names=column_names,
                delimiter="|",
            )

            assert status == ConversionStatus.SUCCESS
            assert row_count == 2

    def test_convert_creates_parent_dirs(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            csv_path = tmpdir / "test.csv"
            csv_path.write_text("x,y\n1,2\n")

            parquet_path = tmpdir / "nested" / "deep" / "test.parquet"

            status, _ = FormatConverter.convert_csv_to_parquet(
                source_path=csv_path,
                target_path=parquet_path,
                delimiter=",",
            )

            assert status == ConversionStatus.SUCCESS
            assert parquet_path.exists()

    def test_convert_nonexistent_file(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=tmpdir / "nonexistent.csv",
                target_path=tmpdir / "out.parquet",
            )

            assert status == ConversionStatus.FAILED
            assert row_count == 0

    def test_convert_csv_to_parquet_date_column_types(self):
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            tbl_path = tmpdir / "lineitem.tbl"
            tbl_path.write_text(
                "1|100|50|1|17.0|100.00|0.04|0.02|N|O|1996-03-13|1996-02-12|1996-03-22|DELIVER IN PERSON|TRUCK|comment|\n"
                "1|200|50|2|36.0|200.00|0.09|0.06|N|O|1996-04-12|1996-02-28|1996-04-20|TAKE BACK RETURN|MAIL|comment|\n"
            )

            parquet_path = tmpdir / "lineitem.parquet"
            column_names = [
                "l_orderkey",
                "l_partkey",
                "l_suppkey",
                "l_linenumber",
                "l_quantity",
                "l_extendedprice",
                "l_discount",
                "l_tax",
                "l_returnflag",
                "l_linestatus",
                "l_shipdate",
                "l_commitdate",
                "l_receiptdate",
                "l_shipinstruct",
                "l_shipmode",
                "l_comment",
            ]
            column_types = {
                "l_orderkey": "int64",
                "l_partkey": "int64",
                "l_suppkey": "int64",
                "l_linenumber": "int64",
                "l_quantity": "float64",
                "l_extendedprice": "float64",
                "l_discount": "float64",
                "l_tax": "float64",
                "l_returnflag": "string",
                "l_linestatus": "string",
                "l_shipdate": "date32",
                "l_commitdate": "date32",
                "l_receiptdate": "date32",
                "l_shipinstruct": "string",
                "l_shipmode": "string",
                "l_comment": "string",
            }

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=tbl_path,
                target_path=parquet_path,
                column_names=column_names,
                delimiter="|",
                column_types=column_types,
            )

            assert status == ConversionStatus.SUCCESS
            assert row_count == 2

            table = pq.read_table(parquet_path)
            schema = table.schema

            import pyarrow as pa

            assert schema.field("l_shipdate").type == pa.date32()
            assert schema.field("l_commitdate").type == pa.date32()
            assert schema.field("l_receiptdate").type == pa.date32()

            shipdate_col = table.column("l_shipdate")
            assert shipdate_col[0].as_py() == __import__("datetime").date(1996, 3, 13)

    def test_convert_csv_inferred_time_column_written_as_string(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            csv_path = tmpdir / "order_lines.csv"
            csv_path.write_text("order_id,order_time,quantity\n1,08:30:00,2\n2,14:45:15,1\n3,23:59:59,5\n")
            parquet_path = tmpdir / "order_lines.parquet"

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=csv_path,
                target_path=parquet_path,
                column_names=["order_id", "order_time", "quantity"],
                delimiter=",",
                has_header=True,
            )

            assert status == ConversionStatus.SUCCESS
            assert row_count == 3

            table = pq.read_table(parquet_path)
            assert table.schema.field("order_time").type == pa.string()
            assert table.column("order_time").to_pylist() == ["08:30:00", "14:45:15", "23:59:59"]
            for field in table.schema:
                assert not pa.types.is_time32(field.type), field.name
                assert not pa.types.is_time64(field.type), field.name

    def test_coerce_time_columns_to_string_preserves_other_types(self):
        import pyarrow as pa

        table = pa.table(
            {
                "id": pa.array([1, 2], type=pa.int64()),
                "day": pa.array([18628, 18629], type=pa.date32()),
                "at": pa.array([8 * 3600, 86399], type=pa.time32("s")),
            }
        )
        coerced = FormatConverter._coerce_time_columns_to_string(table)
        assert coerced.schema.field("at").type == pa.string()
        assert coerced.column("at").to_pylist() == ["08:00:00", "23:59:59"]
        assert coerced.schema.field("id").type == pa.int64()
        assert coerced.schema.field("day").type == pa.date32()

        plain = pa.table({"id": pa.array([1], type=pa.int64())})
        assert FormatConverter._coerce_time_columns_to_string(plain).schema == plain.schema

    def test_coerce_time64_micros_and_nulls_to_string(self):
        import datetime

        import pyarrow as pa

        table = pa.table(
            {
                "at": pa.array(
                    [datetime.time(12, 34, 56, 789123), None],
                    type=pa.time64("us"),
                ),
            }
        )
        coerced = FormatConverter._coerce_time_columns_to_string(table)
        assert coerced.schema.field("at").type == pa.string()
        assert coerced.column("at").to_pylist() == ["12:34:56.789123", None]

    def test_sql_type_to_pyarrow_covers_type_families(self):
        assert SchemaMapper.sql_type_to_pyarrow("TEXT") == "string"
        assert SchemaMapper.sql_type_to_pyarrow("text not null") == "string"
        assert SchemaMapper.sql_type_to_pyarrow("STRING") == "string"
        assert SchemaMapper.sql_type_to_pyarrow("VARCHAR(12)") == "string"
        assert SchemaMapper.sql_type_to_pyarrow("CHARACTER VARYING") == "string"
        assert SchemaMapper.sql_type_to_pyarrow("BIGINT") == "int64"
        assert SchemaMapper.sql_type_to_pyarrow("INTEGER") == "int64"
        assert SchemaMapper.sql_type_to_pyarrow("NUMERIC(10,2)") == "float64"
        assert SchemaMapper.sql_type_to_pyarrow("DOUBLE PRECISION") == "float64"
        assert SchemaMapper.sql_type_to_pyarrow("DATE") == "date32"
        assert SchemaMapper.sql_type_to_pyarrow("TIMESTAMP") == "timestamp[us]"
        assert SchemaMapper.sql_type_to_pyarrow("TIME") == "string"
        assert SchemaMapper.sql_type_to_pyarrow("SOMEWEIRDTYPE") is None

    def test_convert_csv_null_marker_controls_empty_string_vs_null(self):
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            csv_path = tmpdir / "t.csv"
            csv_path.write_text("1|\n2|hello\n")
            column_names = ["id", "note"]
            column_types = {"id": "int64", "note": "string"}

            keep = tmpdir / "keep.parquet"
            status, _ = FormatConverter.convert_csv_to_parquet(
                source_path=csv_path,
                target_path=keep,
                column_names=column_names,
                delimiter="|",
                column_types=column_types,
                null_marker=None,
            )
            assert status == ConversionStatus.SUCCESS
            note_keep = pq.read_table(keep).column("note").to_pylist()
            assert note_keep == ["", "hello"]

            nulled = tmpdir / "nulled.parquet"
            status, _ = FormatConverter.convert_csv_to_parquet(
                source_path=csv_path,
                target_path=nulled,
                column_names=column_names,
                delimiter="|",
                column_types=column_types,
                null_marker="",
            )
            assert status == ConversionStatus.SUCCESS
            note_nulled = pq.read_table(nulled).column("note").to_pylist()
            assert note_nulled == [None, "hello"]

    def test_convert_tbl_with_column_types_casts_dates(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            tbl_path = tmpdir / "orders.tbl"
            tbl_path.write_text(
                "1|370|O|172799.49|1996-01-02|5-LOW|Clerk#000000951|0|comment|\n"
                "2|781|O|38426.09|1996-12-01|1-URGENT|Clerk#000000880|0|comment|\n"
            )

            parquet_path = tmpdir / "orders.parquet"
            column_names = [
                "o_orderkey",
                "o_custkey",
                "o_orderstatus",
                "o_totalprice",
                "o_orderdate",
                "o_orderpriority",
                "o_clerk",
                "o_shippriority",
                "o_comment",
            ]
            column_types = {
                "o_orderkey": "int64",
                "o_custkey": "int64",
                "o_orderstatus": "string",
                "o_totalprice": "float64",
                "o_orderdate": "date32",
                "o_orderpriority": "string",
                "o_clerk": "string",
                "o_shippriority": "int64",
                "o_comment": "string",
            }

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=tbl_path,
                target_path=parquet_path,
                column_names=column_names,
                delimiter="|",
                column_types=column_types,
            )

            assert status == ConversionStatus.SUCCESS
            assert row_count == 2

            table = pq.read_table(parquet_path)
            assert table.schema.field("o_orderdate").type == pa.date32()
            assert table.column("o_orderdate")[0].as_py() == __import__("datetime").date(1996, 1, 2)


class TestSourceFileDiscovery:
    def test_get_source_files_with_list_paths(self, tmp_path):
        loader = DataFrameDataLoader(platform="polars")
        shard1 = tmp_path / "customer.tbl.1"
        shard2 = tmp_path / "customer.tbl.2"
        shard1.write_text("1|Alice|100.00\n")
        shard2.write_text("2|Bob|200.00\n")

        class DummyBenchmark:
            tables = {"customer": [shard1, shard2]}

        result = loader._get_source_files(DummyBenchmark(), data_dir=None)
        assert result["customer"] == [shard1, shard2]


class TestSourceFormatDetection:
    def test_detect_source_format_with_list(self, tmp_path):
        loader = DataFrameDataLoader(platform="polars")
        shard1 = tmp_path / "orders.tbl.1"
        shard2 = tmp_path / "orders.tbl.2"
        shard1.write_text("1|A|100.00\n")
        shard2.write_text("2|B|200.00\n")

        source_files = {"orders": [shard1, shard2]}
        assert loader._detect_source_format(source_files) == DataFormat.CSV


class TestCacheManifest:
    def test_to_dict(self):

        manifest = CacheManifest(
            benchmark="tpch",
            scale_factor=1.0,
            format="parquet",
            created_at="2025-01-01T00:00:00",
            source_hash="abc123",
            tables={"customer": {"file": "customer.parquet", "row_count": 150000}},
        )

        data = manifest.to_dict()

        assert data["benchmark"] == "tpch"
        assert data["scale_factor"] == 1.0
        assert data["format"] == "parquet"
        assert data["tables"]["customer"]["row_count"] == 150000

    def test_from_dict(self):

        data = {
            "benchmark": "tpcds",
            "scale_factor": 10.0,
            "format": "parquet",
            "created_at": "2025-01-01T00:00:00",
            "source_hash": "xyz789",
            "tables": {"store": {"file": "store.parquet"}},
        }

        manifest = CacheManifest.from_dict(data)

        assert manifest.benchmark == "tpcds"
        assert manifest.scale_factor == 10.0
        assert "store" in manifest.tables

    def test_roundtrip(self):

        original = CacheManifest(
            benchmark="tpch",
            scale_factor=0.01,
            format="parquet",
            created_at="2025-06-15T12:00:00",
            source_hash="hash123",
            tables={
                "lineitem": {"file": "lineitem.parquet", "row_count": 60012},
                "orders": {"file": "orders.parquet", "row_count": 15000},
            },
        )

        data = original.to_dict()
        restored = CacheManifest.from_dict(data)

        assert restored.benchmark == original.benchmark
        assert restored.scale_factor == original.scale_factor
        assert len(restored.tables) == len(original.tables)


class TestDataCache:
    def test_explicit_string_cache_dir_is_normalized_to_path(self):
        cache = DataCache("tmp/cache")
        assert isinstance(cache.cache_dir, Path)

        path = cache.get_cache_path("tpch", 1.0, DataFormat.PARQUET)
        assert path == Path("tmp/cache") / "tpch_sf1" / "parquet" / DATAFRAME_CACHE_VERSION

    def test_default_cache_uses_runtime_cwd(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        with patch.dict("os.environ", {}, clear=True):
            monkeypatch.chdir(tmp_path)
            cache = DataCache()
            assert cache.cache_dir == tmp_path / "benchmark_runs" / "datagen"

    def test_get_cache_path(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            path = cache.get_cache_path("tpch", 1.0, DataFormat.PARQUET)

            assert "tpch_sf1" in str(path)
            assert "parquet" in str(path)
            assert DATAFRAME_CACHE_VERSION in str(path)

    def test_get_manifest_path(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            path = cache.get_manifest_path("tpch", 0.01, DataFormat.PARQUET)

            assert path.name == "_manifest.json"

    def test_old_cache_layout_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            cache = DataCache(tmpdir)

            old_cache_path = tmpdir / "tpch" / "sf_1.0" / "parquet"
            old_cache_path.mkdir(parents=True)
            (old_cache_path / "customer.parquet").touch()
            with open(old_cache_path / "_manifest.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "benchmark": "tpch",
                        "scale_factor": 1.0,
                        "format": "parquet",
                        "created_at": "2025-01-01T00:00:00",
                        "source_hash": "abc123",
                        "tables": {"customer": {"file": "customer.parquet"}},
                    },
                    f,
                )

            assert cache.has_cached_data("tpch", 1.0, DataFormat.PARQUET) is False

    def test_has_cached_data_empty(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            assert cache.has_cached_data("tpch", 1.0, DataFormat.PARQUET) is False

    def test_has_cached_data_valid(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            cache_path = cache.get_cache_path("tpch", 1.0, DataFormat.PARQUET)
            cache_path.mkdir(parents=True)

            (cache_path / "customer.parquet").touch()
            (cache_path / "orders.parquet").touch()

            manifest = {
                "benchmark": "tpch",
                "scale_factor": 1.0,
                "format": "parquet",
                "created_at": "2025-01-01T00:00:00",
                "source_hash": "abc123",
                "tables": {
                    "customer": {"file": "customer.parquet"},
                    "orders": {"file": "orders.parquet"},
                },
            }

            with open(cache_path / "_manifest.json", "w", encoding="utf-8") as f:
                json.dump(manifest, f)

            assert cache.has_cached_data("tpch", 1.0, DataFormat.PARQUET) is True

    def test_has_cached_data_missing_file(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            cache_path = cache.get_cache_path("tpch", 1.0, DataFormat.PARQUET)
            cache_path.mkdir(parents=True)

            (cache_path / "customer.parquet").touch()

            manifest = {
                "benchmark": "tpch",
                "scale_factor": 1.0,
                "format": "parquet",
                "created_at": "2025-01-01T00:00:00",
                "source_hash": "abc123",
                "tables": {
                    "customer": {"file": "customer.parquet"},
                    "missing": {"file": "missing.parquet"},
                },
            }

            with open(cache_path / "_manifest.json", "w", encoding="utf-8") as f:
                json.dump(manifest, f)

            assert cache.has_cached_data("tpch", 1.0, DataFormat.PARQUET) is False

    def test_has_cached_data_hash_mismatch(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            cache_path = cache.get_cache_path("tpch", 1.0, DataFormat.PARQUET)
            cache_path.mkdir(parents=True)

            (cache_path / "test.parquet").touch()

            manifest = {
                "benchmark": "tpch",
                "scale_factor": 1.0,
                "format": "parquet",
                "created_at": "2025-01-01T00:00:00",
                "source_hash": "old_hash",
                "tables": {"test": {"file": "test.parquet"}},
            }

            with open(cache_path / "_manifest.json", "w", encoding="utf-8") as f:
                json.dump(manifest, f)

            assert cache.has_cached_data("tpch", 1.0, DataFormat.PARQUET, source_hash="new_hash") is False

    def test_get_cached_files(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            cache_path = cache.get_cache_path("tpch", 1.0, DataFormat.PARQUET)
            cache_path.mkdir(parents=True)

            manifest = {
                "benchmark": "tpch",
                "scale_factor": 1.0,
                "format": "parquet",
                "created_at": "2025-01-01T00:00:00",
                "source_hash": "abc123",
                "tables": {
                    "customer": {"file": "customer.parquet"},
                    "orders": {"file": "orders.parquet"},
                },
            }

            with open(cache_path / "_manifest.json", "w", encoding="utf-8") as f:
                json.dump(manifest, f)

            files = cache.get_cached_files("tpch", 1.0, DataFormat.PARQUET)

            assert files is not None
            assert "customer" in files
            assert "orders" in files
            assert files["customer"].name == "customer.parquet"

    def test_save_manifest(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            cache.save_manifest(
                benchmark="tpch",
                scale_factor=1.0,
                format=DataFormat.PARQUET,
                source_hash="test_hash",
                tables={"lineitem": {"file": "lineitem.parquet", "row_count": 60000}},
            )

            manifest_path = cache.get_manifest_path("tpch", 1.0, DataFormat.PARQUET)
            assert manifest_path.exists()

            with open(manifest_path, encoding="utf-8") as f:
                data = json.load(f)

            assert data["benchmark"] == "tpch"
            assert data["source_hash"] == "test_hash"
            assert data["tables"]["lineitem"]["row_count"] == 60000

    def test_clear_cache_specific(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            cache_path = cache.get_cache_path("tpch", 1.0, DataFormat.PARQUET)
            cache_path.mkdir(parents=True)
            (cache_path / "test.parquet").touch()
            (cache_path / "_manifest.json").touch()

            removed = cache.clear_cache("tpch", 1.0, DataFormat.PARQUET)

            assert removed >= 2
            assert not cache_path.exists()


class TestDataFrameDataLoader:
    def test_init_default(self):

        loader = DataFrameDataLoader()

        assert loader.platform == "polars"
        assert loader.prefer_parquet is True
        assert loader.force_regenerate is False

    def test_init_with_platform(self):

        loader = DataFrameDataLoader(platform="pandas-df")

        assert loader.platform == "pandas"

    def test_get_optimal_format_parquet(self):

        loader = DataFrameDataLoader(platform="polars")

        format = loader.get_optimal_format(scale_factor=1.0)

        assert format == DataFormat.PARQUET

    def test_get_optimal_format_csv(self):

        loader = DataFrameDataLoader(platform="polars", prefer_parquet=False)

        format = loader.get_optimal_format(scale_factor=1.0)

        assert format == DataFormat.CSV

    def test_discover_files(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            (tmpdir / "customer.tbl").touch()
            (tmpdir / "orders.tbl").touch()
            (tmpdir / "lineitem.parquet").touch()

            loader = DataFrameDataLoader()
            files = loader._discover_files(tmpdir)

            assert "customer" in files
            assert "orders" in files
            assert "lineitem" in files

    def test_detect_source_format_tbl(self):

        loader = DataFrameDataLoader()

        files = {"customer": Path("customer.tbl")}
        format = loader._detect_source_format(files)

        assert format == DataFormat.CSV

    def test_detect_source_format_parquet(self):

        loader = DataFrameDataLoader()

        files = {"customer": Path("customer.parquet")}
        format = loader._detect_source_format(files)

        assert format == DataFormat.PARQUET

    def test_get_schema_info_accepts_table_objects(self):
        loader = DataFrameDataLoader()

        column = MagicMock()
        column.name = "load_end_dts"
        table = MagicMock()
        table.columns = [column]
        benchmark = MagicMock()
        benchmark.get_schema.return_value = {"sat_lineitem": table}

        schema_info = loader._get_schema_info(benchmark)

        assert schema_info["sat_lineitem"] == ["load_end_dts"]

    def test_get_schema_info_accepts_column_mapping(self):
        loader = DataFrameDataLoader()
        benchmark = MagicMock()
        benchmark.get_schema.return_value = {
            "trips": {
                "columns": {
                    "pickup_datetime": {"type": "TIMESTAMP"},
                    "total_amount": {"type": "DOUBLE"},
                }
            }
        }

        schema_info = loader._get_schema_info(benchmark)
        pyarrow_types = loader._get_pyarrow_types(benchmark)

        assert schema_info["trips"] == ["pickup_datetime", "total_amount"]
        assert pyarrow_types["trips"]["pickup_datetime"] == "timestamp[us]"

    def test_get_source_files_from_benchmark(self):

        loader = DataFrameDataLoader()

        benchmark = MagicMock()
        benchmark.tables = {
            "customer": "/data/customer.tbl",
            "orders": "/data/orders.tbl",
        }

        files = loader._get_source_files(benchmark, None)

        assert "customer" in files
        assert "orders" in files
        assert files["customer"] == [Path("/data/customer.tbl")]

    def test_get_source_files_rejects_directory_path(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            (tmpdir / "customer").mkdir()

            loader = DataFrameDataLoader()
            benchmark = MagicMock()
            benchmark.tables = {"customer": str(tmpdir / "customer")}

            with pytest.raises(ValueError, match="customer.*expected file.*found directory"):
                loader._get_source_files(benchmark, None)

    def test_get_source_files_rejects_directory_in_list(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            good_file = tmpdir / "orders.tbl.1"
            good_file.write_text("1|data\n")
            bad_dir = tmpdir / "orders"
            bad_dir.mkdir()

            loader = DataFrameDataLoader()
            benchmark = MagicMock()
            benchmark.tables = {"orders": [str(good_file), str(bad_dir)]}

            with pytest.raises(ValueError, match="orders.*expected file.*found directory"):
                loader._get_source_files(benchmark, None)

    def test_get_source_files_from_data_dir(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            (tmpdir / "region.tbl").touch()
            (tmpdir / "nation.tbl").touch()

            loader = DataFrameDataLoader()
            benchmark = MagicMock()
            benchmark.tables = None

            files = loader._get_source_files(benchmark, tmpdir)

            assert "region" in files
            assert "nation" in files
            assert files["region"] == [tmpdir / "region.tbl"]
            assert files["nation"] == [tmpdir / "nation.tbl"]

    def test_prepare_benchmark_data_cached(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            cache_dir = tmpdir / "cache"

            loader = DataFrameDataLoader(cache_dir=cache_dir)

            cache_path = loader.cache.get_cache_path("tpch", 1.0, DataFormat.PARQUET)
            cache_path.mkdir(parents=True)

            (cache_path / "customer.parquet").touch()

            manifest = {
                "benchmark": "tpch",
                "scale_factor": 1.0,
                "format": "parquet",
                "created_at": "2025-01-01T00:00:00",
                "source_hash": "any_hash",
                "tables": {"customer": {"file": "customer.parquet"}},
            }
            with open(cache_path / "_manifest.json", "w", encoding="utf-8") as f:
                json.dump(manifest, f)

            benchmark = MagicMock()
            benchmark.name = "tpch"
            benchmark.tables = {"customer": Path(tmpdir / "customer.tbl")}

            (tmpdir / "customer.tbl").touch()

            with (
                patch.object(loader.cache, "has_cached_data", return_value=True),
                patch.object(
                    loader.cache,
                    "get_cached_files",
                    return_value={"customer": cache_path / "customer.parquet"},
                ),
            ):
                paths = loader.prepare_benchmark_data(benchmark, scale_factor=1.0)

            assert "customer" in paths

    def test_prepare_benchmark_data_preserves_shards(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            shard1 = tmpdir / "customer.tbl.1"
            shard2 = tmpdir / "customer.tbl.2"
            shard1.write_text("1|Alice|100.00\n")
            shard2.write_text("2|Bob|200.00\n")

            loader = DataFrameDataLoader(prefer_parquet=False)
            benchmark = MagicMock()
            benchmark.name = "tpch"
            benchmark.tables = {"customer": [shard1, shard2]}

            paths = loader.prepare_benchmark_data(benchmark, scale_factor=1.0)

            assert paths["customer"] == [shard1, shard2]


class TestLoadedTable:
    def test_creation(self):

        table = LoadedTable(
            table_name="customer",
            file_path=Path("/data/customer.parquet"),
            format=DataFormat.PARQUET,
            row_count=150000,
            size_bytes=1024000,
        )

        assert table.table_name == "customer"
        assert table.row_count == 150000
        assert table.format == DataFormat.PARQUET


class TestDataLoadResult:
    def test_success_with_tables(self):

        result = DataLoadResult(
            tables={"customer": LoadedTable("customer", Path("c.parquet"), DataFormat.PARQUET)},
        )

        assert result.success is True

    def test_failure_with_errors(self):

        result = DataLoadResult(
            tables={"customer": LoadedTable("customer", Path("c.parquet"), DataFormat.PARQUET)},
            errors=["Something went wrong"],
        )

        assert result.success is False

    def test_failure_no_tables(self):

        result = DataLoadResult()

        assert result.success is False


class TestSourceHash:
    def test_compute_source_hash(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            (tmpdir / "a.tbl").write_text("data")
            (tmpdir / "b.tbl").write_text("more data")

            tables = {
                "a": tmpdir / "a.tbl",
                "b": tmpdir / "b.tbl",
            }

            hash1 = _compute_source_hash(tmpdir, tables)

            hash2 = _compute_source_hash(tmpdir, tables)
            assert hash1 == hash2

            (tmpdir / "a.tbl").write_text("different data")
            hash3 = _compute_source_hash(tmpdir, tables)
            assert hash3 != hash1

    def test_compute_source_hash_stable_order(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            (tmpdir / "x.tbl").write_text("x")
            (tmpdir / "y.tbl").write_text("y")
            (tmpdir / "z.tbl").write_text("z")

            tables1 = {"x": tmpdir / "x.tbl", "y": tmpdir / "y.tbl", "z": tmpdir / "z.tbl"}
            tables2 = {"z": tmpdir / "z.tbl", "x": tmpdir / "x.tbl", "y": tmpdir / "y.tbl"}

            assert _compute_source_hash(tmpdir, tables1) == _compute_source_hash(tmpdir, tables2)


class TestGetTPCHColumnNames:
    def test_returns_all_tables(self):

        columns = get_tpch_column_names()

        assert "lineitem" in columns
        assert "orders" in columns
        assert "customer" in columns
        assert "supplier" in columns
        assert "part" in columns
        assert "partsupp" in columns
        assert "nation" in columns
        assert "region" in columns

    def test_lineitem_columns(self):

        columns = get_tpch_column_names()

        assert "l_orderkey" in columns["lineitem"]
        assert "l_quantity" in columns["lineitem"]
        assert "l_shipdate" in columns["lineitem"]

    def test_orders_columns(self):

        columns = get_tpch_column_names()

        assert "o_orderkey" in columns["orders"]
        assert "o_orderdate" in columns["orders"]
        assert "o_totalprice" in columns["orders"]


class TestEnvironmentOverride:
    def test_cache_dir_env_override(self):

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict("os.environ", {"BENCHBOX_CACHE_DIR": tmpdir}):
            cache = DataCache()

            assert str(cache.cache_dir) == tmpdir


class TestFormatConverterWithWriteConfig:
    def test_convert_with_sort_by(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            csv_path = tmpdir / "test.csv"
            csv_path.write_text("id,name,value\n3,charlie,300\n1,alice,100\n2,bob,200\n")

            parquet_path = tmpdir / "test.parquet"

            write_config = DataFrameWriteConfiguration(sort_by=[SortColumn(name="id", order="asc")])

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=csv_path,
                target_path=parquet_path,
                delimiter=",",
                write_config=write_config,
            )

            assert status == ConversionStatus.SUCCESS
            assert row_count == 3
            assert parquet_path.exists()

            import pyarrow.parquet as pq

            table = pq.read_table(parquet_path)
            ids = table.column("id").to_pylist()
            assert ids == [1, 2, 3], f"Expected [1, 2, 3], got {ids}"

    def test_convert_with_sort_by_descending(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            csv_path = tmpdir / "test.csv"
            csv_path.write_text("id,name\n1,a\n3,c\n2,b\n")

            parquet_path = tmpdir / "test.parquet"

            write_config = DataFrameWriteConfiguration(sort_by=[SortColumn(name="id", order="desc")])

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=csv_path,
                target_path=parquet_path,
                delimiter=",",
                write_config=write_config,
            )

            assert status == ConversionStatus.SUCCESS

            import pyarrow.parquet as pq

            table = pq.read_table(parquet_path)
            ids = table.column("id").to_pylist()
            assert ids == [3, 2, 1], f"Expected [3, 2, 1], got {ids}"

    def test_convert_with_row_group_size(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            csv_path = tmpdir / "test.csv"
            csv_path.write_text("id,value\n1,a\n2,b\n3,c\n4,d\n5,e\n")

            parquet_path = tmpdir / "test.parquet"

            write_config = DataFrameWriteConfiguration(row_group_size=2)

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=csv_path,
                target_path=parquet_path,
                delimiter=",",
                write_config=write_config,
            )

            assert status == ConversionStatus.SUCCESS
            assert row_count == 5

            import pyarrow.parquet as pq

            meta = pq.read_metadata(parquet_path)
            assert meta.num_row_groups >= 2

    def test_convert_with_compression(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            csv_path = tmpdir / "test.csv"
            csv_path.write_text("id,value\n1,hello\n2,world\n")

            parquet_path = tmpdir / "test.parquet"

            write_config = DataFrameWriteConfiguration(compression="gzip")

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=csv_path,
                target_path=parquet_path,
                delimiter=",",
                write_config=write_config,
            )

            assert status == ConversionStatus.SUCCESS
            assert parquet_path.exists()

    def test_convert_with_compression_level(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            csv_path = tmpdir / "test.csv"
            csv_path.write_text("id,value\n1,a\n2,b\n")

            parquet_path = tmpdir / "test.parquet"

            write_config = DataFrameWriteConfiguration(
                compression="zstd",
                compression_level=9,
            )

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=csv_path,
                target_path=parquet_path,
                delimiter=",",
                write_config=write_config,
            )

            assert status == ConversionStatus.SUCCESS
            assert parquet_path.exists()

    def test_convert_with_data_page_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            csv_path = tmpdir / "test.csv"
            csv_path.write_text("id,value\n1,a\n2,b\n")

            parquet_path = tmpdir / "test.parquet"

            write_config = DataFrameWriteConfiguration(
                data_page_version="2.0",
            )

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=csv_path,
                target_path=parquet_path,
                delimiter=",",
                write_config=write_config,
            )

            assert status == ConversionStatus.SUCCESS
            assert parquet_path.exists()
            assert row_count == 2

    def test_build_write_kwargs_data_page_version(self):

        import pyarrow as pa

        table = pa.table({"id": [1, 2], "value": ["a", "b"]})

        config_none = DataFrameWriteConfiguration()
        kwargs = FormatConverter._build_write_kwargs("zstd", config_none, table)
        assert "data_page_version" not in kwargs

        config_v1 = DataFrameWriteConfiguration(data_page_version="1.0")
        kwargs = FormatConverter._build_write_kwargs("zstd", config_v1, table)
        assert kwargs["data_page_version"] == "1.0"

        config_v2 = DataFrameWriteConfiguration(data_page_version="2.0")
        kwargs = FormatConverter._build_write_kwargs("zstd", config_v2, table)
        assert kwargs["data_page_version"] == "2.0"

    def test_convert_skips_invalid_sort_column(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            csv_path = tmpdir / "test.csv"
            csv_path.write_text("id,name\n1,a\n2,b\n")

            parquet_path = tmpdir / "test.parquet"

            write_config = DataFrameWriteConfiguration(
                sort_by=[
                    SortColumn(name="nonexistent", order="asc"),
                    SortColumn(name="id", order="asc"),
                ]
            )

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=csv_path,
                target_path=parquet_path,
                delimiter=",",
                write_config=write_config,
            )

            assert status == ConversionStatus.SUCCESS
            assert row_count == 2

    def test_convert_with_default_write_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            csv_path = tmpdir / "test.csv"
            csv_path.write_text("x,y\n1,2\n")

            parquet_path = tmpdir / "test.parquet"

            write_config = DataFrameWriteConfiguration()

            status, _ = FormatConverter.convert_csv_to_parquet(
                source_path=csv_path,
                target_path=parquet_path,
                delimiter=",",
                write_config=write_config,
            )

            assert status == ConversionStatus.SUCCESS


class TestDataFrameDataLoaderWithWriteConfig:
    def test_init_with_write_config(self):

        write_config = DataFrameWriteConfiguration(sort_by=[SortColumn(name="id", order="asc")])
        loader = DataFrameDataLoader(write_config=write_config)

        assert loader.write_config is not None
        assert len(loader.write_config.sort_by) == 1

    def test_get_table_write_config_filters_columns(self):

        loader = DataFrameDataLoader()

        base_config = DataFrameWriteConfiguration(
            sort_by=[
                SortColumn(name="l_shipdate", order="asc"),
                SortColumn(name="l_orderkey", order="asc"),
                SortColumn(name="c_custkey", order="asc"),
            ],
            row_group_size=1000000,
        )

        lineitem_cols = ["l_orderkey", "l_partkey", "l_shipdate", "l_quantity"]

        filtered = loader._get_table_write_config(base_config, "lineitem", lineitem_cols)

        assert filtered is not None
        assert len(filtered.sort_by) == 2
        sort_names = [s.name for s in filtered.sort_by]
        assert "l_shipdate" in sort_names
        assert "l_orderkey" in sort_names
        assert "c_custkey" not in sort_names

        assert filtered.row_group_size == 1000000

    def test_get_table_write_config_returns_none_for_none(self):

        loader = DataFrameDataLoader()

        result = loader._get_table_write_config(None, "table", ["col1"])
        assert result is None

    def test_get_table_write_config_returns_default_unchanged(self):

        loader = DataFrameDataLoader()

        default_config = DataFrameWriteConfiguration()
        result = loader._get_table_write_config(default_config, "table", ["col1"])

        assert result is default_config

    def test_get_table_write_config_no_columns_returns_original(self):
        loader = DataFrameDataLoader()

        config = DataFrameWriteConfiguration(sort_by=[SortColumn(name="id", order="asc")])

        result = loader._get_table_write_config(config, "table", None)
        assert result is config

    def test_applied_write_layout_set_on_cache_hit_with_nondefault_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            loader = DataFrameDataLoader(cache_dir=tmpdir / "cache")
            benchmark = MagicMock()
            benchmark.name = "tpch"
            benchmark.tables = {"customer": Path(tmpdir / "customer.tbl")}
            (tmpdir / "customer.tbl").touch()

            write_config = DataFrameWriteConfiguration(compression="snappy", row_group_size=250_000)
            with (
                patch.object(loader.cache, "has_cached_data", return_value=True),
                patch.object(
                    loader.cache,
                    "get_cached_files",
                    return_value={"customer": tmpdir / "customer.parquet"},
                ),
            ):
                loader.prepare_benchmark_data(benchmark, scale_factor=1.0, write_config=write_config)

            assert loader.applied_write_layout is write_config

    def test_applied_write_layout_none_when_source_returned_verbatim(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            source = tmpdir / "customer.tbl"
            source.write_text("1|Alice|100.00\n")

            loader = DataFrameDataLoader(prefer_parquet=False)
            benchmark = MagicMock()
            benchmark.name = "tpch"
            benchmark.tables = {"customer": source}

            write_config = DataFrameWriteConfiguration(compression="snappy")
            paths = loader.prepare_benchmark_data(benchmark, scale_factor=1.0, write_config=write_config)

            returned = paths["customer"]
            returned = returned if isinstance(returned, list) else [returned]
            assert source in returned
            assert loader.applied_write_layout is None

    def test_applied_write_layout_none_when_target_format_is_not_parquet(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            source = tmpdir / "customer.csv"
            source.write_text("1|Alice|100.00\n")

            loader = DataFrameDataLoader(prefer_parquet=False)
            benchmark = MagicMock()
            benchmark.name = "tpch"
            benchmark.tables = {"customer": source}

            write_config = DataFrameWriteConfiguration(sort_by=[SortColumn(name="c_custkey", order="asc")])
            loader.prepare_benchmark_data(benchmark, scale_factor=1.0, write_config=write_config)

            assert loader.applied_write_layout is None

    def test_applied_write_layout_none_for_default_config_on_cache_hit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            loader = DataFrameDataLoader(cache_dir=tmpdir / "cache")
            benchmark = MagicMock()
            benchmark.name = "tpch"
            benchmark.tables = {"customer": Path(tmpdir / "customer.tbl")}
            (tmpdir / "customer.tbl").touch()

            with (
                patch.object(loader.cache, "has_cached_data", return_value=True),
                patch.object(
                    loader.cache,
                    "get_cached_files",
                    return_value={"customer": tmpdir / "customer.parquet"},
                ),
            ):
                loader.prepare_benchmark_data(benchmark, scale_factor=1.0, write_config=DataFrameWriteConfiguration())

            assert loader.applied_write_layout is None


