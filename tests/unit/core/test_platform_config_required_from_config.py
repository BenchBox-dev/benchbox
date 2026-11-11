from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.platform_config import REQUIRED_FROM_CONFIG_KEYS, get_platform_config
from benchbox.core.schemas import DatabaseConfig
from benchbox.platforms.base.config_utils import TUNING_FORWARD_KEYS, build_adapter_config

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_from_config_required_keys_are_forwarded_by_shared_helper() -> None:
    assert frozenset(TUNING_FORWARD_KEYS) >= REQUIRED_FROM_CONFIG_KEYS
    payload = {key: f"value-{key}" for key in REQUIRED_FROM_CONFIG_KEYS}
    payload["benchmark"] = "tpch"
    payload["scale_factor"] = 1.0
    built = build_adapter_config(payload, platform="sqlite", generated_key=None)
    for key in REQUIRED_FROM_CONFIG_KEYS:
        assert built[key] == payload[key]


def test_from_config_required_key_missing_from_helper_fails_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    key = next(iter(REQUIRED_FROM_CONFIG_KEYS))
    reduced_keys = tuple(k for k in TUNING_FORWARD_KEYS if k != key)
    monkeypatch.setattr("benchbox.platforms.base.config_utils.TUNING_FORWARD_KEYS", reduced_keys)

    payload = {k: f"value-{k}" for k in REQUIRED_FROM_CONFIG_KEYS}
    payload["benchmark"] = "tpch"
    payload["scale_factor"] = 1.0
    built = build_adapter_config(payload, platform="sqlite", generated_key=None)

    assert key not in built


def test_from_config_required_get_platform_config_emits_tuning_config() -> None:
    config = DatabaseConfig(type="sqlite", name="t")
    produced = get_platform_config(config, None, benchmark_name="tpch", scale_factor=1.0, tuning_config={"x": 1})
    assert produced["tuning_config"] == {"x": 1}
    assert produced["benchmark"] == "tpch"
    assert produced["scale_factor"] == 1.0


@pytest.mark.parametrize("memory_limit", ["4G", "", 0, "invalid"])
def test_get_platform_config_preserves_explicit_memory_limit(memory_limit: object) -> None:
    config = DatabaseConfig(
        type="datafusion",
        name="DataFusion",
        options={"memory_limit": memory_limit},
    )
    system_profile = SimpleNamespace(memory_total_gb=16.0, cpu_cores_logical=10)

    produced = get_platform_config(config, system_profile)

    assert produced["memory_limit"] == memory_limit
    assert produced["thread_limit"] == 8


def test_get_platform_config_top_level_memory_limit_wins_over_nested_option() -> None:
    config = DatabaseConfig(
        type="datafusion",
        name="DataFusion",
        memory_limit="6G",
        options={"memory_limit": "4G"},
    )

    produced = get_platform_config(config, SimpleNamespace(memory_total_gb=16.0, cpu_cores_logical=10))

    assert produced["memory_limit"] == "6G"


def test_get_platform_config_keeps_non_adapter_options_nested() -> None:
    options = {
        "type": "other",
        "name": "other",
        "connection_string": "secret",
        "custom_setting": "value",
    }
    config = DatabaseConfig(type="duckdb", name="DuckDB", options=options)

    produced = get_platform_config(config, None)

    assert produced["options"] == options
    assert not {"type", "name", "connection_string", "custom_setting"} & produced.keys()


