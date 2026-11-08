import os
import uuid
from pathlib import Path

import pytest

from benchbox.core.tpcds.generator import TPCDSDataGenerator
from benchbox.core.tpcds.maintenance_functions import MAINTENANCE_FUNCTIONS
from benchbox.core.tpcds.maintenance_test import (
    TPCDSMaintenanceTest,
    TPCDSMaintenanceTestConfig,
    maintenance_unsupported_reason,
)
from benchbox.core.tpcds.schema import get_create_all_tables_sql
from benchbox.core.tpcds.schema.registry import TABLES
from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection
from benchbox.platforms.base.data_loading import CsvDialect
from benchbox.platforms.singlestore import SingleStoreAdapter

from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_singlestore,
    pytest.mark.slow,
    pytest.mark.tpcds,
]

_HOST_PORT = int(os.getenv("SINGLESTORE_HOST_PORT", "13306"))
_PASSWORD = os.getenv("SINGLESTORE_PASSWORD", "benchbox")
_SCALE_FACTOR = 0.01

_INSERTED_ROWS = {
    "LF_CR": {"catalog_returns": 8},
    "LF_CS": {"catalog_sales": 72},
    "LF_I": {"inventory": 540},
    "LF_SR": {"store_returns": 13},
    "LF_SS": {"store_sales": 144},
    "LF_WR": {"web_returns": 2},
    "LF_WS": {"web_sales": 24},
}
_DELETED_FROM = {
    "DF_CS": ("catalog_returns", "catalog_sales"),
    "DF_SS": ("store_returns", "store_sales"),
    "DF_WS": ("web_returns", "web_sales"),
    "DF_I": ("inventory",),
}


@pytest.fixture(scope="module")
def maintenance_run(tmp_path_factory):
    import singlestoredb as s2

    skip_unless_docker_service("localhost", _HOST_PORT, platform="SingleStore")
    database = f"tpcds_maint_{uuid.uuid4().hex[:8]}"
    admin = s2.connect(host="localhost", port=_HOST_PORT, user="root", password=_PASSWORD)
    admin_cursor = admin.cursor()
    admin_cursor.execute(f"CREATE DATABASE `{database}`")
    adapter = SingleStoreAdapter(
        host="localhost", port=_HOST_PORT, username="root", password=_PASSWORD, database=database
    )
    adapter.skip_database_management = True
    connection = adapter.create_connection()
    try:
        cursor = connection.cursor()
        schema = get_create_all_tables_sql(enable_primary_keys=False, enable_foreign_keys=False)
        for statement in (part for part in schema.split(";") if part.strip()):
            cursor.execute(statement)
        cursor.close()
        dialect = CsvDialect(delimiter="|", has_header=False, null_marker="", normalize_booleans=False, quote=None)
        generated = TPCDSDataGenerator(
            scale_factor=_SCALE_FACTOR, output_dir=tmp_path_factory.mktemp("tpcds_sf001"), quiet=True
        ).generate()
        for table in TABLES:
            for path in generated.get(table.name) or []:
                adapter._load_data_infile(connection, table.name, Path(path), dialect, False)

        def factory():
            wrapper = PlatformAdapterConnection(
                adapter.new_stream_connection(connection), adapter, maintenance_mode=True
            )
            wrapper.benchmark_type = "tpcds"
            wrapper.scale_factor = _SCALE_FACTOR
            return wrapper

        harness = TPCDSMaintenanceTest(
            benchmark=None,
            connection_factory=factory,
            scale_factor=_SCALE_FACTOR,
            dialect=adapter.get_target_dialect(),
        )
        yield (
            adapter,
            harness.run(TPCDSMaintenanceTestConfig(scale_factor=_SCALE_FACTOR, refresh_runs=1, first_refresh_set=1)),
        )
    finally:
        adapter.close_connection(connection)
        admin_cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
        admin_cursor.close()
        admin.close()


def test_adapter_is_not_reported_as_unsupported(maintenance_run):
    adapter, _ = maintenance_run
    assert maintenance_unsupported_reason(adapter) is None


def test_all_eleven_functions_complete(maintenance_run):
    _, result = maintenance_run
    assert result["errors"] == []
    assert result["success"] is True
    assert [op.function for op in result["operations"]] == [function.name for function in MAINTENANCE_FUNCTIONS]
    assert result["failed_operations"] == 0
    assert result["successful_operations"] == 11


@pytest.mark.parametrize("function", sorted(_INSERTED_ROWS))
def test_load_function_inserts_every_staged_row(maintenance_run, function):
    _, result = maintenance_run
    operation = next(op for op in result["operations"] if op.function == function)
    assert operation.success, operation.error
    assert operation.rows_by_table == _INSERTED_ROWS[function]
    assert operation.rows_affected == sum(_INSERTED_ROWS[function].values())


@pytest.mark.parametrize("function", sorted(_DELETED_FROM))
def test_delete_function_removes_rows(maintenance_run, function):
    _, result = maintenance_run
    operation = next(op for op in result["operations"] if op.function == function)
    assert operation.success, operation.error
    assert set(operation.rows_by_table) == set(_DELETED_FROM[function])
    assert all(delta <= 0 for delta in operation.rows_by_table.values())
    assert operation.rows_affected > 0
