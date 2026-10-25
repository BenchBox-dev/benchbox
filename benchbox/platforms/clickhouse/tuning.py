from __future__ import annotations

from typing import TYPE_CHECKING, Any

from benchbox.core.exceptions import ConfigurationError
from benchbox.core.tuning.applied_ledger import harness_connection
from benchbox.platforms.base.tuning import make_informational_constraint_applier, supports_named_tuning_type
from benchbox.utils.input_validation import validate_sql_identifier

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        PlatformOptimizationConfiguration,
        UnifiedTuningConfiguration,
    )


class ClickHouseTuningMixin:
    physical_identifier_case = "lower"
    configure_session_is_tuning = True

    def _catalog_names(self, connection: Any, sql: str) -> list[str] | None:
        target = connection if connection is not None else getattr(self, "connection", None)
        if target is None:
            return None
        try:
            result = target.execute(sql)
            return [row[0] for row in list(result)] if result is not None else None
        except Exception:
            return None

    @staticmethod
    def _match_catalog_name(names: list[str] | None, logical: str) -> str | None:
        if not names:
            return None
        if logical in names:
            return logical
        matches = sorted(name for name in names if name.lower() == logical.lower())
        return matches[0] if matches else None

    def resolve_physical_table(self, logical_name: str, connection: Any = None) -> str:
        names = self._catalog_names(connection, "SELECT name FROM system.tables WHERE database = currentDatabase()")
        found = self._match_catalog_name(names, logical_name)
        return found if found is not None else super().resolve_physical_table(logical_name, connection)

    def resolve_physical_column(self, table_name: str, logical_column: str, connection: Any = None) -> str:
        try:
            table = validate_sql_identifier(table_name, "table name")
        except Exception:
            table = None
        if table is not None:
            names = self._catalog_names(
                connection,
                f"SELECT name FROM system.columns WHERE database = currentDatabase() AND table = '{table}'",
            )
            found = self._match_catalog_name(names, logical_column)
            if found is not None:
                return found
        return super().resolve_physical_column(table_name, logical_column, connection)

    @staticmethod
    def _has_tuned_sort_key(config: UnifiedTuningConfiguration) -> bool:
        from benchbox.core.tuning.generators.clickhouse import clickhouse_sort_key_columns

        return any(clickhouse_sort_key_columns(table_tuning) for table_tuning in config.table_tunings.values())

    def get_effective_tuning_configuration(
        self,
    ) -> UnifiedTuningConfiguration | None:
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration

        base_config = super().get_effective_tuning_configuration()
        if base_config:
            tuned_sort_key = getattr(self, "tuning_enabled", False) and self._has_tuned_sort_key(base_config)
            if not tuned_sort_key:
                base_config.primary_keys.enabled = True
            return base_config

        config = UnifiedTuningConfiguration()
        config.primary_keys.enabled = True
        config.foreign_keys.enabled = False

        return config

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:

        settings = {
            "max_memory_usage": self._parse_memory_setting(self.max_memory_usage),
            "max_execution_time": self.max_execution_time,
            "max_threads": self.max_threads,
            "use_uncompressed_cache": 0,
            "enable_optimize_predicate_expression": 1,
            "allow_experimental_correlated_subqueries": 1,
            "join_use_nulls": 1,
        }

        if self.disable_result_cache:
            settings.update(
                {
                    "use_query_cache": 0,
                    "enable_writes_to_query_cache": 0,
                    "enable_reads_from_query_cache": 0,
                }
            )

        tuned_settings: dict[str, Any] = {}
        if self.tuning_enabled and benchmark_type.lower() in [
            "olap",
            "analytics",
            "tpch",
            "tpcds",
        ]:
            tuned_settings = {
                "max_bytes_in_join": int(self._parse_memory_setting(self.max_memory_usage) * 0.5),
                "optimize_aggregation_in_order": 1,
                "group_by_two_level_threshold": 100000,
                "max_bytes_before_external_group_by": int(self._parse_memory_setting(self.max_memory_usage) * 0.5),
                "max_bytes_before_external_sort": int(self._parse_memory_setting(self.max_memory_usage) * 0.5),
            }

        critical_failures = []
        harness = harness_connection(connection)
        for setting, value in settings.items():
            success = self._apply_setting_with_validation(harness, setting, value)
            if not success and setting in [
                "use_query_cache",
                "enable_writes_to_query_cache",
                "enable_reads_from_query_cache",
            ]:
                critical_failures.append(setting)
        for setting, value in tuned_settings.items():
            self._apply_setting_with_validation(connection, setting, value)

        if self.disable_result_cache or critical_failures:
            self.logger.debug("Validating cache control settings...")
            validation_result = self.validate_session_cache_control(connection)

            if not validation_result["validated"]:
                self.logger.warning(f"Cache control validation failed: {validation_result.get('errors', [])}")
            else:
                self.logger.info(
                    f"Cache control validated successfully: cache_disabled={validation_result['cache_disabled']}"
                )

    def validate_session_cache_control(self, connection: Any) -> dict[str, Any]:
        result = {
            "validated": False,
            "cache_disabled": False,
            "settings": {},
            "warnings": [],
            "errors": [],
        }

        try:
            query = """
                SELECT name, value
                FROM system.settings
                WHERE name IN ('use_query_cache', 'enable_writes_to_query_cache', 'enable_reads_from_query_cache')
                ORDER BY name
            """
            rows = connection.execute(query)

            for row in rows:
                setting_name = row[0]
                setting_value = str(row[1])
                result["settings"][setting_name] = setting_value

            expected_cache_value = "0" if self.disable_result_cache else "1"

            cache_settings = ["use_query_cache", "enable_writes_to_query_cache", "enable_reads_from_query_cache"]
            all_validated = True

            for setting_name in cache_settings:
                actual_value = result["settings"].get(setting_name, "unknown")

                if actual_value != expected_cache_value:
                    all_validated = False
                    error_msg = (
                        f"Cache control validation failed for {setting_name}: "
                        f"expected {expected_cache_value}, got {actual_value}"
                    )
                    result["errors"].append(error_msg)
                    self.logger.error(error_msg)

            if all_validated:
                result["validated"] = True
                result["cache_disabled"] = expected_cache_value == "0"
                self.logger.debug(
                    f"Cache control validated: all cache settings={expected_cache_value} (expected {expected_cache_value})"
                )
            else:
                if self.strict_validation:
                    raise ConfigurationError(
                        "ClickHouse session cache control validation failed - "
                        "benchmark results may be incorrect due to cached query results",
                        details=result,
                    )

        except Exception as e:
            if isinstance(e, ConfigurationError):
                raise

            error_msg = f"Validation query failed: {e}"
            result["errors"].append(error_msg)
            self.logger.error(f"Cache control validation error: {e}")

            if self.strict_validation:
                raise ConfigurationError(
                    "Failed to validate ClickHouse cache control settings",
                    details={"original_error": str(e), "validation_result": result},
                ) from e

        return result

    def _parse_memory_setting(self, memory_str: str) -> int:
        if isinstance(memory_str, int):
            return memory_str

        memory_str = memory_str
        if memory_str.endswith("GB"):
            return int(float(memory_str[:-2]) * 1024 * 1024 * 1024)
        elif memory_str.endswith("MB"):
            return int(float(memory_str[:-2]) * 1024 * 1024)
        elif memory_str.endswith("KB"):
            return int(float(memory_str[:-2]) * 1024)
        else:
            return int(memory_str)

    def _apply_setting_with_validation(self, connection: Any, setting: str, value: Any) -> bool:
        local_incompatible_settings = {
            "join_algorithm",
            "enable_multiple_joins_emulation",
        }

        if self.deployment_mode == "local" and setting in local_incompatible_settings:
            self.logger.debug(f"Skipping {setting} in local mode (known incompatible)")
            return False

        try:
            setting_value = self._format_setting_value(value)
            connection.execute(f"SET {setting} = {setting_value}")
            self.logger.debug(f"Set {setting} = {value}")
            return True
        except Exception as e:
            if self.deployment_mode == "local" and setting in local_incompatible_settings:
                self.logger.debug(f"Setting {setting} not available in local mode: {e}")
            else:
                self.logger.warning(f"Failed to set {setting}: {e}")
            return False

    def _format_setting_value(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
            return value
        return "'" + value.replace("'", "''") + "'"

    _supported_tuning_type_names = ("PARTITIONING", "SORTING", "CLUSTERING", "DISTRIBUTION")

    def supports_tuning_type(self, tuning_type) -> bool:
        return supports_named_tuning_type(tuning_type, self._supported_tuning_type_names)

    def _optimize_after_load_enabled(self) -> bool:
        value = getattr(self, "platform_config", {}).get("optimize_after_load", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def apply_post_load_tunings(self, table_name: str, effective_config: Any, connection: Any) -> bool:
        if not getattr(self, "tuning_enabled", False) or not self._optimize_after_load_enabled():
            return False
        if not self.optimize_table(connection, self.resolve_physical_table(table_name, connection)):
            self.note_post_load_maintenance_failure()
        return True

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        if not table_tuning or not table_tuning.has_any_tuning():
            return

        table_name = table_tuning.table_name
        self.logger.info(f"Applying ClickHouse tunings for table: {table_name}")

        try:
            from benchbox.core.tuning.interface import TuningType

            sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
            if sort_columns:
                column_names = [col.name for col in sorted(sort_columns, key=lambda col: col.order)]
                self.logger.info(
                    f"Sorting for table {table_name}: {', '.join(column_names)} (defined at CREATE TABLE time)"
                )

            cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
            if cluster_columns:
                column_names = [col.name for col in sorted(cluster_columns, key=lambda col: col.order)]
                self.logger.info(
                    f"Clustering for table {table_name}: {', '.join(column_names)} (folded into ORDER BY at CREATE TABLE time)"
                )

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns:
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                self.logger.info(
                    f"Partitioning strategy for table {table_name}: {', '.join(column_names)} (defined at CREATE TABLE time)"
                )

            distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
            if distribution_columns:
                sorted_cols = sorted(distribution_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                self.logger.info(
                    f"Distribution strategy for table {table_name}: {', '.join(column_names)} (handled by ClickHouse engine settings)"
                )

        except ImportError:
            self.logger.warning("Tuning interface not available - skipping tuning application")
        except Exception as e:
            raise ValueError(f"Failed to apply tunings to ClickHouse table {table_name}: {e}") from e

    def apply_unified_tuning(self, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
        from benchbox.platforms.base.tuning_config import apply_standard_unified_tuning

        apply_standard_unified_tuning(self, unified_config, connection)

    def apply_platform_optimizations(self, platform_config: PlatformOptimizationConfiguration, connection: Any) -> None:
        if not platform_config:
            return
        self.logger.debug("No ClickHouse-specific platform optimizations to apply")

    apply_constraint_configuration = make_informational_constraint_applier(
        "Primary key constraints enabled for ClickHouse (applied during table creation)",
        "Foreign key constraints enabled for ClickHouse (applied during table creation)",
    )


__all__ = ["ClickHouseTuningMixin"]
