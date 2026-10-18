# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import importlib.util
import sys
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest

from benchbox.utils.format_converters.base import ConversionOptions
from benchbox.utils.format_converters.parquet_converter import ParquetConverter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.fixture
def tpch_customer_schema():
    return {
        "columns": [
            {"name": "c_custkey", "type": "INTEGER"},
            {"name": "c_name", "type": "VARCHAR(25)"},
            {"name": "c_address", "type": "VARCHAR(40)"},
            {"name": "c_nationkey", "type": "INTEGER"},
            {"name": "c_phone", "type": "CHAR(15)"},
            {"name": "c_acctbal", "type": "DECIMAL(15,2)"},
            {"name": "c_mktsegment", "type": "CHAR(10)"},
            {"name": "c_comment", "type": "VARCHAR(117)"},
        ]
    }


@pytest.fixture
def tpch_customer_tbl(tmp_path: Path) -> Path:
    tbl_file = tmp_path / "customer.tbl"
    tbl_file.write_text(
        "1|Customer#000000001|IVhzIApeRb|15|25-989-741-2988|711.56|BUILDING|regular packages|\n"
        "2|Customer#000000002|XSTf4,NCwDVaW|13|23-768-687-3665|121.65|AUTOMOBILE|theodolites|\n"
        "3|Customer#000000003|MG9kdTD2W|1|11-719-748-3364|7498.12|AUTOMOBILE|deposits serve|\n"
        "4|Customer#000000004|XxVSJsLAGt|4|14-128-190-5944|2866.83|MACHINERY|requests haggle|\n"
        "5|Customer#000000005|KvpyuHCplr|3|13-750-942-6364|794.47|HOUSEHOLD|pending foxes|\n"
    )
    return tbl_file


@pytest.fixture
def tpch_lineitem_schema():
    return {
        "columns": [
            {"name": "l_orderkey", "type": "INTEGER"},
            {"name": "l_partkey", "type": "INTEGER"},
            {"name": "l_suppkey", "type": "INTEGER"},
            {"name": "l_linenumber", "type": "INTEGER"},
            {"name": "l_quantity", "type": "DECIMAL(15,2)"},
            {"name": "l_extendedprice", "type": "DECIMAL(15,2)"},
            {"name": "l_discount", "type": "DECIMAL(15,2)"},
            {"name": "l_tax", "type": "DECIMAL(15,2)"},
            {"name": "l_returnflag", "type": "CHAR(1)"},
            {"name": "l_linestatus", "type": "CHAR(1)"},
            {"name": "l_shipdate", "type": "DATE"},
            {"name": "l_commitdate", "type": "DATE"},
            {"name": "l_receiptdate", "type": "DATE"},
            {"name": "l_shipinstruct", "type": "CHAR(25)"},
            {"name": "l_shipmode", "type": "CHAR(10)"},
            {"name": "l_comment", "type": "VARCHAR(44)"},
        ]
    }


@pytest.fixture
def tpch_lineitem_tbl(tmp_path: Path) -> Path:
    tbl_file = tmp_path / "lineitem.tbl"
    tbl_file.write_text(
        "1|155190|7706|1|17.00|21168.23|0.04|0.02|N|O|1996-03-13|1996-02-12|1996-03-22|DELIVER IN PERSON|TRUCK|egular courts|\n"
        "1|67310|7311|2|36.00|45983.16|0.09|0.06|N|O|1996-04-12|1996-02-28|1996-04-20|TAKE BACK RETURN|MAIL|ly final dep|\n"
        "2|106170|1191|1|38.00|44694.46|0.00|0.05|N|O|1997-01-28|1997-01-14|1997-02-02|TAKE BACK RETURN|RAIL|ven requests|\n"
        "3|4297|1798|1|45.00|54058.05|0.06|0.00|R|F|1994-02-02|1994-01-04|1994-02-23|NONE|AIR|ongside of |\n"
        "3|19036|6540|2|49.00|46796.47|0.10|0.00|R|F|1993-11-09|1993-12-20|1993-11-24|TAKE BACK RETURN|RAIL|unusual accounts|\n"
    )
    return tbl_file


