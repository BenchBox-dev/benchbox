from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.dataframe import iceberg_maintenance as im

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestIcebergImportErrors:
    def test_init_raises_importerror_when_iceberg_not_available(self, tmp_path, monkeypatch):
        monkeypatch.setattr(im, "ICEBERG_AVAILABLE", False)
        with pytest.raises(ImportError, match="pyiceberg"):
            im.IcebergMaintenanceOperations(working_dir=tmp_path)

    def test_init_raises_importerror_when_pyarrow_not_available(self, tmp_path, monkeypatch):
        monkeypatch.setattr(im, "ICEBERG_AVAILABLE", True)
        monkeypatch.setattr(im, "PYARROW_AVAILABLE", False)
        with pytest.raises(ImportError, match="PyArrow"):
            im.IcebergMaintenanceOperations(working_dir=tmp_path)


@pytest.mark.skipif(
    not (im.ICEBERG_AVAILABLE and im.PYARROW_AVAILABLE) or sys.platform == "win32",
    reason="PyIceberg/PyArrow not installed or Windows drive-letter paths incompatible with PyIceberg catalog URIs",
)
class TestIcebergMaintenanceCoverage:
    def test_normalize_identifier_and_parse_merge_key(self, tmp_path):
        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        assert ops._normalize_table_identifier("default.orders") == "default.orders"
        assert ops._normalize_table_identifier("/tmp/orders") == "default.orders"
        assert ops._parse_merge_key("target.id = source.id") == "id"
        assert ops._parse_merge_key("orderkey") == "orderkey"

    def test_parse_condition_variants(self, tmp_path):
        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        assert isinstance(ops._parse_condition("id = 10"), im.EqualTo)
        assert isinstance(ops._parse_condition("value >= 3.5"), im.GreaterThanOrEqual)
        assert isinstance(ops._parse_condition("name != 'x'"), im.NotEqualTo)
        assert isinstance(ops._parse_condition("unparseable condition"), im.AlwaysTrue)

    def test_do_delete_nonexistent_table_returns_zero(self, tmp_path):
        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        ops._catalog = MagicMock()
        ops._catalog.load_table.side_effect = RuntimeError("missing")
        assert ops._do_delete("default.missing", "id > 1") == 0

    def test_do_update_zero_matches(self, tmp_path):
        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)

        table = MagicMock()
        table.scan.return_value.to_arrow.return_value = SimpleNamespace(num_rows=0)

        ops._catalog = MagicMock()
        ops._catalog.load_table.return_value = table

        updated = ops._do_update("default.tbl", "id = 1", {"name": "new"})
        assert updated == 0

    def test_get_maintenance_operations_unavailable_branches(self, monkeypatch):
        monkeypatch.setattr(im, "ICEBERG_AVAILABLE", False)
        assert im.get_iceberg_maintenance_operations() is None

        monkeypatch.setattr(im, "ICEBERG_AVAILABLE", True)
        monkeypatch.setattr(im, "PYARROW_AVAILABLE", False)
        assert im.get_iceberg_maintenance_operations() is None

    def test_do_insert_zero_rows(self, tmp_path):

        import pyarrow as pa

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        empty = pa.table({"id": pa.array([], type=pa.int64())})
        count = ops._do_insert("default.tbl", empty, None, "append")
        assert count == 0

    def test_do_insert_overwrite_mode(self, tmp_path):

        import pyarrow as pa

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        data = pa.table({"id": pa.array([1, 2], type=pa.int64())})

        ops._do_insert("default.overwrite_tbl", data, None, "append")

        count = ops._do_insert("default.overwrite_tbl", data, None, "overwrite")
        assert count == 2

    def test_get_or_create_table_new_namespace_and_table(self, tmp_path):

        import pyarrow as pa

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)

        schema = pa.schema([pa.field("id", pa.int64())])

        table = ops._get_or_create_table("myns.newtable", schema)
        assert table is not None

    def test_normalize_table_identifier_with_backslash(self, tmp_path):

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        result = ops._normalize_table_identifier("C:\\Users\\data\\orders")
        assert result.startswith("default.")

    def test_normalize_table_identifier_special_chars(self, tmp_path):

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        result = ops._normalize_table_identifier("/tmp/orders-2024.01")
        assert result.startswith("default.")
        assert "-" not in result.split(".")[-1]

    def test_parse_condition_less_than_and_greater_than(self, tmp_path):

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        assert isinstance(ops._parse_condition("value < 100"), im.LessThan)
        assert isinstance(ops._parse_condition("value > 50"), im.GreaterThan)
        assert isinstance(ops._parse_condition("value <= 100"), im.LessThanOrEqual)
        assert isinstance(ops._parse_condition("status <> 'active'"), im.NotEqualTo)

    def test_parse_condition_float_value(self, tmp_path):

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        expr = ops._parse_condition("price > 3.14")
        assert isinstance(expr, im.GreaterThan)

    def test_parse_condition_string_value_double_quotes(self, tmp_path):

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        expr = ops._parse_condition('status = "active"')
        assert isinstance(expr, im.EqualTo)

    def test_do_update_with_quoted_value(self, tmp_path):

        import pyarrow as pa

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)

        data = pa.table({"id": pa.array([1, 2, 3], type=pa.int64()), "name": pa.array(["a", "b", "c"])})
        ops._do_insert("default.update_tbl", data, None, "append")

        count = ops._do_update("default.update_tbl", "id = 1", {"name": "'new_name'"})
        assert count >= 0

    def test_do_update_with_double_quoted_value(self, tmp_path):

        import pyarrow as pa

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        data = pa.table({"id": pa.array([10, 20], type=pa.int64()), "name": pa.array(["x", "y"])})
        ops._do_insert("default.update_dq_tbl", data, None, "append")

        count = ops._do_update("default.update_dq_tbl", "id = 10", {"name": '"new_name"'})
        assert count >= 0

    def test_do_delete_with_nonstring_condition(self, tmp_path):

        import pyarrow as pa

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)

        data = pa.table({"id": pa.array([1, 2, 3], type=pa.int64())})
        ops._do_insert("default.del_nonstr_tbl", data, None, "append")

        from pyiceberg.expressions import EqualTo

        expr = cast(Any, EqualTo)("id", 1)
        count = ops._do_delete("default.del_nonstr_tbl", expr)
        assert count >= 0

    def test_parse_merge_key_no_equals(self, tmp_path):

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        result = ops._parse_merge_key("orderkey")
        assert result == "orderkey"

    def test_parse_merge_key_no_dot_prefix(self, tmp_path):

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        result = ops._parse_merge_key("id = 5")
        assert result == "id"

    def test_do_merge_when_not_matched(self, tmp_path):

        import pyarrow as pa

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)

        target = pa.table({"id": pa.array([1, 2], type=pa.int64()), "val": pa.array([10, 20], type=pa.int64())})
        ops._do_insert("default.merge_tbl", target, None, "append")

        source = pa.table({"id": pa.array([3], type=pa.int64()), "val": pa.array([30], type=pa.int64())})

        affected = ops._do_merge(
            "default.merge_tbl",
            source,
            "target.id = source.id",
            when_matched=None,
            when_not_matched={"insert": "*"},
        )

        assert affected == 1
        merged = ops.catalog.load_table("default.merge_tbl").scan().to_arrow()
        assert sorted(merged.column("id").to_pylist()) == [1, 2, 3]

    def test_do_merge_preserves_schema_with_nulls_and_decimals(self, tmp_path):

        import decimal

        import pyarrow as pa

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)

        target = pa.table(
            {
                "id": pa.array([1, 2], type=pa.int64()),
                "amt": pa.array([decimal.Decimal("10.50"), decimal.Decimal("20.25")], type=pa.decimal128(15, 2)),
            }
        )
        ops._do_insert("default.merge_schema_tbl", target, None, "append")

        source = pa.table(
            {
                "id": pa.array([2, 3, 4], type=pa.int64()),
                "amt": pa.array([decimal.Decimal("99.99"), None, None], type=pa.decimal128(15, 2)),
            }
        )
        affected = ops._do_merge(
            "default.merge_schema_tbl",
            source,
            "target.id = source.id",
            when_matched=None,
            when_not_matched={"insert": "*"},
        )

        assert affected == 2
        merged = ops.catalog.load_table("default.merge_schema_tbl").scan().to_arrow()
        assert merged.schema.field("amt").type == pa.decimal128(15, 2)
        assert sorted(merged.column("id").to_pylist()) == [1, 2, 3, 4]
        assert merged.column("amt").to_pylist() == [
            decimal.Decimal("10.50"),
            decimal.Decimal("20.25"),
            None,
            None,
        ]

    def test_do_merge_when_matched_with_source_ref(self, tmp_path):

        import pyarrow as pa

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)

        target = pa.table({"id": pa.array([1, 2], type=pa.int64()), "val": pa.array([10, 20], type=pa.int64())})
        ops._do_insert("default.merge_match_tbl", target, None, "append")

        source = pa.table({"id": pa.array([1, 2], type=pa.int64()), "val": pa.array([100, 200], type=pa.int64())})

        count = ops._do_merge(
            "default.merge_match_tbl",
            source,
            "target.id = source.id",
            when_matched={"val": "source.val"},
            when_not_matched=None,
        )
        assert count >= 0

    def test_do_merge_applies_insert_mapping(self, tmp_path):

        import pyarrow as pa

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)

        target = pa.table({"id": pa.array([1], type=pa.int64()), "value": pa.array([10], type=pa.int64())})
        ops._do_insert("default.merge_map_tbl", target, None, "append")

        source = pa.table({"id": pa.array([2], type=pa.int64()), "new_value": pa.array([20], type=pa.int64())})

        affected = ops._do_merge(
            "default.merge_map_tbl",
            source,
            "target.id = source.id",
            when_matched=None,
            when_not_matched={"id": "source.id", "value": "source.new_value"},
        )

        assert affected == 1
        merged = ops.catalog.load_table("default.merge_map_tbl").scan().to_arrow()
        assert sorted(merged.column("id").to_pylist()) == [1, 2]
        assert sorted(merged.column("value").to_pylist()) == [10, 20]

    def test_do_update_preserves_nulls_dates_and_decimals(self, tmp_path):
        import datetime
        import decimal

        import pyarrow as pa

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        target = pa.table(
            {
                "id": pa.array([1, 2, 3], type=pa.int64()),
                "name": pa.array(["a", None, "c"]),
                "amt": pa.array([decimal.Decimal("1.50"), None, decimal.Decimal("3.25")], type=pa.decimal128(15, 2)),
                "d": pa.array([datetime.date(2024, 1, 1), None, datetime.date(2024, 3, 3)], type=pa.date32()),
            }
        )
        ops._do_insert("default.upd_parity", target, None, "append")

        count = ops._do_update("default.upd_parity", "id <= 2", {"name": "'zz'", "amt": decimal.Decimal("9.99")})

        assert count == 2
        out = ops.catalog.load_table("default.upd_parity").scan().to_arrow().sort_by("id")
        assert out.schema.field("amt").type == pa.decimal128(15, 2)
        assert out.column("name").to_pylist() == ["zz", "zz", "c"]
        assert out.column("amt").to_pylist() == [
            decimal.Decimal("9.99"),
            decimal.Decimal("9.99"),
            decimal.Decimal("3.25"),
        ]
        assert out.column("d").to_pylist() == [datetime.date(2024, 1, 1), None, datetime.date(2024, 3, 3)]

    def test_do_merge_matched_and_not_matched_parity_without_full_scan(self, tmp_path):
        import datetime
        import decimal

        import pyarrow as pa

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        target = pa.table(
            {
                "id": pa.array([1, 2, 3, 4], type=pa.int64()),
                "name": pa.array(["a", "b", None, "d"]),
                "amt": pa.array(
                    [decimal.Decimal("1.00"), None, decimal.Decimal("3.00"), decimal.Decimal("4.00")],
                    type=pa.decimal128(15, 2),
                ),
                "d": pa.array([datetime.date(2024, 1, i) for i in (1, 2, 3, 4)], type=pa.date32()),
            }
        )
        ops._do_insert("default.merge_parity", target, None, "append")

        source = pa.table(
            {
                "id": pa.array([2, 3, 3, 7], type=pa.int64()),
                "name": pa.array(["B", "C1", "C2", None]),
                "amt": pa.array(
                    [decimal.Decimal("20.00"), None, decimal.Decimal("30.00"), decimal.Decimal("70.00")],
                    type=pa.decimal128(15, 2),
                ),
                "d": pa.array([None, datetime.date(2025, 1, 1), datetime.date(2025, 1, 2), None], type=pa.date32()),
            }
        )

        table = ops.catalog.load_table("default.merge_parity")
        scans: list[Any] = []
        original_scan = type(table).scan

        def recording_scan(self, *args, **kwargs):
            scans.append(kwargs.get("row_filter"))
            return original_scan(self, *args, **kwargs)

        with patch.object(type(table), "scan", recording_scan):
            affected = ops._do_merge(
                "default.merge_parity",
                source,
                "target.id = source.id",
                when_matched={"name": "source.name", "amt": "source.amt", "d": "source.d"},
                when_not_matched={"id": "source.id", "name": "source.name", "amt": "source.amt"},
            )

        assert affected == 3
        assert scans and all(f is not None for f in scans)
        out = ops.catalog.load_table("default.merge_parity").scan().to_arrow().sort_by("id")
        assert out.schema.field("amt").type == pa.decimal128(15, 2)
        assert out.column("id").to_pylist() == [1, 2, 3, 4, 7]
        assert out.column("name").to_pylist() == ["a", "B", "C2", "d", None]
        assert out.column("amt").to_pylist() == [
            decimal.Decimal("1.00"),
            decimal.Decimal("20.00"),
            decimal.Decimal("30.00"),
            decimal.Decimal("4.00"),
            decimal.Decimal("70.00"),
        ]
        assert out.column("d").to_pylist() == [
            datetime.date(2024, 1, 1),
            None,
            datetime.date(2025, 1, 2),
            datetime.date(2024, 1, 4),
            None,
        ]

    def test_do_merge_constant_matched_value_and_no_overlap(self, tmp_path):
        import pyarrow as pa

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        target = pa.table({"id": pa.array([1, 2], type=pa.int64()), "val": pa.array([10, 20], type=pa.int64())})
        ops._do_insert("default.merge_const", target, None, "append")
        source = pa.table({"id": pa.array([2, 5], type=pa.int64()), "val": pa.array([0, 50], type=pa.int64())})

        affected = ops._do_merge(
            "default.merge_const", source, "id = id", when_matched={"val": -1}, when_not_matched=None
        )

        assert affected == 1
        out = ops.catalog.load_table("default.merge_const").scan().to_arrow().sort_by("id")
        assert out.column("val").to_pylist() == [10, -1]

    def test_default_catalog_config(self, tmp_path):

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        config = ops._default_catalog_config()
        assert "type" in config
        assert "uri" in config
        assert "warehouse" in config

    def test_catalog_property_creates_on_first_access(self, tmp_path):

        ops = im.IcebergMaintenanceOperations(working_dir=tmp_path)
        assert ops._catalog is None
        cat = ops.catalog
        assert cat is not None
        assert ops._catalog is cat

        assert ops.catalog is cat
