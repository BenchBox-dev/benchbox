# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.datavault.queries import DataVaultQueryManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDataVaultQueryManager:
    @pytest.fixture
    def query_manager(self):
        return DataVaultQueryManager()

    def test_all_22_queries_exist(self, query_manager):
        queries = query_manager.get_all_queries()
        assert len(queries) == 22
        for qid in range(1, 23):
            assert qid in queries

    def test_get_query_returns_string(self, query_manager):
        for qid in range(1, 23):
            query = query_manager.get_query(qid)
            assert isinstance(query, str)
            assert len(query) > 0

    def test_get_query_with_string_id(self, query_manager):
        query = query_manager.get_query("1")
        assert isinstance(query, str)
        assert len(query) > 0

    def test_get_query_invalid_raises(self, query_manager):
        with pytest.raises(ValueError, match="Invalid query ID"):
            query_manager.get_query(0)

        with pytest.raises(ValueError, match="Invalid query ID"):
            query_manager.get_query(23)

    def test_get_query_count(self, query_manager):
        assert query_manager.get_query_count() == 22

    def test_queries_use_current_satellite_filter(self, query_manager):
        for qid in range(1, 23):
            query = query_manager.get_query(qid)
            if "sat_" in query.lower():
                assert "load_end_dts IS NULL" in query.lower() or "load_end_dts is null" in query.lower(), (
                    f"Query {qid} uses satellite but may be missing current record filter"
                )

    def test_queries_use_hub_link_satellite_pattern(self, query_manager):
        for qid in range(1, 23):
            query = query_manager.get_query(qid).lower()
            has_dv_table = any(
                [
                    "hub_" in query,
                    "link_" in query,
                    "sat_" in query,
                ]
            )
            assert has_dv_table, f"Query {qid} doesn't appear to use Data Vault tables"

    def test_query_syntax_valid(self, query_manager):
        for qid in range(1, 23):
            query = query_manager.get_query(qid).lower()
            assert "select" in query, f"Query {qid} missing SELECT"
            assert "from" in query, f"Query {qid} missing FROM"


class TestQueryContent:
    @pytest.fixture
    def query_manager(self):
        return DataVaultQueryManager()

    def test_q1_lineitem_aggregation(self, query_manager):
        q1 = query_manager.get_query(1).lower()
        assert "sat_lineitem" in q1
        assert "sum" in q1
        assert "l_returnflag" in q1
        assert "l_linestatus" in q1

    def test_q3_customer_order_lineitem_chain(self, query_manager):
        q3 = query_manager.get_query(3).lower()
        assert "hub_customer" in q3
        assert "hub_order" in q3
        assert "link_order_customer" in q3

    def test_q6_simple_aggregation(self, query_manager):
        q6 = query_manager.get_query(6).lower()
        assert "sat_lineitem" in q6
        assert "sum" in q6
        assert "l_discount" in q6

    def test_q15_uses_cte(self, query_manager):
        q15 = query_manager.get_query(15).lower()
        assert "with" in q15
        assert "revenue" in q15

    def test_q10_preaggregates_returned_lineitems_before_customer_details(self, query_manager):
        q10 = query_manager.get_query(10).lower()

        assert "lineitem_revenue_by_order as" in q10
        assert "customer_revenue as" in q10
        assert q10.index("group by ll.hk_order") < q10.index("join hub_customer")
        assert "join hub_order" in q10
        assert "join hub_nation" in q10
        assert "sum(cr.customer_revenue) as revenue" in q10

    def test_q12_stays_tpc_style_without_cte(self, query_manager):
        q12 = query_manager.get_query(12).lower()
        assert "with " not in q12
        assert "join hub_order" not in q12

    def test_q18_uses_inline_large_orders_subquery(self, query_manager):
        q18 = query_manager.get_query(18).lower()
        assert "with " not in q18
        assert "join (" in q18
        assert "having sum(sl2.l_quantity) >" in q18

    def test_q21_preserves_exists_semantics(self, query_manager):
        q21 = query_manager.get_query(21).lower()
        assert "with " not in q21
        assert "exists (" in q21
        assert "not exists (" in q21


