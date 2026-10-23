from __future__ import annotations

import re
from typing import Any

import pyarrow as pa


class FakeGizmoSQLServer:
    def __init__(
        self,
        *,
        version: str = "v1.39.0",
        edition: str = "Core",
        vendor_version: str = "duckdb v1.5.5",
        duckdb_version: str = "v1.5.5",
        access_mode: str = "automatic",
        database_readonly: bool = False,
        schemas: tuple[str, ...] = ("main",),
        tables: dict[str, int] | None = None,
        setting_scopes: dict[str, str] | None = None,
        arrow_schema: pa.Schema | None = None,
    ) -> None:
        self.version = version
        self.edition = edition
        self.vendor_version = vendor_version
        self.duckdb_version = duckdb_version
        self.access_mode = access_mode
        self.database_readonly = database_readonly
        self.schemas = set(schemas)
        self.tables = dict(tables or {})
        self.setting_scopes = {"default_null_order": "GLOBAL", "threads": "GLOBAL", "enable_profiling": "LOCAL"}
        self.setting_scopes.update(setting_scopes or {})
        self.setting_values = {"enable_profiling": "query_tree", "threads": "4"}
        self.arrow_schema = arrow_schema or pa.schema([("a", pa.int32()), ("b", pa.string())])
        self.statements: list[tuple[int, str, Any]] = []
        self.ingested: dict[str, list[pa.RecordBatch]] = {}
        self.ingest_error: Exception | None = None
        self.ingest_reports: int | None = None
        self.failures: dict[str, Exception] = {}
        self.delay_until_cancel: str | None = None
        self.connections: list[FakeRawConnection] = []

    def sql_log(self, connection_id: int | None = None) -> list[str]:
        return [sql for cid, sql, _ in self.statements if connection_id is None or cid == connection_id]

    def respond(self, sql: str, parameters: Any) -> list[tuple[Any, ...]] | None:
        normalized = " ".join(sql.lower().split())
        for fragment, error in self.failures.items():
            if fragment in normalized:
                raise error
        if "gizmosql_version()" in normalized:
            return [(self.version, self.edition)]
        if normalized == "select version()":
            return [(self.duckdb_version,)]
        if "readonly from duckdb_databases()" in normalized:
            return [(self.database_readonly,)]
        if "select name, scope from duckdb_settings()" in normalized:
            return [(name, self.setting_scopes[name]) for name in parameters if name in self.setting_scopes]
        if "select name, value from duckdb_settings()" in normalized:
            values = {"access_mode": self.access_mode, "memory_limit": "8 GiB", **self.setting_values}
            return [(name, values[name]) for name in parameters if name in values]
        if "from information_schema.schemata" in normalized:
            return [(1,)] if parameters[0] in self.schemas else []
        if "from duckdb_databases() where database_name" in normalized:
            return [(1,)]
        if "from information_schema.tables" in normalized:
            return [(name,) for name in self.tables]
        count = re.match(r'select count\(\*\) from "?([a-z0-9_]+)"?$', normalized)
        if count:
            return [(self.tables.get(count.group(1), 0),)]
        if normalized == "select 1":
            return [(1,)]
        if normalized.startswith("select "):
            return [(1, "row")]
        if normalized.startswith("explain"):
            return [("physical_plan", '{"name": "PROJECTION", "children": []}')]
        return None


class FakeCursor:
    def __init__(self, raw: FakeRawConnection) -> None:
        self.raw = raw
        self.server = raw.server
        self.description: list[tuple[str]] | None = None
        self.rows: list[tuple[Any, ...]] = []
        self.closed = False
        self.cancelled = False
        self.cancel_event = raw.cancel_event

    def execute(self, sql: str, parameters: Any = None) -> None:
        self.server.statements.append((self.raw.connection_id, sql, parameters))
        marker = self.server.delay_until_cancel
        if marker and marker in sql:
            self.cancel_event.wait(5)
            raise RuntimeError("CANCELLED: context canceled")
        rows = self.server.respond(sql, parameters)
        self.rows = rows or []
        self.description = [("c",)] if rows is not None else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows, self.rows = self.rows, []
        return rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows.pop(0) if self.rows else None

    def fetchmany(self, size: int = 1) -> list[tuple[Any, ...]]:
        rows, self.rows = self.rows[:size], self.rows[size:]
        return rows

    def fetch_arrow_table(self) -> pa.Table:
        return self.server.arrow_schema.empty_table()

    def adbc_ingest(self, table: str, data: Any, mode: str = "create") -> int:
        self.server.statements.append((self.raw.connection_id, f"INGEST {mode} {table}", None))
        if self.server.ingest_error is not None:
            for _ in data:
                break
            raise self.server.ingest_error
        batches = list(data)
        self.server.ingested.setdefault(table, []).extend(batches)
        total = sum(batch.num_rows for batch in batches)
        self.server.tables[table] = self.server.tables.get(table, 0) + total
        return self.server.ingest_reports if self.server.ingest_reports is not None else total

    def adbc_cancel(self) -> None:
        self.cancelled = True
        self.cancel_event.set()

    def close(self) -> None:
        self.closed = True


class FakeRawConnection:
    def __init__(self, server: FakeGizmoSQLServer, **kwargs: Any) -> None:
        import threading

        self.server = server
        self.kwargs = kwargs
        self.connection_id = len(server.connections)
        self.cancel_event = threading.Event()
        self.cursors: list[FakeCursor] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        server.connections.append(self)

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        if any(not cursor.closed for cursor in self.cursors):
            raise RuntimeError("Cannot close AdbcConnection with open AdbcStatement")
        self.closed = True

    def adbc_get_info(self) -> dict[str, str]:
        return {"vendor_name": "gizmosql", "vendor_version": self.server.vendor_version}


class FakeDriver:
    def __init__(self, server: FakeGizmoSQLServer, connect_error: Exception | None = None) -> None:
        self.server = server
        self.connect_error = connect_error
        self.connect_calls: list[dict[str, Any]] = []

    def connect(self, uri: str, **kwargs: Any) -> FakeRawConnection:
        self.connect_calls.append({"uri": uri, **kwargs})
        if self.connect_error is not None:
            raise self.connect_error
        return FakeRawConnection(self.server, uri=uri, **kwargs)
