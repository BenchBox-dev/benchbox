# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
from benchbox.sql_compat.rules.execution_filter.questdb_transaction_primitives import (
    QUESTDB_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture()
def txn(tmp_path):
    return TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)


@pytest.mark.parametrize("operation_id", sorted(QUESTDB_TRANSACTION_PRIMITIVES_OPERATION_SKIPS))
def test_unsupported_operations_are_skipped_on_questdb(txn, monkeypatch, operation_id):
    monkeypatch.setattr(txn, "is_setup", lambda connection: True)
    connection = Mock()
    result = txn.execute_operation(operation_id, connection, platform_key="questdb", platform_name="questdb")
    assert result.status == "SKIPPED"
    assert result.skip_reason.startswith(f"Operation '{operation_id}' is skipped on questdb: QuestDB 10.0.1")
    connection.execute.assert_not_called()


def test_commit_large_override_drops_the_leading_delete(txn):
    operation = txn.get_operation("transaction_commit_large")
    assert "DELETE" in operation.write_sql
    assert "DELETE" not in operation.platform_overrides["questdb"]
    assert operation.platform_overrides["questdb"].startswith("BEGIN TRANSACTION;")
