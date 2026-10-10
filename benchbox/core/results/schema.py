from __future__ import annotations

import logging
import statistics
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from benchbox.core.cost.models import normalized_cost_allows_direct_total
from benchbox.core.execution_variant import VariantIdError, derive_variant_id, resolve_variant_platform_id
from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE
from benchbox.core.results.environment import (
    build_environment_payload,
    build_platform_metadata_payload,
)
from benchbox.core.results.execution_variant import execution_engine_payload, gateway_payload
from benchbox.core.results.metrics import UNOFFICIAL_COMPLIANCE_CLASSES, percentile_ms
from benchbox.core.results.platform_options import sanitize_platform_options
from benchbox.core.results.query_execution import (
    QueryExecutionContractError,
    normalize_row_count_validation,
    query_execution_from_legacy_dict,
    query_execution_to_compact_v2,
)
from benchbox.core.results.query_normalizer import normalize_query_id
from benchbox.core.results.query_plan_models import DEFAULT_PLAN_MAX_DEPTH, QueryPlanDAG
from benchbox.core.results.schema_policy import (
    CURRENT_SCHEMA_VERSION,
    ROW_COUNT_VALIDATION_SCHEMA_VERSION,
    RUNTIME_SCHEMA_POLICY,
    result_schema_version_value,
    supports_row_count_validation,
)
from benchbox.validation.bundle import REQUIRED_TOP_KEYS

if TYPE_CHECKING:
    from benchbox.core.results.models import BenchmarkResults, QueryExecution

SCHEMA_VERSION = CURRENT_SCHEMA_VERSION

logger = logging.getLogger(__name__)


