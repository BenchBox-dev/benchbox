import json
from pathlib import Path

import pytest

from benchbox.core.tpcds_obt import schema
from benchbox.core.tpcds_obt.etl.transformer import SUPPORTED_CHANNELS, TPCDSOBTTransformer

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class FakeConnection:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.statements: list[str] = []
        self.last_sql: str = ""

    def execute(self, sql: str) -> "FakeConnection":
        self.last_sql = " ".join(sql.split())
        self.statements.append(self.last_sql)

        if "COPY" in sql:
            start = sql.find("TO '") + 4
            end = sql.find("'", start)
            out_path = Path(sql[start:end])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("data\n", encoding="utf-8")

        return self

    def fetchone(self) -> tuple[int]:
        if "has_return" in self.last_sql:
            return (5,)
        return (15,)

    def fetchall(self) -> list[tuple[str, int]]:
        return [("store", 10), ("web", 5)]

    def close(self) -> None:
        return None


class FakeDuckDB:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.last_connection: FakeConnection | None = None

    def connect(self, _: str) -> FakeConnection:
        self.last_connection = FakeConnection(self.tmp_path)
        return self.last_connection


def test_the_first_channel_creates_the_table_and_the_others_insert() -> None:
    transformer = TPCDSOBTTransformer()
    columns = schema.get_obt_columns("minimal")

    statements = transformer._build_statements(columns, ["store", "web"], "minimal")

    assert len(statements) == 2
    assert statements[0].startswith("CREATE OR REPLACE TABLE tpcds_sales_returns_obt AS")
    assert "CAST('store' AS VARCHAR(10)) AS channel" in statements[0]
    assert "FROM store_sales ss" in statements[0]
    assert statements[1].startswith("INSERT INTO tpcds_sales_returns_obt")
    assert "CAST('web' AS VARCHAR(10)) AS channel" in statements[1]


def _make_fake_transform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TPCDSOBTTransformer:
    fake_duckdb = FakeDuckDB(tmp_path)
    transformer = TPCDSOBTTransformer(duckdb_module=fake_duckdb)

    def fake_resolve(base_dir: Path, table_name: str) -> Path:
        path = base_dir / f"{table_name}.dat"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    monkeypatch.setattr(transformer, "_resolve_source_path", fake_resolve)
    return transformer


def test_transform_with_fake_duckdb_writes_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transformer = _make_fake_transform(tmp_path, monkeypatch)

    result = transformer.transform(
        tpcds_dir=tmp_path,
        output_dir=tmp_path / "out",
        mode="minimal",
        channels=SUPPORTED_CHANNELS,
        output_format="dat",
        scale_factor=1.0,
    )

    manifest_path = result["manifest"]
    output_path = result["table"]

    assert output_path.exists()
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert manifest["table"] == "tpcds_sales_returns_obt"
    assert manifest["rows_total"] == 15
    assert manifest["rows_with_returns"] == 5
    assert manifest["channels"] == list(SUPPORTED_CHANNELS)
    assert manifest["column_count"] == len(schema.get_obt_columns("minimal"))


def test_transform_default_format_is_parquet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transformer = _make_fake_transform(tmp_path, monkeypatch)

    result = transformer.transform(
        tpcds_dir=tmp_path,
        output_dir=tmp_path / "out",
        mode="minimal",
        channels=SUPPORTED_CHANNELS,
        scale_factor=1.0,
    )

    output_path = result["table"]
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))

    assert output_path.suffix == ".parquet", f"Expected .parquet, got {output_path.suffix}"
    assert output_path.exists()
    assert manifest["output_format"] == "parquet"


def test_parquet_output_materially_smaller_than_dat(tmp_path: Path) -> None:
    import os

    import duckdb

    dat_path = tmp_path / "obt_test.dat"
    parquet_path = tmp_path / "obt_test.parquet"

    cols_ddl = ", ".join(f"c{i} VARCHAR" for i in range(20))
    col_exprs = ", ".join(
        f"CASE WHEN (row_number() OVER () + {i}) % 5 < 3 THEN NULL ELSE 'value_{i}' END AS c{i}" for i in range(20)
    )
    conn = duckdb.connect(":memory:")
    conn.execute(f"CREATE TABLE t ({cols_ddl})")
    conn.execute(f"INSERT INTO t SELECT {col_exprs} FROM range(500)")

    conn.execute(f"COPY t TO '{dat_path}' (DELIMITER '|', HEADER FALSE, NULL '')")
    conn.execute(f"COPY t TO '{parquet_path}' (FORMAT PARQUET)")
    conn.close()

    dat_size = os.path.getsize(dat_path)
    parquet_size = os.path.getsize(parquet_path)

    assert parquet_size < dat_size * 0.8, (
        f"Expected parquet ({parquet_size} bytes) to be < 80% of dat ({dat_size} bytes), "
        f"but ratio was {parquet_size / dat_size:.2%}"
    )


def test_full_width_build_at_scale_one_fails_when_coverage_breaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchbox.core.tpcds_obt.etl.transformer import OBTCoverageError

    transformer = _make_fake_transform(tmp_path, monkeypatch)

    with pytest.raises(OBTCoverageError, match="15 calendar days"):
        transformer.transform(
            tpcds_dir=tmp_path,
            output_dir=tmp_path / "out",
            channels=SUPPORTED_CHANNELS,
            scale_factor=1.0,
        )


def test_small_scale_build_records_broken_coverage_without_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transformer = _make_fake_transform(tmp_path, monkeypatch)

    result = transformer.transform(
        tpcds_dir=tmp_path,
        output_dir=tmp_path / "out",
        channels=SUPPORTED_CHANNELS,
        scale_factor=0.01,
    )

    coverage = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))["coverage"]
    assert coverage == {
        "sold_date_range": ["1998-01-01", "2002-12-31"],
        "missing_sold_dates": 15,
        "missing_items": 15,
        "enforced": False,
    }


def test_a_mixed_case_full_mode_still_enforces_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from benchbox.core.tpcds_obt.etl.transformer import OBTCoverageError

    transformer = _make_fake_transform(tmp_path, monkeypatch)

    with pytest.raises(OBTCoverageError, match="15 calendar days"):
        transformer.transform(
            tpcds_dir=tmp_path,
            output_dir=tmp_path / "out",
            mode="Full",
            channels=SUPPORTED_CHANNELS,
            scale_factor=1.0,
        )


def test_wide_tables_get_fewer_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 16)

    assert TPCDSOBTTransformer._thread_limit(715) == 2
    assert TPCDSOBTTransformer._thread_limit(424) == 3
    assert TPCDSOBTTransformer._thread_limit(100_000) == 1
    monkeypatch.setattr("os.cpu_count", lambda: 2)
    assert TPCDSOBTTransformer._thread_limit(715) == 2
