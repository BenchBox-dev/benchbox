from __future__ import annotations

import hashlib
import io
import random
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import benchbox.core.tpcdi.benchmark as benchmark_module
from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.etl.pipeline import TPCDIETLPipeline
from benchbox.core.tpcdi.etl.scd_processor import EnhancedSCDType2Processor
from benchbox.core.tpcdi.etl.sql_backend import SQLETLBackend, bulk_insert_dataframe

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

SCD_ETL_SOURCE = Path(benchmark_module.__file__).parent / "etl" / "scd_processor.py"


class _ExecutemanyOnly:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _duckdb() -> Any:
    return pytest.importorskip("duckdb").connect(":memory:")


def _snapshot(connection: Any, tables: list[str]) -> dict[str, tuple[int, dict[str, str]]]:
    snapshot: dict[str, tuple[int, dict[str, str]]] = {}
    for table in tables:
        columns = [row[0] for row in connection.execute(f'DESCRIBE "{table}"').fetchall()]
        rows = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        checksums = {}
        for column in columns:
            values = connection.execute(f'SELECT CAST("{column}" AS VARCHAR) FROM "{table}" ORDER BY 1').fetchall()
            checksums[column] = hashlib.md5(repr(values).encode()).hexdigest()
        snapshot[table] = (rows, checksums)
    return snapshot


def _backend(connection: Any, benchmark: TPCDIBenchmark) -> SQLETLBackend:
    return SQLETLBackend(
        connection=connection,
        create_tables_sql=benchmark.get_create_tables_sql(),
        execute_validation_query=lambda _qid, _conn: [],
        validation_query_ids=[],
    )


def _customers(count: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "SK_CustomerID": range(1, count + 1),
            "CustomerID": range(1000, 1000 + count),
            "TaxID": [f"TAX{i:05d}" for i in range(count)],
            "Status": ["ACTIVE", "INACTIVE"] * (count // 2) + ["ACTIVE"] * (count % 2),
            "LastName": [f"Last{i}" for i in range(count)],
            "Tier": [1 + i % 3 for i in range(count)],
            "DOB": ["1980-01-01"] * count,
            "NationalTaxRate": [0.25] * count,
            "IsCurrent": [True] * count,
            "BatchID": [1] * count,
            "EffectiveDate": ["2023-01-01"] * count,
            "EndDate": ["9999-12-31"] * count,
        }
    )


def test_duckdb_dataframe_load_matches_executemany_reference(tmp_path: Path) -> None:
    benchmark = TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path, max_workers=1)
    frame = _customers(2500)
    snapshots = []
    for wrap in (False, True):
        connection = _duckdb()
        target = _ExecutemanyOnly(connection) if wrap else connection
        backend = _backend(target, benchmark)
        result = backend.load_dataframes({"DimCustomer": frame}, batch_type="historical")
        assert result == {"records_loaded": 2500, "tables_updated": ["DimCustomer"]}
        snapshots.append(_snapshot(connection, ["DimCustomer"]))
        connection.close()

    assert snapshots[0] == snapshots[1]
    assert snapshots[0]["DimCustomer"][0] == 2500


def test_duckdb_customer_scd2_batch_matches_executemany_reference(tmp_path: Path) -> None:
    benchmark = TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path, max_workers=1)
    first = _customers(300).drop(columns=["SK_CustomerID", "BatchID", "IsCurrent", "EndDate"])
    second = first.iloc[:100].assign(Status="CLOSED", EffectiveDate="2023-06-01")
    snapshots = []
    for wrap in (False, True):
        connection = _duckdb()
        backend = _backend(_ExecutemanyOnly(connection) if wrap else connection, benchmark)
        backend.load_customer_scd2_batch(first, batch_type="historical")
        backend.load_customer_scd2_batch(second, batch_type="incremental")
        snapshots.append(_snapshot(connection, ["DimCustomer"]))
        connection.close()

    assert snapshots[0] == snapshots[1]
    assert snapshots[0]["DimCustomer"][0] == 400


def test_duckdb_dataframe_load_stores_missing_strings_as_null(tmp_path: Path) -> None:
    benchmark = TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path, max_workers=1)
    connection = _duckdb()
    backend = _backend(connection, benchmark)
    backend.load_dataframes(
        {"DimCustomer": pd.DataFrame({"SK_CustomerID": [1, 2], "LastName": ["Smith", None]})}, batch_type="historical"
    )

    assert connection.execute('SELECT "LastName" FROM "DimCustomer" ORDER BY 1').fetchall() == [("Smith",), (None,)]
    connection.close()


