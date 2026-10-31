from __future__ import annotations

import datetime
import json
from collections.abc import Callable, Iterator, Sequence
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.core.exceptions import ConfigurationError

if TYPE_CHECKING:
    from benchbox.platforms.base.data_loading import CsvDialect

_MAX_INSERT_TEXT_CHARS = 500_000
_MAX_INSERT_ROWS = 1000
_PARQUET_READ_ROWS = 1000
_BOOLEAN_TRUE = frozenset({"true", "t", "1"})
_BOOLEAN_FALSE = frozenset({"false", "f", "0"})
_STRUCTURED_BASE_TYPES = frozenset({"array", "map", "row", "json"})


def base_type(type_name: str) -> str:
    return type_name.strip().lower().split("(", 1)[0].strip()


def python_cell_to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    return str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return python_cell_to_text(value)


def _structured_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return _jsonable(value)
    text = value.strip()
    if not text or text.lower() == "null":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def json_cell(value: Any, type_name: str, null_token: str | None) -> Any:
    if value is None:
        return None
    kind = base_type(type_name)
    if kind in _STRUCTURED_BASE_TYPES:
        return _structured_cell(value)
    text = value if isinstance(value, str) else python_cell_to_text(value)
    if kind == "varchar":
        return None if null_token is not None and text == null_token else text
    text = text.strip()
    if text == (null_token or "") or text.lower() == "null":
        return None
    if kind == "boolean":
        lowered = text.lower()
        if lowered in _BOOLEAN_TRUE:
            return "true"
        if lowered in _BOOLEAN_FALSE:
            return "false"
    return text


def _carrier_type(type_name: str) -> str:
    return type_name if base_type(type_name) in _STRUCTURED_BASE_TYPES else "varchar"


def _column_expression(name: str, type_name: str) -> str:
    kind = base_type(type_name)
    if kind in _STRUCTURED_BASE_TYPES or kind == "varchar":
        return name
    if kind == "varbinary":
        return f"from_hex({name})"
    return f"CAST({name} AS {type_name})"


def build_insert_sql(qualified_table: str, column_types: Sequence[str], rows_json: str) -> str:
    names = [f"c{index}" for index in range(len(column_types))]
    carrier = ", ".join(f"{name} {_carrier_type(kind)}" for name, kind in zip(names, column_types))
    projection = ", ".join(_column_expression(name, kind) for name, kind in zip(names, column_types))
    literal = rows_json.replace("'", "''")
    return (
        f"INSERT INTO {qualified_table} SELECT {projection} "
        f"FROM UNNEST(CAST(json_parse('{literal}') AS ARRAY(ROW({carrier})))) AS u({', '.join(names)})"
    )


def fetch_column_types(cursor: Any, qualified_table: str) -> list[str]:
    cursor.execute(f"DESCRIBE {qualified_table}")
    return [str(row[1]) for row in cursor.fetchall()]


def _run_insert(cursor: Any, sql: str) -> None:
    cursor.execute(sql)
    cursor.fetchall()


def _flush_inserts(cursor: Any, qualified_table: str, column_types: Sequence[str], row_jsons: list[str]) -> None:
    budget = _MAX_INSERT_TEXT_CHARS - len(build_insert_sql(qualified_table, column_types, ""))
    chunk: list[str] = []
    size = 0
    for row_json in row_jsons:
        cost = len(row_json) + 1
        if chunk and size + cost > budget:
            _run_insert(cursor, build_insert_sql(qualified_table, column_types, "[" + ",".join(chunk) + "]"))
            chunk = []
            size = 0
        chunk.append(row_json)
        size += cost
    if chunk:
        _run_insert(cursor, build_insert_sql(qualified_table, column_types, "[" + ",".join(chunk) + "]"))


def _iter_delimited_rows(
    file_path: Path, column_count: int, dialect: CsvDialect, delimiter: str
) -> Iterator[list[list[Any]]]:
    from benchbox.platforms.base.data_loading import FileFormatRegistry, RowBatchProcessor

    compression_handler = FileFormatRegistry.get_compression_handler(file_path)
    processor = RowBatchProcessor(batch_size=_MAX_INSERT_ROWS)
    with compression_handler.open(
        file_path, newline="" if dialect.quote else None, encoding=dialect.encoding
    ) as file_handle:
        for batch, _ in processor.process_file(
            file_handle,
            delimiter,
            column_count,
            quote=dialect.quote,
            has_header=dialect.has_header,
        ):
            yield batch


def _iter_parquet_rows(file_path: Path, column_count: int) -> Iterator[list[list[Any]]]:
    import pyarrow.parquet as pq

    parquet_file = pq.ParquetFile(file_path)
    if len(parquet_file.schema_arrow.names) != column_count:
        raise ValueError(
            f"{file_path.name} has {len(parquet_file.schema_arrow.names)} columns; target table has {column_count}"
        )
    for record_batch in parquet_file.iter_batches(batch_size=_PARQUET_READ_ROWS):
        columns = [column.to_pylist() for column in record_batch.columns]
        yield [list(row) for row in zip(*columns)]


