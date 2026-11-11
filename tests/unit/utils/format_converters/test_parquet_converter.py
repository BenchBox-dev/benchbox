import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from benchbox.utils.format_converters.base import (
    ConversionError,
    ConversionOptions,
    SchemaError,
)
from benchbox.utils.format_converters.parquet_converter import ParquetConverter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestParquetConverterBasics:
    def test_get_file_extension(self):

        converter = ParquetConverter()
        assert converter.get_file_extension() == ".parquet"

    def test_get_format_name(self):

        converter = ParquetConverter()
        assert converter.get_format_name() == "Parquet"


class TestParquetSchemaValidation:
    def test_valid_schema(self):

        converter = ParquetConverter()
        schema = {
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "name", "type": "VARCHAR(100)"},
            ]
        }
        assert converter.validate_schema(schema) is True

    def test_empty_schema(self):

        converter = ParquetConverter()
        with pytest.raises(SchemaError, match="Schema is empty or None"):
            converter.validate_schema({})

    def test_none_schema(self):

        converter = ParquetConverter()
        with pytest.raises(SchemaError, match="Schema is empty or None"):
            converter.validate_schema(None)

    def test_schema_missing_columns(self):

        converter = ParquetConverter()
        schema = {"tables": []}
        with pytest.raises(SchemaError, match="Schema missing 'columns' field"):
            converter.validate_schema(schema)

    def test_schema_empty_columns(self):

        converter = ParquetConverter()
        schema = {"columns": []}
        with pytest.raises(SchemaError, match="'columns' must be a non-empty list"):
            converter.validate_schema(schema)

    def test_schema_columns_not_list(self):

        converter = ParquetConverter()
        schema = {"columns": "not a list"}
        with pytest.raises(SchemaError, match="'columns' must be a non-empty list"):
            converter.validate_schema(schema)

    def test_column_not_dict(self):

        converter = ParquetConverter()
        schema = {"columns": ["not a dict"]}
        with pytest.raises(SchemaError, match="Column 0 is not a dictionary"):
            converter.validate_schema(schema)

    def test_column_missing_name(self):

        converter = ParquetConverter()
        schema = {"columns": [{"type": "INTEGER"}]}
        with pytest.raises(SchemaError, match="Column 0 missing 'name' field"):
            converter.validate_schema(schema)

    def test_column_missing_type(self):

        converter = ParquetConverter()
        schema = {"columns": [{"name": "id"}]}
        with pytest.raises(SchemaError, match="Column 0 \\(id\\) missing 'type' field"):
            converter.validate_schema(schema)


