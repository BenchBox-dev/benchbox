import pyarrow as pa
import pytest

from benchbox.utils.format_converters.base import (
    ConversionError,
    ConversionOptions,
    SchemaError,
)
from benchbox.utils.format_converters.ducklake_converter import DuckLakeConverter

try:
    import duckdb

    conn = duckdb.connect(":memory:")
    try:
        conn.execute("INSTALL ducklake")
        conn.execute("LOAD ducklake")
        DUCKLAKE_AVAILABLE = True
    except Exception:
        DUCKLAKE_AVAILABLE = False
    finally:
        conn.close()
except ImportError:
    DUCKLAKE_AVAILABLE = False


pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.skipif(not DUCKLAKE_AVAILABLE, reason="DuckLake extension not available"),
]


class TestDuckLakeConverterBasics:
    def test_get_file_extension(self):

        converter = DuckLakeConverter()
        assert converter.get_file_extension() == ""

    def test_get_format_name(self):

        converter = DuckLakeConverter()
        assert converter.get_format_name() == "DuckLake"


class TestDuckLakeSchemaValidation:
    def test_valid_schema(self):

        converter = DuckLakeConverter()
        schema = {
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "name", "type": "VARCHAR(100)"},
            ]
        }
        assert converter.validate_schema(schema) is True

    def test_empty_schema(self):

        converter = DuckLakeConverter()
        with pytest.raises(SchemaError, match="Schema is empty or None"):
            converter.validate_schema({})

    def test_none_schema(self):

        converter = DuckLakeConverter()
        with pytest.raises(SchemaError, match="Schema is empty or None"):
            converter.validate_schema(None)

    def test_schema_missing_columns(self):

        converter = DuckLakeConverter()
        schema = {"tables": []}
        with pytest.raises(SchemaError, match="Schema missing 'columns' field"):
            converter.validate_schema(schema)

    def test_schema_empty_columns(self):

        converter = DuckLakeConverter()
        schema = {"columns": []}
        with pytest.raises(SchemaError, match="'columns' must be a non-empty list"):
            converter.validate_schema(schema)


class TestSqlTypeMapping:
    def test_integer_mapping(self):

        converter = DuckLakeConverter()
        arrow_type = converter._map_sql_type_to_arrow("INTEGER")
        assert arrow_type == pa.int64()

    def test_decimal_with_params_mapping(self):

        converter = DuckLakeConverter()
        arrow_type = converter._map_sql_type_to_arrow("DECIMAL(15,2)")
        assert arrow_type == pa.decimal128(15, 2)

    def test_varchar_mapping(self):

        converter = DuckLakeConverter()
        arrow_type = converter._map_sql_type_to_arrow("VARCHAR(100)")
        assert arrow_type == pa.string()

    def test_date_mapping(self):

        converter = DuckLakeConverter()
        arrow_type = converter._map_sql_type_to_arrow("DATE")
        assert arrow_type == pa.date32()


