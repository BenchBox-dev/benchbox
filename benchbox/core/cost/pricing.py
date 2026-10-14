import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from importlib import resources
from typing import Any, cast

import yaml

logger = logging.getLogger(__name__)


def _load_pricing_data() -> dict[str, Any]:
    with resources.files(__package__).joinpath("pricing_data.yaml").open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("pricing_data.yaml must contain a mapping")
    return cast("dict[str, Any]", payload)


_PRICING_DATA = _load_pricing_data()
_PRICING_METADATA = cast("dict[str, str]", _PRICING_DATA["metadata"])


PRICING_VERSION = _PRICING_METADATA["version"]


PRICING_LAST_UPDATED: str = _PRICING_METADATA.get("last_updated", "unknown")
PRICING_SOURCE = _PRICING_METADATA["source"]
try:
    PRICING_VALIDATION_DATE = (
        datetime.fromisoformat(PRICING_LAST_UPDATED) if PRICING_LAST_UPDATED != "unknown" else None
    )
except ValueError:
    PRICING_VALIDATION_DATE = None


CURRENCY = _PRICING_METADATA["currency"]

SNOWFLAKE_CREDIT_PRICES: dict[str, dict[str, dict[str, float]]] = cast(
    "dict[str, dict[str, dict[str, float]]]", _PRICING_DATA["snowflake_credit_prices"]
)
ATHENA_PRICE_PER_TB: dict[str, float] = cast("dict[str, float]", _PRICING_DATA["athena_price_per_tb"])
BIGQUERY_ON_DEMAND_PRICES: dict[str, float] = cast("dict[str, float]", _PRICING_DATA["bigquery_on_demand_prices"])
REDSHIFT_NODE_PRICES: dict[str, dict[str, float]] = cast(
    "dict[str, dict[str, float]]", _PRICING_DATA["redshift_node_prices"]
)
DATABRICKS_DBU_PRICES: dict[str, dict[str, dict[str, float]]] = cast(
    "dict[str, dict[str, dict[str, float]]]", _PRICING_DATA["databricks_dbu_prices"]
)
SYNAPSE_SERVERLESS_PRICE_PER_TB: dict[str, float] = cast(
    "dict[str, float]", _PRICING_DATA["synapse_serverless_price_per_tb"]
)
SYNAPSE_DEDICATED_DWU_PRICES: dict[str, dict[str, float]] = cast(
    "dict[str, dict[str, float]]", _PRICING_DATA["synapse_dedicated_dwu_prices"]
)
FABRIC_CU_PRICES: dict[str, float] = cast("dict[str, float]", _PRICING_DATA["fabric_cu_prices"])
FABRIC_SKU_CU_MAP: dict[str, int] = cast("dict[str, int]", _PRICING_DATA["fabric_sku_cu_map"])
FIREBOLT_NODE_FBU_RATES: dict[str, float] = cast("dict[str, float]", _PRICING_DATA["firebolt_node_fbu_rates"])
FIREBOLT_FBU_PRICE = float(_PRICING_DATA["firebolt_fbu_price"])


PRICE_TABLE_PROVENANCE: dict[str, dict[str, Any]] = cast(
    "dict[str, dict[str, Any]]", _PRICING_DATA.get("provenance", {})
)
PRICE_TABLE_UNITS: dict[str, str] = cast("dict[str, str]", _PRICING_DATA.get("units", {}))

PROVENANCE_METHODS = frozenset({"api", "manual", "derived_from_announcement"})


BYTE_PRICED_TABLES = frozenset(
    {
        "bigquery_on_demand_prices",
        "athena_price_per_tb",
        "synapse_serverless_price_per_tb",
    }
)


def get_table_provenance(table: str) -> dict[str, Any] | None:

    entry = PRICE_TABLE_PROVENANCE.get(table)
    return dict(entry) if isinstance(entry, dict) else None


def get_table_unit(table: str) -> str | None:

    unit = PRICE_TABLE_UNITS.get(table)
    return unit if isinstance(unit, str) else None


@dataclass(frozen=True)
class PriceResolution:
    value: float | int | None
    table: str
    resolved_key: tuple[str, ...]
    fallback_used: bool
    unit: str | None = None
    reason: str | None = None


