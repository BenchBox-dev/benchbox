# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeVar

from benchbox.platforms.dataframe.datafusion_identifiers import (
    datafusion_column as df_col,
    datafusion_identifier,
    datafusion_identifiers,
)
from benchbox.utils.polars_collection import collect_frame

if TYPE_CHECKING:
    import polars as pl
    from datafusion import DataFrame as DataFusionDataFrame, Expr as DataFusionExpr
    from pyspark.sql import Column as PySparkColumn, DataFrame as PySparkDataFrame

    from benchbox.platforms.dataframe.expression_family import ExpressionFamilyAdapter
    from benchbox.platforms.dataframe.protocol import LazyFrameLike

logger = logging.getLogger(__name__)

DF = TypeVar("DF")
Expr = TypeVar("Expr")

_DATAFUSION_TYPE_MAPPING: dict[str, Any] | None = None
_NO_LITERAL_VALUE = object()


def _get_datafusion_type_mapping() -> dict[str, Any]:
    global _DATAFUSION_TYPE_MAPPING
    if _DATAFUSION_TYPE_MAPPING is None:
        import pyarrow as pa

        _DATAFUSION_TYPE_MAPPING = {
            "int8": pa.int8(),
            "int16": pa.int16(),
            "int32": pa.int32(),
            "int64": pa.int64(),
            "uint8": pa.uint8(),
            "uint16": pa.uint16(),
            "uint32": pa.uint32(),
            "uint64": pa.uint64(),
            "float32": pa.float32(),
            "float64": pa.float64(),
            "utf8": pa.utf8(),
            "string": pa.utf8(),
            "str": pa.utf8(),
            "bool": pa.bool_(),
            "boolean": pa.bool_(),
            "date": pa.date32(),
            "date32": pa.date32(),
        }
    return _DATAFUSION_TYPE_MAPPING


_PYSPARK_PYTHON_TYPE_MAP: dict[Any, Any] | None = None


def _pyspark_python_type(dtype: Any) -> Any:
    global _PYSPARK_PYTHON_TYPE_MAP
    if _PYSPARK_PYTHON_TYPE_MAP is None:
        from pyspark.sql import types as _pyspark_types

        _PYSPARK_PYTHON_TYPE_MAP = {
            int: _pyspark_types.LongType(),
            float: _pyspark_types.DoubleType(),
            str: _pyspark_types.StringType(),
            bool: _pyspark_types.BooleanType(),
        }
    return _PYSPARK_PYTHON_TYPE_MAP[dtype]


def _is_pyspark_column(expr: Any) -> bool:
    type_name = type(expr).__module__
    return "pyspark" in type_name and "Column" in type(expr).__name__


def _is_polars_expr(expr: Any) -> bool:
    type_name = type(expr).__module__
    return "polars" in type_name and "Expr" in type(expr).__name__


def _unwrap_unified_expr(value: Any) -> Any:
    if isinstance(value, UnifiedExpr):
        literal_value = getattr(value, "_literal_value", _NO_LITERAL_VALUE)
        return value._expr if literal_value is _NO_LITERAL_VALUE else literal_value
    return value


def _resolve_datafusion_value(value: Any, frame: Any) -> Any:
    while (
        frame is not None
        and isinstance(value, _DataFusionDeferredOperations)
        and value._datafusion_resolver is not None
    ):
        value = value._datafusion_resolver(frame)
    if isinstance(value, tuple):
        return tuple(_resolve_datafusion_value(item, frame) for item in value)
    if isinstance(value, list):
        return [_resolve_datafusion_value(item, frame) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_datafusion_value(item, frame) for key, item in value.items()}
    return value


def _has_datafusion_resolver(value: Any) -> bool:
    if isinstance(value, _DataFusionDeferredOperations):
        return value._datafusion_resolver is not None
    if isinstance(value, (tuple, list)):
        return any(_has_datafusion_resolver(item) for item in value)
    if isinstance(value, dict):
        return any(_has_datafusion_resolver(item) for item in value.values())
    return False


