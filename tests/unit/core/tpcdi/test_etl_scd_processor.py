from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from benchbox.core.tpcdi.etl.scd_processor import EnhancedSCDType2Processor

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Cursor:
    def __init__(self, rows: list[tuple[Any, ...]], columns: list[str]) -> None:
        self._rows = rows
        self.description = [(c,) for c in columns]
        self.rowcount = 1

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def fetchone(self) -> tuple[Any, ...]:
        return (0,)


class _Conn:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None, columns: list[str] | None = None) -> None:
        self.rows = rows or []
        self.columns = columns or ["CustomerID", "Status", "SK_CustomerID", "IsCurrent"]
        self.raise_on_execute = False
        self.executed: list[str] = []

    def execute(self, sql: str, params: list[Any] | None = None) -> _Cursor:
        self.executed.append(sql)
        if self.raise_on_execute:
            raise RuntimeError("execute failed")
        return _Cursor(self.rows, self.columns)


def test_detect_scd_changes_trims_padded_strings_on_every_string_dtype() -> None:
    scd = EnhancedSCDType2Processor(connection=_Conn())
    for dtype in ("str", object):
        merged = pd.DataFrame(
            {
                "name_new": pd.Series([" Alice "], dtype=dtype),
                "name_current": pd.Series(["Alice"], dtype=dtype),
            }
        )
        assert scd._detect_scd_changes(merged, ["name"]).tolist() == [False], dtype
    genuinely_changed = pd.DataFrame(
        {
            "name_new": pd.Series(["Bob"], dtype="str"),
            "name_current": pd.Series(["Alice"], dtype="str"),
        }
    )
    assert scd._detect_scd_changes(genuinely_changed, ["name"]).tolist() == [True]


def test_scd_module_core_paths(tmp_path: Path):
    conn = _Conn(rows=[(1, "A", 100, 1)], columns=["CustomerID", "Status", "SK_CustomerID", "IsCurrent"])
    scd = EnhancedSCDType2Processor(connection=conn)
    assert scd.process_dimension("DimCustomer", "CustomerID", ["Status"], 1)["success"] is True

    new_data = pd.DataFrame(
        {
            "CustomerID": [1, 2, 2],
            "Status": ["B", "A", "A"],
            "Attr": [10, 20, 20],
        }
    )

    failed = scd.process_scd_changes(
        new_data=new_data,
        table_name="DimCustomer",
        business_keys=["CustomerID"],
        scd_columns=["Status"],
        batch_id=1,
        effective_date=datetime.now(),
        non_scd_columns=["Attr"],
    )
    assert failed["success"] is False

    good_data = pd.DataFrame({"CustomerID": [1, 3], "Status": ["B", "A"], "Attr": [11, 30]})
    ok = scd.process_scd_changes(
        new_data=good_data,
        table_name="DimCustomer",
        business_keys=["CustomerID"],
        scd_columns=["Status"],
        batch_id=2,
        effective_date=datetime.now(),
        non_scd_columns=["Attr"],
    )
    assert "records_processed" in ok

    trail = scd.get_change_audit_trail(table_name="DimCustomer")
    assert isinstance(trail, list)
    assert isinstance(scd.get_comprehensive_statistics(), dict)
    out_file = tmp_path / "audit.json"
    assert scd.export_audit_trail(str(out_file), format="json") is True
    assert scd.export_audit_trail(str(out_file), format="bad-format") is False
    changes = scd.detect_changes(
        current_data=pd.DataFrame({"Status": ["A"]}),
        new_data=pd.DataFrame({"Status": ["B"]}),
        scd_columns=["Status"],
    )
    assert len(changes) == 1
    internal_changes = scd._detect_changes(
        current_data=pd.DataFrame({"Status": ["A"]}),
        new_data=pd.DataFrame({"Status": ["B"], "CustomerID": [123]}),
        scd_columns=["Status"],
    )
    assert len(internal_changes) == 1
    processed = scd._process_scd_change(internal_changes[0])
    assert processed["processed"] is True
    audit_record = scd._create_audit_record(internal_changes[0])
    assert audit_record["change_type"] == "UPDATE"
    assert not scd._extract_current_data("DimCustomer").empty
    assert scd._extract_new_data("DimCustomer").empty
    scd.clear_change_audit_trail()
    assert scd.get_change_audit_trail() == []


DIM_CUSTOMER_DDL = """
CREATE TABLE DimCustomer (
    SK_CustomerID BIGINT PRIMARY KEY,
    CustomerID BIGINT NOT NULL,
    Status VARCHAR,
    Attr INTEGER,
    IsCurrent BOOLEAN NOT NULL,
    BatchID INTEGER NOT NULL,
    EffectiveDate DATE NOT NULL,
    EndDate DATE NOT NULL
)
"""

EFFECTIVE = datetime(2024, 3, 1)


class _RecordingConn:
    def __init__(self, inner: Any, fail_on: str | None = None) -> None:
        self.inner = inner
        self.fail_on = fail_on
        self.calls: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: list[Any] | None = None) -> Any:
        self.calls.append((sql, params))
        if self.fail_on and sql.lstrip().upper().startswith(self.fail_on):
            raise RuntimeError("injected failure")
        return self.inner.execute(sql, params) if params is not None else self.inner.execute(sql)


@pytest.fixture
def dim_customer():
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(":memory:")
    conn.execute(DIM_CUSTOMER_DDL)
    conn.execute(
        "INSERT INTO DimCustomer VALUES "
        "(10, 1, 'A', 5, TRUE, 1, DATE '2024-01-01', DATE '9999-12-31'),"
        "(20, 2, 'A', 7, TRUE, 1, DATE '2024-01-01', DATE '9999-12-31')"
    )
    yield conn
    conn.close()


