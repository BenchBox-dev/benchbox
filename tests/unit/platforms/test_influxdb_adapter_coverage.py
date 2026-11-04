from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.influxdb.adapter import InfluxDBAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def adapter():
    with patch("benchbox.platforms.influxdb.adapter.INFLUXDB_AVAILABLE", True):
        return InfluxDBAdapter(token="token", mode="core", host="localhost", ssl=False)


class TestInfluxAdapterCoverage:
    def test_table_helpers(self, adapter):
        conn = MagicMock()
        conn.execute.return_value = [(7,)]
        assert adapter.get_table_row_count(conn, "cpu") == 7

        conn.execute.return_value = [(None,)]
        assert adapter.get_table_row_count(conn, "cpu") == 0

        conn.execute.return_value = [("cpu",), ("mem",)]
        tables = adapter.get_tables(conn)
        assert tables == ["cpu", "mem"]
        assert adapter.table_exists("cpu", conn) is True
        assert adapter.table_exists("disk", conn) is False

    def test_get_tables_fallback_on_error(self, adapter):
        conn = MagicMock()
        conn.execute.side_effect = ConnectionError("down")
        assert adapter.get_tables(conn) == []

    def test_load_data_skips_without_influxdb3(self, adapter, tmp_path):
        conn = MagicMock()
        with patch("benchbox.platforms.influxdb._dependencies.INFLUXDB3_AVAILABLE", False):
            rows, duration, meta = adapter.load_data(benchmark=None, connection=conn, data_dir=tmp_path)
        assert rows == {}
        assert duration == 0.0
        assert meta and meta["skipped"] is True

    def test_load_data_skips_without_write_table(self, adapter, tmp_path):
        conn = object()
        rows, duration, meta = adapter.load_data(benchmark=None, connection=conn, data_dir=tmp_path)
        assert rows == {}
        assert duration == 0.0
        assert meta and meta["reason"] == "write not supported"

    def test_load_data_happy_path_parses_and_writes(self, adapter, tmp_path):
        csv_content = "time,hostname,usage_user,usage_system\n2026-01-01T00:00:00Z,h1,1.5,2\n"
        (tmp_path / "cpu.csv").write_text(csv_content)
        seen = []

        class Conn:
            def write_table(self, measurement, table, tag_columns, field_columns, timestamp_column, **kwargs):
                seen.append((measurement, table.to_pydict(), tag_columns, timestamp_column))
                return table.num_rows

        with patch("benchbox.platforms.influxdb._dependencies.INFLUXDB3_AVAILABLE", True):
            rows, duration, meta = adapter.load_data(benchmark=None, connection=Conn(), data_dir=tmp_path)

        assert seen == [
            (
                "cpu",
                {"time": ["2026-01-01T00:00:00Z"], "hostname": ["h1"], "usage_user": ["1.5"], "usage_system": ["2"]},
                ["hostname"],
                "time",
            )
        ]
        assert rows["cpu"] == 1
        assert duration >= 0.0
        assert "cpu" in meta["tables_loaded"]

    def test_execute_query_success_and_failure(self, adapter):
        conn = MagicMock()
        conn.execute.return_value = [(1,), (2,)]
        duration, row_count, plan = adapter.execute_query(conn, "SELECT 1", query_id="q1")
        assert duration >= 0
        assert row_count == 2
        assert plan is None

        conn.execute.side_effect = RuntimeError("query failed")
        with pytest.raises(RuntimeError, match="query failed"):
            adapter.execute_query(conn, "SELECT bad", query_id="q2")

    def test_schema_and_noop_config_methods(self, adapter):
        conn = MagicMock()
        assert adapter.create_schema(benchmark=None, connection=conn) == 0.0
        adapter.configure_for_benchmark(conn, "tsbs_devops")
        adapter.apply_platform_optimizations(platform_config=MagicMock(), connection=conn)
        adapter.apply_constraint_configuration(MagicMock(), MagicMock(), conn)

    def test_drop_table_logs_warning(self, adapter, caplog):
        with caplog.at_level(logging.WARNING, logger="InfluxDBAdapter"):
            adapter.drop_table("cpu")
        assert "does not support DROP TABLE" in caplog.text
