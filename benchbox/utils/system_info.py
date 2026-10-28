# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import platform
from dataclasses import dataclass
from typing import Any

import psutil

from benchbox.utils.environment import detect_execution_class, is_cpu_architecture_token


@dataclass
class SystemInfo:
    os_name: str
    os_version: str
    architecture: str
    cpu_model: str | None
    cpu_cores: int
    total_memory_gb: float
    available_memory_gb: float
    python_version: str
    hostname: str

    cpu_vendor: str | None = None
    cpu_identity_provenance: str | None = None
    execution_class: str | None = None
    container_runtime: str | None = None
    container_host_os: str | None = None
    execution_class_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "os_type": self.os_name,
            "os_version": self.os_version,
            "architecture": self.architecture,
            "cpu_model": self.cpu_model,
            "cpu_vendor": self.cpu_vendor,
            "cpu_identity_provenance": self.cpu_identity_provenance,
            "cpu_cores": self.cpu_cores,
            "cpu_count": self.cpu_cores,
            "memory_gb": self.total_memory_gb,
            "os_release": self.os_version,
            "total_memory_gb": self.total_memory_gb,
            "available_memory_gb": self.available_memory_gb,
            "python_version": self.python_version,
            "hostname": self.hostname,
            "execution_class": self.execution_class,
            "container_runtime": self.container_runtime,
            "container_host_os": self.container_host_os,
            "execution_class_source": self.execution_class_source,
        }


def _proc_cpuinfo_model() -> str | None:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            for line in handle:
                if "model name" in line:
                    return line.split(":")[1].strip()
    except (FileNotFoundError, OSError):
        return None
    return None


def get_system_info() -> SystemInfo:

    memory_info = psutil.virtual_memory()
    total_memory_gb = memory_info.total / (1024**3)
    available_memory_gb = memory_info.available / (1024**3)

    cpu_vendor: str | None = None
    cpu_identity_provenance: str | None = None
    try:
        from benchbox.utils.environment import detect_cpu_info

        cpu_model, cpu_vendor = detect_cpu_info()
        if cpu_model and not is_cpu_architecture_token(cpu_model, platform.machine()):
            cpu_identity_provenance = "measured"
        else:
            cpu_model = None
    except Exception:
        cpu_model = None

    if not cpu_model:
        try:
            cpu_model = platform.processor() or ""
            if not cpu_model:
                cpu_model = _proc_cpuinfo_model() or ""
        except Exception:
            cpu_model = ""
        if not cpu_model or is_cpu_architecture_token(cpu_model, platform.machine()):
            cpu_model = None
        else:
            cpu_identity_provenance = "inferred"

    return SystemInfo(
        os_name=platform.system(),
        os_version=platform.release(),
        architecture=platform.machine(),
        cpu_model=cpu_model,
        cpu_vendor=cpu_vendor,
        cpu_identity_provenance=cpu_identity_provenance,
        cpu_cores=psutil.cpu_count(),
        total_memory_gb=total_memory_gb,
        available_memory_gb=available_memory_gb,
        python_version=platform.python_version(),
        hostname=platform.node(),
        **detect_execution_class(),
    )


def get_memory_info() -> dict[str, float]:
    memory_info = psutil.virtual_memory()
    return {
        "total_gb": memory_info.total / (1024**3),
        "available_gb": memory_info.available / (1024**3),
        "used_gb": memory_info.used / (1024**3),
        "percent_used": memory_info.percent,
    }


def get_cpu_info() -> dict[str, Any]:
    return {
        "logical_cores": psutil.cpu_count(),
        "physical_cores": psutil.cpu_count(logical=False),
        "current_usage_percent": psutil.cpu_percent(interval=1),
        "per_core_usage": psutil.cpu_percent(interval=1, percpu=True),
        "model": platform.processor() or f"{platform.machine()} CPU",
    }