@pytest.mark.integration
class TestParquetFormatSmoke:
    def test_parquet_conversion_and_duckdb_query(
        self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict
    ):

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )

        assert result.success
        assert result.row_count == 5
        parquet_file = result.output_files[0]
        assert parquet_file.exists()

        conn = duckdb.connect(":memory:")
        df = conn.execute(f"SELECT * FROM read_parquet('{parquet_file}')").fetchdf()

        assert len(df) == 5

        count_result = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{parquet_file}')").fetchone()
        assert count_result[0] == 5

        sum_result = conn.execute(f"SELECT SUM(c_acctbal) FROM read_parquet('{parquet_file}')").fetchone()
        expected_sum = 711.56 + 121.65 + 7498.12 + 2866.83 + 794.47
        assert abs(float(sum_result[0]) - expected_sum) < 0.01

        filter_result = conn.execute(
            f"SELECT c_custkey, c_name FROM read_parquet('{parquet_file}') WHERE c_acctbal > 1000"
        ).fetchall()
        assert len(filter_result) == 2
        custkeys = {row[0] for row in filter_result}
        assert custkeys == {3, 4}

        conn.close()

    def test_parquet_snappy_compression(self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict):

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer_snappy",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path, compression="snappy"),
        )

        assert result.success
        assert result.metadata["compression"] == "snappy"

        table = pq.read_table(result.output_files[0])
        assert table.num_rows == 5

        conn = duckdb.connect(":memory:")
        count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{result.output_files[0]}')").fetchone()[0]
        assert count == 5
        conn.close()

    def test_parquet_gzip_compression(self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict):

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer_gzip",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path, compression="gzip"),
        )

        assert result.success
        assert result.metadata["compression"] == "gzip"

        conn = duckdb.connect(":memory:")
        df = conn.execute(f"SELECT * FROM read_parquet('{result.output_files[0]}')").fetchdf()
        assert len(df) == 5
        conn.close()

    def test_parquet_zstd_compression(self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict):

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer_zstd",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path, compression="zstd"),
        )

        assert result.success
        assert result.metadata["compression"] == "zstd"

        conn = duckdb.connect(":memory:")
        df = conn.execute(f"SELECT * FROM read_parquet('{result.output_files[0]}')").fetchdf()
        assert len(df) == 5
        conn.close()

    def test_parquet_hive_partitioned_duckdb_query(
        self, tmp_path: Path, tpch_lineitem_tbl: Path, tpch_lineitem_schema: dict
    ):

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tpch_lineitem_tbl],
            table_name="lineitem",
            schema=tpch_lineitem_schema,
            options=ConversionOptions(
                output_dir=tmp_path,
                partition_cols=["l_returnflag"],
            ),
        )

        assert result.success
        assert result.metadata["partitioned"]
        assert result.metadata["partition_cols"] == ["l_returnflag"]

        partitioned_dir = result.output_files[0]
        assert partitioned_dir.is_dir()
        assert (partitioned_dir / "l_returnflag=N").exists()
        assert (partitioned_dir / "l_returnflag=R").exists()

        conn = duckdb.connect(":memory:")

        all_rows = conn.execute(
            f"SELECT * FROM read_parquet('{partitioned_dir}/**/*.parquet', hive_partitioning=true)"
        ).fetchall()
        assert len(all_rows) == 5

        n_rows = conn.execute(
            f"""
            SELECT l_orderkey, l_returnflag
            FROM read_parquet('{partitioned_dir}/**/*.parquet', hive_partitioning=true)
            WHERE l_returnflag = 'N'
            """
        ).fetchall()
        assert len(n_rows) == 3
        assert all(row[1] == "N" for row in n_rows)

        agg_result = conn.execute(
            f"""
            SELECT l_returnflag, COUNT(*) as cnt
            FROM read_parquet('{partitioned_dir}/**/*.parquet', hive_partitioning=true)
            GROUP BY l_returnflag
            ORDER BY l_returnflag
            """
        ).fetchall()
        assert len(agg_result) == 2
        flag_counts = {row[0]: row[1] for row in agg_result}
        assert flag_counts["N"] == 3
        assert flag_counts["R"] == 2

        conn.close()


