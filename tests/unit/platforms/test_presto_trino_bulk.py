import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.base.data_loading import CsvDialect
from benchbox.platforms.presto import PrestoAdapter
from benchbox.platforms.presto_trino_bulk import (
    STAGE_DIR_NAME,
    BulkLoadSettings,
    _external_parquet_type,
    _parquet_projection,
    _table_properties,
    _text_projection,
    bulk_load_table,
    ineligibility_reason,
    remove_stage_root,
    source_row_count,
    stage_files,
)
from benchbox.platforms.trino import TrinoAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestIneligibilityReason:
    def test_eligible_csv(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("1,alpha\n2,beta\n", encoding="utf-8")
        columns = [("id", "integer"), ("name", "varchar")]
        dialect = CsvDialect(
            delimiter=",", has_header=False, null_marker=None, normalize_booleans=False, quote=None, encoding="utf-8"
        )
        assert ineligibility_reason(columns, [csv_file], dialect) is None

    def test_ineligible_unsupported_column_type(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("1\n", encoding="utf-8")
        columns = [("col", "unsupported_custom_type")]
        dialect = CsvDialect(
            delimiter=",", has_header=False, null_marker=None, normalize_booleans=False, quote=None, encoding="utf-8"
        )
        reason = ineligibility_reason(columns, [csv_file], dialect)
        assert reason is not None
        assert "cannot back an external Hive table" in reason

    def test_ineligible_timestamp_with_time_zone(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("1\n", encoding="utf-8")
        columns = [("ts", "timestamp with time zone")]
        dialect = CsvDialect(
            delimiter=",", has_header=False, null_marker=None, normalize_booleans=False, quote=None, encoding="utf-8"
        )
        reason = ineligibility_reason(columns, [csv_file], dialect)
        assert reason is not None
        assert "cannot back an external Hive table" in reason

    def test_ineligible_varbinary_in_delimited_text(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("1,ff00\n", encoding="utf-8")
        dialect = CsvDialect(
            delimiter=",", has_header=False, null_marker=None, normalize_booleans=False, quote=None, encoding="utf-8"
        )
        reason = ineligibility_reason([("id", "integer"), ("payload", "varbinary")], [csv_file], dialect)
        assert reason is not None
        assert "base64" in reason

    def test_ineligible_quoted_csv(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text('"1","alpha"\n', encoding="utf-8")
        columns = [("id", "integer"), ("name", "varchar")]
        dialect = CsvDialect(
            delimiter=",", has_header=False, null_marker=None, normalize_booleans=False, quote='"', encoding="utf-8"
        )
        reason = ineligibility_reason(columns, [csv_file], dialect)
        assert reason is not None
        assert "quoted CSV fields" in reason

    def test_ineligible_multi_char_delimiter(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("1||alpha\n", encoding="utf-8")
        columns = [("id", "integer"), ("name", "varchar")]
        dialect = CsvDialect(
            delimiter="||", has_header=False, null_marker=None, normalize_booleans=False, quote=None, encoding="utf-8"
        )
        reason = ineligibility_reason(columns, [csv_file], dialect)
        assert reason is not None
        assert "not a single character" in reason

    def test_ineligible_non_utf8_encoding(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_bytes(b"1,alpha\n")
        columns = [("id", "integer"), ("name", "varchar")]
        dialect = CsvDialect(
            delimiter=",", has_header=False, null_marker=None, normalize_booleans=False, quote=None, encoding="latin-1"
        )
        reason = ineligibility_reason(columns, [csv_file], dialect)
        assert reason is not None
        assert "not UTF-8" in reason

    def test_ineligible_custom_null_format_without_property(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("1,alpha\n", encoding="utf-8")
        columns = [("id", "integer"), ("name", "varchar")]
        dialect = CsvDialect(
            delimiter=",", has_header=False, null_marker="", normalize_booleans=False, quote=None, encoding="utf-8"
        )
        settings = BulkLoadSettings(
            source_catalog="hive",
            native_compression=frozenset({".gz"}),
            null_format_property=None,
        )
        reason = ineligibility_reason(columns, [csv_file], dialect, settings=settings)
        assert reason is not None
        assert "custom null format" in reason


class TestTableProperties:
    def test_textfile_trino_properties(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("1,alpha\n", encoding="utf-8")
        settings = BulkLoadSettings(
            source_catalog="hive",
            native_compression=frozenset({".gz"}),
            null_format_property="null_format",
            field_delimiter_property="textfile_field_separator",
        )
        dialect = CsvDialect(
            delimiter=",", has_header=True, null_marker="\\N", normalize_booleans=False, quote=None, encoding="utf-8"
        )
        props = _table_properties(tmp_path, [csv_file], dialect, settings)
        assert f"external_location = 'file://{tmp_path}'" in props
        assert "format = 'TEXTFILE'" in props
        assert "textfile_field_separator = ','" in props
        assert "skip_header_line_count = 1" in props
        assert "null_format = '\\N'" in props

    def test_textfile_presto_properties(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("1,alpha\n", encoding="utf-8")
        settings = BulkLoadSettings(
            source_catalog="hive",
            native_compression=frozenset({".gz"}),
            null_format_property=None,
            field_delimiter_property="textfile_field_delim",
        )
        dialect = CsvDialect(
            delimiter="|", has_header=False, null_marker="", normalize_booleans=False, quote=None, encoding="utf-8"
        )
        props = _table_properties(tmp_path, [csv_file], dialect, settings)
        assert "format = 'TEXTFILE'" in props
        assert "textfile_field_delim = '|'" in props
        assert "null_format" not in props
        assert "skip_header_line_count" not in props

    def test_parquet_properties(self, tmp_path):
        pq_file = tmp_path / "test.parquet"
        pq_file.write_bytes(b"PAR1")
        settings = BulkLoadSettings(
            source_catalog="hive",
            native_compression=frozenset({".gz"}),
            null_format_property="null_format",
        )
        props = _table_properties(tmp_path, [pq_file], None, settings)
        assert "format = 'PARQUET'" in props


class TestStageFiles:
    def test_stage_files_links_or_copies_raw_file(self, tmp_path):
        src = tmp_path / "data.csv"
        src.write_text("1,foo\n2,bar\n", encoding="utf-8")
        stage_dir = tmp_path / "stage"
        staged = stage_files([src], stage_dir, frozenset({".gz"}))
        assert len(staged) == 1
        assert staged[0].exists()
        assert staged[0].name == "0_data.csv"
        assert staged[0].read_text(encoding="utf-8") == "1,foo\n2,bar\n"

    def test_stage_files_unique_names_across_dirs(self, tmp_path):
        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        dir_a.mkdir()
        dir_b.mkdir()
        f1 = dir_a / "part-0.csv"
        f2 = dir_b / "part-0.csv"
        f1.write_text("1,a\n", encoding="utf-8")
        f2.write_text("2,b\n", encoding="utf-8")
        stage_dir = tmp_path / "stage"
        staged = stage_files([f1, f2], stage_dir, frozenset({".gz"}))
        assert len(staged) == 2
        assert staged[0].name == "0_part-0.csv"
        assert staged[1].name == "1_part-0.csv"
        assert staged[0].read_text(encoding="utf-8") == "1,a\n"
        assert staged[1].read_text(encoding="utf-8") == "2,b\n"

    def test_remove_stage_root(self, tmp_path):
        stage_root = tmp_path / STAGE_DIR_NAME
        stage_root.mkdir()
        assert stage_root.exists()
        remove_stage_root(tmp_path)
        assert not stage_root.exists()
        remove_stage_root(tmp_path)


class TestBulkLoadTable:
    def test_bulk_load_table_successful_flow(self, tmp_path):
        csv_file = tmp_path / "orders.csv"
        csv_file.write_text("1,100\n2,200\n", encoding="utf-8")
        cursor = MagicMock()
        cursor.fetchall.side_effect = [[], [], [], [(2,)], [(2,)], []]

        settings = BulkLoadSettings(
            source_catalog="hive",
            native_compression=frozenset({".gz"}),
            null_format_property="null_format",
        )
        logger = logging.getLogger("test")
        rows = bulk_load_table(
            cursor,
            settings=settings,
            target_table="memory.default.orders",
            ext_name="default_orders",
            columns=[("id", "integer"), ("amount", "integer")],
            files=[csv_file],
            dialect=None,
            logger=logger,
        )
        assert rows == 2
        calls = [call.args[0] for call in cursor.execute.call_args_list]
        assert any("CREATE SCHEMA IF NOT EXISTS hive.benchbox_stage" in c for c in calls)
        create = next(c for c in calls if "CREATE TABLE hive.benchbox_stage.default_orders" in c)
        assert '("id" varchar, "amount" varchar)' in create
        insert = next(c for c in calls if c.startswith("INSERT INTO memory.default.orders SELECT "))
        assert 'CAST(trim("id") AS integer)' in insert
        assert insert.endswith("FROM hive.benchbox_stage.default_orders")
        assert any("DROP TABLE IF EXISTS hive.benchbox_stage.default_orders" in c for c in calls)

    def test_text_projection_casts_trims_and_maps_null_tokens(self):
        expression = _text_projection("qty", "integer", "")
        assert expression == (
            'CASE WHEN "qty" IS NULL OR trim("qty") = \'\' OR lower(trim("qty")) = \'null\' '
            'THEN NULL ELSE CAST(trim("qty") AS integer) END'
        )

    def test_text_projection_fails_on_values_longer_than_varchar_n(self):
        expression = _text_projection("name", "varchar(25)", "")
        assert expression.startswith('CASE WHEN length("name") > 25 THEN CAST(fail(')
        assert expression.endswith('ELSE "name" END')

    def test_parquet_reads_bounded_varchar_unbounded_and_checks_the_length(self):
        assert _external_parquet_type("varchar(25)") == "varchar"
        assert _external_parquet_type("integer") == "integer"
        assert _parquet_projection("name", "varchar(25)").startswith('CASE WHEN length("name") > 25 THEN CAST(fail(')
        assert _parquet_projection("id", "integer") == '"id"'

    def test_text_projection_keeps_unbounded_varchar_raw(self):
        assert _text_projection("comment", "varchar", "") == '"comment"'

    def test_text_projection_maps_boolean_spellings(self):
        expression = _text_projection("flag", "boolean", None)
        assert "IN ('true', 't', '1') THEN true" in expression
        assert "IN ('false', 'f', '0') THEN false" in expression

    def test_bulk_load_refuses_when_the_external_table_reads_fewer_rows_than_the_files_hold(self, tmp_path):
        csv_file = tmp_path / "orders.csv"
        csv_file.write_text("1,100\n2,200\n3,300\n", encoding="utf-8")
        cursor = MagicMock()
        cursor.fetchall.side_effect = [[], [], [], [(2,)], []]
        settings = BulkLoadSettings(
            source_catalog="hive", native_compression=frozenset({".gz"}), null_format_property="null_format"
        )
        with pytest.raises(RuntimeError, match="reads 2 row\\(s\\).*staged files hold 3"):
            bulk_load_table(
                cursor,
                settings=settings,
                target_table="memory.default.orders",
                ext_name="default_orders",
                columns=[("id", "integer"), ("amount", "integer")],
                files=[csv_file],
                dialect=None,
                logger=logging.getLogger("test"),
            )
        calls = [call.args[0] for call in cursor.execute.call_args_list]
        assert not any(c.startswith("INSERT INTO") for c in calls)


class TestSourceRowCount:
    def test_counts_lines_including_a_last_line_without_newline(self, tmp_path):
        path = tmp_path / "t.tbl"
        path.write_bytes(b"1|a|\n2|b|\n3|c|")
        assert source_row_count([path], None) == 3

    def test_subtracts_the_header_per_file(self, tmp_path):
        first, second = tmp_path / "a.csv", tmp_path / "b.csv"
        first.write_text("id,v\n1,2\n2,3\n", encoding="utf-8")
        second.write_text("id,v\n3,4\n", encoding="utf-8")
        dialect = CsvDialect(
            delimiter=",", has_header=True, null_marker=None, normalize_booleans=False, quote=None, encoding="utf-8"
        )
        assert source_row_count([first, second], dialect) == 3

    def test_counts_gzip_content(self, tmp_path):
        import gzip

        path = tmp_path / "t.tbl.gz"
        path.write_bytes(gzip.compress(b"1|a|\n2|b|\n"))
        assert source_row_count([path], None) == 2

    def test_uses_parquet_metadata(self, tmp_path):
        import pyarrow as pa
        import pyarrow.parquet as pq

        path = tmp_path / "t.parquet"
        pq.write_table(pa.table({"id": [1, 2, 3, 4]}), path)
        assert source_row_count([path], None) == 4

    def test_bulk_load_skips_ineligible(self, tmp_path):
        csv_file = tmp_path / "orders.csv"
        csv_file.write_text('"1","100"\n', encoding="utf-8")
        cursor = MagicMock()
        settings = BulkLoadSettings(
            source_catalog="hive",
            native_compression=frozenset({".gz"}),
            null_format_property="null_format",
        )
        logger = logging.getLogger("test")
        dialect = CsvDialect(
            delimiter=",", has_header=False, null_marker=None, normalize_booleans=False, quote='"', encoding="utf-8"
        )
        rows = bulk_load_table(
            cursor,
            settings=settings,
            target_table="memory.default.orders",
            ext_name="default_orders",
            columns=[("id", "integer"), ("amount", "integer")],
            files=[csv_file],
            dialect=dialect,
            logger=logger,
        )
        assert rows is None
        assert cursor.execute.call_count == 0


class TestAdapterBulkLoadWiring:
    def test_adapter_properties(self):
        trino = TrinoAdapter(host="localhost", user="test", catalog="memory", schema="default")
        assert trino.bulk_field_delimiter_property == "textfile_field_separator"
        assert trino.bulk_null_format_property == "null_format"
        assert ".zst" in trino.bulk_native_compression

        presto = PrestoAdapter(host="localhost", user="test", catalog="memory", schema="default")
        assert presto.bulk_field_delimiter_property == "textfile_field_delim"
        assert presto.bulk_null_format_property is None
        assert presto.bulk_native_compression == frozenset({".gz"})

    def test_adapter_uses_bulk_load_when_source_catalog_configured(self, tmp_path):
        adapter = TrinoAdapter(
            host="localhost",
            user="test",
            catalog="memory",
            schema="default",
            source_catalog="hive",
        )
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("1,val\n", encoding="utf-8")

        cursor = MagicMock()
        cursor.fetchall.return_value = [("id", "integer"), ("val", "varchar")]

        with patch("benchbox.platforms.presto_trino_adapter_base.bulk_load_table", return_value=1) as mock_bulk:
            loaded = adapter._load_table_data(
                cursor,
                "test_table",
                [csv_file],
                "memory",
                "default",
            )
            assert loaded == (1, 1)
            mock_bulk.assert_called_once()
            settings = mock_bulk.call_args.kwargs["settings"]
            assert settings.source_catalog == "hive"

    def test_adapter_load_data_cleans_stage_root(self, tmp_path):
        adapter = TrinoAdapter(
            host="localhost",
            user="test",
            catalog="memory",
            schema="default",
            source_catalog="hive",
        )
        mock_conn = MagicMock()
        mock_benchmark = MagicMock()
        mock_benchmark.tables = {}

        stage_root = tmp_path / STAGE_DIR_NAME
        stage_root.mkdir()
        assert stage_root.exists()

        with patch("benchbox.platforms.presto_trino_adapter_base.resolve_adapter_data_source") as mock_resolve:
            mock_ds = MagicMock()
            mock_ds.tables = {}
            mock_resolve.return_value = mock_ds
            adapter.load_data(mock_benchmark, mock_conn, tmp_path)

        assert not stage_root.exists()
