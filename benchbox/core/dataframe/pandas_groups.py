from __future__ import annotations

from collections.abc import Callable
from typing import Any

from benchbox.core.dataframe.pandas_ops import is_lazy_frame

_SAMPLE_ROWS = 200


def native_frame(frame: Any) -> Any:
    return frame.native if hasattr(frame, "native") else frame


def collect(value: Any) -> Any:
    return value.compute() if hasattr(value, "compute") else value


def _sample_meta(source: Any, func: Callable[[Any], Any]) -> Any:
    sample = source.head(_SAMPLE_ROWS)
    if len(sample) == 0:
        sample = source.head(_SAMPLE_ROWS, npartitions=-1)
    return func(sample).iloc[:0]


def _map_partitions(target: Any, source: Any, func: Callable[[Any], Any]) -> Any:
    try:
        return target.map_partitions(func)
    except Exception:
        return target.map_partitions(func, meta=_sample_meta(source, func))


def _wrap(frame: Any, result: Any) -> Any:
    if hasattr(frame, "native"):
        return type(frame)(result, frame._adapter)
    return result


def per_group(frame: Any, by: str | list[str], func: Callable[[Any], Any]) -> Any:
    raw = native_frame(frame)
    if not is_lazy_frame(raw):
        return _wrap(frame, func(raw))
    return _wrap(frame, _map_partitions(raw.shuffle(on=by), raw, func))


def per_partition(frame: Any, func: Callable[[Any], Any]) -> Any:
    raw = native_frame(frame)
    if not is_lazy_frame(raw):
        return _wrap(frame, func(raw))
    return _wrap(frame, _map_partitions(raw, raw, func))