DELTALAKE_AVAILABLE = importlib.util.find_spec("deltalake") is not None


@pytest.mark.integration
@pytest.mark.skipif(not DELTALAKE_AVAILABLE, reason="deltalake package not installed")
class TestDeltaLakeFormatSmoke:
    def test_delta_conversion_and_structure(self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict):

        from benchbox.utils.format_converters.delta_converter import DeltaConverter

        converter = DeltaConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )

        assert result.success
        assert result.row_count == 5

        delta_dir = tmp_path / "customer"
        assert delta_dir.exists()
        assert (delta_dir / "_delta_log").exists()

        log_files = list((delta_dir / "_delta_log").glob("*.json"))
        assert len(log_files) >= 1

    def test_delta_duckdb_query_via_delta_scan(
        self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict
    ):

        from benchbox.utils.format_converters.delta_converter import DeltaConverter

        converter = DeltaConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )

        assert result.success
        delta_dir = tmp_path / "customer"

        conn = duckdb.connect(":memory:")

        conn.execute("INSTALL delta")
        conn.execute("LOAD delta")

        count = conn.execute(f"SELECT COUNT(*) FROM delta_scan('{delta_dir}')").fetchone()[0]
        assert count == 5

        sum_result = conn.execute(f"SELECT SUM(c_acctbal) FROM delta_scan('{delta_dir}')").fetchone()[0]
        expected_sum = 711.56 + 121.65 + 7498.12 + 2866.83 + 794.47
        assert abs(float(sum_result) - expected_sum) < 0.01

        conn.close()

    def test_delta_transaction_log_metadata(self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict):

        import json

        from benchbox.utils.format_converters.delta_converter import DeltaConverter

        converter = DeltaConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )

        assert result.success

        delta_log_dir = tmp_path / "customer" / "_delta_log"
        log_file = delta_log_dir / "00000000000000000000.json"
        assert log_file.exists()

        actions = []
        with open(log_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    actions.append(json.loads(line))

        action_types = {list(action.keys())[0] for action in actions}
        assert "protocol" in action_types
        assert "metaData" in action_types
        assert "add" in action_types

    def test_delta_manifest_validation_reuse(self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict):
        import json

        from benchbox.utils.format_converters.delta_converter import DeltaConverter

        converter = DeltaConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )

        assert result.success

        delta_dir = tmp_path / "customer"
        from benchbox.utils.datagen_version import current_datagen_stamp

        manifest = {
            "version": 2,
            "benchmark": "tpch",
            **current_datagen_stamp("tpch"),
            "scale_factor": 0.01,
            "format_preference": ["delta"],
            "tables": {
                "customer": {
                    "formats": {
                        "delta": [
                            {
                                "path": "customer",
                                "size_bytes": result.output_size_bytes,
                                "row_count": result.row_count,
                                "is_directory": True,
                            }
                        ]
                    }
                }
            },
        }

        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        from benchbox.utils.datagen_manifest import compute_entry_size

        actual_size = compute_entry_size(delta_dir)
        assert actual_size == result.output_size_bytes, (
            f"compute_entry_size ({actual_size}) should match converter output_size_bytes ({result.output_size_bytes})"
        )

        from unittest.mock import Mock

        from benchbox.core.runner.runner import _ensure_data_generated
        from benchbox.core.schemas import BenchmarkConfig

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            compress_data=True,
            compression_type="zstd",
            compression_level=None,
            options={},
        )

        class DummyBenchmark:
            def __init__(self) -> None:
                self.output_dir = tmp_path
                self.tables = None
                self.generate_data = Mock()

        dummy = DummyBenchmark()
        regenerated = _ensure_data_generated(dummy, config)

        assert regenerated == (False, True), "Data should be reused, not regenerated"
        dummy.generate_data.assert_not_called()


PYICEBERG_AVAILABLE = importlib.util.find_spec("pyiceberg") is not None


