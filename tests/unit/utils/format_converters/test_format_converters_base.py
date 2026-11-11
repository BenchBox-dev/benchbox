import gzip
import tempfile
from pathlib import Path

import pyarrow as pa
import pytest

from benchbox.utils.format_converters.base import (
    ArrowTypeMapper,
    BaseFormatConverter,
    ConversionError,
    ConversionOptions,
    ConversionResult,
    SchemaError,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestConversionOptions:
    def test_default_options(self):
        opts = ConversionOptions()
        assert opts.compression == "snappy"
        assert opts.row_group_size == 128 * 1024 * 1024
        assert opts.partition_cols == []
        assert opts.merge_shards is True
        assert opts.output_dir is None
        assert opts.preserve_source is True
        assert opts.validate_row_count is True
        assert opts.strict_schema is False

    def test_custom_options(self):
        opts = ConversionOptions(
            compression="gzip",
            row_group_size=64 * 1024 * 1024,
            partition_cols=["date"],
            merge_shards=False,
        )
        assert opts.compression == "gzip"
        assert opts.row_group_size == 64 * 1024 * 1024
        assert opts.partition_cols == ["date"]
        assert opts.merge_shards is False

    def test_strict_schema_option(self):
        opts = ConversionOptions(strict_schema=True)
        assert opts.strict_schema is True

    def test_invalid_compression(self):
        with pytest.raises(ValueError, match="Invalid compression"):
            ConversionOptions(compression="invalid")

    def test_invalid_row_group_size(self):
        with pytest.raises(ValueError, match="row_group_size must be positive"):
            ConversionOptions(row_group_size=0)

        with pytest.raises(ValueError, match="row_group_size must be positive"):
            ConversionOptions(row_group_size=-100)


class TestConversionResult:
    def test_basic_result(self):
        result = ConversionResult(
            output_files=[Path("/tmp/test.parquet")],
            row_count=1000,
            source_size_bytes=10000,
            output_size_bytes=2000,
        )
        assert len(result.output_files) == 1
        assert result.row_count == 1000
        assert result.success is True

    def test_compression_ratio(self):
        result = ConversionResult(
            output_files=[Path("/tmp/test.parquet")],
            row_count=1000,
            source_size_bytes=10000,
            output_size_bytes=2000,
        )
        assert result.compression_ratio == 5.0

    def test_compression_ratio_zero_output(self):
        result = ConversionResult(
            output_files=[],
            row_count=0,
            source_size_bytes=0,
            output_size_bytes=0,
        )
        assert result.compression_ratio == 0.0

    def test_result_with_errors(self):
        result = ConversionResult(
            output_files=[],
            row_count=0,
            source_size_bytes=0,
            output_size_bytes=0,
            errors=["File not found", "Schema mismatch"],
        )
        assert result.success is False
        assert len(result.errors) == 2


class TestConversionErrors:
    def test_conversion_error(self):
        with pytest.raises(ConversionError, match="Test error"):
            raise ConversionError("Test error")

    def test_schema_error(self):
        with pytest.raises(SchemaError, match="Invalid schema"):
            raise SchemaError("Invalid schema")

    def test_schema_error_is_conversion_error(self):
        assert issubclass(SchemaError, ConversionError)


class TestArrowTypeMapper:
    def test_integer_mapping(self):
        assert ArrowTypeMapper.map_sql_type_to_arrow("INTEGER") == pa.int64()
        assert ArrowTypeMapper.map_sql_type_to_arrow("INT") == pa.int64()

    def test_bigint_mapping(self):
        assert ArrowTypeMapper.map_sql_type_to_arrow("BIGINT") == pa.int64()

    def test_float_mapping(self):
        assert ArrowTypeMapper.map_sql_type_to_arrow("FLOAT") == pa.float32()
        assert ArrowTypeMapper.map_sql_type_to_arrow("REAL") == pa.float32()

    def test_double_mapping(self):
        assert ArrowTypeMapper.map_sql_type_to_arrow("DOUBLE") == pa.float64()

    def test_varchar_mapping(self):
        assert ArrowTypeMapper.map_sql_type_to_arrow("VARCHAR(100)") == pa.string()
        assert ArrowTypeMapper.map_sql_type_to_arrow("VARCHAR") == pa.string()

    def test_char_mapping(self):
        assert ArrowTypeMapper.map_sql_type_to_arrow("CHAR(10)") == pa.string()
        assert ArrowTypeMapper.map_sql_type_to_arrow("CHAR") == pa.string()

    def test_date_mapping(self):
        assert ArrowTypeMapper.map_sql_type_to_arrow("DATE") == pa.date32()

    def test_timestamp_mapping(self):
        assert ArrowTypeMapper.map_sql_type_to_arrow("TIMESTAMP") == pa.timestamp("us")

    def test_boolean_mapping(self):
        assert ArrowTypeMapper.map_sql_type_to_arrow("BOOLEAN") == pa.bool_()
        assert ArrowTypeMapper.map_sql_type_to_arrow("BOOL") == pa.bool_()

    def test_text_mapping(self):
        assert ArrowTypeMapper.map_sql_type_to_arrow("TEXT") == pa.string()

    def test_decimal_with_params_mapping(self):
        result = ArrowTypeMapper.map_sql_type_to_arrow("DECIMAL(15,2)")
        assert result == pa.decimal128(15, 2)

    def test_decimal_with_precision_only(self):
        result = ArrowTypeMapper.map_sql_type_to_arrow("DECIMAL(10)")
        assert result == pa.decimal128(10, 0)

    def test_decimal_without_params(self):
        result = ArrowTypeMapper.map_sql_type_to_arrow("DECIMAL")
        assert result == pa.decimal128(15, 2)

    def test_malformed_decimal_raises_error(self):
        with pytest.raises(SchemaError, match="Invalid DECIMAL"):
            ArrowTypeMapper.map_sql_type_to_arrow("DECIMAL(abc,def)")

    def test_malformed_decimal_non_numeric_precision(self):
        with pytest.raises(SchemaError, match="Invalid DECIMAL"):
            ArrowTypeMapper.map_sql_type_to_arrow("DECIMAL(fifteen,two)")

    def test_case_insensitive_mapping(self):
        assert ArrowTypeMapper.map_sql_type_to_arrow("integer") == pa.int64()
        assert ArrowTypeMapper.map_sql_type_to_arrow("Integer") == pa.int64()
        assert ArrowTypeMapper.map_sql_type_to_arrow("INTEGER") == pa.int64()

    def test_unknown_type_defaults_to_string(self):
        assert ArrowTypeMapper.map_sql_type_to_arrow("CUSTOM_TYPE") == pa.string()

    def test_unknown_type_raises_error_in_strict_mode(self):
        with pytest.raises(SchemaError, match="Unknown SQL type.*strict mode"):
            ArrowTypeMapper.map_sql_type_to_arrow("CUSTOM_TYPE", strict=True)

    def test_unknown_type_error_message_contains_type_name(self):
        with pytest.raises(SchemaError, match="WEIRD_TYPE"):
            ArrowTypeMapper.map_sql_type_to_arrow("WEIRD_TYPE", strict=True)

    def test_known_types_work_in_strict_mode(self):
        assert ArrowTypeMapper.map_sql_type_to_arrow("INTEGER", strict=True) == pa.int64()
        assert ArrowTypeMapper.map_sql_type_to_arrow("VARCHAR(50)", strict=True) == pa.string()
        assert ArrowTypeMapper.map_sql_type_to_arrow("DECIMAL(10,2)", strict=True) == pa.decimal128(10, 2)

    def test_build_arrow_schema_basic(self):
        schema = {
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "name", "type": "VARCHAR(100)"},
            ]
        }
        arrow_schema = ArrowTypeMapper.build_arrow_schema(schema)
        assert len(arrow_schema) == 2
        assert arrow_schema.field("id").type == pa.int64()
        assert arrow_schema.field("name").type == pa.string()

    def test_build_arrow_schema_with_nullable(self):
        schema = {
            "columns": [
                {"name": "id", "type": "INTEGER", "nullable": False},
                {"name": "name", "type": "VARCHAR(100)", "nullable": True},
            ]
        }
        arrow_schema = ArrowTypeMapper.build_arrow_schema(schema)
        assert arrow_schema.field("id").nullable is False
        assert arrow_schema.field("name").nullable is True

    def test_build_arrow_schema_missing_columns(self):
        with pytest.raises(SchemaError, match="columns"):
            ArrowTypeMapper.build_arrow_schema({})

    def test_build_arrow_schema_empty(self):
        with pytest.raises(SchemaError, match="columns"):
            ArrowTypeMapper.build_arrow_schema(None)

    def test_build_arrow_schema_strict_mode_unknown_type(self):
        schema = {
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "data", "type": "CUSTOM_TYPE"},
            ]
        }
        with pytest.raises(SchemaError, match="Unknown SQL type"):
            ArrowTypeMapper.build_arrow_schema(schema, strict=True)

    def test_build_arrow_schema_non_strict_mode_unknown_type(self):
        schema = {
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "data", "type": "CUSTOM_TYPE"},
            ]
        }
        arrow_schema = ArrowTypeMapper.build_arrow_schema(schema, strict=False)
        assert arrow_schema.field("id").type == pa.int64()
        assert arrow_schema.field("data").type == pa.string()

    def test_build_arrow_schema_strict_mode_known_types(self):
        schema = {
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "name", "type": "VARCHAR(100)"},
                {"name": "amount", "type": "DECIMAL(15,2)"},
            ]
        }
        arrow_schema = ArrowTypeMapper.build_arrow_schema(schema, strict=True)
        assert arrow_schema.field("id").type == pa.int64()
        assert arrow_schema.field("name").type == pa.string()
        assert arrow_schema.field("amount").type == pa.decimal128(15, 2)


