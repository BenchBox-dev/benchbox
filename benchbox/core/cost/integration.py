import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

import yaml

from benchbox.core.cost.calculator import CostCalculator
from benchbox.core.cost.models import PhaseCost
from benchbox.core.results.models import BenchmarkResults

logger = logging.getLogger(__name__)


_OBSERVED_METADATA_SOURCES = frozenset({"observed"})


def _load_cost_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("cost_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


PLATFORM_CONFIG_REQUIREMENTS = _load_cost_specs()["platform_config_requirements"]


def validate_platform_config(platform: str, config: dict[str, Any]) -> tuple[bool, list[str]]:

    warnings = []
    platform_lower = platform.lower()

    if platform_lower not in PLATFORM_CONFIG_REQUIREMENTS:
        return True, warnings

    requirements = PLATFORM_CONFIG_REQUIREMENTS[platform_lower]

    for field in requirements.get("required", []):
        if field not in config or config[field] is None:
            warnings.append(
                f"Missing required config field '{field}' for {platform} cost calculation. "
                f"Cost estimation may be inaccurate or fail."
            )

    if platform_lower == "synapse" and str(config.get("mode") or "serverless").lower() == "dedicated":
        if config.get("dwu_level") is None:
            warnings.append("Missing required config field 'dwu_level' for dedicated Synapse cost calculation")
    if platform_lower in {"databricks", "databricks-df"}:
        if config.get("workload_type") is None and config.get("warehouse_type") is None:
            warnings.append("Missing workload_type or warehouse_type for Databricks cost calculation")

    is_valid = len(warnings) == 0
    return is_valid, warnings


def add_cost_estimation_to_results(
    results: BenchmarkResults,
    platform_config: Optional[dict[str, Any]] = None,
) -> BenchmarkResults:

    platform = canonical_cost_platform_key(results) or "unknown"

    try:
        if platform == "unknown":
            logger.debug("No platform specified in results, skipping cost estimation")
            return results

        if platform_config is None:
            platform_config = _extract_platform_config_from_results(results)

        _validate_and_warn_platform_config(platform, platform_config)

        calculator = CostCalculator()

        for query_result in results.query_results or []:
            resource_usage = _resource_usage_of(query_result)
            if resource_usage:
                query_cost = calculator.calculate_query_cost(
                    platform=platform,
                    resource_usage=resource_usage,
                    platform_config=platform_config,
                )
                if query_cost:
                    cost_value: float | None
                    if "price_unavailable" in query_cost.pricing_details:
                        cost_value = None
                    else:
                        cost_value = query_cost.compute_cost
                    if isinstance(query_result, dict):
                        query_result["cost"] = cost_value
                    elif hasattr(query_result, "cost"):
                        query_result.cost = cost_value

        phase_costs = _calculate_phase_costs(results, platform, platform_config, calculator)

        platform_details = _build_platform_details(platform, platform_config)

        benchmark_cost = calculator.calculate_benchmark_cost(
            phase_costs=phase_costs,
            platform_details=platform_details,
        )

        _apply_cost_model_and_warnings(benchmark_cost, platform, platform_config)
        _add_storage_cost_estimate(benchmark_cost, results, platform, platform_config, platform_details)
        normalized_cost, normalized_warnings = calculator.calculate_normalized_benchmark_cost(
            platform=platform,
            benchmark_cost=benchmark_cost,
            platform_config=platform_config,
        )
        for warning in normalized_warnings:
            logger.warning(warning)
            benchmark_cost.warnings.append(warning)

        results.cost_summary = benchmark_cost.to_dict()
        results.cost_summary["normalized_cost"] = normalized_cost.to_dict()

        logger.info(f"Cost estimation complete: ${benchmark_cost.total_cost:.4f} across {len(phase_costs)} phases")

    except Exception as e:
        logger.warning(
            f"Failed to add cost estimation to results for platform '{platform}': {e}",
            exc_info=True,
            extra={
                "platform": platform,
                "benchmark_name": results.benchmark_name,
                "scale_factor": results.scale_factor,
            },
        )

    return results


def _validate_and_warn_platform_config(platform: str, platform_config: dict[str, Any]) -> None:

    config_valid, config_warnings = validate_platform_config(platform, platform_config)
    if config_warnings:
        for warning in config_warnings:
            logger.warning(f"Platform config validation: {warning}")
        if not config_valid:
            logger.error(
                f"Platform configuration incomplete for {platform}. Cost estimation may fail or be inaccurate."
            )


def _build_platform_details(platform: str, platform_config: dict[str, Any]) -> dict[str, Any]:

    from benchbox.core.cost.pricing import PRICING_LAST_UPDATED, PRICING_VERSION

    platform_details = {
        "platform": platform,
        "platform_type": platform_config.get("platform_type"),
        "region": platform_config.get("region"),
        "warehouse_size": platform_config.get("warehouse_size"),
        "node_type": platform_config.get("node_type"),
        "edition": platform_config.get("edition"),
        "tier": platform_config.get("tier"),
        "pricing_version": PRICING_VERSION,
        "pricing_date": PRICING_LAST_UPDATED,
    }
    return {k: v for k, v in platform_details.items() if v is not None}


def _snowflake_phase_has_estimated_concurrent_cost(benchmark_cost: Any) -> bool:

    for phase in benchmark_cost.phase_costs or []:
        if (phase.concurrent_streams or 1) <= 1:
            continue
        for query_cost in phase.query_costs or []:
            if query_cost.pricing_details.get("credits_used_estimated"):
                return True
    return False


def _has_estimated_query_cost(benchmark_cost: Any, estimate_flag: str) -> bool:
    return any(
        query_cost.pricing_details.get(estimate_flag)
        for phase in benchmark_cost.phase_costs or []
        for query_cost in phase.query_costs or []
    )


def _apply_cost_model_and_warnings(benchmark_cost: Any, platform: str, platform_config: dict[str, Any]) -> None:

    from benchbox.core.cost.pricing import (
        PRICING_LAST_UPDATED,
        get_pricing_age_days,
        is_pricing_stale,
        resolve_redshift_node_price,
    )

    platform_lower = platform.lower()
    if platform_lower == "redshift":
        benchmark_cost.cost_model = "marginal"
        node_count = platform_config.get("node_count", "N")
        node_type = platform_config.get("node_type", "unknown")
        region = platform_config.get("region", "us-east-1")
        resolution = resolve_redshift_node_price(node_type, region)
        if resolution.fallback_used or resolution.value is None:
            rate_text = "an unknown rate"
        else:
            rate_text = f"${resolution.value:.2f}/hour"
        benchmark_cost.warnings.append(
            f"Redshift costs show marginal per-query costs, not total cluster TCO. "
            f"Cluster idle time is excluded. For full cluster cost, calculate: "
            f"cluster_runtime_hours × {node_count} nodes × {rate_text}."
        )
    elif platform_lower == "databricks":
        workload_type = platform_config.get("workload_type", "")
        if workload_type == "all_purpose":
            if _has_estimated_query_cost(benchmark_cost, "is_estimated"):
                benchmark_cost.cost_model = "estimated"
                benchmark_cost.warnings.append(
                    "Databricks all-purpose costs are timing-derived marginal estimates; cluster idle time is excluded."
                )
            else:
                benchmark_cost.cost_model = "marginal"
                benchmark_cost.warnings.append(
                    "Databricks all-purpose cluster costs show marginal per-query costs. Cluster idle time is excluded."
                )
        elif _has_estimated_query_cost(benchmark_cost, "is_estimated"):
            benchmark_cost.cost_model = "estimated"
            benchmark_cost.warnings.append(
                "Databricks SQL warehouse costs are timing-derived estimates; warehouse idle time, startup, and scaling "
                "overhead are excluded."
            )
        else:
            benchmark_cost.cost_model = "actual"
    elif platform_lower == "snowflake":
        has_estimated_cost = _has_estimated_query_cost(benchmark_cost, "credits_used_estimated")
        if has_estimated_cost:
            benchmark_cost.cost_model = "estimated"
            benchmark_cost.warnings.append(
                "Snowflake costs are timing-derived estimates; warehouse startup minimums and idle time are excluded."
            )
        if _snowflake_phase_has_estimated_concurrent_cost(benchmark_cost):
            benchmark_cost.warnings.append(
                "Snowflake costs include runtime-estimated query costs sharing a warehouse "
                "across concurrent streams. Each estimate prices exclusive warehouse use, "
                "so the summed total may exceed the warehouse's wall-clock spend; metered "
                "credits_used attributes shared cost exactly."
            )
        if not has_estimated_cost:
            benchmark_cost.cost_model = "actual"
    elif platform_lower in ("bigquery", "duckdb", "clickhouse"):
        benchmark_cost.cost_model = "actual"
    else:
        benchmark_cost.cost_model = "estimated"

    if is_pricing_stale(threshold_days=90):
        pricing_age = get_pricing_age_days()
        benchmark_cost.warnings.append(
            f"Pricing data is {pricing_age} days old (last updated: {PRICING_LAST_UPDATED}). "
            f"Costs may be inaccurate. Please check for pricing updates."
        )


def _add_storage_cost_estimate(
    benchmark_cost: Any,
    results: BenchmarkResults,
    platform: str,
    platform_config: dict[str, Any],
    platform_details: dict[str, Any],
) -> None:

    if not results.data_size_mb or results.data_size_mb <= 0:
        return

    from benchbox.core.cost.storage import estimate_storage_cost

    total_bytes = int(results.data_size_mb * 1024 * 1024)
    storage_duration_hours = max(1.0, results.duration_seconds / 3600.0)
    region = platform_config.get("region", "us-east-1")

    storage_est = estimate_storage_cost(
        platform=platform,
        total_bytes=total_bytes,
        storage_duration_hours=storage_duration_hours,
        region=region,
    )

    benchmark_cost.storage_cost = storage_est["storage_cost"]
    platform_details["storage_estimate"] = storage_est

    if storage_est["storage_cost"] > 0:
        benchmark_cost.warnings.append(
            f"Storage cost estimate: ${storage_est['storage_cost']:.4f} for "
            f"{storage_est['storage_tb']:.2f} TB over {storage_duration_hours:.1f} hours. "
            f"{storage_est['note']}"
        )


def canonical_cost_platform_key(results: BenchmarkResults) -> str:

    platform_info = results.platform_info if isinstance(results.platform_info, Mapping) else {}
    for key in ("platform_type", "platform_name", "name", "platform"):
        declared = platform_info.get(key)
        if declared:
            return _normalize_platform_token(str(declared))
        nested = platform_info.get("configuration")
        if isinstance(nested, Mapping):
            declared = nested.get(key)
            if declared:
                return _normalize_platform_token(str(declared))
            inner = nested.get("configuration")
            if isinstance(inner, Mapping):
                declared = inner.get(key)
                if declared:
                    return _normalize_platform_token(str(declared))

    display_name = getattr(results, "platform", None)
    if display_name:
        return _normalize_platform_token(str(display_name))
    return ""


def _normalize_platform_token(raw: str) -> str:

    from benchbox.core.platform_manifest import get_all_platform_aliases

    token = raw.strip().lower()

    for marker in (" (dataframe)", " (sql)"):
        if token.endswith(marker):
            token = token[: -len(marker)]
    token = token.replace(" ", "-").replace("_", "-")
    if token in {"fabric-warehouse", "microsoft-fabric-warehouse", "fabric-dw"}:
        return "fabric_dw"
    aliases = {key.replace("_", "-"): value for key, value in get_all_platform_aliases().items()}
    canonical = aliases.get(token, token)

    if canonical in {"fabric-dw", "fabric_dw"}:
        return "fabric_dw"
    if canonical in {"clickhouse-cloud", "clickhouse_cloud"}:
        return "clickhouse_cloud"
    return canonical


def _collect_lookup_dicts(platform_info: Mapping[str, Any]) -> list[Mapping[str, Any]]:

    dicts: list[Mapping[str, Any]] = []
    if isinstance(platform_info, Mapping):
        dicts.append(platform_info)
        cfg = platform_info.get("configuration")
        if isinstance(cfg, Mapping):
            dicts.append(cfg)
            inner = cfg.get("configuration")
            if isinstance(inner, Mapping):
                dicts.append(inner)
            compute_cfg = cfg.get("compute_configuration")
            if isinstance(compute_cfg, Mapping):
                dicts.append(compute_cfg)
        compute_cfg_top = platform_info.get("compute_configuration")
        if isinstance(compute_cfg_top, Mapping):
            dicts.append(compute_cfg_top)
        cluster_info = platform_info.get("cluster_info")
        if isinstance(cluster_info, Mapping):
            dicts.append(cluster_info)
    return dicts


def _resource_usage_of(query_result: Any) -> Any | None:

    if isinstance(query_result, Mapping):
        return query_result.get("resource_usage")
    return getattr(query_result, "resource_usage", None)


def _first_present(dicts: list[Mapping[str, Any]], keys: list[str]) -> Any | None:

    for source in dicts:
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    continue
                return stripped
            return value
    return None


def _extract_platform_config_from_results(results: BenchmarkResults) -> dict[str, Any]:

    config: dict[str, Any] = {}
    defaulted_fields: list[str] = []

    platform_type = canonical_cost_platform_key(results)
    normalized = _normalized_platform_facets(results)

    if not results.platform_info and not any(normalized.values()):
        return config

    platform_info = results.platform_info if isinstance(results.platform_info, Mapping) else {}

    config["platform_type"] = platform_type

    dicts = _collect_lookup_dicts(platform_info)
    config_section = platform_info.get("configuration")
    config_section = config_section if isinstance(config_section, Mapping) else {}

    _resolve_cloud_and_region(
        config,
        platform_type,
        normalized,
        platform_info,
        config_section,
        defaulted_fields,
        dicts=dicts,
    )

    compute = _effective_compute_block(normalized, platform_info)

    if platform_type == "snowflake":
        edition = _first_present(dicts, ["edition"])
        if edition is None:
            defaulted_fields.append("edition")
            edition = "standard"
        config["edition"] = str(edition).strip().lower()
        warehouse_size = _observed_or_requested(
            compute,
            "warehouse_size",
            "warehouse_size",
            defaulted_fields,
            fallback=_first_present(dicts, ["warehouse_size"]),
        )
        if warehouse_size:
            config["warehouse_size"] = str(warehouse_size).strip()

    elif platform_type == "bigquery":
        location = normalized["cloud"].get("location") or _first_present(
            dicts, ["location", "dataset_location", "cloud_region"]
        )
        if location and isinstance(location, str):
            location = location.strip()
        if location:
            config["location"] = location
        else:
            defaulted_fields.append("location")

    elif platform_type == "redshift":
        cluster_info = platform_info.get("cluster_info")
        cluster_info = cluster_info if isinstance(cluster_info, Mapping) else {}
        node_type = _observed_or_requested(
            compute, "node_type", "node_type", defaulted_fields, fallback=_first_present(dicts, ["node_type"])
        )
        node_count = _observed_or_requested(
            compute,
            "node_count",
            "node_count",
            defaulted_fields,
            fallback=_first_present(dicts, ["number_of_nodes", "num_nodes", "node_count"]),
        )
        if node_type:
            config["node_type"] = str(node_type).strip()
        else:
            defaulted_fields.append("node_type")
        if node_count is not None:
            try:
                config["node_count"] = int(node_count)
            except (ValueError, TypeError):
                config["node_count"] = node_count
        else:
            defaulted_fields.append("node_count")

    elif platform_type in {"databricks", "databricks-df", "databricks_df"}:
        config["tier"] = str(_first_present(dicts, ["tier"]) or "premium").strip().lower()
        _resolve_databricks_compute(config, compute, config_section, defaulted_fields, dicts=dicts)

    if defaulted_fields:
        config["_defaulted_fields"] = sorted(set(defaulted_fields))

    return config


def _normalized_platform_facets(results: BenchmarkResults) -> dict[str, Mapping[str, Any]]:

    facets: dict[str, Mapping[str, Any]] = {}
    for facet, attribute in (
        ("cloud", "platform_cloud"),
        ("compute", "platform_compute"),
        ("deployment", "platform_deployment"),
        ("storage", "platform_storage"),
    ):
        block = getattr(results, attribute, None)
        if block is not None and not isinstance(block, Mapping) and hasattr(block, "to_dict"):
            block = block.to_dict()
        facets[facet] = block if isinstance(block, Mapping) else {}
    return facets


_COMPUTE_SIZING_KEYS = ("warehouse_size", "warehouse_type", "node_type", "node_count")


def _effective_compute_block(
    normalized: dict[str, Mapping[str, Any]],
    platform_info: Mapping[str, Any],
) -> Mapping[str, Any]:

    block = normalized["compute"]
    legacy = platform_info.get("compute_configuration")
    if not isinstance(legacy, Mapping):
        cfg = platform_info.get("configuration")
        if isinstance(cfg, Mapping):
            legacy = cfg.get("compute_configuration")
    if not isinstance(legacy, Mapping):
        return block

    missing = [key for key in _COMPUTE_SIZING_KEYS if block.get(key) is None and legacy.get(key) is not None]
    if not missing:
        return block

    status = str(legacy.get("warehouse_metadata_collection_status") or "available").lower()
    legacy_source = "observed" if status == "available" else "inferred"
    merged = dict(block)
    merged.update({key: legacy[key] for key in missing})

    merged["_field_sources"] = {**dict(block.get("_field_sources") or {}), **dict.fromkeys(missing, legacy_source)}
    return merged


def _field_source(block: Mapping[str, Any], key: str) -> str:

    field_sources = block.get("_field_sources")
    if isinstance(field_sources, Mapping) and key in field_sources:
        return str(field_sources[key] or "").lower()
    return str(block.get("source") or "").lower()


def _observed_or_requested(
    block: Mapping[str, Any],
    key: str,
    alias: str,
    defaulted_fields: list[str],
    *,
    fallback: Any = None,
) -> Any:

    value = block.get(key)
    if value is None:
        if fallback is None:
            return None
        defaulted_fields.append(alias)
        return fallback
    if _field_source(block, key) not in _OBSERVED_METADATA_SOURCES:
        defaulted_fields.append(alias)
    return value


_SINGLE_CLOUD_PLATFORMS: dict[str, str] = {
    "athena": "aws",
    "athena-spark": "aws",
    "redshift": "aws",
    "emr-serverless": "aws",
    "glue": "aws",
    "bigquery": "gcp",
    "dataproc": "gcp",
    "dataproc-serverless": "gcp",
    "synapse": "azure",
    "synapse-spark": "azure",
    "fabric_dw": "azure",
    "fabric-lakehouse": "azure",
    "fabric-spark": "azure",
}


def _resolve_cloud_and_region(
    config: dict[str, Any],
    platform_type: str,
    normalized: dict[str, Mapping[str, Any]],
    platform_info: Mapping[str, Any],
    config_section: Mapping[str, Any],
    defaulted_fields: list[str],
    *,
    dicts: list[Mapping[str, Any]] | None = None,
) -> None:

    lookup_dicts = dicts or _collect_lookup_dicts(platform_info)
    cloud_block = normalized["cloud"]
    cloud = cloud_block.get("provider")
    if not cloud:
        cloud = _first_present(lookup_dicts, ["cloud_provider", "cloud", "provider"])
    if not cloud and platform_type in {"databricks", "databricks-df", "databricks_df"}:
        hostname = str(_first_present(lookup_dicts, ["server_hostname", "host", "hostname"]) or "").lower()
        if "azuredatabricks" in hostname:
            cloud = "azure"
        elif "gcp.databricks.com" in hostname:
            cloud = "gcp"
        elif hostname:
            cloud = "aws"
    if not cloud:
        cloud = _SINGLE_CLOUD_PLATFORMS.get(platform_type)
    if cloud:
        config["cloud"] = str(cloud).strip().lower()
    else:
        defaulted_fields.append("cloud")

    region = (
        cloud_block.get("region")
        or cloud_block.get("location")
        or _first_present(lookup_dicts, ["region", "cloud_region", "location"])
        or normalized["deployment"].get("region")
    )
    if region:
        config["region"] = str(region).strip()
    else:
        defaulted_fields.append("region")


def _resolve_databricks_compute(
    config: dict[str, Any],
    compute: Mapping[str, Any],
    config_section: Mapping[str, Any],
    defaulted_fields: list[str],
    *,
    dicts: list[Mapping[str, Any]] | None = None,
) -> None:

    lookup_dicts = dicts or _collect_lookup_dicts(config_section)
    warehouse_type = (
        compute.get("warehouse_type")
        or _first_present(lookup_dicts, ["warehouse_type"])
        or config_section.get("warehouse_type")
    )
    if warehouse_type:
        config["warehouse_type"] = warehouse_type
        warehouse_type_upper = str(warehouse_type).upper()
        if warehouse_type_upper == "SERVERLESS":
            config["workload_type"] = "serverless_sql"
        elif warehouse_type_upper == "CLASSIC":
            config["workload_type"] = "sql_classic"
        else:
            config["workload_type"] = "sql_compute"
    else:
        config["workload_type"] = "all_purpose"
        defaulted_fields.append("workload_type")

    warehouse_size = _observed_or_requested(
        compute,
        "warehouse_size",
        "warehouse_size",
        defaulted_fields,
        fallback=_first_present(lookup_dicts, ["warehouse_size"]),
    )
    if warehouse_size:
        from benchbox.core.cost.pricing import resolve_databricks_warehouse_dbu_per_hour

        config["warehouse_size"] = warehouse_size
        resolution = resolve_databricks_warehouse_dbu_per_hour(str(warehouse_size))
        config["cluster_size_dbu_per_hour"] = resolution.value
        if resolution.fallback_used or resolution.value is None:
            defaulted_fields.append("cluster_size_dbu_per_hour")
            logger.warning(
                f"Databricks warehouse size '{warehouse_size}' is not in the size map; "
                f"using a conservative 2.0 DBU/hour estimate that cannot publish as normalized."
            )
        return

    config["cluster_size_dbu_per_hour"] = 2.0
    defaulted_fields.append("cluster_size_dbu_per_hour")
    logger.warning(
        "Databricks warehouse size unavailable in platform.compute; using a conservative 2.0 DBU/hour estimate. "
        "Install databricks-sdk so the warehouse metadata can be collected."
    )


def _value_or_default(
    data: dict[str, Any],
    key: str,
    default: Any,
    defaulted_fields: list[str],
    *,
    alias: str | None = None,
) -> Any:
    value = data.get(key)
    if value is None:
        defaulted_fields.append(alias or key)
        return default
    return value


def _calculate_phase_costs(
    results: BenchmarkResults,
    platform: str,
    platform_config: dict[str, Any],
    calculator: CostCalculator,
) -> list[PhaseCost]:

    phase_costs: list[PhaseCost] = []

    if results.execution_phases:
        _calculate_power_test_cost(results.execution_phases, platform, platform_config, calculator, phase_costs)
        _calculate_throughput_test_cost(results.execution_phases, platform, platform_config, calculator, phase_costs)
        _calculate_maintenance_test_cost(results.execution_phases, platform, platform_config, calculator, phase_costs)

    if not phase_costs and results.query_results:
        _calculate_fallback_costs(results.query_results, platform, platform_config, calculator, phase_costs)

    return phase_costs


def _collect_query_costs_from_executions(
    query_executions: Any,
    platform: str,
    platform_config: dict[str, Any],
    calculator: CostCalculator,
) -> list:

    query_costs = []
    for query_exec in query_executions:
        if hasattr(query_exec, "resource_usage") and query_exec.resource_usage:
            qc = calculator.calculate_query_cost(
                platform=platform,
                resource_usage=query_exec.resource_usage,
                platform_config=platform_config,
            )
            if qc:
                query_costs.append(qc)
    return query_costs


def _calculate_power_test_cost(
    execution_phases: Any,
    platform: str,
    platform_config: dict[str, Any],
    calculator: CostCalculator,
    phase_costs: list[PhaseCost],
) -> None:

    if not execution_phases.power_test:
        return
    power_phase = execution_phases.power_test
    query_costs = _collect_query_costs_from_executions(
        power_phase.query_executions, platform, platform_config, calculator
    )
    if query_costs:
        phase_cost = calculator.calculate_phase_cost("power_test", query_costs)
        if hasattr(power_phase, "duration_ms"):
            phase_cost.wall_clock_duration_seconds = power_phase.duration_ms / 1000.0
        phase_cost.concurrent_streams = 1
        phase_costs.append(phase_cost)


def _calculate_throughput_test_cost(
    execution_phases: Any,
    platform: str,
    platform_config: dict[str, Any],
    calculator: CostCalculator,
    phase_costs: list[PhaseCost],
) -> None:

    if not execution_phases.throughput_test:
        return
    throughput_phase = execution_phases.throughput_test
    query_costs = []
    for stream in throughput_phase.streams:
        query_costs.extend(
            _collect_query_costs_from_executions(stream.query_executions, platform, platform_config, calculator)
        )
    if query_costs:
        phase_cost = calculator.calculate_phase_cost("throughput_test", query_costs)
        if hasattr(throughput_phase, "duration_ms"):
            phase_cost.wall_clock_duration_seconds = throughput_phase.duration_ms / 1000.0
        if hasattr(throughput_phase, "streams"):
            phase_cost.concurrent_streams = len(throughput_phase.streams)
        phase_costs.append(phase_cost)


def _calculate_maintenance_test_cost(
    execution_phases: Any,
    platform: str,
    platform_config: dict[str, Any],
    calculator: CostCalculator,
    phase_costs: list[PhaseCost],
) -> None:

    if not execution_phases.maintenance_test:
        return
    maintenance_phase = execution_phases.maintenance_test
    query_costs = _collect_query_costs_from_executions(
        maintenance_phase.query_executions, platform, platform_config, calculator
    )
    if query_costs:
        phase_cost = calculator.calculate_phase_cost("data_maintenance", query_costs)
        if hasattr(maintenance_phase, "duration_ms"):
            phase_cost.wall_clock_duration_seconds = maintenance_phase.duration_ms / 1000.0
        phase_cost.concurrent_streams = 1
        phase_costs.append(phase_cost)


def _calculate_fallback_costs(
    query_results: list,
    platform: str,
    platform_config: dict[str, Any],
    calculator: CostCalculator,
    phase_costs: list[PhaseCost],
) -> None:

    query_costs = []
    for query_result in query_results:
        resource_usage = _resource_usage_of(query_result)
        if resource_usage:
            qc = calculator.calculate_query_cost(
                platform=platform,
                resource_usage=resource_usage,
                platform_config=platform_config,
            )
            if qc:
                query_costs.append(qc)
    if query_costs:
        phase_cost = calculator.calculate_phase_cost("all_queries", query_costs)
        phase_costs.append(phase_cost)
