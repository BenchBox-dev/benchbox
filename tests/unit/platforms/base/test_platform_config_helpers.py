from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from benchbox.core.tuning.interface import TuningType
from benchbox.platforms.base.config_utils import build_adapter_config
from benchbox.platforms.base.tuning import TuningHooksMixin, make_informational_constraint_applier
from benchbox.platforms.base.tuning_config import apply_standard_unified_tuning

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_build_adapter_config_generates_name_and_skips_none(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_generate_database_name(**kwargs):
        calls.append(kwargs)
        return "tpch_sf001_platform"

    monkeypatch.setattr("benchbox.utils.database_naming.generate_database_name", fake_generate_database_name)

    result = build_adapter_config(
        {"benchmark": "tpch", "scale_factor": 0.01, "host": "localhost", "port": None},
        platform="platform",
        fields=("host", "port"),
        include_none=False,
    )

    assert result == {"database": "tpch_sf001_platform", "host": "localhost"}
    assert calls == [{"benchmark_name": "tpch", "scale_factor": 0.01, "platform": "platform", "tuning_config": None}]


def test_build_adapter_config_honors_explicit_generated_key(monkeypatch: pytest.MonkeyPatch) -> None:
    generate = MagicMock(return_value="generated")
    monkeypatch.setattr("benchbox.utils.database_naming.generate_database_name", generate)

    result = build_adapter_config(
        {"benchmark": "tpch", "scale_factor": 1, "schema": "explicit", "catalog": "memory"},
        platform="trino",
        generated_key="schema",
        fields=("catalog",),
    )

    assert result == {"schema": "explicit", "catalog": "memory"}
    generate.assert_not_called()


def test_supported_tuning_type_names_override_base_compatibility() -> None:
    class FixedSupportAdapter(TuningHooksMixin):
        platform_name = "not-used"
        _supported_tuning_type_names = ("PARTITIONING",)

    adapter = FixedSupportAdapter()

    assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True
    assert adapter.supports_tuning_type(TuningType.SORTING) is False


def test_informational_constraint_applier_logs_enabled_constraints() -> None:
    class Adapter:
        logger = MagicMock()
        apply_constraint_configuration = make_informational_constraint_applier("primary enabled", "foreign enabled")

    adapter = Adapter()
    adapter.apply_constraint_configuration(SimpleNamespace(enabled=True), SimpleNamespace(enabled=False), object())

    adapter.logger.info.assert_called_once_with("primary enabled")


def test_apply_standard_unified_tuning_preserves_dispatch_order() -> None:
    class Adapter:
        def __init__(self) -> None:
            self.calls = []

        def apply_constraint_configuration(self, primary_keys, foreign_keys, connection) -> None:
            self.calls.append(("constraints", primary_keys, foreign_keys, connection))

        def apply_platform_optimizations(self, platform_optimizations, connection) -> None:
            self.calls.append(("platform", platform_optimizations, connection))

        def apply_table_tunings(self, table_tuning, connection) -> None:
            self.calls.append(("table", table_tuning, connection))

    adapter = Adapter()
    connection = object()
    config = SimpleNamespace(
        primary_keys="pk",
        foreign_keys="fk",
        platform_optimizations="platform",
        table_tunings={"orders": "orders_tuning", "lineitem": "lineitem_tuning"},
    )

    apply_standard_unified_tuning(adapter, config, connection)

    assert adapter.calls == [
        ("constraints", "pk", "fk", connection),
        ("platform", "platform", connection),
        ("table", "orders_tuning", connection),
        ("table", "lineitem_tuning", connection),
    ]


def test_build_adapter_config_forwards_plan_keys_and_skips_none() -> None:

    result = build_adapter_config(
        {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "show_query_plans": True,
            "capture_plans": False,
            "plan_queries": "1,6",
            "plan_capture_timeout_seconds": None,
        },
        platform="platform",
        fields=(),
        include_none=False,
    )

    assert result["show_query_plans"] is True
    assert result["capture_plans"] is False
    assert result["plan_queries"] == "1,6"
    assert "plan_capture_timeout_seconds" not in result