DATABRICKS_WAREHOUSE_DBU_PER_HOUR: dict[str, float] = {
    "2X-Small": 1.0,
    "X-Small": 2.0,
    "Small": 4.0,
    "Medium": 8.0,
    "Large": 16.0,
    "X-Large": 32.0,
    "2X-Large": 64.0,
    "3X-Large": 128.0,
    "4X-Large": 256.0,
}

DATABRICKS_SERVERLESS_SQL_DBU_PER_HOUR: dict[str, float] = {
    "2X-Small": 4.0,
    "X-Small": 6.0,
    "Small": 12.0,
    "Medium": 24.0,
    "Large": 40.0,
    "X-Large": 80.0,
    "2X-Large": 144.0,
    "3X-Large": 272.0,
    "4X-Large": 528.0,
}


SNOWFLAKE_WAREHOUSE_CREDITS_PER_HOUR: dict[str, float] = {
    "X-Small": 1.0,
    "Small": 2.0,
    "Medium": 4.0,
    "Large": 8.0,
    "X-Large": 16.0,
    "2X-Large": 32.0,
    "3X-Large": 64.0,
    "4X-Large": 128.0,
    "5X-Large": 256.0,
    "6X-Large": 512.0,
}


def _normalize_warehouse_size_label(warehouse_size: str) -> str:

    return warehouse_size.strip().lower().replace("-", "").replace(" ", "").replace("_", "")


_SNOWFLAKE_SIZE_ALIASES: dict[str, str] = {
    "XS": "X-Small",
    "S": "Small",
    "M": "Medium",
    "L": "Large",
    "XL": "X-Large",
    "2XL": "2X-Large",
    "3XL": "3X-Large",
    "4XL": "4X-Large",
    "5XL": "5X-Large",
    "6XL": "6X-Large",
}


def _snowflake_credits_by_normalized_label() -> dict[str, tuple[str, float]]:

    table: dict[str, tuple[str, float]] = {}
    for size, credits in SNOWFLAKE_WAREHOUSE_CREDITS_PER_HOUR.items():
        table[_normalize_warehouse_size_label(size)] = (size, credits)
    for alias, canonical in _SNOWFLAKE_SIZE_ALIASES.items():
        table[_normalize_warehouse_size_label(alias)] = (
            canonical,
            SNOWFLAKE_WAREHOUSE_CREDITS_PER_HOUR[canonical],
        )
    return table


_SNOWFLAKE_CREDITS_BY_LABEL = _snowflake_credits_by_normalized_label()


def _resolve_regional_tb_rate(
    *,
    table: str,
    region: str,
    prices: dict[str, float],
    default_region: str,
    service_label: str,
) -> PriceResolution:

    unit = get_table_unit(table)
    normalized = (region or "").strip().lower()
    display = (region or "").strip()
    if normalized in prices:
        return PriceResolution(
            value=prices[normalized],
            table=table,
            resolved_key=(normalized,),
            fallback_used=False,
            unit=unit,
        )
    value = prices[default_region]
    logger.warning(
        f"{service_label} price for region '{display}' is unpriced; using {default_region} "
        f"(${value:.2f}/TB) as fallback"
    )
    return PriceResolution(
        value=value,
        table=table,
        resolved_key=(default_region,),
        fallback_used=True,
        unit=unit,
        reason=f"{service_label.lower()} region '{display}' is unpriced; {default_region} rate is a guess",
    )


def _make_regional_tb_price_resolver(
    *,
    table: str,
    prices: dict[str, float],
    default_region: str,
    service_label: str,
    name: str,
) -> Callable[[str], PriceResolution]:

    def _resolve(region: str = "") -> PriceResolution:
        return _resolve_regional_tb_rate(
            table=table,
            region=region,
            prices=prices,
            default_region=default_region,
            service_label=service_label,
        )

    _resolve.__name__ = name
    _resolve.__qualname__ = name
    _resolve.__module__ = __name__
    return _resolve


resolve_athena_price_per_tb = _make_regional_tb_price_resolver(
    table="athena_price_per_tb",
    prices=ATHENA_PRICE_PER_TB,
    default_region="us-east-1",
    service_label="Athena",
    name="resolve_athena_price_per_tb",
)


