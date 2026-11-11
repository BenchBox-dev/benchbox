# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

from benchbox.core.dataframe.query import QueryCategory

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


ALL_QUERY_IDS = [f"Q{i}" for i in range(1, 23)]


class TestDataVaultQueryRegistry:
    def test_registry_imports_successfully(self):
        from benchbox.core.datavault.dataframe_queries import DATAVAULT_DATAFRAME_QUERIES

        assert DATAVAULT_DATAFRAME_QUERIES is not None

    def test_registry_has_22_queries(self):
        from benchbox.core.datavault.dataframe_queries import DATAVAULT_DATAFRAME_QUERIES

        queries = DATAVAULT_DATAFRAME_QUERIES.get_all_queries()
        assert len(queries) == 22

    def test_registry_benchmark_name(self):
        from benchbox.core.datavault.dataframe_queries import DATAVAULT_DATAFRAME_QUERIES

        assert DATAVAULT_DATAFRAME_QUERIES.benchmark == "datavault"

    def test_get_query_by_id(self):
        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        query = get_datavault_query("Q1")
        assert query is not None
        assert query.query_id == "Q1"

    def test_get_nonexistent_query(self):
        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        assert get_datavault_query("Q99") is None

    def test_list_queries(self):
        from benchbox.core.datavault.dataframe_queries import list_datavault_queries

        assert len(list_datavault_queries()) == 22

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_all_queries_registered(self, query_id):
        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        assert get_datavault_query(query_id) is not None, f"Query {query_id} should be registered"

    def test_queries_have_both_implementations(self):
        from benchbox.core.datavault.dataframe_queries import DATAVAULT_DATAFRAME_QUERIES

        for query in DATAVAULT_DATAFRAME_QUERIES.get_all_queries():
            assert query.expression_impl is not None, f"{query.query_id} missing expression impl"
            assert query.pandas_impl is not None, f"{query.query_id} missing pandas impl"

    def test_all_queries_callable(self):
        from benchbox.core.datavault.dataframe_queries import DATAVAULT_DATAFRAME_QUERIES

        for query in DATAVAULT_DATAFRAME_QUERIES.get_all_queries():
            assert callable(query.expression_impl), f"{query.query_id} expression_impl not callable"
            assert callable(query.pandas_impl), f"{query.query_id} pandas_impl not callable"

    def test_query_descriptions_not_empty(self):
        from benchbox.core.datavault.dataframe_queries import DATAVAULT_DATAFRAME_QUERIES

        for query in DATAVAULT_DATAFRAME_QUERIES.get_all_queries():
            assert query.description, f"{query.query_id} missing description"
            assert len(query.description) > 10, f"{query.query_id} description too short"

    def test_query_names_not_empty(self):
        from benchbox.core.datavault.dataframe_queries import DATAVAULT_DATAFRAME_QUERIES

        for query in DATAVAULT_DATAFRAME_QUERIES.get_all_queries():
            assert query.query_name, f"{query.query_id} missing query_name"


class TestDataVaultQueryCategories:
    def test_simple_queries_have_aggregate(self):
        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        for qid in ["Q1", "Q6"]:
            query = get_datavault_query(qid)
            assert QueryCategory.AGGREGATE in query.categories, f"{qid} should have AGGREGATE"

    def test_multi_join_queries(self):
        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        for qid in ["Q2", "Q3", "Q5", "Q7", "Q8", "Q9", "Q10", "Q18", "Q20", "Q21"]:
            query = get_datavault_query(qid)
            assert QueryCategory.MULTI_JOIN in query.categories, f"{qid} should have MULTI_JOIN"

    def test_subquery_queries(self):
        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        for qid in ["Q2", "Q4", "Q11", "Q13", "Q15", "Q16", "Q17", "Q18", "Q20", "Q21", "Q22"]:
            query = get_datavault_query(qid)
            assert QueryCategory.SUBQUERY in query.categories, f"{qid} should have SUBQUERY"

    def test_analytical_queries(self):
        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        query = get_datavault_query("Q8")
        assert QueryCategory.ANALYTICAL in query.categories, "Q8 should have ANALYTICAL"