def _load_schema_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("schema_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_SCHEMA_SPECS = _load_schema_specs()
CANONICAL_KEY_ORDER = list(_SCHEMA_SPECS["canonical_key_order"])
QUERY_KEY_ORDER = list(_SCHEMA_SPECS["query_key_order"])
CONFIG_KEY_ORDER = list(_SCHEMA_SPECS["config_key_order"])
PHASE_KEY_ORDER = list(_SCHEMA_SPECS["phase_key_order"])
PLATFORM_KEY_ORDER = list(_SCHEMA_SPECS["platform_key_order"])
PLATFORM_EXECUTION_ENGINE_KEY_ORDER = list(_SCHEMA_SPECS["platform_execution_engine_key_order"])
PLATFORM_GATEWAY_KEY_ORDER = list(_SCHEMA_SPECS["platform_gateway_key_order"])
PLATFORM_COMPUTE_LEAD_KEYS = tuple(_SCHEMA_SPECS["platform_compute_lead_keys"])
PLATFORM_DEPLOYMENT_LEAD_KEYS = tuple(_SCHEMA_SPECS["platform_deployment_lead_keys"])
DRIVER_METADATA_KEYS = tuple(_SCHEMA_SPECS["driver_metadata_keys"])
ENGINE_VERSION_KEYS = tuple(_SCHEMA_SPECS["engine_version_keys"])
_DRIVER_PLATFORM_KEYS = [tuple(item) for item in _SCHEMA_SPECS["driver_platform_keys"]]


def order_dict(d: dict[str, Any], key_order: list[str]) -> dict[str, Any]:
    from collections import OrderedDict

    ordered: OrderedDict[str, Any] = OrderedDict()
    for key in key_order:
        if key in d:
            ordered[key] = d[key]
    for key in sorted(d.keys()):
        if key not in ordered:
            ordered[key] = d[key]
    return ordered


def lead_keys(d: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    leading = {key: d[key] for key in keys if key in d}
    return {**leading, **{key: value for key, value in d.items() if key not in leading}}


def _normalize_query_result(qr: Any) -> QueryExecution:
    return query_execution_from_legacy_dict(qr, default_iteration=1, default_stream_id=0)


def _round_duration_ms_for_export(value: float) -> float:
    rounded = round(value, 1)
    if value <= 0 or rounded > 0:
        return rounded
    return float(f"{value:.6g}")


class SchemaV2ValidationError(ValueError):
    pass


class SchemaV2Validator:
    REQUIRED_KEYS = REQUIRED_TOP_KEYS
    OPTIONAL_KEYS = (
        "version",
        "environment",
        "tables",
        "errors",
        "cost",
        "normalized_cost",
        "export",
        "tuning",
        "execution",
        "provenance",
        "config",
        "phases",
        "comparisons",
    )

    RUN_REQUIRED = ("id", "timestamp", "total_duration_ms", "query_time_ms")
    BENCHMARK_REQUIRED = ("id", "name", "scale_factor")
    PLATFORM_REQUIRED = ("name",)
    SUMMARY_REQUIRED = ("queries", "timing")

    def validate(self, payload: dict[str, Any]) -> None:
        version = result_schema_version_value(payload)
        missing_top = [key for key in self.REQUIRED_KEYS if key not in payload and key != "result_schema_version"]
        if version is None and "result_schema_version" in self.REQUIRED_KEYS:
            missing_top.insert(0, "result_schema_version")
        if missing_top:
            raise SchemaV2ValidationError(f"schema v2.0 payload missing keys: {missing_top}")

        version_decision = RUNTIME_SCHEMA_POLICY.evaluate(version)
        if not version_decision.accepted:
            raise SchemaV2ValidationError(version_decision.error_message())

        run = payload.get("run", {})
        if not isinstance(run, Mapping):
            raise SchemaV2ValidationError("run block must be a mapping")
        missing_run = [key for key in self.RUN_REQUIRED if key not in run]
        if missing_run:
            raise SchemaV2ValidationError(f"run block missing keys: {missing_run}")

        benchmark = payload.get("benchmark", {})
        if not isinstance(benchmark, Mapping):
            raise SchemaV2ValidationError("benchmark block must be a mapping")
        missing_benchmark = [key for key in self.BENCHMARK_REQUIRED if key not in benchmark]
        if missing_benchmark:
            raise SchemaV2ValidationError(f"benchmark block missing keys: {missing_benchmark}")

        platform = payload.get("platform", {})
        if not isinstance(platform, Mapping):
            raise SchemaV2ValidationError("platform block must be a mapping")
        missing_platform = [key for key in self.PLATFORM_REQUIRED if key not in platform]
        if missing_platform:
            raise SchemaV2ValidationError(f"platform block missing keys: {missing_platform}")

        summary = payload.get("summary", {})
        if not isinstance(summary, Mapping):
            raise SchemaV2ValidationError("summary block must be a mapping")
        missing_summary = [key for key in self.SUMMARY_REQUIRED if key not in summary]
        if missing_summary:
            raise SchemaV2ValidationError(f"summary block missing keys: {missing_summary}")

        queries = payload.get("queries")
        if not isinstance(queries, list):
            raise SchemaV2ValidationError("queries must be a list")
        for index, query in enumerate(queries):
            if not isinstance(query, Mapping) or "row_count_validation" not in query:
                continue
            if not supports_row_count_validation(version):
                raise SchemaV2ValidationError(
                    f"queries[{index}].row_count_validation requires schema version "
                    f"{ROW_COUNT_VALIDATION_SCHEMA_VERSION} or later"
                )
            try:
                normalize_row_count_validation(query.get("row_count_validation"), rows_returned=query.get("rows"))
            except QueryExecutionContractError as exc:
                raise SchemaV2ValidationError(f"queries[{index}].row_count_validation is invalid: {exc}") from exc

        unexpected = set(payload.keys()) - set(self.REQUIRED_KEYS) - set(self.OPTIONAL_KEYS)
        if unexpected:
            raise SchemaV2ValidationError(f"schema v2.0 payload contains unexpected keys: {sorted(unexpected)}")


def build_result_payload(result: BenchmarkResults, *, sanitize_platform_secrets: bool = True) -> dict[str, Any]:
    query_times_ms, queries_list, errors_list, iterations_set, streams_set = _build_query_results_section(result)

    measurement_queries = [q for q in queries_list if q.get("run_type") == "measurement"]
    total_queries = len(measurement_queries)
    successful_queries = len([q for q in measurement_queries if q.get("status") == "SUCCESS"])
    skipped_queries = len([q for q in measurement_queries if q.get("status") == "SKIPPED"])
    failed_count = total_queries - successful_queries - skipped_queries

    summary = _build_summary_section(
        result, query_times_ms, total_queries, successful_queries, failed_count, skipped_queries
    )
    scan_bytes = _aggregate_scan_bytes(result)
    if scan_bytes:
        summary["cost"] = scan_bytes
    run = _build_run_section(result, query_times_ms, iterations_set, streams_set)
    benchmark = _build_benchmark_section(result)
    driver_metadata = _collect_driver_metadata(result)
    platform = _build_platform_section(result, driver_metadata, sanitize_platform_secrets=sanitize_platform_secrets)

    environment = _build_environment_block(result)

    tables = _build_tables_block(result.table_statistics)

    config_block = _build_config_block(result)
    phases_block = _build_phases_block(result)

    if result.execution_phases:
        table_errors = _extract_table_errors(result.execution_phases)
        errors_list.extend(table_errors)

    payload: dict[str, Any] = {
        "result_schema_version": SCHEMA_VERSION,
        "version": SCHEMA_VERSION,
        "run": order_dict(
            run, ["id", "timestamp", "total_duration_ms", "query_time_ms", "iterations", "streams", "query_subset"]
        ),
        "benchmark": order_dict(benchmark, ["id", "name", "scale_factor", "test_type"]),
        "platform": order_dict(platform, PLATFORM_KEY_ORDER),
        "config": order_dict(config_block, CONFIG_KEY_ORDER),
        "summary": order_dict(summary, ["queries", "timing", "data", "cost", "validation", "tpc_metrics"]),
        "phases": order_dict(phases_block, PHASE_KEY_ORDER),
        "queries": queries_list,
    }
    if platform.get("gateway", {}).get("routed") is True:
        payload["tpc_compliant"] = False
        gateway_name = platform["gateway"].get("name")
        from benchbox.core.gateway import supported_gateways

        gateways = supported_gateways(str(result.platform))
        allowed_engines = set()
        if gateway_name in gateways:
            allowed_engines = set(gateways[gateway_name].routes_to.keys())

        query_engines = set()
        for q in queries_list:
            engine = q.get("execution_engine")
            if engine:
                if allowed_engines and engine not in allowed_engines:
                    q["execution_engine"] = "unknown"
                    engine = "unknown"
                query_engines.add(engine)

        if len(query_engines) > 1 and "execution_engine" in payload["platform"]:
            engine_map = dict(payload["platform"]["execution_engine"])
            engine_map["observed"] = "mixed"
            payload["platform"]["execution_engine"] = order_dict(engine_map, PLATFORM_EXECUTION_ENGINE_KEY_ORDER)

    if environment:
        payload["environment"] = environment
    if tables:
        payload["tables"] = tables
    if errors_list:
        errors_list.sort(key=lambda e: (e.get("phase", ""), e.get("query_id", ""), e.get("type", "")))
        payload["errors"] = errors_list

    _add_comparisons_section(payload, result)
    _add_cost_section(payload, result)
    _add_execution_section(payload, result, driver_metadata)
    _add_provenance_section(payload, result)

    return order_dict(payload, CANONICAL_KEY_ORDER)


def _build_query_results_section(
    result: BenchmarkResults,
) -> tuple[list[float], list[dict[str, Any]], list[dict[str, Any]], set[int], set[int]]:
    query_times_ms: list[float] = []
    queries_list: list[dict[str, Any]] = []
    errors_list: list[dict[str, Any]] = []
    iterations_set: set[int] = set()
    streams_set: set[int] = set()

    normalized_results = [_normalize_query_result(qr) for qr in (result.query_results or [])]

    throughput_streams_set: set[int] = set()
    for qr in normalized_results:
        iteration = qr.iteration
        stream_id = qr.stream_id
        if iteration is not None:
            iterations_set.add(int(iteration))
        if stream_id is not None:
            streams_set.add(int(stream_id))
            if qr.test_type == "throughput":
                throughput_streams_set.add(int(stream_id))
    if throughput_streams_set:
        streams_set = throughput_streams_set

    for qr in normalized_results:
        query_id = normalize_query_id(qr.query_id)
        status = qr.status
        exec_time_ms = qr.execution_time_ms
        iteration = int(qr.iteration if qr.iteration is not None else 1)
        stream_id = int(qr.stream_id if qr.stream_id is not None else 0)
        run_type = qr.run_type if qr.run_type is not None else ("warmup" if iteration == 0 else "measurement")

        if status == "SUCCESS":
            if exec_time_ms is not None and run_type == "measurement":
                query_times_ms.append(exec_time_ms)
        elif status != "SKIPPED":
            error_entry = {
                "phase": "query",
                "query_id": str(query_id),
            }
            error_type = qr.error_type or (qr.error_message.split(":")[0] if qr.error_message else "UnknownError")
            error_entry["type"] = error_type or "UnknownError"
            error_entry["message"] = qr.error_message or "Query failed"
            errors_list.append(error_entry)

        entry = query_execution_to_compact_v2(qr)
        entry["id"] = str(query_id)
        if exec_time_ms is not None:
            entry["ms"] = _round_duration_ms_for_export(float(exec_time_ms))
        entry["iter"] = iteration
        entry["stream"] = stream_id
        entry["run_type"] = run_type

        queries_list.append(order_dict(entry, QUERY_KEY_ORDER))

    return query_times_ms, queries_list, errors_list, iterations_set, streams_set


def _aggregate_scan_bytes(result: BenchmarkResults) -> dict[str, Any] | None:
    preserved = result.cost_summary.get("scan_bytes") if isinstance(result.cost_summary, Mapping) else None
    if isinstance(preserved, Mapping) and preserved:
        return dict(preserved)

    total_billed = 0
    total_scanned = 0
    billed_seen = False
    scanned_seen = False
    for qr in result.query_results or []:
        try:
            execution = _normalize_query_result(qr)
        except Exception:
            continue
        resource_usage = getattr(execution, "resource_usage", None)
        if not isinstance(resource_usage, dict):
            continue
        billed = resource_usage.get("bytes_billed")
        if isinstance(billed, (int, float)) and not isinstance(billed, bool) and billed >= 0:
            total_billed += int(billed)
            billed_seen = True
        for key in ("bytes_processed", "data_scanned_bytes", "bytes_scanned"):
            scanned = resource_usage.get(key)
            if isinstance(scanned, (int, float)) and not isinstance(scanned, bool) and scanned >= 0:
                total_scanned += int(scanned)
                scanned_seen = True
                break
    if not billed_seen and not scanned_seen:
        return None
    totals: dict[str, Any] = {}
    if billed_seen:
        totals["total_bytes_billed"] = total_billed
    if scanned_seen:
        totals["total_bytes_scanned"] = total_scanned
    return totals or None


def _build_summary_section(
    result: BenchmarkResults,
    query_times_ms: list[float],
    total_queries: int,
    successful_queries: int,
    failed_count: int,
    skipped_queries: int = 0,
) -> dict[str, Any]:
    timing_stats = _compute_timing_stats(query_times_ms)

    summary: dict[str, Any] = {
        "queries": {
            "total": total_queries,
            "passed": successful_queries,
            "failed": failed_count,
        },
        "timing": timing_stats,
    }
    if skipped_queries:
        summary["queries"]["skipped"] = skipped_queries

    if result.total_rows_loaded or result.data_loading_time:
        data_stats: dict[str, Any] = {}
        if result.total_rows_loaded:
            data_stats["rows_loaded"] = result.total_rows_loaded
        if result.data_loading_time:
            data_stats["load_time_ms"] = round(result.data_loading_time * 1000, 1)
        if data_stats:
            summary["data"] = data_stats

    if result.validation_status:
        summary["validation"] = result.validation_status.lower()

    tpc_metrics = _build_tpc_metrics(result)
    if tpc_metrics:
        summary["tpc_metrics"] = tpc_metrics

    return summary


def _build_run_section(
    result: BenchmarkResults,
    query_times_ms: list[float],
    iterations_set: set[int],
    streams_set: set[int],
) -> dict[str, Any]:
    run: dict[str, Any] = {
        "id": result.execution_id,
        "timestamp": result.timestamp.isoformat() if result.timestamp else datetime.now().isoformat(),
        "total_duration_ms": round(result.duration_seconds * 1000),
        "query_time_ms": round(sum(query_times_ms)),
    }
    run["iterations"] = max(iterations_set) if iterations_set else 1
    run["streams"] = len(streams_set) if streams_set else 1
    if result.query_subset:
        run["query_subset"] = result.query_subset
    return run


def _build_benchmark_section(result: BenchmarkResults) -> dict[str, Any]:
    benchmark_name = _shorten_benchmark_name(result.benchmark_name)
    section: dict[str, Any] = {
        "id": result.benchmark_id,
        "name": benchmark_name,
        "scale_factor": result.scale_factor,
        "test_type": result.test_execution_type,
    }
    compliance_class = getattr(result, "compliance_class", None)
    if compliance_class is not None:
        section["compliance_class"] = compliance_class
    if getattr(result, "data_generation_version", None) is not None:
        section["data_generation_version"] = result.data_generation_version
    if getattr(result, "data_generation_hash", None) is not None:
        section["data_generation_hash"] = result.data_generation_hash
    source_provenance = getattr(result, "flightdata_source_provenance", None)
    if source_provenance is not None:
        section["source_provenance"] = source_provenance
    section.update(_dataset_identity_fields(result))
    return section


def _dataset_identity_fields(result: BenchmarkResults) -> dict[str, str]:
    captured_identity = {
        "dataset_version": getattr(result, "dataset_version", None),
        "manifest_hash": getattr(result, "manifest_hash", None),
        "data_archive_hash": getattr(result, "data_archive_hash", None),
    }
    if any(value is not None for value in captured_identity.values()):
        return {key: str(value) for key, value in captured_identity.items() if value is not None}

    benchmark_id = result.benchmark_id
    try:
        from benchbox.core.benchmark_registry import get_benchmark_metadata
        from benchbox.core.data_fetch import load_manifest

        metadata = get_benchmark_metadata(benchmark_id) or {}
        manifest_rel = metadata.get("data_manifest")
        if not manifest_rel:
            return {}
        repo_root = Path(__file__).resolve().parents[3]
        manifest = load_manifest(repo_root / str(manifest_rel))
    except Exception as exc:  # pragma: no cover
        logger.warning("Unable to load dataset identity for %s: %s", benchmark_id, exc)
        return {}
    return {
        "dataset_version": manifest.dataset_version,
        "manifest_hash": manifest.manifest_hash,
        "data_archive_hash": manifest.data_archive_hash,
    }


def _build_platform_section(
    result: BenchmarkResults,
    driver_metadata: dict[str, Any],
    *,
    sanitize_platform_secrets: bool = True,
) -> dict[str, Any]:
    platform_name = str(result.platform).replace(" (DataFrame)", "")
    platform: dict[str, Any] = {"name": platform_name}
    config: dict[str, Any] = {}

    if result.platform_info:
        version = result.platform_info.get("platform_version") or result.platform_info.get("version")
        client_version = result.platform_info.get("client_library_version")
        platform["version"] = version or "unknown"
        platform["client_version"] = client_version or "unknown"

        config = _extract_platform_config(result.platform_info)

    metadata_payload = build_platform_metadata_payload(
        platform_info=result.platform_info,
        platform_config=config,
        deployment=getattr(result, "platform_deployment", None),
        cloud=getattr(result, "platform_cloud", None),
        compute=getattr(result, "platform_compute", None),
        storage=getattr(result, "platform_storage", None),
        raw_config=getattr(result, "platform_raw_config", None) or sanitize_platform_options(config),
        raw_metadata=(
            getattr(result, "platform_raw_metadata", None)
            if getattr(result, "platform_raw_metadata", None) is not None
            else getattr(result, "platform_metadata", None)
        ),
        sanitize_raw_config=True,
    )

    if config:
        public_config = _prune_duplicated_platform_config(config, metadata_payload.get("compute"))
        if public_config:
            platform["config"] = public_config

    platform.update(metadata_payload)
    _add_execution_variant_fields(platform, result)

    for src_key, dest_key in _DRIVER_PLATFORM_KEYS:
        if driver_metadata.get(src_key):
            platform[dest_key] = driver_metadata[src_key]

    engine_metadata = _collect_engine_version_metadata(result)
    if engine_metadata.get("engine_version"):
        platform["engine_version"] = engine_metadata["engine_version"]
    if engine_metadata.get("engine_version_source"):
        platform["engine_version_source"] = engine_metadata["engine_version_source"]

    if "version" not in platform:
        platform["version"] = "unknown"
    if "client_version" not in platform:
        platform["client_version"] = "unknown"

    tuning = _build_tuning_summary(result)
    if tuning:
        platform["tuning"] = tuning

    return platform


def _platform_info_entry(platform_info: Mapping[str, Any], key: str) -> Any:
    if key in platform_info:
        return platform_info[key]
    configuration = platform_info.get("configuration")
    return configuration.get(key) if isinstance(configuration, Mapping) else None


def _requested_execution_engine(result: BenchmarkResults, engine_block: Mapping[str, Any]) -> str:
    if engine_block.get("requested"):
        return str(engine_block["requested"])
    metadata = result.execution_metadata if isinstance(result.execution_metadata, Mapping) else {}
    run_config = metadata.get("run_config")
    requested = run_config.get("execution_engine") if isinstance(run_config, Mapping) else None
    if requested:
        return str(requested)
    execution_engine = getattr(result, "execution_engine", None)
    if isinstance(execution_engine, Mapping) and execution_engine.get("requested"):
        return str(execution_engine["requested"])
    platform_info = result.platform_info if isinstance(result.platform_info, Mapping) else {}
    requested = _platform_info_entry(platform_info, "engine_requested")
    return str(requested) if requested else DEFAULT_EXECUTION_ENGINE


def _variant_id(
    result: BenchmarkResults,
    platform: Mapping[str, Any],
    engine_block: Mapping[str, Any],
    gateway_block: Mapping[str, Any],
) -> str | None:
    platform_info = result.platform_info if isinstance(result.platform_info, Mapping) else {}
    if platform_info.get("variant"):
        return str(platform_info["variant"])
    metadata = result.execution_metadata if isinstance(result.execution_metadata, Mapping) else {}
    execution_mode = platform_info.get("execution_mode") or metadata.get("mode")
    platform_id = resolve_variant_platform_id(
        (
            platform_info.get("platform_type"),
            platform_info.get("platform_name"),
            platform_info.get("platform"),
            platform.get("name"),
        ),
        execution_mode,
    )
    if platform_id is None:
        return None
    deployment = platform.get("deployment")
    try:
        return derive_variant_id(
            platform_id,
            deployment.get("selected") if isinstance(deployment, Mapping) else None,
            _requested_execution_engine(result, engine_block),
            gateway_block.get("name"),
        )
    except VariantIdError as exc:
        logger.warning("Result bundle has no platform variant: %s", exc)
        return None


def _add_execution_variant_fields(platform: dict[str, Any], result: BenchmarkResults) -> None:
    platform_info = result.platform_info if isinstance(result.platform_info, Mapping) else {}
    engine_block = sanitize_platform_options(
        execution_engine_payload(_platform_info_entry(platform_info, "execution_engine"))
    )
    if engine_block:
        platform["execution_engine"] = order_dict(engine_block, PLATFORM_EXECUTION_ENGINE_KEY_ORDER)
    gateway_block = gateway_payload(_platform_info_entry(platform_info, "gateway"))
    if gateway_block:
        platform["gateway"] = order_dict(gateway_block, PLATFORM_GATEWAY_KEY_ORDER)
        if gateway_block.get("routed") is True:
            platform["tpc_compliant"] = False
    for key, lead in (("compute", PLATFORM_COMPUTE_LEAD_KEYS), ("deployment", PLATFORM_DEPLOYMENT_LEAD_KEYS)):
        block = platform.get(key)
        if isinstance(block, Mapping):
            platform[key] = lead_keys(block, lead)
    variant = _variant_id(result, platform, engine_block, gateway_block)
    if variant:
        platform["variant"] = variant


def _add_comparisons_section(payload: dict[str, Any], result: BenchmarkResults) -> None:
    nc = getattr(result, "native_comparison", None)
    if nc is None:
        return
    payload["comparisons"] = {
        "native_duckdb": {
            "generated_at": nc.generated_at,
            "scale_factor": nc.scale_factor,
            "total_queries": nc.total_queries,
            "mean_delta_ms": nc.mean_delta_ms,
            "max_delta_ms": nc.max_delta_ms,
            "queries": [
                {
                    "id": e.query_id,
                    "pg_duckdb_ms": e.pg_duckdb_ms,
                    "duckdb_ms": e.duckdb_ms,
                    "delta_ms": e.delta_ms,
                }
                for e in nc.entries
            ],
        }
    }


def _add_cost_section(payload: dict[str, Any], result: BenchmarkResults) -> None:
    if not result.cost_summary:
        return
    normalized_cost = result.cost_summary.get("normalized_cost")
    if isinstance(normalized_cost, dict):
        payload["normalized_cost"] = normalized_cost
    if not ({"total_cost", "cost_model"} & result.cost_summary.keys()):
        return
    cost_block: dict[str, Any] = {}
    if "total_cost" in result.cost_summary and _normalized_cost_allows_direct_total(normalized_cost):
        cost_block["total_usd"] = result.cost_summary["total_cost"]
    cost_block["model"] = result.cost_summary.get("cost_model", "estimated")
    if cost_block:
        payload["cost"] = cost_block


def _normalized_cost_allows_direct_total(normalized_cost: Any) -> bool:
    return normalized_cost_allows_direct_total(normalized_cost)


def _add_provenance_section(payload: dict[str, Any], result: BenchmarkResults) -> None:
    from benchbox.core.results.provenance import normalize_funding, normalize_source

    funding = getattr(result, "funding", None)
    source = getattr(result, "result_source", None)
    if not funding and not source:
        return

    block: dict[str, Any] = {}
    if funding:
        block["funding"] = normalize_funding(funding)
    if source:
        block["source"] = normalize_source(source)
    payload["provenance"] = block


def _add_execution_section(payload: dict[str, Any], result: BenchmarkResults, driver_metadata: dict[str, Any]) -> None:
    if result.execution_context:
        exec_block = _build_execution_from_context(result, driver_metadata)
        if exec_block:
            payload["execution"] = exec_block

    if "execution" not in payload:
        exec_block = _build_execution_fallback(result, driver_metadata)
        if exec_block:
            payload["execution"] = exec_block


_EXEC_CTX_PASSTHROUGH = [
    ("entry_point", "entry_point"),
    ("invocation_timestamp", "timestamp"),
]
_EXEC_CTX_TRUTHY = [
    ("official", "official", True),
    ("capture_plans", "capture_plans", True),
    ("strict_plan_capture", "strict_plan_capture", True),
    ("non_interactive", "non_interactive", True),
]
_EXEC_CTX_VALUE = ["validation_mode", "query_subset", "tuning_mode"]


def _build_execution_from_context(result: BenchmarkResults, driver_metadata: dict[str, Any]) -> dict[str, Any]:
    exec_block: dict[str, Any] = {}
    ctx = result.execution_context

    for ctx_key, block_key in _EXEC_CTX_PASSTHROUGH:
        if ctx.get(ctx_key):
            exec_block[block_key] = ctx[ctx_key]

    phases = ctx.get("phases")
    if phases and phases != ["power"]:
        exec_block["phases"] = phases

    if ctx.get("seed") is not None:
        exec_block["seed"] = ctx["seed"]

    if ctx.get("compression_enabled"):
        compression = {"type": ctx.get("compression_type", "none")}
        if ctx.get("compression_level"):
            compression["level"] = ctx["compression_level"]
        exec_block["compression"] = compression

    mode_value = _resolve_execution_mode(result, ctx)
    if mode_value:
        exec_block["mode"] = mode_value

    _inject_driver_metadata(exec_block, driver_metadata)

    for ctx_key, block_key, value in _EXEC_CTX_TRUTHY:
        if ctx.get(ctx_key):
            exec_block[block_key] = value

    force_flags = [f for f, key in [("datagen", "force_datagen"), ("upload", "force_upload")] if ctx.get(key)]
    if force_flags:
        exec_block["force"] = force_flags

    for key in _EXEC_CTX_VALUE:
        if ctx.get(key):
            exec_block[key] = ctx[key]

    _inject_translation_metadata(exec_block, result)
    _inject_variant_comparability_metadata(exec_block, result)
    return exec_block


def _build_execution_fallback(result: BenchmarkResults, driver_metadata: dict[str, Any]) -> dict[str, Any]:
    exec_block: dict[str, Any] = {}
    mode_value = None
    if isinstance(result.execution_metadata, Mapping):
        mode_value = result.execution_metadata.get("mode")
    if not mode_value and result.platform_info:
        mode_value = result.platform_info.get("execution_mode")
    if mode_value:
        exec_block["mode"] = mode_value
    _inject_driver_metadata(exec_block, driver_metadata)
    _inject_translation_metadata(exec_block, result)
    _inject_variant_comparability_metadata(exec_block, result)
    return exec_block


def _resolve_execution_mode(result: BenchmarkResults, ctx: dict[str, Any]) -> Any:
    if ctx.get("mode"):
        return ctx["mode"]
    if isinstance(result.execution_metadata, Mapping):
        mode = result.execution_metadata.get("mode")
        if mode:
            return mode
    if result.platform_info:
        return result.platform_info.get("execution_mode")
    return None


def _collect_driver_metadata(result: BenchmarkResults) -> dict[str, Any]:
    metadata: dict[str, Any] = {}

    for key in DRIVER_METADATA_KEYS:
        value = getattr(result, key, None)
        if value:
            metadata[key] = value

    execution_metadata = result.execution_metadata if isinstance(result.execution_metadata, Mapping) else {}
    for key in DRIVER_METADATA_KEYS:
        if key in metadata:
            continue
        value = execution_metadata.get(key)
        if value:
            metadata[key] = value

    platform_info = result.platform_info if isinstance(result.platform_info, Mapping) else {}
    aliases = {
        "driver_package": ("driver_package",),
        "driver_version_requested": ("driver_version_requested",),
        "driver_version_resolved": ("driver_version_resolved",),
        "driver_version_actual": ("driver_version_actual", "runtime_version_actual"),
        "driver_runtime_strategy": ("driver_runtime_strategy",),
        "driver_runtime_path": ("driver_runtime_path",),
        "driver_runtime_python_executable": ("driver_runtime_python_executable",),
    }
    for canonical, keys in aliases.items():
        if canonical in metadata:
            continue
        for key in keys:
            value = platform_info.get(key)
            if value:
                metadata[canonical] = value
                break

    return metadata


def _collect_engine_version_metadata(result: BenchmarkResults) -> dict[str, Any]:
    metadata: dict[str, Any] = {}

    for key in ENGINE_VERSION_KEYS:
        value = getattr(result, key, None)
        if value:
            metadata[key] = value

    if not metadata.get("engine_version"):
        execution_metadata = result.execution_metadata if isinstance(result.execution_metadata, Mapping) else {}
        for key in ENGINE_VERSION_KEYS:
            if key not in metadata:
                value = execution_metadata.get(key)
                if value:
                    metadata[key] = value

    if not metadata.get("engine_version"):
        platform_info = result.platform_info if isinstance(result.platform_info, Mapping) else {}
        for key in ENGINE_VERSION_KEYS:
            if key not in metadata:
                value = platform_info.get(key)
                if value:
                    metadata[key] = value

    return metadata


def _inject_driver_metadata(exec_block: dict[str, Any], driver_metadata: dict[str, Any]) -> None:
    for key in DRIVER_METADATA_KEYS:
        value = driver_metadata.get(key)
        if value:
            exec_block[key] = value


def _inject_translation_metadata(exec_block: dict[str, Any], result: BenchmarkResults) -> None:
    if not isinstance(result.execution_metadata, Mapping):
        return
    translation = result.execution_metadata.get("translation")
    if isinstance(translation, Mapping):
        exec_block["translation"] = dict(translation)


def _inject_variant_comparability_metadata(exec_block: dict[str, Any], result: BenchmarkResults) -> None:
    if not isinstance(result.execution_metadata, Mapping):
        return
    comparability = result.execution_metadata.get("variant_comparability")
    if isinstance(comparability, Mapping):
        exec_block["variant_comparability"] = dict(comparability)


def _shorten_benchmark_name(name: str) -> str:
    if name.lower().endswith(" benchmark"):
        return name[: -len(" benchmark")]
    return name


_CONFIG_CTX_OR_RUN = ["seed", "phases", "query_subset"]
_CONFIG_RUN_ONLY = [
    "execution_engine",
    "platform_options",
    "platform_option_sources",
    "tuning_mode",
    "tuning_config",
    "table_mode",
    "external_format",
    "table_format",
    "table_format_compression",
    "table_format_partition_cols",
    "query_parameters",
]


def _build_config_block(result: BenchmarkResults) -> dict[str, Any]:
    config: dict[str, Any] = {}
    ctx = result.execution_context or {}
    run_cfg = {}
    if isinstance(result.execution_metadata, Mapping):
        run_cfg = result.execution_metadata.get("run_config") or {}

    compression = None
    if ctx.get("compression_type") or run_cfg.get("compression"):
        if ctx.get("compression_type"):
            compression = {"type": ctx.get("compression_type"), "level": ctx.get("compression_level")}
        else:
            compression = run_cfg.get("compression")
    if compression:
        config["compression"] = compression

    for key in _CONFIG_CTX_OR_RUN:
        value = ctx.get(key)
        if value is None:
            value = run_cfg.get(key)
        if value is not None:
            config[key] = value

    for key in _CONFIG_RUN_ONLY:
        if run_cfg.get(key):
            config[key] = run_cfg[key]

    exec_mode = None
    if isinstance(result.execution_metadata, Mapping):
        exec_mode = result.execution_metadata.get("mode")
    if not exec_mode and result.platform_info:
        exec_mode = result.platform_info.get("execution_mode")
    if exec_mode:
        config["mode"] = exec_mode

    return config


def _post_load_maintenance_phase_payload(setup: Any) -> dict[str, Any]:
    maintenance = getattr(setup, "post_load_maintenance", None)
    if not maintenance:
        return {}
    return {
        "post_load_maintenance": {
            "status": maintenance.status,
            "duration_ms": maintenance.duration_ms,
            "tables_processed": maintenance.tables_processed,
        }
    }


_VALIDATION_SEVERITY = {"PASSED": 0, "PARTIAL": 1, "FAILED": 2}


def _validation_phase_status(row_count_status: str | None, overall_status: str | None) -> str | None:
    row_count = (row_count_status or "").upper()
    overall = (overall_status or "").upper()
    if row_count in _VALIDATION_SEVERITY and _VALIDATION_SEVERITY.get(overall, -1) > _VALIDATION_SEVERITY[row_count]:
        return overall
    return row_count_status


def _build_phases_block(result: BenchmarkResults) -> dict[str, Any]:
    phases: dict[str, Any] = {}
    standard = [
        "data_generation",
        "schema_creation",
        "data_loading",
        "validation",
        "power_test",
        "throughput_test",
    ]

    setup = getattr(result.execution_phases, "setup", None) if result.execution_phases else None
    if setup:
        if setup.data_generation:
            phases["data_generation"] = {
                "status": setup.data_generation.status,
                "duration_ms": setup.data_generation.duration_ms,
            }
        if setup.schema_creation:
            phases["schema_creation"] = {
                "status": setup.schema_creation.status,
                "duration_ms": setup.schema_creation.duration_ms,
            }
        if setup.data_loading:
            phases["data_loading"] = {
                "status": setup.data_loading.status,
                "duration_ms": setup.data_loading.duration_ms,
            }
        if setup.validation:
            phases["validation"] = {
                "status": _validation_phase_status(setup.validation.row_count_validation, result.validation_status),
                "duration_ms": setup.validation.duration_ms,
            }
        if setup.statistics_gathering:
            stats = setup.statistics_gathering
            phases["statistics"] = {
                "status": stats.status,
                "duration_ms": stats.duration_ms,
                "stats_mode": stats.stats_mode,
                "tables_analyzed": stats.tables_analyzed,
            }
            if stats.error_message:
                phases["statistics"]["error_message"] = stats.error_message
            if stats.stats_lifecycle:
                phases["statistics"]["stats_lifecycle"] = stats.stats_lifecycle
            if stats.per_table_ms:
                phases["statistics"]["per_table_ms"] = stats.per_table_ms

    phases.update(_post_load_maintenance_phase_payload(setup))

    if result.execution_phases and result.execution_phases.power_test:
        power_test = result.execution_phases.power_test
        real_executions = [qe for qe in power_test.query_executions if qe.query_id != "power_test_error"]
        power_test_status = "COMPLETED" if real_executions else "FAILED"
        phases["power_test"] = {
            "status": power_test_status,
            "duration_ms": power_test.duration_ms,
        }
    if result.execution_phases and result.execution_phases.throughput_test:
        phases["throughput_test"] = _throughput_phase_payload(result.execution_phases.throughput_test)

    if result.execution_phases and result.execution_phases.migration:
        m = result.execution_phases.migration
        phases["migration"] = {
            "status": m.status,
            "duration_ms": m.duration_ms,
            "tables_migrated": m.tables_migrated,
            "tables_failed": m.tables_failed,
            "storage_before_bytes": m.storage_before_bytes,
            "storage_after_bytes": m.storage_after_bytes,
            "storage_delta_bytes": m.storage_delta_bytes,
        }

    if isinstance(result.execution_metadata, Mapping):
        phase_status = result.execution_metadata.get("phase_status") or {}
        for name, status in phase_status.items():
            if name not in phases:
                phases[name] = status

    for phase in standard:
        if phase not in phases:
            phases[phase] = {"status": "NOT_RUN"}

    return phases


def _throughput_phase_payload(throughput: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "COMPLETED" if throughput.success else "FAILED",
        "duration_ms": throughput.duration_ms,
        "stream_results": [
            {
                "stream_id": stream.stream_id,
                "success": stream.success,
                **({"error": stream.error_message} if stream.error_message else {}),
            }
            for stream in throughput.streams
        ],
    }
    if throughput.errors:
        payload["errors"] = list(throughput.errors)
    if throughput.outstanding_work is not None:
        payload["outstanding_work"] = {
            "stream_ids": list(throughput.outstanding_work["stream_ids"]),
            "cleanup_state": throughput.outstanding_work["cleanup_state"],
        }
    if throughput.stream_numbering is not None:
        payload["stream_numbering"] = dict(throughput.stream_numbering)
    return payload


def compute_plan_capture_stats(
    query_results: list[dict[str, Any]],
    capture_plans: bool,
    *,
    existing_errors: list[dict[str, Any]] | None = None,
) -> tuple[int, int, list[dict[str, Any]]]:
    ids_with_plan: set[str] = {
        str(qr.get("query_id", "")) for qr in query_results if isinstance(qr, dict) and qr.get("query_plan") is not None
    }
    plans_captured = len(ids_with_plan)

    if not capture_plans:
        return plans_captured, 0, []

    measurement_success_ids: set[str] = {
        str(qr.get("query_id", ""))
        for qr in query_results
        if isinstance(qr, dict)
        and str(qr.get("status", "")).upper() == "SUCCESS"
        and str(qr.get("run_type", "")) == "measurement"
    }
    successful_ids: set[str]
    if measurement_success_ids:
        successful_ids = measurement_success_ids
    else:
        successful_ids = {
            str(qr.get("query_id", ""))
            for qr in query_results
            if isinstance(qr, dict) and str(qr.get("status", "")).upper() == "SUCCESS"
        }

    failed_ids = successful_ids - ids_with_plan

    if existing_errors is not None:
        failed_ids_str = {str(i) for i in failed_ids}
        capture_errors: list[dict[str, Any]] = [
            e for e in existing_errors if str(e.get("query_id", "")) in failed_ids_str
        ]
    else:
        capture_errors = [{"query_id": qid, "error": "Query plan not captured"} for qid in sorted(failed_ids)]

    return plans_captured, len(failed_ids), capture_errors


def _resolve_companion_max_depth(result: Any) -> int:
    metadata = getattr(result, "execution_metadata", None)
    run_cfg = metadata.get("run_config") if isinstance(metadata, Mapping) else None
    platform_options = run_cfg.get("platform_options") if isinstance(run_cfg, Mapping) else None
    raw = platform_options.get("plan_max_depth") if isinstance(platform_options, Mapping) else None
    if raw is None:
        return DEFAULT_PLAN_MAX_DEPTH
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PLAN_MAX_DEPTH


def _build_plan_entry(qr: dict[str, Any], *, max_depth: int = DEFAULT_PLAN_MAX_DEPTH) -> dict[str, Any]:
    query_plan = qr.get("query_plan")
    plan_fingerprint = qr.get("plan_fingerprint")
    plan_fingerprint_normalized = qr.get("plan_fingerprint_normalized")
    capture_time = qr.get("plan_capture_time_ms")

    plan_entry: dict[str, Any] = {}
    if plan_fingerprint:
        plan_entry["fingerprint"] = plan_fingerprint
        fingerprint_version = getattr(query_plan, "fingerprint_version", None)
        if fingerprint_version is not None:
            plan_entry["fingerprint_version"] = fingerprint_version
    if plan_fingerprint_normalized:
        plan_entry["fingerprint_normalized"] = plan_fingerprint_normalized
    if capture_time is not None:
        plan_entry["capture_time_ms"] = round(capture_time, 1)

    if isinstance(query_plan, QueryPlanDAG):
        plan_entry["plan"] = query_plan.to_dict(max_depth=max_depth)
    elif hasattr(query_plan, "to_dict"):
        plan_entry["plan"] = query_plan.to_dict()
    elif isinstance(query_plan, dict):
        plan_entry["plan"] = query_plan
    elif is_dataclass(query_plan):
        plan_entry["plan"] = asdict(query_plan)
    else:
        plan_entry["plan"] = str(query_plan)

    serialized_plan = plan_entry["plan"]
    plan_entry["plan_format"] = (
        "dag" if isinstance(serialized_plan, dict) and "logical_root" in serialized_plan else "text"
    )

    return plan_entry


def build_plans_payload(result: BenchmarkResults) -> dict[str, Any] | None:
    if not result.query_plans_captured or result.query_plans_captured == 0:
        return None

    rows_by_query_id: dict[str, list[dict[str, Any]]] = {}
    for qr in result.query_results or []:
        if qr.get("query_plan") is None:
            continue
        query_id = str(qr.get("query_id", qr.get("id", "")))
        rows_by_query_id.setdefault(query_id, []).append(qr)

    plans_by_query: dict[str, Any] = {}
    errors_list: list[dict[str, Any]] = []

    max_depth = _resolve_companion_max_depth(result)

    for query_id, rows in rows_by_query_id.items():
        multi_stream = len(rows) > 1
        stream_ids = [qr.get("stream_id", 0) for qr in rows]
        stream_id_disambiguates = len(set(stream_ids)) == len(stream_ids)
        for qr in rows:
            if not multi_stream:
                key = query_id
            elif stream_id_disambiguates:
                key = f"{query_id}#{qr.get('stream_id', 0)}"
            else:
                key = f"{query_id}#{qr.get('stream_id', 0)}:{qr.get('test_type', '')}"
            plans_by_query[key] = _build_plan_entry(qr, max_depth=max_depth)

    for error in result.plan_capture_errors or []:
        errors_list.append(
            {
                "query_id": error.get("query_id", "unknown"),
                "error": error.get("error", "Unknown error"),
            }
        )

    if not plans_by_query and not errors_list:
        return None

    return {
        "version": SCHEMA_VERSION,
        "run_id": result.execution_id,
        "plans_captured": len(rows_by_query_id),
        "capture_failures": result.plan_capture_failures or 0,
        "max_depth": max_depth,
        "truncated": _payload_has_truncation_marker(plans_by_query),
        "queries": plans_by_query,
        "errors": errors_list if errors_list else None,
    }


def _payload_has_truncation_marker(node: Any) -> bool:
    if isinstance(node, dict):
        if "truncated_at_depth" in node:
            return True
        return any(_payload_has_truncation_marker(value) for value in node.values())
    if isinstance(node, list):
        return any(_payload_has_truncation_marker(item) for item in node)
    return False


_YAML_TUNING_SOURCES = frozenset({"explicit_file", "auto_discovered", "packaged_resource"})


def _legacy_tuning_source_bridge(tuning_source: str | None, tuning_source_file: str | None) -> str:
    if tuning_source in _YAML_TUNING_SOURCES or (tuning_source is None and tuning_source_file):
        return "yaml"
    return "auto"


def _non_default_platform_optimizations(platform_optimizations: dict[str, Any]) -> dict[str, Any]:
    from benchbox.core.tuning.interface import PlatformOptimizationConfiguration

    defaults = PlatformOptimizationConfiguration().to_dict()
    return {key: value for key, value in platform_optimizations.items() if defaults.get(key) != value}


def _requested_tuning_sections(tuning_applied: dict[str, Any]) -> dict[str, Any]:
    requested: dict[str, Any] = {}

    constraints = {
        key: tuning_applied[key]
        for key in ("primary_keys", "foreign_keys", "unique_constraints", "check_constraints")
        if key in tuning_applied
    }
    if constraints:
        requested["constraints"] = constraints

    platform_optimizations = tuning_applied.get("platform_optimizations")
    if isinstance(platform_optimizations, dict):
        non_default = _non_default_platform_optimizations(platform_optimizations)
        if non_default:
            requested["platform_optimizations"] = non_default

    table_tunings = tuning_applied.get("table_tunings")
    if table_tunings:
        requested["table_tunings"] = table_tunings

    return requested


def _tuning_types_present(tuning_applied: dict[str, Any]) -> list[str]:
    types_present: set[str] = set()

    for key in ("primary_keys", "foreign_keys", "unique_constraints", "check_constraints"):
        block = tuning_applied.get(key)
        if isinstance(block, dict) and block.get("enabled"):
            types_present.add(key)

    platform_optimizations = tuning_applied.get("platform_optimizations")
    if isinstance(platform_optimizations, dict):
        for key, value in platform_optimizations.items():
            if key.endswith("_enabled") and value:
                types_present.add(key[: -len("_enabled")])

    for table_tuning in (tuning_applied.get("table_tunings") or {}).values():
        if isinstance(table_tuning, dict):
            for clause in ("partitioning", "clustering", "distribution", "sorting"):
                if table_tuning.get(clause):
                    types_present.add(clause)

    return sorted(types_present)


def build_tuning_payload(result: BenchmarkResults) -> dict[str, Any] | None:
    if not result.tunings_applied:
        return None

    tuning_applied = result.tunings_applied or {}
    if not tuning_applied:
        return None

    payload: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "run_id": result.execution_id,
    }

    tuning_source = getattr(result, "tuning_source", None)
    if tuning_source:
        payload["tuning_source"] = tuning_source
    if result.tuning_source_file:
        payload["source_file"] = result.tuning_source_file
    payload["source"] = _legacy_tuning_source_bridge(tuning_source, result.tuning_source_file)

    if result.tuning_config_hash:
        payload["requested_config_hash"] = result.tuning_config_hash
        payload["hash"] = result.tuning_config_hash

    applied_ledger_hash = getattr(result, "applied_ledger_hash", None)
    if applied_ledger_hash:
        payload["applied_ledger_hash"] = applied_ledger_hash

    from benchbox.core.tuning.policy_generation import TUNING_POLICY_GENERATION

    payload["tuning_policy_generation"] = TUNING_POLICY_GENERATION

    validation_status = _derived_validation_status(result)
    if validation_status:
        payload["validation_status"] = validation_status

    tuning_profile = _extract_tuning_profile_metadata(result)
    if tuning_profile:
        payload["logical_profile"] = tuning_profile

    requested = _requested_tuning_sections(tuning_applied)
    if requested:
        payload["requested"] = requested

    return payload


_TUNING_COMPANION_ENVELOPE_KEYS = frozenset({"version", "result_schema_version", "run_id"})

_TUNING_SUMMARY_OWNED_KEYS = frozenset(
    {
        "tuning_source",
        "source",
        "requested_config_hash",
        "hash",
        "applied_ledger_hash",
        "validation_status",
        "tuning_policy_generation",
        "logical_profile",
    }
)


def inline_tuning_artifacts(
    payload: dict[str, Any],
    tuning_payload: dict[str, Any] | None,
    applied_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if not tuning_payload and not applied_payload:
        return payload

    platform_block = payload.get("platform")
    if not isinstance(platform_block, dict):
        return payload

    tuning_block = platform_block.get("tuning")
    if not isinstance(tuning_block, dict):
        tuning_block = {}
        platform_block["tuning"] = tuning_block

    if tuning_payload:
        source_file = tuning_payload.get("source_file")
        if source_file and "source_file" not in tuning_block:
            tuning_block["source_file"] = source_file
        requested = {
            key: value
            for key, value in tuning_payload.items()
            if key not in _TUNING_COMPANION_ENVELOPE_KEYS
            and key not in _TUNING_SUMMARY_OWNED_KEYS
            and key != "source_file"
        }
        nested = requested.pop("requested", None)
        if isinstance(nested, dict):
            requested.update(nested)
        if requested:
            tuning_block["requested"] = requested

    if applied_payload:
        applied = dict(applied_payload)
        ledger_hash = applied.pop("applied_ledger_hash", None)
        if ledger_hash and not tuning_block.get("applied_ledger_hash"):
            tuning_block["applied_ledger_hash"] = ledger_hash
        if applied:
            tuning_block["applied"] = applied

    return payload


def build_applied_ledger_payload(result: BenchmarkResults) -> dict[str, Any] | None:
    payload = getattr(result, "applied_tuning_ledger", None)
    if not payload:
        return None
    if (
        not payload.get("statements")
        and not payload.get("dropped")
        and not payload.get("satisfied")
        and not payload.get("drift_check")
    ):
        return None
    return payload


def _compute_timing_stats(times_ms: list[float]) -> dict[str, Any]:
    if not times_ms:
        return {
            "total_ms": 0,
            "avg_ms": 0,
            "min_ms": 0,
            "max_ms": 0,
        }

    total_ms = sum(times_ms)
    avg_ms = total_ms / len(times_ms)
    min_ms = min(times_ms)
    max_ms = max(times_ms)

    stats: dict[str, Any] = {
        "total_ms": _round_duration_ms_for_export(total_ms),
        "avg_ms": _round_duration_ms_for_export(avg_ms),
        "min_ms": _round_duration_ms_for_export(min_ms),
        "max_ms": _round_duration_ms_for_export(max_ms),
    }

    if all(t > 0 for t in times_ms):
        try:
            geo_mean = statistics.geometric_mean(times_ms)
            stats["geometric_mean_ms"] = _round_duration_ms_for_export(geo_mean)
        except (statistics.StatisticsError, ValueError):
            pass

    if len(times_ms) >= 3:
        try:
            stdev = statistics.stdev(times_ms)
            stats["stdev_ms"] = _round_duration_ms_for_export(stdev)
        except statistics.StatisticsError:
            pass

    if len(times_ms) >= 10:
        stats["p90_ms"] = _round_duration_ms_for_export(percentile_ms(times_ms, 0.90))
        stats["p95_ms"] = _round_duration_ms_for_export(percentile_ms(times_ms, 0.95))
        stats["p99_ms"] = _round_duration_ms_for_export(percentile_ms(times_ms, 0.99))

    return stats


def _build_tpc_metrics(result: BenchmarkResults) -> dict[str, Any] | None:
    compliance_class = getattr(result, "compliance_class", None)
    if compliance_class in UNOFFICIAL_COMPLIANCE_CLASSES:
        return {"suppressed": True, "reason": f"compliance_class={compliance_class}"}

    metrics: dict[str, Any] = {}

    if result.power_at_size is not None:
        metrics["power_at_size"] = result.power_at_size
    if result.throughput_at_size is not None:
        metrics["throughput_at_size"] = result.throughput_at_size

    return metrics if metrics else None


def _derived_validation_status(result: BenchmarkResults) -> str | None:
    from benchbox.core.tuning.applied_ledger import NOT_VALIDATED

    status = (getattr(result, "tuning_validation_status", None) or "").lower()
    if status and status != NOT_VALIDATED:
        return status
    ledger = getattr(result, "applied_tuning_ledger", None)
    ledger_status = ledger.get("status") if isinstance(ledger, dict) else None
    if isinstance(ledger_status, str) and ledger_status and ledger_status.lower() != NOT_VALIDATED:
        return ledger_status.lower()
    return None


def _untuned_tuning_summary(result: BenchmarkResults) -> dict[str, Any]:
    from benchbox.core.tuning.applied_ledger import NOT_APPLICABLE

    status = _derived_validation_status(result)
    if status:
        return {"validation_status": status}
    if getattr(result, "tuning_validation_status", None) is not None:
        return {"validation_status": NOT_APPLICABLE}

    summary: dict[str, Any] = {}
    tuning_source = getattr(result, "tuning_source", None)
    if tuning_source:
        summary["tuning_source"] = tuning_source
    source_file = getattr(result, "tuning_source_file", None)
    config_hash = getattr(result, "tuning_config_hash", None)
    if tuning_source or source_file or config_hash:
        summary["source"] = _legacy_tuning_source_bridge(tuning_source, source_file)
    else:
        legacy_source = getattr(result, "tuning_legacy_source", None)
        if legacy_source in ("yaml", "auto"):
            summary["source"] = legacy_source
    if config_hash:
        summary["requested_config_hash"] = config_hash
        summary["hash"] = config_hash
    return summary


def _build_tuning_summary(result: BenchmarkResults) -> dict[str, Any]:
    if not result.tunings_applied:
        return _untuned_tuning_summary(result)

    tuning_applied = result.tunings_applied or {}
    summary: dict[str, Any] = {}

    tuning_source = getattr(result, "tuning_source", None)
    if tuning_source:
        summary["tuning_source"] = tuning_source
    summary["source"] = _legacy_tuning_source_bridge(tuning_source, result.tuning_source_file)

    if result.tuning_config_hash:
        summary["requested_config_hash"] = result.tuning_config_hash
        summary["hash"] = result.tuning_config_hash

    applied_ledger_hash = getattr(result, "applied_ledger_hash", None)
    if applied_ledger_hash:
        summary["applied_ledger_hash"] = applied_ledger_hash

    validation_status = _derived_validation_status(result)
    if validation_status:
        summary["validation_status"] = validation_status

    from benchbox.core.tuning.policy_generation import TUNING_POLICY_GENERATION

    summary["tuning_policy_generation"] = TUNING_POLICY_GENERATION

    table_tunings = tuning_applied.get("table_tunings") or {}
    tuning_types = _tuning_types_present(tuning_applied)
    if table_tunings or tuning_types:
        summary["counts"] = {
            "tables_tuned": len(table_tunings),
            "tuning_types": tuning_types,
        }

    tuning_profile = _extract_tuning_profile_metadata(result)
    if tuning_profile:
        coverage = tuning_profile.get("logical_profile_coverage")
        logical_profile = {
            "id": tuning_profile.get("logical_tuning_profile_id"),
            "version": tuning_profile.get("logical_tuning_profile_version"),
            "template_hash": tuning_profile.get("tuning_template_hash"),
            "physical_rendering_id": tuning_profile.get("physical_rendering_id"),
            "physical_mechanisms": tuning_profile.get("platform_physical_tuning_mechanisms", []),
        }
        if coverage:
            logical_profile["coverage"] = coverage
        summary["logical_profile"] = {key: value for key, value in logical_profile.items() if value}

    return summary


def _extract_tuning_profile_metadata(result: BenchmarkResults) -> dict[str, Any] | None:
    if not isinstance(result.execution_metadata, Mapping):
        return None
    tuning_profile = result.execution_metadata.get("tuning_profile")
    if not isinstance(tuning_profile, Mapping):
        return None
    return dict(tuning_profile)


def _build_environment_block(result: BenchmarkResults) -> dict[str, Any]:
    return build_environment_payload(
        system_profile=getattr(result, "system_profile", None),
        execution_environment=getattr(result, "execution_environment", None),
    )


def _build_tables_block(table_statistics: dict[str, Any] | None) -> dict[str, Any]:
    if not table_statistics:
        return {}

    tables: dict[str, Any] = {}
    for table_name in sorted(table_statistics.keys()):
        stats = table_statistics[table_name]
        if isinstance(stats, dict):
            entry: dict[str, Any] = {}
            if "rows" in stats:
                entry["rows"] = stats["rows"]
            elif "rows_loaded" in stats:
                entry["rows"] = stats["rows_loaded"]
            if "load_time_ms" in stats and stats["load_time_ms"] is not None:
                entry["load_ms"] = round(stats["load_time_ms"], 1)
            elif "load_ms" in stats and stats["load_ms"] is not None:
                entry["load_ms"] = round(stats["load_ms"], 1)
            if entry:
                tables[table_name] = entry
        elif isinstance(stats, int):
            tables[table_name] = {"rows": stats}

    return tables


def _extract_table_errors(execution_phases: Any) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []

    if execution_phases is None:
        return errors

    if is_dataclass(execution_phases):
        phases = asdict(execution_phases)
    elif isinstance(execution_phases, dict):
        phases = execution_phases
    else:
        return errors

    setup = phases.get("setup", {})
    if not setup:
        return errors

    data_loading = setup.get("data_loading", {})
    if not data_loading:
        return errors

    per_table = data_loading.get("per_table_stats", {})
    for table_name, stats in (per_table or {}).items():
        if isinstance(stats, dict):
            error_type = stats.get("error_type")
            error_msg = stats.get("error_message")
            if error_type or error_msg:
                errors.append(
                    {
                        "phase": "data_loading",
                        "table": table_name,
                        "type": error_type or "LoadError",
                        "message": error_msg or "Table loading failed",
                    }
                )

    return errors


def _extract_platform_config(platform_info: dict[str, Any]) -> dict[str, Any]:
    if not platform_info:
        return {}

    exclude_keys = {
        "version",
        "variant",
        "execution_engine",
        "gateway",
        "name",
        "platform",
        "adapter_name",
        "adapter_version",
        "platform_version",
        "client_library_version",
        "client_version",
        "platform_name",
        "platform_type",
    }
    config: dict[str, Any] = {}

    def extract_recursive(d: dict[str, Any]) -> None:
        for key, value in d.items():
            if key.lower() in exclude_keys or value is None:
                continue
            if isinstance(value, str) and not value:
                continue
            if isinstance(value, (list, dict)) and not value:
                continue
            if key == "configuration" and isinstance(value, dict):
                extract_recursive(value)
            else:
                config[key] = value

    extract_recursive(platform_info)
    return sanitize_platform_options(config)


_PLATFORM_CONFIG_COMPUTE_KEYS = frozenset({"compute_configuration", "cluster_info"})

_PLATFORM_CONFIG_LAYOUT_KEYS = frozenset(
    {
        "applied_layout_operations",
        "skipped_layout_operations",
        "liquid_clustering_operations",
        "z_order_operations",
    }
)


def _prune_duplicated_platform_config(
    config: dict[str, Any],
    normalized_compute: Any = None,
) -> dict[str, Any]:
    drop = set(_PLATFORM_CONFIG_LAYOUT_KEYS)
    if isinstance(normalized_compute, Mapping) and any(
        key not in {"source", "collection_status"} and value is not None for key, value in normalized_compute.items()
    ):
        drop |= _PLATFORM_CONFIG_COMPUTE_KEYS
    return {key: value for key, value in config.items() if key not in drop}