def resolve_snowflake_credit_price(edition: str, cloud: str, region: str) -> PriceResolution:

    table = "snowflake_credit_prices"
    edition = edition.strip().lower().replace("-", "_").replace(" ", "_")
    cloud = cloud.strip().lower()

    region_tier = _map_region_to_tier(region)

    try:
        return PriceResolution(
            value=SNOWFLAKE_CREDIT_PRICES[edition][cloud][region_tier],
            table=table,
            resolved_key=(edition, cloud, region_tier),
            fallback_used=False,
        )
    except KeyError:
        value = SNOWFLAKE_CREDIT_PRICES.get("standard", {}).get("aws", {}).get("us", 2.00)
        logger.warning(
            f"Unknown Snowflake edition/cloud '{edition}/{cloud}'; defaulting to standard/aws/us (${value:.2f}/credit)"
        )
        return PriceResolution(
            value=value,
            table=table,
            resolved_key=("standard", "aws", "us"),
            fallback_used=True,
            reason=f"snowflake edition/cloud '{edition}/{cloud}' not in price table",
        )


def resolve_bigquery_price_per_tb(location: str) -> PriceResolution:

    table = "bigquery_on_demand_prices"
    unit = get_table_unit(table)
    location = location.strip().lower()

    def _hit(bucket: str, *, fallback_used: bool = False, reason: str | None = None) -> PriceResolution:
        return PriceResolution(
            value=BIGQUERY_ON_DEMAND_PRICES[bucket],
            table=table,
            resolved_key=(bucket,),
            fallback_used=fallback_used,
            unit=unit,
            reason=reason,
        )

    if location in ["us", "us-multi"]:
        return _hit("us")
    elif location in ["eu", "eu-multi"]:
        return _hit("eu")
    elif location in ["asia", "asia-multi"]:
        return _hit("asia")

    if location in BIGQUERY_ON_DEMAND_PRICES and location != "other":
        return _hit(location)

    us_single_regions = {
        "us-central1",
        "us-east1",
        "us-east4",
        "us-west1",
        "us-west2",
        "us-west3",
        "us-west4",
        "northamerica-northeast1",
        "northamerica-northeast2",
        "northamerica-south1",
    }
    if location in us_single_regions or location.startswith("us-"):
        return _hit("us-single")

    eu_single_regions = {
        "europe-central2",
        "europe-north1",
        "europe-north2",
        "europe-southwest1",
        "europe-west1",
        "europe-west2",
        "europe-west3",
        "europe-west4",
        "europe-west6",
        "europe-west8",
        "europe-west9",
        "europe-west10",
        "europe-west12",
    }
    if location in eu_single_regions or location.startswith("europe-"):
        return _hit("eu-single")

    asia_single_regions = {
        "asia-east1",
        "asia-east2",
        "asia-northeast1",
        "asia-northeast2",
        "asia-northeast3",
        "asia-south1",
        "asia-south2",
        "asia-southeast1",
        "asia-southeast2",
        "asia-southeast3",
        "asia-southeast4",
    }
    if location in asia_single_regions or location.startswith("asia-"):
        return _hit("asia-single")

    australia_regions = {"australia-southeast1", "australia-southeast2"}
    if location in australia_regions or location.startswith("australia-"):
        return _hit("australia")

    southamerica_regions = {"southamerica-east1", "southamerica-west1"}
    if location in southamerica_regions or location.startswith("southamerica-"):
        return _hit("southamerica")

    middleeast_regions = {"me-west1", "me-central1", "me-central2"}
    if location in middleeast_regions or location.startswith("me-"):
        return _hit("middleeast")

    africa_regions = {"africa-south1"}
    if location in africa_regions or location.startswith("africa-"):
        logger.warning(f"BigQuery location '{location}' has no priced bucket; using 'other' as fallback")
        return _hit(
            "other",
            fallback_used=True,
            reason=f"bigquery location '{location}' is unpriced; 'other' is a guess",
        )

    logger.warning(f"Unknown BigQuery location '{location}'; using 'other' pricing as fallback")
    return _hit(
        "other",
        fallback_used=True,
        reason=f"bigquery location '{location}' is unknown; 'other' is a guess",
    )