class _CopyCursor:
    __module__ = "psycopg2.extensions"

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.payloads: list[str] = []

    def copy_expert(self, sql: str, handle: io.StringIO) -> None:
        self.statements.append(sql)
        self.payloads.append(handle.read())

    def executemany(self, *_args: Any) -> None:
        raise AssertionError("PostgreSQL loads must use COPY")


_CopyCursor.__name__ = "cursor"
_CopyCursor.__module__ = "psycopg2.extensions"


class _CopyConnection:
    __module__ = "psycopg2.extensions"

    def __init__(self, cursor: _CopyCursor) -> None:
        self._cursor = cursor
        self.committed = False

    def cursor(self) -> _CopyCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True


def test_postgres_load_uses_copy_with_null_markers(tmp_path: Path) -> None:
    benchmark = TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path, max_workers=1)
    cursor = _CopyCursor()
    connection = _CopyConnection(cursor)
    backend = _backend(connection, benchmark)
    frame = pd.DataFrame({"SK_CustomerID": [1, 2], "LastName": ["Smith, J", None], "IsCurrent": [True, False]})

    loaded = backend._load_table_dataframe("DimCustomer", frame)

    assert loaded == 2
    assert connection.committed
    assert cursor.statements == [
        'COPY "DimCustomer" ("SK_CustomerID","LastName","IsCurrent") FROM STDIN WITH (FORMAT csv, NULL \'\\N\')'
    ]
    assert cursor.payloads == ['1,"Smith, J",True\n2,\\N,False\n']


def test_other_engines_keep_the_row_path() -> None:
    class _Other:
        pass

    assert bulk_insert_dataframe(_Other(), '"T"', ['"a"'], pd.DataFrame({"a": [1]})) is False


def test_pipeline_generators_match_executemany_reference(tmp_path: Path) -> None:
    benchmark = TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path, max_workers=1)
    tables = ["DimDate", "DimTime", "DimCustomer", "DimCompany", "DimSecurity", "DimAccount", "FactTrade"]
    snapshots = []
    for wrap in (False, True):
        connection = _duckdb()
        benchmark.create_schema(connection, "duckdb")
        pipeline = TPCDIETLPipeline(_ExecutemanyOnly(connection) if wrap else connection, benchmark)
        random.seed(7)
        counts = [
            pipeline._generate_date_dimension(),
            pipeline._generate_time_dimension(),
            pipeline._generate_customer_dimension(40),
            pipeline._generate_company_dimension(10),
            pipeline._generate_security_dimension(20),
            pipeline._generate_account_dimension(30),
            pipeline._generate_trade_fact(200),
        ]
        assert counts == [30, 48, 40, 10, 20, 30, 200]
        snapshots.append(_snapshot(connection, tables))
        connection.close()

    assert snapshots[0] == snapshots[1]


def _seed_dimension(connection: Any) -> None:
    connection.execute(
        "CREATE TABLE DimCustomer(SK_CustomerID BIGINT PRIMARY KEY, CustomerID BIGINT NOT NULL, Status VARCHAR, "
        "Attr INTEGER, Note VARCHAR, IsCurrent BOOLEAN NOT NULL, BatchID INTEGER NOT NULL, "
        "EffectiveDate DATE NOT NULL, EndDate DATE NOT NULL)"
    )
    rows = ", ".join(
        f"({i}, {i}, 'A', {i}, 'n{i}', TRUE, 1, DATE '2024-01-01', DATE '9999-12-31')" for i in range(1, 1201)
    )
    connection.execute(f"INSERT INTO DimCustomer VALUES {rows}")


def _scd_input() -> pd.DataFrame:
    keys = list(range(1, 1201)) + [5000, 5001]
    status = ["A"] * 1202
    attr = list(range(1, 1201)) + [1, 2]
    note = [f"n{i}" for i in range(1, 1201)] + ["x", "y"]
    for i in range(0, 1200, 4):
        status[i] = "B"
    for i in range(1, 1200, 4):
        attr[i] += 100
        note[i] = None if i % 8 == 1 else f"changed{i}"
    return pd.DataFrame({"CustomerID": keys, "Status": status, "Attr": attr, "Note": note})


