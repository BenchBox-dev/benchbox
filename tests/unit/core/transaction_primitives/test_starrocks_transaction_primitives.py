from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
from benchbox.sql_compat.rules.execution_filter.starrocks_transaction_primitives import (
    STARROCKS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture()
def txn(tmp_path):
    return TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)


@pytest.mark.parametrize("operation_id", sorted(STARROCKS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS))
def test_unsupported_operations_are_skipped_on_starrocks(txn, monkeypatch, operation_id):
    monkeypatch.setattr(txn, "is_setup", lambda connection: True)
    connection = Mock()
    result = txn.execute_operation(operation_id, connection, platform_key="starrocks", platform_name="starrocks")
    assert result.status == "SKIPPED"
    assert result.skip_reason.startswith(f"Operation '{operation_id}' is skipped on starrocks: StarRocks 3.5")
    connection.execute.assert_not_called()


def test_begin_transaction_is_rewritten_to_begin_on_starrocks(txn):
    sql = txn._rewrite_transactional_sql_for_platform(
        "BEGIN TRANSACTION;\nINSERT INTO t VALUES (1);\nCOMMIT;\n", "starrocks"
    )
    assert sql.startswith("BEGIN;\n")
    assert "TRANSACTION" not in sql


def test_starrocks_transaction_sql_is_never_merged_and_skips_are_exactly_the_multi_dml_operations(txn):
    import re
    from collections import Counter

    multi_dml = set()
    for operation_id, operation in txn.get_all_operations().items():
        original = operation.write_sql
        sql = txn._rewrite_transactional_sql_for_platform(original, "starrocks")
        dml = r"(?im)^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(\w+)"
        assert Counter(re.findall(dml, sql)) == Counter(
            re.findall(dml, txn._rewrite_transactional_sql_for_platform(original, "mysql"))
        ), operation_id
        if any(count > 1 for count in Counter(re.findall(dml, sql)).values()):
            multi_dml.add(operation_id)

    one_dml_skips = {
        operation_id
        for operation_id, reason in STARROCKS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS.items()
        if "one DML statement per table" in reason
    }
    assert multi_dml <= set(STARROCKS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS)
    assert one_dml_skips <= multi_dml
    assert {
        "transaction_commit_small",
        "transaction_rollback_small",
        "transaction_isolation_read_committed",
        "transaction_isolation_repeatable_read",
        "transaction_isolation_serializable",
        "transaction_multi_table_writes",
    } <= one_dml_skips
    runnable = set(txn.get_all_operations()) - set(STARROCKS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS)
    assert not runnable & multi_dml


def test_generate_series_is_rewritten_to_the_table_function_on_starrocks(txn):
    sql = txn._rewrite_transactional_sql_for_platform(
        "INSERT INTO t SELECT 9200000 + n FROM (SELECT unnest(generate_series(1, 100)) AS n) t;", "starrocks"
    )
    assert "TABLE(generate_series(1, 100)) AS series(n)" in sql
    assert "unnest" not in sql


def test_write_sql_without_series_or_transaction_is_left_untouched_on_starrocks(txn):
    sql = "UPDATE t SET a = 1 WHERE b = 2"
    assert txn._rewrite_transactional_sql_for_platform(sql, "starrocks") == sql