def resolve_redshift_node_price(node_type: str, region: str) -> PriceResolution:

    table = "redshift_node_prices"
    node_type = node_type.strip().lower()
    region = region.strip().lower()

    try:
        return PriceResolution(
            value=REDSHIFT_NODE_PRICES[node_type][region],
            table=table,
            resolved_key=(node_type, region),
            fallback_used=False,
        )
    except KeyError:
        if node_type in REDSHIFT_NODE_PRICES:
            return PriceResolution(
                value=REDSHIFT_NODE_PRICES[node_type].get("other", 1.00),
                table=table,
                resolved_key=(node_type, "other"),
                fallback_used=False,
            )

        logger.warning(f"Unknown Redshift node type '{node_type}'; defaulting to $1.00/node-hour")
        return PriceResolution(
            value=1.00,
            table=table,
            resolved_key=(node_type, region),
            fallback_used=True,
            reason=f"redshift node type '{node_type}' not in price table",
        )


def resolve_databricks_dbu_price(cloud: str, tier: str, workload_type: str) -> PriceResolution:

    table = "databricks_dbu_prices"
    cloud = cloud.strip().lower()
    tier = tier.strip().lower()
    workload_type = workload_type.strip().lower().replace("-", "_").replace(" ", "_")

    if workload_type == "serverless_sql":
        workload_type = "sql_serverless"
    elif workload_type == "sql_compute":
        workload_type = "sql_pro"

    try:
        return PriceResolution(
            value=DATABRICKS_DBU_PRICES[cloud][tier][workload_type],
            table=table,
            resolved_key=(cloud, tier, workload_type),
            fallback_used=False,
        )
    except KeyError:
        value = DATABRICKS_DBU_PRICES.get("aws", {}).get("premium", {}).get("all_purpose", 0.55)
        logger.warning(
            f"Unknown Databricks key '{cloud}/{tier}/{workload_type}'; defaulting to aws/premium/all_purpose "
            f"(${value:.2f}/DBU)"
        )
        return PriceResolution(
            value=value,
            table=table,
            resolved_key=("aws", "premium", "all_purpose"),
            fallback_used=True,
            reason=f"databricks key '{cloud}/{tier}/{workload_type}' not in price table",
        )


def resolve_databricks_warehouse_dbu_per_hour(warehouse_size: str) -> PriceResolution:

    table = "databricks_warehouse_dbu_per_hour"
    normalized = warehouse_size.strip()
    for size, dbu in DATABRICKS_WAREHOUSE_DBU_PER_HOUR.items():
        if size.lower() == normalized.lower():
            return PriceResolution(
                value=dbu,
                table=table,
                resolved_key=(size,),
                fallback_used=False,
                unit="DBU/hour",
            )
    logger.warning(
        f"Unknown Databricks warehouse size '{warehouse_size.strip()}'; defaulting to a conservative 2.0 DBU/hour"
    )
    return PriceResolution(
        value=2.0,
        table=table,
        resolved_key=(normalized,),
        fallback_used=True,
        unit="DBU/hour",
        reason=f"databricks warehouse size '{normalized}' not in size map",
    )


def resolve_databricks_serverless_sql_dbu_per_hour(warehouse_size: str) -> PriceResolution:

    table = "databricks_serverless_sql_dbu_per_hour"
    normalized = warehouse_size.strip()
    for size, dbu in DATABRICKS_SERVERLESS_SQL_DBU_PER_HOUR.items():
        if size.lower() == normalized.lower():
            return PriceResolution(
                value=dbu,
                table=table,
                resolved_key=(size,),
                fallback_used=False,
                unit="DBU/hour",
            )
    return PriceResolution(
        value=None,
        table=table,
        resolved_key=(normalized,),
        fallback_used=True,
        unit="DBU/hour",
        reason=f"Databricks serverless SQL warehouse size '{normalized}' not in size map",
    )


def resolve_snowflake_warehouse_credits_per_hour(warehouse_size: str) -> PriceResolution:

    table = "snowflake_warehouse_credits_per_hour"
    normalized = _normalize_warehouse_size_label(warehouse_size)
    if normalized in _SNOWFLAKE_CREDITS_BY_LABEL:
        size, credits = _SNOWFLAKE_CREDITS_BY_LABEL[normalized]
        return PriceResolution(
            value=credits,
            table=table,
            resolved_key=(size,),
            fallback_used=False,
            unit="credits/hour",
        )
    logger.warning(
        f"Unknown Snowflake warehouse size '{warehouse_size.strip()}'; defaulting to a Medium 4.0 credits/hour"
    )
    return PriceResolution(
        value=4.0,
        table=table,
        resolved_key=(warehouse_size.strip(),),
        fallback_used=True,
        unit="credits/hour",
        reason=f"snowflake warehouse size '{warehouse_size.strip()}' not in size map",
    )


