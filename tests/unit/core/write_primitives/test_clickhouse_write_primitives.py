from __future__ import annotations

import pytest

from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark
from benchbox.core.write_primitives.schema import get_create_table_sql

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture()
def wp(tmp_path):
    return WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)


@pytest.mark.parametrize(
    "operation_id",
    [
        "merge_simple_upsert_small",
        "bulk_load_csv_small_uncompressed",
        "bulk_load_upsert_mode",
        "insert_on_conflict_ignore",
        "update_returning",
        "update_with_join",
        "delete_with_not_exists",
        "ddl_drop_index",
    ],
)
def test_unsupported_operations_are_skipped_with_the_engine_error(wp, operation_id):
    sql, reason = wp._get_effective_write_sql(wp.get_operation(operation_id), platform_key="clickhouse")
    assert sql is None
    assert reason.startswith(f"Operation '{operation_id}' is skipped on ClickHouse: ClickHouse 25.8.33.6")
    assert "Code" in reason


def test_supported_operations_are_not_skipped(wp):
    for operation_id in (
        "insert_single_row",
        "update_single_row_pk",
        "delete_single_row_pk",
        "ddl_alter_table_add_column",
    ):
        sql, reason = wp._get_effective_write_sql(wp.get_operation(operation_id), platform_key="clickhouse")
        assert reason is None
        assert sql


def test_staging_ddl_declares_a_merge_tree_and_nullable_columns():
    sql = get_create_table_sql("batch_metadata", "clickhouse", if_not_exists=True)
    assert sql.rstrip(";").endswith("ENGINE = MergeTree ORDER BY (batch_id)")
    assert "file_path Nullable(VARCHAR(500))" in sql
    assert "operation_id VARCHAR(100) NOT NULL" in sql
    assert "ENGINE" not in get_create_table_sql("batch_metadata", "duckdb")


class _ProbeConnection:
    def __init__(self, counts):
        self.counts = counts
        self.executed = []

    def execute(self, sql, *args, **kwargs):
        self.executed.append(sql)
        for table, count in self.counts.items():
            if f'"{table}"' in sql and "COUNT(*)" in sql:
                return _Rows([(count,)])
        return _Rows([(1,)])


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


def test_staging_probe_counts_rows_instead_of_scanning_for_one_on_clickhouse(wp, monkeypatch):
    wp._setup_dialect = "clickhouse"
    monkeypatch.setattr(wp, "_table_exists", lambda _c, _t: True)
    monkeypatch.setattr(wp, "_staging_manifest_matches", lambda _c, _t: True)
    conn = _ProbeConnection({})
    assert wp.is_setup(conn) is True
    probes = [sql for sql in conn.executed if "_ops_" in sql or "ddl_truncate_target" in sql]
    assert probes
    assert all(sql.startswith("SELECT COUNT(*) FROM") for sql in probes)
    assert not any("LIMIT 1" in sql for sql in probes)


def test_staging_probe_reports_an_emptied_clickhouse_table_as_not_setup(wp, monkeypatch):
    wp._setup_dialect = "clickhouse"
    monkeypatch.setattr(wp, "_table_exists", lambda _c, _t: True)
    monkeypatch.setattr(wp, "_staging_manifest_matches", lambda _c, _t: True)
    assert wp.is_setup(_ProbeConnection({"delete_ops_lineitem": 0})) is False


def test_staging_probe_keeps_limit_one_on_other_dialects(wp, monkeypatch):
    wp._setup_dialect = "duckdb"
    monkeypatch.setattr(wp, "_table_exists", lambda _c, _t: True)
    monkeypatch.setattr(wp, "_staging_manifest_matches", lambda _c, _t: True)
    conn = _ProbeConnection({})
    assert wp.is_setup(conn) is True
    assert any(sql.endswith("LIMIT 1") for sql in conn.executed)
