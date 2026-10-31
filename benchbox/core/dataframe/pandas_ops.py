from __future__ import annotations

from typing import Any

import numpy as np


def is_lazy_frame(value: Any) -> bool:
    return hasattr(value, "dask") and hasattr(value, "npartitions")


def _partition_where(condition: Any, if_true: Any, if_false: Any) -> Any:
    import pandas as pd

    mask = condition.to_numpy(dtype=bool, na_value=False)
    return pd.Series(np.where(mask, if_true, if_false), index=condition.index)


def where(condition: Any, if_true: Any, if_false: Any) -> Any:
    if not is_lazy_frame(condition):
        return np.where(condition, if_true, if_false)
    import dask.dataframe as dd

    return dd.map_partitions(_partition_where, condition, if_true, if_false)


def select(conditions: list[Any], choices: list[Any], default: Any) -> Any:
    if not any(is_lazy_frame(condition) for condition in conditions):
        return np.select(conditions, choices, default=default)
    result = default
    for condition, choice in reversed(list(zip(conditions, choices, strict=True))):
        result = where(condition, choice, result)
    return result


def materialize(*values: Any) -> Any:
    lazy = [value for value in values if hasattr(value, "compute")]
    if not lazy:
        return values if len(values) > 1 else values[0]
    import dask

    computed = iter(dask.compute(*lazy))
    resolved = tuple(next(computed) if hasattr(value, "compute") else value for value in values)
    return resolved if len(resolved) > 1 else resolved[0]


def to_datetime(series: Any) -> Any:
    if is_lazy_frame(series):
        import dask.dataframe as dd

        return dd.to_datetime(series)
    import pandas as pd

    return pd.to_datetime(series)
