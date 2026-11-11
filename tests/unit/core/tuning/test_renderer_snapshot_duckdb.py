# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tuning.ddl_generator import get_ddl_generator
from benchbox.core.tuning.interface import TableTuning, TuningColumn
from benchbox.platforms.duckdb import _build_duckdb_ctas_sort_sql

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _preview_sort_clause(table_tuning: TableTuning) -> str | None:
    generator = get_ddl_generator("duckdb")
    tuning_clauses = generator.generate_tuning_clauses(table_tuning)
    return tuning_clauses.sort_by


class TestDuckdbPreviewExecutionParity:
    def test_single_sort_column(self):
        sort_columns = [TuningColumn(name="l_shipdate", type="DATE", order=1)]
        table_tuning = TableTuning(table_name="lineitem", sorting=sort_columns)

        preview_clause = _preview_sort_clause(table_tuning)
        execution_sql = _build_duckdb_ctas_sort_sql("lineitem", sort_columns)

        assert preview_clause == "ORDER BY l_shipdate"
        assert preview_clause in execution_sql
        assert execution_sql == "CREATE OR REPLACE TABLE lineitem AS SELECT * FROM lineitem ORDER BY l_shipdate;"

    def test_multi_column_sort_preserves_order(self):
        sort_columns = [
            TuningColumn(name="l_shipdate", type="DATE", order=1),
            TuningColumn(name="l_orderkey", type="INTEGER", order=2),
        ]
        table_tuning = TableTuning(table_name="lineitem", sorting=sort_columns)

        preview_clause = _preview_sort_clause(table_tuning)
        execution_sql = _build_duckdb_ctas_sort_sql("lineitem", sort_columns)

        assert preview_clause == "ORDER BY l_shipdate, l_orderkey"
        assert preview_clause in execution_sql

    def test_no_sorting_configured_yields_no_clause(self):
        table_tuning = TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
        )

        assert _preview_sort_clause(table_tuning) is None


