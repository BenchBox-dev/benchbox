# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Any, Optional, Union

from benchbox.utils.clock import elapsed_seconds, mono_time

_NO_NULL_SENTINEL = "<<benchbox-no-null>>"


def duckdb_insert_from_csv(
    connection: Any,
    table_name: str,
    file_path: Any,
    num_columns: int,
    *,
    delimiter: str = "|",
    skip_header: bool = False,
    null_if_empty: bool | list[bool] = False,
    trailing_delimiter: bool = False,
    skip_malformed: bool = True,
) -> int | None:
    try:
        import duckdb
    except ImportError:
        return None
    if not isinstance(connection, duckdb.DuckDBPyConnection):
        return None

    flags = null_if_empty if isinstance(null_if_empty, list) else [null_if_empty] * num_columns
    read_columns = num_columns + 1 if trailing_delimiter else num_columns
    columns = ", ".join(f"'c{i}': 'VARCHAR'" for i in range(read_columns))
    select = ", ".join(f"NULLIF(c{i}, '')" if flags[i] else f"c{i}" for i in range(num_columns))
    path = str(file_path).replace("'", "''")
    delim = delimiter.replace("'", "''")
    sql = (
        f"INSERT INTO {table_name} SELECT {select} FROM read_csv('{path}', delim='{delim}', "
        f"header={str(skip_header).lower()}, columns={{{columns}}}, auto_detect=false, "
        f"nullstr='{_NO_NULL_SENTINEL}', quote='\"', escape='\"', "
        f"null_padding={str(trailing_delimiter).lower()}, ignore_errors={str(skip_malformed).lower()})"
    )
    row = connection.execute(sql).fetchone()
    return int(row[0]) if row else 0


class DataGenerationMixin:
    tables: dict[str, Any]
    data_generator: Any

    def _get_table_schema(self) -> dict[str, Any]:
        raise NotImplementedError

    def _get_data_loading_batch_size(self) -> int | None:
        return None

    def generate_data(self, tables: Optional[list[str]] = None, output_format: str = "csv") -> dict[str, Any]:
        if output_format != "csv":
            raise ValueError(f"Unsupported output format: {output_format}")

        table_schema = self._get_table_schema()

        if tables is None:
            tables = list(table_schema.keys())

        invalid_tables = set(tables) - set(table_schema.keys())
        if invalid_tables:
            raise ValueError(f"Invalid table names: {invalid_tables}")

        self.tables = self.data_generator.generate_data(tables)
        return self.tables

    def load_data_to_database(self, connection: Any, tables: Optional[list[str]] = None) -> None:
        if not self.tables:
            raise ValueError("No data generated. Call generate_data() first.")

        if tables is None:
            tables = list(self.tables.keys())

        self._execute_schema_sql(connection)

        table_schema = self._get_table_schema()
        batch_size = self._get_data_loading_batch_size()

        for table_name in tables:
            if table_name not in self.tables or table_name not in table_schema:
                continue
            self._load_single_table(connection, table_name, table_schema[table_name], batch_size)

        if hasattr(connection, "commit"):
            connection.commit()

    def _execute_schema_sql(self, connection: Any) -> None:
        schema_sql = self.get_create_tables_sql()
        if hasattr(connection, "executescript"):
            connection.executescript(schema_sql)
        else:
            cursor = connection.cursor()
            for statement in schema_sql.split(";"):
                if statement.strip():
                    cursor.execute(statement)

    def _load_single_table(self, connection: Any, table_name: str, table_def: dict, batch_size: int | None) -> None:
        import csv

        file_path = self.tables[table_name]

        if (
            duckdb_insert_from_csv(connection, table_name, file_path, len(table_def["columns"]), skip_malformed=False)
            is not None
        ):
            return

        with open(file_path, encoding="utf-8") as handle:
            reader = csv.reader(handle, delimiter="|")

            columns = [col["name"] for col in table_def["columns"]]
            placeholders = ",".join(["?" for _ in columns])
            insert_sql = f"INSERT INTO {table_name} VALUES ({placeholders})"

            if hasattr(connection, "executemany"):
                if batch_size:
                    batch = []
                    for row in reader:
                        batch.append(row)
                        if len(batch) >= batch_size:
                            connection.executemany(insert_sql, batch)
                            batch = []
                    if batch:
                        connection.executemany(insert_sql, batch)
                else:
                    connection.executemany(insert_sql, list(reader))
            else:
                cursor = connection.cursor()
                for row in reader:
                    cursor.execute(insert_sql, row)


