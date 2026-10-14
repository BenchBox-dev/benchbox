import logging
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from benchbox.core.cost.models import BenchmarkCost, DeploymentMetadata, NormalizedCost, PhaseCost, QueryCost
from benchbox.core.cost.pricing import (
    CURRENCY,
    PRICING_VERSION,
    PriceResolution,
    get_pricing_age_days,
    get_table_unit,
    resolve_athena_price_per_tb,
    resolve_bigquery_price_per_tb,
    resolve_databricks_dbu_price,
    resolve_fabric_cu_price,
    resolve_fabric_sku_cu_count,
    resolve_firebolt_fbu_price,
    resolve_firebolt_fbu_rate,
    resolve_redshift_node_price,
    resolve_snowflake_credit_price,
    resolve_snowflake_warehouse_credits_per_hour,
    resolve_synapse_dedicated_price,
    resolve_synapse_serverless_price_per_tb,
)

logger = logging.getLogger(__name__)
_COST_MODEL_SOURCE = "benchbox.core.cost.pricing"


BYTES_PER_UNIT: dict[str, int] = {
    "tebibyte": 1024**4,
    "terabyte": 10**12,
}


def _byte_unit_for_table(table: str) -> tuple[str, int]:

    unit = get_table_unit(table)
    if unit is None:
        raise KeyError(f"No unit declared for price table {table!r} in pricing_data.yaml")
    return unit, BYTES_PER_UNIT[unit]


def _stamp_price_unavailable(details: dict[str, Any], resolution: PriceResolution) -> None:

    if resolution.fallback_used or resolution.value is None:
        details["price_unavailable"] = {
            "table": resolution.table,
            "resolved_key": list(resolution.resolved_key),
            "reason": resolution.reason,
        }


def _fallback_price_tables(benchmark_cost: BenchmarkCost) -> dict[str, str | None]:

    markers: dict[str, str | None] = {}
    for phase in benchmark_cost.phase_costs or []:
        for query_cost in phase.query_costs or []:
            marker = query_cost.pricing_details.get("price_unavailable")
            if not isinstance(marker, dict):
                continue
            table = marker.get("table")
            if not isinstance(table, str) or not table or table in markers:
                continue
            reason = marker.get("reason")
            markers[table] = reason if isinstance(reason, str) and reason else None
    return markers


def _execution_seconds_from_resource_usage(resource_usage: dict[str, Any]) -> float | None:

    milliseconds = resource_usage.get("execution_time_ms")
    if isinstance(milliseconds, (int, float)) and not isinstance(milliseconds, bool):
        return float(milliseconds) / 1000.0
    seconds = resource_usage.get("execution_time_seconds")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
        return float(seconds)
    milliseconds = resource_usage.get("total_elapsed_time_ms")
    if isinstance(milliseconds, (int, float)) and not isinstance(milliseconds, bool):
        return float(milliseconds) / 1000.0
    return None


