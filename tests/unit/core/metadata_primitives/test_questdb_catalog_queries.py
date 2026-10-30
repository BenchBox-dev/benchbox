from __future__ import annotations

import pytest

from benchbox.core.metadata_primitives.benchmark import MetadataPrimitivesBenchmark
from benchbox.core.metadata_primitives.catalog.loader import load_metadata_catalog
from benchbox.core.metadata_primitives.ddl import supports_acl

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_VIEW_QUERIES = (
    "schema_list_views",
    "view_hierarchy_list",
    "view_hierarchy_depth_analysis",
    "view_hierarchy_column_introspection",
    "view_hierarchy_definition_search",
)

_MISSING_RELATIONS = (
    "information_schema.views",
    "information_schema.schemata",
    "information_schema.table_privileges",
    "information_schema.column_privileges",
    "information_schema.constraint_column_usage",
    "pg_auth_members",
)


@pytest.fixture(scope="module")
def questdb_queries() -> dict[str, str]:
    return MetadataPrimitivesBenchmark(scale_factor=1.0).get_queries(dialect="questdb")


def test_questdb_queries_never_reference_relations_questdb_lacks(questdb_queries) -> None:
    offenders = {
        qid: relation
        for qid, sql in questdb_queries.items()
        for relation in _MISSING_RELATIONS
        if relation in sql.lower()
    }
    assert offenders == {}


@pytest.mark.parametrize("query_id", _VIEW_QUERIES)
def test_view_queries_read_the_views_function(questdb_queries, query_id) -> None:
    assert "views()" in questdb_queries[query_id].lower()


def test_schemata_query_reads_pg_namespace(questdb_queries) -> None:
    assert "pg_catalog.pg_namespace" in questdb_queries["schema_list_schemata"]


@pytest.mark.parametrize("query_id", ["column_numeric_columns", "column_string_columns"])
def test_column_queries_use_only_columns_questdb_exposes(questdb_queries, query_id) -> None:
    sql = questdb_queries[query_id].lower()
    assert "information_schema.columns" in sql
    assert "numeric_precision" not in sql
    assert "numeric_scale" not in sql
    assert "character_maximum_length" not in sql


def test_privilege_and_foreign_key_queries_are_skipped_on_questdb(questdb_queries) -> None:
    catalog = load_metadata_catalog().queries
    skipped = {qid for qid, query in catalog.items() if "questdb" in (query.skip_on or [])}
    assert skipped == {
        "acl_table_privileges_list",
        "acl_table_privileges_filtered",
        "acl_privilege_count_by_type",
        "acl_grantee_count_per_table",
        "acl_tables_with_grants",
        "acl_column_privileges_list",
        "acl_role_membership",
        "acl_grantable_privileges",
        "acl_benchmark_role_grants",
        "acl_privilege_summary",
        "constraint_referential_integrity",
    }
    assert skipped.isdisjoint(questdb_queries)


def test_questdb_does_not_run_acl_mutations() -> None:
    assert supports_acl("questdb") is False
    assert supports_acl("postgres") is True
