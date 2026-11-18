# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import copy
import threading
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    pass


DF = TypeVar("DF")


@runtime_checkable
class DataFrameContext(Protocol):
    @property
    def platform(self) -> str: ...

    def get_table(self, name: str) -> Any: ...

    def list_tables(self) -> list[str]: ...

    def table_exists(self, name: str) -> bool: ...

    def col(self, name: str) -> Any: ...

    def lit(self, value: Any) -> Any: ...

    def element(self) -> Any: ...

    def date_sub(self, column: Any, days: int) -> Any: ...

    def date_add(self, column: Any, days: int) -> Any: ...

    def cast_date(self, column: Any) -> Any: ...

    def cast_string(self, column: Any) -> Any: ...

    def window_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Any: ...

    def window_row_number(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Any: ...

    def window_dense_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Any: ...

    def window_sum(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Any: ...

    def window_avg(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Any: ...

    def window_count(
        self,
        column: str | None = None,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Any: ...

    def window_min(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> Any: ...

    def window_max(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> Any: ...

    def union_all(self, *dataframes: Any) -> Any: ...

    def rename_columns(self, df: Any, mapping: dict[str, str]) -> Any: ...

    def scalar(self, df: Any, column: str | None = None) -> Any: ...

    def scalar_to_df(self, data: dict[str, Any]) -> Any: ...

    @property
    def family(self) -> str: ...


class DataFrameContextImpl(Generic[DF], ABC):
    def __init__(self, platform: str, family: str) -> None:

        self._tables: dict[str, DF] = {}
        self._tables_lock = threading.RLock()
        self._platform = platform
        self._family = family

    @property
    def family(self) -> str:

        return self._family

    @property
    def platform(self) -> str:

        return self._platform

    def register_table(self, name: str, df: DF) -> None:

        with self._tables_lock:
            self._tables[name.lower()] = df

    def swap_tables(self, replacements: dict[str, DF]) -> None:

        with self._tables_lock:
            unknown = sorted(name for name in replacements if name.lower() not in self._tables)
            if unknown:
                raise KeyError(f"Cannot swap unregistered tables: {', '.join(unknown)}")
            self._tables.update({name.lower(): df for name, df in replacements.items()})

    def snapshot(self) -> DataFrameContextImpl[DF]:

        with self._tables_lock:
            view = copy.copy(self)
            view._tables = dict(self._tables)
        return view

    def unregister_table(self, name: str) -> bool:

        name_lower = name.lower()
        with self._tables_lock:
            if name_lower in self._tables:
                del self._tables[name_lower]
                return True
        return False

    def get_table(self, name: str) -> DF:

        name_lower = name.lower()
        if name_lower not in self._tables:
            available = ", ".join(sorted(self._tables.keys()))
            raise KeyError(f"Table '{name}' not found. Available tables: {available or 'none'}")
        return self._tables[name_lower]

    def get_native_table(self, name: str) -> DF:

        name_lower = name.lower()
        try:
            return self._tables[name_lower]
        except KeyError:
            available = ", ".join(sorted(self._tables.keys()))
            raise KeyError(f"Table '{name}' not found. Available tables: {available or 'none'}") from None

    def list_tables(self) -> list[str]:

        return sorted(self._tables.keys())

    def table_exists(self, name: str) -> bool:

        return name.lower() in self._tables

    def clear_tables(self) -> None:

        with self._tables_lock:
            self._tables.clear()

    @abstractmethod
    def col(self, name: str) -> Any:
        pass

    @abstractmethod
    def lit(self, value: Any) -> Any:
        pass

    def element(self) -> Any:

        raise NotImplementedError(f"element() not supported on {self._platform}")

    @abstractmethod
    def date_sub(self, column: Any, days: int) -> Any:
        pass

    @abstractmethod
    def date_add(self, column: Any, days: int) -> Any:
        pass

    @abstractmethod
    def cast_date(self, column: Any) -> Any:
        pass

    @abstractmethod
    def cast_string(self, column: Any) -> Any:
        pass

    @abstractmethod
    def window_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Any:
        pass

    @abstractmethod
    def window_row_number(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Any:
        pass

    @abstractmethod
    def window_dense_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> Any:
        pass

    @abstractmethod
    def window_sum(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Any:
        pass

    @abstractmethod
    def window_avg(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Any:
        pass

    @abstractmethod
    def window_count(
        self,
        column: str | None = None,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> Any:
        pass

    @abstractmethod
    def window_min(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> Any:
        pass

    @abstractmethod
    def window_max(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> Any:
        pass

    @abstractmethod
    def union_all(self, *dataframes: Any) -> Any:
        pass

    @abstractmethod
    def rename_columns(self, df: Any, mapping: dict[str, str]) -> Any:
        pass

    @abstractmethod
    def scalar(self, df: Any, column: str | None = None) -> Any:
        pass

    @abstractmethod
    def scalar_to_df(self, data: dict[str, Any]) -> Any:
        pass

    def to_date(self, value: str | date | datetime) -> date:

        if isinstance(value, datetime):
            return value.date()
        elif isinstance(value, date):
            return value
        elif isinstance(value, str):
            return datetime.strptime(value, "%Y-%m-%d").date()
        else:
            raise TypeError(f"Cannot convert {type(value)} to date")

    def days(self, n: int) -> timedelta:

        return timedelta(days=n)
