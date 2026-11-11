from __future__ import annotations

import pytest

from benchbox.platforms.datafusion_write_transformer import transform_write_sql

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestTransformWriteSql:
    def test_non_bulk_load_sql_is_unchanged(self):
        sql = "INSERT INTO bulk_load_ops_target VALUES (1)"

        result = transform_write_sql("insert_single_row", "insert", sql)

        assert result == sql

    def test_copy_based_csv_bulk_load_rewrites_to_external_table(self):
        sql = """
        TRUNCATE TABLE bulk_load_ops_target;
        COPY bulk_load_ops_target FROM '/tmp/input.csv.gz'
        WITH (DELIMITER '|', QUOTE '"', ESCAPE '"', DATEFORMAT '%Y/%m/%d', NULL 'NULL')
        """

        result = transform_write_sql("bulk_load_csv_small", "bulk_load", sql)

        assert "TRUNCATE TABLE" not in result
        assert "DROP TABLE IF EXISTS bb_df_ext_bulk_load_csv_small" in result
        assert "CREATE EXTERNAL TABLE bb_df_ext_bulk_load_csv_small STORED AS CSV" in result
        assert "LOCATION '/tmp/input.csv.gz'" in result
        assert "'has_header' 'true'" in result
        assert "'compression' 'gzip'" in result
        assert "'delimiter' '|'" in result
        assert "'quote' '\"'" in result
        assert "'escape' '\"'" in result
        assert "'date_format' '%Y/%m/%d'" in result
        assert "'null_value' 'NULL'" in result
        assert "INSERT INTO bulk_load_ops_target SELECT * FROM bb_df_ext_bulk_load_csv_small" in result

    def test_copy_based_parquet_bulk_load_uses_parquet_storage(self):
        sql = "COPY bulk_load_ops_target FROM '/tmp/input.parquet' (FORMAT PARQUET)"

        result = transform_write_sql("bulk_load_parquet", "bulk_load", sql)

        assert "CREATE EXTERNAL TABLE bb_df_ext_bulk_load_parquet STORED AS PARQUET" in result
        assert "OPTIONS (" not in result

    def test_parallel_multi_file_bulk_load_builds_union_all_insert(self):
        result = transform_write_sql(
            "bulk_load_parallel_multi_file",
            "bulk_load",
            "TRUNCATE TABLE bulk_load_ops_target;",
            ["part-000.csv", "part-001.csv"],
        )

        assert "CREATE EXTERNAL TABLE bb_df_ext_bulk_load_parallel_multi_file_0" in result
        assert "CREATE EXTERNAL TABLE bb_df_ext_bulk_load_parallel_multi_file_1" in result
        assert "LOCATION '{file_path}/part-000.csv'" in result
        assert "LOCATION '{file_path}/part-001.csv'" in result
        assert "UNION ALL" in result

    def test_read_csv_auto_rewrite_replaces_scan_and_strips_truncate(self):
        sql = """
        TRUNCATE TABLE bulk_load_ops_target;
        INSERT INTO bulk_load_ops_target
        SELECT * FROM read_csv_auto('/tmp/input.csv')
        """

        result = transform_write_sql("bulk_load_csv_auto", "bulk_load", sql)

        assert "TRUNCATE TABLE" not in result
        assert "CREATE EXTERNAL TABLE bb_df_ext_bulk_load_csv_auto STORED AS CSV" in result
        assert "LOCATION '/tmp/input.csv'" in result
        assert "FROM bb_df_ext_bulk_load_csv_auto" in result

    def test_unrecognized_bulk_load_sql_falls_back_to_non_truncate_statements(self):
        sql = """
        TRUNCATE TABLE bulk_load_ops_target;
        INSERT INTO bulk_load_ops_target SELECT 1
        """

        result = transform_write_sql("bulk_load_fallback", "bulk_load", sql)

        assert result.strip() == "INSERT INTO bulk_load_ops_target SELECT 1"


