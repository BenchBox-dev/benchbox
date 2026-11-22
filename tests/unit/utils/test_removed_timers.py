from __future__ import annotations

import importlib

import pytest

import benchbox.utils

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REMOVED_EXPORTS = [
    "TimeoutManager",
    "get_timeout_manager",
    "timeout",
    "TimeoutConfig",
    "TimeoutError",
    "run_with_timeout",
]


def test_benchbox_utils_imports_without_the_removed_timers() -> None:
    for name in REMOVED_EXPORTS:
        assert not hasattr(benchbox.utils, name)
        assert name not in benchbox.utils.__all__


def test_the_timeout_manager_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("benchbox.utils.timeout_manager")


def test_resource_limits_config_has_no_timeout_fields() -> None:
    from benchbox.utils.resource_limits import ResourceLimitsConfig

    config = ResourceLimitsConfig.from_config_dict({"default_operation_timeout": 1, "enforce_timeouts": False})

    assert not hasattr(config, "default_operation_timeout")
    assert not hasattr(config, "enforce_timeouts")
