from __future__ import annotations

import re
from pathlib import Path

import pytest

from benchbox.core.tpcds import maintenance_test as maintenance_test_module
from benchbox.core.tpcds.maintenance_functions import (
    MAINTENANCE_FUNCTIONS,
    MAINTENANCE_FUNCTIONS_BY_NAME,
    METHOD_FACT_DELETE,
    METHOD_FACT_INSERT,
    METHOD_INVENTORY_DELETE,
    MaintenanceFunction,
)
from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTest
from benchbox.core.tpcds.refresh_data import SOURCE_TABLES_BY_NAME, RefreshSet, generate_refresh_sets
from benchbox.core.tpcds.schema.registry import TABLES_BY_NAME

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TABLE_5_4_IDS = {
    "LF_CR": 1,
    "LF_CS": 2,
    "LF_I": 3,
    "LF_SR": 4,
    "LF_SS": 5,
    "LF_WR": 6,
    "LF_WS": 7,
    "DF_CS": 8,
    "DF_SS": 9,
    "DF_WS": 10,
    "DF_I": 11,
}
INSERT_PATTERN = re.compile(r"^INSERT INTO (\w+) \(([^)]*)\) SELECT (.+) FROM ", re.IGNORECASE | re.DOTALL)
DELETE_PATTERN = re.compile(r"^DELETE FROM (\w+) WHERE ", re.IGNORECASE)
SCALE_FACTOR_MULTIPLIER = 100


class _RecordingCursor:
    def fetchone(self):
        return (0,)


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql, *args):
        self.statements.append(sql)
        return _RecordingCursor()

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture(scope="module")
def refresh_set(tmp_path_factory: pytest.TempPathFactory) -> RefreshSet:
    return generate_refresh_sets(0.01, 1, 1, tmp_path_factory.mktemp("functions") / "dsdgen")[1]


def _scaled(refresh_set: RefreshSet, directory: Path) -> RefreshSet:
    directory.mkdir(parents=True)
    files = {}
    for name, path in refresh_set.files.items():
        scaled = directory / path.name
        scaled.write_text(path.read_text(encoding="utf-8") * SCALE_FACTOR_MULTIPLIER, encoding="utf-8")
        files[name] = scaled
    return RefreshSet(
        set_id=refresh_set.set_id,
        directory=directory,
        files=files,
        row_counts={name: count * SCALE_FACTOR_MULTIPLIER for name, count in refresh_set.row_counts.items()},
    )


def _run_function(function: MaintenanceFunction, refresh_set: RefreshSet):
    connection = _RecordingConnection()
    harness = TPCDSMaintenanceTest(benchmark=None, connection_factory=lambda: connection, scale_factor=0.01)
    operation = harness._execute_function(function, refresh_set)
    issued = [statement for statement in connection.statements if statement in function.statements]
    return operation, issued


def test_exactly_eleven_functions_with_seven_load_and_four_delete() -> None:
    assert len(MAINTENANCE_FUNCTIONS) == 11
    assert len(MAINTENANCE_FUNCTIONS_BY_NAME) == 11
    load = [function for function in MAINTENANCE_FUNCTIONS if function.name.startswith("LF_")]
    delete = [function for function in MAINTENANCE_FUNCTIONS if function.name.startswith("DF_")]
    assert len(load) == 7
    assert len(delete) == 4
    assert {function.method for function in load} == {METHOD_FACT_INSERT}
    assert {function.method for function in delete} == {METHOD_FACT_DELETE, METHOD_INVENTORY_DELETE}


def test_function_names_and_ids_match_specification_table_5_4() -> None:
    assert {function.name: function.function_id for function in MAINTENANCE_FUNCTIONS} == TABLE_5_4_IDS


def test_methods_follow_specification_per_function() -> None:
    methods = {function.name: function.method for function in MAINTENANCE_FUNCTIONS}

    assert methods["DF_I"] == METHOD_INVENTORY_DELETE == 3
    assert methods["DF_CS"] == methods["DF_SS"] == methods["DF_WS"] == METHOD_FACT_DELETE == 2
    assert all(methods[name] == METHOD_FACT_INSERT == 1 for name in TABLE_5_4_IDS if name.startswith("LF_"))


def test_every_delete_function_precedes_every_load_function() -> None:
    kinds = [function.name[:2] for function in MAINTENANCE_FUNCTIONS]

    assert kinds == ["DF"] * 4 + ["LF"] * 7


def test_operation_type_reflects_method() -> None:
    for function in MAINTENANCE_FUNCTIONS:
        assert function.operation_type == ("INSERT" if function.name.startswith("LF_") else "DELETE")


def test_statement_counts_per_function_kind() -> None:
    for function in MAINTENANCE_FUNCTIONS:
        if function.name.startswith("LF_"):
            assert len(function.statements) == 1, function.name
        else:
            assert 1 <= len(function.statements) <= 2, function.name


def test_statements_are_set_based_without_update_or_truncate() -> None:
    for function in MAINTENANCE_FUNCTIONS:
        for statement in function.statements:
            upper = statement.upper()
            assert "UPDATE " not in upper, function.name
            assert "TRUNCATE" not in upper, function.name
            if function.name.startswith("LF_"):
                assert INSERT_PATTERN.match(statement), function.name
            else:
                assert DELETE_PATTERN.match(statement), function.name


def test_insert_target_columns_equal_warehouse_table_columns_in_order() -> None:
    for function in MAINTENANCE_FUNCTIONS:
        if not function.name.startswith("LF_"):
            continue
        (statement,) = function.statements
        match = INSERT_PATTERN.match(statement)
        assert match is not None
        target, column_list, select_list = match.groups()
        assert (target,) == function.target_tables
        listed = [column.strip() for column in column_list.split(",")]
        assert listed == [column.name for column in TABLES_BY_NAME[target].columns], function.name
        assert len(re.findall(r" AS (\w+)(?:,|$)", select_list.strip() + ",")) == len(listed), function.name
        assert re.findall(r" AS (\w+)(?:,|$)", select_list.strip() + ",") == listed, function.name


def test_statements_reference_only_declared_staging_and_warehouse_tables() -> None:
    staging_names = set(SOURCE_TABLES_BY_NAME)
    for function in MAINTENANCE_FUNCTIONS:
        assert set(function.staging_tables) <= staging_names
        assert all(target in TABLES_BY_NAME for target in function.target_tables)
        text = " ".join(function.statements)
        used = set(re.findall(r"\bs_\w+", text)) & staging_names
        assert used == set(function.staging_tables), function.name


def test_expected_rows_table_is_a_declared_staging_table_for_load_functions() -> None:
    for function in MAINTENANCE_FUNCTIONS:
        if function.name.startswith("LF_"):
            assert function.expected_rows_table in function.staging_tables
        else:
            assert function.expected_rows_table is None


def test_statement_count_is_independent_of_refresh_row_count(tmp_path: Path, refresh_set: RefreshSet) -> None:
    scaled = _scaled(refresh_set, tmp_path / "scaled")
    assert scaled.row_counts["s_inventory"] == 100 * refresh_set.row_counts["s_inventory"]

    for function in MAINTENANCE_FUNCTIONS:
        small_operation, small_issued = _run_function(function, refresh_set)
        large_operation, large_issued = _run_function(function, scaled)

        assert small_operation.statements_executed == len(function.statements), function.name
        assert large_operation.statements_executed == len(function.statements), function.name
        assert small_issued == large_issued == list(function.statements), function.name


def test_maintenance_test_source_has_no_per_row_update() -> None:
    source = Path(maintenance_test_module.__file__).read_text(encoding="utf-8")

    assert "UPDATE " not in source
