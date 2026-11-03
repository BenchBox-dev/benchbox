from __future__ import annotations

import contextlib
import csv
import io
import logging
import re
from collections.abc import Sequence

import pyarrow as pa

from .statements import (
    CREATE_READY,
    DROP,
    EXCHANGE,
    IF_MISSING,
    RENAME,
    USE,
    lightweight_update_target,
    prepare_clickhouse_statements,
    table_lifecycle,
)

_ABSENT = object()


class _Lifecycle:
    def __init__(self, database: str, known) -> None:
        self.database = database
        self.state: dict[str, object] = {}
        self.uncertain: set[str] = set()
        self._known = known

    def qualify(self, name: str) -> str:
        return name if "." in name else f"{self.database}.{name}"

    def resolve(self, name: str) -> object:
        return self.state.get(name, self._known(name))

    def apply(self, statement: str) -> None:
        for event in table_lifecycle(statement):
            kind = event[0]
            if kind == USE:
                self.database = event[1]
            elif kind == RENAME:
                source, target = self.qualify(event[1]), self.qualify(event[2])
                self.state[target] = self.resolve(source)
                self.state[source] = _ABSENT
            elif kind == EXCHANGE:
                left, right = self.qualify(event[1]), self.qualify(event[2])
                self.state[left], self.state[right] = self.resolve(right), self.resolve(left)
            elif kind == DROP:
                self.state[self.qualify(event[1])] = _ABSENT
            else:
                table = self.qualify(event[1])
                created = True if kind == CREATE_READY else _ABSENT
                prior = self.resolve(table) if event[2] == IF_MISSING else _ABSENT
                if isinstance(prior, str):
                    self.uncertain.add(prior)
                self.state[table] = created if prior is _ABSENT else prior


logger = logging.getLogger(__name__)


class _ResultProxy(Sequence):
    def __init__(self, rows_or_df) -> None:
        self._df = None
        self._rows: list | None = None
        if rows_or_df is None:
            self._rows = []
        elif hasattr(rows_or_df, "itertuples"):
            self._df = rows_or_df
        else:
            self._rows = list(rows_or_df)
        self._pos = 0

    def _ensure_rows(self) -> list:
        if self._rows is None:
            assert self._df is not None
            self._rows = [tuple(r) for r in self._df.itertuples(index=False, name=None)]
        return self._rows

    def fetchone(self):
        rows = self._ensure_rows()
        if self._pos < len(rows):
            row = rows[self._pos]
            self._pos += 1
            return row
        return None

    def fetchall(self):
        rows = self._ensure_rows()
        remaining = rows[self._pos :]
        self._pos = len(rows)
        return remaining

    def __getitem__(self, idx):
        return self._ensure_rows()[idx]

    def __iter__(self):
        return iter(self._ensure_rows())

    def __len__(self):
        if self._df is not None and self._rows is None:
            return len(self._df)
        return len(self._rows or [])

    def __bool__(self):
        return self.__len__() > 0

    def __eq__(self, other):
        if isinstance(other, _ResultProxy):
            return self._ensure_rows() == other._ensure_rows()
        if isinstance(other, list):
            return self._ensure_rows() == other
        return NotImplemented


class _ListResult(list):
    _pos = 0

    def fetchone(self):
        if self._pos < len(self):
            row = self[self._pos]
            self._pos += 1
            return row
        return None

    def fetchall(self):
        remaining = self[self._pos :]
        self._pos = len(self)
        return remaining


