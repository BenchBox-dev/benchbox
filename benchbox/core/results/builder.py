from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from benchbox.core.results.environment import (
    NormalizedExecutionEnvironment,
    PlatformCloudMetadata,
    PlatformComputeMetadata,
    PlatformDeploymentMetadata,
    PlatformStorageMetadata,
    build_environment_payload,
    build_platform_metadata_payload,
)
from benchbox.core.results.metrics import (
    NON_POWER_TEST_TYPES,
    TimingStatsCalculator,
    TPCMetricsCalculator,
)
from benchbox.core.results.models import (
    BenchmarkResults,
    DataLoadingPhase,
    ExecutionPhases,
    PowerTestPhase,
    QueryExecution,
    SetupPhase,
    TableLoadingStats,
)
from benchbox.core.results.platform_info import (
    PlatformInfoInput,
    format_platform_display_name,
)
from benchbox.core.results.platform_options import sanitize_platform_options
from benchbox.core.results.query_execution import query_execution_to_legacy_dict
from benchbox.core.results.query_normalizer import (
    QueryResultInput,
    format_query_id,
)
from benchbox.core.results.query_status import has_failed_query_validation
from benchbox.core.results.subscale_policy import (
    NONSTANDARD_COMPLIANCE_CLASS,
    platform_info_gateway,
    routed_compliance_class,
)

if TYPE_CHECKING:
    pass


