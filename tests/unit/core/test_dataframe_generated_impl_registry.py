from __future__ import annotations

import pytest

import benchbox.core.joinorder.dataframe_queries as jo
import benchbox.core.read_primitives.dataframe_queries as rp

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_read_primitives_generated_impl_in_registry_not_globals() -> None:
    name = "aggregation_distinct_groupby_pandas_impl"
    assert name in rp._IMPLS
    assert name not in vars(rp)
    assert getattr(rp, name) is rp._IMPLS[name]


def test_joinorder_generated_impls_in_registry_not_globals() -> None:
    assert jo._IMPLS
    for name in jo._IMPLS:
        assert name not in vars(jo)
    sample = next(iter(jo._IMPLS))
    assert getattr(jo, sample) is jo._IMPLS[sample]


def test_tpcds_generated_impls_in_registry_not_globals() -> None:
    from benchbox.core.tpcds.dataframe_queries import queries as tpcds

    for name in ("q3_expression_impl", "q19_pandas_impl"):
        assert name in tpcds._GENERATED_IMPLS
        assert name not in vars(tpcds)
        assert getattr(tpcds, name) is tpcds._GENERATED_IMPLS[name]


def test_dispatch_resolves_explicit_and_generated_from_registry() -> None:
    generated = rp._impl_for("aggregation_distinct_groupby", "pandas")
    assert generated is rp._IMPLS["aggregation_distinct_groupby_pandas_impl"]


def test_generated_impl_names_survive_star_import() -> None:
    read_primitives_ns: dict[str, object] = {}
    exec("from benchbox.core.read_primitives.dataframe_queries import *", read_primitives_ns)
    assert (
        read_primitives_ns["aggregation_distinct_groupby_pandas_impl"]
        is rp._IMPLS["aggregation_distinct_groupby_pandas_impl"]
    )

    joinorder_ns: dict[str, object] = {}
    exec("from benchbox.core.joinorder.dataframe_queries import *", joinorder_ns)
    assert joinorder_ns["q1a_pandas_impl"] is jo._IMPLS["q1a_pandas_impl"]


def test_unknown_generated_name_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        _ = rp.totally_missing_query_impl
    with pytest.raises(AttributeError):
        _ = jo.totally_missing_query_impl
    from benchbox.core.tpcds.dataframe_queries import queries as tpcds

    with pytest.raises(AttributeError):
        _ = tpcds.totally_missing_query_impl
