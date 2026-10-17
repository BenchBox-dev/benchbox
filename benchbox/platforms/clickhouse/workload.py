from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.core.tuning.introspection import has_order_by_clause
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.cloud_storage import get_cloud_path_info, is_cloud_path
from benchbox.utils.printing import emit
from benchbox.utils.sql_parsing import find_matching_parenthesis

from .merge_settle import wait_for_merges_to_settle
from .query_transformer import ClickHouseQueryTransformer

if TYPE_CHECKING:
    from .delta_lake import DeltaReader

logger = logging.getLogger(__name__)


def _clickhouse_handler_for(file_path, adapter, benchmark_instance, table_name=None, data_source=None):
    from benchbox.platforms.base.data_loading import (
        ClickHouseNativeHandler,
        DataSource,
        FileFormatRegistry,
        is_delta_table_dir,
        resolve_csv_dialect,
    )

    if is_delta_table_dir(file_path):
        return ClickHouseDeltaHandler(adapter, benchmark_instance)

    base_ext = FileFormatRegistry.get_base_data_extension(file_path)

    if base_ext in (".tbl", ".dat", ".csv"):
        dialect_source = data_source or DataSource(source_type="clickhouse_handler", tables={})
        dialect = resolve_csv_dialect(dialect_source, table_name or file_path.stem, file_path, benchmark_instance)
        return ClickHouseNativeHandler(
            dialect.delimiter,
            adapter,
            benchmark_instance,
            has_header=dialect.has_header,
        )
    elif base_ext == ".parquet":
        return ClickHouseNativeHandler(",", adapter, benchmark_instance)
    return None


class ClickHouseDeltaHandler:
    def __init__(self, adapter: Any, benchmark: Any):
        self.adapter = adapter
        self.benchmark = benchmark

    def get_delimiter(self) -> str:
        return ""

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        from benchbox.platforms.base.data_loading import ClickHouseNativeHandler, validate_sql_identifier

        validated_table = validate_sql_identifier(table_name, "table name")
        reader = self.adapter.delta_reader_for(connection, str(file_path))
        if reader.kind == "native":
            load_query = f"INSERT INTO {validated_table} SELECT * FROM {reader.source_sql}"
            before_result = connection.execute(f"SELECT COUNT(*) FROM {validated_table}")
            before = before_result[0][0] if before_result and before_result[0] else 0
            connection.execute(load_query)
            after_result = connection.execute(f"SELECT COUNT(*) FROM {validated_table}")
            after = after_result[0][0] if after_result and after_result[0] else 0
            return after - before
        import tempfile

        from benchbox.utils.delta_export import export_delta_to_parquet

        with tempfile.TemporaryDirectory(prefix="benchbox_delta_snapshot_") as tmp_dir:
            result = export_delta_to_parquet(file_path, tmp_dir)
            parquet_handler = ClickHouseNativeHandler(",", self.adapter, benchmark or self.benchmark)
            return parquet_handler.load_table_bulk(
                table_name, [Path(p) for p in result.parquet_files], connection, benchmark, logger
            )