class TestDataVaultParameters:
    def test_all_queries_have_parameters(self):
        from benchbox.core.datavault.dataframe_queries.parameters import DATAVAULT_DEFAULT_PARAMS

        assert len(DATAVAULT_DEFAULT_PARAMS) == 22

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_parameter_entries_exist(self, query_id):
        from benchbox.core.datavault.dataframe_queries.parameters import DATAVAULT_DEFAULT_PARAMS

        assert query_id in DATAVAULT_DEFAULT_PARAMS, f"Missing parameters for {query_id}"

    def test_q1_has_delta(self):
        from benchbox.core.datavault.dataframe_queries.parameters import get_parameters

        result = get_parameters("Q1")
        assert result.get("delta") == 90

    def test_q2_has_region(self):
        from benchbox.core.datavault.dataframe_queries.parameters import get_parameters

        result = get_parameters("Q2")
        assert result.get("region") == "EUROPE"

    def test_q22_has_country_codes(self):
        from benchbox.core.datavault.dataframe_queries.parameters import get_parameters

        result = get_parameters("Q22")
        codes = result.get("country_codes")
        assert len(codes) == 7

    def test_get_parameters_returns_container(self):
        from benchbox.core.datavault.dataframe_queries.parameters import DataVaultParameters, get_parameters

        result = get_parameters("Q1")
        assert isinstance(result, DataVaultParameters)
        assert result.query_id == "Q1"


class TestDataVaultBenchmarkRegistry:
    def test_datavault_supports_dataframe(self):
        from benchbox.core.benchmark_registry import get_benchmark_metadata

        meta = get_benchmark_metadata("datavault")
        assert meta is not None
        assert meta.get("supports_dataframe") is True


def _schema_faithful_tables(n_rows: int = 4):
    import datetime

    import pandas as pd

    from benchbox.core.datavault import schema as dvschema

    strings = {
        "r_name": "ASIA",
        "n_name": "GERMANY",
        "c_mktsegment": "BUILDING",
        "c_phone": "13-123-456",
        "o_orderstatus": "F",
        "o_orderpriority": "1-URGENT",
        "l_returnflag": "R",
        "l_linestatus": "O",
        "l_shipmode": "MAIL",
        "l_shipinstruct": "DELIVER IN PERSON",
        "p_type": "ECONOMY ANODIZED STEEL",
        "p_brand": "Brand#23",
        "p_container": "MED BOX",
        "p_name": "forest green widget",
        "s_comment": "regular comment",
        "o_comment": "plain comment",
    }

    def values(col):
        name = col.name
        dtype = col.data_type.name
        if name.startswith("hk_"):
            return [f"{name}#{i % 3}" for i in range(n_rows)]
        if name == "load_end_dts":
            return [None] * n_rows
        if name == "load_dts":
            return [datetime.datetime(2024, 1, 1)] * n_rows
        if name == "record_source":
            return ["TPCH"] * n_rows
        if name == "hashdiff":
            return [f"hashdiff#{i}" for i in range(n_rows)]
        if dtype == "INTEGER":
            return [i % 3 for i in range(n_rows)]
        if dtype == "DECIMAL":
            return [float(i) + 0.5 for i in range(n_rows)]
        if dtype == "DATE":
            return [datetime.date(1994, 6, 1) for _ in range(n_rows)]
        if dtype == "TIMESTAMP":
            return [datetime.datetime(2024, 1, 1)] * n_rows
        if name in strings:
            return [strings[name]] * n_rows
        return [f"{name}-v{i % 2}" for i in range(n_rows)]

    return {table.name: pd.DataFrame({col.name: values(col) for col in table.columns}) for table in dvschema.TABLES}


