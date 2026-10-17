from __future__ import annotations

import pytest

from benchbox.core.tpchavoc.variant_base import StaticSQLVariant, VariantGenerator
from benchbox.core.tpchavoc.variant_sets import VARIANT_REGISTRY
from benchbox.core.tpchavoc.variants import Q1_VARIANTS

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_registry_covers_all_queries():
    assert set(VARIANT_REGISTRY.keys()) == set(range(1, 23))
    for query_id, mapping in VARIANT_REGISTRY.items():
        assert len(mapping) == 10, f"Query {query_id} missing variants"
        assert all(isinstance(item, VariantGenerator) for item in mapping.values())


def test_static_variants_return_sql():
    for mapping in VARIANT_REGISTRY.values():
        for generator in mapping.values():
            sql = generator.generate("base_query")
            assert isinstance(sql, str)
            assert sql.strip()
            assert "select" in sql.lower() or "with" in sql.lower()
            assert isinstance(generator, StaticSQLVariant)


def test_query1_signature_variants():

    sql1 = Q1_VARIANTS[1].generate("base")
    assert "select avg(l2.l_quantity)" in sql1.lower()
    assert "from lineitem l2" in sql1.lower()

    sql4 = Q1_VARIANTS[4].generate("base")
    assert "union all" in sql4.lower()

    sql6 = Q1_VARIANTS[6].generate("base")
    assert "filter (where" in sql6.lower()

    sql9 = Q1_VARIANTS[9].generate("base")
    assert "grouping sets" in sql9.lower()

    sql10 = Q1_VARIANTS[10].generate("base")
    assert "case when" in sql10.lower()


def test_query16_derived_table_variant_excludes_every_complaint_supplier():
    duckdb = pytest.importorskip("duckdb")
    from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark

    benchmark = TPCHavocBenchmark(scale_factor=0.01)
    connection = duckdb.connect()
    connection.execute(
        """
        CREATE TABLE part AS SELECT * FROM (VALUES (1, 'Brand#12', 'STANDARD BRUSHED TIN', 49))
            AS t(p_partkey, p_brand, p_type, p_size);
        CREATE TABLE supplier AS SELECT * FROM (VALUES
            (1, 'Customer Complaints'), (2, 'Customer Complaints'), (3, 'no complaints'))
            AS t(s_suppkey, s_comment);
        CREATE TABLE partsupp AS SELECT * FROM (VALUES (1, 1), (1, 2), (1, 3)) AS t(ps_partkey, ps_suppkey);
        """
    )

    base = connection.execute(benchmark.get_query(16, dialect="duckdb")).fetchall()
    variant = connection.execute(benchmark.get_queries(dialect="duckdb")["16_v6"]).fetchall()

    assert base == [("Brand#12", "STANDARD BRUSHED TIN", 49, 1)]
    assert variant == base