class TestParameterSubstitution:
    @pytest.fixture
    def query_manager(self):
        return DataVaultQueryManager()

    def test_get_template_returns_placeholders(self, query_manager):
        template = query_manager.get_template(1)
        assert ":delta" in template

    def test_get_default_params_returns_dict(self, query_manager):
        params = query_manager.get_default_params(1)
        assert "delta" in params
        assert isinstance(params["delta"], int)

    def test_get_query_with_custom_params(self, query_manager):
        query = query_manager.get_query(1, params={"delta": 30})
        assert "INTERVAL 30 DAY" in query
        assert ":delta" not in query

    def test_get_parameterized_query_returns_tuple(self, query_manager):
        sql, params = query_manager.get_parameterized_query(1, stream_id=0)
        assert isinstance(sql, str)
        assert hasattr(params, "query_id")
        assert params.query_id == 1

    def test_parameterized_query_no_placeholders(self, query_manager):
        for qid in range(1, 23):
            sql, _ = query_manager.get_parameterized_query(qid)
            import re

            placeholders = re.findall(r":\w+", sql)
            invalid = [p for p in placeholders if not p.upper() == p]
            assert not invalid, f"Query {qid} has unsubstituted placeholders: {invalid}"

    def test_different_stream_ids_produce_different_params(self, query_manager):
        _, params1 = query_manager.get_parameterized_query(2, stream_id=0)
        _, params2 = query_manager.get_parameterized_query(2, stream_id=1)
        assert params1.as_dict() != params2.as_dict()

    def test_same_stream_id_is_deterministic(self, query_manager):
        _, params1 = query_manager.get_parameterized_query(1, stream_id=42)
        _, params2 = query_manager.get_parameterized_query(1, stream_id=42)
        assert params1.as_dict() == params2.as_dict()

    def test_all_22_queries_have_default_params(self, query_manager):
        for qid in range(1, 23):
            params = query_manager.get_default_params(qid)
            assert isinstance(params, dict)

    def test_list_parameter_substitution(self, query_manager):
        params = query_manager.get_default_params(16)
        assert "sizes" in params
        assert isinstance(params["sizes"], list)

        query = query_manager.get_query(16)
        for size in params["sizes"]:
            assert str(size) in query


class TestParameterGenerator:
    def test_parameter_generator_deterministic(self):
        from benchbox.core.datavault.queries import DataVaultParameterGenerator

        gen1 = DataVaultParameterGenerator(seed=12345)
        gen2 = DataVaultParameterGenerator(seed=12345)

        params1 = gen1.generate_parameters(1, stream_id=0)
        params2 = gen2.generate_parameters(1, stream_id=0)

        assert params1.as_dict() == params2.as_dict()

    def test_parameter_generator_all_queries(self):
        from benchbox.core.datavault.queries import DataVaultParameterGenerator

        gen = DataVaultParameterGenerator(seed=42)
        for qid in range(1, 23):
            params = gen.generate_parameters(qid)
            assert params.query_id == qid
            assert isinstance(params.as_dict(), dict)

    def test_q1_delta_in_valid_range(self):
        from benchbox.core.datavault.queries import DataVaultParameterGenerator

        gen = DataVaultParameterGenerator(seed=0)
        for stream in range(10):
            params = gen.generate_parameters(1, stream_id=stream)
            delta = params.as_dict()["delta"]
            assert 60 <= delta <= 120, f"delta {delta} out of range [60,120]"

    def test_q2_region_valid(self):
        from benchbox.core.datavault.queries import REGIONS, DataVaultParameterGenerator

        gen = DataVaultParameterGenerator(seed=0)
        for stream in range(10):
            params = gen.generate_parameters(2, stream_id=stream)
            region = params.as_dict()["region"]
            assert region in REGIONS, f"Invalid region: {region}"


def test_every_query_translates_to_sqlite_without_unsupported_interval() -> None:
    from benchbox.core.datavault.benchmark import DataVaultBenchmark

    queries = DataVaultBenchmark(scale_factor=0.01).get_queries(dialect="sqlite")

    assert queries
    assert not [qid for qid, sql in queries.items() if "does not support" in sql or "INTERVAL" in sql.upper()]