@pytest.mark.integration
@pytest.mark.skipif(not PYICEBERG_AVAILABLE, reason="pyiceberg package not installed")
@pytest.mark.skipif(sys.platform == "win32", reason="pyiceberg generates Unix-style paths incompatible with Windows")
class TestIcebergFormatSmoke:
    def test_iceberg_conversion_and_structure(
        self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict
    ):

        from benchbox.utils.format_converters.iceberg_converter import IcebergConverter

        converter = IcebergConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )

        assert result.success
        assert result.row_count == 5

        iceberg_dir = tmp_path / "customer"
        assert iceberg_dir.exists()
        metadata_dir = iceberg_dir / "metadata"
        assert metadata_dir.exists()

        metadata_files = list(metadata_dir.glob("*.json")) + list(metadata_dir.glob("*.avro"))
        assert len(metadata_files) >= 1

    def test_iceberg_metadata_structure(self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict):

        import json

        from benchbox.utils.format_converters.iceberg_converter import IcebergConverter

        converter = IcebergConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )

        assert result.success

        metadata_dir = tmp_path / "customer" / "metadata"
        metadata_files = sorted(metadata_dir.glob("v*.metadata.json"))

        if metadata_files:
            with open(metadata_files[-1], encoding="utf-8") as f:
                metadata = json.load(f)

            assert "format-version" in metadata
            assert "table-uuid" in metadata
            assert "schema" in metadata or "schemas" in metadata


@pytest.fixture
def tpch_data_dir_with_manifest(tmp_path_factory) -> Path:
    import json

    data_dir = tmp_path_factory.mktemp("cli_test_data")

    tbl_file = data_dir / "customer.tbl"
    tbl_file.write_text(
        "1|Customer#000000001|IVhzIApeRb|15|25-989-741-2988|711.56|BUILDING|regular packages|\n"
        "2|Customer#000000002|XSTf4,NCwDVaW|13|23-768-687-3665|121.65|AUTOMOBILE|theodolites|\n"
        "3|Customer#000000003|MG9kdTD2W|1|11-719-748-3364|7498.12|AUTOMOBILE|deposits serve|\n"
        "4|Customer#000000004|XxVSJsLAGt|4|14-128-190-5944|2866.83|MACHINERY|requests haggle|\n"
        "5|Customer#000000005|KvpyuHCplr|3|13-750-942-6364|794.47|HOUSEHOLD|pending foxes|\n"
    )

    manifest = {
        "version": 2,
        "benchmark": "tpch",
        "scale_factor": 0.01,
        "format_preference": ["tbl"],
        "tables": {
            "customer": {
                "formats": {
                    "tbl": [
                        {
                            "path": "customer.tbl",
                            "size_bytes": tbl_file.stat().st_size,
                            "row_count": 5,
                        }
                    ]
                }
            }
        },
    }

    manifest_path = data_dir / "_datagen_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return data_dir


@pytest.mark.integration
class TestCLIConvertIntegration:
    def test_cli_convert_help(self):

        from click.testing import CliRunner

        from benchbox.cli.commands.convert import convert

        runner = CliRunner()
        result = runner.invoke(convert, ["--help"])

        assert result.exit_code == 0
        assert "Convert benchmark data to optimized table formats" in result.output
        assert "--format" in result.output
        assert "--compression" in result.output
        assert "parquet" in result.output
        assert "delta" in result.output
        assert "iceberg" in result.output

    def test_cli_convert_missing_input_error(self):

        from click.testing import CliRunner

        from benchbox.cli.commands.convert import convert

        runner = CliRunner()
        result = runner.invoke(convert, ["--format", "parquet"])

        assert result.exit_code != 0
        assert "Missing option '--input'" in result.output

    def test_cli_convert_missing_format_error(self, tpch_data_dir_with_manifest: Path):

        from click.testing import CliRunner

        from benchbox.cli.commands.convert import convert

        runner = CliRunner()
        result = runner.invoke(convert, ["--input", str(tpch_data_dir_with_manifest)])

        assert result.exit_code != 0
        assert "Missing option '--format'" in result.output

    def test_cli_convert_invalid_format_error(self, tpch_data_dir_with_manifest: Path):

        from click.testing import CliRunner

        from benchbox.cli.commands.convert import convert

        runner = CliRunner()
        result = runner.invoke(
            convert,
            [
                "--input",
                str(tpch_data_dir_with_manifest),
                "--format",
                "invalid_format",
            ],
        )

        assert result.exit_code != 0
        assert "Invalid value for '--format'" in result.output

    def test_cli_convert_missing_manifest_error(self, tmp_path_factory):

        from click.testing import CliRunner

        from benchbox.cli.commands.convert import convert

        empty_dir = tmp_path_factory.mktemp("empty_dir")

        runner = CliRunner()
        result = runner.invoke(
            convert,
            ["--input", str(empty_dir), "--format", "parquet"],
        )

        assert result.exit_code != 0
        assert "Manifest not found" in result.output

    def test_cli_convert_compression_options_accepted(self):

        from click.testing import CliRunner

        from benchbox.cli.commands.convert import convert

        runner = CliRunner()
        result = runner.invoke(convert, ["--help"])

        assert result.exit_code == 0
        for compression in ["snappy", "gzip", "zstd", "none"]:
            assert compression in result.output


