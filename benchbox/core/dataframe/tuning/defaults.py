# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from benchbox.core.dataframe.tuning.interface import (
    DataFrameTuningConfiguration,
    TuningMetadata,
)

logger = logging.getLogger(__name__)


@dataclass
class SystemProfile:
    cpu_cores: int
    available_memory_gb: float
    has_gpu: bool = False
    gpu_memory_gb: float = 0.0
    gpu_device_count: int = 0


def detect_system_profile() -> SystemProfile:
    import multiprocessing

    cpu_cores = multiprocessing.cpu_count()

    available_memory_gb = _get_available_memory_gb()

    has_gpu, gpu_memory_gb, gpu_device_count = _detect_gpu()

    return SystemProfile(
        cpu_cores=cpu_cores,
        available_memory_gb=available_memory_gb,
        has_gpu=has_gpu,
        gpu_memory_gb=gpu_memory_gb,
        gpu_device_count=gpu_device_count,
    )


def _get_available_memory_gb() -> float:
    try:
        import psutil

        mem = psutil.virtual_memory()
        return mem.available / (1024**3)
    except ImportError:
        pass

    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb / (1024**2)
    except (OSError, ValueError, IndexError):
        pass

    try:
        import subprocess

        result = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True)
        total_bytes = int(result.stdout.strip())
        return (total_bytes * 0.7) / (1024**3)
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        pass

    logger.warning("Could not detect system memory, assuming 8GB")
    return 8.0


def _detect_gpu() -> tuple[bool, float, int]:
    try:
        import cupy

        device_count = cupy.cuda.runtime.getDeviceCount()
        if device_count > 0:
            mem_info = cupy.cuda.runtime.memGetInfo()
            total_memory = mem_info[1]
            return True, total_memory / (1024**3), device_count
    except (ImportError, Exception):
        pass

    try:
        import pynvml

        pynvml.nvmlInit()
        device_count = pynvml.nvmlDeviceGetCount()
        if device_count > 0:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            pynvml.nvmlShutdown()
            return True, info.total / (1024**3), device_count
        pynvml.nvmlShutdown()
    except (ImportError, Exception):
        pass

    if os.environ.get("CUDA_VISIBLE_DEVICES"):
        logger.info("CUDA_VISIBLE_DEVICES set but cannot query GPU, assuming GPU available")
        return True, 8.0, 1

    return False, 0.0, 0


def get_smart_defaults(
    platform: str,
    system_profile: SystemProfile | None = None,
) -> DataFrameTuningConfiguration:
    if system_profile is None:
        system_profile = detect_system_profile()

    platform_lower = platform.lower()
    if platform_lower.endswith("-df"):
        platform_lower = platform_lower[:-3]

    config = DataFrameTuningConfiguration(
        metadata=TuningMetadata(
            platform=platform_lower,
            description=f"Auto-generated defaults for {platform_lower}",
            generated_by="benchbox-smart-defaults",
        )
    )

    _apply_memory_recommendations(config, system_profile)

    if platform_lower == "datafusion":
        _configure_datafusion(config, system_profile)
    elif platform_lower == "polars":
        _configure_polars(config, system_profile)
    elif platform_lower == "pandas":
        _configure_pandas(config, system_profile)
    elif platform_lower == "dask":
        _configure_dask(config, system_profile)
    elif platform_lower == "cudf":
        _configure_cudf(config, system_profile)

    return config


def _configure_datafusion(config: DataFrameTuningConfiguration, profile: SystemProfile) -> None:
    config.execution.streaming_mode = False
    config.parallelism.thread_count = profile.cpu_cores


def _apply_memory_recommendations(
    config: DataFrameTuningConfiguration,
    profile: SystemProfile,
) -> None:
    available_gb = profile.available_memory_gb

    if available_gb < 4:
        config.execution.streaming_mode = True
        config.memory.chunk_size = 50_000
        logger.info("Low memory detected (<4GB), using 50,000-row chunks")

    elif available_gb < 8:
        config.execution.streaming_mode = True
        config.memory.chunk_size = 100_000

    elif available_gb < 32:
        config.memory.chunk_size = 500_000

    else:
        config.execution.streaming_mode = False


def _configure_polars(config: DataFrameTuningConfiguration, profile: SystemProfile) -> None:
    config.execution.engine_affinity = None
    config.execution.streaming_mode = False

    config.execution.lazy_evaluation = True

    config.memory.rechunk_after_filter = True

    if profile.cpu_cores > 16:
        config.parallelism.thread_count = min(profile.cpu_cores, 32)


def _configure_pandas(config: DataFrameTuningConfiguration, profile: SystemProfile) -> None:
    available_gb = profile.available_memory_gb

    if available_gb >= 8:
        config.data_types.dtype_backend = "pyarrow"
    else:
        config.data_types.dtype_backend = "numpy_nullable"

    if available_gb < 16:
        config.io.memory_map = True

    if available_gb < 8:
        config.data_types.auto_categorize_strings = True
        config.data_types.categorical_threshold = 0.5


def _configure_dask(config: DataFrameTuningConfiguration, profile: SystemProfile) -> None:
    available_gb = profile.available_memory_gb
    cpu_cores = profile.cpu_cores

    workers_by_memory = max(1, int(available_gb / 4))
    workers_by_cpu = max(1, cpu_cores // 4)
    worker_count = min(workers_by_memory, workers_by_cpu, 8)

    config.parallelism.worker_count = worker_count

    if worker_count <= 2:
        config.parallelism.threads_per_worker = 4
    elif worker_count <= 4:
        config.parallelism.threads_per_worker = 2
    else:
        config.parallelism.threads_per_worker = 1

    memory_per_worker = available_gb / worker_count
    config.memory.memory_limit = f"{int(memory_per_worker * 0.8)}GB"

    config.memory.spill_to_disk = True

    config.data_types.dtype_backend = "pyarrow"


def _configure_cudf(config: DataFrameTuningConfiguration, profile: SystemProfile) -> None:
    config.gpu.enabled = True
    config.gpu.device_id = 0

    if profile.has_gpu:
        if profile.gpu_memory_gb < 8:
            config.gpu.spill_to_host = True
            config.gpu.pool_type = "managed"
        else:
            config.gpu.spill_to_host = True
            config.gpu.pool_type = "pool"

        if profile.gpu_device_count > 1:
            logger.info(f"Multiple GPUs detected ({profile.gpu_device_count}), using device 0")
    else:
        logger.warning("cuDF selected but no GPU detected")
        config.gpu.spill_to_host = True


def get_profile_summary(profile: SystemProfile) -> dict:
    return {
        "cpu_cores": profile.cpu_cores,
        "available_memory_gb": round(profile.available_memory_gb, 1),
        "has_gpu": profile.has_gpu,
        "gpu_memory_gb": round(profile.gpu_memory_gb, 1) if profile.has_gpu else None,
        "gpu_device_count": profile.gpu_device_count if profile.has_gpu else None,
        "memory_category": (
            "very_low"
            if profile.available_memory_gb < 4
            else "low"
            if profile.available_memory_gb < 8
            else "medium"
            if profile.available_memory_gb < 32
            else "high"
        ),
    }
