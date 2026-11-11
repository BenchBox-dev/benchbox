from __future__ import annotations

import pytest

from benchbox.core.platform_registry import PlatformRegistry
from benchbox.platforms.base import PlatformAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _registered_adapter_classes() -> list[tuple[str, type[PlatformAdapter]]]:
    adapters: list[tuple[str, type[PlatformAdapter]]] = []
    for platform_name in PlatformRegistry.get_available_platforms():
        adapter_class = PlatformRegistry.get_adapter_class(platform_name)
        if adapter_class is None:
            continue
        if isinstance(adapter_class, type) and issubclass(adapter_class, PlatformAdapter):
            adapters.append((platform_name, adapter_class))
    return adapters


def test_registered_adapters_declare_plan_capture_phase_eligibility() -> None:

    missing = [
        f"{platform_name}: {adapter_class.__module__}.{adapter_class.__name__}"
        for platform_name, adapter_class in _registered_adapter_classes()
        if "plan_capture_phase_eligible" not in adapter_class.__dict__
    ]

    assert not missing, "adapters missing explicit plan_capture_phase_eligible declarations: " + ", ".join(missing)


