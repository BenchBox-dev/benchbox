from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypedDict

from benchbox.core.results.environment import (
    NormalizedExecutionEnvironment,
    PlatformCloudMetadata,
    PlatformComputeMetadata,
    PlatformDeploymentMetadata,
    PlatformStorageMetadata,
)

if TYPE_CHECKING:
    from benchbox.core.results.query_plan_models import QueryPlanDAG


QUERY_RUN_TYPE_WARMUP = "warmup"
QUERY_RUN_TYPE_MEASUREMENT = "measurement"
QUERY_RUN_TYPE_METADATA = "metadata"
QUERY_RUN_TYPE_SUMMARY = "summary"
QUERY_RUN_TYPES = {
    QUERY_RUN_TYPE_WARMUP,
    QUERY_RUN_TYPE_MEASUREMENT,
    QUERY_RUN_TYPE_METADATA,
    QUERY_RUN_TYPE_SUMMARY,
}


@dataclass
class TableGenerationStats:
    generation_time_ms: int
    status: str
    rows_generated: int
    data_size_bytes: int
    file_path: str
    error_type: str | None = None
    error_message: str | None = None
    rows_attempted: int | None = None
    bytes_attempted: int | None = None
    error_timestamp: str | None = None


@dataclass
class DataGenerationPhase:
    duration_ms: int
    status: str
    tables_generated: int
    total_rows_generated: int
    total_data_size_bytes: int
    per_table_stats: dict[str, TableGenerationStats]


@dataclass
class TableCreationStats:
    creation_time_ms: int
    status: str
    constraints_applied: int
    indexes_created: int
    error_type: str | None = None
    error_message: str | None = None
    error_timestamp: str | None = None


@dataclass
class SchemaCreationPhase:
    duration_ms: int
    status: str
    tables_created: int
    constraints_applied: int
    indexes_created: int
    per_table_creation: dict[str, TableCreationStats]


@dataclass
class TableLoadingStats:
    rows: int
    load_time_ms: int
    status: str
    error_type: str | None = None
    error_message: str | None = None
    rows_processed: int | None = None
    rows_successful: int | None = None
    error_timestamp: str | None = None


@dataclass
class DataLoadingPhase:
    duration_ms: int
    status: str
    total_rows_loaded: int
    tables_loaded: int
    per_table_stats: dict[str, TableLoadingStats]


@dataclass
class ValidationPhase:
    duration_ms: int
    row_count_validation: str
    schema_validation: str
    data_integrity_checks: str
    validation_details: dict[str, Any] | None = None


@dataclass
class MergeSettlePhase:
    settled: bool
    waited_seconds: float
    active_parts: int | None
    timeout_seconds: float


@dataclass
class PostLoadMaintenancePhase:
    duration_ms: int
    status: str = "SUCCESS"
    tables_processed: int = 0


@dataclass
class StatisticsGatheringPhase:
    duration_ms: int
    status: str
    stats_mode: str
    tables_analyzed: int = 0
    error_message: str | None = None
    stats_lifecycle: str | None = None
    per_table_ms: dict[str, int] | None = None


@dataclass
class SetupPhase:
    data_generation: DataGenerationPhase | None = None
    schema_creation: SchemaCreationPhase | None = None
    data_loading: DataLoadingPhase | None = None
    validation: ValidationPhase | None = None
    statistics_gathering: StatisticsGatheringPhase | None = None
    post_load_maintenance: PostLoadMaintenancePhase | None = None
    merge_settle: MergeSettlePhase | None = None