def _load_cost_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("cost_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


RESOURCE_USAGE_SCHEMA = _load_cost_specs()["resource_usage_schema"]


def validate_resource_usage(platform: str, resource_usage: dict[str, Any]) -> tuple[bool, list[str]]:

    warnings = []
    platform_lower = platform.lower()

    if platform_lower not in RESOURCE_USAGE_SCHEMA:
        warnings.append(f"No validation schema defined for platform '{platform}'")
        return True, warnings

    schema = RESOURCE_USAGE_SCHEMA[platform_lower]

    for field in schema.get("required", []):
        if field not in resource_usage:
            warnings.append(f"Missing required field '{field}' for {platform} cost calculation")

    requires_one_of = schema.get("requires_one_of", [])
    if requires_one_of:
        has_at_least_one = any(field in resource_usage for field in requires_one_of)
        if not has_at_least_one:
            warnings.append(f"Missing at least one of {requires_one_of} for {platform} cost calculation")

    expected_fields = set(schema.get("required", []) + schema.get("optional", []))
    unexpected_fields = set(resource_usage.keys()) - expected_fields
    if unexpected_fields:
        warnings.append(f"Unexpected fields in resource_usage for {platform}: {unexpected_fields}")

    is_valid = len(warnings) == 0 or all("Unexpected fields" in w for w in warnings)
    return is_valid, warnings


class CostCalculator:
    def __init__(self) -> None:

        self._platform_calculators: dict[str, Callable[[dict[str, Any], dict[str, Any]], Optional[QueryCost]]] = {
            "snowflake": self._calculate_snowflake_cost,
            "bigquery": self._calculate_bigquery_cost,
            "redshift": self._calculate_redshift_cost,
            "databricks": self._calculate_databricks_cost,
            "databricks-df": self._calculate_databricks_cost,
            "athena": self._calculate_athena_cost,
            "synapse": self._calculate_synapse_cost,
            "fabric_dw": self._calculate_fabric_cost,
            "firebolt": self._calculate_firebolt_cost,
        }

        self._local_platforms = {
            "duckdb",
            "sqlite",
            "clickhouse",
            "clickhouse-local",
            "clickhouse-server",
            "chdb",
            "postgresql",
            "timescaledb",
            "trino",
            "presto",
            "influxdb",
            "spark",
            "pyspark",
            "datafusion",
            "datafusion-df",
            "polars",
            "polars-df",
            "pandas",
            "pandas-df",
            "cudf",
            "cudf-df",
            "dask",
            "dask-df",
            "pyspark-df",
            "lakesail",
            "lakesail-df",
        }

    def is_local_platform(self, platform: str) -> bool:

        return platform.lower() in self._local_platforms

    def calculate_normalized_benchmark_cost(
        self,
        platform: str,
        benchmark_cost: BenchmarkCost,
        platform_config: dict[str, Any],
    ) -> tuple[NormalizedCost, list[str]]:

        platform_lower = platform.lower()
        if self.is_local_platform(platform):
            return (
                NormalizedCost(
                    normalized_cost_usd=Decimal("0"),
                    cost_model_version=PRICING_VERSION,
                    cost_model_source=_COST_MODEL_SOURCE,
                    cost_scope="compute_only",
                    cost_status="not_applicable_local",
                    billing_unit="not_applicable",
                    pricing_region="not_applicable",
                ),
                [],
            )

        billing_unit = self._billing_unit(platform_lower, platform_config)
        deployment = self._deployment_metadata(platform_lower, platform_config)
        pricing_region = self._pricing_region(platform_lower, platform_config)
        warnings = self._normalized_cost_warnings(
            platform_lower,
            benchmark_cost,
            platform_config,
            deployment,
            billing_unit,
            pricing_region,
        )

        if warnings:
            return (
                NormalizedCost(
                    normalized_cost_usd=None,
                    cost_model_version=PRICING_VERSION,
                    cost_model_source=_COST_MODEL_SOURCE,
                    cost_scope="compute_only",
                    cost_status="unavailable",
                    billing_unit=billing_unit or "unknown",
                    pricing_region=pricing_region or "unknown",
                    deployment=deployment,
                    unavailability_reasons=tuple(warnings),
                ),
                warnings,
            )

        return (
            NormalizedCost(
                normalized_cost_usd=Decimal(str(benchmark_cost.total_cost)),
                cost_model_version=PRICING_VERSION,
                cost_model_source=_COST_MODEL_SOURCE,
                cost_scope="compute_only",
                cost_status="normalized",
                billing_unit=billing_unit,
                pricing_region=pricing_region,
                deployment=deployment,
            ),
            [],
        )

    def _billing_unit(self, platform_lower: str, platform_config: dict[str, Any]) -> str:

        if platform_lower == "snowflake":
            return "credit"
        if platform_lower == "bigquery":
            return "tib_scanned"
        if platform_lower == "athena":
            return "tb_scanned"
        if platform_lower == "redshift":
            return "node_hour"
        if platform_lower in {"databricks", "databricks-df"}:
            return "dbu"
        if platform_lower == "synapse":
            mode = str(platform_config.get("mode") or "serverless").lower()
            return "tb_scanned" if mode == "serverless" else "dwu_hour"
        if platform_lower == "fabric_dw":
            return "cu_hour"
        if platform_lower == "firebolt":
            return "fbu"
        return "unknown"

    def _pricing_region(self, platform_lower: str, platform_config: dict[str, Any]) -> str:
        if platform_lower == "bigquery":
            return str(platform_config.get("location") or "")
        return str(platform_config.get("region") or "")

    def _deployment_metadata(self, platform_lower: str, platform_config: dict[str, Any]) -> DeploymentMetadata:
        provider = platform_config.get("cloud") or platform_config.get("cloud_provider")
        if not provider:
            provider = {
                "athena": "aws",
                "bigquery": "gcp",
                "redshift": "aws",
                "synapse": "azure",
                "fabric_dw": "azure",
            }.get(platform_lower)
        return DeploymentMetadata(
            cloud_provider=provider,
            cloud_region=self._pricing_region(platform_lower, platform_config) or None,
            instance_type=platform_config.get("node_type") or platform_config.get("instance_type"),
            warehouse_size=platform_config.get("warehouse_size") or platform_config.get("dwu_level"),
            node_count=platform_config.get("node_count"),
            cluster_size=(
                str(platform_config["cluster_size_dbu_per_hour"])
                if platform_config.get("cluster_size_dbu_per_hour") is not None
                else platform_config.get("cluster_size")
            ),
            storage_format=platform_config.get("storage_format"),
            storage_tier=platform_config.get("storage_tier"),
        )

    def _normalized_cost_warnings(
        self,
        platform_lower: str,
        benchmark_cost: BenchmarkCost,
        platform_config: dict[str, Any],
        deployment: DeploymentMetadata,
        billing_unit: str,
        pricing_region: str,
    ) -> list[str]:
        warnings: list[str] = []
        if not benchmark_cost.phase_costs:
            warnings.append("normalized cost unavailable: no computed query or phase cost measurements")
        for field in platform_config.get("_defaulted_fields", []):
            warnings.append(f"normalized cost unavailable: {field} metadata was defaulted for {platform_lower}")
        if not billing_unit or billing_unit == "unknown":
            warnings.append(f"normalized cost unavailable: billing unit metadata missing for {platform_lower}")
        if not deployment.cloud_provider:
            warnings.append(f"normalized cost unavailable: cloud provider metadata missing for {platform_lower}")
        if not pricing_region:
            warnings.append(f"normalized cost unavailable: pricing region metadata missing for {platform_lower}")
        if platform_lower == "snowflake" and not deployment.warehouse_size:
            warnings.append("normalized cost unavailable: Snowflake warehouse_size metadata missing")
        estimated_concurrent = platform_lower == "snowflake" and any(
            (phase.concurrent_streams or 1) > 1
            and any(query.pricing_details.get("credits_used_estimated") for query in phase.query_costs or [])
            for phase in benchmark_cost.phase_costs or []
        )
        if estimated_concurrent:
            warnings.append("normalized cost unavailable: runtime-estimated Snowflake costs overlap concurrent streams")
        if platform_lower == "redshift":
            if not deployment.instance_type:
                warnings.append("normalized cost unavailable: Redshift node_type metadata missing")
            if not deployment.node_count:
                warnings.append("normalized cost unavailable: Redshift node_count metadata missing")
        if platform_lower in {"databricks", "databricks-df"} and not (
            deployment.warehouse_size or deployment.cluster_size
        ):
            warnings.append("normalized cost unavailable: Databricks warehouse or cluster size metadata missing")
        for table, reason in sorted(_fallback_price_tables(benchmark_cost).items()):
            if reason:
                warnings.append(f"normalized cost unavailable: fallback pricing used for {table} ({reason})")
            else:
                warnings.append(f"normalized cost unavailable: fallback pricing used for {table}")
        pricing_tables = {
            "snowflake": ("snowflake_credit_prices",),
            "bigquery": ("bigquery_on_demand_prices",),
            "redshift": ("redshift_node_prices",),
            "databricks": ("databricks_dbu_prices",),
            "databricks-df": ("databricks_dbu_prices",),
            "athena": ("athena_price_per_tb",),
            "synapse": (
                "synapse_dedicated_dwu_prices"
                if str(platform_config.get("mode") or "serverless").lower() == "dedicated"
                else "synapse_serverless_price_per_tb",
            ),
            "fabric_dw": ("fabric_cu_prices", "fabric_sku_cu_map"),
            "firebolt": ("firebolt_node_fbu_rates", "firebolt_fbu_price"),
        }.get(platform_lower, ())
        unknown_tables = [table for table in pricing_tables if get_pricing_age_days(table) is None]
        stale_tables = [table for table in pricing_tables if (get_pricing_age_days(table) or 0) > 90]
        if unknown_tables:
            warnings.append(
                "normalized cost unavailable: pricing provenance is unknown for " + ", ".join(unknown_tables)
            )
        if stale_tables:
            warnings.append("normalized cost unavailable: pricing tables are stale: " + ", ".join(stale_tables))
        return warnings

    def calculate_query_cost(
        self,
        platform: str,
        resource_usage: dict[str, Any],
        platform_config: dict[str, Any],
        validate: bool = True,
    ) -> Optional[QueryCost]:

        platform_lower = platform.lower()

        if validate:
            is_valid, validation_warnings = validate_resource_usage(platform, resource_usage)
            for warning in validation_warnings:
                if not warning.startswith("Unexpected fields"):
                    logger.warning(f"Resource usage validation: {warning}")
                else:
                    logger.debug(f"Resource usage validation: {warning}")

        try:
            if platform_lower in self._local_platforms:
                return QueryCost(
                    compute_cost=0.0,
                    currency=CURRENCY,
                    pricing_details={"platform": platform_lower, "note": "Local execution, no cloud costs"},
                )

            calculator = self._platform_calculators.get(platform_lower)
            if calculator:
                return calculator(resource_usage, platform_config)

            logger.warning(f"Cost calculation not supported for platform: {platform}")
            return None
        except Exception as e:
            logger.warning(f"Failed to calculate cost for {platform}: {e}")
            return None

    def _calculate_snowflake_cost(
        self,
        resource_usage: dict[str, Any],
        platform_config: dict[str, Any],
    ) -> Optional[QueryCost]:

        edition = platform_config.get("edition", "standard")
        cloud = platform_config.get("cloud", "aws")
        region = platform_config.get("region", "us-east-1")

        resolution = resolve_snowflake_credit_price(edition, cloud, region)
        if resolution.value is None:
            return None
        price_per_credit = resolution.value

        credits_used = resource_usage.get("credits_used")
        if credits_used is not None:
            compute_cost = credits_used * price_per_credit
            details: dict[str, Any] = {
                "credits_used": credits_used,
                "price_per_credit": price_per_credit,
                "edition": edition,
                "cloud": cloud,
                "region": region,
            }
            execution_seconds = _execution_seconds_from_resource_usage(resource_usage)
            if execution_seconds is not None:
                details["execution_time_seconds"] = execution_seconds
            _stamp_price_unavailable(details, resolution)
            return QueryCost(
                compute_cost=compute_cost,
                currency=CURRENCY,
                pricing_details=details,
            )

        execution_seconds = _execution_seconds_from_resource_usage(resource_usage)
        warehouse_size = resource_usage.get("warehouse_size") or platform_config.get("warehouse_size")
        if execution_seconds is None or warehouse_size is None:
            return None

        size_resolution = resolve_snowflake_warehouse_credits_per_hour(str(warehouse_size))
        if size_resolution.value is None:
            return None
        credits_per_hour = size_resolution.value

        credits_used_estimated = (execution_seconds / 3600.0) * credits_per_hour
        compute_cost = credits_used_estimated * price_per_credit

        estimated_details: dict[str, Any] = {
            "credits_used": credits_used_estimated,
            "credits_used_estimated": True,
            "execution_time_seconds": execution_seconds,
            "warehouse_size": warehouse_size,
            "credits_per_hour": credits_per_hour,
            "price_per_credit": price_per_credit,
            "edition": edition,
            "cloud": cloud,
            "region": region,
            "note": "Warehouse credits estimated from measured execution time; warehouse idle time excluded",
        }

        _stamp_price_unavailable(estimated_details, size_resolution)
        _stamp_price_unavailable(estimated_details, resolution)
        return QueryCost(
            compute_cost=compute_cost,
            currency=CURRENCY,
            pricing_details=estimated_details,
        )

    def _calculate_bigquery_cost(
        self,
        resource_usage: dict[str, Any],
        platform_config: dict[str, Any],
    ) -> Optional[QueryCost]:

        bytes_processed = resource_usage.get("bytes_billed") or resource_usage.get("bytes_processed")
        if bytes_processed is None:
            return None

        location = platform_config.get("location", "us")

        resolution = resolve_bigquery_price_per_tb(location)
        if resolution.value is None:
            return None
        price_per_tb = resolution.value

        unit, bytes_per_unit = _byte_unit_for_table("bigquery_on_demand_prices")
        tb_processed = bytes_processed / bytes_per_unit
        compute_cost = tb_processed * price_per_tb

        details = {
            "bytes_processed": bytes_processed,
            "tb_processed": tb_processed,
            "price_per_tb": price_per_tb,
            "unit": unit,
            "location": location,
        }
        _stamp_price_unavailable(details, resolution)
        return QueryCost(
            compute_cost=compute_cost,
            currency=CURRENCY,
            pricing_details=details,
        )

    def _calculate_redshift_cost(
        self,
        resource_usage: dict[str, Any],
        platform_config: dict[str, Any],
    ) -> Optional[QueryCost]:

        execution_time_seconds = resource_usage.get("execution_time_seconds")
        if execution_time_seconds is None:
            return None

        node_type = platform_config.get("node_type", "dc2.large")
        node_count = platform_config.get("node_count", 1)
        region = platform_config.get("region", "us-east-1")

        resolution = resolve_redshift_node_price(node_type, region)
        if resolution.value is None:
            return None
        price_per_node_hour = resolution.value

        hours = execution_time_seconds / 3600.0
        compute_cost = hours * node_count * price_per_node_hour

        details = {
            "execution_time_seconds": execution_time_seconds,
            "node_type": node_type,
            "node_count": node_count,
            "price_per_node_hour": price_per_node_hour,
            "region": region,
        }
        _stamp_price_unavailable(details, resolution)
        return QueryCost(
            compute_cost=compute_cost,
            currency=CURRENCY,
            pricing_details=details,
        )

    def _calculate_databricks_cost(
        self,
        resource_usage: dict[str, Any],
        platform_config: dict[str, Any],
    ) -> Optional[QueryCost]:

        dbu_consumed = resource_usage.get("dbu_consumed")

        if dbu_consumed is None:
            execution_time_seconds = resource_usage.get("execution_time_seconds")
            cluster_size_dbu_per_hour = platform_config.get("cluster_size_dbu_per_hour")

            if execution_time_seconds is None or cluster_size_dbu_per_hour is None:
                return None

            hours = execution_time_seconds / 3600.0
            dbu_consumed = hours * cluster_size_dbu_per_hour
            is_estimated = True
        else:
            is_estimated = False

        cloud = platform_config.get("cloud", "aws")
        tier = platform_config.get("tier", "premium")
        workload_type = platform_config.get("workload_type", "all_purpose")

        resolution = resolve_databricks_dbu_price(cloud, tier, workload_type)
        if resolution.value is None:
            return None
        price_per_dbu = resolution.value

        compute_cost = dbu_consumed * price_per_dbu

        details: dict[str, Any] = {
            "dbu_consumed": dbu_consumed,
            "price_per_dbu": price_per_dbu,
            "cloud": cloud,
            "tier": tier,
            "workload_type": workload_type,
            "is_estimated": is_estimated,
        }
        _stamp_price_unavailable(details, resolution)

        if is_estimated:
            details["note"] = "DBU consumption estimated from execution time"

        return QueryCost(
            compute_cost=compute_cost,
            currency=CURRENCY,
            pricing_details=details,
        )

    def _calculate_athena_cost(
        self,
        resource_usage: dict[str, Any],
        platform_config: dict[str, Any],
    ) -> Optional[QueryCost]:

        data_scanned_bytes = resource_usage.get("data_scanned_bytes")
        if data_scanned_bytes is None:
            return None

        region = platform_config.get("region", "us-east-1")
        resolution = resolve_athena_price_per_tb(region)
        if resolution.value is None:
            return None
        price_per_tb = resolution.value

        unit, bytes_per_unit = _byte_unit_for_table("athena_price_per_tb")
        tb_scanned = data_scanned_bytes / bytes_per_unit
        compute_cost = tb_scanned * price_per_tb

        details = {
            "data_scanned_bytes": data_scanned_bytes,
            "tb_scanned": tb_scanned,
            "price_per_tb": price_per_tb,
            "unit": unit,
            "region": region,
        }
        _stamp_price_unavailable(details, resolution)
        return QueryCost(
            compute_cost=compute_cost,
            currency=CURRENCY,
            pricing_details=details,
        )

    def _calculate_synapse_cost(
        self,
        resource_usage: dict[str, Any],
        platform_config: dict[str, Any],
    ) -> Optional[QueryCost]:

        mode = str(platform_config.get("mode") or "serverless").lower()
        region = platform_config.get("region", "eastus")

        if mode == "serverless":
            bytes_processed = resource_usage.get("bytes_processed")
            if bytes_processed is None:
                return None

            resolution = resolve_synapse_serverless_price_per_tb(region)
            if resolution.value is None:
                return None
            price_per_tb = resolution.value
            unit, bytes_per_unit = _byte_unit_for_table("synapse_serverless_price_per_tb")
            tb_processed = bytes_processed / bytes_per_unit
            compute_cost = tb_processed * price_per_tb

            details = {
                "mode": "serverless",
                "bytes_processed": bytes_processed,
                "tb_processed": tb_processed,
                "price_per_tb": price_per_tb,
                "unit": unit,
                "region": region,
            }
            _stamp_price_unavailable(details, resolution)
            return QueryCost(
                compute_cost=compute_cost,
                currency=CURRENCY,
                pricing_details=details,
            )
        else:
            execution_time_seconds = resource_usage.get("execution_time_seconds")
            if execution_time_seconds is None:
                return None

            dwu_level = platform_config.get("dwu_level", "dw100c")
            resolution = resolve_synapse_dedicated_price(dwu_level, region)
            if resolution.value is None:
                return None
            price_per_hour = resolution.value

            hours = execution_time_seconds / 3600.0
            compute_cost = hours * price_per_hour

            details = {
                "mode": "dedicated",
                "execution_time_seconds": execution_time_seconds,
                "dwu_level": dwu_level,
                "price_per_hour": price_per_hour,
                "region": region,
            }
            _stamp_price_unavailable(details, resolution)
            return QueryCost(
                compute_cost=compute_cost,
                currency=CURRENCY,
                pricing_details=details,
            )

    def _calculate_fabric_cost(
        self,
        resource_usage: dict[str, Any],
        platform_config: dict[str, Any],
    ) -> Optional[QueryCost]:

        region = platform_config.get("region", "eastus")
        sku = platform_config.get("sku", "f64")

        cu_seconds = resource_usage.get("cu_seconds")

        if cu_seconds is None:
            execution_time_seconds = resource_usage.get("execution_time_seconds")
            if execution_time_seconds is None:
                return None

            sku_resolution = resolve_fabric_sku_cu_count(sku)
            if sku_resolution.value is None:
                return None
            cu_count = sku_resolution.value
            cu_seconds = execution_time_seconds * cu_count
            is_estimated = True
        else:
            sku_resolution = None
            is_estimated = False

        cu_hours = cu_seconds / 3600.0
        price_resolution = resolve_fabric_cu_price(region)
        if price_resolution.value is None:
            return None
        price_per_cu_hour = price_resolution.value
        compute_cost = cu_hours * price_per_cu_hour

        details: dict[str, Any] = {
            "cu_seconds": cu_seconds,
            "cu_hours": cu_hours,
            "price_per_cu_hour": price_per_cu_hour,
            "sku": sku,
            "region": region,
            "is_estimated": is_estimated,
        }
        if sku_resolution is not None:
            _stamp_price_unavailable(details, sku_resolution)
        _stamp_price_unavailable(details, price_resolution)

        if is_estimated:
            details["note"] = "CU consumption estimated from execution time and SKU"

        return QueryCost(
            compute_cost=compute_cost,
            currency=CURRENCY,
            pricing_details=details,
        )

    def _calculate_firebolt_cost(
        self,
        resource_usage: dict[str, Any],
        platform_config: dict[str, Any],
    ) -> Optional[QueryCost]:

        fbu_consumed = resource_usage.get("fbu_consumed")

        if fbu_consumed is None:
            execution_time_seconds = resource_usage.get("execution_time_seconds")
            if execution_time_seconds is None:
                return None

            node_type = platform_config.get("node_type", "m")
            node_count = platform_config.get("node_count", 1)

            rate_resolution = resolve_firebolt_fbu_rate(node_type)
            if rate_resolution.value is None:
                return None
            fbu_per_hour = rate_resolution.value

            hours = execution_time_seconds / 3600.0
            fbu_consumed = hours * fbu_per_hour * node_count
            is_estimated = True
        else:
            rate_resolution = None
            is_estimated = False
            node_type = platform_config.get("node_type", "unknown")
            node_count = platform_config.get("node_count", 1)

        price_resolution = resolve_firebolt_fbu_price()
        if price_resolution.value is None:
            return None
        fbu_price = price_resolution.value
        compute_cost = fbu_consumed * fbu_price

        details: dict[str, Any] = {
            "fbu_consumed": fbu_consumed,
            "fbu_price": fbu_price,
            "node_type": node_type,
            "node_count": node_count,
            "is_estimated": is_estimated,
        }
        if rate_resolution is not None:
            _stamp_price_unavailable(details, rate_resolution)
        _stamp_price_unavailable(details, price_resolution)

        if is_estimated:
            details["note"] = "FBU consumption estimated from execution time and node configuration"

        return QueryCost(
            compute_cost=compute_cost,
            currency=CURRENCY,
            pricing_details=details,
        )

    def calculate_phase_cost(
        self,
        phase_name: str,
        query_costs: list[QueryCost],
    ) -> PhaseCost:

        valid_costs = [qc for qc in query_costs if qc is not None]

        total = sum(qc.compute_cost for qc in valid_costs)

        return PhaseCost(
            phase_name=phase_name,
            total_cost=total,
            query_count=len(query_costs),
            currency=CURRENCY,
            query_costs=valid_costs if valid_costs else None,
        )

    def calculate_benchmark_cost(
        self,
        phase_costs: list[PhaseCost],
        platform_details: Optional[dict[str, Any]] = None,
    ) -> BenchmarkCost:

        return BenchmarkCost.from_phase_costs(
            phase_costs=phase_costs,
            platform_details=platform_details,
            currency=CURRENCY,
        )