class TestBaseFormatConverterValidation:
    @pytest.fixture
    def converter(self):
        from benchbox.utils.format_converters.parquet_converter import ParquetConverter

        return ParquetConverter()

    def test_validate_row_count_match(self, converter):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            tbl_file = temp_path / "test.tbl"
            tbl_file.write_text("1|Alice|\n2|Bob|\n3|Charlie|\n")

            converter.validate_row_count([tbl_file], 3, "test")

    def test_validate_row_count_mismatch_single_file(self, converter):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            tbl_file = temp_path / "test.tbl"
            tbl_file.write_text("1|Alice|\n2|Bob|\n3|Charlie|\n")

            with pytest.raises(ConversionError, match="Row count mismatch"):
                converter.validate_row_count([tbl_file], 2, "test")

    def test_validate_row_count_mismatch_merged_shards(self, converter):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            shard1 = temp_path / "test.tbl.1"
            shard1.write_text("1|Alice|\n2|Bob|\n")

            shard2 = temp_path / "test.tbl.2"
            shard2.write_text("3|Charlie|\n4|David|\n5|Eve|\n")

            shard3 = temp_path / "test.tbl.3"
            shard3.write_text("6|Frank|\n")

            source_files = [shard1, shard2, shard3]

            converter.validate_row_count(source_files, 6, "test")

            with pytest.raises(ConversionError, match="Row count mismatch.*input=6.*output=5"):
                converter.validate_row_count(source_files, 5, "test")

            with pytest.raises(ConversionError, match="Row count mismatch.*input=6.*output=4"):
                converter.validate_row_count(source_files, 4, "test")

    def test_validate_row_count_error_message_contains_details(self, converter):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            tbl_file = temp_path / "customer.tbl"
            tbl_file.write_text("1|Alice|\n2|Bob|\n3|Charlie|\n")

            with pytest.raises(ConversionError) as exc_info:
                converter.validate_row_count([tbl_file], 1, "customer")

            error_msg = str(exc_info.value)
            assert "customer" in error_msg
            assert "input=3" in error_msg
            assert "output=1" in error_msg
            assert "TPC compliance" in error_msg

    def test_validate_row_count_skips_empty_lines(self, converter):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            tbl_file = temp_path / "test.tbl"
            tbl_file.write_text("1|Alice|\n\n2|Bob|\n\n\n3|Charlie|\n")

            converter.validate_row_count([tbl_file], 3, "test")

    def test_count_rows_across_shards(self, converter):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            shard1 = temp_path / "test.tbl.1"
            shard1.write_text("1|Alice|\n2|Bob|\n")

            shard2 = temp_path / "test.tbl.2"
            shard2.write_text("3|Charlie|\n")

            count = converter.count_rows([shard1, shard2])
            assert count == 3

    def test_read_tbl_files_handles_trailing_delimiter(self, converter):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tbl_file = temp_path / "test.tbl"
            tbl_file.write_text("1|Alice|\n2|Bob|\n")

            schema = {
                "columns": [
                    {"name": "id", "type": "INTEGER"},
                    {"name": "name", "type": "VARCHAR(100)"},
                ]
            }

            table = converter.read_tbl_files([tbl_file], schema)
            assert table.num_rows == 2
            assert table.column_names == ["id", "name"]
            assert table["id"].to_pylist() == [1, 2]
            assert table["name"].to_pylist() == ["Alice", "Bob"]

    def test_read_tbl_files_handles_missing_trailing_delimiter(self, converter):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tbl_file = temp_path / "test.tbl"
            tbl_file.write_text("1|Alice\n2|Bob\n")

            schema = {
                "columns": [
                    {"name": "id", "type": "INTEGER"},
                    {"name": "name", "type": "VARCHAR(100)"},
                ]
            }

            table = converter.read_tbl_files([tbl_file], schema)
            assert table.num_rows == 2
            assert table.column_names == ["id", "name"]
            assert table["id"].to_pylist() == [1, 2]
            assert table["name"].to_pylist() == ["Alice", "Bob"]

    def test_read_tbl_files_handles_gzip_compressed_tbl(self, converter):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tbl_file = temp_path / "test.tbl.gz"
            with gzip.open(tbl_file, "wt", encoding="utf-8") as f:
                f.write("1|Alice\n2|Bob\n")

            schema = {
                "columns": [
                    {"name": "id", "type": "INTEGER"},
                    {"name": "name", "type": "VARCHAR(100)"},
                ]
            }

            table = converter.read_tbl_files([tbl_file], schema)
            assert table.num_rows == 2
            assert table.column_names == ["id", "name"]
            assert table["id"].to_pylist() == [1, 2]
            assert table["name"].to_pylist() == ["Alice", "Bob"]

    def test_count_rows_handles_gzip_compressed_tbl(self, converter):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tbl_file = temp_path / "test.tbl.gz"
            with gzip.open(tbl_file, "wt", encoding="utf-8") as f:
                f.write("1|Alice\n\n2|Bob\n")

            assert converter.count_rows([tbl_file]) == 2

    def test_detect_source_format_tbl(self, converter):
        assert converter._detect_source_format([Path("customer.tbl")]) == "tbl"
        assert converter._detect_source_format([Path("customer.tbl.1")]) == "tbl"

    def test_detect_source_format_dat(self, converter):
        assert converter._detect_source_format([Path("store_sales.dat")]) == "tbl"

    def test_detect_source_format_csv(self, converter):
        assert converter._detect_source_format([Path("data.csv")]) == "csv"

    def test_detect_source_format_parquet(self, converter):
        assert converter._detect_source_format([Path("data.parquet")]) == "parquet"

    def test_detect_source_format_default(self, converter):
        assert converter._detect_source_format([Path("data.unknown")]) == "tbl"
        assert converter._detect_source_format([]) == "tbl"

    def test_get_current_timestamp(self):
        timestamp = BaseFormatConverter.get_current_timestamp()
        assert "T" in timestamp
        assert "+" in timestamp or "Z" in timestamp


