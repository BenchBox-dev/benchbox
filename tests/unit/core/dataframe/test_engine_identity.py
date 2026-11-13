from __future__ import annotations

import importlib
from typing import Any

import pytest

from benchbox.core.dataframe import DataFrameContext, DataFrameEngine
from benchbox.core.dataframe.context import DataFrameContextImpl
from benchbox.core.dataframe.engine import SPARK_ENGINES
from benchbox.core.read_primitives import dataframe_queries as read_primitives
from benchbox.core.tpcds.dataframe_queries import queries as tpcds_queries

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ADAPTERS = [
    ("benchbox.platforms.dataframe.polars_df", "PolarsDataFrameAdapter"),
    ("benchbox.platforms.dataframe.pandas_df", "PandasDataFrameAdapter"),
    ("benchbox.platforms.dataframe.cudf_df", "CuDFDataFrameAdapter"),
    ("benchbox.platforms.dataframe.dask_df", "DaskDataFrameAdapter"),
    ("benchbox.platforms.dataframe.datafusion_df", "DataFusionDataFrameAdapter"),
    ("benchbox.platforms.dataframe.pyspark_df", "PySparkDataFrameAdapter"),
    ("benchbox.platforms.dataframe.lakesail_df", "LakeSailDataFrameAdapter"),
]


class _Ctx(DataFrameContextImpl[Any]):
    pass


_Ctx.__abstractmethods__ = frozenset()


def _ctx(platform: str) -> _Ctx:
    return _Ctx(platform=platform, family="expression")


class _Value:
    def __init__(self, log):
        self.log = log

    def rank(self, method, descending):
        return self

    def over(self, partition_by):
        return self

    def is_null(self):
        self.log.append("is_null")
        return self

    def sum(self):
        return self

    def count(self):
        return self

    def __add__(self, other):
        return self


class _RankCtx(_Ctx):
    def __init__(self, platform, log):
        super().__init__(platform=platform, family="expression")
        self.log = log

    def col(self, name):
        return _Value(self.log)

    def when(self, cond):
        self.log.append("when")
        return self

    def then(self, value):
        return self

    def otherwise(self, value):
        return self


@pytest.mark.parametrize("member", list(DataFrameEngine))
def test_display_name_round_trips(member):
    assert DataFrameEngine.from_platform_name(member.value) is member


@pytest.mark.parametrize(
    "name",
    [
        "polars",
        "POLARS",
        "datafusion",
        "Datafusion",
        "datafusion-df",
        "dask-df",
        "pyspark",
        "Cudf",
        "",
        "Modin",
        "test",
    ],
)
def test_unknown_or_wrong_case_name_raises(name):
    with pytest.raises(ValueError, match="Unknown DataFrame engine"):
        DataFrameEngine.from_platform_name(name)


def test_predicates_raise_for_wrong_case_platform():
    with pytest.raises(ValueError):
        read_primitives._is_datafusion_context(_ctx("datafusion"))
    with pytest.raises(ValueError):
        tpcds_queries._sql_rank_expression(_RankCtx("polars", []), "c", [], descending=False)


@pytest.mark.parametrize(("module", "cls"), ADAPTERS)
def test_every_adapter_platform_name_maps_to_member(module, cls):
    source = importlib.import_module(module)
    adapter_cls = getattr(source, cls)
    name = adapter_cls.platform_name.fget(object.__new__(adapter_cls))
    assert DataFrameEngine.from_platform_name(name).value == name


def test_every_member_has_an_adapter():
    names = set()
    for module, cls in ADAPTERS:
        adapter_cls = getattr(importlib.import_module(module), cls)
        names.add(adapter_cls.platform_name.fget(object.__new__(adapter_cls)))
    assert names == {member.value for member in DataFrameEngine}


def test_spark_engines():
    assert {DataFrameEngine.PYSPARK, DataFrameEngine.LAKESAIL} == SPARK_ENGINES


@pytest.mark.parametrize("member", list(DataFrameEngine))
def test_read_primitive_predicates(member):
    ctx = _ctx(member.value)
    assert read_primitives._is_dask_context(ctx) is (member is DataFrameEngine.DASK)
    assert read_primitives._is_datafusion_context(ctx) is (member is DataFrameEngine.DATAFUSION)


@pytest.mark.parametrize("member", list(DataFrameEngine))
def test_context_keeps_display_string(member):
    ctx = _ctx(member.value)
    assert ctx.platform == member.value
    assert isinstance(ctx, DataFrameContext)


@pytest.mark.parametrize("member", list(DataFrameEngine))
def test_sql_rank_expression_only_adjusts_polars(member):
    log: list[str] = []
    ctx = _RankCtx(member.value, log)
    result = tpcds_queries._sql_rank_expression(ctx, "c", ["p"], descending=True)
    if member is DataFrameEngine.POLARS:
        assert "when" in log
    else:
        assert log == []
        assert isinstance(result, _Value)
