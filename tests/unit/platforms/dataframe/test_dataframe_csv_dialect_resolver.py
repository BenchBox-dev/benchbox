from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from benchbox.platforms.base.data_loading import DataSource
from benchbox.platforms.dataframe.expression_family import ExpressionFamilyAdapter
from benchbox.platforms.dataframe.pandas_family import PandasFamilyAdapter, PandasFamilyContext

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _PandasStub(PandasFamilyAdapter[dict]):
    platform_name = "StubPandas"

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.table_mode = "native"
        self.platform_config: dict[str, Any] = {}
        self.verbose = False
        self.very_verbose = False

    def _log_verbose(self, msg: str) -> None:
        pass

    def _log_very_verbose(self, msg: str) -> None:
        pass

    def read_csv(
        self,
        path: Path,
        *,
        delimiter: str = ",",
        header: int | None = 0,
        names: list[str] | None = None,
        null_marker: str | None = None,
        column_types: list[str] | None = None,
    ) -> dict:
        self.calls.append({"path": path, "delimiter": delimiter, "null_marker": null_marker})
        return {"rows": 0, "_columns": names or []}

    def read_parquet(self, path: Path) -> dict:
        return {"rows": 0, "_columns": []}

    def to_datetime(self, series: Any) -> Any:
        return series

    def timedelta_days(self, days: int) -> Any:
        from datetime import timedelta

        return timedelta(days=days)

    def concat(self, dfs: list) -> dict:
        return dfs[0] if dfs else {}

    def get_row_count(self, df: dict) -> int:
        return len(df.get("_columns", []))

    def _get_first_row(self, df: dict) -> tuple | None:
        return None

    def scalar(self, df: dict, column: str | None = None) -> Any:
        return 0

    def scalar_to_df(self, data: dict) -> dict:
        return {}

    def groupby_agg(self, df: Any, *, by: Any, agg_spec: Any, as_index: bool = True) -> Any:
        return {}

    def merge(self, left: Any, right: Any, **kwargs: Any) -> Any:
        return {}

    def to_pandas(self, df: Any) -> Any:
        return df

    def from_pandas(self, df: Any) -> Any:
        return df

    def col(self, name: str) -> str:
        return name

    def lit(self, value: Any) -> Any:
        return value

    def date_sub(self, column: Any, days: int) -> Any:
        return column

    def date_add(self, column: Any, days: int) -> Any:
        return column

    def cast_date(self, column: Any) -> Any:
        return column

    def cast_string(self, column: Any) -> Any:
        return column

    def get_platform_info(self) -> dict:
        return {}


def _pandas_ctx(adapter: _PandasStub) -> PandasFamilyContext:
    return adapter.create_context()


class _ExpressionStub(ExpressionFamilyAdapter[dict, dict, str]):
    platform_name = "StubExpr"
    table_mode = "native"
    config = None
    verbose = False
    very_verbose = False

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.platform_config: dict[str, Any] = {}

    def _log_verbose(self, msg: str) -> None:
        pass

    def _log_very_verbose(self, msg: str) -> None:
        pass

    def read_csv(
        self,
        path: Path,
        *,
        delimiter: str = ",",
        has_header: bool = True,
        column_names: list[str] | None = None,
        null_marker: str | None = None,
    ) -> dict:
        self.calls.append({"path": path, "delimiter": delimiter, "null_marker": null_marker})
        return {"rows": 0, "_columns": column_names or []}

    def read_parquet(self, path: Path) -> dict:
        return {"rows": 0, "_columns": []}

    def collect(self, df: dict) -> dict:
        return df

    def get_row_count(self, df: dict | Any) -> int:
        return 0

    def scalar(self, df: dict, column: str | None = None) -> Any:
        return 0

    def scalar_to_df(self, data: dict) -> dict:
        return {}

    def col(self, name: str) -> str:
        return name

    def lit(self, value: Any) -> str:
        return str(value)

    def date_sub(self, column: str, days: int) -> str:
        return column

    def date_add(self, column: str, days: int) -> str:
        return column

    def cast_date(self, column: str) -> str:
        return column

    def cast_string(self, column: str) -> str:
        return column

    def window_rank(self, order_by: Any, partition_by: Any = None) -> str:
        return "rank"

    def window_row_number(self, order_by: Any, partition_by: Any = None) -> str:
        return "row_number"

    def window_dense_rank(self, order_by: Any, partition_by: Any = None) -> str:
        return "dense_rank"

    def window_sum(self, column: str, partition_by: Any = None, order_by: Any = None) -> str:
        return "sum"

    def window_avg(self, column: str, partition_by: Any = None, order_by: Any = None) -> str:
        return "avg"

    def window_count(self, column: str | None = None, partition_by: Any = None, order_by: Any = None) -> str:
        return "count"

    def window_min(self, column: str, partition_by: Any = None) -> str:
        return "min"

    def window_max(self, column: str, partition_by: Any = None) -> str:
        return "max"

    def union_all(self, *dataframes: Any) -> dict:
        return {}

    def rename_columns(self, df: Any, mapping: dict) -> Any:
        return df

    def get_platform_info(self) -> dict:
        return {}


