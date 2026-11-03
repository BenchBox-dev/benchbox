from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
from benchbox.sql_compat.rules.execution_filter.doris_transaction_primitives import (
    DORIS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture()
def txn(tmp_path):
    return TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)


@pytest.mark.parametrize("operation_id", sorted(DORIS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS))
def test_unsupported_operations_are_skipped_on_doris(txn, monkeypatch, operation_id):
    monkeypatch.setattr(txn, "is_setup", lambda connection: True)
    connection = Mock()
    result = txn.execute_operation(operation_id, connection, platform_key="doris", platform_name="Apache Doris")
    assert result.status == "SKIPPED"
    assert result.skip_reason.startswith(f"Operation '{operation_id}' is skipped on apache doris: Doris 4.0.3")
    connection.execute.assert_not_called()


def test_begin_transaction_is_rewritten_to_begin_on_doris(txn):
    sql = txn._rewrite_transactional_sql_for_platform(
        "BEGIN TRANSACTION;\nINSERT INTO t VALUES (1);\nCOMMIT;\n", "doris"
    )
    assert sql.startswith("BEGIN;\n")
    assert "TRANSACTION" not in sql


def test_generate_series_is_rewritten_to_the_numbers_table_function_on_doris(txn):
    sql = txn._rewrite_transactional_sql_for_platform(
        "INSERT INTO t SELECT 9200000 + n FROM (SELECT unnest(generate_series(1, 100)) AS n) t;", "doris"
    )
    assert 'SELECT number + 1 AS n FROM numbers("number" = 100)' in sql
    assert "generate_series" not in sql


def test_other_platforms_keep_begin_transaction(txn):
    sql = "BEGIN TRANSACTION;\nCOMMIT;\n"
    assert txn._rewrite_transactional_sql_for_platform(sql, "mysql") == sql
