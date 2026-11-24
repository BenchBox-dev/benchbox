from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from benchbox.core.platform_manifest import (
    DEFAULT_EXECUTION_ENGINE,
    EXECUTION_ENGINE_CLASSES,
    get_all_platform_aliases,
)

if TYPE_CHECKING:
    from benchbox.core.platform_registry import ExecutionEngineCapability

ExecutionEngineResolution = Literal[
    "explicit",
    "tuning_profile",
    "legacy_option",
    "version_default",
    "platform_default",
]
ObservedSource = Literal["explain", "vendor_log", "session_var", "none"]

NOT_CAPTURED = "not_captured"
RESERVED_OPTION_KEYS = frozenset({"engine", "mode", "backend", "runtime", "executor", "warehouse"})


class UnsupportedExecutionEngineError(ValueError):
    pass


class ExecutionEngineUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, kw_only=True)
class ExecutionEngineReceipt:
    requested: str
    applied: str | None
    applied_class: str | None
    applied_native: Mapping[str, Any] = field(default_factory=dict)
    resolution: ExecutionEngineResolution
    observed: str = NOT_CAPTURED
    observed_source: ObservedSource = "none"


def _manifest_platform_key(platform: str) -> str:
    name = platform.lower().strip().split(":", 1)[0]
    return get_all_platform_aliases().get(name, name)


def supported_execution_engines(platform: str) -> Mapping[str, ExecutionEngineCapability]:
    from benchbox.core.platform_registry import PlatformRegistry

    caps = PlatformRegistry.get_platform_capabilities(_manifest_platform_key(platform))
    return MappingProxyType({} if caps is None else dict(caps.execution_engines))


def _reject(platform: str, value: str, selectable: list[str]) -> UnsupportedExecutionEngineError:
    allowed = ", ".join([DEFAULT_EXECUTION_ENGINE, *selectable])
    return UnsupportedExecutionEngineError(
        f"Platform {platform!r} does not support execution engine {value!r}. Allowed: {allowed}"
    )


def resolve_requested(platform: str, value: str) -> str:
    if value == DEFAULT_EXECUTION_ENGINE:
        return value
    engines = supported_execution_engines(platform)
    selectable = [name for name, engine in engines.items() if engine.selectable]
    if value not in selectable:
        raise _reject(platform, value, selectable)
    return value


def platform_default_receipt(platform: str, requested: str) -> ExecutionEngineReceipt:
    if requested != DEFAULT_EXECUTION_ENGINE:
        raise _reject(platform, requested, [])
    return ExecutionEngineReceipt(
        requested=requested,
        applied=None,
        applied_class=None,
        resolution="platform_default",
    )


class ExecutionEngineHook:
    def resolve_execution_engine(self, requested: str) -> ExecutionEngineReceipt:
        return platform_default_receipt(str(getattr(self, "platform_name", type(self).__name__)), requested)

    @property
    def requested_execution_engine(self) -> str:
        receipt = getattr(self, "_engine_receipt", None)
        return receipt.requested if isinstance(receipt, ExecutionEngineReceipt) else DEFAULT_EXECUTION_ENGINE


class EngineBypassedByBenchmarkError(ValueError):
    pass


def reject_engine_bypassing_benchmark(benchmark: object, benchmark_name: str, platform: str, requested: str) -> None:
    if requested == DEFAULT_EXECUTION_ENGINE or getattr(benchmark, "DATAFRAME_WORKLOAD_USES_PLATFORM_ENGINE", True):
        return
    raise EngineBypassedByBenchmarkError(
        f"The {benchmark_name} benchmark does not run its timed DataFrame operations on the {platform} "
        f"execution engine, so execution engine {requested!r} cannot apply. "
        "Run it with the default execution engine."
    )


__all__ = [
    "DEFAULT_EXECUTION_ENGINE",
    "EXECUTION_ENGINE_CLASSES",
    "NOT_CAPTURED",
    "RESERVED_OPTION_KEYS",
    "EngineBypassedByBenchmarkError",
    "ExecutionEngineHook",
    "ExecutionEngineReceipt",
    "ExecutionEngineResolution",
    "ExecutionEngineUnavailableError",
    "ObservedSource",
    "UnsupportedExecutionEngineError",
    "platform_default_receipt",
    "reject_engine_bypassing_benchmark",
    "resolve_requested",
    "supported_execution_engines",
]
