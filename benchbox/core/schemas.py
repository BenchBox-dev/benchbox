from collections.abc import Iterable
from datetime import datetime
from typing import Any, ClassVar, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from benchbox.core.constants import (
    GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS,
    GENERIC_POWER_DEFAULT_WARMUP_ITERATIONS,
)
from benchbox.utils.verbosity import VerbositySettings

MIN_THROUGHPUT_STREAMS = 2
ALL_COMBINED_PHASES = ("power", "throughput", "maintenance")


def reject_single_stream_throughput(streams: Optional[int], phases: Iterable[str] | None) -> None:
    if streams is None or streams >= MIN_THROUGHPUT_STREAMS:
        return
    if "throughput" not in {str(phase).strip().lower() for phase in phases or ()}:
        return
    raise ValueError(
        f"Throughput requires at least {MIN_THROUGHPUT_STREAMS} concurrent streams (TPC minimum); got {streams}. "
        "Omit the stream count to use the default of 2."
    )


def _throughput_phases(test_execution_type: str, options: dict[str, Any]) -> tuple[str, ...]:
    if test_execution_type == "throughput":
        return ("throughput",)
    if test_execution_type == "combined":
        return tuple(options.get("requested_phases") or ALL_COMBINED_PHASES)
    return ()


class QueryResult(BaseModel):
    query_id: str
    query_name: str
    sql_text: str
    execution_time_seconds: float | None = None
    execution_time_ms: float | None = None
    rows_returned: int
    status: str
    error_message: Optional[str] = None
    resource_usage: Optional[dict[str, Any]] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"SUCCESS", "ERROR", "TIMEOUT"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}, got: {v}")
        return v

    @field_validator("execution_time_seconds", "execution_time_ms")
    @classmethod
    def validate_execution_time(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if v < 0:
            raise ValueError(f"execution time must be non-negative, got: {v}")
        return float(v)

    @model_validator(mode="after")
    def normalize_execution_time_fields(self) -> "QueryResult":
        if self.execution_time_seconds is None and self.execution_time_ms is None:
            raise ValueError("either execution_time_seconds or execution_time_ms must be provided")
        if self.execution_time_seconds is None and self.execution_time_ms is not None:
            self.execution_time_seconds = float(self.execution_time_ms) / 1000.0
        if self.execution_time_ms is None and self.execution_time_seconds is not None:
            self.execution_time_ms = float(self.execution_time_seconds) * 1000.0
        return self

    @field_validator("rows_returned")
    @classmethod
    def validate_rows_returned(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"rows_returned must be non-negative, got: {v}")
        return v


class RunConfig(BaseModel):
    benchmark: Optional[str] = None
    database_type: Optional[str] = None
    query_subset: Optional[list[str]] = None
    concurrent_streams: Optional[int] = None
    test_execution_type: str = "standard"
    scale_factor: float = 0.01
    seed: Optional[int] = None
    tuning_config: Optional[dict[str, Any]] = None
    platform_options: Optional[dict[str, Any]] = None
    platform_option_sources: Optional[dict[str, str]] = None
    connection: Optional[dict[str, Any]] = None
    options: dict[str, Any] = Field(default_factory=dict)
    enable_postload_validation: bool = False
    capture_plans: bool = False
    show_query_plans: bool | None = None
    analyze_plans: bool | None = None
    strict_plan_capture: bool = False
    normalize_plan_literals: bool = False
    gather_statistics: bool = False
    stats_reset: Optional[bool] = None
    stats_per_table_timing: bool = False
    driver_package: Optional[str] = None
    driver_version: Optional[str] = None
    driver_version_resolved: Optional[str] = None
    driver_version_actual: Optional[str] = None
    driver_runtime_strategy: Optional[str] = None
    driver_runtime_path: Optional[str] = None
    driver_runtime_python_executable: Optional[str] = None
    driver_auto_install: bool = False
    driver_auto_install_used: bool = False
    verbose: bool = False
    verbose_level: int = 0
    verbose_enabled: bool = False
    very_verbose: bool = False
    quiet: bool = False
    iterations: int = GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS
    warm_up_iterations: int = GENERIC_POWER_DEFAULT_WARMUP_ITERATIONS
    power_fail_fast: bool = False
    stream_timeout_seconds: Optional[int] = None
    stream_timeout_source: Optional[str] = None
    cancel_on_timeout: bool = False

    client_region: str | None = None
    client_cloud: str | None = None
    link_probe: bool = True

    table_format: Optional[str] = None
    table_format_compression: str = "snappy"
    table_format_partition_cols: list[str] = Field(default_factory=list)

    @field_validator("scale_factor")
    @classmethod
    def validate_scale_factor(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"scale_factor must be positive, got: {v}")
        if v > 100000:
            raise ValueError(f"scale_factor must be ≤ 100000, got: {v}")
        return v

    @field_validator("iterations")
    @classmethod
    def validate_iterations(cls, v: int) -> int:
        if v < 1:
            return 1
        return v

    @field_validator("warm_up_iterations")
    @classmethod
    def validate_warm_up_iterations(cls, v: int) -> int:
        if v < 0:
            return 0
        return v

    @field_validator("concurrent_streams")
    @classmethod
    def validate_concurrent_streams(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError(f"concurrent_streams must be at least 1, got: {v}")
        return v

    @model_validator(mode="after")
    def reject_single_stream_throughput_request(self) -> "RunConfig":
        reject_single_stream_throughput(
            self.concurrent_streams, _throughput_phases(self.test_execution_type, self.options)
        )
        return self

    @field_validator("test_execution_type")
    @classmethod
    def validate_test_execution_type(cls, v: str) -> str:
        allowed = {"standard", "power", "throughput", "maintenance", "combined", "data_only", "load_only"}
        if v not in allowed:
            raise ValueError(f"test_execution_type must be one of {allowed}, got: {v}")
        return v

    @field_validator("table_format")
    @classmethod
    def validate_table_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            allowed = {"parquet", "vortex", "delta", "iceberg"}
            v_lower = v.lower()
            if v_lower not in allowed:
                raise ValueError(f"table_format must be one of {allowed}, got: {v}")
            return v_lower
        return v

    _DRIVER_OPTION_FIELDS: ClassVar[list[tuple[str, str, type]]] = [
        ("driver_version", "driver_version", str),
        ("driver_package", "driver_package", str),
        ("driver_version_resolved", "driver_version_resolved", str),
        ("driver_version_actual", "driver_version_actual", str),
        ("driver_runtime_strategy", "driver_runtime_strategy", str),
        ("driver_runtime_path", "driver_runtime_path", str),
        ("driver_runtime_python_executable", "driver_runtime_python_executable", str),
    ]

    @model_validator(mode="after")
    def populate_driver_metadata(self) -> "RunConfig":
        for attr, option_key, expected_type in self._DRIVER_OPTION_FIELDS:
            if getattr(self, attr) is None:
                value = self.options.get(option_key)
                if isinstance(value, expected_type):
                    setattr(self, attr, value)

        if not self.driver_auto_install:
            auto_install_option = self.options.get("driver_auto_install")
            if isinstance(auto_install_option, bool):
                self.driver_auto_install = auto_install_option

        verbosity_source: dict[str, Any] = {
            "verbose": self.verbose,
            "verbose_level": self.verbose_level,
            "verbose_enabled": self.verbose_enabled,
            "very_verbose": self.very_verbose,
            "quiet": self.quiet,
        }
        verbosity = VerbositySettings.from_mapping(verbosity_source)
        self.verbose = verbosity.verbose
        self.verbose_level = verbosity.level
        self.verbose_enabled = verbosity.verbose_enabled
        self.very_verbose = verbosity.very_verbose
        self.quiet = verbosity.quiet

        return self


class BenchmarkConfig(BaseModel):
    name: str
    display_name: str
    scale_factor: float = 0.01
    queries: Optional[list[str]] = None
    concurrency: Optional[int] = None
    capture_plans: bool = False
    analyze_plans: bool | None = None
    strict_plan_capture: bool = False
    stats_reset: Optional[bool] = None
    stats_per_table_timing: bool = False
    options: dict[str, Any] = Field(default_factory=dict)
    compress_data: bool = False
    compression_type: str = "zstd"
    compression_level: Optional[int] = None
    test_execution_type: str = "standard"
    official: bool = False
    client_region: str | None = None
    client_cloud: str | None = None
    link_probe: bool = True

    @field_validator("scale_factor")
    @classmethod
    def validate_scale_factor(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"scale_factor must be positive, got: {v}")
        if v > 100000:
            raise ValueError(f"scale_factor must be ≤ 100000, got: {v}")
        return v

    @field_validator("concurrency")
    @classmethod
    def validate_concurrency(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError(f"concurrency must be at least 1, got: {v}")
        return v

    @model_validator(mode="after")
    def reject_single_stream_throughput_request(self) -> "BenchmarkConfig":
        reject_single_stream_throughput(self.concurrency, _throughput_phases(self.test_execution_type, self.options))
        return self

    @field_validator("compression_type")
    @classmethod
    def validate_compression_type(cls, v: str) -> str:
        allowed = {"zstd", "gzip", "lz4", "snappy", "none"}
        if v not in allowed:
            raise ValueError(f"compression_type must be one of {allowed}, got: {v}")
        return v

    @field_validator("compression_level")
    @classmethod
    def validate_compression_level(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 1 or v > 22):
            raise ValueError(f"compression_level must be between 1 and 22, got: {v}")
        return v

    @field_validator("test_execution_type")
    @classmethod
    def validate_test_execution_type(cls, v: str) -> str:
        allowed = {"standard", "power", "throughput", "maintenance", "combined", "data_only", "load_only"}
        if v not in allowed:
            raise ValueError(f"test_execution_type must be one of {allowed}, got: {v}")
        return v


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    name: str
    connection_string: Optional[str] = None
    options: dict[str, Any] = Field(default_factory=dict)
    driver_package: Optional[str] = None
    driver_version: Optional[str] = None
    driver_version_resolved: Optional[str] = None
    driver_version_actual: Optional[str] = None
    driver_runtime_strategy: Optional[str] = None
    driver_runtime_path: Optional[str] = None
    driver_runtime_python_executable: Optional[str] = None
    driver_auto_install: bool = False
    driver_auto_install_used: bool = False
    execution_mode: Optional[Literal["sql", "dataframe", "data_only"]] = None
    execution_engine: str = "default"
    gateway: str | None = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("database type cannot be empty")
        return v.strip()

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("database name cannot be empty")
        return v.strip()

    @model_validator(mode="after")
    def resolve_platform_selector(self) -> "DatabaseConfig":
        if ":" not in self.type:
            return self
        from benchbox.core.deployment import normalize_config_platform

        self.type, self.options, self.execution_mode = normalize_config_platform(
            self.type, self.options, self.execution_mode
        )
        return self


class SystemProfile(BaseModel):
    os_name: str
    os_version: str
    architecture: str
    cpu_model: Optional[str] = None
    cpu_identity_provenance: Optional[Literal["measured", "user_attested", "inferred"]] = None
    cpu_cores_physical: int
    cpu_cores_logical: int
    memory_total_gb: float
    memory_available_gb: float
    python_version: str
    disk_space_gb: float
    timestamp: datetime
    hostname: Optional[str] = None

    @field_validator("cpu_cores_physical", "cpu_cores_logical")
    @classmethod
    def validate_cpu_cores(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"CPU cores must be at least 1, got: {v}")
        return v

    @field_validator("memory_total_gb", "memory_available_gb", "disk_space_gb")
    @classmethod
    def validate_sizes(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"Size values must be non-negative, got: {v}")
        return v


class LibraryInfo(BaseModel):
    name: str
    version: Optional[str]
    installed: bool
    import_error: Optional[str] = None


class PlatformInfo(BaseModel):
    name: str
    display_name: str
    description: str
    libraries: list[LibraryInfo]
    available: bool
    enabled: bool
    requirements: list[str]
    installation_command: str
    adoption: str = "niche"
    support_status: Optional[str] = None
    category: str = "database"
    supports: list[str] = Field(default_factory=list)
    driver_package: Optional[str] = None
    driver_version_requested: Optional[str] = None
    driver_version_resolved: Optional[str] = None
    driver_version_actual: Optional[str] = None
    driver_runtime_strategy: Optional[str] = None


class ExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_point: Literal["cli", "mcp", "python_api"] = "python_api"
    invocation_timestamp: datetime = Field(default_factory=datetime.now)

    phases: list[str] = Field(default_factory=lambda: ["power"])

    seed: Optional[int] = None

    compression_enabled: bool = False
    compression_type: str = "none"
    compression_level: Optional[int] = None

    mode: Literal["sql", "dataframe"] = "sql"

    official: bool = False

    validation_mode: Optional[str] = None

    force_datagen: bool = False
    force_upload: bool = False

    query_subset: Optional[list[str]] = None

    capture_plans: bool = False
    strict_plan_capture: bool = False

    non_interactive: bool = False

    tuning_mode: Optional[str] = None

    def to_cli_args(self) -> list[str]:
        args: list[str] = []

        if self.phases != ["power"]:
            args.extend(["--phases", ",".join(self.phases)])
        if self.seed is not None:
            args.extend(["--seed", str(self.seed)])

        if self.compression_enabled and self.compression_type != "none":
            comp = self.compression_type
            if self.compression_level is not None:
                comp = f"{comp}:{self.compression_level}"
            args.extend(["--compression", comp])

        _VALUE_FLAGS: list[tuple[str, str, Any]] = [
            ("mode", "--mode", "sql"),
            ("validation_mode", "--validation", None),
        ]
        for attr, flag, default in _VALUE_FLAGS:
            value = getattr(self, attr)
            if value and value != default:
                args.extend([flag, value])

        _BOOL_FLAGS = [
            ("official", "--official"),
            ("capture_plans", "--capture-plans"),
            ("non_interactive", "--non-interactive"),
        ]
        for attr, flag in _BOOL_FLAGS:
            if getattr(self, attr):
                args.append(flag)

        force_parts = [
            f for f, attr in [("datagen", "force_datagen"), ("upload", "force_upload")] if getattr(self, attr)
        ]
        if force_parts:
            args.extend(["--force", ",".join(force_parts)])

        if self.query_subset:
            args.extend(["--queries", ",".join(self.query_subset)])
        if self.strict_plan_capture:
            args.extend(["--plan-config", "strict:true"])
        if self.tuning_mode:
            args.extend(["--tuning", self.tuning_mode])

        return args

    def to_cli_string(self, platform: str, benchmark: str, scale: float) -> str:
        base = f"benchbox run --platform {platform} --benchmark {benchmark} --scale {scale}"
        args = self.to_cli_args()
        return f"{base} {' '.join(args)}" if args else base


class DryRunResult(BaseModel):
    benchmark_config: dict[str, Any]
    database_config: dict[str, Any]
    system_profile: dict[str, Any]
    platform_config: dict[str, Any]
    queries: dict[str, str]
    execution_mode: str = "sql"
    deployment: Optional[str] = None
    schema_sql: Optional[str] = None
    dataframe_schema: Optional[str] = None
    ddl_preview: Optional[dict[str, dict[str, Any]]] = None
    post_load_statements: Optional[dict[str, list[str]]] = None
    ddl_preview_notes: list[str] = Field(default_factory=list)
    tuning_config: Optional[dict[str, Any]] = None
    constraint_config: Optional[dict[str, Any]] = None
    estimated_resources: Optional[dict[str, Any]] = None
    query_preview: Optional[dict[str, Any]] = None
    warnings: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.now)


__all__ = [
    "QueryResult",
    "RunConfig",
    "BenchmarkConfig",
    "DatabaseConfig",
    "SystemProfile",
    "LibraryInfo",
    "PlatformInfo",
    "ExecutionContext",
    "DryRunResult",
]
