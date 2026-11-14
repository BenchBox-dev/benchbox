from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable

import pandas as pd

from benchbox.core.tpcdi.schema import TABLES
from benchbox.utils.duckdb_extensions import finish_unfinished_result

_STAGING_VIEW = "benchbox_tpcdi_stage"
_COPY_NULL = "\\N"
_DUCKDB_DRIVERS = frozenset({"duckdb", "_duckdb"})
_POSTGRES_DRIVERS = frozenset({"psycopg2", "psycopg"})


def _driver_family(obj: Any) -> str:
    return type(obj).__module__.split(".", 1)[0]


def bulk_insert_dataframe(cursor: Any, quoted_table: str, quoted_columns: list[str], dataframe: pd.DataFrame) -> bool:
    column_sql = ",".join(quoted_columns)
    if _driver_family(cursor) in _DUCKDB_DRIVERS and hasattr(cursor, "register"):
        finish_unfinished_result(cursor)
        cursor.register(_STAGING_VIEW, dataframe)
        try:
            cursor.execute(f"INSERT INTO {quoted_table} ({column_sql}) SELECT * FROM {_STAGING_VIEW}")
        finally:
            cursor.unregister(_STAGING_VIEW)
        return True

    if _driver_family(cursor) in _POSTGRES_DRIVERS:
        if not (hasattr(cursor, "copy_expert") or hasattr(cursor, "copy")):
            cursor = cursor.cursor()
        whole_floats = {
            column: "Int64"
            for column in dataframe.select_dtypes(include="float").columns
            if ((values := dataframe[column].dropna()) == values.round()).all() and values.abs().lt(2**63).all()
        }
        dataframe = dataframe.astype(whole_floats)
        buffer = io.StringIO()
        dataframe.to_csv(buffer, index=False, header=False, na_rep=_COPY_NULL, lineterminator="\n")
        copy_sql = f"COPY {quoted_table} ({column_sql}) FROM STDIN WITH (FORMAT csv, NULL '{_COPY_NULL}')"
        if hasattr(cursor, "copy_expert"):
            buffer.seek(0)
            cursor.copy_expert(copy_sql, buffer)
        else:
            with cursor.copy(copy_sql) as copy:
                copy.write(buffer.getvalue())
        return True
    return False


