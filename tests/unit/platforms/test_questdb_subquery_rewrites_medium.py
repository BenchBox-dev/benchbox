from __future__ import annotations

import pytest

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _where_subqueries(sql: str) -> list:
    tree = sqlglot.parse_one(sql, read="questdb")
    found = []
    for where in tree.find_all(exp.Where):
        found.extend(where.find_all(exp.Exists))
        found.extend(predicate for predicate in where.find_all(exp.In) if predicate.args.get("query"))
        found.extend(sub for sub in where.find_all(exp.Subquery) if isinstance(sub.parent, (exp.Binary, exp.Between)))
    return found


@pytest.mark.parametrize("query_id", ["1", "10", "30", "32", "35", "41", "69", "81", "92"])
def test_tpcds_correlated_queries_leave_no_subquery_predicate(query_id):
    from benchbox.core.tpcds import TPCDSBenchmark

    sql = TPCDSBenchmark(scale_factor=0.01).get_queries(dialect="questdb")[query_id]
    assert _where_subqueries(sql) == []


def test_tpchavoc_case_in_subquery_query_leaves_no_subquery_predicate():
    from benchbox.core.tpchavoc import TPCHavocBenchmark

    sql = TPCHavocBenchmark(scale_factor=0.01).get_queries(dialect="questdb")["18_v10"]
    assert _where_subqueries(sql) == []


def test_core_schema_modules_do_not_import_the_vendored_sqlglot():
    import subprocess
    import sys

    code = (
        "import sys, benchbox.core.tpch.schema, benchbox.core.datavault.schema, "
        "benchbox.core.tpcds.schema.registry\n"
        "assert not any(name.startswith('benchbox._vendor.sqlglot') for name in sys.modules)"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
