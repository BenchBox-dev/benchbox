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

            def execute(self, query):
                return [(1,)]

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
        import pyarrow as pa

        from benchbox.platforms.influxdb.client import InfluxResultCursor

        conn = MagicMock()
        conn.cursor.return_value = InfluxResultCursor(conn)
        conn.execute_arrow.return_value = pa.table({"a": [1, 2]})
        result = adapter.execute_query(conn, "SELECT 1", query_id="q1", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2

        conn.execute_arrow.side_effect = RuntimeError("query failed")
        failed = adapter.execute_query(conn, "SELECT bad", query_id="q2", validate_row_count=False)
        assert failed["status"] == "FAILED"
        assert "query failed" in failed["error"]

    @pytest.mark.parametrize("mode", ["rows", "arrow"])
    def test_execute_query_records_fetch_mode(self, mode):
        import pyarrow as pa

        from benchbox.platforms.influxdb.adapter import InfluxDBAdapter
        from benchbox.platforms.influxdb.client import InfluxResultCursor

        with patch("benchbox.platforms.influxdb.adapter.INFLUXDB_AVAILABLE", True):
            influx = InfluxDBAdapter(host="localhost", token="t", fetch_mode=mode)
        conn = MagicMock()
        conn.cursor.return_value = InfluxResultCursor(conn)
        conn.execute_arrow.return_value = pa.table({"a": [1, 2], "b": ["x", "y"]})
        result = influx.execute_query(conn, "SELECT a, b FROM t", query_id="q1", validate_row_count=False)
        assert influx.fetch_mode == mode
        assert result["fetch_mode"] == mode
        assert result["rows_returned"] == 2
        assert result["first_row"] == (1, "x")

    def test_client_rows_and_arrow_agree(self):
        import pyarrow as pa

        from benchbox.platforms.influxdb.client import InfluxDBConnection as Client

        client = Client(host="localhost", token="t", database="d")
        client._client_type = "influxdb3"
        client._client = MagicMock()
        client._client.query.return_value = pa.table({"a": [1, 2], "b": ["x", "y"]})
        assert client.execute("SELECT 1") == [(1, "x"), (2, "y")]
        assert client.execute_arrow("SELECT 1").num_rows == 2

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


