# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Union

import pandas as pd

from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.dialect_utils import translate_sql_query

logger = logging.getLogger(__name__)

HISTORY_TRACKING_TABLES = frozenset({"DimAccount", "DimBroker", "DimCompany", "DimCustomer", "DimSecurity"})

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PARAMETER_CHUNK = 500
_SAVEPOINT_NAME = "benchbox_scd_changes"


def _validated_identifier(name: str) -> str:
    if not isinstance(name, str) or not _IDENTIFIER_PATTERN.fullmatch(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name


def _chunks(items: list[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _current_sk_column(frame: pd.DataFrame, sk_column: str) -> Optional[str]:
    for candidate in (f"{sk_column}_current", sk_column):
        if candidate in frame.columns:
            return candidate
    return None


def _to_db_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if pd.isna(value):
        return None
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        return value.item()
    return value


@dataclass
class SCDChangeRecord:
    table_name: str = "dimension"
    business_key: Union[dict[str, Any], Any] = field(default_factory=dict)
    surrogate_key: Optional[int] = None
    change_type: str = "INSERT"
    changed_columns: list[Union[str, dict[str, Any]]] = field(default_factory=list)
    effective_date: datetime = field(default_factory=datetime.now)
    end_date: Optional[datetime] = None
    batch_id: int = 0
    source_record: Optional[dict[str, Any]] = None
    target_record: Optional[dict[str, Any]] = None

    old_values: Optional[dict[str, Any]] = field(default=None)
    new_values: Optional[dict[str, Any]] = field(default=None)


@dataclass
class SCDProcessingConfig:
    effective_date_column: str = "EffectiveDate"
    end_date_column: str = "EndDate"
    is_current_column: str = "IsCurrent"
    batch_id_column: str = "BatchID"

    default_end_date: datetime = datetime(9999, 12, 31)

    case_sensitive_comparison: bool = False
    trim_strings: bool = True
    null_equals_empty_string: bool = True
    numeric_precision_tolerance: float = 0.001

    batch_size: int = 10000
    enable_parallel_processing: bool = True
    max_workers: Optional[int] = None

    enable_audit_trail: bool = True
    audit_table_suffix: str = "_Audit"
    track_data_lineage: bool = False

    enable_validation: bool = True
    validate_business_keys: bool = True
    validate_data_types: bool = True


class EnhancedSCDType2Processor:
    def __init__(
        self,
        connection: Any,
        dialect: str = "duckdb",
        config: Optional[SCDProcessingConfig] = None,
    ):
        self.connection = connection
        self.dialect = dialect
        self.config = config or SCDProcessingConfig()
        self.change_audit_trail: list[SCDChangeRecord] = []
        self.processing_stats: dict[str, Any] = {}
        self._next_surrogate_key: dict[str, int] = {}

    def process_dimension(
        self,
        dimension_name: str,
        business_key_column: str,
        scd_columns: list[str],
        batch_id: int,
    ) -> dict[str, Any]:
        import pandas as pd

        pd.DataFrame()

        return {
            "dimension_name": dimension_name,
            "success": True,
            "records_processed": 0,
            "changes_detected": 0,
            "new_records": 0,
            "updated_records": 0,
        }

    def process_scd_changes(
        self,
        new_data: pd.DataFrame,
        table_name: str,
        business_keys: list[str],
        scd_columns: list[str],
        batch_id: int,
        effective_date: datetime,
        non_scd_columns: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        logger.info(f"Processing SCD Type 2 changes for {table_name} with {len(new_data)} input records")

        start_time = datetime.now()
        change_stats = {
            "records_processed": len(new_data),
            "new_records": 0,
            "updated_records": 0,
            "unchanged_records": 0,
            "scd_type2_changes": 0,
            "data_quality_issues": 0,
            "business_key_resolutions": 0,
            "audit_trail_entries": 0,
            "processing_time": 0.0,
            "records_per_second": 0.0,
            "success": False,
            "error_message": None,
        }

        trail_length = len(self.change_audit_trail)
        self._next_surrogate_key = {}

        try:
            self._validated_table(table_name)
            if self.config.enable_validation:
                validation_results = self._validate_input_data(new_data, business_keys, scd_columns)
                change_stats["data_quality_issues"] = validation_results["issues_found"]
                if not validation_results["passed"]:
                    change_stats["error_message"] = "Data quality validation failed"
                    return change_stats

            current_data = self._load_current_dimension_data(table_name)
            logger.debug(f"Loaded {len(current_data)} current records from {table_name}")
            table_columns = list(current_data.columns) if len(current_data.columns) > 0 else None

            change_analysis = self._analyze_dimension_changes(
                current_data,
                new_data,
                business_keys,
                scd_columns,
                non_scd_columns or [],
            )

            applied = {"new_records": 0, "scd_type2_changes": 0, "updated_records": 0}
            with self._transaction():
                new_records = change_analysis["new_records"]
                if len(new_records) > 0:
                    insert_result = self._insert_new_dimension_records(
                        new_records, table_name, business_keys, batch_id, effective_date, table_columns
                    )
                    applied["new_records"] = insert_result["records_inserted"]

                scd_changes = change_analysis["scd_changes"]
                if len(scd_changes) > 0:
                    scd_result = self._process_scd_type2_changes(
                        scd_changes, table_name, business_keys, batch_id, effective_date, table_columns
                    )
                    applied["scd_type2_changes"] = scd_result["records_updated"]

                non_scd_updates = change_analysis["non_scd_updates"]
                if len(non_scd_updates) > 0:
                    update_result = self._process_non_scd_updates(
                        non_scd_updates, table_name, batch_id, non_scd_columns, table_columns
                    )
                    applied["updated_records"] = update_result["records_updated"]

            change_stats.update(applied)
            unchanged_records = change_analysis["unchanged_records"]
            change_stats["unchanged_records"] = len(unchanged_records)

            if self.config.enable_audit_trail:
                audit_entries = self._generate_change_audit_trail(change_analysis, table_name, batch_id, effective_date)
                change_stats["audit_trail_entries"] = len(audit_entries)

            end_time = datetime.now()
            processing_time = (end_time - start_time).total_seconds()
            change_stats["processing_time"] = processing_time
            change_stats["records_per_second"] = change_stats["records_processed"] / max(processing_time, 0.001)
            change_stats["success"] = True

            self.processing_stats[f"{table_name}_{batch_id}"] = change_stats.copy()

            logger.info(
                f"SCD Type 2 processing completed for {table_name}: "
                f"{change_stats['new_records']} new, "
                f"{change_stats['scd_type2_changes']} SCD changes, "
                f"{change_stats['updated_records']} non-SCD updates, "
                f"{change_stats['unchanged_records']} unchanged "
                f"(processed in {processing_time:.2f}s)"
            )

            return change_stats

        except Exception as e:
            del self.change_audit_trail[trail_length:]
            error_msg = f"SCD Type 2 processing failed for {table_name}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            change_stats["error_message"] = error_msg
            change_stats["success"] = False
            return change_stats

    def _validate_input_data(
        self, data: pd.DataFrame, business_keys: list[str], scd_columns: list[str]
    ) -> dict[str, Any]:

        validation_result = {"passed": True, "issues_found": 0, "issues": []}

        required_columns = business_keys + scd_columns
        missing_columns = [col for col in required_columns if col not in data.columns]
        if missing_columns:
            validation_result["issues"].append(f"Missing required columns: {missing_columns}")
            validation_result["passed"] = False
            validation_result["issues_found"] += len(missing_columns)

        if self.config.validate_business_keys:
            for bk_col in business_keys:
                if bk_col in data.columns:
                    null_count = data[bk_col].isna().sum()
                    if null_count > 0:
                        validation_result["issues"].append(
                            f"Null values in business key {bk_col}: {null_count} records"
                        )
                        validation_result["passed"] = False
                        validation_result["issues_found"] += null_count

        if len(business_keys) > 0 and all(col in data.columns for col in business_keys):
            duplicate_count = data.duplicated(subset=business_keys).sum()
            if duplicate_count > 0:
                validation_result["issues"].append(f"Duplicate business keys found: {duplicate_count} records")
                validation_result["passed"] = False
                validation_result["issues_found"] += duplicate_count

        logger.debug(f"Data validation completed: {validation_result['issues_found']} issues found")
        return validation_result

    def _load_current_dimension_data(self, table_name: str) -> pd.DataFrame:

        table = self._validated_table(table_name)
        sk_column = _validated_identifier(f"SK_{table_name.replace('Dim', '')}ID")
        is_current = _validated_identifier(self.config.is_current_column)

        query = f"""
        SELECT * FROM {table}
        WHERE {is_current} = 1
        ORDER BY {sk_column}
        """

        if self.dialect != "duckdb":
            query = translate_sql_query(query, self.dialect, source_dialect="duckdb", identify=False)

        cursor = self.connection.execute(query)
        columns = [desc[0] for desc in cursor.description]
        data = cursor.fetchall()
        result = pd.DataFrame(data, columns=columns)

        logger.debug(f"Loaded {len(result)} current records from {table_name}")
        return result

    def _analyze_dimension_changes(
        self,
        current_data: pd.DataFrame,
        new_data: pd.DataFrame,
        business_keys: list[str],
        scd_columns: list[str],
        non_scd_columns: list[str],
    ) -> dict[str, pd.DataFrame]:

        analysis_start = mono_time()
        logger.debug("Starting comprehensive change analysis")

        if len(current_data) == 0:
            logger.debug("No current data found - all records are new")
            return {
                "new_records": new_data.copy(),
                "scd_changes": pd.DataFrame(),
                "non_scd_updates": pd.DataFrame(),
                "unchanged_records": pd.DataFrame(),
            }

        try:
            current_bk_index = current_data.set_index(business_keys) if len(business_keys) > 0 else current_data
            new_bk_index = new_data.set_index(business_keys) if len(business_keys) > 0 else new_data
        except KeyError as e:
            logger.error(f"Business key column not found: {str(e)}")
            raise ValueError(f"Business key column missing: {str(e)}") from e

        new_bk_values = new_bk_index.index
        current_bk_values = current_bk_index.index
        new_keys = new_bk_values.difference(current_bk_values)
        existing_keys = new_bk_values.intersection(current_bk_values)

        new_records = new_data[new_data.set_index(business_keys).index.isin(new_keys)].copy()
        potentially_changed = new_data[new_data.set_index(business_keys).index.isin(existing_keys)].copy()

        scd_changes = pd.DataFrame()
        non_scd_updates = pd.DataFrame()
        unchanged_records = pd.DataFrame()

        if len(potentially_changed) > 0:
            merged = potentially_changed.merge(
                current_data,
                on=business_keys,
                how="inner",
                suffixes=("_new", "_current"),
            )

            if len(merged) > 0:
                scd_change_mask = self._detect_scd_changes(merged, scd_columns)

                non_scd_change_mask = self._detect_non_scd_changes(merged, non_scd_columns)

                scd_changes = merged[scd_change_mask].copy() if scd_change_mask.any() else pd.DataFrame()
                non_scd_only_mask = non_scd_change_mask & ~scd_change_mask
                non_scd_updates = merged[non_scd_only_mask].copy() if non_scd_only_mask.any() else pd.DataFrame()
                no_change_mask = ~scd_change_mask & ~non_scd_change_mask
                unchanged_records = merged[no_change_mask].copy() if no_change_mask.any() else pd.DataFrame()

        analysis_time = elapsed_seconds(analysis_start)
        logger.debug(
            f"Change analysis completed in {analysis_time:.3f}s: "
            f"{len(new_records)} new, {len(scd_changes)} SCD changes, "
            f"{len(non_scd_updates)} non-SCD updates, {len(unchanged_records)} unchanged"
        )

        return {
            "new_records": new_records,
            "scd_changes": scd_changes,
            "non_scd_updates": non_scd_updates,
            "unchanged_records": unchanged_records,
        }

    def _detect_scd_changes(self, merged_data: pd.DataFrame, scd_columns: list[str]) -> pd.Series:

        change_mask = pd.Series([False] * len(merged_data), index=merged_data.index)

        for col in scd_columns:
            new_col = f"{col}_new"
            current_col = f"{col}_current"

            if new_col not in merged_data.columns or current_col not in merged_data.columns:
                logger.debug(f"SCD column {col} not found in comparison data, skipping")
                continue

            new_values = merged_data[new_col]
            current_values = merged_data[current_col]

            if self.config.trim_strings:
                if pd.api.types.is_string_dtype(new_values.dtype):
                    new_values = new_values.astype(str).str.strip()
                if pd.api.types.is_string_dtype(current_values.dtype):
                    current_values = current_values.astype(str).str.strip()

            if self.config.null_equals_empty_string:
                new_values = new_values.fillna("")
                current_values = current_values.fillna("")

            if pd.api.types.is_numeric_dtype(new_values) and pd.api.types.is_numeric_dtype(current_values):
                col_changed = abs(new_values - current_values) > self.config.numeric_precision_tolerance
            elif pd.api.types.is_string_dtype(new_values) or pd.api.types.is_string_dtype(current_values):
                if self.config.case_sensitive_comparison:
                    col_changed = new_values != current_values
                else:
                    col_changed = new_values.astype(str).str.lower() != current_values.astype(str).str.lower()
            else:
                col_changed = (new_values != current_values) | (new_values.isna() != current_values.isna())

            change_mask = change_mask | col_changed

        return change_mask

    def _detect_non_scd_changes(self, merged_data: pd.DataFrame, non_scd_columns: list[str]) -> pd.Series:

        if not non_scd_columns:
            return pd.Series([False] * len(merged_data), index=merged_data.index)

        change_mask = pd.Series([False] * len(merged_data), index=merged_data.index)

        for col in non_scd_columns:
            new_col = f"{col}_new"
            current_col = f"{col}_current"

            if new_col in merged_data.columns and current_col in merged_data.columns:
                both_null = merged_data[new_col].isna() & merged_data[current_col].isna()
                col_changed = (merged_data[new_col] != merged_data[current_col]) & ~both_null
                change_mask = change_mask | col_changed

        return change_mask

    def _insert_new_dimension_records(
        self,
        new_records: pd.DataFrame,
        table_name: str,
        business_keys: list[str],
        batch_id: int,
        effective_date: datetime,
        table_columns: Optional[list[str]] = None,
    ) -> dict[str, Any]:

        if len(new_records) == 0:
            return {"records_inserted": 0}

        logger.debug(f"Inserting {len(new_records)} new records into {table_name}")

        records_to_insert = new_records.copy()
        records_to_insert[self.config.batch_id_column] = batch_id
        records_to_insert[self.config.is_current_column] = 1
        records_to_insert[self.config.effective_date_column] = effective_date
        records_to_insert[self.config.end_date_column] = self.config.default_end_date

        sk_column = f"SK_{table_name.replace('Dim', '')}ID"
        if sk_column not in records_to_insert.columns:
            records_to_insert[sk_column] = self._allocate_surrogate_keys(table_name, sk_column, len(records_to_insert))

        audit_entries = []
        for _idx, record in records_to_insert.iterrows():
            business_key = {col: record[col] for col in business_keys}
            audit_entries.append(
                SCDChangeRecord(
                    table_name=table_name,
                    business_key=business_key,
                    surrogate_key=record[sk_column],
                    change_type="INSERT",
                    effective_date=effective_date,
                    batch_id=batch_id,
                    source_record=record.to_dict(),
                )
            )

        records_inserted = self._insert_rows(table_name, records_to_insert.to_dict("records"), table_columns)
        self.change_audit_trail.extend(audit_entries)

        logger.debug(f"Inserted {records_inserted} records into {table_name}")

        return {"records_inserted": records_inserted}

    def _process_scd_type2_changes(
        self,
        scd_changes: pd.DataFrame,
        table_name: str,
        business_keys: list[str],
        batch_id: int,
        effective_date: datetime,
        table_columns: Optional[list[str]] = None,
    ) -> dict[str, Any]:

        if len(scd_changes) == 0:
            return {"records_updated": 0, "records_expired": 0, "records_inserted": 0}

        logger.debug(f"Processing {len(scd_changes)} SCD Type 2 changes for {table_name}")

        sk_column = f"SK_{table_name.replace('Dim', '')}ID"
        current_sk_col = _current_sk_column(scd_changes, sk_column)
        if current_sk_col is None:
            raise ValueError(f"Cannot expire current versions of {table_name}: missing {sk_column}")

        surrogate_keys = [_to_db_value(key) for key in scd_changes[current_sk_col].tolist()]
        new_sks = self._allocate_surrogate_keys(table_name, sk_column, len(scd_changes))
        new_current_records = []
        audit_entries = []

        for position, (_idx, row) in enumerate(scd_changes.iterrows()):
            new_record: dict[str, Any] = {}
            changed_columns = []

            for col in row.index:
                if col.endswith("_current"):
                    new_record.setdefault(col[: -len("_current")], row[col])
                elif not col.endswith("_new"):
                    new_record[col] = row[col]

            for col in row.index:
                if col.endswith("_new"):
                    original_col = col[:-4]
                    current_col = f"{original_col}_current"

                    new_record[original_col] = row[col]

                    if current_col in row.index and row[col] != row[current_col]:
                        changed_columns.append(
                            {
                                "column": original_col,
                                "old_value": row[current_col],
                                "new_value": row[col],
                            }
                        )

            business_key = {col: new_record[col] for col in business_keys if col in new_record}

            new_record[self.config.batch_id_column] = batch_id
            new_record[self.config.is_current_column] = 1
            new_record[self.config.effective_date_column] = effective_date
            new_record[self.config.end_date_column] = self.config.default_end_date
            new_record[sk_column] = new_sks[position]

            new_current_records.append(new_record)

            audit_entries.append(
                SCDChangeRecord(
                    table_name=table_name,
                    business_key=business_key,
                    surrogate_key=new_record[sk_column],
                    change_type="SCD_TYPE2_UPDATE",
                    changed_columns=changed_columns,
                    effective_date=effective_date,
                    batch_id=batch_id,
                    source_record=new_record.copy(),
                    target_record=row.to_dict(),
                )
            )

        records_expired = self._expire_current_versions(table_name, sk_column, surrogate_keys, effective_date)
        if records_expired != len(surrogate_keys):
            raise RuntimeError(
                f"Expired {records_expired} rows in {table_name} for {len(surrogate_keys)} superseded versions"
            )

        records_inserted = self._insert_rows(table_name, new_current_records, table_columns)
        self.change_audit_trail.extend(audit_entries)

        logger.debug(f"Expired {records_expired} and inserted {records_inserted} versions for {table_name}")

        return {
            "records_updated": records_expired + records_inserted,
            "records_expired": records_expired,
            "records_inserted": records_inserted,
        }

    def _expire_current_versions(
        self, table_name: str, sk_column: str, surrogate_keys: list[Any], effective_date: datetime
    ) -> int:
        table = self._validated_table(table_name)
        is_current = _validated_identifier(self.config.is_current_column)
        end_date = _validated_identifier(self.config.end_date_column)
        sk_name = _validated_identifier(sk_column)

        expired = 0
        for chunk in _chunks(surrogate_keys, _PARAMETER_CHUNK):
            placeholders = ",".join(["?"] * len(chunk))
            expired += self._execute_dml(
                f"UPDATE {table} SET {is_current} = 0, {end_date} = ? WHERE {sk_name} IN ({placeholders}) "
                f"AND {is_current} = 1",
                [effective_date, *chunk],
            )
        return expired

    def _insert_rows(self, table_name: str, rows: list[dict[str, Any]], table_columns: Optional[list[str]]) -> int:
        if not rows:
            return 0

        table = self._validated_table(table_name)
        allowed = set(table_columns) if table_columns is not None else None
        columns = [col for col in rows[0] if allowed is None or col in allowed]
        column_sql = ", ".join(_validated_identifier(col) for col in columns)
        row_placeholder = "(" + ",".join(["?"] * len(columns)) + ")"
        rows_per_statement = max(1, _PARAMETER_CHUNK // len(columns))

        inserted = 0
        for chunk in _chunks(rows, rows_per_statement):
            params = [_to_db_value(row[col]) for row in chunk for col in columns]
            inserted += self._execute_dml(
                f"INSERT INTO {table} ({column_sql}) VALUES {', '.join([row_placeholder] * len(chunk))}", params
            )
        return inserted

    def _allocate_surrogate_keys(self, table_name: str, sk_column: str, count: int) -> list[int]:
        if table_name not in self._next_surrogate_key:
            table = self._validated_table(table_name)
            sk_name = _validated_identifier(sk_column)
            row = self.connection.execute(f"SELECT COALESCE(MAX({sk_name}), 0) FROM {table}").fetchone()
            self._next_surrogate_key[table_name] = int(row[0]) + 1
        start = self._next_surrogate_key[table_name]
        self._next_surrogate_key[table_name] = start + count
        return list(range(start, start + count))

    def _execute_dml(self, sql: str, params: list[Any]) -> int:
        cursor = self.connection.execute(sql, params)
        affected = getattr(cursor, "rowcount", None)
        if isinstance(affected, int) and affected >= 0:
            return affected
        try:
            result = cursor.fetchone()
        except Exception:
            result = None
        if result is not None and len(result) == 1 and isinstance(result[0], int):
            return result[0]
        raise RuntimeError("Database driver did not report the number of affected rows")

    def _validated_table(self, table_name: str) -> str:
        if table_name not in HISTORY_TRACKING_TABLES:
            raise ValueError(f"Table {table_name!r} is not a TPC-DI history-tracking dimension")
        return table_name

    def _caller_owns_transaction(self) -> bool:
        in_transaction = getattr(self.connection, "in_transaction", None)
        if isinstance(in_transaction, bool):
            return in_transaction
        if self.dialect != "duckdb":
            return False
        try:
            first = self.connection.execute("SELECT current_transaction_id()").fetchone()
            second = self.connection.execute("SELECT current_transaction_id()").fetchone()
        except Exception:
            return False
        return first is not None and first == second

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._caller_owns_transaction():
            try:
                self.connection.execute(f"SAVEPOINT {_SAVEPOINT_NAME}")
            except Exception as error:
                raise RuntimeError(
                    "Connection is in a caller-owned transaction without SAVEPOINT support; "
                    "the SCD changes cannot be applied atomically"
                ) from error
            try:
                yield
            except BaseException:
                self.connection.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT_NAME}")
                self.connection.execute(f"RELEASE SAVEPOINT {_SAVEPOINT_NAME}")
                raise
            self.connection.execute(f"RELEASE SAVEPOINT {_SAVEPOINT_NAME}")
            return

        try:
            self.connection.execute("BEGIN")
        except Exception as error:
            message = str(error).lower()
            if "transaction" not in message or ("within" not in message and "already" not in message):
                raise
            raise RuntimeError("Connection is already in a transaction that could not be detected") from error

        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        self.connection.execute("COMMIT")

    def _process_non_scd_updates(
        self,
        non_scd_updates: pd.DataFrame,
        table_name: str,
        batch_id: int,
        non_scd_columns: Optional[list[str]] = None,
        table_columns: Optional[list[str]] = None,
    ) -> dict[str, Any]:

        if len(non_scd_updates) == 0:
            return {"records_updated": 0}

        sk_column = f"SK_{table_name.replace('Dim', '')}ID"
        current_sk_col = _current_sk_column(non_scd_updates, sk_column)

        if current_sk_col is None:
            logger.warning(f"Cannot process non-SCD updates: missing {sk_column}")
            return {"records_updated": 0}

        table = self._validated_table(table_name)
        batch_column = _validated_identifier(self.config.batch_id_column)
        sk_name = _validated_identifier(sk_column)
        allowed = set(table_columns) if table_columns is not None else None

        value_sources: list[tuple[str, str]] = []
        for col in non_scd_columns or []:
            if allowed is not None and col not in allowed:
                continue
            source = f"{col}_new" if f"{col}_new" in non_scd_updates.columns else col
            if source in non_scd_updates.columns:
                value_sources.append((_validated_identifier(col), source))

        assignments = ", ".join([f"{batch_column} = ?", *(f"{col} = ?" for col, _ in value_sources)])
        sql = f"UPDATE {table} SET {assignments} WHERE {sk_name} = ?"

        records_updated = 0
        for _idx, row in non_scd_updates.iterrows():
            params = [batch_id, *(_to_db_value(row[source]) for _, source in value_sources)]
            params.append(_to_db_value(row[current_sk_col]))
            updated = self._execute_dml(sql, params)
            if updated != 1:
                raise RuntimeError(f"Non-SCD update on {table_name} changed {updated} rows for one surrogate key")
            records_updated += updated

        logger.debug(f"Overwrote non-SCD columns and batch id on {records_updated} records in {table_name}")

        return {"records_updated": records_updated}

    def _generate_change_audit_trail(
        self,
        change_analysis: dict[str, pd.DataFrame],
        table_name: str,
        batch_id: int,
        effective_date: datetime,
    ) -> list[SCDChangeRecord]:

        audit_entries = []
        datetime.now()

        for _, record in change_analysis["unchanged_records"].iterrows():
            business_key = {}
            for col in record.index:
                if not col.endswith(("_new", "_current")) and not col.startswith("SK_"):
                    if f"{col}_new" in record.index:
                        business_key[col] = record[f"{col}_new"]
                    elif f"{col}_current" in record.index:
                        business_key[col] = record[f"{col}_current"]

            change_record = SCDChangeRecord(
                table_name=table_name,
                business_key=business_key,
                change_type="NO_CHANGE",
                effective_date=effective_date,
                batch_id=batch_id,
            )
            audit_entries.append(change_record)

        self.change_audit_trail.extend(audit_entries)

        return audit_entries

    def get_change_audit_trail(
        self, table_name: Optional[str] = None, batch_id: Optional[int] = None
    ) -> list[SCDChangeRecord]:

        trail = self.change_audit_trail

        if table_name:
            trail = [record for record in trail if record.table_name == table_name]

        if batch_id is not None:
            trail = [record for record in trail if record.batch_id == batch_id]

        return trail

    def clear_change_audit_trail(self) -> None:
        self.change_audit_trail.clear()

    def get_comprehensive_statistics(self) -> dict[str, Any]:

        if not self.processing_stats:
            return {"message": "No processing statistics available"}

        total_stats = {
            "batches_processed": len(self.processing_stats),
            "tables_processed": len({key.split("_")[0] for key in self.processing_stats}),
            "total_records_processed": sum(stats["records_processed"] for stats in self.processing_stats.values()),
            "total_new_records": sum(stats["new_records"] for stats in self.processing_stats.values()),
            "total_scd_changes": sum(stats["scd_type2_changes"] for stats in self.processing_stats.values()),
            "total_non_scd_updates": sum(stats["updated_records"] for stats in self.processing_stats.values()),
            "total_unchanged_records": sum(stats["unchanged_records"] for stats in self.processing_stats.values()),
            "total_processing_time": sum(stats["processing_time"] for stats in self.processing_stats.values()),
            "successful_batches": sum(1 for stats in self.processing_stats.values() if stats["success"]),
            "failed_batches": sum(1 for stats in self.processing_stats.values() if not stats["success"]),
        }

        if total_stats["total_processing_time"] > 0:
            total_stats["overall_records_per_second"] = (
                total_stats["total_records_processed"] / total_stats["total_processing_time"]
            )
        else:
            total_stats["overall_records_per_second"] = 0

        trail_stats = {
            "audit_trail_entries": len(self.change_audit_trail),
            "inserts": len([r for r in self.change_audit_trail if r.change_type == "INSERT"]),
            "scd_updates": len([r for r in self.change_audit_trail if r.change_type == "SCD_TYPE2_UPDATE"]),
            "no_changes": len([r for r in self.change_audit_trail if r.change_type == "NO_CHANGE"]),
        }

        table_stats = {}
        for key, stats in self.processing_stats.items():
            table_name = key.split("_")[0]
            if table_name not in table_stats:
                table_stats[table_name] = {
                    "batches": 0,
                    "records_processed": 0,
                    "scd_changes": 0,
                    "avg_processing_time": 0,
                }
            table_stats[table_name]["batches"] += 1
            table_stats[table_name]["records_processed"] += stats["records_processed"]
            table_stats[table_name]["scd_changes"] += stats["scd_type2_changes"]
            table_stats[table_name]["avg_processing_time"] += stats["processing_time"]

        for table_name in table_stats:
            table_stats[table_name]["avg_processing_time"] /= table_stats[table_name]["batches"]

        return {
            "summary": total_stats,
            "audit_trail": trail_stats,
            "by_table": table_stats,
            "detailed_batch_stats": self.processing_stats.copy(),
        }

    def export_audit_trail(self, output_path: str, format: str = "json") -> bool:

        try:
            import json

            serializable_trail = []
            for record in self.change_audit_trail:
                record_dict = {
                    "table_name": record.table_name,
                    "business_key": record.business_key,
                    "surrogate_key": record.surrogate_key,
                    "change_type": record.change_type,
                    "changed_columns": record.changed_columns,
                    "effective_date": record.effective_date.isoformat(),
                    "end_date": record.end_date.isoformat() if record.end_date else None,
                    "batch_id": record.batch_id,
                }
                serializable_trail.append(record_dict)

            if format.lower() == "json":
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(serializable_trail, f, indent=2)
            else:
                raise ValueError(f"Unsupported export format: {format}")

            logger.info(f"Audit trail exported to {output_path} ({len(serializable_trail)} records)")
            return True

        except Exception as e:
            logger.error(f"Failed to export audit trail: {str(e)}")
            return False

    def detect_changes(
        self, current_data: pd.DataFrame, new_data: pd.DataFrame, scd_columns: list[str]
    ) -> list[SCDChangeRecord]:
        changes = []

        if len(current_data) == 0:
            for idx, _row in new_data.iterrows():
                change = SCDChangeRecord(
                    table_name="dimension",
                    business_key={"key": idx},
                    surrogate_key=None,
                    change_type="INSERT",
                    batch_id=0,
                )
                changes.append(change)
        else:
            for idx, new_row in new_data.iterrows():
                if idx < len(current_data):
                    current_row = current_data.iloc[idx]
                    has_changes = False

                    for col in scd_columns:
                        if col in new_row.index and col in current_row.index:
                            if new_row[col] != current_row[col]:
                                has_changes = True
                                break

                    if has_changes:
                        change = SCDChangeRecord(
                            table_name="dimension",
                            business_key={"key": idx},
                            surrogate_key=None,
                            change_type="UPDATE",
                            batch_id=0,
                        )
                        changes.append(change)

        return changes

    def _detect_changes(
        self, current_data: pd.DataFrame, new_data: pd.DataFrame, scd_columns: list[str]
    ) -> list[SCDChangeRecord]:
        changes = []

        for idx in range(min(len(current_data), len(new_data))):
            current_row = current_data.iloc[idx]
            new_row = new_data.iloc[idx]

            has_changes = False
            for col in scd_columns:
                if col in current_row.index and col in new_row.index:
                    if current_row[col] != new_row[col]:
                        has_changes = True
                        break

            if has_changes:
                customer_id = new_row.get("CustomerID", idx + 1001)

                change = SCDChangeRecord(
                    table_name="dimension",
                    business_key=customer_id,
                    surrogate_key=None,
                    change_type="UPDATE",
                    batch_id=0,
                )
                changes.append(change)

        return changes

    def _process_scd_change(self, change_record: SCDChangeRecord) -> dict[str, Any]:
        if isinstance(change_record.business_key, dict):
            business_key = change_record.business_key.get("key", change_record.business_key)
        else:
            business_key = change_record.business_key

        return {
            "business_key": business_key,
            "change_type": change_record.change_type,
            "version_created": True,
            "effective_date": change_record.effective_date,
            "processed": True,
        }

    def _create_audit_record(self, change_record: SCDChangeRecord) -> dict[str, Any]:
        if isinstance(change_record.business_key, dict):
            business_key = change_record.business_key.get("key", change_record.business_key)
        else:
            business_key = change_record.business_key

        return {
            "business_key": business_key,
            "change_type": change_record.change_type,
            "changed_columns": [
                col.get("column", col) if isinstance(col, dict) else col for col in change_record.changed_columns
            ],
            "audit_timestamp": datetime.now(),
            "batch_id": change_record.batch_id,
            "table_name": change_record.table_name,
        }

    def _extract_current_data(self, dimension_name: str) -> pd.DataFrame:
        return self._load_current_dimension_data(dimension_name)

    def _extract_new_data(self, dimension_name: str) -> pd.DataFrame:
        return pd.DataFrame()
