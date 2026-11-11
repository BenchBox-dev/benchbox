# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

try:
    import pandas as pd
    import polars as pl

    from benchbox.core.tpcdi.etl.dataframe_backend import DataFrameETLBackend
    from benchbox.platforms.dataframe.polars_maintenance import PolarsMaintenanceOperations

    DEPS_AVAILABLE = True
except ImportError:
    pd = None
    pl = None
    DataFrameETLBackend = None
    PolarsMaintenanceOperations = None
    DEPS_AVAILABLE = False

pytestmark.append(pytest.mark.skipif(not DEPS_AVAILABLE, reason="pandas/polars not installed"))


def _customer_v1() -> Any:
    return pd.DataFrame(
        {
            "SK_CustomerID": [1, 2, 3],
            "CustomerID": [101, 102, 103],
            "IsCurrent": [True, True, True],
            "BatchID": [1, 1, 1],
        }
    )


def _customer_v2() -> Any:
    return pd.DataFrame(
        {
            "SK_CustomerID": [4],
            "CustomerID": [101],
            "IsCurrent": [True],
            "BatchID": [2],
        }
    )


def _read_table(root: Path, table_name: str) -> Any:
    return pl.scan_parquet(str(root / table_name)).collect().sort("SK_CustomerID")


class TestScd2MutationExecution:
    def test_historical_load_expire_insert_cycle(self, tmp_path: Path) -> None:
        root = tmp_path / "tables"
        backend = DataFrameETLBackend(
            maintenance_ops=PolarsMaintenanceOperations(working_dir=tmp_path),
            platform_name="polars-df",
            table_root=root,
        )

        load = backend.load_dataframes({"DimCustomer": _customer_v1()}, batch_type="historical")
        assert load == {"records_loaded": 3, "tables_updated": ["DimCustomer"]}

        expire = backend.execute_scd2_expire(
            "DimCustomer",
            {"IsCurrent": True, "CustomerID": 101},
            {"IsCurrent": False},
        )
        assert expire == {"success": True, "rows_affected": 1}

        insert = backend.execute_scd2_insert("DimCustomer", _customer_v2())
        assert insert == {"success": True, "rows_affected": 1}

        stored = _read_table(root, "DimCustomer")
        assert stored.height == 4
        assert stored["SK_CustomerID"].to_list() == [1, 2, 3, 4]
        assert stored["IsCurrent"].to_list() == [False, True, True, True]
        assert stored["BatchID"].to_list() == [1, 1, 1, 2]

    def test_tables_stay_under_table_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        root = tmp_path / "warehouse"
        backend = DataFrameETLBackend(
            maintenance_ops=PolarsMaintenanceOperations(working_dir=tmp_path),
            platform_name="polars-df",
            table_root=root,
        )

        backend.load_dataframes({"DimCustomer": _customer_v1()}, batch_type="historical")

        assert (root / "DimCustomer").is_dir()
        assert [child.name for child in tmp_path.iterdir()] == ["warehouse"]

    def test_string_condition_and_values_execute(self, tmp_path: Path) -> None:
        root = tmp_path / "tables"
        backend = DataFrameETLBackend(
            maintenance_ops=PolarsMaintenanceOperations(working_dir=tmp_path),
            platform_name="polars-df",
            table_root=root,
        )
        backend.load_dataframes({"DimCustomer": _customer_v1()}, batch_type="historical")

        expire = backend.execute_scd2_expire(
            "DimCustomer",
            '"IsCurrent" = TRUE AND "CustomerID" = 102',
            {"BatchID": 7},
        )
        assert expire == {"success": True, "rows_affected": 1}

        stored = _read_table(root, "DimCustomer")
        assert stored["BatchID"].to_list() == [1, 7, 1]


def _recording_ops() -> tuple[SimpleNamespace, dict[str, Any]]:
    seen: dict[str, Any] = {}

    def insert_rows(table_path: Any, dataframe: Any, **kwargs: Any) -> Any:
        seen["insert"] = (str(table_path), kwargs.get("mode"))
        return SimpleNamespace(success=True, rows_affected=len(dataframe), error_message=None)

    def update_rows(table_path: Any, condition: Any, updates: Any) -> Any:
        seen["update"] = (str(table_path), condition, updates)
        return SimpleNamespace(success=True, rows_affected=1, error_message=None)

    return SimpleNamespace(insert_rows=insert_rows, update_rows=update_rows), seen


