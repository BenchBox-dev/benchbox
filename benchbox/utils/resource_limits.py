# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from benchbox.utils.clock import elapsed_seconds, mono_time

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


GIB = 1024**3
EXTERNAL_TIMEOUT_SECONDS = 5.0
RETRY_INTERVAL_SECONDS = 5.0
RETRY_WINDOW_SECONDS = 30.0
NORMAL_PRESSURE_LEVEL = 1
ADMIT_MIN_GIB_ENV = "BENCHBOX_HEAVY_ADMIT_MIN_GIB"
MIN_LEVEL_ENV = "BENCHBOX_HEAVY_MIN_LEVEL"
MIN_FREE_DISK_GIB_ENV = "BENCHBOX_HEAVY_MIN_FREE_DISK_GIB"
MEMORY_BUDGET_GIB_ENV = "BENCHBOX_HEAVY_MEMORY_BUDGET_GIB"
OUTPUT_DIR_ENV = "BENCHBOX_OUTPUT_DIR"
DEFAULT_ADMIT_MIN_GIB = 2.0
DEFAULT_MIN_LEVEL = 35.0
DEFAULT_MIN_FREE_DISK_GIB = 50.0
DEFAULT_BUDGET_FRACTION = 0.5
DEFAULT_OUTPUT_ROOT = "benchmark_runs"
SYSCTL_PATH = "/usr/sbin/sysctl"

_sleep = time.sleep

Runner = Callable[[Sequence[str], float], "str | None"]


def _existing_ancestor(path: Path) -> Path:
    if path.exists():
        return path
    return next((ancestor for ancestor in path.parents if ancestor.exists()), Path("/"))


def free_space_gib(path: str | Path) -> float:
    usage = shutil.disk_usage(_existing_ancestor(Path(path).expanduser()))
    return usage.free / GIB


@dataclass(frozen=True)
class MemorySnapshot:
    free_gib: float | None
    swap_used_percent: float | None


def read_memory_snapshot() -> MemorySnapshot:
    if psutil is None:
        return MemorySnapshot(free_gib=None, swap_used_percent=None)
    try:
        free_gib = psutil.virtual_memory().available / GIB
    except (OSError, RuntimeError, ValueError, AttributeError):
        free_gib = None
    try:
        swap_used_percent = psutil.swap_memory().percent
    except (OSError, RuntimeError, ValueError, AttributeError):
        swap_used_percent = None
    return MemorySnapshot(free_gib=free_gib, swap_used_percent=swap_used_percent)


def _kill_started_group(process: subprocess.Popen[str]) -> None:
    try:
        if hasattr(os, "killpg") and hasattr(signal, "SIGKILL"):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        pass


def run_text(argv: Sequence[str], timeout: float) -> str | None:
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=sys.platform != "win32",
        )
    except OSError:
        return None
    try:
        output, _ = process.communicate(timeout=max(timeout, 0.0))
    except subprocess.TimeoutExpired:
        _kill_started_group(process)
        try:
            process.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        return None
    except OSError:
        return None
    return output if process.returncode == 0 else None


@dataclass(frozen=True)
class VmReservation:
    kind: str
    name: str
    state: str | None
    memory_gib: float | None


@dataclass(frozen=True)
class DiskReading:
    label: str
    path: str
    free_gib: float | None
    device: int | None = None


@dataclass(frozen=True)
class HostPressure:
    total_gib: float | None
    available_gib: float | None
    swap_used_percent: float | None
    pressure_level: int | None
    memorystatus_level: int | None
    disks: tuple[DiskReading, ...]
    vms: tuple[VmReservation, ...] | None


@dataclass(frozen=True)
class AdmissionPolicy:
    need_gib: float = DEFAULT_ADMIT_MIN_GIB
    min_level: float = DEFAULT_MIN_LEVEL
    min_free_disk_gib: float = DEFAULT_MIN_FREE_DISK_GIB


@dataclass(frozen=True)
class AdmissionResult:
    admitted: bool
    refusals: tuple[str, ...]
    pressure: HostPressure
    samples: int = 1
    forced: bool = False


