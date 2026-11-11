from __future__ import annotations

import pytest

from benchbox.core.results.maintenance_method import MAINTENANCE_METHOD
from benchbox.core.tpcds.benchmark import MaintenanceTestConfig, MaintenanceTestResult, TPCDSBenchmark
from benchbox.core.tpcds.maintenance_functions import MAINTENANCE_FUNCTIONS_BY_NAME
from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTest, TPCDSMaintenanceTestConfig
from benchbox.core.tpcds.refresh_data import generate_refresh_sets
from benchbox.core.tpcds.schema import get_create_all_tables_sql

duckdb = pytest.importorskip("duckdb")

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.tpcds]


class _SessionConnection:
    def __init__(self, raw):
        self._raw = raw
        self.closes = 0

    def execute(self, sql):
        return self._raw.execute(sql)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self.closes += 1


@pytest.fixture(scope="module")
def refresh_set(tmp_path_factory):
    sets = generate_refresh_sets(0.01, 1, 1, tmp_path_factory.mktemp("tpcds-refresh") / "dsdgen")
    return sets[1]


@pytest.fixture
def empty_database():
    raw = duckdb.connect()
    raw.execute(get_create_all_tables_sql(enable_primary_keys=True, enable_foreign_keys=False))
    yield raw
    raw.close()


def _staging_tables(raw):
    return [
        row[0]
        for row in raw.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 's\\_%' ESCAPE '\\'"
        ).fetchall()
    ]


def test_delete_function_loads_stage_inside_interval_and_drops_it(empty_database, refresh_set):
    session = _SessionConnection(empty_database)
    test = TPCDSMaintenanceTest(benchmark=None, connection_factory=lambda: session, scale_factor=0.01)

    operation = test._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["DF_SS"], refresh_set)

    assert operation.success is True, operation.error
    assert operation.function == "DF_SS"
    assert operation.refresh_set == 1
    assert operation.staged_rows == {"s_delete": refresh_set.row_counts["s_delete"]}
    assert operation.rows_by_table == {"store_returns": 0, "store_sales": 0}
    assert operation.statements_executed == 2
    assert operation.end_time >= operation.start_time > 0
    assert operation.duration == pytest.approx(operation.end_time - operation.start_time)
    assert session.closes == 1
    assert _staging_tables(empty_database) == []


def test_failed_insert_function_rolls_back_and_cleans_staging(empty_database, refresh_set):
    session = _SessionConnection(empty_database)
    test = TPCDSMaintenanceTest(benchmark=None, connection_factory=lambda: session, scale_factor=0.01)

    operation = test._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["LF_CR"], refresh_set)

    assert operation.success is False
    assert "catalog_returns" in operation.error
    assert operation.end_time >= operation.start_time > 0
    assert operation.rows_by_table == {}
    assert empty_database.execute("SELECT COUNT(*) FROM catalog_returns").fetchone() == (0,)
    assert session.closes == 1
    assert _staging_tables(empty_database) == []


def test_run_against_empty_database_executes_deletes_then_stops_at_first_failed_load_function(empty_database):
    session = _SessionConnection(empty_database)
    test = TPCDSMaintenanceTest(benchmark=None, connection_factory=lambda: session, scale_factor=0.01)

    result = test.run(TPCDSMaintenanceTestConfig(scale_factor=0.01))

    assert result["maintenance_method"] == MAINTENANCE_METHOD
    assert result["refresh_sets"] == [1]
    assert [op.function for op in result["operations"]] == ["DF_CS", "DF_SS", "DF_WS", "DF_I", "LF_CR"]
    assert [op.success for op in result["operations"]] == [True, True, True, True, False]
    assert result["delete_operations"] == 4
    assert result["insert_operations"] == 1
    assert result["success"] is False
    assert result["integrity_valid"] is None
    assert len(result["errors"]) == 1
    assert result["errors"][0].startswith("LF_CR on refresh set 1 failed:")
    assert _staging_tables(empty_database) == []


def test_benchmark_run_maintenance_test_reports_method_and_function_rows(empty_database, tmp_path):
    session = _SessionConnection(empty_database)
    benchmark = TPCDSBenchmark(scale_factor=0.01, output_dir=tmp_path / "tpcds")
    config = MaintenanceTestConfig(scale_factor=0.01, refresh_runs=1, first_refresh_set=1)

    result = benchmark.run_maintenance_test(connection=session, config=config, dialect="duckdb")

    assert isinstance(result, MaintenanceTestResult)
    assert result.maintenance_method == MAINTENANCE_METHOD
    assert result.total_operations == 5
    assert result.successful_operations == 4
    assert result.failed_operations == 1
    assert [row["operation"] for row in result.maintenance_operations] == ["DF_CS", "DF_SS", "DF_WS", "DF_I", "LF_CR"]
    assert result.maintenance_operations[-1]["success"] is False
    assert result.maintenance_operations[-1]["error"]
    assert result.test_duration > 0
    assert result.error_details


