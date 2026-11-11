from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.dataframe.shared_loading import (
    dialect_preserves_empty_strings,
    load_tables_from_data_source_impl,
    resolve_empty_string_restore_columns,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_adapter(platform_name: str = "polars", load_row_count: int = 10) -> MagicMock:
    adapter = MagicMock()
    adapter.platform_name = platform_name
    adapter.table_mode = "native"
    adapter.platform_config = {}
    adapter.requested_table_format = None
    adapter.load_table.return_value = load_row_count
    return adapter


class TestLoadTablesFromDataSourceImpl:
    def test_passes_adapter_loading_contract_to_resolver(self, tmp_path: Path) -> None:

        adapter = _make_adapter(platform_name="polars")

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = MagicMock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = SimpleNamespace(tables={"orders": [tmp_path / "orders.tbl"]})
            (tmp_path / "orders.tbl").write_bytes(b"")
            adapter.load_table.return_value = 5
            load_tables_from_data_source_impl(adapter, MagicMock(), tmp_path)

        assert mock_cls.call_args.kwargs == {
            "platform_name": "polars",
            "table_mode": "native",
            "platform_config": {},
            "requested_format": None,
        }

    def test_raises_when_resolver_returns_none(self, tmp_path: Path) -> None:

        adapter = _make_adapter()

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = MagicMock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = None

            with pytest.raises(ValueError, match="No data files found"):
                load_tables_from_data_source_impl(adapter, MagicMock(), tmp_path)

    def test_raises_when_resolver_returns_empty_tables(self, tmp_path: Path) -> None:

        adapter = _make_adapter()

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = MagicMock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = SimpleNamespace(tables={})

            with pytest.raises(ValueError, match="No data files found"):
                load_tables_from_data_source_impl(adapter, MagicMock(), tmp_path)

    def test_calls_load_table_for_each_valid_file(self, tmp_path: Path) -> None:

        adapter = _make_adapter(load_row_count=42)
        (tmp_path / "orders.tbl").write_bytes(b"data")
        (tmp_path / "customer.tbl").write_bytes(b"data")

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = MagicMock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = SimpleNamespace(
                tables={
                    "orders": [tmp_path / "orders.tbl"],
                    "customer": [tmp_path / "customer.tbl"],
                }
            )
            stats = load_tables_from_data_source_impl(adapter, MagicMock(), tmp_path)

        assert stats == {"orders": 42, "customer": 42}
        assert adapter.load_table.call_count == 2

    def test_skips_tables_with_no_existing_files(self, tmp_path: Path) -> None:

        adapter = _make_adapter(load_row_count=5)
        (tmp_path / "orders.tbl").write_bytes(b"data")

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = MagicMock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = SimpleNamespace(
                tables={
                    "orders": [tmp_path / "orders.tbl"],
                    "customer": [tmp_path / "customer.tbl"],
                }
            )
            stats = load_tables_from_data_source_impl(adapter, MagicMock(), tmp_path)

        assert "orders" in stats
        assert "customer" not in stats

    def test_uses_schema_info_column_names_when_provided(self, tmp_path: Path) -> None:

        adapter = _make_adapter(load_row_count=3)
        (tmp_path / "orders.tbl").write_bytes(b"data")
        schema_info = {"orders": {"columns": [{"name": "o_orderkey"}, {"name": "o_custkey"}]}}

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = MagicMock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = SimpleNamespace(tables={"orders": [tmp_path / "orders.tbl"]})
            load_tables_from_data_source_impl(adapter, MagicMock(), tmp_path, schema_info=schema_info)

        _, call_args, call_kwargs = adapter.load_table.mock_calls[0]
        column_names = call_args[3] if len(call_args) > 3 else call_kwargs.get("column_names")
        assert column_names == ["o_orderkey", "o_custkey"]

    def test_uses_table_object_schema_column_names(self, tmp_path: Path) -> None:

        adapter = _make_adapter(load_row_count=3)
        (tmp_path / "sat_lineitem.tbl").write_bytes(b"data")
        schema_info = {
            "sat_lineitem": SimpleNamespace(
                columns=[
                    SimpleNamespace(name="lineitem_hk"),
                    SimpleNamespace(name="load_end_dts"),
                ]
            )
        }

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = MagicMock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = SimpleNamespace(
                tables={"sat_lineitem": [tmp_path / "sat_lineitem.tbl"]}
            )
            load_tables_from_data_source_impl(adapter, MagicMock(), tmp_path, schema_info=schema_info)

        _, call_args, call_kwargs = adapter.load_table.mock_calls[0]
        column_names = call_args[3] if len(call_args) > 3 else call_kwargs.get("column_names")
        assert column_names == ["lineitem_hk", "load_end_dts"]

    def test_platform_name_attribute_used_directly(self, tmp_path: Path) -> None:

        adapter = MagicMock(spec=[])
        with pytest.raises(AttributeError):
            load_tables_from_data_source_impl(adapter, MagicMock(), tmp_path)


@pytest.mark.parametrize(
    ("null_marker", "expected"),
    [
        (None, True),
        ("", False),
        ("__NULL__", True),
        ("NULL", True),
    ],
)
def test_dialect_preserves_empty_strings(null_marker: str | None, expected: bool) -> None:

    assert dialect_preserves_empty_strings(null_marker) is expected


def test_restore_columns_includes_sentinel_marker_columns() -> None:

    assert resolve_empty_string_restore_columns(["a", "b"], "__NULL__", ["a", "b", "c"]) == ["a", "b"]


def test_restore_columns_skips_empty_marker_columns() -> None:

    assert resolve_empty_string_restore_columns(["a"], "", ["a"]) == []