def _register_contexts(tables):
    import polars as pl

    from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    expr_ctx = PolarsDataFrameAdapter().create_context()
    pandas_ctx = PandasDataFrameAdapter().create_context()
    for name, pdf in tables.items():
        pandas_ctx.register_table(name, pdf)
        expr_ctx.register_table(name, pl.from_pandas(pdf).lazy())
    return expr_ctx, pandas_ctx


def _materialize(result):
    import pandas as pd

    native = getattr(result, "native", result)
    if hasattr(native, "collect"):
        native = native.collect()
    if hasattr(native, "rows"):
        return list(native.columns), [tuple(r) for r in native.rows()]
    rows = list(native.itertuples(index=False, name=None))
    return list(native.columns), [tuple(None if v is pd.NA else v for v in r) for r in rows]


class TestHousekeepingColumnStripping:
    def test_expression_strips_all_housekeeping_columns(self):
        import polars as pl

        from benchbox.core.datavault.dataframe_queries.queries import _strip_audit_columns
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        ctx.register_table(
            "sat",
            pl.DataFrame(
                {
                    "hk": ["a"],
                    "payload": [1],
                    "load_dts": ["2024-01-01"],
                    "record_source": ["TPCH"],
                    "load_end_dts": [None],
                    "hashdiff": ["h"],
                }
            ).lazy(),
        )
        stripped = _strip_audit_columns(ctx.get_table("sat"))
        assert stripped.columns == ["hk", "payload"]

    def test_expression_tolerates_missing_columns(self):
        import polars as pl

        from benchbox.core.datavault.dataframe_queries.queries import _strip_audit_columns
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        ctx.register_table("hub", pl.DataFrame({"hk": ["a"], "payload": [1]}).lazy())
        assert _strip_audit_columns(ctx.get_table("hub")).columns == ["hk", "payload"]

    def test_pandas_strips_all_housekeeping_columns(self):
        import pandas as pd

        from benchbox.core.datavault.dataframe_queries.queries import _strip_audit_columns_pandas

        frame = pd.DataFrame(
            {
                "hk": ["a"],
                "payload": [1],
                "load_dts": ["2024-01-01"],
                "record_source": ["TPCH"],
                "load_end_dts": [None],
                "hashdiff": ["h"],
            }
        )
        assert list(_strip_audit_columns_pandas(frame).columns) == ["hk", "payload"]

    def test_pandas_tolerates_missing_columns(self):
        import pandas as pd

        from benchbox.core.datavault.dataframe_queries.queries import _strip_audit_columns_pandas

        frame = pd.DataFrame({"hk": ["a"], "payload": [1]})
        assert list(_strip_audit_columns_pandas(frame).columns) == ["hk", "payload"]


class TestAllQueriesExecuteOnSchemaFaithfulData:
    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    @pytest.mark.parametrize("family", ["expression", "pandas"])
    def test_query_executes(self, query_id, family):
        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        expr_ctx, pandas_ctx = _register_contexts(_schema_faithful_tables())
        ctx = expr_ctx if family == "expression" else pandas_ctx
        query = get_datavault_query(query_id)
        impl = query.expression_impl if family == "expression" else query.pandas_impl
        columns, _rows = _materialize(impl(ctx))
        assert columns, f"{query_id}/{family} returned no columns"
        dups = [c for c in columns if c.endswith(("_right", "_x", "_y"))]
        assert not dups, f"{query_id}/{family} leaked duplicate columns: {dups}"


