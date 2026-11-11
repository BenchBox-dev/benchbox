# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.platforms.clickhouse.workload import ClickHouseWorkloadMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class StubConnection:
    def __init__(self, functions: list[str], engines: list[str]) -> None:
        self._functions = functions
        self._engines = engines
        self.queries: list[str] = []

    def execute(self, sql: str) -> list[tuple[str]]:
        self.queries.append(sql)
        if "system.table_functions" in sql:
            return [(name,) for name in self._functions]
        if "system.table_engines" in sql:
            return [(name,) for name in self._engines]
        raise AssertionError(f"unexpected query: {sql}")


@pytest.fixture
def mixin() -> ClickHouseWorkloadMixin:
    return ClickHouseWorkloadMixin()


class TestDeltaNativeRegistration:
    def test_true_on_capable_server(self, mixin: ClickHouseWorkloadMixin) -> None:
        connection = StubConnection(["deltaLake", "deltaLakeS3"], ["DeltaLake", "MergeTree"])
        assert mixin.delta_native_registration(connection) is True
        assert len(connection.queries) == 2

    def test_false_without_base_function(self, mixin: ClickHouseWorkloadMixin) -> None:
        connection = StubConnection(["deltaLakeS3"], ["DeltaLake"])
        assert mixin.delta_native_registration(connection) is False

    def test_false_without_engine(self, mixin: ClickHouseWorkloadMixin) -> None:
        connection = StubConnection(["deltaLake"], ["MergeTree"])
        assert mixin.delta_native_registration(connection) is False


class TestDeltaReaderFor:
    def test_native_for_s3_on_capable_server(self, mixin: ClickHouseWorkloadMixin) -> None:
        connection = StubConnection(["deltaLake"], ["DeltaLake"])
        reader = mixin.delta_reader_for(connection, "s3://bucket/orders")
        assert reader.kind == "native"
        assert reader.source_sql == "deltaLake('s3://bucket/orders')"

    def test_native_for_https_s3_url(self, mixin: ClickHouseWorkloadMixin) -> None:
        connection = StubConnection(["deltaLake"], ["DeltaLake"])
        reader = mixin.delta_reader_for(
            connection, "https://clickhouse-public-datasets.s3.amazonaws.com/delta_lake/hits/"
        )
        assert (reader.kind, reader.location_kind) == ("native", "s3")

    def test_remote_without_native_reads_raises(self, mixin: ClickHouseWorkloadMixin) -> None:
        connection = StubConnection([], [])
        with pytest.raises(ValueError, match="No executable Delta read path"):
            mixin.delta_reader_for(connection, "s3://bucket/orders")

    def test_local_requires_local_alias(self, mixin: ClickHouseWorkloadMixin) -> None:
        base_only = StubConnection(["deltaLake"], ["DeltaLake"])
        reader = mixin.delta_reader_for(base_only, "/data/orders")
        assert reader.kind == "parquet-snapshot"

        with_alias = StubConnection(["deltaLake", "deltaLakeLocal"], ["DeltaLake"])
        reader = mixin.delta_reader_for(with_alias, "/data/orders")
        assert reader.kind == "native"
        assert reader.source_sql == "deltaLakeLocal('/data/orders')"

    def test_unknown_location_raises(self, mixin: ClickHouseWorkloadMixin) -> None:
        connection = StubConnection(["deltaLake"], ["DeltaLake"])
        with pytest.raises(ValueError, match="Unrecognized Delta location"):
            mixin.delta_reader_for(connection, "hdfs://namenode/table")


class RecordingConnection(StubConnection):
    def __init__(
        self,
        functions: list[str],
        engines: list[str],
        *,
        counts: list[int] | None = None,
    ) -> None:
        super().__init__(functions, engines)
        self.statements: list[str] = []
        self._counts = list(counts or [0, 0])

    def execute(self, sql: str) -> list[tuple]:
        if "system.table_functions" in sql or "system.table_engines" in sql:
            return super().execute(sql)
        self.statements.append(sql)
        if sql.strip().upper().startswith("SELECT COUNT(*)"):
            return [(self._counts.pop(0),)] if self._counts else [(0,)]
        return []


def _write_delta_dir(path) -> None:

    import pyarrow as pa
    from deltalake.writer import write_deltalake

    write_deltalake(
        str(path),
        pa.table({"id": [1, 2, 3], "v": ["a", "b", "c"]}),
        mode="overwrite",
    )


class TestClickHouseDeltaHandlerDispatch:
    def test_delta_dir_selects_delta_handler(self, mixin: ClickHouseWorkloadMixin, tmp_path) -> None:
        from benchbox.platforms.clickhouse.workload import ClickHouseDeltaHandler, _clickhouse_handler_for

        delta_dir = tmp_path / "orders"
        _write_delta_dir(delta_dir)
        handler = _clickhouse_handler_for(delta_dir, mixin, benchmark_instance=None)
        assert isinstance(handler, ClickHouseDeltaHandler)

    def test_non_delta_paths_keep_extension_dispatch(self, mixin: ClickHouseWorkloadMixin, tmp_path) -> None:
        from benchbox.platforms.base.data_loading import ClickHouseNativeHandler
        from benchbox.platforms.clickhouse.workload import _clickhouse_handler_for

        parquet_file = tmp_path / "orders.parquet"
        parquet_file.write_bytes(b"PAR1")
        handler = _clickhouse_handler_for(parquet_file, mixin, benchmark_instance=None)
        assert isinstance(handler, ClickHouseNativeHandler)
        assert _clickhouse_handler_for(tmp_path / "orders.unknown", mixin, benchmark_instance=None) is None


class TestClickHouseDeltaHandlerLoad:
    def test_native_load_issues_insert_select(self, mixin: ClickHouseWorkloadMixin, tmp_path) -> None:
        import logging

        from benchbox.platforms.clickhouse.workload import ClickHouseDeltaHandler

        delta_dir = tmp_path / "orders"
        _write_delta_dir(delta_dir)
        connection = RecordingConnection(["deltaLake", "deltaLakeLocal"], ["DeltaLake"], counts=[0, 3])
        handler = ClickHouseDeltaHandler(mixin, None)

        loaded = handler.load_table("orders", delta_dir, connection, None, logging.getLogger())

        assert loaded == 3
        inserts = [s for s in connection.statements if s.strip().upper().startswith("INSERT INTO")]
        assert len(inserts) == 1
        assert "deltaLakeLocal(" in inserts[0]

    def test_snapshot_load_exports_then_loads_parquet(self, mixin: ClickHouseWorkloadMixin, tmp_path) -> None:
        import logging

        from benchbox.platforms.clickhouse.workload import ClickHouseDeltaHandler

        delta_dir = tmp_path / "orders"
        _write_delta_dir(delta_dir)

        connection = RecordingConnection(["deltaLake"], ["DeltaLake"], counts=[0, 3])
        handler = ClickHouseDeltaHandler(mixin, None)

        loaded = handler.load_table("orders", delta_dir, connection, None, logging.getLogger())

        assert loaded == 3
        inserts = [s for s in connection.statements if s.strip().upper().startswith("INSERT INTO")]
        assert len(inserts) == 1
        assert ".parquet" in inserts[0]