resolve_synapse_serverless_price_per_tb = _make_regional_tb_price_resolver(
    table="synapse_serverless_price_per_tb",
    prices=SYNAPSE_SERVERLESS_PRICE_PER_TB,
    default_region="eastus",
    service_label="Synapse serverless",
    name="resolve_synapse_serverless_price_per_tb",
)


def resolve_synapse_dedicated_price(dwu_level: str, region: str) -> PriceResolution:

    table = "synapse_dedicated_dwu_prices"
    dwu_level = dwu_level.strip().lower()
    region_tier = _map_region_to_tier(region)

    try:
        return PriceResolution(
            value=SYNAPSE_DEDICATED_DWU_PRICES[dwu_level][region_tier],
            table=table,
            resolved_key=(dwu_level, region_tier),
            fallback_used=False,
        )
    except KeyError:
        if dwu_level in SYNAPSE_DEDICATED_DWU_PRICES:
            price = SYNAPSE_DEDICATED_DWU_PRICES[dwu_level].get("us", 1.20)
            if region_tier != "us":
                logger.warning(
                    f"Regional pricing for Synapse {dwu_level} in tier '{region_tier}' not available; "
                    f"using US pricing as fallback"
                )
            return PriceResolution(
                value=price,
                table=table,
                resolved_key=(dwu_level, "us"),
                fallback_used=True,
                reason=f"synapse tier '{region_tier}' missing for {dwu_level}; US pricing is a guess",
            )
        logger.warning(f"DWU level '{dwu_level}' not found in pricing table; defaulting to DW100c US pricing")
        return PriceResolution(
            value=SYNAPSE_DEDICATED_DWU_PRICES.get("dw100c", {}).get("us", 1.20),
            table=table,
            resolved_key=("dw100c", "us"),
            fallback_used=True,
            reason=f"synapse DWU level '{dwu_level}' not in price table",
        )


def resolve_fabric_cu_price(region: str) -> PriceResolution:

    table = "fabric_cu_prices"
    region_tier = _map_region_to_tier(region)
    if region_tier in FABRIC_CU_PRICES:
        return PriceResolution(
            value=FABRIC_CU_PRICES[region_tier],
            table=table,
            resolved_key=(region_tier,),
            fallback_used=False,
        )
    return PriceResolution(
        value=FABRIC_CU_PRICES["other"],
        table=table,
        resolved_key=("other",),
        fallback_used=False,
    )


def resolve_fabric_sku_cu_count(sku: str) -> PriceResolution:

    table = "fabric_sku_cu_map"
    normalized = sku.strip().lower()
    cu_count = FABRIC_SKU_CU_MAP.get(normalized)
    if cu_count is not None:
        return PriceResolution(
            value=cu_count,
            table=table,
            resolved_key=(normalized,),
            fallback_used=False,
            unit="CU",
        )
    logger.warning(f"Unknown Fabric SKU '{sku.strip()}'; defaulting to F2 (2 CUs)")
    return PriceResolution(
        value=2,
        table=table,
        resolved_key=(normalized,),
        fallback_used=True,
        unit="CU",
        reason=f"fabric SKU '{normalized}' not in SKU map",
    )


def resolve_firebolt_fbu_rate(node_type: str) -> PriceResolution:

    table = "firebolt_node_fbu_rates"
    normalized = node_type.strip().lower()
    fbu_rate = FIREBOLT_NODE_FBU_RATES.get(normalized)
    if fbu_rate is not None:
        return PriceResolution(
            value=fbu_rate,
            table=table,
            resolved_key=(normalized,),
            fallback_used=False,
            unit="FBU/hour",
        )
    logger.warning(f"Unknown Firebolt node type '{node_type.strip()}'; defaulting to M (16 FBU/hour)")
    return PriceResolution(
        value=FIREBOLT_NODE_FBU_RATES["m"],
        table=table,
        resolved_key=("m",),
        fallback_used=True,
        unit="FBU/hour",
        reason=f"firebolt node type '{normalized}' not in rate table",
    )