try:
    _test_conn = duckdb.connect(":memory:")
    _test_conn.execute("INSTALL ducklake")
    _test_conn.execute("LOAD ducklake")
    DUCKLAKE_AVAILABLE = True
    _test_conn.close()
except Exception:
    DUCKLAKE_AVAILABLE = False


@pytest.mark.integration
@pytest.mark.skipif(not DUCKLAKE_AVAILABLE, reason="DuckLake extension not available")
class TestDuckLakeFormatSmoke:
    def test_ducklake_conversion_and_structure(
        self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict
    ):

        from benchbox.utils.format_converters.ducklake_converter import DuckLakeConverter

        converter = DuckLakeConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )

        assert result.success
        assert result.row_count == 5

        ducklake_dir = tmp_path / "customer"
        assert ducklake_dir.exists()
        assert ducklake_dir.is_dir()

        assert (ducklake_dir / "metadata.ducklake").exists()
        assert (ducklake_dir / "data").exists()
        assert (ducklake_dir / "data").is_dir()

    def test_ducklake_duckdb_query(self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict):

        from benchbox.utils.format_converters.ducklake_converter import DuckLakeConverter

        converter = DuckLakeConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )

        assert result.success
        ducklake_dir = tmp_path / "customer"
        metadata_path = ducklake_dir / "metadata.ducklake"
        data_path = ducklake_dir / "data"

        conn = duckdb.connect(":memory:")
        conn.execute("LOAD ducklake")

        conn.execute(f"ATTACH 'ducklake:{metadata_path}' AS ducklake_db (DATA_PATH '{data_path}')")

        count = conn.execute("SELECT COUNT(*) FROM ducklake_db.main.customer").fetchone()[0]
        assert count == 5

        sum_result = conn.execute("SELECT SUM(c_acctbal) FROM ducklake_db.main.customer").fetchone()[0]
        expected_sum = 711.56 + 121.65 + 7498.12 + 2866.83 + 794.47
        assert abs(float(sum_result) - expected_sum) < 0.01

        filter_result = conn.execute(
            "SELECT c_custkey, c_name FROM ducklake_db.main.customer WHERE c_acctbal > 1000"
        ).fetchall()
        assert len(filter_result) == 2
        custkeys = {row[0] for row in filter_result}
        assert custkeys == {3, 4}

        conn.close()

    def test_ducklake_metadata(self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict):

        from benchbox.utils.format_converters.ducklake_converter import DuckLakeConverter

        converter = DuckLakeConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )

        assert result.success

        assert result.metadata["format"] == "ducklake"
        assert "table_path" in result.metadata
        assert "metadata_path" in result.metadata
        assert "data_path" in result.metadata
        assert result.metadata["num_columns"] == 8

        assert result.source_size_bytes > 0
        assert result.output_size_bytes > 0

    def test_ducklake_sharded_files(self, tmp_path: Path, tpch_customer_schema: dict):

        from benchbox.utils.format_converters.ducklake_converter import DuckLakeConverter

        tbl_file1 = tmp_path / "customer.tbl.1"
        tbl_file1.write_text(
            "1|Customer#000000001|IVhzIApeRb|15|25-989-741-2988|711.56|BUILDING|regular packages|\n"
            "2|Customer#000000002|XSTf4,NCwDVaW|13|23-768-687-3665|121.65|AUTOMOBILE|theodolites|\n"
        )

        tbl_file2 = tmp_path / "customer.tbl.2"
        tbl_file2.write_text(
            "3|Customer#000000003|MG9kdTD2W|1|11-719-748-3364|7498.12|AUTOMOBILE|deposits serve|\n"
            "4|Customer#000000004|XxVSJsLAGt|4|14-128-190-5944|2866.83|MACHINERY|requests haggle|\n"
            "5|Customer#000000005|KvpyuHCplr|3|13-750-942-6364|794.47|HOUSEHOLD|pending foxes|\n"
        )

        converter = DuckLakeConverter()
        result = converter.convert(
            source_files=[tbl_file1, tbl_file2],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )

        assert result.success
        assert result.row_count == 5

        ducklake_dir = tmp_path / "customer"
        metadata_path = ducklake_dir / "metadata.ducklake"
        data_path = ducklake_dir / "data"

        conn = duckdb.connect(":memory:")
        conn.execute("LOAD ducklake")
        conn.execute(f"ATTACH 'ducklake:{metadata_path}' AS ducklake_db (DATA_PATH '{data_path}')")

        count = conn.execute("SELECT COUNT(*) FROM ducklake_db.main.customer").fetchone()[0]
        assert count == 5
        conn.close()


