from __future__ import annotations

import logging
import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchbox.core.execution_engine import ExecutionEngineHook
from benchbox.core.loaded_tables import is_data_loading_skipped, require_loaded_tables
from benchbox.core.results.query_plan_models import DEFAULT_PLAN_MAX_DEPTH
from benchbox.core.results.schema import compute_plan_capture_stats
from benchbox.core.throughput.containment import await_quiescence
from benchbox.core.tuning.applied_ledger import (
    PHASE_DDL,
    PHASE_SESSION,
    AppliedTuningLedger,
    is_schema_tuning_statement,
    recording_connection,
)
from benchbox.core.tuning.introspection import Introspector
from benchbox.platforms.base import tuning_trust
from benchbox.platforms.base.client_region import discover_client_region
from benchbox.platforms.base.connection_lifecycle import ConnectionLifecycleMixin
from benchbox.platforms.base.connection_wrappers import (
    DriverIsolationCapability,
    PlatformAdapterConnection,  # noqa: F401
    PlatformAdapterCursor,  # noqa: F401
    StreamConnectionCapability,
    _make_stream_cursor,
    _NoCloseProxy,  # noqa: F401
    check_isolation_capability,  # noqa: F401
    require_throughput_stream_capability,  # noqa: F401
    resolve_stream_connection_capability,  # noqa: F401
)
from benchbox.platforms.base.data_loading import SchemaHelpersMixin
from benchbox.platforms.base.dialect_translation import DialectTranslationMixin
from benchbox.platforms.base.execution import TestDriversMixin
from benchbox.platforms.base.link_probe import probe_statement_overhead
from benchbox.platforms.base.models import (
    SetupPhase,
    StatisticsGatheringPhase,
)
from benchbox.platforms.base.phase_tracking import (
    PhaseTrackingMixin,
    _extract_table_names,  # noqa: F401
    _resolve_benchmark_table_names,
)
from benchbox.platforms.base.result_capture import ResultCaptureMixin
from benchbox.platforms.base.sorted_ingestion import SortedIngestionMixin
from benchbox.platforms.base.tuning import TuningHooksMixin
from benchbox.platforms.base.tuning_config import TuningConfigMixin
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.printing import quiet_console
from benchbox.utils.toggles import is_probe_requested
from benchbox.utils.verbosity import VerbosityMixin, VerbositySettings

try:
    from benchbox.core.results.models import (
        BenchmarkResults,
        ExecutionPhases,
        QueryDefinition,
    )
except ImportError:
    BenchmarkResults = None
    ExecutionPhases = None
    QueryDefinition = None

try:
    from benchbox.core.tuning.interface import (
        ForeignKeyConfiguration,
        PlatformOptimizationConfiguration,
        PrimaryKeyConfiguration,
    )
except ImportError:
    PrimaryKeyConfiguration = None
    ForeignKeyConfiguration = None
    PlatformOptimizationConfiguration = None

try:
    from benchbox.core.validation import ValidationResult
except ImportError:
    ValidationResult = None

EnhancedBenchmarkResults = BenchmarkResults


def exclude_probe_wall_time(total_seconds: float, probe_seconds: float) -> float:
    return max(0.0, total_seconds - probe_seconds)


class _ManifestStreamCapability:
    def __get__(self, instance: Any, owner: type) -> StreamConnectionCapability:
        capability, _declared = resolve_stream_connection_capability(owner if instance is None else type(instance))
        return capability


