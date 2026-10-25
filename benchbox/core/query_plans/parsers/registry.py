from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from packaging import version as pkg_version

if TYPE_CHECKING:
    from benchbox.core.query_plans.parsers.base import QueryPlanParser

logger = logging.getLogger(__name__)


class ParserRegistry:
    def __init__(self):
        self._parsers: dict[str, list[tuple[str, type[QueryPlanParser]]]] = {}

    def register(
        self,
        platform: str,
        min_version: str,
        parser_class: type[QueryPlanParser],
    ) -> None:
        platform_lower = platform.lower()
        if platform_lower not in self._parsers:
            self._parsers[platform_lower] = []

        self._parsers[platform_lower].append((min_version, parser_class))
        logger.debug(
            "Registered parser %s for %s >= %s",
            parser_class.__name__,
            platform,
            min_version,
        )

    def get_parser(
        self,
        platform: str,
        platform_version: str | None = None,
    ) -> QueryPlanParser | None:
        platform_lower = platform.lower()
        if platform_lower not in self._parsers:
            logger.debug("No parsers registered for platform: %s", platform)
            return None

        candidates = self._parsers[platform_lower]
        if not candidates:
            return None

        if platform_version is None:
            candidates_sorted = sorted(
                candidates,
                key=lambda x: pkg_version.parse(x[0]),
                reverse=True,
            )
            return candidates_sorted[0][1]()

        try:
            target_version = pkg_version.parse(platform_version)
        except Exception as e:
            logger.warning(
                "Could not parse version '%s': %s, using latest parser",
                platform_version,
                e,
            )
            candidates_sorted = sorted(
                candidates,
                key=lambda x: pkg_version.parse(x[0]),
                reverse=True,
            )
            return candidates_sorted[0][1]()

        suitable = []
        for min_ver, parser_class in candidates:
            try:
                if target_version >= pkg_version.parse(min_ver):
                    suitable.append((min_ver, parser_class))
            except Exception:
                continue

        if not suitable:
            logger.warning(
                "No parser available for %s version %s",
                platform,
                platform_version,
            )
            return None

        suitable.sort(key=lambda x: pkg_version.parse(x[0]), reverse=True)
        parser_class = suitable[0][1]
        logger.debug(
            "Selected parser %s for %s version %s",
            parser_class.__name__,
            platform,
            platform_version,
        )
        return parser_class()

    def get_all_platforms(self) -> list[str]:
        return list(self._parsers.keys())

    def get_parser_versions(self, platform: str) -> list[tuple[str, str]]:
        platform_lower = platform.lower()
        if platform_lower not in self._parsers:
            return []
        return [(min_ver, cls.__name__) for min_ver, cls in self._parsers[platform_lower]]

    def clear(self) -> None:
        self._parsers.clear()


_global_registry: ParserRegistry | None = None


def get_parser_registry() -> ParserRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = _create_default_registry()
    return _global_registry


def _create_default_registry() -> ParserRegistry:
    from benchbox.core.query_plans.parsers.azure_synapse import AzureSynapseQueryPlanParser
    from benchbox.core.query_plans.parsers.bigquery import BigQueryQueryPlanParser
    from benchbox.core.query_plans.parsers.clickhouse import ClickHouseQueryPlanParser
    from benchbox.core.query_plans.parsers.databend import DatabendQueryPlanParser
    from benchbox.core.query_plans.parsers.datafusion import DataFusionQueryPlanParser
    from benchbox.core.query_plans.parsers.doris import DorisQueryPlanParser
    from benchbox.core.query_plans.parsers.duckdb import DuckDBQueryPlanParser
    from benchbox.core.query_plans.parsers.fabric_warehouse import FabricWarehouseQueryPlanParser
    from benchbox.core.query_plans.parsers.firebolt import FireboltQueryPlanParser
    from benchbox.core.query_plans.parsers.postgresql import PostgreSQLQueryPlanParser
    from benchbox.core.query_plans.parsers.presto_trino import PrestoTrinoQueryPlanParser
    from benchbox.core.query_plans.parsers.questdb import QuestDBQueryPlanParser
    from benchbox.core.query_plans.parsers.redshift import RedshiftQueryPlanParser
    from benchbox.core.query_plans.parsers.singlestore import SingleStoreQueryPlanParser
    from benchbox.core.query_plans.parsers.snowflake import SnowflakeQueryPlanParser
    from benchbox.core.query_plans.parsers.spark import SparkQueryPlanParser
    from benchbox.core.query_plans.parsers.sqlite import SQLiteQueryPlanParser

    registry = ParserRegistry()

    registry.register("duckdb", "0.0.0", DuckDBQueryPlanParser)

    registry.register("motherduck", "0.0.0", DuckDBQueryPlanParser)

    registry.register("postgresql", "0.0.0", PostgreSQLQueryPlanParser)
    registry.register("postgres", "0.0.0", PostgreSQLQueryPlanParser)

    registry.register("redshift", "0.0.0", RedshiftQueryPlanParser)

    registry.register("datafusion", "0.0.0", DataFusionQueryPlanParser)

    registry.register("sqlite", "0.0.0", SQLiteQueryPlanParser)

    for _ch_key in ("clickhouse", "clickhouse-local", "clickhouse-server", "clickhouse-cloud"):
        registry.register(_ch_key, "0.0.0", ClickHouseQueryPlanParser)

    registry.register("presto", "0.0.0", PrestoTrinoQueryPlanParser)
    registry.register("trino", "0.0.0", PrestoTrinoQueryPlanParser)
    registry.register("starburst", "0.0.0", PrestoTrinoQueryPlanParser)
    registry.register("athena", "0.0.0", PrestoTrinoQueryPlanParser)

    registry.register("spark", "0.0.0", SparkQueryPlanParser)
    registry.register("databricks", "0.0.0", SparkQueryPlanParser)
    registry.register("velox", "0.0.0", SparkQueryPlanParser)

    registry.register("databend", "0.0.0", DatabendQueryPlanParser)

    registry.register("questdb", "0.0.0", QuestDBQueryPlanParser)

    registry.register("doris", "0.0.0", DorisQueryPlanParser)
    registry.register("singlestore", "0.0.0", SingleStoreQueryPlanParser)

    registry.register("snowflake", "0.0.0", SnowflakeQueryPlanParser)
    registry.register("firebolt", "0.0.0", FireboltQueryPlanParser)
    registry.register("azure_synapse", "0.0.0", AzureSynapseQueryPlanParser)
    registry.register("fabric_warehouse", "0.0.0", FabricWarehouseQueryPlanParser)
    registry.register("bigquery", "0.0.0", BigQueryQueryPlanParser)
    registry.register("lakesail", "0.0.0", SparkQueryPlanParser)

    return registry


def get_parser_for_platform(
    platform: str,
    platform_version: str | None = None,
) -> QueryPlanParser | None:
    registry = get_parser_registry()
    return registry.get_parser(platform, platform_version)


def reset_global_registry() -> None:
    global _global_registry
    _global_registry = None
