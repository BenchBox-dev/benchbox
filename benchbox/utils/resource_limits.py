# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger(__name__)


class ResourceWarningLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class ResourceWarning:
    timestamp: float
    level: ResourceWarningLevel
    resource_type: str
    current_value: float
    threshold_value: float
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "level": self.level.value,
            "resource_type": self.resource_type,
            "current_value": self.current_value,
            "threshold_value": self.threshold_value,
            "message": self.message,
        }


@dataclass
class ResourceLimitsConfig:
    memory_limit_mb: float | None = None
    memory_warning_percent: float = 75.0
    memory_critical_percent: float = 90.0

    cpu_warning_percent: float = 90.0

    default_operation_timeout: float = 300.0
    enforce_timeouts: bool = True

    warning_callback: Callable[[ResourceWarning], None] | None = None
    critical_callback: Callable[[ResourceWarning], None] | None = None

    enable_graceful_degradation: bool = False
    degradation_memory_threshold_percent: float = 80.0

    def __post_init__(self):
        if not 0 < self.memory_warning_percent <= 100:
            raise ValueError("memory_warning_percent must be between 0 and 100")
        if not 0 < self.memory_critical_percent <= 100:
            raise ValueError("memory_critical_percent must be between 0 and 100")
        if self.memory_warning_percent >= self.memory_critical_percent:
            raise ValueError("memory_warning_percent must be less than memory_critical_percent")

    @classmethod
    def from_config_dict(cls, config: dict[str, Any]) -> ResourceLimitsConfig:
        return cls(
            memory_limit_mb=config.get("memory_limit_mb"),
            memory_warning_percent=config.get("memory_warning_percent", 75.0),
            memory_critical_percent=config.get("memory_critical_percent", 90.0),
            cpu_warning_percent=config.get("cpu_warning_percent", 90.0),
            default_operation_timeout=config.get("default_operation_timeout", 300.0),
            enforce_timeouts=config.get("enforce_timeouts", True),
            enable_graceful_degradation=config.get("enable_graceful_degradation", False),
            degradation_memory_threshold_percent=config.get("degradation_memory_threshold_percent", 80.0),
        )


class ResourceLimitExceeded(Exception):
    def __init__(self, message: str, resource_type: str, current_value: float, limit_value: float):
        self.resource_type = resource_type
        self.current_value = current_value
        self.limit_value = limit_value
        super().__init__(message)


@dataclass
class ResourceUsageSummary:
    peak_memory_mb: float = 0.0
    average_memory_mb: float = 0.0
    peak_cpu_percent: float = 0.0
    average_cpu_percent: float = 0.0
    warnings: list[ResourceWarning] = field(default_factory=list)
    limit_exceeded: bool = False
    degradation_triggered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "peak_memory_mb": self.peak_memory_mb,
            "average_memory_mb": self.average_memory_mb,
            "peak_cpu_percent": self.peak_cpu_percent,
            "average_cpu_percent": self.average_cpu_percent,
            "warnings": [w.to_dict() for w in self.warnings],
            "warning_count": len(self.warnings),
            "limit_exceeded": self.limit_exceeded,
            "degradation_triggered": self.degradation_triggered,
        }


