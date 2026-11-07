from __future__ import annotations

import logging

from benchbox.core.deployment import deployment_class, select_adapter_deployment
from benchbox.core.results.fetch_mode import FETCH_MODE_ARROW, FETCH_MODE_ROWS
from benchbox.platforms.base import DriverIsolationCapability, PlatformAdapter
from benchbox.utils.dependencies import check_platform_dependencies, get_dependency_error_message

from .deployment_mode import CLICKHOUSE_DEPLOYMENT_MODE_VALUES, resolve_clickhouse_deployment_mode
from .diagnostics import ClickHouseDiagnosticsMixin
from .metadata import ClickHouseMetadataMixin
from .setup import ClickHouseSetupMixin
from .tuning import ClickHouseTuningMixin
from .workload import ClickHouseWorkloadMixin

logger = logging.getLogger(__name__)


class ClickHouseAdapter(
    ClickHouseMetadataMixin,
    ClickHouseSetupMixin,
    ClickHouseDiagnosticsMixin,
    ClickHouseWorkloadMixin,
    ClickHouseTuningMixin,
    PlatformAdapter,
):
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE

    KNOWN_INCOMPATIBLE_QUERIES = {
        "tpcds": [14, 30, 81],
    }

    @property
    def default_fetch_mode(self) -> str:
        config = self.platform_config
        if config.get("_is_cloud_subclass", False):
            return FETCH_MODE_ROWS
        mode = resolve_clickhouse_deployment_mode(config, allow_cloud=True)
        return FETCH_MODE_ARROW if mode == "local" else FETCH_MODE_ROWS

    def __init__(self, **config):
        super().__init__(**config)

        self._dialect = "clickhouse"

        is_cloud_subclass = config.get("_is_cloud_subclass", False)
        self.deployment_mode = resolve_clickhouse_deployment_mode(config, allow_cloud=is_cloud_subclass)
        if not is_cloud_subclass:
            select_adapter_deployment("clickhouse", config, self.deployment_mode)
        self.deployment_selected = self.deployment_mode
        self.deployment_selected_class = deployment_class("clickhouse", self.deployment_mode)

        valid_modes = set(CLICKHOUSE_DEPLOYMENT_MODE_VALUES)
        if is_cloud_subclass:
            valid_modes.add("cloud")
        if self.deployment_mode not in valid_modes:
            raise ValueError(
                f"Invalid ClickHouse deployment mode '{self.deployment_mode}'. "
                f"Valid modes: {', '.join(sorted(valid_modes))}"
            )

        if self.deployment_mode == "server":
            available, missing = check_platform_dependencies("clickhouse", ["clickhouse-driver"])
            if not available:
                error_msg = get_dependency_error_message("clickhouse", missing)
                raise ImportError(error_msg)
            self._setup_server_mode(config)
        elif self.deployment_mode == "local":
            import importlib.util

            if importlib.util.find_spec("chdb") is None:
                raise ImportError(
                    "ClickHouse local mode requires chDB but it is not installed.\n"
                    "To resolve this issue:\n"
                    "  1. Install chDB: uv add chdb\n"
                    "  2. Or switch to server mode: --platform clickhouse:server\n"
                    "  3. Or use ClickHouse Cloud: --platform clickhouse-cloud\n"
                    "  4. Or use a different platform (e.g., DuckDB)\n"
                    "\nFor more information about chDB, visit: https://github.com/chdb-io/chdb"
                )
            self._setup_local_mode(config)
        elif self.deployment_mode == "cloud":
            import importlib.util

            if importlib.util.find_spec("clickhouse_connect") is None:
                raise ImportError(
                    "ClickHouse Cloud requires clickhouse-connect but it is not installed.\n"
                    "To resolve this issue:\n"
                    "  1. Install ClickHouse Cloud extra: uv add benchbox --extra clickhouse-cloud\n"
                    "  2. Or use local mode: --platform clickhouse-local\n"
                    "\nFor more information, visit: https://clickhouse.com/docs/en/integrations/python"
                )
            self._setup_cloud_mode(config)

    def get_query_plan(self, connection, query: str) -> str | None:
        from benchbox.platforms.base.sql_execution import join_explain_rows

        try:
            result = connection.execute(f"EXPLAIN PLAN {query}")
            if not result:
                return None
            return join_explain_rows(list(result))
        except Exception as e:
            self.logger.debug(f"Could not get ClickHouse query plan: {e}")
            return None

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.clickhouse import ClickHouseQueryPlanParser

        return ClickHouseQueryPlanParser()

    def get_tuning_introspector(self):
        from benchbox.platforms.base import tuning_trust

        return tuning_trust.clickhouse_tuning_introspector()


__all__ = ["ClickHouseAdapter"]
