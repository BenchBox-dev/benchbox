from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    pd = None
    PANDAS_AVAILABLE = False

from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration
from benchbox.platforms.dataframe.pandas_family import (
    PandasFamilyAdapter,
)
from benchbox.platforms.dataframe.shared_loading import coerce_empty_string_columns
from benchbox.utils.file_format import TRAILING_DUMMY_COLUMN, has_trailing_delimiter

logger = logging.getLogger(__name__)

PandasDF = pd.DataFrame if PANDAS_AVAILABLE else Any


def _parse_date_value(value: Any) -> Any:
    if value is None or (isinstance(value, str) and value == "") or (not isinstance(value, str) and pd.isna(value)):
        return None
    return pd.to_datetime(value).date()


def _parse_date_column(series: Any) -> Any:
    try:
        parsed = pd.to_datetime(series, format="ISO8601")
    except (ValueError, TypeError):
        return series.map(_parse_date_value)
    if not pd.api.types.is_datetime64_any_dtype(parsed):
        return series.map(_parse_date_value)
    return parsed.dt.date.astype(object).where(parsed.notna(), None)


_NUMERIC_SQL_TYPE_PREFIXES = ("INT", "BIGINT", "SMALLINT", "TINYINT", "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL")


def _is_numeric_sql_type(sql_type: str | None) -> bool:
    if not sql_type:
        return False
    upper = sql_type.upper()
    return any(upper.startswith(prefix) for prefix in _NUMERIC_SQL_TYPE_PREFIXES)


