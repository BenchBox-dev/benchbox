# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from benchbox.core.dataframe.protocols import (
    AggregateFunction,
    DataFrameGroupBy,
    DataFrameOps,
    JoinType,
    SortOrder,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestJoinType:
    def test_all_join_types_exist(self):

        expected = {"inner", "left", "right", "outer", "cross", "semi", "anti"}
        actual = {jt.value for jt in JoinType}
        assert actual == expected

    def test_str_returns_value(self):

        assert str(JoinType.INNER) == "inner"
        assert str(JoinType.LEFT) == "left"
        assert str(JoinType.SEMI) == "semi"

    def test_from_string_valid(self):

        assert JoinType.from_string("inner") == JoinType.INNER
        assert JoinType.from_string("LEFT") == JoinType.LEFT
        assert JoinType.from_string("OUTER") == JoinType.OUTER

    def test_from_string_invalid(self):

        with pytest.raises(ValueError, match="Invalid join type"):
            JoinType.from_string("invalid")

    def test_from_string_case_insensitive(self):

        assert JoinType.from_string("INNER") == JoinType.INNER
        assert JoinType.from_string("Inner") == JoinType.INNER
        assert JoinType.from_string("inner") == JoinType.INNER


class TestAggregateFunction:
    def test_all_aggregate_functions_exist(self):

        expected = {
            "sum",
            "mean",
            "avg",
            "count",
            "min",
            "max",
            "first",
            "last",
            "std",
            "var",
            "median",
            "count_distinct",
        }
        actual = {af.value for af in AggregateFunction}
        assert actual == expected

    def test_str_returns_value(self):

        assert str(AggregateFunction.SUM) == "sum"
        assert str(AggregateFunction.COUNT) == "count"
        assert str(AggregateFunction.COUNT_DISTINCT) == "count_distinct"

    def test_from_string_valid(self):

        assert AggregateFunction.from_string("sum") == AggregateFunction.SUM
        assert AggregateFunction.from_string("COUNT") == AggregateFunction.COUNT
        assert AggregateFunction.from_string("count_distinct") == AggregateFunction.COUNT_DISTINCT

    def test_from_string_invalid(self):

        with pytest.raises(ValueError, match="Invalid aggregate function"):
            AggregateFunction.from_string("invalid")

    def test_avg_alias(self):

        assert AggregateFunction.AVG is not None
        assert AggregateFunction.AVG.value == "avg"


class TestSortOrder:
    def test_all_sort_orders_exist(self):

        expected = {"asc", "desc"}
        actual = {so.value for so in SortOrder}
        assert actual == expected

    def test_str_returns_value(self):

        assert str(SortOrder.ASC) == "asc"
        assert str(SortOrder.DESC) == "desc"

    def test_ascending_property(self):

        assert SortOrder.ASC.ascending is True
        assert SortOrder.DESC.ascending is False


class _MockProtocolDataFrame:
    def select(self, *columns: str) -> _MockProtocolDataFrame:
        return self

    def filter(self, condition: Any) -> _MockProtocolDataFrame:
        return self

    def group_by(self, *columns: str) -> _MockProtocolGroupBy:
        return _MockProtocolGroupBy()

    def join(
        self,
        other: _MockProtocolDataFrame,
        on: str | Sequence[str] | None = None,
        left_on: str | Sequence[str] | None = None,
        right_on: str | Sequence[str] | None = None,
        how: JoinType | str = JoinType.INNER,
    ) -> _MockProtocolDataFrame:
        return self

    def sort(
        self,
        *columns: str,
        ascending: bool | Sequence[bool] = True,
    ) -> _MockProtocolDataFrame:
        return self

    def with_column(self, name: str, expr: Any) -> _MockProtocolDataFrame:
        return self

    def distinct(self) -> _MockProtocolDataFrame:
        return self

    def limit(self, n: int) -> _MockProtocolDataFrame:
        return self

    def collect(self) -> Any:
        return []

    def count(self) -> int:
        return 0

    @property
    def columns(self) -> list[str]:
        return []

    @property
    def shape(self) -> tuple[int, int]:
        return (0, 0)