def load_file_batches(
    cursor: Any,
    file_path: Path,
    qualified_table: str,
    *,
    dialect: CsvDialect | None = None,
    column_types: Sequence[str] | None = None,
) -> int:
    from benchbox.platforms.base.data_loading import CsvDialect as _CsvDialect
    from benchbox.utils.file_format import get_data_extension, get_delimiter_for_file

    if column_types is None:
        column_types = fetch_column_types(cursor, qualified_table)
    column_count = len(column_types)
    rows_loaded = 0

    if get_data_extension(file_path) == ".parquet":
        batches = _iter_parquet_rows(file_path, column_count)
        null_token: str | None = None
    else:
        if dialect is None:
            is_tpc = get_data_extension(file_path) in (".tbl", ".dat")
            dialect = _CsvDialect(
                delimiter=get_delimiter_for_file(file_path),
                has_header=False,
                null_marker="" if is_tpc else None,
                normalize_booleans=False,
                quote=None,
            )
        batches = _iter_delimited_rows(file_path, column_count, dialect, dialect.delimiter)
        null_token = dialect.null_marker

    for batch in batches:
        row_jsons = [
            json.dumps(
                [json_cell(cell, kind, null_token) for cell, kind in zip(cells, column_types)],
                separators=(",", ":"),
            )
            for cells in batch
        ]
        _flush_inserts(cursor, qualified_table, column_types, row_jsons)
        rows_loaded += len(row_jsons)

    return rows_loaded


def normalize_existing_files(file_paths: Any) -> list[Path]:
    normalized_paths = file_paths if isinstance(file_paths, list) else [file_paths]
    valid_files: list[Path] = []
    for file_path in normalized_paths:
        path = Path(file_path)
        if path.exists() and path.stat().st_size > 0:
            valid_files.append(path)
    return valid_files


def show_tables_lower(connection: Any) -> list[str]:
    cursor = connection.cursor()
    try:
        cursor.execute("SHOW TABLES")
        return [row[0].lower() for row in cursor.fetchall()]
    except Exception:
        return []
    finally:
        cursor.close()


def validate_catalog_exists(
    catalog: str | None,
    *,
    platform_name: str,
    auto_select_catalog: Callable[[], str | None],
    get_available_catalogs: Callable[[], list[str]],
    logger: Logger,
) -> tuple[str, bool]:
    auto_selected = False

    if not catalog:
        catalog = auto_select_catalog()
        if catalog:
            auto_selected = True
            logger.info(f"Auto-selected catalog '{catalog}' for benchmarking")
        else:
            available = get_available_catalogs()
            if available:
                catalog_list = ", ".join(sorted(available))
                raise ConfigurationError(
                    f"No usable data catalogs found on {platform_name} server.\n"
                    f"Available catalogs ({catalog_list}) are system-only and cannot store data.\n"
                    f"Configure a data catalog (hive, iceberg, delta, or memory) on your {platform_name} server,\n"
                    "or specify a catalog explicitly with --platform-option catalog=<name>"
                )
            raise ConfigurationError(
                f"{platform_name} requires a catalog but server is unreachable.\n"
                "Ensure the server is running, then either:\n"
                "  1. Run again (catalog will be auto-selected)\n"
                "  2. Specify explicitly: --platform-option catalog=<name>\n"
                "  3. Configure default: benchbox platforms setup"
            )

    available_catalogs = get_available_catalogs()
    if not available_catalogs:
        logger.debug("Could not query available catalogs - proceeding with specified catalog")
        return catalog, auto_selected

    if catalog in available_catalogs:
        return catalog, auto_selected

    catalog_list = ", ".join(sorted(available_catalogs))
    raise ConfigurationError(
        f"Catalog '{catalog}' does not exist on the {platform_name} server.\n"
        f"Available catalogs: {catalog_list}\n"
        "Specify a valid catalog with --platform-option catalog=<name> (e.g., --platform-option catalog=hive)"
    )


def execute_schema_statements(
    statements: list[str],
    connection: Any,
    logger: Logger,
    extract_table_name: Callable[[str], str | None],
    log_notice: Callable[[str], None],
) -> None:
    cursor = connection.cursor()

    try:
        for statement in statements:
            try:
                cursor.execute(statement)
                logger.debug(f"Executed schema statement: {statement[:100]}...")
            except Exception as e:
                if "already exists" in str(e).lower():
                    table_name = extract_table_name(statement)
                    if table_name:
                        log_notice(f"Dropping existing table before recreate: {table_name}")
                        cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
                        cursor.execute(statement)
                    else:
                        raise
                else:
                    raise

        logger.info("Schema created")

    except Exception as e:
        logger.error(f"Schema creation failed: {e}")
        raise
    finally:
        cursor.close()
