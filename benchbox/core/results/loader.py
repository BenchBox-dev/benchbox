from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

from benchbox.core.execution_variant import resolve_variant_platform_id
from benchbox.core.results.execution_variant import read_execution_engine, read_platform_compute
from benchbox.core.results.models import (
    BenchmarkResults,
    ExecutionPhases,
    MigrationPhase,
    NativeComparison,
    NativeComparisonEntry,
    PowerTestPhase,
    SetupPhase,
    ThroughputStream,
    ThroughputTestPhase,
)
from benchbox.core.results.query_execution import (
    query_execution_from_compact_v2,
    query_execution_to_legacy_dict,
)
from benchbox.core.results.query_normalizer import normalize_query_id
from benchbox.core.results.query_plan_models import QueryPlanDAG
from benchbox.core.results.schema_policy import (
    LOADER_SCHEMA_POLICY,
    ROW_COUNT_VALIDATION_SCHEMA_VERSION,
    is_loader_supported_result_schema,
    result_schema_version_value,
    supports_row_count_validation,
)
from benchbox.validation.bundle import COMPANION_SUFFIXES

logger = logging.getLogger(__name__)


class ResultLoadError(Exception):
    pass


class UnsupportedSchemaError(ResultLoadError):
    pass


def find_latest_result(
    directory: Path | str,
    benchmark: str | None = None,
    platform: str | None = None,
) -> Path | None:
    directory_path = Path(directory) if isinstance(directory, str) else directory

    if not directory_path.exists() or not directory_path.is_dir():
        return None

    result_files = [f for f in directory_path.glob("*.json") if not f.name.endswith(COMPANION_SUFFIXES)]

    if not result_files:
        return None

    candidates: list[tuple[Path, datetime]] = []
    for filepath in result_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)

            if not is_loader_supported_result_schema(data):
                continue

            file_benchmark = data.get("benchmark", {}).get("id", "")
            file_platform = data.get("platform", {}).get("name", "")
            file_timestamp = data.get("run", {}).get("timestamp", "")

            if benchmark and benchmark.lower() not in file_benchmark.lower():
                continue
            if platform and platform.lower() not in file_platform.lower():
                continue

            try:
                timestamp_dt = datetime.fromisoformat(file_timestamp)
            except (ValueError, TypeError):
                timestamp_dt = datetime.fromtimestamp(filepath.stat().st_mtime)

            candidates.append((filepath, timestamp_dt))

        except (json.JSONDecodeError, OSError):
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def load_result_file(filepath: Path | str) -> tuple[BenchmarkResults, dict[str, Any]]:
    filepath_obj = Path(filepath) if isinstance(filepath, str) else filepath

    if not filepath_obj.exists():
        raise FileNotFoundError(f"Result file not found: {filepath}")

    try:
        with open(filepath_obj, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ResultLoadError(f"Invalid JSON in result file: {e}") from e
    except OSError as e:
        raise ResultLoadError(f"Failed to read result file: {e}") from e

    version_decision = LOADER_SCHEMA_POLICY.evaluate(result_schema_version_value(data))
    if not version_decision.accepted:
        raise UnsupportedSchemaError(version_decision.error_message())

    plans_data, plans_load_error = _load_companion_file(filepath_obj, ".plans.json")
    tuning_data, _tuning_load_error = _load_companion_file(filepath_obj, ".tuning.json")
    applied_data, _applied_load_error = _load_companion_file(filepath_obj, ".applied.json")

    try:
        result = reconstruct_benchmark_results(data, plans_data, tuning_data, applied_data)
    except Exception as e:
        raise ResultLoadError(f"Failed to reconstruct BenchmarkResults: {e}") from e

    result.plans_load_error = plans_load_error

    return result, data


def _load_companion_file(main_file: Path, suffix: str) -> tuple[dict[str, Any] | None, str | None]:
    name = main_file.name
    companion_name = name[: -len(".json")] + suffix if name.endswith(".json") else name + suffix
    companion_path = main_file.with_name(companion_name)

    if not companion_path.exists():
        return None, None

    try:
        with open(companion_path, encoding="utf-8") as f:
            return json.load(f), None
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Companion file %s exists but could not be loaded: %s", companion_path, e)
        return None, f"{companion_path.name}: {e}"


def _validate_versioned_query_extensions(data: dict[str, Any]) -> None:
    version = str(result_schema_version_value(data) or "")
    if supports_row_count_validation(version):
        return

    for index, query in enumerate(data.get("queries", [])):
        if isinstance(query, dict) and "row_count_validation" in query:
            raise ValueError(
                f"queries[{index}].row_count_validation requires schema version "
                f"{ROW_COUNT_VALIDATION_SCHEMA_VERSION} or later, got {version!r}"
            )


def reconstruct_benchmark_results(
    data: dict[str, Any],
    plans_data: dict[str, Any] | None = None,
    tuning_data: dict[str, Any] | None = None,
    applied_data: dict[str, Any] | None = None,
) -> BenchmarkResults:
    _validate_versioned_query_extensions(data)
    run_section = data.get("run", {})
    benchmark_section = data.get("benchmark", {})
    platform_section = data.get("platform", {})
    summary_section = data.get("summary", {})
    execution_section = data.get("execution", {})
    provenance_section = data.get("provenance", {})

    timestamp = _parse_timestamp(run_section.get("timestamp", ""))
    query_results = _reconstruct_query_results(data.get("queries", []), data.get("errors", []), plans_data)

    timing = _extract_timing_metrics(summary_section)
    tpc = _extract_tpc_metrics(summary_section)
    platform_info = _extract_platform_info(platform_section)
    config_section = data.get("config", {})
    if result_schema_version_value(data) in ("2.0", "2.1", "2.2") and not platform_info.get("variant"):
        platform_info["variant"] = resolve_variant_platform_id(
            (platform_section.get("name"),), config_section.get("mode") or platform_info.get("execution_mode")
        )
    execution_metadata = _extract_execution_metadata(execution_section)
    if isinstance(config_section.get("execution_engine"), str) and config_section["execution_engine"]:
        execution_metadata = execution_metadata or {}
        execution_metadata["run_config"] = {
            **execution_metadata.get("run_config", {}),
            "execution_engine": config_section["execution_engine"],
        }
    recorded_phases = _recorded_phase_status(data.get("phases", {}))
    if recorded_phases:
        execution_metadata = {**(execution_metadata or {}), "phase_status": recorded_phases}
    tuning = _extract_tuning_info(platform_section, tuning_data)
    environment_section = data.get("environment", {})
    system_profile = _extract_system_profile(environment_section)
    execution_environment = _extract_execution_environment(environment_section)
    cost_summary = _extract_cost_summary(data.get("cost", {}), data.get("normalized_cost"), summary_section.get("cost"))
    plans_captured, plan_failures = _extract_plans_info(plans_data)

    queries_counts = summary_section.get("queries", {})
    tables_section = data.get("tables", {})
    table_statistics = {
        name: {"rows": stats.get("rows"), "load_time_ms": stats.get("load_ms")}
        for name, stats in tables_section.items()
    }

    execution_phases = _reconstruct_execution_phases(data.get("phases", {}))
    native_comparison = _reconstruct_native_comparison(data.get("comparisons", {}))

    return BenchmarkResults(
        benchmark_name=benchmark_section.get("name", "Unknown"),
        platform=platform_section.get("name", "Unknown"),
        scale_factor=benchmark_section.get("scale_factor", 1.0),
        execution_id=run_section.get("id", ""),
        timestamp=timestamp,
        duration_seconds=run_section.get("total_duration_ms", 0) / 1000.0,
        total_queries=queries_counts.get("total", 0),
        successful_queries=queries_counts.get("passed", 0),
        failed_queries=queries_counts.get("failed", 0),
        query_results=query_results,
        total_execution_time=timing["total_execution_time"],
        average_query_time=timing["average_query_time"],
        data_loading_time=timing["data_loading_time"],
        total_rows_loaded=timing["total_rows_loaded"],
        table_statistics=table_statistics,
        power_at_size=tpc["power_at_size"],
        throughput_at_size=tpc["throughput_at_size"],
        qph_at_size=tpc["qph_at_size"],
        refresh_included=tpc["refresh_included"],
        qphh_unavailable=tpc["qphh_unavailable"],
        geometric_mean_execution_time=tpc["geometric_mean_execution_time"],
        test_execution_type=benchmark_section.get("mode", "standard"),
        validation_status=summary_section.get("validation", "NOT_RUN"),
        execution_metadata=execution_metadata,
        execution_environment=execution_environment,
        platform_deployment=platform_section.get("deployment"),
        platform_cloud=platform_section.get("cloud"),
        platform_compute=read_platform_compute(platform_section),
        platform_storage=platform_section.get("storage"),
        platform_raw_config=platform_section.get("raw_config"),
        platform_raw_metadata=platform_section.get("raw_metadata"),
        system_profile=system_profile,
        query_subset=run_section.get("query_subset"),
        platform_info=platform_info,
        tunings_applied=tuning["tunings_applied"],
        tuning_source_file=tuning["tuning_source_file"],
        tuning_config_hash=tuning["tuning_config_hash"],
        tuning_source=tuning["tuning_source"],
        tuning_validation_status=tuning["tuning_validation_status"],
        tuning_legacy_source=tuning["tuning_legacy_source"],
        applied_tuning_ledger=_extract_applied_ledger(platform_section, applied_data),
        applied_ledger_hash=_extract_applied_ledger_hash(platform_section, applied_data),
        query_plans_captured=plans_captured,
        plan_capture_failures=plan_failures,
        cost_summary=cost_summary,
        execution_phases=execution_phases,
        native_comparison=native_comparison,
        _benchmark_id_override=benchmark_section.get("id"),
        compliance_class=benchmark_section.get("compliance_class"),
        dataset_version=benchmark_section.get("dataset_version"),
        data_generation_version=_coerce_datagen_version(benchmark_section.get("data_generation_version")),
        data_generation_hash=_coerce_datagen_hash(benchmark_section.get("data_generation_hash")),
        flightdata_source_provenance=benchmark_section.get("source_provenance"),
        manifest_hash=benchmark_section.get("manifest_hash"),
        data_archive_hash=benchmark_section.get("data_archive_hash"),
        funding=provenance_section.get("funding"),
        result_source=provenance_section.get("source"),
        execution_engine=read_execution_engine(data),
    )


def _coerce_datagen_version(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _coerce_datagen_hash(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _extract_execution_metadata(execution_section: dict[str, Any]) -> dict[str, Any] | None:
    metadata: dict[str, Any] = {}
    translation = execution_section.get("translation")
    if isinstance(translation, dict):
        metadata["translation"] = translation
    comparability = execution_section.get("variant_comparability")
    if isinstance(comparability, dict):
        metadata["variant_comparability"] = comparability
    return metadata or None


def _parse_timestamp(timestamp_str: str) -> datetime:
    try:
        return datetime.fromisoformat(timestamp_str)
    except (ValueError, TypeError):
        return datetime.now()


def _extract_timing_metrics(summary_section: dict[str, Any]) -> dict[str, Any]:
    timing = summary_section.get("timing", {})
    data_section = summary_section.get("data", {})
    return {
        "total_execution_time": timing.get("total_ms", 0.0) / 1000.0,
        "average_query_time": timing.get("avg_ms", 0.0) / 1000.0,
        "data_loading_time": data_section.get("load_time_ms", 0.0) / 1000.0,
        "total_rows_loaded": data_section.get("rows_loaded", 0),
    }


def _extract_tpc_metrics(summary_section: dict[str, Any]) -> dict[str, Any]:
    tpc = summary_section.get("tpc_metrics", {})
    timing = summary_section.get("timing", {})
    geometric_mean_ms = timing.get("geometric_mean_ms")
    return {
        "power_at_size": tpc.get("power_at_size"),
        "throughput_at_size": tpc.get("throughput_at_size"),
        "qph_at_size": tpc.get("qphh_at_size") or tpc.get("qphds_at_size"),
        "refresh_included": tpc.get("refresh_included"),
        "qphh_unavailable": tpc.get("qphh_unavailable"),
        "geometric_mean_execution_time": geometric_mean_ms / 1000.0 if geometric_mean_ms else None,
    }


def _extract_platform_info(platform_section: dict[str, Any]) -> dict[str, Any]:
    info: dict[str, Any] = {
        "name": platform_section.get("name"),
        "version": platform_section.get("version"),
        "variant": platform_section.get("variant"),
    }
    if platform_section.get("config"):
        info.update(platform_section["config"])
    for key in ("execution_engine", "gateway"):
        if isinstance(platform_section.get(key), dict):
            info[key] = platform_section[key]
    return info


def _extract_tuning_info(platform_section: dict[str, Any], tuning_data: dict[str, Any] | None) -> dict[str, Any]:
    from benchbox.core.tuning.applied_ledger import NOT_VALIDATED

    tunings_applied = None
    tuning_source_file = None
    tuning_config_hash = None
    tuning_source = None
    tuning_validation_status = None
    tuning_legacy_source = None

    tuning_summary = platform_section.get("tuning", {})
    if tuning_summary:
        tuning_config_hash = tuning_summary.get("requested_config_hash") or tuning_summary.get("hash")
        tuning_source = tuning_summary.get("tuning_source")
        tuning_validation_status = tuning_summary.get("validation_status")
        legacy_source = tuning_summary.get("source")
        if legacy_source in ("yaml", "auto"):
            tuning_legacy_source = legacy_source
        tuning_source_file = tuning_summary.get("source_file") or (
            "yaml" if tuning_summary.get("source") == "yaml" else None
        )
        inline_requested = tuning_summary.get("requested")
        if isinstance(inline_requested, dict) and inline_requested:
            tunings_applied = _flatten_requested_tuning(inline_requested)

    if tuning_data:
        requested = tuning_data.get("requested")
        if requested:
            tunings_applied = _flatten_requested_tuning(requested)
        elif tuning_data.get("clauses"):
            tunings_applied = tuning_data["clauses"]
        tuning_source_file = tuning_data.get("source_file") or tuning_source_file
        tuning_config_hash = tuning_data.get("requested_config_hash") or tuning_data.get("hash") or tuning_config_hash
        tuning_source = tuning_data.get("tuning_source") or tuning_source
        tuning_validation_status = tuning_data.get("validation_status") or tuning_validation_status

    if tuning_validation_status == NOT_VALIDATED:
        tuning_validation_status = None

    return {
        "tunings_applied": tunings_applied,
        "tuning_source_file": tuning_source_file,
        "tuning_config_hash": tuning_config_hash,
        "tuning_source": tuning_source,
        "tuning_validation_status": tuning_validation_status,
        "tuning_legacy_source": tuning_legacy_source,
    }


def _extract_applied_ledger(
    platform_section: dict[str, Any],
    applied_data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if applied_data:
        return applied_data

    tuning_summary = platform_section.get("tuning")
    if not isinstance(tuning_summary, dict):
        return None
    applied = tuning_summary.get("applied")
    if not isinstance(applied, dict) or not applied:
        return None

    ledger = dict(applied)
    ledger_hash = tuning_summary.get("applied_ledger_hash")
    if ledger_hash and "applied_ledger_hash" not in ledger:
        ledger["applied_ledger_hash"] = ledger_hash
    return ledger


def _extract_applied_ledger_hash(
    platform_section: dict[str, Any],
    applied_data: dict[str, Any] | None,
) -> str | None:
    if applied_data and applied_data.get("applied_ledger_hash"):
        return applied_data["applied_ledger_hash"]
    tuning_summary = platform_section.get("tuning")
    if isinstance(tuning_summary, dict):
        return tuning_summary.get("applied_ledger_hash")
    return None


def _flatten_requested_tuning(requested: dict[str, Any]) -> dict[str, Any]:
    flattened = dict(requested.get("constraints") or {})
    for key in ("platform_optimizations", "table_tunings"):
        if key in requested:
            flattened[key] = requested[key]
    return flattened


def _extract_system_profile(environment_section: dict[str, Any]) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    if not environment_section:
        return profile

    source = dict(environment_section)
    client_host = environment_section.get("client_host")
    if isinstance(client_host, dict):
        source.update({key: value for key, value in client_host.items() if value is not None})

    _env_field_map = {
        "arch": "architecture",
        "cpu_count": "cpu_count",
        "memory_gb": "memory_gb",
        "python": "python_version",
        "machine_id": "machine_id",
    }

    if source.get("os"):
        os_parts = source["os"].split(" ", 1)
        profile["os_type"] = os_parts[0]
        profile["os_release"] = os_parts[1] if len(os_parts) > 1 else ""

    for env_key, profile_key in _env_field_map.items():
        if source.get(env_key):
            profile[profile_key] = source[env_key]

    return profile


def _extract_execution_environment(environment_section: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(environment_section, dict):
        return None

    normalized = {
        key: environment_section[key]
        for key in ("client_host", "platform_runtime", "container", "client_link")
        if isinstance(environment_section.get(key), dict)
    }
    return normalized or None


def _extract_cost_summary(
    cost_section: dict[str, Any],
    normalized_cost_section: dict[str, Any] | None = None,
    scan_bytes_section: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not cost_section and not isinstance(scan_bytes_section, dict):
        return None
    summary: dict[str, Any] = {}
    if cost_section:
        summary.update(
            {
                "total_cost": cost_section.get("total_usd"),
                "cost_model": cost_section.get("model", "estimated"),
            }
        )
    if isinstance(normalized_cost_section, dict):
        summary["normalized_cost"] = normalized_cost_section
    if isinstance(scan_bytes_section, dict):
        summary["scan_bytes"] = dict(scan_bytes_section)
    return summary


def _extract_plans_info(plans_data: dict[str, Any] | None) -> tuple[int, int]:
    if not plans_data:
        return 0, 0
    return plans_data.get("plans_captured", 0), plans_data.get("capture_failures", 0)


def _reconstruct_query_results(
    queries_list: list[dict[str, Any]],
    errors_list: list[dict[str, Any]],
    plans_data: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    plan_entries = _index_plan_entries(plans_data)
    results: list[dict[str, Any]] = []

    query_errors_by_id: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for error in errors_list:
        if error.get("phase") != "query":
            continue
        qid = error.get("query_id")
        if qid is None:
            continue
        query_errors_by_id[normalize_query_id(qid)].append(error)

    consumed_error_ids: set[int] = set()

    for q in queries_list:
        execution = query_execution_from_compact_v2(q)
        query_id = execution.query_id
        status = execution.status
        result = query_execution_to_legacy_dict(execution)
        result.setdefault("execution_time_ms", None)
        result.setdefault("rows_returned", None)

        if status not in ("SUCCESS", "SKIPPED") and query_id is not None:
            error_queue = query_errors_by_id.get(normalize_query_id(query_id))
            if error_queue:
                error = error_queue.popleft()
                consumed_error_ids.add(id(error))
                result["error_type"] = error.get("type")
                result["error_message"] = error.get("message")

        _attach_plan(
            result,
            _lookup_plan_entry(plan_entries, query_id, q.get("stream", 0), q.get("test_type")),
        )
        results.append(result)

    for error in errors_list:
        if error.get("phase") != "query":
            continue
        if id(error) in consumed_error_ids:
            continue
        results.append(
            {
                "query_id": error.get("query_id"),
                "status": "FAILED",
                "error_type": error.get("type"),
                "error_message": error.get("message"),
            }
        )

    return results


def _attach_plan(result: dict[str, Any], plan_entry: dict[str, Any] | None) -> None:
    if not plan_entry:
        return

    plan_dict = plan_entry.get("plan")
    if plan_dict is not None:
        result["query_plan"] = QueryPlanDAG.from_dict(plan_dict)
    if plan_entry.get("fingerprint"):
        result["plan_fingerprint"] = plan_entry["fingerprint"]
    if plan_entry.get("fingerprint_normalized"):
        result["plan_fingerprint_normalized"] = plan_entry["fingerprint_normalized"]
    if plan_entry.get("capture_time_ms") is not None:
        result["plan_capture_time_ms"] = plan_entry["capture_time_ms"]


def _index_plan_entries(plans_data: dict[str, Any] | None) -> dict[str, Any]:
    raw_entries: dict[str, Any] = (plans_data or {}).get("queries") or {}
    indexed: dict[str, Any] = {}
    for key, entry in raw_entries.items():
        if "#" in key:
            base_id, _, stream_id = key.rpartition("#")
            bucket = indexed.setdefault(normalize_query_id(base_id), {})
            bucket[stream_id] = entry
        else:
            indexed[normalize_query_id(key)] = entry
    return indexed


def _lookup_plan_entry(
    plan_entries: dict[str, Any], query_id: Any, stream_id: Any, test_type: Any = None
) -> dict[str, Any] | None:
    if query_id is None:
        return None
    entry = plan_entries.get(normalize_query_id(query_id))
    if entry is None or "plan" in entry:
        return entry
    if test_type:
        qualified = entry.get(f"{stream_id}:{test_type}")
        if qualified is not None:
            return qualified
    return entry.get(str(stream_id))


def iter_query_results(results: Any) -> list[dict[str, Any]]:
    return list(getattr(results, "query_results", None) or [])


_RECONSTRUCTED_PHASES = frozenset({"power_test", "throughput_test", "migration"})


def _recorded_phase_status(phases_section: dict[str, Any]) -> dict[str, dict[str, Any]]:
    recorded: dict[str, dict[str, Any]] = {}
    for name, entry in (phases_section or {}).items():
        if name in _RECONSTRUCTED_PHASES or not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if not status or status == "NOT_RUN":
            continue
        recorded[name] = {key: entry[key] for key in ("status", "duration_ms") if key in entry}
    return recorded


def _reconstruct_execution_phases(phases_section: dict[str, Any]) -> ExecutionPhases | None:
    if not phases_section:
        return None

    power = None
    power_data = phases_section.get("power_test")
    if power_data and power_data.get("status") != "NOT_RUN":
        power = PowerTestPhase(
            start_time="",
            end_time="",
            duration_ms=power_data.get("duration_ms", 0),
            query_executions=[],
            geometric_mean_time=0.0,
            power_at_size=0.0,
            refresh_first_set=power_data.get("refresh_first_set"),
            refresh_functions=power_data.get("refresh_functions"),
            query_count=power_data.get("query_count"),
            success=power_data.get("status") == "COMPLETED",
        )

    migration = None
    mig = phases_section.get("migration")
    if mig and mig.get("status") != "NOT_RUN":
        migration = MigrationPhase(
            duration_ms=mig.get("duration_ms", 0),
            status=mig.get("status", "UNKNOWN"),
            tables_migrated=mig.get("tables_migrated", 0),
            tables_failed=mig.get("tables_failed", 0),
            storage_before_bytes=mig.get("storage_before_bytes", 0),
            storage_after_bytes=mig.get("storage_after_bytes", 0),
            storage_delta_bytes=mig.get("storage_delta_bytes", 0),
            per_table_stats={},
        )

    throughput = None
    throughput_data = phases_section.get("throughput_test")
    if throughput_data and throughput_data.get("status") != "NOT_RUN":
        streams = [
            ThroughputStream(
                stream_id=stream.get("stream_id", 0),
                start_time="",
                end_time="",
                duration_ms=0,
                query_executions=[],
                success=stream.get("success", False),
                error_message=stream.get("error"),
            )
            for stream in throughput_data.get("stream_results", [])
        ]
        outstanding_work = throughput_data.get("outstanding_work")
        stream_numbering = throughput_data.get("stream_numbering")
        refresh_stream = throughput_data.get("refresh_stream")
        throughput = ThroughputTestPhase(
            start_time="",
            end_time="",
            duration_ms=throughput_data.get("duration_ms", 0),
            num_streams=throughput_data.get("num_streams", len(streams)),
            streams=streams,
            total_queries_executed=0,
            throughput_at_size=None,
            success=throughput_data.get("status") == "COMPLETED",
            errors=list(throughput_data.get("errors", [])),
            outstanding_work=(
                {
                    "stream_ids": list(outstanding_work.get("stream_ids", [])),
                    "cleanup_state": outstanding_work.get("cleanup_state", "complete"),
                }
                if isinstance(outstanding_work, dict)
                else None
            ),
            stream_numbering=dict(stream_numbering) if isinstance(stream_numbering, dict) else None,
            refresh_stream=dict(refresh_stream) if isinstance(refresh_stream, dict) else None,
        )

    if power is None and migration is None and throughput is None:
        return None

    return ExecutionPhases(
        setup=SetupPhase(),
        power_test=power,
        migration=migration,
        throughput_test=throughput,
    )


def _reconstruct_native_comparison(comparisons_section: dict[str, Any]) -> NativeComparison | None:
    if not comparisons_section:
        return None

    nd = comparisons_section.get("native_duckdb")
    if not nd:
        return None

    entries = [
        NativeComparisonEntry(
            query_id=q.get("id", ""),
            pg_duckdb_ms=q.get("pg_duckdb_ms", 0.0),
            duckdb_ms=q.get("duckdb_ms", 0.0),
            delta_ms=q.get("delta_ms", 0.0),
        )
        for q in nd.get("queries", [])
    ]

    return NativeComparison(
        generated_at=nd.get("generated_at", ""),
        scale_factor=nd.get("scale_factor", 0.0),
        total_queries=nd.get("total_queries", 0),
        mean_delta_ms=nd.get("mean_delta_ms", 0.0),
        max_delta_ms=nd.get("max_delta_ms", 0.0),
        entries=entries,
    )


__all__ = [
    "find_latest_result",
    "iter_query_results",
    "load_result_file",
    "reconstruct_benchmark_results",
    "ResultLoadError",
    "UnsupportedSchemaError",
]
