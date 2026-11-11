from __future__ import annotations

import csv
from pathlib import Path

import pytest

from benchbox.core.tsbs_devops.benchmark import TSBSDevOpsBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object | None]] = []
        self.commits = 0

    def execute(self, sql: str, params=None) -> None:
        self.executed.append((sql, params))

    def commit(self) -> None:
        self.commits += 1


def test_load_data_requires_generated_tables(tmp_path: Path) -> None:
    benchmark = TSBSDevOpsBenchmark(scale_factor=0.01, output_dir=tmp_path)

    with pytest.raises(ValueError, match="No data has been generated"):
        benchmark._load_data(_FakeConnection())


def test_load_table_data_skips_malformed_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "tags.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "hostname",
                "region",
                "datacenter",
                "rack",
                "os",
                "arch",
                "team",
                "service",
                "service_version",
                "service_environment",
            ]
        )
        writer.writerow(["host-a", "us-east", "dc1", "rack1", "linux", "x86_64", "team-a", "svc", "1", "prod"])
        writer.writerow(["broken", "row"])

    benchmark = TSBSDevOpsBenchmark(scale_factor=0.01, output_dir=tmp_path)
    loaded = benchmark._load_table_data(_FakeConnection(), "tags", csv_path)

    assert loaded == 1


def test_load_data_creates_schema_and_loads_known_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    benchmark = TSBSDevOpsBenchmark(scale_factor=0.01, output_dir=tmp_path)

    files = {}
    for name in ["tags", "cpu", "mem", "disk", "net"]:
        path = tmp_path / f"{name}.csv"
        path.write_text("ignored\n", encoding="utf-8")
        files[name] = path

    benchmark.tables = files

    monkeypatch.setattr(
        benchmark, "get_create_tables_sql", lambda **_kwargs: "CREATE TABLE a(x INT);CREATE TABLE b(y INT);"
    )
    monkeypatch.setattr(benchmark, "_load_table_data", lambda _conn, _table, _file: 3)

    conn = _FakeConnection()
    benchmark._load_data(conn)

    assert conn.commits == 2
    assert any(sql.startswith("CREATE TABLE a") for sql, _ in conn.executed)
    assert any(sql.startswith("CREATE TABLE b") for sql, _ in conn.executed)