def resolve_firebolt_fbu_price() -> PriceResolution:

    return PriceResolution(value=FIREBOLT_FBU_PRICE, table="firebolt_fbu_price", resolved_key=(), fallback_used=False)


def _map_region_to_tier(region: str) -> str:

    region = region.strip().lower()

    us_regions = {
        "us-east-1",
        "us-east-2",
        "us-west-1",
        "us-west-2",
        "eastus",
        "eastus2",
        "centralus",
        "northcentralus",
        "southcentralus",
        "westus",
        "westus2",
        "westus3",
        "westcentralus",
        "us-central1",
        "us-east1",
        "us-east4",
        "us-west1",
        "us-west2",
        "us-west3",
        "us-west4",
    }
    if region in us_regions or region.startswith("us-"):
        return "us"

    canada_regions = {
        "ca-central-1",
        "canadacentral",
        "canadaeast",
        "northamerica-northeast1",
        "northamerica-northeast2",
    }
    if region in canada_regions or region.startswith("ca-"):
        return "ca"

    eu_regions = {
        "eu-west-1",
        "eu-west-2",
        "eu-west-3",
        "eu-central-1",
        "eu-central-2",
        "eu-north-1",
        "eu-south-1",
        "eu-south-2",
        "northeurope",
        "westeurope",
        "francecentral",
        "francesouth",
        "germanynorth",
        "germanywestcentral",
        "norwayeast",
        "norwaywest",
        "switzerlandnorth",
        "switzerlandwest",
        "uksouth",
        "ukwest",
        "swedencentral",
        "swedensouth",
        "europe-west1",
        "europe-west2",
        "europe-west3",
        "europe-west4",
        "europe-west6",
        "europe-west8",
        "europe-west9",
        "europe-central2",
        "europe-north1",
        "europe-southwest1",
    }
    if region in eu_regions or region.startswith(("eu-", "europe-")):
        return "eu"

    ap_regions = {
        "ap-south-1",
        "ap-south-2",
        "ap-northeast-1",
        "ap-northeast-2",
        "ap-northeast-3",
        "ap-southeast-1",
        "ap-southeast-2",
        "ap-southeast-3",
        "ap-southeast-4",
        "ap-east-1",
        "eastasia",
        "southeastasia",
        "australiaeast",
        "australiacentral",
        "australiasoutheast",
        "japaneast",
        "japanwest",
        "koreacentral",
        "koreasouth",
        "centralindia",
        "southindia",
        "westindia",
        "jioindiawest",
        "jioindiacentral",
        "asia-east1",
        "asia-east2",
        "asia-northeast1",
        "asia-northeast2",
        "asia-northeast3",
        "asia-south1",
        "asia-south2",
        "asia-southeast1",
        "asia-southeast2",
        "australia-southeast1",
        "australia-southeast2",
    }
    if region in ap_regions or region.startswith(("ap-", "asia-", "australia")):
        return "ap"

    middle_east_regions = {
        "me-south-1",
        "me-central-1",
        "uaenorth",
        "uaecentral",
        "qatarcentral",
        "me-west1",
    }
    if region in middle_east_regions:
        return "other"

    south_america_regions = {
        "sa-east-1",
        "brazilsouth",
        "brazilsoutheast",
        "southamerica-east1",
        "southamerica-west1",
    }
    if region in south_america_regions:
        return "other"

    africa_regions = {
        "af-south-1",
        "southafricanorth",
        "southafricawest",
    }
    if region in africa_regions:
        return "other"

    return "other"


def get_pricing_age_days(table: str | None = None) -> int | None:

    if table is not None:
        retrieved = (PRICE_TABLE_PROVENANCE.get(table) or {}).get("retrieved")
        if not isinstance(retrieved, str) or retrieved == "unknown":
            return None
        try:
            validation_date = datetime.fromisoformat(retrieved)
        except ValueError:
            return None
        return (date.today() - validation_date.date()).days
    if PRICING_VALIDATION_DATE is None:
        return None
    return (date.today() - PRICING_VALIDATION_DATE.date()).days


def is_pricing_stale(threshold_days: int = 90) -> bool:

    age_days = get_pricing_age_days()
    if age_days is None:
        return False
    return age_days > threshold_days