class TestBackendContracts:
    def test_dict_condition_and_updates_render_to_sql(self, tmp_path: Path) -> None:
        ops, seen = _recording_ops()
        backend = DataFrameETLBackend(maintenance_ops=ops, platform_name="polars-df", table_root=tmp_path / "tables")

        result = backend.execute_scd2_expire(
            "DimCustomer",
            {"IsCurrent": True, "CustomerID": 101, "Status": "A", "ClosedDate": None},
            {"IsCurrent": False},
        )

        assert result == {"success": True, "rows_affected": 1}
        table_path, condition, updates = seen["update"]
        assert table_path == str(tmp_path / "tables" / "DimCustomer")
        assert condition == ('"IsCurrent" = TRUE AND "CustomerID" = 101 AND "Status" = \'A\' AND "ClosedDate" IS NULL')
        assert updates == {"IsCurrent": "FALSE"}

    def test_empty_updates_short_circuit_without_ops_call(self, tmp_path: Path) -> None:
        ops, seen = _recording_ops()
        backend = DataFrameETLBackend(maintenance_ops=ops, platform_name="polars-df", table_root=tmp_path)

        assert backend.execute_scd2_expire("DimCustomer", {"IsCurrent": True}, {}) == {
            "success": True,
            "rows_affected": 0,
        }
        assert "update" not in seen

    def test_no_table_root_passes_bare_name_through(self) -> None:
        ops, seen = _recording_ops()
        backend = DataFrameETLBackend(maintenance_ops=ops, platform_name="polars-df")

        backend.load_dataframes({"DimCustomer": _customer_v1()}, batch_type="historical")

        assert seen["insert"][0] == "DimCustomer"

    def test_rejects_empty_and_unsafe_conditions(self, tmp_path: Path) -> None:
        ops, _ = _recording_ops()
        backend = DataFrameETLBackend(maintenance_ops=ops, platform_name="polars-df", table_root=tmp_path)

        with pytest.raises(ValueError, match="cannot be empty"):
            backend.execute_scd2_expire("DimCustomer", {}, {"IsCurrent": False})
        with pytest.raises(ValueError, match="cannot be empty"):
            backend.execute_scd2_expire("DimCustomer", "   ", {"IsCurrent": False})
        with pytest.raises(ValueError, match="Unsafe column name"):
            backend.execute_scd2_expire("DimCustomer", {"IsCurrent; DROP TABLE x": True}, {"IsCurrent": False})
        with pytest.raises(TypeError, match="must be a SQL expression string or dict"):
            backend.execute_scd2_expire("DimCustomer", [("IsCurrent", True)], {"IsCurrent": False})

    def test_dict_condition_passes_through_natively_for_non_sql_adapters(self, tmp_path: Path) -> None:
        ops, seen = _recording_ops()
        ops.get_capabilities = lambda: SimpleNamespace(accepts_sql_predicates=False)
        backend = DataFrameETLBackend(maintenance_ops=ops, platform_name="iceberg", table_root=tmp_path)

        result = backend.execute_scd2_expire(
            "DimCustomer",
            {"IsCurrent": True, "CustomerID": 101},
            {"IsCurrent": False},
        )

        assert result == {"success": True, "rows_affected": 1}
        _, condition, updates = seen["update"]
        assert condition == {"IsCurrent": True, "CustomerID": 101}
        assert updates == {"IsCurrent": False}

    def test_render_literal_supports_frame_scalar_types(self) -> None:
        np = pytest.importorskip("numpy")
        from benchbox.core.tpcdi.etl.dataframe_backend import _render_literal

        assert _render_literal(np.int64(101)) == "101"
        assert _render_literal(np.bool_(True)) == "TRUE"
        assert _render_literal(np.float64(1.5)) == "1.5"

    def test_render_literal_supports_temporal_and_decimal(self) -> None:
        from datetime import date, datetime, timezone
        from decimal import Decimal

        from benchbox.core.tpcdi.etl.dataframe_backend import _render_literal

        assert _render_literal(date(2020, 1, 1)) == "'2020-01-01'"
        assert _render_literal(datetime(2020, 1, 1, 12, 30, 0)) == "'2020-01-01 12:30:00'"
        assert _render_literal(datetime(2020, 1, 1, 12, 30, 0, 123456)) == "'2020-01-01 12:30:00.123456'"
        assert _render_literal(datetime(2020, 1, 1, 12, 30, 0, tzinfo=timezone.utc)) == "'2020-01-01 12:30:00+00:00'"
        assert _render_literal(Decimal("10.5")) == "10.5"

    def test_render_literal_rejects_non_finite_floats(self) -> None:
        from benchbox.core.tpcdi.etl.dataframe_backend import _render_literal

        with pytest.raises(ValueError, match="Non-finite float"):
            _render_literal(float("nan"))
        with pytest.raises(ValueError, match="Non-finite float"):
            _render_literal(float("inf"))

    def test_string_condition_rejected_for_non_sql_adapters(self, tmp_path: Path) -> None:
        ops, seen = _recording_ops()
        ops.get_capabilities = lambda: SimpleNamespace(accepts_sql_predicates=False)
        backend = DataFrameETLBackend(maintenance_ops=ops, platform_name="iceberg", table_root=tmp_path)

        with pytest.raises(TypeError, match="does not accept SQL predicate text"):
            backend.execute_scd2_expire(
                "DimCustomer",
                '"IsCurrent" = TRUE AND "CustomerID" = 101',
                {"IsCurrent": False},
            )
        assert "update" not in seen

    def test_native_dict_condition_validates_column_names(self, tmp_path: Path) -> None:
        ops, _ = _recording_ops()
        ops.get_capabilities = lambda: SimpleNamespace(accepts_sql_predicates=False)
        backend = DataFrameETLBackend(maintenance_ops=ops, platform_name="iceberg", table_root=tmp_path)

        with pytest.raises(ValueError, match="Unsafe column name"):
            backend.execute_scd2_expire("DimCustomer", {"IsCurrent; DROP TABLE x": True}, {"IsCurrent": False})

    def test_string_condition_rejects_unsafe_tokens(self, tmp_path: Path) -> None:
        ops, _ = _recording_ops()
        backend = DataFrameETLBackend(maintenance_ops=ops, platform_name="polars-df", table_root=tmp_path)

        with pytest.raises(ValueError, match="Unsafe SQL tokens"):
            backend.execute_scd2_expire("DimCustomer", '"IsCurrent" = TRUE; DROP TABLE x', {"IsCurrent": False})
        with pytest.raises(ValueError, match="DML/DDL keywords"):
            backend.execute_scd2_expire("DimCustomer", '"IsCurrent" = TRUE OR UPDATE x', {"IsCurrent": False})


