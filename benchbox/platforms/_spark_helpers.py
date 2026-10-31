from __future__ import annotations

import logging
import socket
from collections.abc import Callable, Mapping
from typing import Any

from benchbox.core.sql_utils import (
    extract_table_name as _extract_table_name,
    normalize_table_name_in_sql as _normalize_table_name_in_sql,
)
from benchbox.utils.sql_identifier import is_valid_sql_identifier

_SPARK_IDENTIFIER_MAX_LEN = 128

SPARK_AQE_KEYS = (
    "spark.sql.adaptive.enabled",
    "spark.sql.adaptive.coalescePartitions.enabled",
    "spark.sql.adaptive.skewJoin.enabled",
)

SPARK_CBO_KEYS = (
    "spark.sql.cbo.enabled",
    "spark.sql.cbo.joinReorder.enabled",
)


def spark_aqe_conf_entries(adaptive_enabled: bool) -> dict[str, str]:
    value = "true" if adaptive_enabled else "false"
    return dict.fromkeys(SPARK_AQE_KEYS, value)


SPARK_TUNED_SHUFFLE_PARTITIONS = 200


def spark_session_tuning_conf_entries(
    shuffle_partitions: int | None,
    adaptive_enabled: bool | None,
    *,
    tuning_enabled: bool,
) -> dict[str, str]:
    if shuffle_partitions is None and tuning_enabled:
        shuffle_partitions = SPARK_TUNED_SHUFFLE_PARTITIONS
    if adaptive_enabled is None and tuning_enabled:
        adaptive_enabled = True
    entries: dict[str, str] = {}
    if shuffle_partitions is not None:
        entries["spark.sql.shuffle.partitions"] = str(shuffle_partitions)
    if adaptive_enabled is not None:
        entries.update(spark_aqe_conf_entries(adaptive_enabled))
    return entries


def adaptive_enabled_from_config(config: Mapping[str, Any]) -> bool:
    options = config.get("options") or {}
    value = config.get("adaptive_enabled", options.get("adaptive_enabled", True))
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "on")
    return bool(value)


SPARK_DEFAULT_DRIVER_MEMORY_GIB = 4
SPARK_LOCAL_DRIVER_MEMORY_FRACTION = 0.6
SPARK_LOCAL_DRIVER_MEMORY_CAP_GIB = 31


def default_spark_driver_memory(master: str, total_memory_bytes: int | None = None) -> str:
    if not master.startswith("local"):
        return f"{SPARK_DEFAULT_DRIVER_MEMORY_GIB}g"
    if total_memory_bytes is None:
        try:
            import psutil

            total_memory_bytes = psutil.virtual_memory().total
        except Exception:
            return f"{SPARK_DEFAULT_DRIVER_MEMORY_GIB}g"
    scaled_gib = int(total_memory_bytes / (1024**3) * SPARK_LOCAL_DRIVER_MEMORY_FRACTION)
    heap_gib = max(SPARK_DEFAULT_DRIVER_MEMORY_GIB, min(SPARK_LOCAL_DRIVER_MEMORY_CAP_GIB, scaled_gib))
    return f"{heap_gib}g"


def apply_spark_olap_runtime_conf(
    connection: Any,
    benchmark_type: str,
    benchmark_types: tuple[str, ...] = ("olap", "analytics", "tpch", "tpcds", "joinorder"),
    *,
    adaptive_enabled: bool = True,
    spark_config: Mapping[str, str] | None = None,
    logger: logging.Logger | None = None,
    platform_label: str = "Spark",
) -> None:
    if benchmark_type.lower() not in tuple(t.lower() for t in benchmark_types):
        return
    overrides = spark_config or {}
    aqe_value = "true" if adaptive_enabled else "false"
    try:
        for key in (*SPARK_AQE_KEYS, *SPARK_CBO_KEYS):
            default = aqe_value if key in SPARK_AQE_KEYS else "true"
            connection.conf.set(key, overrides.get(key, default))
        if logger is not None:
            logger.debug("Applied OLAP optimizations for %s", platform_label)
    except Exception as exc:
        (logger or logging.getLogger(__name__)).warning("Failed to apply benchmark configuration: %s", exc)