class ResourceLimitMonitor:
    def __init__(
        self,
        config: ResourceLimitsConfig | None = None,
        sample_interval: float = 2.0,
    ):
        self.config = config or ResourceLimitsConfig()
        self.sample_interval = sample_interval

        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._process: Any = None

        self._peak_memory_mb: float = 0.0
        self._peak_cpu_percent: float = 0.0
        self._memory_samples: list[float] = []
        self._cpu_samples: list[float] = []
        self._warnings: list[ResourceWarning] = []
        self._limit_exceeded: bool = False
        self._degradation_triggered: bool = False

        self._last_memory_warning_level: ResourceWarningLevel | None = None
        self._last_cpu_warning_level: ResourceWarningLevel | None = None

        self._total_memory_mb: float | None = None

    def start(self) -> None:
        if psutil is None:
            logger.debug("psutil not available, resource monitoring disabled")
            return

        if self._thread is not None and self._thread.is_alive():
            return

        self._process = psutil.Process()
        self._stop_event = threading.Event()

        self._total_memory_mb = psutil.virtual_memory().total / (1024 * 1024)

        self._peak_memory_mb = 0.0
        self._peak_cpu_percent = 0.0
        self._memory_samples = []
        self._cpu_samples = []
        self._warnings = []
        self._limit_exceeded = False
        self._degradation_triggered = False
        self._last_memory_warning_level = None
        self._last_cpu_warning_level = None

        self._thread = threading.Thread(
            target=self._sample_loop,
            daemon=True,
            name="ResourceLimitMonitor",
        )
        self._thread.start()
        logger.debug("Resource limit monitoring started")

    def _sample_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._sample_and_check()
            except Exception as e:
                logger.debug(f"Resource sampling error: {e}")

            self._stop_event.wait(self.sample_interval)

    def _sample_and_check(self) -> None:
        if self._process is None:
            return

        try:
            mem_info = self._process.memory_info()
            memory_mb = mem_info.rss / (1024 * 1024)
            memory_percent = (memory_mb / self._total_memory_mb * 100) if self._total_memory_mb else 0

            cpu_percent = self._process.cpu_percent(interval=0.1)

            self._memory_samples.append(memory_mb)
            self._cpu_samples.append(cpu_percent)

            if memory_mb > self._peak_memory_mb:
                self._peak_memory_mb = memory_mb
            if cpu_percent > self._peak_cpu_percent:
                self._peak_cpu_percent = cpu_percent

            self._check_memory_limits(memory_mb, memory_percent)
            self._check_cpu_limits(cpu_percent)

        except Exception as e:
            logger.debug(f"Error sampling resources: {e}")

    def _check_memory_limits(self, memory_mb: float, memory_percent: float) -> None:
        if self.config.memory_limit_mb and memory_mb > self.config.memory_limit_mb:
            self._limit_exceeded = True
            warning = ResourceWarning(
                timestamp=time.time(),
                level=ResourceWarningLevel.CRITICAL,
                resource_type="memory",
                current_value=memory_mb,
                threshold_value=self.config.memory_limit_mb,
                message=f"Memory limit exceeded: {memory_mb:.1f}MB > {self.config.memory_limit_mb:.1f}MB",
            )
            self._record_warning(warning)
            logger.error(warning.message)
            if self._stop_event:
                self._stop_event.set()
            return

        if memory_percent >= self.config.memory_critical_percent:
            if self._last_memory_warning_level != ResourceWarningLevel.CRITICAL:
                warning = ResourceWarning(
                    timestamp=time.time(),
                    level=ResourceWarningLevel.CRITICAL,
                    resource_type="memory",
                    current_value=memory_percent,
                    threshold_value=self.config.memory_critical_percent,
                    message=f"Critical memory usage: {memory_percent:.1f}% >= {self.config.memory_critical_percent:.1f}%",
                )
                self._record_warning(warning)
                logger.warning(warning.message)
                self._last_memory_warning_level = ResourceWarningLevel.CRITICAL

        elif memory_percent >= self.config.memory_warning_percent:
            if self._last_memory_warning_level not in (ResourceWarningLevel.WARNING, ResourceWarningLevel.CRITICAL):
                warning = ResourceWarning(
                    timestamp=time.time(),
                    level=ResourceWarningLevel.WARNING,
                    resource_type="memory",
                    current_value=memory_percent,
                    threshold_value=self.config.memory_warning_percent,
                    message=f"High memory usage: {memory_percent:.1f}% >= {self.config.memory_warning_percent:.1f}%",
                )
                self._record_warning(warning)
                logger.warning(warning.message)
                self._last_memory_warning_level = ResourceWarningLevel.WARNING

        if (
            self.config.enable_graceful_degradation
            and memory_percent >= self.config.degradation_memory_threshold_percent
            and not self._degradation_triggered
        ):
            self._degradation_triggered = True
            logger.info(
                f"Graceful degradation triggered at {memory_percent:.1f}% memory "
                f"(threshold: {self.config.degradation_memory_threshold_percent:.1f}%)"
            )

    def _check_cpu_limits(self, cpu_percent: float) -> None:
        if cpu_percent >= self.config.cpu_warning_percent:
            if self._last_cpu_warning_level != ResourceWarningLevel.WARNING:
                warning = ResourceWarning(
                    timestamp=time.time(),
                    level=ResourceWarningLevel.WARNING,
                    resource_type="cpu",
                    current_value=cpu_percent,
                    threshold_value=self.config.cpu_warning_percent,
                    message=f"High CPU usage: {cpu_percent:.1f}% >= {self.config.cpu_warning_percent:.1f}%",
                )
                self._record_warning(warning)
                logger.info(warning.message)
                self._last_cpu_warning_level = ResourceWarningLevel.WARNING

    def _record_warning(self, warning: ResourceWarning) -> None:
        self._warnings.append(warning)

        if warning.level == ResourceWarningLevel.CRITICAL and self.config.critical_callback:
            try:
                self.config.critical_callback(warning)
            except Exception as e:
                logger.debug(f"Critical callback error: {e}")
        elif self.config.warning_callback:
            try:
                self.config.warning_callback(warning)
            except Exception as e:
                logger.debug(f"Warning callback error: {e}")

    def stop(self) -> ResourceUsageSummary:
        if self._stop_event is not None:
            self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

        avg_memory = sum(self._memory_samples) / len(self._memory_samples) if self._memory_samples else 0.0
        avg_cpu = sum(self._cpu_samples) / len(self._cpu_samples) if self._cpu_samples else 0.0

        summary = ResourceUsageSummary(
            peak_memory_mb=self._peak_memory_mb,
            average_memory_mb=avg_memory,
            peak_cpu_percent=self._peak_cpu_percent,
            average_cpu_percent=avg_cpu,
            warnings=list(self._warnings),
            limit_exceeded=self._limit_exceeded,
            degradation_triggered=self._degradation_triggered,
        )

        logger.debug(
            f"Resource monitoring stopped: peak_mem={self._peak_memory_mb:.1f}MB, warnings={len(self._warnings)}"
        )

        return summary

    def get_current_usage(self) -> dict[str, float]:
        if self._process is None:
            return {"memory_mb": 0.0, "memory_percent": 0.0, "cpu_percent": 0.0}

        try:
            mem_info = self._process.memory_info()
            memory_mb = mem_info.rss / (1024 * 1024)
            memory_percent = (memory_mb / self._total_memory_mb * 100) if self._total_memory_mb else 0
            return {
                "memory_mb": memory_mb,
                "memory_percent": memory_percent,
                "cpu_percent": self._process.cpu_percent(interval=0.1),
            }
        except Exception:
            return {"memory_mb": 0.0, "memory_percent": 0.0, "cpu_percent": 0.0}

    def should_degrade(self) -> bool:
        return self._degradation_triggered

    @property
    def warnings(self) -> list[ResourceWarning]:
        return list(self._warnings)

    @property
    def limit_exceeded(self) -> bool:
        return self._limit_exceeded


def get_system_memory_mb() -> float:
    if psutil is None:
        return 0.0
    try:
        return psutil.virtual_memory().total / (1024 * 1024)
    except Exception:
        return 0.0


def get_available_memory_mb() -> float:
    if psutil is None:
        return 0.0
    try:
        return psutil.virtual_memory().available / (1024 * 1024)
    except Exception:
        return 0.0


def calculate_safe_memory_limit(
    safety_margin_percent: float = 20.0,
    max_limit_mb: float | None = None,
) -> float:
    total_mb = get_system_memory_mb()
    if total_mb == 0:
        return max_limit_mb or 8192.0

    safe_mb = total_mb * (1 - safety_margin_percent / 100)

    if max_limit_mb is not None:
        safe_mb = min(safe_mb, max_limit_mb)

    return safe_mb