class QueryCategoryFacadeMixin:
    _impl: Any

    def get_queries_by_category(self, category: str) -> dict[str, str]:
        return self._impl.get_queries_by_category(category)

    def get_query_categories(self) -> list[str]:
        return self._impl.get_query_categories()


class OperationCategoryFacadeMixin:
    _impl: Any

    def get_operations_by_category(self, category: str) -> dict[str, Any]:
        return self._impl.get_operations_by_category(category)

    def get_operation_categories(self) -> list[str]:
        return self._impl.get_operation_categories()


class QueryFacadeMixin:
    _impl: Any

    def get_queries(self, dialect: Optional[str] = None) -> dict[str, str]:
        return self._impl.get_queries(dialect=dialect)

    def get_query(
        self,
        query_id: Union[int, str],
        *,
        params: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> str:
        if params is None:
            return self._impl.get_query(query_id, **kwargs)
        return self._impl.get_query(query_id, params=params, **kwargs)


class CursorValidationQueryExecutionMixin:
    _REQUIRED_METHODS = ("log_verbose", "log_very_verbose", "_build_query_result_with_validation")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__name__.endswith(("Mixin", "Base")):
            return
        for method_name in CursorValidationQueryExecutionMixin._REQUIRED_METHODS:
            providers = [
                c
                for c in cls.__mro__
                if c not in (cls, CursorValidationQueryExecutionMixin, object) and method_name in c.__dict__
            ]
            if not providers:
                raise TypeError(
                    f"{cls.__name__} uses CursorValidationQueryExecutionMixin but does not "
                    f"inherit a concrete implementation of {method_name}(). "
                    f"Ensure a parent class (e.g. PlatformAdapter) provides it."
                )

    def _build_query_stats(
        self,
        execution_time: float,
        *,
        connection: Any = None,
        query_id: str | None = None,
    ) -> dict[str, Any]:
        return {"execution_time_seconds": execution_time}

    def _format_query_error(self, exc: BaseException) -> str:
        return str(exc) or repr(exc) or type(exc).__name__

    def _attach_query_stats(self, result_dict: dict[str, Any], query_stats: dict[str, Any]) -> None:
        result_dict["query_statistics"] = query_stats
        result_dict["resource_usage"] = query_stats

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
        cursor = connection.cursor()

        try:
            cursor.execute(query)
            result = cursor.fetchall()

            execution_time = elapsed_seconds(start_time)
            actual_row_count = len(result) if result else 0
            query_stats = self._build_query_stats(
                execution_time,
                connection=connection,
                query_id=query_id,
            )

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

            result_dict = self._build_query_result_with_validation(
                query_id=query_id,
                execution_time=execution_time,
                actual_row_count=actual_row_count,
                first_row=result[0] if result else None,
                validation_result=validation_result,
                materialized_rows=result,
            )
            self._attach_query_stats(result_dict, query_stats)
            return result_dict

        except Exception as e:
            execution_time = elapsed_seconds(start_time)
            return {
                "query_id": query_id,
                "status": "FAILED",
                "execution_time_seconds": execution_time,
                "rows_returned": 0,
                "error": self._format_query_error(e),
                "error_type": type(e).__name__,
            }
        finally:
            cursor.close()
