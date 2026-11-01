from __future__ import annotations

import contextlib
import csv
import io
import logging
import re
from collections.abc import Sequence

import pyarrow as pa

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


__all__ = ["ClickHouseLocalClient", "ClickHouseCloudClient"]
