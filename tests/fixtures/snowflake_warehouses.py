from __future__ import annotations

from typing import Any

SHOW_WAREHOUSES_COLUMNS: tuple[str, ...] = (
    "name",
    "state",
    "type",
    "size",
    "min_cluster_count",
    "max_cluster_count",
    "started_clusters",
    "running",
    "queued",
    "is_default",
    "is_current",
    "auto_suspend",
    "auto_resume",
    "available",
    "provisioning",
    "quiescing",
    "other",
    "created_on",
    "resumed_on",
    "updated_on",
    "owner",
    "comment",
    "enable_query_acceleration",
    "query_acceleration_max_scale_factor",
    "resource_monitor",
    "actives",
    "pendings",
    "failed",
    "suspended",
    "uuid",
    "scaling_policy",
)


def warehouse_description(columns: tuple[str, ...] = SHOW_WAREHOUSES_COLUMNS) -> list[tuple[Any, ...]]:
    return [(name, 0, None, None, None, None, True) for name in columns]


def warehouse_row(columns: tuple[str, ...] = SHOW_WAREHOUSES_COLUMNS, **values: Any) -> tuple[Any, ...]:
    defaults: dict[str, Any] = {"name": "MY_WH", "state": "STARTED", "type": "STANDARD", "size": "LARGE"}
    defaults.update(values)
    return tuple(defaults.get(name) for name in columns)