class ClickHouseServerConnection:
    def __init__(self, client) -> None:
        self._client = client
        self._lightweight_update_tables: set[str] = set()
        self._database: str | None = None

    def execute(self, query, *args, **kwargs):
        if args or not isinstance(query, str):
            return self._run(query, *args, **kwargs)
        result = None
        for statement in prepare_clickhouse_statements(query):
            result = self._run(statement, **kwargs)
            self._track(statement)
        return result

    def _track(self, statement: str) -> None:
        events = table_lifecycle(statement)
        ready = self._lightweight_update_tables
        if events and all(event[0] == USE for event in events):
            self._database = events[-1][1]
            return
        if not ready and all(event[0] != CREATE_READY for event in events):
            return
        lifecycle = _Lifecycle(self._current_database(), lambda name: name in ready)
        lifecycle.apply(statement)
        for name, value in lifecycle.state.items():
            if value is True:
                ready.add(name)
            else:
                ready.discard(name)
        self._database = lifecycle.database

    def _current_database(self) -> str:
        if self._database is None:
            self._database = str(self._client.execute("SELECT currentDatabase()")[0][0])
        return self._database

    def _run(self, query, *args, **kwargs):
        result = self._client.execute(query, *args, **kwargs)
        return _ListResult(result) if isinstance(result, list) else result

    def prepare_write(self, query: str) -> None:
        lifecycle = _Lifecycle(self._current_database(), lambda name: name)
        for statement in prepare_clickhouse_statements(query):
            table = lightweight_update_target(statement)
            if table is not None:
                origin = lifecycle.resolve(lifecycle.qualify(table))
                if (
                    isinstance(origin, str)
                    and origin not in self._lightweight_update_tables
                    and (origin not in lifecycle.uncertain or self._table_exists(origin))
                ):
                    self._apply_lightweight_update_settings(origin)
            lifecycle.apply(statement)

    def _table_exists(self, table: str) -> bool:
        rows = self._client.execute(f"EXISTS TABLE {table}")
        return bool(rows) and str(rows[0][0]) == "1"

    def _apply_lightweight_update_settings(self, table: str) -> None:
        self._client.execute(
            f"ALTER TABLE {table} MODIFY SETTING enable_block_number_column = 1, enable_block_offset_column = 1"
        )
        self._lightweight_update_tables.add(table)

    def __getattr__(self, name):
        return getattr(self._client, name)


class ClickHouseLocalClient:
    _conn: object
    _is_persistent: bool

    def __init__(self, db_path: str | None = None):
        self._initialized = True
        from ._dependencies import import_chdb, import_chdb_session

        if db_path:
            Session = import_chdb_session().Session
            self._conn = Session(path=db_path)
            self._is_persistent = True
        else:
            self._conn = import_chdb().connect()
            self._is_persistent = False

    def execute(self, query: str, params=None):
        try:
            if query.strip().upper().startswith("INSERT") and params:
                return self._execute_insert(query, params)

            result = (
                self._conn.query(query, "ArrowStream")
                if self._is_persistent
                else self._conn.query(query, format="ArrowStream")
            )
            return _ResultProxy(self._arrow_to_dataframe(result))

        except Exception as e:
            raise RuntimeError(f"ClickHouse local query failed: {e}") from e

    @staticmethod
    def _arrow_to_dataframe(result):
        import pandas as pd

        data = result.bytes()
        if not data:
            return pd.DataFrame()
        reader = pa.ipc.open_stream(io.BytesIO(data))
        return reader.read_all().to_pandas()

    def close(self):
        if hasattr(self, "_conn"):
            try:
                if hasattr(self._conn, "close"):
                    self._conn.close()
                elif hasattr(self._conn, "__del__"):
                    del self._conn
            except Exception:
                pass
        self._conn = None

    def _execute_insert(self, query: str, params):
        if isinstance(params, list) and params:
            values_list = []
            for row in params:
                formatted_values = []
                for val in row:
                    if isinstance(val, str):
                        escaped_val = val.replace("'", "''")
                        formatted_values.append(f"'{escaped_val}'")
                    elif val is None:
                        formatted_values.append("NULL")
                    else:
                        formatted_values.append(str(val))
                values_list.append(f"({', '.join(formatted_values)})")

            full_query = f"{query} {', '.join(values_list)}"
            if self._is_persistent:
                self._conn.query(full_query)
            else:
                self._conn.query(full_query, format="CSV")
            return _ResultProxy([])
        else:
            if self._is_persistent:
                self._conn.query(query)
            else:
                self._conn.query(query, format="CSV")
            return _ResultProxy([])

    def _parse_csv_line(self, line: str) -> tuple:
        import io

        reader = csv.reader(io.StringIO(line))
        row = next(reader)

        converted_row = []
        for val in row:
            if val == "":
                converted_row.append(None)
            else:
                try:
                    if "." not in val and val.lstrip("-").isdigit():
                        converted_row.append(int(val))
                    elif self._is_float(val):
                        converted_row.append(float(val))
                    else:
                        converted_row.append(val)
                except (ValueError, TypeError):
                    converted_row.append(val)

        return tuple(converted_row)

    def _is_float(self, val: str) -> bool:
        try:
            float(val)
            return True
        except ValueError:
            return False

    def executemany(self, query: str, params: list) -> _ResultProxy:
        if not params:
            return _ResultProxy([])

        parts = re.split(r"\sVALUES\s", query, maxsplit=1, flags=re.IGNORECASE)
        base_query = parts[0]

        values_list = []
        for row in params:
            formatted_values = []
            for val in row:
                if isinstance(val, str):
                    escaped = val.replace("\\", "\\\\").replace("'", "''")
                    formatted_values.append(f"'{escaped}'")
                elif val is None:
                    formatted_values.append("NULL")
                else:
                    formatted_values.append(str(val))
            values_list.append(f"({', '.join(formatted_values)})")

        full_query = f"{base_query} VALUES {', '.join(values_list)}"
        if self._is_persistent:
            self._conn.query(full_query)
        else:
            self._conn.query(full_query, format="CSV")
        return _ResultProxy([])

    def disconnect(self):
        self.close()

    def commit(self):
        pass