class SQLETLBackend:
    def __init__(
        self,
        *,
        connection: Any,
        create_tables_sql: str,
        execute_validation_query: Callable[[str, Any], Any],
        validation_query_ids: list[str],
    ) -> None:
        self.connection = connection
        self._create_tables_sql = create_tables_sql
        self._execute_validation_query = execute_validation_query
        self._validation_query_ids = validation_query_ids

    def create_schema(self) -> None:
        if hasattr(self.connection, "executescript"):
            self.connection.executescript(self._create_tables_sql)
            return
        cursor = self.connection.cursor()
        for statement in self._create_tables_sql.split(";"):
            if statement.strip():
                cursor.execute(statement)

    def load_dataframes(self, staged_data: dict[str, pd.DataFrame], batch_type: str) -> dict[str, Any]:
        _ = batch_type
        results: dict[str, Any] = {"records_loaded": 0, "tables_updated": []}
        self.create_schema()

        for table_name, df in staged_data.items():
            if df is None or df.empty:
                continue
            loaded = self._load_table_dataframe(table_name, df)
            results["records_loaded"] += loaded
            if loaded and table_name not in results["tables_updated"]:
                results["tables_updated"].append(table_name)
        return results

    def validate_results(self) -> dict[str, Any]:
        validation_results: dict[str, Any] = {
            "validation_queries": {},
            "data_quality_issues": [],
            "data_quality_score": 0,
            "completeness_checks": {},
            "consistency_checks": {},
            "accuracy_checks": {},
        }

        for query_id in self._validation_query_ids:
            try:
                result = self._execute_validation_query(query_id, self.connection)
                validation_results["validation_queries"][query_id] = {
                    "success": True,
                    "row_count": len(result) if result else 0,
                    "result": result[:10] if result else [],
                }
            except Exception as exc:
                validation_results["validation_queries"][query_id] = {"success": False, "error": str(exc)}
                validation_results["data_quality_issues"].append(
                    {"type": "query_execution_error", "query_id": query_id, "error": str(exc)}
                )

        validation_results["completeness_checks"] = self._check_data_completeness()
        validation_results["consistency_checks"] = self._check_data_consistency()
        validation_results["accuracy_checks"] = self._check_data_accuracy()
        validation_results["data_quality_score"] = self._calculate_data_quality_score(validation_results)
        return validation_results

    def execute_scd2_expire(self, table_name: str, condition: str | Any, updates: dict[str, Any]) -> dict[str, Any]:
        if not updates:
            return {"success": True, "rows_affected": 0}

        validated_table = self._validate_table_name(table_name)
        update_columns = [self._validate_column_name(validated_table, column) for column in updates]
        set_parts = [f"{self._quote_identifier(column)} = ?" for column in update_columns]
        where_clause, where_params = self._build_safe_where_clause(validated_table, condition)
        sql = f"UPDATE {self._quote_identifier(validated_table)} SET {', '.join(set_parts)} WHERE {where_clause}"
        values = tuple(updates[column] for column in update_columns) + where_params
        cursor = self.connection.cursor() if hasattr(self.connection, "cursor") else self.connection
        cursor.execute(sql, values)
        if hasattr(self.connection, "commit"):
            self.connection.commit()
        return {"success": True, "rows_affected": int(getattr(cursor, "rowcount", 0) or 0)}

    def execute_scd2_insert(self, table_name: str, dataframe: pd.DataFrame) -> dict[str, Any]:
        return {"success": True, "rows_affected": self._load_table_dataframe(table_name, dataframe)}

    def load_customer_scd2_batch(self, dataframe: pd.DataFrame, *, batch_type: str) -> dict[str, Any]:
        self.create_schema()
        customers = dataframe.copy()
        cursor = self.connection
        if not hasattr(cursor, "execute") and hasattr(cursor, "cursor"):
            cursor = cursor.cursor()
        try:
            cursor.execute("BEGIN")
            current_max_sk, current_max_batch, latest_effective = cursor.execute(
                'SELECT COALESCE(MAX("SK_CustomerID"), 0), COALESCE(MAX("BatchID"), 0), MAX("EffectiveDate") '
                'FROM "DimCustomer"'
            ).fetchall()[0]
            effective_date = self._next_effective_date(latest_effective, customers["EffectiveDate"].iloc[0])
            batch_id = int(current_max_batch) + 1
            initial_sk = {"historical": 1, "incremental": 1_000_001, "scd": 2_000_001}.get(batch_type, 1)
            first_sk = max(int(current_max_sk) + 1, initial_sk)
            customers["SK_CustomerID"] = range(first_sk, first_sk + len(customers))
            customers["BatchID"] = batch_id
            customers["IsCurrent"] = True
            customers["EffectiveDate"] = effective_date.isoformat()
            customers["EndDate"] = "9999-12-31"

            if batch_type in {"incremental", "scd"}:
                business_keys = customers["CustomerID"].drop_duplicates().tolist()
                placeholders = ", ".join("?" for _ in business_keys)
                cursor.execute(
                    'UPDATE "DimCustomer" SET "IsCurrent" = ?, "EndDate" = ? '
                    f'WHERE "IsCurrent" = ? AND "CustomerID" IN ({placeholders})',
                    (False, (effective_date - timedelta(days=1)).isoformat(), True, *business_keys),
                )

            self._insert_table_dataframe(cursor, "DimCustomer", customers)
            if hasattr(self.connection, "commit"):
                self.connection.commit()
            return {"success": True, "rows_affected": len(customers)}
        except Exception:
            if hasattr(self.connection, "rollback"):
                self.connection.rollback()
            raise

    @staticmethod
    def _next_effective_date(latest_effective: Any, source_effective: Any) -> date:
        if latest_effective is None:
            latest_effective = source_effective
            if isinstance(latest_effective, datetime):
                return latest_effective.date()
            if isinstance(latest_effective, date):
                return latest_effective
            return date.fromisoformat(str(latest_effective)[:10])
        if isinstance(latest_effective, datetime):
            latest_effective = latest_effective.date()
        if isinstance(latest_effective, date):
            return latest_effective + timedelta(days=1)
        return date.fromisoformat(str(latest_effective)[:10]) + timedelta(days=1)

    def read_current_dimension(self, table_name: str) -> pd.DataFrame | None:
        try:
            validated_table = self._validate_table_name(table_name)
            if not self._column_exists(validated_table, "IsCurrent"):
                return None
            query = (
                f"SELECT * FROM {self._quote_identifier(validated_table)} "
                f"WHERE {self._quote_identifier('IsCurrent')} = TRUE"
            )
            return pd.read_sql(query, self.connection)
        except ValueError:
            raise
        except Exception:
            return None

    def _load_table_dataframe(self, table_name: str, dataframe: pd.DataFrame) -> int:
        validated_table = self._validate_table_name(table_name)
        headers = dataframe.columns.tolist()
        if not headers:
            return 0

        cursor = self.connection
        if not hasattr(cursor, "executemany") and hasattr(cursor, "cursor"):
            cursor = cursor.cursor()
        loaded = self._insert_table_dataframe(cursor, validated_table, dataframe)

        if hasattr(self.connection, "commit"):
            self.connection.commit()
        return loaded

    def _insert_table_dataframe(self, cursor: Any, table_name: str, dataframe: pd.DataFrame) -> int:
        validated_table = self._validate_table_name(table_name)
        headers = dataframe.columns.tolist()
        if not headers:
            return 0
        validated_headers = [self._validate_column_name(validated_table, str(header)) for header in headers]
        placeholders = ",".join("?" for _ in validated_headers)
        quoted_columns = ",".join(self._quote_identifier(column) for column in validated_headers)
        insert_sql = f"INSERT INTO {self._quote_identifier(validated_table)} ({quoted_columns}) VALUES ({placeholders})"
        if bulk_insert_dataframe(
            cursor,
            self._quote_identifier(validated_table),
            [self._quote_identifier(c) for c in validated_headers],
            dataframe,
        ):
            return len(dataframe)
        loaded = 0
        for start_idx in range(0, len(dataframe), 1000):
            batch_rows = [tuple(row) for row in dataframe.iloc[start_idx : start_idx + 1000].values]
            if hasattr(cursor, "executemany"):
                cursor.executemany(insert_sql, batch_rows)
            else:
                for record in batch_rows:
                    cursor.execute(insert_sql, record)
            loaded += len(batch_rows)
        return loaded

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
            raise ValueError(f"Unsafe SQL identifier: {identifier!r}")
        return f'"{identifier}"'

    def _validate_table_name(self, table_name: str) -> str:
        if table_name not in TABLES:
            raise ValueError(f"Unsupported table for TPC-DI ETL: {table_name}")
        return table_name

    def _validate_column_name(self, table_name: str, column_name: str) -> str:
        allowed = {str(col["name"]) for col in TABLES[table_name]["columns"]}
        if column_name not in allowed:
            raise ValueError(f"Unsupported column {column_name!r} for table {table_name}")
        return column_name

    def _column_exists(self, table_name: str, column_name: str) -> bool:
        return any(str(col["name"]) == column_name for col in TABLES[table_name]["columns"])

    def _build_safe_where_clause(self, table_name: str, condition: str | Any) -> tuple[str, tuple[Any, ...]]:
        if isinstance(condition, dict):
            return self._build_parameterized_where_clause(table_name, condition)

        if not isinstance(condition, str):
            raise TypeError("SCD2 expire condition must be a SQL expression string or dict[str, Any]")

        normalized = self._validate_sql_where_clause(table_name, condition)
        return normalized, ()

    def _build_parameterized_where_clause(
        self, table_name: str, condition: dict[str, Any]
    ) -> tuple[str, tuple[Any, ...]]:
        if not condition:
            raise ValueError("SCD2 expire condition dictionary cannot be empty")
        clauses: list[str] = []
        values: list[Any] = []
        for raw_column, value in condition.items():
            column = self._validate_column_name(table_name, str(raw_column))
            quoted_column = self._quote_identifier(column)
            if value is None:
                clauses.append(f"{quoted_column} IS NULL")
            else:
                clauses.append(f"{quoted_column} = ?")
                values.append(value)
        return " AND ".join(clauses), tuple(values)

    def _validate_sql_where_clause(self, table_name: str, condition: str) -> str:
        normalized = condition.strip()
        if not normalized:
            raise ValueError("SCD2 expire condition cannot be empty")
        if any(token in normalized for token in (";", "--", "/*", "*/")):
            raise ValueError("Unsafe SQL tokens are not allowed in SCD2 expire condition")

        forbidden = {
            "DROP",
            "DELETE",
            "INSERT",
            "UPDATE",
            "ALTER",
            "CREATE",
            "ATTACH",
            "DETACH",
            "PRAGMA",
        }
        upper_condition = normalized.upper()
        if any(re.search(rf"\b{keyword}\b", upper_condition) for keyword in forbidden):
            raise ValueError("DML/DDL keywords are not allowed in SCD2 expire condition")

        self._validate_where_clause_identifiers(table_name, normalized)
        return normalized

    def _validate_where_clause_identifiers(self, table_name: str, normalized: str) -> None:
        scrubbed = re.sub(r"'[^']*'", " ", normalized)
        tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", scrubbed)
        allowed_keywords = {
            "AND",
            "OR",
            "NOT",
            "IS",
            "NULL",
            "TRUE",
            "FALSE",
            "LIKE",
            "IN",
            "BETWEEN",
        }
        allowed_columns = {str(col["name"]).upper() for col in TABLES[table_name]["columns"]}
        for token in tokens:
            token_upper = token.upper()
            if token_upper in allowed_keywords or token_upper in allowed_columns:
                continue
            raise ValueError(f"Unsupported identifier in SCD2 expire condition: {token}")

    def _check_data_completeness(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for table_name, schema in TABLES.items():
            try:
                validated_table = self._validate_table_name(table_name)
                quoted_table = self._quote_identifier(validated_table)
                cursor = self.connection.execute(f"SELECT COUNT(*) FROM {quoted_table}")
                total = cursor.fetchone()[0]
                null_checks = {}
                key_cols = [col["name"] for col in schema["columns"] if not col.get("nullable", True)]
                for column in key_cols[:5]:
                    try:
                        validated_column = self._validate_column_name(validated_table, str(column))
                        quoted_column = self._quote_identifier(validated_column)
                        cursor = self.connection.execute(
                            f"SELECT COUNT(*) FROM {quoted_table} WHERE {quoted_column} IS NULL"
                        )
                        null_count = cursor.fetchone()[0]
                        null_checks[column] = {
                            "null_count": null_count,
                            "null_percentage": (null_count / total * 100) if total > 0 else 0,
                        }
                    except Exception as exc:
                        null_checks[column] = {"error": str(exc)}
                results[table_name] = {"total_records": total, "null_checks": null_checks}
            except Exception as exc:
                results[table_name] = {"error": str(exc)}
        return results

    def _check_data_consistency(self) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        try:
            cursor = self.connection.execute(
                """
                SELECT COUNT(*) as orphaned_trades
                FROM FactTrade f
                LEFT JOIN DimCustomer c ON f.SK_CustomerID = c.SK_CustomerID
                WHERE c.SK_CustomerID IS NULL
                """
            )
            checks["orphaned_trades"] = cursor.fetchone()[0]
            cursor = self.connection.execute(
                """
                SELECT COUNT(*) - COUNT(DISTINCT SK_CustomerID) as duplicate_customers
                FROM DimCustomer
                """
            )
            checks["duplicate_customers"] = cursor.fetchone()[0]
            cursor = self.connection.execute(
                """
                SELECT COUNT(*) as invalid_dates
                FROM FactTrade
                WHERE SK_CreateDateID > SK_CloseDateID
                """
            )
            checks["invalid_date_sequences"] = cursor.fetchone()[0]
        except Exception as exc:
            checks["error"] = str(exc)
        return checks

    def _check_data_accuracy(self) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        try:
            cursor = self.connection.execute("SELECT COUNT(*) as negative_prices FROM FactTrade WHERE TradePrice < 0")
            checks["negative_trade_prices"] = cursor.fetchone()[0]
            cursor = self.connection.execute(
                "SELECT COUNT(*) as invalid_tiers FROM DimCustomer WHERE Tier NOT IN (1, 2, 3)"
            )
            checks["invalid_customer_tiers"] = cursor.fetchone()[0]
            cursor = self.connection.execute(
                "SELECT COUNT(*) as future_birth_dates FROM DimCustomer WHERE DOB > DATE('now')"
            )
            checks["future_birth_dates"] = cursor.fetchone()[0]
        except Exception as exc:
            checks["error"] = str(exc)
        return checks

    @staticmethod
    def _calculate_data_quality_score(validation_results: dict[str, Any]) -> float:
        score = 100.0
        validation_queries = validation_results.get("validation_queries", {})
        failed = sum(1 for payload in validation_queries.values() if not payload.get("success", False))
        total = len(validation_queries)
        if total > 0:
            score -= (failed / total) * 20
        consistency = validation_results.get("consistency_checks", {})
        if consistency.get("orphaned_trades", 0) > 0:
            score -= 15
        if consistency.get("duplicate_customers", 0) > 0:
            score -= 10
        if consistency.get("invalid_date_sequences", 0) > 0:
            score -= 10
        accuracy = validation_results.get("accuracy_checks", {})
        if accuracy.get("negative_trade_prices", 0) > 0:
            score -= 15
        if accuracy.get("invalid_customer_tiers", 0) > 0:
            score -= 10
        if accuracy.get("future_birth_dates", 0) > 0:
            score -= 10
        return max(0.0, score)