class TestQ17NullSemantics:
    def _tables(self, brand, container):
        import pandas as pd

        ll = pd.DataFrame({"hk_lineitem_link": ["li#1"], "hk_part": ["hp#1"], "hk_order": ["ho#1"]})
        sl = pd.DataFrame(
            {
                "hk_lineitem_link": ["li#1"],
                "l_quantity": [1],
                "l_extendedprice": [100.0],
                "load_end_dts": [None],
            }
        )
        sp = pd.DataFrame(
            {
                "hk_part": ["hp#1"],
                "p_brand": [brand],
                "p_container": [container],
                "load_end_dts": [None],
            }
        )
        return {"link_lineitem": ll, "sat_lineitem": sl, "sat_part": sp}

    @pytest.mark.parametrize("family", ["expression", "pandas"])
    def test_empty_filter_yields_single_null_row(self, family):
        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        expr_ctx, pandas_ctx = _register_contexts(self._tables("Brand#99", "NO SUCH BOX"))
        ctx = expr_ctx if family == "expression" else pandas_ctx
        query = get_datavault_query("Q17")
        impl = query.expression_impl if family == "expression" else query.pandas_impl
        columns, rows = _materialize(impl(ctx))
        assert columns == ["avg_yearly"]
        assert len(rows) == 1
        assert rows[0][0] is None, f"Q17/{family} should be NULL, got {rows[0][0]!r}"

    @pytest.mark.parametrize("family", ["expression", "pandas"])
    def test_nonempty_filter_yields_value(self, family):
        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        tables = self._tables("Brand#23", "MED BOX")
        import pandas as pd

        tables["link_lineitem"] = pd.DataFrame(
            {
                "hk_lineitem_link": ["li#1", "li#2", "li#3"],
                "hk_part": ["hp#1", "hp#1", "hp#1"],
                "hk_order": ["ho#1", "ho#1", "ho#1"],
            }
        )
        tables["sat_lineitem"] = pd.DataFrame(
            {
                "hk_lineitem_link": ["li#1", "li#2", "li#3"],
                "l_quantity": [10, 10, 1],
                "l_extendedprice": [100.0, 100.0, 70.0],
                "load_end_dts": [None, None, None],
            }
        )
        expr_ctx, pandas_ctx = _register_contexts(tables)
        ctx = expr_ctx if family == "expression" else pandas_ctx
        query = get_datavault_query("Q17")
        impl = query.expression_impl if family == "expression" else query.pandas_impl
        columns, rows = _materialize(impl(ctx))
        assert columns == ["avg_yearly"]
        assert len(rows) == 1
        assert rows[0][0] == pytest.approx(70.0 / 7.0)


class TestQ11ProjectsPartKey:
    @pytest.mark.parametrize("family", ["expression", "pandas"])
    def test_partkey_projection_and_values(self, family):
        import pandas as pd

        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        tables = {
            "link_part_supplier": pd.DataFrame(
                {"hk_part_supplier": ["ps#1", "ps#2"], "hk_part": ["hp#1", "hp#2"], "hk_supplier": ["hs#1", "hs#1"]}
            ),
            "sat_partsupp": pd.DataFrame(
                {
                    "hk_part_supplier": ["ps#1", "ps#2"],
                    "ps_supplycost": [10.0, 20.0],
                    "ps_availqty": [100, 100],
                    "load_end_dts": [None, None],
                }
            ),
            "link_supplier_nation": pd.DataFrame({"hk_supplier": ["hs#1"], "hk_nation": ["hn#1"]}),
            "sat_nation": pd.DataFrame({"hk_nation": ["hn#1"], "n_name": ["GERMANY"], "load_end_dts": [None]}),
            "hub_part": pd.DataFrame({"hk_part": ["hp#1", "hp#2"], "p_partkey": [101, 202]}),
        }
        expr_ctx, pandas_ctx = _register_contexts(tables)
        ctx = expr_ctx if family == "expression" else pandas_ctx
        query = get_datavault_query("Q11")
        impl = query.expression_impl if family == "expression" else query.pandas_impl
        columns, rows = _materialize(impl(ctx))
        assert columns == ["p_partkey", "value"]
        by_key = {r[0]: r[1] for r in rows}
        assert by_key[101] == pytest.approx(1000.0)
        assert by_key[202] == pytest.approx(2000.0)