@pytest.mark.integration
@pytest.mark.skipif(not DUCKLAKE_AVAILABLE, reason="DuckLake extension not available")
class TestDuckLakeMaintenanceOperations:
    @pytest.fixture
    def ducklake_customer_table(self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict) -> Path:
        from benchbox.utils.format_converters.ducklake_converter import DuckLakeConverter

        converter = DuckLakeConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )
        assert result.success
        return tmp_path / "customer"

    def test_ducklake_insert_rows(self, ducklake_customer_table: Path):

        import pyarrow as pa

        from benchbox.platforms.dataframe.ducklake_maintenance import DuckLakeMaintenanceOperations

        ops = DuckLakeMaintenanceOperations()

        new_data = pa.table(
            {
                "c_custkey": [6, 7],
                "c_name": ["Customer#000000006", "Customer#000000007"],
                "c_address": ["Address6", "Address7"],
                "c_nationkey": [1, 2],
                "c_phone": ["11-111-111-1111", "22-222-222-2222"],
                "c_acctbal": [1000.00, 2000.00],
                "c_mktsegment": ["BUILDING", "AUTOMOBILE"],
                "c_comment": ["comment 6", "comment 7"],
            }
        )

        result = ops.insert_rows(
            table_path=ducklake_customer_table,
            dataframe=new_data,
            mode="append",
        )

        assert result.success
        assert result.rows_affected == 2

        metadata_path = ducklake_customer_table / "metadata.ducklake"
        data_path = ducklake_customer_table / "data"

        conn = duckdb.connect(":memory:")
        conn.execute("LOAD ducklake")
        conn.execute(f"ATTACH 'ducklake:{metadata_path}' AS ducklake_db (DATA_PATH '{data_path}')")

        count = conn.execute("SELECT COUNT(*) FROM ducklake_db.main.customer").fetchone()[0]
        assert count == 7
        conn.close()

    def test_ducklake_delete_rows(self, ducklake_customer_table: Path):

        from benchbox.platforms.dataframe.ducklake_maintenance import DuckLakeMaintenanceOperations

        ops = DuckLakeMaintenanceOperations()

        result = ops.delete_rows(
            table_path=ducklake_customer_table,
            condition="c_acctbal > 5000",
        )

        assert result.success
        assert result.rows_affected == 1

        metadata_path = ducklake_customer_table / "metadata.ducklake"
        data_path = ducklake_customer_table / "data"

        conn = duckdb.connect(":memory:")
        conn.execute("LOAD ducklake")
        conn.execute(f"ATTACH 'ducklake:{metadata_path}' AS ducklake_db (DATA_PATH '{data_path}')")

        count = conn.execute("SELECT COUNT(*) FROM ducklake_db.main.customer").fetchone()[0]
        assert count == 4

        remaining = conn.execute("SELECT c_custkey FROM ducklake_db.main.customer ORDER BY c_custkey").fetchall()
        custkeys = [row[0] for row in remaining]
        assert 3 not in custkeys
        conn.close()

    def test_ducklake_update_rows(self, ducklake_customer_table: Path):

        from benchbox.platforms.dataframe.ducklake_maintenance import DuckLakeMaintenanceOperations

        ops = DuckLakeMaintenanceOperations()

        result = ops.update_rows(
            table_path=ducklake_customer_table,
            condition="c_acctbal < 800",
            updates={"c_mktsegment": "'UPDATED'"},
        )

        assert result.success
        assert result.rows_affected == 3

        metadata_path = ducklake_customer_table / "metadata.ducklake"
        data_path = ducklake_customer_table / "data"

        conn = duckdb.connect(":memory:")
        conn.execute("LOAD ducklake")
        conn.execute(f"ATTACH 'ducklake:{metadata_path}' AS ducklake_db (DATA_PATH '{data_path}')")

        updated = conn.execute(
            "SELECT c_custkey FROM ducklake_db.main.customer WHERE c_mktsegment = 'UPDATED'"
        ).fetchall()
        assert len(updated) == 3
        custkeys = {row[0] for row in updated}
        assert custkeys == {1, 2, 5}
        conn.close()

    def test_ducklake_capabilities(self):

        from benchbox.platforms.dataframe.ducklake_maintenance import (
            DUCKLAKE_CAPABILITIES,
            DuckLakeMaintenanceOperations,
        )

        ops = DuckLakeMaintenanceOperations()
        caps = ops.get_capabilities()

        assert caps.supports_insert is True
        assert caps.supports_delete is True
        assert caps.supports_update is True
        assert caps.supports_merge is True
        assert caps.supports_transactions is True
        assert caps.supports_time_travel is True
        assert caps.supports_row_level_delete is True

        assert caps == DUCKLAKE_CAPABILITIES


