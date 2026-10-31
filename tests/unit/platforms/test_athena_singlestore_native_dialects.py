import re

import pytest

from benchbox.core.ai_primitives.queries import AIQueryManager
from benchbox.core.h2odb.benchmark import H2OBenchmark
from benchbox.core.joinorder.schema import JoinOrderSchema
from benchbox.core.metadata_primitives.queries import MetadataPrimitivesQueryManager
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.transaction_primitives.benchmark import _pk_lock_bypass_required as txn_lock_bypass
from benchbox.core.transaction_primitives.catalog.loader import load_transaction_primitives_catalog
from benchbox.core.vector_search.schema import get_embedding_type
from benchbox.core.write_primitives.benchmark import _pk_lock_bypass_required as write_lock_bypass
from benchbox.core.write_primitives.catalog.loader import load_write_primitives_catalog

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(scope="module")
def tpch(tmp_path_factory):
    return TPCHBenchmark(scale_factor=0.01, output_dir=tmp_path_factory.mktemp("tpch"))


def test_write_primitives_athena_overrides_equal_trino_overrides():
    operations = load_write_primitives_catalog().operations.values()
    trino_keyed = [op for op in operations if "trino" in op.platform_overrides]
    assert trino_keyed
    for op in trino_keyed:
        assert op.platform_overrides.get("athena", "<missing>") == op.platform_overrides["trino"], op.id


def test_write_primitives_overrides_name_singlestore_not_mysql():
    operations = load_write_primitives_catalog().operations.values()
    assert not [op.id for op in operations if "mysql" in op.platform_overrides]
    singlestore_keyed = [op for op in operations if "singlestore" in op.platform_overrides]
    assert len(singlestore_keyed) == 14
    assert {"ddl_create_table_with_index", "ddl_create_index_on_existing"} <= {op.id for op in singlestore_keyed}


def test_transaction_primitives_overrides_name_singlestore_not_mysql():
    operations = load_transaction_primitives_catalog().operations.values()
    assert not [op.id for op in operations if "mysql" in op.platform_overrides]
    assert [op.id for op in operations if "singlestore" in op.platform_overrides] == [
        op.id for op in operations if "mariadb" in op.platform_overrides
    ]


@pytest.mark.parametrize("lock_bypass", [write_lock_bypass, txn_lock_bypass], ids=["write", "transaction"])
def test_athena_bypasses_the_primary_key_lock_table_like_trino(lock_bypass):
    assert lock_bypass("trino") is True
    assert lock_bypass("athena") is True


def test_ai_primitives_skip_on_athena_wherever_they_skip_on_trino():
    manager = AIQueryManager()
    assert manager.get_supported_queries("trino") == {}
    assert manager.get_supported_queries("athena") == {}


def test_metadata_primitives_acl_queries_are_skipped_on_athena():
    manager = MetadataPrimitivesQueryManager()
    assert not [query_id for query_id in manager.get_queries_for_dialect("athena") if query_id.startswith("acl_")]


def test_singlestore_expands_the_tpch_named_column_alias(tpch):
    query = tpch.get_query(13, dialect="singlestore")
    assert not re.search(r"\(\s*`?c_custkey`?\s*,\s*`?c_count`?\s*\)", query)
    assert re.search(r"`?c_count`?", query)


def test_singlestore_embedding_type_and_joinorder_charset_follow_the_dialect():
    assert get_embedding_type("singlestore", 128) == "VECTOR(128, F32)"
    assert get_embedding_type("mysql", 128) == "REAL[128]"
    schema = JoinOrderSchema()
    assert "CHARACTER SET utf8mb4" in schema.get_create_tables_sql("singlestore")
    assert "CHARACTER SET utf8mb4" not in schema.get_create_tables_sql("mysql")


def test_singlestore_native_dialect_writes_native_casts_and_null_ordering(tpch):
    query = tpch.get_query(1, dialect="singlestore")
    assert ":> DATE" in query
    assert "CAST(" not in query
    assert re.search(r"ORDER BY `l_returnflag` NULLS LAST, `l_linestatus` NULLS LAST", query)
    assert "CASE WHEN" not in query


def test_athena_native_dialect_query_text_equals_trino(tpch):
    for query_id in (1, 3, 13, 18):
        assert tpch.get_query(query_id, dialect="athena") == tpch.get_query(query_id, dialect="trino")


def test_singlestore_h2odb_q9_has_one_sort_key_in_within_group(tmp_path):
    query = H2OBenchmark(scale_factor=0.01, output_dir=tmp_path).get_queries(dialect="singlestore")["Q9"]
    assert "WITHIN GROUP (ORDER BY `fare_amount`)" in query
    assert "CASE WHEN" not in query
