from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from benchbox.core.platform_manifest import (
    DEFAULT_EXECUTION_ENGINE,
    EXECUTION_ENGINE_CLASSES,
    get_all_platform_aliases,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from benchbox.core.platform_registry import ExecutionEngineCapability

ExecutionEngineResolution = Literal[
    "explicit",
    "tuning_profile",
    "legacy_option",
    "version_default",
    "platform_default",
]
ObservedSource = Literal["explain", "vendor_log", "session_var", "runtime_state", "none"]

NOT_CAPTURED = "not_captured"
MIXED = "mixed"
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
    observed_reason: str | None = None

    def __post_init__(self) -> None:
        if self.observed == NOT_CAPTURED:
            if self.observed_source != "none":
                raise ValueError(f"A {NOT_CAPTURED!r} engine observation cannot name source {self.observed_source!r}")
            return
        if self.observed_source == "none":
            raise ValueError(f"Observed engine {self.observed!r} needs the source it was observed from")
        if self.observed_reason is not None:
            raise ValueError(f"Observed engine {self.observed!r} cannot carry a {NOT_CAPTURED!r} reason")


def summarize_observed_engines(
    receipt: ExecutionEngineReceipt,
    observations: Sequence[str | None],
    source: ObservedSource,
    unavailable: str | None = None,
) -> ExecutionEngineReceipt:
    missing = sum(1 for observation in observations if not observation)
    reason = unavailable
    if reason is None and not observations:
        reason = "no query completed"
    if reason is None and missing:
        reason = f"the library reported no engine for {missing} of {len(observations)} queries"
    if reason is not None:
        return replace(receipt, observed=NOT_CAPTURED, observed_source="none", observed_reason=reason)
    distinct = set(observations)
    observed = next(iter(distinct)) if len(distinct) == 1 else MIXED
    return replace(receipt, observed=str(observed), observed_source=source, observed_reason=None)


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


class ObservationNotRecognizedError(Exception):
    pass


@dataclass(frozen=True)
class QueryObservationTarget:
    connection: Any
    query: str
    query_id: str
    statement_ids: tuple[str, ...] = ()
    evidence: Any = None


_UNMEASURED_STATEMENTS: ContextVar[bool] = ContextVar("benchbox_unmeasured_statements", default=False)


@contextmanager
def unmeasured_statements() -> Iterator[None]:
    token = _UNMEASURED_STATEMENTS.set(True)
    try:
        yield
    finally:
        _UNMEASURED_STATEMENTS.reset(token)


class ExecutionEngineHook:
    OBSERVED_ENGINE_SOURCE: ObservedSource = "none"
    OBSERVATION_AT_RUN_END: bool = False
    RUN_END_OBSERVATION_USES_CONNECTION: bool = False

    def reset_query_engine_observation(self) -> None:
        self._engine_targets = []
        self._engine_observations = []
        self._unrecognized_observations = 0
        self._engine_measured_phase_open = False
        self._engine_window_active = False
        self._engine_run_measured = False
        self._engine_run_end_cache = None

    def open_engine_observation_window(self) -> None:
        self._engine_measured_phase_open = True
        self._engine_window_active = True

    def close_engine_observation_window(self) -> None:
        self._engine_measured_phase_open = False
        self._engine_window_active = False
        self._engine_run_measured = True

    def engine_observation_gap(self) -> str | None:
        return None

    def observe_execution_engine(self, lazy: Any) -> str | None:
        return None

    def record_query_engine_observation(
        self, connection: Any, query: str, query_id: str, statement_ids: Sequence[str] = ()
    ) -> None:
        if self.OBSERVED_ENGINE_SOURCE == "none" or self.engine_observation_gap() is not None:
            return
        if _UNMEASURED_STATEMENTS.get() or not getattr(self, "_engine_measured_phase_open", True):
            return
        target = QueryObservationTarget(connection, query, query_id, tuple(statement_ids))
        if self.OBSERVATION_AT_RUN_END:
            self.__dict__.setdefault("_engine_targets", []).append(target)
            return
        self.__dict__.setdefault("_engine_observations", []).append(self._observe_target(target))

    def observe_run_end_targets(
        self, targets: Sequence[QueryObservationTarget], connection: Any = None
    ) -> list[str | None]:
        return [
            self._observe_target(target if connection is None else replace(target, connection=connection))
            for target in targets
        ]

    def _observe_run_end(self, connection: Any) -> tuple[list[str | None], int, str | None]:
        targets = getattr(self, "_engine_targets", [])
        if not targets:
            return [], 0, None
        if self.RUN_END_OBSERVATION_USES_CONNECTION and connection is None:
            reason = "the run ended contained, so its connections were not used to observe the engine"
            return [None for _ in targets], 0, reason
        self._unrecognized_observations = 0
        observations = self.observe_run_end_targets(targets, connection)
        return observations, self._unrecognized_observations, None

    def _observe_target(self, target: QueryObservationTarget) -> str | None:
        from benchbox.core.results.timer_scope import outside_query_timer

        try:
            with outside_query_timer():
                return self.observe_execution_engine(target)
        except ObservationNotRecognizedError as exc:
            logger.debug("Engine observation not recognized: %s", exc)
            self._unrecognized_observations = getattr(self, "_unrecognized_observations", 0) + 1
        except Exception as exc:
            logger.debug("Engine observation failed: %s", exc)
        return None

    def finalize_query_engine_observation(self, receipt: Mapping[str, Any], connection: Any = None) -> dict[str, Any]:
        recorded = dict(receipt)
        if self.OBSERVED_ENGINE_SOURCE == "none":
            return recorded
        skipped_reason = None
        if self.OBSERVATION_AT_RUN_END and not getattr(self, "_engine_window_active", False):
            cache = getattr(self, "_engine_run_end_cache", None)
            if cache is None:
                cache = self._observe_run_end(connection)
                if getattr(self, "_engine_run_measured", False):
                    self._engine_run_end_cache = cache
            self._engine_observations, self._unrecognized_observations, skipped_reason = cache
        observations = getattr(self, "_engine_observations", [])
        gap = self.engine_observation_gap()
        summary = summarize_observed_engines(
            ExecutionEngineReceipt(**recorded), observations, self.OBSERVED_ENGINE_SOURCE, unavailable=gap
        )
        reason = summary.observed_reason
        unrecognized = getattr(self, "_unrecognized_observations", 0)
        if unrecognized and gap is None and summary.observed == NOT_CAPTURED:
            reason = f"query evidence not recognized for {unrecognized} of {len(observations)} queries"
        if skipped_reason and gap is None and summary.observed == NOT_CAPTURED:
            reason = skipped_reason
        recorded.update(observed=summary.observed, observed_source=summary.observed_source, observed_reason=reason)
        return recorded

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
    "MIXED",
    "NOT_CAPTURED",
    "RESERVED_OPTION_KEYS",
    "EngineBypassedByBenchmarkError",
    "ExecutionEngineHook",
    "ExecutionEngineReceipt",
    "ExecutionEngineResolution",
    "ExecutionEngineUnavailableError",
    "ObservedSource",
    "QueryObservationTarget",
    "UnsupportedExecutionEngineError",
    "platform_default_receipt",
    "reject_engine_bypassing_benchmark",
    "resolve_requested",
    "summarize_observed_engines",
    "supported_execution_engines",
    "unmeasured_statements",
]