@pytest.mark.integration
@pytest.mark.skipif(not DUCKLAKE_AVAILABLE, reason="DuckLake extension not available")
class TestDuckLakeDataLoadingHandlers:
    def test_ducklake_file_handler_detection(self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict):

        from benchbox.platforms.base.data_loading import FileFormatRegistry
        from benchbox.utils.format_converters.ducklake_converter import DuckLakeConverter

        converter = DuckLakeConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )
        assert result.success

        ducklake_dir = tmp_path / "customer"

        handler = FileFormatRegistry.get_handler(ducklake_dir)
        assert handler is not None
        assert handler.__class__.__name__ == "DuckLakeFileHandler"

    def test_duckdb_ducklake_handler_load(self, tmp_path: Path, tpch_customer_tbl: Path, tpch_customer_schema: dict):

        from benchbox.platforms.base.data_loading import DuckDBDuckLakeHandler
        from benchbox.utils.format_converters.ducklake_converter import DuckLakeConverter

        converter = DuckLakeConverter()
        result = converter.convert(
            source_files=[tpch_customer_tbl],
            table_name="customer",
            schema=tpch_customer_schema,
            options=ConversionOptions(output_dir=tmp_path),
        )
        assert result.success

        ducklake_dir = tmp_path / "customer"

        conn = duckdb.connect(":memory:")
        conn.execute("""
            CREATE TABLE customer (
                c_custkey INTEGER,
                c_name VARCHAR(25),
                c_address VARCHAR(40),
                c_nationkey INTEGER,
                c_phone CHAR(15),
                c_acctbal DECIMAL(15,2),
                c_mktsegment CHAR(10),
                c_comment VARCHAR(117)
            )
        """)

        class MockAdapter:
            dry_run_mode = False

        handler = DuckDBDuckLakeHandler(MockAdapter())

        row_count = handler.load_table(
            table_name="customer",
            file_path=ducklake_dir,
            connection=conn,
            benchmark=None,
            logger=None,
        )

        assert row_count == 5

        loaded_count = conn.execute("SELECT COUNT(*) FROM customer").fetchone()[0]
        assert loaded_count == 5
        conn.close()