@dataclass(init=False)
class QueryExecution:
    query_id: str
    stream_id: str | int | None
    execution_order: int | None
    execution_time_ms: float | None
    status: str
    rows_returned: int | None = None
    resource_usage: dict[str, Any] | None = None
    error_message: str | None = None
    iteration: int | None = None
    run_type: str | None = None
    row_count_validation: dict[str, Any] | None = None
    cost: float | None = None
    query_plan: QueryPlanDAG | dict[str, Any] | str | None = None
    plan_fingerprint: str | None = None
    plan_fingerprint_normalized: str | None = None
    plan_capture_time_ms: float | None = None
    plan_capture_error: str | None = None
    dataframe_skip_summary: dict[str, Any] | None = None
    result_digest: str | None = None
    test_type: str | None = None
    error_type: str | None = None
    execution_engine: str | None = None
    request_count: int | None = None
    result_bytes: int | None = None
    platform_timing: dict[str, Any] | None = None

    def __init__(
        self,
        query_id: str,
        stream_id: str | int | None = None,
        execution_order: int | None = None,
        execution_time_ms: float | None = None,
        status: str = "UNKNOWN",
        rows_returned: int | None = None,
        resource_usage: dict[str, Any] | None = None,
        error_message: str | None = None,
        iteration: int | None = None,
        run_type: str | None = None,
        row_count_validation: dict[str, Any] | None = None,
        cost: float | None = None,
        query_plan: QueryPlanDAG | dict[str, Any] | str | None = None,
        plan_fingerprint: str | None = None,
        plan_fingerprint_normalized: str | None = None,
        plan_capture_time_ms: float | None = None,
        plan_capture_error: str | None = None,
        dataframe_skip_summary: dict[str, Any] | None = None,
        result_digest: str | None = None,
        test_type: str | None = None,
        error_type: str | None = None,
        execution_engine: str | None = None,
        request_count: int | None = None,
        result_bytes: int | None = None,
        platform_timing: dict[str, Any] | None = None,
        *,
        execution_time_seconds: float | None = None,
    ) -> None:
        from benchbox.core.results.query_execution import (
            normalize_duration_ms,
            normalize_non_negative_integer,
            normalize_platform_timing,
            normalize_status,
            normalize_stream_id,
        )

        self.query_id = str(query_id)
        self.stream_id = normalize_stream_id(stream_id)
        self.execution_order = (
            normalize_non_negative_integer("execution_order", execution_order) if execution_order is not None else None
        )
        self.execution_time_ms = normalize_duration_ms(
            execution_time_ms=execution_time_ms,
            execution_time_seconds=execution_time_seconds,
        )
        self.status = normalize_status(status)
        self.rows_returned = (
            normalize_non_negative_integer("rows_returned", rows_returned) if rows_returned is not None else None
        )
        self.resource_usage = resource_usage
        self.error_message = error_message
        self.iteration = normalize_non_negative_integer("iteration", iteration) if iteration is not None else None
        self.run_type = run_type
        self.row_count_validation = row_count_validation
        self.cost = cost
        self.query_plan = query_plan
        self.plan_fingerprint = plan_fingerprint
        self.plan_fingerprint_normalized = plan_fingerprint_normalized
        self.plan_capture_time_ms = (
            normalize_duration_ms(execution_time_ms=plan_capture_time_ms) if plan_capture_time_ms is not None else None
        )
        self.plan_capture_error = plan_capture_error
        self.dataframe_skip_summary = dataframe_skip_summary
        self.result_digest = result_digest
        self.test_type = test_type
        self.error_type = error_type
        self.execution_engine = execution_engine
        self.request_count = (
            normalize_non_negative_integer("request_count", request_count) if request_count is not None else None
        )
        self.result_bytes = (
            normalize_non_negative_integer("result_bytes", result_bytes) if result_bytes is not None else None
        )
        self.platform_timing = normalize_platform_timing(platform_timing)

    @property
    def execution_time_seconds(self) -> float | None:
        if self.execution_time_ms is None:
            return None
        return self.execution_time_ms / 1000.0


@dataclass
class PowerTestPhase:
    start_time: str
    end_time: str
    duration_ms: int
    query_executions: list[QueryExecution]
    geometric_mean_time: float
    power_at_size: float

    refresh_first_set: int | None = None
    refresh_functions: list[dict[str, Any]] | None = None
    query_count: int | None = None
    success: bool | None = None


@dataclass
class ThroughputStream:
    stream_id: int
    start_time: str
    end_time: str
    duration_ms: int
    query_executions: list[QueryExecution]
    success: bool = True
    error_message: str | None = None


class ThroughputOutstandingWork(TypedDict):
    stream_ids: list[int]
    cleanup_state: str


@dataclass
class ThroughputTestPhase:
    start_time: str
    end_time: str
    duration_ms: int
    num_streams: int
    streams: list[ThroughputStream]
    total_queries_executed: int
    throughput_at_size: float | None
    success: bool = True
    errors: list[str] = field(default_factory=list)
    outstanding_work: ThroughputOutstandingWork | None = None
    stream_numbering: dict[str, Any] | None = None
    refresh_stream: dict[str, Any] | None = None


@dataclass
class MaintenanceOperation:
    operation: str
    operation_type: str
    table: str
    execution_time_ms: int
    rows_affected: int
    status: str
    error_message: str | None = None


@dataclass
class MaintenanceTestPhase:
    start_time: str
    end_time: str
    duration_ms: int
    maintenance_operations: list[MaintenanceOperation]
    query_executions: list[QueryExecution]


@dataclass
class MigrationTableStats:
    duration_ms: int
    status: str
    storage_before_bytes: int
    storage_after_bytes: int
    storage_delta_bytes: int
    error_message: str | None = None


@dataclass
class MigrationPhase:
    duration_ms: int
    status: str
    tables_migrated: int
    tables_failed: int
    storage_before_bytes: int
    storage_after_bytes: int
    storage_delta_bytes: int
    per_table_stats: dict[str, MigrationTableStats]


@dataclass
class ExecutionPhases:
    setup: SetupPhase
    power_test: PowerTestPhase | None = None
    throughput_test: ThroughputTestPhase | None = None
    maintenance_test: MaintenanceTestPhase | None = None
    migration: MigrationPhase | None = None


@dataclass
class NativeComparisonEntry:
    query_id: str
    pg_duckdb_ms: float
    duckdb_ms: float
    delta_ms: float


