# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from benchbox.platforms.base.config_utils import make_platform_config_builder
from benchbox.utils.dependencies import check_platform_dependencies, get_dependency_error_message

from .adapter import DatabricksAdapter
from .dataframe_adapter import DATABRICKS_CONNECT_AVAILABLE, DatabricksDataFrameAdapter

__all__ = [
    "DatabricksAdapter",
    "DatabricksDataFrameAdapter",
    "DATABRICKS_CONNECT_AVAILABLE",
    "check_platform_dependencies",
    "get_dependency_error_message",
]


_build_databricks_config = make_platform_config_builder(
    "databricks",
    __name__,
    "Databricks",
    "databricks-sql-connector",
    [
        "server_hostname",
        "http_path",
        "compute_resource",
        "warehouse_id",
        "access_token",
        "catalog",
        "schema",
        "uc_catalog",
        "uc_schema",
        "uc_volume",
        "staging_root",
        "region",
        "cloud_region",
        "workspace_region",
        "cluster_size",
        "auto_terminate_minutes",
        "enable_delta_optimization",
        "delta_auto_optimize",
        "delta_auto_compact",
        "table_format",
        "hudi_primary_key",
        "hudi_precombine_field",
        "hudi_table_type",
        "create_catalog",
        "disable_result_cache",
    ],
    linked_resource_keys=("http_path",),
)