def _pandas_parse_date_columns(
    names: list[str] | None,
    column_types: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    if not names:
        return [], []

    types_by_name: dict[str, str] = {}
    if column_types and len(column_types) == len(names):
        types_by_name = {name.lower(): sql_type for name, sql_type in zip(names, column_types)}

    date_columns: list[str] = []
    datetime_columns: list[str] = []
    seen: set[str] = set()
    for name in names:
        lower_name = name.lower()
        if lower_name.endswith("_sk"):
            continue
        if _is_numeric_sql_type(types_by_name.get(lower_name)):
            continue
        if lower_name.endswith(("datetime", "_datetime", "timestamp", "_timestamp", "_dts")):
            datetime_columns.append(name)
            seen.add(name)
        elif lower_name.endswith(("date", "_date")):
            date_columns.append(name)
            seen.add(name)
        elif lower_name in {"eventtime"}:
            datetime_columns.append(name)
            seen.add(name)

    if types_by_name:
        from benchbox.core.dataframe.data_loader import SchemaMapper

        for name in names:
            if name in seen:
                continue
            arrow_type = SchemaMapper.sql_type_to_pyarrow(str(types_by_name.get(name.lower()) or ""))
            if arrow_type == "date32":
                date_columns.append(name)
            elif arrow_type == "timestamp[us]":
                datetime_columns.append(name)

    return date_columns, datetime_columns


def _pandas_string_columns(
    names: list[str] | None,
    column_types: list[str] | None,
    exclude: set[str],
) -> list[str]:
    if not names or not column_types or len(column_types) != len(names):
        return []
    from benchbox.core.dataframe.data_loader import SchemaMapper

    string_columns: list[str] = []
    for name, sql_type in zip(names, column_types):
        if name in exclude or not sql_type:
            continue
        if SchemaMapper.sql_type_to_pyarrow(str(sql_type)) == "string":
            string_columns.append(name)
    return string_columns


def _coerce_date_columns(df: PandasDF, date_columns: list[str]) -> PandasDF:
    if not date_columns:
        return df

    import pyarrow as pa

    date_dtype = pd.ArrowDtype(pa.date32())
    for column in date_columns:
        if column in df.columns:
            df[column] = pd.array(_parse_date_column(df[column]), dtype=date_dtype)
    return df


class PandasDataFrameAdapter(PandasFamilyAdapter[PandasDF]):
    def __init__(
        self,
        working_dir: str | Path | None = None,
        verbose: bool = False,
        very_verbose: bool = False,
        dtype_backend: str = "numpy_nullable",
        copy_on_write: bool | None = None,
        tuning_config: DataFrameTuningConfiguration | None = None,
    ) -> None:
        if not PANDAS_AVAILABLE:
            raise ImportError("Pandas not installed. Install with: pip install pandas")

        super().__init__(
            working_dir=working_dir,
            verbose=verbose,
            very_verbose=very_verbose,
            tuning_config=tuning_config,
        )

        self.dtype_backend = dtype_backend

        self._configure_copy_on_write(copy_on_write)

        self._validate_and_apply_tuning()

    def _configure_copy_on_write(self, copy_on_write: bool | None) -> None:
        try:
            version_parts = pd.__version__.split(".")[:2]
            pandas_version = tuple(
                int(p.split("+")[0].split("a")[0].split("b")[0].split("rc")[0]) for p in version_parts
            )
        except (ValueError, IndexError):
            logger.warning(f"Could not parse Pandas version '{pd.__version__}', assuming < 2.0")
            pandas_version = (1, 0)
        self._pandas_version = pandas_version

        if pandas_version >= (3, 0):
            if copy_on_write is False:
                logger.warning(f"Copy-on-write cannot be disabled in Pandas {pd.__version__} (permanently enabled).")
            self.copy_on_write = True
            self._log_verbose(f"Copy-on-write: enabled (permanent in Pandas {pd.__version__})")
        elif pandas_version >= (2, 0):
            if copy_on_write is not None:
                self.copy_on_write = copy_on_write
                pd.options.mode.copy_on_write = copy_on_write
                self._log_verbose(
                    f"Copy-on-write {'enabled' if copy_on_write else 'disabled'} (Pandas {pd.__version__})"
                )
            else:
                self.copy_on_write = pd.options.mode.copy_on_write
                self._log_verbose(
                    f"Copy-on-write: {'enabled' if self.copy_on_write else 'disabled'} "
                    f"(Pandas {pd.__version__} default)"
                )
        elif copy_on_write is True:
            logger.warning(f"Copy-on-write requires Pandas 2.0+, but found {pd.__version__}. CoW will not be enabled.")
            self.copy_on_write = False
        else:
            self.copy_on_write = False

    def _apply_tuning(self) -> None:
        config = self._tuning_config

        if config.data_types.dtype_backend != "numpy_nullable":
            self.dtype_backend = config.data_types.dtype_backend
            self._log_verbose(f"Set dtype_backend={self.dtype_backend} from tuning configuration")
            self._record_runtime_tuning(f"dtype_backend={self.dtype_backend}")

        self._auto_categorize = config.data_types.auto_categorize_strings
        self._categorical_threshold = config.data_types.categorical_threshold

        if self._auto_categorize:
            self._log_verbose(f"Auto-categorize strings enabled (threshold={self._categorical_threshold})")
            self._record_runtime_tuning(f"auto_categorize_strings=on;threshold={self._categorical_threshold}")

    @property
    def platform_name(self) -> str:
        return "Pandas"

    def read_csv(
        self,
        path: Path,
        *,
        delimiter: str = ",",
        header: int | None = 0,
        names: list[str] | None = None,
        null_marker: str | None = None,
        column_types: list[str] | None = None,
    ) -> PandasDF:
        read_kwargs: dict[str, Any] = {
            "sep": delimiter,
            "header": header,
            "on_bad_lines": "skip",
        }
        date_columns: list[str] = []
        string_columns: list[str] = []

        if names:
            read_kwargs["names"] = names
            date_columns, datetime_columns = _pandas_parse_date_columns(names, column_types)
            if datetime_columns:
                read_kwargs["parse_dates"] = datetime_columns
            string_columns = _pandas_string_columns(names, column_types, set(date_columns) | set(datetime_columns))
            if string_columns or date_columns:
                read_kwargs["dtype"] = dict.fromkeys([*string_columns, *date_columns], "object")

        if null_marker is not None and names and has_trailing_delimiter(path, delimiter, names):
            extended_names = names + [TRAILING_DUMMY_COLUMN]
            read_kwargs["names"] = extended_names

        df = pd.read_csv(path, **read_kwargs)
        df = _coerce_date_columns(df, date_columns)

        df = coerce_empty_string_columns(df, string_columns, null_marker)

        if TRAILING_DUMMY_COLUMN in df.columns:
            df = df.drop(columns=[TRAILING_DUMMY_COLUMN])

        return df

    def read_parquet(self, path: Path) -> PandasDF:
        return pd.read_parquet(path, dtype_backend="pyarrow")

    def to_datetime(self, series: Any) -> Any:
        return pd.to_datetime(series)

    def timedelta_days(self, days: int) -> timedelta:
        return pd.Timedelta(days=days)

    def concat(self, dfs: list[PandasDF]) -> PandasDF:
        if len(dfs) == 1:
            return dfs[0]
        return pd.concat(dfs, ignore_index=True)

    def concat_shards(self, dfs: list[PandasDF]) -> PandasDF:
        if len(dfs) == 1:
            return dfs[0]
        columns = list(dfs[0].columns)
        if any(list(df.columns) != columns for df in dfs[1:]):
            return pd.concat(dfs, ignore_index=True)
        shard_columns: list[dict[str, Any]] = []
        while dfs:
            df = dfs.pop(0)
            shard_columns.append({column: df[column].copy() for column in columns})
            del df
        data = {
            column: pd.concat([shard.pop(column) for shard in shard_columns], ignore_index=True) for column in columns
        }
        return pd.DataFrame(data, copy=False)

    def get_row_count(self, df: PandasDF) -> int:
        return len(df)

    def _get_first_row(self, df: PandasDF) -> tuple | None:
        if len(df) == 0:
            return None

        return tuple(df.iloc[0])

    def get_platform_info(self) -> dict[str, Any]:
        info = {
            "platform": self.platform_name,
            "family": self.family,
            "dtype_backend": self.dtype_backend,
            "working_dir": str(self.working_dir),
        }

        if PANDAS_AVAILABLE:
            info["version"] = pd.__version__
            info["copy_on_write"] = self.copy_on_write

            if self._pandas_version >= (3, 0):
                info["copy_on_write_active"] = True
            elif self._pandas_version >= (2, 0):
                current_global = pd.options.mode.copy_on_write
                info["copy_on_write_active"] = current_global
                if current_global != self.copy_on_write:
                    logger.warning(
                        f"CoW state mismatch: this adapter configured {self.copy_on_write}, "
                        f"but global state is {current_global}. Another adapter may have "
                        "changed the setting. Pandas CoW is process-global."
                    )
            else:
                info["copy_on_write_active"] = False

        return info

    def filter_rows(
        self,
        df: PandasDF,
        column: str,
        op: str,
        value: Any,
    ) -> PandasDF:
        if op == ">":
            return df[df[column] > value]
        elif op == "<":
            return df[df[column] < value]
        elif op == ">=":
            return df[df[column] >= value]
        elif op == "<=":
            return df[df[column] <= value]
        elif op == "==":
            return df[df[column] == value]
        elif op == "!=":
            return df[df[column] != value]
        else:
            raise ValueError(f"Unknown operator: {op}")

    def sort_values(
        self,
        df: PandasDF,
        by: str | list[str],
        ascending: bool | list[bool] = True,
    ) -> PandasDF:
        return df.sort_values(by=by, ascending=ascending)

    def select_columns(self, df: PandasDF, columns: list[str]) -> PandasDF:
        return df[columns]

    def with_column(
        self,
        df: PandasDF,
        name: str,
        values: Any,
    ) -> PandasDF:
        df = df.copy()
        df[name] = values
        return df