class TestQ3Q10ColumnOrder:
    def test_q3_column_order(self):
        import datetime

        import pandas as pd

        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        tables = {
            "hub_customer": pd.DataFrame({"hk_customer": ["hc#1"]}),
            "sat_customer": pd.DataFrame(
                {"hk_customer": ["hc#1"], "c_mktsegment": ["BUILDING"], "load_end_dts": [None]}
            ),
            "link_order_customer": pd.DataFrame({"hk_customer": ["hc#1"], "hk_order": ["ho#1"]}),
            "hub_order": pd.DataFrame({"hk_order": ["ho#1"]}),
            "sat_order": pd.DataFrame(
                {
                    "hk_order": ["ho#1"],
                    "o_orderkey": [7],
                    "o_orderdate": [datetime.date(1995, 1, 1)],
                    "o_shippriority": [0],
                    "load_end_dts": [None],
                }
            ),
            "link_lineitem": pd.DataFrame({"hk_order": ["ho#1"], "hk_lineitem_link": ["li#1"]}),
            "sat_lineitem": pd.DataFrame(
                {
                    "hk_lineitem_link": ["li#1"],
                    "l_extendedprice": [100.0],
                    "l_discount": [0.1],
                    "l_shipdate": [datetime.date(1995, 4, 1)],
                    "load_end_dts": [None],
                }
            ),
        }
        for family in ("expression", "pandas"):
            expr_ctx, pandas_ctx = _register_contexts(tables)
            ctx = expr_ctx if family == "expression" else pandas_ctx
            query = get_datavault_query("Q3")
            impl = query.expression_impl if family == "expression" else query.pandas_impl
            columns, rows = _materialize(impl(ctx))
            assert columns == ["o_orderkey", "revenue", "o_orderdate", "o_shippriority"]
            assert len(rows) == 1
            assert rows[0][1] == pytest.approx(90.0)

    def test_q10_column_order(self):
        import datetime

        import pandas as pd

        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        tables = {
            "hub_customer": pd.DataFrame({"hk_customer": ["hc#1"], "c_custkey": [42]}),
            "sat_customer": pd.DataFrame(
                {
                    "hk_customer": ["hc#1"],
                    "c_name": ["Alice"],
                    "c_acctbal": [100.0],
                    "c_phone": ["13-1"],
                    "c_address": ["addr"],
                    "c_comment": ["cmt"],
                    "load_end_dts": [None],
                }
            ),
            "link_order_customer": pd.DataFrame({"hk_customer": ["hc#1"], "hk_order": ["ho#1"]}),
            "sat_order": pd.DataFrame(
                {"hk_order": ["ho#1"], "o_orderdate": [datetime.date(1993, 11, 1)], "load_end_dts": [None]}
            ),
            "link_lineitem": pd.DataFrame({"hk_order": ["ho#1"], "hk_lineitem_link": ["li#1"]}),
            "sat_lineitem": pd.DataFrame(
                {
                    "hk_lineitem_link": ["li#1"],
                    "l_extendedprice": [100.0],
                    "l_discount": [0.0],
                    "l_returnflag": ["R"],
                    "load_end_dts": [None],
                }
            ),
            "link_customer_nation": pd.DataFrame({"hk_customer": ["hc#1"], "hk_nation": ["hn#1"]}),
            "sat_nation": pd.DataFrame({"hk_nation": ["hn#1"], "n_name": ["GERMANY"], "load_end_dts": [None]}),
        }
        for family in ("expression", "pandas"):
            expr_ctx, pandas_ctx = _register_contexts(tables)
            ctx = expr_ctx if family == "expression" else pandas_ctx
            query = get_datavault_query("Q10")
            impl = query.expression_impl if family == "expression" else query.pandas_impl
            columns, rows = _materialize(impl(ctx))
            assert columns == [
                "c_custkey",
                "c_name",
                "revenue",
                "c_acctbal",
                "n_name",
                "c_address",
                "c_phone",
                "c_comment",
            ]