def _defer_datafusion_operation(operation: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(operation)
    def apply(*args: Any, **kwargs: Any) -> Any:
        result = operation(*args, **kwargs)
        if (
            isinstance(result, _DataFusionDeferredOperations)
            and all(result is not argument for argument in args)
            and (_has_datafusion_resolver(args) or _has_datafusion_resolver(kwargs))
        ):
            captured_args = _resolve_datafusion_value(args, None)
            captured_kwargs = _resolve_datafusion_value(kwargs, None)
            result._datafusion_resolver = lambda frame: operation(
                *_resolve_datafusion_value(captured_args, frame), **_resolve_datafusion_value(captured_kwargs, frame)
            )
        return result

    return apply


class _DataFusionDeferredOperations:
    _datafusion_resolver: Callable[[Any], Any] | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for name, member in tuple(vars(cls).items()):
            if name in {"__init__", "__repr__", "native"} or (name.startswith("_") and not name.endswith("__")):
                continue
            if isinstance(member, property) and member.fget is not None:
                setattr(
                    cls,
                    name,
                    property(_defer_datafusion_operation(member.fget), member.fset, member.fdel, member.__doc__),
                )
            elif callable(member):
                setattr(cls, name, _defer_datafusion_operation(member))


def _bind_datafusion_arguments(operation: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(operation)
    def apply(self: Any, *args: Any, **kwargs: Any) -> Any:
        frame = self._source_df if isinstance(self, UnifiedGroupBy) else self._df
        if _is_datafusion_df(frame):
            args = _resolve_datafusion_value(args, frame)
            kwargs = _resolve_datafusion_value(kwargs, frame)
        return operation(self, *args, **kwargs)

    return apply


def _datafusion_division(numerator: Any, denominator: Any) -> UnifiedExpr:
    from datafusion import lit as df_lit

    def native(value: Any) -> Any:
        value = value.native if isinstance(value, UnifiedExpr) else value
        return value if _is_datafusion_expr(value) else df_lit(value)

    def build(frame: Any = None) -> UnifiedExpr:
        import pyarrow as pa
        from datafusion import functions as df_f

        left = native(numerator if frame is None else _resolve_datafusion_value(numerator, frame))
        right = native(denominator if frame is None else _resolve_datafusion_value(denominator, frame))
        divisor = df_f.nullif(right, df_f.cast_to_type(df_lit(0), right))
        quotient = left / divisor
        if frame is not None and pa.types.is_integer(
            frame.select(quotient.alias("__division_type__")).schema()[0].type
        ):
            quotient = left.cast(pa.float64()) / divisor
        return UnifiedExpr(quotient)

    result = build()
    result._datafusion_resolver = build
    return result


class UnifiedStrExpr(_DataFusionDeferredOperations):
    def __init__(self, expr: Any, is_pyspark: bool, is_datafusion: bool = False) -> None:
        self._expr = expr
        self._is_pyspark = is_pyspark
        self._is_datafusion = is_datafusion

    def starts_with(self, prefix: str) -> UnifiedExpr:
        prefix = _unwrap_unified_expr(prefix)
        if self._is_pyspark:
            return UnifiedExpr(self._expr.startswith(prefix))
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            prefix = prefix if _is_datafusion_expr(prefix) else df_lit(prefix)
            return UnifiedExpr(df_f.starts_with(self._expr, prefix))
        return UnifiedExpr(self._expr.str.starts_with(prefix))

    def ends_with(self, suffix: str) -> UnifiedExpr:
        suffix = _unwrap_unified_expr(suffix)
        if self._is_pyspark:
            return UnifiedExpr(self._expr.endswith(suffix))
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            suffix = suffix if _is_datafusion_expr(suffix) else df_lit(suffix)
            return UnifiedExpr(df_f.ends_with(self._expr, suffix))
        return UnifiedExpr(self._expr.str.ends_with(suffix))

    def contains(self, pattern: str) -> UnifiedExpr:
        pattern = _unwrap_unified_expr(pattern)
        if self._is_pyspark:
            return UnifiedExpr(self._expr.rlike(pattern))
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            pattern = pattern if _is_datafusion_expr(pattern) else df_lit(pattern)
            return UnifiedExpr(df_f.regexp_like(self._expr, pattern))
        return UnifiedExpr(self._expr.str.contains(pattern))

    def replace(self, pattern: Any, value: Any) -> UnifiedExpr:
        pattern = _unwrap_unified_expr(pattern)
        value = _unwrap_unified_expr(value)
        if self._is_pyspark:
            from pyspark.sql.functions import regexp_replace

            return UnifiedExpr(regexp_replace(self._expr, pattern, value))
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            pattern = pattern if _is_datafusion_expr(pattern) else df_lit(pattern)
            value = value if _is_datafusion_expr(value) else df_lit(value)
            return UnifiedExpr(df_f.regexp_replace(self._expr, pattern, value))
        return UnifiedExpr(self._expr.str.replace(pattern, value))

    def slice(self, offset: int, length: int | None = None) -> UnifiedExpr:
        if self._is_pyspark:
            substr_length = length if length is not None else 1000000
            return UnifiedExpr(self._expr.substr(offset + 1, substr_length))
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            substr_length = length if length is not None else 1000000
            return UnifiedExpr(df_f.substring(self._expr, df_lit(offset + 1), df_lit(substr_length)))
        return UnifiedExpr(self._expr.str.slice(offset, length))

    def to_uppercase(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.upper(self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.upper(self._expr))
        return UnifiedExpr(self._expr.str.to_uppercase())

    def to_lowercase(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.lower(self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.lower(self._expr))
        return UnifiedExpr(self._expr.str.to_lowercase())

    def split(self, separator: str) -> UnifiedListExpr:
        separator = _unwrap_unified_expr(separator)
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedListExpr(F.split(self._expr, separator), is_pyspark=True)
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            sep = separator if _is_datafusion_expr(separator) else df_lit(separator)
            return UnifiedListExpr(df_f.string_to_array(self._expr, sep), is_datafusion=True)
        return UnifiedListExpr(self._expr.str.split(separator), is_polars=True)

    def len_chars(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.length(self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.character_length(self._expr))
        return UnifiedExpr(self._expr.str.len_chars())


class UnifiedListExpr(_DataFusionDeferredOperations):
    def __init__(
        self,
        expr: Any,
        *,
        is_pyspark: bool = False,
        is_datafusion: bool = False,
        is_polars: bool = False,
        _ordered_collect_descending: bool | None = None,
    ) -> None:
        self._expr = expr
        self._is_pyspark = is_pyspark or _is_pyspark_column(expr)
        self._is_datafusion = is_datafusion or _is_datafusion_expr(expr)
        self._is_polars = is_polars or _is_polars_expr(expr)
        self._ordered_collect_descending = _ordered_collect_descending

    def __call__(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            collected = F.collect_list(self._expr)
            if self._ordered_collect_descending is not None:
                collected = F.sort_array(collected, asc=not self._ordered_collect_descending)
            return UnifiedExpr(collected, _is_agg_array=True)
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.array_agg(self._expr))
        return UnifiedExpr(self._expr.implode())

    def _wrap(self, expr: Any) -> UnifiedListExpr:
        return UnifiedListExpr(
            expr,
            is_pyspark=self._is_pyspark,
            is_datafusion=self._is_datafusion,
            is_polars=self._is_polars,
        )

    def contains(self, value: Any) -> UnifiedExpr:
        val = value._expr if isinstance(value, UnifiedExpr) else value
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.array_contains(self._expr, val))
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            lit_val = df_lit(val) if not _is_datafusion_expr(val) else val
            return UnifiedExpr(df_f.array_has(self._expr, lit_val))
        return UnifiedExpr(self._expr.list.contains(val))

    def unique(self) -> UnifiedListExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return self._wrap(F.array_distinct(self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return self._wrap(df_f.array_distinct(self._expr))
        return self._wrap(self._expr.list.unique())

    def len(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.size(self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.array_length(self._expr))
        return UnifiedExpr(self._expr.list.len())

    def min(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.array_min(self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            return UnifiedExpr(df_f.array_element(df_f.array_sort(self._expr), df_lit(1)))
        return UnifiedExpr(self._expr.list.min())

    def max(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.array_max(self._expr))
        if self._is_datafusion:
            import pyarrow as pa
            from datafusion import functions as df_f

            return UnifiedExpr(
                df_f.array_element(df_f.array_sort(self._expr), df_f.array_length(self._expr).cast(pa.int64()))
            )
        return UnifiedExpr(self._expr.list.max())

    def sort(self, descending: bool = False) -> UnifiedListExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return self._wrap(F.sort_array(self._expr, asc=not descending))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return self._wrap(df_f.array_sort(self._expr))
        return self._wrap(self._expr.list.sort(descending=descending))

    def slice(self, offset: int, length: int) -> UnifiedListExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return self._wrap(F.slice(self._expr, offset + 1, length))
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            return self._wrap(df_f.array_slice(self._expr, df_lit(offset + 1), df_lit(length)))
        return self._wrap(self._expr.list.slice(offset, length))

    def sum(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.aggregate(self._expr, F.lit(0.0).cast("double"), lambda acc, x: acc + x))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.array_sum(self._expr))
        return UnifiedExpr(self._expr.list.sum())

    def get(self, index: int | Any) -> UnifiedExpr:
        native_index = index.native if isinstance(index, UnifiedExpr) else index
        if self._is_pyspark:
            return UnifiedExpr(self._expr.getItem(native_index))
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            if isinstance(native_index, int):
                native_index = df_lit(native_index + 1)
            else:
                import pyarrow as pa

                native_index = native_index.cast(pa.int64()) + 1
            return UnifiedExpr(df_f.array_element(self._expr, native_index))
        return UnifiedExpr(self._expr.list.get(native_index))

    def eval(self, expr: Any) -> UnifiedListExpr:
        native_expr = expr._expr if isinstance(expr, UnifiedExpr) else expr
        if self._is_polars:
            return self._wrap(self._expr.list.eval(native_expr))
        raise NotImplementedError("list.eval() is only supported on Polars")

    @property
    def list(self) -> UnifiedListExpr:
        return self

    @property
    def native(self) -> Any:
        return self._expr

    def alias(self, name: str) -> UnifiedExpr:
        return UnifiedExpr(self._expr.alias(name))


class UnifiedMapExpr(_DataFusionDeferredOperations):
    def __init__(self, expr: Any, is_pyspark: bool, is_datafusion: bool) -> None:
        self._expr = expr
        self._is_pyspark = is_pyspark
        self._is_datafusion = is_datafusion

    def get(self, key: Any) -> UnifiedExpr:
        key_val = key._expr if isinstance(key, UnifiedExpr) else key
        if self._is_pyspark:
            return UnifiedExpr(self._expr.getItem(key_val))
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            lit_key = df_lit(key_val) if not _is_datafusion_expr(key_val) else key_val
            return UnifiedExpr(df_f.map_extract(self._expr, lit_key))
        raise NotImplementedError("Map operations not supported on Polars (no native Map dtype)")

    def keys(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.map_keys(self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.map_keys(self._expr))
        raise NotImplementedError("Map operations not supported on Polars (no native Map dtype)")

    def values(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.map_values(self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.map_values(self._expr))
        raise NotImplementedError("Map operations not supported on Polars (no native Map dtype)")


class UnifiedDtExpr(_DataFusionDeferredOperations):
    def __init__(self, expr: Any, is_pyspark: bool, is_datafusion: bool = False) -> None:
        self._expr = expr
        self._is_pyspark = is_pyspark
        self._is_datafusion = is_datafusion

    def _extract_date_part(self, part: str, pyspark_fn_name: str | None = None) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(getattr(F, pyspark_fn_name or part)(self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.date_part(part, self._expr))
        return UnifiedExpr(getattr(self._expr.dt, part)())

    def year(self) -> UnifiedExpr:
        return self._extract_date_part("year")

    def month(self) -> UnifiedExpr:
        return self._extract_date_part("month")

    def day(self) -> UnifiedExpr:
        return self._extract_date_part("day", pyspark_fn_name="dayofmonth")

    def hour(self) -> UnifiedExpr:
        return self._extract_date_part("hour")

    def minute(self) -> UnifiedExpr:
        return self._extract_date_part("minute")

    def weekday(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr((F.dayofweek(self._expr) + 5) % 7)
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr((df_f.date_part("dow", self._expr) + 6) % 7)
        return UnifiedExpr(self._expr.dt.weekday() - 1)

    def truncate(self, every: str) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            fmt_map = {"1m": "minute", "1h": "hour", "1d": "day", "1w": "week", "1mo": "month", "1y": "year"}
            fmt = fmt_map.get(every, every)
            return UnifiedExpr(F.date_trunc(fmt, self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f

            fmt_map = {"1m": "minute", "1h": "hour", "1d": "day", "1w": "week", "1mo": "month", "1y": "year"}
            fmt = fmt_map.get(every, every)
            return UnifiedExpr(df_f.date_trunc(fmt, self._expr))
        return UnifiedExpr(self._expr.dt.truncate(every))

    def total_seconds(self) -> UnifiedExpr:
        if self._is_pyspark:
            return UnifiedExpr(self._expr.cast("long"))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.extract("epoch", self._expr))
        return UnifiedExpr(self._expr.dt.total_seconds())

    def total_days(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr((self._expr.cast("long") / F.lit(86400)).cast("long"))
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            return UnifiedExpr(df_f.extract("epoch", self._expr) / df_lit(86400))
        return UnifiedExpr(self._expr.dt.total_days())


class UnifiedStructExpr(_DataFusionDeferredOperations):
    def __init__(self, expr: Any, is_pyspark: bool = False, is_datafusion: bool = False) -> None:
        self._expr = expr
        self._is_pyspark = is_pyspark
        self._is_datafusion = is_datafusion

    def field(self, name: str) -> UnifiedExpr:
        if self._is_pyspark:
            return UnifiedExpr(self._expr.getField(name))
        if self._is_datafusion:
            return UnifiedExpr(self._expr[name])
        return UnifiedExpr(self._expr.struct.field(name))


class UnifiedExpr(_DataFusionDeferredOperations):
    def __init__(
        self,
        expr: Any,
        *,
        _is_string_literal: bool = False,
        _literal_value: Any = _NO_LITERAL_VALUE,
        _is_agg_array: bool = False,
        _ordered_collect_descending: bool | None = None,
    ) -> None:
        self._expr = expr
        self._is_pyspark = _is_pyspark_column(expr)
        self._is_datafusion = _is_datafusion_expr(expr)
        self._is_string_literal = _is_string_literal
        self._literal_value = _literal_value
        self._is_agg_array = _is_agg_array
        self._ordered_collect_descending = _ordered_collect_descending

    @property
    def native(self) -> Any:
        return self._expr

    def __repr__(self) -> str:
        return f"UnifiedExpr({self._expr})"

    @staticmethod
    def _unwrap(other: Any) -> Any:
        return other._expr if isinstance(other, UnifiedExpr) else other

    def __add__(self, other: Any) -> UnifiedExpr:
        other_expr = other._expr if isinstance(other, UnifiedExpr) else other

        self_is_string = getattr(self, "_is_string_literal", False)
        other_is_string = isinstance(other, str) or (
            isinstance(other, UnifiedExpr) and getattr(other, "_is_string_literal", False)
        )
        is_string_concat = self_is_string or other_is_string

        if self._is_pyspark and is_string_concat:
            from pyspark.sql import functions as F

            if isinstance(other, str):
                return UnifiedExpr(F.concat(self._expr, F.lit(other)), _is_string_literal=True)
            return UnifiedExpr(F.concat(self._expr, other_expr), _is_string_literal=True)

        if self._is_datafusion and is_string_concat:
            from datafusion import functions as df_f, lit as df_lit

            if isinstance(other, str):
                return UnifiedExpr(df_f.concat(self._expr, df_lit(other)), _is_string_literal=True)
            return UnifiedExpr(df_f.concat(self._expr, other_expr), _is_string_literal=True)

        return UnifiedExpr(self._expr + other_expr)

    def concat_str(self, *others: Any) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            exprs = [self._expr]
            for o in others:
                if isinstance(o, str):
                    exprs.append(F.lit(o))
                elif isinstance(o, UnifiedExpr):
                    exprs.append(o._expr)
                else:
                    exprs.append(o)
            return UnifiedExpr(F.concat(*exprs))

        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            exprs = [self._expr]
            for o in others:
                if isinstance(o, str):
                    exprs.append(df_lit(o))
                elif isinstance(o, UnifiedExpr):
                    exprs.append(o._expr)
                else:
                    exprs.append(o)
            return UnifiedExpr(df_f.concat(*exprs), _is_string_literal=True)

        import polars as pl

        result = self._expr
        for o in others:
            if isinstance(o, str):
                result = result + pl.lit(o)
            elif isinstance(o, UnifiedExpr):
                result = result + o._expr
            else:
                result = result + o
        return UnifiedExpr(result)

    def __radd__(self, other: Any) -> UnifiedExpr:
        other_expr = other._expr if isinstance(other, UnifiedExpr) else other

        if self._is_pyspark and isinstance(other, str):
            from pyspark.sql import functions as F

            return UnifiedExpr(F.concat(F.lit(other), self._expr))

        if self._is_datafusion and isinstance(other, str):
            from datafusion import functions as df_f, lit as df_lit

            return UnifiedExpr(df_f.concat(df_lit(other), self._expr), _is_string_literal=True)

        return UnifiedExpr(other_expr + self._expr)

    def __sub__(self, other: Any) -> UnifiedExpr:
        return UnifiedExpr(self._expr - self._unwrap(other))

    def __rsub__(self, other: Any) -> UnifiedExpr:
        return UnifiedExpr(self._unwrap(other) - self._expr)

    def __mul__(self, other: Any) -> UnifiedExpr:
        return UnifiedExpr(self._expr * self._unwrap(other))

    def __rmul__(self, other: Any) -> UnifiedExpr:
        return UnifiedExpr(self._unwrap(other) * self._expr)

    def __truediv__(self, other: Any) -> UnifiedExpr:
        if self._is_datafusion:
            return _datafusion_division(self, other)
        return UnifiedExpr(self._expr / self._unwrap(other))

    def __rtruediv__(self, other: Any) -> UnifiedExpr:
        if self._is_datafusion:
            return _datafusion_division(other, self)
        return UnifiedExpr(self._unwrap(other) / self._expr)

    def __eq__(self, other: Any) -> UnifiedExpr:
        other_expr = other._expr if isinstance(other, UnifiedExpr) else other
        result = UnifiedExpr(self._expr == other_expr)
        result._eq_left = self
        result._eq_right = other if isinstance(other, UnifiedExpr) else UnifiedExpr(other_expr)
        return result

    def __ne__(self, other: Any) -> UnifiedExpr:
        return UnifiedExpr(self._expr != self._unwrap(other))

    def __lt__(self, other: Any) -> UnifiedExpr:
        return UnifiedExpr(self._expr < self._unwrap(other))

    def __le__(self, other: Any) -> UnifiedExpr:
        return UnifiedExpr(self._expr <= self._unwrap(other))

    def __gt__(self, other: Any) -> UnifiedExpr:
        return UnifiedExpr(self._expr > self._unwrap(other))

    def __ge__(self, other: Any) -> UnifiedExpr:
        return UnifiedExpr(self._expr >= self._unwrap(other))

    def __and__(self, other: Any) -> UnifiedExpr:
        other_expr = self._unwrap(other)

        if self._is_pyspark and isinstance(other, int):
            col_expr = self._expr
            return UnifiedExpr((col_expr.cast("bigint").bitwiseAND(other)).cast("int"))

        if self._is_datafusion and isinstance(other, int):
            import pyarrow as pa
            from datafusion import lit as df_lit

            n = other
            if n == 1:
                return UnifiedExpr(self._expr % df_lit(2))
            elif n > 0 and (n & (n - 1)) == 0:
                div_expr = (self._expr / df_lit(n)).cast(pa.int64())
                return UnifiedExpr((div_expr % df_lit(2)) * df_lit(n))
            else:
                pass

        return UnifiedExpr(self._expr & other_expr)

    def __or__(self, other: Any) -> UnifiedExpr:
        return UnifiedExpr(self._expr | self._unwrap(other))

    def __invert__(self) -> UnifiedExpr:
        return UnifiedExpr(~self._expr)

    def _apply_aggregation(self, pyspark_name: str, datafusion_name: str, polars_name: str) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(getattr(F, pyspark_name)(self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(getattr(df_f, datafusion_name)(self._expr))
        return UnifiedExpr(getattr(self._expr, polars_name)())

    def sum(self) -> UnifiedExpr:
        return self._apply_aggregation("sum", "sum", "sum")

    def mean(self) -> UnifiedExpr:
        return self._apply_aggregation("avg", "avg", "mean")

    def avg(self) -> UnifiedExpr:
        return self.mean()

    def count(self) -> UnifiedExpr:
        return self._apply_aggregation("count", "count", "count")

    def min(self) -> UnifiedExpr:
        return self._apply_aggregation("min", "min", "min")

    def max(self) -> UnifiedExpr:
        return self._apply_aggregation("max", "max", "max")

    def first(self) -> UnifiedExpr:
        return self._apply_aggregation("first", "first_value", "first")

    def last(self) -> UnifiedExpr:
        return self._apply_aggregation("last", "last_value", "last")

    def std(self) -> UnifiedExpr:
        return self._apply_aggregation("stddev", "stddev", "std")

    def var(self) -> UnifiedExpr:
        return self._apply_aggregation("variance", "var_samp", "var")

    def quantile(self, q: float, interpolation: str = "nearest") -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.percentile_approx(self._expr, q))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.approx_percentile_cont(self._expr, q))
        return UnifiedExpr(self._expr.quantile(q, interpolation=interpolation))

    def alias(self, name: str) -> UnifiedExpr:
        return UnifiedExpr(
            self._expr.alias(name),
            _is_agg_array=self._is_agg_array,
            _ordered_collect_descending=self._ordered_collect_descending,
        )

    def cast(self, dtype: Any) -> UnifiedExpr:
        if self._is_pyspark and dtype in (int, float, str, bool):
            return UnifiedExpr(self._expr.cast(_pyspark_python_type(dtype)))

        if self._is_datafusion:
            import pyarrow as pa

            if isinstance(dtype, pa.DataType):
                return UnifiedExpr(self._expr.cast(dtype))

            dtype_str = str(dtype).lower()
            type_mapping = _get_datafusion_type_mapping()
            if dtype_str in type_mapping:
                return UnifiedExpr(self._expr.cast(type_mapping[dtype_str]))

            return UnifiedExpr(self._expr.cast(dtype))

        return UnifiedExpr(self._expr.cast(dtype))

    def round(self, decimals: int = 0) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.round(self._expr, decimals))
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            return UnifiedExpr(df_f.round(self._expr, df_lit(decimals)))
        return UnifiedExpr(self._expr.round(decimals))

    def floor(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.floor(self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.floor(self._expr))
        return UnifiedExpr(self._expr.floor())

    def abs(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.abs(self._expr))
        return UnifiedExpr(self._expr.abs())

    def cast_float(self) -> UnifiedExpr:
        return self.cast_float64()

    def _apply_cast(self, target_type: str) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql.types import DoubleType, IntegerType, LongType

            pyspark_types = {
                "float64": DoubleType,
                "int32": IntegerType,
                "int64": LongType,
            }
            return UnifiedExpr(self._expr.cast(pyspark_types[target_type]()))
        if self._is_datafusion:
            import pyarrow as pa

            datafusion_types = {
                "float64": pa.float64,
                "int32": pa.int32,
                "int64": pa.int64,
            }
            return UnifiedExpr(self._expr.cast(datafusion_types[target_type]()))

        import polars as pl

        polars_types = {
            "float64": pl.Float64,
            "int32": pl.Int32,
            "int64": pl.Int64,
        }
        return UnifiedExpr(self._expr.cast(polars_types[target_type]))

    def cast_float64(self) -> UnifiedExpr:
        return self._apply_cast("float64")

    def cast_string(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql.types import StringType

            return UnifiedExpr(self._expr.cast(StringType()))
        if self._is_datafusion:
            import pyarrow as pa

            return UnifiedExpr(self._expr.cast(pa.utf8()))

        import polars as pl

        return UnifiedExpr(self._expr.cast(pl.Utf8))

    def cast_date(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql.types import DateType

            return UnifiedExpr(self._expr.cast(DateType()))
        if self._is_datafusion:
            import pyarrow as pa

            return UnifiedExpr(self._expr.cast(pa.date32()))

        from benchbox.platforms.dataframe.polars_df import polars_cast_date

        return UnifiedExpr(polars_cast_date(self._expr))

    def cast_int32(self) -> UnifiedExpr:
        return self._apply_cast("int32")

    def cast_int64(self) -> UnifiedExpr:
        return self._apply_cast("int64")

    def cast_int(self) -> UnifiedExpr:
        return self.cast_int32()

    def is_in(self, values: list) -> UnifiedExpr:
        if self._is_pyspark:
            return UnifiedExpr(self._expr.isin(values))
        if self._is_datafusion:
            from datafusion import functions as df_f, lit as df_lit

            lit_values = [df_lit(v) for v in values]
            return UnifiedExpr(df_f.in_list(self._expr, lit_values, negated=False))
        return UnifiedExpr(self._expr.is_in(values))

    def n_unique(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.countDistinct(self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.count(self._expr, distinct=True))
        return UnifiedExpr(self._expr.n_unique())

    def approx_n_unique(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.approx_count_distinct(self._expr))
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.approx_distinct(self._expr))
        return UnifiedExpr(self._expr.approx_n_unique())

    def filter(self, condition: Any) -> UnifiedExpr:
        cond = condition.native if isinstance(condition, UnifiedExpr) else condition
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.when(cond, self._expr))
        if self._is_datafusion:
            return _DataFusionDeferredFilter(self._expr, cond)
        return UnifiedExpr(self._expr.filter(cond))

    def is_null(self) -> UnifiedExpr:
        if self._is_pyspark:
            return UnifiedExpr(self._expr.isNull())
        return UnifiedExpr(self._expr.is_null())

    def is_not_null(self) -> UnifiedExpr:
        if self._is_pyspark:
            return UnifiedExpr(self._expr.isNotNull())
        return UnifiedExpr(self._expr.is_not_null())

    def fill_null(self, value: Any) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            fill_value = value._expr if isinstance(value, UnifiedExpr) else F.lit(value)
            return UnifiedExpr(F.coalesce(self._expr, fill_value))
        fill_value = value._expr if isinstance(value, UnifiedExpr) else value
        return UnifiedExpr(self._expr.fill_null(fill_value))

    def is_between(self, low: Any, high: Any) -> UnifiedExpr:
        low_val = low._expr if isinstance(low, UnifiedExpr) else low
        high_val = high._expr if isinstance(high, UnifiedExpr) else high

        if self._is_pyspark:
            return UnifiedExpr(self._expr.between(low_val, high_val))
        if self._is_datafusion:
            from datafusion import lit as df_lit

            if not _is_datafusion_expr(low_val):
                low_val = df_lit(low_val)
            if not _is_datafusion_expr(high_val):
                high_val = df_lit(high_val)
            return UnifiedExpr((self._expr >= low_val) & (self._expr <= high_val))
        return UnifiedExpr(self._expr.is_between(low_val, high_val))

    def rank(self, method: str = "min", descending: bool = False) -> UnifiedExpr:
        if self._is_pyspark:
            return _PySparkDeferredRank(self._expr, method, descending)
        if self._is_datafusion:
            return _DataFusionDeferredRank(self._expr, method, descending)
        return UnifiedExpr(self._expr.rank(method=method, descending=descending))

    def over(
        self,
        partition_by: str | list[str],
        order_by: str | None = None,
    ) -> UnifiedExpr:
        partition_cols = [partition_by] if isinstance(partition_by, str) else list(partition_by)

        if self._is_pyspark:
            from pyspark.sql import functions as F
            from pyspark.sql.window import Window

            window = Window.partitionBy(*[F.col(c) for c in partition_cols])
            if order_by:
                window = window.orderBy(F.col(order_by))

            return UnifiedExpr(self._expr.over(window))

        if self._is_datafusion:
            from datafusion.expr import Window

            partition_exprs = [df_col(c) if isinstance(c, str) else c for c in partition_cols]

            if order_by:
                order_exprs = [df_col(order_by).sort(ascending=True)]
                window = Window(partition_by=partition_exprs, order_by=order_exprs)
            else:
                window = Window(partition_by=partition_exprs)

            return UnifiedExpr(self._expr.over(window))

        if order_by is not None:
            return UnifiedExpr(self._expr.over(partition_cols, order_by=order_by))
        return UnifiedExpr(self._expr.over(partition_cols))

    def cum_sum(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.sum(self._expr))
        if self._is_datafusion:
            return _DataFusionDeferredCumulative(self._expr, "sum")
        return UnifiedExpr(self._expr.cum_sum())

    def cum_max(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.max(self._expr))
        if self._is_datafusion:
            return _DataFusionDeferredCumulative(self._expr, "max")
        return UnifiedExpr(self._expr.cum_max())

    def cum_min(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.min(self._expr))
        return UnifiedExpr(self._expr.cum_min())

    def sort_by(self, column: str | Any, descending: bool = False) -> UnifiedExpr:
        if self._is_datafusion:
            from datafusion import functions as df_f

            col_name = column._expr if isinstance(column, UnifiedExpr) else column
            if isinstance(col_name, str):
                order_expr = df_col(col_name).sort(ascending=not descending)
            else:
                order_expr = col_name.sort(ascending=not descending)
            return UnifiedExpr(df_f.array_agg(self._expr, order_by=[order_expr]))
        if self._is_pyspark:
            from pyspark.sql import functions as F

            key = column._expr if isinstance(column, UnifiedExpr) else column
            if isinstance(key, str):
                key = F.col(key)
            ordered = F.sort_array(F.collect_list(F.struct(key.alias("__sort_key"), self._expr.alias("__sort_value"))))
            if descending:
                ordered = F.reverse(ordered)
            return UnifiedExpr(F.transform(ordered, lambda entry: entry.getField("__sort_value")), _is_agg_array=True)
        col_name = column._expr if isinstance(column, UnifiedExpr) else column
        return UnifiedExpr(self._expr.sort_by(col_name, descending=descending))

    def unique(self) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            return UnifiedExpr(F.collect_set(self._expr), _is_agg_array=True)
        if self._is_datafusion:
            from datafusion import functions as df_f

            return UnifiedExpr(df_f.array_agg(self._expr, distinct=True))
        return UnifiedExpr(self._expr.unique())

    def sort(self, descending: bool = False) -> UnifiedExpr:
        if self._is_pyspark:
            from pyspark.sql import functions as F

            if self._is_agg_array:
                return UnifiedExpr(F.sort_array(self._expr, asc=not descending), _is_agg_array=True)
            return UnifiedExpr(self._expr, _ordered_collect_descending=descending)
        if self._is_datafusion:
            return UnifiedExpr(self._expr)
        return UnifiedExpr(self._expr.sort(descending=descending))

    def desc(self) -> UnifiedExpr:
        if self._is_datafusion:
            return UnifiedExpr(self._expr.sort(ascending=False, nulls_first=False))
        elif self._is_pyspark:
            return UnifiedExpr(self._expr.desc())
        marked = UnifiedExpr(self._expr)
        marked._sort_descending = True
        return marked

    @property
    def str(self) -> UnifiedStrExpr:
        return UnifiedStrExpr(self._expr, self._is_pyspark, self._is_datafusion)

    @property
    def dt(self) -> UnifiedDtExpr:
        return UnifiedDtExpr(self._expr, self._is_pyspark, self._is_datafusion)

    @property
    def list(self) -> UnifiedListExpr:
        return UnifiedListExpr(
            self._expr,
            is_pyspark=self._is_pyspark,
            is_datafusion=self._is_datafusion,
            is_polars=not self._is_pyspark and not self._is_datafusion,
            _ordered_collect_descending=self._ordered_collect_descending,
        )

    @property
    def map(self) -> UnifiedMapExpr:
        return UnifiedMapExpr(self._expr, self._is_pyspark, self._is_datafusion)

    @property
    def struct(self) -> UnifiedStructExpr:
        return UnifiedStructExpr(self._expr, self._is_pyspark, self._is_datafusion)


class _PySparkDeferredRank(UnifiedExpr):
    def __init__(self, expr: PySparkColumn, method: str, descending: bool) -> None:
        super().__init__(expr)
        self._rank_method = method
        self._rank_descending = descending

    def over(
        self,
        partition_by: str | list[str],
        order_by: str | None = None,
    ) -> UnifiedExpr:
        from pyspark.sql import functions as F
        from pyspark.sql.window import Window

        partition_cols = [partition_by] if isinstance(partition_by, str) else list(partition_by)

        window = Window.partitionBy(*[F.col(c) for c in partition_cols])

        order_expr = self._expr.desc() if self._rank_descending else self._expr.asc()
        window = window.orderBy(order_expr)

        if self._rank_method == "min":
            rank_expr = F.rank().over(window)
        elif self._rank_method == "dense":
            rank_expr = F.dense_rank().over(window)
        elif self._rank_method == "ordinal":
            rank_expr = F.row_number().over(window)
        elif self._rank_method == "average":
            rank_expr = F.rank().over(window)
        else:
            rank_expr = F.rank().over(window)

        return UnifiedExpr(rank_expr)


_DATAFUSION_RANK_METHODS = frozenset({"min", "max", "dense", "ordinal", "average"})


def _datafusion_whole_frame_rank(expr: DataFusionExpr, method: str, descending: bool) -> DataFusionExpr:
    from datafusion import functions as df_f, lit as df_lit
    from datafusion.expr import Window

    nulls_last_window = Window(order_by=[expr.sort(ascending=not descending, nulls_first=False)])
    rows_sharing_value = df_f.count(expr).over(Window(partition_by=[expr]))
    min_rank = df_f.rank().over(nulls_last_window)
    if method == "min":
        ranked = min_rank
    elif method == "dense":
        ranked = df_f.dense_rank().over(nulls_last_window)
    elif method == "ordinal":
        ranked = df_f.row_number().over(nulls_last_window)
    elif method == "max":
        ranked = min_rank + rows_sharing_value - df_lit(1)
    elif method == "average":
        ranked = min_rank + (rows_sharing_value - df_lit(1)) / df_lit(2.0)
    else:
        raise ValueError(f"Unsupported rank method: {method!r}. Expected one of {sorted(_DATAFUSION_RANK_METHODS)}.")
    return df_f.when(expr.is_null(), df_lit(None)).otherwise(ranked)


class _DataFusionDeferredRank(UnifiedExpr):
    def __init__(self, expr: DataFusionExpr, method: str, descending: bool) -> None:
        super().__init__(_datafusion_whole_frame_rank(expr, method, descending))
        self._rank_source = expr
        self._rank_method = method
        self._rank_descending = descending

    def over(
        self,
        partition_by: str | list[str],
        order_by: str | None = None,
    ) -> UnifiedExpr:
        from datafusion import functions as df_f
        from datafusion.expr import Window

        partition_cols = [partition_by] if isinstance(partition_by, str) else list(partition_by)
        partition_exprs = [df_col(c) if isinstance(c, str) else c for c in partition_cols]

        order_expr = self._rank_source.sort(ascending=not self._rank_descending, nulls_first=self._rank_descending)

        window = Window(partition_by=partition_exprs, order_by=[order_expr])

        if self._rank_method == "min":
            rank_func = df_f.rank()
        elif self._rank_method == "dense":
            rank_func = df_f.dense_rank()
        elif self._rank_method == "ordinal":
            rank_func = df_f.row_number()
        elif self._rank_method == "average":
            rank_func = df_f.rank()
        else:
            rank_func = df_f.rank()

        return UnifiedExpr(rank_func.over(window))


class _DataFusionDeferredCumulative(UnifiedExpr):
    def __init__(self, expr: DataFusionExpr, operation: str) -> None:
        super().__init__(expr)
        self._operation = operation

    def over(self, partition_by: str | list[str], order_by: str | None = None) -> UnifiedExpr:
        from datafusion import functions as df_f
        from datafusion.expr import Window, WindowFrame

        if order_by is None:
            raise ValueError("DataFusion cumulative windows require an explicit order_by column")
        partition_cols = [partition_by] if isinstance(partition_by, str) else list(partition_by)
        window = Window(
            partition_by=[df_col(column) for column in partition_cols],
            order_by=[df_col(order_by).sort(ascending=True, nulls_first=False)],
            window_frame=WindowFrame("rows", None, 0),
        )
        aggregate = df_f.sum(self._expr) if self._operation == "sum" else df_f.max(self._expr)
        return UnifiedExpr(aggregate.over(window))


class _DataFusionDeferredFilter(UnifiedExpr):
    def __init__(self, expr: DataFusionExpr, condition: DataFusionExpr) -> None:
        super().__init__(expr)
        self._filter_condition = condition

    def _apply_filtered_agg(self, agg_func: Any) -> UnifiedExpr:

        agg_expr = agg_func(self._expr)
        filtered = agg_expr.filter(self._filter_condition).build()
        return UnifiedExpr(filtered)

    def sum(self) -> UnifiedExpr:
        from datafusion import functions as df_f

        return self._apply_filtered_agg(df_f.sum)

    def count(self) -> UnifiedExpr:
        from datafusion import functions as df_f

        return self._apply_filtered_agg(df_f.count)

    def mean(self) -> UnifiedExpr:
        from datafusion import functions as df_f

        return self._apply_filtered_agg(df_f.avg)

    def avg(self) -> UnifiedExpr:
        return self.mean()

    def min(self) -> UnifiedExpr:
        from datafusion import functions as df_f

        return self._apply_filtered_agg(df_f.min)

    def max(self) -> UnifiedExpr:
        from datafusion import functions as df_f

        return self._apply_filtered_agg(df_f.max)


class UnifiedWhenThen(_DataFusionDeferredOperations):
    def __init__(self, when_builder: Any, platform: str, when_pairs: list | None = None) -> None:
        self._when_builder = when_builder
        self._platform = platform
        self._when_pairs = when_pairs or []

    def when(self, condition: Any) -> UnifiedWhen:
        cond = condition._expr if isinstance(condition, UnifiedExpr) else condition

        if self._platform == "PySpark":
            return UnifiedWhen(self._when_builder.when(cond), platform="PySpark")
        if self._platform == "DataFusion":
            return UnifiedWhen(cond, platform="DataFusion", when_pairs=self._when_pairs)
        return UnifiedWhen(self._when_builder.when(cond), platform="Polars")

    def otherwise(self, value: Any) -> UnifiedExpr:
        val = value._expr if isinstance(value, UnifiedExpr) else value

        if self._platform == "PySpark":
            from pyspark.sql import functions as F

            return UnifiedExpr(self._when_builder.otherwise(val if val is not None else F.lit(None)))

        if self._platform == "DataFusion":
            from datafusion import functions as df_f, lit as df_lit

            if val is None or not _is_datafusion_expr(val):
                val = df_lit(val)

            if len(self._when_pairs) > 1:
                case_exprs = []
                for cond, then_val in self._when_pairs:
                    case_expr = df_f.case(cond).when(df_lit(True), then_val).end()
                    case_exprs.append(case_expr)
                case_exprs.append(val)
                return UnifiedExpr(df_f.coalesce(*case_exprs))
            else:
                return UnifiedExpr(self._when_builder.otherwise(val))

        return UnifiedExpr(self._when_builder.otherwise(val))


class UnifiedWhen(_DataFusionDeferredOperations):
    def __init__(self, when_builder: Any, platform: str, when_pairs: list | None = None) -> None:
        self._when_builder = when_builder
        self._platform = platform
        self._when_pairs = when_pairs or []

    def then(self, value: Any) -> UnifiedWhenThen:
        val = value._expr if isinstance(value, UnifiedExpr) else value

        if self._platform == "PySpark":
            from pyspark.sql import functions as F

            when_expr = F.when(self._when_builder, val)
            return UnifiedWhenThen(when_expr, platform="PySpark")

        if self._platform == "DataFusion":
            from datafusion import functions as df_f, lit as df_lit

            cond = self._when_builder

            if not _is_datafusion_expr(val):
                val = df_lit(val)

            new_pairs = self._when_pairs + [(cond, val)]

            case_builder = df_f.case(cond).when(df_lit(True), val)

            return UnifiedWhenThen(case_builder, platform="DataFusion", when_pairs=new_pairs)

        return UnifiedWhenThen(self._when_builder.then(val), platform="Polars")


def wrap_expr(expr: Any) -> UnifiedExpr:
    if isinstance(expr, UnifiedExpr):
        return expr
    return UnifiedExpr(expr)


def _is_pyspark_df(df: Any) -> bool:
    type_name = type(df).__module__
    return "pyspark" in type_name


def _is_polars_df(df: Any) -> bool:
    type_name = type(df).__module__
    return "polars" in type_name


def _is_datafusion_df(df: Any) -> bool:
    type_name = type(df).__module__
    return "datafusion" in type_name


def _is_datafusion_expr(expr: Any) -> bool:
    type_name = type(expr).__module__
    return "datafusion" in type_name


class UnifiedGroupBy(Generic[DF, Expr]):
    def __init__(
        self,
        grouped: Any,
        columns: list[str],
        adapter: ExpressionFamilyAdapter,
        source_df: Any,
    ) -> None:
        self._grouped = grouped
        self._columns = columns
        self._adapter = adapter
        self._source_df = source_df

    @_bind_datafusion_arguments
    def agg(self, *exprs: Expr) -> UnifiedLazyFrame:
        if len(exprs) == 1 and isinstance(exprs[0], list):
            exprs = tuple(exprs[0])

        unwrapped = [e.native if isinstance(e, UnifiedExpr) else e for e in exprs]

        if _is_pyspark_df(self._source_df) or _is_polars_df(self._source_df):
            result = self._grouped.agg(*unwrapped)
        elif _is_datafusion_df(self._source_df):
            result = self._source_df.aggregate(self._columns, unwrapped)
        else:
            result = self._grouped.agg(*unwrapped)

        return UnifiedLazyFrame(result, self._adapter)


def _normalize_join_keys(on: Any, left_on: Any, right_on: Any, is_cross_join: bool) -> tuple[list, list, bool]:

    def has_expressions(val: Any) -> bool:
        if isinstance(val, UnifiedExpr):
            return True
        if isinstance(val, (list, tuple)):
            return any(isinstance(item, UnifiedExpr) for item in val)
        return False

    left_has_expr = has_expressions(left_on)
    right_has_expr = has_expressions(right_on)
    has_expr_join = left_has_expr or right_has_expr

    if is_cross_join:
        return [], [], has_expr_join

    if on is not None:
        left_items = [on] if isinstance(on, str) else list(on)
        return left_items, left_items.copy(), has_expr_join

    if left_on is None:
        left_items = []
    elif isinstance(left_on, (str, UnifiedExpr)):
        left_items = [left_on]
    else:
        left_items = list(left_on)

    if right_on is None:
        right_items = []
    elif isinstance(right_on, (str, UnifiedExpr)):
        right_items = [right_on]
    else:
        right_items = list(right_on)

    if (
        len(left_items) == 1
        and not right_items
        and isinstance(left_items[0], UnifiedExpr)
        and hasattr(left_items[0], "_eq_left")
    ):
        eq_expr = left_items[0]
        left_items = [eq_expr._eq_left]
        right_items = [eq_expr._eq_right]
        has_expr_join = True

    return left_items, right_items, has_expr_join


def _rename_duplicate_right_columns(other_df: Any, duplicate_cols: set[str], used_names: set[str], suffix: str) -> Any:
    for col_name in duplicate_cols:
        new_name = f"{col_name}{suffix}"
        counter = 2
        while new_name in used_names:
            new_name = f"{col_name}{suffix}_{counter}"
            counter += 1
        other_df = other_df.with_column_renamed(datafusion_identifier(col_name), new_name)
        used_names.add(new_name)
    return other_df


def _rename_conflicting_join_keys(
    other_df: Any,
    left_cols: list[str],
    right_cols: list[str],
    left_columns: set[str],
    used_names: set[str],
    suffix: str,
    is_outer_join: bool,
) -> tuple[Any, dict[str, str]]:
    right_join_key_renames: dict[str, str] = {}
    for i, (left_key, right_key) in enumerate(zip(left_cols, right_cols)):
        if right_key == left_key or (right_key in left_columns and right_key != left_key):
            if is_outer_join:
                new_right_key = f"{right_key}{suffix}"
                counter = 2
                while new_right_key in used_names:
                    new_right_key = f"{right_key}{suffix}_{counter}"
                    counter += 1
                used_names.add(new_right_key)
            else:
                new_right_key = f"__right_join_key_{i}__"
            other_df = other_df.with_column_renamed(datafusion_identifier(right_key), new_right_key)
            right_join_key_renames[right_key] = new_right_key
    return other_df, right_join_key_renames


def _normalize_sort_inputs(
    by: Any,
    more_columns: tuple,
    descending: bool | list[bool],
) -> tuple[list, list[bool]]:
    cols = list(by) if isinstance(by, list) else [by]
    cols.extend(more_columns)

    tuple_desc_flags: list[bool] | None = None
    if cols and isinstance(cols[0], tuple):
        tuple_desc_flags = []
        parsed_cols = []
        for item in cols:
            if isinstance(item, tuple):
                col_name, direction = item
                parsed_cols.append(col_name)
                tuple_desc_flags.append(direction.lower().startswith("desc"))
            else:
                parsed_cols.append(item)
                tuple_desc_flags.append(False)
        cols = parsed_cols

    if tuple_desc_flags is not None:
        desc_flags = tuple_desc_flags
    elif isinstance(descending, bool):
        desc_flags = [descending] * len(cols)
    else:
        desc_flags = list(descending)
        if len(desc_flags) < len(cols):
            desc_flags.extend([False] * (len(cols) - len(desc_flags)))

    for i, c in enumerate(cols):
        if getattr(c, "_sort_descending", False):
            desc_flags[i] = True
    return cols, desc_flags


def _sort_pyspark(df, cols: list, desc_flags: list[bool], nulls_last: bool):
    from pyspark.sql import functions as F

    order_exprs = []
    for col_item, desc in zip(cols, desc_flags, strict=False):
        if isinstance(col_item, UnifiedExpr):
            expr = col_item.native
        elif isinstance(col_item, str):
            expr = F.col(col_item)
        else:
            expr = col_item

        if desc:
            order_exprs.append(expr.desc_nulls_last() if nulls_last else expr.desc())
        else:
            order_exprs.append(expr.asc_nulls_last() if nulls_last else expr.asc())
    return df.orderBy(*order_exprs)


def _sort_datafusion(df, cols: list, desc_flags: list[bool], nulls_last: bool):
    from datafusion.expr import SortExpr

    sort_exprs = []
    for col_item, desc in zip(cols, desc_flags, strict=False):
        if isinstance(col_item, UnifiedExpr):
            expr = col_item.native
        elif isinstance(col_item, str):
            expr = df_col(col_item)
        else:
            expr = col_item
        if isinstance(expr, SortExpr):
            sort_exprs.append(expr)
        else:
            sort_exprs.append(expr.sort(ascending=not desc, nulls_first=not nulls_last))
    return df.sort(*sort_exprs)


def _prepare_join_items(df, items: list, side: str) -> tuple[Any, list[str], list[str]]:
    join_cols: list[str] = []
    temp_cols: list[str] = []
    for i, item in enumerate(items):
        item = _resolve_datafusion_value(item, df)
        if isinstance(item, UnifiedExpr):
            temp_col = f"__{side}_join_key_{i}__"
            df = df.with_column(temp_col, item.native)
            join_cols.append(temp_col)
            temp_cols.append(temp_col)
        elif isinstance(item, str):
            join_cols.append(item)
        elif _is_datafusion_expr(item):
            temp_col = f"__{side}_join_key_{i}__"
            df = df.with_column(temp_col, item)
            join_cols.append(temp_col)
            temp_cols.append(temp_col)
        else:
            join_cols.append(str(item))
    return df, join_cols, temp_cols


def _rename_same_named_join_keys(
    right_df,
    left_join_cols: list[str],
    right_join_cols: list[str],
    temp_cols_right: list[str],
    left_columns: set[str],
    used_names: set[str],
    is_outer_join: bool,
    suffix: str,
) -> tuple[Any, dict[str, str]]:
    renames: dict[str, str] = {}
    for i, (left_key, right_key) in enumerate(zip(left_join_cols, right_join_cols)):
        collides = right_key == left_key or (right_key in left_columns and right_key not in temp_cols_right)
        if not collides:
            continue
        if is_outer_join:
            new_right_key = f"{right_key}{suffix}"
            counter = 2
            while new_right_key in used_names:
                new_right_key = f"{right_key}{suffix}_{counter}"
                counter += 1
            used_names.add(new_right_key)
        else:
            new_right_key = f"__expr_join_right_key_{i}__"
        right_df = right_df.with_column_renamed(datafusion_identifier(right_key), new_right_key)
        renames[right_key] = new_right_key
    return right_df, renames


def _drop_post_join_temp_columns(
    result,
    temp_cols_left: list[str],
    temp_cols_right: list[str],
    right_join_key_renames: dict[str, str],
    is_outer_join: bool,
    suffix: str,
):
    if not is_outer_join:
        for new_key in right_join_key_renames.values():
            if new_key in [field.name for field in result.schema()]:
                result = result.drop(new_key)
    for temp_col in temp_cols_left:
        if temp_col in [field.name for field in result.schema()]:
            result = result.drop(temp_col)
    for temp_col in temp_cols_right:
        for col_name in (temp_col, f"{temp_col}{suffix}"):
            if col_name in [field.name for field in result.schema()]:
                result = result.drop(col_name)
    return result


class UnifiedLazyFrame(Generic[DF, Expr]):
    def __init__(self, df: DF, adapter: ExpressionFamilyAdapter) -> None:
        self._df = df
        self._adapter = adapter

    @property
    def native(self) -> DF:
        return self._df

    @property
    def columns(self) -> list[str]:
        if _is_datafusion_df(self._df):
            return [field.name for field in self._df.schema()]
        if hasattr(self._df, "collect_schema"):
            return self._df.collect_schema().names()
        return list(self._df.columns)

    def join(
        self,
        other: UnifiedLazyFrame | LazyFrameLike,
        left_on: str | list | None = None,
        right_on: str | list | Any | None = None,
        on: str | list[str] | None = None,
        how: str = "inner",
        suffix: str = "_right",
    ) -> UnifiedLazyFrame:
        other_df = other._df if isinstance(other, UnifiedLazyFrame) else other

        is_cross_join = how == "cross" or (on is None and left_on is None and right_on is None)

        left_items, right_items, has_expr_join = _normalize_join_keys(on, left_on, right_on, is_cross_join)

        if _is_pyspark_df(self._df):
            if is_cross_join:
                result = self._pyspark_cross_join(other_df, suffix)
            elif has_expr_join:
                result = self._pyspark_join_multi_expr(other_df, left_items, right_items, how, suffix)
            else:
                result = self._pyspark_join(other_df, left_items, right_items, how, suffix)
        elif _is_polars_df(self._df):
            if how == "outer":
                how = "full"
            if is_cross_join:
                result = self._df.join(other_df, how="cross", suffix=suffix)
            elif has_expr_join:
                result = self._polars_join_with_exprs(other_df, left_items, right_items, how, suffix)
            else:
                left_cols = [item for item in left_items if isinstance(item, str)]
                right_cols = [item for item in right_items if isinstance(item, str)]
                result = self._df.join(
                    other_df,
                    left_on=left_cols if len(left_cols) > 1 else left_cols[0],
                    right_on=right_cols if len(right_cols) > 1 else right_cols[0],
                    how=how,
                    suffix=suffix,
                )
        elif _is_datafusion_df(self._df):
            if how == "outer":
                how = "full"

            if is_cross_join:
                result = self._datafusion_cross_join(other_df, suffix)
            elif has_expr_join:
                result = self._datafusion_join_with_exprs(other_df, left_items, right_items, how, suffix)
            else:
                result = self._datafusion_standard_join(other_df, left_items, right_items, how, suffix)
        else:
            left_cols = [item for item in left_items if isinstance(item, str)]
            right_cols = [item for item in right_items if isinstance(item, str)]
            result = self._df.join(
                other_df,
                left_on=left_cols,
                right_on=right_cols,
                how=how,
            )

        return UnifiedLazyFrame(result, self._adapter)

    def _datafusion_cross_join(self, other_df: Any, suffix: str) -> Any:
        from datafusion import lit as df_lit

        left_columns = {field.name for field in self._df.schema()}
        right_columns = {field.name for field in other_df.schema()}
        duplicate_cols = left_columns & right_columns

        renamed_other = _rename_duplicate_right_columns(other_df, duplicate_cols, set(left_columns), suffix)

        left_with_key = self._df.with_column("__cross_key_left__", df_lit(1))
        right_with_key = renamed_other.with_column("__cross_key_right__", df_lit(1))
        result = left_with_key.join(
            right_with_key,
            left_on=["__cross_key_left__"],
            right_on=["__cross_key_right__"],
            how="inner",
        )
        return result.drop("__cross_key_left__").drop("__cross_key_right__")

    def _datafusion_standard_join(
        self,
        other_df: Any,
        left_items: list,
        right_items: list,
        how: str,
        suffix: str,
    ) -> Any:
        left_cols = [item for item in left_items if isinstance(item, str)]
        right_cols = [item for item in right_items if isinstance(item, str)]

        left_columns = {field.name for field in self._df.schema()}
        right_columns = {field.name for field in other_df.schema()}

        join_keys_on_right = set(right_cols)
        non_join_right_cols = right_columns - join_keys_on_right
        duplicate_cols = left_columns & non_join_right_cols

        used_names = set(left_columns)
        renamed_other = _rename_duplicate_right_columns(other_df, duplicate_cols, used_names, suffix)

        is_outer_join = how in ("full", "outer", "left", "right")
        renamed_other, right_join_key_renames = _rename_conflicting_join_keys(
            renamed_other, left_cols, right_cols, left_columns, used_names, suffix, is_outer_join
        )

        actual_right_cols = [right_join_key_renames.get(c, c) for c in right_cols]
        result = self._df.join(
            renamed_other,
            left_on=datafusion_identifiers(left_cols),
            right_on=datafusion_identifiers(actual_right_cols),
            how=how,
        )

        if not is_outer_join:
            for _old_key, new_key in right_join_key_renames.items():
                if new_key in [field.name for field in result.schema()]:
                    result = result.drop(new_key)
        return result

    def _pyspark_join(
        self,
        other: PySparkDataFrame,
        left_cols: list[str],
        right_cols: list[str],
        how: str,
        suffix: str = "_right",
    ) -> PySparkDataFrame:
        join_map = {
            "inner": "inner",
            "left": "left",
            "right": "right",
            "outer": "outer",
            "full": "outer",
            "semi": "leftsemi",
            "leftsemi": "leftsemi",
            "anti": "leftanti",
            "leftanti": "leftanti",
        }
        spark_how = join_map.get(how, how)

        left_columns = set(self._df.columns)
        right_columns = set(other.columns)
        duplicate_cols = left_columns & right_columns

        same_join_cols = left_cols == right_cols

        is_outer_join = how in ("full", "outer")

        renamed_other = other
        for col_name in duplicate_cols:
            if is_outer_join and same_join_cols or col_name not in right_cols:
                new_name = f"{col_name}{suffix}"
                renamed_other = renamed_other.withColumnRenamed(col_name, new_name)

        if is_outer_join and same_join_cols:
            conditions = [
                self._df[lc] == renamed_other[f"{rc}{suffix}"] for lc, rc in zip(left_cols, right_cols, strict=True)
            ]
            condition = conditions[0]
            for c in conditions[1:]:
                condition = condition & c
            result = self._df.join(renamed_other, condition, spark_how)
        elif same_join_cols:
            join_cols = left_cols if len(left_cols) > 1 else left_cols[0]
            result = self._df.join(renamed_other, join_cols, spark_how)
        else:
            conditions = [self._df[lc] == renamed_other[rc] for lc, rc in zip(left_cols, right_cols, strict=True)]
            condition = conditions[0]
            for c in conditions[1:]:
                condition = condition & c
            result = self._df.join(renamed_other, condition, spark_how)
            if how not in ("semi", "leftsemi", "anti", "leftanti", "full", "outer"):
                for lc, rc in zip(left_cols, right_cols, strict=True):
                    if lc != rc and rc in result.columns:
                        result = result.drop(rc)

        return result

    def _pyspark_cross_join(
        self,
        other: PySparkDataFrame,
        suffix: str = "_right",
    ) -> PySparkDataFrame:
        left_columns = set(self._df.columns)
        right_columns = set(other.columns)
        duplicate_cols = left_columns & right_columns

        renamed_other = other
        for col_name in duplicate_cols:
            new_name = f"{col_name}{suffix}"
            renamed_other = renamed_other.withColumnRenamed(col_name, new_name)

        return self._df.crossJoin(renamed_other)

    def _pyspark_join_expr(
        self,
        other: PySparkDataFrame,
        left_col: str,
        right_expr: UnifiedExpr,
        how: str,
        suffix: str = "_right",
    ) -> PySparkDataFrame:
        join_map = {
            "inner": "inner",
            "left": "left",
            "right": "right",
            "outer": "outer",
            "full": "outer",
            "semi": "leftsemi",
            "leftsemi": "leftsemi",
            "anti": "leftanti",
            "leftanti": "leftanti",
        }
        spark_how = join_map.get(how, how)

        left_columns = set(self._df.columns)
        right_columns = set(other.columns)
        duplicate_cols = left_columns & right_columns

        renamed_other = other
        for col_name in duplicate_cols:
            new_name = f"{col_name}{suffix}"
            renamed_other = renamed_other.withColumnRenamed(col_name, new_name)

        condition = self._df[left_col] == right_expr.native

        return self._df.join(renamed_other, condition, spark_how)

    def _pyspark_join_multi_expr(
        self,
        other: PySparkDataFrame,
        left_items: list,
        right_items: list,
        how: str,
        suffix: str = "_right",
    ) -> PySparkDataFrame:

        join_map = {
            "inner": "inner",
            "left": "left",
            "right": "right",
            "outer": "outer",
            "full": "outer",
            "semi": "leftsemi",
            "leftsemi": "leftsemi",
            "anti": "leftanti",
            "leftanti": "leftanti",
        }
        spark_how = join_map.get(how, how)

        left_columns = set(self._df.columns)
        right_columns = set(other.columns)
        duplicate_cols = left_columns & right_columns

        renamed_other = other
        for col_name in duplicate_cols:
            new_name = f"{col_name}{suffix}"
            renamed_other = renamed_other.withColumnRenamed(col_name, new_name)

        conditions = []
        for left_item, right_item in zip(left_items, right_items):
            if isinstance(left_item, UnifiedExpr):
                left_col = left_item.native
            elif isinstance(left_item, str):
                left_col = self._df[left_item]
            else:
                left_col = left_item

            if isinstance(right_item, UnifiedExpr):
                right_col = right_item.native
            elif isinstance(right_item, str):
                if right_item in duplicate_cols:
                    right_col = renamed_other[f"{right_item}{suffix}"]
                else:
                    right_col = renamed_other[right_item]
            else:
                right_col = right_item

            conditions.append(left_col == right_col)

        combined_condition = conditions[0]
        for cond in conditions[1:]:
            combined_condition = combined_condition & cond

        return self._df.join(renamed_other, combined_condition, spark_how)

    def _polars_join_with_exprs(
        self,
        other: pl.DataFrame | pl.LazyFrame,
        left_items: list,
        right_items: list,
        how: str,
        suffix: str = "_right",
    ) -> pl.DataFrame | pl.LazyFrame:

        left_df = self._df
        right_df = other

        temp_cols_left = []
        temp_cols_right = []

        left_join_cols = []
        for i, item in enumerate(left_items):
            if isinstance(item, UnifiedExpr):
                temp_col = f"__left_join_key_{i}__"
                left_df = left_df.with_columns(item.native.alias(temp_col))
                left_join_cols.append(temp_col)
                temp_cols_left.append(temp_col)
            elif isinstance(item, str):
                left_join_cols.append(item)
            else:
                temp_col = f"__left_join_key_{i}__"
                left_df = left_df.with_columns(item.alias(temp_col))
                left_join_cols.append(temp_col)
                temp_cols_left.append(temp_col)

        right_join_cols = []
        for i, item in enumerate(right_items):
            if isinstance(item, UnifiedExpr):
                temp_col = f"__right_join_key_{i}__"
                right_df = right_df.with_columns(item.native.alias(temp_col))
                right_join_cols.append(temp_col)
                temp_cols_right.append(temp_col)
            elif isinstance(item, str):
                right_join_cols.append(item)
            else:
                temp_col = f"__right_join_key_{i}__"
                right_df = right_df.with_columns(item.alias(temp_col))
                right_join_cols.append(temp_col)
                temp_cols_right.append(temp_col)

        result = left_df.join(
            right_df,
            left_on=left_join_cols if len(left_join_cols) > 1 else left_join_cols[0],
            right_on=right_join_cols if len(right_join_cols) > 1 else right_join_cols[0],
            how=how,
            suffix=suffix,
        )

        all_temp_cols = temp_cols_left + [f"{c}{suffix}" if c in temp_cols_right else c for c in temp_cols_right]
        result_cols = result.collect_schema().names() if hasattr(result, "collect_schema") else list(result.columns)
        for temp_col in temp_cols_right:
            if f"{temp_col}{suffix}" in result_cols:
                all_temp_cols.append(f"{temp_col}{suffix}")

        cols_to_drop = [c for c in all_temp_cols if c in result_cols]
        if cols_to_drop:
            result = result.drop(cols_to_drop)

        return result

    def _datafusion_join_with_exprs(
        self,
        other: DataFusionDataFrame,
        left_items: list,
        right_items: list,
        how: str,
        suffix: str = "_right",
    ) -> DataFusionDataFrame:
        left_df, left_join_cols, temp_cols_left = _prepare_join_items(self._df, left_items, "left")
        right_df, right_join_cols, temp_cols_right = _prepare_join_items(other, right_items, "right")

        left_columns = {field.name for field in left_df.schema()}
        right_columns = {field.name for field in right_df.schema()}

        duplicate_cols = left_columns & (right_columns - set(right_join_cols))
        used_names = set(left_columns)
        right_df = _rename_duplicate_right_columns(right_df, duplicate_cols, used_names, suffix)

        is_outer_join = how in ("full", "outer", "left", "right")
        right_df, right_join_key_renames = _rename_same_named_join_keys(
            right_df,
            left_join_cols,
            right_join_cols,
            temp_cols_right,
            left_columns,
            used_names,
            is_outer_join,
            suffix,
        )

        actual_right_join_cols = [right_join_key_renames.get(c, c) for c in right_join_cols]
        result = left_df.join(
            right_df,
            left_on=datafusion_identifiers(left_join_cols),
            right_on=datafusion_identifiers(actual_right_join_cols),
            how=how,
        )
        return _drop_post_join_temp_columns(
            result, temp_cols_left, temp_cols_right, right_join_key_renames, is_outer_join, suffix
        )

    @_bind_datafusion_arguments
    def group_by(self, *columns: str | Expr | list) -> UnifiedGroupBy:
        if len(columns) == 1 and isinstance(columns[0], list):
            columns = tuple(columns[0])

        col_list = [c.native if isinstance(c, UnifiedExpr) else c for c in columns]

        if _is_pyspark_df(self._df):
            grouped = self._df.groupBy(*col_list)
        elif _is_polars_df(self._df):
            grouped = self._df.group_by(*col_list)
        elif _is_datafusion_df(self._df):
            col_exprs = [df_col(c) if isinstance(c, str) else c for c in col_list]
            grouped = None
            return UnifiedGroupBy(grouped, col_exprs, self._adapter, self._df)
        else:
            grouped = self._df.group_by(*col_list)

        return UnifiedGroupBy(grouped, col_list, self._adapter, self._df)

    @_bind_datafusion_arguments
    def filter(self, condition: Expr) -> UnifiedLazyFrame:
        native_condition = condition.native if isinstance(condition, UnifiedExpr) else condition
        result = self._df.filter(native_condition)
        return UnifiedLazyFrame(result, self._adapter)

    @_bind_datafusion_arguments
    def select(self, *columns: str | Expr | list) -> UnifiedLazyFrame:
        if len(columns) == 1 and isinstance(columns[0], list):
            columns = tuple(columns[0])

        unwrapped = [c.native if isinstance(c, (UnifiedExpr, UnifiedListExpr)) else c for c in columns]

        if _is_datafusion_df(self._df):
            has_aggregate = self._has_aggregate_expr(unwrapped)
            if has_aggregate:
                result = self._df.aggregate([], unwrapped)
            else:
                result = self._df.select(*datafusion_identifiers(unwrapped))
        else:
            result = self._df.select(*unwrapped)
        return UnifiedLazyFrame(result, self._adapter)

    def _has_aggregate_expr(self, exprs: list) -> bool:
        if not _is_datafusion_df(self._df):
            return False

        for expr in exprs:
            expr_str = str(expr)
            agg_patterns = [
                "sum(",
                "avg(",
                "count(",
                "min(",
                "max(",
                "SUM(",
                "AVG(",
                "COUNT(",
                "MIN(",
                "MAX(",
                "first_value(",
                "last_value(",
                "stddev(",
                "FIRST_VALUE(",
                "LAST_VALUE(",
                "STDDEV(",
            ]
            for pattern in agg_patterns:
                if pattern in expr_str:
                    return True
        return False

    @_bind_datafusion_arguments
    def with_columns(self, *exprs: Expr | list) -> UnifiedLazyFrame:
        if len(exprs) == 1 and isinstance(exprs[0], list):
            exprs = tuple(exprs[0])

        unwrapped = [e.native if isinstance(e, (UnifiedExpr, UnifiedListExpr)) else e for e in exprs]

        if _is_pyspark_df(self._df):
            result = self._df
            for expr in unwrapped:
                col_name = None
                try:
                    if hasattr(expr, "_jc"):
                        expr_str = str(expr._jc.toString())
                        if " AS " in expr_str:
                            col_name = expr_str.split(" AS ")[-1].strip("`")
                except Exception:
                    pass

                result = result.withColumn(col_name, expr) if col_name else result.select("*", expr)
        elif _is_polars_df(self._df):
            result = self._df.with_columns(*unwrapped)
        else:
            result = self._df.with_columns(*unwrapped)

        return UnifiedLazyFrame(result, self._adapter)

    def sum(self) -> UnifiedLazyFrame:
        if _is_pyspark_df(self._df):
            from pyspark.sql import functions as F

            sum_exprs = [F.sum(F.col(c)).alias(c) for c in self._df.columns]
            result = self._df.agg(*sum_exprs)
        elif _is_polars_df(self._df):
            result = self._df.sum()
        elif _is_datafusion_df(self._df):
            from datafusion import functions as df_f

            schema_fields = [field.name for field in self._df.schema()]
            sum_exprs = [df_f.sum(df_col(c)).alias(c) for c in schema_fields]
            result = self._df.aggregate([], sum_exprs)
        else:
            result = self._df.sum()

        return UnifiedLazyFrame(result, self._adapter)

    def mean(self) -> UnifiedLazyFrame:
        if _is_pyspark_df(self._df):
            from pyspark.sql import functions as F

            mean_exprs = [F.avg(F.col(c)).alias(c) for c in self._df.columns]
            result = self._df.agg(*mean_exprs)
        elif _is_polars_df(self._df):
            result = self._df.mean()
        elif _is_datafusion_df(self._df):
            from datafusion import functions as df_f

            schema_fields = [field.name for field in self._df.schema()]
            mean_exprs = [df_f.avg(df_col(c)).alias(c) for c in schema_fields]
            result = self._df.aggregate([], mean_exprs)
        else:
            result = self._df.mean()

        return UnifiedLazyFrame(result, self._adapter)

    def agg(self, *exprs: Expr | list) -> UnifiedLazyFrame:
        return self.select(*exprs)

    def unique(self, subset: str | list[str] | None = None) -> UnifiedLazyFrame:
        if _is_pyspark_df(self._df):
            if subset is not None:
                cols = [subset] if isinstance(subset, str) else list(subset)
                result = self._df.dropDuplicates(cols)
            else:
                result = self._df.distinct()
        elif _is_polars_df(self._df):
            result = self._df.unique(subset=subset) if subset is not None else self._df.unique()
        elif _is_datafusion_df(self._df):
            if subset is not None:
                from datafusion import functions as df_f, lit as df_lit
                from datafusion.expr import Window, WindowFrame

                cols = [subset] if isinstance(subset, str) else list(subset)
                partition_exprs = [df_col(c) for c in cols]
                all_cols = [field.name for field in self._df.schema()]
                order_col = df_col(all_cols[0]).sort(ascending=True)
                window_frame = WindowFrame("rows", None, None)
                rn_expr = df_f.row_number().over(
                    Window(partition_by=partition_exprs, order_by=[order_col], window_frame=window_frame)
                )
                result = (
                    self._df.with_column("__dedup_rn__", rn_expr)
                    .filter(df_col("__dedup_rn__") == df_lit(1))
                    .drop("__dedup_rn__")
                )
            else:
                result = self._df.distinct()
        else:
            result = self._df.unique(subset=subset) if subset is not None else self._df.unique()

        return UnifiedLazyFrame(result, self._adapter)

    def distinct(self) -> UnifiedLazyFrame:
        return self.unique()

    @_bind_datafusion_arguments
    def sort(
        self,
        by: str | list[str] | list[tuple[str, str]],
        *more_columns: str,
        descending: bool | list[bool] = False,
        nulls_last: bool = False,
    ) -> UnifiedLazyFrame:
        cols, desc_flags = _normalize_sort_inputs(by, more_columns, descending)

        if _is_pyspark_df(self._df):
            result = _sort_pyspark(self._df, cols, desc_flags, nulls_last)
        elif _is_polars_df(self._df):
            unwrapped_cols = [c.native if isinstance(c, UnifiedExpr) else c for c in cols]
            result = self._df.sort(unwrapped_cols, descending=desc_flags, nulls_last=nulls_last)
        elif _is_datafusion_df(self._df):
            result = _sort_datafusion(self._df, cols, desc_flags, nulls_last)
        else:
            unwrapped_cols = [c.native if isinstance(c, UnifiedExpr) else c for c in cols]
            result = self._df.sort(unwrapped_cols, descending=desc_flags)

        return UnifiedLazyFrame(result, self._adapter)

    def limit(self, n: int) -> UnifiedLazyFrame:
        result = self._df.limit(n)
        return UnifiedLazyFrame(result, self._adapter)

    def head(self, n: int = 10) -> UnifiedLazyFrame:
        return self.limit(n)

    def slice(self, offset: int, length: int | None = None) -> UnifiedLazyFrame:
        if _is_polars_df(self._df):
            result = self._df.slice(offset, length)
        elif _is_datafusion_df(self._df):
            if length is None:
                raise NotImplementedError("UnifiedLazyFrame.slice without a length is not supported for DataFusion")
            result = self._df.limit(length, offset=offset)
        elif _is_pyspark_df(self._df):
            raise NotImplementedError("UnifiedLazyFrame.slice is not implemented for PySpark")
        else:
            result = self._df.slice(offset, length)
        return UnifiedLazyFrame(result, self._adapter)

    def rename(self, mapping: dict[str, str]) -> UnifiedLazyFrame:
        if _is_pyspark_df(self._df):
            result = self._df
            for old_name, new_name in mapping.items():
                if old_name in result.columns:
                    result = result.withColumnRenamed(old_name, new_name)
        elif _is_polars_df(self._df):
            result = self._df.rename(mapping)
        elif _is_datafusion_df(self._df):
            result = self._df
            for old_name, new_name in mapping.items():
                result = result.with_column_renamed(datafusion_identifier(old_name), new_name)
        else:
            result = self._df.rename(mapping)

        return UnifiedLazyFrame(result, self._adapter)

    def explode(self, column: str) -> UnifiedLazyFrame:
        if _is_pyspark_df(self._df):
            from pyspark.sql import functions as F

            result = self._df.withColumn(column, F.explode(F.col(column)))
        elif _is_polars_df(self._df):
            result = self._df.explode(column)
        elif _is_datafusion_df(self._df):
            result = self._df.unnest_columns(column)
        else:
            result = self._df.explode(column)

        return UnifiedLazyFrame(result, self._adapter)

    def vstack(self, other: UnifiedLazyFrame) -> UnifiedLazyFrame:
        other_df = other._df if isinstance(other, UnifiedLazyFrame) else other
        if _is_datafusion_df(self._df):
            result = self._df.union(other_df)
        elif _is_polars_df(self._df):
            import polars as pl

            result = pl.concat([self._df, other_df])
        elif _is_pyspark_df(self._df):
            result = self._df.unionAll(other_df)
        else:
            result = self._df.union(other_df)
        return UnifiedLazyFrame(result, self._adapter)

    def melt(
        self,
        id_vars: list[str],
        value_vars: list[str],
        variable_name: str = "variable",
        value_name: str = "value",
    ) -> UnifiedLazyFrame:
        if _is_polars_df(self._df):
            result = self._df.unpivot(
                on=value_vars,
                index=id_vars,
                variable_name=variable_name,
                value_name=value_name,
            )
        elif _is_pyspark_df(self._df):
            from pyspark.sql import functions as F

            stack_args = [F.lit(len(value_vars))]
            for var in value_vars:
                stack_args.extend([F.lit(var), F.col(var)])
            stack_expr = F.expr(
                f"stack({len(value_vars)}, "
                + ", ".join(f"'{v}', `{v}`" for v in value_vars)
                + f") as ({variable_name}, {value_name})"
            )
            result = self._df.select(*id_vars, stack_expr)
        elif _is_datafusion_df(self._df):
            import pyarrow as pa
            from datafusion import lit as df_lit

            frames = []
            for var in value_vars:
                select_exprs = [df_col(c) for c in id_vars]
                select_exprs.append(df_lit(var).alias(variable_name))
                select_exprs.append(df_col(var).cast(pa.float64()).alias(value_name))
                frames.append(self._df.select(*select_exprs))
            result = frames[0]
            for frame in frames[1:]:
                result = result.union(frame)
        else:
            result = self._df.melt(
                id_vars=id_vars,
                value_vars=value_vars,
                variable_name=variable_name,
                value_name=value_name,
            )
        return UnifiedLazyFrame(result, self._adapter)

    def _collect_polars(self) -> Any:
        if not hasattr(self._df, "collect"):
            return self._df
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        if isinstance(self._adapter, PolarsDataFrameAdapter):
            return self._adapter.materialize(self._df)
        return collect_frame(self._df)

    def collect(self) -> Any:
        if _is_pyspark_df(self._df):
            return self._df
        elif _is_polars_df(self._df):
            return self._collect_polars()
        elif _is_datafusion_df(self._df):
            import pyarrow as pa

            batches = self._df.collect()
            if not batches:
                return pa.table({})
            return pa.Table.from_batches(batches)
        else:
            if hasattr(self._df, "collect"):
                return self._df.collect()
            return self._df

    def collect_column_as_list(self, column: str) -> list:
        if _is_pyspark_df(self._df):
            return [row[column] for row in self._df.select(column).collect()]
        elif _is_polars_df(self._df):
            return self._collect_polars()[column].to_list()
        elif _is_datafusion_df(self._df):
            import pyarrow as pa

            batches = self._df.collect()
            if not batches:
                return []
            combined = pa.Table.from_batches(batches)
            return combined.to_pandas()[column].tolist()
        else:
            collected = self._df.collect() if hasattr(self._df, "collect") else self._df
            if hasattr(collected, "to_pandas"):
                return collected.to_pandas()[column].tolist()
            return list(collected[column])

    def scalar(self, row: int = 0, col: int = 0) -> Any:
        if _is_pyspark_df(self._df):
            rows = self._df.collect()
            if not rows:
                return None
            return rows[row][col]
        elif _is_polars_df(self._df):
            return self._collect_polars()[row, col]
        elif _is_datafusion_df(self._df):
            import pyarrow as pa

            batches = self._df.collect()
            if not batches:
                return None
            combined = pa.Table.from_batches(batches)
            return combined.to_pandas().iloc[row, col]
        else:
            collected = self._df.collect() if hasattr(self._df, "collect") else self._df
            if hasattr(collected, "to_pandas"):
                return collected.to_pandas().iloc[row, col]
            return collected[row, col]

    def drop(self, *columns: str) -> UnifiedLazyFrame:
        if _is_datafusion_df(self._df):
            result = self._df.drop(*datafusion_identifiers(columns))
        else:
            result = self._df.drop(*columns)
        return UnifiedLazyFrame(result, self._adapter)


def wrap_dataframe(df: LazyFrameLike, adapter: ExpressionFamilyAdapter) -> UnifiedLazyFrame:
    return UnifiedLazyFrame(df, adapter)
