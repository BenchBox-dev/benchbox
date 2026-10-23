from __future__ import annotations

import re
from collections.abc import Callable
from logging import Logger
from pathlib import Path
from typing import Any

from benchbox.core.exceptions import ConfigurationError

_DATE_LITERAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_date_value(value: str) -> bool:
    return bool(_DATE_LITERAL_RE.match(value))


def escape_insert_value(value: str) -> str:
    if value == "" or value.lower() == "null":
        return "NULL"
    if is_date_value(value):
        return f"DATE '{value}'"
    try:
        float(value)
        return value
    except ValueError:
        return "'" + str(value).replace("'", "''") + "'"


def load_file_batches(cursor: Any, file_path: Path, qualified_table: str, *, delimiter: str | None = None) -> int:
    from benchbox.platforms.base.data_loading import FileFormatRegistry
    from benchbox.utils.file_format import get_delimiter_for_file

    if delimiter is None:
        delimiter = get_delimiter_for_file(file_path)
    compression_handler = FileFormatRegistry.get_compression_handler(file_path)
    rows_loaded = 0

    with compression_handler.open(file_path) as file_handle:
        batch_size = 500
        batch_data: list[str] = []

        for line in file_handle:
            line = line.strip()
            if line and line.endswith(delimiter):
                line = line[:-1]
            if not line:
                continue

            escaped_values = [escape_insert_value(value) for value in line.split(delimiter)]
            batch_data.append(f"({', '.join(escaped_values)})")

            if len(batch_data) >= batch_size:
                cursor.execute(f"INSERT INTO {qualified_table} VALUES " + ", ".join(batch_data))
                rows_loaded += len(batch_data)
                batch_data = []

        if batch_data:
            cursor.execute(f"INSERT INTO {qualified_table} VALUES " + ", ".join(batch_data))
            rows_loaded += len(batch_data)

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
