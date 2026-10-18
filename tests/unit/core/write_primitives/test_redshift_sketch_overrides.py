from __future__ import annotations

import pytest

from benchbox._vendor import sqlglot
from benchbox.core.write_primitives.catalog.loader import load_write_primitives_catalog

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


REDSHIFT_HLL_OPS = (
    "sketch_ddl_create_persistent_table",
    "sketch_insert_theta_per_partition",
    "sketch_query_theta_union_merge",
    "sketch_drop_persistent_table",
)


def test_redshift_hll_overrides_parse_with_sqlglot() -> None:
    operations = load_write_primitives_catalog().operations

    for operation_id in REDSHIFT_HLL_OPS:
        sql = operations[operation_id].platform_overrides["redshift"]
        parsed = sqlglot.parse(sql, read="redshift")

        assert parsed, operation_id


def test_redshift_hll_distinct_bounds_are_tightened_from_initial_theta_bounds() -> None:
    operation = load_write_primitives_catalog().operations["sketch_query_theta_union_merge"]
    validation = operation.validation_queries[0]

    assert validation.expected_value_min == 14500
    assert validation.expected_value_max == 15500


def test_redshift_hll_distinct_validation_uses_redshift_hll_functions() -> None:
    operation = load_write_primitives_catalog().operations["sketch_query_theta_union_merge"]
    validation_sql = operation.validation_queries[0].platform_overrides["redshift"]
    parsed = sqlglot.parse(validation_sql, read="redshift")

    assert parsed
    assert "HLL_CARDINALITY(HLL_COMBINE(USER_SKETCH))" in validation_sql.upper()
    assert "DATASKETCH_THETA" not in validation_sql.upper()


def test_redshift_hll_overrides_avoid_hllsketch_key_and_grouping_clauses() -> None:
    operations = load_write_primitives_catalog().operations

    for operation_id in REDSHIFT_HLL_OPS:
        sql = operations[operation_id].platform_overrides["redshift"].upper()

        assert "DISTKEY" not in sql
        assert "SORTKEY" not in sql
        assert "GROUP BY USER_SKETCH" not in sql
        assert "ORDER BY USER_SKETCH" not in sql
        assert "DISTINCT USER_SKETCH" not in sql