def validate_spark_identifier(identifier: str) -> bool:
    return is_valid_sql_identifier(identifier, max_length=_SPARK_IDENTIFIER_MAX_LEN)


def extract_spark_table_name(statement: str) -> str | None:
    return _extract_table_name(statement)


def _unquote_spark_retry_identifier(identifier: str | None) -> str | None:
    if not identifier:
        return None
    identifier = identifier.strip()
    if len(identifier) >= 2 and identifier[0] == identifier[-1] and identifier[0] in {'"', "`"}:
        identifier = identifier[1:-1]
    return identifier


def normalize_spark_table_name_in_sql(sql: str) -> str:
    return _normalize_table_name_in_sql(sql)


def parse_spark_connect_endpoint(endpoint: str, *, default_port: int = 50051) -> tuple[str, int]:
    url = endpoint
    if url.startswith("sc://"):
        url = url[5:]
    host, _, port_str = url.partition(":")
    try:
        port = int(port_str) if port_str else default_port
    except ValueError:
        port = default_port
    return host or "localhost", port


def is_spark_connect_reachable(endpoint: str, *, timeout: float = 2.0) -> bool:
    host, port = parse_spark_connect_endpoint(endpoint)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def list_spark_tables(connection: Any) -> list[str]:
    try:
        return [t.name.lower() for t in connection.catalog.listTables()]
    except Exception:
        return []


def analyze_spark_table(connection: Any, table_name: str, *, logger: logging.Logger) -> None:
    if not validate_spark_identifier(table_name):
        logger.warning(f"Skipping ANALYZE: invalid table identifier '{table_name}'")
        return
    try:
        connection.sql(f"ANALYZE TABLE {table_name.lower()} COMPUTE STATISTICS")
        logger.debug(f"Analyzed table {table_name}")
    except Exception as e:
        logger.warning(f"Failed to analyze table {table_name}: {e}")


def get_spark_query_plan(
    connection: Any,
    query: str,
    *,
    logger: logging.Logger | None = None,
) -> str | None:
    log = logger or logging.getLogger(__name__)
    spark = connection
    from benchbox.platforms.base.sql_execution import join_explain_rows

    try:
        result_df = spark.sql(f"EXPLAIN EXTENDED {query}")
        plan_rows = result_df.collect()
        return join_explain_rows(plan_rows) or ""
    except Exception as e:
        log.warning("Could not get query plan via EXPLAIN: %s", e)
        return None


def spark_schema_statements(schema_sql: str, *, table_format: str, strip_constraints: bool) -> list[str]:
    from benchbox.utils.dialect_utils import lift_ddl_table_layouts, schema_ddl_read_dialect, translate_ddl

    fmt = (table_format or "parquet").lower()
    options: dict[str, Any] = {"default_layout": {"format": fmt}}
    if strip_constraints:
        options.update(
            strip_primary_keys=True,
            strip_foreign_keys=True,
            strip_unique=True,
            type_map={"SMALLINT": "INT"},
        )
    read = schema_ddl_read_dialect(schema_sql, "spark")
    schema_sql, layouts = lift_ddl_table_layouts(schema_sql, read, fmt)
    if layouts:
        options["layouts"] = layouts
    return translate_ddl(schema_sql, "spark", read, **options)


def spark_format_strips_constraints(table_format: str) -> bool:
    return (table_format or "parquet").lower() in {"parquet", "orc"}