@dataclass
class NativeComparison:
    generated_at: str
    scale_factor: float
    total_queries: int
    mean_delta_ms: float
    max_delta_ms: float
    entries: list[NativeComparisonEntry]


@dataclass
class QueryDefinition:
    sql: str
    parameters: dict[str, Any] | None = None


@dataclass
class BenchmarkResults:
    benchmark_name: str
    platform: str
    scale_factor: float
    execution_id: str
    timestamp: datetime
    duration_seconds: float
    total_queries: int
    successful_queries: int
    failed_queries: int
    query_results: list[dict[str, Any]] = field(default_factory=list)
    total_execution_time: float = 0.0
    average_query_time: float = 0.0
    data_loading_time: float = 0.0
    schema_creation_time: float = 0.0
    total_rows_loaded: int = 0
    data_size_mb: float = 0.0
    table_statistics: dict[str, int] = field(default_factory=dict)
    data_generation_version: int | None = None
    data_generation_hash: str | None = None
    flightdata_source_provenance: dict[str, Any] | None = None
    per_query_timings: list[dict[str, Any]] | None = field(default_factory=list)
    execution_phases: ExecutionPhases | None = None
    query_definitions: dict[str, dict[str, QueryDefinition]] | None = None
    test_execution_type: str = "standard"
    power_at_size: float | None = None
    throughput_at_size: float | None = None
    qph_at_size: float | None = None
    refresh_included: bool | None = None
    qphh_unavailable: list[str] | None = None
    geometric_mean_execution_time: float | None = None
    validation_status: str = "PASSED"
    validation_details: dict[str, Any] | None = None
    execution_environment: NormalizedExecutionEnvironment | dict[str, Any] | None = None
    platform_deployment: PlatformDeploymentMetadata | dict[str, Any] | None = None
    platform_cloud: PlatformCloudMetadata | dict[str, Any] | None = None
    platform_compute: PlatformComputeMetadata | dict[str, Any] | None = None
    platform_storage: PlatformStorageMetadata | dict[str, Any] | None = None
    platform_raw_config: dict[str, Any] | None = None
    platform_raw_metadata: dict[str, Any] | None = None
    platform_info: dict[str, Any] | None = None
    platform_metadata: dict[str, Any] | None = None
    tunings_applied: dict[str, Any] | None = None
    tuning_config_hash: str | None = None
    applied_tuning_ledger: dict[str, Any] | None = None
    applied_ledger_hash: str | None = None
    effective_session_config: dict[str, Any] | None = None
    tuning_source_file: str | None = None
    tuning_source: str | None = None
    tuning_legacy_source: str | None = None
    tuning_validation_status: str = "not_validated"
    tuning_metadata_saved: bool = False
    system_profile: dict[str, Any] | None = None
    database_name: str | None = None
    anonymous_machine_id: str | None = None
    execution_metadata: dict[str, Any] | None = None
    performance_characteristics: dict[str, Any] = field(default_factory=dict)
    performance_summary: dict[str, Any] = field(default_factory=dict)
    cost_summary: dict[str, Any] | None = None
    driver_package: str | None = None
    driver_version_requested: str | None = None
    driver_version_resolved: str | None = None
    driver_version_actual: str | None = None
    driver_runtime_strategy: str | None = None
    driver_runtime_path: str | None = None
    driver_runtime_python_executable: str | None = None
    driver_auto_install: bool = False
    engine_version: str | None = None
    engine_version_source: str | None = None
    output_filename: str | None = None
    resource_utilization: dict[str, Any] | None = None
    _benchmark_id_override: str | None = None
    summary_metrics: dict[str, Any] = field(default_factory=dict)
    query_subset: list[str] | None = None
    concurrency_level: int | None = None
    benchmark_version: str | None = None
    query_plans_captured: int = 0
    plan_capture_failures: int = 0
    plan_capture_errors: list[dict[str, str]] = field(default_factory=list)
    plan_comparison_summary: dict[str, Any] | None = None
    total_plan_capture_time_ms: float = 0.0
    avg_plan_capture_overhead_pct: float = 0.0
    max_plan_capture_time_ms: float = 0.0
    execution_context: dict[str, Any] | None = None
    native_comparison: NativeComparison | None = None
    compliance_class: str | None = None
    dataset_version: str | None = None
    manifest_hash: str | None = None
    data_archive_hash: str | None = None
    funding: str | None = None
    result_source: str | None = None
    execution_engine: dict[str, Any] | None = None

    @property
    def benchmark_id(self) -> str:
        override = getattr(self, "_benchmark_id_override", None)
        if override:
            return override

        if isinstance(self.execution_metadata, dict):
            metadata_override = self.execution_metadata.get("benchmark_id")
            if isinstance(metadata_override, str) and metadata_override:
                return metadata_override

        normalized = self.benchmark_name.lower().replace(" ", "_").replace("-", "_")
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        return normalized