class ClickHouseCloudClient:
    def __init__(
        self,
        host: str,
        port: int = 8443,
        user: str = "default",
        password: str = "",
        database: str = "default",
        secure: bool = True,
        access_token: str | None = None,
        **kwargs,
    ):
        from ._dependencies import clickhouse_connect

        if clickhouse_connect is None:
            raise ImportError(
                "ClickHouse Cloud mode requires the clickhouse-connect package.\n"
                "Install with: uv add benchbox --extra clickhouse-cloud\n"
            )

        self._host = host
        self._port = port
        self._database = database

        connect_kwargs: dict = {
            "host": host,
            "port": port,
            "database": database,
            "secure": secure,
        }

        if access_token:
            connect_kwargs["access_token"] = access_token
            logger.info("Using OAuth/bearer token authentication for ClickHouse Cloud")
        else:
            connect_kwargs["username"] = user
            connect_kwargs["password"] = password

        connect_kwargs.update(kwargs)

        self._client = clickhouse_connect.get_client(**connect_kwargs)

        logger.info(f"ClickHouse Cloud client initialized: {host}:{port}")

    def execute(self, query: str, params=None):
        try:
            if query.strip().upper().startswith("INSERT") and params:
                return self._execute_insert(query, params)

            result = self._client.query(query)

            if result.result_set:
                return [tuple(row) for row in result.result_set]
            return []

        except Exception as e:
            raise RuntimeError(f"ClickHouse Cloud query failed: {e}") from e

    def _execute_insert(self, query: str, params):
        if isinstance(params, list) and params:
            import re

            match = re.match(r"INSERT\s+INTO\s+(\S+)", query, re.IGNORECASE)
            if match:
                table_name = match.group(1)
                self._client.insert(table_name, params)
                return []

        self._client.command(query)
        return []

    def command(self, query: str):
        return self._client.command(query)

    def close(self):
        if hasattr(self, "_client") and self._client:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None

    def disconnect(self):
        self.close()

    def commit(self):
        pass

    @property
    def database(self) -> str:
        return self._database


__all__ = ["ClickHouseLocalClient", "ClickHouseCloudClient", "ClickHouseServerConnection"]
