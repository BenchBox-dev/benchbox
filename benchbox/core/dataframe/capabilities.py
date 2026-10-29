# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class DataFormat(Enum):
    CSV = "csv"
    PARQUET = "parquet"
    ARROW = "arrow"


class ExecutionModel(Enum):
    EAGER = "eager"
    LAZY = "lazy"
    DISTRIBUTED = "distributed"
    OUT_OF_CORE = "out_of_core"


@dataclass
class PlatformCapabilities:
    platform_name: str
    max_recommended_sf: float
    memory_overhead_factor: float
    execution_model: ExecutionModel
    requires_partitioning: bool = False
    supports_streaming: bool = False
    gpu_required: bool = False
    recommended_data_format: DataFormat = DataFormat.PARQUET
    min_memory_gb: float = 2.0
    description: str = ""
    notes: list[str] = field(default_factory=list)

    def estimate_memory_for_sf(self, scale_factor: float) -> float:
        raw_data_gb = scale_factor
        return raw_data_gb * self.memory_overhead_factor

    def can_handle_sf(self, scale_factor: float) -> bool:
        return scale_factor <= self.max_recommended_sf


PLATFORM_CAPABILITIES: dict[str, PlatformCapabilities] = {
    "polars": PlatformCapabilities(
        platform_name="Polars",
        max_recommended_sf=100.0,
        memory_overhead_factor=2.0,
        execution_model=ExecutionModel.LAZY,
        supports_streaming=True,
        recommended_data_format=DataFormat.PARQUET,
        description="Fast Rust-based DataFrame library with lazy evaluation",
        notes=[
            "Streaming mode can handle data larger than memory",
            "LazyFrame optimizes query execution",
            "Excellent for single-machine workloads",
        ],
    ),
    "pandas": PlatformCapabilities(
        platform_name="Pandas",
        max_recommended_sf=10.0,
        memory_overhead_factor=2.5,
        execution_model=ExecutionModel.EAGER,
        recommended_data_format=DataFormat.CSV,
        min_memory_gb=4.0,
        description="Reference Python DataFrame library",
        notes=[
            "Loads entire dataset into memory",
            "Use smaller scale factors or switch to Dask for larger data",
            "Memory overhead can be 2-3x raw data size",
        ],
    ),
    "cudf": PlatformCapabilities(
        platform_name="cuDF",
        max_recommended_sf=1.0,
        memory_overhead_factor=1.8,
        execution_model=ExecutionModel.EAGER,
        gpu_required=True,
        recommended_data_format=DataFormat.PARQUET,
        min_memory_gb=8.0,
        description="NVIDIA RAPIDS GPU DataFrame library",
        notes=[
            "Limited by GPU VRAM (typically 8-24GB)",
            "Extremely fast for data that fits in GPU memory",
            "Requires NVIDIA GPU with CUDA support",
        ],
    ),
    "dask": PlatformCapabilities(
        platform_name="Dask",
        max_recommended_sf=1000.0,
        memory_overhead_factor=1.5,
        execution_model=ExecutionModel.OUT_OF_CORE,
        requires_partitioning=True,
        recommended_data_format=DataFormat.PARQUET,
        description="Parallel computing library for out-of-core processing",
        notes=[
            "Can process datasets larger than memory",
            "Requires proper partitioning for efficiency",
            "Supports distributed clusters",
        ],
    ),
    "vaex": PlatformCapabilities(
        platform_name="Vaex",
        max_recommended_sf=100.0,
        memory_overhead_factor=1.2,
        execution_model=ExecutionModel.OUT_OF_CORE,
        recommended_data_format=DataFormat.PARQUET,
        description="Memory-mapped DataFrame library for large datasets",
        notes=[
            "Uses memory mapping for efficient large dataset handling",
            "Lazy evaluation with expression system",
            "Good for exploratory analysis of large files",
        ],
    ),
    "pyspark": PlatformCapabilities(
        platform_name="PySpark",
        max_recommended_sf=10000.0,
        memory_overhead_factor=3.0,
        execution_model=ExecutionModel.DISTRIBUTED,
        requires_partitioning=True,
        recommended_data_format=DataFormat.PARQUET,
        min_memory_gb=8.0,
        description="Apache Spark Python API for distributed computing",
        notes=[
            "Designed for cluster-scale data processing",
            "Higher overhead for small datasets",
            "Best for very large scale factors (100+)",
        ],
    ),
    "lakesail": PlatformCapabilities(
        platform_name="LakeSail",
        max_recommended_sf=10000.0,
        memory_overhead_factor=3.0,
        execution_model=ExecutionModel.DISTRIBUTED,
        requires_partitioning=True,
        recommended_data_format=DataFormat.PARQUET,
        min_memory_gb=8.0,
        description="LakeSail Sail engine driven through the PySpark Spark Connect client",
        notes=[
            "Executes on a Sail server reached over Spark Connect",
            "Client-side memory is small; server memory governs capacity",
        ],
    ),
    "datafusion": PlatformCapabilities(
        platform_name="DataFusion",
        max_recommended_sf=100.0,
        memory_overhead_factor=1.8,
        execution_model=ExecutionModel.LAZY,
        supports_streaming=True,
        recommended_data_format=DataFormat.PARQUET,
        description="Apache Arrow DataFusion query engine",
        notes=[
            "SQL and DataFrame APIs available",
            "Built on Apache Arrow for efficient memory",
            "Good balance of performance and memory efficiency",
        ],
    ),
}