class TestDuckLakeConversion:
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

        converter = DuckLakeConverter()
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
        assert result.output_files[0].is_dir()

        ducklake_path = result.output_files[0]
        assert (ducklake_path / "metadata.ducklake").exists()
        assert (ducklake_path / "data").exists()

    def test_convert_sharded_files(self, temp_dir, sample_schema):

        tbl_file1 = temp_dir / "test.tbl.1"
        tbl_file1.write_text("1|Alice|100.50|\n2|Bob|200.75|\n")

        tbl_file2 = temp_dir / "test.tbl.2"
        tbl_file2.write_text("3|Charlie|300.00|\n4|David|400.25|\n")

        converter = DuckLakeConverter()
        result = converter.convert(
            source_files=[tbl_file1, tbl_file2],
            table_name="test",
            schema=sample_schema,
            options=ConversionOptions(output_dir=temp_dir),
        )

        assert result.success is True
        assert result.row_count == 4
        assert len(result.output_files) == 1

    def test_convert_with_empty_strings_as_null(self, temp_dir):

        schema = {
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "name", "type": "VARCHAR(25)", "nullable": True},
            ]
        }

        tbl_file = temp_dir / "test.tbl"

        tbl_file.write_text("1|Alice|\n2||\n")

        converter = DuckLakeConverter()
        result = converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=schema,
            options=ConversionOptions(output_dir=temp_dir),
        )

        assert result.success is True
        assert result.row_count == 2

    def test_convert_missing_source_file(self, temp_dir, sample_schema):

        missing_file = temp_dir / "missing.tbl"

        converter = DuckLakeConverter()
        with pytest.raises(ConversionError, match="Source file not found"):
            converter.convert(
                source_files=[missing_file],
                table_name="test",
                schema=sample_schema,
            )

    def test_convert_empty_source_list(self, sample_schema):

        converter = DuckLakeConverter()
        with pytest.raises(ConversionError, match="No source files provided"):
            converter.convert(
                source_files=[],
                table_name="test",
                schema=sample_schema,
            )

    def test_convert_invalid_schema(self, temp_dir):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|\n")

        converter = DuckLakeConverter()
        with pytest.raises(SchemaError):
            converter.convert(
                source_files=[tbl_file],
                table_name="test",
                schema={},
            )

    def test_conversion_result_metadata(self, temp_dir, sample_schema):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|100.50|\n2|Bob|200.75|\n")

        converter = DuckLakeConverter()
        result = converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=sample_schema,
            options=ConversionOptions(output_dir=temp_dir),
        )

        assert "format" in result.metadata
        assert result.metadata["format"] == "ducklake"
        assert "partition_cols" in result.metadata
        assert "num_columns" in result.metadata
        assert result.metadata["num_columns"] == 3
        assert "table_path" in result.metadata
        assert "metadata_path" in result.metadata
        assert "data_path" in result.metadata

        assert result.source_size_bytes > 0
        assert result.output_size_bytes > 0
        assert result.compression_ratio > 0

    def test_progress_callback(self, temp_dir, sample_schema):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|100.50|\n")

        progress_updates = []

        def progress_callback(message: str, progress: float):
            progress_updates.append((message, progress))

        converter = DuckLakeConverter()
        converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=sample_schema,
            options=ConversionOptions(output_dir=temp_dir),
            progress_callback=progress_callback,
        )

        assert len(progress_updates) > 0

        assert any("Starting" in msg for msg, _ in progress_updates)
        assert any("complete" in msg.lower() for msg, _ in progress_updates)

    def test_overwrite_existing_table(self, temp_dir, sample_schema):

        tbl_file = temp_dir / "test.tbl"

        tbl_file.write_text("1|Alice|100.50|\n")
        converter = DuckLakeConverter()
        converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=sample_schema,
            options=ConversionOptions(output_dir=temp_dir),
        )

        tbl_file.write_text("2|Bob|200.75|\n3|Charlie|300.00|\n")
        result2 = converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=sample_schema,
            options=ConversionOptions(output_dir=temp_dir),
        )

        assert result2.row_count == 2


class TestDuckLakeTableRead:
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

    def test_read_ducklake_table(self, temp_dir, sample_schema):

        tbl_file = temp_dir / "test.tbl"
        tbl_file.write_text("1|Alice|100.50|\n2|Bob|200.75|\n3|Charlie|300.00|\n")

        converter = DuckLakeConverter()
        result = converter.convert(
            source_files=[tbl_file],
            table_name="test",
            schema=sample_schema,
            options=ConversionOptions(output_dir=temp_dir),
        )

        ducklake_path = result.output_files[0]
        metadata_path = ducklake_path / "metadata.ducklake"
        data_path = ducklake_path / "data"

        conn = duckdb.connect(":memory:")
        conn.execute("LOAD ducklake")

        conn.execute(f"ATTACH 'ducklake:{metadata_path}' AS ducklake_db (DATA_PATH '{data_path}')")

        df = conn.execute("SELECT * FROM ducklake_db.main.test ORDER BY id").fetchdf()
        conn.close()

        assert len(df) == 3
        assert list(df["id"]) == [1, 2, 3]
        assert list(df["name"]) == ["Alice", "Bob", "Charlie"]