def _float_setting(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value != value or value < 0:
        return default
    return value


def admission_policy_from_env(
    environ: Mapping[str, str] | None = None, *, need_gib: float | None = None
) -> AdmissionPolicy:
    env = os.environ if environ is None else environ
    return AdmissionPolicy(
        need_gib=_float_setting(env, ADMIT_MIN_GIB_ENV, DEFAULT_ADMIT_MIN_GIB) if need_gib is None else need_gib,
        min_level=_float_setting(env, MIN_LEVEL_ENV, DEFAULT_MIN_LEVEL),
        min_free_disk_gib=_float_setting(env, MIN_FREE_DISK_GIB_ENV, DEFAULT_MIN_FREE_DISK_GIB),
    )


def heavy_memory_budget_gib(environ: Mapping[str, str] | None = None) -> float | None:
    env = os.environ if environ is None else environ
    raw = env.get(MEMORY_BUDGET_GIB_ENV)
    if raw is not None and raw.strip():
        try:
            value = float(raw)
        except ValueError:
            value = -1.0
        if value > 0 and value == value:
            return value
    total_mb = get_system_memory_mb()
    if total_mb <= 0:
        return None
    return total_mb / 1024 * DEFAULT_BUDGET_FRACTION


def _sysctl_int(name: str, runner: Runner) -> int | None:
    text = runner([SYSCTL_PATH, "-n", name], EXTERNAL_TIMEOUT_SECONDS)
    if text is None:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def _bytes_to_gib(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return value / GIB


def _nested(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _parse_containers(text: str) -> tuple[VmReservation, ...] | None:
    try:
        items = json.loads(text)
    except ValueError:
        return None
    if not isinstance(items, list):
        return None
    found = []
    for item in items:
        name = _nested(item, "configuration", "id") or _nested(item, "id")
        if not isinstance(name, str):
            continue
        state = _nested(item, "status", "state")
        found.append(
            VmReservation(
                kind="container",
                name=name,
                state=state if isinstance(state, str) else None,
                memory_gib=_bytes_to_gib(_nested(item, "configuration", "resources", "memoryInBytes")),
            )
        )
    return tuple(found)


def _parse_machines(text: str) -> tuple[VmReservation, ...] | None:
    try:
        items = json.loads(text)
    except ValueError:
        return None
    if not isinstance(items, list):
        return None
    found = []
    for item in items:
        name = _nested(item, "id")
        if not isinstance(name, str):
            continue
        state = _nested(item, "status")
        found.append(
            VmReservation(
                kind="machine",
                name=name,
                state=state if isinstance(state, str) else None,
                memory_gib=_bytes_to_gib(_nested(item, "memory")),
            )
        )
    return tuple(found)


def read_vm_reservations(
    runner: Runner = run_text, which: Callable[[str], str | None] = shutil.which
) -> tuple[VmReservation, ...] | None:
    if which("container") is None:
        return ()
    started = mono_time()
    found: list[VmReservation] = []
    for argv, parse in (
        (["container", "list", "--format", "json"], _parse_containers),
        (["container", "machine", "list", "--format", "json"], _parse_machines),
    ):
        remaining = EXTERNAL_TIMEOUT_SECONDS - elapsed_seconds(started)
        if remaining <= 0:
            return None
        text = runner(argv, remaining)
        parsed = None if text is None else parse(text)
        if parsed is None:
            return None
        found.extend(parsed)
    return tuple(found)


def default_disk_roots(environ: Mapping[str, str] | None = None) -> tuple[tuple[str, str], ...]:
    env = os.environ if environ is None else environ
    output = env.get(OUTPUT_DIR_ENV) or DEFAULT_OUTPUT_ROOT
    return (("boot volume", Path.cwd().anchor or "/"), ("output root", str(Path(output).expanduser())))


def _read_disk(label: str, path: str) -> DiskReading:
    try:
        free = free_space_gib(path)
    except (OSError, ValueError):
        free = None
    try:
        device = os.stat(_existing_ancestor(Path(path).expanduser())).st_dev
    except OSError:
        device = None
    return DiskReading(label=label, path=path, free_gib=free, device=device)


def read_host_pressure(
    roots: Sequence[tuple[str, str]] | None = None,
    *,
    runner: Runner = run_text,
    which: Callable[[str], str | None] = shutil.which,
    platform: str | None = None,
) -> HostPressure:
    is_macos = (sys.platform if platform is None else platform) == "darwin"
    snapshot = read_memory_snapshot()
    total_mb = get_system_memory_mb()
    return HostPressure(
        total_gib=total_mb / 1024 if total_mb > 0 else None,
        available_gib=snapshot.free_gib,
        swap_used_percent=snapshot.swap_used_percent,
        pressure_level=_sysctl_int("kern.memorystatus_vm_pressure_level", runner) if is_macos else None,
        memorystatus_level=_sysctl_int("kern.memorystatus_level", runner) if is_macos else None,
        disks=tuple(_read_disk(label, path) for label, path in (default_disk_roots() if roots is None else roots)),
        vms=read_vm_reservations(runner, which),
    )


def evaluate_admission(pressure: HostPressure, policy: AdmissionPolicy) -> tuple[str, ...]:
    refusals: list[str] = []
    level = pressure.pressure_level
    if level is not None and level != NORMAL_PRESSURE_LEVEL:
        refusals.append(f"memory pressure level {level} (admits only {NORMAL_PRESSURE_LEVEL}, normal)")
    status = pressure.memorystatus_level
    if status is not None and status < policy.min_level:
        refusals.append(f"memorystatus_level {status} below floor {policy.min_level:g} ({MIN_LEVEL_ENV})")
    available = pressure.available_gib
    if available is not None and available < policy.need_gib:
        refusals.append(
            f"available memory {available:.2f} GiB below need {policy.need_gib:g} GiB ({ADMIT_MIN_GIB_ENV})"
        )
    groups: dict[object, list[DiskReading]] = {}
    for index, disk in enumerate(pressure.disks):
        if disk.free_gib is not None and disk.free_gib < policy.min_free_disk_gib:
            groups.setdefault(("dev", disk.device) if disk.device is not None else ("root", index), []).append(disk)
    for members in groups.values():
        names = ", ".join(f"{disk.label} ({disk.path})" for disk in members)
        lowest = min(disk.free_gib for disk in members if disk.free_gib is not None)
        refusals.append(
            f"free disk on {names} {lowest:.1f} GiB below floor {policy.min_free_disk_gib:g} GiB "
            f"({MIN_FREE_DISK_GIB_ENV})"
        )
    return tuple(refusals)


def _fmt(value: float | int | None, unit: str = "") -> str:
    if value is None:
        return "unmeasured"
    return f"{value:.2f}{unit}" if isinstance(value, float) else f"{value}{unit}"


def format_admission_report(result: AdmissionResult, policy: AdmissionPolicy) -> str:
    pressure = result.pressure
    verdict = (
        "FORCED (refusals overridden)"
        if result.forced and result.refusals
        else ("ADMITTED" if result.admitted else "REFUSED")
    )
    lines = [
        f"heavy-work admission: {verdict} after {result.samples} sample(s)",
        f"  memory pressure level: {_fmt(pressure.pressure_level)} (admit {NORMAL_PRESSURE_LEVEL})",
        f"  memorystatus_level: {_fmt(pressure.memorystatus_level)} (floor {policy.min_level:g})",
        f"  available memory: {_fmt(pressure.available_gib, ' GiB')} (need {policy.need_gib:g} GiB)",
        f"  total memory: {_fmt(pressure.total_gib, ' GiB')}; swap used (reported only): "
        f"{_fmt(pressure.swap_used_percent, '%')}",
    ]
    for disk in pressure.disks:
        lines.append(
            f"  free disk, {disk.label} ({disk.path}): {_fmt(disk.free_gib, ' GiB')} (floor {policy.min_free_disk_gib:g} GiB)"
        )
    if pressure.vms is None:
        lines.append("  VM reservations: unmeasured")
    elif not pressure.vms:
        lines.append("  VM reservations: none")
    else:
        known = sum(vm.memory_gib or 0.0 for vm in pressure.vms)
        lines.append(f"  VM reservations (reported, not subtracted): {known:.2f} GiB across {len(pressure.vms)}")
        for vm in pressure.vms:
            lines.append(f"    {vm.kind} {vm.name} [{vm.state or 'unknown'}]: {_fmt(vm.memory_gib, ' GiB')}")
    for refusal in result.refusals:
        lines.append(f"  refused: {refusal}")
    return "\n".join(lines)


def admit_heavy_work(
    policy: AdmissionPolicy,
    *,
    reader: Callable[[], HostPressure] | None = None,
    force: bool = False,
    retry_window_seconds: float = RETRY_WINDOW_SECONDS,
    retry_interval_seconds: float = RETRY_INTERVAL_SECONDS,
) -> AdmissionResult:
    read = read_host_pressure if reader is None else reader
    started = mono_time()
    samples = 0
    while True:
        pressure = read()
        samples += 1
        refusals = evaluate_admission(pressure, policy)
        if not refusals:
            return AdmissionResult(True, (), pressure, samples)
        if force:
            return AdmissionResult(True, refusals, pressure, samples, forced=True)
        remaining = retry_window_seconds - elapsed_seconds(started)
        if remaining <= 0:
            return AdmissionResult(False, refusals, pressure, samples)
        _sleep(min(retry_interval_seconds, remaining))