def get_platform_capabilities(platform: str) -> PlatformCapabilities:
    platform_lower = platform.lower()

    if platform_lower.endswith("-df"):
        platform_lower = platform_lower[:-3]

    if platform_lower not in PLATFORM_CAPABILITIES:
        available = ", ".join(sorted(PLATFORM_CAPABILITIES.keys()))
        raise ValueError(f"Unknown DataFrame platform: {platform}. Available: {available}")

    return PLATFORM_CAPABILITIES[platform_lower]


def list_platform_capabilities() -> dict[str, PlatformCapabilities]:
    return PLATFORM_CAPABILITIES.copy()


@dataclass
class MemoryEstimate:
    raw_data_gb: float
    estimated_memory_gb: float
    platform: str
    scale_factor: float
    benchmark: str


def estimate_memory_required(
    benchmark: str,
    scale_factor: float,
    platform: str,
) -> MemoryEstimate:
    caps = get_platform_capabilities(platform)

    if benchmark.lower() == "tpch" or benchmark.lower() == "tpcds":
        raw_data_gb = scale_factor
    else:
        raw_data_gb = scale_factor

    estimated_gb = raw_data_gb * caps.memory_overhead_factor

    return MemoryEstimate(
        raw_data_gb=raw_data_gb,
        estimated_memory_gb=estimated_gb,
        platform=caps.platform_name,
        scale_factor=scale_factor,
        benchmark=benchmark,
    )


def get_available_memory_gb() -> float:
    try:
        import psutil

        return psutil.virtual_memory().available / (1024**3)
    except ImportError:
        logger.debug("psutil not available, cannot determine available memory")
        return 0.0


def get_total_memory_gb() -> float:
    try:
        import psutil

        return psutil.virtual_memory().total / (1024**3)
    except ImportError:
        logger.debug("psutil not available, cannot determine total memory")
        return 0.0


def get_gpu_memory_gb() -> float | None:
    try:
        import pynvml

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        pynvml.nvmlShutdown()
        return info.free / (1024**3)
    except Exception:
        return None


@dataclass
class MemoryCheckResult:
    is_safe: bool
    message: str
    estimated_memory_gb: float
    available_memory_gb: float
    scale_factor: float
    platform: str
    suggestions: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.is_safe