def purge_orphaned_warehouse_directory(spark: Any, *, logger: logging.Logger) -> None:
    try:
        existing = list(spark.catalog.listTables())
    except Exception as exc:
        logger.debug(f"Warehouse purge skipped (catalog probe failed): {exc}")
        return
    if existing:
        return
    try:
        rows = spark.sql("SELECT current_database()").collect()
        if not rows:
            return
        current_db = rows[0][0]
        if not validate_spark_identifier(current_db):
            return
        spark.sql(f"DROP DATABASE IF EXISTS `{current_db}` CASCADE")
        spark.sql(f"CREATE DATABASE IF NOT EXISTS `{current_db}`")
        spark.sql(f"USE `{current_db}`")
        logger.info(f"Purged potentially-orphaned warehouse directory for database '{current_db}'")
    except Exception as exc:
        logger.debug(f"Warehouse purge aborted mid-cycle: {exc}")


class SparkLikeAdapterMixin:
    ledger_execute_verbs = ("sql",)
    handles_spark_session_config = True

    def capture_effective_session_config(self, connection: Any, snapshot: str) -> None:
        from benchbox.platforms.spark_effective_config import (
            SNAPSHOT_AFTER_CONFIGURATION,
            SNAPSHOT_SESSION_CREATED,
            EffectiveSparkConfigCapture,
            external_spark_conf,
        )

        capture = getattr(self, "_effective_spark_config", None)
        if capture is None or snapshot == SNAPSHOT_SESSION_CREATED:
            capture = EffectiveSparkConfigCapture(engine=str(self.platform_name))
            self._effective_spark_config = capture
        if snapshot == SNAPSHOT_SESSION_CREATED:
            get_conf = getattr(self, "_get_spark_conf", None)
            try:
                builder_conf = get_conf() if callable(get_conf) else {}
            except Exception:
                builder_conf = {}
            capture.builder_conf = {str(key): str(value) for key, value in builder_conf.items() if value is not None}
            capture.external_conf = external_spark_conf()
        if snapshot == SNAPSHOT_AFTER_CONFIGURATION:
            capture.requested_conf = requested_tuned_spark_map(self)
        capture.take_snapshot(snapshot, connection)

    def effective_session_config_payload(self) -> dict[str, Any] | None:
        capture = getattr(self, "_effective_spark_config", None)
        return capture.payload() if capture is not None else None

    def effective_session_config_report(self) -> list[str]:
        capture = getattr(self, "_effective_spark_config", None)
        return capture.console_lines() if capture is not None else []

    def apply_constraint_configuration(
        self,
        primary_key_config: Any,
        foreign_key_config: Any,
        connection: Any,
    ) -> None:
        platform = self.platform_name
        if primary_key_config and primary_key_config.enabled:
            self.logger.info(f"Primary key constraints enabled for {platform} (informational only, not enforced)")
        if foreign_key_config and foreign_key_config.enabled:
            self.logger.info(f"Foreign key constraints enabled for {platform} (informational only, not enforced)")

    def apply_unified_tuning(self, unified_config: Any, connection: Any) -> None:
        from benchbox.platforms.base.tuning_config import apply_standard_unified_tuning

        apply_standard_unified_tuning(self, unified_config, connection)

    def apply_olap_runtime_conf(self, connection: Any, benchmark_type: str, platform_label: str) -> None:
        apply_spark_olap_runtime_conf(
            connection,
            benchmark_type,
            adaptive_enabled=self.adaptive_enabled if self.adaptive_enabled is not None else True,
            spark_config=self.spark_config,
            logger=self.logger,
            platform_label=platform_label,
        )

    def apply_platform_optimizations(self, platform_config: Any, connection: Any) -> None:
        if not platform_config:
            return

    def record_harness_spark_conf(self, connection: Any, keys: tuple[str, ...]) -> None:
        ledger = getattr(self, "_applied_tuning_ledger", None)
        if ledger is None:
            return
        try:
            from benchbox.core.tuning.applied_ledger import HARNESS_DEFAULT

            for key in keys:
                try:
                    value = connection.conf.get(key, None)
                except Exception:
                    continue
                if value is None:
                    continue
                recorded_value = (
                    "<redacted>"
                    if key
                    in {
                        "spark.app.name",
                        "spark.sql.warehouse.dir",
                        "spark.jars",
                        "spark.driver.extraClassPath",
                        "spark.executor.extraClassPath",
                    }
                    else value
                )
                self._record_spark_conf_ledger(
                    ledger,
                    key,
                    recorded_value,
                    applied=True,
                    status_override=HARNESS_DEFAULT,
                    mechanism="harness_required",
                )
        except Exception:
            pass

    @staticmethod
    def _record_spark_conf_ledger(
        ledger: Any,
        key: str,
        value: Any,
        *,
        applied: bool,
        error: Any = None,
        status_override: str | None = None,
        mechanism: str = "spark_session_config",
    ) -> None:
        if ledger is None:
            return
        try:
            from benchbox.core.tuning.applied_ledger import EXECUTED, PHASE_SESSION, STATEMENT_FAILED

            recorded = key if key.startswith("spark.") else f"spark.{key}"
            ledger.record(
                f"SET {recorded}={value}",
                PHASE_SESSION,
                status=status_override or (EXECUTED if applied else STATEMENT_FAILED),
                mechanism=mechanism,
                error=error,
            )
        except Exception:
            pass