class PlatformAdapter(
    ExecutionEngineHook,
    ConnectionLifecycleMixin,
    DialectTranslationMixin,
    PhaseTrackingMixin,
    ResultCaptureMixin,
    SchemaHelpersMixin,
    SortedIngestionMixin,
    TestDriversMixin,
    TuningConfigMixin,
    TuningHooksMixin,
    VerbosityMixin,
    ABC,
):
    driver_isolation_capability: DriverIsolationCapability = DriverIsolationCapability.NOT_APPLICABLE
    stream_connection_capability = _ManifestStreamCapability()
    default_service_port: int | None = None
    supports_external_tables: bool = False
    plan_capture_phase_eligible: bool = True
    deployment_selected: str | None = None
    deployment_selected_class: str | None = None

    def __init__(self, **config):
        self.platform_config = config
        self.connection = None
        self.connection_pool = None
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
        self._dialect = self.get_target_dialect()
        self.force_recreate = config.get("force_recreate", False)
        self.show_query_plans = config.get("show_query_plans", False)
        self.capture_plans = config.get("capture_plans", False)
        self.analyze_plans: bool = config.get("analyze_plans", False)
        self.strict_plan_capture = config.get("strict_plan_capture", False)
        self.normalize_plan_literals = config.get("normalize_plan_literals", False)
        self.plan_capture_timeout_seconds = int(config.get("plan_capture_timeout_seconds", 30))
        self.plan_max_depth = int(config.get("plan_max_depth", DEFAULT_PLAN_MAX_DEPTH))
        plan_queries_str = config.get("plan_queries")
        self.plan_query_filter: set[str] | None = (
            {q.strip() for q in plan_queries_str.split(",") if q.strip()} if plan_queries_str else None
        )
        self._plan_capture_lock = threading.Lock()
        self._plan_capture_phase_active: bool = False
        self._phase_recorded_queries: dict[str, str] = {}
        self.tuning_enabled = config.get("tuning_enabled", False)
        self.driver_package = config.get("driver_package")
        self.driver_version_requested = config.get("driver_version") or config.get("driver_version_requested")
        self.driver_version_resolved = config.get("driver_version_resolved")
        self.driver_version_actual = config.get("driver_version_actual")
        self.driver_runtime_strategy = config.get("driver_runtime_strategy")
        self.driver_runtime_path = config.get("driver_runtime_path")
        self.driver_runtime_python_executable = config.get("driver_runtime_python_executable")
        self.driver_auto_install_used = bool(config.get("driver_auto_install_used", False))

        _legacy_tuning_config = config.get("unified_tuning_configuration")
        if isinstance(_legacy_tuning_config, dict):
            _legacy_tuning_config = None
        self.unified_tuning_configuration = config.get("tuning_config") or _legacy_tuning_config
        self.tuning_source: str | None = config.get("tuning_source")
        self.tuning_source_file: str | None = config.get("tuning_source_file")
        self._applied_tuning_ledger: AppliedTuningLedger | None = None
        self._tuning_marker_save_failed = False

        self.apply_verbosity(VerbositySettings.from_mapping(config))

        self.database_was_reused = False

        self.table_mode: str = "native"
        self.external_format: str | None = None
        self.requested_table_format: str | None = None

        self._initial_dry_run = bool(config.get("dry_run", False))
        self.dry_run = self._initial_dry_run
        self.dry_run_mode = False
        self.captured_sql = []
        self.query_counter = 0

        self.enable_validation = config.get("enable_validation", False)

        self._last_throughput_test_result = None
        self._last_power_workload_timing: tuple[str, str, int] | None = None
        self._last_per_table_timings: dict[str, Any] | None = None
        self._sorted_ingestion_applied_tables: list[str] = []
        self._sorted_ingestion_total_apply_seconds: float = 0.0
        self._post_load_maintenance_seconds: float = 0.0
        self._post_load_maintenance_tables: list[str] = []
        self._post_load_maintenance_by_table: dict[str, float] = {}
        self._post_load_maintenance_errors: int = 0
        self._reset_plan_capture_stats()
        self._client_link_metadata: dict[str, Any] | None = None
        self._link_probe_timed_out = False
        self._post_measurement_contained = False
        self._contained_throughput_result: Any = None

    def _reset_run_scoped_state(self) -> None:
        self.database_was_reused = False
        self._existing_db_decided = False
        self._last_power_test_result = None
        self._last_throughput_test_result = None
        self._last_power_workload_timing = None
        self._last_per_table_timings: dict[str, Any] | None = None
        self._sorted_ingestion_applied_tables = []
        self._sorted_ingestion_total_apply_seconds = 0.0
        self._post_load_maintenance_seconds = 0.0
        self._post_load_maintenance_tables = []
        self._post_load_maintenance_by_table = {}
        self._post_load_maintenance_errors = 0
        self._reset_plan_capture_stats()
        self._client_link_metadata = None
        self._link_probe_timed_out = False
        self._post_measurement_contained = False
        self._contained_throughput_result = None
        if self.dry_run_mode:
            self.captured_sql = []
            self.query_counter = 0

    @staticmethod
    @abstractmethod
    def add_cli_arguments(parser) -> None:

        pass

    @classmethod
    @abstractmethod
    def from_config(cls, config: dict[str, Any]):

        pass

    @property
    def is_dry_run(self) -> bool:
        return bool(getattr(self, "dry_run", False) or getattr(self, "dry_run_mode", False))

    @property
    def platform_name(self) -> str:
        return self.__class__.__name__

    @property
    def canonical_platform_type(self) -> str:
        platform_config = getattr(self, "platform_config", None)
        config_type = platform_config.get("type") if isinstance(platform_config, dict) else None
        if config_type:
            return str(config_type).strip().lower()
        return self.platform_name.strip().lower().replace(" ", "-")

    def get_tuning_introspector(self) -> Introspector | None:
        return None

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = {
            "platform_type": self.platform_name.lower(),
            "platform_name": self.platform_name,
            "platform_version": "unknown",
            "connection_mode": "unknown",
            "host": None,
            "port": None,
            "configuration": {},
            "client_library_version": None,
            "embedded_library_version": None,
        }
        if self.driver_package:
            platform_info["driver_package"] = self.driver_package
        if self.driver_version_requested:
            platform_info["driver_version_requested"] = self.driver_version_requested
        if self.driver_version_resolved:
            platform_info["driver_version_resolved"] = self.driver_version_resolved
        if self.driver_version_actual:
            platform_info["driver_version_actual"] = self.driver_version_actual
        if self.driver_runtime_strategy:
            platform_info["driver_runtime_strategy"] = self.driver_runtime_strategy
        return platform_info

    @abstractmethod
    def create_connection(self, **connection_config) -> Any:

        pass

    @abstractmethod
    def create_schema(self, benchmark, connection: Any) -> float:

        pass

    @abstractmethod
    def apply_platform_optimizations(self, platform_config: PlatformOptimizationConfiguration, connection: Any) -> None:

        pass

    @abstractmethod
    def apply_constraint_configuration(
        self,
        primary_key_config: PrimaryKeyConfiguration,
        foreign_key_config: ForeignKeyConfiguration,
        connection: Any,
    ) -> None:

        pass

    @abstractmethod
    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:

        pass

    def materialize_schema_only_tables(self, benchmark, connection: Any) -> dict[str, int]:
        self.logger.debug(f"materialize_schema_only_tables not implemented for {self.__class__.__name__}")
        return {}

    def create_external_tables(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        raise NotImplementedError(f"{self.platform_name} does not support external table mode")

    def upload_manifest(self, manifest_path: Path, remote_path: str) -> bool:
        self.logger.debug(f"upload_manifest not implemented for {self.__class__.__name__} (remote_path={remote_path})")
        return False

    @abstractmethod
    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:

        pass

    def gather_statistics(self, connection: Any, table_names: list[str]) -> tuple[str, int]:
        analyze_tables = getattr(self, "analyze_tables", None)
        if callable(analyze_tables):
            analyze_tables(connection)
            return "explicit", len(table_names)
        analyze_table = getattr(self, "analyze_table", None)
        if callable(analyze_table):
            for table_name in table_names:
                analyze_table(connection, table_name)
            return "explicit", len(table_names)
        return "unsupported", 0

    def reset_statistics(self, connection: Any, table_names: list[str]) -> str:
        self.logger.debug(
            f"reset_statistics not implemented for {self.__class__.__name__}; "
            "no generic drop-stats primitive, deferring to the gather_statistics rebuild"
        )
        return "unsupported"

    def run_statistics_phase(
        self,
        benchmark: Any,
        connection: Any,
        *,
        benchmark_name: str = "",
        table_names: list[str] | None = None,
        reset: bool | None = None,
        collect_per_table_timing: bool = False,
    ) -> StatisticsGatheringPhase | None:
        from benchbox.core.benchmark_registry import get_benchmark_metadata

        slug = (benchmark_name or "").lower()
        metadata = get_benchmark_metadata(slug) if slug else None
        if not (metadata or {}).get("supports_statistics_phase"):
            quiet_console.print(
                f"⏭️  Statistics phase requested but benchmark '{slug or 'unknown'}' has not opted in; "
                "skipping (load keeps legacy statistics semantics)"
            )
            return None

        names = table_names or _resolve_benchmark_table_names(benchmark)
        quiet_console.print("Gathering optimizer statistics...")
        start_time = mono_time()

        stats_lifecycle: str | None = None
        if reset:
            try:
                stats_lifecycle = self.reset_statistics(connection, names) or "unsupported"
            except Exception as exc:
                self.logger.warning(f"Statistics reset failed, continuing with rebuild: {exc}")
                stats_lifecycle = "unsupported"
        elif reset is False:
            stats_lifecycle = "persist"

        per_table_ms: dict[str, int] | None = None
        use_per_table_loop = (
            collect_per_table_timing
            and type(self).gather_statistics is PlatformAdapter.gather_statistics
            and callable(getattr(self, "analyze_table", None))
            and not callable(getattr(self, "analyze_tables", None))
        )
        try:
            if use_per_table_loop:
                per_table_ms = {}
                analyze_table = self.analyze_table
                for table_name in names:
                    table_start = mono_time()
                    analyze_table(connection, table_name)
                    per_table_ms[table_name] = int(elapsed_seconds(table_start) * 1000)
                stats_mode, tables_analyzed = "explicit", len(names)
            else:
                stats_mode, tables_analyzed = self.gather_statistics(connection, names)
        except Exception as exc:
            self.logger.warning(f"Statistics phase failed: {exc}")
            return StatisticsGatheringPhase(
                duration_ms=int(elapsed_seconds(start_time) * 1000),
                status="FAILED",
                stats_mode="explicit",
                tables_analyzed=0,
                error_message=str(exc),
                stats_lifecycle=stats_lifecycle,
            )
        return StatisticsGatheringPhase(
            duration_ms=int(elapsed_seconds(start_time) * 1000),
            status="COMPLETED",
            stats_mode=stats_mode,
            tables_analyzed=tables_analyzed,
            stats_lifecycle=stats_lifecycle,
            per_table_ms=(per_table_ms or None),
        )

    @abstractmethod
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

        pass

    def close_connection(self, connection: Any) -> None:
        if connection and hasattr(connection, "close"):
            connection.close()

    def ensure_stream_sessions_supported(self, connection: Any = None) -> None:
        return None

    def new_stream_connection(self, connection: Any, *, benchmark_type: str | None = None) -> Any:
        del benchmark_type
        if self.stream_connection_capability is StreamConnectionCapability.INDEPENDENT_CONNECTION:
            raise NotImplementedError(
                f"{self.platform_name} declares stream_connection_capability="
                "StreamConnectionCapability.INDEPENDENT_CONNECTION but does not override "
                "new_stream_connection() to open an independent per-stream connection/session."
            )
        return _make_stream_cursor(connection)

    def validate_platform_capabilities(self, benchmark_type: str) -> ValidationResult:
        errors = []
        warnings = []

        platform_info = {
            "platform": self.platform_name,
            "benchmark_type": benchmark_type,
            "dry_run_mode": self.dry_run_mode,
        }

        if benchmark_type.lower() not in ["tpcds", "tpch"]:
            warnings.append(f"Benchmark type '{benchmark_type}' may not be fully supported")

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            details=platform_info,
        )

    def _apply_run_plan_flags(self, run_config: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = {
            "capture_plans": self.capture_plans,
            "show_query_plans": self.show_query_plans,
            "analyze_plans": self.analyze_plans,
            "strict_plan_capture": self.strict_plan_capture,
            "normalize_plan_literals": self.normalize_plan_literals,
            "plan_capture_timeout_seconds": self.plan_capture_timeout_seconds,
        }
        new_capture_plans = bool(run_config["capture_plans"]) if "capture_plans" in run_config else self.capture_plans
        new_show_query_plans = (
            bool(run_config["show_query_plans"])
            if run_config.get("show_query_plans") is not None
            else self.show_query_plans
        )
        new_analyze_plans = (
            bool(run_config["analyze_plans"]) if run_config.get("analyze_plans") is not None else self.analyze_plans
        )
        new_strict_plan_capture = (
            bool(run_config["strict_plan_capture"]) if "strict_plan_capture" in run_config else self.strict_plan_capture
        )
        new_normalize_plan_literals = (
            bool(run_config["normalize_plan_literals"])
            if "normalize_plan_literals" in run_config
            else self.normalize_plan_literals
        )
        new_plan_capture_timeout_seconds = (
            int(run_config["plan_capture_timeout_seconds"])
            if run_config.get("plan_capture_timeout_seconds") is not None
            else self.plan_capture_timeout_seconds
        )
        self.capture_plans = new_capture_plans
        self.show_query_plans = new_show_query_plans
        self.analyze_plans = new_analyze_plans
        self.strict_plan_capture = new_strict_plan_capture
        self.normalize_plan_literals = new_normalize_plan_literals
        self.plan_capture_timeout_seconds = new_plan_capture_timeout_seconds
        return snapshot

    def run_enhanced_benchmark(self, benchmark, **run_config) -> EnhancedBenchmarkResults:
        start_time = mono_time()
        execution_id = str(uuid.uuid4())[:8]
        self._reset_run_scoped_state()
        plan_capture_config = self._apply_run_plan_flags(run_config)

        try:
            data_generation_phase = self._create_enhanced_data_generation_phase(benchmark)

            quiet_console.print(f"Connecting to {self.platform_name}...")
            self.log_very_verbose(f"database_was_reused flag BEFORE connection: {self.database_was_reused}")
            self.benchmark = benchmark
            self._drift_validation_result = None
            connection = self.create_connection(**run_config.get("connection", {}))
            self.connection = connection
            self.log_very_verbose(f"database_was_reused flag AFTER connection: {self.database_was_reused}")

            effective_tuning_config = self.get_effective_tuning_configuration()
            if self.tuning_enabled and effective_tuning_config:
                quiet_console.print("Validating unified tuning configuration...")
                tuning_errors, tuning_warnings = effective_tuning_config.validate_for_platform_detailed(
                    self.canonical_platform_type
                )
                for warning in tuning_warnings:
                    self.logger.warning(f"Tuning configuration warning: {warning}")
                if tuning_errors:
                    raise ValueError(f"Invalid tuning configuration: {'; '.join(tuning_errors)}")
                quiet_console.print("✅ Unified tuning configuration validated")

            self._applied_tuning_ledger = AppliedTuningLedger()
            self._applied_layout_operations = []

            self.log_verbose(f"Checking database_was_reused flag before schema creation: {self.database_was_reused}")
            if self.database_was_reused:
                (
                    schema_time,
                    schema_creation_phase,
                    loading_time,
                    table_stats,
                    data_loading_phase,
                    tuning_metadata_saved,
                ) = self._setup_reused_database_phases(benchmark, connection)
            else:
                (
                    schema_time,
                    schema_creation_phase,
                    loading_time,
                    table_stats,
                    data_loading_phase,
                    tuning_metadata_saved,
                ) = self._setup_fresh_database_phases(benchmark, connection, effective_tuning_config)

            quiet_console.print("Validating benchmark data...")
            validation_phase = self._create_enhanced_validation_phase(benchmark, connection, table_stats)

            tunings_applied_dict = None
            requested_config_hash = None
            if self.tuning_enabled and effective_tuning_config:
                tunings_applied_dict = effective_tuning_config.to_dict()
                requested_config_hash = effective_tuning_config.get_configuration_hash()

            for _dropped in self._applied_tuning_ledger.dropped:
                self.logger.warning(
                    "Requested tuning intent not applied: %s (%s)",
                    _dropped.intent,
                    _dropped.reason,
                )

            tuning_validation_status = tuning_trust.apply_phase_status(self, bool(effective_tuning_config))

            if self._check_validation_failure(validation_phase):
                failed_result = self._create_failed_benchmark_result(
                    benchmark,
                    validation_phase,
                    table_stats,
                    loading_time,
                    schema_creation_phase,
                    data_loading_phase,
                    tunings_applied_dict,
                    tuning_validation_status,
                    tuning_metadata_saved,
                    requested_config_hash,
                    per_table_timings=getattr(self, "_last_per_table_timings", None),
                )
                self._attach_applied_ledger_payload(failed_result, tuning_validation_status)
                return failed_result

            quiet_console.print("✅ Data validation passed")

            self._fold_layout_operations_into_ledger()
            self._reconcile_requested_tuning(effective_tuning_config)
            if self.tuning_enabled and effective_tuning_config and not self.database_was_reused:
                quiet_console.print(self._applied_tuning_ledger.describe_outcome())

            benchmark_type = run_config.get("benchmark_type", "olap")
            self.configure_for_benchmark(
                recording_connection(connection, self._applied_tuning_ledger, PHASE_SESSION),
                benchmark_type,
            )

            statistics_phase = None
            if run_config.get("gather_statistics"):
                statistics_phase = self.run_statistics_phase(
                    benchmark,
                    connection,
                    benchmark_name=run_config.get("statistics_benchmark_name") or run_config.get("benchmark_name", ""),
                    table_names=sorted(table_stats) if table_stats else None,
                    reset=run_config.get("stats_reset"),
                    collect_per_table_timing=bool(run_config.get("stats_per_table_timing", False)),
                )

            test_execution_type = run_config.get("test_execution_type", "standard")
            quiet_console.print(f"Executing benchmark queries ({test_execution_type} mode)...")
            self._last_throughput_test_result = None
            query_results = self._execute_queries_by_type(benchmark, connection, run_config)
            probe_elapsed_s = self._collect_post_measurement_metadata(connection, run_config)

            queries = self._get_dialect_queries(
                benchmark,
                benchmark_slug=run_config.get("benchmark_name", ""),
                connection=None if self._post_measurement_contained else connection,
            )
            stream_id = "standard"
            self._extract_query_definitions(benchmark, queries, stream_id)
            query_executions = self._create_standard_execution_phase(query_results, stream_id)

            total_duration = exclude_probe_wall_time(elapsed_seconds(start_time), probe_elapsed_s)

            setup_phase = SetupPhase(
                data_generation=data_generation_phase,
                schema_creation=schema_creation_phase,
                data_loading=data_loading_phase,
                validation=validation_phase,
                statistics_gathering=statistics_phase,
                post_load_maintenance=self.build_post_load_maintenance_phase(),
            )

            execution_phases, total_exec_time, power_test_phase, throughput_test_phase = self._build_execution_phases(
                query_results,
                query_executions,
                run_config,
                setup_phase,
                power_workload_timing=self._last_power_workload_timing,
            )

            platform_info, normalized_metadata = self._collect_platform_metadata(
                None if self._post_measurement_contained else connection
            )
            execution_metadata, system_profile, anonymous_machine_id = self._build_execution_metadata(run_config)

            total_rows_loaded = sum(table_stats.values()) if table_stats else 0
            data_size_mb = self._calculate_data_size(benchmark.output_dir) if hasattr(benchmark, "output_dir") else 0.0

            resource_snapshot = self._collect_resource_utilization()
            performance_summary = self._summarize_performance_characteristics(
                query_results=query_results,
                total_duration=total_duration,
                total_rows_loaded=total_rows_loaded,
            )

            power_at_size = power_test_phase.power_at_size if power_test_phase else None
            throughput_at_size = throughput_test_phase.throughput_at_size if throughput_test_phase else None

            eet = run_config.get("_effective_execution_type")
            execution_type = eet if eet is not None else run_config.get("test_execution_type", "standard")

            _plans_captured, _failures, _plan_errors = compute_plan_capture_stats(
                query_results,
                self.capture_plans,
                existing_errors=list(self.plan_capture_errors),
            )

            (
                final_tuning_status,
                applied_ledger_payload,
                applied_ledger_hash,
                applied_receipt_payload,
            ) = tuning_trust.read_back_applied_ledger(
                self, connection, tuning_validation_status, bool(effective_tuning_config)
            )

            return benchmark.create_enhanced_benchmark_result(
                platform=self.platform_name,
                query_results=query_results,
                execution_metadata=execution_metadata,
                phases=execution_phases,
                resource_utilization=resource_snapshot,
                performance_characteristics=performance_summary,
                query_plans_captured=_plans_captured,
                plan_capture_failures=_failures,
                plan_capture_errors=_plan_errors,
                execution_id=execution_id,
                duration_seconds=total_duration,
                data_loading_time=loading_time,
                schema_creation_time=schema_time,
                total_rows_loaded=total_rows_loaded,
                data_size_mb=data_size_mb,
                table_statistics=table_stats or {},
                per_table_timings=getattr(self, "_last_per_table_timings", None),
                platform_info=platform_info,
                **normalized_metadata,
                tunings_applied=tunings_applied_dict,
                tuning_validation_status=final_tuning_status,
                tuning_metadata_saved=tuning_metadata_saved,
                tuning_config_hash=requested_config_hash,
                applied_tuning_ledger=applied_ledger_payload,
                applied_ledger_hash=applied_ledger_hash,
                tuning_source_file=self.tuning_source_file,
                tuning_source=self.tuning_source,
                system_profile=system_profile,
                anonymous_machine_id=anonymous_machine_id,
                validation_status=self._determine_overall_validation_status(validation_phase),
                validation_details=validation_phase.validation_details,
                power_at_size=power_at_size,
                throughput_at_size=throughput_at_size,
                test_execution_type=execution_type,
            )

        finally:
            self.capture_plans = plan_capture_config["capture_plans"]
            self.show_query_plans = plan_capture_config["show_query_plans"]
            self.analyze_plans = plan_capture_config["analyze_plans"]
            self.strict_plan_capture = plan_capture_config["strict_plan_capture"]
            self.normalize_plan_literals = plan_capture_config["normalize_plan_literals"]
            self.plan_capture_timeout_seconds = plan_capture_config["plan_capture_timeout_seconds"]
            if hasattr(self, "connection") and self.connection:
                self._close_run_connection()

    def _defer_connection_close_until_quiescent(self, connection: Any, throughput_result: Any) -> None:
        if throughput_result is None:
            self.close_connection(connection)
            return

        def _close_when_quiescent() -> None:
            while not await_quiescence(throughput_result, timeout=1.0):
                pass
            try:
                self.close_connection(connection)
            except Exception as exc:
                self.logger.warning("Deferred benchmark connection cleanup failed: %r", exc)

        threading.Thread(
            target=_close_when_quiescent,
            name="benchbox-throughput-connection-cleanup",
            daemon=True,
        ).start()

    def _collect_post_measurement_metadata(self, connection: Any, run_config: Mapping[str, Any]) -> float:
        if self._post_measurement_contained:
            self._client_link_metadata = {
                "collection_status": "unavailable",
                "source": "unavailable",
                "client_region": None,
                "client_cloud": None,
                "statement_overhead_ms": None,
                "collection_error_class": "OutstandingThroughputWork",
                "collection_error_message": (
                    "Post-measurement metadata collection was skipped because a timed-out "
                    "throughput worker still owns benchmark resources."
                ),
            }
            self._link_probe_timed_out = True
            return 0.0

        probe_start = mono_time()
        self._collect_client_link_metadata(connection, run_config)
        return elapsed_seconds(probe_start)

    def _close_run_connection(self) -> None:
        connection = self.connection
        if self._post_measurement_contained:
            self._defer_connection_close_until_quiescent(connection, self._contained_throughput_result)
        else:
            self.close_connection(connection)
        self.connection = None

    def _collect_client_link_metadata(self, connection: Any, run_config: Mapping[str, Any]) -> None:
        try:
            self._client_link_metadata = self._build_client_link_metadata(connection, run_config)
            self._link_probe_timed_out = bool(
                (self._client_link_metadata or {}).get("collection_error_class") == "TimeoutError"
            )
        except Exception as exc:
            self.logger.warning("Client-link metadata collection failed: %r", exc)
            self._link_probe_timed_out = False
            self._client_link_metadata = {
                "collection_status": "unavailable",
                "source": "unavailable",
                "client_region": None,
                "client_cloud": None,
                "statement_overhead_ms": None,
                "collection_error_class": type(exc).__name__,
                "collection_error_message": f"{type(exc).__name__}: client-link metadata collection failed",
            }

    def _build_client_link_metadata(self, connection: Any, run_config: Mapping[str, Any]) -> dict[str, Any]:
        dry_run = bool(getattr(self, "dry_run_mode", False) or run_config.get("dry_run_mode", False))
        probe_requested = is_probe_requested(run_config.get("link_probe", True)) and not dry_run
        probe_result: dict[str, Any] | None = None
        if probe_requested:
            probe_result = probe_statement_overhead(connection)

        merged_config = {
            **self.platform_config,
            **{key: value for key, value in run_config.items() if value is not None},
        }
        if dry_run:
            region_info: dict[str, Any] = {"client_region": None, "client_cloud": None, "source": "unavailable"}
        else:
            region_info = discover_client_region(merged_config)

        has_region = bool(region_info.get("client_region"))
        probe_available = bool(probe_result and probe_result.get("collection_status") == "available")

        if has_region and probe_available:
            collection_status = "available"
        elif has_region or probe_available:
            collection_status = "partial"
        elif not probe_requested and not has_region:
            collection_status = "not_requested"
        else:
            collection_status = "unavailable"

        return {
            "collection_status": collection_status,
            "source": region_info.get("source", "unavailable"),
            "client_region": region_info.get("client_region"),
            "client_cloud": region_info.get("client_cloud"),
            "statement_overhead_ms": probe_result.get("statement_overhead_ms") if probe_result else None,
            "collection_error_class": probe_result.get("collection_error_class") if probe_result else None,
            "collection_error_message": probe_result.get("collection_error_message") if probe_result else None,
        }

    def _collect_platform_metadata(self, connection: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        if self._link_probe_timed_out:
            self.logger.warning(
                "Statement overhead probe timed out; skipping live platform "
                "metadata collection on the possibly-held connection."
            )
            return {}, self._client_link_only_metadata()
        platform_info = self.get_platform_info(connection)
        return platform_info, self._resolve_normalized_metadata(connection, platform_info)

    def _client_link_only_metadata(self) -> dict[str, Any]:
        from benchbox.platforms.cloud_shared import empty_cache_control_receipt, sanitize_cache_control_receipt

        metadata: dict[str, Any] = {}
        if self._client_link_metadata:
            metadata["execution_environment"] = {"client_link": dict(self._client_link_metadata)}
        cached_receipt = getattr(self, "_cache_control_receipt", None)
        if cached_receipt is not None:
            receipt = sanitize_cache_control_receipt(cached_receipt)
            if receipt is None:
                receipt = empty_cache_control_receipt()
                receipt["errors"].append("Cached cache-control receipt has an unsupported shape")
            metadata["platform_compute"] = {"cache_control": receipt}
        return metadata

    def _resolve_normalized_metadata(self, connection: Any, platform_info: Mapping[str, Any] | None) -> dict[str, Any]:
        metadata = self.get_normalized_result_metadata(
            connection=connection,
            platform_info=platform_info,
        )
        if self._client_link_metadata:
            exec_env = metadata.setdefault("execution_environment", {})
            if isinstance(exec_env, dict) and ("client_link" not in exec_env or not exec_env["client_link"]):
                exec_env["client_link"] = dict(self._client_link_metadata)
        return metadata

    def _corroborate_applied_ledger(self, connection: Any, status: str) -> tuple[str, dict[str, Any] | None]:
        return tuning_trust.corroborate_applied_ledger(self, connection, status)

    def _attach_applied_ledger_payload(self, result: Any, status: str) -> None:
        tuning_trust.attach_applied_ledger_payload(self, result, status)

    def _build_drift_check_payload(self) -> dict[str, Any] | None:
        return tuning_trust.build_drift_check_payload(self)

    def _fold_layout_operations_into_ledger(self) -> None:
        tuning_trust.fold_layout_operations_into_ledger(self)

    def _reconcile_requested_tuning(self, config: Any) -> None:
        tuning_trust.reconcile_requested_tuning(self, config)

    def _setup_fresh_database_phases(self, benchmark, connection: Any, effective_tuning_config) -> tuple:
        data_dir = Path(benchmark.output_dir) if hasattr(benchmark, "output_dir") else Path(".")

        if self.table_mode == "external":
            if not self.supports_external_tables:
                raise RuntimeError(f"Platform '{self.platform_name}' does not support --table-mode external")
            validate_fn = getattr(self, "validate_external_table_requirements", None)
            if callable(validate_fn):
                validate_fn()

            if self.tuning_enabled and effective_tuning_config and not self.ensure_tuned_run_marker(connection):
                raise RuntimeError(
                    "Failed to write the tuned-run marker; refusing to proceed with a tuned run "
                    "rather than leave a database with no notuning-reuse refusal evidence"
                )

            schema_time = 0.0
            schema_creation_phase = self._create_enhanced_schema_creation_phase(benchmark, connection, 0.0)
            schema_creation_phase.status = "SKIPPED"

            quiet_console.print("Creating external tables...")
            table_stats, loading_time, per_table_timings = self.create_external_tables(benchmark, connection, data_dir)
            _fmt_tag = f" [{self.external_format}]" if self.external_format else ""
            quiet_console.print(f"✅ External tables created in {loading_time:.2f}s{_fmt_tag}")
            data_loading_phase = self._create_enhanced_data_loading_phase(table_stats, loading_time, per_table_timings)
            self._last_per_table_timings = per_table_timings
            return schema_time, schema_creation_phase, loading_time, table_stats, data_loading_phase, False

        quiet_console.print("Creating database schema...")
        schema_records_ddl = bool(self.tuning_enabled and effective_tuning_config) and not any(
            callable(getattr(self, method_name, None))
            for method_name in ("_record_tuned_sort_key_op", "_record_starrocks_tuning_to_ledger")
        )
        schema_connection = (
            recording_connection(
                connection,
                getattr(self, "_applied_tuning_ledger", None),
                PHASE_DDL,
                statement_filter=is_schema_tuning_statement,
            )
            if schema_records_ddl
            else connection
        )
        schema_time = self.create_schema(benchmark, schema_connection)
        schema_creation_phase = self._create_enhanced_schema_creation_phase(benchmark, connection, schema_time)

        tuning_metadata_saved = False
        if self.tuning_enabled and effective_tuning_config:
            quiet_console.print("Applying unified tuning configuration...")
            if not self.ensure_tuned_run_marker(connection):
                raise RuntimeError(
                    "Failed to write the tuned-run marker; refusing to apply tuning "
                    "rather than leave a database with no notuning-reuse refusal evidence"
                )
            apply_ledger = self._applied_tuning_ledger or AppliedTuningLedger()
            ledger_before_apply = apply_ledger.snapshot()
            self.apply_unified_tuning(effective_tuning_config, connection)
            quiet_console.print(apply_ledger.describe_apply_step(ledger_before_apply))

            quiet_console.print("Saving tuning metadata...")
            tuning_metadata_saved = self.save_tuning_metadata(connection)
            if not tuning_metadata_saved:
                quiet_console.print("⚠️ Failed to save tuning metadata")
            elif self._tuning_marker_save_failed:
                quiet_console.print(
                    "⚠️ Tuning metadata saved without section markers; drift checks have reduced coverage"
                )
            else:
                quiet_console.print("✅ Tuning metadata saved")

        if is_data_loading_skipped(benchmark):
            quiet_console.print("Benchmark uses schema only; skipping data loading")
            schema_only_stats = self.materialize_schema_only_tables(benchmark, connection)
            data_loading_phase = self._create_enhanced_data_loading_phase(schema_only_stats, 0.0, {})
            data_loading_phase.status = "SKIPPED"
            self._last_per_table_timings = {}
            return schema_time, schema_creation_phase, 0.0, schema_only_stats, data_loading_phase, tuning_metadata_saved

        quiet_console.print("Loading benchmark data...")
        table_stats, loading_time, per_table_timings = self.load_data(benchmark, connection, data_dir)
        require_loaded_tables(benchmark, table_stats)
        loading_time, per_table_timings = self.exclude_post_load_maintenance(loading_time, per_table_timings)
        quiet_console.print(f"✅ Data loading completed in {loading_time:.2f}s")
        data_loading_phase = self._create_enhanced_data_loading_phase(table_stats, loading_time, per_table_timings)
        self._last_per_table_timings = per_table_timings
        return schema_time, schema_creation_phase, loading_time, table_stats, data_loading_phase, tuning_metadata_saved

    def run_benchmark(self, benchmark, **run_config) -> EnhancedBenchmarkResults:
        return self.run_enhanced_benchmark(benchmark, **run_config)