def check_sufficient_memory(
    benchmark: str,
    scale_factor: float,
    platform: str,
    *,
    safety_margin: float = 0.2,
) -> MemoryCheckResult:
    caps = get_platform_capabilities(platform)
    estimate = estimate_memory_required(benchmark, scale_factor, platform)

    if caps.gpu_required:
        gpu_mem = get_gpu_memory_gb()
        if gpu_mem is None:
            return MemoryCheckResult(
                is_safe=False,
                message=f"{caps.platform_name} requires a GPU but none was detected.",
                estimated_memory_gb=estimate.estimated_memory_gb,
                available_memory_gb=0,
                scale_factor=scale_factor,
                platform=platform,
                suggestions=[
                    "Ensure NVIDIA GPU is available with CUDA support",
                    "Try a CPU-based platform like Polars or Pandas",
                ],
            )
        available = gpu_mem
        memory_type = "GPU"
    else:
        available = get_available_memory_gb()
        memory_type = "system"

    if available == 0:
        return MemoryCheckResult(
            is_safe=True,
            message=f"Could not determine available {memory_type} memory. Proceeding with caution.",
            estimated_memory_gb=estimate.estimated_memory_gb,
            available_memory_gb=0,
            scale_factor=scale_factor,
            platform=platform,
            suggestions=["Install psutil for memory checks: uv add psutil"],
        )

    required_with_margin = estimate.estimated_memory_gb * (1 + safety_margin)

    if available >= required_with_margin:
        return MemoryCheckResult(
            is_safe=True,
            message=(
                f"Memory check passed: {available:.1f} GB available, "
                f"~{estimate.estimated_memory_gb:.1f} GB required for SF {scale_factor}."
            ),
            estimated_memory_gb=estimate.estimated_memory_gb,
            available_memory_gb=available,
            scale_factor=scale_factor,
            platform=platform,
        )

    suggestions = []

    if scale_factor > caps.max_recommended_sf:
        suggestions.append(
            f"Scale factor {scale_factor} exceeds recommended limit for {caps.platform_name} "
            f"(max: {caps.max_recommended_sf})"
        )

    if scale_factor > 1:
        safe_sf = available / caps.memory_overhead_factor * (1 - safety_margin)
        if safe_sf >= 0.01:
            suggestions.append(f"Try a smaller scale factor (suggested: SF {safe_sf:.2f} or less)")

    alternatives = []
    for name, alt_caps in PLATFORM_CAPABILITIES.items():
        if name != platform.lower() and alt_caps.can_handle_sf(scale_factor):
            if alt_caps.execution_model in (ExecutionModel.OUT_OF_CORE, ExecutionModel.DISTRIBUTED):
                alternatives.append(f"{alt_caps.platform_name} ({alt_caps.execution_model.value})")

    if alternatives:
        suggestions.append(f"Consider platforms that handle larger data: {', '.join(alternatives[:3])}")

    if caps.supports_streaming:
        suggestions.append(f"Enable streaming mode for {caps.platform_name}")

    return MemoryCheckResult(
        is_safe=False,
        message=(
            f"Insufficient memory: ~{estimate.estimated_memory_gb:.1f} GB required, "
            f"but only {available:.1f} GB available.\n"
            f"Platform: {caps.platform_name}, Scale Factor: {scale_factor}"
        ),
        estimated_memory_gb=estimate.estimated_memory_gb,
        available_memory_gb=available,
        scale_factor=scale_factor,
        platform=platform,
        suggestions=suggestions,
    )


def validate_scale_factor(
    scale_factor: float,
    platform: str,
    *,
    strict: bool = False,
) -> tuple[bool, str | None]:
    caps = get_platform_capabilities(platform)

    if scale_factor <= 0:
        return False, "Scale factor must be positive"

    if scale_factor > caps.max_recommended_sf:
        message = (
            f"Scale factor {scale_factor} exceeds recommended limit of {caps.max_recommended_sf} "
            f"for {caps.platform_name}.\n"
            f"Estimated memory: ~{caps.estimate_memory_for_sf(scale_factor):.1f} GB\n"
        )

        if caps.notes:
            message += f"Note: {caps.notes[0]}"

        if strict:
            return False, message
        return True, message

    return True, None


def format_memory_warning(check_result: MemoryCheckResult) -> str:
    lines = [check_result.message, ""]

    if check_result.suggestions:
        lines.append("Suggestions:")
        for suggestion in check_result.suggestions:
            lines.append(f"  • {suggestion}")
        lines.append("")

    lines.append("To proceed anyway (not recommended):")
    lines.append("  benchbox run --ignore-memory-warnings ...")

    return "\n".join(lines)


def recommend_platform_for_sf(scale_factor: float) -> str:
    if scale_factor <= 10:
        return "polars"

    if scale_factor <= 100:
        return "polars"

    if scale_factor <= 1000:
        return "dask"

    return "pyspark"