def _effective_tuning_configuration(adapter: Any) -> Any:
    getter = getattr(adapter, "get_effective_tuning_configuration", None)
    return getter() if callable(getter) else None


def _tuned_spark_map(adapter: Any, config: Any) -> dict[str, str]:
    if not getattr(adapter, "tuning_enabled", False) or config is None:
        return {}
    return dict(getattr(config.platform_optimizations, "spark", None) or {})


def requested_tuned_spark_map(adapter: Any) -> dict[str, str]:
    spark_map = _tuned_spark_map(adapter, _effective_tuning_configuration(adapter))
    return {(key if key.startswith("spark.") else f"spark.{key}"): str(value) for key, value in spark_map.items()}


LAKESAIL_SPARK_MAP_DROP_REASON = (
    "LakeSail accepts spark.conf.set for keys it ignores and BenchBox has no verified list of the keys it "
    "honours, so the setting is not applied"
)


def drop_tuned_spark_session_config(adapter: Any, reason: str) -> list[str]:
    requested = requested_tuned_spark_map(adapter)
    if not requested:
        return []
    ledger = getattr(adapter, "_applied_tuning_ledger", None)
    keys = sorted(requested)
    adapter.logger.warning(f"Tuned session config not applied on {adapter.platform_name} ({', '.join(keys)}): {reason}")
    if ledger is not None:
        for key in keys:
            ledger.record_dropped(f"spark_session_config:{key}", reason)
    return keys