class TestQ15MaxSupplier:
    def _tables(self, revenues):
        import datetime

        import pandas as pd

        n = len(revenues)
        return {
            "link_lineitem": pd.DataFrame(
                {
                    "hk_lineitem_link": [f"li#{i}" for i in range(n)],
                    "hk_supplier": ["hs#1" if i % 2 == 0 else "hs#2" for i in range(n)],
                }
            ),
            "sat_lineitem": pd.DataFrame(
                {
                    "hk_lineitem_link": [f"li#{i}" for i in range(n)],
                    "l_extendedprice": revenues,
                    "l_discount": [0.0] * n,
                    "l_shipdate": [datetime.date(1996, 2, 1)] * n,
                    "load_end_dts": [None] * n,
                }
            ),
            "hub_supplier": pd.DataFrame({"hk_supplier": ["hs#1", "hs#2"], "s_suppkey": [1, 2]}),
            "sat_supplier": pd.DataFrame(
                {
                    "hk_supplier": ["hs#1", "hs#2"],
                    "s_name": ["Sup1", "Sup2"],
                    "s_address": ["a1", "a2"],
                    "s_phone": ["p1", "p2"],
                    "load_end_dts": [None, None],
                }
            ),
        }

    @pytest.mark.parametrize("family", ["expression", "pandas"])
    def test_top_supplier_selected(self, family):
        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        expr_ctx, pandas_ctx = _register_contexts(self._tables([100.0, 300.0]))
        ctx = expr_ctx if family == "expression" else pandas_ctx
        query = get_datavault_query("Q15")
        impl = query.expression_impl if family == "expression" else query.pandas_impl
        columns, rows = _materialize(impl(ctx))
        assert columns == ["s_suppkey", "s_name", "s_address", "s_phone", "total_revenue"]
        assert len(rows) == 1
        assert rows[0][0] == 2
        assert rows[0][4] == pytest.approx(300.0)

    @pytest.mark.parametrize("family", ["expression", "pandas"])
    def test_exact_tie_returns_both_suppliers(self, family):
        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        expr_ctx, pandas_ctx = _register_contexts(self._tables([100.0, 100.0]))
        ctx = expr_ctx if family == "expression" else pandas_ctx
        query = get_datavault_query("Q15")
        impl = query.expression_impl if family == "expression" else query.pandas_impl
        _, rows = _materialize(impl(ctx))
        assert sorted(r[0] for r in rows) == [1, 2]

    @pytest.mark.parametrize("family", ["expression", "pandas"])
    def test_sub_cent_difference_selects_true_max(self, family):
        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        expr_ctx, pandas_ctx = _register_contexts(self._tables([100.0, 100.0001]))
        ctx = expr_ctx if family == "expression" else pandas_ctx
        query = get_datavault_query("Q15")
        impl = query.expression_impl if family == "expression" else query.pandas_impl
        _, rows = _materialize(impl(ctx))
        assert len(rows) == 1
        assert rows[0][0] == 2

    @pytest.mark.parametrize("family", ["expression", "pandas"])
    def test_empty_revenue_yields_no_rows(self, family):
        import datetime

        import pandas as pd

        from benchbox.core.datavault.dataframe_queries import get_datavault_query

        tables = self._tables([100.0])
        tables["sat_lineitem"]["l_shipdate"] = pd.Series([datetime.date(1990, 1, 1)])
        expr_ctx, pandas_ctx = _register_contexts(tables)
        ctx = expr_ctx if family == "expression" else pandas_ctx
        query = get_datavault_query("Q15")
        impl = query.expression_impl if family == "expression" else query.pandas_impl
        _, rows = _materialize(impl(ctx))
        assert rows == []