class TestSqlTypeMapping:
    def test_integer_mapping(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("INTEGER")
        assert arrow_type == pa.int64()

    def test_int_mapping(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("INT")
        assert arrow_type == pa.int64()

    def test_bigint_mapping(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("BIGINT")
        assert arrow_type == pa.int64()

    def test_decimal_with_params_mapping(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("DECIMAL(15,2)")
        assert arrow_type == pa.decimal128(15, 2)

    def test_decimal_with_one_param_mapping(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("DECIMAL(10)")
        assert arrow_type == pa.decimal128(10, 0)

    def test_decimal_without_params_mapping(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("DECIMAL")
        assert arrow_type == pa.decimal128(15, 2)

    def test_date_mapping(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("DATE")
        assert arrow_type == pa.date32()

    def test_varchar_mapping(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("VARCHAR(100)")
        assert arrow_type == pa.string()

    def test_varchar_no_size_mapping(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("VARCHAR")
        assert arrow_type == pa.string()

    def test_char_mapping(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("CHAR(10)")
        assert arrow_type == pa.string()

    def test_float_mapping(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("FLOAT")
        assert arrow_type == pa.float32()

    def test_real_mapping(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("REAL")
        assert arrow_type == pa.float32()

    def test_double_mapping(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("DOUBLE")
        assert arrow_type == pa.float64()

    def test_unknown_type_defaults_to_string(self):

        converter = ParquetConverter()
        arrow_type = converter._map_sql_type_to_arrow("UNKNOWN_TYPE")
        assert arrow_type == pa.string()

    def test_case_insensitive_mapping(self):

        converter = ParquetConverter()
        assert converter._map_sql_type_to_arrow("integer") == pa.int64()
        assert converter._map_sql_type_to_arrow("Integer") == pa.int64()
        assert converter._map_sql_type_to_arrow("INTEGER") == pa.int64()


class TestArrowSchemaBuilding:
    def test_build_basic_schema(self):

        converter = ParquetConverter()
        schema = {
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "name", "type": "VARCHAR(100)"},
            ]
        }
        arrow_schema = converter._build_arrow_schema(schema)
        assert len(arrow_schema) == 2
        assert arrow_schema.field("id").type == pa.int64()
        assert arrow_schema.field("name").type == pa.string()

    def test_build_schema_with_nullable(self):

        converter = ParquetConverter()
        schema = {
            "columns": [
                {"name": "id", "type": "INTEGER", "nullable": False},
                {"name": "name", "type": "VARCHAR(100)", "nullable": True},
            ]
        }
        arrow_schema = converter._build_arrow_schema(schema)
        assert arrow_schema.field("id").nullable is False
        assert arrow_schema.field("name").nullable is True

    def test_build_schema_default_nullable(self):

        converter = ParquetConverter()
        schema = {
            "columns": [
                {"name": "id", "type": "INTEGER"},
            ]
        }
        arrow_schema = converter._build_arrow_schema(schema)
        assert arrow_schema.field("id").nullable is True

    def test_build_complex_schema(self):

        converter = ParquetConverter()
        schema = {
            "columns": [
                {"name": "c_custkey", "type": "INTEGER"},
                {"name": "c_name", "type": "VARCHAR(25)"},
                {"name": "c_acctbal", "type": "DECIMAL(15,2)"},
                {"name": "c_mktsegment", "type": "CHAR(10)"},
            ]
        }
        arrow_schema = converter._build_arrow_schema(schema)
        assert len(arrow_schema) == 4
        assert arrow_schema.field("c_custkey").type == pa.int64()
        assert arrow_schema.field("c_name").type == pa.string()
        assert arrow_schema.field("c_acctbal").type == pa.decimal128(15, 2)
        assert arrow_schema.field("c_mktsegment").type == pa.string()


class TestParquetConversion:
    @pytest.fixture
    def temp_dir(self, tmp_path):

        return tmp_path

    @pytest.fixture
    def sample_schema(self):

        return {
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "name", "type": "VARCHAR(25)"},
                {"name": "amount", "type": "DECIMAL(15,2)"},
            ]
        }

    def test_convert_single_file(self, temp_dir, sample_schema):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|100.50|\n2|Bob|200.75|\n3|Charlie|300.00|\n")

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=sample_schema,
            options=ConversionOptions(output_dir=temp_dir),
        )

        assert result.success is True
        assert result.row_count == 3
        assert len(result.output_files) == 1
        assert result.output_files[0].exists()
        assert result.output_files[0].suffix == ".parquet"

        table = pq.read_table(result.output_files[0])
        assert table.num_rows == 3
        assert table.num_columns == 3

        data = table.to_pydict()
        assert data["id"] == [1, 2, 3]
        assert data["name"] == ["Alice", "Bob", "Charlie"]

    def test_convert_sharded_files(self, temp_dir, sample_schema):

        tbl_file1 = temp_dir / "test.tbl.1"
        tbl_file1.write_text("1|Alice|100.50|\n2|Bob|200.75|\n")

        tbl_file2 = temp_dir / "test.tbl.2"
        tbl_file2.write_text("3|Charlie|300.00|\n4|David|400.25|\n")

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tbl_file1, tbl_file2],
            table_name="test",
            schema=sample_schema,
            options=ConversionOptions(output_dir=temp_dir, merge_shards=True),
        )

        assert result.success is True
        assert result.row_count == 4
        assert len(result.output_files) == 1

        table = pq.read_table(result.output_files[0])
        assert table.num_rows == 4
        data = table.to_pydict()
        assert data["id"] == [1, 2, 3, 4]

    def test_convert_with_compression_options(self, temp_dir, sample_schema):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|100.50|\n2|Bob|200.75|\n")

        converter = ParquetConverter()

        result = converter.convert(
            source_files=[tbl_file],
            table_name="test_snappy",
            schema=sample_schema,
            options=ConversionOptions(output_dir=temp_dir, compression="snappy"),
        )
        assert result.success is True
        assert result.metadata["compression"] == "snappy"

        result = converter.convert(
            source_files=[tbl_file],
            table_name="test_gzip",
            schema=sample_schema,
            options=ConversionOptions(output_dir=temp_dir, compression="gzip"),
        )
        assert result.success is True
        assert result.metadata["compression"] == "gzip"

        result = converter.convert(
            source_files=[tbl_file],
            table_name="test_zstd",
            schema=sample_schema,
            options=ConversionOptions(output_dir=temp_dir, compression="zstd"),
        )
        assert result.success is True
        assert result.metadata["compression"] == "zstd"

    def test_convert_with_empty_strings_as_null(self, temp_dir):

        schema = {
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "name", "type": "VARCHAR(25)", "nullable": True},
            ]
        }

        tbl_file = temp_dir / "test.tbl"

        tbl_file.write_text("1|Alice|\n2||\n")

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=schema,
            options=ConversionOptions(output_dir=temp_dir),
        )

        table = pq.read_table(result.output_files[0])
        data = table.to_pydict()
        assert data["id"] == [1, 2]
        assert data["name"][0] == "Alice"
        assert data["name"][1] is None

    def test_convert_missing_source_file(self, temp_dir, sample_schema):

        missing_file = temp_dir / "missing.tbl"

        converter = ParquetConverter()
        with pytest.raises(ConversionError, match="Source file not found"):
            converter.convert(
                source_files=[missing_file],
                table_name="test",
                schema=sample_schema,
            )

    def test_convert_empty_source_list(self, sample_schema):

        converter = ParquetConverter()
        with pytest.raises(ConversionError, match="No source files provided"):
            converter.convert(
                source_files=[],
                table_name="test",
                schema=sample_schema,
            )

    def test_convert_invalid_schema(self, temp_dir):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|\n")

        converter = ParquetConverter()
        with pytest.raises(SchemaError):
            converter.convert(
                source_files=[tbl_file],
                table_name="test",
                schema={},
            )

    def test_conversion_result_metadata(self, temp_dir, sample_schema):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|100.50|\n2|Bob|200.75|\n")

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=sample_schema,
            options=ConversionOptions(output_dir=temp_dir),
        )

        assert "row_groups" in result.metadata
        assert "compression" in result.metadata
        assert "num_columns" in result.metadata
        assert result.metadata["num_columns"] == 3

        assert result.source_size_bytes > 0
        assert result.output_size_bytes > 0
        assert result.compression_ratio > 0

    def test_progress_callback(self, temp_dir, sample_schema):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|100.50|\n")

        progress_updates = []

        def progress_callback(message: str, progress: float):
            progress_updates.append((message, progress))

        converter = ParquetConverter()
        converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=sample_schema,
            options=ConversionOptions(output_dir=temp_dir),
            progress_callback=progress_callback,
        )

        assert len(progress_updates) > 0

        assert any("Starting" in msg for msg, _ in progress_updates)
        assert any("Reading" in msg for msg, _ in progress_updates)
        assert any("Writing" in msg for msg, _ in progress_updates)
        assert any("complete" in msg for msg, _ in progress_updates)

    def test_get_output_path(self, temp_dir):

        converter = ParquetConverter()

        output_path = converter.get_output_path("customer", temp_dir)
        assert output_path == temp_dir / "customer.parquet"

        custom_dir = temp_dir / "custom"
        options = ConversionOptions(output_dir=custom_dir)
        output_path = converter.get_output_path("customer", temp_dir, options)
        assert output_path == custom_dir / "customer.parquet"


class TestParquetPartitioning:
    @pytest.fixture
    def temp_dir(self, tmp_path):

        return tmp_path

    @pytest.fixture
    def partitioned_schema(self):

        return {
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "name", "type": "VARCHAR(25)"},
                {"name": "region", "type": "VARCHAR(10)"},
                {"name": "year", "type": "INTEGER"},
            ]
        }

    def test_convert_with_single_partition_column(self, temp_dir, partitioned_schema):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|East|2023|\n2|Bob|West|2023|\n3|Charlie|East|2024|\n4|David|West|2024|\n")

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=partitioned_schema,
            options=ConversionOptions(
                output_dir=temp_dir,
                partition_cols=["region"],
            ),
        )

        assert result.success is True
        assert result.row_count == 4
        assert result.metadata["partitioned"] is True
        assert result.metadata["partition_cols"] == ["region"]

        partitioned_dir = temp_dir / "test"
        assert partitioned_dir.exists()
        assert partitioned_dir.is_dir()

        east_dir = partitioned_dir / "region=East"
        west_dir = partitioned_dir / "region=West"
        assert east_dir.exists()
        assert west_dir.exists()

        east_files = list(east_dir.glob("*.parquet"))
        west_files = list(west_dir.glob("*.parquet"))
        assert len(east_files) >= 1
        assert len(west_files) >= 1

    def test_convert_with_multiple_partition_columns(self, temp_dir, partitioned_schema):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|East|2023|\n2|Bob|West|2023|\n3|Charlie|East|2024|\n4|David|West|2024|\n")

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=partitioned_schema,
            options=ConversionOptions(
                output_dir=temp_dir,
                partition_cols=["region", "year"],
            ),
        )

        assert result.success is True
        assert result.metadata["partitioned"] is True
        assert result.metadata["partition_cols"] == ["region", "year"]

        partitioned_dir = temp_dir / "test"
        assert (partitioned_dir / "region=East" / "year=2023").exists()
        assert (partitioned_dir / "region=East" / "year=2024").exists()
        assert (partitioned_dir / "region=West" / "year=2023").exists()
        assert (partitioned_dir / "region=West" / "year=2024").exists()

    def test_partitioned_data_can_be_read_as_dataset(self, temp_dir, partitioned_schema):

        import pyarrow.dataset as ds

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|East|2023|\n2|Bob|West|2023|\n3|Charlie|East|2024|\n")

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=partitioned_schema,
            options=ConversionOptions(
                output_dir=temp_dir,
                partition_cols=["region"],
            ),
        )

        partitioned_dir = result.output_files[0]
        dataset = ds.dataset(partitioned_dir, format="parquet", partitioning="hive")
        table = dataset.to_table()

        assert table.num_rows == 3

        data = table.to_pydict()
        assert set(data["region"]) == {"East", "West"}

    def test_partition_column_validation_invalid_column(self, temp_dir, partitioned_schema):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|East|2023|\n")

        converter = ParquetConverter()
        with pytest.raises(ConversionError, match="Partition column 'invalid_col' not found"):
            converter.convert(
                source_files=[tbl_file],
                table_name="test",
                schema=partitioned_schema,
                options=ConversionOptions(
                    output_dir=temp_dir,
                    partition_cols=["invalid_col"],
                ),
            )

    def test_partition_counts_in_metadata(self, temp_dir, partitioned_schema):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|East|2023|\n2|Bob|West|2023|\n3|Charlie|East|2024|\n4|David|North|2024|\n")

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=partitioned_schema,
            options=ConversionOptions(
                output_dir=temp_dir,
                partition_cols=["region"],
            ),
        )

        assert "partition_counts" in result.metadata
        assert result.metadata["partition_counts"]["region"] == 3

    def test_partitioned_with_compression(self, temp_dir, partitioned_schema):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|East|2023|\n2|Bob|West|2023|\n")

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=partitioned_schema,
            options=ConversionOptions(
                output_dir=temp_dir,
                partition_cols=["region"],
                compression="gzip",
            ),
        )

        assert result.success is True
        assert result.metadata["compression"] == "gzip"

    def test_non_partitioned_output_has_partitioned_false(self, temp_dir, partitioned_schema):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|East|2023|\n")

        converter = ParquetConverter()
        result = converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=partitioned_schema,
            options=ConversionOptions(output_dir=temp_dir),
        )

        assert result.metadata.get("partitioned") is False


