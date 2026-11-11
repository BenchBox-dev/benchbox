# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from benchbox.core.tpch.power_test import TPCHPowerTest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _harness(benchmark: Any, connection: Any) -> TPCHPowerTest:
    return TPCHPowerTest(
        benchmark=benchmark,
        connection=connection,
        scale_factor=0.1,
        query_subset=["Q1"],
        warm_up=False,
        validation=False,
        verbose=False,
    )


def _benchmark_double() -> Mock:
    benchmark = Mock()
    benchmark.get_query.return_value = "SELECT 1"
    return benchmark


class TestPowerHarnessFailureAccounting:
    def test_failing_connection_reports_failure_not_success(self):
        connection = Mock()
        connection.execute.side_effect = RuntimeError("connection refused")

        result = _harness(_benchmark_double(), connection).run()

        assert connection.execute.called
        assert result.queries_executed == 1
        assert result.queries_successful == 0
        assert result.success is False
        assert result.errors, "failing queries must leave an error trail"
        assert "Query 1 failed" in result.errors[0]

    def test_success_requires_executed_sql(self):
        cursor = Mock()
        cursor.fetchall.return_value = [(1,)]
        del cursor.platform_result
        connection = Mock()
        connection.execute.return_value = cursor

        result = _harness(_benchmark_double(), connection).run()

        assert connection.execute.called
        assert result.queries_executed == 1
        assert result.queries_successful == 1
        assert result.success is True
        assert result.errors == []


