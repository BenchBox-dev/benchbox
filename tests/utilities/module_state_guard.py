from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from typing import Any

import pytest

LoggerState = tuple[int, tuple[logging.Handler, ...], tuple[logging.Filter, ...], bool, bool]
_DEFAULT_LOGGER_STATE: LoggerState = (logging.NOTSET, (), (), True, False)
_LOGGER_FIELDS = ("level", "handlers", "filters", "propagate", "disabled")


def _benchbox_loggers() -> dict[str, logging.Logger]:
    return {
        name: logger
        for name, logger in list(logging.root.manager.loggerDict.items())
        if isinstance(logger, logging.Logger) and (name == "benchbox" or name.startswith("benchbox."))
    }


def _logger_state(logger: logging.Logger) -> LoggerState:
    return logger.level, tuple(logger.handlers), tuple(logger.filters), logger.propagate, logger.disabled


def _tracer_provider() -> Any:
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    return trace.get_tracer_provider()


def _quiet() -> bool:
    printing = sys.modules.get("benchbox.utils.printing")
    return bool(printing.is_quiet()) if printing is not None else False


def snapshot() -> dict[str, Any]:
    return {
        "loggers": {name: _logger_state(logger) for name, logger in _benchbox_loggers().items()},
        "quiet": _quiet(),
        "tracer_provider": _tracer_provider(),
    }


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for name in sorted(set(before["loggers"]) | set(after["loggers"])):
        old = before["loggers"].get(name, _DEFAULT_LOGGER_STATE)
        new = after["loggers"].get(name, _DEFAULT_LOGGER_STATE)
        for field, was, now in zip(_LOGGER_FIELDS, old, new, strict=True):
            if was != now:
                problems.append(f"logger {name!r} {field} changed from {_describe(was)} to {_describe(now)}")
    if before["quiet"] != after["quiet"]:
        problems.append(f"quiet flag changed from {before['quiet']} to {after['quiet']}")
    if before["tracer_provider"] is not after["tracer_provider"]:
        problems.append(
            f"tracer provider changed from {type(before['tracer_provider']).__name__} "
            f"to {type(after['tracer_provider']).__name__}"
        )
    return problems


def _describe(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return logging.getLevelName(value)
    if isinstance(value, tuple):
        return "[" + ", ".join(type(item).__name__ for item in value) + "]"
    return str(value)


def restore(before: dict[str, Any]) -> None:
    for name, logger in _benchbox_loggers().items():
        level, handlers, filters, propagate, disabled = before["loggers"].get(name, _DEFAULT_LOGGER_STATE)
        logger.level = level
        logger.handlers[:] = handlers
        logger.filters[:] = filters
        logger.propagate = propagate
        logger.disabled = disabled
    printing = sys.modules.get("benchbox.utils.printing")
    if printing is not None:
        printing.set_quiet(before["quiet"])


@pytest.fixture(autouse=True, scope="module")
def _process_state_unchanged_after_module() -> Iterator[None]:
    before = snapshot()
    yield
    problems = diff(before, snapshot())
    if problems:
        restore(before)
        pytest.fail(
            "this module left process-wide state changed, which leaks into later modules in the same worker: "
            + "; ".join(problems),
            pytrace=False,
        )