def test_pandas_load_table_tbl_sets_null_marker(tmp_path: Path) -> None:

    f = tmp_path / "lineitem.tbl"
    f.write_text("1|ok\n")

    adapter = _PandasStub()
    ctx = _pandas_ctx(adapter)
    adapter.load_table(ctx, "lineitem", [f], format_hint="tbl")

    assert adapter.calls, "read_csv was not called"
    assert adapter.calls[0]["null_marker"] == ""


def test_pandas_load_table_csv_leaves_null_marker_none(tmp_path: Path) -> None:

    f = tmp_path / "lineitem.csv"
    f.write_text("id,name\n1,ok\n")

    adapter = _PandasStub()
    ctx = _pandas_ctx(adapter)
    adapter.load_table(ctx, "lineitem", [f], format_hint="csv")

    assert adapter.calls, "read_csv was not called"
    assert adapter.calls[0]["null_marker"] is None


def test_pandas_load_table_uses_resolver_null_marker_when_data_source_provided(tmp_path: Path) -> None:

    f = tmp_path / "lineitem.csv"
    f.write_text("1|ok|\n")

    ds = DataSource(
        source_type="manifest_v2",
        tables={"lineitem": [f]},
        table_metadata={"lineitem": {"csv_delimiter": "|", "csv_has_header": False, "csv_null_marker": ""}},
    )

    adapter = _PandasStub()
    ctx = _pandas_ctx(adapter)
    adapter.load_table(ctx, "lineitem", [f], format_hint="csv", data_source=ds)

    assert adapter.calls, "read_csv was not called"
    assert adapter.calls[0]["null_marker"] == ""


def test_pandas_load_table_null_marker_none_when_no_null_marker_in_manifest(tmp_path: Path) -> None:

    f = tmp_path / "clickbench.csv"
    f.write_text("1,ok\n")

    ds = DataSource(
        source_type="manifest_v2",
        tables={"hits": [f]},
        table_metadata={"hits": {"csv_delimiter": ",", "csv_has_header": False, "csv_null_marker": None}},
    )

    adapter = _PandasStub()
    ctx = _pandas_ctx(adapter)
    adapter.load_table(ctx, "hits", [f], format_hint="csv", data_source=ds)

    assert adapter.calls, "read_csv was not called"
    assert adapter.calls[0]["null_marker"] is None


def test_expression_load_table_tbl_sets_null_marker(tmp_path: Path) -> None:

    f = tmp_path / "orders.tbl"
    f.write_text("1|ok\n")

    adapter = _ExpressionStub()
    ctx = adapter.create_context()
    adapter.load_table(ctx, "orders", [f], format_hint="tbl")

    assert adapter.calls, "read_csv was not called"
    assert adapter.calls[0]["null_marker"] == ""


def test_expression_load_table_csv_leaves_null_marker_none(tmp_path: Path) -> None:

    f = tmp_path / "orders.csv"
    f.write_text("id,name\n1,ok\n")

    adapter = _ExpressionStub()
    ctx = adapter.create_context()
    adapter.load_table(ctx, "orders", [f], format_hint="csv")

    assert adapter.calls, "read_csv was not called"
    assert adapter.calls[0]["null_marker"] is None


def test_expression_load_table_uses_resolver_null_marker_when_data_source_provided(tmp_path: Path) -> None:

    f = tmp_path / "orders.csv"
    f.write_text("1|ok|\n")

    ds = DataSource(
        source_type="manifest_v2",
        tables={"orders": [f]},
        table_metadata={"orders": {"csv_delimiter": "|", "csv_has_header": False, "csv_null_marker": ""}},
    )

    adapter = _ExpressionStub()
    ctx = adapter.create_context()
    adapter.load_table(ctx, "orders", [f], format_hint="csv", data_source=ds)

    assert adapter.calls, "read_csv was not called"
    assert adapter.calls[0]["null_marker"] == ""