def apply_tuned_spark_session_config(adapter: Any, connection: Any) -> dict[str, str]:
    config = _effective_tuning_configuration(adapter)
    spark_map = _tuned_spark_map(adapter, config)
    if getattr(adapter, "_tuned_session_config_id", None) == id(config) and spark_map:
        return dict(getattr(adapter, "_tuned_session_applied", {}) or {})
    adapter._tuned_session_config_id = id(config)
    adapter._tuned_session_applied = {}
    if not spark_map:
        return {}
    spark = connection
    conf = spark.conf
    ledger = getattr(adapter, "_applied_tuning_ledger", None)
    explicit = _explicit_spark_option_keys(adapter)
    applied: dict[str, str] = {}
    for key, value in spark_map.items():
        conf_key = key if key.startswith("spark.") else f"spark.{key}"
        text = str(value)
        if conf_key in explicit and explicit[conf_key].lower() != text.lower():
            adapter.logger.warning(
                f"Tuned session config {conf_key}={text} overrides explicit adapter option "
                f"({explicit[conf_key]}); the tuning file wins"
            )
        try:
            is_modifiable = conf.isModifiable(conf_key) if hasattr(conf, "isModifiable") else True
        except Exception:
            is_modifiable = True
        if not is_modifiable:
            error = RuntimeError(f"engine refused session key {conf_key} (not modifiable)")
            adapter.logger.warning(f"Tuned session config {conf_key} was refused by the engine: {error}")
            SparkLikeAdapterMixin._record_spark_conf_ledger(ledger, conf_key, text, applied=False, error=error)
            continue
        try:
            conf.set(conf_key, text)
            actual = conf.get(conf_key, None)
        except Exception as exc:
            adapter.logger.warning(f"Tuned session config {conf_key} failed to apply: {exc}")
            SparkLikeAdapterMixin._record_spark_conf_ledger(ledger, conf_key, text, applied=False, error=exc)
            continue
        if actual is None or str(actual).lower() != text.lower():
            error = RuntimeError(f"session key {conf_key} reads back as {actual!r}, expected {text!r}")
            adapter.logger.warning(f"Tuned session config {conf_key} did not take effect: {error}")
            SparkLikeAdapterMixin._record_spark_conf_ledger(ledger, conf_key, text, applied=False, error=error)
            continue
        SparkLikeAdapterMixin._record_spark_conf_ledger(ledger, conf_key, text, applied=True)
        applied[conf_key] = text
    adapter._tuned_session_applied = applied
    return dict(applied)


def _explicit_spark_option_keys(adapter: Any) -> dict[str, str]:
    explicit: dict[str, str] = {}
    raw_options = getattr(adapter, "spark_config", None) or {}
    for key, value in raw_options.items():
        qualified = key if str(key).startswith("spark.") else f"spark.{key}"
        explicit[qualified] = str(value)
    shuffle_partitions = getattr(adapter, "shuffle_partitions", None)
    if shuffle_partitions is not None:
        explicit["spark.sql.shuffle.partitions"] = str(shuffle_partitions)
    adaptive_enabled = getattr(adapter, "adaptive_enabled", None)
    if adaptive_enabled is not None:
        adaptive_text = "true" if bool(adaptive_enabled) else "false"
        for aqe_key in SPARK_AQE_KEYS:
            explicit[aqe_key] = adaptive_text
    return explicit


def _run_schema_statement(spark: Any, statement: str) -> None:
    result = spark.sql(statement)
    collect = getattr(result, "collect", None)
    if callable(collect):
        collect()


def run_spark_schema_creation_loop(
    spark: Any,
    statements: list[str],
    *,
    logger: logging.Logger,
    on_pre_loop: Callable[[Any], None] | None = None,
    on_location_collision: Callable[[Any, str], None] | None = None,
) -> None:
    if on_pre_loop is not None:
        on_pre_loop(spark)

    for statement in statements:
        statement = statement.strip() if statement else ""
        if not statement:
            continue
        statement = normalize_spark_table_name_in_sql(statement)
        try:
            _run_schema_statement(spark, statement)
            logger.debug(f"Executed schema statement: {statement[:100]}...")
        except Exception as exc:
            error_lower = str(exc).lower()
            if "already exists" not in error_lower:
                raise

            extracted_table_name = extract_spark_table_name(statement)
            table_name = _unquote_spark_retry_identifier(extracted_table_name)
            if not (table_name and validate_spark_identifier(table_name)):
                raise RuntimeError(
                    "Cannot safely retry CREATE: extracted table name "
                    f"{extracted_table_name!r} from statement {statement[:80]!r} failed "
                    "strict-ASCII identifier validation. Either rename the "
                    "table to match ^[a-zA-Z_][a-zA-Z0-9_]*$ (<=128 chars) or "
                    "drop the conflicting object manually."
                ) from exc

            _run_schema_statement(spark, f"DROP TABLE IF EXISTS {table_name}")
            if on_location_collision is not None and "location_already_exists" in error_lower:
                on_location_collision(spark, table_name)
            _run_schema_statement(spark, statement)
