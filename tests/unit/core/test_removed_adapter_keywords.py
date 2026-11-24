from __future__ import annotations

import importlib

import pytest

from benchbox.core.platform_manifest import PLATFORM_MANIFEST
from benchbox.core.removed_options import adapter_manifest_key, removed_option_pairs

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ADAPTER_PAIRS = [
    (platform, key)
    for platform, key in removed_option_pairs()
    if any(entry.key == platform and entry.adapter is not None for entry in PLATFORM_MANIFEST)
]


def _adapter_class(platform: str) -> type:
    entry = next(item for item in PLATFORM_MANIFEST if item.key == platform)
    return getattr(importlib.import_module(entry.adapter.module), entry.adapter.class_name)


@pytest.mark.parametrize("entry", [e for e in PLATFORM_MANIFEST if e.adapter is not None], ids=lambda e: e.key)
def test_every_registered_adapter_class_maps_to_its_manifest_key(entry) -> None:
    cls = _adapter_class(entry.key)
    assert adapter_manifest_key(cls) == entry.key


@pytest.mark.parametrize(("platform", "key"), ADAPTER_PAIRS)
def test_adapter_constructor_rejects_removed_keyword(platform: str, key: str) -> None:
    cls = _adapter_class(platform)
    with pytest.raises(TypeError, match=f"Platform option '{key}' for '{platform}' was removed in BenchBox 0.6.0"):
        cls(**{key: "x"})