def _rows(conn: Any) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT SK_CustomerID, CustomerID, Status, Attr, IsCurrent, BatchID, EffectiveDate, EndDate "
        "FROM DimCustomer ORDER BY SK_CustomerID"
    ).fetchall()


def _apply(conn: Any, data: pd.DataFrame, batch_id: int = 2) -> dict[str, Any]:
    scd = EnhancedSCDType2Processor(connection=conn)
    return scd.process_scd_changes(
        new_data=data,
        table_name="DimCustomer",
        business_keys=["CustomerID"],
        scd_columns=["Status"],
        batch_id=batch_id,
        effective_date=EFFECTIVE,
        non_scd_columns=["Attr"],
    )


def test_scd_change_expires_current_row_and_inserts_new_version(dim_customer) -> None:
    before = _rows(dim_customer)
    stats = _apply(dim_customer, pd.DataFrame({"CustomerID": [1, 2], "Status": ["B", "A"], "Attr": [5, 7]}))

    assert stats["success"] is True, stats["error_message"]
    assert stats["scd_type2_changes"] == 2
    assert stats["unchanged_records"] == 1

    rows = _rows(dim_customer)
    assert len(rows) == 3
    assert rows[1] == before[1]

    expired = [r for r in rows if r[1] == 1 and r[4] is False]
    current = [r for r in rows if r[1] == 1 and r[4] is True]
    assert len(expired) == 1
    assert len(current) == 1
    assert expired[0][0] == 10
    assert expired[0][2] == "A"
    assert expired[0][7] == EFFECTIVE.date()
    assert expired[0][5] == 1
    assert current[0][0] not in (10, 20)
    assert current[0][2] == "B"
    assert current[0][3] == 5
    assert current[0][5] == 2
    assert current[0][6] == EFFECTIVE.date()
    assert current[0][7].isoformat() == "9999-12-31"


def test_unchanged_rows_are_untouched(dim_customer) -> None:
    before = _rows(dim_customer)
    stats = _apply(dim_customer, pd.DataFrame({"CustomerID": [1, 2], "Status": ["A", "A"], "Attr": [5, 7]}))

    assert stats["success"] is True
    assert stats["scd_type2_changes"] == 0
    assert stats["updated_records"] == 0
    assert _rows(dim_customer) == before


def test_new_business_key_is_inserted_as_current(dim_customer) -> None:
    stats = _apply(dim_customer, pd.DataFrame({"CustomerID": [3], "Status": ["A"], "Attr": [9]}))

    assert stats["success"] is True
    assert stats["new_records"] == 1
    rows = _rows(dim_customer)
    assert len(rows) == 3
    new = [r for r in rows if r[1] == 3]
    assert len(new) == 1
    assert new[0][4] is True
    assert new[0][5] == 2
    assert new[0][7].isoformat() == "9999-12-31"


def test_non_scd_change_updates_batch_id_only(dim_customer) -> None:
    stats = _apply(dim_customer, pd.DataFrame({"CustomerID": [1, 2], "Status": ["A", "A"], "Attr": [6, 7]}), 3)

    assert stats["success"] is True
    assert stats["updated_records"] == 1
    assert stats["scd_type2_changes"] == 0
    rows = _rows(dim_customer)
    assert len(rows) == 2
    assert rows[0][0] == 10
    assert rows[0][4] is True
    assert rows[0][5] == 3
    assert rows[0][3] == 5
    assert rows[1][5] == 1


def test_dml_uses_bound_parameters(dim_customer) -> None:
    conn = _RecordingConn(dim_customer)
    stats = _apply(conn, pd.DataFrame({"CustomerID": [1, 2], "Status": ["B", "A"], "Attr": [5, 8]}))

    assert stats["success"] is True
    dml = [(sql, params) for sql, params in conn.calls if sql.lstrip().upper().startswith(("UPDATE", "INSERT"))]
    assert len(dml) == 3
    for sql, params in dml:
        assert "?" in sql
        assert params
        assert "'B'" not in sql
        assert "2024" not in sql
    expire_sql, expire_params = next((s, p) for s, p in dml if "IsCurrent = 0" in s)
    assert expire_params[0] == EFFECTIVE
    assert 10 in expire_params
    batch_params = next(p for s, p in dml if s.lstrip().upper().startswith("UPDATE") and "BatchID = ?" in s)
    assert batch_params[0] == 2
    assert 20 in batch_params


def test_failed_insert_rolls_back_expiration(dim_customer) -> None:
    before = _rows(dim_customer)
    conn = _RecordingConn(dim_customer, fail_on="INSERT")
    scd = EnhancedSCDType2Processor(connection=conn)
    stats = scd.process_scd_changes(
        new_data=pd.DataFrame({"CustomerID": [1], "Status": ["B"], "Attr": [5]}),
        table_name="DimCustomer",
        business_keys=["CustomerID"],
        scd_columns=["Status"],
        batch_id=2,
        effective_date=EFFECTIVE,
    )

    assert stats["success"] is False
    assert _rows(dim_customer) == before
    assert scd.get_change_audit_trail() == []


def test_unlisted_table_name_is_rejected_before_any_write(dim_customer) -> None:
    conn = _RecordingConn(dim_customer)
    scd = EnhancedSCDType2Processor(connection=conn)
    stats = scd.process_scd_changes(
        new_data=pd.DataFrame({"CustomerID": [1], "Status": ["B"]}),
        table_name="DimCustomer; DROP TABLE DimCustomer",
        business_keys=["CustomerID"],
        scd_columns=["Status"],
        batch_id=2,
        effective_date=EFFECTIVE,
    )

    assert stats["success"] is False
    assert not any(sql.lstrip().upper().startswith(("UPDATE", "INSERT")) for sql, _ in conn.calls)
    assert len(_rows(dim_customer)) == 2