def normalize_benchmark_id(name: str) -> str:
    import re

    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    if cleaned.lower().endswith(" benchmark"):
        cleaned = cleaned[:-10]
    lowered = cleaned.lower().strip()

    benchmark_mappings: list[tuple[str, tuple[str, ...]]] = [
        ("tpcds_obt", ("tpcds_obt", "tpcds-obt", "tpc-ds obt", "tpc-ds one big table")),
        ("tpchavoc", ("tpchavoc", "tpch-avoc", "tpch_avoc", "tpc-havoc", "tpc-h avoc")),
        ("tpch_skew", ("tpch_skew", "tpch-skew", "tpc-h skew")),
        ("tpch", ("tpch", "tpc-h", "tpc_h")),
        ("tpcds", ("tpcds", "tpc-ds", "tpc_ds")),
        ("ssb", ("ssb", "star schema")),
        ("clickbench", ("clickbench",)),
    ]

    for canonical_id, variants in benchmark_mappings:
        if any(lowered == v for v in variants):
            return canonical_id

    normalized = lowered.replace(" ", "_").replace("-", "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized


_BENCHMARK_FAMILY: dict[str, str] = {
    "tpch": "tpch",
    "tpch_skew": "tpch",
    "tpchavoc": "tpch",
    "tpcds": "tpcds",
    "tpcds_obt": "tpcds",
}


def benchmark_family(benchmark_id: str) -> str:
    return _BENCHMARK_FAMILY.get(benchmark_id, "generic")


@dataclass
class BenchmarkInfoInput:
    name: str
    scale_factor: float
    test_type: str = "power"
    benchmark_id: str | None = None
    display_name: str | None = None
    compliance_class: str | None = None


@dataclass
class TableStats:
    rows: int
    load_time_ms: int = 0
    status: str = "SUCCESS"
    error_message: str | None = None


@dataclass
class RunConfigInput:
    compression_type: str | None = None
    compression_level: int | None = None
    seed: int | None = None
    phases: list[str] | None = None
    query_subset: list[str] | None = None
    parallelism: int | None = None
    tuning_mode: str | None = None
    tuning_config: Any | None = None
    platform_options: dict[str, Any] | None = None
    platform_option_sources: dict[str, str] | None = None
    table_mode: str | None = None
    external_format: str | None = None
    table_format: str | None = None
    table_format_compression: str | None = None
    table_format_partition_cols: list[str] | None = None
    query_parameters: str | None = None
    execution_engine: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.compression_type:
            data["compression"] = {"type": self.compression_type, "level": self.compression_level}
        if self.seed is not None:
            data["seed"] = self.seed
        if self.phases:
            data["phases"] = self.phases
        if self.query_subset:
            data["query_subset"] = self.query_subset
        if self.parallelism is not None:
            data["parallelism"] = self.parallelism
        if self.tuning_mode:
            data["tuning_mode"] = self.tuning_mode
        if self.tuning_config:
            data["tuning_config"] = sanitize_platform_options({"tuning_config": self.tuning_config})["tuning_config"]
        if self.platform_options:
            data["platform_options"] = self.platform_options
        if self.platform_option_sources:
            data["platform_option_sources"] = self.platform_option_sources
        if self.table_mode and self.table_mode != "native":
            data["table_mode"] = self.table_mode
        if self.external_format:
            data["external_format"] = self.external_format
        if self.table_format:
            data["table_format"] = self.table_format
            if self.table_format_compression:
                data["table_format_compression"] = self.table_format_compression
            if self.table_format_partition_cols:
                data["table_format_partition_cols"] = self.table_format_partition_cols
        if self.query_parameters:
            data["query_parameters"] = self.query_parameters
        if self.execution_engine:
            data["execution_engine"] = self.execution_engine
        return data


class ResultBuilder:
    def __init__(
        self,
        benchmark: BenchmarkInfoInput,
        platform: PlatformInfoInput,
        execution_id: str | None = None,
    ):
        self._benchmark = benchmark
        self._platform = platform
        self._execution_id = execution_id or self._generate_id()

        self._query_results: list[QueryResultInput] = []

        self._table_stats: dict[str, TableStats] = {}
        self._loading_time_ms: float = 0.0

        self._start_time: datetime | None = None
        self._end_time: datetime | None = None

        self._validation_status: str = "PASSED"
        self._validation_details: dict[str, Any] | None = None
        self._execution_metadata: dict[str, Any] = {}
        self._run_config: RunConfigInput | None = None
        self._phase_status: dict[str, dict[str, Any]] = {}
        self._system_profile: dict[str, Any] | None = None
        self._execution_environment: NormalizedExecutionEnvironment | dict[str, Any] | None = None
        self._platform_deployment: PlatformDeploymentMetadata | dict[str, Any] | None = None
        self._platform_cloud: PlatformCloudMetadata | dict[str, Any] | None = None
        self._platform_compute: PlatformComputeMetadata | dict[str, Any] | None = None
        self._platform_storage: PlatformStorageMetadata | dict[str, Any] | None = None
        self._platform_raw_config: dict[str, Any] | None = None
        self._platform_raw_metadata: dict[str, Any] | None = None
        self._tunings_applied: dict[str, Any] | None = None
        self._tuning_config_hash: str | None = None
        self._tuning_source_file: str | None = None
        self._tuning_source: str | None = None
        self._tuning_validation_status: str = "not_validated"
        self._tuning_metadata_saved: bool = False
        self._applied_tuning_ledger: dict[str, Any] | None = None
        self._applied_ledger_hash: str | None = None

        self._query_plans_captured: int = 0
        self._plan_capture_failures: int = 0
        self._plan_capture_errors: list[dict[str, str]] = []

        self._cost_summary: dict[str, Any] | None = None

        self._execution_phases_override: ExecutionPhases | None = None
        self._total_duration_seconds: float | None = None

    @staticmethod
    def _generate_id() -> str:
        return uuid.uuid4().hex[:8]

    def add_query_result(self, result: QueryResultInput) -> None:
        self._query_results.append(result)

    def add_query_results(self, results: list[QueryResultInput]) -> None:
        self._query_results.extend(results)

    def add_table_stats(
        self,
        table_name: str,
        row_count: int,
        load_time_ms: int = 0,
        status: str = "SUCCESS",
        error_message: str | None = None,
    ) -> None:
        self._table_stats[table_name] = TableStats(
            rows=row_count,
            load_time_ms=load_time_ms,
            status=status,
            error_message=error_message,
        )

    def set_loading_time(self, time_ms: float) -> None:
        self._loading_time_ms = time_ms

    def set_total_duration(self, duration_seconds: float) -> None:
        self._total_duration_seconds = duration_seconds

    def set_start_time(self, start_time: datetime) -> None:
        self._start_time = start_time

    def set_end_time(self, end_time: datetime) -> None:
        self._end_time = end_time

    def mark_started(self) -> None:
        self._start_time = datetime.now()

    def mark_completed(self) -> None:
        self._end_time = datetime.now()

    def set_validation_status(
        self,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._validation_status = status
        self._validation_details = details

    def set_execution_metadata(self, metadata: dict[str, Any]) -> None:
        self._execution_metadata = metadata

    def add_execution_metadata(self, key: str, value: Any) -> None:
        self._execution_metadata[key] = value

    def set_run_config(self, config: RunConfigInput) -> None:
        self._run_config = config

    def set_phase_status(
        self,
        phase: str,
        status: str,
        duration_ms: float | None = None,
        **extra: Any,
    ) -> None:
        entry: dict[str, Any] = {"status": status}
        if duration_ms is not None:
            entry["duration_ms"] = duration_ms
        entry.update(extra)
        self._phase_status[phase] = entry

    def set_system_profile(self, profile: Any) -> None:
        self._system_profile = profile

    def set_execution_environment(self, environment: NormalizedExecutionEnvironment | dict[str, Any]) -> None:
        self._execution_environment = environment

    def set_platform_environment_metadata(
        self,
        *,
        deployment: PlatformDeploymentMetadata | dict[str, Any] | None = None,
        cloud: PlatformCloudMetadata | dict[str, Any] | None = None,
        compute: PlatformComputeMetadata | dict[str, Any] | None = None,
        storage: PlatformStorageMetadata | dict[str, Any] | None = None,
        raw_config: dict[str, Any] | None = None,
        raw_metadata: dict[str, Any] | None = None,
    ) -> None:
        if deployment is not None:
            self._platform_deployment = deployment
        if cloud is not None:
            self._platform_cloud = cloud
        if compute is not None:
            self._platform_compute = compute
        if storage is not None:
            self._platform_storage = storage
        if raw_config is not None:
            self._platform_raw_config = raw_config
        if raw_metadata is not None:
            self._platform_raw_metadata = raw_metadata

    def set_tuning_info(
        self,
        tunings_applied: dict[str, Any] | None = None,
        config_hash: str | None = None,
        source_file: str | None = None,
        source: str | None = None,
        *,
        validation_status: str | None = None,
        metadata_saved: bool | None = None,
        applied_tuning_ledger: dict[str, Any] | None = None,
        applied_ledger_hash: str | None = None,
    ) -> None:
        self._tunings_applied = tunings_applied
        self._tuning_config_hash = config_hash
        self._tuning_source_file = source_file
        self._tuning_source = source
        if validation_status is not None:
            self._tuning_validation_status = validation_status
        if metadata_saved is not None:
            self._tuning_metadata_saved = bool(metadata_saved)
        if applied_tuning_ledger is not None:
            self._applied_tuning_ledger = applied_tuning_ledger
        if applied_ledger_hash is not None:
            self._applied_ledger_hash = applied_ledger_hash

    def set_cost_summary(self, cost_summary: dict[str, Any]) -> None:
        self._cost_summary = cost_summary

    def set_execution_phases(self, phases: ExecutionPhases) -> None:
        self._execution_phases_override = phases

    def add_plan_capture_stats(
        self,
        plans_captured: int,
        capture_failures: int = 0,
        capture_errors: list[dict[str, str]] | None = None,
    ) -> None:
        self._query_plans_captured = plans_captured
        self._plan_capture_failures = capture_failures
        self._plan_capture_errors = capture_errors or []

    def build(self) -> BenchmarkResults:
        duration_seconds = self._calculate_duration()

        measurement_results = [r for r in self._query_results if r.run_type == "measurement" and r.iteration > 0]
        results_for_stats = measurement_results if measurement_results else self._query_results
        successful_queries = [r for r in results_for_stats if r.status == "SUCCESS"]
        failed_queries = [r for r in results_for_stats if r.status not in ("SUCCESS", "SKIPPED")]

        exec_times_all = [
            seconds for r in results_for_stats if (seconds := r.execution_time_seconds) is not None and seconds > 0
        ]

        timing_stats = TimingStatsCalculator.calculate_seconds(exec_times_all)
        total_exec_time = timing_stats.get("total_s", 0.0)
        avg_time = timing_stats.get("avg_s", 0.0)
        geometric_mean = timing_stats.get("geometric_mean_s", 0.0)

        platform_info = self._build_platform_info_dict()
        compliance_class = routed_compliance_class(
            self._benchmark.compliance_class, platform_info_gateway(platform_info)
        )
        if failed_queries or compliance_class == NONSTANDARD_COMPLIANCE_CLASS:
            tpc_metrics = {
                "power_at_size": None,
                "throughput_at_size": None,
            }
            if failed_queries:
                geometric_mean = 0.0
        else:
            tpc_metrics = self._calculate_tpc_metrics()
        self._add_qphh_metrics(tpc_metrics, len(failed_queries), compliance_class)

        execution_phases = self._build_execution_phases(
            exec_times_all,
            tpc_metrics,
        )
        if self._execution_phases_override is not None:
            execution_phases = self._execution_phases_override

        query_results_list = self._format_query_results()

        execution_environment = self._build_execution_environment_metadata()
        platform_environment = self._build_platform_environment_metadata(platform_info)

        validation_status = self._validation_status
        if has_failed_query_validation(query_results_list):
            validation_status = "FAILED"
        elif failed_queries and validation_status == "PASSED":
            validation_status = "PARTIAL"

        return BenchmarkResults(
            benchmark_name=self._benchmark.display_name or self._benchmark.name,
            platform=format_platform_display_name(
                self._platform.name,
                self._platform.execution_mode,
            ),
            scale_factor=self._benchmark.scale_factor,
            execution_id=self._execution_id,
            timestamp=self._start_time or datetime.now(),
            duration_seconds=duration_seconds,
            total_queries=len(results_for_stats),
            successful_queries=len(successful_queries),
            failed_queries=len(failed_queries),
            query_results=query_results_list,
            total_execution_time=total_exec_time,
            average_query_time=avg_time,
            data_loading_time=self._loading_time_ms / 1000.0,
            total_rows_loaded=sum(ts.rows for ts in self._table_stats.values()),
            table_statistics={
                name: {"rows": stats.rows, "load_time_ms": stats.load_time_ms}
                if stats.load_time_ms > 0
                else {"rows": stats.rows}
                for name, stats in self._table_stats.items()
            },
            execution_phases=execution_phases,
            test_execution_type=self._benchmark.test_type,
            query_subset=self._run_config.query_subset if self._run_config else None,
            power_at_size=tpc_metrics.get("power_at_size"),
            throughput_at_size=tpc_metrics.get("throughput_at_size"),
            qph_at_size=tpc_metrics.get("qphh_at_size"),
            refresh_included=tpc_metrics.get("refresh_included"),
            qphh_unavailable=tpc_metrics.get("qphh_unavailable"),
            geometric_mean_execution_time=geometric_mean or None,
            validation_status=validation_status,
            validation_details=self._validation_details,
            execution_environment=execution_environment,
            platform_deployment=(
                self._platform_deployment
                if self._platform_deployment is not None
                else platform_environment.get("deployment")
            ),
            platform_cloud=self._platform_cloud
            if self._platform_cloud is not None
            else platform_environment.get("cloud"),
            platform_compute=(
                self._platform_compute if self._platform_compute is not None else platform_environment.get("compute")
            ),
            platform_storage=(
                self._platform_storage if self._platform_storage is not None else platform_environment.get("storage")
            ),
            platform_raw_config=(
                self._platform_raw_config
                if self._platform_raw_config is not None
                else platform_environment.get("raw_config")
            ),
            platform_raw_metadata=(
                self._platform_raw_metadata
                if self._platform_raw_metadata is not None
                else platform_environment.get("raw_metadata")
            ),
            platform_info=platform_info,
            execution_metadata=self._build_execution_metadata(),
            system_profile=self._system_profile,
            tunings_applied=self._tunings_applied,
            tuning_config_hash=self._tuning_config_hash,
            tuning_source_file=self._tuning_source_file,
            tuning_source=self._tuning_source,
            tuning_validation_status=self._tuning_validation_status,
            tuning_metadata_saved=self._tuning_metadata_saved,
            applied_tuning_ledger=self._applied_tuning_ledger,
            applied_ledger_hash=self._applied_ledger_hash,
            query_plans_captured=self._query_plans_captured,
            plan_capture_failures=self._plan_capture_failures,
            plan_capture_errors=self._plan_capture_errors,
            cost_summary=self._cost_summary,
            _benchmark_id_override=self._benchmark.benchmark_id,
            compliance_class=compliance_class,
        )

    def _calculate_duration(self) -> float:
        if self._total_duration_seconds is not None:
            return self._total_duration_seconds

        if self._start_time and self._end_time:
            delta = self._end_time - self._start_time
            return delta.total_seconds()

        total_query_time = sum(r.execution_time_seconds or 0.0 for r in self._query_results)
        return total_query_time + (self._loading_time_ms / 1000.0)

    def _calculate_tpc_metrics(self) -> dict[str, Any]:
        metrics: dict[str, Any] = {
            "power_at_size": None,
            "throughput_at_size": None,
        }

        benchmark_id = normalize_benchmark_id(self._benchmark.name)
        if benchmark_id not in ("tpch", "tpcds"):
            return metrics

        test_type = self._benchmark.test_type
        power_phase = self._execution_phases_override.power_test if self._execution_phases_override else None
        if test_type in ("power", "standard", "combined"):
            power = power_phase.power_at_size if power_phase and power_phase.power_at_size > 0 else None
            refresh_configured = power_phase is not None and power_phase.refresh_first_set is not None
            if power is None and not refresh_configured:
                power = self._calculate_power_at_size()
            if power and power > 0:
                metrics["power_at_size"] = power

        throughput_phase = self._execution_phases_override.throughput_test if self._execution_phases_override else None
        if throughput_phase and throughput_phase.success and throughput_phase.throughput_at_size:
            if throughput_phase.throughput_at_size > 0:
                metrics["throughput_at_size"] = throughput_phase.throughput_at_size

        return metrics

    def _add_qphh_metrics(self, metrics: dict[str, Any], failed_query_count: int, compliance_class: str | None) -> None:
        if normalize_benchmark_id(self._benchmark.name) != "tpch":
            return

        execution_phases = self._execution_phases_override
        power_phase = execution_phases.power_test if execution_phases else None
        throughput_phase = execution_phases.throughput_test if execution_phases else None
        power_refresh_functions = power_phase.refresh_functions if power_phase else None
        power_data_phase = None
        if power_phase is not None:
            executions = [
                execution for execution in power_phase.query_executions if execution.query_id != "power_test_error"
            ]
            query_count = power_phase.query_count if power_phase.query_count is not None else len(executions)
            query_success = (
                power_phase.success
                if power_phase.success is not None
                else bool(executions) and all(execution.status == "SUCCESS" for execution in executions)
            )
            power_refresh_success = power_refresh_functions is None or (
                len(power_refresh_functions) == 2
                and all(function.get("status") == "SUCCESS" for function in power_refresh_functions)
            )
            power_data_phase = {
                "status": "COMPLETED" if query_count == 22 and query_success and power_refresh_success else "FAILED",
                "query_count": query_count,
                "refresh_first_set": power_phase.refresh_first_set,
                "refresh_functions": power_refresh_functions,
            }

        throughput_data_phase = None
        if throughput_phase is not None:
            refresh_stream = throughput_phase.refresh_stream
            throughput_data_phase = {
                "status": "COMPLETED" if throughput_phase.success else "FAILED",
                "num_streams": throughput_phase.num_streams,
                "stream_results": [{} for _ in throughput_phase.streams],
                "refresh_stream": refresh_stream,
            }

        power_data = {
            "benchmark": {"id": "tpch", "compliance_class": compliance_class},
            "phases": {"power_test": power_data_phase} if power_data_phase else {},
            "summary": {"queries": {"failed": failed_query_count}},
            "run": {"query_subset": self._run_config.query_subset if self._run_config else None},
        }
        throughput_data = {
            "benchmark": {"id": "tpch", "compliance_class": compliance_class},
            "phases": {"throughput_test": throughput_data_phase} if throughput_data_phase else {},
            "summary": {"queries": {"failed": failed_query_count}},
            "run": {"query_subset": self._run_config.query_subset if self._run_config else None},
        }
        metrics["refresh_included"] = TPCMetricsCalculator.refresh_included(
            power_data_phase,
            throughput_data_phase,
        )
        unavailable = TPCMetricsCalculator.qphh_unavailable_reasons(power_data, throughput_data)
        if not unavailable and metrics.get("power_at_size") and metrics.get("throughput_at_size"):
            metrics["qphh_at_size"] = TPCMetricsCalculator.calculate_qphh_at_size(
                metrics["power_at_size"],
                metrics["throughput_at_size"],
            )
        else:
            if not unavailable:
                unavailable.append("Power@Size or Throughput@Size could not be derived")
            metrics["qphh_unavailable"] = unavailable

    def _calculate_power_at_size(self) -> float | None:
        measurement = [
            r
            for r in self._query_results
            if r.run_type == "measurement" and r.iteration > 0 and r.test_type not in NON_POWER_TEST_TYPES
        ]
        if not measurement:
            return None

        final_iter = max((r.iteration for r in measurement), default=0)
        final_results = [r for r in measurement if r.iteration == final_iter]
        if not final_results:
            return None
        if any(r.status != "SUCCESS" for r in final_results):
            return None

        times = [seconds for r in final_results if (seconds := r.execution_time_seconds) is not None and seconds > 0]
        if not times:
            return None
        return TPCMetricsCalculator.calculate_power_at_size(times, self._benchmark.scale_factor)

    def _build_execution_phases(
        self,
        exec_times_seconds: list[float],
        tpc_metrics: dict[str, float | None],
    ) -> ExecutionPhases | None:
        setup_phase = self._build_setup_phase()
        power_test_phase = self._build_power_test_phase(
            exec_times_seconds,
            tpc_metrics,
        )
        if not any([setup_phase, power_test_phase]):
            return None

        return ExecutionPhases(
            setup=setup_phase or SetupPhase(),
            power_test=power_test_phase,
        )

    def _build_setup_phase(self) -> SetupPhase | None:
        if not self._table_stats:
            return None

        per_table_stats = {
            name: TableLoadingStats(
                rows=stats.rows,
                load_time_ms=stats.load_time_ms,
                status=stats.status,
                error_message=stats.error_message,
            )
            for name, stats in self._table_stats.items()
        }

        total_rows = sum(ts.rows for ts in self._table_stats.values())
        failed_count = sum(1 for ts in self._table_stats.values() if ts.status == "FAILED")
        status = "FAILED" if failed_count > 0 else "SUCCESS"

        return SetupPhase(
            data_loading=DataLoadingPhase(
                duration_ms=int(self._loading_time_ms),
                status=status,
                total_rows_loaded=total_rows,
                tables_loaded=len(self._table_stats),
                per_table_stats=per_table_stats,
            )
        )

    def _build_power_test_phase(
        self,
        exec_times_seconds: list[float],
        tpc_metrics: dict[str, float | None],
    ) -> PowerTestPhase | None:
        if self._benchmark.test_type not in ("power", "standard", "combined"):
            return None

        if not self._query_results:
            return None

        query_executions = []
        for i, result in enumerate(self._query_results):
            query_executions.append(
                QueryExecution(
                    query_id=format_query_id(result.query_id),
                    stream_id=str(result.stream_id),
                    execution_order=i + 1,
                    execution_time_ms=result.execution_time_ms,
                    status=result.status,
                    rows_returned=result.rows_returned,
                    error_message=result.error_message,
                    iteration=result.iteration,
                    run_type=result.run_type,
                    row_count_validation=result.row_count_validation,
                    cost=result.cost,
                )
            )

        geometric_mean = 0.0
        if exec_times_seconds:
            geometric_mean = TPCMetricsCalculator.calculate_geometric_mean(exec_times_seconds)

        total_duration_ms = sum(int(r.execution_time_ms or 0.0) for r in self._query_results)

        now_iso = datetime.now().isoformat()

        return PowerTestPhase(
            start_time=self._start_time.isoformat() if self._start_time else now_iso,
            end_time=self._end_time.isoformat() if self._end_time else now_iso,
            duration_ms=total_duration_ms,
            query_executions=query_executions,
            geometric_mean_time=geometric_mean,
            power_at_size=tpc_metrics.get("power_at_size") or 0.0,
        )

    def _format_query_results(self) -> list[dict[str, Any]]:
        results = []
        for result in self._query_results:
            result_dict = query_execution_to_legacy_dict(
                result,
                include_seconds=True,
                include_legacy_seconds_alias=True,
            )
            result_dict["query_id"] = format_query_id(result.query_id)
            if result.execution_time_ms is not None:
                result_dict["execution_time_ms"] = int(result.execution_time_ms)

            results.append(result_dict)

        return results

    def _build_platform_info_dict(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "platform_name": self._platform.name,
            "execution_mode": self._platform.execution_mode,
        }

        if self._platform.platform_version:
            info["platform_version"] = self._platform.platform_version
        if self._platform.client_library_version:
            info["client_library_version"] = self._platform.client_library_version

        if self._platform.connection_mode:
            info["connection_mode"] = self._platform.connection_mode

        if self._platform.family:
            info["family"] = self._platform.family

        if self._platform.config:
            info["configuration"] = self._platform.config

        if self._platform.engine_version:
            info["engine_version"] = self._platform.engine_version
        if self._platform.engine_version_source:
            info["engine_version_source"] = self._platform.engine_version_source

        return info

    def _build_execution_environment_metadata(self) -> dict[str, Any]:
        payload = build_environment_payload(
            system_profile=self._system_profile,
            execution_environment=self._execution_environment,
        )
        return {
            key: payload[key]
            for key in ("client_host", "platform_runtime", "container", "client_link")
            if isinstance(payload.get(key), dict)
        }

    def _build_platform_environment_metadata(self, platform_info: dict[str, Any]) -> dict[str, Any]:
        platform_config = (
            platform_info.get("configuration") if isinstance(platform_info.get("configuration"), dict) else {}
        )
        return build_platform_metadata_payload(
            platform_info=platform_info,
            platform_config=platform_config,
            deployment=self._platform_deployment,
            cloud=self._platform_cloud,
            compute=self._platform_compute,
            storage=self._platform_storage,
            raw_config=self._platform_raw_config
            if self._platform_raw_config is not None
            else sanitize_platform_options(platform_config),
            raw_metadata=self._platform_raw_metadata,
        )

    def _build_execution_metadata(self) -> dict[str, Any]:
        metadata = dict(self._execution_metadata)

        metadata["mode"] = self._platform.execution_mode
        metadata["execution_mode"] = self._platform.execution_mode

        if self._platform.execution_mode == "dataframe":
            metadata["dataframe_platform"] = self._platform.name
            if self._platform.family:
                metadata["dataframe_family"] = self._platform.family

        if self._run_config:
            run_config = {}
            existing_run_config = metadata.get("run_config")
            if isinstance(existing_run_config, dict):
                run_config.update(existing_run_config)
            run_config.update(self._run_config.to_dict())
            metadata["run_config"] = run_config
        if self._phase_status:
            merged_phase_status: dict[str, dict[str, Any]] = {}
            existing_phase_status = metadata.get("phase_status")
            if isinstance(existing_phase_status, dict):
                for phase_name, phase_data in existing_phase_status.items():
                    if isinstance(phase_data, dict):
                        merged_phase_status[phase_name] = dict(phase_data)
            for phase_name, phase_data in self._phase_status.items():
                current = merged_phase_status.get(phase_name, {})
                current.update(phase_data)
                merged_phase_status[phase_name] = current
            metadata["phase_status"] = merged_phase_status

        return metadata


def build_benchmark_results(
    benchmark_name: str,
    platform_name: str,
    scale_factor: float,
    query_results: list[QueryResultInput],
    *,
    execution_mode: str = "sql",
    test_type: str = "power",
    platform_version: str | None = None,
    table_stats: dict[str, int] | None = None,
    loading_time_ms: float = 0.0,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> BenchmarkResults:
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(
            name=benchmark_name,
            scale_factor=scale_factor,
            test_type=test_type,
        ),
        platform=PlatformInfoInput(
            name=platform_name,
            platform_version=platform_version,
            client_library_version=None,
            execution_mode=execution_mode,
        ),
    )

    builder.add_query_results(query_results)

    if table_stats:
        for table_name, row_count in table_stats.items():
            builder.add_table_stats(table_name, row_count)

    if loading_time_ms:
        builder.set_loading_time(loading_time_ms)

    if start_time:
        builder.set_start_time(start_time)
    if end_time:
        builder.set_end_time(end_time)

    return builder.build()