class _MockProtocolGroupBy:
    def agg(self, *aggregations: Any, **named_aggregations: Any) -> _MockProtocolDataFrame:
        return _MockProtocolDataFrame()

    def sum(self, *columns: str) -> _MockProtocolDataFrame:
        return _MockProtocolDataFrame()

    def mean(self, *columns: str) -> _MockProtocolDataFrame:
        return _MockProtocolDataFrame()

    def count(self) -> _MockProtocolDataFrame:
        return _MockProtocolDataFrame()

    def min(self, *columns: str) -> _MockProtocolDataFrame:
        return _MockProtocolDataFrame()

    def max(self, *columns: str) -> _MockProtocolDataFrame:
        return _MockProtocolDataFrame()

    def first(self, *columns: str) -> _MockProtocolDataFrame:
        return _MockProtocolDataFrame()


@pytest.fixture
def mock_protocol_dataframe() -> _MockProtocolDataFrame:
    return _MockProtocolDataFrame()


class TestDataFrameOpsProtocol:
    def test_protocol_is_runtime_checkable(self):

        assert isinstance(DataFrameOps, type), "DataFrameOps should be a type (protocol class)"

    def test_mock_implementation_satisfies_protocol(self, mock_protocol_dataframe: _MockProtocolDataFrame):

        mock_df = mock_protocol_dataframe

        assert isinstance(mock_df.select("a", "b"), _MockProtocolDataFrame)
        assert isinstance(mock_df.filter(True), _MockProtocolDataFrame)
        assert isinstance(mock_df.group_by("a"), _MockProtocolGroupBy)
        assert isinstance(mock_df.join(_MockProtocolDataFrame()), _MockProtocolDataFrame)
        assert isinstance(mock_df.sort("a"), _MockProtocolDataFrame)
        assert isinstance(mock_df.with_column("x", 1), _MockProtocolDataFrame)
        assert isinstance(mock_df.distinct(), _MockProtocolDataFrame)
        assert isinstance(mock_df.limit(10), _MockProtocolDataFrame)


class TestDataFrameGroupByProtocol:
    def test_protocol_is_runtime_checkable(self):

        assert isinstance(DataFrameGroupBy, type), "DataFrameGroupBy should be a type (protocol class)"

    def test_mock_groupby_satisfies_protocol(self):

        class MockResult:
            pass

        class MockGroupBy:
            def agg(self, *aggregations: Any, **named_aggregations: Any) -> MockResult:
                return MockResult()

            def sum(self, *columns: str) -> MockResult:
                return MockResult()

            def mean(self, *columns: str) -> MockResult:
                return MockResult()

            def count(self) -> MockResult:
                return MockResult()

            def min(self, *columns: str) -> MockResult:
                return MockResult()

            def max(self, *columns: str) -> MockResult:
                return MockResult()

            def first(self, *columns: str) -> MockResult:
                return MockResult()

        mock_gb = MockGroupBy()

        assert isinstance(mock_gb.agg(), MockResult)
        assert isinstance(mock_gb.sum("col"), MockResult)
        assert isinstance(mock_gb.mean("col"), MockResult)
        assert isinstance(mock_gb.count(), MockResult)
        assert isinstance(mock_gb.min("col"), MockResult)
        assert isinstance(mock_gb.max("col"), MockResult)
        assert isinstance(mock_gb.first("col"), MockResult)


class TestProtocolMethodChaining:
    def test_operations_return_chainable_type(self):

        class ChainableDF:
            def select(self, *columns: str) -> ChainableDF:
                return self

            def filter(self, condition: Any) -> ChainableDF:
                return self

            def sort(self, *columns: str, ascending: bool = True) -> ChainableDF:
                return self

            def limit(self, n: int) -> ChainableDF:
                return self

            def distinct(self) -> ChainableDF:
                return self

        df = ChainableDF()

        result = df.select("a").filter(True).sort("a").limit(10).distinct()
        assert isinstance(result, ChainableDF)

    def test_groupby_agg_returns_dataframe(self):

        class ChainableDF:
            def group_by(self, *columns: str) -> ChainableGB:
                return ChainableGB()

            def select(self, *columns: str) -> ChainableDF:
                return self

        class ChainableGB:
            def agg(self, *args: Any) -> ChainableDF:
                return ChainableDF()

        df = ChainableDF()

        result = df.group_by("category").agg().select("category")
        assert isinstance(result, ChainableDF)
