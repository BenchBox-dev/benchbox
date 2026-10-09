# ruff: noqa: SIM905

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Any

from benchbox.platforms.base.data_loading import (
    DataSource,
    FileFormatHandler,
    FileFormatRegistry,
    resolve_csv_dialect,
    validate_sql_identifier,
)
from benchbox.platforms.base.ddl_helpers import strip_foreign_keys
from benchbox.platforms.base.sql_execution import execute_sql_query
from benchbox.utils.clock import elapsed_seconds, mono_time

logger = logging.getLogger(__name__)


def _stream_load_treats_empty_as_null(delimiter: str) -> bool:
    return delimiter == ","


def _split_sql_literals(query: str) -> list[tuple[str, bool]]:
    segments: list[tuple[str, bool]] = []
    n = len(query)
    i = 0
    start = 0
    while i < n:
        if query[i] == "'":
            if start < i:
                segments.append((query[start:i], False))
            lit_start = i
            i += 1
            while i < n:
                c = query[i]
                if c == "\\" and i + 1 < n:
                    i += 2
                    continue
                if c == "'":
                    if i + 1 < n and query[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            segments.append((query[lit_start:i], True))
            start = i
        else:
            i += 1
    if start < n:
        segments.append((query[start:], False))
    return segments


def _apply_outside_literals(pattern: re.Pattern[str], repl: str, query: str) -> str:
    parts = _split_sql_literals(query)
    return "".join(text if is_lit else pattern.sub(repl, text) for text, is_lit in parts)


class StarRocksWorkloadMixin:
    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()

        enable_primary_keys, enable_foreign_keys = self._get_constraint_configuration()
        self._log_constraint_configuration(enable_primary_keys, enable_foreign_keys)

        try:
            effective_config = self.get_effective_tuning_configuration()
            schema_sql = benchmark.get_create_tables_sql(
                dialect="duckdb",
                tuning_config=effective_config,
            )

            table_tunings = None
            if self.tuning_enabled and effective_config is not None:
                table_tunings = effective_config.table_tunings

            schema_sql_clean = re.sub(r"--[^\n]*\n?", "", schema_sql)
            statements = [stmt.strip() for stmt in schema_sql_clean.split(";") if stmt.strip()]

            cursor = connection.cursor()
            try:
                try:
                    cursor.execute('ADMIN SET FRONTEND CONFIG("tablet_create_timeout_second"="600")')
                except Exception:
                    pass

                for statement in statements:
                    statement = self._optimize_table_definition(statement, table_tunings)
                    cursor.execute(statement)
                    self.logger.debug(f"Executed schema statement: {statement[:100]}...")
            finally:
                cursor.close()

            self.logger.info(f"Schema created (PKs: {enable_primary_keys}, FKs: {enable_foreign_keys})")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise

        return elapsed_seconds(start_time)

    def _optimize_table_definition(self, statement: str, table_tunings: dict[str, Any] | None = None) -> str:
        if not statement.upper().startswith("CREATE TABLE"):
            return statement

        statement = re.sub(r"\bAUTOINCREMENT\b", "", statement, flags=re.IGNORECASE)
        statement = re.sub(r"\bAUTO_INCREMENT\b", "", statement, flags=re.IGNORECASE)

        statement = re.sub(r"\s+ENGINE\s*=\s*\w+(\([^)]*\))?", "", statement, flags=re.IGNORECASE)

        statement = self._convert_types(statement)

        statement = strip_foreign_keys(statement)

        pk_match = re.search(r"\bPRIMARY\s+KEY\s*\(([^)]+)\)", statement, re.IGNORECASE)
        if pk_match:
            pk_cols_raw = pk_match.group(1)
        else:
            pk_inline = re.search(r"^\s+(\w+)\s+\w[\w()]*.*\bPRIMARY\s+KEY\b", statement, re.IGNORECASE | re.MULTILINE)
            pk_cols_raw = pk_inline.group(1) if pk_inline else None

        statement = re.sub(r",?\s*PRIMARY\s+KEY\s*\([^)]+\)", "", statement, flags=re.IGNORECASE)

        statement = re.sub(r"\s+PRIMARY\s+KEY\b", "", statement, flags=re.IGNORECASE)

        first_col = self._extract_first_column(statement)

        current_upper = statement.upper()
        if "DUPLICATE KEY" not in current_upper and "PRIMARY KEY" not in current_upper:
            use_pk = False
            if pk_cols_raw:
                pk_cols = [c.strip().strip("`").strip('"') for c in pk_cols_raw.split(",")]
                col_names = re.findall(r"^\s+(\w+)\s+\w", statement, re.MULTILINE)
                use_pk = len(col_names) > 0 and len(col_names) >= len(pk_cols) and col_names[: len(pk_cols)] == pk_cols

            if use_pk and pk_cols_raw:
                pk_clause = f"PRIMARY KEY ({pk_cols_raw.strip()})"
                if table_tunings is not None:
                    self._record_starrocks_tuning_to_ledger(self._created_table_name(statement), [pk_clause])
                stripped = statement.rstrip()
                if stripped.endswith(";"):
                    statement = stripped[:-1] + f"\n{pk_clause};"
                else:
                    statement = stripped + f"\n{pk_clause}"
            elif first_col:
                stripped = statement.rstrip()
                if stripped.endswith(";"):
                    statement = stripped[:-1] + f"\nDUPLICATE KEY(`{first_col}`);"
                else:
                    statement = stripped + f"\nDUPLICATE KEY(`{first_col}`)"

        current_upper = statement.upper()

        from benchbox.core.tuning.ddl_generator import get_ddl_generator

        generator = get_ddl_generator("starrocks")
        tuning_clauses, tuned_table_name = self._resolve_tuned_ddl_clauses(statement, table_tunings, generator)

        if "DISTRIBUTED BY" not in current_upper and first_col:
            suffix_clauses: list[str] = []
            tuned_clauses: list[str] = []

            if tuning_clauses is not None and tuning_clauses.partition_by and "PARTITION BY" not in current_upper:
                partition_clause = tuning_clauses.partition_by
                suffix_clauses.append(partition_clause)
                tuned_clauses.append(partition_clause)

            dist_tuned = bool(tuning_clauses and tuning_clauses.distribute_by)
            if dist_tuned:
                distribution_clause = tuning_clauses.distribute_by
            else:
                distribution_clause = generator.render_distribution_clause(first_col)
            suffix_clauses.append(distribution_clause)
            if dist_tuned:
                tuned_clauses.append(distribution_clause)

            if tuning_clauses is not None and tuning_clauses.order_by and "ORDER BY" not in current_upper:
                order_clause = tuning_clauses.order_by
                suffix_clauses.append(order_clause)
                tuned_clauses.append(order_clause)

            suffix = "\n".join(suffix_clauses)
            stripped = statement.rstrip()
            if stripped.endswith(";"):
                statement = stripped[:-1] + f"\n{suffix};"
            else:
                statement = stripped + f"\n{suffix}"

            self._record_starrocks_tuning_to_ledger(tuned_table_name, tuned_clauses)

        return statement

    def _resolve_tuned_ddl_clauses(self, statement: str, table_tunings: dict[str, Any] | None, generator: Any):
        if not table_tunings:
            return None, None

        table_name = self._created_table_name(statement)
        if table_name is None:
            return None, None

        table_tuning = None
        for configured_name, configured_tuning in table_tunings.items():
            if str(configured_name).upper() == table_name.upper():
                table_tuning = configured_tuning
                break

        if table_tuning is None or not table_tuning.has_any_tuning():
            return None, table_name

        clauses = generator.generate_tuning_clauses(table_tuning)
        if clauses.is_empty():
            return None, table_name
        return clauses, table_name

    @staticmethod
    def _created_table_name(statement: str) -> str | None:
        match = re.search(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+`?(\w+)`?", statement, re.IGNORECASE)
        return match.group(1) if match else None

    def _record_starrocks_tuning_to_ledger(self, table_name: str | None, clauses: list[str]) -> None:
        ledger = getattr(self, "_applied_tuning_ledger", None)
        if ledger is None or not clauses:
            return
        try:
            from benchbox.core.tuning.applied_ledger import PHASE_DDL

            for clause in clauses:
                ledger.record(clause, PHASE_DDL, mechanism="starrocks_ddl_generator", table=table_name)
        except Exception as exc:
            self.logger.debug("StarRocks applied-ledger record degraded: %s", exc)

    _TYPE_MAPPINGS: tuple[tuple[str, str, bool], ...] = (
        (r"\bHUGEINT\b", "LARGEINT", False),
        (r"\bINTEGER\b", "INT", False),
        (r"\bSMALLINT\b", "INT", False),
        (r"\bTIMESTAMP\b", "DATETIME", True),
        (r"\bTIME\b", "VARCHAR(10)", True),
        (r"\bSTRING\b", "VARCHAR(65533)", False),
        (r"\bTEXT\b", "VARCHAR(65533)", False),
        (r"\bVARCHAR\b(?!\s*\()", "VARCHAR(65533)", False),
        (r"\bBOOLEAN\b", "BOOLEAN", False),
        (r"\bDOUBLE\s+PRECISION\b", "DOUBLE", False),
        (r"\bREAL\b", "FLOAT", False),
        (r"\bBLOB\b", "VARCHAR(65533)", False),
        (r"\bFLOAT\[\d+\]", "ARRAY<FLOAT>", False),
    )

    def _convert_types(self, statement: str) -> str:
        for pattern, replacement, case_sensitive in self._TYPE_MAPPINGS:
            flags = 0 if case_sensitive else re.IGNORECASE
            statement = re.sub(pattern, replacement, statement, flags=flags)
        statement = re.sub(r"(\w+\s+)timestamp\b", r"\1DATETIME", statement)
        statement = re.sub(r"(\w+\s+)time\b", r"\1VARCHAR(10)", statement)
        return statement

    def _extract_first_column(self, statement: str) -> str | None:
        paren_start = statement.find("(")
        if paren_start == -1:
            return None

        content = statement[paren_start + 1 :]
        match = re.match(r"\s*`?(\w+)`?", content)
        if not match:
            return None
        name = match.group(1)
        try:
            validate_sql_identifier(name, "column name")
        except ValueError:
            return None
        return name

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        from benchbox.platforms.base.data_loading import DataLoader

        def starrocks_handler_factory(file_path, adapter, benchmark_instance, table_name=None, data_source=None):
            base_ext = FileFormatRegistry.get_base_data_extension(file_path)

            if base_ext in (".tbl", ".dat", ".csv"):
                dialect_source = data_source or DataSource(source_type="starrocks_handler", tables={})
                dialect = resolve_csv_dialect(
                    dialect_source, table_name or file_path.stem, file_path, benchmark_instance
                )
                null_empty = _stream_load_treats_empty_as_null(dialect.delimiter)
                return StarRocksStreamLoadHandler(
                    delimiter=dialect.delimiter,
                    host=self.host,
                    http_port=self.http_port,
                    database=self.database,
                    username=self.username,
                    password=self.password,
                    null_empty_strings=null_empty,
                    has_header=dialect.has_header,
                )
            elif base_ext == ".parquet":
                return StarRocksParquetHandler()
            return None

        cursor = connection.cursor()
        try:
            cursor.execute("SET query_timeout = 86400")
            cursor.execute("SET SESSION net_read_timeout = 86400")
            cursor.execute("SET SESSION net_write_timeout = 86400")
            cursor.execute("SET SESSION max_allowed_packet = 67108864")
        except Exception:
            pass
        finally:
            cursor.close()

        loader = DataLoader(
            adapter=self,
            benchmark=benchmark,
            connection=connection,
            data_dir=data_dir,
            handler_factory=starrocks_handler_factory,
            tuning_config=self.unified_tuning_configuration if self.tuning_enabled else None,
        )
        table_stats, loading_time = loader.load()
        return table_stats, loading_time, None

    def _get_existing_tables(self, connection) -> list[str]:
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SHOW TABLES")
                tables = [row[0].lower() for row in cursor.fetchall()]
                return tables
            finally:
                cursor.close()
        except Exception as e:
            self.logger.debug(f"Failed to get existing tables: {e}")
            return []

    _RESERVED_ALIAS_WORDS: frozenset[str] = frozenset(
        "character rank order group key value values partition range rows select table column columns index database "
        "schema status type default primary unique current_date current_time current_timestamp interval match natural "
        "dense_rank row_number percent_rank cume_dist ntile lead lag".split()
    )

    _ALIAS_RE = re.compile(r"\bAS\s+(\w+)\b", re.IGNORECASE)

    _ANSI_SUBSTRING_RE = re.compile(
        r"\bSUBSTRING\s*\(\s*([^()]+?)\s+FROM\s+([^()]+?)\s+FOR\s+([^()]+?)\s*\)",
        re.IGNORECASE,
    )
    _ANSI_SUBSTRING_DETECT_RE = re.compile(r"\bSUBSTRING\b[^;]*?\bFROM\b[^;]*?\bFOR\b", re.IGNORECASE)

    def _quote_reserved_aliases(self, query: str) -> str:

        def replacer(m: re.Match) -> str:
            alias = m.group(1)
            if alias.lower() in self._RESERVED_ALIAS_WORDS:
                return f"AS `{alias}`"
            return m.group(0)

        parts = _split_sql_literals(query)
        return "".join(text if is_lit else self._ALIAS_RE.sub(replacer, text) for text, is_lit in parts)

    def _translate_ansi_substring(self, query: str) -> str:
        result = _apply_outside_literals(self._ANSI_SUBSTRING_RE, r"SUBSTRING(\1, \2, \3)", query)
        code_only = "".join(text for text, is_lit in _split_sql_literals(result) if not is_lit)
        if self._ANSI_SUBSTRING_DETECT_RE.search(code_only):
            logger.warning(
                "StarRocks SUBSTRING translator left an ANSI 'FROM … FOR' form in the "
                "query; StarRocks will reject it. Query fragment: %s...",
                result[:200].replace("\n", " "),
            )
        return result

    _SQL_KEYWORDS = frozenset(
        "WHERE HAVING GROUP ORDER LIMIT UNION EXCEPT INTERSECT JOIN INNER LEFT RIGHT FULL CROSS ON AND OR NOT THEN "
        "ELSE END CASE WHEN SELECT FROM AS IS IN NULL TRUE FALSE BETWEEN LIKE ILIKE SIMILAR".split()
    )

    def _inject_missing_subquery_aliases(self, query: str) -> str:  # noqa: C901
        out: list[str] = []
        i = 0
        n = len(query)
        counter = 0

        def skip_quoted(pos: int) -> int:
            q = query[pos]
            pos += 1
            while pos < n:
                if query[pos] == "\\" and q != "`":
                    pos += 2
                    continue
                if query[pos] == q:
                    return pos + 1
                pos += 1
            return pos

        def consume_subquery_and_alias(open_paren_pos: int) -> int:
            nonlocal counter
            out.append("(")
            pos = open_paren_pos + 1
            depth = 1
            while pos < n and depth > 0:
                c = query[pos]
                if c in ("'", '"', "`"):
                    j = skip_quoted(pos)
                    out.append(query[pos:j])
                    pos = j
                    continue
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        out.append(")")
                        pos += 1
                        break
                out.append(c)
                pos += 1

            j = pos
            while j < n and query[j] in " \t\n\r":
                j += 1
            after_sub = query[j:]

            if re.match(r"^AS\s+", after_sub, re.IGNORECASE):
                return pos

            m_id = re.match(r"^([A-Za-z_`]\w*)", after_sub)
            if m_id:
                word = m_id.group(1).strip("`").upper()
                if word not in self._SQL_KEYWORDS:
                    return pos

            inner_start = open_paren_pos + 1
            inner_stripped = query[inner_start:].lstrip()
            first_kw = re.match(r"^(SELECT|WITH)\b", inner_stripped, re.IGNORECASE)
            if first_kw:
                out.append(f" AS `_sq{counter}`")
                counter += 1

            return pos

        while i < n:
            ch = query[i]

            if ch in ("'", '"', "`"):
                j = skip_quoted(i)
                out.append(query[i:j])
                i = j
                continue

            if ch in ("F", "f") and query[i : i + 4].upper() == "FROM":
                before_ok = i == 0 or not (query[i - 1].isalnum() or query[i - 1] == "_")
                j = i + 4
                after_ok = j >= n or not (query[j].isalnum() or query[j] == "_")
                if before_ok and after_ok:
                    ws_end = j
                    while ws_end < n and query[ws_end] in " \t\n\r":
                        ws_end += 1
                    if ws_end < n and query[ws_end] == "(":
                        out.append(query[i:ws_end])
                        i = consume_subquery_and_alias(ws_end)
                        continue

            if ch == ",":
                out.append(",")
                i += 1
                ws_end = i
                while ws_end < n and query[ws_end] in " \t\n\r":
                    ws_end += 1
                if ws_end < n and query[ws_end] == "(":
                    out.append(query[i:ws_end])
                    i = consume_subquery_and_alias(ws_end)
                continue

            out.append(ch)
            i += 1

        return "".join(out)

    _ANSI_IDENTIFIER_RE = re.compile(r'"([a-zA-Z_][a-zA-Z0-9_]*)"')

    def _translate_ansi_identifiers(self, query: str) -> str:
        return _apply_outside_literals(self._ANSI_IDENTIFIER_RE, r"`\1`", query)

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        query = self._quote_reserved_aliases(query)
        query = self._translate_ansi_substring(query)
        query = self._translate_ansi_identifiers(query)
        query = self._inject_missing_subquery_aliases(query)
        result = execute_sql_query(
            connection,
            query,
            query_id,
            log_verbose=self.log_verbose,
            build_query_result_with_validation=self._build_query_result_with_validation,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )
        result.setdefault("translated_query", None)
        return result


class StarRocksStreamLoadHandler(FileFormatHandler):
    def __init__(
        self,
        delimiter: str,
        host: str,
        http_port: int,
        database: str,
        username: str,
        password: str,
        *,
        null_empty_strings: bool = True,
        has_header: bool = False,
    ) -> None:
        self.delimiter = delimiter
        self.host = host
        self.http_port = http_port
        self.database = database
        self.username = username
        self.password = password
        self.null_empty_strings = null_empty_strings
        self.has_header = has_header

    def get_delimiter(self) -> str:
        return self.delimiter

    def load_table(
        self,
        table_name: str,
        file_path: Path,
        connection: Any,
        benchmark: Any,
        logger: Any,
    ) -> int:
        import base64
        import json as _json
        import time as _time

        import requests as _requests

        validate_sql_identifier(table_name, "table name")

        url = f"http://{self.host}:{self.http_port}/api/{self.database}/{table_name}/_stream_load"
        creds = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        headers = {
            "Authorization": f"Basic {creds}",
            "Expect": "100-continue",
            "column_separator": self.delimiter,
            "format": "CSV",
            "max_filter_ratio": "0",
            "strict_mode": "false",
        }
        if self.null_empty_strings:
            headers["null_format"] = "\\N"

        compression_handler = FileFormatRegistry.get_compression_handler(file_path)
        null_markers = frozenset(("nan", "inf", "-inf", "+inf"))

        def _data_stream():
            with compression_handler.open(file_path) as f:
                reader = csv.reader(f, delimiter=self.delimiter)
                if self.has_header:
                    next(reader, None)
                for row in reader:
                    if self.null_empty_strings:
                        row = ["\\N" if (cell == "" or cell.lower() in null_markers) else cell for cell in row]
                    yield (self.delimiter.join(row) + "\n").encode("utf-8")

        max_attempts = 3
        last_err: Exception | None = None
        resp: Any = None
        for attempt in range(1, max_attempts + 1):
            try:
                resp = _requests.put(url, headers=headers, data=_data_stream(), timeout=7200)
                if resp.status_code in (200, 307):
                    try:
                        body = _json.loads(resp.text)
                        msg = body.get("Message", "")
                        if ":0" in msg and attempt < max_attempts:
                            logger.warning(
                                "STREAM LOAD BE unavailable for %s (attempt %d/%d, msg=%r); retrying",
                                table_name,
                                attempt,
                                max_attempts,
                                msg,
                            )
                            _time.sleep(10 * attempt)
                            continue
                    except Exception:
                        pass
                    break
                if 500 <= resp.status_code < 600 and attempt < max_attempts:
                    logger.warning(
                        "STREAM LOAD HTTP %s for %s (attempt %d/%d); retrying",
                        resp.status_code,
                        table_name,
                        attempt,
                        max_attempts,
                    )
                    _time.sleep(5 * attempt)
                    continue
                raise RuntimeError(f"STREAM LOAD HTTP {resp.status_code} for {table_name}: {resp.text[:500]}")
            except _requests.ConnectionError as e:
                last_err = e
                if attempt < max_attempts:
                    logger.warning(
                        "STREAM LOAD connection error for %s (attempt %d/%d): %s; retrying",
                        table_name,
                        attempt,
                        max_attempts,
                        e,
                    )
                    _time.sleep(5 * attempt)
                    continue
                raise RuntimeError(f"STREAM LOAD connection error for {table_name}: {e}") from e
        else:
            raise RuntimeError(f"STREAM LOAD exhausted retries for {table_name}: {last_err}")

        result = _json.loads(resp.text)
        status = result.get("Status", "")
        if status not in ("Success", "Publish Timeout"):
            raise RuntimeError(f"STREAM LOAD failed for {table_name}: {result.get('Message', result)}")

        return int(result.get("NumberLoadedRows", 0))


class StarRocksCSVHandler(FileFormatHandler):
    def __init__(self, delimiter: str, *, null_empty_strings: bool = True, has_header: bool = False):
        self.delimiter = delimiter
        self.null_empty_strings = null_empty_strings
        self.has_header = has_header

    def get_delimiter(self) -> str:
        return self.delimiter

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validate_sql_identifier(table_name, "table name")

        row_count = 0
        batch_size = 9_999
        compression_handler = FileFormatRegistry.get_compression_handler(file_path)
        sql: str | None = None

        cursor = connection.cursor()
        try:
            with compression_handler.open(file_path) as f:
                reader = csv.reader(f, delimiter=self.delimiter)
                if self.has_header:
                    next(reader, None)
                batch: list[list] = []

                for row in reader:
                    if self.null_empty_strings:
                        batch.append(
                            [
                                None if cell == "" or cell.lower() in ("nan", "inf", "-inf", "+inf") else cell
                                for cell in row
                            ]
                        )
                    else:
                        batch.append(list(row))
                    row_count += 1

                    if sql is None:
                        placeholders = ", ".join(["%s"] * len(row))
                        sql = f"INSERT INTO `{table_name}` VALUES ({placeholders})"

                    if len(batch) >= batch_size:
                        cursor.executemany(sql, batch)
                        batch = []

                if batch and sql:
                    cursor.executemany(sql, batch)

        except Exception as e:
            logger.error(f"Failed to load data from {file_path} into {table_name}: {e}")
            raise
        finally:
            cursor.close()

        return row_count


class StarRocksParquetHandler(FileFormatHandler):
    def get_delimiter(self) -> str:
        return ""

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validate_sql_identifier(table_name, "table name")

        try:
            import pyarrow.parquet as pq
        except ImportError as e:
            raise RuntimeError("pyarrow is required to load Parquet files") from e

        pf = pq.ParquetFile(file_path)
        if pf.metadata.num_rows == 0:
            return 0

        schema = pf.schema_arrow
        column_names = schema.names
        validated_cols = [validate_sql_identifier(c, "column name") for c in column_names]
        placeholders = ", ".join(["%s"] * len(validated_cols))
        columns_str = ", ".join(f"`{c}`" for c in validated_cols)
        insert_sql = f"INSERT INTO `{table_name}` ({columns_str}) VALUES ({placeholders})"

        num_cols = len(column_names)
        if num_cols <= 30:
            batch_size = 10_000
        elif num_cols <= 100:
            batch_size = 2_000
        else:
            batch_size = 100

        row_count = 0
        cursor = connection.cursor()
        try:
            for batch in pf.iter_batches(batch_size=batch_size):
                rows = [tuple(row[col] for col in column_names) for row in batch.to_pylist()]
                if rows:
                    cursor.executemany(insert_sql, rows)
                    row_count += len(rows)
        except Exception as e:
            logger.error(f"Failed to load Parquet data from {file_path} into {table_name}: {e}")
            raise
        finally:
            cursor.close()

        return row_count


__all__ = ["StarRocksWorkloadMixin", "StarRocksCSVHandler", "StarRocksParquetHandler"]
