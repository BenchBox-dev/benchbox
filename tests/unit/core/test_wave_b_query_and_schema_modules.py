from __future__ import annotations

import pytest

from benchbox.core.amplab.queries import AMPLabQueryManager
from benchbox.core.joinorder.queries import JoinOrderQueryManager
from benchbox.core.joinorder.schema import JoinOrderSchema
from benchbox.core.metadata_primitives import schema as metadata_schema
from benchbox.core.tpchavoc.queries import TPCHavocQueryManager
from benchbox.core.transaction_primitives import schema as txn_schema

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_transaction_schema_sql_and_lookup():
    sql = txn_schema.get_create_table_sql("txn_orders", if_not_exists=True)
    assert "CREATE TABLE IF NOT EXISTS txn_orders" in sql
    assert "PRIMARY KEY" in sql

    all_sql = txn_schema.get_all_staging_tables_sql()
    assert "txn_lineitem" in all_sql
    assert txn_schema.get_table_schema("orders").name == "orders"

    with pytest.raises(ValueError, match="Unknown staging table"):
        txn_schema.get_create_table_sql("bad_table")


def test_metadata_schema_helpers():
    names = metadata_schema.get_table_names()
    schema = metadata_schema.get_schema()
    assert "lineitem" in names
    assert "lineitem" in schema
    assert metadata_schema.get_table_count() == len(names)
    assert metadata_schema.get_total_column_count() > 0
    ddl = metadata_schema.get_create_tables_sql()
    assert "Metadata Primitives Benchmark Schema" in ddl


def test_amplab_query_manager_paths():
    qm = AMPLabQueryManager()
    q1 = qm.get_query("1")
    assert "pageRank" in q1
    custom = qm.get_query("2", {"limit_rows": 5})
    assert "LIMIT 5" in custom
    all_q = qm.get_all_queries()
    assert "3a" in all_q

    with pytest.raises(ValueError, match="Invalid query ID"):
        qm.get_query("999")


def test_joinorder_query_manager_embedded_and_file_loading(tmp_path):
    embedded = JoinOrderQueryManager()
    assert embedded.get_query_count() > 0
    assert "1a" in embedded.get_query_ids()
    assert embedded.get_query("1a")
    assert "simple" in embedded.get_queries_by_complexity()
    assert "star_join" in embedded.get_queries_by_pattern()

    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    (query_dir / "x1.sql").write_text("SELECT 1;")
    (query_dir / "bad.txt").write_text("SELECT 2;")
    from_files = JoinOrderQueryManager(str(query_dir))
    assert "x1" in from_files.get_all_queries()

    with pytest.raises(ValueError, match="Invalid query ID"):
        embedded.get_query("does-not-exist")


def test_joinorder_schema_helpers():
    schema = JoinOrderSchema()
    sql = schema.get_create_table_sql("title", dialect="postgres")
    assert "CHARACTER VARYING" in sql

    all_sql = schema.get_create_tables_sql(dialect="singlestore")
    assert "TEXT CHARACTER SET utf8mb4" in all_sql
    assert "title" in schema.get_table_names()
    assert "movie_companies" in schema.get_relationship_tables()
    assert "keyword" in schema.get_dimension_tables()
    assert "columns" in schema.get_table_info("title")

    with pytest.raises(ValueError, match="not found"):
        schema.get_table_info("missing")


def test_tpchavoc_query_manager_variants_and_dispatch():
    qm = TPCHavocQueryManager()
    assert 1 in qm.get_implemented_queries()
    variant = qm.get_query_variant(1, 1, params=None)
    assert "select" in variant.lower()
    assert qm.get_variant_description(1, 1)
    assert 1 in qm.get_all_variants(1)
    assert 1 in qm.get_all_variants_info(1)
    assert "select" in qm.get_query("1_v1").lower()
    assert "select" in qm.get_parameterized_query_variant(1, 1).lower()

    with pytest.raises(ValueError, match="Query variants not implemented"):
        qm.get_query_variant(999, 1)

    with pytest.raises(ValueError, match="Invalid variant ID"):
        qm.get_query_variant(1, 99)

    with pytest.raises(ValueError, match="Invalid variant query ID format"):
        qm.get_query("bad_vx")