def _expected_state(connection: Any, data: pd.DataFrame, effective: datetime) -> None:
    current = {row[1]: row for row in connection.execute("SELECT * FROM DimCustomer").fetchall()}
    next_sk = 1201
    for row in data.itertuples(index=False):
        old = current.get(row.CustomerID)
        note = None if pd.isna(row.Note) else row.Note
        if old is None:
            connection.execute(
                "INSERT INTO DimCustomer VALUES (?, ?, ?, ?, ?, TRUE, 2, ?, DATE '9999-12-31')",
                [next_sk, row.CustomerID, row.Status, row.Attr, note, effective],
            )
            next_sk += 1
        elif old[2] != row.Status:
            connection.execute(
                "UPDATE DimCustomer SET IsCurrent = FALSE, EndDate = ? WHERE SK_CustomerID = ?", [effective, old[0]]
            )
            connection.execute(
                "INSERT INTO DimCustomer VALUES (?, ?, ?, ?, ?, TRUE, 2, ?, DATE '9999-12-31')",
                [next_sk, row.CustomerID, row.Status, row.Attr, note, effective],
            )
            next_sk += 1
        elif old[3] != row.Attr or old[4] != note:
            connection.execute(
                "UPDATE DimCustomer SET BatchID = 2, Attr = ?, Note = ? WHERE SK_CustomerID = ?",
                [row.Attr, note, old[0]],
            )


def test_scd_batch_matches_per_row_reference_and_uses_set_based_updates() -> None:
    effective = datetime(2024, 3, 1)
    data = _scd_input()

    reference = _duckdb()
    _seed_dimension(reference)
    _expected_state(reference, data, effective)

    class _Recorder:
        def __init__(self, inner: Any) -> None:
            self.inner = inner
            self.sql: list[str] = []

        @property
        def in_transaction(self) -> Any:
            return getattr(self.inner, "in_transaction", None)

        def execute(self, sql: str, params: Any = None) -> Any:
            self.sql.append(sql)
            return self.inner.execute(sql) if params is None else self.inner.execute(sql, params)

    actual = _duckdb()
    _seed_dimension(actual)
    recorder = _Recorder(actual)
    stats = EnhancedSCDType2Processor(connection=recorder).process_scd_changes(
        new_data=data,
        table_name="DimCustomer",
        business_keys=["CustomerID"],
        scd_columns=["Status"],
        batch_id=2,
        effective_date=effective,
        non_scd_columns=["Attr", "Note"],
    )

    assert stats["success"] is True, stats["error_message"]
    assert (stats["new_records"], stats["scd_type2_changes"], stats["updated_records"]) == (2, 300, 300)
    assert _snapshot(actual, ["DimCustomer"]) == _snapshot(reference, ["DimCustomer"])
    updates = [sql for sql in recorder.sql if sql.lstrip().upper().startswith("UPDATE")]
    assert sum("FROM (VALUES" in sql for sql in updates) == 3
    assert len(updates) == 4
    assert actual.execute("SELECT COUNT(*) FROM DimCustomer WHERE BatchID = 2").fetchone() == (602,)


def test_scd_processor_has_no_row_loops() -> None:
    source = SCD_ETL_SOURCE.read_text(encoding="utf-8")
    assert "iterrows" not in source
    assert "executemany" not in source


def test_timed_load_and_transform_regions_contain_their_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    benchmark = TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path, max_workers=1)
    events: list[Any] = []
    ticks = iter(range(1, 1000))

    def fake_mono_time() -> int:
        token = next(ticks)
        events.append(("start", token))
        return token

    def fake_elapsed(token: int) -> float:
        events.append(("stop", token))
        return 0.0

    original_load = benchmark._load_transformed_data
    original_transform = benchmark._transform_source_data

    def load(**kwargs: Any) -> Any:
        events.append("load_work")
        return original_load(**kwargs)

    def transform(*args: Any, **kwargs: Any) -> Any:
        events.append("transform_work")
        return original_transform(*args, **kwargs)

    monkeypatch.setattr(benchmark_module, "mono_time", fake_mono_time)
    monkeypatch.setattr(benchmark_module, "elapsed_seconds", fake_elapsed)
    monkeypatch.setattr(benchmark, "_load_transformed_data", load)
    monkeypatch.setattr(benchmark, "_transform_source_data", transform)

    connection = _duckdb()
    benchmark.create_schema(connection, "duckdb")
    result = benchmark.run_etl_pipeline(connection, batch_type="historical", validate_data=False)

    assert result["success"] is True
    for work in ("transform_work", "load_work"):
        position = events.index(work)
        assert events[position - 1][0] == "start"
        assert events[position + 1] == ("stop", events[position - 1][1])
    connection.close()


def test_source_csv_read_matches_default_engine(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text(
        "TradeID,Symbol,TradeDate,Stamp,Quantity,Flag,Price\n"
        "1,AAA,2023-01-15,2023-01-15 10:30:00,100,true,1.5\n"
        "2,,2023-01-16,2023-01-16 11:00:00,,false,\n",
        encoding="utf-8",
    )

    frame = TPCDIBenchmark._read_source_csv(str(source))

    pd.testing.assert_frame_equal(frame, pd.read_csv(source))
