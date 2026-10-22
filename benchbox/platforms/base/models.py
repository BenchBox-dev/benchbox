from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from benchbox.core.results.models import (
    DataGenerationPhase,
    DataLoadingPhase,
    MaintenanceOperation,
    MaintenanceTestPhase,
    PowerTestPhase,
    QueryExecution,
    SchemaCreationPhase,
    SetupPhase,
    StatisticsGatheringPhase,
    TableCreationStats,
    TableGenerationStats,
    TableLoadingStats,
    ThroughputStream,
    ThroughputTestPhase,
    ValidationPhase,
)

try:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration
except ImportError:  # pragma: no cover
    UnifiedTuningConfiguration = None


@dataclass
class ConnectionConfig:
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None
    connection_string: str | None = None

    auth_method: str = "password"
    token: str | None = None
    service_account_path: str | None = None

    ssl_enabled: bool = False
    ssl_cert_path: str | None = None
    ssl_key_path: str | None = None
    ssl_ca_path: str | None = None

    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30

    extra_params: dict[str, Any] = field(default_factory=dict)

    tuning_enabled: bool = False
    unified_tuning_configuration: UnifiedTuningConfiguration | None = None

    def get_env_value(self, key: str, default: Any = None) -> Any:
        value = getattr(self, key, default)
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            import os

            env_var = value[2:-1]
            return os.getenv(env_var, default)
        return value


@dataclass
class DatabaseValidationResult:
    is_valid: bool
    can_reuse: bool
    issues: list[str]
    warnings: list[str]
    tuning_valid: bool | None = None
    tables_valid: bool | None = None
    row_counts_valid: bool | None = None
    database_empty: bool = False
    foreign_tables: tuple[str, ...] = ()


__all__ = [
    "ConnectionConfig",
    "TableGenerationStats",
    "DataGenerationPhase",
    "TableCreationStats",
    "SchemaCreationPhase",
    "TableLoadingStats",
    "DataLoadingPhase",
    "ValidationPhase",
    "SetupPhase",
    "StatisticsGatheringPhase",
    "QueryExecution",
    "PowerTestPhase",
    "ThroughputStream",
    "ThroughputTestPhase",
    "MaintenanceOperation",
    "MaintenanceTestPhase",
    "DatabaseValidationResult",
]
