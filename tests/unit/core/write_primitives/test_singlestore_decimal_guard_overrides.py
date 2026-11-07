import re
from unittest.mock import patch

import pytest

from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark
from benchbox.platforms.singlestore import _DECIMAL_TEXT_PATTERN, _OUT_OF_RANGE_DECIMAL
from benchbox.sql_compat.rules.execution_filter.singlestore_write_primitives import (
    SINGLESTORE_WRITE_PRIMITIVES_OPERATION_SKIPS,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

TEXT_LOAD_TABLES = ("bulk_load_ops_target", "bulk_load_upsert_stage")


@pytest.fixture()
def bench(tmp_path):
    with patch("benchbox.core.write_primitives.benchmark.WritePrimitivesDataGenerator"):
        return WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)


def _load_statements(sql):
    return [part.strip() for part in re.split(r";\n", sql) if part.strip().startswith("LOAD DATA")]


def _text_load_operations(bench):
    for operation_id in sorted(bench.get_all_operations()):
        operation = bench.get_operation(operation_id)
        sql = (operation.platform_overrides or {}).get("singlestore")
        if not sql or operation_id in SINGLESTORE_WRITE_PRIMITIVES_OPERATION_SKIPS:
            continue
        statements = [
            stmt
            for stmt in _load_statements(sql)
            if "FORMAT PARQUET" not in stmt and any(f"INTO TABLE {table}\n" in stmt for table in TEXT_LOAD_TABLES)
        ]
        if statements:
            yield operation_id, statements


def test_singlestore_text_loads_use_the_server_side_decimal_abort_guard(bench):
    seen = list(_text_load_operations(bench))
    assert len(seen) == 18
    for operation_id, statements in seen:
        for statement in statements:
            assert f"REGEXP {_DECIMAL_TEXT_PATTERN}" in statement, operation_id
            assert _OUT_OF_RANGE_DECIMAL in statement, operation_id
            assert "@o_totalprice" in statement or "@total_price" in statement, operation_id
            assert not re.search(r"[(,]\s*o_totalprice\s*[,)]", statement.split("\n(", 1)[-1].split("\n", 1)[0]), (
                operation_id
            )


def test_guard_assigns_the_decimal_column_only_through_the_checked_expression(bench):
    for operation_id, statements in _text_load_operations(bench):
        for statement in statements:
            assert statement.count("o_totalprice =") == 1, operation_id
            assert "SET " in statement, operation_id


def test_every_guarded_statement_keeps_clause_order_valid_for_load_data(bench):
    for operation_id, statements in _text_load_operations(bench):
        for statement in statements:
            lines = statement.rstrip(";").split("\n")
            column_line = next(i for i, line in enumerate(lines) if line.startswith("("))
            set_line = next(i for i, line in enumerate(lines) if line.startswith("SET "))
            assert column_line < set_line, operation_id
            assert all(not line.startswith(("FIELDS", "LINES", "IGNORE")) for line in lines[column_line:]), operation_id


def test_parquet_loads_stay_untouched_because_parquet_decimals_are_typed(bench):
    sql = bench.get_operation("bulk_load_parquet_small_uncompressed").platform_overrides["singlestore"]

    assert "SET " not in sql
    assert "REGEXP" not in sql


def test_guard_pattern_accepts_decimals_and_rejects_text():
    pattern = re.compile(_DECIMAL_TEXT_PATTERN.strip("'").replace("\\\\.", "\\."))

    for accepted in ("0", "12", "-3", "1234.5", "-0.07"):
        assert pattern.match(accepted), accepted
    for rejected in ("abc", "", "1e5", "1,5", "NaN", "12abc", ".5"):
        assert not pattern.match(rejected), rejected


@pytest.mark.parametrize("operation_id", ["merge_scd_type2_basic", "merge_scd_type2_no_change"])
def test_scd2_cleanup_uses_a_cast_date_literal_singlestore_accepts(bench, operation_id):
    operation = bench.get_operation(operation_id)

    assert "DATE '9999-12-31'" in operation.cleanup_sql
    cleanup = operation.cleanup_platform_overrides["singlestore"]
    assert "DATE '" not in cleanup
    assert "CAST('9999-12-31' AS DATE)" in cleanup
    assert cleanup.replace("CAST('9999-12-31' AS DATE)", "DATE '9999-12-31'") == operation.cleanup_sql


def test_no_cleanup_statement_reaches_singlestore_with_a_typed_date_literal(bench):
    for operation_id in sorted(bench.get_all_operations()):
        operation = bench.get_operation(operation_id)
        overrides = operation.cleanup_platform_overrides or {}
        cleanup = overrides["singlestore"] if "singlestore" in overrides else operation.cleanup_sql
        if cleanup:
            assert not re.search(r"\b(DATE|TIMESTAMP)\s+'", cleanup), operation_id
