from __future__ import annotations

import pytest

from benchbox.core.tpcds.maintenance_functions import MAINTENANCE_FUNCTIONS, MAINTENANCE_FUNCTIONS_BY_NAME
from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTest, maintenance_unsupported_reason
from benchbox.platforms.singlestore import SingleStoreAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

RETURN_FUNCTIONS_WITH_TIME_PARSING = ("LF_SR", "LF_WR", "LF_CR")


def test_singlestore_adapter_runs_tpcds_maintenance() -> None:
    assert not hasattr(SingleStoreAdapter, "tpcds_maintenance_unsupported_reason")
    assert SingleStoreAdapter.supports_refresh_functions is True
    adapter = SingleStoreAdapter.__new__(SingleStoreAdapter)
    assert maintenance_unsupported_reason(adapter) is None


@pytest.mark.parametrize("dialect", ["singlestore", "mysql", "SingleStore"])
def test_mysql_wire_dialects_cast_to_signed(dialect: str) -> None:
    for name in RETURN_FUNCTIONS_WITH_TIME_PARSING:
        statements = MAINTENANCE_FUNCTIONS_BY_NAME[name].statements_for(dialect)
        assert all("AS INTEGER)" not in statement for statement in statements)
        assert any("AS SIGNED)" in statement for statement in statements)


@pytest.mark.parametrize("dialect", [None, "duckdb", "postgres", "snowflake"])
def test_other_dialects_keep_integer_casts(dialect: str | None) -> None:
    for function in MAINTENANCE_FUNCTIONS:
        assert function.statements_for(dialect) == function.statements


def test_operations_sql_uses_the_target_dialect() -> None:
    harness = TPCDSMaintenanceTest(benchmark=None, connection_factory=lambda: None, dialect="singlestore")
    emitted = "\n".join(harness.get_maintenance_operations_sql()["LF_CR"])
    assert "AS SIGNED)" in emitted
    assert "AS INTEGER)" not in emitted.split("INSERT INTO", 1)[1]