class ClickHouseWorkloadMixin:
    def _reset_run_scoped_state(self) -> None:
        super()._reset_run_scoped_state()
        self._merge_settle_result = None
        self._merge_settle_timeout_seconds = 0.0

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()

        enable_primary_keys, enable_foreign_keys = self._get_constraint_configuration()
        self._log_constraint_configuration(enable_primary_keys, enable_foreign_keys)

        try:
            effective_config = self.get_effective_tuning_configuration()
            schema_config = copy.deepcopy(effective_config)
            if schema_config is not None:
                schema_config.primary_keys.enabled = True
            schema_sql = benchmark.get_create_tables_sql(
                dialect="duckdb",
                tuning_config=schema_config,
            )
            nullable_columns_by_table = self._get_nullable_columns_by_table(benchmark)

            table_tunings = None
            if self.tuning_enabled and effective_config is not None:
                table_tunings = effective_config.table_tunings

            primary_keys_enabled = effective_config.primary_keys.enabled if effective_config else True

            statements = [stmt.strip() for stmt in schema_sql.split(";") if stmt.strip()]

            prepared_statements = []
            for statement in statements:
                table_name = self._extract_table_name(statement)
                nullable_columns = nullable_columns_by_table.get(table_name.lower(), set()) if table_name else set()
                optimized = self._optimize_table_definition(
                    statement,
                    table_tunings,
                    nullable_columns=nullable_columns,
                    primary_keys_enabled=primary_keys_enabled,
                )
                prepared_statements.append((statement, table_name, optimized))

            for statement, table_name, optimized in prepared_statements:
                connection.execute(optimized)
                self._record_tuned_sort_key_op(statement, optimized, table_name, table_tunings)
                self._record_primary_key_order_op(statement, optimized, table_name, table_tunings, effective_config)
                self.logger.debug(f"Executed schema statement: {optimized[:100]}...")

            self.logger.info(f"Schema created (PKs: {enable_primary_keys}, FKs: {enable_foreign_keys})")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise

        return elapsed_seconds(start_time)

    def _optimize_table_definition(
        self,
        statement: str,
        table_tunings: dict[str, Any] | None = None,
        *,
        nullable_columns: set[str] | None = None,
        primary_keys_enabled: bool = True,
    ) -> str:
        if not statement.upper().startswith("CREATE TABLE"):
            return statement

        import re

        statement = re.sub(
            r"Nullable\((?:[^()]+|\([^)]*\))+\)\s+NOT\s+NULL",
            lambda m: m.group(0).replace(" NOT NULL", ""),
            statement,
            flags=re.IGNORECASE,
        )

        statement = re.sub(r"\bFLOAT\s*\[\s*\d+\s*\]", "Array(Float32)", statement, flags=re.IGNORECASE)
        statement = re.sub(r"\bDOUBLE\s*\[\s*\d+\s*\]", "Array(Float64)", statement, flags=re.IGNORECASE)

        statement = re.sub(
            r"(?P<prefix>[,(]\s*(?:\"[^\"]+\"|`[^`]+`|[A-Za-z_]\w*)\s+)TIME\b",
            r"\g<prefix>String",
            statement,
            flags=re.IGNORECASE,
        )

        tuned_order_by_applies = "ORDER BY" not in statement.upper()
        schema_primary_key_columns = self._extract_primary_key_columns(statement)
        tuning_clauses = self._resolve_tuned_ddl_clauses(
            statement,
            table_tunings,
            primary_key_columns=(
                schema_primary_key_columns if primary_keys_enabled and tuned_order_by_applies else None
            ),
        )
        if nullable_columns:
            key_columns = self._resolve_key_columns(statement, tuning_clauses, nullable_columns)
            tuned_nullable_keys = self._tuned_nullable_key_columns(tuning_clauses, nullable_columns)
            if tuned_nullable_keys:
                raise ValueError(self._nullable_key_error(statement, tuned_nullable_keys))
            statement = self._apply_nullable_column_types(statement, nullable_columns - key_columns)

        tuned_sort_key_applies = tuned_order_by_applies and tuning_clauses is not None and bool(tuning_clauses.sort_by)
        if tuned_sort_key_applies and schema_primary_key_columns:
            statement = self._strip_primary_key_constraints(statement)

        statement_upper = statement.upper()

        if "ENGINE" not in statement_upper:
            if statement.endswith(";"):
                statement = statement[:-1] + " ENGINE = MergeTree();"
            else:
                statement = statement + " ENGINE = MergeTree()"

        statement_upper = statement.upper()
        if "PARTITION BY" not in statement_upper and tuning_clauses is not None and tuning_clauses.partition_by:
            partition_clause = f" PARTITION BY ({tuning_clauses.partition_by})"
            if statement.endswith(";"):
                statement = statement[:-1] + partition_clause + ";"
            else:
                statement = statement + partition_clause

        statement_upper = statement.upper()
        if "ORDER BY" not in statement_upper:
            if tuning_clauses is not None and tuning_clauses.sort_by:
                order_by_clause = f" ORDER BY ({tuning_clauses.sort_by})"
                if tuning_clauses.primary_key:
                    order_by_clause += f" PRIMARY KEY ({tuning_clauses.primary_key})"
            else:
                pk_columns = self._extract_primary_key_columns(statement)
                if pk_columns:
                    order_by_clause = f" ORDER BY ({', '.join(pk_columns)})"
                else:
                    order_by_clause = " ORDER BY tuple()"

            if statement.endswith(";"):
                statement = statement[:-1] + order_by_clause + ";"
            else:
                statement = statement + order_by_clause

        return statement

    def _record_tuned_sort_key_op(
        self,
        original_statement: str,
        executed_statement: str,
        table_name: str | None,
        table_tunings: dict[str, Any] | None,
    ) -> None:
        try:
            if not (getattr(self, "tuning_enabled", False) and table_tunings):
                return
            if has_order_by_clause(original_statement):
                return
            tuning_clauses = self._resolve_tuned_ddl_clauses(original_statement, table_tunings)
            if tuning_clauses is None:
                return
            tuned_sort = bool(tuning_clauses.sort_by)
            tuned_partition = bool(tuning_clauses.partition_by)
            if not (tuned_sort or tuned_partition):
                return
            ledger = getattr(self, "_applied_tuning_ledger", None)
            if ledger is None:
                self.logger.debug("clickhouse tuned key ledger record skipped: no applied-tuning ledger on adapter")
                return
            from benchbox.core.tuning.applied_ledger import PHASE_DDL

            if tuned_sort and tuned_partition:
                mechanism = "table_keys"
            elif tuned_sort:
                mechanism = "sort_key"
            else:
                mechanism = "partition_key"
            ledger.record(
                executed_statement,
                PHASE_DDL,
                mechanism=mechanism,
                table=table_name,
            )
        except Exception as exc:
            self.logger.debug("clickhouse tuned key ledger record degraded: %s", exc)

    def _record_primary_key_order_op(
        self,
        original_statement: str,
        executed_statement: str,
        table_name: str | None,
        table_tunings: dict[str, Any] | None,
        effective_config: Any,
    ) -> None:
        try:
            if not getattr(self, "tuning_enabled", False) or effective_config is None:
                return
            if not effective_config.primary_keys.enabled or has_order_by_clause(original_statement):
                return
            tuning_clauses = self._resolve_tuned_ddl_clauses(original_statement, table_tunings)
            if tuning_clauses is not None and (tuning_clauses.sort_by or tuning_clauses.partition_by):
                return
            if not self._extract_primary_key_columns(original_statement):
                return
            ledger = getattr(self, "_applied_tuning_ledger", None)
            if ledger is None:
                return
            from benchbox.core.tuning.applied_ledger import PHASE_DDL

            ledger.record(executed_statement, PHASE_DDL, mechanism="primary_key_order_by", table=table_name)
        except Exception as exc:
            self.logger.debug("clickhouse primary-key ledger record degraded: %s", exc)

    @staticmethod
    def _extract_table_name(statement: str) -> str | None:
        import re

        match = re.search(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+([A-Za-z_]\w*)", statement, re.IGNORECASE)
        return match.group(1) if match else None

    @staticmethod
    def _get_nullable_columns_by_table(benchmark: Any) -> dict[str, set[str]]:
        schema_getter = getattr(benchmark, "get_schema", None)
        if not callable(schema_getter):
            return {}
        schema = schema_getter()
        if not isinstance(schema, dict):
            return {}

        nullable_by_table: dict[str, set[str]] = {}
        for table_name, table_schema in schema.items():
            if isinstance(table_schema, dict):
                columns = table_schema.get("columns")
                table_primary_keys = {str(name).lower() for name in table_schema.get("primary_key", [])}
            else:
                columns = getattr(table_schema, "columns", None)
                table_primary_keys = set()

            if isinstance(columns, dict):
                column_items = [(name, spec) for name, spec in columns.items()]
            elif isinstance(columns, (list, tuple)):
                column_items = []
                for column in columns:
                    name = column.get("name") if isinstance(column, dict) else getattr(column, "name", None)
                    column_items.append((name, column))
            else:
                continue

            nullable_columns: set[str] = set()
            for raw_name, column in column_items:
                if not isinstance(raw_name, str):
                    continue
                if isinstance(column, dict):
                    is_nullable = column.get("nullable", True)
                    is_primary_key = column.get("primary_key", False)
                else:
                    is_nullable = getattr(column, "nullable", True)
                    is_primary_key = getattr(column, "primary_key", False)
                column_name = raw_name.lower()
                if bool(is_nullable) and not bool(is_primary_key) and column_name not in table_primary_keys:
                    nullable_columns.add(column_name)

            if nullable_columns:
                nullable_by_table[str(table_name).lower()] = nullable_columns

        return nullable_by_table

    def _resolve_key_columns(self, statement: str, tuning_clauses: Any, nullable_columns: set[str]) -> set[str]:
        key_columns = {name.lower() for name in self._extract_primary_key_columns(statement)}
        key_columns.update(self._tuned_nullable_key_columns(tuning_clauses, nullable_columns))
        return key_columns

    _TUNED_KEY_ROLES: tuple[tuple[str, str], ...] = (
        ("partition_by", "PARTITION BY"),
        ("sort_by", "ORDER BY"),
        ("order_by", "ORDER BY"),
        ("cluster_by", "CLUSTER BY"),
        ("primary_key", "PRIMARY KEY"),
    )

    def _tuned_nullable_key_columns(self, tuning_clauses: Any, nullable_columns: set[str]) -> dict[str, list[str]]:
        import re

        matched: dict[str, list[str]] = {}
        if tuning_clauses is None:
            return matched
        expressions = [(role, getattr(tuning_clauses, attribute, None)) for attribute, role in self._TUNED_KEY_ROLES]
        for column_name in nullable_columns:
            identifier = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(column_name)}(?![A-Za-z0-9_])",
                re.IGNORECASE,
            )
            roles = sorted({role for role, expression in expressions if expression and identifier.search(expression)})
            if roles:
                matched[column_name] = roles
        return matched

    @staticmethod
    def _nullable_key_error(statement: str, tuned_nullable_keys: dict[str, list[str]]) -> str:
        import re

        match = re.search(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)", statement, re.IGNORECASE)
        table = match.group(1) if match else "<unknown table>"
        columns = ", ".join(f"{name} ({'/'.join(roles)})" for name, roles in sorted(tuned_nullable_keys.items()))
        return (
            f"tuned key columns of table {table!r} are nullable in the source schema: {columns}; "
            "BenchBox renders key columns non-Nullable, which would load their NULLs as 0 and "
            "silently change query answers. Use NOT NULL key columns (as in the curated ClickHouse "
            "TPC-DS template) or declare the columns non-nullable."
        )

    @classmethod
    def _apply_nullable_column_types(cls, statement: str, nullable_columns: set[str]) -> str:
        import re

        if not nullable_columns:
            return statement

        create_match = re.search(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+[A-Za-z_]\w*\s*\(", statement, re.IGNORECASE)
        if create_match is None:
            return statement
        open_index = statement.find("(", create_match.start())
        close_index = find_matching_parenthesis(statement, open_index)
        body = statement[open_index + 1 : close_index]
        edits: list[tuple[int, int, str]] = []

        for start, end in cls._top_level_column_spans(body):
            segment = body[start:end]
            name_match = re.match(r'^\s*(?:"(?P<quoted>[^"]+)"|(?P<plain>[A-Za-z_]\w*))\s+', segment)
            if name_match is None:
                continue
            column_name = (name_match.group("quoted") or name_match.group("plain")).lower()
            if column_name not in nullable_columns:
                continue

            type_start = name_match.end()
            type_end = cls._column_type_end(segment, type_start)
            data_type = segment[type_start:type_end].rstrip()
            if not data_type or data_type.upper().startswith("NULLABLE("):
                continue
            if data_type.upper().startswith("ARRAY("):
                continue
            suffix = segment[type_end:]
            if re.search(r"\bNOT\s+NULL\b", suffix, re.IGNORECASE):
                raise ValueError(f"source-nullable column {column_name!r} is declared NOT NULL")
            replacement_end = type_start + len(data_type)
            edits.append((start + type_start, start + replacement_end, f"Nullable({data_type})"))

        for start, end, replacement in reversed(edits):
            body = body[:start] + replacement + body[end:]
        return statement[: open_index + 1] + body + statement[close_index:]

    @staticmethod
    def _top_level_column_spans(body: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        start = 0
        depth = 0
        quote: str | None = None
        index = 0
        while index < len(body):
            char = body[index]
            if quote is not None:
                if char == quote:
                    if index + 1 < len(body) and body[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
            elif char in {"'", '"', "`"}:
                quote = char
            elif char == "-" and body[index + 1 : index + 2] == "-":
                end = body.find("\n", index)
                index = len(body) if end == -1 else end
                continue
            elif char == "/" and body[index + 1 : index + 2] == "*":
                end = body.find("*/", index + 2)
                index = len(body) if end == -1 else end + 2
                continue
            elif char in "([":
                depth += 1
            elif char in ")]":
                depth -= 1
            elif char == "," and depth == 0:
                spans.append((start, index))
                start = index + 1
            index += 1
        spans.append((start, len(body)))
        return spans

    @staticmethod
    def _blank_quoted_and_commented(text: str) -> str:
        chars = list(text)
        index = 0
        quote: str | None = None
        while index < len(chars):
            char = chars[index]
            if quote is not None:
                if chars[index] != "\n":
                    chars[index] = " "
                if char == quote:
                    if index + 1 < len(chars) and chars[index + 1] == quote:
                        if chars[index + 1] != "\n":
                            chars[index + 1] = " "
                        index += 2
                        continue
                    quote = None
            elif char in {"'", '"', "`"}:
                chars[index] = " "
                quote = char
            elif char == "-" and index + 1 < len(chars) and chars[index + 1] == "-":
                while index < len(chars) and chars[index] != "\n":
                    chars[index] = " "
                    index += 1
                continue
            elif char == "/" and index + 1 < len(chars) and chars[index + 1] == "*":
                chars[index] = " "
                chars[index + 1] = " "
                index += 2
                while index < len(chars):
                    if chars[index] == "*" and index + 1 < len(chars) and chars[index + 1] == "/":
                        chars[index] = " "
                        chars[index + 1] = " "
                        index += 2
                        break
                    if chars[index] != "\n":
                        chars[index] = " "
                    index += 1
                continue
            index += 1
        return "".join(chars)

    @staticmethod
    def _strip_inline_primary_key(segment: str) -> str:
        import re

        while True:
            probe = ClickHouseWorkloadMixin._blank_quoted_and_commented(segment)
            match = re.search(r"PRIMARY\s+KEY\b", probe, flags=re.IGNORECASE)
            if match is None:
                return segment
            start = match.start()
            while start > 0 and segment[start - 1].isspace():
                start -= 1
            segment = segment[:start] + segment[match.end() :]

    @staticmethod
    def _column_type_end(segment: str, type_start: int) -> int:
        import re

        constraint = re.compile(
            r"(?:NOT\s+NULL|NULL|PRIMARY\s+KEY|REFERENCES|DEFAULT|UNIQUE|CHECK|COLLATE|GENERATED|CONSTRAINT)\b",
            re.IGNORECASE,
        )
        depth = 0
        quote: str | None = None
        index = type_start
        while index < len(segment):
            char = segment[index]
            if quote is not None:
                if char == quote:
                    if index + 1 < len(segment) and segment[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
            elif char in {"'", '"'}:
                quote = char
            elif char in "([":
                depth += 1
            elif char in ")]":
                depth -= 1
            elif char.isspace() and depth == 0:
                next_token = segment[index:].lstrip()
                if constraint.match(next_token):
                    return index
            index += 1
        return len(segment.rstrip())

    @classmethod
    def _strip_primary_key_constraints(cls, statement: str) -> str:
        import re

        table_name = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_]\w*)'
        create_match = re.search(rf"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+{table_name}\s*\(", statement, re.IGNORECASE)
        if create_match is None:
            return statement
        open_index = statement.find("(", create_match.start())
        close_index = find_matching_parenthesis(statement, open_index)
        body = statement[open_index + 1 : close_index]

        table_constraint = re.compile(r"^\s*(?:CONSTRAINT\s+[\s\S]+?\s+)?PRIMARY\s+KEY\s*\(", re.IGNORECASE)
        kept_segments: list[str] = []
        dropped_last_segment = False
        spans = cls._top_level_column_spans(body)
        for position, (start, end) in enumerate(spans):
            segment = body[start:end]
            probe = cls._blank_quoted_and_commented(segment)
            if table_constraint.match(probe):
                dropped_last_segment = position == len(spans) - 1
                continue
            kept_segments.append(cls._strip_inline_primary_key(segment))

        rebuilt = ",".join(kept_segments)
        if dropped_last_segment:
            rebuilt = rebuilt.rstrip() + body[len(body.rstrip()) :]
        return statement[: open_index + 1] + rebuilt + statement[close_index:]

    def _resolve_tuned_ddl_clauses(
        self,
        statement: str,
        table_tunings: dict[str, Any] | None,
        primary_key_columns: list[str] | None = None,
    ):
        if not table_tunings:
            return None

        import re

        match = re.search(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)", statement, re.IGNORECASE)
        if not match:
            return None
        table_name = match.group(1)

        table_tuning = None
        for configured_name, configured_tuning in table_tunings.items():
            if str(configured_name).upper() == table_name.upper():
                table_tuning = configured_tuning
                break

        if table_tuning is None or not table_tuning.has_any_tuning():
            return None

        from benchbox.core.tuning.generators.clickhouse import ClickHouseDDLGenerator

        generator = ClickHouseDDLGenerator()
        clauses = generator.generate_tuning_clauses(table_tuning, primary_key_columns=primary_key_columns or None)
        if clauses.is_empty():
            return None
        self._normalize_tuning_clause_identifiers(statement, clauses)
        return clauses

    @classmethod
    def _normalize_tuning_clause_identifiers(cls, statement: str, tuning_clauses: Any) -> None:
        import re

        column_names = cls._ddl_column_name_map(statement)
        if not column_names:
            return

        identifier = re.compile(r"[A-Za-z_]\w*")
        for attribute in ("partition_by", "sort_by", "order_by", "cluster_by", "primary_key"):
            expression = getattr(tuning_clauses, attribute, None)
            if expression:
                setattr(
                    tuning_clauses,
                    attribute,
                    identifier.sub(lambda match: column_names.get(match.group(0).lower(), match.group(0)), expression),
                )

    @classmethod
    def _ddl_column_name_map(cls, statement: str) -> dict[str, str]:
        import re

        create_match = re.search(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+[A-Za-z_]\w*\s*\(", statement, re.IGNORECASE)
        if create_match is None:
            return {}
        open_index = statement.find("(", create_match.start())
        close_index = find_matching_parenthesis(statement, open_index)
        body = statement[open_index + 1 : close_index]
        names: dict[str, str] = {}
        for start, end in cls._top_level_column_spans(body):
            segment = body[start:end]
            match = re.match(r'^\s*(?:"(?P<quoted>[^"]+)"|(?P<plain>[A-Za-z_]\w*))\s+', segment)
            if match is None:
                continue
            name = match.group("quoted") or match.group("plain")
            if name.upper() not in {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}:
                names[name.lower()] = name
        return names

    def _extract_primary_key_columns(self, statement: str) -> list[str]:
        import re

        pk_columns = []

        inline_pk_pattern = r"(\w+)\s+\w+(?:\((?:[^()]+|\([^)]*\))*\))?\s+PRIMARY\s+KEY"
        inline_matches = re.findall(inline_pk_pattern, statement, re.IGNORECASE)
        pk_columns.extend(inline_matches)

        composite_pk_pattern = r"PRIMARY\s+KEY\s*\(\s*([^)]+)\s*\)"
        composite_matches = re.findall(composite_pk_pattern, statement, re.IGNORECASE)
        for match in composite_matches:
            cols = [col.strip() for col in match.split(",")]
            pk_columns.extend(cols)

        return pk_columns

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        from benchbox.platforms.base.data_loading import DataLoader

        if is_cloud_path(str(data_dir)):
            path_info = get_cloud_path_info(str(data_dir))
            self.log_verbose(f"Loading data from cloud storage: {path_info['provider']} bucket '{path_info['bucket']}'")
            emit(f"  Loading data from {path_info['provider']} cloud storage")

        def clickhouse_handler_factory(file_path, adapter, benchmark_instance, table_name=None, data_source=None):
            return _clickhouse_handler_for(file_path, adapter, benchmark_instance, table_name, data_source)

        loader = DataLoader(
            adapter=self,
            benchmark=benchmark,
            connection=connection,
            data_dir=data_dir,
            handler_factory=clickhouse_handler_factory,
            tuning_config=self.unified_tuning_configuration if self.tuning_enabled else None,
        )
        self._merge_settle_result = None
        self._merge_settle_timeout_seconds = 0.0
        table_stats, loading_time = loader.load()
        if self.deployment_mode == "server":
            self._settle_background_merges(connection)
        return table_stats, loading_time, None

    def build_merge_settle_phase(self) -> Any:
        from benchbox.core.results.models import MergeSettlePhase

        settled = getattr(self, "_merge_settle_result", None)
        if settled is None:
            return None
        return MergeSettlePhase(
            settled=settled.settled,
            waited_seconds=settled.waited_seconds,
            active_parts=settled.active_parts,
            timeout_seconds=getattr(self, "_merge_settle_timeout_seconds", 0.0),
        )

    def _settle_background_merges(self, connection: Any) -> None:
        from benchbox.platforms.clickhouse.merge_settle import MERGE_SETTLE_TIMEOUT_SECONDS, MergeSettleResult

        started = mono_time()
        try:
            result = wait_for_merges_to_settle(connection)
        except Exception as exc:
            logger.warning("Could not check ClickHouse background merges after load: %s", exc)
            self._merge_settle_result = MergeSettleResult(False, elapsed_seconds(started), None)
            self._merge_settle_timeout_seconds = MERGE_SETTLE_TIMEOUT_SECONDS
            return
        self._merge_settle_result = result
        self._merge_settle_timeout_seconds = MERGE_SETTLE_TIMEOUT_SECONDS
        if not result.settled:
            logger.warning(
                "ClickHouse background merges had not settled after %.0fs (%d active parts); "
                "query timings may include merge activity",
                result.waited_seconds,
                result.active_parts,
            )
            return
        if result.waited_seconds >= 1.0:
            emit(f"  Background merges settled after {result.waited_seconds:.1f}s ({result.active_parts} active parts)")

    def _get_existing_tables(self, connection) -> list[str]:
        try:
            if self.deployment_mode == "local":
                result = connection.execute("SHOW TABLES")
                if result:
                    tables = []
                    for row in result:
                        if isinstance(row, (list, tuple)):
                            tables.append(str(row[0]).lower())
                        else:
                            tables.append(str(row).lower())
                    return tables
                return []
            else:
                result = connection.execute("SELECT name FROM system.tables WHERE database = currentDatabase()")
                return [row[0].lower() for row in result] if result else []
        except Exception as e:
            self.logger.debug(f"Failed to get existing tables: {e}")
            return []

    def get_table_row_count(self, connection: Any, table: str) -> int:
        try:
            result = connection.execute(f"SELECT COUNT(*) FROM {table}")
            if result:
                row = result[0]
                return int(row[0])
            return 0
        except Exception as e:
            self.logger.debug(f"Failed to get row count for {table}: {e}")
            return 0

    def delta_native_registration(self, connection: Any) -> bool:
        from .delta_lake import delta_engine_probe_sql, delta_function_probe_sql, has_native_delta_registration

        function_rows = connection.execute(delta_function_probe_sql()) or []
        engine_rows = connection.execute(delta_engine_probe_sql()) or []
        return has_native_delta_registration(
            [str(row[0]) for row in function_rows],
            [str(row[0]) for row in engine_rows],
        )

    def delta_reader_for(self, connection: Any, location: str) -> DeltaReader:
        from .delta_lake import (
            delta_engine_probe_sql,
            delta_function_probe_sql,
            has_local_delta_registration,
            has_native_delta_registration,
            resolve_delta_reader,
        )

        function_rows = connection.execute(delta_function_probe_sql()) or []
        engine_rows = connection.execute(delta_engine_probe_sql()) or []
        functions = [str(row[0]) for row in function_rows]
        engines = [str(row[0]) for row in engine_rows]
        return resolve_delta_reader(
            location,
            native_available=has_native_delta_registration(functions, engines),
            local_native_available=has_local_delta_registration(functions),
        )

    def _get_constraint_configuration(self) -> tuple[bool, bool]:
        effective_config = self.get_effective_tuning_configuration()

        enable_primary_keys = True

        enable_foreign_keys = effective_config.foreign_keys.enabled if effective_config else False

        return enable_primary_keys, enable_foreign_keys

    def _validate_data_integrity(
        self,
        benchmark: Any,
        connection: Any,
        table_stats: dict[str, int],
    ) -> tuple[str, dict[str, Any]]:
        validation_details: dict[str, Any] = {}

        try:
            execute_fn = getattr(connection, "execute", None)
            if not callable(execute_fn):
                raise TypeError("ClickHouse connection must expose an execute() method")

            accessible_tables = []
            inaccessible_tables = []

            for table_name in table_stats:
                try:
                    execute_fn(f"SELECT 1 FROM {table_name} LIMIT 1")
                    accessible_tables.append(table_name)
                except Exception as e:
                    self.logger.debug(f"Table {table_name} inaccessible: {e}")
                    inaccessible_tables.append(table_name)

            if inaccessible_tables:
                validation_details["inaccessible_tables"] = inaccessible_tables
                validation_details["constraints_enabled"] = False
                return "FAILED", validation_details
            else:
                validation_details["accessible_tables"] = accessible_tables
                validation_details["constraints_enabled"] = True
                return "PASSED", validation_details

        except Exception as e:
            validation_details["constraints_enabled"] = False
            validation_details["integrity_error"] = str(e)
            return "FAILED", validation_details

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
        start_time = mono_time()

        try:
            transformer = ClickHouseQueryTransformer(verbose=self.very_verbose)
            transformed_query = transformer.transform(query)
            additional_settings: tuple[tuple[str, str | int], ...] = ()
            if (
                self.deployment_mode == "local"
                and (benchmark_type or "").lower() == "tpchavoc"
                and str(query_id).lower() in {"5_v7", "5_v8"}
            ):
                additional_settings = (
                    ("join_algorithm", "grace_hash"),
                    ("grace_hash_join_initial_buckets", 8),
                )
            elif (
                self.deployment_mode == "server"
                and getattr(self, "tuning_enabled", False)
                and (benchmark_type or "").lower() == "tpch"
                and str(query_id).lower().removeprefix("q") == "21"
            ):
                additional_settings = (("join_algorithm", "hash"),)
            transformed_query = transformer.add_query_settings(transformed_query, additional_settings)

            if transformer.get_transformations_applied() and self.verbose_enabled:
                self.log_verbose(
                    f"Query {query_id}: Applied transformations: {', '.join(transformer.get_transformations_applied())}"
                )

            result = connection.execute(transformed_query)

            execution_time = elapsed_seconds(start_time)
            actual_row_count = len(result) if result else 0

            validation_result = None
            if validate_row_count and benchmark_type:
                from benchbox.core.validation.query_validation import QueryValidator

                validator = QueryValidator()
                validation_result = validator.validate_query_result(
                    benchmark_type=benchmark_type,
                    query_id=query_id,
                    actual_row_count=actual_row_count,
                    scale_factor=scale_factor,
                    stream_id=stream_id,
                )

                if validation_result.warning_message:
                    self.log_verbose(f"Row count validation: {validation_result.warning_message}")
                elif not validation_result.is_valid:
                    self.log_verbose(f"Row count validation FAILED: {validation_result.error_message}")
                else:
                    self.log_very_verbose(
                        f"Row count validation PASSED: {actual_row_count} rows "
                        f"(expected: {validation_result.expected_row_count})"
                    )

                if not validation_result.is_valid:
                    return {
                        "query_id": query_id,
                        "status": "FAILED",
                        "execution_time_seconds": execution_time,
                        "rows_returned": actual_row_count,
                        "row_count_validation": {
                            "expected": validation_result.expected_row_count,
                            "actual": actual_row_count,
                            "status": "FAILED",
                            "error": validation_result.error_message,
                        },
                        "error": validation_result.error_message,
                        "first_row": result[0] if result else None,
                        "translated_query": None,
                    }

            result_dict = {
                "query_id": query_id,
                "status": "SUCCESS",
                "execution_time_seconds": execution_time,
                "rows_returned": actual_row_count,
                "first_row": result[0] if result else None,
                "translated_query": None,
            }

            if validation_result:
                row_count_validation = {
                    "expected": validation_result.expected_row_count,
                    "actual": actual_row_count,
                    "status": "PASSED" if validation_result.is_valid else "SKIPPED",
                }
                if validation_result.warning_message:
                    row_count_validation["warning"] = validation_result.warning_message
                result_dict["row_count_validation"] = row_count_validation

            from benchbox.platforms.base.result_capture import apply_materialized_result_validation

            apply_materialized_result_validation(result_dict, query_id, result)

        except Exception as e:
            execution_time = elapsed_seconds(start_time)
            error_type = type(e).__name__
            error_message = str(e) or repr(e) or error_type

            return {
                "query_id": query_id,
                "status": "FAILED",
                "execution_time_seconds": execution_time,
                "rows_returned": 0,
                "error": error_message,
                "error_type": error_type,
            }

        self._merge_plan_capture_into_result(result_dict, connection, transformed_query, query_id)

        return result_dict


__all__ = ["ClickHouseWorkloadMixin"]
