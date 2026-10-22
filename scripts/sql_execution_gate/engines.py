from __future__ import annotations

import csv
import importlib.util
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Dataset:
    benchmark: str
    ddl: str
    tables: dict[str, Path]


class QueryTimeout(Exception):
    pass


def _split(sql: str, dialect: str) -> list[str]:
    from benchbox.utils.dialect_utils import split_sql_statements

    return split_sql_statements(sql, dialect)


def _data_lines(path: Path, columns: int):
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for line in handle:
            fields = line.rstrip("\r\n").split("|")
            fields = fields[:columns] + [""] * (columns - len(fields))
            yield [None if field == "" else field for field in fields]


class SqliteEngine:
    name = "sqlite"
    dialect = "sqlite"

    def __init__(self, timeout: float):
        self.timeout = timeout
        self.connection: sqlite3.Connection | None = None

    @property
    def version(self) -> str:
        return sqlite3.sqlite_version

    @staticmethod
    def available() -> bool:
        return True

    def open(self, dataset: Dataset, parquet_dir: Path) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(dataset.ddl)
        for table, path in dataset.tables.items():
            columns = len(self.connection.execute(f"PRAGMA table_info({table})").fetchall())
            marks = ",".join("?" * columns)
            self.connection.executemany(f"INSERT INTO {table} VALUES ({marks})", _data_lines(path, columns))
        self.connection.commit()

    def execute(self, sql: str) -> list[tuple]:
        assert self.connection is not None
        deadline = time.monotonic() + self.timeout
        self.connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
        rows: list[tuple] = []
        try:
            for statement in _split(sql, self.dialect):
                cursor = self.connection.execute(statement)
                if cursor.description is not None:
                    rows = cursor.fetchall()
        except sqlite3.OperationalError as error:
            if "interrupted" in str(error):
                raise QueryTimeout(f"exceeded {self.timeout:g}s") from error
            raise
        finally:
            self.connection.set_progress_handler(None, 0)
        return rows

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()


class DuckdbEngine:
    name = "duckdb"
    dialect = "duckdb"

    def __init__(self, timeout: float):
        self.timeout = timeout
        self.connection = None

    @property
    def version(self) -> str:
        import duckdb

        return duckdb.__version__

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("duckdb") is not None

    def open(self, dataset: Dataset, parquet_dir: Path) -> None:
        import duckdb

        self.connection = duckdb.connect(":memory:")
        self.connection.execute(dataset.ddl)
        for table, path in dataset.tables.items():
            described = self.connection.execute(f"DESCRIBE {table}").fetchall()
            columns = ", ".join(f"'{row[0]}': '{row[1]}'" for row in described)
            self.connection.execute(
                f"INSERT INTO {table} SELECT * FROM read_csv('{path}', delim='|', header=false, quote='', "
                f"escape='', null_padding=true, columns={{{columns}}})"
            )
            self.connection.execute(f"COPY {table} TO '{parquet_dir / (table + '.parquet')}' (FORMAT parquet)")

    def execute(self, sql: str) -> list[tuple]:
        assert self.connection is not None
        timer = threading.Timer(self.timeout, self.connection.interrupt)
        timer.start()
        rows: list[tuple] = []
        try:
            for statement in _split(sql, self.dialect):
                cursor = self.connection.execute(statement)
                if cursor.description is not None:
                    rows = cursor.fetchall()
        except Exception as error:
            if type(error).__name__ == "InterruptException":
                raise QueryTimeout(f"exceeded {self.timeout:g}s") from error
            raise
        finally:
            timer.cancel()
        return rows

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()


class DatafusionEngine:
    name = "datafusion"
    dialect = "datafusion"

    def __init__(self, timeout: float):
        self.timeout = timeout
        self.context = None

    @property
    def version(self) -> str:
        import datafusion

        return datafusion.__version__

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("datafusion") is not None

    def open(self, dataset: Dataset, parquet_dir: Path) -> None:
        import datafusion

        self.context = datafusion.SessionContext()
        for table in dataset.tables:
            self.context.register_parquet(table, str(parquet_dir / f"{table}.parquet"))

    def execute(self, sql: str) -> list[tuple]:
        assert self.context is not None
        rows: list[tuple] = []
        for statement in _split(sql, self.dialect):
            batches = self.context.sql(statement).collect()
            rows = [
                tuple(column[index].as_py() for column in batch.columns)
                for batch in batches
                for index in range(batch.num_rows)
            ]
        return rows

    def close(self) -> None:
        self.context = None


class PostgresEngine:
    name = "postgres"
    dialect = "postgres"

    def __init__(self, timeout: float):
        self.timeout = timeout
        self.connection = None
        self.schema = f"sql_gate_{os.getpid()}"

    @property
    def version(self) -> str:
        assert self.connection is not None
        return str(self.connection.execute("SHOW server_version").fetchone()[0])

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("BENCHBOX_POSTGRES_DSN")) and importlib.util.find_spec("psycopg") is not None

    def open(self, dataset: Dataset, parquet_dir: Path) -> None:
        import psycopg

        self.connection = psycopg.connect(os.environ["BENCHBOX_POSTGRES_DSN"], autocommit=True)
        self.connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        self.connection.execute(f"CREATE SCHEMA {self.schema}")
        self.connection.execute(f"SET search_path TO {self.schema}")
        self.connection.execute(dataset.ddl)
        for table, path in dataset.tables.items():
            columns = self.connection.execute(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
                (self.schema, table),
            ).fetchone()[0]
            statement = f"COPY {table} FROM STDIN (FORMAT csv, DELIMITER '|', NULL '', QUOTE E'\\x01')"
            with self.connection.cursor().copy(statement) as copy:
                writer = csv.writer(_Sink(copy), delimiter="|", quotechar="\x01", lineterminator="\n")
                for row in _data_lines(path, columns):
                    writer.writerow(["" if cell is None else cell for cell in row])
        self.connection.execute(f"SET statement_timeout = {int(self.timeout * 1000)}")

    def execute(self, sql: str) -> list[tuple]:
        assert self.connection is not None
        rows: list[tuple] = []
        for statement in _split(sql, self.dialect):
            cursor = self.connection.execute(statement)
            if cursor.description is not None:
                rows = cursor.fetchall()
        return rows

    def close(self) -> None:
        if self.connection is not None:
            self.connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
            self.connection.close()


class _Sink:
    def __init__(self, copy):
        self.copy = copy

    def write(self, text: str) -> int:
        self.copy.write(text)
        return len(text)


ENGINE_CLASSES = (SqliteEngine, DuckdbEngine, DatafusionEngine, PostgresEngine)


def available_engines(selected: list[str] | None = None) -> list[type]:
    return [engine for engine in ENGINE_CLASSES if (not selected or engine.name in selected) and engine.available()]
